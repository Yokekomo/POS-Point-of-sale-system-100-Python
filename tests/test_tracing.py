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


def test_the_history_shows_the_label_of_each_cut(ctx):
    s, rest, ana, luis = ctx
    filete_m = madre(s, rest, "Striploin steak")
    p = primal(s, rest, "8017", kg=10.0)
    p.grade, p.origin = "MB9+", "AUS"
    s.flush()
    despiezar(s, rest, ana, "TG-0020", ["8017"], 10.0,
              [("Steak", articulo(s, rest, filete_m, "Filete"), 20, 330, 1.0, False)],
              merma=3.4)
    h = tracing.history(s, rest.id, "8017")
    corte = h.butchery.cuts[0]
    assert corte.label == "330 g · MB9+ · AUS"
    assert corte.pieces == 20 and corte.piece_weight_g == 330


def test_it_says_how_many_pieces_are_left(ctx):
    s, rest, ana, luis = ctx
    p, filete_m, burger_m = montar_burger(s, rest, ana)
    h = tracing.history(s, rest.id, "8017")
    filete = next(c for c in h.butchery.cuts if not c.is_trim)
    assert filete.avg_piece_g == 250                     # 5 kg entre 20 piezas
    assert filete.remaining_pieces == 20
    costing.consume_sales(s, luis, [("ENTRECOT", 4)], on=HOY)
    h = tracing.history(s, rest.id, "8017")
    filete = next(c for c in h.butchery.cuts if not c.is_trim)
    assert filete.remaining_pieces == 16                 # se han ido cuatro


def test_without_a_piece_weight_it_does_not_guess_the_count(ctx):
    """La etiqueta dice lo que sabe: sin peso por pieza, no cuenta piezas."""
    s, rest, ana, luis = ctx
    from thegrill.models import IngredientLot
    montar_burger(s, rest, ana)
    lote = s.query(IngredientLot).filter_by(serial="8017-01").one()
    lote.piece_weight_g = lote.pieces = None
    s.flush()
    h = tracing.history(s, rest.id, "8017")
    corte = next(c for c in h.butchery.cuts if c.serial == "8017-01")
    assert corte.remaining_pieces is None        # sin recuento no se estiman piezas
    assert corte.label == "250 g · AUS"          # el peso de carta se sigue sabiendo


def test_a_cut_with_nothing_on_its_label_says_nothing(ctx):
    s, rest, ana, luis = ctx
    from thegrill.models import IngredientLot
    montar_burger(s, rest, ana)
    lote = s.query(IngredientLot).filter_by(serial="8017-01").one()
    lote.piece_weight_g = lote.pieces = None
    lote.nominal_piece_g = lote.grade = lote.origin = None
    s.flush()
    h = tracing.history(s, rest.id, "8017")
    corte = next(c for c in h.butchery.cuts if c.serial == "8017-01")
    assert corte.label == ""


def test_the_history_compares_the_real_cut_with_the_target(ctx):
    """Lo que dice si el carnicero está cortando de más."""
    s, rest, ana, luis = ctx
    from thegrill.models import Despiece as D, DespiecePrimal as DP, DespieceCut as DC
    filete_m = madre(s, rest, "Striploin steak")
    item = articulo(s, rest, filete_m, "Filete")
    primal(s, rest, "8017", kg=10.0)
    d = D(restaurant_id=rest.id, tg="TG-0030", date=HOY, weight_before_kg=10.0,
          waste_kg=2.2, country="AUS")
    d.primals.append(DP(serial="8017"))
    d.cuts.append(DC(cut_name="Steak", item_id=item.id, pieces=22,
                     weight_per_piece_g=330, total_kg=7.8, value_index=1.0))
    s.add(d)
    s.flush()
    butchery.post(s, ana, d)

    corte = tracing.history(s, rest.id, "8017").butchery.cuts[0]
    assert corte.pieces == 22
    assert corte.avg_piece_g == 354.5                 # 7,8 kg entre 22
    assert corte.nominal_piece_g == 330
    assert corte.piece_gap_pct == 7.4                 # se corta un 7 % de más
    assert corte.label.startswith("330 g (~354.5 g)")


