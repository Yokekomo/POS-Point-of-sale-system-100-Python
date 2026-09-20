"""Maduración, congelador y venta a peso.

Lo que se comprueba aquí es una sola idea repetida: el agua se va y el dinero
no. Una pieza que madura pesa menos cada semana, pero costó lo que costó, así
que el kilo que queda vale más. Si eso no se cumple, el asador vende su
maduración a precio de carne fresca.
"""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.models import (AlertSeverity, CountPeriod, Primal, PrimalStatus,
                             PrimalWeighing, Storage, WeightSale)
from thegrill.web import aging, auth, inventory

HOY = date(2026, 9, 20)


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'a.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Asador", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@a.com", "Luis", "clave-larga-2")
        yield s, rest, ana, luis


def pieza(s, rest, serial="8017", kg=9.0, precio=30.0, sku="RIBEYE_AUS") -> Primal:
    p = Primal(restaurant_id=rest.id, serial=serial, sku=sku, weight_kg=kg,
               received_date=HOY, landed_usd_per_kg=precio, piece_cost_usd=kg * precio)
    s.add(p); s.flush(); return p


# ------------------------------------------------------------- el traslado
def test_a_piece_goes_into_the_aging_fridge_with_its_starting_weight(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest)

    movida = aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY)
    assert movida.was == Storage.CHILLED and movida.now == Storage.AGING

    p = s.query(Primal).one()
    assert p.storage == Storage.AGING
    assert p.storage_since == HOY
    assert p.aging_start_kg == 9.0          # contra este peso se mide todo lo demás
    assert p.aging_target_days == 45


def test_freezing_takes_its_own_use_by_date(ctx):
    s, rest, ana, _ = ctx
    p = pieza(s, rest)
    p.expiry_label = HOY + timedelta(days=20)
    s.flush()

    aging.move(s, ana, "8017", Storage.FROZEN, use_by=HOY + timedelta(days=180), on=HOY)
    assert s.query(Primal).one().frozen_use_by == HOY + timedelta(days=180)


def test_a_piece_cannot_be_moved_where_it_already_is(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest)
    aging.move(s, ana, "8017", Storage.AGING, on=HOY)
    with pytest.raises(aging.AgingError):
        aging.move(s, ana, "8017", Storage.AGING, on=HOY)


def test_leaving_the_aging_fridge_forgets_the_target_but_not_the_weighings(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest)
    aging.move(s, ana, "8017", Storage.AGING, target_days=30, on=HOY)
    aging.weigh(s, ana, "8017", 8.4, on=HOY + timedelta(days=21))
    aging.move(s, ana, "8017", Storage.CHILLED, on=HOY + timedelta(days=30))

    p = s.query(Primal).one()
    assert p.aging_target_days is None and p.aging_start_kg is None
    assert len(aging.history(s, rest.id, "8017")) == 1      # lo pesado no se borra


def test_the_move_is_signed(ctx):
    s, rest, ana, _ = ctx
    from thegrill.models import AuditLog
    pieza(s, rest)
    aging.move(s, ana, "8017", Storage.FROZEN, on=HOY, note="se acumuló")

    log = s.query(AuditLog).one()
    assert log.actor == "Ana" and log.key == "8017"
    assert "CHILLED → FROZEN" in log.detail and "se acumuló" in log.detail


# --------------------------------------------------------------- la pesada
def test_what_evaporates_raises_the_price_of_what_is_left(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=9.0, precio=30.0)          # 270 € la pieza
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY)

    r = aging.weigh(s, ana, "8017", 7.6, on=HOY + timedelta(days=45))
    assert r.loss_kg == 1.4
    assert r.total_loss_pct == pytest.approx(15.56, abs=0.01)
    assert r.cost_per_kg_before == 30.0
    assert r.cost_per_kg == pytest.approx(35.526316, abs=1e-6)   # 270 / 7,6
    assert r.cost_rise_pct == pytest.approx(18.42, abs=0.01)

    p = s.query(Primal).one()
    assert p.weight_kg == 7.6
    assert p.piece_cost_usd == 270.0             # el dinero no se evapora
    assert round(p.weight_kg * p.landed_usd_per_kg, 2) == 270.0


