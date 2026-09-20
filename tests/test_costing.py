"""Precios reales, rotación de lotes y descuento por venta."""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.models import (Ingredient, IngredientItem, IngredientLot, IngredientMovement,
                             MovementKind, PosProduct, Recipe, RecipeKind, RecipeLine,
                             Rotation, Unit)
from thegrill.web import auth, costing
from thegrill.web.seed import seed_templates

HOY = date(2026, 9, 20)


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'c.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Casa", "ana@casa.com", "Ana",
                                           "clave-larga-1", language="es")
        seed_templates(s, rest.id, "es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@casa.com", "Luis", "clave-larga-2")
        yield s, rest, ana, luis


def madre(s, rest, name, unit=Unit.KG, rotation=Rotation.FEFO) -> Ingredient:
    ing = Ingredient(restaurant_id=rest.id, name=name, unit=unit, rotation=rotation)
    s.add(ing)
    s.flush()
    return ing


def articulo(s, rest, ing, name, brand=None) -> IngredientItem:
    item = IngredientItem(restaurant_id=rest.id, ingredient_id=ing.id, name=name, brand=brand)
    s.add(item)
    s.flush()
    return item


def receta(s, rest, code, name, lines, **kw) -> Recipe:
    r = Recipe(restaurant_id=rest.id, code=code, name=name, **kw)
    for i, (ing, qty, waste, sub) in enumerate(lines):
        r.lines.append(RecipeLine(ingredient_id=ing.id if ing else None,
                                  sub_recipe_id=sub.id if sub else None,
                                  qty=qty, waste_pct=waste, sort_order=i))
    s.add(r)
    s.flush()
    return r


# ------------------------------------------------------------------ precios
def test_the_price_of_a_mother_ingredient_blends_its_brands(ctx):
    """Dos marcas de chunk beef bajo la misma madre: el precio es el ponderado."""
    s, rest, ana, _ = ctx
    carne = madre(s, rest, "Beef for burger")
    marca_a = articulo(s, rest, carne, "Chunk beef A", brand="A")
    marca_b = articulo(s, rest, carne, "Chunk beef B", brand="B")
    costing.receive(s, ana, marca_a, qty=10, unit_cost=9.0, expiry=HOY + timedelta(days=10), on=HOY)
    costing.receive(s, ana, marca_b, qty=30, unit_cost=10.0, expiry=HOY + timedelta(days=20), on=HOY)

    precios = costing.unit_costs(s, rest.id)
    assert precios[carne.id] == round((10 * 9.0 + 30 * 10.0) / 40, 6)
    assert costing.stock_on_hand(s, rest.id)[carne.id] == 40


def test_without_stock_it_falls_back_to_the_last_known_price(ctx):
    s, rest, ana, _ = ctx
    leche = madre(s, rest, "Leche", unit=Unit.L)
    item = articulo(s, rest, leche, "Leche entera", brand="Marca X")
    lote = costing.receive(s, ana, item, qty=6, unit_cost=0.80,
                           expiry=HOY + timedelta(days=5), on=HOY)
    lote.qty_remaining = 0
    s.flush()
    assert costing.unit_costs(s, rest.id)[leche.id] == 0.80


def test_an_ingredient_never_bought_has_no_price_at_all(ctx):
    s, rest, _, _ = ctx
    azafran = madre(s, rest, "Azafrán")
    assert azafran.id not in costing.unit_costs(s, rest.id)   # desconocido, no cero


def test_receiving_records_the_lot_and_its_movement(ctx):
    s, rest, ana, _ = ctx
    leche = madre(s, rest, "Leche", unit=Unit.L)
    item = articulo(s, rest, leche, "Leche entera")
    lot = costing.receive(s, ana, item, qty=12, unit_cost=0.75,
                          expiry=HOY + timedelta(days=7), lot_code="L-1", on=HOY)
    assert lot.qty_remaining == 12 and lot.ingredient_id == leche.id
    assert item.last_cost == 0.75
    mv = s.query(IngredientMovement).one()
    assert mv.kind == MovementKind.IN and mv.qty == 12 and mv.cost == 9.0
    for bad in (0, -5):
        with pytest.raises(ValueError):
            costing.receive(s, ana, item, qty=bad, unit_cost=1, expiry=HOY)


