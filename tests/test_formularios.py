"""Lo que se teclea no se pierde, y lo que sale mal se puede leer.

Nada de esto hace perder dinero en un día, y por eso lleva tanto sin
arreglarse. Lo que hace es que la gente deje de usar el programa, que a la
larga es peor: con el camión en el muelle, un «4 C» en la casilla de la
temperatura tumbaba la recepción entera con un error 500, una pantalla en
blanco en inglés y la hoja por volver a teclear.
"""
import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.models import Primal

from tests.meat_helpers import SPANISH, csrf_from, login, new_house


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'carne.db'}")
    db.create_all()
    new_house(language="es")
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        login(c)
        yield c


def _recibir(client, **extra):
    form = client.get("/recepcion")
    datos = {"csrf": csrf_from(form.text), "lot": "DXB20260910", "sku": "Striploin AUS",
             "origin": "AUS", "price_kg": "32", "serial:0": "8017", "kg:0": "9,4"}
    datos.update(extra)
    return client.post("/recepcion", data=datos)


# ------------------------------------------------ el número que no es número
def test_four_c_in_the_temperature_no_longer_takes_the_screen_down(client):
    r = _recibir(client, arrival_c="4 C")
    assert r.status_code == 200                  # ni 500 ni pantalla en blanco
    with db.session_scope() as s:
        assert s.query(Primal).count() == 0      # y no entra nada a medias


def test_and_it_says_what_was_typed_in_the_language_of_the_house(client):
    r = _recibir(client, arrival_c="4 C")
    assert "4 C" in r.text                       # lo que se escribió, delante
    assert "no es un número" in r.text           # en español, no en inglés
    assert "could not convert" not in r.text     # y no el error de Python


def test_a_bad_weight_says_the_same(client):
    r = _recibir(client, **{"kg:0": "nueve"})
    assert r.status_code == 200 and "nueve" in r.text
    with db.session_scope() as s:
        assert s.query(Primal).count() == 0


# ------------------------------------------- lo tecleado vuelve donde estaba
def test_an_error_no_longer_wipes_the_sheet(client):
    """Un cero de más en una casilla obligaba a teclear la hoja otra vez."""
    r = _recibir(client, arrival_c="4 C", **{"serial:0": "8099", "kg:0": "12,345",
                                             "sku:0": "Ribeye", "slot:0": "LOTE-7"})
    assert 'value="8099"' in r.text              # el número de la pieza
    assert 'value="12,345"' in r.text            # sus kilos, como se escribieron
    assert 'value="Ribeye"' in r.text            # lo que era
    assert 'value="LOTE-7"' in r.text            # y el lote del proveedor


def test_what_the_lorry_brought_comes_back_too(client):
    r = _recibir(client, arrival_c="4 C")
    assert 'value="DXB20260910"' in r.text and 'value="Striploin AUS"' in r.text
    assert 'value="AUS"' in r.text and 'value="32"' in r.text


def test_the_temperature_that_was_wrong_comes_back_to_be_corrected(client):
    """La casilla que hay que arreglar vuelve con lo que había, no vacía."""
    r = _recibir(client, arrival_c="4 C")
    assert 'value="4 C"' in r.text


# -------------------------------------------- la pantalla de error se lee
def _banner(html: str) -> str:
    import re
    hallado = re.findall(r'<div class="banner bad">(.*?)</div>', html, re.S)
    return hallado[0].strip() if hallado else ""


def test_an_error_with_nothing_to_say_no_longer_says_nothing(client):
    """«Error 404» y una pantalla en blanco no le dicen nada a nadie."""
    r = client.get("/idioma/marciano")
    assert r.status_code == 404
    assert _banner(r.text) == "Eso ya no está, o nunca estuvo."


def test_the_error_page_speaks_the_language_of_the_house(client):
    """Una casa española se equivocaba y recibía el aviso en inglés."""
    r = client.get("/carta/no-existe", headers={"accept-language": "en"})
    assert r.status_code == 404
    assert _banner(r.text) == "Plantilla no encontrada"


def test_and_the_button_goes_back_to_the_screen_you_were_on(client):
    """El que se equivoca en una casilla no quiere empezar de cero."""
    import re
    r = client.get("/carta/no-existe",
                   headers={"referer": "http://testserver/inventario"})
    assert re.findall(r'<a class="btn" href="([^"]*)"', r.text) == \
        ["http://testserver/inventario"]


def test_a_referer_from_somewhere_else_is_not_followed(client):
    """Un enlace de vuelta lo pone quien manda la cabecera: no se obedece."""
    import re
    r = client.get("/carta/no-existe",
                   headers={"referer": "https://sitio-de-fuera.example/trampa"})
    assert re.findall(r'<a class="btn" href="([^"]*)"', r.text) == ["/"]


# ------------------------------------------ dos toques con guante, un apunte
def test_two_taps_with_a_glove_are_one_entry(client):
    """La llave del envío la lleva el formulario desde que se pinta, con red y
    sin ella. Antes solo se generaba en el camino de la cola, así que con
    cobertura —que es lo normal— estaba desconectada."""
    _recibir(client, envio="abc-123")
    _recibir(client, envio="abc-123")            # el segundo toque del guante
    with db.session_scope() as s:
        assert s.query(Primal).count() == 1