def test_every_weighing_is_written_down(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest)
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY)
    aging.weigh(s, ana, "8017", 8.6, on=HOY + timedelta(days=14))
    aging.weigh(s, ana, "8017", 8.1, on=HOY + timedelta(days=28))

    filas = aging.history(s, rest.id, "8017")
    assert [f.kg for f in filas] == [8.6, 8.1]
    assert [f.previous_kg for f in filas] == [9.0, 8.6]
    assert [f.days for f in filas] == [14, 28]
    assert all(f.storage == Storage.AGING for f in filas)
    assert s.query(PrimalWeighing).count() == 2


def test_a_piece_does_not_put_on_weight(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=9.0)
    aging.move(s, ana, "8017", Storage.AGING, on=HOY)
    with pytest.raises(aging.AgingError):
        aging.weigh(s, ana, "8017", 9.4, on=HOY + timedelta(days=7))
    assert s.query(Primal).one().weight_kg == 9.0


def test_losing_more_than_a_maturing_explains_raises_an_alert(ctx):
    s, rest, ana, luis = ctx
    pieza(s, rest, kg=10.0)
    aging.move(s, ana, "8017", Storage.AGING, target_days=60, on=HOY)

    tranquila = aging.weigh(s, ana, "8017", 9.4, on=HOY + timedelta(days=14))
    assert tranquila.alert is None               # un 6 % es lo normal

    fuerte = aging.weigh(s, ana, "8017", 6.5, on=HOY + timedelta(days=60))
    assert fuerte.alert is not None
    assert fuerte.alert.severity == AlertSeverity.CRITICAL      # 35 % ya no es maduración
    assert "8017" in fuerte.alert.message


def test_a_frozen_piece_is_weighed_without_the_aging_alarm(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=10.0)
    aging.move(s, ana, "8017", Storage.FROZEN, on=HOY)
    r = aging.weigh(s, ana, "8017", 6.0, on=HOY + timedelta(days=30))
    assert r.alert is None                       # congelada no madura: no hay nada que avisar
    assert r.cost_per_kg == pytest.approx(50.0)  # 300 € entre 6 kg


# ---------------------------------------------------------- venta a peso
def test_selling_by_weight_takes_the_grams_and_their_share_of_the_cost(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=9.0, precio=30.0)
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY)
    aging.weigh(s, ana, "8017", 7.6, on=HOY + timedelta(days=45))     # 35,53 €/kg

    venta = aging.sell_by_weight(s, ana, "8017", 420, price=52.0, dish="Chuleta madurada",
                                 on=HOY + timedelta(days=46))
    assert venta.cost == pytest.approx(14.921053, abs=1e-5)
    assert venta.margin == pytest.approx(37.08, abs=0.01)
    assert venta.food_cost_pct == pytest.approx(28.69, abs=0.01)
    assert venta.kg_left == 7.18

    p = s.query(Primal).one()
    assert p.weight_kg == 7.18
    # Lo vendido se lleva su parte: el kilo de lo que queda no se mueve.
    assert round(p.piece_cost_usd / p.weight_kg, 4) == pytest.approx(35.5263, abs=1e-3)
    assert p.status == PrimalStatus.IN_STOCK


def test_the_last_gram_closes_the_piece(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=1.2, precio=40.0)
    venta = aging.sell_by_weight(s, ana, "8017", 1200, price=90.0, on=HOY)

    assert venta.finished is True
    p = s.query(Primal).one()
    assert p.status == PrimalStatus.CUT and p.status_ref == "PESO"
    assert p.weight_kg == 0.0


