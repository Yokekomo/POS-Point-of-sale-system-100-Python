"""Cuánta carne queda al acabar el día, y de qué hay que avisar."""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.models import (ConsumptionMode, Ingredient, IngredientItem, IngredientLot,
                             Primal, PrimalPar, PrimalStatus, Unit)
from thegrill.web import auth, butchery, defrost, service

HOY = date(2026, 9, 20)


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'m.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Bistró", "ana@b.com", "Ana",
                                           "clave-larga-1", language="es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@b.com", "Luis", "clave-larga-2")
        yield s, rest, ana, luis


def madre(s, rest, name, minimo=None, mode=ConsumptionMode.COUNT) -> Ingredient:
    i = Ingredient(restaurant_id=rest.id, name=name, unit=Unit.KG,
                   consumption=mode, min_stock=minimo)
    s.add(i); s.flush(); return i


def corte(s, rest, ing, serial, kg, dias=20, coste=26.0) -> IngredientLot:
    item = IngredientItem(restaurant_id=rest.id, ingredient_id=ing.id, name=f"Art {serial}")
    s.add(item); s.flush()
    lot = IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=ing.id,
                        serial=serial, parent_serial=serial.split("-")[0], lot_code="TG-1",
                        expiry=HOY + timedelta(days=dias), received=HOY, qty=kg,
                        qty_remaining=kg, unit_cost=coste)
    s.add(lot); s.flush(); return lot


def primal(s, rest, serial, sku="STRIPLOIN_AUS", kg=10.0) -> Primal:
    p = Primal(restaurant_id=rest.id, serial=serial, sku=sku, weight_kg=kg,
               landed_usd_per_kg=20.0, piece_cost_usd=kg * 20.0)
    s.add(p); s.flush(); return p


# -------------------------------------------------------------- la foto
def test_it_counts_the_cuts_left_and_their_open_serials(ctx):
    s, rest, ana, luis = ctx
    steak = madre(s, rest, "Striploin steak")
    corte(s, rest, steak, "8017-01", 6.0)
    corte(s, rest, steak, "8018-01", 4.5)
    agotado = corte(s, rest, steak, "8019-01", 3.0)
    agotado.qty_remaining = 0.0
    s.flush()

    estado = butchery.status(s, rest.id, on=HOY)
    cut = estado.cuts[0]
    assert cut.name == "Striploin steak"
    assert cut.kg == 10.5 and cut.open_serials == 2      # el agotado ya no cuenta
    assert estado.total_cut_kg == 10.5


def test_it_counts_the_primals_still_whole(ctx):
    s, rest, ana, luis = ctx
    primal(s, rest, "8017", "STRIPLOIN_AUS", 9.5)
    primal(s, rest, "8018", "STRIPLOIN_AUS", 10.2)
    cortado = primal(s, rest, "8019", "STRIPLOIN_AUS", 10.0)
    cortado.status = PrimalStatus.CUT
    primal(s, rest, "9001", "CUBE_ROLL_AUS", 8.0)
    s.flush()

    estado = butchery.status(s, rest.id, on=HOY)
    por_sku = {p.sku: p for p in estado.primals}
    assert por_sku["STRIPLOIN_AUS"].pieces == 2          # el cortado no está
    assert por_sku["STRIPLOIN_AUS"].kg == 19.7
    assert por_sku["CUBE_ROLL_AUS"].pieces == 1
    assert estado.total_primals == 3


def test_it_says_how_much_is_thawed_right_now(ctx):
    s, rest, ana, luis = ctx
    steak = madre(s, rest, "Striploin steak")
    corte(s, rest, steak, "8017-01", 20.0)
    defrost.intake(s, luis, "8017-01", pieces=40, total_kg=10.0, on=HOY, shift="noche")
    defrost.count(s, luis, "8017-01", pieces=6, total_kg=1.5, on=HOY, shift="noche")

    cut = butchery.status(s, rest.id, on=HOY).cuts[0]
    assert cut.thawed_pieces == 6 and cut.thawed_kg == 1.5


def test_only_cuts_from_a_butchery_show_up_here(ctx):
    """Un saco de harina no es carne: sin serial, no entra en esta foto."""
    s, rest, ana, luis = ctx
    harina = madre(s, rest, "Harina", mode=ConsumptionMode.RECIPE)
    item = IngredientItem(restaurant_id=rest.id, ingredient_id=harina.id, name="Harina 25kg")
    s.add(item); s.flush()
    s.add(IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=harina.id,
                        expiry=HOY + timedelta(days=200), qty=25, qty_remaining=25,
                        unit_cost=1.2))
    s.flush()
    assert butchery.status(s, rest.id, on=HOY).cuts == []