# ----------------------------------------------------------------- rotación
def test_fefo_takes_what_expires_first_whatever_the_brand(ctx):
    s, rest, ana, _ = ctx
    carne = madre(s, rest, "Beef for burger")
    a = articulo(s, rest, carne, "Chunk A", brand="A")
    b = articulo(s, rest, carne, "Chunk B", brand="B")
    viejo = costing.receive(s, ana, a, qty=5, unit_cost=9.0,
                            expiry=HOY + timedelta(days=30), received=HOY - timedelta(days=10), on=HOY)
    urgente = costing.receive(s, ana, b, qty=5, unit_cost=11.0,
                              expiry=HOY + timedelta(days=2), received=HOY, on=HOY)
    orden = costing.rotation_order(s, rest.id, carne)
    assert [l.id for l in orden] == [urgente.id, viejo.id]    # caduca antes, sale antes


def test_fifo_takes_what_came_in_first(ctx):
    s, rest, ana, _ = ctx
    sal = madre(s, rest, "Sal", rotation=Rotation.FIFO)
    item = articulo(s, rest, sal, "Sal marina")
    primero = costing.receive(s, ana, item, qty=5, unit_cost=1.0,
                              expiry=HOY + timedelta(days=900), received=HOY - timedelta(days=60), on=HOY)
    segundo = costing.receive(s, ana, item, qty=5, unit_cost=1.2,
                              expiry=HOY + timedelta(days=400), received=HOY, on=HOY)
    assert [l.id for l in costing.rotation_order(s, rest.id, sal)] == [primero.id, segundo.id]


# ------------------------------------------------------------------ consumo
def build_burger(s, rest, ana):
    """chunk beef (dos marcas) → Beef for burger → patty → burger → Cheese burger"""
    carne = madre(s, rest, "Beef for burger")
    pan = madre(s, rest, "Pan de burger", unit=Unit.UNIT)
    costing.receive(s, ana, articulo(s, rest, carne, "Chunk A", brand="A"),
                    qty=2, unit_cost=9.0, expiry=HOY + timedelta(days=2), on=HOY)
    costing.receive(s, ana, articulo(s, rest, carne, "Chunk B", brand="B"),
                    qty=5, unit_cost=11.0, expiry=HOY + timedelta(days=20), on=HOY)
    costing.receive(s, ana, articulo(s, rest, pan, "Pan brioche"),
                    qty=50, unit_cost=0.35, expiry=HOY + timedelta(days=5), on=HOY)

    patty = receta(s, rest, "patty", "Burger patty", [(carne, 0.16, 5, None)],
                   kind=RecipeKind.PREP, yield_qty=1, yield_unit=Unit.UNIT)
    burger = receta(s, rest, "burger", "Burger", [(None, 1, 0, patty), (pan, 1, 0, None)],
                    kind=RecipeKind.PREP, yield_qty=1, yield_unit=Unit.UNIT)
    plato = receta(s, rest, "cheese", "Cheese burger", [(None, 1, 0, burger)],
                   kind=RecipeKind.DISH, portions=1, sale_price=13.50, vat_pct=10)
    s.add(PosProduct(restaurant_id=rest.id, pos_name="CHEESE BURGER", recipe_id=plato.id))
    s.flush()
    return carne, pan, plato


def test_selling_a_burger_discounts_stock_through_the_whole_chain(ctx):
    s, rest, ana, luis = ctx
    carne, pan, _ = build_burger(s, rest, ana)
    antes = costing.stock_on_hand(s, rest.id)

    result = costing.consume_sales(s, luis, [("CHEESE BURGER", 5)], on=HOY)

    esperado = 5 * 0.16 / 0.95
    despues = costing.stock_on_hand(s, rest.id)
    assert round(antes[carne.id] - despues[carne.id], 6) == round(esperado, 6)
    assert round(antes[pan.id] - despues[pan.id], 6) == 5
    assert result.lines == 1 and not result.shortfalls and not result.unmapped


def test_the_cheapest_lot_is_not_the_one_that_leaves_first(ctx):
    """Sale el que antes caduca, aunque sea el más caro: eso es FEFO."""
    s, rest, ana, luis = ctx
    carne, _, _ = build_burger(s, rest, ana)
    costing.consume_sales(s, luis, [("CHEESE BURGER", 5)], on=HOY)
    lotes = {l.item.brand: l.qty_remaining for l in s.query(IngredientLot)
             .filter_by(ingredient_id=carne.id)}
    assert lotes["A"] < 2 and lotes["B"] == 5      # A caduca en dos días, se gasta antes