def test_a_sale_cannot_leave_the_piece_in_negative(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=2.0)
    with pytest.raises(aging.AgingError):
        aging.sell_by_weight(s, ana, "8017", 2500, price=100.0, on=HOY)
    assert s.query(Primal).one().weight_kg == 2.0
    assert s.query(WeightSale).count() == 0


def test_the_weight_sales_are_kept_with_their_food_cost(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=6.0, precio=25.0)
    aging.sell_by_weight(s, ana, "8017", 380, price=44.0, dish="Lomo al corte", on=HOY)

    venta = s.query(WeightSale).one()
    assert venta.serial == "8017" and venta.dish == "Lomo al corte"
    assert venta.grams == 380 and venta.price == 44.0
    assert venta.cost == pytest.approx(9.5)
    assert aging.sales(s, rest.id, serial="8017")[0].id == venta.id


# ------------------------------------------------------------- la pizarra
def test_the_board_says_what_is_left_and_when_it_is_ready(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, serial="8017", kg=9.0)
    pieza(s, rest, serial="8018", kg=8.0, sku="STRIPLOIN_AUS")
    pieza(s, rest, serial="8019", kg=7.0)
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY - timedelta(days=45))
    aging.move(s, ana, "8018", Storage.AGING, target_days=60, on=HOY - timedelta(days=10))
    aging.move(s, ana, "8019", Storage.FROZEN, on=HOY - timedelta(days=3))

    filas = {r.serial: r for r in aging.board(s, rest.id, on=HOY)}
    assert set(filas) == {"8017", "8018", "8019"}        # la que está en cámara no sale aquí
    assert filas["8017"].ready is True and filas["8017"].days == 45
    assert filas["8018"].ready is False and filas["8018"].days_left == 50
    assert filas["8018"].ready_on == HOY + timedelta(days=50)
    assert filas["8019"].storage == Storage.FROZEN

    resumen = aging.summary(s, rest.id, on=HOY)
    assert resumen.aging_pieces == 2 and resumen.frozen_pieces == 1
    assert resumen.ready == ["8017"]
    assert resumen.aging_value == pytest.approx(9.0 * 30 + 8.0 * 30)


def test_the_board_leaves_out_what_is_no_longer_in_stock(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=1.0, precio=30.0)
    aging.move(s, ana, "8017", Storage.AGING, on=HOY)
    aging.sell_by_weight(s, ana, "8017", 1000, price=45.0, on=HOY)
    assert aging.board(s, rest.id, on=HOY) == []


# ------------------------------------------------- el inventario que pesa
def test_the_monthly_count_weighs_the_aging_pieces(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=9.0, precio=30.0)
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY - timedelta(days=45))

    count = inventory.open_count(s, ana, period=CountPeriod.MONTHLY, on=HOY)
    inventory.record(s, ana, count, "8017", 7.6)
    result = inventory.close_count(s, ana, count, lang="es")

    # La merma de maduración se apunta como pesada, no como carne que falta.
    assert result.aging_kg == 1.4
    assert [w.serial for w in result.weighings] == ["8017"]
    assert any(a.code == "count.aging" for a in result.alerts)
    assert not any(a.code == "count.shrink" for a in result.alerts)

    p = s.query(Primal).one()
    assert p.weight_kg == 7.6 and p.piece_cost_usd == 270.0
    assert s.query(PrimalWeighing).one().source == "count"


def test_meat_that_really_is_missing_still_raises_its_alarm(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, serial="8017", kg=9.0, precio=30.0)
    pieza(s, rest, serial="8020", kg=9.0, precio=30.0)     # esta no madura: está en cámara
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY - timedelta(days=45))

    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, ana, count, "8017", 7.6)           # agua
    inventory.record(s, ana, count, "8020", 7.0)           # carne que falta
    result = inventory.close_count(s, ana, count, lang="es")

    assert result.aging_kg == 1.4
    shrink = next(a for a in result.alerts if a.code == "count.shrink")
    assert "2 kg" in shrink.message          # 3,4 contados menos el agua de la que madura


