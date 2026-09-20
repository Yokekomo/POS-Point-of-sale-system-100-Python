"""Historia de un primal: el árbol de lo que salió de él y lo que dejó."""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.models import (ConsumptionMode, Despiece, DespieceCut, DespiecePrimal,
                             Ingredient, IngredientItem, IngredientMovement, MovementKind,
                             PosProduct, Primal, PrimalStatus, Recipe, RecipeKind,
                             RecipeLine, Unit)
from thegrill.web import auth, butchery, costing, defrost, tracing
from thegrill.web.tracing import NotFound

HOY = date(2026, 9, 21)


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'t.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Bistró", "ana@b.com", "Ana",
                                           "clave-larga-1", language="es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@b.com", "Luis", "clave-larga-2")
        yield s, rest, ana, luis


def madre(s, rest, name, mode=ConsumptionMode.RECIPE, unit=Unit.KG):
    i = Ingredient(restaurant_id=rest.id, name=name, unit=unit, consumption=mode)
    s.add(i); s.flush(); return i


def articulo(s, rest, ing, name):
    it = IngredientItem(restaurant_id=rest.id, ingredient_id=ing.id, name=name)
    s.add(it); s.flush(); return it


def primal(s, rest, serial, sku="STRIPLOIN_AUS", kg=10.0, usd_kg=20.0, lot="DXB20260910"):
    p = Primal(restaurant_id=rest.id, serial=serial, sku=sku, weight_kg=kg, lot=lot,
               landed_usd_per_kg=usd_kg, piece_cost_usd=round(kg * usd_kg, 2),
               received_date=HOY - timedelta(days=4),
               frozen_use_by=HOY + timedelta(days=30))
    s.add(p); s.flush(); return p


def despiezar(s, rest, ana, tg, serials, antes, cortes, merma):
    d = Despiece(restaurant_id=rest.id, tg=tg, date=HOY, weight_before_kg=antes,
                 waste_kg=merma, country="AUS")
    for serial in serials:
        d.primals.append(DespiecePrimal(serial=serial))
    for nombre, item, piezas, gramos, indice, trim in cortes:
        d.cuts.append(DespieceCut(cut_name=nombre, item_id=item.id, pieces=piezas,
                                  weight_per_piece_g=gramos,
                                  total_kg=round(piezas * gramos / 1000, 4),
                                  value_index=indice, is_trim=trim))
    s.add(d); s.flush()
    butchery.post(s, ana, d)
    return d


def montar_burger(s, rest, ana):
    """Un striploin da filete y recorte; el recorte va a la hamburguesa."""
    filete_m = madre(s, rest, "Striploin steak")
    burger_m = madre(s, rest, "Beef for burger")
    pan_m = madre(s, rest, "Pan de burger", unit=Unit.UNIT)
    costing.receive(s, ana, articulo(s, rest, pan_m, "Pan brioche"), 100, 0.40,
                    HOY + timedelta(days=5), on=HOY)
    p = primal(s, rest, "8017", kg=10.0, usd_kg=20.0)
    despiezar(s, rest, ana, "TG-0010", ["8017"], 10.0, [
        ("Striploin steak", articulo(s, rest, filete_m, "Filete 250g"), 20, 250, 1.6, False),
        ("Recorte", articulo(s, rest, burger_m, "Recorte striploin"), 12, 200, 0.45, True),
    ], merma=2.6)

    patty = Recipe(restaurant_id=rest.id, code="patty", name="Burger patty",
                   kind=RecipeKind.PREP, yield_qty=1, yield_unit=Unit.UNIT)
    patty.lines.append(RecipeLine(ingredient_id=burger_m.id, qty=0.16))
    s.add(patty); s.flush()
    burger = Recipe(restaurant_id=rest.id, code="cheese", name="Cheese burger",
                    kind=RecipeKind.DISH, portions=1, sale_price=13.20, vat_pct=10)
    burger.lines.append(RecipeLine(sub_recipe_id=patty.id, qty=1))
    burger.lines.append(RecipeLine(ingredient_id=pan_m.id, qty=1))
    s.add(burger); s.flush()
    s.add(PosProduct(restaurant_id=rest.id, pos_name="CHEESE BURGER", recipe_id=burger.id))

    entrecot = Recipe(restaurant_id=rest.id, code="entrecot", name="Entrecot",
                      kind=RecipeKind.DISH, portions=1, sale_price=27.50, vat_pct=10)
    entrecot.lines.append(RecipeLine(ingredient_id=filete_m.id, qty=0.25))
    s.add(entrecot); s.flush()
    s.add(PosProduct(restaurant_id=rest.id, pos_name="ENTRECOT", recipe_id=entrecot.id))
    s.flush()
    return p, filete_m, burger_m


