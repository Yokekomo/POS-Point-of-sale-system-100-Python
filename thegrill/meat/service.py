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
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thegrill.models import (ConsumptionMode, CountStatus, Despiece, DespieceCut,
                             DespiecePrimal, Ingredient, IngredientItem, IngredientLot,
                             MeatCount, PosProduct, Primal, PrimalStatus, Recipe,
                             RecipeKind, RecipeLine, Rotation, Storage, Unit, User)
from thegrill.web import aging as aging_mod
from thegrill.web import waste as waste_mod
from thegrill.web import butchery, costing, defrost, inventory, locking, sites
from thegrill.web.i18n import t

MAX_CUTS = 10
# En cocina se habla en gramos, no en kilos. Cada unidad base tiene su unidad
# pequeña, que es la que se escribe y la que se lee.
SMALL = {Unit.KG: ("g", 1000.0), Unit.L: ("ml", 1000.0), Unit.UNIT: ("", 1.0)}
CATEGORY = "carne"      # lo que se despieza, se cuenta y se descuenta
EXTRA = "extra"         # lo que acompaña en el plato: solo interesa su coste


AUTO_TG = re.compile(r"^TG-\d{4}$")      # el que propone la pantalla


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
    # El número lo ha puesto la casa, no el proveedor: si otra recepción se
    # adelanta con ese mismo número, este se puede cambiar sin preguntar.
    auto: bool = False


def next_lot(session: Session, restaurant_id: int, on: date | None = None) -> str:
    """El número del próximo lote de recepción: la fecha y un orden del día.

    Nadie tiene que inventarse un código en el muelle con el camión esperando.
    Se propone uno —`L-260921-1`— y se confirma al dar de alta: hasta entonces
    no existe, así que abrir la pantalla y cerrarla no quema ningún número.
    """
    on = on or date.today()
    base = f"L-{on:%y%m%d}"
    usados = {p.lot for p in session.query(Primal.lot)
              .filter(Primal.restaurant_id == restaurant_id,
                      Primal.lot.like(f"{base}%"))}
    for n in range(1, 100):
        propuesto = f"{base}-{n}"
        if propuesto not in usados:
            return propuesto
    return f"{base}-{len(usados) + 1}"


def next_serials(session: Session, restaurant_id: int, count: int = 1) -> list[str]:
    """Los próximos números de pieza, siguiendo por donde iba la casa.

    Si los que hay son números, se sigue contando; si no lo son —porque el
    proveedor los trae con letras—, se empieza una serie propia. Se proponen,
    y solo se quedan cogidos cuando la recepción se da de alta.
    """
    numeros = []
    for (serial,) in session.query(Primal.serial).filter_by(restaurant_id=restaurant_id):
        limpio = (serial or "").strip()
        if limpio.isdigit():
            numeros.append(int(limpio))
    siguiente = (max(numeros) + 1) if numeros else 8001
    ancho = max(4, len(str(siguiente)))
    return [f"{siguiente + i:0{ancho}d}" for i in range(max(1, count))]


def receive_primals(session: Session, user: User, lot: str, rows: list[PrimalRow],
                    received: date | None = None, lang: str = "es",
                    chamber: str | None = None) -> list[Primal]:
    """Da de alta un grupo de primales bajo un lote de recepción común.

    Cada pieza lleva su número y su coste: ese coste es el que luego reparte el
    despiece entre los cortes, así que no se pierde por el camino.
    """
    rows = [r for r in rows if r.serial.strip() or r.kg]
    # La carne entra donde está quien la recibe: el obrador, casi siempre.
    destino = sites.of_user(session, user) or sites.main(session, user.restaurant_id)
    if not rows:
        raise MeatError(t(lang, "m.rec.empty"))
    received = received or date.today()
    lot = (lot or "").strip() or next_lot(session, user.restaurant_id, received)

    # Los números que no se hayan escrito se ponen aquí, al dar de alta, y no
    # al abrir la pantalla: dos personas recibiendo a la vez no se pisan, y el
    # que abre y cierra no deja un hueco en la serie.
    faltan = [r for r in rows if not r.serial.strip()]
    for row in faltan:
        row.auto = True           # numerada por la casa: si choca, se renumera
    if faltan:
        libres = next_serials(session, user.restaurant_id, len(faltan) + len(rows))
        escritos = {r.serial.strip() for r in rows if r.serial.strip()}
        for row in faltan:
            while libres and (libres[0] in escritos or
                              session.query(Primal).filter_by(
                                  restaurant_id=user.restaurant_id,
                                  serial=libres[0]).first()):
                libres.pop(0)
            if not libres:
                raise MeatError(t(lang, "m.rec.needs"))
            row.serial = libres.pop(0)
            escritos.add(row.serial)

    seen: set[str] = set()
    for row in rows:
        serial = row.serial.strip()
        if not serial or row.kg <= 0:
            raise MeatError(t(lang, "m.rec.needs"))
        if serial in seen:
            raise MeatError(t(lang, "m.rec.dup", serial=serial))
        seen.add(serial)
        # Al número que ha escrito una persona se le dice aquí que ya existe,
        # que es lo que quiere oír: se ha equivocado de pieza. Al que ha puesto
        # la casa no se le dice nada —lo elegimos nosotros—: si justo lo acaba
        # de coger otra recepción, se cambia al guardar y nadie se entera.
        if not getattr(row, "auto", False) and (
                session.query(Primal)
                .filter_by(restaurant_id=user.restaurant_id, serial=serial).first()):
            raise MeatError(t(lang, "m.rec.dup", serial=serial))

    return _save_primals(session, user, rows, lot, received, destino, chamber, lang)