# ------------------------------------------------------------- las alertas
def test_a_cut_below_its_minimum_is_flagged(ctx):
    s, rest, ana, luis = ctx
    steak = madre(s, rest, "Striploin steak", minimo=8.0)
    corte(s, rest, steak, "8017-01", 5.0)
    result = butchery.close_day(s, luis, on=HOY)
    assert [c.name for c in result.cuts_below] == ["Striploin steak"]
    assert any("por debajo del mínimo" in a.message and "Striploin" in a.message
               for a in result.alerts)


def test_a_cut_above_its_minimum_says_nothing(ctx):
    s, rest, ana, luis = ctx
    steak = madre(s, rest, "Striploin steak", minimo=4.0)
    corte(s, rest, steak, "8017-01", 9.0)
    result = butchery.close_day(s, luis, on=HOY)
    assert result.cuts_below == [] and result.alerts == []


def test_an_ingredient_without_a_minimum_is_never_flagged(ctx):
    s, rest, ana, luis = ctx
    steak = madre(s, rest, "Striploin steak", minimo=None)
    corte(s, rest, steak, "8017-01", 0.2)
    assert butchery.close_day(s, luis, on=HOY).cuts_below == []


def test_running_out_of_primals_says_it_is_time_to_order(ctx):
    s, rest, ana, luis = ctx
    s.add(PrimalPar(restaurant_id=rest.id, sku="STRIPLOIN_AUS", min_pieces=4))
    primal(s, rest, "8017", "STRIPLOIN_AUS")
    primal(s, rest, "8018", "STRIPLOIN_AUS")
    s.flush()
    result = butchery.close_day(s, luis, on=HOY)
    assert [p.sku for p in result.primals_below] == ["STRIPLOIN_AUS"]
    assert any("Hay que pedir" in a.message for a in result.alerts)


def test_a_sku_with_none_left_still_appears_if_it_has_a_minimum(ctx):
    """Cero primales es justo el caso que hay que gritar, no el que se oculta."""
    s, rest, ana, luis = ctx
    s.add(PrimalPar(restaurant_id=rest.id, sku="TENDERLOIN_AUS", min_pieces=2))
    s.flush()
    result = butchery.close_day(s, luis, on=HOY)
    fila = next(p for p in result.primals if p.sku == "TENDERLOIN_AUS")
    assert fila.pieces == 0 and fila.below_par


def test_cuts_about_to_expire_are_listed_first(ctx):
    s, rest, ana, luis = ctx
    lento = madre(s, rest, "Striploin steak")
    urgente = madre(s, rest, "Tiras")
    corte(s, rest, lento, "8017-01", 5.0, dias=30)
    corte(s, rest, urgente, "8018-01", 2.0, dias=1)
    result = butchery.close_day(s, luis, on=HOY)
    assert [c.name for c in result.expiring] == ["Tiras"]
    assert any("caducan en 1" in a.message for a in result.alerts)


def test_meat_already_expired_is_critical(ctx):
    s, rest, ana, luis = ctx
    steak = madre(s, rest, "Striploin steak")
    corte(s, rest, steak, "8017-01", 5.0, dias=-1)
    result = butchery.close_day(s, luis, on=HOY)
    caducado = result.expiring[0]
    assert caducado.days_to_expiry == -1
    assert any(a.severity.value == "CRITICAL" for a in result.alerts)


def test_the_manager_is_told_at_the_close(ctx):
    s, rest, ana, luis = ctx
    steak = madre(s, rest, "Striploin steak", minimo=8.0)
    corte(s, rest, steak, "8017-01", 1.0, dias=1)
    butchery.close_day(s, luis, on=HOY)
    assert service.unread_count(s, ana.id) >= 2       # poco stock y caduca pronto
    assert service.unread_count(s, luis.id) == 0


def test_a_quiet_day_wakes_nobody(ctx):
    s, rest, ana, luis = ctx
    steak = madre(s, rest, "Striploin steak", minimo=2.0)
    corte(s, rest, steak, "8017-01", 9.0, dias=30)
    primal(s, rest, "8017")
    result = butchery.close_day(s, luis, on=HOY)
    assert result.alerts == []
    assert service.unread_count(s, ana.id) == 0


def test_meat_stock_never_crosses_between_restaurants(ctx):
    s, rest, ana, luis = ctx
    steak = madre(s, rest, "Striploin steak", minimo=8.0)
    corte(s, rest, steak, "8017-01", 1.0)
    primal(s, rest, "8017")
    otro, eva = auth.create_restaurant(s, "Otro", "eva@otro.com", "Eva", "clave-larga-9")
    vecino = butchery.status(s, otro.id, on=HOY)
    assert vecino.cuts == [] and vecino.primals == []
    assert butchery.close_day(s, eva, on=HOY).alerts == []