def test_every_gram_leaves_a_movement_with_its_cost(ctx):
    s, rest, ana, luis = ctx
    carne, _, _ = build_burger(s, rest, ana)
    result = costing.consume_sales(s, luis, [("CHEESE BURGER", 2)], on=HOY)
    salidas = (s.query(IngredientMovement)
               .filter_by(ingredient_id=carne.id, kind=MovementKind.SALE).all())
    assert salidas and all(m.qty < 0 and m.cost > 0 and m.source == "pos" for m in salidas)
    assert round(sum(-m.qty for m in salidas), 6) == round(2 * 0.16 / 0.95, 6)
    assert result.cost > 0


def test_running_out_of_stock_is_recorded_and_raises_an_alert(ctx):
    s, rest, ana, luis = ctx
    carne, _, _ = build_burger(s, rest, ana)
    result = costing.consume_sales(s, luis, [("CHEESE BURGER", 60)], on=HOY)

    faltan = {g.name: g.missing_qty for g in result.shortfalls}
    assert set(faltan) == {"Beef for burger", "Pan de burger"}   # 60 agotan ambos
    assert faltan["Beef for burger"] > 0 and faltan["Pan de burger"] == 10
    assert any("faltan" in a.message for a in result.alerts)
    assert costing.stock_on_hand(s, rest.id)[carne.id] == 0        # nunca queda negativo
    sin_lote = (s.query(IngredientMovement)
                .filter_by(ingredient_id=carne.id, lot_id=None).one())
    assert sin_lote.qty < 0 and "SIN STOCK" in sin_lote.source_ref


def test_a_product_without_a_recipe_is_flagged_not_ignored(ctx):
    s, rest, ana, luis = ctx
    build_burger(s, rest, ana)
    result = costing.consume_sales(s, luis, [("CHEESE BURGER", 1), ("COCA COLA", 4)], on=HOY)
    assert result.unmapped == ["COCA COLA"] and result.lines == 1
    assert any("COCA COLA" in a.message for a in result.alerts)


def test_the_manager_is_notified_of_stock_problems(ctx):
    s, rest, ana, luis = ctx
    build_burger(s, rest, ana)
    costing.consume_sales(s, luis, [("CHEESE BURGER", 99)], on=HOY)
    from thegrill.web import service
    assert service.unread_count(s, ana.id) >= 1
    assert service.unread_count(s, luis.id) == 0


def test_consumption_never_crosses_between_restaurants(ctx):
    s, rest, ana, luis = ctx
    carne, _, _ = build_burger(s, rest, ana)
    otro, eva = auth.create_restaurant(s, "Otro", "eva@otro.com", "Eva", "clave-larga-9")
    result = costing.consume_sales(s, eva, [("CHEESE BURGER", 3)], on=HOY)
    assert result.unmapped == ["CHEESE BURGER"]                 # no ve la receta del vecino
    assert costing.stock_on_hand(s, rest.id)[carne.id] == 7      # intacto


# --------------------------------------------------------------- escandallo
def test_the_cost_of_the_dish_uses_the_real_price_of_the_lots(ctx):
    s, rest, ana, _ = ctx
    carne, pan, plato = build_burger(s, rest, ana)
    c = costing.cost_of(s, plato)
    precio_carne = round((2 * 9.0 + 5 * 11.0) / 7, 6)
    assert c.complete
    assert c.cost_per_portion == pytest.approx(0.16 / 0.95 * precio_carne + 0.35, abs=1e-4)
    assert c.food_cost_pct is not None and 0 < c.food_cost_pct < 40


def test_the_menu_lists_the_worst_margin_first(ctx):
    s, rest, ana, _ = ctx
    carne, pan, plato = build_burger(s, rest, ana)
    barato = receta(s, rest, "pan", "Pan solo", [(pan, 1, 0, None)],
                    kind=RecipeKind.DISH, portions=1, sale_price=5.0, vat_pct=10)
    carta = costing.menu(s, rest.id)
    assert [r.code for r in carta] == ["cheese", "pan"]
    assert carta[0].food_cost_pct > carta[1].food_cost_pct
