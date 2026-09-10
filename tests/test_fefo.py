from datetime import date
import pytest

from thegrill.engine.fefo import Lot, NoCostBasis, consume, expiring

D = date(2026, 9, 10)
LOTS = [
    Lot("beef_trim", "B", date(2026, 9, 15), 5.0, 8.0, received=date(2026, 9, 1)),
    Lot("beef_trim", "A", date(2026, 9, 12), 3.0, 10.0, received=date(2026, 9, 5)),
    Lot("beef_trim", "C", date(2026, 9, 12), 2.0, 9.0, received=date(2026, 9, 2)),   # misma caducidad que A, recibido antes
    Lot("butter", "X", date(2026, 10, 1), 10.0, 6.0),
]


def test_consumes_earliest_expiry_then_fifo():
    r = consume(LOTS, "beef_trim", 4.0)
    assert [(c.lot_id, c.kg) for c in r.consumptions] == [("C", 2.0), ("A", 2.0)]
    assert r.cost_usd == 2 * 9.0 + 2 * 10.0
    left = {l.lot_id: l.kg for l in r.remaining}
    assert left == {"A": 1.0, "B": 5.0, "X": 10.0}


def test_never_blank_cost():
    with pytest.raises(NoCostBasis):
        consume(LOTS, "saffron", 0.1)


def test_shortfall_flagged_not_silent():
    with pytest.raises(NoCostBasis):
        consume(LOTS, "beef_trim", 20.0)
    r = consume(LOTS, "beef_trim", 20.0, allow_shortfall=True)
    assert r.shortfall_kg == 10.0 and r.consumptions[-1].lot_id == "SHORTFALL"


def test_expiring_alert():
    assert [l.lot_id for l in expiring(LOTS, D, within_days=2)] == ["C", "A"]
