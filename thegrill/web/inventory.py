"""Inventario físico de carne: semanal, mensual o puntual.

Se abre el inventario y el sistema saca la lista de lo que cree tener: cada
corte con su serial y sus kilos, y cada primal sin despiezar. Se cuenta a mano
pieza a pieza. Al cerrarlo:

- lo contado re-ancla el stock, con su movimiento de ajuste valorado,
- lo que no aparece se marca **sospechoso de fantasma**, nunca cortado: un
  primal solo pasa a cortado con el despiece que lo confirma,
- lo que aparece sin estar en el sistema se nombra, no se inventa un lote,
- y lo que quedó sin contar se dice y no se toca. Un parcial no cuadra.
"""
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from thegrill.engine.inventory import (MATCH, NOT_FOUND, OVER, SHORT, UNCOUNTED, UNKNOWN,
                                       Counted, Expected, Summary, reconcile)
from thegrill.models import (Alert, AlertSeverity, CountItemKind, CountPeriod, CountStatus,
                             Ingredient, IngredientLot, IngredientMovement, MeatCount,
                             MeatCountLine, MovementKind, Primal, PrimalStatus, User)
from thegrill.web import service
from thegrill.web.i18n import t

EPSILON = 1e-9


class InventoryError(ValueError):
    """El inventario no se puede abrir o cerrar tal y como está."""


@dataclass
class CloseResult:
    count: MeatCount
    summary: Summary
    adjusted_kg: float = 0.0
    adjusted_value: float = 0.0
    phantoms: list[str] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


# ------------------------------------------------------------- lo esperado
def expected_now(session: Session, restaurant_id: int) -> list[Expected]:
    """Lo que el sistema cree tener en este momento, pieza a pieza."""
    rows: list[Expected] = []
    names = {i.id: i.name for i in session.query(Ingredient)
             .filter_by(restaurant_id=restaurant_id)}
    for lot in (session.query(IngredientLot)
                .filter(IngredientLot.restaurant_id == restaurant_id,
                        IngredientLot.serial.isnot(None),
                        IngredientLot.qty_remaining > EPSILON)):
        rows.append(Expected(serial=lot.serial,
                             label=names.get(lot.ingredient_id, lot.serial),
                             kind=CountItemKind.CUT.value,
                             kg=round(lot.qty_remaining, 6), unit_cost=lot.unit_cost))
    for primal in (session.query(Primal)
                   .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK)):
        rows.append(Expected(serial=primal.serial, label=primal.sku,
                             kind=CountItemKind.PRIMAL.value,
                             kg=round(primal.weight_kg or 0.0, 6),
                             unit_cost=primal.landed_usd_per_kg))
    return sorted(rows, key=lambda e: (e.kind, e.label, e.serial))


# --------------------------------------------------------------- apertura
def open_count(session: Session, user: User, period: CountPeriod = CountPeriod.WEEKLY,
               on: date | None = None, note: str | None = None) -> MeatCount:
    """Abre un inventario con la lista de lo que hay que contar."""
    on = on or date.today()
    already = (session.query(MeatCount)
               .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN).first())
    if already:
        raise InventoryError(f"Ya hay un inventario abierto del {already.date}")

    count = MeatCount(restaurant_id=user.restaurant_id, date=on, period=period,
                      note=note, created_by=user.id)
    for item in expected_now(session, user.restaurant_id):
        count.lines.append(MeatCountLine(
            kind=CountItemKind(item.kind), serial=item.serial, label=item.label,
            expected_kg=item.kg, unit_cost=item.unit_cost))
    session.add(count)
    session.flush()
    return count


def record(session: Session, user: User, count: MeatCount, serial: str, kg: float,
           pieces: int | None = None, note: str | None = None) -> MeatCountLine:
    """Apunta lo contado de una pieza. Si no estaba en la lista, se añade."""
    if count.status != CountStatus.OPEN:
        raise InventoryError("El inventario ya está cerrado")
    if kg < 0:
        raise InventoryError("El peso contado no puede ser negativo")
    line = next((l for l in count.lines if l.serial == serial), None)
    if line is None:
        line = MeatCountLine(count_id=count.id, kind=CountItemKind.CUT, serial=serial,
                             label=serial, expected_kg=0.0)
        count.lines.append(line)
    line.counted_kg = kg
    line.counted_pieces = pieces
    if note:
        line.note = note
    session.flush()
    return line


