"""[01066] Precios, stock y descuento por consumo.

Une la base de datos con el motor de escandallo:

- El **precio de un ingrediente madre** es la media ponderada de lo que queda
  en sus lotes, sea cual sea la marca. Sin stock, el último precio conocido de
  sus artículos. Sin ninguno de los dos, el precio es desconocido y se dice:
  nunca se cuenta como cero.
- El **consumo** explota la receta hasta ingredientes madre y descuenta por
  FEFO entre todos los lotes de esa madre, mezclando marcas: sale antes lo que
  antes caduca.
- Si no hay bastante stock se descuenta lo que hay, se registra el faltante y
  se abre una alerta. El faltante significa que alguien no registró una entrada.

Y el descuento sale de donde se ha vendido. La carne de un grupo está en una
sede: la del obrador no la sirve nadie en la playa, y descontarla allí es
cuadrar el papel descuadrando la cámara. Quien vende con sede puesta descuenta
de su sede; quien no la tiene —el manager que mete las ventas de la casa—
descuenta de la casa entera, como siempre.
"""
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from thegrill.engine import fefo
from thegrill.engine.recipes import (RecipeCost, cost_recipe, explode, menu_ranking,
                                      por_raciones)
from thegrill.models import (Alert, AlertSeverity, ConsumptionMode, Ingredient,
                             IngredientItem, IngredientLot, IngredientMovement,
                             MovementKind, PosMatch, PosProduct, Recipe, RecipeKind,
                             Restaurant, SalesByProduct, Site, User)
from thegrill.web import jornada, locking, service, sites
from thegrill.web.i18n import DEFAULT_LANG, Aviso, t

EPSILON = 1e-9


class NotMapped(KeyError):
    """[01067] Un producto del POS sin receta detrás: no se puede descontar nada."""


def _norm(value: str | None) -> str:
    """[01068] Deja un texto comparable: mayúsculas, sin espacios de sobra."""
    return " ".join(str(value or "").strip().upper().split())


def pos_index(session: Session, restaurant_id: int) -> dict[str, PosProduct]:
    """[01069] Índice de búsqueda del POS, según cómo identifique este restaurante.

    Unos POS mandan el número de artículo y otros el nombre. El restaurante
    elige en su configuración; con «ambos», el código manda, porque un nombre
    se reescribe y un código no.
    """
    restaurant = session.get(Restaurant, restaurant_id)
    mode = restaurant.pos_match if restaurant else PosMatch.BOTH
    products = session.query(PosProduct).filter_by(restaurant_id=restaurant_id).all()

    index: dict[str, PosProduct] = {}
    if mode in (PosMatch.NAME, PosMatch.BOTH):
        for product in products:
            if product.pos_name:
                index.setdefault(_norm(product.pos_name), product)
    if mode in (PosMatch.CODE, PosMatch.BOTH):
        for product in products:
            if product.pos_code:
                index[_norm(product.pos_code)] = product
    return index


# ------------------------------------------------------------------ precios
def unit_costs(session: Session, restaurant_id: int) -> dict[int, float]:
    """[01070] Precio por unidad base de cada ingrediente madre."""
    costs: dict[int, float] = {}
    weighted: dict[int, list[float]] = {}
    for lot in (session.query(IngredientLot)
                .filter(IngredientLot.restaurant_id == restaurant_id,
                        IngredientLot.qty_remaining > EPSILON)):
        value, qty = weighted.setdefault(lot.ingredient_id, [0.0, 0.0])
        weighted[lot.ingredient_id] = [value + lot.qty_remaining * lot.unit_cost,
                                       qty + lot.qty_remaining]
    for ingredient_id, (value, qty) in weighted.items():
        if qty > EPSILON:
            costs[ingredient_id] = round(value / qty, 6)

    # [01083] Sin stock: el último precio conocido de cualquiera de sus artículos.
    for item in (session.query(IngredientItem)
                 .filter(IngredientItem.restaurant_id == restaurant_id,
                         IngredientItem.last_cost.isnot(None))):
        costs.setdefault(item.ingredient_id, round(item.last_cost, 6))
    return costs


