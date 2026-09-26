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
             "origin": "AUS", "price:0": "32", "serial:0": "8017", "g:0": "9400"}
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
    r = _recibir(client, **{"g:0": "nueve"})
    assert r.status_code == 200 and "nueve" in r.text
    with db.session_scope() as s:
        assert s.query(Primal).count() == 0


# ------------------------------------------- lo tecleado vuelve donde estaba
def test_an_error_no_longer_wipes_the_sheet(client):
    """Un cero de más en una casilla obligaba a teclear la hoja otra vez."""
    r = _recibir(client, arrival_c="4 C", **{"serial:0": "8099", "g:0": "12345",
                                             "sku:0": "Ribeye", "slot:0": "LOTE-7"})
    assert 'value="8099"' in r.text              # el número de la pieza
    assert 'value="12345"' in r.text             # su peso, como se escribió
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
    # El cartel rojo lleva ahora `role="alert"` y `tabindex="-1"`, para que un
    # lector de pantalla lo cante y para poder llevarle el cursor. Buscarlo
    # exigiendo que la etiqueta acabe justo después de la clase dejaba la
    # prueba en blanco y el fallo parecía del programa.
    import re
    hallado = re.findall(r'<div class="banner bad"[^>]*>(.*?)</div>', html, re.S)
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
    assert primera.status_code == 303          # guardar redirige: recargar no reenvía
    otra_vez = _recibir(client, envio="llave-de-la-pantalla")
    assert otra_vez.status_code in (200, 303)
    with db.session_scope() as s:
        assert s.query(Primal).count() == 1


def _merma(client, envio, **extra):
    pantalla = client.get("/merma")
    datos = {"csrf": csrf_from(pantalla.text), "g": "1200", "serial": "",
             "reason": "Caducado", "envio": envio}
    datos.update(extra)
    return client.post("/merma", data=datos)


def test_the_waste_form_keeps_what_was_typed(client):
    """Un error en los kilos no puede borrar el motivo ni las piezas."""
    r = _merma(client, "abc-125", serial="8017", g="dos kilos",
               pieces="3", reason="Se cayó al suelo")
    assert r.status_code == 200
    assert 'value="dos kilos"' in r.text
    assert 'value="3"' in r.text
    assert 'value="Se cayó al suelo"' in r.text
    assert 'value="8017"' in r.text


# ------------------------------------ las dos pantallas que quedaban del bloque
def test_a_bad_price_for_all_no_longer_takes_the_screen_down(client):
    """El mismo fallo que el «4 C», en los precios: se leía fuera del `try`."""
    _recibir(client, **{"price:0": ""})                # entra sin precio
    pantalla = client.get("/recepcion/precios")
    r = client.post("/recepcion/precios", data={
        "csrf": csrf_from(pantalla.text), "all_price": "32 eur",
        "serial": "8017", "envio": "p-1"})
    assert r.status_code == 200                  # ni 500 ni pantalla en blanco
    assert "32 eur" in r.text                    # y lo que se escribió, delante


def test_the_prices_screen_keeps_what_was_typed(client):
    _recibir(client, **{"price:0": ""})
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
        "before_g": "nueve coma cuatro", "waste_g": "1200",
        "cut:0": "Striploin steak", "pieces:0": "20", "total:0": "5000",
        "envio": "d-1"})
    assert r.status_code == 200
    assert 'value="nueve coma cuatro"' in r.text
    assert 'value="Striploin steak"' in r.text
    assert 'value="20"' in r.text and 'value="5000"' in r.text
    assert 'value="1200"' in r.text               # y la merma, en gramos


# ----------------------------------------------- y el móvil abre por arriba
def test_the_reception_screen_does_not_open_a_thousand_pixels_down(client):
    """El cursor iba a los kilos, y con el bloque del lote abierto eso está
    mil píxeles más abajo: en el móvil la pantalla abría ahí, enseñando media
    hoja de nada y sin poder ver siquiera en qué lote se estaba."""
    import re
    # Sin los guiones: en sus comentarios se habla de `autofocus` —de por qué
    # no está—, y buscar la palabra a pelo en toda la página encontraba el
    # comentario y daba por puesto un atributo que no está en ninguna casilla.
    primera = re.sub(r"<script.*?</script>", "", client.get("/recepcion").text,
                     flags=re.S)
    assert "autofocus" not in primera             # la primera bolsa, por arriba