def test_but_two_real_entries_are_two(client):
    _recibir(client, envio="abc-123")
    _recibir(client, envio="abc-124", **{"serial:0": "8018"})
    with db.session_scope() as s:
        assert s.query(Primal).count() == 2


def test_reloading_the_screen_does_not_book_the_piece_again(client):
    """Recargar reenvía el mismo formulario, con la misma llave: no repite."""
    primera = _recibir(client, envio="llave-de-la-pantalla")
    assert primera.status_code == 200
    otra_vez = _recibir(client, envio="llave-de-la-pantalla")
    assert otra_vez.status_code in (200, 303)
    with db.session_scope() as s:
        assert s.query(Primal).count() == 1


def _merma(client, envio, **extra):
    pantalla = client.get("/merma")
    datos = {"csrf": csrf_from(pantalla.text), "kg": "1,2", "serial": "",
             "reason": "Caducado", "envio": envio}
    datos.update(extra)
    return client.post("/merma", data=datos)


def test_the_waste_form_keeps_what_was_typed(client):
    """Un error en los kilos no puede borrar el motivo ni las piezas."""
    r = _merma(client, "abc-125", serial="8017", kg="dos kilos",
               pieces="3", reason="Se cayó al suelo")
    assert r.status_code == 200
    assert 'value="dos kilos"' in r.text
    assert 'value="3"' in r.text
    assert 'value="Se cayó al suelo"' in r.text
    assert 'value="8017"' in r.text


# ------------------------------------ las dos pantallas que quedaban del bloque
def test_a_bad_price_for_all_no_longer_takes_the_screen_down(client):
    """El mismo fallo que el «4 C», en los precios: se leía fuera del `try`."""
    _recibir(client, price_kg="")                # entra sin precio
    pantalla = client.get("/recepcion/precios")
    r = client.post("/recepcion/precios", data={
        "csrf": csrf_from(pantalla.text), "all_price": "32 eur",
        "serial": "8017", "envio": "p-1"})
    assert r.status_code == 200                  # ni 500 ni pantalla en blanco
    assert "32 eur" in r.text                    # y lo que se escribió, delante


def test_the_prices_screen_keeps_what_was_typed(client):
    _recibir(client, price_kg="")
    pantalla = client.get("/recepcion/precios")
    r = client.post("/recepcion/precios", data={
        "csrf": csrf_from(pantalla.text), "all_price": "treinta",
        "price:8017": "35", "serial": "8017", "envio": "p-2"})
    assert 'value="treinta"' in r.text and 'value="35"' in r.text


def test_the_butchery_sheet_comes_back_with_its_ten_lines(client):
    """Un despiece son diez líneas de números: un error no las borra todas."""
    pantalla = client.get("/despiece")
    r = client.post("/despiece", data={
        "csrf": csrf_from(pantalla.text), "tg": "TG-0001",
        "before_kg": "nueve coma cuatro", "waste_kg": "1,2",
        "cut:0": "Striploin steak", "pieces:0": "20", "total:0": "5",
        "envio": "d-1"})
    assert r.status_code == 200
    assert 'value="nueve coma cuatro"' in r.text
    assert 'value="Striploin steak"' in r.text
    assert 'value="20"' in r.text and 'value="5"' in r.text
    assert 'value="1,2"' in r.text


# ----------------------------------------------- y el móvil abre por arriba
def test_the_reception_screen_does_not_open_a_thousand_pixels_down(client):
    """El cursor iba a los kilos, y con el bloque del lote abierto eso está
    mil píxeles más abajo: en el móvil la pantalla abría ahí, enseñando media
    hoja de nada y sin poder ver siquiera en qué lote se estaba."""
    primera = client.get("/recepcion").text
    assert "autofocus" not in primera             # la primera bolsa, por arriba


def test_but_from_the_second_bag_on_the_cursor_does_help(client):
    """Con el lote ya puesto el bloque viene plegado, los kilos están arriba
    y el cursor ahí ahorra un toque por pieza."""
    despues = _recibir(client, envio="r-1").text
    assert "autofocus" in despues


# ------------------------- recargar no vuelve a mandar lo que ya se guardó
#
# Contestar a un POST con la pantalla entera es lo que hace que recargar
# pregunte «¿reenviar formulario?», y esa pregunta, con una pieza en la mano y
# guantes puestos, no la sabe contestar nadie. Se contesta con una redirección
# y el recado de lo que se guardó viaja aparte, en la sesión.
def test_the_message_of_what_was_saved_survives_the_redirect(client):
    from thegrill.models import AuthSession
    with db.session_scope() as s:
        sesion = s.query(AuthSession).order_by(AuthSession.id.desc()).first()
        sesion.flash = "Merma apuntada · Entrecot · 1,200 kg"

    primera = client.get("/merma").text
    assert "Merma apuntada · Entrecot · 1,200 kg" in primera


def test_and_it_is_shown_once_and_only_once(client):
    """Un recado que se queda pegado a la pantalla miente al día siguiente."""
    from thegrill.models import AuthSession
    with db.session_scope() as s:
        sesion = s.query(AuthSession).order_by(AuthSession.id.desc()).first()
        sesion.flash = "Merma apuntada · Entrecot · 1,200 kg"

    assert "Merma apuntada" in client.get("/merma").text
    assert "Merma apuntada" not in client.get("/merma").text
    with db.session_scope() as s:
        sesion = s.query(AuthSession).order_by(AuthSession.id.desc()).first()
        assert sesion.flash is None
