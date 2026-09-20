"""Precios, stock y descuento por consumo.

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
"""
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from thegrill.engine import fefo
from thegrill.engine.recipes import RecipeCost, cost_recipe, explode, menu_ranking
from thegrill.models import (Alert, AlertSeverity, Ingredient, IngredientItem,
                             IngredientLot, IngredientMovement, MovementKind, PosMatch,
                             PosProduct, Recipe, RecipeKind, Restaurant, User)
from thegrill.web import service
from thegrill.web.i18n import DEFAULT_LANG, t

EPSILON = 1e-9


class NotMapped(KeyError):
    """Un producto del POS sin receta detrás: no se puede descontar nada."""


def _norm(value: str | None) -> str:
    return " ".join(str(value or "").strip().upper().split())


def pos_index(session: Session, restaurant_id: int) -> dict[str, PosProduct]:
    """Índice de búsqueda del POS, según cómo identifique este restaurante.

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
    """Precio por unidad base de cada ingrediente madre."""
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

    # Sin stock: el último precio conocido de cualquiera de sus artículos.
    for item in (session.query(IngredientItem)
                 .filter(IngredientItem.restaurant_id == restaurant_id,
                         IngredientItem.last_cost.isnot(None))):
        costs.setdefault(item.ingredient_id, round(item.last_cost, 6))
    return costs


def stock_on_hand(session: Session, restaurant_id: int) -> dict[int, float]:
    """Cantidad que queda de cada ingrediente madre, sumando marcas."""
    out: dict[int, float] = {}
    for lot in (session.query(IngredientLot)
                .filter(IngredientLot.restaurant_id == restaurant_id)):
        out[lot.ingredient_id] = round(out.get(lot.ingredient_id, 0.0) + lot.qty_remaining, 6)
    return out


def rotation_order(session: Session, restaurant_id: int, ingredient: Ingredient) -> list[IngredientLot]:
    """Lotes con existencias de una madre, en el orden en que deben salir.

    Compiten todos los lotes de todas sus marcas: el ingrediente madre existe
    precisamente para poder gastarlos en una sola cola.
    """
    lots = (session.query(IngredientLot)
            .filter(IngredientLot.restaurant_id == restaurant_id,
                    IngredientLot.ingredient_id == ingredient.id,
                    IngredientLot.qty_remaining > EPSILON).all())
    as_dataclass = [fefo.Lot(ingredient=str(ingredient.id), lot_id=str(l.id), expiry=l.expiry,
                             kg=l.qty_remaining, unit_cost_usd=l.unit_cost, received=l.received)
                    for l in lots]
    by_id = {str(l.id): l for l in lots}
    return [by_id[x.lot_id] for x in fefo.order_by(as_dataclass, ingredient.rotation.value)]


# ------------------------------------------------------------------ entradas
def receive(session: Session, user: User, item: IngredientItem, qty: float, unit_cost: float,
            expiry: date, lot_code: str | None = None, received: date | None = None,
            on: date | None = None) -> IngredientLot:
    """Da de alta un lote y deja su movimiento de entrada."""
    if qty <= 0:
        raise ValueError("La cantidad recibida tiene que ser mayor que cero")
    if unit_cost < 0:
        raise ValueError("El precio no puede ser negativo")
    on = on or received or date.today()
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


@dataclass
class ConsumptionResult:
    date: date
    lines: int = 0
    cost: float = 0.0
    consumed: dict[int, float] = field(default_factory=dict)
    shortfalls: list[Shortfall] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)


def take_from_stock(session: Session, user: User, ingredient: Ingredient, qty: float,
                    kind: MovementKind, on: date, source: str, source_ref: str | None = None
                    ) -> tuple[float, float]:
    """Descuenta `qty` por FEFO. Devuelve (coste, faltante)."""
    pending, cost = qty, 0.0
    for lot in rotation_order(session, user.restaurant_id, ingredient):
        if pending <= EPSILON:
            break
        take = min(lot.qty_remaining, pending)
        lot.qty_remaining = round(lot.qty_remaining - take, 6)
        line_cost = round(take * lot.unit_cost, 6)
        cost += line_cost
        pending = round(pending - take, 6)
        session.add(IngredientMovement(
            restaurant_id=user.restaurant_id, ingredient_id=ingredient.id, lot_id=lot.id,
            date=on, kind=kind, qty=-take, cost=line_cost, source=source,
            source_ref=source_ref, created_by=user.id))

    if pending > EPSILON:
        # No había bastante: se registra igual, valorado al último precio conocido.
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


def consume_sales(session: Session, user: User, sales: list[tuple[str, float]],
                  on: date | None = None, lang: str | None = None) -> ConsumptionResult:
    """Descuenta del almacén lo que se ha vendido en el POS.

    `sales` son pares (nombre del producto en el POS, unidades vendidas).
    """
    on = on or date.today()
    lang = lang or service.restaurant_language(session, user.restaurant_id)
    result = ConsumptionResult(date=on)

    mapping = pos_index(session, user.restaurant_id)
    needed: dict[int, float] = {}
    for pos_name, units in sales:
        if units <= 0:
            continue
        product = mapping.get(_norm(pos_name))
        if product is None or product.recipe is None:
            result.unmapped.append(pos_name)
            continue
        result.lines += 1
        for ingredient_id, qty in explode(product.recipe, units).items():
            needed[ingredient_id] = round(needed.get(ingredient_id, 0.0) + qty, 6)

    ingredients = {i.id: i for i in session.query(Ingredient)
                   .filter(Ingredient.restaurant_id == user.restaurant_id,
                           Ingredient.id.in_(needed or [0]))}
    for ingredient_id, qty in needed.items():
        ingredient = ingredients.get(ingredient_id)
        if ingredient is None:
            continue
        cost, missing = take_from_stock(session, user, ingredient, qty,
                                        MovementKind.SALE, on, "pos", f"ventas {on}")
        result.cost = round(result.cost + cost, 6)
        result.consumed[ingredient_id] = qty
        if missing > EPSILON:
            result.shortfalls.append(Shortfall(ingredient_id, ingredient.name, missing,
                                               ingredient.unit.value))

    _raise_alerts(session, user, result, lang)
    return result


def _raise_alerts(session: Session, user: User, result: ConsumptionResult, lang: str) -> None:
    managers = service.manager_ids(session, user.restaurant_id)
    targets = [uid for uid in managers if uid != user.id]
    now = datetime.utcnow()

    for gap in result.shortfalls:
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
    costs = unit_costs(session, recipe.restaurant_id) if costs is None else costs
    return cost_recipe(recipe, costs)


def menu(session: Session, restaurant_id: int) -> list:
    """Toda la carta ordenada por food cost: arriba lo que peor margen deja."""
    costs = unit_costs(session, restaurant_id)
    dishes = (session.query(Recipe)
              .filter_by(restaurant_id=restaurant_id, kind=RecipeKind.DISH, active=True)
              .order_by(Recipe.name).all())
    return menu_ranking([cost_recipe(d, costs) for d in dishes])