def test_but_from_the_second_bag_on_the_cursor_does_help(client):
    """Con el lote ya puesto el bloque viene plegado, los kilos están arriba
    y el cursor ahí ahorra un toque por pieza."""
    assert _recibir(client, envio="r-1").status_code == 303
    despues = client.get("/recepcion").text    # el lote se quedó puesto
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


def test_a_message_never_shows_up_on_a_screen_it_was_not_meant_for(client):
    """Un recado de la recepción en la hoja del despiece confunde y miente.

    Con la redirección, el recado espera en la sesión a que se pida la
    pantalla. Si quien lo guardó se va a otra sin pasar por ella —del muelle
    directo a la mesa de despiece— el recado no puede aparecer allí: habla de
    una pieza que se acaba de dar de alta y en esa hoja no significa nada.
    Perderlo es mejor que enseñarlo donde no toca.
    """
    assert _recibir(client, envio="r-2").status_code == 303
    # «8017» sí sale en el despiece: es la pieza, que está esperando a que la
    # corten. Lo que no puede salir es el recado de que se acaba de dar de alta.
    assert "dado de alta" not in client.get("/despiece").text

    # Y tampoco se queda esperando a la próxima vez que se entre en recepción.
    assert "dado de alta" not in client.get("/recepcion").text


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


# ------------------------------- el peso, en gramos, y la cola de ayer
def test_the_weight_box_asks_for_grams_and_echoes_the_kilos(client):
    """Gramos dentro, kilos debajo: el eco es lo que caza un cero de más."""
    pantalla = client.get("/recepcion").text
    assert 'name="g:0"' in pantalla and 'name="kg:0"' not in pantalla
    assert "data-peso" in pantalla and "data-eco" in pantalla


def test_a_sheet_queued_before_the_change_is_still_booked_in_kilos(client):
    """El teléfono que se quedó sin cobertura con la pantalla vieja abierta.

    Guarda el formulario tal cual y lo manda horas o días después. Si ese
    «kg:0=9,4» se leyera como gramos quedarían apuntados nueve gramos: mil
    veces menos, en silencio. Como el nombre cambió, no hay confusión posible
    —una hoja de ahora nunca trae `kg:0`— y la pieza queda bien apuntada.
    """
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L-AYER", "sku": "Striploin",
        "price:0": "32", "serial:0": "8050", "kg:0": "9,4",
        "envio": "de-la-cola-de-ayer"})
    assert r.status_code == 303
    with db.session_scope() as s:
        assert s.query(Primal).filter_by(serial="8050").one().weight_kg == 9.4


def test_grams_with_a_comma_say_which_figure_to_write(client):
    """«9,4» en gramos no existe: casi siempre son 9,4 kg mal puestos."""
    r = _recibir(client, **{"serial:0": "8060", "g:0": "9,4"})
    assert r.status_code == 200
    assert "9400" in r.text                       # la cifra que hay que poner
    with db.session_scope() as s:
        assert s.query(Primal).filter_by(serial="8060").count() == 0


def test_a_count_line_that_cannot_be_written_is_said_out_loud(client):
    """Una hoja de inventario manda cincuenta líneas: una mala no tumba el resto.

    Pero callarla es peor que tumbarlas. La pieza se queda «sin contar», quien
    la acaba de pesar se ha ido de la cámara convencido de que la contó, y en
    el cierre aparece como que falta. El recuento se guarda a medias y nadie
    lo sabe hasta el cuadre.
    """
    from thegrill.models import MeatCount
    _recibir(client, **{"serial:0": "8070", "g:0": "9400"})
    pantalla = client.get("/inventario")
    client.post("/inventario/abrir",
                data={"csrf": csrf_from(pantalla.text), "period": "MONTHLY"})
    pantalla = client.get("/inventario")

    # Una en gramos de verdad y otra escrita con coma, que en gramos no existe.
    r = client.post("/inventario/contar", data={
        "csrf": csrf_from(pantalla.text), "g:8070": "9,4"})
    assert r.status_code == 303
    pantalla = client.get("/inventario").text
    # Y se dice: con la pieza y con la cifra que había que escribir.
    assert "8070" in pantalla
    assert "9400" in pantalla, "no se dice qué había que escribir"
    assert "banner warn" in pantalla, "la línea se perdió en silencio"

    with db.session_scope() as s:
        hoja = s.query(MeatCount).order_by(MeatCount.id.desc()).first()
        linea = next((l for l in hoja.lines if l.serial == "8070"), None)
        if linea is not None:
            assert linea.counted_kg is None, "se apuntó un peso que no se entendía"


