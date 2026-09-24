"""Descongelado y recuento de cierre: el consumo real de la carne al corte."""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.engine.defrost import SerialState, reconcile, variances
from thegrill.models import (ConsumptionMode, DefrostEntry, DefrostKind, Ingredient,
                             IngredientItem, IngredientLot, IngredientMovement,
                             MovementKind, PosProduct, Recipe, RecipeKind, RecipeLine, Unit)
from thegrill.web import auth, costing, defrost
from thegrill.web.defrost import DefrostError

HOY = date(2026, 9, 20)
AYER = HOY - timedelta(days=1)


# ==================================================== el motor, por su cuenta
def test_what_was_used_is_what_is_missing():
    st = SerialState("8017-01", "Striploin steak", opening_kg=4.0, opening_pieces=16,
                     intake_kg=6.0, intake_pieces=24, closing_kg=3.5, closing_pieces=14)
    row = reconcile([st])[0]
    assert row.kg == 6.5 and row.pieces == 26 and row.counted
    assert row.avg_g_per_piece == 250.0


def test_without_a_closing_count_nothing_is_invented():
    st = SerialState("8017-01", "Steak", intake_kg=6.0, intake_pieces=24)
    row = reconcile([st])[0]
    assert not row.counted and row.kg == 0.0 and row.available_kg == 6.0


def test_more_left_than_there_was_is_impossible():
    st = SerialState("8017-01", "Steak", opening_kg=1.0, intake_kg=2.0, closing_kg=5.0,
                     closing_pieces=0)
    row = reconcile([st])[0]
    assert row.impossible and row.kg == 0.0      # no se descuenta un consumo negativo


def test_the_variance_says_if_they_are_cutting_too_much():
    rows = variances(real={"Striploin steak": 11.0}, theoretical={"Striploin steak": 10.0},
                     units={"Striploin steak": 40})
    v = rows[0]
    assert v.gap_kg == 1.0 and v.gap_pct == 10.0 and v.overcut
    assert v.real_g_per_unit == 275.0 and v.theoretical_g_per_unit == 250.0


def test_variances_come_sorted_by_how_much_meat_is_going():
    rows = variances(real={"A": 10.0, "B": 3.0}, theoretical={"A": 9.5, "B": 1.0})
    assert [r.ingredient for r in rows] == ["B", "A"]      # B se va 2 kg, A solo 0.5


# ====================================================== contra la base de datos
@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'d.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Bistró", "ana@b.com", "Ana",
                                           "clave-larga-1", language="es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@b.com", "Luis", "clave-larga-2")

        steak = Ingredient(restaurant_id=rest.id, name="Striploin steak", unit=Unit.KG,
                           consumption=ConsumptionMode.COUNT)
        s.add(steak); s.flush()
        item = IngredientItem(restaurant_id=rest.id, ingredient_id=steak.id,
                              name="Striploin steak 250g AUS")
        s.add(item); s.flush()
        lote = IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=steak.id,
                             serial="8017-01", parent_serial="8017", parent_lot="DXB20260910",
                             lot_code="TG-0001", expiry=HOY + timedelta(days=20),
                             received=AYER, qty=20.0, qty_remaining=20.0, unit_cost=26.0)
        s.add(lote); s.flush()

        plato = Recipe(restaurant_id=rest.id, code="entrecot", name="Entrecot a la brasa",
                       kind=RecipeKind.DISH, portions=1, sale_price=28.0, vat_pct=10)
        plato.lines.append(RecipeLine(ingredient_id=steak.id, qty=0.25))
        s.add(plato); s.flush()
        s.add(PosProduct(restaurant_id=rest.id, pos_name="ENTRECOT", pos_code="2201",
                         recipe_id=plato.id))
        s.flush()
        yield s, rest, ana, luis, steak, lote


def test_selling_does_not_deduct_meat_controlled_by_count(ctx):
    """Descontar por receta y luego por conteo sería contar el doble."""
    s, rest, ana, luis, steak, lote = ctx
    antes = costing.stock_on_hand(s, rest.id)[steak.id]
    result = costing.consume_sales(s, luis, [("ENTRECOT", 40)], on=HOY)
    assert costing.stock_on_hand(s, rest.id)[steak.id] == antes    # intacto
    assert result.theoretical[steak.id] == 10.0                    # pero queda anotado
    assert result.consumed == {}


def test_a_defrost_entry_needs_a_real_serial(ctx):
    s, rest, ana, luis, steak, lote = ctx
    with pytest.raises(DefrostError, match="No hay ninguna pieza"):
        defrost.intake(s, luis, "NO-EXISTE", pieces=10, total_kg=2.5, on=HOY)
    with pytest.raises(DefrostError):
        defrost.intake(s, luis, "8017-01", pieces=-1, total_kg=2.5, on=HOY)