def at_site(query, session: Session, restaurant_id: int, site_id: int | None):
    """[01071] Deja en la consulta solo los lotes de esa sede.

    La carne que no dice dónde está, está en la principal: así una casa de toda
    la vida —que no ha oído hablar de sedes— sigue funcionando igual.
    """
    if not site_id:
        return query
    if site_id == sites.main(session, restaurant_id).id:
        return query.filter((IngredientLot.site_id == site_id)
                            | (IngredientLot.site_id.is_(None)))
    return query.filter(IngredientLot.site_id == site_id)


def elsewhere_on_hand(session: Session, restaurant_id: int,
                      site_id: int | None) -> dict[int, tuple[float, set[str]]]:
    """[01072] Lo que hay de cada madre en las **otras** sedes, y en cuáles.

    No falta carne: está en otro sitio. Lo que hace falta es un traslado, y
    decir eso es más útil que decir que no hay.
    """
    if not site_id:
        return {}
    principal = sites.main(session, restaurant_id)
    nombres = {s.id: s.name for s in sites.all_sites(session, restaurant_id, active=False)}
    out: dict[int, tuple[float, set[str]]] = {}
    for lot in (session.query(IngredientLot)
                .filter(IngredientLot.restaurant_id == restaurant_id,
                        IngredientLot.frozen.isnot(True),
                        IngredientLot.qty_remaining > EPSILON)):
        donde = lot.site_id or principal.id
        if donde == site_id:
            continue
        qty, casas = out.get(lot.ingredient_id, (0.0, set()))
        casas.add(nombres.get(donde, ""))
        out[lot.ingredient_id] = (round(qty + lot.qty_remaining, 6), casas)
    return out


def frozen_on_hand(session: Session, restaurant_id: int,
                   site_id: int | None = None) -> dict[int, float]:
    """[01073] Lo que hay congelado de cada madre: existe, pero todavía no se vende."""
    out: dict[int, float] = {}
    query = at_site(session.query(IngredientLot)
                    .filter(IngredientLot.restaurant_id == restaurant_id,
                            IngredientLot.frozen.is_(True),
                            IngredientLot.qty_remaining > EPSILON),
                    session, restaurant_id, site_id)
    for lot in query:
        out[lot.ingredient_id] = round(out.get(lot.ingredient_id, 0.0) + lot.qty_remaining, 6)
    return out


def stock_on_hand(session: Session, restaurant_id: int) -> dict[int, float]:
    """[01074] Cantidad que queda de cada ingrediente madre, sumando marcas."""
    out: dict[int, float] = {}
    for lot in (session.query(IngredientLot)
                .filter(IngredientLot.restaurant_id == restaurant_id)):
        out[lot.ingredient_id] = round(out.get(lot.ingredient_id, 0.0) + lot.qty_remaining, 6)
    return out


def rotation_order(session: Session, restaurant_id: int, ingredient: Ingredient,
                   include_frozen: bool = False,
                   site_id: int | None = None) -> list[IngredientLot]:
    """[01075] Lotes con existencias de una madre, en el orden en que deben salir.

    Compiten todos los lotes de todas sus marcas: el ingrediente madre existe
    precisamente para poder gastarlos en una sola cola.

    Lo congelado no compite. Un número congelado está **en espera**: no se
    vende hasta que alguien lo saca a descongelar, y hasta entonces descontarle
    una venta es apuntar que se ha servido carne que sigue dura en el arcón. Lo
    que se vende son los números descongelados; los otros esperan su turno.
    `include_frozen` es para contar, no para vender.

    Con `site_id`, solo compiten los números que están en esa sede: lo que hay
    en el obrador no lo sirve el local, por muy antiguo que sea.
    """
    query = (session.query(IngredientLot)
             .filter(IngredientLot.restaurant_id == restaurant_id,
                     IngredientLot.ingredient_id == ingredient.id,
                     IngredientLot.qty_remaining > EPSILON))
    if not include_frozen:
        query = query.filter(IngredientLot.frozen.isnot(True))
    lots = at_site(query, session, restaurant_id, site_id).all()
    as_dataclass = [fefo.Lot(ingredient=str(ingredient.id), lot_id=str(l.id), expiry=l.expiry,
                             kg=l.qty_remaining, unit_cost_usd=l.unit_cost, received=l.received)
                    for l in lots]
    by_id = {str(l.id): l for l in lots}
    return [by_id[x.lot_id] for x in fefo.order_by(as_dataclass, ingredient.rotation.value)]