def test_what_was_sold_at_the_block_is_not_counted_as_evaporated(ctx):
    """Vender no es perder: los gramos cobrados no son merma de maduración."""
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=9.4, precio=32.0)
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY - timedelta(days=46))
    aging.weigh(s, ana, "8017", 7.9, on=HOY - timedelta(days=1))      # 1,5 kg de agua
    aging.sell_by_weight(s, ana, "8017", 800, price=104.0, on=HOY)    # 0,8 kg cobrados

    fila = aging.board(s, rest.id, on=HOY)[0]
    assert fila.kg == 7.1
    assert fila.sold_kg == 0.8
    assert fila.loss_kg == 1.5                      # solo el agua
    assert fila.loss_pct == pytest.approx(15.96, abs=0.01)
    assert aging.summary(s, rest.id, on=HOY).lost_kg == 1.5


# ------------------------------- del primal madurado al POS, por su camino
def test_an_aged_piece_is_trimmed_in_the_butchery_and_sold_by_weight(ctx):
    """El camino entero: madurar, limpiar y vender por gramos desde la caja.

    La carne madurada no sale de la nevera al plato: se limpia primero, y esa
    limpieza la pagan los kilos que quedan. Lo que entra en cámara es un lote a
    peso, sin piezas, y lo que lo descuenta son los gramos del POS.
    """
    from thegrill.meat import service as meat
    from thegrill.models import Ingredient, IngredientLot
    from thegrill.web import costing

    s, rest, ana, _ = ctx
    pieza(s, rest, kg=9.0, precio=30.0)
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY - timedelta(days=45))
    aging.weigh(s, ana, "8017", 7.6, on=HOY)                 # 35,53 €/kg

    corte = meat.create_cut(s, ana, "Lomo madurado", sold_by_weight=True)
    articulo = meat.add_article(s, ana, corte, "Ribeye AUS")
    s.query(Primal).one().expiry_label = HOY + timedelta(days=30)
    s.flush()

    meat.post_butchery(s, ana, tg="TG-0001", serials=["8017"], before_kg=7.6,
                       rows=[meat.CutRow(name="Lomo madurado", item_id=articulo.id,
                                         pieces=0, grams=0, by_weight=True, kg=6.4)],
                       waste_kg=1.2, on=HOY)

    lote = s.query(IngredientLot).one()
    assert lote.pieces is None and lote.piece_weight_g is None   # no hay ración que fingir
    assert lote.qty_remaining == 6.4
    # Los 270 € de la pieza siguen ahí, ahora en 6,4 kg: la limpieza la pagan ellos.
    assert lote.unit_cost == pytest.approx(42.1875, abs=1e-3)

    plato = meat.add_dish(s, ana, "Lomo madurado al corte", corte.id, 300,
                          by_weight=True, price_per_kg=129.0, vat_pct=10.0,
                          pos_code="1401", pos_name="LOMO MADURADO")
    assert plato.by_weight and plato.price_per_kg == 129.0
    assert plato.sale_price == pytest.approx(38.7)      # la ración de referencia, para la carta

    venta = costing.consume_sales(s, ana, [("LOMO MADURADO", 1, 0.412)], on=HOY, lang="es")
    assert venta.weighed_kg == 0.412
    assert venta.cost == pytest.approx(17.381, abs=0.01)
    assert s.query(IngredientLot).one().qty_remaining == pytest.approx(5.988)
    assert not venta.missing_weight


