"""La carne, que va por su cuenta.

Un primal llega en un lote de recepción, con su número de pieza y su coste.
Al despiezarlo salen hasta diez cortes distintos, partes para reusar y merma.
Cada corte recibe un serial nuevo y entra en el almacén como un ingrediente
más, que luego se usa en subrecetas, recetas y emplatados.
"""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.engine.recipes import cost_tree, ingredient_rollup
from thegrill.models import (Despiece, DespieceCut, DespiecePrimal, Ingredient,
                             IngredientItem, IngredientLot, IngredientMovement,
                             MovementKind, PosProduct, Primal, PrimalStatus, Recipe,
                             RecipeKind, RecipeLine, Rotation, Unit)
from thegrill.web import auth, butchery, costing
from thegrill.web.butchery import ButcheryError

HOY = date(2026, 9, 20)


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'b.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Bistró", "ana@b.com", "Ana",
                                           "clave-larga-1", language="es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@b.com", "Luis", "clave-larga-2")
        yield s, rest, ana, luis


# ------------------------------------------------------------- utilidades
def madre(s, rest, name, unit=Unit.KG, rotation=Rotation.FEFO) -> Ingredient:
    i = Ingredient(restaurant_id=rest.id, name=name, unit=unit, rotation=rotation)
    s.add(i); s.flush(); return i


def articulo(s, rest, ing, name) -> IngredientItem:
    it = IngredientItem(restaurant_id=rest.id, ingredient_id=ing.id, name=name)
    s.add(it); s.flush(); return it


def primal(s, rest, serial, sku="STRIPLOIN_AUS", kg=10.0, usd_kg=20.0, lot="DXB20260910",
           use_by=None) -> Primal:
    p = Primal(restaurant_id=rest.id, serial=serial, sku=sku, weight_kg=kg, lot=lot,
               landed_usd_per_kg=usd_kg, piece_cost_usd=round(kg * usd_kg, 2),
               received_date=HOY - timedelta(days=5),
               frozen_use_by=use_by or HOY + timedelta(days=30))
    s.add(p); s.flush(); return p


def despiece(s, rest, tg, serials, before_kg, cuts, waste_kg=0.0) -> Despiece:
    """`cuts`: (nombre, artículo, piezas, gramos/pieza, índice de valor, es_recorte)"""
    d = Despiece(restaurant_id=rest.id, tg=tg, date=HOY, weight_before_kg=before_kg,
                 waste_kg=waste_kg, country="AUS")
    for serial in serials:
        d.primals.append(DespiecePrimal(serial=serial))
    for name, item, pieces, grams, index, trim in cuts:
        d.cuts.append(DespieceCut(cut_name=name, item_id=item.id, pieces=pieces,
                                  weight_per_piece_g=grams,
                                  total_kg=round(pieces * grams / 1000, 4),
                                  value_index=index, is_trim=trim))
    s.add(d); s.flush(); return d


def striploin_setup(s, rest):
    """Un striploin que da filetes, tiras, recorte para hamburguesa y grasa."""
    filete_m = madre(s, rest, "Striploin steak")
    tiras_m = madre(s, rest, "Striploin tiras")
    burger_m = madre(s, rest, "Beef for burger")
    grasa_m = madre(s, rest, "Grasa de vacuno")
    return (madre_items(s, rest, filete_m, "Striploin steak 250g AUS"),
            madre_items(s, rest, tiras_m, "Tiras de striploin AUS"),
            madre_items(s, rest, burger_m, "Recorte de striploin"),
            madre_items(s, rest, grasa_m, "Grasa de striploin"),
            filete_m, tiras_m, burger_m, grasa_m)


def madre_items(s, rest, ing, name):
    return articulo(s, rest, ing, name)


# ============================================================ el despiece
def test_a_primal_arrives_in_a_lot_with_its_own_number_and_cost(ctx):
    s, rest, ana, _ = ctx
    p = primal(s, rest, "8017", kg=9.4, usd_kg=21.5)
    assert p.lot == "DXB20260910"                 # el lote de recepción, común al grupo
    assert p.serial == "8017"                     # su número de pieza
    assert p.piece_cost_usd == round(9.4 * 21.5, 2)
    assert butchery.primal_cost(p) == p.piece_cost_usd
    assert p.status == PrimalStatus.IN_STOCK