# ------------------------------------------------------------------ entradas
def receive(session: Session, user: User, item: IngredientItem, qty: float, unit_cost: float,
            expiry: date, lot_code: str | None = None, received: date | None = None,
            on: date | None = None) -> IngredientLot:
    """[01076] Da de alta un lote y deja su movimiento de entrada."""
    if qty <= 0:
        raise ValueError(Aviso("err.co.qty_zero"))
    if unit_cost < 0:
        raise ValueError(Aviso("err.co.price_neg"))
    on = on or received or jornada.del_usuario(session, user)
    lot = IngredientLot(restaurant_id=user.restaurant_id, item_id=item.id,
                        ingredient_id=item.ingredient_id, lot_code=lot_code, expiry=expiry,
                        received=received or on, qty=qty, qty_remaining=qty, unit_cost=unit_cost)
    session.add(lot)
    item.last_cost = unit_cost
    session.flush()
    session.add(IngredientMovement(
        restaurant_id=user.restaurant_id, ingredient_id=item.ingredient_id, lot_id=lot.id,
        date=on, kind=MovementKind.IN, qty=qty, cost=round(qty * unit_cost, 6),
        source="purchase", source_ref=lot_code, created_by=user.id))
    session.flush()
    return lot


# ------------------------------------------------------------------ consumo
@dataclass
class Shortfall:
    ingredient_id: int
    name: str
    missing_qty: float
    unit: str
    # [01084] Lo que hay de eso mismo, pero congelado: no falta carne, falta sacarla.
    frozen_qty: float = 0.0
    # [01085] Y lo que hay en otra sede: tampoco falta carne, falta traerla.
    elsewhere_qty: float = 0.0
    elsewhere: str = ""


@dataclass
class ConsumptionResult:
    date: date
    lines: int = 0
    cost: float = 0.0
    consumed: dict[int, float] = field(default_factory=dict)
    shortfalls: list[Shortfall] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    # [01086] Lo que las recetas dicen que se gastaría de los ingredientes que se
    # controlan por conteo. No se descuenta aquí: se compara al cerrar turno.
    theoretical: dict[int, float] = field(default_factory=dict)
    site: str = ""                        # de qué sede ha salido el descuento
    weighed_kg: float = 0.0               # lo vendido a peso, con su peso real
    # [01087] Platos que se cobran por kilo y han llegado sin peso: ahí el descuento
    # sale de la ración de referencia, que no es lo que se cortó.
    missing_weight: list[str] = field(default_factory=list)


def _by_weight_line(recipe: Recipe):
    """[01077] La línea del plato cuyo peso lo decide la balanza, si la hay."""
    if not getattr(recipe, "by_weight", False):
        return None
    for line in recipe.lines:
        if line.by_weight and line.ingredient_id:
            return line
    return None


def take_from_stock(session: Session, user: User, ingredient: Ingredient, qty: float,
                    kind: MovementKind, on: date, source: str, source_ref: str | None = None,
                    site_id: int | None = None) -> tuple[float, float]:
    """[01078] Descuenta `qty` por FEFO, de la sede que se diga. Devuelve (coste, faltante)."""
    pending, cost = qty, 0.0
    for lot in rotation_order(session, user.restaurant_id, ingredient, site_id=site_id):
        if pending <= EPSILON:
            break
        take = min(lot.qty_remaining, pending)
        # [01088] La resta va dentro de la orden, no en Python: si otra persona acaba
        # de gastar de este lote, aquí no se saca lo que ya no está. Si no
        # llega, se relee lo que queda de verdad y se coge eso.
        if take > EPSILON and not locking.take(session, IngredientLot, lot.id,
                                               "qty_remaining", take):
            session.refresh(lot, ["qty_remaining"])
            take = min(lot.qty_remaining, pending)
            if take <= EPSILON or not locking.take(session, IngredientLot, lot.id,
                                                   "qty_remaining", take):
                continue
        line_cost = round(take * lot.unit_cost, 6)
        cost += line_cost
        pending = round(pending - take, 6)
        session.add(IngredientMovement(
            restaurant_id=user.restaurant_id, ingredient_id=ingredient.id, lot_id=lot.id,
            date=on, kind=kind, qty=-take, cost=line_cost, source=source,
            source_ref=source_ref, created_by=user.id))

    if pending > EPSILON:
        # [01089] No había bastante: se registra igual, valorado al último precio conocido.
        fallback = unit_costs(session, user.restaurant_id).get(ingredient.id)
        line_cost = round(pending * fallback, 6) if fallback is not None else None
        if line_cost:
            cost += line_cost
        session.add(IngredientMovement(
            restaurant_id=user.restaurant_id, ingredient_id=ingredient.id, lot_id=None,
            date=on, kind=kind, qty=-pending, cost=line_cost, source=source,
            source_ref=(source_ref or "") + " SIN STOCK", created_by=user.id))
    session.flush()
    return round(cost, 6), round(pending, 6)