def test_a_weight_dish_without_its_weight_says_so(ctx):
    """Si el POS no manda el peso se descuenta la ración de referencia, y se avisa."""
    from thegrill.meat import service as meat
    from thegrill.models import IngredientLot
    from thegrill.web import costing

    s, rest, ana, _ = ctx
    corte = meat.create_cut(s, ana, "Lomo madurado", sold_by_weight=True)
    articulo = meat.add_article(s, ana, corte, "Ribeye AUS")
    costing.receive(s, ana, articulo, 6.0, 40.0, HOY + timedelta(days=20),
                    lot_code="TG-9", on=HOY)
    meat.add_dish(s, ana, "Lomo al corte", corte.id, 300, by_weight=True,
                  price_per_kg=129.0, pos_name="LOMO")

    venta = costing.consume_sales(s, ana, [("LOMO", 1)], on=HOY, lang="es")
    assert venta.missing_weight == ["LOMO"]
    assert s.query(IngredientLot).one().qty_remaining == pytest.approx(5.7)   # los 300 g de referencia


def test_a_dish_by_weight_needs_its_price_per_kilo(ctx):
    from thegrill.meat import service as meat
    s, rest, ana, _ = ctx
    corte = meat.create_cut(s, ana, "Lomo madurado", sold_by_weight=True)
    with pytest.raises(meat.MeatError):
        meat.add_dish(s, ana, "Lomo al corte", corte.id, 300, by_weight=True)


def test_pieces_and_by_weight_come_out_of_the_same_butchery(ctx):
    """Dos opciones a la vez: parte en raciones y parte entera para cortar al vender."""
    from thegrill.meat import service as meat
    from thegrill.models import IngredientLot

    s, rest, ana, _ = ctx
    p = pieza(s, rest, kg=9.0, precio=30.0)
    p.expiry_label = HOY + timedelta(days=30)
    s.flush()
    raciones = meat.create_cut(s, ana, "Chuletón")
    entero = meat.create_cut(s, ana, "Lomo madurado", sold_by_weight=True)
    art_r = meat.add_article(s, ana, raciones, "Ribeye AUS")
    art_e = meat.add_article(s, ana, entero, "Ribeye AUS entero")

    meat.post_butchery(s, ana, tg="TG-0002", serials=["8017"], before_kg=9.0,
                       rows=[meat.CutRow(name="Chuletón", item_id=art_r.id,
                                         pieces=10, grams=400),
                             meat.CutRow(name="Lomo madurado", item_id=art_e.id,
                                         pieces=0, grams=0, by_weight=True, kg=3.6)],
                       waste_kg=1.4, on=HOY)

    lotes = {l.ingredient_id: l for l in s.query(IngredientLot)}
    assert lotes[raciones.id].pieces == 10
    assert lotes[raciones.id].piece_weight_g == pytest.approx(400)
    assert lotes[entero.id].pieces is None
    assert lotes[entero.id].qty_remaining == 3.6


# --------------------------------------------------------------- limpieza
def test_trimming_before_ageing_raises_the_price_of_what_is_left(ctx):
    """La grasa que se quita al entrar no se lleva el dinero de la pieza."""
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=10.0, precio=30.0)                 # 300 €

    limpia = aging.trim(s, ana, "8017", removed_kg=1.0, on=HOY)
    assert limpia.removed_kg == 1.0
    assert limpia.removed_pct == 10.0
    assert limpia.cost_per_kg_before == 30.0
    assert limpia.cost_per_kg == pytest.approx(33.333333, abs=1e-5)   # 300 / 9
    assert limpia.kept_kg == 0.0

    p = s.query(Primal).one()
    assert p.weight_kg == 9.0 and p.piece_cost_usd == 300.0
    pesada = s.query(PrimalWeighing).one()
    assert pesada.kind.value == "TRIM" and pesada.loss_kg == 1.0


