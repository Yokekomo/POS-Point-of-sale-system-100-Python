"""Números que no pueden ser, y números que pueden ser y están mal.

Dos cosas distintas y la diferencia importa. Un solomillo de 1370 kg no se
guarda: no existe, es el dedo en la tecla de al lado, y guardarlo llena el
registro sanitario de mentiras que nadie va a poder desmontar dentro de un
año. Una carne refrigerada que baja del camión a doce grados **sí** se guarda:
pasó de verdad, es la única prueba de que ese camión vino caliente, y borrarla
sería lo único imperdonable.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.models import Alert, AlertSeverity, Primal, Restaurant, Storage
from thegrill.web import rangos

from tests.meat_helpers import SPANISH, csrf_from, signup

HOY = date.today()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'carne.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        yield c


# ------------------------------------------------- lo que no se guarda nunca
def test_a_tenderloin_does_not_weigh_a_tonne(client):
    signup(client)
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "sku": "Solomillo",
        "price_kg": "32", "serial:0": "8017", "g:0": "1370000"})
    assert r.status_code == 200
    assert "1370" in r.text                      # dice el número que se escribió
    with db.session_scope() as s:
        assert s.query(Primal).count() == 0      # y no entra nada


def test_no_probe_reads_two_hundred_and_forty_degrees(client):
    signup(client)
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "price_kg": "32",
        "arrival": "CHILLED", "arrival_c": "240",
        "serial:0": "8017", "g:0": "9400"})
    assert r.status_code == 200
    with db.session_scope() as s:
        assert s.query(Primal).count() == 0


def test_the_message_says_what_was_expected(client):
    """Un «valor no válido» no sirve de nada con el camión esperando."""
    signup(client)
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "price_kg": "32",
        "serial:0": "8017", "g:0": "1370000"})
    assert "0,1" in r.text and "250" in r.text   # el rango, delante y con coma


def test_a_reception_that_is_refused_keeps_the_lorry_on_screen(client):
    """Lo del camión no se vuelve a teclear por un cero de más en una pieza."""
    signup(client)
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "DXB20260910", "sku": "Striploin AUS",
        "origin": "AUS", "price_kg": "32", "serial:0": "8017", "g:0": "1370000"})
    assert "DXB20260910" in r.text and "Striploin AUS" in r.text


# ------------------------------------ lo que sí se guarda, y además se avisa
def test_meat_that_arrives_warm_is_written_down_not_rejected(client):
    signup(client)
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "price_kg": "32",
        "arrival": "CHILLED", "arrival_c": "12",
        "serial:0": "8017", "g:0": "9400"})
    assert r.status_code == 303                   # se guarda: el aviso no la tumba
    with db.session_scope() as s:
        pieza = s.query(Primal).one()
        assert pieza.arrival_c == 12.0            # el número, tal cual llegó
        aviso = s.query(Alert).filter(Alert.code == "haccp.arrival_warm").one()
        assert aviso.severity == AlertSeverity.CRITICAL
        assert "8017" in aviso.message and "12" in aviso.message


def test_the_manager_sees_it_the_same_day(client):
    signup(client)
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "price_kg": "32",
        "arrival": "CHILLED", "arrival_c": "12",
        "serial:0": "8017", "g:0": "9400"})
    pagina = client.get("/manager/alertas")
    assert pagina.status_code == 200 and "8017" in pagina.text


def test_frozen_that_arrives_above_minus_twelve_is_not_frozen(client):
    """De serie, congelado es de −20 a −12: es lo que pide el 853/2004."""
    signup(client)
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "price_kg": "32",
        "arrival": "FROZEN", "arrival_c": "2",
        "serial:0": "8017", "g:0": "9400"})
    with db.session_scope() as s:
        assert s.query(Alert).filter(Alert.code == "haccp.arrival_warm").count() == 1


def test_frozen_at_minus_eighteen_is_where_it_should_be(client):
    signup(client)
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "price_kg": "32",
        "arrival": "FROZEN", "arrival_c": "-18",
        "serial:0": "8017", "g:0": "9400"})
    with db.session_scope() as s:
        assert s.query(Alert).filter(Alert.code.startswith("haccp.")).count() == 0


def test_meat_inside_the_band_raises_nothing(client):
    signup(client)
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "price_kg": "32",
        "arrival": "CHILLED", "arrival_c": "2",
        "serial:0": "8017", "g:0": "9400"})
    with db.session_scope() as s:
        assert s.query(Primal).count() == 1
        assert s.query(Alert).filter(Alert.code.startswith("haccp.")).count() == 0


# ------------------------------------------------------------- las funciones
def test_the_limits_are_wide_enough_for_real_meat():
    for kg in (0.4, 2.5, 9.4, 28.0, 120.0, 240.0):
        rangos.peso_pieza(kg)                     # no levanta
    for grados in (-45.0, -18.0, -1.0, 0.0, 7.0, 25.0):
        rangos.temperatura(grados)
    for eur in (0.5, 32.0, 450.0, 1800.0):
        rangos.precio_kg(eur)


def test_and_narrow_enough_to_catch_the_finger():
    for kg in (0.0, 1370.0, -9.4, 9400.0):
        with pytest.raises(rangos.FueraDeRango):
            rangos.peso_pieza(kg)
    for grados in (240.0, -400.0, 1200.0):
        with pytest.raises(rangos.FueraDeRango):
            rangos.temperatura(grados)


def test_nothing_is_checked_when_nothing_was_written():
    """No apuntar la temperatura es otro problema, y no es este."""
    rangos.peso_pieza(None)
    rangos.temperatura(None)
    rangos.precio_kg(None)
    assert rangos.llegada(None, Storage.CHILLED, "8017") == []


def test_a_heavy_piece_is_flagged_but_allowed():
    avisos = rangos.llegada(2.0, Storage.CHILLED, "8017", kg=95.0)
    assert [a.code for a in avisos] == ["meat.heavy_piece"]


def test_the_house_band_is_tighter_than_the_law_on_chilled():
    """Cinco grados, no siete: la norma es el mínimo y no el objetivo."""
    assert rangos.LEGAL[Storage.CHILLED] == (-5.0, 5.0)
    assert rangos.llegada(6.0, Storage.CHILLED, "8017") != []
    assert rangos.llegada(5.0, Storage.CHILLED, "8017") == []


def test_the_warning_speaks_every_language():
    for lang in ("es", "en", "fr", "de", "nl", "ar", "hu"):
        aviso = rangos.llegada(12.0, Storage.CHILLED, "8017", lang=lang)[0]
        assert "8017" in aviso.message and "12" in aviso.message
        assert not aviso.message.startswith("alert.")


# ------------------------------------- las bandas las pone la casa, no el programa
def test_the_house_can_tighten_the_band(client):
    """La norma es el mínimo. Una casa que se exige más no tiene por qué
    renunciar a que el programa se lo controle."""
    signup(client)
    form = client.get("/configuracion")
    r = client.post("/configuracion", data={
        "csrf": csrf_from(form.text), "language": "es", "chilled_max_c": "3"})
    assert r.status_code == 303
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert rangos.banda(Storage.CHILLED, casa) == (-5.0, 3.0)

    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "price_kg": "32",
        "arrival": "CHILLED", "arrival_c": "4",
        "serial:0": "8017", "g:0": "9400"})
    with db.session_scope() as s:
        aviso = s.query(Alert).filter(Alert.code == "haccp.arrival_warm").one()
        assert "3" in aviso.message          # se le dice el límite de la casa


def test_touching_one_limit_leaves_the_other_alone(client):
    signup(client)
    form = client.get("/configuracion")
    client.post("/configuracion", data={
        "csrf": csrf_from(form.text), "language": "es", "frozen_max_c": "-18"})
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert rangos.banda(Storage.FROZEN, casa) == (-20.0, -18.0)
        assert rangos.banda(Storage.CHILLED, casa) == (-5.0, 5.0)


def test_a_limit_that_is_not_a_number_leaves_the_one_that_was(client):
    """Con algo que decide si una carne se devuelve, lo prudente es no tocarlo."""
    signup(client)
    form = client.get("/configuracion")
    client.post("/configuracion", data={
        "csrf": csrf_from(form.text), "language": "es", "chilled_max_c": "cuatro"})
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert casa.chilled_max_c is None
        assert rangos.banda(Storage.CHILLED, casa) == (-5.0, 5.0)


def test_limits_the_wrong_way_round_still_measure(client):
    """Si alguien cruza los dos números, se enderezan en vez de no medir nada."""
    class Casa:
        chilled_min_c, chilled_max_c = 5.0, -5.0
        frozen_min_c = frozen_max_c = None
    assert rangos.banda(Storage.CHILLED, Casa()) == (-5.0, 5.0)


def test_the_screen_shows_the_band_of_the_house(client):
    signup(client)
    form = client.get("/configuracion")
    client.post("/configuracion", data={
        "csrf": csrf_from(form.text), "language": "es",
        "chilled_max_c": "4", "frozen_max_c": "-18"})
    pantalla = client.get("/configuracion").text
    assert 'name="chilled_max_c" inputmode="decimal" value="4.0"' in pantalla
    assert 'name="frozen_max_c" inputmode="decimal" value="-18.0"' in pantalla
