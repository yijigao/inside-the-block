from __future__ import annotations

import csv
import json
from pathlib import Path

from .rpc import RpcClient


def load_sends(data_dir: Path) -> list[dict]:
    path = data_dir / "sends.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def collect(cfg_rpc_url: str, data_dir: Path) -> list[dict]:
    """Locate every live probe signature on-chain and record its block index.

    Writes data/raw/block_<slot>.json (ordered signature lists), 
    data/raw/landed.jsonl, data/summary.json and data/summary.csv.
    """
    rpc = RpcClient(cfg_rpc_url)
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    sends = [s for s in load_sends(data_dir) if s.get("mode") == "live" and s.get("signature")]
    summary: list[dict] = []
    if not sends:
        (data_dir / "summary.json").write_text("[]")
        return summary

    statuses = rpc.get_signature_statuses([s["signature"] for s in sends])
    landed_slots: dict[str, int] = {}
    for s, st in zip(sends, statuses):
        if st and st.get("slot") is not None and st.get("confirmationStatus") in ("confirmed", "finalized"):
            landed_slots[s["signature"]] = int(st["slot"])

    block_sigs: dict[int, list[str]] = {}
    for slot in sorted(set(landed_slots.values())):
        sigs = rpc.get_block_signatures(slot)
        if sigs is None:
            sigs = []
        block_sigs[slot] = sigs
        (raw_dir / f"block_{slot}.json").write_text(
            json.dumps({"slot": slot, "signatures": sigs}, indent=1)
        )

    with (raw_dir / "landed.jsonl").open("w") as landed_f:
        for s in sends:
            slot = landed_slots.get(s["signature"])
            index = None
            status = "dropped"
            if slot is not None:
                sigs = block_sigs.get(slot, [])
                index = sigs.index(s["signature"]) if s["signature"] in sigs else None
                status = "in_window" if slot in s["window_slots"] else "late"
            row = {
                "signature": s["signature"],
                "batch_id": s["batch_id"],
                "role": s["role"],
                "send_rank": s["send_rank"],
                "fee_price_micro": s["fee_price_micro"],
                "account_pubkey": s["account_pubkey"],
                "memo": s["memo"],
                "target_leader_name": s["target_leader_name"],
                "target_leader_node": s["target_leader_node"],
                "target_leader_vote": s["target_leader_vote"],
                "target_client": s["target_client"],
                "window_start_slot": s["window_start_slot"],
                "window_slots": s["window_slots"],
                "landed_slot": slot,
                "block_index": index,
                "status": status,
                "estimated_fee_lamports": s["estimated_fee_lamports"],
            }
            summary.append(row)
            landed_f.write(json.dumps(row) + "\n")

    (data_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    if summary:
        with (data_dir / "summary.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
            writer.writeheader()
            for row in summary:
                writer.writerow({k: (json.dumps(v) if isinstance(v, list) else v) for k, v in row.items()})
    return summary
