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
                             IngredientMovement, LossKind, MovementKind, Primal,
                             PrimalWeighing, User)
from thegrill.web import costing, jornada, locking, rangos, service, sites
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
    rangos.peso_corte(kg, lang or "es")
    if pieces is not None and pieces < 0:
        raise WasteError("Las piezas no pueden ser negativas")
    on = on or jornada.del_usuario(session, user)
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
    if not locking.take(session, IngredientLot, lot.id, "qty_remaining", kg):
        raise WasteError(
            f"Del lote {lot.serial or lot.id} ya no quedan {kg:.10g} kg: otra persona "
            "acaba de gastarlos. Mira lo que queda y repítelo.")

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
    """El lote del que se tira, por su número o por el ingrediente.

    Y con la puerta cerrada: no se tira la carne de otra sede. De un lote que
    ya está a cero no queda nada que tirar, y decirlo es mejor que dejar el
    stock en negativo.
    """
    if serial:
        lot = (session.query(IngredientLot)
               .filter_by(restaurant_id=user.restaurant_id, serial=serial.strip()).first())
        if lot is None:
            raise WasteError(f"No hay ninguna pieza con el serial {serial}")
        if lot.qty_remaining <= EPSILON:
            raise WasteError(f"Del lote {serial} no queda nada que tirar")
        try:
            sites.guard(session, user, lot)   # no se tira la carne de otra sede
        except sites.SiteError as e:
            raise WasteError(str(e)) from None
        return lot
    if ingredient_id:
        ingredient = session.get(Ingredient, ingredient_id)
        if ingredient is None or ingredient.restaurant_id != user.restaurant_id:
            raise WasteError("Ese ingrediente no es de este restaurante")
        # La merma sale de donde está quien la apunta: el que tira carne en el
        # local no está tirando la del obrador.
        mia = sites.of_user(session, user)
        lots = costing.rotation_order(session, user.restaurant_id, ingredient,
                                      site_id=mia.id if mia else None)
        if not lots:
            raise WasteError(f"No queda stock de {ingredient.name}"
                             + (f" en {mia.name}" if mia else ""))
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
    """Deja el aviso de la merma y se lo manda a quien lleva la casa.

    De cincuenta euros para arriba se sube el tono: no es lo mismo tirar un
    recorte que tirar un lomo, y si las dos cosas avisan igual se dejan de
    mirar las dos.
    """
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


def recent(session: Session, restaurant_id: int, days: int = 30,
           on: date | None = None) -> list[IngredientMovement]:
    """Las últimas mermas de cámara, con su lote, sus kilos y su coste.

    La ventana cuenta hacia atrás desde `on`, que por defecto es hoy. El parte
    de un día pasado —el de ayer, que se imprime por la mañana— pregunta por
    su día, no por el de hoy: si la ventana se contara siempre desde hoy, ese
    parte saldría sin la merma que sí se apuntó.
    """
    from datetime import timedelta
    since = (on or jornada.hoy(session, restaurant_id)) - timedelta(days=days)
    return (session.query(IngredientMovement)
            .filter(IngredientMovement.restaurant_id == restaurant_id,
                    IngredientMovement.kind == MovementKind.WASTE,
                    IngredientMovement.date >= since)
            .order_by(IngredientMovement.date.desc(), IngredientMovement.id.desc())
            .limit(200).all())


# ------------------------------------------------- todo lo que se tira, junto
CHAMBER = "chamber"      # la pieza ya cortada que se echa a perder
TRIM = "trim"            # la costra y la grasa que se van al limpiar una pieza


@dataclass
class WasteLine:
    """Una línea de lo que se ha tirado, venga de donde venga.

    La merma de cámara y lo que se tira limpiando una pieza son la misma cosa
    mirada desde dos sitios: carne comprada que no se va a vender. Se apuntan
    en pantallas distintas porque se hacen en momentos distintos, pero a fin de
    mes lo que importa es el total, y para eso tienen que estar juntas.
    """
    date: date
    source: str                  # CHAMBER o TRIM
    label: str                   # el ingrediente, o el SKU de la pieza
    serial: str | None
    kg: float
    cost: float | None           # lo que valía; None si no se sabe el precio
    lot: str | None = None       # el despiece del que salió, o el lote de recepción
    pieces: int | None = None
    reason: str | None = None
    who: str | None = None


@dataclass
class WasteTotals:
    kg: float = 0.0
    cost: float = 0.0
    chamber_kg: float = 0.0
    trim_kg: float = 0.0
    lines: int = 0