def test_the_pieces_left_follow_the_real_average(ctx):
    s, rest, ana, luis = ctx
    from thegrill.models import Despiece as D, DespiecePrimal as DP, DespieceCut as DC
    filete_m = madre(s, rest, "Striploin steak")
    item = articulo(s, rest, filete_m, "Filete")
    primal(s, rest, "8017", kg=10.0)
    d = D(restaurant_id=rest.id, tg="TG-0031", date=HOY, weight_before_kg=10.0,
          waste_kg=2.2, country="AUS")
    d.primals.append(DP(serial="8017"))
    d.cuts.append(DC(cut_name="Steak", item_id=item.id, pieces=20,
                     weight_per_piece_g=330, total_kg=7.8, value_index=1.0))
    s.add(d)
    s.flush()
    butchery.post(s, ana, d)
    corte = tracing.history(s, rest.id, "8017").butchery.cuts[0]
    assert corte.avg_piece_g == 390
    assert corte.remaining_pieces == 20               # nada vendido todavía


def test_the_label_shows_the_food_cost_of_that_cut(ctx):
    """Lo que interesa de un vistazo: cuánto pesa de verdad y a qué FC sale."""
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    filete = next(c for c in tracing.history(s, rest.id, "8017").butchery.cuts
                  if not c.is_trim)
    assert filete.food_cost_pct is None
    assert "FC" not in filete.label              # sin ventas no hay food cost que dar

    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)

    filete = next(c for c in tracing.history(s, rest.id, "8017").butchery.cuts
                  if not c.is_trim)
    assert filete.food_cost_pct is not None
    # Sin decimales y hacia arriba: un food cost no se redondea a la baja.
    assert f"{butchery.ceil_pct(filete.food_cost_pct)} % FC" in filete.label
    assert butchery.ceil_pct(filete.food_cost_pct) >= filete.food_cost_pct
    assert filete.label.startswith("250 g (")    # el peso de carta sigue delante


