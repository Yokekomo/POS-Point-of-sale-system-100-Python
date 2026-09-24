"""Lo descongelado caduca como lo descongelado, y por eso sale primero.

Un lomo congelado caduca dentro de diez meses; el mismo lomo, sacado del arcón
el martes, caduca el viernes. Es la misma carne y son dos fechas, y la que
manda es la segunda. El programa le dejaba la del congelador, y como la
rotación va por fecha —lo que antes caduca, antes sale— lo descongelado se iba
al final de la cola: la bandeja que había que gastar esta semana esperando
detrás de la que aguantaba hasta el año que viene.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.models import (Ingredient, IngredientItem, IngredientLot, Primal,
                             PrimalStatus, Restaurant, Rotation, Storage, Unit, User)
from thegrill.web import aging, caducidad, costing, defrost

from tests.meat_helpers import SPANISH, csrf_from, login, new_house

HOY = date.today()
DENTRO_DE_UN_AÑO = HOY + timedelta(days=300)


# --------------------------------------------------------------- la regla
def test_the_freezer_date_never_survives_the_thaw():
    assert caducidad.tras_descongelar(DENTRO_DE_UN_AÑO, HOY) == HOY + timedelta(days=3)


def test_but_a_label_that_said_sooner_wins():
    """Descongelar no alarga la vida de nada."""
    pasado_mañana = HOY + timedelta(days=2)
    assert caducidad.tras_descongelar(pasado_mañana, HOY) == pasado_mañana


def test_with_no_date_at_all_it_still_gets_one():
    assert caducidad.tras_descongelar(None, HOY) == HOY + timedelta(days=3)


def test_the_house_says_how_many_days():
    class Casa:
        thaw_days = 5
    assert caducidad.tras_descongelar(DENTRO_DE_UN_AÑO, HOY, Casa()) == HOY + timedelta(days=5)


def test_a_finger_cannot_give_a_tray_a_month_of_life():
    class Casa:
        thaw_days = 90
    assert caducidad.dias(Casa()) == caducidad.MAXIMO
    class Cero:
        thaw_days = 0
    assert caducidad.dias(Cero()) == 1      # nada dura cero días
    class Letras:
        thaw_days = "tres"
    assert caducidad.dias(Letras()) == caducidad.POR_DEFECTO


# -------------------------------------------------------------- los lotes
@pytest.fixture
def casa(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'carne.db'}")
    db.create_all()
    new_house(language="es")
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        user = s.query(User).filter_by(restaurant_id=rest.id).first()
        ing = Ingredient(restaurant_id=rest.id, name="Lomo", unit=Unit.KG,
                         rotation=Rotation.FEFO)
        s.add(ing); s.flush()
        item = IngredientItem(restaurant_id=rest.id, ingredient_id=ing.id, name="Lomo AUS")
        s.add(item); s.flush()
        ids = (rest.id, user.id, ing.id, item.id)
    # Fuera del `with`: si la sesión que la monta sigue abierta mientras corre
    # la prueba, las dos se pelean por la misma base y una espera treinta
    # segundos antes de rendirse.
    return ids


def _lote(s, rest_id, ing_id, item_id, serial, kg, expiry, frozen):
    lote = IngredientLot(restaurant_id=rest_id, ingredient_id=ing_id, item_id=item_id,
                         serial=serial, expiry=expiry, received=HOY, qty=kg,
                         qty_remaining=kg, unit_cost=30.0, frozen=frozen, pieces=1)
    s.add(lote); s.flush()
    return lote


def test_what_comes_out_of_the_freezer_goes_to_the_front_of_the_queue(casa):
    """La prueba de por qué esto importaba: el orden de rotación."""
    rest_id, user_id, ing_id, item_id = casa
    with db.session_scope() as s:
        user = s.get(User, user_id)
        _lote(s, rest_id, ing_id, item_id, "1001", 5.0, HOY + timedelta(days=20), False)
        congelado = _lote(s, rest_id, ing_id, item_id, "1002", 9.0, DENTRO_DE_UN_AÑO, True)
        hijo = defrost.thaw(s, user, congelado, 4.0, 1, on=HOY)
        serial_hijo = hijo.serial
        assert hijo.expiry == HOY + timedelta(days=3)
        assert hijo.frozen_expiry == DENTRO_DE_UN_AÑO      # la de antes, guardada

    with db.session_scope() as s:
        ing = s.get(Ingredient, ing_id)
        cola = costing.rotation_order(s, rest_id, ing)
        assert cola[0].serial == serial_hijo               # lo descongelado, primero


def test_the_whole_lot_thawed_keeps_the_record_of_what_it_was(casa):
    rest_id, user_id, ing_id, item_id = casa
    with db.session_scope() as s:
        user = s.get(User, user_id)
        lote = _lote(s, rest_id, ing_id, item_id, "1003", 9.0, DENTRO_DE_UN_AÑO, True)
        salido = defrost.thaw(s, user, lote, 9.0, 1, on=HOY)
        assert salido.id == lote.id                        # sale entero: el mismo lote
        assert salido.frozen is False
        assert salido.expiry == HOY + timedelta(days=3)
        assert salido.frozen_expiry == DENTRO_DE_UN_AÑO


def test_thawing_twice_does_not_move_the_date_forward(casa):
    """Sacar, volver a tocar: la fecha del arcón no vuelve nunca."""
    rest_id, user_id, ing_id, item_id = casa
    with db.session_scope() as s:
        user = s.get(User, user_id)
        lote = _lote(s, rest_id, ing_id, item_id, "1004", 9.0, DENTRO_DE_UN_AÑO, True)
        defrost.thaw(s, user, lote, 9.0, 1, on=HOY)
        antes = lote.expiry
        lote.frozen = True                                  # alguien lo devuelve al arcón
        defrost.thaw(s, user, lote, 9.0, 1, on=HOY + timedelta(days=1))
        assert lote.expiry == antes                         # no se alarga
        assert lote.frozen_expiry == DENTRO_DE_UN_AÑO


# ------------------------------------------------------------- las piezas
def test_thawing_a_whole_piece_no_longer_erases_its_only_date(casa):
    """Una pieza que llegó congelada no trae más fecha que la del arcón.

    Al sacarla se le borraba y se quedaba sin caducidad, sin aviso y sin sitio
    en la cola. Ahora se le pone la de después de descongelar.
    """
    rest_id, user_id, _ing_id, _item_id = casa
    with db.session_scope() as s:
        user = s.get(User, user_id)
        pieza = Primal(restaurant_id=rest_id, serial="8017", sku="Lomo AUS",
                       weight_kg=9.0, received_kg=9.0, received_date=HOY,
                       landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                       status=PrimalStatus.IN_STOCK, storage=Storage.FROZEN,
                       frozen_use_by=DENTRO_DE_UN_AÑO)
        s.add(pieza); s.flush()
        aging.move(s, user, "8017", Storage.CHILLED, on=HOY)
        assert pieza.frozen_use_by is None                  # la del arcón deja de mandar
        assert pieza.expiry_label == HOY + timedelta(days=3)


def test_a_piece_with_a_nearer_label_keeps_it(casa):
    rest_id, user_id, _ing_id, _item_id = casa
    pasado_mañana = HOY + timedelta(days=2)
    with db.session_scope() as s:
        user = s.get(User, user_id)
        pieza = Primal(restaurant_id=rest_id, serial="8018", sku="Lomo AUS",
                       weight_kg=9.0, received_kg=9.0, received_date=HOY,
                       landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                       status=PrimalStatus.IN_STOCK, storage=Storage.FROZEN,
                       frozen_use_by=DENTRO_DE_UN_AÑO, expiry_label=pasado_mañana)
        s.add(pieza); s.flush()
        aging.move(s, user, "8018", Storage.CHILLED, on=HOY)
        assert pieza.expiry_label == pasado_mañana


def test_moving_between_two_fridges_does_not_touch_the_date(casa):
    """Solo cuenta salir del congelador. De cámara a maduración no es descongelar."""
    rest_id, user_id, _ing_id, _item_id = casa
    dentro_de_un_mes = HOY + timedelta(days=30)
    with db.session_scope() as s:
        user = s.get(User, user_id)
        pieza = Primal(restaurant_id=rest_id, serial="8019", sku="Lomo AUS",
                       weight_kg=9.0, received_kg=9.0, received_date=HOY,
                       landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                       status=PrimalStatus.IN_STOCK, storage=Storage.CHILLED,
                       expiry_label=dentro_de_un_mes)
        s.add(pieza); s.flush()
        aging.move(s, user, "8019", Storage.AGING, on=HOY)
        assert pieza.expiry_label == dentro_de_un_mes


# ------------------------------------------------------------ la pantalla
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'carne.db'}")
    db.create_all()
    new_house(language="es")
    with TestClient(__import__("thegrill.meat.app", fromlist=["app"]).app,
                    follow_redirects=False, headers=SPANISH) as c:
        login(c)
        yield c


def test_the_manager_sets_the_days(client):
    form = client.get("/configuracion")
    client.post("/configuracion", data={"csrf": csrf_from(form.text), "language": "es",
                                        "thaw_days": "5"})
    with db.session_scope() as s:
        assert s.query(Restaurant).filter(
            Restaurant.platform.isnot(True)).one().thaw_days == 5
    assert "5" in client.get("/configuracion").text


# ================================== el número que nacía sin kilos que llevar
#
# El hijo se escribía antes de comprobar que quedaban kilos en el padre. Si la
# resta fallaba —otra persona se había llevado esos kilos un segundo antes— se
# levantaba el error, la pantalla lo decía, el de fuera lo leía y se iba... y
# el hijo se quedaba escrito igual, porque la petición terminaba bien. En la
# cámara quedaba un número con kilos que no habían salido de ninguna parte.
def test_a_thaw_that_loses_the_race_leaves_no_phantom_number(casa):
    rest_id, user_id, ing_id, item_id = casa
    with db.session_scope() as s:
        _lote(s, rest_id, ing_id, item_id, "1010", 9.0, DENTRO_DE_UN_AÑO, True)

    # La carrera de verdad: se abre la pantalla con nueve kilos delante y, un
    # segundo antes de darle, otra persona se lleva casi todo. La pantalla
    # levanta el error, lo enseña y contesta: la petición termina **bien** y
    # la sesión se guarda. Eso es lo que dejaba el número fantasma.
    with db.session_scope() as s:
        user = s.get(User, user_id)
        lote = s.query(IngredientLot).filter_by(restaurant_id=rest_id,
                                                serial="1010").one()
        (s.query(IngredientLot).filter_by(id=lote.id)
         .update({"qty_remaining": 0.5}, synchronize_session=False))
        with pytest.raises(defrost.DefrostError):
            defrost.thaw(s, user, lote, 4.0, 1, on=HOY)

    with db.session_scope() as s:
        assert s.query(IngredientLot).filter(
            IngredientLot.serial.like("1010·%")).count() == 0
        quedo = s.query(IngredientLot).filter_by(restaurant_id=rest_id,
                                                 serial="1010").one()
        assert quedo.qty_remaining == 0.5      # ni un kilo de más ni de menos
        assert quedo.pieces == 1               # ni una pieza descontada


def test_and_neither_does_a_transfer(casa):
    rest_id, user_id, ing_id, item_id = casa
    from thegrill.models import Site, SiteKind
    from thegrill.web import sites
    with db.session_scope() as s:
        obrador = Site(restaurant_id=rest_id, name="Obrador", kind=SiteKind.WAREHOUSE)
        playa = Site(restaurant_id=rest_id, name="Playa", kind=SiteKind.OUTLET)
        s.add_all([obrador, playa]); s.flush()
        lote = _lote(s, rest_id, ing_id, item_id, "1011", 9.0,
                     HOY + timedelta(days=20), False)
        lote.site_id = obrador.id

    with db.session_scope() as s:
        user = s.get(User, user_id)
        playa_id = s.query(Site).filter_by(restaurant_id=rest_id, name="Playa").one().id
        lote = s.query(IngredientLot).filter_by(restaurant_id=rest_id,
                                                serial="1011").one()
        (s.query(IngredientLot).filter_by(id=lote.id)
         .update({"qty_remaining": 0.5}, synchronize_session=False))
        with pytest.raises(sites.SiteError):
            sites.send_cut(s, user, "1011", 4.0, playa_id, on=HOY)

    with db.session_scope() as s:
        assert s.query(IngredientLot).filter(
            IngredientLot.serial.like("1011·%")).count() == 0
        quedo = s.query(IngredientLot).filter_by(restaurant_id=rest_id,
                                                 serial="1011").one()
        assert quedo.qty_remaining == 0.5
        assert quedo.pieces == 1