def test_the_shift_close_deducts_what_was_really_used_from_that_piece(ctx):
    s, rest, ana, luis, steak, lote = ctx
    defrost.intake(s, luis, "8017-01", pieces=40, total_kg=10.0, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=6, total_kg=1.5, on=HOY, shift="noche")

    result = defrost.close(s, luis, on=HOY, shift="noche")

    fila = result.consumed[0]
    assert fila.serial == "8017-01" and fila.kg == 8.5 and fila.pieces == 34
    assert fila.avg_g_per_piece == 250.0
    s.refresh(lote)
    assert lote.qty_remaining == 11.5                      # 20 − 8.5, de esa pieza
    assert result.cost == round(8.5 * 26.0, 6)
    mv = (s.query(IngredientMovement).filter_by(source="defrost").one())
    assert mv.lot_id == lote.id and mv.qty == -8.5 and "8017-01" in mv.source_ref


def test_yesterdays_count_is_todays_opening(ctx):
    s, rest, ana, luis, steak, lote = ctx
    defrost.intake(s, luis, "8017-01", pieces=20, total_kg=5.0, on=AYER, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=8, total_kg=2.0, on=AYER, shift="noche")
    defrost.close(s, luis, on=AYER, shift="noche")

    defrost.intake(s, luis, "8017-01", pieces=12, total_kg=3.0, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=4, total_kg=1.0, on=HOY, shift="noche")
    result = defrost.close(s, luis, on=HOY, shift="noche")

    fila = result.consumed[0]
    assert fila.available_kg == 5.0                         # 2 de ayer + 3 de hoy
    assert fila.kg == 4.0 and fila.pieces == 16


def test_the_close_compares_the_real_weight_with_the_recipe(ctx):
    """El número que importa: cuánto pesa de verdad cada entrecot que sale."""
    s, rest, ana, luis, steak, lote = ctx
    costing.consume_sales(s, luis, [("ENTRECOT", 34)], on=HOY)     # teórico 8.5 kg
    defrost.intake(s, luis, "8017-01", pieces=40, total_kg=10.0, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=3, total_kg=0.65, on=HOY, shift="noche")

    result = defrost.close(s, luis, on=HOY, shift="noche")
    v = next(v for v in result.variances if v.ingredient == "Striploin steak")
    assert v.real_kg == 9.35 and v.theoretical_kg == 8.5
    assert v.units_sold == 34
    assert v.real_g_per_unit == 275.0 and v.theoretical_g_per_unit == 250.0
    assert v.overcut and v.gap_pct == 10.0
    assert any("Striploin steak" in a.message and "+10,0" in a.message
                   for a in result.alerts)


def test_cutting_to_the_gram_raises_nothing(ctx):
    s, rest, ana, luis, steak, lote = ctx
    costing.consume_sales(s, luis, [("ENTRECOT", 34)], on=HOY)
    defrost.intake(s, luis, "8017-01", pieces=40, total_kg=10.0, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=6, total_kg=1.5, on=HOY, shift="noche")
    result = defrost.close(s, luis, on=HOY, shift="noche")
    v = next(v for v in result.variances if v.ingredient == "Striploin steak")
    assert v.gap_kg == 0.0 and not v.overcut
    assert result.alerts == []


def test_a_missing_count_is_reported_not_guessed(ctx):
    s, rest, ana, luis, steak, lote = ctx
    defrost.intake(s, luis, "8017-01", pieces=40, total_kg=10.0, on=HOY, shift="noche")
    result = defrost.close(s, luis, on=HOY, shift="noche")
    assert result.missing_counts == ["8017-01"]
    assert result.cost == 0.0
    s.refresh(lote)
    assert lote.qty_remaining == 20.0                      # no se toca sin contar
    assert any("Sin recuento" in a.message for a in result.alerts)


def test_counting_more_than_there_was_raises_an_alert(ctx):
    s, rest, ana, luis, steak, lote = ctx
    defrost.intake(s, luis, "8017-01", pieces=10, total_kg=2.5, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=20, total_kg=5.0, on=HOY, shift="noche")
    result = defrost.close(s, luis, on=HOY, shift="noche")
    assert result.consumed[0].impossible
    assert any("Falta apuntar" in a.message for a in result.alerts)


def test_the_manager_hears_about_the_variance(ctx):
    s, rest, ana, luis, steak, lote = ctx
    from thegrill.web import service
    costing.consume_sales(s, luis, [("ENTRECOT", 30)], on=HOY)
    defrost.intake(s, luis, "8017-01", pieces=40, total_kg=12.0, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=5, total_kg=1.0, on=HOY, shift="noche")
    defrost.close(s, luis, on=HOY, shift="noche")
    assert service.unread_count(s, ana.id) >= 1


