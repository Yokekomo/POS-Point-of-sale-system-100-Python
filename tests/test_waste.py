"""La merma de lo que ya estaba cortado y en la cámara.

La merma del despiece ya la absorben los cortes al repartir el coste del
primal. Esta es la otra: la pieza que se echa a perder después.

Lo que se comprueba aquí es lo que se pide en cocina: que quede escrito el
número de despiece, el serial, los kilos y las piezas, y que el coste de lo
tirado no se evapore — se queda en lo que sobra, que sube de precio y con él
su food cost.
"""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.models import (AlertSeverity, Ingredient, IngredientItem, IngredientLot,
                             IngredientMovement, MovementKind, Notification, Rotation, Unit)
from thegrill.web import auth, costing, waste
from thegrill.web.waste import WasteError

HOY = date(2026, 9, 20)


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'w.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Bistró", "ana@b.com", "Ana",
                                           "clave-larga-1", language="es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@b.com", "Luis", "clave-larga-2")
        yield s, rest, ana, luis


def madre(s, rest, name, unit=Unit.KG, rotation=Rotation.FEFO) -> Ingredient:
    i = Ingredient(restaurant_id=rest.id, name=name, unit=unit, rotation=rotation)
    s.add(i); s.flush(); return i


def articulo(s, rest, ing, name) -> IngredientItem:
    it = IngredientItem(restaurant_id=rest.id, ingredient_id=ing.id, name=name)
    s.add(it); s.flush(); return it


def corte(s, rest, ana, *, kg=5.1, unit_cost=30.0, serial="8017-01", tg="TG-0010",
          pieces=17, ingredient=None, item=None, days=10):
    """Un lote de filetes ya cortados, como los deja el despiece."""
    ing = ingredient or madre(s, rest, "Striploin steak")
    it = item or articulo(s, rest, ing, "Striploin steak 300g AUS")
    lot = costing.receive(s, ana, it, kg, unit_cost, HOY + timedelta(days=days),
                          lot_code=tg, received=HOY, on=HOY)
    lot.serial = serial
    lot.parent_serial = "8017"
    lot.pieces = pieces
    lot.piece_weight_g = round(kg * 1000 / pieces, 1)
    lot.nominal_piece_g = 300.0
    s.flush()
    return ing, it, lot


# =================================================== queda escrito en algún sitio
def test_waste_is_written_down_with_its_tg_serial_kilos_and_pieces(ctx):
    s, rest, ana, _ = ctx
    ing, _, lot = corte(s, rest, ana)

    r = waste.record(s, ana, kg=0.6, serial="8017-01", pieces=2, reason="Caducado", on=HOY)

    mov = (s.query(IngredientMovement)
           .filter_by(restaurant_id=rest.id, kind=MovementKind.WASTE).one())
    assert mov.lot_id == lot.id
    assert mov.qty == -0.6                        # sale del stock, no entra
    assert mov.date == HOY
    assert mov.created_by == ana.id
    assert "TG-0010" in mov.source_ref            # el despiece del que salió
    assert "8017-01" in mov.source_ref            # el serial de la pieza
    assert "2 pz" in mov.source_ref               # cuántas piezas
    assert "Caducado" in mov.source_ref           # y por qué
    assert r.tg == "TG-0010" and r.serial == "8017-01" and r.pieces == 2
    assert r.ingredient == ing.name


def test_what_was_thrown_is_priced_at_what_it_cost(ctx):
    s, rest, ana, _ = ctx
    corte(s, rest, ana, kg=5.1, unit_cost=30.0)
    r = waste.record(s, ana, kg=0.6, serial="8017-01", pieces=2, on=HOY)
    assert r.cost == 18.0                          # 0.6 kg a 30 €/kg
    assert r.kg == 0.6


def test_the_stock_goes_down_by_what_was_thrown(ctx):
    s, rest, ana, _ = ctx
    ing, _, lot = corte(s, rest, ana, kg=5.1)
    waste.record(s, ana, kg=0.6, serial="8017-01", on=HOY)
    assert lot.qty_remaining == 4.5
    assert costing.stock_on_hand(s, rest.id)[ing.id] == 4.5


