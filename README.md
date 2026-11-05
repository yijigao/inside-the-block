# Inside the Block — How Solana Validators Order Transactions

A measurement project: we send our own transactions into the leader windows of
specific Solana mainnet validators and read the resulting blocks to infer, per
validator, what rule decides which transaction lands earlier — priority
(compute-unit price), arrival order, or no distinguishable rule at all.

> Status skeleton: experiment design, probe, collector and analysis are
> implemented. Measurement results live in `REPORT.md` and `data/` once the
> live runs are done.

## Quick start

```bash
# venv route
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m inside_block.probe setup            # dry-run: create probe accounts, simulate funding
.venv/bin/python -m inside_block.probe run              # dry-run: build a batch, simulate it against mainnet RPC
CONFIRM_LIVE=1 INSIDE_BLOCK_KEYPAIR_PATH=/path/to/main.json \
  .venv/bin/python -m inside_block.probe setup --live   # fund probe accounts for real
CONFIRM_LIVE=1 INSIDE_BLOCK_KEYPAIR_PATH=/path/to/main.json \
  .venv/bin/python -m inside_block.probe run --live     # fire one batch into the next target window
.venv/bin/python -m inside_block.probe collect          # locate signatures, write data/summary.{json,csv}
.venv/bin/python -m inside_block.probe analyze          # write data/analysis.json + data/tables.md

# docker route (same commands)
docker compose run --rm probe setup
docker compose run --rm probe run
```

Dry-run is the default everywhere. Live mode requires **both** `--live` and
`CONFIRM_LIVE=1`, and a hard spend cap (`--max-fee-sol`, default **0.05 SOL**)
enforced by a persistent ledger (`data/spend_ledger.json`): the probe refuses
to send a batch whose estimated fees would exceed the cap.

## Experiment design

Each probe batch (10 transactions, all ours, all inside one leader window)
mixes three probe families:

| Family | Accounts | Fee tiers (µlamports/CU) | Send order | What it separates |
|---|---|---|---|---|
| fee | 4 distinct | 0 / 1k / 10k / 100k | ascending fee, ~75 ms apart | priority vs arrival: if priority rules, block order inverts send order |
| arrival | 4 distinct | all 1k | fixed known order | baseline send↔block correlation with fee held constant (Kendall tau) |
| conflict | 1 account × 2 | 1k then 100k | low fee first, high fee second | whether priority jumps a write-lock conflict queue |

Every transaction is a 1-lamport self-transfer inside the experiment's own
accounts (no third party funds move), with fixed compute-unit limit (30,000; measured consumption is ~10,503 CU — the current SPL Memo program is far heavier than its historical ~300 CU, and a 5,000 limit makes the memo instruction run out of compute)
and an SPL Memo `IB1|<batch>|<role>|<send_rank>|<fee_price>` so any judge can
identify each probe inside raw block data.

### How the design handles the confounders

- **Leader schedule / leader types.** Targets come from `getLeaderSchedule`
  for the current epoch, grouped into each validator's (normally 4-slot)
  leader windows; we arm one window ahead and fire only inside it. Client
  identity per validator (version string / fork, e.g. Agave, Jito-Agave,
  Frankendancer) is recorded in `config.json` at measurement time, from
  `getClusterNodes` / validators.app data.
- **Transaction path.** All probes go through a single RPC endpoint
  (`INSIDE_BLOCK_RPC_URL`, default public mainnet-beta). We do **not**
  separate "the endpoint's forwarding behaviour" from "the leader's
  scheduler": if the endpoint staked-connections route differently, that is
  part of the measured path and is stated as a limitation.
- **Own transactions interacting.** Non-conflicting families use distinct
  accounts (no shared write locks except the system program); the conflict
  family deliberately shares one account to force serialization.
- **Competition inside the block.** Other users' transactions are noise we
  cannot control; we only draw conclusions from the relative order of *our
  own* signatures, never from absolute positions alone.
- **Send order.** Monotonic and wall-clock timestamps are logged per send
  (`data/sends.jsonl`) with a fixed ~75 ms stagger; send order is a designed
  variable, not an assumption.
- **Noise vs rule.** Analysis reports per-validator reversal rates, Kendall
  tau for equal-fee probes, conflict outcomes and inclusion rates, with
  explicit `INSUFFICIENT_DATA` and `NO_CLEAR_RULE_NOISE` conclusions.
  "No rule / it's noise" is a valid result when the data supports it.
- **What this does NOT separate.** (1) RPC endpoint routing vs leader
  scheduling (single endpoint). (2) Jito bundles/tips — out of scope per the
  bounty; Jito *validators* are in scope, bundle ordering is not measured.
  (3) Intra-block execution order vs the order signatures are listed in the
  block — we measure the latter, which is what `getBlock` exposes.
- **Alpenglow.** All measurements are taken after the Oct 21 Alpenglow
  upgrade window; pre-upgrade data is never mixed in. Every batch records
  its date.

## Spend cap and safety

- Fees are estimated before sending (base fee + CU-limit × CU-price) and the
  batch is refused if it would exceed `--max-fee-sol` (default 0.05 SOL).
- Keys live only in files provided via env/volume
  (`INSIDE_BLOCK_KEYPAIR_PATH`, `keys/` for probe accounts). Nothing secret
  is committed: `.gitignore` covers keypairs and wallet files.
- Probe accounts are funded by the `setup` step with a small fixed amount
  each; funding principal stays inside the experiment's own accounts (probe
  transactions are self-transfers), only fees are consumed.

## Layout

- `inside_block/` — config, rpc, keys, ledger (spend cap), batch (probe
  design), runner (setup/run), collect (block indices), analyze (statistics)
- `config.json` — target validators (operator fills real pubkeys)
- `data/` — `sends.jsonl`, `raw/` block signature lists, `summary.{json,csv}`,
  `analysis.json`, `tables.md`, `spend_ledger.json`
- `tests/` — unit tests for spend cap, batch construction, analysis math