def test_the_crust_taken_off_after_ageing_is_not_counted_as_water(ctx):
    """A los cuarenta y cinco días la costra es mucha, y no es evaporación."""
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=9.0, precio=30.0)
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY - timedelta(days=45))
    aging.weigh(s, ana, "8017", 7.6, on=HOY)             # 1,4 kg de agua
    aging.trim(s, ana, "8017", new_kg=6.4, on=HOY)       # 1,2 kg de costra

    fila = aging.board(s, rest.id, on=HOY)[0]
    assert fila.kg == 6.4
    assert fila.loss_kg == 1.4          # el agua, sola
    assert fila.trim_kg == 1.2          # la costra, aparte
    assert fila.cost_per_kg == pytest.approx(42.1875, abs=1e-3)    # 270 / 6,4

    resumen = aging.summary(s, rest.id, on=HOY)
    assert resumen.lost_kg == 1.4 and resumen.trimmed_kg == 1.2


def test_a_trim_leaves_something_to_reuse_and_something_to_throw(ctx):
    """De una limpieza salen siempre las dos cosas, y no valen lo mismo."""
    from thegrill.meat import service as meat
    from thegrill.models import IngredientLot

    s, rest, ana, _ = ctx
    p = pieza(s, rest, kg=10.0, precio=30.0)
    p.expiry_label = HOY + timedelta(days=20)
    s.flush()
    recortes = meat.create_cut(s, ana, "Recortes de vacuno")
    grasa = meat.create_cut(s, ana, "Grasa para fondo")
    art_r = meat.add_article(s, ana, recortes, "Recortes AUS")
    art_g = meat.add_article(s, ana, grasa, "Grasa AUS")

    limpia = aging.trim(s, ana, "8017", removed_kg=1.6, on=HOY,
                        parts=[aging.TrimPart(item_id=art_r.id, kg=0.6, value_index=0.25),
                               aging.TrimPart(item_id=art_g.id, kg=0.3, value_index=0.1)])
    assert limpia.kept_kg == 0.9
    assert limpia.waste_kg == pytest.approx(0.7)         # lo que no se reparte, se tira
    assert limpia.waste_pct == pytest.approx(43.8, abs=0.1)

    lotes = {l.ingredient_id: l for l in s.query(IngredientLot)}
    assert lotes[recortes.id].qty_remaining == 0.6
    assert lotes[recortes.id].unit_cost == pytest.approx(7.5)    # 30 × 0,25
    assert lotes[grasa.id].unit_cost == pytest.approx(3.0)       # 30 × 0,10
    assert all(l.parent_serial == "8017" for l in lotes.values())
    assert limpia.trim_serial == ", ".join(sorted(l.serial for l in lotes.values()))

    # Lo aprovechado se lleva 5,40 €; lo tirado no se lleva nada y lo paga la pieza.
    assert limpia.kept_cost == pytest.approx(5.4)
    p = s.query(Primal).one()
    assert p.piece_cost_usd == pytest.approx(294.6)
    assert p.weight_kg == 8.4
    assert p.landed_usd_per_kg == pytest.approx(35.071429, abs=1e-5)


def test_what_is_kept_and_what_is_thrown_stays_written(ctx):
    from thegrill.meat import service as meat
    s, rest, ana, _ = ctx
    p = pieza(s, rest, kg=10.0, precio=30.0)
    p.expiry_label = HOY + timedelta(days=20)
    s.flush()
    recortes = meat.create_cut(s, ana, "Recortes")
    articulo = meat.add_article(s, ana, recortes, "Recortes AUS")
    aging.trim(s, ana, "8017", removed_kg=1.2, on=HOY,
               parts=[aging.TrimPart(item_id=articulo.id, kg=0.5)])

    fila = s.query(PrimalWeighing).one()
    assert fila.loss_kg == 1.2 and fila.kept_kg == 0.5 and fila.waste_kg == 0.7
    assert fila.trim_serial