# ----------------------------------------------------------------- el árbol
def test_a_whole_primal_has_no_story_yet(ctx):
    s, rest, ana, luis = ctx
    primal(s, rest, "8017", kg=9.4, usd_kg=21.0)
    h = tracing.history(s, rest.id, "8017")
    assert h.serial == "8017" and h.sku == "STRIPLOIN_AUS"
    assert h.lot == "DXB20260910" and h.cost == round(9.4 * 21.0, 2)
    assert h.status == "IN_STOCK" and h.butchery is None
    assert h.revenue == 0.0 and h.food_cost_pct is None


def test_an_unknown_serial_says_so(ctx):
    s, rest, ana, luis = ctx
    with pytest.raises(NotFound):
        tracing.history(s, rest.id, "NO-EXISTE")


def test_the_tree_shows_the_butchery_and_its_cuts(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    h = tracing.history(s, rest.id, "8017")

    assert h.status == "CUT"
    assert h.butchery.tg == "TG-0010" and h.butchery.waste_kg == 2.6
    assert h.butchery.yield_pct == 50.0            # 5 kg de filete sobre 10
    assert [c.name for c in h.butchery.cuts] == ["Beef for burger", "Striploin steak"]

    recorte = next(c for c in h.butchery.cuts if c.is_trim)
    filete = next(c for c in h.butchery.cuts if not c.is_trim)
    assert recorte.produced_kg == 2.4 and filete.produced_kg == 5.0
    assert round(recorte.cost + filete.cost, 2) == 200.0     # todo el coste repartido
    assert filete.unit_cost > 20.0 > recorte.unit_cost       # la merma la pagan los cortes


def test_every_cut_carries_its_serial(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    h = tracing.history(s, rest.id, "8017")
    assert sorted(c.serial for c in h.butchery.cuts) == ["8017-01", "8017-02"]


def test_a_batch_says_which_other_primals_went_in(ctx):
    s, rest, ana, luis = ctx
    filete_m = madre(s, rest, "Striploin steak")
    item = articulo(s, rest, filete_m, "Filete")
    primal(s, rest, "8017", kg=10.0)
    primal(s, rest, "8018", kg=10.0)
    despiezar(s, rest, ana, "TG-0011", ["8017", "8018"], 20.0,
              [("Filete", item, 40, 350, 1.0, False)], merma=6.0)
    h = tracing.history(s, rest.id, "8017")
    assert h.butchery.shared_with == ["8018"]


# ------------------------------------------------------------- las ventas
def test_the_sales_say_which_dish_took_each_cut(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    costing.consume_sales(s, luis, [("CHEESE BURGER", 10), ("ENTRECOT", 4)], on=HOY)

    h = tracing.history(s, rest.id, "8017")
    recorte = next(c for c in h.butchery.cuts if c.is_trim)
    filete = next(c for c in h.butchery.cuts if not c.is_trim)

    assert [l.dish for l in recorte.sales] == ["CHEESE BURGER"]
    assert recorte.sold_kg == 1.6                   # 10 × 160 g
    assert [l.dish for l in filete.sales] == ["ENTRECOT"]
    assert filete.sold_kg == 1.0                    # 4 × 250 g
    assert all(l.source == "pos" for l in recorte.sales + filete.sales)


def test_what_is_left_and_what_was_thrown_away_show_up(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    costing.consume_sales(s, luis, [("ENTRECOT", 4)], on=HOY)
    h = tracing.history(s, rest.id, "8017")
    filete = next(c for c in h.butchery.cuts if not c.is_trim)
    assert filete.produced_kg == 5.0 and filete.sold_kg == 1.0
    assert filete.remaining_kg == 4.0
    assert filete.unaccounted_kg == 0.0             # todo cuadra


def test_the_kilos_that_do_not_add_up_are_visible(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    from thegrill.models import IngredientLot
    lote = s.query(IngredientLot).filter_by(serial="8017-01").one()
    lote.qty_remaining = round(lote.qty_remaining - 0.7, 6)   # alguien toca el stock a mano
    s.flush()
    h = tracing.history(s, rest.id, "8017")
    corte = next(c for c in h.butchery.cuts if c.serial == "8017-01")
    assert corte.unaccounted_kg == 0.7


# ---------------------------------------------------- lo ganado y el food cost
def test_the_revenue_is_shared_out_by_what_each_ingredient_costs_in_the_dish(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    costing.consume_sales(s, luis, [("ENTRECOT", 4)], on=HOY)

    h = tracing.history(s, rest.id, "8017")
    filete = next(c for c in h.butchery.cuts if not c.is_trim)
    # el entrecot es solo filete: se lleva todo el ingreso neto del plato
    assert filete.revenue == pytest.approx(4 * 27.50 / 1.1, abs=0.05)
    assert filete.food_cost_pct is not None
    assert 20 < filete.food_cost_pct < 60


def test_a_dish_with_more_ingredients_shares_the_revenue(ctx):
    """La hamburguesa lleva pan: el recorte no se lleva todo el ingreso."""
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    costing.consume_sales(s, luis, [("CHEESE BURGER", 10)], on=HOY)
    h = tracing.history(s, rest.id, "8017")
    recorte = next(c for c in h.butchery.cuts if c.is_trim)
    neto = 10 * 13.20 / 1.1
    assert 0 < recorte.revenue < neto               # una parte, no todo
    assert recorte.revenue / neto > 0.5             # pero la carne manda


def test_the_summary_adds_the_whole_piece_up(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    costing.consume_sales(s, luis, [("CHEESE BURGER", 10), ("ENTRECOT", 8)], on=HOY)

    h = tracing.history(s, rest.id, "8017")
    assert h.cost == 200.0
    assert h.sold_kg == round(1.6 + 2.0, 4)
    assert h.revenue > 0 and h.margin == round(h.revenue - h.sold_cost, 2)
    assert h.food_cost_pct == round(h.sold_cost / h.revenue * 100, 2)
    assert h.recovered_pct == round(h.revenue / 200.0 * 100, 1)
    assert h.waste_kg == 2.6                        # la merma del despiece
    assert not h.sold_out and h.remaining_kg > 0


def test_a_piece_sold_out_says_so(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    costing.consume_sales(s, luis, [("CHEESE BURGER", 15), ("ENTRECOT", 20)], on=HOY)
    h = tracing.history(s, rest.id, "8017")
    assert h.sold_out and h.remaining_kg == 0.0


def test_meat_sold_by_defrost_count_also_lands_in_the_history(ctx):
    """El filete se descuenta por conteo y aun así se ve de dónde salió."""
    s, rest, ana, luis = ctx
    p, filete_m, burger_m = montar_burger(s, rest, ana)
    filete_m.consumption = ConsumptionMode.COUNT
    s.flush()
    costing.consume_sales(s, luis, [("ENTRECOT", 8)], on=HOY)   # no descuenta
    defrost.intake(s, luis, "8017-01", pieces=16, total_kg=4.0, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=8, total_kg=2.0, on=HOY, shift="noche")
    defrost.close(s, luis, on=HOY, shift="noche")

    h = tracing.history(s, rest.id, "8017")
    filete = next(c for c in h.butchery.cuts if c.serial == "8017-01")
    assert filete.sold_kg == 2.0
    assert [l.source for l in filete.sales] == ["defrost"]
    assert filete.revenue > 0                       # se reparte por el plato que lo usa


def test_a_piece_never_sold_has_no_food_cost(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    h = tracing.history(s, rest.id, "8017")
    assert h.revenue == 0.0 and h.food_cost_pct is None and h.margin == 0.0


# ------------------------------------------------------------------ búsqueda
def test_you_can_look_a_piece_up_by_serial_or_by_shipment(ctx):
    s, rest, ana, luis = ctx
    primal(s, rest, "8017", lot="DXB20260910")
    primal(s, rest, "8018", lot="DXB20260910")
    primal(s, rest, "9001", lot="DXB20261002")
    assert [p.serial for p in tracing.search(s, rest.id, "801")] == ["8017", "8018"]
    assert len(tracing.search(s, rest.id, "DXB20260910")) == 2
    assert tracing.search(s, rest.id, "") == []


def test_you_cannot_read_another_restaurants_piece(ctx):
    s, rest, ana, luis = ctx
    primal(s, rest, "8017")
    otro, eva = auth.create_restaurant(s, "Otro", "eva@otro.com", "Eva", "clave-larga-9")
    with pytest.raises(NotFound):
        tracing.history(s, otro.id, "8017")
    assert tracing.search(s, otro.id, "8017") == []
