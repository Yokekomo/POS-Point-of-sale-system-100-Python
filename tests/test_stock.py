from datetime import date
import pytest

from thegrill.engine import stock
from thegrill.engine.stock import Movement, MappingGap
from thegrill.models import MovementType as T

D1, D2, D3 = date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)


def test_rebuild_in_out_and_readjust_reanchors():
    mv = [Movement(D1, "STRIP", T.IN, 10), Movement(D2, "STRIP", T.OUT, 3),
          Movement(D2, "STRIP", T.READJUST, 6.5), Movement(D3, "STRIP", T.WASTE, 0.5)]
    r = stock.rebuild(mv)
    assert r.kg("STRIP") == 6.0


def test_v4_floor_only_with_physical_base():
    mv = [Movement(D1, "A", T.IN, 1), Movement(D2, "A", T.OUT, 3),
          Movement(D1, "B", T.IN, 1), Movement(D2, "B", T.OUT, 3)]
    r = stock.rebuild(mv, physical_base={"A"})
    assert r.kg("A") == 0.0 and r.balances["A"].floored_kg == 2.0 and "A" in r.floored
    assert r.kg("B") == -2.0 and r.balances["B"].negative_flag and "B" in r.negatives


def test_mass_balance_tolerance():
    ok = stock.mass_balance("TG-1", 20.0, 16.0, 2.0, 1.8)
    bad = stock.mass_balance("TG-2", 20.0, 12.0, 2.0, 1.0)
    assert ok.ok and ok.drift_kg == 0.2
    assert not bad.ok and bad.drift_pct == 25.0
    assert stock.yield_pct(20.0, 16.0) == 80.0


def test_mapping_gap_is_loud():
    assert stock.primal_sku("CUBE_ROLL_WAGYU") == "CUBE_ROLL_WAGYU"
    with pytest.raises(MappingGap):
        stock.primal_sku("PICANHA_UNKNOWN")
    assert stock.mapping_gaps({"TOMAHAWK", "PICANHA_UNKNOWN"}) == {"PICANHA_UNKNOWN"}


def test_sales_to_movements_via_portion_map():
    pm = {"Striploin 300g": ("STRIP", 0.3), "Rib eye 400g": ("RIBEYE", 0.4)}
    mv, unmapped = stock.sales_to_movements(
        [{"dish": "Striploin 300g", "units": 10}, {"dish": "Rib eye 400g", "units": 2}, {"dish": "Caesar", "units": 5}], pm, D3)
    assert {m.sku: m.kg for m in mv} == {"STRIP": 3.0, "RIBEYE": 0.8}
    assert unmapped == ["Caesar"]


def test_drain_primals_by_serial_only():
    primals = {"8017": {"status": "IN_STOCK"}, "8018": {"status": "IN_STOCK"}}
    missing = stock.drain_primals(primals, [("TG-0042", "8017", D3), ("TG-0042", "9999", D3)])
    assert primals["8017"] == {"status": "CUT", "status_ref": "TG-0042", "status_date": D3}
    assert primals["8018"]["status"] == "IN_STOCK" and missing == ["9999"]


def test_suspect_phantoms_need_a_count():
    primals = {"1": {"status": "IN_STOCK", "received_date": D1},
               "2": {"status": "IN_STOCK", "received_date": D1},
               "3": {"status": "IN_STOCK", "received_date": D3}}   # llegó después del conteo
    assert stock.suspect_phantoms(primals, {"1"}, None) == []
    assert stock.suspect_phantoms(primals, {"1"}, D2) == ["2"]