def _save_primals(session: Session, user: User, rows: list[PrimalRow], lot: str,
                  received: date, destino, chamber: str | None, lang: str,
                  intentos: int = 3) -> list[Primal]:
    """Escribe las piezas. Si dos muelles dan de alta a la vez, se renumera.

    Los números automáticos se piden justo antes de guardar, pero entre pedirlos
    y guardarlos cabe otra recepción: los dos piden el 8016 y el segundo se
    estrellaba con un error rojo con el camión esperando y la hoja entera por
    volver a escribir. Ahora se vuelve a intentar con los siguientes libres y
    el de fuera ni se entera.
    """
    automaticos = [r for r in rows if getattr(r, "auto", False)]

    def otros_numeros():
        if not automaticos:
            raise MeatError(t(lang, "m.rec.dup", serial=rows[0].serial)) from None
        _renumber(session, user.restaurant_id, rows, automaticos, lang)

    def escribir():
        return _insert_primals(session, user, rows, lot, received, destino, chamber)

    try:
        return locking.retry(session, escribir, otros_numeros, intentos=intentos)
    except IntegrityError:
        raise MeatError(t(lang, "m.rec.dup", serial=rows[0].serial)) from None


def _renumber(session: Session, restaurant_id: int, rows: list[PrimalRow],
              automaticos: list[PrimalRow], lang: str) -> None:
    """Vuelve a repartir los números que había puesto la casa."""
    libres = next_serials(session, restaurant_id, len(automaticos) + len(rows) + 4)
    ocupados = {r.serial.strip() for r in rows if r not in automaticos}
    for row in automaticos:
        while libres and (libres[0] in ocupados or
                          session.query(Primal).filter_by(
                              restaurant_id=restaurant_id, serial=libres[0]).first()):
            libres.pop(0)
        if not libres:
            raise MeatError(t(lang, "m.rec.needs"))
        row.serial = libres.pop(0)
        ocupados.add(row.serial)


def _insert_primals(session: Session, user: User, rows: list[PrimalRow], lot: str,
                    received: date, destino, chamber: str | None) -> list[Primal]:
    created = []
    for row in rows:
        primal = Primal(
            restaurant_id=user.restaurant_id, serial=row.serial.strip(),
            sku=(row.sku or "").strip() or row.serial.strip(),
            grade=(row.grade or None), origin=(row.origin or None),
            weight_kg=row.kg, received_kg=row.kg, lot=lot.strip() or None,
            received_date=received, site_id=destino.id,
            chamber=(chamber or "").strip()[:48] or None,
            landed_usd_per_kg=row.price_kg,
            piece_cost_usd=round(row.kg * row.price_kg, 4) if row.price_kg else None,
            frozen_use_by=row.use_by, status=PrimalStatus.IN_STOCK)
        session.add(primal)
        created.append(primal)
    session.flush()
    return created


def primals_in_stock(session: Session, restaurant_id: int,
                     site_id: int | None = None) -> list[Primal]:
    """Las piezas enteras que todavía se pueden despiezar.

    Con sede, las que están en esa sede: el local corta lo suyo, no lo que
    está colgado en el obrador.
    """
    rows = (session.query(Primal)
            .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK)
            .order_by(Primal.sku, Primal.serial).all())
    if not site_id:
        return rows
    principal = sites.main(session, restaurant_id).id
    return [p for p in rows if (p.site_id or principal) == site_id]


