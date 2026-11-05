from inside_block.analyze import analyze_rows, kendall_tau


def row(role, send_rank, fee, slot, index, batch="B1", node="NODE1", status="in_window"):
    return {
        "role": role,
        "send_rank": send_rank,
        "fee_price_micro": fee,
        "landed_slot": slot,
        "block_index": index,
        "batch_id": batch,
        "target_leader_node": node,
        "target_client": "agave",
        "status": status,
        "window_slots": [100, 101, 102, 103],
    }


def test_kendall_tau_extremes():
    assert kendall_tau([0, 1, 2, 3], [0, 1, 2, 3]) == 1.0
    assert kendall_tau([0, 1, 2, 3], [3, 2, 1, 0]) == -1.0
    assert kendall_tau([0], [0]) is None


def test_priority_dominant_synthetic():
    rows = []
    # fee probes sent ascending fee (ranks 0..3) but land in exact reverse order
    fees = [0, 1_000, 10_000, 100_000]
    for rep in range(3):  # 3 batches -> 18 pairs
        base = 100 + rep * 10
        for i, f in enumerate(fees):
            rows.append(row("fee", i, f, base, 40 - i * 10, batch=f"B{rep}"))
    out = analyze_rows(rows)
    assert out["overall"]["fee_pairs"] == 18
    assert out["overall"]["fee_reversal_rate"] == 1.0
    assert out["overall"]["conclusion"] == "PRIORITY_DOMINANT"


def test_insufficient_data_and_dropped():
    rows = [
        row("fee", 0, 0, 100, 5),
        row("fee", 1, 100_000, None, None, status="dropped"),
    ]
    out = analyze_rows(rows)
    assert out["overall"]["conclusion"] == "INSUFFICIENT_DATA"
    v = out["per_validator"]["NODE1"]
    assert v["dropped"] == 1
    assert v["inclusion_rate"] == 0.5


def test_conflict_outcome_high_first():
    rows = [
        row("conflict", 8, 1_000, 100, 30),
        row("conflict", 9, 100_000, 100, 12),
    ]
    out = analyze_rows(rows)
    assert out["per_validator"]["NODE1"]["conflict_outcomes"] == ["high_fee_first"]