# ============================== el coste de lo tirado se queda en lo que sobra
def test_the_pieces_that_survive_pay_for_the_ones_thrown(ctx):
    s, rest, ana, _ = ctx
    # Diecisiete filetes de 300 g a 30 €/kg: 5.1 kg que costaron 153 €.
    ing, _, lot = corte(s, rest, ana, kg=5.1, unit_cost=30.0, pieces=17)
    antes = lot.unit_cost

    r = waste.record(s, ana, kg=0.6, serial="8017-01", pieces=2, reason="Oxidado", on=HOY)

    assert r.absorbed
    # Los quince que quedan (4.5 kg) siguen valiendo los 153 € de los diecisiete.
    assert round(lot.qty_remaining * lot.unit_cost, 2) == 153.0
    assert lot.unit_cost == 34.0                   # 153 / 4.5
    assert r.unit_cost_before == antes == 30.0
    assert r.unit_cost_after == 34.0
    assert r.cost_increase_pct == pytest.approx(13.33, abs=0.01)


def test_the_food_cost_of_the_rest_goes_up(ctx):
    s, rest, ana, _ = ctx
    ing, _, _ = corte(s, rest, ana, kg=5.1, unit_cost=30.0)
    # Un filete de 300 g en un plato de 20 € netos.
    fc_antes = costing.unit_costs(s, rest.id)[ing.id] * 0.3 / 20 * 100
    waste.record(s, ana, kg=0.6, serial="8017-01", pieces=2, on=HOY)
    fc_despues = costing.unit_costs(s, rest.id)[ing.id] * 0.3 / 20 * 100
    assert fc_antes == pytest.approx(45.0)
    assert fc_despues == pytest.approx(51.0)       # el mismo plato cuesta más
    assert fc_despues > fc_antes


def test_other_lots_of_the_same_mother_are_not_touched(ctx):
    s, rest, ana, _ = ctx
    ing, it, lot = corte(s, rest, ana, kg=5.1, unit_cost=30.0)
    otro = costing.receive(s, ana, it, 4.0, 28.0, HOY + timedelta(days=20),
                           lot_code="TG-0011", received=HOY, on=HOY)
    otro.serial = "8020-01"; s.flush()

    waste.record(s, ana, kg=0.6, serial="8017-01", on=HOY)

    assert otro.unit_cost == 28.0                  # la merma es de un lote, no de la madre
    assert otro.qty_remaining == 4.0


def test_when_the_whole_lot_goes_there_is_nothing_left_to_absorb_it(ctx):
    s, rest, ana, _ = ctx
    _, _, lot = corte(s, rest, ana, kg=5.1, unit_cost=30.0)
    r = waste.record(s, ana, kg=5.1, serial="8017-01", pieces=17, reason="Rotura de frío", on=HOY)
    assert r.remaining_kg == 0.0
    assert not r.absorbed                          # se pierde entero, no hay a quién cargárselo
    assert r.cost == 153.0
    assert r.cost_increase_pct is None
    assert lot.unit_cost == 30.0                   # el precio del lote no se toca


def test_waste_can_be_recorded_without_absorbing_when_asked(ctx):
    s, rest, ana, _ = ctx
    _, _, lot = corte(s, rest, ana, kg=5.1, unit_cost=30.0)
    r = waste.record(s, ana, kg=0.6, serial="8017-01", on=HOY, absorb=False)
    assert not r.absorbed
    assert lot.unit_cost == 30.0
    assert lot.qty_remaining == 4.5                # el stock sí baja


# ======================================================== lo que no se admite
def test_a_waste_can_never_leave_the_stock_negative(ctx):
    s, rest, ana, _ = ctx
    _, _, lot = corte(s, rest, ana, kg=5.1)
    with pytest.raises(WasteError, match="negativo"):
        waste.record(s, ana, kg=6.0, serial="8017-01", on=HOY)
    assert lot.qty_remaining == 5.1                # nada se ha movido
    assert s.query(IngredientMovement).filter_by(kind=MovementKind.WASTE).count() == 0


def test_zero_or_negative_kilos_is_not_a_waste(ctx):
    s, rest, ana, _ = ctx
    corte(s, rest, ana)
    for kg in (0.0, -1.0):
        with pytest.raises(WasteError):
            waste.record(s, ana, kg=kg, serial="8017-01", on=HOY)


def test_an_unknown_serial_is_refused(ctx):
    s, rest, ana, _ = ctx
    corte(s, rest, ana)
    with pytest.raises(WasteError, match="8099"):
        waste.record(s, ana, kg=0.5, serial="8099-01", on=HOY)


