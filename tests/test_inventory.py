"""Inventario de carne: cuadrar lo que dice el sistema con lo que hay."""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.engine.inventory import (MATCH, NOT_FOUND, OVER, SHORT, UNCOUNTED, UNKNOWN,
                                       Counted, Expected, reconcile)
from thegrill.models import (CountPeriod, CountStatus, Ingredient, IngredientItem,
                             IngredientLot, IngredientMovement, MovementKind, Primal,
                             PrimalStatus, Unit)
from thegrill.web import auth, inventory, service
from thegrill.web.inventory import InventoryError

HOY = date(2026, 9, 21)


# =================================================== el motor, por su cuenta
def E(serial, kg, kind="CUT", cost=30.0):
    return Expected(serial, f"Corte {serial}", kind, kg, cost)


def test_what_matches_matches():
    r = reconcile([E("8017-01", 5.0)], [Counted("8017-01", 5.0)])
    assert r.lines[0].outcome == MATCH and r.lines[0].gap_kg == 0.0
    assert r.complete and r.accuracy_pct == 100.0


def test_the_scale_wobble_is_not_a_difference():
    r = reconcile([E("8017-01", 5.0)], [Counted("8017-01", 5.003)])
    assert r.lines[0].outcome == MATCH        # tres gramos es la báscula


def test_less_than_expected_is_meat_that_walked():
    r = reconcile([E("8017-01", 5.0, cost=30.0)], [Counted("8017-01", 4.2)])
    line = r.lines[0]
    assert line.outcome == SHORT and line.gap_kg == -0.8
    assert line.gap_value == -24.0
    assert r.shrink_kg == 0.8 and r.shrink_value == 24.0


def test_more_than_expected_means_a_withdrawal_was_overstated():
    r = reconcile([E("8017-01", 5.0)], [Counted("8017-01", 5.6)])
    assert r.lines[0].outcome == OVER and r.lines[0].gap_kg == 0.6
    assert r.shrink_kg == 0.0                 # sobrar no es merma


def test_counted_at_zero_means_it_is_not_there():
    r = reconcile([E("8017-01", 5.0)], [Counted("8017-01", 0.0)])
    assert r.lines[0].outcome == NOT_FOUND


def test_a_piece_nobody_counted_is_pending_not_zero():
    r = reconcile([E("8017-01", 5.0), E("8018-01", 3.0)], [Counted("8017-01", 5.0)])
    pendiente = r.of(UNCOUNTED)[0]
    assert pendiente.serial == "8018-01" and pendiente.gap_kg == 0.0
    assert not r.complete and not pendiente.adjusts   # sin contar, no se ajusta
    assert r.accuracy_pct == 100.0                    # de lo contado, todo cuadraba


def test_something_in_the_fridge_the_system_never_had():
    r = reconcile([], [Counted("9999-01", 2.0)])
    assert r.of(UNKNOWN)[0].serial == "9999-01"


def test_the_worst_lines_come_first_by_money():
    r = reconcile([E("A", 10.0, cost=40.0), E("B", 10.0, cost=5.0)],
                  [Counted("A", 9.5), Counted("B", 8.0)])
    assert [l.serial for l in r.worst()] == ["A", "B"]   # A pierde 20, B solo 10


# ====================================================== contra la base de datos
@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'i.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Bistró", "ana@b.com", "Ana",
                                           "clave-larga-1", language="es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@b.com", "Luis", "clave-larga-2")
        yield s, rest, ana, luis


def corte(s, rest, nombre, serial, kg, coste=30.0) -> IngredientLot:
    ing = (s.query(Ingredient).filter_by(restaurant_id=rest.id, name=nombre).first()
           or Ingredient(restaurant_id=rest.id, name=nombre, unit=Unit.KG))
    if ing.id is None:
        s.add(ing); s.flush()
    item = IngredientItem(restaurant_id=rest.id, ingredient_id=ing.id, name=f"Art {serial}")
    s.add(item); s.flush()
    lot = IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=ing.id,
                        serial=serial, lot_code="TG-1", expiry=HOY + timedelta(days=20),
                        received=HOY, qty=kg, qty_remaining=kg, unit_cost=coste)
    s.add(lot); s.flush(); return lot


