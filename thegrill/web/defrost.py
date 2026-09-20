"""Descongelado: apuntes, recuento de cierre y cuadre del turno.

Lo que la cocina hace de verdad con la carne al corte:

1. Se saca una pieza del congelador a descongelar y se apunta su serial, las
   piezas y el peso total.
2. Al acabar el turno se cuenta lo que queda descongelado, con sus seriales y
   su peso.
3. La diferencia es lo consumido. Como va por serial, se sabe qué pieza se
   gastó; y cruzado con lo vendido en el POS sale el peso real por pieza.

Ese peso real, comparado con el teórico del escandallo, es lo que dice si se
está cortando de más.
"""
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import and_
from sqlalchemy.orm import Session

from thegrill import config
from thegrill.engine.defrost import Consumed, SerialState, Variance, reconcile, variances
from thegrill.engine.recipes import explode
from thegrill.models import (Alert, AlertSeverity, ConsumptionMode, DefrostEntry, DefrostKind,
                             Ingredient, IngredientLot, IngredientMovement, MovementKind,
                             SalesByProduct, User)
from thegrill.web import costing, service
from thegrill.web.i18n import t

EPSILON = 1e-6


class DefrostError(ValueError):
    """El apunte de descongelado no se puede registrar tal y como está."""


@dataclass
class ShiftClose:
    date: date
    shift: str
    consumed: list[Consumed] = field(default_factory=list)
    variances: list[Variance] = field(default_factory=list)
    cost: float = 0.0                 # lo que se ha gastado de verdad, en dinero
    missing_counts: list[str] = field(default_factory=list)
    not_by_count: list[str] = field(default_factory=list)   # cortes que descuentan al vender
    alerts: list[Alert] = field(default_factory=list)

    @property
    def loss_cost(self) -> float:
        """Lo que se pierde en el día: el desvío contra la carta, en dinero."""
        return round(sum(v.loss_cost for v in self.variances), 4)

    @property
    def loss_kg(self) -> float:
        return round(sum(v.gap_kg for v in self.variances), 6)

    @property
    def piece_gaps(self) -> list[Consumed]:
        """Piezas que faltan y las ventas no explican."""
        return [c for c in self.consumed if c.counted and c.piece_gap]


# ------------------------------------------------------------------ apuntes
def _lot_by_serial(session: Session, restaurant_id: int, serial: str) -> IngredientLot:
    lot = (session.query(IngredientLot)
           .filter_by(restaurant_id=restaurant_id, serial=serial).first())
    if lot is None:
        raise DefrostError(f"No hay ninguna pieza con el serial {serial}")
    return lot


def record(session: Session, user: User, kind: DefrostKind, serial: str, pieces: int,
           total_kg: float, on: date | None = None, shift: str = "",
           note: str | None = None) -> DefrostEntry:
    """Apunta una salida a descongelar o un recuento de cierre."""
    if pieces < 0 or total_kg < 0:
        raise DefrostError("Ni las piezas ni el peso pueden ser negativos")
    lot = _lot_by_serial(session, user.restaurant_id, serial)
    entry = DefrostEntry(restaurant_id=user.restaurant_id, date=on or date.today(),
                         shift=shift or "", kind=kind, lot_serial=serial,
                         ingredient_id=lot.ingredient_id, lot_id=lot.id, pieces=pieces,
                         total_kg=total_kg, note=note, created_by=user.id)
    session.add(entry)
    session.flush()
    return entry


def intake(session: Session, user: User, serial: str, pieces: int, total_kg: float,
           on: date | None = None, shift: str = "", note: str | None = None) -> DefrostEntry:
    return record(session, user, DefrostKind.INTAKE, serial, pieces, total_kg, on, shift, note)


def count(session: Session, user: User, serial: str, pieces: int, total_kg: float,
          on: date | None = None, shift: str = "", note: str | None = None) -> DefrostEntry:
    return record(session, user, DefrostKind.COUNT, serial, pieces, total_kg, on, shift, note)


# ------------------------------------------------------------------ cuadre
def _previous_count(session: Session, restaurant_id: int, serial: str,
                    on: date, shift: str) -> DefrostEntry | None:
    """El último recuento de esa pieza antes de este turno: lo que había."""
    return (session.query(DefrostEntry)
            .filter(DefrostEntry.restaurant_id == restaurant_id,
                    DefrostEntry.lot_serial == serial,
                    DefrostEntry.kind == DefrostKind.COUNT,
                    (DefrostEntry.date < on) |
                    and_(DefrostEntry.date == on, DefrostEntry.shift < shift))
            .order_by(DefrostEntry.date.desc(), DefrostEntry.shift.desc(),
                      DefrostEntry.id.desc()).first())


