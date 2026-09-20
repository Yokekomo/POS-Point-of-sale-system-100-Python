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
                             RecipeKind, RecipeLine, Rotation, Unit, User)
from thegrill.web import butchery, costing, defrost, inventory
from thegrill.web.i18n import t

MAX_CUTS = 10
CATEGORY = "carne"


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
               rotation: Rotation = Rotation.FEFO) -> Ingredient:
    """Un corte es un ingrediente madre de carne: lo que se cuenta y se vende."""
    name = name.strip()
    if not name:
        raise MeatError("El corte necesita un nombre")
    if (session.query(Ingredient)
            .filter_by(restaurant_id=user.restaurant_id, name=name).first()):
        raise MeatError(f"Ya hay un corte llamado {name}")
    cut = Ingredient(restaurant_id=user.restaurant_id, name=name, unit=Unit.KG,
                     rotation=rotation, consumption=ConsumptionMode.RECIPE,
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
    return (session.query(Ingredient)
            .filter_by(restaurant_id=restaurant_id, active=True)
            .order_by(Ingredient.name).all())


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
        line = dish.lines[0] if dish.lines else None
        cut = session.get(Ingredient, line.ingredient_id) if line and line.ingredient_id else None
        cost = costing.cost_of(session, dish, costs)
        product = products.get(dish.id)
        rows.append(MenuRow(
            dish=dish, cut=cut.name if cut else "", grams=round((line.qty if line else 0) * 1000, 1),
            cost=cost.cost_per_portion, food_cost_pct=cost.food_cost_pct,
            pos_code=product.pos_code if product else None,
            pos_name=product.pos_name if product else dish.name))
    rows.sort(key=lambda r: (r.food_cost_pct is None, -(r.food_cost_pct or 0)))
    return rows


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

    return Today(status=status, pending=pending, stock_value=value,
                 thawing=len(thawing), uncounted=len(uncounted),
                 open_count=open_count, month_due=not month.done)