# ----------------------------------------------------------------- cierre
def close_count(session: Session, user: User, count: MeatCount,
                lang: str | None = None) -> CloseResult:
    """Cierra el inventario: cuadra, re-ancla el stock y avisa de lo que falta."""
    if count.status == CountStatus.CLOSED:
        raise InventoryError("Ese inventario ya estaba cerrado")
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    now = datetime.utcnow()

    # Lo esperado se relee al cerrar: el ajuste tiene que cuadrar contra el
    # estado de ahora, no contra el de cuando se abrió la hoja.
    expected = expected_now(session, user.restaurant_id)
    by_serial = {e.serial: e for e in expected}
    counted = [Counted(l.serial, l.counted_kg, l.counted_pieces)
               for l in count.lines if l.counted_kg is not None]
    summary = reconcile(expected, counted)
    result = CloseResult(count=count, summary=summary)

    lines_by_serial = {l.serial: l for l in count.lines}
    for row in summary.lines:
        line = lines_by_serial.get(row.serial)
        if line is None:
            line = MeatCountLine(count_id=count.id, kind=CountItemKind.CUT, serial=row.serial,
                                 label=row.label, expected_kg=row.expected_kg,
                                 counted_kg=row.counted_kg)
            count.lines.append(line)
        line.expected_kg = row.expected_kg
        line.unit_cost = row.unit_cost
        line.outcome = row.outcome

        if not row.adjusts:
            continue
        item = by_serial.get(row.serial)
        if item is None:
            continue
        if item.kind == CountItemKind.CUT.value:
            _reanchor_cut(session, user, row, count, now)
            result.adjusted_kg = round(result.adjusted_kg + abs(row.gap_kg), 6)
            result.adjusted_value = round(result.adjusted_value + row.gap_value, 4)
        else:
            _flag_primal(session, user, row, result)

    count.status = CountStatus.CLOSED
    count.closed_by = user.id
    count.closed_at = now
    session.flush()
    _raise_alerts(session, user, result, lang, now)
    return result


def _reanchor_cut(session: Session, user: User, row, count: MeatCount, now: datetime) -> None:
    """El conteo manda: el lote pasa a valer lo que se ha pesado."""
    lot = (session.query(IngredientLot)
           .filter_by(restaurant_id=user.restaurant_id, serial=row.serial).first())
    if lot is None:
        return
    difference = round((row.counted_kg or 0.0) - lot.qty_remaining, 6)
    lot.qty_remaining = round(row.counted_kg or 0.0, 6)
    session.add(IngredientMovement(
        restaurant_id=user.restaurant_id, ingredient_id=lot.ingredient_id, lot_id=lot.id,
        date=count.date, kind=MovementKind.ADJUST, qty=difference,
        cost=round(difference * lot.unit_cost, 6), source="count",
        source_ref=f"INV-{count.id} {row.serial}", created_by=user.id))


def _flag_primal(session: Session, user: User, row, result: CloseResult) -> None:
    """Un primal que no aparece queda sospechoso, nunca cortado por inferencia."""
    primal = (session.query(Primal)
              .filter_by(restaurant_id=user.restaurant_id, serial=row.serial).first())
    if primal is None:
        return
    if row.outcome == NOT_FOUND:
        primal.suspect_phantom = True
        result.phantoms.append(primal.serial)
    elif row.counted_kg:
        primal.weight_kg = round(row.counted_kg, 6)


def _raise_alerts(session: Session, user: User, result: CloseResult, lang: str,
                  now: datetime) -> None:
    summary = result.summary
    targets = [uid for uid in service.manager_ids(session, user.restaurant_id) if uid != user.id]

    if not summary.complete:
        pending = [l.serial for l in summary.of(UNCOUNTED)]
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="count.partial",
            message=t(lang, "alert.count_partial", n=len(pending),
                      serials=", ".join(pending[:8])),
            severity=AlertSeverity.WARNING, created_at=now))
    if summary.shrink_kg > EPSILON:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="count.shrink",
            message=t(lang, "alert.count_shrink", kg=f"{summary.shrink_kg:.10g}",
                      value=f"{summary.shrink_value:.2f}"),
            severity=AlertSeverity.CRITICAL, created_at=now))
    if result.phantoms:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="count.phantom",
            message=t(lang, "alert.count_phantom", serials=", ".join(result.phantoms)),
            severity=AlertSeverity.CRITICAL, created_at=now))
    unknown = summary.of(UNKNOWN)
    if unknown:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="count.unknown",
            message=t(lang, "alert.count_unknown",
                      serials=", ".join(l.serial for l in unknown)),
            severity=AlertSeverity.WARNING, created_at=now))

    for alert in result.alerts:
        session.add(alert)
    session.flush()
    for alert in result.alerts:
        service.notify(session, user.restaurant_id, targets,
                       title=t(lang, "alert.count_title"), body=alert.message,
                       severity=alert.severity, alert_id=alert.id, now=now)


def last_closed(session: Session, restaurant_id: int) -> MeatCount | None:
    return (session.query(MeatCount)
            .filter_by(restaurant_id=restaurant_id, status=CountStatus.CLOSED)
            .order_by(MeatCount.date.desc(), MeatCount.id.desc()).first())


def is_overdue(session: Session, restaurant_id: int, on: date | None = None,
               max_age_days: int = 8) -> bool:
    """Un conteo vencido deja de sostener el stock: hay que rehacerlo."""
    from thegrill.rules import weekly_count_is_stale
    last = last_closed(session, restaurant_id)
    return weekly_count_is_stale(last.date if last else None, on or date.today(), max_age_days)
