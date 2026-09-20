"""Merma de producto ya en cámara.

La merma del despiece ya la absorben los cortes al repartir el coste del
primal. Esto es la otra: la pieza que se echa a perder **después**, ya cortada
y en la cámara.

Cuando se tira parte de un lote, su coste no desaparece. Se queda en lo que
queda de ese lote: si de diecisiete filetes se tiran dos, los quince que se
vendan tienen que pagar los diecisiete. Por eso al registrar una merma sube el
precio por kilo de lo que sobra, y con él su food cost.

Lo que queda escrito de cada merma: el lote y su serial, el despiece del que
salió, los kilos, las piezas, el motivo y quién lo tiró.
"""
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from thegrill.models import (Alert, AlertSeverity, Ingredient, IngredientLot,
                             IngredientMovement, MovementKind, User)
from thegrill.web import costing, service
from thegrill.web.i18n import t

EPSILON = 1e-9


class WasteError(ValueError):
    """La merma no se puede registrar tal y como está."""


@dataclass
class WasteResult:
    serial: str | None
    ingredient: str
    tg: str | None                 # el despiece del que salió, si viene de uno
    kg: float
    pieces: int | None
    cost: float                    # lo que se ha tirado, en dinero
    unit_cost_before: float
    unit_cost_after: float
    remaining_kg: float
    absorbed: bool                 # si lo que queda ha asumido el coste
    alert: Alert | None = None

    @property
    def cost_increase_pct(self) -> float | None:
        """Cuánto sube el coste por kilo de lo que queda, y con él su food cost."""
        if not self.absorbed or self.unit_cost_before <= EPSILON:
            return None
        return round((self.unit_cost_after - self.unit_cost_before)
                     / self.unit_cost_before * 100, 2)


def record(session: Session, user: User, kg: float, serial: str | None = None,
           ingredient_id: int | None = None, pieces: int | None = None,
           reason: str | None = None, on: date | None = None,
           absorb: bool = True, lang: str | None = None) -> WasteResult:
    """Registra una merma y reparte su coste entre lo que queda del lote.

    Se indica el serial de la pieza, o el ingrediente si no lleva serial, en
    cuyo caso se tira del lote que toque por rotación.
    """
    if kg <= 0:
        raise WasteError("Los kilos tirados tienen que ser mayores que cero")
    if pieces is not None and pieces < 0:
        raise WasteError("Las piezas no pueden ser negativas")
    on = on or date.today()
    lang = lang or service.restaurant_language(session, user.restaurant_id)

    lot = _find_lot(session, user, serial, ingredient_id)
    if kg > lot.qty_remaining + EPSILON:
        raise WasteError(
            f"Se quieren tirar {kg:.10g} y del lote {lot.serial or lot.id} solo "
            f"quedan {lot.qty_remaining:.10g}. Una merma no puede dejar el stock en negativo.")

    ingredient = session.get(Ingredient, lot.ingredient_id)
    before = lot.unit_cost
    value = round(lot.qty_remaining * lot.unit_cost, 6)   # lo que valía el lote entero
    thrown = round(kg * lot.unit_cost, 6)
    lot.qty_remaining = round(lot.qty_remaining - kg, 6)

    absorbed = False
    if absorb and lot.qty_remaining > EPSILON:
        # El coste de lo tirado se queda en lo que sobra: sube su precio por kilo.
        lot.unit_cost = round(value / lot.qty_remaining, 6)
        absorbed = True

    session.add(IngredientMovement(
        restaurant_id=user.restaurant_id, ingredient_id=lot.ingredient_id, lot_id=lot.id,
        date=on, kind=MovementKind.WASTE, qty=-kg, cost=thrown, source="waste",
        source_ref=_ref(lot, pieces, reason), created_by=user.id))
    session.flush()

    result = WasteResult(
        serial=lot.serial, ingredient=ingredient.name if ingredient else "",
        tg=lot.lot_code, kg=round(kg, 6), pieces=pieces, cost=thrown,
        unit_cost_before=before, unit_cost_after=lot.unit_cost,
        remaining_kg=lot.qty_remaining, absorbed=absorbed)
    _announce(session, user, result, lang)
    return result


def _find_lot(session: Session, user: User, serial: str | None,
              ingredient_id: int | None) -> IngredientLot:
    if serial:
        lot = (session.query(IngredientLot)
               .filter_by(restaurant_id=user.restaurant_id, serial=serial.strip()).first())
        if lot is None:
            raise WasteError(f"No hay ninguna pieza con el serial {serial}")
        if lot.qty_remaining <= EPSILON:
            raise WasteError(f"Del lote {serial} no queda nada que tirar")
        return lot
    if ingredient_id:
        ingredient = session.get(Ingredient, ingredient_id)
        if ingredient is None or ingredient.restaurant_id != user.restaurant_id:
            raise WasteError("Ese ingrediente no es de este restaurante")
        lots = costing.rotation_order(session, user.restaurant_id, ingredient)
        if not lots:
            raise WasteError(f"No queda stock de {ingredient.name}")
        return lots[0]
    raise WasteError("Hay que decir de qué pieza o de qué ingrediente es la merma")


def _ref(lot: IngredientLot, pieces: int | None, reason: str | None) -> str:
    """Todo lo que identifica la merma, en una línea del libro."""
    bits = [lot.lot_code or "", lot.serial or ""]
    if pieces:
        bits.append(f"{pieces} pz")
    if reason:
        bits.append(reason.strip())
    return " · ".join(b for b in bits if b)[:96]


def _announce(session: Session, user: User, result: WasteResult, lang: str) -> None:
    now = datetime.utcnow()
    severity = AlertSeverity.CRITICAL if result.cost >= 50 else AlertSeverity.WARNING
    alert = Alert(restaurant_id=user.restaurant_id, code="waste.meat",
                  message=t(lang, "alert.waste_recorded", ingredient=result.ingredient,
                            kg=f"{result.kg:.10g}", serial=result.serial or "—",
                            cost=f"{result.cost:.2f}"),
                  severity=severity, created_at=now)
    session.add(alert)
    session.flush()
    result.alert = alert
    targets = [uid for uid in service.manager_ids(session, user.restaurant_id) if uid != user.id]
    service.notify(session, user.restaurant_id, targets, title=t(lang, "alert.waste_title"),
                   body=alert.message, severity=severity, alert_id=alert.id, now=now)


def recent(session: Session, restaurant_id: int, days: int = 30) -> list[IngredientMovement]:
    """Las últimas mermas registradas, con su lote, sus kilos y su coste."""
    from datetime import timedelta
    since = date.today() - timedelta(days=days)
    return (session.query(IngredientMovement)
            .filter(IngredientMovement.restaurant_id == restaurant_id,
                    IngredientMovement.kind == MovementKind.WASTE,
                    IngredientMovement.date >= since)
            .order_by(IngredientMovement.date.desc(), IngredientMovement.id.desc())
            .limit(200).all())
