"""Del primal al plato.

Un primal entra como pieza física con su número de serie y su coste puesto en
almacén. Al despiezarlo sale:

- hasta una decena de **cortes** distintos, cada uno atado a un artículo,
- **partes para reusar** (el recorte), que son un corte más marcado como tal,
- y **merma**, que no se puede usar.

Cada corte entra en el almacén de ingredientes como un lote. A partir de ahí es
un ingrediente igual que cualquier otro: cuelga de su madre, se gasta por
rotación y se usa en subrecetas, recetas y emplatados.

**Cómo se reparte el coste.** El coste de los primales consumidos se reparte
entre lo aprovechable, en proporción a `kg × índice de valor`. La merma no
recibe nada: su coste lo absorben los cortes. Por eso el precio real por kilo de
un solomillo sube cuando el despiece rinde mal, que es justo lo que hay que ver.
"""
import math
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from thegrill import config
from thegrill.engine.stock import MassCheck, mass_balance, yield_pct
from thegrill.models import (Despiece, DespieceCut, DespiecePrimal, IngredientItem,
                             IngredientLot, IngredientMovement, MovementKind, Primal,
                             PrimalStatus, Storage, User)
from thegrill.rules import TGInput, validate_tg
from thegrill.web import exacto, jornada, locking

EPSILON = 1e-9
MAX_CUTS_PER_PRIMAL = 10      # lo que sale de un primal en la práctica


def next_serial(session: Session, restaurant_id: int, base: str, index: int) -> str:
    """Serial nuevo para un corte: «8017-01», «8017-02»…

    Cada corte y cada recorte sale del despiece con su propio número, para
    poder seguirlo hasta el plato en que se vendió.
    """
    candidate = f"{base}-{index:02d}"
    n = index
    while (session.query(IngredientLot)
           .filter_by(restaurant_id=restaurant_id, serial=candidate).first()):
        n += 1
        candidate = f"{base}-{n:02d}"
    return candidate


class ButcheryError(ValueError):
    """El despiece no se puede volcar al almacén tal y como está."""


@dataclass
class Allocation:
    cut: DespieceCut
    kg: float
    weight: float            # kg × índice de valor
    cost: float
    unit_cost: float


@dataclass
class PostResult:
    tg: str
    mass: MassCheck
    yield_pct: float
    total_cost: float
    allocations: list[Allocation] = field(default_factory=list)
    lots: list[IngredientLot] = field(default_factory=list)
    serials_cut: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


# --------------------------------------------------------------- validación
def primal_cost(primal: Primal) -> float | None:
    """Coste puesto en almacén de la pieza. Sin precio no se inventa nada."""
    if primal.piece_cost_usd is not None:
        return primal.piece_cost_usd
    if primal.landed_usd_per_kg is not None and primal.weight_kg:
        return round(primal.landed_usd_per_kg * primal.weight_kg, 6)
    return None


def check(session: Session, despiece: Despiece) -> list[str]:
    """Todo lo que impide volcar este despiece, dicho de una vez."""
    problems: list[str] = []
    if despiece.posted:
        problems.append(f"{despiece.tg}: ya estaba volcado al almacén")

    cuts = list(despiece.cuts)
    if len(cuts) > MAX_CUTS_PER_PRIMAL:
        problems.append(f"{despiece.tg}: {len(cuts)} cortes, más de los {MAX_CUTS_PER_PRIMAL} habituales")

    serials = [p.serial for p in despiece.primals]
    for issue in validate_tg(TGInput(despiece.tg, despiece.country, serials,
                                     [{"cut_name": c.cut_name, "pieces": c.pieces,
                                       "weight_per_piece_g": c.weight_per_piece_g,
                                       "by_weight": bool(c.by_weight),
                                       "total_kg": c.total_kg} for c in cuts])):
        if issue.severity == "ERROR":
            problems.append(issue.message)

    for cut in cuts:
        if cut.item_id is None:
            problems.append(f"{despiece.tg}/{cut.cut_name}: sin artículo, no puede entrar en almacén")
        if cut.value_index is None or cut.value_index <= 0:
            problems.append(f"{despiece.tg}/{cut.cut_name}: índice de valor inválido")

    for link in despiece.primals:
        if link.serial is None:
            continue
        primal = (session.query(Primal)
                  .filter_by(restaurant_id=despiece.restaurant_id, serial=link.serial).first())
        if primal is None:
            problems.append(f"{despiece.tg}: el serial {link.serial} no está en el registro")
        elif primal.status != PrimalStatus.IN_STOCK:
            problems.append(f"{despiece.tg}: el serial {link.serial} ya estaba {primal.status.value}")
        elif primal_cost(primal) is None:
            problems.append(f"{despiece.tg}: el serial {link.serial} no tiene coste")
    return problems