def test_a_cut_on_target_shows_only_its_food_cost(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    costing.consume_sales(s, luis, [("ENTRECOT", 4)], on=HOY)
    filete = next(c for c in tracing.history(s, rest.id, "8017").butchery.cuts
                  if not c.is_trim)
    assert filete.avg_piece_g == filete.nominal_piece_g == 250
    assert "~" not in filete.label               # clavado: no se repite el peso
    assert "% FC" in filete.label and filete.label.startswith("250 g (")


def test_meat_that_moved_to_another_site_is_not_unaccounted_for():
    """Lo que viaja no desaparece: tiene su número y su fila.

    Un corte que sale al local de la playa —o unas piezas que salen del arcón
    con su propio número— deja de estar en este lote, pero está perfectamente
    seguido. Contándolo como «sin explicar» se marcaba en rojo toda la carne
    trasladada, y una columna que avisa de lo que sí cuadra deja de mirarse a
    la semana.
    """
    from thegrill.web.tracing import CutNode

    corte = CutNode(serial="8017-01", name="Entrecot", unit="KG",
                    produced_kg=5.918, sold_kg=0.0, waste_kg=0.0,
                    remaining_kg=0.189, moved_kg=5.729)
    assert corte.unaccounted_kg == 0.0

    # Y lo que de verdad falta sigue saliendo.
    corte.moved_kg = 5.0
    assert corte.unaccounted_kg == 0.729


def test_the_food_cost_inside_the_cut_label_is_only_for_who_sees_money():
    """La etiqueta verde del corte llevaba el food cost dentro.

    «330 g (28 % FC) · MB9+ · AUS» se pintaba igual para todos, así que el
    carnicero veía a qué porcentaje salía cada corte. El peso de sus piezas y
    la calidad sí son suyos; el porcentaje, no, igual que no lo es el precio
    del kilo.
    """
    from thegrill.web.tracing import CutNode

    corte = CutNode(serial="8017-01", name="Entrecot", unit="KG", pieces=18,
                    nominal_piece_g=300.0, produced_kg=5.4, grade="MB9+", origin="AUS",
                    revenue=372.48, sold_cost=100.75)
    assert "% FC" in corte.label_with(True)
    assert "% FC" not in corte.label_with(False)
    # Lo que sí es suyo se queda en las dos.
    for texto in (corte.label_with(True), corte.label_with(False)):
        assert "300 g" in texto and "MB9+" in texto and "AUS" in texto


def test_a_butchery_says_how_many_portions_came_out_and_at_what_weight():
    """De nueve kilos salieron treinta y ocho raciones, a 228 g de media.

    Es el número con el que se mira un despiece de un vistazo y el que se
    compara con el de la semana pasada. Lo que sale entero para cortarlo
    delante del cliente no tiene raciones, y meter sus kilos en la media la
    hunde sin que nadie haya cortado ancho ni estrecho.
    """
    from datetime import date as fecha

    from thegrill.web.tracing import ButcheryNode, CutNode

    despiece = ButcheryNode(
        tg="TG-9100", date=fecha(2026, 9, 9), weight_before_kg=9.4, waste_kg=0.55,
        trim_kg=0.6, total_cuts_kg=8.68, yield_pct=92.3, cuts=[
            CutNode(serial="a", name="Entrecot", unit="KG", pieces=18, produced_kg=5.4),
            CutNode(serial="b", name="Solomillo", unit="KG", pieces=10, produced_kg=2.2),
            # A peso: entra entero en cámara y no se cuenta en la media.
            CutNode(serial="c", name="Lomo a peso", unit="KG", pieces=None,
                    produced_kg=1.08)])
    assert despiece.pieces == 28
    assert despiece.avg_piece_g == 271.4          # (5,4 + 2,2) kg entre 28

    # Un despiece que sale entero a peso no se inventa una media.
    solo_peso = ButcheryNode(tg="TG-1", date=fecha(2026, 9, 9), weight_before_kg=9.0,
                             waste_kg=0.0, trim_kg=0.0, total_cuts_kg=9.0, yield_pct=100.0,
                             cuts=[CutNode(serial="x", name="Lomo", unit="KG",
                                           pieces=None, produced_kg=9.0)])
    assert solo_peso.pieces == 0
    assert solo_peso.avg_piece_g is None


def test_a_cut_says_which_dishes_it_ended_up_in():
    """La pregunta de verdad sobre lo aprovechado: ¿dónde ha ido?

    El recorte de un lomo caro acaba en la hamburguesa o en el tartar, y saber
    en cuál de los dos es lo que dice si ese recorte se está pagando. Las
    ventas estaban una a una con su día: el detalle se veía y lo importante no.
    """
    from datetime import date as fecha

    from thegrill.web.tracing import CutNode, SaleLine

    corte = CutNode(serial="8017-04", name="Recorte de vacuno", unit="KG", is_trim=True,
                    sales=[
        SaleLine(date=fecha(2026, 9, 10), dish="HAMBURGUESA", kg=0.18, cost=1.0,
                 revenue=16.5, source="pos"),
        SaleLine(date=fecha(2026, 9, 11), dish="TARTAR", kg=0.12, cost=0.7,
                 revenue=19.0, source="pos"),
        SaleLine(date=fecha(2026, 9, 12), dish="HAMBURGUESA", kg=0.18, cost=1.0,
                 revenue=16.5, source="pos")])

    # Juntos por plato, y el que más carne se ha llevado, primero.
    assert corte.by_dish == [("HAMBURGUESA", 0.36, 33.0), ("TARTAR", 0.12, 19.0)]

    # Un corte que no se ha vendido no inventa platos.
    assert CutNode(serial="x", name="y", unit="KG").by_dish == []


# ================================================== lo que faltaba de la historia
#
# Tres agujeros, y los tres del mismo tamaño: lo que la trazabilidad no enseña
# no existe. Lo trasladado a otra sede, lo que se limpia de la pieza y lo que
# se corta y se cobra al peso no salían por ninguna parte; y en un despiece de
# varias piezas cada una se apuntaba el cien por cien de lo que salió.
from thegrill.models import IngredientLot, Site, SiteKind, Storage, WeightSale  # noqa: E402
from thegrill.web import aging, sites                             # noqa: E402


def _sede(s, rest, nombre, kind=SiteKind.OUTLET):
    site = Site(restaurant_id=rest.id, name=nombre, kind=kind, active=True)
    s.add(site); s.flush(); return site


# ------------------------------------------------------- lo que se trasladó
def test_a_cut_sent_to_another_site_is_still_followed(ctx):
    """Antes la historia de la pieza se acababa en el muelle del obrador."""
    s, rest, ana, luis = ctx
    obrador = _sede(s, rest, "Obrador", SiteKind.WAREHOUSE)
    playa = _sede(s, rest, "Playa")
    p, filete_m, _burger_m = montar_burger(s, rest, ana)
    for lote in s.query(IngredientLot).filter_by(restaurant_id=rest.id):
        lote.site_id = obrador.id
    s.flush()

    filete = (s.query(IngredientLot)
              .filter_by(restaurant_id=rest.id, ingredient_id=filete_m.id).one())
    sites.send_cut(s, ana, filete.serial, 2.0, playa.id, on=HOY)

    h = tracing.history(s, rest.id, "8017")
    corte = next(c for c in h.butchery.cuts if c.serial == filete.serial)
    assert corte.moved_kg == 2.0
    assert [hijo.serial for hijo in corte.children] == [f"{filete.serial}·T1"]
    hijo = corte.children[0]
    assert hijo.site == "Playa"                 # y se dice dónde está
    assert hijo.remaining_kg == 2.0


def test_and_what_it_sells_over_there_counts_for_the_piece(ctx):
    s, rest, ana, luis = ctx
    obrador = _sede(s, rest, "Obrador", SiteKind.WAREHOUSE)
    playa = _sede(s, rest, "Playa")
    p, filete_m, _b = montar_burger(s, rest, ana)
    for lote in s.query(IngredientLot).filter_by(restaurant_id=rest.id):
        lote.site_id = obrador.id
    s.flush()
    filete = (s.query(IngredientLot)
              .filter_by(restaurant_id=rest.id, ingredient_id=filete_m.id).one())
    enviado = sites.send_cut(s, ana, filete.serial, 2.0, playa.id, on=HOY)

    hijo = (s.query(IngredientLot)
            .filter_by(restaurant_id=rest.id, serial=enviado.new_serial).one())
    s.add(IngredientMovement(
        restaurant_id=rest.id, ingredient_id=filete_m.id, lot_id=hijo.id, date=HOY,
        kind=MovementKind.SALE, qty=-1.0, cost=round(1.0 * hijo.unit_cost, 6),
        source="pos", source_ref="ENTRECOT", created_by=ana.id))
    hijo.qty_remaining = round(hijo.qty_remaining - 1.0, 6)
    s.flush()

    h = tracing.history(s, rest.id, "8017")
    corte = next(c for c in h.butchery.cuts if c.serial == filete.serial)
    assert corte.children[0].sold_kg == 1.0
    assert corte.children[0].revenue > 0
    assert h.sold_kg >= 1.0                     # y sube el total de la pieza
    assert h.revenue >= corte.children[0].revenue


def test_a_grandchild_is_followed_too(ctx):
    """Se manda a la playa, y allí lo sacan del arcón: dos saltos."""
    s, rest, ana, luis = ctx
    obrador = _sede(s, rest, "Obrador", SiteKind.WAREHOUSE)
    playa = _sede(s, rest, "Playa")
    p, filete_m, _b = montar_burger(s, rest, ana)
    for lote in s.query(IngredientLot).filter_by(restaurant_id=rest.id):
        lote.site_id = obrador.id
    s.flush()
    filete = (s.query(IngredientLot)
              .filter_by(restaurant_id=rest.id, ingredient_id=filete_m.id).one())
    enviado = sites.send_cut(s, ana, filete.serial, 3.0, playa.id, on=HOY)
    hijo = (s.query(IngredientLot)
            .filter_by(restaurant_id=rest.id, serial=enviado.new_serial).one())
    hijo.frozen = True
    s.flush()
    defrost.thaw(s, ana, hijo, 1.0, 2, on=HOY)

    h = tracing.history(s, rest.id, "8017")
    corte = next(c for c in h.butchery.cuts if c.serial == filete.serial)
    nieto = corte.children[0].children[0]
    assert nieto.serial == f"{enviado.new_serial}·D1"
    assert nieto.produced_kg == 1.0


# -------------------------------------------------------- lo que se limpió
def test_what_was_cleaned_off_the_piece_shows_up(ctx):
    """Una pieza que se limpia y no se despieza tenía historia de una línea."""
    s, rest, ana, luis = ctx
    recorte_m = madre(s, rest, "Recorte de limpieza")
    item = articulo(s, rest, recorte_m, "Recorte")
    pieza = primal(s, rest, "8020", kg=10.0, usd_kg=20.0)
    pieza.expiry_label = HOY + timedelta(days=10)
    s.flush()
    aging.trim(s, ana, "8020", removed_kg=1.2,
               parts=[aging.TrimPart(kg=0.8, item_id=item.id, value_index=0.45)],
               on=HOY)

    h = tracing.history(s, rest.id, "8020")
    assert h.butchery is None                   # no se ha cortado
    assert [t.produced_kg for t in h.trims] == [0.8]
    assert h.trims[0].is_trim
    assert h.remaining_kg == 0.8                # y cuenta en lo que queda


def test_a_trim_that_travels_is_followed_as_well(ctx):
    s, rest, ana, luis = ctx
    obrador = _sede(s, rest, "Obrador", SiteKind.WAREHOUSE)
    playa = _sede(s, rest, "Playa")
    recorte_m = madre(s, rest, "Recorte de limpieza")
    item = articulo(s, rest, recorte_m, "Recorte")
    pieza = primal(s, rest, "8021", kg=10.0, usd_kg=20.0)
    pieza.expiry_label = HOY + timedelta(days=10)
    s.flush()
    aging.trim(s, ana, "8021", removed_kg=1.2,
               parts=[aging.TrimPart(kg=0.8, item_id=item.id, value_index=0.45)],
               on=HOY)
    lote = (s.query(IngredientLot)
            .filter_by(restaurant_id=rest.id, parent_serial="8021").one())
    lote.site_id = obrador.id
    s.flush()
    sites.send_cut(s, ana, lote.serial, 0.3, playa.id, on=HOY)

    h = tracing.history(s, rest.id, "8021")
    assert h.trims[0].moved_kg == 0.3
    assert h.trims[0].children[0].site == "Playa"


# ----------------------------------------------------- lo que se cortó al peso
def test_what_was_cut_and_charged_by_the_kilo_shows_up(ctx):
    """Sin esto, una pieza madurada vendida entera al corte no se vendió nunca."""
    s, rest, ana, luis = ctx
    pieza = primal(s, rest, "8022", kg=9.0, usd_kg=30.0)
    pieza.storage = Storage.AGING
    s.flush()
    aging.sell_by_weight(s, ana, "8022", grams=400, price=48.0, dish="Chuletón", on=HOY)

    h = tracing.history(s, rest.id, "8022")
    assert len(h.weight_sales) == 1
    assert h.weight_sold_kg == 0.4
    assert h.weight_revenue == 48.0
    assert h.sold_kg == 0.4 and h.revenue == 48.0
    assert h.food_cost_pct is not None          # y ya se le puede calcular el FC


# ---------------------------------------- el despiece que comparten varias
def test_a_butchery_of_three_does_not_credit_each_one_with_everything(ctx):
    """El agujero que multiplicaba por tres los kilos vendidos de cada pieza."""
    s, rest, ana, luis = ctx
    filete_m = madre(s, rest, "Striploin steak")
    item = articulo(s, rest, filete_m, "Filete 250g")
    primal(s, rest, "9001", kg=10.0, usd_kg=20.0)
    primal(s, rest, "9002", kg=5.0, usd_kg=20.0)
    primal(s, rest, "9003", kg=5.0, usd_kg=20.0)
    despiezar(s, rest, ana, "TG-0020", ["9001", "9002", "9003"], 20.0, [
        ("Striploin steak", item, 60, 250, 1.0, False),
    ], merma=5.0)

    grande = tracing.history(s, rest.id, "9001")
    pequeña = tracing.history(s, rest.id, "9002")
    assert grande.butchery.shared
    assert grande.butchery.share == 0.5          # diez kilos de veinte
    assert pequeña.butchery.share == 0.25
    # Y lo que se le apunta a cada una es su parte, no el despiece entero.
    assert grande.remaining_kg == round(15.0 * 0.5, 4)
    assert pequeña.remaining_kg == round(15.0 * 0.25, 4)
    # Las tres partes suman el despiece: ni sobra ni falta.
    tercera = tracing.history(s, rest.id, "9003")
    assert round(grande.butchery.share + pequeña.butchery.share
                 + tercera.butchery.share, 6) == 1.0


def test_with_no_weights_the_butchery_splits_evenly(ctx):
    s, rest, ana, luis = ctx
    filete_m = madre(s, rest, "Striploin steak")
    item = articulo(s, rest, filete_m, "Filete 250g")
    for serial in ("9101", "9102"):
        pieza = primal(s, rest, serial, kg=10.0, usd_kg=20.0)
        pieza.weight_kg = 0.0
        pieza.received_kg = 0.0
    s.flush()
    despiezar(s, rest, ana, "TG-0021", ["9101", "9102"], 20.0, [
        ("Striploin steak", item, 60, 250, 1.0, False),
    ], merma=5.0)
    assert tracing.history(s, rest.id, "9101").butchery.share == 0.5


def test_one_piece_alone_still_gets_all_of_it(ctx):
    s, rest, ana, luis = ctx
    montar_burger(s, rest, ana)
    h = tracing.history(s, rest.id, "8017")
    assert h.butchery.share == 1.0 and not h.butchery.shared


# =========================== lo que pregunta un inspector delante de una pieza
#
# Siempre lo mismo: de dónde vino, qué matadero, qué lote, a qué temperatura
# bajó del camión, hasta cuándo valía, qué le pasó mientras estuvo guardada y
# en qué acabó. En una hoja, y que se pueda imprimir.
def _con_papeles(s, rest, serial="9017", **extra):
    datos = dict(restaurant_id=rest.id, serial=serial, sku="RIBEYE_AUS",
                 lot="DXB20260910", weight_kg=9.0, received_kg=9.0,
                 received_date=HOY, landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                 supplier_lot="A-77123", producer_plant="Teys Biloela",
                 est_code="ES 10.03456/M CE", origin="AUS", grade="MB4",
                 slaughter_date=HOY - timedelta(days=10),
                 expiry_label=HOY + timedelta(days=90),
                 frozen_use_by=HOY + timedelta(days=200),
                 arrival_c=2.0, arrival=Storage.CHILLED,
                 status=PrimalStatus.IN_STOCK)
    datos.update(extra)
    pieza = Primal(**datos)
    s.add(pieza); s.flush()
    return pieza


def test_the_use_by_date_is_on_the_sheet(ctx):
    """Toda la rotación del programa se sostiene sobre esa fecha, y no salía.

    Y las dos: una pieza congelada se rige por la del arcón y la de la etiqueta
    deja de valer, así que enseñar solo una es justo el dato que falta el día
    que preguntan por ella.
    """
    s, rest, ana, _ = ctx
    _con_papeles(s, rest)
    h = tracing.history(s, rest.id, "9017")
    assert h.label.use_by == HOY + timedelta(days=90)
    assert h.label.frozen_use_by == HOY + timedelta(days=200)


def test_the_arrival_temperature_reaches_the_sheet_on_its_own(ctx):
    """La temperatura sola tiene que llegar hasta la hoja, sin nada más.

    Que esté en la base no basta: se enseñaba solo si además constaba si venía
    fresca o congelada, así que una casa que apuntaba los grados y no lo otro
    tenía el número guardado y no salía en ninguna pantalla. Lo que se mira
    aquí es que la etiqueta llega con ella; que la pantalla la pinte se mira en
    `tests/test_meat_app.py`, delante del HTML.
    """
    s, rest, ana, _ = ctx
    _con_papeles(s, rest, serial="9099", arrival=None, supplier_lot=None,
                 producer_plant=None, est_code=None, origin=None, grade=None,
                 slaughter_date=None, expiry_label=None, frozen_use_by=None)
    h = tracing.history(s, rest.id, "9099")
    assert h.label, "la etiqueta salió vacía teniendo la temperatura"
    assert h.label.arrival_c == 2.0 and h.label.arrival is None


def test_what_happened_in_the_chiller_is_listed(ctx):
    """Se veía lo que pesa hoy, no las seis semanas que la llevaron hasta ahí.

    En una carne madurada esa es la mitad de lo que se pregunta: cuánto
    perdió, cuándo y por qué.
    """
    s, rest, ana, _ = ctx
    _con_papeles(s, rest, serial="9018")
    aging.weigh(s, ana, "9018", 8.6, on=HOY + timedelta(days=7))
    aging.weigh(s, ana, "9018", 8.2, on=HOY + timedelta(days=14))
    h = tracing.history(s, rest.id, "9018")
    assert len(h.weighings) == 2, h.weighings
    assert [round(p.kg, 3) for p in h.weighings] == [8.6, 8.2]
    # Y en orden: una historia al revés no es una historia.
    assert h.weighings[0].date <= h.weighings[1].date
    assert round(h.weighings[1].previous_kg, 3) == 8.6
