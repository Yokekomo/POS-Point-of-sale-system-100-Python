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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thegrill import config
from thegrill.engine.defrost import Consumed, SerialState, Variance, reconcile, variances
from thegrill.engine.recipes import explode
from thegrill.models import (Alert, AlertSeverity, ConsumptionMode, DefrostEntry, DefrostKind,
                             Ingredient, IngredientLot, IngredientMovement, MovementKind,
                             SalesByProduct, ShiftClosure, User)
from thegrill.web import aging, costing, jornada, locking, service, sites
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
    drip_kg: float = 0.0              # la diferencia, anotada como merma de descongelado
    drip_cost: float = 0.0
    missing_counts: list[str] = field(default_factory=list)
    not_by_count: list[str] = field(default_factory=list)   # cortes que descuentan al vender
    # Las piezas que maduran y hoy no se han pesado. Maduran en el local, son
    # carne fresca abierta y se cuentan todas las noches, como lo descongelado.
    aging_pending: list[str] = field(default_factory=list)
    # Piezas que este turno ya había descontado antes: se vuelven a sumar en el
    # cuadre, pero no se vuelven a sacar de la cámara.
    already: list[str] = field(default_factory=list)
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


def _thaw_serial(session: Session, restaurant_id: int, base: str) -> str:
    """Un número nuevo para lo que sale del arcón: «8017-01·D1», «·D2»…"""
    for n in range(1, 100):
        candidate = f"{base}·D{n}"
        if not (session.query(IngredientLot)
                .filter_by(restaurant_id=restaurant_id, serial=candidate).first()):
            return candidate
    raise DefrostError(f"Demasiadas salidas del congelador del lote {base}")


def thaw(session: Session, user: User, lot: IngredientLot, kg: float,
         pieces: int = 0, on: date | None = None, lang: str | None = None) -> IngredientLot:
    """Saca del arcón lo que se va a descongelar, y solo eso.

    Lo congelado está en espera: no se vende. Lo que lo despierta es esto, que
    es lo que pasa de verdad en la cocina —alguien abre el arcón y saca unas
    piezas—. Si sale el número entero, ese número deja de estar congelado. Si
    salen unas piezas, el número se parte: lo que sale nace con su propio
    número colgando del de origen y ya descongelado, y lo que queda sigue
    congelado esperando su turno. Así el POS descuenta de lo que de verdad hay
    en la cámara, y no de lo que sigue duro.
    """
    if not lot.frozen:
        return lot
    if kg <= EPSILON:
        raise DefrostError(
            f"Para sacar del congelador el número {lot.serial} hace falta su peso: "
            f"es lo que deja de estar en espera.")
    if kg >= lot.qty_remaining - EPSILON:
        lot.frozen = False                      # sale entero: se despierta entero
        session.flush()
        return lot

    movido = round(kg, 6)
    salen = pieces if pieces > 0 else None
    if lot.pieces and lot.qty_remaining > EPSILON:
        salen = min(lot.pieces, salen or max(1, int(round(lot.pieces * movido / lot.qty_remaining))))
        lot.pieces = max(0, lot.pieces - salen)
    hijo = IngredientLot(
        restaurant_id=lot.restaurant_id, item_id=lot.item_id,
        ingredient_id=lot.ingredient_id, lot_code=lot.lot_code,
        serial=_thaw_serial(session, lot.restaurant_id, lot.serial),
        parent_serial=lot.parent_serial or lot.serial, parent_lot=lot.parent_lot,
        expiry=lot.expiry, received=lot.received, qty=movido, qty_remaining=movido,
        unit_cost=lot.unit_cost, pieces=salen, piece_weight_g=lot.piece_weight_g,
        nominal_piece_g=lot.nominal_piece_g, grade=lot.grade, origin=lot.origin,
        frozen=False, site_id=lot.site_id, chamber=lot.chamber)
    session.add(hijo)
    if not locking.take(session, IngredientLot, lot.id, "qty_remaining", movido):
        raise DefrostError(
            f"Del número {lot.serial} ya no quedan {movido:.10g} kg en el congelador: "
            "otra persona acaba de sacarlos. Mira lo que queda y repítelo.")
    session.flush()
    # Lo que sale del arcón no se ha vendido ni se ha tirado, pero del número
    # han salido kilos: quedan apuntados en los dos, o el lote no se explica.
    sites.journal_split(session, user, lot, hijo, movido, on or jornada.del_usuario(session, user), "defrost",
                        "descongelado")
    return hijo