def test_a_trim_cannot_keep_more_than_was_cut(ctx):
    from thegrill.meat import service as meat
    s, rest, ana, _ = ctx
    p = pieza(s, rest, kg=10.0, precio=30.0)
    p.expiry_label = HOY + timedelta(days=20)
    s.flush()
    recortes = meat.create_cut(s, ana, "Recortes")
    articulo = meat.add_article(s, ana, recortes, "Recortes AUS")
    with pytest.raises(aging.AgingError):
        aging.trim(s, ana, "8017", removed_kg=1.0, on=HOY,
                   parts=[aging.TrimPart(item_id=articulo.id, kg=1.4)])
    assert s.query(Primal).one().weight_kg == 10.0


def test_the_trim_has_to_add_up(ctx):
    """Lo quitado es lo guardado más lo tirado. Si no cuadra, no se apunta."""
    from thegrill.meat import service as meat
    s, rest, ana, _ = ctx
    p = pieza(s, rest, kg=10.0, precio=30.0)
    p.expiry_label = HOY + timedelta(days=20)
    s.flush()
    recortes = meat.create_cut(s, ana, "Recortes")
    articulo = meat.add_article(s, ana, recortes, "Recortes AUS")
    with pytest.raises(aging.AgingError):
        aging.trim(s, ana, "8017", removed_kg=1.6, waste_kg=0.2, on=HOY,
                   parts=[aging.TrimPart(item_id=articulo.id, kg=0.6)])
    assert s.query(Primal).one().weight_kg == 10.0


def test_a_trim_cannot_eat_the_whole_piece(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=4.0)
    with pytest.raises(aging.AgingError):
        aging.trim(s, ana, "8017", removed_kg=4.0, on=HOY)
    with pytest.raises(aging.AgingError):
        aging.trim(s, ana, "8017", on=HOY)               # ni cuánto se quita ni cuánto queda
    assert s.query(Primal).one().weight_kg == 4.0


def test_kept_trim_needs_a_use_by_date(ctx):
    """Una caducidad no se inventa: sin fecha en la pieza, no entra en cámara."""
    from thegrill.meat import service as meat
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=10.0)
    recortes = meat.create_cut(s, ana, "Recortes")
    articulo = meat.add_article(s, ana, recortes, "Recortes AUS")
    with pytest.raises(aging.AgingError):
        aging.trim(s, ana, "8017", removed_kg=1.0, on=HOY,
                   parts=[aging.TrimPart(item_id=articulo.id, kg=1.0)])


def test_trimming_in_the_middle_does_not_inflate_the_water(ctx):
    """La costra no evaporó: la cortó alguien, y va en su propia columna."""
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=10.0, precio=30.0)
    aging.move(s, ana, "8017", Storage.AGING, target_days=60, on=HOY - timedelta(days=30))
    aging.weigh(s, ana, "8017", 9.3, on=HOY - timedelta(days=1))    # 0,7 de agua
    aging.trim(s, ana, "8017", removed_kg=0.8, on=HOY)              # 0,8 de limpieza

    fila = aging.board(s, rest.id, on=HOY)[0]
    assert fila.start_kg == 10.0         # el peso con el que entró a madurar no se toca
    assert fila.kg == 8.5
    assert fila.loss_kg == 0.7 and fila.trim_kg == 0.8
    assert fila.loss_pct == 7.0          # el agua, contra los diez kilos que entraron


def test_the_yield_says_what_is_left_of_what_came_in(ctx):
    """De nueve kilos que entraron, cuánto se puede vender. Eso decide los días."""
    from thegrill.meat import service as meat
    s, rest, ana, _ = ctx
    meat.receive_primals(s, ana, "DXB1",
                         [meat.PrimalRow(serial="8017", sku="Ribeye AUS", kg=9.0,
                                         price_kg=30.0, use_by=HOY + timedelta(days=60))],
                         received=HOY - timedelta(days=46))
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY - timedelta(days=45))
    aging.weigh(s, ana, "8017", 7.6, on=HOY)             # agua
    aging.trim(s, ana, "8017", new_kg=6.4, on=HOY)       # costra

    fila = aging.board(s, rest.id, on=HOY)[0]
    assert fila.received_kg == 9.0
    assert fila.yield_pct == pytest.approx(71.1, abs=0.1)

    # Lo vendido cuenta como aprovechado, no como pérdida.
    aging.sell_by_weight(s, ana, "8017", 400, price=52.0, on=HOY)
    fila = aging.board(s, rest.id, on=HOY)[0]
    assert fila.kg == 6.0 and fila.sold_kg == 0.4
    assert fila.yield_pct == pytest.approx(71.1, abs=0.1)