def sold_units(session: Session, restaurant_id: int, on: date) -> dict[int, int]:
    """Unidades vendidas en el POS ese día, por ingrediente."""
    return theoretical_for(session, restaurant_id, on)[1]


def shift_states(session: Session, restaurant_id: int, on: date, shift: str = "") -> list[SerialState]:
    """Lo que había, lo que se sacó, lo que el POS ha vendido y lo que queda.

    Lo vendido se reparte entre las piezas que están descongeladas, en orden:
    así el recuento de cierre se hace contra un número, no contra el aire.
    """
    entries = (session.query(DefrostEntry)
               .filter_by(restaurant_id=restaurant_id, date=on, shift=shift or "").all())
    by_ingredient: dict[str, int] = {}
    states: dict[str, SerialState] = {}
    for entry in entries:
        by_ingredient.setdefault(entry.lot_serial, entry.ingredient_id)
    for entry in entries:
        st = states.get(entry.lot_serial)
        if st is None:
            previous = _previous_count(session, restaurant_id, entry.lot_serial, on, shift or "")
            st = SerialState(serial=entry.lot_serial,
                             ingredient=entry.ingredient.name if entry.ingredient else "",
                             opening_kg=previous.total_kg if previous else 0.0,
                             opening_pieces=previous.pieces if previous else 0)
            states[entry.lot_serial] = st
        if entry.kind == DefrostKind.INTAKE:
            st.intake_kg = round(st.intake_kg + entry.total_kg, 6)
            st.intake_pieces += entry.pieces
        else:
            st.closing_kg = entry.total_kg
            st.closing_pieces = entry.pieces

    _attribute_sales(session, restaurant_id, on, states, by_ingredient)
    return [states[k] for k in sorted(states)]


def _attribute_sales(session: Session, restaurant_id: int, on: date,
                     states: dict[str, SerialState], ingredient_of: dict[str, int]) -> None:
    """Reparte lo vendido entre las piezas descongeladas de ese corte.

    En orden de serial y sin pasarse de lo que cada pieza tenía fuera: una
    pieza no puede vender más de lo que había descongelado de ella.
    """
    pending = dict(sold_units(session, restaurant_id, on))
    for serial in sorted(states):
        state = states[serial]
        ingredient_id = ingredient_of.get(serial)
        left = pending.get(ingredient_id, 0)
        if not left:
            continue
        take = min(left, state.out_pieces)
        state.sold_pieces = take
        pending[ingredient_id] = left - take


def theoretical_for(session: Session, restaurant_id: int, on: date) -> tuple[dict[int, float], dict[int, int]]:
    """Lo que las recetas dicen que debería haberse gastado, por lo vendido."""
    index = costing.pos_index(session, restaurant_id)
    needed: dict[int, float] = {}
    units: dict[int, int] = {}
    for line in (session.query(SalesByProduct)
                 .filter_by(restaurant_id=restaurant_id, op_date=on)):
        product = index.get(" ".join(line.pos_name.strip().upper().split()))
        if product is None or product.recipe is None:
            continue
        for ingredient_id, qty in explode(product.recipe, line.units).items():
            needed[ingredient_id] = round(needed.get(ingredient_id, 0.0) + qty, 6)
            units[ingredient_id] = units.get(ingredient_id, 0) + int(line.units)
    return needed, units


