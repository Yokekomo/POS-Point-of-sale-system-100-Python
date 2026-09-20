"""El cuadre del turno: el POS, la balanza y lo que se pierde en el día.

El POS dice cuántas piezas salieron a la mesa. La balanza dice cuántos kilos
faltan de lo descongelado. Cruzando las dos cosas salen las tres que interesan:

- lo vendido se descuenta de lo que hay descongelado, así el recuento de cierre
  se hace contra un número y no contra el aire;
- de los kilos que faltan entre las piezas vendidas sale el peso real por pieza;
- y comparando ese consumo con lo que dice la carta sale, en dinero, lo que se
  está perdiendo cada día por cortar de más.
"""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.models import (Alert, ConsumptionMode, Ingredient, IngredientItem,
                             IngredientLot, MovementKind, IngredientMovement, PosProduct,
                             Recipe, RecipeKind, RecipeLine, Rotation, Unit)
from thegrill.web import auth, costing, defrost

HOY = date(2026, 9, 20)


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'shift.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Hotel Marina", "albano@marina.com",
                                           "Albano", "clave-larga-1", language="es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@marina.com", "Luis",
                                    "clave-larga-2")
        yield s, rest, ana, luis


def entrecot(s, rest, ana, *, kg=6.8, precio=43.0, piezas=20, gramos_carta=330,
             pvp=29.50, serial="8017-01"):
    """Un lote de entrecots en cámara y el plato que los vende."""
    corte = Ingredient(restaurant_id=rest.id, name="Striploin steak", unit=Unit.KG,
                       rotation=Rotation.FEFO, consumption=ConsumptionMode.COUNT)
    s.add(corte); s.flush()
    item = IngredientItem(restaurant_id=rest.id, ingredient_id=corte.id, name="Striploin AUS")
    s.add(item); s.flush()
    lot = costing.receive(s, ana, item, kg, precio, HOY + timedelta(days=10),
                          lot_code="TG-0001", received=HOY, on=HOY)
    lot.serial = serial
    lot.pieces = piezas
    s.flush()

    plato = Recipe(restaurant_id=rest.id, code="entrecot", name="Entrecot a la brasa",
                   kind=RecipeKind.DISH, portions=1, sale_price=pvp, vat_pct=10.0)
    s.add(plato); s.flush()
    s.add(RecipeLine(recipe_id=plato.id, ingredient_id=corte.id,
                     qty=gramos_carta / 1000, waste_pct=0.0, sort_order=0))
    s.add(PosProduct(restaurant_id=rest.id, recipe_id=plato.id, pos_code="1201",
                     pos_name="ENTRECOT"))
    s.flush()
    return corte, lot, plato


def turno(s, user, *, sale=8, kg_sale=2.81, queda=2, kg_queda=0.70, serial="8017-01"):
    """Sale carne a descongelar y al cerrar se cuenta lo que queda."""
    defrost.intake(s, user, serial, sale, kg_sale, on=HOY)
    if queda is not None:
        defrost.count(s, user, serial, queda, kg_queda, on=HOY)


# ------------------------------ lo vendido se descuenta de lo descongelado
def test_what_the_pos_sells_comes_off_what_is_thawed(ctx):
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana)
    defrost.intake(s, luis, "8017-01", 8, 2.81, on=HOY)

    antes = defrost.shift_states(s, rest.id, HOY)[0]
    assert antes.out_pieces == 8
    assert antes.sold_pieces == 0
    assert antes.expected_pieces == 8            # sin ventas, deberían quedar las ocho

    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)

    despues = defrost.shift_states(s, rest.id, HOY)[0]
    assert despues.sold_pieces == 6              # el POS se lleva seis
    assert despues.expected_pieces == 2          # y el recuento se hace contra dos