def recent_primals(session: Session, restaurant_id: int, limit: int = 50) -> list[Primal]:
    return (session.query(Primal).filter_by(restaurant_id=restaurant_id)
            .order_by(Primal.received_date.desc(), Primal.id.desc()).limit(limit).all())


# ============================================================ cortes madre
def create_cut(session: Session, user: User, name: str, min_stock: float | None = None,
               rotation: Rotation = Rotation.FEFO,
               consumption: ConsumptionMode = ConsumptionMode.RECIPE,
               sold_by_weight: bool = False) -> Ingredient:
    """Un corte es un ingrediente madre de carne: lo que se cuenta y se vende.

    `consumption` dice de dónde sale el consumo: de la venta en el POS, o del
    recuento de descongelado al cerrar el turno. Las dos cosas a la vez
    descontarían el doble.

    `sold_by_weight` es el corte que no se raciona: entra entero y limpio en
    cámara y se corta delante del cliente, así que lo que descuenta cada venta
    son los gramos que manda el POS, no un gramaje de carta.
    """
    name = name.strip()
    if not name:
        raise MeatError("El corte necesita un nombre")
    if (session.query(Ingredient)
            .filter_by(restaurant_id=user.restaurant_id, name=name).first()):
        raise MeatError(f"Ya hay un corte llamado {name}")
    cut = Ingredient(restaurant_id=user.restaurant_id, name=name, unit=Unit.KG,
                     rotation=rotation, consumption=consumption,
                     min_stock=min_stock, category=CATEGORY,
                     sold_by_weight=bool(sold_by_weight))
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
    by_weight: bool = False     # sale entero: se corta al vender
    kg: float = 0.0             # solo para los que salen a peso


def post_butchery(session: Session, user: User, tg: str, serials: list[str],
                  before_kg: float, rows: list[CutRow], waste_kg: float = 0.0,
                  on: date | None = None, staff: str | None = None,
                  country: str | None = None, grade: str | None = None,
                  lang: str = "es") -> tuple[Despiece, butchery.PostResult]:
    """Monta el despiece con lo que se ha escrito y lo vuelca a cámara."""
    # Una fila vale si dice cuántas piezas y de cuántos gramos, o —si sale a
    # peso— cuántos kilos entran enteros en cámara.
    rows = [r for r in rows if r.name.strip()
            and ((r.by_weight and r.kg) or (not r.by_weight and r.pieces and r.grams))]
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
    # Todas las piezas de un despiece tienen que estar donde se despieza: un
    # despiece que mezcla la cámara del obrador con la del local no ha pasado
    # por ninguna mesa, y los cortes que salen no sabrían de dónde son.
    piezas = (session.query(Primal)
              .filter(Primal.restaurant_id == user.restaurant_id,
                      Primal.serial.in_(serials)).all())
    principal = sites.main(session, user.restaurant_id).id
    donde = {(p.site_id or principal) for p in piezas}
    mia = sites.of_user(session, user)
    if len(donde) > 1:
        raise MeatError(t(lang, "m.tg.mixed_sites"))
    if mia is not None and donde and mia.id not in donde:
        nombres = {x.id: x.name for x in sites.all_sites(session, user.restaurant_id,
                                                         active=False)}
        raise MeatError(t(lang, "m.tg.other_site",
                          site=nombres.get(next(iter(donde)), "")))
    tg = tg.strip()
    if not tg:
        raise MeatError("El despiece necesita su número")
    if session.query(Despiece).filter_by(restaurant_id=user.restaurant_id, tg=tg).first():
        # Si el número lo puso la casa —TG-0007, el que propone la pantalla—,
        # dos carniceros que abren la hoja a la vez traen el mismo y el segundo
        # perdía el despiece entero por un número. Se le da el siguiente libre.
        # Si el número lo escribió una persona, no se toca: ahí sí hay que
        # mirar qué despiece es el que ya existe.
        libre = _free_tg(session, user.restaurant_id) if AUTO_TG.match(tg) else None
        if libre is None:
            raise MeatError(f"Ya hay un despiece con el número {tg}")
        tg = libre

    numero = {"tg": tg}

    def otro_numero():
        libre = (_free_tg(session, user.restaurant_id)
                 if AUTO_TG.match(numero["tg"]) else None)
        if libre is None:
            raise MeatError(f"Ya hay un despiece con el número {numero['tg']}") from None
        numero["tg"] = libre

    def montar():
        return _write_despiece(session, user, numero["tg"], serials, before_kg, rows,
                               waste_kg, on, staff, country, grade)

    return locking.retry(session, montar, otro_numero)


