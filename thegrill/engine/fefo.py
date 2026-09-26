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

    # [01668] Los dos totales, con los mismos decimales con los que se apunta cada
    # salida. Iban a cuatro mientras las salidas iban a seis, así que el total
    # no era la suma de sus partes: hasta medio decigramo y medio céntimo de
    # diferencia entre lo que dice el resumen y lo que dicen las líneas. En un
    # módulo cuyo trabajo entero es que las partes sumen el total, ese es el
    # sitio donde menos puede pasar.
    @property
    def cost_usd(self) -> float:
        """[00114] Lo que cuesta todo lo consumido, lote a lote."""
        return round(sum(c.cost_usd for c in self.consumptions), 6)

    @property
    def kg(self) -> float:
        """[00115] Los kilos consumidos en total."""
        return round(sum(c.kg for c in self.consumptions), 6)


def _antiguedad(lot_id: str) -> tuple[int, int, str]:
    """[01884] El desempate final: el número que se dio de alta antes.

    `lot_id` viaja como texto, y ordenar números escritos como texto los pone
    en orden de diccionario: el 10 antes que el 9. Con dos lotes de la misma
    caducidad y la misma entrada —el caso corriente de dos cajas del mismo
    palé— la cola salía «1, 10, 11, 12, 2, 3…» y se servía antes la caja que
    llegó después. No cambia la cuenta, pero sí qué caja se abre y qué número
    acaba en el plato, que es justo lo que hay que poder contar luego.
    """
    return (0, int(lot_id), "") if lot_id.lstrip("-").isdigit() else (1, 0, lot_id)


def order_fefo(lots: list[Lot]) -> list[Lot]:
    """[00108] Antes lo que antes caduca; a igual caducidad, lo que antes entró."""
    return sorted(lots, key=lambda l: (l.expiry, l.received or date.min, _antiguedad(l.lot_id)))


def order_fifo(lots: list[Lot]) -> list[Lot]:
    """[00109] Antes lo que antes entró; a igual entrada, lo que antes caduca."""
    return sorted(lots, key=lambda l: (l.received or date.min, l.expiry, _antiguedad(l.lot_id)))


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
        # [01669] Lo que se apunta es lo que se saca, con los mismos decimales.
        # Se apuntaba redondeado a cuatro y se descontaba a seis, así que el
        # papel decía una cosa y el lote otra: hasta medio decigramo por lote
        # y por salida, siempre en la misma dirección. No llega a la báscula,
        # pero es un libro que no cuadra con el almacén, y esa clase de hueco
        # es la que se descubre tres meses después sin poder explicarla.
        result.consumptions.append(Consumption(lot.lot_id, round(take, 6),
                                               lot.unit_cost_usd))
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
