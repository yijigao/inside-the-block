from __future__ import annotations

import base64
from dataclasses import dataclass

from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

MEMO_PROGRAM_ID = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
BASE_FEE_LAMPORTS_PER_SIGNATURE = 5000

# Fee tiers in micro-lamports per compute unit.
FEE_TIERS = [0, 1_000, 10_000, 100_000]
ARRIVAL_FEE = 1_000
CONFLICT_LOW_FEE = 1_000
CONFLICT_HIGH_FEE = 100_000


@dataclass
class ProbeSpec:
    batch_id: str
    role: str  # "fee" | "arrival" | "conflict"
    send_rank: int  # position in the deliberate send sequence of this batch
    account_index: int  # index into the probe keypair list
    fee_price_micro: int  # compute unit price, micro-lamports per CU
    cu_limit: int

    @property
    def memo(self) -> str:
        # Unique per probe; lets judges (and us) find probes inside raw blocks.
        return f"IB1|{self.batch_id}|{self.role}|{self.send_rank}|{self.fee_price_micro}"

    @property
    def priority_fee_lamports(self) -> int:
        # ceil(cu_limit * price / 1e6)
        return (self.cu_limit * self.fee_price_micro + 999_999) // 1_000_000

    @property
    def estimated_fee_lamports(self) -> int:
        return BASE_FEE_LAMPORTS_PER_SIGNATURE + self.priority_fee_lamports


def parse_memo(memo: str) -> dict:
    parts = memo.split("|")
    if len(parts) != 5 or parts[0] != "IB1":
        raise ValueError(f"not an IB1 memo: {memo!r}")
    return {
        "batch_id": parts[1],
        "role": parts[2],
        "send_rank": int(parts[3]),
        "fee_price_micro": int(parts[4]),
    }


def build_batch_plan(batch_id: str, cu_limit: int = 30000) -> list[ProbeSpec]:
    """The designed probe mix, in deliberate SEND order.

    fee probes (accounts 0-3, non-conflicting): sent in ASCENDING fee order
    (lowest fee first, highest fee last). If the leader's scheduler orders by
    priority, block order will invert send order; if it orders by arrival, it
    will match send order. That inversion rate is the core measurement.

    arrival probes (accounts 4-7): identical fee tier, known send order.
    Baseline for send-order vs block-order correlation with fee held constant.

    conflict probes (account 8 twice): same account => same write lock, so the
    pair must serialize. Low fee sent first, high fee second: does priority
    jump the queue even under a write-lock conflict?
    """
    plan: list[ProbeSpec] = []
    rank = 0
    for i, tier in enumerate(FEE_TIERS):
        plan.append(ProbeSpec(batch_id, "fee", rank, i, tier, cu_limit))
        rank += 1
    for j in range(4):
        plan.append(ProbeSpec(batch_id, "arrival", rank, 4 + j, ARRIVAL_FEE, cu_limit))
        rank += 1
    plan.append(ProbeSpec(batch_id, "conflict", rank, 8, CONFLICT_LOW_FEE, cu_limit))
    rank += 1
    plan.append(ProbeSpec(batch_id, "conflict", rank, 8, CONFLICT_HIGH_FEE, cu_limit))
    return plan


def build_transaction(
    spec: ProbeSpec,
    keypair: Keypair,
    blockhash: str,
    transfer_lamports: int = 1,
) -> Transaction:
    """One probe transaction: compute-budget price/limit + a 1-lamport
    self-transfer (funds stay inside the experiment's own accounts) + memo."""
    payer = keypair.pubkey()
    instructions = [
        set_compute_unit_limit(spec.cu_limit),
        set_compute_unit_price(spec.fee_price_micro),
        transfer(
            TransferParams(from_pubkey=payer, to_pubkey=payer, lamports=transfer_lamports)
        ),
        Instruction(MEMO_PROGRAM_ID, spec.memo.encode(), []),
    ]
    return Transaction.new_signed_with_payer(
        instructions, payer, [keypair], Hash.from_string(blockhash)
    )


def transaction_to_b64(tx: Transaction) -> str:
    return base64.b64encode(bytes(tx)).decode()


def batch_estimated_fee_lamports(plan: list[ProbeSpec]) -> int:
    return sum(p.estimated_fee_lamports for p in plan)