def test_a_lot_already_finished_has_nothing_to_throw(ctx):
    s, rest, ana, _ = ctx
    _, _, lot = corte(s, rest, ana, kg=2.0)
    lot.qty_remaining = 0.0; s.flush()
    with pytest.raises(WasteError):
        waste.record(s, ana, kg=0.5, serial="8017-01", on=HOY)


def test_without_a_serial_or_an_ingredient_there_is_nothing_to_record(ctx):
    s, rest, ana, _ = ctx
    corte(s, rest, ana)
    with pytest.raises(WasteError):
        waste.record(s, ana, kg=0.5, on=HOY)


# ============================ sin serial: sale del lote que tocaría por rotación
def test_without_a_serial_it_takes_the_lot_that_would_leave_first(ctx):
    s, rest, ana, _ = ctx
    ing = madre(s, rest, "Leche", unit=Unit.L)
    it = articulo(s, rest, ing, "Leche entera")
    pronto = costing.receive(s, ana, it, 6.0, 1.0, HOY + timedelta(days=2),
                             lot_code="L-1", received=HOY, on=HOY)
    tarde = costing.receive(s, ana, it, 6.0, 1.2, HOY + timedelta(days=30),
                            lot_code="L-2", received=HOY, on=HOY)

    r = waste.record(s, ana, kg=2.0, ingredient_id=ing.id, reason="Cortada", on=HOY)

    assert r.tg == "L-1"                           # FEFO: primero lo que antes caduca
    assert pronto.qty_remaining == 4.0
    assert tarde.qty_remaining == 6.0
    assert round(pronto.qty_remaining * pronto.unit_cost, 4) == 6.0


def test_an_ingredient_with_no_stock_has_nothing_to_throw(ctx):
    s, rest, ana, _ = ctx
    ing = madre(s, rest, "Leche", unit=Unit.L)
    articulo(s, rest, ing, "Leche entera")
    with pytest.raises(WasteError, match="Leche"):
        waste.record(s, ana, kg=1.0, ingredient_id=ing.id, on=HOY)


# ================================================================ quién se entera
def test_the_managers_hear_about_it(ctx):
    s, rest, ana, luis = ctx
    corte(s, rest, ana)
    r = waste.record(s, luis, kg=0.6, serial="8017-01", pieces=2, reason="Caducado", on=HOY)

    assert r.alert is not None
    assert "8017-01" in r.alert.message
    notas = s.query(Notification).filter_by(restaurant_id=rest.id).all()
    assert [n.user_id for n in notas] == [ana.id]        # al manager, no a quien lo tiró
    assert notas[0].alert_id == r.alert.id


def test_a_big_loss_is_critical_and_a_small_one_only_a_warning(ctx):
    s, rest, ana, _ = ctx
    ing = madre(s, rest, "Leche", unit=Unit.L)
    it = articulo(s, rest, ing, "Leche entera")
    costing.receive(s, ana, it, 100.0, 1.0, HOY + timedelta(days=5), lot_code="L-1", on=HOY)
    chica = waste.record(s, ana, kg=2.0, ingredient_id=ing.id, on=HOY)
    grande = waste.record(s, ana, kg=60.0, ingredient_id=ing.id, on=HOY)
    assert chica.alert.severity == AlertSeverity.WARNING
    assert grande.alert.severity == AlertSeverity.CRITICAL


# ================================================================ el histórico
def test_recent_waste_is_listed_newest_first(ctx):
    s, rest, ana, _ = ctx
    corte(s, rest, ana, kg=8.0)
    waste.record(s, ana, kg=0.3, serial="8017-01", on=HOY - timedelta(days=2))
    waste.record(s, ana, kg=0.5, serial="8017-01", on=HOY)
    vieja = waste.record(s, ana, kg=0.4, serial="8017-01", on=HOY - timedelta(days=90))

    lista = waste.recent(s, rest.id, days=30)
    assert [m.qty for m in lista] == [-0.5, -0.3]   # la de hace tres meses ya no sale
    assert vieja.kg == 0.4