def test_the_pos_can_never_sell_more_than_what_went_out(ctx):
    """Si el POS dice más de lo que salió, no se inventa stock descongelado."""
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana)
    defrost.intake(s, luis, "8017-01", 4, 1.40, on=HOY)
    costing.consume_sales(s, luis, [("ENTRECOT", 9)], on=HOY)

    estado = defrost.shift_states(s, rest.id, HOY)[0]
    assert estado.sold_pieces == 4
    assert estado.expected_pieces == 0           # cero, nunca negativo


def test_sales_are_spread_over_the_pieces_that_are_thawed(ctx):
    """Dos piezas del mismo corte fuera: lo vendido sale de la primera."""
    s, rest, ana, luis = ctx
    corte, lot, plato = entrecot(s, rest, ana)
    otro = costing.receive(s, ana, s.query(IngredientItem).one(), 5.0, 41.0,
                           HOY + timedelta(days=12), lot_code="TG-0002", on=HOY)
    otro.serial = "8018-01"; s.flush()
    defrost.intake(s, luis, "8017-01", 4, 1.40, on=HOY)
    defrost.intake(s, luis, "8018-01", 6, 2.10, on=HOY)

    costing.consume_sales(s, luis, [("ENTRECOT", 7)], on=HOY)

    estados = {e.serial: e for e in defrost.shift_states(s, rest.id, HOY)}
    assert estados["8017-01"].sold_pieces == 4    # se agota la primera
    assert estados["8018-01"].sold_pieces == 3    # y el resto sale de la segunda


# ----------------------------------------------- de ahí sale el peso real
def test_the_weight_per_piece_comes_out_of_the_count_and_the_till(ctx):
    """Salieron 2,81 kg en 8 piezas; quedan 0,70 en 2; el POS vendió 6."""
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana, gramos_carta=330)
    turno(s, luis)
    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)

    result = defrost.close(s, luis, on=HOY)

    fila = result.consumed[0]
    assert fila.kg == pytest.approx(2.11, abs=0.001)      # lo que falta de verdad
    assert fila.sold_pieces == 6
    assert fila.avg_g_per_piece == pytest.approx(351.7, abs=0.1)   # 2,11 entre 6
    variacion = result.variances[0]
    assert variacion.units_sold == 6
    assert variacion.real_g_per_unit == pytest.approx(351.7, abs=0.1)
    assert variacion.theoretical_g_per_unit == pytest.approx(330.0, abs=0.1)
    assert variacion.overcut                              # se corta por encima de la carta


def test_the_real_weight_is_measured_against_what_was_sold_not_what_is_missing(ctx):
    """Si falta una pieza que nadie vendió, el peso por pieza no se falsea."""
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana)
    turno(s, luis, sale=8, kg_sale=2.64, queda=1, kg_queda=0.33)   # faltan 7 piezas
    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)      # pero solo 6 vendidas

    result = defrost.close(s, luis, on=HOY)
    fila = result.consumed[0]
    assert fila.pieces == 7                                # de la cámara faltan siete
    assert fila.sold_pieces == 6                           # la mesa pagó seis
    assert fila.avg_g_per_piece == pytest.approx(385.0, abs=0.5)   # 2,31 entre 6
    assert fila.piece_gap == 1                             # y una pieza sin explicar


def test_a_piece_that_nobody_sold_raises_an_alert(ctx):
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana)
    turno(s, luis, sale=8, kg_sale=2.64, queda=1, kg_queda=0.33)
    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)

    result = defrost.close(s, luis, on=HOY)
    assert [c.serial for c in result.piece_gaps] == ["8017-01"]
    aviso = next(a for a in result.alerts if a.code == "defrost.pieces")
    assert "8017-01" in aviso.message and "1" in aviso.message


def test_when_everything_adds_up_nobody_is_woken_up(ctx):
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana, gramos_carta=351.7)
    turno(s, luis)
    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)

    result = defrost.close(s, luis, on=HOY)
    assert not result.piece_gaps
    assert [a.code for a in result.alerts] == []