# ----------------------------------------------------------------- reparto
def allocate(cuts: list[DespieceCut], total_cost: float) -> list[Allocation]:
    """Reparte el coste del primal entre lo aprovechable, por kg × valor.

    La merma no entra en el reparto: su coste lo absorben los cortes.

    El reparto es **exacto al céntimo**: la suma de lo que se le asigna a cada
    corte es idéntica al coste de la pieza, no parecida. Se hace en céntimos
    enteros y lo que sobra se reparte por resto mayor (ver `exacto.py`).
    Dividiendo en coma flotante y redondeando cada parte por su lado, la suma
    fallaba por unas millonésimas: nada en una pieza, y un cuadre que no sale
    al cabo de cien mil.

    El precio por kilo que sale de aquí es un **derivado**: 100 € entre 3 kg
    son 33,333… y eso no es exacto en ninguna representación. La verdad es el
    coste; el precio por kilo se calcula para enseñarlo y para valorar, pero
    quien quiera reconstruir el total tiene que sumar los costes, no
    multiplicar los kilos por su precio.
    """
    usable = [c for c in cuts if c.total_kg > EPSILON]
    total_weight = sum(c.total_kg * (c.value_index or 1.0) for c in usable)
    if total_weight <= EPSILON:
        raise ButcheryError("El despiece no tiene cortes con peso: no hay entre qué repartir")

    pesos = [c.total_kg * (c.value_index or 1.0) for c in usable]
    costes = exacto.repartir_dinero(total_cost, pesos)
    out = []
    for cut, peso, cost in zip(usable, pesos, costes):
        out.append(Allocation(cut=cut, kg=cut.total_kg, weight=round(peso, 6), cost=cost,
                              unit_cost=round(cost / cut.total_kg, 6)))
    return out