def close(session: Session, user: User, on: date | None = None, shift: str = "",
          lang: str | None = None) -> ShiftClose:
    """Cierra el turno: descuenta lo consumido de verdad y lo compara con lo teórico."""
    on = on or date.today()
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    result = ShiftClose(date=on, shift=shift or "")

    states = shift_states(session, user.restaurant_id, on, shift)
    result.consumed = reconcile(states)

    real_by_name: dict[str, float] = {}
    units_by_name: dict[str, int] = {}
    cost_by_name: dict[str, float] = {}
    for row in result.consumed:
        if not row.counted:
            result.missing_counts.append(row.serial)
            continue
        if row.kg <= EPSILON:
            continue
        lot = _lot_by_serial(session, user.restaurant_id, row.serial)
        ingredient = session.get(Ingredient, lot.ingredient_id)
        if ingredient is not None and ingredient.consumption != ConsumptionMode.COUNT:
            # Ese corte ya se descuenta al vender. Descontarlo otra vez aquí
            # sería gastar dos veces la misma carne.
            result.not_by_count.append(row.serial)
            continue
        cost_by_name.setdefault(row.ingredient, lot.unit_cost)
        take = min(row.kg, lot.qty_remaining)
        lot.qty_remaining = round(lot.qty_remaining - take, 6)
        cost = round(take * lot.unit_cost, 6)
        result.cost = round(result.cost + cost, 6)
        session.add(IngredientMovement(
            restaurant_id=user.restaurant_id, ingredient_id=lot.ingredient_id, lot_id=lot.id,
            date=on, kind=MovementKind.SALE, qty=-take, cost=cost, source="defrost",
            source_ref=f"{row.serial} {shift or ''}".strip(), created_by=user.id))
        real_by_name[row.ingredient] = round(real_by_name.get(row.ingredient, 0.0) + row.kg, 6)

    # comparación con lo que dicen las recetas de lo vendido ese día
    needed, units = theoretical_for(session, user.restaurant_id, on)
    names = {i.id: i.name for i in session.query(Ingredient)
             .filter_by(restaurant_id=user.restaurant_id,
                        consumption=ConsumptionMode.COUNT)}
    theoretical_by_name = {names[k]: v for k, v in needed.items() if k in names}
    for ingredient_id, name in names.items():
        units_by_name[name] = units.get(ingredient_id, 0)
    result.variances = variances(real_by_name, theoretical_by_name, units_by_name,
                                 cost_by_name)

    session.flush()
    _raise_alerts(session, user, result, lang)
    return result


def _raise_alerts(session: Session, user: User, result: ShiftClose, lang: str) -> None:
    now = datetime.utcnow()
    targets = [uid for uid in service.manager_ids(session, user.restaurant_id) if uid != user.id]

    for row in result.consumed:
        if row.impossible:
            result.alerts.append(Alert(
                restaurant_id=user.restaurant_id, code="defrost.impossible",
                message=t(lang, "alert.defrost_impossible", serial=row.serial),
                severity=AlertSeverity.WARNING, created_at=now))
    for row in result.piece_gaps:
        # El POS dice lo que salió a la mesa; la cámara, lo que falta. Si no
        # cuadran, alguien tiene que mirarlo hoy, no a fin de mes.
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="defrost.pieces",
            message=t(lang, "alert.defrost_pieces", n=row.piece_gap,
                      ingredient=row.ingredient, serial=row.serial,
                      sold=row.sold_pieces, out=row.pieces),
            severity=AlertSeverity.WARNING, created_at=now))
    if result.not_by_count:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="defrost.not_by_count",
            message=t(lang, "alert.defrost_not_by_count",
                      serials=", ".join(result.not_by_count)),
            severity=AlertSeverity.WARNING, created_at=now))
    if abs(result.loss_cost) >= 1:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="defrost.loss",
            message=t(lang, "alert.defrost_loss", cost=f"{result.loss_cost:+.2f}"),
            severity=(AlertSeverity.CRITICAL if result.loss_cost >= 50
                      else AlertSeverity.WARNING), created_at=now))
    if result.missing_counts:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="defrost.missing_count",
            message=t(lang, "alert.defrost_missing", serials=", ".join(result.missing_counts)),
            severity=AlertSeverity.WARNING, created_at=now))
    for gap in result.variances:
        pct = gap.gap_pct
        if pct is not None and abs(pct) >= config.PORTION_VARIANCE_PCT:
            result.alerts.append(Alert(
                restaurant_id=user.restaurant_id, code="portion.variance",
                message=t(lang, "alert.portion_variance", ingredient=gap.ingredient,
                          pct=f"{pct:+.1f}", real=f"{gap.real_g_per_unit or 0:.0f}",
                          theoretical=f"{gap.theoretical_g_per_unit or 0:.0f}"),
                severity=AlertSeverity.WARNING, created_at=now))
    for alert in result.alerts:
        session.add(alert)
    session.flush()
    for alert in result.alerts:
        service.notify(session, user.restaurant_id, targets,
                       title=t(lang, "alert.defrost_title"), body=alert.message,
                       severity=alert.severity, alert_id=alert.id, now=now)