# ----------------------------------- lo que se pierde en el día, en dinero
def test_the_day_says_how_much_money_is_going_out_of_the_door(ctx):
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana, precio=43.0, gramos_carta=330)
    turno(s, luis)
    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)

    result = defrost.close(s, luis, on=HOY)

    # La carta dice 6 × 330 g = 1,98 kg. La balanza dice 2,11.
    variacion = result.variances[0]
    assert variacion.theoretical_kg == pytest.approx(1.98, abs=0.001)
    assert variacion.real_kg == pytest.approx(2.11, abs=0.001)
    assert variacion.gap_kg == pytest.approx(0.13, abs=0.001)
    assert variacion.loss_cost == pytest.approx(0.13 * 43.0, abs=0.01)   # 5,59
    assert result.loss_cost == pytest.approx(5.59, abs=0.01)
    assert result.loss_kg == pytest.approx(0.13, abs=0.001)


def test_cutting_short_also_shows_up_with_its_sign(ctx):
    """Gastar menos de lo que dice la carta tampoco es normal: o se corta corto,
    o falta un apunte."""
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana, precio=43.0, gramos_carta=400)
    turno(s, luis)
    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)

    result = defrost.close(s, luis, on=HOY)
    assert result.loss_kg < 0
    assert result.loss_cost < 0
    assert not result.variances[0].overcut


def test_a_big_daily_loss_is_critical(ctx):
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana, kg=60.0, precio=43.0, gramos_carta=330)
    turno(s, luis, sale=60, kg_sale=21.0, queda=0, kg_queda=0.0)
    costing.consume_sales(s, luis, [("ENTRECOT", 40)], on=HOY)

    result = defrost.close(s, luis, on=HOY)
    aviso = next(a for a in result.alerts if a.code == "defrost.loss")
    assert aviso.severity.value == "CRITICAL"
    assert result.loss_cost > 50


def test_a_day_that_adds_up_does_not_report_a_loss(ctx):
    s, rest, ana, luis = ctx
    entrecot(s, rest, ana, precio=43.0, gramos_carta=351.7)
    turno(s, luis)
    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)

    result = defrost.close(s, luis, on=HOY)
    assert abs(result.loss_cost) < 1
    assert not [a for a in result.alerts if a.code == "defrost.loss"]


# --------------------------------------------- nunca se descuenta dos veces
def test_a_cut_that_is_deducted_on_sale_is_not_deducted_again_by_the_count(ctx):
    """El error caro: descontar la misma carne al vender y al contar."""
    s, rest, ana, luis = ctx
    corte, lot, plato = entrecot(s, rest, ana)
    corte.consumption = ConsumptionMode.RECIPE          # se descuenta al vender
    s.flush()

    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)
    tras_venta = lot.qty_remaining
    assert tras_venta == pytest.approx(6.8 - 6 * 0.33, abs=0.001)

    turno(s, luis)
    result = defrost.close(s, luis, on=HOY)

    assert lot.qty_remaining == tras_venta              # el conteo no vuelve a descontar
    assert result.not_by_count == ["8017-01"]
    assert any(a.code == "defrost.not_by_count" for a in result.alerts)
    assert result.cost == 0.0


def test_a_cut_measured_by_count_is_not_deducted_when_it_is_sold(ctx):
    s, rest, ana, luis = ctx
    corte, lot, plato = entrecot(s, rest, ana)
    assert corte.consumption == ConsumptionMode.COUNT

    costing.consume_sales(s, luis, [("ENTRECOT", 6)], on=HOY)
    assert lot.qty_remaining == 6.8                     # la venta no toca la cámara

    turno(s, luis)
    defrost.close(s, luis, on=HOY)
    assert lot.qty_remaining == pytest.approx(6.8 - 2.11, abs=0.001)   # el conteo sí
    salidas = s.query(IngredientMovement).filter_by(kind=MovementKind.SALE).all()
    assert [m.source for m in salidas] == ["defrost"]   # una sola salida, la de verdad
