from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .batch import batch_estimated_fee_lamports, build_batch_plan, build_transaction, transaction_to_b64
from .config import ProbeConfig, ValidatorTarget
from .keys import load_main_keypair, load_or_create_probe_keypairs
from .ledger import SpendLedger, sol_to_lamports
from .rpc import RpcClient


@dataclass
class SendRecord:
    signature: str | None
    batch_id: str
    role: str
    send_rank: int
    fee_price_micro: int
    account_pubkey: str
    memo: str
    mode: str  # "live" | "dry-run"
    target_leader_name: str
    target_leader_node: str
    target_leader_vote: str
    target_client: str
    window_start_slot: int
    window_slots: list[int]
    sent_monotonic: float | None
    sent_wall_iso: str | None
    estimated_fee_lamports: int
    simulate_err: str | None = None


def find_windows(schedule: dict[str, list[int]], node_pubkey: str) -> list[list[int]]:
    """Group a validator's leader slots into maximal consecutive windows (usually 4)."""
    slots = sorted(schedule.get(node_pubkey, []))
    windows: list[list[int]] = []
    for s in slots:
        if windows and s == windows[-1][-1] + 1:
            windows[-1].append(s)
        else:
            windows.append([s])
    return windows


def next_window(schedule: dict[str, list[int]], target: ValidatorTarget, after_slot: int):
    for w in find_windows(schedule, target.node_pubkey):
        if w[-1] >= after_slot:
            return w
    return None


def run_setup(cfg: ProbeConfig, keys_dir: Path, data_dir: Path, live: bool) -> dict:
    """Create probe accounts locally; fund them from the main wallet.
    Dry-run (default): only prints/simulates the funding transfers."""
    probes = load_or_create_probe_keypairs(keys_dir, cfg.batch.probe_account_count)
    rpc = RpcClient(cfg.rpc_url)
    blockhash = rpc.get_latest_blockhash()
    funding = cfg.batch.funding_lamports_per_probe
    actions = []
    if live:
        main = load_main_keypair()
        from solders.system_program import TransferParams, transfer as sys_transfer
        from solders.transaction import Transaction
        from solders.hash import Hash

        for kp in probes:
            tx = Transaction.new_signed_with_payer(
                [sys_transfer(TransferParams(from_pubkey=main.pubkey(), to_pubkey=kp.pubkey(), lamports=funding))],
                main.pubkey(),
                [main],
                Hash.from_string(blockhash),
            )
            sig = rpc.send_transaction(transaction_to_b64(tx))
            actions.append({"probe": str(kp.pubkey()), "funding_lamports": funding, "signature": sig})
    else:
        for kp in probes:
            actions.append(
                {"probe": str(kp.pubkey()), "funding_lamports": funding, "signature": None, "simulated": True}
            )
    return {"mode": "live" if live else "dry-run", "probe_accounts": actions, "blockhash": blockhash}


def _pick_dryrun_window(cfg: ProbeConfig, rpc: RpcClient):
    """For a dry-run we do not wait for a far-away window: use the next real
    window of a configured target if one is near, otherwise fall back to the
    current slot leaders so the batch can be built and simulated now."""
    epoch = rpc.get_epoch_info()
    schedule = rpc.get_leader_schedule(epoch["epoch"])
    current_slot = rpc.get_slot()
    for t in cfg.configured_validators():
        w = next_window(schedule, t, current_slot)
        if w:
            return t, w, current_slot, False
    leaders = rpc.get_slot_leaders(current_slot, 4)
    node = leaders[0]
    target = ValidatorTarget(name="dryrun-current-leader", vote_pubkey="", node_pubkey=node, client="unknown (dry-run fallback)")
    return target, [current_slot + i for i in range(4)], current_slot, True


def run_probe(
    cfg: ProbeConfig,
    keys_dir: Path,
    data_dir: Path,
    live: bool,
    max_fee_sol: float = 0.05,
    batch_id: str | None = None,
) -> list[SendRecord]:
    if live and os.environ.get("CONFIRM_LIVE") != "1":
        raise SystemExit("refusing live mode: set CONFIRM_LIVE=1 in the environment as well as --live")
    data_dir.mkdir(parents=True, exist_ok=True)
    ledger = SpendLedger(data_dir / "spend_ledger.json")
    cap = sol_to_lamports(max_fee_sol)

    probes = load_or_create_probe_keypairs(keys_dir, cfg.batch.probe_account_count)
    rpc = RpcClient(cfg.rpc_url)
    batch_id = batch_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plan = build_batch_plan(batch_id, cu_limit=cfg.batch.cu_limit)
    est = batch_estimated_fee_lamports(plan)
    ledger.check(est, cap)  # refuse BEFORE any send if the batch would break the cap

    if live:
        # Wait for the next target window and fire inside it.
        epoch = rpc.get_epoch_info()
        schedule = rpc.get_leader_schedule(epoch["epoch"])
        current = rpc.get_slot()
        candidates = []
        for t in cfg.configured_validators():
            w = next_window(schedule, t, current + 1)
            if w:
                candidates.append((w[0], t, w))
        if not candidates:
            raise SystemExit("no upcoming leader window for any configured target in this epoch")
        candidates.sort()
        _, target, window = candidates[0]
        blockhash = rpc.get_latest_blockhash()
        txs = [(spec, build_transaction(spec, probes[spec.account_index], blockhash, cfg.batch.transfer_lamports)) for spec in plan]
        while rpc.get_slot() < window[0]:
            time.sleep(0.1)
        records: list[SendRecord] = []
        for spec, tx in txs:
            ledger.check(spec.estimated_fee_lamports, cap)
            mono = time.monotonic()
            wall = datetime.now(timezone.utc).isoformat()
            sig = rpc.send_transaction(transaction_to_b64(tx))
            ledger.record(spec.estimated_fee_lamports, "send-estimated", str(sig))
            records.append(
                SendRecord(str(sig), batch_id, spec.role, spec.send_rank, spec.fee_price_micro,
                           str(probes[spec.account_index].pubkey()), spec.memo, "live",
                           target.name, target.node_pubkey, target.vote_pubkey, target.client,
                           window[0], window, mono, wall, spec.estimated_fee_lamports)
            )
            time.sleep(cfg.batch.stagger_ms / 1000.0)
    else:
        target, window, current_slot, fallback = _pick_dryrun_window(cfg, rpc)
        blockhash = rpc.get_latest_blockhash()
        records = []
        for spec in plan:
            tx = build_transaction(spec, probes[spec.account_index], blockhash, cfg.batch.transfer_lamports)
            sim_err = None
            try:
                sim = rpc.simulate_transaction(transaction_to_b64(tx))
                if sim.get("err") is not None:
                    sim_err = json.dumps(sim["err"])
            except Exception as exc:  # simulation transport failure is recorded, not fatal for dry-run
                sim_err = f"simulate rpc error: {exc}"
            records.append(
                SendRecord(None, batch_id, spec.role, spec.send_rank, spec.fee_price_micro,
                           str(probes[spec.account_index].pubkey()), spec.memo, "dry-run",
                           target.name, target.node_pubkey, target.vote_pubkey, target.client,
                           window[0], window, None, None, spec.estimated_fee_lamports, sim_err)
            )
    sends_path = data_dir / "sends.jsonl"
    with sends_path.open("a") as f:
        for r in records:
            f.write(json.dumps(asdict(r)) + "\n")
    return records