def record(session: Session, user: User, kind: DefrostKind, serial: str, pieces: int,
           total_kg: float, on: date | None = None, shift: str = "",
           note: str | None = None) -> DefrostEntry:
    """Apunta una salida a descongelar o un recuento de cierre."""
    if pieces < 0 or total_kg < 0:
        raise DefrostError("Ni las piezas ni el peso pueden ser negativos")
    lot = _lot_by_serial(session, user.restaurant_id, serial)
    try:
        sites.guard(session, user, lot)       # ese arcón no se abre desde aquí
    except sites.SiteError as e:
        raise DefrostError(str(e)) from None
    if kind == DefrostKind.INTAKE and lot.frozen:
        # Lo que sale del arcón deja de estar en espera, y lo que se queda no.
        lot = thaw(session, user, lot, total_kg, pieces, on=on or jornada.del_usuario(session, user))
        serial = lot.serial
    entry = DefrostEntry(restaurant_id=user.restaurant_id, date=on or jornada.del_usuario(session, user),
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


def shift_states(session: Session, restaurant_id: int, on: date, shift: str = "",
                 site_id: int | None = None) -> list[SerialState]:
    """Lo que había, lo que se sacó, lo que el POS ha vendido y lo que queda.

    Lo vendido se reparte entre las piezas que están descongeladas, en orden:
    así el recuento de cierre se hace contra un número, no contra el aire.

    Con sede, el turno de esa sede: cada barra cierra el suyo.
    """
    entries = (session.query(DefrostEntry)
               .filter_by(restaurant_id=restaurant_id, date=on, shift=shift or "").all())
    if site_id:
        principal = sites.main(session, restaurant_id).id
        lotes = {l.id: (l.site_id or principal) for l in session.query(IngredientLot)
                 .filter_by(restaurant_id=restaurant_id)}
        entries = [e for e in entries if lotes.get(e.lot_id, principal) == site_id]
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


def _taken_before(session: Session, restaurant_id: int, on: date,
                  shift: str) -> dict[str, dict]:
    """Lo que este turno ya sacó de la cámara, pieza a pieza.

    Un turno se puede cerrar dos veces —porque faltaba un recuento, o porque
    dos personas le dieron al botón—, y el cuadre se rehace entero. Lo que no
    se puede rehacer es el descuento: la carne solo sale una vez.
    """
    out: dict[str, dict] = {}
    rows = (session.query(IngredientMovement)
            .filter(IngredientMovement.restaurant_id == restaurant_id,
                    IngredientMovement.date == on,
                    IngredientMovement.source == "defrost").all())
    for movement in rows:
        ref = (movement.source_ref or "").split(" · ")[0].strip()
        partes = ref.split(" ", 1)
        serial = partes[0]
        if (partes[1].strip() if len(partes) > 1 else "") != (shift or ""):
            continue
        fila = out.setdefault(serial, {"cost": 0.0, "drip_kg": 0.0, "drip_cost": 0.0})
        if movement.kind == MovementKind.WASTE:
            fila["drip_kg"] = round(fila["drip_kg"] + abs(movement.qty or 0.0), 6)
            fila["drip_cost"] = round(fila["drip_cost"] + abs(movement.cost or 0.0), 6)
        fila["cost"] = round(fila["cost"] + abs(movement.cost or 0.0), 6)
    return out


def close(session: Session, user: User, on: date | None = None, shift: str = "",
          lang: str | None = None) -> ShiftClose:
    """Cierra el turno: descuenta lo consumido de verdad y lo compara con lo teórico."""
    on = on or jornada.del_usuario(session, user)
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    result = ShiftClose(date=on, shift=shift or "")

    mia = sites.of_user(session, user)
    states = shift_states(session, user.restaurant_id, on, shift,
                          site_id=mia.id if mia else None)
    result.consumed = reconcile(states)
    # El turno se coge antes de tocar la cámara. Si otra persona le está dando
    # a cerrar en este mismo momento, una de las dos escribe y la otra se
    # entera; lo que no pasa es que las dos descuenten los mismos kilos.
    cuadre = _claim_shift(session, user, result, mia.id if mia else None, lang)
    # Y lo que ya había salido se mira **después** de coger el turno: si se
    # mirara antes, la que llega segunda habría leído la cámara cuando la otra
    # todavía no había guardado, y volvería a sacar los mismos kilos.
    taken = _taken_before(session, user.restaurant_id, on, shift or "")

    # Lo que la carta dice que debería haberse gastado por lo vendido. Hace
    # falta antes de tocar el almacén: lo que sobre de ahí es merma, no venta.
    needed, units = theoretical_for(session, user.restaurant_id, on)
    per_unit = {ingredient_id: needed[ingredient_id] / units[ingredient_id]
                for ingredient_id in needed if units.get(ingredient_id)}

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

        # ¿Esta pieza ya se descontó en este mismo turno? Pasa más de lo que
        # parece: dos personas le dan a cerrar a la vez, o el móvil se queda
        # pensando y el cocinero pulsa otra vez. Sin esto, los mismos ocho
        # kilos salen dos veces de la cámara y el stock se queda a cero solo.
        # El cuadre sí los vuelve a sumar —el turno gastó lo que gastó—, pero
        # de la carne no se saca nada: ya estaba sacada.
        visto = taken.get(row.serial)
        if visto is not None:
            result.already.append(row.serial)
            result.cost = round(result.cost + visto["cost"], 6)
            result.drip_kg = round(result.drip_kg + visto["drip_kg"], 6)
            result.drip_cost = round(result.drip_cost + visto["drip_cost"], 6)
            real_by_name[row.ingredient] = round(
                real_by_name.get(row.ingredient, 0.0) + row.kg, 6)
            continue

        # Y se sacan con la resta metida en la propia orden: si otra persona se
        # llevó esos kilos mientras tanto, la cámara no se queda en negativo.
        take = min(row.kg, lot.qty_remaining)
        if take > EPSILON and not locking.take(session, IngredientLot, lot.id,
                                               "qty_remaining", take):
            session.refresh(lot, ["qty_remaining"])
            take = min(row.kg, lot.qty_remaining)
            if take > EPSILON:
                locking.take(session, IngredientLot, lot.id, "qty_remaining", take)
        cost = round(take * lot.unit_cost, 6)
        result.cost = round(result.cost + cost, 6)

        # Lo que se vendió de verdad, al peso de la carta, y lo que se fue por
        # el camino: la carne pierde agua al descongelar y el corte nunca sale
        # exacto. Esa diferencia no es una venta, es merma de descongelado, y
        # se apunta como tal para que se pueda mirar y sumar.
        expected = round(row.sold_pieces * per_unit.get(lot.ingredient_id, 0.0), 6)
        sold_part = round(min(take, expected), 6) if expected > EPSILON else take
        drip = round(take - sold_part, 6)
        ref = f"{row.serial} {shift or ''}".strip()
        if sold_part > EPSILON:
            session.add(IngredientMovement(
                restaurant_id=user.restaurant_id, ingredient_id=lot.ingredient_id,
                lot_id=lot.id, date=on, kind=MovementKind.SALE, qty=-sold_part,
                cost=round(sold_part * lot.unit_cost, 6), source="defrost",
                source_ref=ref, created_by=user.id))
        if drip > EPSILON:
            drip_cost = round(drip * lot.unit_cost, 6)
            result.drip_kg = round(result.drip_kg + drip, 6)
            result.drip_cost = round(result.drip_cost + drip_cost, 6)
            session.add(IngredientMovement(
                restaurant_id=user.restaurant_id, ingredient_id=lot.ingredient_id,
                lot_id=lot.id, date=on, kind=MovementKind.WASTE, qty=-drip,
                cost=drip_cost, source="defrost",
                source_ref=f"{ref} · {t(lang, 'defrost.drip')}"[:96], created_by=user.id))
        real_by_name[row.ingredient] = round(real_by_name.get(row.ingredient, 0.0) + row.kg, 6)

    # comparación con lo que dicen las recetas de lo vendido ese día
    names = {i.id: i.name for i in session.query(Ingredient)
             .filter_by(restaurant_id=user.restaurant_id,
                        consumption=ConsumptionMode.COUNT)}
    theoretical_by_name = {names[k]: v for k, v in needed.items() if k in names}
    for ingredient_id, name in names.items():
        units_by_name[name] = units.get(ingredient_id, 0)
    result.variances = variances(real_by_name, theoretical_by_name, units_by_name,
                                 cost_by_name)

    # El turno no está contado del todo si quedan piezas madurando sin pesar:
    # esa agua es merma del día, y mañana ya no se sabe de qué día era.
    result.aging_pending = aging.pending_today(session, user.restaurant_id, on,
                                               site_id=mia.id if mia else None)

    _save_closure(session, user, result, mia.id if mia else None, cuadre)
    session.flush()
    _raise_alerts(session, user, result, lang)
    return result


def _claim_shift(session: Session, user: User, result: ShiftClose,
                 site_id: int | None, lang: str) -> ShiftClosure:
    """Coge el cuadre de este turno, o dice quién lo tiene cogido.

    La primera vez es una fila nueva, y la regla de la base de datos —un turno,
    una fila— decide quién la escribe si son dos a la vez. Cuando el turno ya
    estaba cerrado, se vuelve a coger pisando la hora: solo lo consigue quien
    lee la misma hora que había, así que de dos que rehacen el cuadre a la vez
    solo pasa uno.
    """
    row = (session.query(ShiftClosure)
           .filter_by(restaurant_id=user.restaurant_id, date=result.date,
                      shift=result.shift or "", site_id=site_id).first())
    if row is not None:
        if not locking.claim(session, ShiftClosure, row.id,
                             {"closed_at": row.closed_at},
                             {"closed_at": datetime.utcnow()}):
            raise DefrostError(t(lang, "defrost.closing_now"))
        return row
    row = ShiftClosure(restaurant_id=user.restaurant_id, date=result.date,
                       shift=result.shift or "", site_id=site_id,
                       site_key=site_id or 0, closed_by=user.id)
    try:
        session.add(row)
        session.flush()
    except IntegrityError:
        # La otra persona escribió su cuadre mientras montábamos el nuestro.
        # Se deshace lo de aquí entero —que no había tocado la cámara todavía—
        # y se le dice que mire cómo ha quedado el turno.
        session.rollback()
        raise DefrostError(t(lang, "defrost.closing_now")) from None
    return row


def _save_closure(session: Session, user: User, result: ShiftClose,
                  site_id: int | None, row: ShiftClosure | None = None) -> ShiftClosure:
    """Deja escrito el cuadre del turno, para que el mes se sume solo.

    Si el mismo turno se vuelve a cerrar, se pisa la fila: un turno tiene un
    cuadre, el último, y no tres versiones de lo mismo.
    """
    if row is None:
        row = (session.query(ShiftClosure)
               .filter_by(restaurant_id=user.restaurant_id, date=result.date,
                          shift=result.shift or "", site_id=site_id).first())
    if row is None:
        row = ShiftClosure(restaurant_id=user.restaurant_id, date=result.date,
                           shift=result.shift or "", site_id=site_id,
                           site_key=site_id or 0)
        session.add(row)
    row.site_key = site_id or 0
    row.cost = round(result.cost, 4)
    row.loss_kg = round(result.loss_kg, 6)
    row.loss_cost = round(result.loss_cost, 4)
    row.drip_kg = round(result.drip_kg, 6)
    row.drip_cost = round(result.drip_cost, 4)
    row.pieces = len([c for c in result.consumed if c.counted])
    row.uncounted = ", ".join(result.missing_counts)[:2000] or None
    row.aging_pending = ", ".join(result.aging_pending)[:2000] or None
    row.closed_by = user.id
    row.closed_at = datetime.utcnow()
    session.flush()
    return row


@dataclass
class MonthSoFar:
    """Lo que llevamos del mes, sumando los turnos ya cerrados."""
    year: int
    month: int
    shifts: int = 0
    cost: float = 0.0
    loss_kg: float = 0.0
    loss_cost: float = 0.0
    drip_kg: float = 0.0
    drip_cost: float = 0.0
    rows: list = field(default_factory=list)

    @property
    def total_loss(self) -> float:
        """Lo que se ha ido en el mes, en dinero. Una sola vez.

        Sumaba el desvío y el agua, y son **la misma cifra contada dos veces**.
        El desvío es lo consumido menos lo que dice la carta; el agua del
        descongelado es la parte de lo consumido que pasa de lo que dice la
        carta. Un turno que se pasa un kilo salía como cincuenta y dos euros
        perdidos donde hubo veintiséis.

        El agua sigue estando —`drip_cost`— pero como desglose de esto, que es
        lo que de verdad explica: «de los veintiséis, veintiséis son agua».
        """
        return round(self.loss_cost, 2)

    @property
    def drip_share(self) -> float | None:
        """Qué parte del desvío es agua del descongelado, en tanto por ciento."""
        if abs(self.loss_cost) < 0.005:
            return None
        return round(self.drip_cost / self.loss_cost * 100, 1)


def month_so_far(session: Session, restaurant_id: int, on: date | None = None,
                 site_id: int | None = None) -> MonthSoFar:
    """El mes en curso, turno a turno. Sin ir aviso por aviso."""
    on = on or jornada.hoy(session, restaurant_id)
    first = date(on.year, on.month, 1)
    query = (session.query(ShiftClosure)
             .filter(ShiftClosure.restaurant_id == restaurant_id,
                     ShiftClosure.date >= first, ShiftClosure.date <= on))
    if site_id:
        query = query.filter(ShiftClosure.site_id == site_id)
    rows = query.order_by(ShiftClosure.date.desc(), ShiftClosure.id.desc()).all()
    out = MonthSoFar(year=on.year, month=on.month, shifts=len(rows), rows=rows)
    for row in rows:
        out.cost = round(out.cost + (row.cost or 0.0), 4)
        out.loss_kg = round(out.loss_kg + (row.loss_kg or 0.0), 6)
        out.loss_cost = round(out.loss_cost + (row.loss_cost or 0.0), 4)
        out.drip_kg = round(out.drip_kg + (row.drip_kg or 0.0), 6)
        out.drip_cost = round(out.drip_cost + (row.drip_cost or 0.0), 4)
    return out


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
    if result.aging_pending:
        # Lo que madura se pesa cada noche: si el turno se cierra sin eso, la
        # merma de hoy se pierde y mañana no se sabe de qué día era.
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="aging.uncounted",
            message=t(lang, "alert.aging_uncounted", n=len(result.aging_pending),
                      serials=", ".join(result.aging_pending[:6])),
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