def _write_despiece(session: Session, user: User, tg: str, serials: list[str],
                    before_kg: float, rows: list[CutRow], waste_kg: float,
                    on: date | None, staff: str | None, country: str | None,
                    grade: str | None) -> tuple[Despiece, butchery.PostResult]:
    """Escribe el despiece entero. Se monta de cero en cada intento."""
    despiece = Despiece(restaurant_id=user.restaurant_id, tg=tg, date=on or date.today(),
                        staff=(staff or None), weight_before_kg=before_kg,
                        waste_kg=waste_kg, country=(country or None), grade=(grade or None))
    for serial in serials:
        despiece.primals.append(DespiecePrimal(serial=serial))
    for row in rows:
        despiece.cuts.append(DespieceCut(
            cut_name=row.name.strip(), item_id=row.item_id,
            pieces=0 if row.by_weight else row.pieces,
            weight_per_piece_g=0.0 if row.by_weight else row.grams,
            total_kg=round(row.kg, 4) if row.by_weight
            else round(row.pieces * row.grams / 1000, 4),
            value_index=row.value_index or 1.0, is_trim=row.is_trim,
            by_weight=row.by_weight))
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


def _free_tg(session: Session, restaurant_id: int) -> str | None:
    """Un número de despiece que no esté cogido, empezando por el siguiente."""
    usados = {d.tg for d in session.query(Despiece.tg)
              .filter(Despiece.restaurant_id == restaurant_id)}
    n = len(usados) + 1
    for _ in range(200):
        propuesto = f"TG-{n:04d}"
        if propuesto not in usados:
            return propuesto
        n += 1
    return None