def test_the_board_counts_what_was_reused_apart_from_what_was_thrown(ctx):
    """En la pizarra, la limpieza se ve partida: lo que volvió y lo que se fue."""
    from thegrill.meat import service as meat
    s, rest, ana, _ = ctx
    p = pieza(s, rest, kg=10.0, precio=30.0)
    p.expiry_label = HOY + timedelta(days=30)
    s.flush()
    recortes = meat.create_cut(s, ana, "Recortes")
    articulo = meat.add_article(s, ana, recortes, "Recortes AUS")
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY - timedelta(days=45))
    aging.weigh(s, ana, "8017", 8.7, on=HOY - timedelta(days=1))     # 1,3 de agua
    aging.trim(s, ana, "8017", removed_kg=1.5, on=HOY,               # 0,5 vuelven, 1,0 fuera
               parts=[aging.TrimPart(item_id=articulo.id, kg=0.5)])

    fila = aging.board(s, rest.id, on=HOY)[0]
    assert fila.loss_kg == 1.3
    assert fila.trim_kg == 1.5
    assert fila.trim_kept_kg == 0.5 and fila.trim_waste_kg == 1.0

    resumen = aging.summary(s, rest.id, on=HOY)
    assert resumen.trimmed_kg == 1.5
    assert resumen.kept_kg == 0.5 and resumen.thrown_kg == 1.0


# --------------------------------------------- ¿compensan los días de más?
def test_the_yield_by_days_says_where_the_extra_days_stop_paying(ctx):
    """Cuarenta y cinco días o sesenta: la pregunta se contesta con las piezas."""
    s, rest, ana, _ = ctx

    def madurada(serial, dias, agua, costra, kg=10.0):
        p = pieza(s, rest, serial=serial, kg=kg, precio=30.0)
        p.expiry_label = HOY + timedelta(days=90)
        s.flush()
        aging.move(s, ana, serial, Storage.AGING, target_days=dias,
                   on=HOY - timedelta(days=dias))
        aging.weigh(s, ana, serial, kg - agua, on=HOY)
        aging.trim(s, ana, serial, removed_kg=costra, on=HOY)

    for n in range(3):                       # tres piezas de cuarenta y cinco días
        madurada(f"90{n}", 45, 1.5, 1.0)
    for n in range(3):                       # y tres de sesenta, que pierden más
        madurada(f"91{n}", 60, 2.0, 1.8)
    madurada("9200", 30, 0.8, 0.5)           # una sola de treinta: no hace media

    tramos = {b.days: b for b in aging.yield_by_days(s, rest.id)}
    assert set(tramos) == {45, 60}           # con una pieza no se dice nada
    assert tramos[45].pieces == 3 and tramos[60].pieces == 3
    assert tramos[45].water_pct == 15.0 and tramos[45].trim_pct == 10.0
    assert tramos[45].yield_pct == 75.0
    assert tramos[60].yield_pct == 62.0      # quince días más, trece puntos menos
    assert tramos[60].yield_pct < tramos[45].yield_pct


def test_without_enough_pieces_it_says_nothing(ctx):
    s, rest, ana, _ = ctx
    pieza(s, rest, kg=9.0)
    aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY - timedelta(days=45))
    aging.weigh(s, ana, "8017", 7.6, on=HOY)
    assert aging.yield_by_days(s, rest.id) == []