def test_the_cost_survives_even_if_only_the_price_per_kilo_was_recorded(ctx):
    s, rest, ana, _ = ctx
    p = primal(s, rest, "8018", kg=8.0, usd_kg=19.0)
    p.piece_cost_usd = None
    s.flush()
    assert butchery.primal_cost(p) == 152.0       # se deduce del precio por kilo


def test_a_primal_becomes_up_to_ten_cuts_plus_trim_and_waste(ctx):
    s, rest, ana, _ = ctx
    filete, tiras, recorte, grasa, *_ = striploin_setup(s, rest)
    p = primal(s, rest, "8017", kg=10.0, usd_kg=20.0)
    d = despiece(s, rest, "TG-0001", ["8017"], 10.0, [
        ("Striploin steak", filete, 20, 250, 1.6, False),     # 5.0 kg
        ("Tiras", tiras, 8, 150, 1.0, False),                 # 1.2 kg
        ("Recorte hamburguesa", recorte, 12, 200, 0.45, True),  # 2.4 kg, para reusar
        ("Grasa", grasa, 4, 200, 0.1, False),                 # 0.8 kg
    ], waste_kg=0.6)

    result = butchery.post(s, ana, d)

    assert d.total_cuts_kg == 7.0                  # lo vendible, sin el recorte
    assert d.trim_kg == 2.4                        # las partes para reusar
    assert result.mass.ok                          # 7.0 + 2.4 + 0.6 = 10.0
    assert d.yield_pct == 70.0
    assert len(result.lots) == 4