# =================================================================== carta
def add_dish(session: Session, user: User, name: str, cut_id: int, grams: float,
             sale_price: float | None = None, vat_pct: float = 0.0,
             pos_code: str | None = None, pos_name: str | None = None,
             by_weight: bool = False, price_per_kg: float | None = None,
             lang: str = "es") -> Recipe:
    """Un plato de carne: un corte, unos gramos y su producto del POS.

    Por dentro es una receta de una línea, así que el food cost, el descuento
    de cámara y el reparto del ingreso salen del mismo motor que ya está
    probado, sin una segunda manera de calcular lo mismo.

    Un plato **a peso** es el mismo plato con otra manera de cobrar: el precio
    va por kilo y los gramos los manda el POS en cada venta. Los gramos que se
    escriben aquí son la ración de referencia, la que sirve para ver el food
    cost en la carta antes de vender nada.
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

    if by_weight and not price_per_kg:
        raise MeatError(t(lang, "m.menu.need_price_kg"))
    if by_weight and sale_price is None:
        # El PVP de la ración de referencia, para que la carta sepa comparar.
        sale_price = round(price_per_kg * grams / 1000, 4)

    dish = Recipe(restaurant_id=user.restaurant_id, code=code, name=name,
                  kind=RecipeKind.DISH, portions=1, sale_price=sale_price, vat_pct=vat_pct,
                  by_weight=bool(by_weight), price_per_kg=price_per_kg)
    session.add(dish)
    session.flush()
    session.add(RecipeLine(recipe_id=dish.id, ingredient_id=cut.id,
                           qty=round(grams / 1000, 6), waste_pct=0.0, sort_order=0,
                           by_weight=bool(by_weight)))
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


# ============================================================ parte del día
@dataclass
class DailyReport:
    """El parte de carne de un día, para el pase y para la carpeta.

    Es la foto que se cuelga: lo que hay, lo que se ha ido y lo que queda
    pendiente. Se imprime desde el navegador, que es lo que hay en una cocina,
    y sale igual en papel que en pantalla.
    """
    date: date
    site: str = ""
    status: butchery.MeatStatus | None = None
    aging: aging_mod.Summary | None = None
    to_weigh: list = field(default_factory=list)       # las que maduran sin pesar hoy
    counted: list = field(default_factory=list)        # lo pesado hoy, pieza a pieza
    thawing: list = field(default_factory=list)        # los números descongelando
    shifts: list = field(default_factory=list)         # los turnos cerrados del día
    waste: list = field(default_factory=list)          # lo tirado hoy
    sales_units: int = 0
    sales_kg: float = 0.0
    pending: list[str] = field(default_factory=list)
    stock_value: float = 0.0

    @property
    def waste_kg(self) -> float:
        return round(sum(w.kg for w in self.waste), 3)

    @property
    def waste_cost(self) -> float:
        return round(sum(w.cost or 0.0 for w in self.waste), 2)

    @property
    def day_loss(self) -> float:
        """Lo que se ha ido hoy en dinero: el desvío del turno, el agua y la merma."""
        return round(sum((c.loss_cost or 0.0) + (c.drip_cost or 0.0) for c in self.shifts)
                     + self.waste_cost, 2)


def daily_report(session: Session, restaurant_id: int, on: date | None = None,
                 lang: str = "es", site_id: int | None = None) -> DailyReport:
    """El parte del día: lo que hay, lo que se ha ido y lo que falta por hacer."""
    from thegrill.models import SalesByProduct, ShiftClosure, Site

    on = on or date.today()
    hoy = today(session, restaurant_id, on=on, lang=lang, site_id=site_id)
    sede = session.get(Site, site_id) if site_id else None
    out = DailyReport(date=on, site=sede.name if sede else "", status=hoy.status,
                      aging=hoy.aging, pending=hoy.pending, stock_value=hoy.stock_value)

    out.to_weigh = [l for l in aging_mod.to_count(session, restaurant_id, on, site_id)
                    if l.kg is None]
    out.counted = [l for l in aging_mod.to_count(session, restaurant_id, on, site_id)
                   if l.kg is not None]
    out.thawing = [st for st in defrost.shift_states(session, restaurant_id, on,
                                                     site_id=site_id)
                   if st.intake_pieces or st.opening_pieces]
    query = (session.query(ShiftClosure)
             .filter(ShiftClosure.restaurant_id == restaurant_id, ShiftClosure.date == on))
    if site_id:
        query = query.filter(ShiftClosure.site_id == site_id)
    out.shifts = query.order_by(ShiftClosure.shift).all()
    out.waste = [w for w in waste_mod.everything(session, restaurant_id, days=1)
                 if w.date == on]
    for row in (session.query(SalesByProduct)
                .filter_by(restaurant_id=restaurant_id, op_date=on)):
        out.sales_units += row.units or 0
        out.sales_kg = round(out.sales_kg + (row.kg or 0.0), 6)
    return out


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
          lang: str = "es", site_id: int | None = None) -> Today:
    """Lo que está pendiente en la carne, en una pantalla.

    Con sede, lo pendiente de esa sede: el del local no arregla la cámara del
    obrador, y las piezas que maduran en su local las pesa él.
    """
    on = on or date.today()
    status = butchery.status(session, restaurant_id, on=on, site_id=site_id)
    value = round(sum(lot.qty_remaining * lot.unit_cost for lot in
                      costing.at_site(session.query(IngredientLot)
                                      .filter(IngredientLot.restaurant_id == restaurant_id,
                                              IngredientLot.qty_remaining > 1e-9),
                                      session, restaurant_id, site_id)), 2)

    states = defrost.shift_states(session, restaurant_id, on)
    thawing = [s for s in states if s.opening_pieces or s.intake_pieces]
    # Salió a descongelar y nadie ha contado lo que quedaba: sin eso no hay cierre.
    uncounted = [s for s in thawing if s.closing_pieces is None]

    # Lo que madura: las que ya han cumplido sus días y las que llevan una
    # semana sin pesar, que es cuando la merma deja de estar controlada.
    aging_rows = aging_mod.board(session, restaurant_id, on=on, site_id=site_id)
    ready = [r for r in aging_rows if r.storage == Storage.AGING and r.ready]
    # Lo que madura está fresco y abierto: se pesa todos los días, como se
    # cuenta lo descongelado. Sin ese peso, la merma del día no existe.
    stale = [r for r in aging_rows if r.storage == Storage.AGING and r.last_weighed != on]

    month = inventory.monthly_status(session, restaurant_id, on=on, site_id=site_id)
    open_count = inventory.open_now(session, restaurant_id, site_id)
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
                 aging=aging_mod.summary(session, restaurant_id, on=on, site_id=site_id))