# ------------------------------------------------------------------ volcado
def post(session: Session, user: User, despiece: Despiece, use_by: date | None = None,
         tolerance_pct: float | None = None) -> PostResult:
    """Vuelca el despiece al almacén: cada corte entra como lote de su artículo.

    Es idempotente por el flag `posted`: un despiece no se vuelca dos veces.
    """
    problems = check(session, despiece)
    if problems:
        raise ButcheryError("; ".join(problems))

    cuts = list(despiece.cuts)
    total_cuts_kg = round(sum(c.total_kg for c in cuts if not c.is_trim), 6)
    trim_kg = round(sum(c.total_kg for c in cuts if c.is_trim), 6)
    mass = mass_balance(despiece.tg, despiece.weight_before_kg, total_cuts_kg,
                        despiece.waste_kg, trim_kg,
                        tolerance_pct if tolerance_pct is not None
                        else config.MASS_DRIFT_TOLERANCE_PCT)

    primals, total_cost, expiries = [], 0.0, []
    for link in despiece.primals:
        if link.serial is None:
            continue
        primal = (session.query(Primal)
                  .filter_by(restaurant_id=despiece.restaurant_id, serial=link.serial).one())
        primals.append(primal)
        cost = primal_cost(primal)
        # El coste de la pieza se congela aquí para que no se pierda ni cambie.
        if primal.piece_cost_usd is None:
            primal.piece_cost_usd = cost
        total_cost += cost
        for candidate in (primal.frozen_use_by, primal.expiry_label):
            if candidate:
                expiries.append(candidate)
    total_cost = round(total_cost, 6)

    # Un corte solo se puede atribuir a una pieza concreta si el despiece
    # consumió una sola. Con varias, el padre es el batch: no se finge una
    # trazabilidad por pieza que no existe.
    # Si la pieza venía del congelador, lo que sale de ella nace congelado: no
    # se vende hasta que alguien lo saque a descongelar, y su fecha es la del
    # congelador, no la de la etiqueta original.
    from_freezer = bool(primals) and all(
        (p.storage or Storage.CHILLED) == Storage.FROZEN for p in primals)

    single = primals[0] if len(primals) == 1 else None
    base_serial = single.serial if single else despiece.tg
    parent_lot = single.lot if single else None
    grade = _shared(primals, "grade") or despiece.grade
    origin = _shared(primals, "origin") or despiece.country

    if use_by is None:
        if not expiries:
            raise ButcheryError(
                f"{despiece.tg}: los primales no traen fecha de consumo y no se ha dado una. "
                "Una fecha de caducidad no se inventa.")
        use_by = min(expiries)

    allocations = allocate(cuts, total_cost)

    # Las piezas se cogen aquí, antes de escribir un solo kilo en cámara, y se
    # cogen con el estado de antes metido en la orden: «ponla cortada si sigue
    # entera». Si otra persona la está despiezando en la mesa de al lado, una
    # de las dos se lo lleva y la otra se entera ahora, no cuando el inventario
    # de fin de mes diga que sobran nueve kilos que nunca existieron.
    for primal in primals:
        if not locking.claim(session, Primal, primal.id,
                             {"status": PrimalStatus.IN_STOCK},
                             {"status": PrimalStatus.CUT, "status_ref": despiece.tg,
                              "status_date": despiece.date}):
            raise ButcheryError(
                f"{despiece.tg}: la pieza {primal.serial} la acaba de despiezar otra "
                "persona. Mira el despiece que ya está hecho antes de repetirlo.")

    result = PostResult(tg=despiece.tg, mass=mass,
                        yield_pct=yield_pct(despiece.weight_before_kg, total_cuts_kg),
                        total_cost=total_cost, allocations=allocations)
    if not mass.ok:
        result.issues.append(
            f"{despiece.tg}: descuadre de masa de {mass.drift_kg} kg ({mass.drift_pct} %)")
    for alloc in allocations:
        if alloc.cut.by_weight:
            continue          # se corta al vender: no hay peso por pieza que comparar
        gap = piece_gap_pct(avg_piece_g(alloc.kg, alloc.cut.pieces),
                            alloc.cut.weight_per_piece_g)
        if gap is not None and abs(gap) >= config.PORTION_VARIANCE_PCT:
            real = avg_piece_g(alloc.kg, alloc.cut.pieces)
            result.issues.append(
                f"{despiece.tg}/{alloc.cut.cut_name}: piezas de {real:.10g} g de media "
                f"frente a {alloc.cut.weight_per_piece_g:.10g} g de objetivo ({gap:+.1f} %)")

    for position, alloc in enumerate(allocations, start=1):
        item = session.get(IngredientItem, alloc.cut.item_id)
        serial = next_serial(session, despiece.restaurant_id, base_serial, position)
        lot = IngredientLot(restaurant_id=despiece.restaurant_id, item_id=item.id,
                            ingredient_id=item.ingredient_id, lot_code=despiece.tg,
                            serial=serial, parent_serial=single.serial if single else None,
                            parent_lot=parent_lot,
                            expiry=use_by, received=despiece.date, qty=alloc.kg,
                            qty_remaining=alloc.kg, unit_cost=alloc.unit_cost,
                            # Lo que sale a peso entra en kilos y sin piezas: la
                            # ración la decide el cuchillo en el momento de vender.
                            pieces=None if alloc.cut.by_weight else alloc.cut.pieces,
                            piece_weight_g=None if alloc.cut.by_weight else (
                                avg_piece_g(alloc.kg, alloc.cut.pieces)
                                or alloc.cut.weight_per_piece_g),
                            nominal_piece_g=None if alloc.cut.by_weight
                            else alloc.cut.weight_per_piece_g,
                            grade=grade, origin=origin, frozen=from_freezer,
                            # Los cortes se quedan donde estaba la pieza: el
                            # despiece no mueve carne de sede, la transforma.
                            chamber=(single.chamber if single else
                                     (primals[0].chamber if primals else None)),
                            site_id=(single.site_id if single else
                                     (primals[0].site_id if primals else None)))
        session.add(lot)
        session.flush()
        alloc.cut.lot_id = lot.id
        item.last_cost = alloc.unit_cost
        result.lots.append(lot)
        session.add(IngredientMovement(
            restaurant_id=despiece.restaurant_id, ingredient_id=item.ingredient_id,
            lot_id=lot.id, date=despiece.date, kind=MovementKind.IN, qty=alloc.kg,
            cost=alloc.cost, source="butchery", source_ref=serial, created_by=user.id))

    # Un primal solo se marca cortado con el despiece que lo confirma. El
    # estado ya se cogió arriba; aquí solo queda dejar dicho cuáles fueron.
    for primal in primals:
        result.serials_cut.append(primal.serial)

    despiece.total_cuts_kg = total_cuts_kg
    despiece.trim_kg = trim_kg
    despiece.yield_pct = result.yield_pct
    despiece.posted = True
    despiece.posted_at = datetime.utcnow()
    session.flush()
    return result


