import pytest

from inside_block.ledger import SpendCapExceeded, SpendLedger, sol_to_lamports


def test_record_and_persist(tmp_path):
    path = tmp_path / "ledger.json"
    led = SpendLedger(path)
    assert led.spent_lamports == 0
    led.record(1000, "send-estimated", "sig1")
    led.record(2500, "send-estimated", "sig2")
    assert led.spent_lamports == 3500
    reloaded = SpendLedger(path)
    assert reloaded.spent_lamports == 3500
    assert len(reloaded.entries) == 2


def test_cap_boundary(tmp_path):
    led = SpendLedger(tmp_path / "ledger.json")
    cap = sol_to_lamports(0.05)
    assert cap == 50_000_000
    led.check(cap, cap)  # exactly at cap is allowed
    with pytest.raises(SpendCapExceeded):
        led.check(cap + 1, cap)
    led.record(cap - 10, "send-estimated", "sig")
    assert led.remaining(cap) == 10
    led.check(10, cap)
    with pytest.raises(SpendCapExceeded):
        led.check(11, cap)


def test_negative_record_rejected(tmp_path):
    led = SpendLedger(tmp_path / "ledger.json")
    with pytest.raises(ValueError):
        led.record(-1, "x", "y")
