"""[00106] Motor FEFO / FIFO (§7.2).

Consumo por caducidad más próxima (FEFO); a igual caducidad, el recibido antes
(FIFO). Toda merma y producción se valora por FEFO: nunca se deja coste en blanco.
El maestro FEFO no se toca desde aquí: solo se opera sobre lotes en memoria y
las altas van a staging TO_ADD.
"""
from dataclasses import dataclass, field, replace
from datetime import date


class NoCostBasis(ValueError):
    """[00107] No hay lotes para valorar el consumo: hay que resolverlo, no dejarlo en blanco."""


@dataclass
class Lot:
    ingredient: str
    lot_id: str
    expiry: date
    kg: float
    unit_cost_usd: float
    received: date | None = None


@dataclass
class Consumption:
    lot_id: str
    kg: float
    unit_cost_usd: float

    @property
    def cost_usd(self) -> float:
        """[00113] Lo que cuesta lo que se sacó de ese lote."""
        return round(self.kg * self.unit_cost_usd, 4)


@dataclass
class FefoResult:
    consumptions: list[Consumption] = field(default_factory=list)
    remaining: list[Lot] = field(default_factory=list)
    shortfall_kg: float = 0.0

    @property
    def cost_usd(self) -> float:
        """[00114] Lo que cuesta todo lo consumido, lote a lote."""
        return round(sum(c.cost_usd for c in self.consumptions), 4)

    @property
    def kg(self) -> float:
        """[00115] Los kilos consumidos en total."""
        return round(sum(c.kg for c in self.consumptions), 4)


def order_fefo(lots: list[Lot]) -> list[Lot]:
    """[00108] Antes lo que antes caduca; a igual caducidad, lo que antes entró."""
    return sorted(lots, key=lambda l: (l.expiry, l.received or date.min, l.lot_id))


def order_fifo(lots: list[Lot]) -> list[Lot]:
    """[00109] Antes lo que antes entró; a igual entrada, lo que antes caduca."""
    return sorted(lots, key=lambda l: (l.received or date.min, l.expiry, l.lot_id))


def order_by(lots: list[Lot], rotation: str = "FEFO") -> list[Lot]:
    """[00110] Ordena los lotes según la rotación de la casa: FEFO o FIFO.

    No es lo mismo: FEFO saca primero lo que caduca antes, que es lo que
    protege al cliente; FIFO saca lo que entró antes, que es lo que cuadra el
    almacén. Con carne manda la caducidad.
    """
    return order_fifo(lots) if str(rotation).upper().endswith("FIFO") else order_fefo(lots)


def consume(lots: list[Lot], ingredient: str, kg: float, allow_shortfall: bool = False) -> FefoResult:
    """[00111] Descuenta `kg` del ingrediente por FEFO. Devuelve consumos valorados y stock restante.

    Si no hay lotes del ingrediente => NoCostBasis (regla 5: nunca coste en blanco).
    Si hay lotes pero no llegan, con allow_shortfall=True se registra el faltante
    valorado al último coste conocido, y se devuelve shortfall_kg > 0 para flag.
    """
    if kg <= 0:
        return FefoResult(remaining=list(lots))
    mine = [l for l in lots if l.ingredient == ingredient and l.kg > 0]
    others = [l for l in lots if l.ingredient != ingredient or l.kg <= 0]
    if not mine:
        raise NoCostBasis(f"Sin lotes FEFO para '{ingredient}': no se puede valorar {kg} kg")

    result = FefoResult()
    pending = kg
    last_cost = None
    for lot in order_fefo(mine):
        if pending <= 0:
            result.remaining.append(lot)
            continue
        take = min(lot.kg, pending)
        result.consumptions.append(Consumption(lot.lot_id, round(take, 4), lot.unit_cost_usd))
        last_cost = lot.unit_cost_usd
        pending = round(pending - take, 6)
        left = round(lot.kg - take, 6)
        if left > 0:
            result.remaining.append(replace(lot, kg=left))
    if pending > 0:
        if not allow_shortfall:
            raise NoCostBasis(f"Stock FEFO insuficiente para '{ingredient}': faltan {pending} kg")
        result.shortfall_kg = pending
        result.consumptions.append(Consumption("SHORTFALL", pending, last_cost))
    result.remaining.extend(others)
    return result


def expiring(lots: list[Lot], today: date, within_days: int = 3) -> list[Lot]:
    """[00112] Alerta FEFO: lotes que caducan en <= within_days (incluye ya caducados)."""
    return [l for l in order_fefo(lots) if l.kg > 0 and (l.expiry - today).days <= within_days]
