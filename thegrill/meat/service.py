"""Lo que la edición de carne hace y la plataforma de cocina no tenía pantalla.

El motor no cambia: despiece, reparto de coste, FEFO, descongelado, inventario,
trazabilidad y merma son los mismos de siempre. Aquí está lo que faltaba para
que una cocina de carne pueda trabajar sola:

- dar de alta un lote de primales, cada pieza con su número y su coste;
- montar un despiece desde un formulario y volcarlo a cámara;
- el catálogo de cortes, que es lo que se cuenta y se vende;
- la carta: un plato de carne es un corte y unos gramos, atado a su POS;
- el resumen de lo que está pendiente hoy.
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from thegrill.models import (ConsumptionMode, CountStatus, Despiece, DespieceCut,
                             DespiecePrimal, Ingredient, IngredientItem, IngredientLot,
                             MeatCount, PosProduct, Primal, PrimalStatus, Recipe,
                             RecipeKind, RecipeLine, Rotation, Storage, Unit, User)
from thegrill.web import aging as aging_mod
from thegrill.web import butchery, costing, defrost, inventory
from thegrill.web.i18n import t

MAX_CUTS = 10
# En cocina se habla en gramos, no en kilos. Cada unidad base tiene su unidad
# pequeña, que es la que se escribe y la que se lee.
SMALL = {Unit.KG: ("g", 1000.0), Unit.L: ("ml", 1000.0), Unit.UNIT: ("", 1.0)}
CATEGORY = "carne"      # lo que se despieza, se cuenta y se descuenta
EXTRA = "extra"         # lo que acompaña en el plato: solo interesa su coste


class MeatError(ValueError):
    """Lo que se ha metido no se puede dar de alta tal y como está."""


# ===================================================== recepción de primales
@dataclass
class PrimalRow:
    serial: str
    kg: float
    price_kg: float | None = None
    sku: str = ""
    grade: str | None = None
    origin: str | None = None
    use_by: date | None = None


def receive_primals(session: Session, user: User, lot: str, rows: list[PrimalRow],
                    received: date | None = None, lang: str = "es") -> list[Primal]:
    """Da de alta un grupo de primales bajo un lote de recepción común.

    Cada pieza lleva su número y su coste: ese coste es el que luego reparte el
    despiece entre los cortes, así que no se pierde por el camino.
    """
    rows = [r for r in rows if r.serial.strip() or r.kg]
    if not rows:
        raise MeatError(t(lang, "m.rec.empty"))
    received = received or date.today()

    seen: set[str] = set()
    for row in rows:
        serial = row.serial.strip()
        if not serial or row.kg <= 0:
            raise MeatError(t(lang, "m.rec.needs"))
        if serial in seen:
            raise MeatError(t(lang, "m.rec.dup", serial=serial))
        seen.add(serial)
        if (session.query(Primal)
                .filter_by(restaurant_id=user.restaurant_id, serial=serial).first()):
            raise MeatError(t(lang, "m.rec.dup", serial=serial))

    created = []
    for row in rows:
        primal = Primal(
            restaurant_id=user.restaurant_id, serial=row.serial.strip(),
            sku=(row.sku or "").strip() or row.serial.strip(),
            grade=(row.grade or None), origin=(row.origin or None),
            weight_kg=row.kg, lot=lot.strip() or None, received_date=received,
            landed_usd_per_kg=row.price_kg,
            piece_cost_usd=round(row.kg * row.price_kg, 4) if row.price_kg else None,
            frozen_use_by=row.use_by, status=PrimalStatus.IN_STOCK)
        session.add(primal)
        created.append(primal)
    session.flush()
    return created


def primals_in_stock(session: Session, restaurant_id: int) -> list[Primal]:
    """Las piezas enteras que todavía se pueden despiezar."""
    return (session.query(Primal)
            .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK)
            .order_by(Primal.sku, Primal.serial).all())


def recent_primals(session: Session, restaurant_id: int, limit: int = 50) -> list[Primal]:
    return (session.query(Primal).filter_by(restaurant_id=restaurant_id)
            .order_by(Primal.received_date.desc(), Primal.id.desc()).limit(limit).all())


# ============================================================ cortes madre
def create_cut(session: Session, user: User, name: str, min_stock: float | None = None,
               rotation: Rotation = Rotation.FEFO,
               consumption: ConsumptionMode = ConsumptionMode.RECIPE) -> Ingredient:
    """Un corte es un ingrediente madre de carne: lo que se cuenta y se vende.

    `consumption` dice de dónde sale el consumo: de la venta en el POS, o del
    recuento de descongelado al cerrar el turno. Las dos cosas a la vez
    descontarían el doble.
    """
    name = name.strip()
    if not name:
        raise MeatError("El corte necesita un nombre")
    if (session.query(Ingredient)
            .filter_by(restaurant_id=user.restaurant_id, name=name).first()):
        raise MeatError(f"Ya hay un corte llamado {name}")
    cut = Ingredient(restaurant_id=user.restaurant_id, name=name, unit=Unit.KG,
                     rotation=rotation, consumption=consumption,
                     min_stock=min_stock, category=CATEGORY)
    session.add(cut)
    session.flush()
    return cut


def add_article(session: Session, user: User, cut: Ingredient, name: str,
                supplier: str | None = None) -> IngredientItem:
    """Una procedencia concreta del mismo corte. Todas se gastan en una cola."""
    name = name.strip()
    if not name:
        raise MeatError("El artículo necesita un nombre")
    item = IngredientItem(restaurant_id=user.restaurant_id, ingredient_id=cut.id,
                          name=name, supplier=(supplier or None))
    session.add(item)
    session.flush()
    return item


def cuts(session: Session, restaurant_id: int) -> list[Ingredient]:
    """Los cortes de carne. La guarnición no se cuenta ni se despieza."""
    return (session.query(Ingredient)
            .filter(Ingredient.restaurant_id == restaurant_id, Ingredient.active.is_(True),
                    (Ingredient.category == CATEGORY) | (Ingredient.category.is_(None)))
            .order_by(Ingredient.name).all())


# ================================================== otros ingredientes del plato
def small_unit(unit: Unit, lang: str = "es") -> str:
    """Cómo se llama la unidad pequeña: gramos, mililitros o unidades."""
    label = SMALL.get(unit, ("", 1.0))[0]
    return label or t(lang, "m.unit.piece")


def to_base(unit: Unit, qty_small: float) -> float:
    """De gramos a kilos, de mililitros a litros. Las unidades no se tocan."""
    return round(qty_small / SMALL.get(unit, ("", 1.0))[1], 6)


def to_small(unit: Unit, qty_base: float) -> float:
    return round(qty_base * SMALL.get(unit, ("", 1.0))[1], 4)


def portion_cost(extra: Ingredient) -> float | None:
    """A cuánto sale la ración: el precio por kilo por lo que lleva el plato."""
    cost = extra_cost(extra)
    if cost is None or not extra.portion_g:
        return None
    return round(cost * to_base(extra.unit, extra.portion_g), 4)


def create_extra(session: Session, user: User, name: str, unit: Unit = Unit.KG,
                 cost: float | None = None, portion_g: float | None = None) -> Ingredient:
    """Lo que acompaña a la carne: guarnición, salsa, pan.

    De esto no se lleva stock —aquí no se cuentan patatas—, pero su coste sí
    cuenta: sin él, el food cost del emplatado se queda corto. Por eso se marca
    para que la venta no intente descontarlo del almacén.
    """
    name = name.strip()
    if not name:
        raise MeatError("El ingrediente necesita un nombre")
    if (session.query(Ingredient)
            .filter_by(restaurant_id=user.restaurant_id, name=name).first()):
        raise MeatError(f"Ya hay un ingrediente llamado {name}")
    if cost is not None and cost < 0:
        raise MeatError("El coste no puede ser negativo")
    if portion_g is not None and portion_g <= 0:
        raise MeatError("La porción tiene que ser mayor que cero")
    extra = Ingredient(restaurant_id=user.restaurant_id, name=name, unit=unit,
                       rotation=Rotation.FIFO, consumption=ConsumptionMode.COUNT,
                       category=EXTRA, portion_g=portion_g)
    session.add(extra)
    session.flush()
    session.add(IngredientItem(restaurant_id=user.restaurant_id, ingredient_id=extra.id,
                               name=name, last_cost=cost))
    session.flush()
    return extra


def set_extra_cost(session: Session, user: User, ingredient_id: int, cost: float,
                   portion_g: float | None = None) -> Ingredient:
    """Cambia el coste configurado y su porción. Se aplica a los platos desde ya."""
    extra = session.get(Ingredient, ingredient_id)
    if extra is None or extra.restaurant_id != user.restaurant_id:
        raise MeatError("Ese ingrediente no es de este restaurante")
    if cost < 0:
        raise MeatError("El coste no puede ser negativo")
    if portion_g is not None:
        if portion_g < 0:
            raise MeatError("La porción no puede ser negativa")
        extra.portion_g = portion_g or None
    item = extra.items[0] if extra.items else None
    if item is None:
        item = IngredientItem(restaurant_id=user.restaurant_id, ingredient_id=extra.id,
                              name=extra.name)
        session.add(item)
    item.last_cost = cost
    session.flush()
    return extra


def extras(session: Session, restaurant_id: int) -> list[Ingredient]:
    return (session.query(Ingredient)
            .filter_by(restaurant_id=restaurant_id, active=True, category=EXTRA)
            .order_by(Ingredient.name).all())


def extra_cost(extra: Ingredient) -> float | None:
    return extra.items[0].last_cost if extra.items else None


def extras_usage(session: Session, restaurant_id: int) -> dict[int, int]:
    """En cuántos platos entra cada ingrediente. Cambiar su coste los mueve todos."""
    counts: dict[int, int] = {}
    for line in (session.query(RecipeLine)
                 .join(Recipe, RecipeLine.recipe_id == Recipe.id)
                 .filter(Recipe.restaurant_id == restaurant_id,
                         Recipe.kind == RecipeKind.DISH,
                         Recipe.active.is_(True),
                         RecipeLine.ingredient_id.isnot(None))):
        counts[line.ingredient_id] = counts.get(line.ingredient_id, 0) + 1
    return counts


def articles(session: Session, restaurant_id: int) -> list[IngredientItem]:
    return (session.query(IngredientItem)
            .filter_by(restaurant_id=restaurant_id, active=True)
            .order_by(IngredientItem.name).all())


# ================================================================= despiece
@dataclass
class CutRow:
    name: str
    item_id: int
    pieces: int
    grams: float
    value_index: float = 1.0
    is_trim: bool = False


def post_butchery(session: Session, user: User, tg: str, serials: list[str],
                  before_kg: float, rows: list[CutRow], waste_kg: float = 0.0,
                  on: date | None = None, staff: str | None = None,
                  country: str | None = None, grade: str | None = None,
                  lang: str = "es") -> tuple[Despiece, butchery.PostResult]:
    """Monta el despiece con lo que se ha escrito y lo vuelca a cámara."""
    rows = [r for r in rows if r.name.strip() and r.pieces and r.grams]
    serials = [s.strip() for s in serials if s.strip()]
    if not serials:
        raise MeatError(t(lang, "m.tg.need_primals"))
    if not rows:
        raise MeatError(t(lang, "m.tg.need_cuts"))
    if any(not r.item_id for r in rows):
        raise MeatError(t(lang, "m.tg.need_article"))
    # El artículo tiene que ser de esta casa. Si no se comprueba, un formulario
    # manipulado mete un lote de este restaurante colgando del corte de otro.
    mine = {i.id for i in session.query(IngredientItem.id)
            .filter_by(restaurant_id=user.restaurant_id)}
    if any(r.item_id not in mine for r in rows):
        raise MeatError(t(lang, "m.tg.need_article"))
    tg = tg.strip()
    if not tg:
        raise MeatError("El despiece necesita su número")
    if session.query(Despiece).filter_by(restaurant_id=user.restaurant_id, tg=tg).first():
        raise MeatError(f"Ya hay un despiece con el número {tg}")

    despiece = Despiece(restaurant_id=user.restaurant_id, tg=tg, date=on or date.today(),
                        staff=(staff or None), weight_before_kg=before_kg,
                        waste_kg=waste_kg, country=(country or None), grade=(grade or None))
    for serial in serials:
        despiece.primals.append(DespiecePrimal(serial=serial))
    for row in rows:
        despiece.cuts.append(DespieceCut(
            cut_name=row.name.strip(), item_id=row.item_id, pieces=row.pieces,
            weight_per_piece_g=row.grams,
            total_kg=round(row.pieces * row.grams / 1000, 4),
            value_index=row.value_index or 1.0, is_trim=row.is_trim))
    session.add(despiece)
    session.flush()

    try:
        result = butchery.post(session, user, despiece)
    except butchery.ButcheryError as e:
        session.delete(despiece)      # no se deja a medias un despiece que no cuadra
        session.flush()
        raise MeatError(str(e)) from None
    return despiece, result


def recent_butchery(session: Session, restaurant_id: int, limit: int = 30) -> list[Despiece]:
    return (session.query(Despiece).filter_by(restaurant_id=restaurant_id)
            .order_by(Despiece.date.desc(), Despiece.id.desc()).limit(limit).all())


def next_tg(session: Session, restaurant_id: int) -> str:
    """El siguiente número de despiece, para no tener que acordarse."""
    n = session.query(Despiece).filter_by(restaurant_id=restaurant_id).count()
    return f"TG-{n + 1:04d}"


# =================================================================== carta
def add_dish(session: Session, user: User, name: str, cut_id: int, grams: float,
             sale_price: float | None = None, vat_pct: float = 0.0,
             pos_code: str | None = None, pos_name: str | None = None,
             lang: str = "es") -> Recipe:
    """Un plato de carne: un corte, unos gramos y su producto del POS.

    Por dentro es una receta de una línea, así que el food cost, el descuento
    de cámara y el reparto del ingreso salen del mismo motor que ya está
    probado, sin una segunda manera de calcular lo mismo.
    """
    name = name.strip()
    if not name:
        raise MeatError("El plato necesita un nombre")
    cut = session.get(Ingredient, cut_id)
    if cut is None or cut.restaurant_id != user.restaurant_id:
        raise MeatError(t(lang, "m.menu.need_cut"))
    if grams <= 0:
        raise MeatError(t(lang, "m.menu.need_cut"))

    code = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")[:64]
    if session.query(Recipe).filter_by(restaurant_id=user.restaurant_id, code=code).first():
        raise MeatError(f"Ya hay un plato llamado {name}")

    dish = Recipe(restaurant_id=user.restaurant_id, code=code, name=name,
                  kind=RecipeKind.DISH, portions=1, sale_price=sale_price, vat_pct=vat_pct)
    session.add(dish)
    session.flush()
    session.add(RecipeLine(recipe_id=dish.id, ingredient_id=cut.id,
                           qty=round(grams / 1000, 6), waste_pct=0.0, sort_order=0))
    session.add(PosProduct(restaurant_id=user.restaurant_id, recipe_id=dish.id,
                           pos_code=(pos_code or "").strip() or None,
                           pos_name=(pos_name or "").strip() or name))
    session.flush()
    return dish


@dataclass
class MenuRow:
    dish: Recipe
    cut: str
    grams: float
    extras: int            # cuántas cosas más van en el plato
    cost: float | None
    food_cost_pct: float | None
    pos_code: str | None
    pos_name: str


def menu(session: Session, restaurant_id: int) -> list[MenuRow]:
    """La carta de carnes, del peor food cost al mejor."""
    costs = costing.unit_costs(session, restaurant_id)
    products = {p.recipe_id: p for p in
                session.query(PosProduct).filter_by(restaurant_id=restaurant_id)}
    rows = []
    for dish in (session.query(Recipe)
                 .filter_by(restaurant_id=restaurant_id, kind=RecipeKind.DISH, active=True)
                 .order_by(Recipe.name)):
        line = meat_line(dish, session)
        cut = session.get(Ingredient, line.ingredient_id) if line and line.ingredient_id else None
        cost = costing.cost_of(session, dish, costs)
        product = products.get(dish.id)
        rows.append(MenuRow(
            dish=dish, cut=cut.name if cut else "", grams=round((line.qty if line else 0) * 1000, 1),
            extras=max(len(dish.lines) - (1 if line else 0), 0),
            cost=cost.cost_per_portion, food_cost_pct=cost.food_cost_pct,
            pos_code=product.pos_code if product else None,
            pos_name=product.pos_name if product else dish.name))
    rows.sort(key=lambda r: (r.food_cost_pct is None, -(r.food_cost_pct or 0)))
    return rows


# =============================================================== emplatado
def meat_line(dish: Recipe, session: Session) -> RecipeLine | None:
    """La línea de carne del plato: la que manda y la que se descuenta."""
    for line in dish.lines:
        if not line.ingredient_id:
            continue
        ingredient = session.get(Ingredient, line.ingredient_id)
        if ingredient is not None and ingredient.category != EXTRA:
            return line
    return None


def add_plate_line(session: Session, user: User, dish: Recipe, ingredient_id: int,
                   qty_small: float, waste_pct: float = 0.0, lang: str = "es") -> RecipeLine:
    """Añade al plato algo que no es la carne. La cantidad, en gramos."""
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None or ingredient.restaurant_id != user.restaurant_id:
        raise MeatError("Ese ingrediente no es de este restaurante")
    if qty_small <= 0:
        raise MeatError(t(lang, "m.plate.qty"))
    qty = to_base(ingredient.unit, qty_small)
    if not 0 <= waste_pct < 100:
        raise MeatError("La merma de limpieza va entre 0 y 100")
    # Detrás de lo que ya hay, para que la carne siga la primera y el orden del
    # plato sea el orden en que se fue montando.
    last = max((l.sort_order for l in dish.lines), default=0)
    line = RecipeLine(recipe_id=dish.id, ingredient_id=ingredient.id, qty=qty,
                      waste_pct=waste_pct, sort_order=last + 10)
    session.add(line)
    session.flush()
    return line


def remove_plate_line(session: Session, user: User, dish: Recipe, line_id: int,
                      lang: str = "es") -> None:
    """Quita del plato una línea. La de carne no se quita: el plato es de carne."""
    line = session.get(RecipeLine, line_id)
    if line is None or line.recipe_id != dish.id:
        raise MeatError("Esa línea no es de este plato")
    carne = meat_line(dish, session)
    if carne is not None and line.id == carne.id:
        raise MeatError(t(lang, "m.menu.need_cut"))
    session.delete(line)
    session.flush()


def set_plate_grams(session: Session, user: User, dish: Recipe, grams: float,
                    lang: str = "es") -> None:
    """Cambia el gramaje de carne del plato, que es lo que se descuenta."""
    if grams <= 0:
        raise MeatError(t(lang, "m.menu.need_cut"))
    line = meat_line(dish, session)
    if line is None:
        raise MeatError(t(lang, "m.menu.need_cut"))
    line.qty = round(grams / 1000, 6)
    session.flush()


@dataclass
class PlateLine:
    line_id: int | None
    name: str
    unit: str
    qty: float                 # en la unidad base, que es como se guarda
    qty_small: float           # y en gramos, que es como se lee
    small: str                 # g, ml o unidades
    waste_pct: float
    gross_qty: float
    gross_small: float
    unit_cost: float | None    # por kilo, por litro o por unidad
    cost: float                # lo que sale esa porción
    share_pct: float
    is_meat: bool


@dataclass
class Plate:
    dish: Recipe
    lines: list[PlateLine]
    cost: float
    food_cost_pct: float | None
    margin: float | None
    pos_code: str | None
    pos_name: str
    missing_price: list[str]

    @property
    def meat(self) -> PlateLine | None:
        return next((l for l in self.lines if l.is_meat), None)

    @property
    def extras(self) -> list[PlateLine]:
        return [l for l in self.lines if not l.is_meat]


def plate(session: Session, restaurant_id: int, dish: Recipe, lang: str = "es") -> Plate:
    """El emplatado entero: qué lleva, qué cuesta cada cosa y su food cost."""
    costs = costing.unit_costs(session, restaurant_id)
    detail = costing.cost_of(session, dish, costs)
    carne = meat_line(dish, session)
    product = (session.query(PosProduct)
               .filter_by(restaurant_id=restaurant_id, recipe_id=dish.id).first())

    lines = []
    for line, computed in zip(dish.lines, detail.lines):
        ingredient = session.get(Ingredient, line.ingredient_id) if line.ingredient_id else None
        unit = ingredient.unit if ingredient else Unit.KG
        lines.append(PlateLine(
            line_id=line.id, name=computed.label, unit=computed.unit,
            qty=computed.net_qty, qty_small=to_small(unit, computed.net_qty),
            small=small_unit(unit, lang), waste_pct=computed.waste_pct,
            gross_qty=computed.gross_qty, gross_small=to_small(unit, computed.gross_qty),
            unit_cost=computed.unit_cost, cost=computed.cost, share_pct=computed.share_pct,
            is_meat=carne is not None and line.id == carne.id))
    return Plate(dish=dish, lines=lines, cost=detail.cost_per_portion,
                 food_cost_pct=detail.food_cost_pct, margin=detail.margin_per_portion,
                 pos_code=product.pos_code if product else None,
                 pos_name=product.pos_name if product else dish.name,
                 missing_price=list(detail.missing))


# ==================================================================== hoy
@dataclass
class Today:
    status: butchery.MeatStatus
    pending: list[str] = field(default_factory=list)
    stock_value: float = 0.0
    thawing: int = 0
    uncounted: int = 0
    open_count: MeatCount | None = None
    month_due: bool = False
    aging: aging_mod.Summary | None = None


def today(session: Session, restaurant_id: int, on: date | None = None,
          lang: str = "es") -> Today:
    """Lo que está pendiente en la carne, en una pantalla."""
    on = on or date.today()
    status = butchery.status(session, restaurant_id, on=on)
    value = round(sum(lot.qty_remaining * lot.unit_cost for lot in
                      session.query(IngredientLot)
                      .filter(IngredientLot.restaurant_id == restaurant_id,
                              IngredientLot.qty_remaining > 1e-9)), 2)

    states = defrost.shift_states(session, restaurant_id, on)
    thawing = [s for s in states if s.opening_pieces or s.intake_pieces]
    # Salió a descongelar y nadie ha contado lo que quedaba: sin eso no hay cierre.
    uncounted = [s for s in thawing if s.closing_pieces is None]

    # Lo que madura: las que ya han cumplido sus días y las que llevan una
    # semana sin pesar, que es cuando la merma deja de estar controlada.
    aging_rows = aging_mod.board(session, restaurant_id, on=on)
    ready = [r for r in aging_rows if r.storage == Storage.AGING and r.ready]
    stale = [r for r in aging_rows if r.storage == Storage.AGING
             and (on - (r.last_weighed or r.since or on)).days > 7]

    month = inventory.monthly_status(session, restaurant_id, on=on)
    open_count = (session.query(MeatCount)
                  .filter_by(restaurant_id=restaurant_id, status=CountStatus.OPEN).first())
    unposted = (session.query(Despiece)
                .filter_by(restaurant_id=restaurant_id, posted=False).count())

    pending = []
    if uncounted:
        pending.append(t(lang, "m.home.defrost_open", n=len(uncounted)))
    if not month.done:
        pending.append(t(lang, "m.home.count_due"))
    below = len(status.cuts_below) + len(status.primals_below)
    if below:
        pending.append(t(lang, "m.home.below_par", n=below))
    if status.expiring:
        pending.append(t(lang, "m.home.expiring", n=len(status.expiring)))
    if unposted:
        pending.append(t(lang, "m.home.unposted", n=unposted))
    if ready:
        pending.append(t(lang, "m.home.aging_ready", n=len(ready)))
    if stale:
        pending.append(t(lang, "m.home.aging_unweighed", n=len(stale)))

    return Today(status=status, pending=pending, stock_value=value,
                 thawing=len(thawing), uncounted=len(uncounted),
                 open_count=open_count, month_due=not month.done,
                 aging=aging_mod.summary(session, restaurant_id, on=on))