def trace(session: Session, restaurant_id: int, serial: str) -> dict:
    """Dónde ha ido un corte: de qué primal salió y en qué se ha gastado."""
    lot = (session.query(IngredientLot)
           .filter_by(restaurant_id=restaurant_id, serial=serial).first())
    if lot is None:
        raise ButcheryError(f"No hay ningún corte con el serial {serial}")
    movements = (session.query(IngredientMovement)
                 .filter_by(restaurant_id=restaurant_id, lot_id=lot.id)
                 .order_by(IngredientMovement.date, IngredientMovement.id).all())
    return {
        "serial": lot.serial,
        "ingredient": lot.ingredient.name if lot.ingredient else None,
        "article": lot.item.name if lot.item else None,
        "from_primal": lot.parent_serial,
        "reception_lot": lot.parent_lot,
        "butchery": lot.lot_code,
        "received_kg": lot.qty,
        "remaining_kg": lot.qty_remaining,
        "unit_cost": lot.unit_cost,
        "movements": [{"date": m.date, "kind": m.kind.value, "qty": m.qty,
                       "cost": m.cost, "source": m.source, "ref": m.source_ref}
                      for m in movements],
    }


def avg_piece_g(total_kg: float, pieces: int | None) -> float | None:
    """Peso medio real por pieza: los kilos pesados entre las piezas contadas.

    Es lo único fiable. El peso «objetivo» de la hoja es a lo que se apunta, no
    lo que sale: un corte a mano varía pieza a pieza.
    """
    if not pieces or pieces <= 0 or total_kg <= 0:
        return None
    return round(total_kg * 1000 / pieces, 1)


def piece_gap_pct(real_g: float | None, nominal_g: float | None) -> float | None:
    """Cuánto se desvía el corte real del objetivo. Positivo es cortar de más."""
    if not real_g or not nominal_g:
        return None
    return round((real_g - nominal_g) / nominal_g * 100, 1)


def _shared(primals: list[Primal], field_name: str) -> str | None:
    """El valor solo si todas las piezas coinciden: si no, no se afirma nada."""
    values = {getattr(p, field_name) for p in primals if getattr(p, field_name)}
    return values.pop() if len(values) == 1 else None


def cut_summary(result: PostResult) -> list[tuple[str, float, float, float]]:
    """(corte, kg, coste, precio por kg) de mayor a menor coste."""
    rows = [(a.cut.cut_name, a.kg, a.cost, a.unit_cost) for a in result.allocations]
    return sorted(rows, key=lambda r: -r[2])


# ============================================ cuánta carne queda al cerrar
@dataclass
class CutStock:
    """Lo que queda de un corte: kilos, seriales abiertos y piezas descongeladas."""
    ingredient_id: int
    name: str
    unit: str
    kg: float
    labels: list[str] = field(default_factory=list)   # peso, calidad y procedencia
    open_serials: int = 0
    thawed_pieces: int = 0
    thawed_kg: float = 0.0
    # De esos kilos, los que están congelados: existen, pero están en espera
    # de que alguien los saque. No se venden, así que no cuentan para servir.
    frozen_kg: float = 0.0
    min_stock: float | None = None
    days_to_expiry: int | None = None

    @property
    def below_par(self) -> bool:
        return self.min_stock is not None and self.kg < self.min_stock


@dataclass
class PrimalStock:
    sku: str
    pieces: int
    kg: float
    min_pieces: int | None

    @property
    def below_par(self) -> bool:
        return self.min_pieces is not None and self.pieces < self.min_pieces


@dataclass
class MeatStatus:
    date: date
    cuts: list[CutStock] = field(default_factory=list)
    primals: list[PrimalStock] = field(default_factory=list)
    expiring: list[CutStock] = field(default_factory=list)
    alerts: list = field(default_factory=list)

    @property
    def cuts_below(self) -> list[CutStock]:
        return [c for c in self.cuts if c.below_par]

    @property
    def primals_below(self) -> list[PrimalStock]:
        return [p for p in self.primals if p.below_par]

    @property
    def total_primals(self) -> int:
        return sum(p.pieces for p in self.primals)

    @property
    def total_cut_kg(self) -> float:
        return round(sum(c.kg for c in self.cuts), 3)