def test_you_still_know_which_primal_that_steak_came_from(ctx):
    """Con el serial en el descongelado, la venta llega hasta la pieza."""
    s, rest, ana, luis, steak, lote = ctx
    from thegrill.web import butchery
    defrost.intake(s, luis, "8017-01", pieces=40, total_kg=10.0, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=6, total_kg=1.5, on=HOY, shift="noche")
    defrost.close(s, luis, on=HOY, shift="noche")

    historia = butchery.trace(s, rest.id, "8017-01")
    assert historia["from_primal"] == "8017"
    assert historia["reception_lot"] == "DXB20260910"
    salidas = [m for m in historia["movements"] if m["kind"] == "SALE"]
    assert salidas and salidas[0]["source"] == "defrost"
    assert round(-salidas[0]["qty"], 4) == 8.5


def test_shifts_are_closed_one_by_one(ctx):
    s, rest, ana, luis, steak, lote = ctx
    defrost.intake(s, luis, "8017-01", pieces=20, total_kg=5.0, on=HOY, shift="dia")
    defrost.count(s, luis, "8017-01", pieces=4, total_kg=1.0, on=HOY, shift="dia")
    defrost.intake(s, luis, "8017-01", pieces=16, total_kg=4.0, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=2, total_kg=0.5, on=HOY, shift="noche")

    dia = defrost.close(s, luis, on=HOY, shift="dia")
    noche = defrost.close(s, luis, on=HOY, shift="noche")
    assert dia.consumed[0].kg == 4.0                        # 5 − 1
    assert noche.consumed[0].available_kg == 5.0            # 1 del día + 4 de la noche
    assert noche.consumed[0].kg == 4.5
    s.refresh(lote)
    assert lote.qty_remaining == round(20.0 - 8.5, 6)


def test_defrost_never_crosses_between_restaurants(ctx):
    s, rest, ana, luis, steak, lote = ctx
    otro, eva = auth.create_restaurant(s, "Otro", "eva@otro.com", "Eva", "clave-larga-9")
    with pytest.raises(DefrostError):
        defrost.intake(s, eva, "8017-01", pieces=5, total_kg=1.0, on=HOY)
    assert defrost.close(s, eva, on=HOY).consumed == []


def test_closing_a_shift_still_takes_the_meat_out_when_something_was_thawed_today(tmp_path):
    """Sacar del arcón y cerrar el turno el mismo día: la carne tiene que salir.

    Sacar del congelado parte el lote, y ese reparto deja dos apuntes —un MOVE
    y un IN— con el mismo `source="defrost"` que usa el cierre. El contador de
    «lo que este turno ya descontó» los estaba sumando, así que daba el turno
    por descontado sin estarlo: el cierre decía «tres kilos consumidos» y los
    tres kilos seguían en la cámara. La carne se comía y no salía de los
    libros, y el sobrante fantasma aparecía en el inventario de fin de mes
    como una merma que nadie sabe explicar.
    """
    from datetime import date, timedelta

    from thegrill.models import (ConsumptionMode, DefrostKind, Ingredient,
                                 IngredientItem, IngredientLot, Rotation, Unit, User)
    from thegrill.web import auth, defrost

    hoy = date.today()
    db.init_engine(f"sqlite:///{tmp_path/'df.db'}")
    db.create_all()
    with db.session_scope() as s:
        casa, ana = auth.create_restaurant(s, "Marina", "ana@m.com", "Ana",
                                           "clave-larga-1", language="es")
        corte = Ingredient(restaurant_id=casa.id, name="Entrecot", unit=Unit.KG,
                           rotation=Rotation.FEFO, consumption=ConsumptionMode.COUNT)
        s.add(corte); s.flush()
        art = IngredientItem(restaurant_id=casa.id, ingredient_id=corte.id, name="AUS")
        s.add(art); s.flush()
        s.add(IngredientLot(restaurant_id=casa.id, item_id=art.id, ingredient_id=corte.id,
                            serial="8017-01", lot_code="TG-1",
                            expiry=hoy + timedelta(days=30), received=hoy,
                            qty=10.0, qty_remaining=10.0, unit_cost=30.0,
                            frozen=True, pieces=20))
        s.flush()
        aid = ana.id

    with db.session_scope() as s:
        ana = s.get(User, aid)
        lote = s.query(IngredientLot).filter_by(serial="8017-01").one()
        hijo = defrost.thaw(s, ana, lote, 4.0, pieces=8, on=hoy)   # parte el lote
        nacido = hijo.serial
        defrost.record(s, ana, DefrostKind.INTAKE, nacido, 8, 4.0, on=hoy)
        defrost.record(s, ana, DefrostKind.COUNT, nacido, 2, 1.0, on=hoy)

    with db.session_scope() as s:
        cierre = defrost.close(s, s.get(User, aid), on=hoy)
        assert [(c.serial, c.kg) for c in cierre.consumed] == [(nacido, 3.0)]

    with db.session_scope() as s:
        queda = s.query(IngredientLot).filter_by(serial=nacido).one().qty_remaining
        assert queda == pytest.approx(1.0), (
            f"se consumieron 3 kg y en la cámara quedan {queda}: no salieron")
