from solders.keypair import Keypair

from inside_block.batch import (
    FEE_TIERS,
    batch_estimated_fee_lamports,
    build_batch_plan,
    build_transaction,
    parse_memo,
    transaction_to_b64,
)


def test_plan_shape_and_send_order():
    plan = build_batch_plan("B1")
    assert len(plan) == 10
    assert [p.send_rank for p in plan] == list(range(10))
    fee = [p for p in plan if p.role == "fee"]
    # fee probes are sent in ascending fee order (lowest first, highest last)
    assert [p.fee_price_micro for p in fee] == sorted(FEE_TIERS)
    assert [p.account_index for p in fee] == [0, 1, 2, 3]  # non-conflicting accounts
    arrival = [p for p in plan if p.role == "arrival"]
    assert len({p.fee_price_micro for p in arrival}) == 1  # fee held constant
    assert len({p.account_index for p in arrival}) == 4  # distinct accounts
    conflict = [p for p in plan if p.role == "conflict"]
    assert len(conflict) == 2
    assert conflict[0].account_index == conflict[1].account_index  # same write lock
    assert conflict[0].fee_price_micro < conflict[1].fee_price_micro  # high fee sent second
    assert conflict[0].send_rank < conflict[1].send_rank


def test_memos_unique_and_parseable():
    plan = build_batch_plan("BATCH42")
    memos = [p.memo for p in plan]
    assert len(set(memos)) == len(memos)
    for spec in plan:
        parsed = parse_memo(spec.memo)
        assert parsed["batch_id"] == "BATCH42"
        assert parsed["role"] == spec.role
        assert parsed["send_rank"] == spec.send_rank
        assert parsed["fee_price_micro"] == spec.fee_price_micro


def test_fee_math():
    plan = build_batch_plan("B1", cu_limit=5000)
    by_fee = {p.fee_price_micro: p for p in plan if p.role == "fee"}
    assert by_fee[0].priority_fee_lamports == 0
    assert by_fee[1_000].priority_fee_lamports == 5
    assert by_fee[10_000].priority_fee_lamports == 50
    assert by_fee[100_000].priority_fee_lamports == 500
    assert by_fee[0].estimated_fee_lamports == 5000  # base fee only
    total = batch_estimated_fee_lamports(plan)
    # 10 base fees (50_000) + priorities: fee 555 + arrival 4*5 + conflict 5+500
    assert total == 50_000 + 555 + 20 + 505


def test_transaction_builds_and_signs():
    plan = build_batch_plan("B1")
    kp = Keypair()
    # rebuild plan against this keypair's account slot 0 only for construction check
    spec = plan[0]
    from solders.keypair import Keypair as KP

    tx = build_transaction(spec, kp, "4uQeVj5tqViQh7yWWGStvkEG1Zmhx6uasJtWCJziofM")
    assert str(tx.signatures[0]) != "1111111111111111111111111111111111111111111111111111111111111111"
    b64 = transaction_to_b64(tx)
    assert len(b64) > 100
    assert isinstance(KP, type)  # silence linter noise