def primal(s, rest, serial, sku="STRIPLOIN_AUS", kg=10.0) -> Primal:
    p = Primal(restaurant_id=rest.id, serial=serial, sku=sku, weight_kg=kg,
               landed_usd_per_kg=22.0, piece_cost_usd=kg * 22.0)
    s.add(p); s.flush(); return p


def test_opening_a_count_lists_everything_there_is_to_count(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Striploin steak", "8017-01", 5.0)
    corte(s, rest, "Beef for burger", "8017-02", 2.4)
    primal(s, rest, "9001")
    vacio = corte(s, rest, "Tiras", "8018-01", 3.0)
    vacio.qty_remaining = 0.0
    s.flush()

    count = inventory.open_count(s, ana, CountPeriod.WEEKLY, on=HOY)
    assert count.status == CountStatus.OPEN and count.period == CountPeriod.WEEKLY
    seriales = {l.serial for l in count.lines}
    assert seriales == {"8017-01", "8017-02", "9001"}    # el lote a cero no se cuenta
    primal_line = next(l for l in count.lines if l.serial == "9001")
    assert primal_line.kind.value == "PRIMAL" and primal_line.label == "STRIPLOIN_AUS"


def test_only_one_count_can_be_open_at_a_time(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    inventory.open_count(s, ana, on=HOY)
    with pytest.raises(InventoryError, match="Ya hay un inventario abierto"):
        inventory.open_count(s, ana, on=HOY)


def test_counting_re_anchors_the_stock_and_leaves_its_movement(ctx):
    s, rest, ana, luis = ctx
    lot = corte(s, rest, "Striploin steak", "8017-01", 5.0, coste=30.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=4.2)

    result = inventory.close_count(s, ana, count)

    s.refresh(lot)
    assert lot.qty_remaining == 4.2                      # el conteo manda
    ajuste = s.query(IngredientMovement).filter_by(kind=MovementKind.ADJUST).one()
    assert ajuste.qty == -0.8 and ajuste.cost == -24.0
    assert ajuste.source == "count" and "8017-01" in ajuste.source_ref
    assert result.summary.shrink_kg == 0.8 and result.summary.shrink_value == 24.0
    assert count.status == CountStatus.CLOSED and count.closed_by == ana.id


def test_a_piece_left_uncounted_is_never_touched(ctx):
    s, rest, ana, luis = ctx
    contado = corte(s, rest, "Steak", "8017-01", 5.0)
    olvidado = corte(s, rest, "Tiras", "8018-01", 3.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=5.0)

    result = inventory.close_count(s, ana, count)

    s.refresh(olvidado)
    assert olvidado.qty_remaining == 3.0                 # intacto
    assert not result.summary.complete
    assert any("parcial" in a.message for a in result.alerts)
    assert any("8018-01" in a.message for a in result.alerts)


def test_a_primal_that_does_not_turn_up_is_suspect_not_butchered(ctx):
    """Regla vieja y buena: un primal solo pasa a cortado con su despiece."""
    s, rest, ana, luis = ctx
    p = primal(s, rest, "9001")
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "9001", kg=0.0)

    result = inventory.close_count(s, ana, count)

    s.refresh(p)
    assert p.status == PrimalStatus.IN_STOCK             # no se infiere que se cortó
    assert p.suspect_phantom is True
    assert result.phantoms == ["9001"]
    assert any("sospechosos" in a.message for a in result.alerts)


def test_a_primal_that_weighs_less_than_recorded_is_corrected(ctx):
    s, rest, ana, luis = ctx
    p = primal(s, rest, "9001", kg=10.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "9001", kg=9.4)
    inventory.close_count(s, ana, count)
    s.refresh(p)
    assert p.weight_kg == 9.4 and not p.suspect_phantom


def test_a_piece_found_that_the_system_did_not_have_is_named(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=5.0)
    inventory.record(s, luis, count, "7777-09", kg=1.8)   # apareció en cámara

    result = inventory.close_count(s, ana, count)
    desconocida = result.summary.of(UNKNOWN)[0]
    assert desconocida.serial == "7777-09"
    assert any("no tenía" in a.message for a in result.alerts)
    assert s.query(IngredientLot).filter_by(serial="7777-09").first() is None   # no se inventa


def test_a_count_that_reconciles_wakes_nobody(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    primal(s, rest, "9001", kg=10.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=5.0)
    inventory.record(s, luis, count, "9001", kg=10.0)

    result = inventory.close_count(s, luis, count)
    assert result.summary.complete and result.summary.accuracy_pct == 100.0
    assert result.alerts == []
    assert service.unread_count(s, ana.id) == 0


def test_the_manager_hears_about_the_shrink(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Striploin steak", "8017-01", 5.0, coste=30.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=3.0)
    result = inventory.close_count(s, luis, count)
    assert any(a.severity.value == "CRITICAL" for a in result.alerts)
    assert service.unread_count(s, ana.id) >= 1


def test_the_expected_is_re_read_when_closing_not_when_opening(ctx):
    """Si se vende mientras se cuenta, el ajuste cuadra contra el estado de ahora."""
    s, rest, ana, luis = ctx
    lot = corte(s, rest, "Steak", "8017-01", 5.0)
    count = inventory.open_count(s, ana, on=HOY)
    lot.qty_remaining = 4.0                     # una venta durante el conteo
    s.flush()
    inventory.record(s, luis, count, "8017-01", kg=4.0)

    result = inventory.close_count(s, ana, count)
    assert result.summary.lines[0].outcome == MATCH
    assert result.summary.shrink_kg == 0.0


def test_a_closed_count_cannot_be_touched_again(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=5.0)
    inventory.close_count(s, ana, count)
    with pytest.raises(InventoryError, match="ya estaba cerrado"):
        inventory.close_count(s, ana, count)
    with pytest.raises(InventoryError, match="ya está cerrado"):
        inventory.record(s, luis, count, "8017-01", kg=9.0)


def test_a_negative_weight_is_refused(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    count = inventory.open_count(s, ana, on=HOY)
    with pytest.raises(InventoryError):
        inventory.record(s, luis, count, "8017-01", kg=-1.0)


def test_the_only_obligation_is_one_complete_count_a_month(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    assert not inventory.monthly_status(s, rest.id, on=HOY).done

    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=5.0)
    inventory.close_count(s, ana, count)

    hecho = inventory.monthly_status(s, rest.id, on=HOY)
    assert hecho.done and hecho.last_date == HOY and not hecho.due_soon
    # el mes que viene vuelve a tocar
    assert not inventory.monthly_status(s, rest.id, on=HOY + timedelta(days=30)).done
    assert inventory.last_closed(s, rest.id).id == count.id


def test_a_partial_count_does_not_satisfy_the_month(ctx):
    """Quedaron piezas sin mirar: no vale como inventario del mes."""
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    corte(s, rest, "Tiras", "8018-01", 3.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=5.0)
    inventory.close_count(s, ana, count)
    assert count.complete is False
    assert not inventory.monthly_status(s, rest.id, on=HOY).done


def test_it_warns_when_the_month_is_running_out(ctx):
    s, rest, ana, luis = ctx
    fin_de_mes = date(2026, 9, 28)
    corte(s, rest, "Steak", "8017-01", 5.0)
    estado = inventory.monthly_status(s, rest.id, on=fin_de_mes)
    assert estado.days_left == 2 and estado.due_soon


def test_a_count_can_be_cancelled_and_changes_nothing(ctx):
    s, rest, ana, luis = ctx
    lot = corte(s, rest, "Steak", "8017-01", 5.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=2.0)      # ya se había contado algo

    inventory.cancel_count(s, ana, count, "Nos quedamos sin tiempo")

    assert count.status == CountStatus.CANCELLED
    assert count.cancel_reason == "Nos quedamos sin tiempo"
    s.refresh(lot)
    assert lot.qty_remaining == 5.0                          # nada se ajustó
    assert s.query(IngredientMovement).count() == 0
    assert not inventory.monthly_status(s, rest.id, on=HOY).done
    assert inventory.last_closed(s, rest.id) is None


def test_after_cancelling_you_can_start_another(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    primero = inventory.open_count(s, ana, on=HOY)
    inventory.cancel_count(s, ana, primero)
    segundo = inventory.open_count(s, ana, on=HOY)
    assert segundo.id != primero.id and segundo.status == CountStatus.OPEN


def test_a_closed_count_cannot_be_cancelled(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=5.0)
    inventory.close_count(s, ana, count)
    with pytest.raises(InventoryError, match="abierto"):
        inventory.cancel_count(s, ana, count)


def test_monthly_counts_are_the_same_thing_with_another_label(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    count = inventory.open_count(s, ana, CountPeriod.MONTHLY, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=4.9)
    result = inventory.close_count(s, ana, count)
    assert count.period == CountPeriod.MONTHLY
    assert result.summary.lines[0].outcome == SHORT


def test_counts_never_cross_between_restaurants(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    otro, eva = auth.create_restaurant(s, "Otro", "eva@otro.com", "Eva", "clave-larga-9")
    vecino = inventory.open_count(s, eva, on=HOY)
    assert vecino.lines == []
    result = inventory.close_count(s, eva, vecino)
    assert result.summary.lines == [] and result.alerts == []


# ============================ cuando la pieza aparece después de darla por perdida
def test_a_primal_that_turns_up_goes_back_to_normal(ctx):
    s, rest, ana, luis = ctx
    p = primal(s, rest, "9001", kg=10.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "9001", kg=0.0)
    inventory.close_count(s, ana, count)
    assert p.suspect_phantom is True

    r = inventory.recover(s, ana, "9001", note="Estaba en el arcón de abajo")

    s.refresh(p)
    assert p.suspect_phantom is False and p.status == PrimalStatus.IN_STOCK
    assert r.kind == "PRIMAL" and r.serial == "9001"
    from thegrill.models import AuditLog
    apunte = s.query(AuditLog).filter_by(key="9001").one()
    assert apunte.action == "RECOVER" and "arcón" in apunte.detail and apunte.actor == "Ana"


def test_recovering_a_primal_can_correct_its_weight(ctx):
    s, rest, ana, luis = ctx
    p = primal(s, rest, "9001", kg=10.0)
    p.suspect_phantom = True
    s.flush()
    inventory.recover(s, ana, "9001", kg=9.6)
    s.refresh(p)
    assert p.weight_kg == 9.6 and not p.suspect_phantom


def test_a_primal_already_butchered_is_not_recovered_here(ctx):
    """Si consta cortado, lo que está mal es el despiece, no la pieza."""
    s, rest, ana, luis = ctx
    p = primal(s, rest, "9001")
    p.status = PrimalStatus.CUT
    p.status_ref = "TG-0007"
    s.flush()
    with pytest.raises(InventoryError, match="corregir el despiece"):
        inventory.recover(s, ana, "9001")


def test_a_cut_that_turns_up_comes_back_with_its_movement(ctx):
    s, rest, ana, luis = ctx
    lot = corte(s, rest, "Striploin steak", "8017-01", 5.0, coste=30.0)
    count = inventory.open_count(s, ana, on=HOY)
    inventory.record(s, luis, count, "8017-01", kg=0.0)
    inventory.close_count(s, ana, count)
    s.refresh(lot)
    assert lot.qty_remaining == 0.0

    r = inventory.recover(s, ana, "8017-01", kg=4.6, note="Apareció en el abatidor", on=HOY)

    s.refresh(lot)
    assert lot.qty_remaining == 4.6
    assert r.restored_kg == 4.6 and r.value == 138.0
    ajuste = (s.query(IngredientMovement).filter_by(source="recovery").one())
    assert ajuste.qty == 4.6 and ajuste.cost == 138.0 and ajuste.source_ref == "8017-01"


def test_recovering_a_cut_needs_to_say_how_much(ctx):
    s, rest, ana, luis = ctx
    lot = corte(s, rest, "Steak", "8017-01", 5.0)
    lot.qty_remaining = 0.0
    s.flush()
    with pytest.raises(InventoryError, match="cuántos kilos"):
        inventory.recover(s, ana, "8017-01")


def test_recovery_never_lowers_stock(ctx):
    """Para bajar se cuenta en un inventario, no se 'corrige' a la baja."""
    s, rest, ana, luis = ctx
    corte(s, rest, "Steak", "8017-01", 5.0)
    with pytest.raises(InventoryError, match="se cuenta en un inventario"):
        inventory.recover(s, ana, "8017-01", kg=3.0)


def test_an_unknown_serial_is_not_invented(ctx):
    s, rest, ana, luis = ctx
    with pytest.raises(InventoryError, match="hay que darla de alta"):
        inventory.recover(s, ana, "NUNCA-VISTO", kg=2.0)


def test_a_piece_the_system_never_had_can_be_registered(ctx):
    s, rest, ana, luis = ctx
    from thegrill.models import IngredientItem
    corte(s, rest, "Striploin steak", "8017-01", 5.0)
    item = s.query(IngredientItem).first()

    r = inventory.adopt(s, ana, "7777-09", item.id, kg=1.8, unit_cost=28.0,
                        expiry=HOY + timedelta(days=10), note="Estaba sin etiqueta", on=HOY)

    assert r.adopted and r.restored_kg == 1.8 and r.value == 50.4
    lot = s.query(IngredientLot).filter_by(serial="7777-09").one()
    assert lot.qty_remaining == 1.8 and lot.unit_cost == 28.0 and lot.lot_code == "RECUPERADO"
    from thegrill.models import AuditLog
    assert s.query(AuditLog).filter_by(key="7777-09").one().action == "ADOPT"


def test_registering_a_found_piece_needs_a_price(ctx):
    s, rest, ana, luis = ctx
    from thegrill.models import IngredientItem
    corte(s, rest, "Steak", "8017-01", 5.0)
    item = s.query(IngredientItem).first()
    for kg, coste in ((0, 28.0), (-1, 28.0), (1.0, -5.0)):
        with pytest.raises(InventoryError):
            inventory.adopt(s, ana, "7777-09", item.id, kg=kg, unit_cost=coste,
                            expiry=HOY + timedelta(days=10))


def test_a_serial_is_never_registered_twice(ctx):
    s, rest, ana, luis = ctx
    from thegrill.models import IngredientItem
    corte(s, rest, "Steak", "8017-01", 5.0)
    item = s.query(IngredientItem).first()
    with pytest.raises(InventoryError, match="Ya existe"):
        inventory.adopt(s, ana, "8017-01", item.id, kg=1.0, unit_cost=10.0,
                        expiry=HOY + timedelta(days=5))


def test_a_correction_is_never_made_quietly(ctx):
    s, rest, ana, luis = ctx
    lot = corte(s, rest, "Steak", "8017-01", 5.0)
    lot.qty_remaining = 0.0
    s.flush()
    inventory.recover(s, luis, "8017-01", kg=4.0, on=HOY)
    assert service.unread_count(s, ana.id) >= 1        # el manager se entera
    assert service.unread_count(s, luis.id) == 0


def test_you_cannot_recover_a_piece_from_another_restaurant(ctx):
    s, rest, ana, luis = ctx
    lot = corte(s, rest, "Steak", "8017-01", 5.0)
    lot.qty_remaining = 0.0
    s.flush()
    otro, eva = auth.create_restaurant(s, "Otro", "eva@otro.com", "Eva", "clave-larga-9")
    with pytest.raises(InventoryError):
        inventory.recover(s, eva, "8017-01", kg=4.0)
    s.refresh(lot)
    assert lot.qty_remaining == 0.0