def status(session: Session, restaurant_id: int, on: date | None = None,
           expiry_days: int = 3, site_id: int | None = None) -> MeatStatus:
    """Foto de la carne al cerrar el día: cortes y primales que quedan.

    Con `site_id` es la foto de una sede. Quien trabaja en un local no quiere
    ver las ocho piezas del obrador cuando mira lo que le queda para el pase.
    """
    from thegrill.models import (ConsumptionMode, DefrostEntry, DefrostKind, Ingredient,
                                 PrimalPar)
    from thegrill.web import costing, exacto, sites
    on = on or jornada.hoy(session, restaurant_id)
    result = MeatStatus(date=on)
    principal = sites.main(session, restaurant_id).id if site_id else None

    pars = {p.sku: p.min_pieces for p in session.query(PrimalPar)
            .filter_by(restaurant_id=restaurant_id)}
    # Lo que la sede haya puesto manda sobre el mínimo de la casa: la playa en
    # agosto y la sierra en enero no quieren el mismo.
    suyos = sites.pars_of(session, restaurant_id, site_id)
    pars.update(suyos.primals)
    by_sku: dict[str, list[Primal]] = {}
    for primal in (session.query(Primal)
                   .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK)):
        if site_id and (primal.site_id or principal) != site_id:
            continue
        by_sku.setdefault(primal.sku, []).append(primal)
    for sku in sorted(set(by_sku) | set(pars)):
        pieces = by_sku.get(sku, [])
        result.primals.append(PrimalStock(
            sku=sku, pieces=len(pieces),
            kg=round(sum(p.weight_kg or 0 for p in pieces), 3),
            min_pieces=pars.get(sku)))

    # Cortes: solo lo que sale de un despiece, es decir lo que tiene serial.
    lots = costing.at_site(session.query(IngredientLot)
                           .filter(IngredientLot.restaurant_id == restaurant_id,
                                   IngredientLot.serial.isnot(None),
                                   IngredientLot.qty_remaining > EPSILON),
                           session, restaurant_id, site_id).all()
    ingredients = {i.id: i for i in session.query(Ingredient)
                   .filter_by(restaurant_id=restaurant_id)}
    recuentos = last_counts(session, restaurant_id, on)     # uno para todos los cortes
    grouped: dict[int, list[IngredientLot]] = {}
    for lot in lots:
        grouped.setdefault(lot.ingredient_id, []).append(lot)

    for ingredient_id, rows in grouped.items():
        ing = ingredients.get(ingredient_id)
        if ing is None:
            continue
        thawed_pieces, thawed_kg = _thawed(session, restaurant_id, [l.serial for l in rows],
                                           on, counts=recuentos)
        soonest = min(l.expiry for l in rows)
        result.cuts.append(CutStock(
            ingredient_id=ingredient_id, name=ing.name, unit=ing.unit.value,
            kg=round(sum(l.qty_remaining for l in rows), 3),
            labels=_labels(rows), open_serials=len(rows),
            thawed_pieces=thawed_pieces, thawed_kg=thawed_kg,
            min_stock=suyos.cuts.get(ingredient_id, ing.min_stock),
            frozen_kg=round(sum(l.qty_remaining for l in rows if l.frozen), 3),
            days_to_expiry=(soonest - on).days))
    result.cuts.sort(key=lambda c: c.name)
    result.expiring = sorted((c for c in result.cuts
                              if c.days_to_expiry is not None and c.days_to_expiry <= expiry_days),
                             key=lambda c: c.days_to_expiry)
    return result


def ceil_pct(value: float | None) -> int | None:
    """Food cost sin decimales y hacia arriba: más vale pasarse que quedarse corto."""
    return None if value is None else math.ceil(value - 1e-9)


def piece_label(nominal_g: float | None, real_g: float | None,
                food_cost_pct: float | None = None) -> str:
    """«330 g (~354 g · 31,8 % FC)»: lo de carta y lo que pasa de verdad.

    Delante va el peso de la carta, que es el que ve el cliente y con el que se
    hace el escandallo. Entre paréntesis, la realidad: el promedio que salió del
    despiece, y el food cost al que está saliendo ese corte. El peso solo se
    repite si difiere, porque un paréntesis con el mismo número no dice nada.
    """
    inside = []
    if real_g and (not nominal_g or abs(real_g - nominal_g) / nominal_g >= 0.005):
        inside.append(f"~{real_g:.10g} g")
    if food_cost_pct is not None:
        inside.append(f"{ceil_pct(food_cost_pct)} % FC")

    head = f"{nominal_g:.10g} g" if nominal_g else ""
    if not head:
        return " · ".join(inside)
    return f"{head} ({' · '.join(inside)})" if inside else head