def consume_sales(session: Session, user: User, sales: list[tuple],
                  on: date | None = None, lang: str | None = None,
                  site_id: int | None = None) -> ConsumptionResult:
    """[01079] Descuenta del almacén lo que se ha vendido en el POS.

    `sales` son pares (nombre del producto en el POS, unidades vendidas), o
    tríos con el peso real cuando ese producto se cobra por kilo: la carne
    madurada se corta delante del cliente y no hay dos raciones iguales, así
    que lo que descuenta son los gramos de esa venta y no un gramaje de carta.

    Se descuenta de la sede donde se ha vendido: la de quien mete las ventas,
    o la que se diga en `site_id` —el manager que sube el fichero de cada
    local—. Sin sede, la casa entera, como se ha trabajado siempre.
    """
    on = on or jornada.del_usuario(session, user)
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    result = ConsumptionResult(date=on)
    donde = sites.of_user(session, user) if site_id is None else session.get(Site, site_id)
    if donde is not None and donde.restaurant_id != user.restaurant_id:
        raise ValueError(Aviso("err.co.site_other_house"))
    site_id = donde.id if donde is not None else None
    result.site = donde.name if donde is not None else ""

    mapping = pos_index(session, user.restaurant_id)
    # [01090] Se descuenta plato a plato, no todo junto: así cada salida deja escrito
    # a qué plato fue, y luego se puede repartir lo que ingresó.
    by_dish: list[tuple[str, dict[int, float]]] = []
    needed: dict[int, float] = {}
    for line in sales:
        pos_name, units = line[0], line[1]
        kg = line[2] if len(line) > 2 else None
        if units <= 0:
            continue
        product = mapping.get(_norm(pos_name))
        if product is None or product.recipe is None:
            result.unmapped.append(pos_name)
            continue
        result.lines += 1
        recipe = product.recipe
        exploded = por_raciones(recipe, units)
        weighed = _by_weight_line(recipe)
        if weighed is not None and kg and kg > EPSILON:
            # [01091] Manda la balanza: los gramos de esta venta sustituyen a la ración
            # de la carta, que en un corte a peso es solo una referencia.
            exploded[weighed.ingredient_id] = round(kg, 6)
            result.weighed_kg = round(result.weighed_kg + kg, 6)
        elif weighed is not None:
            result.missing_weight.append(product.pos_name)
        session.add(SalesByProduct(restaurant_id=user.restaurant_id, op_date=on,
                                   pos_name=product.pos_name, units=int(units),
                                   kg=round(kg, 6) if kg else None))
        by_dish.append((product.pos_name, exploded))
        for ingredient_id, qty in exploded.items():
            needed[ingredient_id] = round(needed.get(ingredient_id, 0.0) + qty, 6)

    ingredients = {i.id: i for i in session.query(Ingredient)
                   .filter(Ingredient.restaurant_id == user.restaurant_id,
                           Ingredient.id.in_(needed or [0]))}
    missing_total: dict[int, float] = {}
    for pos_name, exploded in by_dish:
        for ingredient_id, qty in exploded.items():
            ingredient = ingredients.get(ingredient_id)
            if ingredient is None:
                continue
            if ingredient.consumption == ConsumptionMode.COUNT:
                # [01092] Esta carne se descuenta por el conteo de descongelado, no aquí:
                # descontarla dos veces sería inventarse el doble de consumo.
                result.theoretical[ingredient_id] = round(
                    result.theoretical.get(ingredient_id, 0.0) + qty, 6)
                continue
            cost, missing = take_from_stock(session, user, ingredient, qty,
                                            MovementKind.SALE, on, "pos", pos_name,
                                            site_id=site_id)
            result.cost = round(result.cost + cost, 6)
            result.consumed[ingredient_id] = round(
                result.consumed.get(ingredient_id, 0.0) + qty, 6)
            if missing > EPSILON:
                missing_total[ingredient_id] = round(
                    missing_total.get(ingredient_id, 0.0) + missing, 6)

    congelado = frozen_on_hand(session, user.restaurant_id, site_id) if missing_total else {}
    fuera = elsewhere_on_hand(session, user.restaurant_id, site_id) if missing_total else {}
    for ingredient_id, missing in missing_total.items():
        ingredient = ingredients[ingredient_id]
        otras_kg, otras = fuera.get(ingredient_id, (0.0, set()))
        result.shortfalls.append(Shortfall(ingredient_id, ingredient.name, missing,
                                           ingredient.unit.value,
                                           frozen_qty=congelado.get(ingredient_id, 0.0),
                                           elsewhere_qty=otras_kg,
                                           elsewhere=", ".join(sorted(n for n in otras if n))))

    _raise_alerts(session, user, result, lang)
    return result


