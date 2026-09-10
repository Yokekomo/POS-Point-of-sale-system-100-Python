from datetime import date

from thegrill.config import to_usd
from thegrill.engine import cost


def test_fx():
    assert to_usd(1550, "IQD") == 1.0
    assert round(to_usd(1, "AED"), 4) == round(360 / 1550, 4)


def test_landed_and_piece_cost():
    lot = cost.LandedLot("DXB20260828", 9000, 800, 200, 250)
    assert lot.landed_usd_per_kg == 40.0
    assert cost.piece_cost(40.0, 6.25) == 250.0
    assert cost.cut_cost_per_kg(40.0, 80.0) == 50.0


def test_food_cost_proxy_and_real():
    on = date(2026, 9, 10)
    purchases = {date(2026, 9, 9): (1550 * 300, "IQD"), date(2026, 9, 10): (200, "USD")}
    sales = {date(2026, 9, 9): 1000.0, date(2026, 9, 10): 1000.0}
    assert cost.food_cost_proxy(purchases, sales, on) == 25.0
    assert cost.food_cost_real(5000, 3000, 4500, 10000) == 35.0
