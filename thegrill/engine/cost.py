"""Coste y food cost (§7.3)."""
from dataclasses import dataclass
from datetime import date, timedelta

from thegrill.config import to_usd


@dataclass
class LandedLot:
    lot: str
    goods_usd: float
    freight_usd: float
    duty_usd: float
    total_kg: float

    @property
    def landed_usd_per_kg(self) -> float:
        if self.total_kg <= 0:
            raise ValueError("Lote sin kg")
        return round((self.goods_usd + self.freight_usd + self.duty_usd) / self.total_kg, 4)


def piece_cost(landed_usd_per_kg: float, weight_kg: float) -> float:
    return round(landed_usd_per_kg * weight_kg, 2)


def cut_cost_per_kg(landed_usd_per_kg: float, yield_pct: float) -> float:
    """Roll-forward: el coste del primal se reparte sobre los kg vendibles."""
    if yield_pct <= 0:
        raise ValueError("Rendimiento inválido")
    return round(landed_usd_per_kg / (yield_pct / 100), 4)


def food_cost_proxy(purchases: dict[date, tuple[float, str]], sales_usd: dict[date, float],
                    on: date, window_days: int = 7) -> float | None:
    """(a) proxy cash-basis: compras ÷ ventas, media móvil de `window_days`."""
    days = [on - timedelta(days=i) for i in range(window_days)]
    buys = sum(to_usd(*purchases[d]) for d in days if d in purchases)
    sold = sum(sales_usd.get(d, 0.0) for d in days)
    return round(buys / sold * 100, 2) if sold else None


def food_cost_real(opening_usd: float, purchases_usd: float, closing_usd: float,
                   sales_usd: float) -> float | None:
    """(b) real por consumo entre dos conteos físicos: (apertura + compras - cierre) ÷ ventas."""
    consumed = opening_usd + purchases_usd - closing_usd
    return round(consumed / sales_usd * 100, 2) if sales_usd else None