def _raise_alerts(session: Session, user: User, result: ConsumptionResult, lang: str) -> None:
    """[01080] Los avisos de lo que falta para cocinar lo vendido, y a quién le llegan.

    No falta carne de la misma manera en los tres casos, y por eso no se dice
    igual: si la hay en otra sede se arregla con un traslado, si está en el
    arcón sacándola a descongelar, y si no hay ninguna de las dos cosas es que
    alguien no registró una entrada.
    """
    managers = service.manager_ids(session, user.restaurant_id)
    targets = [uid for uid in managers if uid != user.id]
    now = datetime.utcnow()

    for gap in result.shortfalls:
        # [01093] No falta carne de la misma manera en los tres casos: en otra sede se
        # arregla con un traslado, en el arcón sacándola a descongelar, y sin
        # nada de eso es que alguien no registró una entrada.
        if gap.elsewhere_qty > EPSILON:
            message = t(lang, "alert.stock_elsewhere", ingredient=gap.name,
                        qty=f"{gap.missing_qty:.10g}", unit=gap.unit,
                        there=f"{gap.elsewhere_qty:.10g}", sites=gap.elsewhere)
        elif gap.frozen_qty > EPSILON:
            message = t(lang, "alert.stock_frozen", ingredient=gap.name,
                        qty=f"{gap.missing_qty:.10g}", unit=gap.unit,
                        frozen=f"{gap.frozen_qty:.10g}")
        else:
            message = t(lang, "alert.stock_short", ingredient=gap.name,
                        qty=f"{gap.missing_qty:.10g}", unit=gap.unit)
        alert = Alert(restaurant_id=user.restaurant_id, code="stock.short",
                      message=message, severity=AlertSeverity.WARNING, created_at=now)
        session.add(alert)
        result.alerts.append(alert)
    if result.unmapped:
        message = t(lang, "alert.pos_unmapped", products=", ".join(sorted(set(result.unmapped))))
        alert = Alert(restaurant_id=user.restaurant_id, code="pos.unmapped",
                      message=message, severity=AlertSeverity.WARNING, created_at=now)
        session.add(alert)
        result.alerts.append(alert)
    session.flush()
    for alert in result.alerts:
        service.notify(session, user.restaurant_id, targets,
                       title=t(lang, "alert.stock_title"), body=alert.message,
                       severity=alert.severity, alert_id=alert.id, now=now)


# ----------------------------------------------------------------- escandallo
def cost_of(session: Session, recipe: Recipe,
            costs: dict[int, float] | None = None) -> RecipeCost:
    """[01081] Lo que cuesta una receta. Si no le dan los precios, los busca."""
    costs = unit_costs(session, recipe.restaurant_id) if costs is None else costs
    return cost_recipe(recipe, costs)


def menu(session: Session, restaurant_id: int) -> list:
    """[01082] Toda la carta ordenada por food cost: arriba lo que peor margen deja."""
    costs = unit_costs(session, restaurant_id)
    dishes = (session.query(Recipe)
              .filter_by(restaurant_id=restaurant_id, kind=RecipeKind.DISH, active=True)
              .order_by(Recipe.name).all())
    return menu_ranking([cost_recipe(d, costs) for d in dishes])