def everything(session: Session, restaurant_id: int, days: int = 30,
               on: date | None = None) -> list[WasteLine]:
    """Todo lo tirado en el periodo: lo de cámara y lo de las limpiezas.

    Como en `recent`, la ventana termina en `on` —hoy si no se dice otra cosa—
    para que el parte de un día pasado encuentre lo que se tiró ese día.
    """
    from datetime import timedelta
    since = (on or jornada.hoy(session, restaurant_id)) - timedelta(days=days)
    names = {i.id: i.name for i in session.query(Ingredient)
             .filter_by(restaurant_id=restaurant_id)}
    people = {u.id: u.name for u in session.query(User)
              .filter_by(restaurant_id=restaurant_id)}
    # Solo se traen los lotes y las piezas de las mermas que se van a enseñar.
    # Cargar la cámara entera —miles de lotes de medio año— para poner nombre a
    # las cuatro mermas de hoy era la mitad de lo que tardaba el parte del día.
    movimientos = recent(session, restaurant_id, days=days, on=on)
    limpiezas = (session.query(PrimalWeighing)
                 .filter(PrimalWeighing.restaurant_id == restaurant_id,
                         PrimalWeighing.kind == LossKind.TRIM,
                         PrimalWeighing.date >= since)
                 .order_by(PrimalWeighing.date.desc(), PrimalWeighing.id.desc())
                 .limit(200).all())
    seriales = {l.serial for l in limpiezas if l.serial}
    skus, lote_de = {}, {}
    if seriales:
        for serial, sku, lote in (session.query(Primal.serial, Primal.sku, Primal.lot)
                                  .filter(Primal.restaurant_id == restaurant_id,
                                          Primal.serial.in_(seriales))):
            skus[serial] = sku
            lote_de[serial] = lote
    # El serial sale del lote, no de leerlo de la referencia: la referencia es
    # un texto para el ojo humano y cambia de forma según lo que traiga.
    ids = {m.lot_id for m in movimientos if m.lot_id}
    lotes = {lot.id: lot for lot in session.query(IngredientLot)
             .filter(IngredientLot.restaurant_id == restaurant_id,
                     IngredientLot.id.in_(ids))} if ids else {}

    rows: list[WasteLine] = []
    for movement in movimientos:
        lot = lotes.get(movement.lot_id)
        rows.append(WasteLine(
            date=movement.date, source=CHAMBER,
            label=names.get(movement.ingredient_id, ""),
            serial=lot.serial if lot else None, kg=round(-movement.qty, 6),
            cost=round(movement.cost, 4) if movement.cost is not None else None,
            lot=lot.lot_code if lot else None,
            pieces=_pieces_of(movement.source_ref or ""),
            reason=_reason_of(movement.source_ref or ""),
            who=people.get(movement.created_by)))

    for limpieza in limpiezas:
        thrown = limpieza.waste_kg or 0.0
        if thrown <= EPSILON:
            continue           # esa limpieza se aprovechó entera: no hay nada tirado
        per_kg = limpieza.cost_per_kg_before
        rows.append(WasteLine(
            date=limpieza.date, source=TRIM,
            label=skus.get(limpieza.serial, limpieza.serial),
            serial=limpieza.serial, kg=round(thrown, 6),
            cost=round(thrown * per_kg, 4) if per_kg is not None else None,
            lot=lote_de.get(limpieza.serial), reason=limpieza.note,
            who=people.get(limpieza.created_by)))

    rows.sort(key=lambda r: (r.date, r.serial or ""), reverse=True)
    return rows


def totals(lines: list[WasteLine]) -> WasteTotals:
    """Lo que suma todo eso, que es la pregunta de fin de mes."""
    out = WasteTotals(lines=len(lines))
    for line in lines:
        out.kg = round(out.kg + line.kg, 6)
        out.cost = round(out.cost + (line.cost or 0.0), 4)
        if line.source == CHAMBER:
            out.chamber_kg = round(out.chamber_kg + line.kg, 6)
        else:
            out.trim_kg = round(out.trim_kg + line.kg, 6)
    return out


def _pieces_of(ref: str) -> int | None:
    """Las piezas que se tiraron, si se contaron al apuntarlo."""
    for parte in (p.strip() for p in ref.split("·")):
        if parte.endswith("pz"):
            try:
                return int(parte[:-2].strip())
            except ValueError:
                return None
    return None


def _reason_of(ref: str) -> str | None:
    """El motivo que se escribió al tirar, si se escribió alguno.

    La referencia lleva el lote, el serial, las piezas y el motivo, separados
    por puntos, y lo único que no es ninguna de las otras tres cosas es el
    motivo: por eso se descarta lo que acaba en «pz».
    """
    partes = [p.strip() for p in ref.split("·") if p.strip()]
    if len(partes) < 2:
        return None
    ultima = partes[-1]
    return None if ultima.endswith("pz") else ultima