def test_more_than_ten_cuts_is_flagged(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    primal(s, rest, "8017")
    d = despiece(s, rest, "TG-X", ["8017"], 10.0,
                 [(f"Corte {i}", filete, 1, 800, 1.0, False) for i in range(11)])
    assert any("más de los 10" in p for p in butchery.check(s, d))


def test_mass_that_does_not_reconcile_is_reported(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    primal(s, rest, "8017", kg=10.0)
    d = despiece(s, rest, "TG-0002", ["8017"], 10.0,
                 [("Steak", filete, 10, 250, 1.0, False)], waste_kg=0.5)   # faltan 7 kg
    result = butchery.post(s, ana, d)
    assert not result.mass.ok
    assert any("descuadre de masa" in i for i in result.issues)


# ======================================================= reparto del coste
def test_the_cost_of_the_primal_lands_on_what_can_be_used(ctx):
    """La merma no paga: su coste lo absorben los cortes, y por eso el filete
    real sale más caro que el precio del primal."""
    s, rest, ana, _ = ctx
    filete, tiras, recorte, grasa, *_ = striploin_setup(s, rest)
    primal(s, rest, "8017", kg=10.0, usd_kg=20.0)
    d = despiece(s, rest, "TG-0003", ["8017"], 10.0, [
        ("Striploin steak", filete, 20, 250, 1.6, False),
        ("Recorte hamburguesa", recorte, 12, 200, 0.45, True),
    ], waste_kg=2.6)

    result = butchery.post(s, ana, d)
    assert result.total_cost == 200.0
    repartido = round(sum(a.cost for a in result.allocations), 4)
    assert repartido == 200.0                       # nada se pierde por el camino

    precios = {a.cut.cut_name: a.unit_cost for a in result.allocations}
    assert precios["Striploin steak"] > 20.0        # más caro que el primal: hay merma
    assert precios["Recorte hamburguesa"] < 10.0    # el recorte vale poco
    assert precios["Striploin steak"] > precios["Recorte hamburguesa"] * 3


def test_a_bad_yield_makes_the_steak_dearer(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    for tg, kg_steak, waste in (("TG-BUENO", 8.0, 2.0), ("TG-MALO", 6.0, 4.0)):
        primal(s, rest, f"S{tg}", kg=10.0, usd_kg=20.0)
        d = despiece(s, rest, tg, [f"S{tg}"], 10.0,
                     [("Steak", filete, 1, kg_steak * 1000, 1.0, False)], waste_kg=waste)
        globals()[tg] = butchery.post(s, ana, d)
    bueno = globals()["TG-BUENO"].allocations[0].unit_cost
    malo = globals()["TG-MALO"].allocations[0].unit_cost
    assert bueno == 25.0 and malo == round(200 / 6, 6)
    assert malo > bueno


# ============================================== trazabilidad de cada corte
def test_each_cut_gets_its_own_serial_tied_to_the_primal(ctx):
    s, rest, ana, _ = ctx
    filete, tiras, recorte, grasa, *_ = striploin_setup(s, rest)
    primal(s, rest, "8017", lot="DXB20260910")
    d = despiece(s, rest, "TG-0004", ["8017"], 10.0, [
        ("Steak", filete, 20, 250, 1.6, False),
        ("Recorte", recorte, 12, 200, 0.45, True),
        ("Grasa", grasa, 4, 200, 0.1, False),
    ], waste_kg=0.6)
    butchery.post(s, ana, d)

    seriales = sorted(l.serial for l in s.query(IngredientLot))
    assert seriales == ["8017-01", "8017-02", "8017-03"]
    for lot in s.query(IngredientLot):
        assert lot.parent_serial == "8017"          # de qué pieza salió
        assert lot.parent_lot == "DXB20260910"      # en qué envío llegó
        assert lot.lot_code == "TG-0004"            # en qué despiece se cortó


def test_a_batch_of_several_primals_does_not_fake_per_piece_traceability(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    primal(s, rest, "8017", kg=10.0)
    primal(s, rest, "8018", kg=10.0)
    d = despiece(s, rest, "TG-0005", ["8017", "8018"], 20.0,
                 [("Steak", filete, 40, 350, 1.0, False)], waste_kg=6.0)
    butchery.post(s, ana, d)
    lot = s.query(IngredientLot).one()
    assert lot.serial == "TG-0005-01"               # el padre es el batch
    assert lot.parent_serial is None                # no se finge de cuál de los dos salió


def test_serials_never_collide(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    for n, tg in (("8017", "TG-A"), ("8017b", "TG-B")):
        primal(s, rest, n, kg=10.0)
    d1 = despiece(s, rest, "TG-A", ["8017"], 10.0, [("Steak", filete, 1, 8000, 1, False)], waste_kg=2.0)
    butchery.post(s, ana, d1)
    lot = s.query(IngredientLot).one()
    lot.serial = "8017b-01"                         # forzamos la colisión
    s.flush()
    d2 = despiece(s, rest, "TG-B", ["8017b"], 10.0, [("Steak", filete, 1, 8000, 1, False)], waste_kg=2.0)
    butchery.post(s, ana, d2)
    assert len({l.serial for l in s.query(IngredientLot)}) == 2


def test_you_can_follow_a_cut_from_the_primal_to_the_sale(ctx):
    s, rest, ana, luis = ctx
    filete, tiras, recorte, grasa, filete_m, tiras_m, burger_m, grasa_m = striploin_setup(s, rest)
    primal(s, rest, "8017", kg=10.0, usd_kg=20.0, lot="DXB20260910")
    d = despiece(s, rest, "TG-0006", ["8017"], 10.0, [
        ("Steak", filete, 20, 250, 1.6, False),
        ("Recorte", recorte, 12, 200, 0.45, True),
    ], waste_kg=2.6)
    butchery.post(s, ana, d)

    # el recorte se usa en la hamburguesa y se vende
    patty = Recipe(restaurant_id=rest.id, code="patty", name="Burger patty",
                   kind=RecipeKind.PREP, yield_qty=1, yield_unit=Unit.UNIT)
    patty.lines.append(RecipeLine(ingredient_id=burger_m.id, qty=0.16, waste_pct=5))
    plato = Recipe(restaurant_id=rest.id, code="cheese", name="Cheese burger",
                   kind=RecipeKind.DISH, portions=1, sale_price=13.5, vat_pct=10)
    s.add_all([patty, plato]); s.flush()
    plato.lines.append(RecipeLine(sub_recipe_id=patty.id, qty=1))
    s.add(PosProduct(restaurant_id=rest.id, pos_name="CHEESE BURGER", recipe_id=plato.id))
    s.flush()
    costing.consume_sales(s, luis, [("CHEESE BURGER", 5)], on=HOY)

    historia = butchery.trace(s, rest.id, "8017-02")
    assert historia["from_primal"] == "8017"
    assert historia["reception_lot"] == "DXB20260910"
    assert historia["butchery"] == "TG-0006"
    assert historia["ingredient"] == "Beef for burger"
    tipos = [m["kind"] for m in historia["movements"]]
    assert tipos[0] == "IN" and "SALE" in tipos      # entró del despiece y se vendió
    vendido = -sum(m["qty"] for m in historia["movements"] if m["kind"] == "SALE")
    assert round(vendido, 4) == round(5 * 0.16 / 0.95, 4)
    with pytest.raises(ButcheryError):
        butchery.trace(s, rest.id, "NO-EXISTE")


# ================================================ del primal al emplatado
def test_the_whole_chain_from_primal_to_the_plated_dish(ctx):
    """Dos striploins dan recorte; el recorte alimenta la madre «Beef for
    burger»; la madre va a la patty; la patty a la burger; la burger al plato."""
    s, rest, ana, luis = ctx
    filete, tiras, recorte, grasa, filete_m, tiras_m, burger_m, grasa_m = striploin_setup(s, rest)
    otro_recorte = articulo(s, rest, burger_m, "Recorte de cube roll")

    primal(s, rest, "8017", sku="STRIPLOIN_AUS", kg=10.0, usd_kg=20.0)
    primal(s, rest, "9001", sku="CUBE_ROLL_AUS", kg=8.0, usd_kg=26.0)
    d1 = despiece(s, rest, "TG-0010", ["8017"], 10.0, [
        ("Steak", filete, 20, 250, 1.6, False),
        ("Recorte striploin", recorte, 12, 200, 0.45, True),
    ], waste_kg=2.6)
    d2 = despiece(s, rest, "TG-0011", ["9001"], 8.0, [
        ("Tiras", tiras, 10, 400, 1.3, False),
        ("Recorte cube roll", otro_recorte, 8, 250, 0.5, True),
    ], waste_kg=2.0)
    butchery.post(s, ana, d1)
    butchery.post(s, ana, d2)

    # dos recortes distintos bajo la misma madre, gastados en una sola cola
    assert costing.stock_on_hand(s, rest.id)[burger_m.id] == 2.4 + 2.0
    precio_madre = costing.unit_costs(s, rest.id)[burger_m.id]
    assert precio_madre > 0

    pan_m = madre(s, rest, "Pan de burger", Unit.UNIT)
    costing.receive(s, ana, articulo(s, rest, pan_m, "Pan brioche"), 100, 0.38,
                    HOY + timedelta(days=4), on=HOY)

    patty = Recipe(restaurant_id=rest.id, code="patty", name="Burger patty",
                   kind=RecipeKind.PREP, yield_qty=1, yield_unit=Unit.UNIT)
    patty.lines.append(RecipeLine(ingredient_id=burger_m.id, qty=0.16, waste_pct=5))
    s.add(patty); s.flush()
    burger = Recipe(restaurant_id=rest.id, code="burger", name="Burger",
                    kind=RecipeKind.PREP, yield_qty=1, yield_unit=Unit.UNIT)
    burger.lines.append(RecipeLine(sub_recipe_id=patty.id, qty=1))
    burger.lines.append(RecipeLine(ingredient_id=pan_m.id, qty=1))
    s.add(burger); s.flush()
    plato = Recipe(restaurant_id=rest.id, code="cheese", name="Cheese burger",
                   kind=RecipeKind.DISH, portions=1, sale_price=13.5, vat_pct=10)
    plato.lines.append(RecipeLine(sub_recipe_id=burger.id, qty=1))
    s.add(plato); s.flush()

    costs = costing.unit_costs(s, rest.id)
    arbol = cost_tree(plato, costs)
    niveles = [(n.label, n.depth) for n in arbol.walk()]
    assert niveles[:3] == [("Cheese burger", 0), ("Burger", 1), ("Burger patty", 2)]
    assert ("Beef for burger", 3) in niveles

    carne = next(n for n in arbol.walk() if n.label == "Beef for burger")
    assert carne.unit_cost == precio_madre          # el precio viene del despiece real
    resumen = ingredient_rollup(arbol)
    assert resumen[0].name == "Beef for burger"

    c = costing.cost_of(s, plato)
    assert c.complete and 0 < c.food_cost_pct < 40

    # y al venderlo, el stock de los recortes baja
    s.add(PosProduct(restaurant_id=rest.id, pos_name="CHEESE BURGER", recipe_id=plato.id))
    s.flush()
    antes = costing.stock_on_hand(s, rest.id)[burger_m.id]
    costing.consume_sales(s, luis, [("CHEESE BURGER", 10)], on=HOY)
    despues = costing.stock_on_hand(s, rest.id)[burger_m.id]
    assert round(antes - despues, 4) == round(10 * 0.16 / 0.95, 4)


# ================================================== lo que no se permite
def test_a_cut_without_an_article_cannot_reach_the_storeroom(ctx):
    s, rest, ana, _ = ctx
    striploin_setup(s, rest)
    primal(s, rest, "8017")
    d = Despiece(restaurant_id=rest.id, tg="TG-0007", date=HOY, weight_before_kg=10.0, country="AUS")
    d.primals.append(DespiecePrimal(serial="8017"))
    d.cuts.append(DespieceCut(cut_name="Steak", pieces=20, weight_per_piece_g=250, total_kg=5.0))
    s.add(d); s.flush()
    with pytest.raises(ButcheryError, match="sin artículo"):
        butchery.post(s, ana, d)


def test_a_primal_is_only_marked_cut_by_the_butchery_that_confirms_it(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    p = primal(s, rest, "8017", kg=10.0)
    d = despiece(s, rest, "TG-0008", ["8017"], 10.0,
                 [("Steak", filete, 1, 8000, 1.0, False)], waste_kg=2.0)
    assert p.status == PrimalStatus.IN_STOCK
    result = butchery.post(s, ana, d)
    assert p.status == PrimalStatus.CUT and p.status_ref == "TG-0008"
    assert result.serials_cut == ["8017"]


def test_the_same_primal_cannot_be_cut_twice(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    primal(s, rest, "8017", kg=10.0)
    d1 = despiece(s, rest, "TG-A", ["8017"], 10.0, [("Steak", filete, 1, 8000, 1, False)], waste_kg=2.0)
    butchery.post(s, ana, d1)
    d2 = despiece(s, rest, "TG-B", ["8017"], 10.0, [("Steak", filete, 1, 8000, 1, False)], waste_kg=2.0)
    with pytest.raises(ButcheryError, match="ya estaba CUT"):
        butchery.post(s, ana, d2)


def test_a_butchery_is_never_posted_twice(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    primal(s, rest, "8017", kg=10.0)
    d = despiece(s, rest, "TG-0009", ["8017"], 10.0,
                 [("Steak", filete, 1, 8000, 1, False)], waste_kg=2.0)
    butchery.post(s, ana, d)
    lotes = s.query(IngredientLot).count()
    with pytest.raises(ButcheryError, match="ya estaba volcado"):
        butchery.post(s, ana, d)
    assert s.query(IngredientLot).count() == lotes      # no duplica stock


def test_a_primal_without_a_use_by_date_needs_one_given(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    p = primal(s, rest, "8017", kg=10.0)
    p.frozen_use_by = None
    p.expiry_label = None
    s.flush()
    d = despiece(s, rest, "TG-0012", ["8017"], 10.0,
                 [("Steak", filete, 1, 8000, 1, False)], waste_kg=2.0)
    with pytest.raises(ButcheryError, match="no se inventa"):
        butchery.post(s, ana, d)
    result = butchery.post(s, ana, d, use_by=HOY + timedelta(days=5))
    assert result.lots[0].expiry == HOY + timedelta(days=5)


def test_a_serial_that_is_not_in_the_register_stops_everything(ctx):
    s, rest, ana, _ = ctx
    filete, *_ = striploin_setup(s, rest)
    d = despiece(s, rest, "TG-0013", ["NOEXISTE"], 10.0,
                 [("Steak", filete, 1, 8000, 1, False)], waste_kg=2.0)
    with pytest.raises(ButcheryError, match="no está en el registro"):
        butchery.post(s, ana, d)
    assert s.query(IngredientLot).count() == 0