# ------------------------------- el número que no cabe, y el que no es número
def test_a_sheet_of_paper_in_the_site_box_does_not_take_the_inventory_down(client):
    """Abrir inventario leía la sede con `int()` a pelo, y fuera del `try`.

    Cualquier cosa que no fuera un número —una fecha pegada en la casilla
    equivocada, el nombre del obrador escrito a mano— no daba «esa sede no
    está»: tumbaba la pantalla con un error del servidor, con el inventario a
    medio abrir. Es la misma enfermedad del «4 C» de la temperatura, en una
    ruta que no se había barrido.
    """
    duro = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH,
                      raise_server_exceptions=False)
    duro.cookies = client.cookies
    token = csrf_from(client.get("/inventario").text)
    for basura in ("obrador", "2026-13-45", "-4", "1,5", "abc"):
        r = duro.post("/inventario/abrir",
                      data={"csrf": token, "site": basura, "period": "MONTHLY"})
        assert r.status_code < 500, f"«{basura}» tumbó la pantalla: {r.status_code}"


def test_an_identifier_that_does_not_fit_in_the_database_is_not_an_identifier(client):
    """Cuarenta nueves no son un identificador grande: no son un identificador.

    Python no tiene techo para sus enteros, así que el número pasaba entero
    hasta el conector de la base, y el que avisaba era él: reventando la
    pantalla al ir a buscar una fila que no puede existir. La respuesta
    correcta es la misma que para lo que no existe.
    """
    duro = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH,
                      raise_server_exceptions=False)
    duro.cookies = client.cookies
    token = csrf_from(client.get("/carta").text)
    enorme = "9" * 40
    r = duro.post("/carta/nuevo", data={"csrf": token, "name": "Chuleton",
                                        "cut_id": enorme, "grams": "300"})
    assert r.status_code < 500, f"por la casilla: {r.status_code}"

    r = duro.post(f"/manager/equipo/{enorme}/activar", data={"csrf": token})
    assert r.status_code < 500, f"por la dirección: {r.status_code}"


def test_no_route_takes_a_raw_identifier():
    """La guardia: una ruta nueva con `x_id: int` a secas vuelve a abrir el agujero.

    Se arregló en las cuarenta y tantas que había; lo que mantiene esto
    cerrado es que la número cuarenta y cinco no pueda escribirse sin el tope.

    Se lee el árbol del programa y no el texto: lo que importa es si el
    parámetro de una **ruta** lleva su límite, y eso no se ve con una
    expresión regular sin equivocarse con los ayudantes de dentro.
    """
    import ast
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parents[1]
    sueltos = []
    for fichero in (raiz / "thegrill/meat/app.py", raiz / "thegrill/web/app.py"):
        arbol = ast.parse(fichero.read_text())
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            es_ruta = any("app." in ast.unparse(d) and "(" in ast.unparse(d)
                          for d in nodo.decorator_list)
            if not es_ruta:
                continue
            args = nodo.args
            todos = list(zip(args.args[-len(args.defaults):] if args.defaults else [],
                             args.defaults))
            todos += list(zip(args.kwonlyargs, args.kw_defaults))
            sin_defecto = args.args[:len(args.args) - len(args.defaults)]
            for arg in sin_defecto:
                if arg.arg.endswith("_id") and arg.annotation and \
                        ast.unparse(arg.annotation) == "int":
                    sueltos.append(f"{fichero.name} {nodo.name}({arg.arg}) sin tope")
            for arg, defecto in todos:
                if not arg.arg.endswith("_id") or not arg.annotation:
                    continue
                if ast.unparse(arg.annotation) != "int":
                    continue
                texto = ast.unparse(defecto) if defecto is not None else ""
                if "TOPE_ID" not in texto:
                    sueltos.append(f"{fichero.name} {nodo.name}({arg.arg}) = {texto or 'sin defecto'}")
    assert not sueltos, "identificadores de ruta sin tope:\n" + "\n".join(sueltos)