def test_one_restaurant_never_sees_another_kitchens_waste(ctx):
    s, rest, ana, _ = ctx
    corte(s, rest, ana)
    waste.record(s, ana, kg=0.6, serial="8017-01", on=HOY)

    otro, bea = auth.create_restaurant(s, "Otra cocina", "bea@o.com", "Bea",
                                       "clave-larga-3", language="es")
    assert waste.recent(s, otro.id) == []

    ing2 = madre(s, otro, "Striploin steak")
    it2 = articulo(s, otro, ing2, "Striploin AUS")
    lot2 = costing.receive(s, bea, it2, 3.0, 25.0, HOY + timedelta(days=5),
                           lot_code="TG-9", received=HOY, on=HOY)
    lot2.serial = "9001-01"; s.flush()
    with pytest.raises(WasteError):                 # el serial del vecino no existe aquí
        waste.record(s, bea, kg=0.2, serial="8017-01", on=HOY)
    with pytest.raises(WasteError, match="restaurante"):
        waste.record(s, bea, kg=0.2, ingredient_id=s.query(Ingredient)
                     .filter_by(restaurant_id=rest.id).first().id, on=HOY)


# ------------------------------------------ todo lo que se tira, en una lista
def test_the_waste_list_puts_the_chiller_and_the_trimming_together(ctx):
    """Lo tirado de cámara y lo tirado limpiando son lo mismo: carne que no se vende."""
    from datetime import timedelta
    from thegrill.meat import service as meat
    from thegrill.models import Primal, Storage
    from thegrill.web import aging

    s, rest, ana, luis = ctx
    corte(s, rest, ana, kg=5.1, unit_cost=30.0, serial="8017-01")
    waste.record(s, luis, kg=0.6, serial="8017-01", pieces=2, reason="Caducado", on=HOY)

    s.add(Primal(restaurant_id=rest.id, serial="9001", sku="Ribeye AUS", weight_kg=10.0,
                 landed_usd_per_kg=30.0, piece_cost_usd=300.0, received_date=HOY,
                 expiry_label=HOY + timedelta(days=30)))
    s.flush()
    aging.trim(s, ana, "9001", removed_kg=1.2, on=HOY)      # se tira entera

    filas = waste.everything(s, rest.id)
    assert len(filas) == 2
    fuentes = {f.source: f for f in filas}
    assert fuentes["chamber"].kg == 0.6
    assert fuentes["chamber"].label == "Striploin steak"
    assert fuentes["chamber"].cost == pytest.approx(18.0)       # 0,6 × 30
    assert fuentes["chamber"].who == "Luis"
    assert fuentes["trim"].kg == 1.2
    assert fuentes["trim"].label == "Ribeye AUS"
    assert fuentes["trim"].serial == "9001"
    assert fuentes["trim"].cost == pytest.approx(36.0)          # 1,2 × 30
    assert fuentes["trim"].who == "Ana"

    total = waste.totals(filas)
    assert total.kg == pytest.approx(1.8)
    assert total.cost == pytest.approx(54.0)
    assert total.chamber_kg == 0.6 and total.trim_kg == 1.2


def test_a_trim_that_is_all_reused_is_not_waste(ctx):
    """Si de la limpieza no se tira nada, no aparece en la lista de merma."""
    from datetime import timedelta
    from thegrill.meat import service as meat
    from thegrill.models import Primal
    from thegrill.web import aging

    s, rest, ana, luis = ctx
    recortes = meat.create_cut(s, ana, "Recortes")
    articulo = meat.add_article(s, ana, recortes, "Recortes AUS")
    s.add(Primal(restaurant_id=rest.id, serial="9002", sku="Ribeye AUS", weight_kg=10.0,
                 landed_usd_per_kg=30.0, piece_cost_usd=300.0, received_date=HOY,
                 expiry_label=HOY + timedelta(days=30)))
    s.flush()
    aging.trim(s, ana, "9002", removed_kg=0.8, on=HOY,
               parts=[aging.TrimPart(item_id=articulo.id, kg=0.8)])

    assert waste.everything(s, rest.id) == []
    assert waste.totals(waste.everything(s, rest.id)).kg == 0.0


def test_each_waste_line_says_which_butchery_and_how_many_pieces(ctx):
    """Lo que se tira se lee sin ir a buscarlo: despiece, serial, piezas y motivo."""
    s, rest, ana, luis = ctx
    corte(s, rest, ana, kg=5.1, unit_cost=30.0, serial="8017-01", tg="TG-0010", pieces=17)
    waste.record(s, luis, kg=0.6, serial="8017-01", pieces=2, reason="Caducado", on=HOY)

    linea = waste.everything(s, rest.id)[0]
    assert linea.lot == "TG-0010"
    assert linea.serial == "8017-01"
    assert linea.pieces == 2
    assert linea.reason == "Caducado"
    assert linea.who == "Luis"
