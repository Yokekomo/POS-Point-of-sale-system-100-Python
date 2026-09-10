"""Motor de stock de carne (§7.1), versión v4 sobre base de datos.

Reconstruye el stock desde cero en cada corrida:
    IN (recepciones) + cortes de despiece - OUT (ventas vía Portion_Map)
    - WASTE ± READJUST (conteos físicos re-anclan)

v4 = v3 + floors "grounded": un negativo imposible sube a 0 SOLO si ese SKU
tiene base física (conteo / FEFO / baseline). Sin base => se comporta como v3:
el negativo queda y se flaggea. Nunca se inventa un IN.

Genealogía: primal (serial) -> TG -> cortes -> hojas. Los primales se drenan
SIEMPRE por serial a través de `despiece_primals`, única fuente de verdad.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from thegrill import config
from thegrill.models import MovementType

# Ruta corte -> pool de primal. Un hueco aquí = stock fantasma (el corte no drena el primal).
PRIMAL_SKU_MAP: dict[str, str] = {
    "STRIPLOIN_AUS_MB3": "STRIPLOIN_BONELESS_AUS",
    "STRIPLOIN_USA_PRIME": "STRIPLOIN_BONELESS_USA",
    "CUBE_ROLL_WAGYU": "CUBE_ROLL_WAGYU",
    "RIB_EYE_USA_PRIME": "RIB_EYE_USA_PRIME",
    "TENDERLOIN_AUS": "TENDERLOIN_AUS",
    "TOMAHAWK": "RIB_BONE_IN",
    "T_BONE": "SHORTLOIN_BONE_IN",
}


class MappingGap(KeyError):
    """Corte sin ruta a primal: hay que cubrirlo, no ignorarlo."""


def primal_sku(cut_sku: str, mapping: dict[str, str] | None = None) -> str:
    m = mapping if mapping is not None else PRIMAL_SKU_MAP
    try:
        return m[cut_sku]
    except KeyError:
        raise MappingGap(f"Corte '{cut_sku}' sin ruta primal: causa stock fantasma") from None


def mapping_gaps(cut_skus: set[str], mapping: dict[str, str] | None = None) -> set[str]:
    m = mapping if mapping is not None else PRIMAL_SKU_MAP
    return {c for c in cut_skus if c not in m}


@dataclass
class Movement:
    date: date
    sku: str
    type: MovementType
    kg: float
    pieces: int | None = None
    source: str = ""
    source_ref: str | None = None


@dataclass
class SkuBalance:
    sku: str
    kg: float = 0.0
    pieces: int = 0
    floored_kg: float = 0.0          # cuánto subió v4 a 0 (para auditoría)
    negative_flag: bool = False      # v3: negativo sin base física
    history: list[tuple[date, str, float]] = field(default_factory=list)


@dataclass
class StockResult:
    balances: dict[str, SkuBalance]
    negatives: list[str]
    floored: list[str]

    def kg(self, sku: str) -> float:
        return round(self.balances[sku].kg, 3) if sku in self.balances else 0.0


def rebuild(movements: list[Movement], physical_base: set[str] | None = None) -> StockResult:
    """Reconstruye el stock por SKU en orden cronológico.

    - READJUST fija el saldo al valor del conteo (re-ancla), no suma.
    - v4 floors: al final, un saldo negativo sube a 0 solo si sku ∈ physical_base.
    """
    physical_base = physical_base or set()
    balances: dict[str, SkuBalance] = {}
    for mv in sorted(movements, key=lambda m: (m.date, _order(m.type))):
        b = balances.setdefault(mv.sku, SkuBalance(mv.sku))
        if mv.type == MovementType.READJUST:
            b.kg = mv.kg
            if mv.pieces is not None:
                b.pieces = mv.pieces
        elif mv.type in (MovementType.IN, MovementType.FROZEN_CUT):
            b.kg += mv.kg
            b.pieces += mv.pieces or 0
        elif mv.type in (MovementType.OUT, MovementType.WASTE):
            b.kg -= mv.kg
            b.pieces -= mv.pieces or 0
        b.history.append((mv.date, mv.type.value, round(b.kg, 3)))

    negatives, floored = [], []
    for sku, b in balances.items():
        if b.kg < -1e-9:
            if sku in physical_base:
                b.floored_kg = -b.kg
                b.kg = 0.0
                floored.append(sku)
            else:
                b.negative_flag = True
                negatives.append(sku)
    return StockResult(balances, negatives, floored)


def _order(t: MovementType) -> int:
    # dentro de un mismo día: entradas, luego salidas, y el conteo re-ancla al final
    return {MovementType.IN: 0, MovementType.FROZEN_CUT: 1, MovementType.OUT: 2,
            MovementType.WASTE: 3, MovementType.READJUST: 9}[t]


# ---------------------------------------------------------------- Despiece
@dataclass
class MassCheck:
    tg: str
    input_kg: float
    output_kg: float
    drift_kg: float
    drift_pct: float
    ok: bool


def mass_balance(tg: str, weight_before_kg: float, total_cuts_kg: float, waste_kg: float,
                 trim_kg: float, tolerance_pct: float = config.MASS_DRIFT_TOLERANCE_PCT) -> MassCheck:
    """Conservación de masa: in = cortes + merma + trim ± drift."""
    out = total_cuts_kg + waste_kg + trim_kg
    drift = round(weight_before_kg - out, 3)
    pct = abs(drift) / weight_before_kg * 100 if weight_before_kg else 0.0
    return MassCheck(tg, weight_before_kg, round(out, 3), drift, round(pct, 2), pct <= tolerance_pct)


def yield_pct(weight_before_kg: float, total_cuts_kg: float) -> float:
    return round(total_cuts_kg / weight_before_kg * 100, 2) if weight_before_kg else 0.0


def sales_to_movements(sales_lines: list[dict], portion_map: dict[str, tuple[str, float]],
                       op_date: date) -> tuple[list[Movement], list[str]]:
    """Ventas POS -> OUT de carne vía Portion_Map. Devuelve movimientos y platos sin mapa."""
    out, unmapped = [], []
    agg: dict[str, float] = defaultdict(float)
    for line in sales_lines:
        dish = line["dish"]
        if dish not in portion_map:
            unmapped.append(dish)
            continue
        sku, kg_per_portion = portion_map[dish]
        agg[sku] += line["units"] * kg_per_portion
    for sku, kg in agg.items():
        out.append(Movement(op_date, sku, MovementType.OUT, round(kg, 4), source="POS"))
    return out, sorted(set(unmapped))


# ---------------------------------------------------------- Primales / fantasmas
def drain_primals(primals: dict[str, dict], consumed: list[tuple[str, str, date]]) -> list[str]:
    """Marca CUT por serial a partir de despiece_primals (tg, serial, fecha).

    Es la ÚNICA vía para pasar un primal a CUT (junto con conteo físico).
    Devuelve seriales referenciados que no existen en el registro.
    """
    missing = []
    for tg, serial, on in consumed:
        p = primals.get(serial)
        if p is None:
            missing.append(serial)
            continue
        p["status"] = "CUT"
        p["status_ref"] = tg
        p["status_date"] = on
    return missing


def suspect_phantoms(primals: dict[str, dict], last_count_serials: set[str],
                     last_count_date: date | None) -> list[str]:
    """IN_STOCK que no apareció en el último conteo físico completo => SUSPECT PHANTOM.
    Sin conteo no se puede sospechar de nadie (regla 3: no inferir)."""
    if last_count_date is None:
        return []
    return sorted(s for s, p in primals.items()
                  if p.get("status") == "IN_STOCK" and s not in last_count_serials
                  and (p.get("received_date") is None or p["received_date"] <= last_count_date))