def lot_label(lot) -> str:
    """«330 g (~354 g) · MB9+ · AUS», lo que hay que leer de un vistazo."""
    bits = []
    live = avg_piece_g(lot.qty, lot.pieces) or lot.piece_weight_g
    weight = piece_label(lot.nominal_piece_g, live)
    if weight:
        bits.append(weight)
    if lot.grade:
        bits.append(lot.grade)
    if lot.origin:
        bits.append(lot.origin)
    return " · ".join(bits)


def _labels(lots: list) -> list[str]:
    seen: list[str] = []
    for lot in lots:
        label = lot_label(lot)
        if label and label not in seen:
            seen.append(label)
    return seen


def last_counts(session: Session, restaurant_id: int, on: date) -> dict[str, tuple[int, float]]:
    """El último recuento de descongelado de cada número, de una sola vez.

    Antes se preguntaba número a número, y una casa con seis meses de trabajo
    hacía ciento cincuenta consultas para pintar la cámara: la pantalla tardaba
    más cuanto más tiempo llevaba abierta la casa, que es la peor manera de
    envejecer. Se trae todo en una y se queda el último de cada uno.
    """
    from thegrill.models import DefrostEntry, DefrostKind
    out: dict[str, tuple[int, float]] = {}
    for entry in (session.query(DefrostEntry)
                  .filter(DefrostEntry.restaurant_id == restaurant_id,
                          DefrostEntry.kind == DefrostKind.COUNT,
                          DefrostEntry.date <= on)
                  .order_by(DefrostEntry.date, DefrostEntry.shift, DefrostEntry.id)):
        out[entry.lot_serial] = (entry.pieces or 0, entry.total_kg or 0.0)
    return out


def _thawed(session: Session, restaurant_id: int, serials: list[str], on: date,
            counts: dict[str, tuple[int, float]] | None = None) -> tuple[int, float]:
    """Piezas descongeladas que quedan, según el último recuento de cada serial."""
    counts = last_counts(session, restaurant_id, on) if counts is None else counts
    pieces = kg = 0
    for serial in serials:
        ultimo = counts.get(serial)
        if ultimo:
            pieces += ultimo[0]
            kg = round(kg + ultimo[1], 6)
    return pieces, kg


def close_day(session: Session, user: User, on: date | None = None,
              expiry_days: int = 3, lang: str | None = None) -> MeatStatus:
    """Cierra el día de carne y avisa de lo que se está acabando."""
    from thegrill.models import Alert, AlertSeverity
    from thegrill.web import service
    from thegrill.web.i18n import t

    on = on or jornada.del_usuario(session, user)
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    result = status(session, user.restaurant_id, on, expiry_days)
    now = datetime.utcnow()

    for cut in result.cuts_below:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="meat.cut_low",
            message=t(lang, "alert.cut_low", cut=cut.name, kg=f"{cut.kg:.10g}",
                      unit=cut.unit, min=f"{cut.min_stock:.10g}"),
            severity=AlertSeverity.WARNING, created_at=now))
    for primal in result.primals_below:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="meat.primal_low",
            message=t(lang, "alert.primal_low", sku=primal.sku, pieces=primal.pieces,
                      min=primal.min_pieces),
            severity=AlertSeverity.WARNING, created_at=now))
    from thegrill.web import inventory as inventory_service
    month = inventory_service.monthly_status(session, user.restaurant_id, on)
    if month.due_soon:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="count.month_due",
            message=t(lang, "alert.count_month_due", days=month.days_left),
            severity=AlertSeverity.WARNING, created_at=now))

    for cut in result.expiring:
        result.alerts.append(Alert(
            restaurant_id=user.restaurant_id, code="meat.expiring",
            message=t(lang, "alert.cut_expiring", cut=cut.name, days=cut.days_to_expiry,
                      kg=f"{cut.kg:.10g}", unit=cut.unit),
            severity=AlertSeverity.CRITICAL if cut.days_to_expiry <= 0 else AlertSeverity.WARNING,
            created_at=now))

    for alert in result.alerts:
        session.add(alert)
    session.flush()
    targets = [uid for uid in service.manager_ids(session, user.restaurant_id) if uid != user.id]
    for alert in result.alerts:
        service.notify(session, user.restaurant_id, targets,
                       title=t(lang, "alert.meat_title"), body=alert.message,
                       severity=alert.severity, alert_id=alert.id, now=now)
    return result
