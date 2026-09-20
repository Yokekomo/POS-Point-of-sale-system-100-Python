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
