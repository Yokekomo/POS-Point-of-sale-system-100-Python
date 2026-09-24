"""[01250] Inventario físico de carne: semanal, mensual o puntual.

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

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thegrill.engine.inventory import (MATCH, NOT_FOUND, OVER, SHORT, UNCOUNTED, UNKNOWN,
                                       Counted, Expected, Summary, reconcile)
from thegrill.models import (Alert, AlertSeverity, CountItemKind, CountPeriod, CountStatus,
                             Ingredient, IngredientLot, IngredientMovement, MeatCount,
                             MeatCountLine, MovementKind, Primal, PrimalStatus, Storage, User)
from thegrill.web import aging, costing, jornada, locking, rangos, service, sites
from thegrill.web.i18n import t

EPSILON = 1e-9


class InventoryError(ValueError):
    """[01251] El inventario no se puede abrir o cerrar tal y como está."""


@dataclass
class CloseResult:
    count: MeatCount
    summary: Summary
    adjusted_kg: float = 0.0
    adjusted_value: float = 0.0
    phantoms: list[str] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    weighings: list = field(default_factory=list)   # piezas que maduran, repesadas

    @property
    def aging_kg(self) -> float:
        """[01273] El agua que se han dejado las piezas que maduran. No es carne que falte."""
        return round(sum(w.loss_kg for w in self.weighings), 4)


# ------------------------------------------------------------- lo esperado
def expected_now(session: Session, restaurant_id: int,
                 site_id: int | None = None) -> list[Expected]:
    """[01252] Lo que el sistema cree tener en este momento, pieza a pieza.

    Con sede, lo de esa cámara. Contar a la vez el obrador y el local no cuadra
    nada: nadie pesa dos cámaras que están a veinte kilómetros, y lo que no se
    mira sale como faltante.
    """
    rows: list[Expected] = []
    names = {i.id: i.name for i in session.query(Ingredient)
             .filter_by(restaurant_id=restaurant_id)}
    principal = sites.main(session, restaurant_id).id if site_id else None
    for lot in costing.at_site(
            session.query(IngredientLot)
            .filter(IngredientLot.restaurant_id == restaurant_id,
                    IngredientLot.serial.isnot(None),
                    IngredientLot.qty_remaining > EPSILON),
            session, restaurant_id, site_id):
        rows.append(Expected(serial=lot.serial,
                             label=names.get(lot.ingredient_id, lot.serial),
                             kind=CountItemKind.CUT.value,
                             kg=round(lot.qty_remaining, 6), unit_cost=lot.unit_cost))
    for primal in (session.query(Primal)
                   .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK)):
        if site_id and (primal.site_id or principal) != site_id:
            continue
        rows.append(Expected(serial=primal.serial, label=primal.sku,
                             kind=CountItemKind.PRIMAL.value,
                             kg=round(primal.weight_kg or 0.0, 6),
                             unit_cost=primal.landed_usd_per_kg))
    return sorted(rows, key=lambda e: (e.kind, e.label, e.serial))


# --------------------------------------------------------------- apertura
def open_count(session: Session, user: User, period: CountPeriod = CountPeriod.MONTHLY,
               on: date | None = None, note: str | None = None,
               site_id: int | None = None) -> MeatCount:
    """[01253] Abre un inventario con la lista de lo que hay que contar.

    Cada sede cuenta su cámara, y las dos pueden estar contando a la vez: lo
    que no se puede es tener dos hojas abiertas de la misma cámara.
    """
    on = on or jornada.del_usuario(session, user)
    if site_id is None:
        mia = sites.of_user(session, user)
        site_id = mia.id if mia else None
    else:
        # [01275] La sede viene del formulario, así que hay que preguntar de quién es.
        # Sin esto, una casa abría la hoja colgada de la cámara de otra —y lo
        # peor no era el cruce: al cerrar no encontraba nada que ajustar, así
        # que se contaba la cámara entera, no saltaba ningún error y no se
        # movía un kilo.
        site_id = sites._site(session, user, site_id).id
    already = (session.query(MeatCount)
               .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN,
                          site_id=site_id).first())
    if already:
        raise InventoryError(f"Ya hay un inventario abierto del {already.date}")

    count = MeatCount(restaurant_id=user.restaurant_id, date=on, period=period,
                      note=note, created_by=user.id, site_id=site_id,
                      open_key=site_id or 0)
    for item in expected_now(session, user.restaurant_id, site_id):
        count.lines.append(MeatCountLine(
            kind=CountItemKind(item.kind), serial=item.serial, label=item.label,
            expected_kg=item.kg, unit_cost=item.unit_cost))
    session.add(count)
    try:
        session.flush()
    except IntegrityError:
        # [01276] Dos encargados le han dado a abrir en el mismo segundo. Se deshace lo
        # de aquí —que no es más que una lista— y se cuenta en la que ya está.
        session.rollback()
        raise InventoryError("Otra persona acaba de abrir el inventario de esta cámara") \
            from None
    return count


def record(session: Session, user: User, count: MeatCount, serial: str, kg: float,
           pieces: int | None = None, note: str | None = None,
           lang: str | None = None) -> MeatCountLine:
    """[01254] Apunta lo contado de una pieza. Si no estaba en la lista, se añade.

    Una cámara grande se cuenta entre dos, y los dos escriben en la misma hoja
    desde su móvil. Eso está bien: lo que no puede pasar es que el segundo pise
    al primero sin que nadie se entere. Si la pieza ya estaba contada por otra
    persona y no les da lo mismo, se guarda el último —el que está delante de
    la pieza ahora— pero la línea queda marcada y con los dos números escritos.
    Al cerrar se avisa, porque una pieza en discusión no es una pieza contada.
    """
    if count.status != CountStatus.OPEN:
        raise InventoryError("El inventario ya está cerrado")
    if kg < 0:
        raise InventoryError("El peso contado no puede ser negativo")
    rangos.peso_corte(kg, lang or "es")
    line = _linea(session, count, serial)

    # [01277] Guardar la hoja manda **todo** lo que hay en la pantalla, no solo lo que
    # uno acaba de escribir. Si el número que llega es el mismo que ya estaba,
    # no se toca nada: ni cambia de dueño ni hay discusión. Si no, la columna
    # «quién» diría que la contó el último que le dio a guardar.
    if (line.counted_kg is not None and abs(line.counted_kg - kg) <= EPSILON
            and line.counted_pieces == pieces and not note):
        return line

    # [01278] Lo que había cuando miramos. Se guarda aparte porque hay que poder
    # preguntarle a la base si sigue siendo eso a la hora de escribir.
    visto = line.counted_at
    _decir_si_hay_lio(session, line, user, kg, lang)

    ahora = datetime.utcnow()
    valores = {"counted_kg": kg, "counted_pieces": pieces, "counted_by": user.id,
               "counted_at": ahora, "disputed": line.disputed, "note": line.note}
    if note:
        valores["note"] = (" · ".join(x for x in (line.note, note) if x)[:1000]
                           if line.disputed else note)

    # [01279] Y aquí está lo que costó encontrar: cuatro personas contando la misma
    # pieza a la vez leían las cuatro la línea **sin contar**, así que ninguna
    # veía a nadie con quien discutir. Se guardaban los cuatro números encima
    # del anterior y la hoja quedaba diciendo que la contó uno solo, limpio. No
    # es que se perdiera el aviso: es que el inventario mentía justo en el caso
    # para el que se escribió el aviso.
    #
    # Mirar antes no sirve —entre mirar y escribir cabe otra persona—, así que
    # se pregunta al escribir: «cámbialo solo si sigue como lo vi». Lo resuelve
    # la base dentro de su propio candado, y de dos que lo intenten a la vez se
    # lo lleva exactamente una.
    if not locking.claim(session, MeatCountLine, line.id, {"counted_at": visto},
                         valores):
        # [01280] Perdimos: alguien escribió entre nuestra lectura y la nuestra. Se
        # relee lo que dejó, se mira si hay que decir algo —casi siempre sí— y
        # se escribe encima, que es lo pactado: manda el último, el que está
        # delante de la pieza ahora. Pero ya no en silencio.
        session.expire(line)
        _decir_si_hay_lio(session, line, user, kg, lang)
        valores["disputed"] = line.disputed
        valores["note"] = ((" · ".join(x for x in (line.note, note) if x)[:1000]
                            if note and line.disputed else (note or line.note)))
        session.query(MeatCountLine).filter_by(id=line.id).update(
            valores, synchronize_session=False)
        session.expire(line)
    session.flush()
    return line


def _linea(session: Session, count: MeatCount, serial: str) -> MeatCountLine:
    """[01255] La línea de esa pieza en la hoja; si no estaba, se añade.

    Una pieza que aparece en la cámara y no estaba en la lista la pueden
    apuntar dos personas en el mismo segundo, y la hoja no admite dos líneas
    con el mismo número. Sin red, la segunda reventaba con un fallo de la base
    —un 500 en la cara, y su recuento a la basura— en vez de entender lo
    único que había pasado: que llegó segunda. Se intenta dentro de un punto
    de retorno, y si choca, se deshace solo eso y se sigue con la línea que
    creó el otro, que es la misma pieza.
    """
    line = next((l for l in count.lines if l.serial == serial), None)
    if line is not None:
        return line
    nueva = MeatCountLine(count_id=count.id, kind=CountItemKind.CUT, serial=serial,
                          label=serial, expected_kg=0.0)
    try:
        with session.begin_nested():
            # [01281] Va colgada de la hoja, no suelta en la sesión: el cierre recorre
            # `count.lines`, y una línea que se guarda pero no entra en esa
            # lista queda escrita en la base y fuera del cuadre. La pieza que
            # aparece en la cámara es justo la que no puede perderse.
            count.lines.append(nueva)
            session.flush()
    except IntegrityError:
        # [01282] La creó otro en el mismo segundo. Se deshace solo esto y se sigue con
        # la suya, que es la misma pieza; la hoja se relee para que la lista no
        # se quede con la línea que no llegó a existir.
        session.expire(count, ["lines"])
        ya = (session.query(MeatCountLine)
              .filter_by(count_id=count.id, serial=serial).one_or_none())
        if ya is None:                                   # pragma: no cover
            raise InventoryError("Vuelve a intentarlo: esa pieza se está "
                                 "apuntando ahora mismo") from None
        return ya
    return nueva


def _decir_si_hay_lio(session: Session, line: MeatCountLine, user: User,
                      kg: float, lang: str | None) -> None:
    """[01256] Si otra persona ya contó esta pieza y no les da lo mismo, queda escrito.

    No se decide nada aquí sobre qué número manda —eso está pactado: manda el
    último—, solo que la discusión no se pierda. Una pieza en discusión no es
    una pieza contada, y al cerrar se avisa.
    """
    otro = (line.counted_by is not None and line.counted_by != user.id
            and line.counted_kg is not None)
    if not (otro and abs((line.counted_kg or 0.0) - kg) > EPSILON):
        return
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    quien = session.get(User, line.counted_by)
    aviso = t(lang, "inv.clash", who=(quien.name if quien else "?"),
              kg=f"{line.counted_kg:.10g}",
              when=(line.counted_at or datetime.utcnow()).strftime("%H:%M"))
    line.note = " · ".join(x for x in (line.note, aviso) if x)[:1000]
    line.disputed = True


# ----------------------------------------------------------------- cierre
def close_count(session: Session, user: User, count: MeatCount,
                lang: str | None = None) -> CloseResult:
    """[01257] Cierra el inventario: cuadra, re-ancla el stock y avisa de lo que falta."""
    if count.status == CountStatus.CLOSED:
        raise InventoryError("Ese inventario ya estaba cerrado")
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    now = datetime.utcnow()

    # [01283] La hoja se coge antes de ajustar nada. Dos personas dándole a cerrar a la
    # vez —el encargado desde el móvil y el jefe desde el ordenador— escribían
    # los dos el mismo ajuste: los kilos quedaban bien, porque el segundo
    # re-ancla sobre lo mismo, pero el libro se llevaba dos apuntes de dinero
    # por la misma merma y el mes salía el doble de malo de lo que fue.
    if not locking.claim(session, MeatCount, count.id, {"status": CountStatus.OPEN},
                         {"status": CountStatus.CLOSED, "closed_by": user.id,
                          "closed_at": now, "open_key": None}):
        raise InventoryError(t(lang, "inv.closed_by_other"))

    # [01284] Lo esperado se relee al cerrar: el ajuste tiene que cuadrar contra el
    # estado de ahora, no contra el de cuando se abrió la hoja.
    expected = expected_now(session, user.restaurant_id, count.site_id)
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
            _flag_primal(session, user, row, result, count=count, lang=lang)

    count.complete = summary.complete
    session.flush()
    _raise_alerts(session, user, result, lang, now)
    return result


def _reanchor_cut(session: Session, user: User, row, count: MeatCount, now: datetime) -> None:
    """[01258] El conteo manda: el lote pasa a valer lo que se ha pesado."""
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


def _flag_primal(session: Session, user: User, row, result: CloseResult,
                 count: MeatCount | None = None, lang: str | None = None) -> None:
    """[01259] Un primal que no aparece queda sospechoso, nunca cortado por inferencia.

    Y si la pieza estaba madurando, pesarla en el inventario es una pesada como
    cualquier otra: lo que ha perdido es agua, no carne que falte, así que se
    apunta como tal y el coste se queda en los kilos que quedan.
    """
    primal = (session.query(Primal)
              .filter_by(restaurant_id=user.restaurant_id, serial=row.serial).first())
    if primal is None:
        return
    if row.outcome == NOT_FOUND:
        primal.suspect_phantom = True
        result.phantoms.append(primal.serial)
    elif row.counted_kg:
        if aging.where(primal) != Storage.CHILLED:
            try:
                weighed = aging.weigh(session, user, primal.serial, row.counted_kg,
                                      on=count.date if count else None, source="count",
                                      lang=lang)
                result.weighings.append(weighed)
                return
            except aging.AgingError:
                pass      # pesa más que antes o ya no está en stock: se trata como antes
        primal.weight_kg = round(row.counted_kg, 6)


def _raise_alerts(session: Session, user: User, result: CloseResult, lang: str,
                  now: datetime) -> None:
    """[01260] Los avisos que deja un inventario cerrado, y a quién le llegan.

    Tres cosas distintas: lo que dos personas contaron distinto —manda el
    último número, pero hay que volver a mirarlo—, lo que no llegó a contarse,
    y lo que falta. Le llegan a quien lleva la casa y no a quien contó: quien
    cuenta ya sabe lo que contó.
    """
    summary = result.summary
    targets = [uid for uid in service.manager_ids(session, user.restaurant_id) if uid != user.id]

    # [01285] Lo que contaron dos personas y no les dio lo mismo. El número que manda
    # es el último, pero el jefe se entera de cuáles hay que volver a mirar.
    disputed = [l.serial for l in result.count.lines if l.disputed]
    if disputed:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="count.disputed",
            message=t(lang, "alert.count_disputed", n=len(disputed),
                      serials=", ".join(disputed[:8])),
            severity=AlertSeverity.WARNING, created_at=now))

    if not summary.complete:
        pending = [l.serial for l in summary.of(UNCOUNTED)]
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="count.partial",
            message=t(lang, "alert.count_partial", n=len(pending),
                      serials=", ".join(pending[:8])),
            severity=AlertSeverity.WARNING, created_at=now))
    # [01286] Lo que se han dejado madurando es agua, y el dinero sigue en la pieza: no
    # entra en lo que falta. Lo que quede después de descontarlo, sí.
    aging_kg = result.aging_kg
    aging_value = round(sum(w.loss_kg * (w.cost_per_kg_before or 0.0)
                            for w in result.weighings), 2)
    missing_kg = round(summary.shrink_kg - aging_kg, 4)
    missing_value = round(summary.shrink_value - aging_value, 2)
    if aging_kg > EPSILON:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="count.aging",
            message=t(lang, "alert.count_aging", kg=f"{aging_kg:.10g}",
                      n=len(result.weighings)),
            severity=AlertSeverity.INFO, created_at=now))
    if missing_kg > EPSILON:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="count.shrink",
            message=t(lang, "alert.count_shrink", kg=f"{missing_kg:.10g}",
                      value=f"{max(0.0, missing_value):.2f}"),
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


def cancel_count(session: Session, user: User, count: MeatCount,
                 reason: str | None = None) -> MeatCount:
    """[01261] Cancela un inventario a medias. No ajusta nada y no cuenta para el mes."""
    if count.status != CountStatus.OPEN:
        raise InventoryError("Solo se puede cancelar un inventario abierto")
    if not locking.claim(session, MeatCount, count.id, {"status": CountStatus.OPEN},
                         {"status": CountStatus.CANCELLED, "closed_by": user.id,
                          "closed_at": datetime.utcnow(), "open_key": None}):
        raise InventoryError("Otra persona acaba de cerrar o cancelar este inventario")
    count.cancel_reason = (reason or "").strip() or None
    count.complete = False
    session.flush()
    return count


def last_closed(session: Session, restaurant_id: int,
                site_id: int | None = None) -> MeatCount | None:
    """[01262] El último inventario cerrado, de la casa o de una sede."""
    query = (session.query(MeatCount)
             .filter_by(restaurant_id=restaurant_id, status=CountStatus.CLOSED))
    if site_id:
        query = query.filter(MeatCount.site_id == site_id)
    return query.order_by(MeatCount.date.desc(), MeatCount.id.desc()).first()


def open_now(session: Session, restaurant_id: int,
             site_id: int | None = None) -> MeatCount | None:
    """[01263] La hoja abierta de esa cámara, si la hay."""
    query = (session.query(MeatCount)
             .filter_by(restaurant_id=restaurant_id, status=CountStatus.OPEN))
    if site_id:
        query = query.filter(MeatCount.site_id == site_id)
    return query.order_by(MeatCount.id.desc()).first()


@dataclass
class MonthlyStatus:
    """[01264] La única obligación: un inventario completo dentro del mes natural."""
    year: int
    month: int
    done: bool
    last_date: date | None
    days_left: int

    @property
    def due_soon(self) -> bool:
        """[01274] Si el inventario del mes está encima y todavía no se ha hecho."""
        return not self.done and self.days_left <= 5


def monthly_status(session: Session, restaurant_id: int, on: date | None = None,
                   site_id: int | None = None) -> MonthlyStatus:
    """[01265] Si el inventario obligatorio del mes ya está hecho, y cuántos días quedan.

    Lo cumple un inventario cerrado y completo. Uno parcial no cuenta, por lo
    mismo que no cuadra: quedaron piezas sin mirar.
    """
    import calendar
    on = on or jornada.hoy(session, restaurant_id)
    first = date(on.year, on.month, 1)
    last_day = calendar.monthrange(on.year, on.month)[1]
    query = (session.query(MeatCount)
             .filter(MeatCount.restaurant_id == restaurant_id,
                     MeatCount.status == CountStatus.CLOSED,
                     MeatCount.complete.is_(True),
                     MeatCount.date >= first,
                     MeatCount.date <= date(on.year, on.month, last_day)))
    if site_id:
        query = query.filter(MeatCount.site_id == site_id)
    done = query.order_by(MeatCount.date.desc()).first()
    return MonthlyStatus(year=on.year, month=on.month, done=done is not None,
                         last_date=done.date if done else None,
                         days_left=last_day - on.day)


# ================================================== cuando la pieza aparece
@dataclass
class Recovery:
    """[01266] Una pieza que se había dado por perdida y ha aparecido."""
    serial: str
    kind: str                # CUT o PRIMAL
    label: str
    kg: float
    restored_kg: float = 0.0
    value: float = 0.0
    adopted: bool = False    # el sistema no la tenía y se ha dado de alta


def _audit(session: Session, user: User, table: str, key: str, action: str, detail: str) -> None:
    """[01267] Deja escrito quién tocó qué y por qué. No se borra."""
    from thegrill.models import AuditLog
    session.add(AuditLog(restaurant_id=user.restaurant_id, actor=user.name, table=table,
                         key=key, action=action, detail=detail))


def recover(session: Session, user: User, serial: str, kg: float | None = None,
            note: str | None = None, on: date | None = None,
            lang: str | None = None) -> Recovery:
    """[01268] Devuelve al stock una pieza que había desaparecido y ha vuelto a aparecer.

    Vale igual para un primal marcado como sospechoso y para un corte que el
    inventario dejó a cero. Queda registrado quién lo hizo, cuándo y por qué:
    una corrección no se hace a escondidas.
    """
    on = on or jornada.del_usuario(session, user)
    lang = lang or service.restaurant_language(session, user.restaurant_id)

    primal = (session.query(Primal)
              .filter_by(restaurant_id=user.restaurant_id, serial=serial).first())
    if primal is not None:
        return _recover_primal(session, user, primal, kg, note, lang)

    lot = (session.query(IngredientLot)
           .filter_by(restaurant_id=user.restaurant_id, serial=serial).first())
    if lot is not None:
        return _recover_cut(session, user, lot, kg, note, on, lang)

    raise InventoryError(
        f"No hay ninguna pieza con el serial {serial}. Si nunca estuvo en el "
        "sistema, hay que darla de alta diciendo de qué artículo es y a qué precio.")


def _recover_primal(session: Session, user: User, primal: Primal, kg: float | None,
                    note: str | None, lang: str) -> Recovery:
    """[01269] Da por reaparecida una pieza que constaba perdida.

    Una pieza que consta cortada no reaparece: si el despiece estuvo mal, lo
    que se corrige es el despiece. Levantarla aquí metería en la cámara una
    pieza que ya está repartida en cortes, y esos kilos saldrían dos veces.
    """
    if primal.status == PrimalStatus.CUT:
        raise InventoryError(
            f"El primal {primal.serial} consta cortado en {primal.status_ref}. "
            "Si el despiece fue un error, hay que corregir el despiece, no la pieza.")
    was_suspect = primal.suspect_phantom
    primal.suspect_phantom = False
    if kg:
        primal.weight_kg = round(kg, 6)
    detail = f"Reaparece el primal {primal.serial}." + (f" {note}" if note else "")
    _audit(session, user, "primals", primal.serial, "RECOVER", detail)
    session.flush()
    _announce(session, user, lang, "alert.recovered_primal",
              {"serial": primal.serial, "sku": primal.sku}, was_suspect)
    return Recovery(serial=primal.serial, kind="PRIMAL", label=primal.sku,
                    kg=primal.weight_kg or 0.0)


def _recover_cut(session: Session, user: User, lot: IngredientLot, kg: float | None,
                 note: str | None, on: date, lang: str) -> Recovery:
    """[01270] Sube un lote de cortes a los kilos que han aparecido de verdad.

    Solo hacia arriba: esto es para lo que apareció. Bajar un lote es contar
    menos de lo que consta, y eso se hace en un inventario, con su firma y su
    merma, no por la puerta de atrás. Queda un movimiento de ajuste con su
    coste, para que el cuadre lo vea.
    """
    if kg is None or kg <= 0:
        raise InventoryError("Hay que decir cuántos kilos han aparecido")
    difference = round(kg - lot.qty_remaining, 6)
    if difference <= 0:
        raise InventoryError(
            f"El corte {lot.serial} ya consta con {lot.qty_remaining}. "
            "Para bajarlo, se cuenta en un inventario.")
    lot.qty_remaining = round(kg, 6)
    value = round(difference * lot.unit_cost, 6)
    session.add(IngredientMovement(
        restaurant_id=user.restaurant_id, ingredient_id=lot.ingredient_id, lot_id=lot.id,
        date=on, kind=MovementKind.ADJUST, qty=difference, cost=value,
        source="recovery", source_ref=lot.serial, created_by=user.id))
    label = lot.ingredient.name if lot.ingredient else lot.serial
    detail = f"Reaparece el corte {lot.serial}: +{difference}." + (f" {note}" if note else "")
    _audit(session, user, "ingredient_lots", lot.serial, "RECOVER", detail)
    session.flush()
    _announce(session, user, lang, "alert.recovered_cut",
              {"serial": lot.serial, "cut": label, "kg": f"{difference:.10g}"}, True)
    return Recovery(serial=lot.serial, kind="CUT", label=label, kg=kg,
                    restored_kg=difference, value=value)


def adopt(session: Session, user: User, serial: str, item_id: int, kg: float,
          unit_cost: float, expiry: date, note: str | None = None,
          on: date | None = None, lang: str | None = None) -> Recovery:
    """[01271] Da de alta una pieza que estaba en cámara y el sistema no tenía.

    Hay que decir de qué artículo es y a qué precio: un lote no se inventa con
    un coste en blanco.
    """
    from thegrill.models import IngredientItem
    on = on or jornada.del_usuario(session, user)
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    if kg <= 0:
        raise InventoryError("La cantidad tiene que ser mayor que cero")
    if unit_cost < 0:
        raise InventoryError("El precio no puede ser negativo")
    if (session.query(IngredientLot)
            .filter_by(restaurant_id=user.restaurant_id, serial=serial).first()):
        raise InventoryError(f"Ya existe una pieza con el serial {serial}")
    item = session.get(IngredientItem, item_id)
    if item is None or item.restaurant_id != user.restaurant_id:
        raise InventoryError("Ese artículo no es de este restaurante")

    lot = IngredientLot(restaurant_id=user.restaurant_id, item_id=item.id,
                        ingredient_id=item.ingredient_id, serial=serial,
                        lot_code="RECUPERADO", expiry=expiry, received=on, qty=kg,
                        qty_remaining=kg, unit_cost=unit_cost)
    session.add(lot)
    session.flush()
    session.add(IngredientMovement(
        restaurant_id=user.restaurant_id, ingredient_id=item.ingredient_id, lot_id=lot.id,
        date=on, kind=MovementKind.ADJUST, qty=kg, cost=round(kg * unit_cost, 6),
        source="recovery", source_ref=serial, created_by=user.id))
    detail = f"Alta de {serial} encontrada en cámara." + (f" {note}" if note else "")
    _audit(session, user, "ingredient_lots", serial, "ADOPT", detail)
    session.flush()
    label = item.ingredient.name if item.ingredient else item.name
    _announce(session, user, lang, "alert.adopted_cut",
              {"serial": serial, "cut": label, "kg": f"{kg:.10g}"}, True)
    return Recovery(serial=serial, kind="CUT", label=label, kg=kg, restored_kg=kg,
                    value=round(kg * unit_cost, 6), adopted=True)


def _announce(session: Session, user: User, lang: str, key: str, params: dict,
              worth_telling: bool) -> None:
    """[01272] Una corrección de stock no se hace en silencio."""
    if not worth_telling:
        return
    from thegrill.models import AlertSeverity
    targets = [uid for uid in service.manager_ids(session, user.restaurant_id) if uid != user.id]
    service.notify(session, user.restaurant_id, targets,
                   title=t(lang, "alert.recovery_title"),
                   body=t(lang, key, **params), severity=AlertSeverity.INFO)
