"""El parte de fallos: que lo cuente quien lo sufre.

Ninguna prueba ve lo que ve un carnicero a las siete de la mañana. Esto es el
botón para contarlo sin salir del programa y sin escribir un correo, y la
pantalla donde la plataforma los lee y dice en qué han quedado.
"""
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import bugs
from thegrill.web import i18n
from thegrill.models import BugReport, BugStatus, Restaurant, Role, User
from tests.meat_helpers import SPANISH, add_user, csrf_from, login, signup

HOY = date.today()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    monkeypatch.delenv("GRILL_BUGS_EMAIL", raising=False)
    monkeypatch.delenv("GRILL_MAIL_TO", raising=False)
    db.init_engine(f"sqlite:///{tmp_path/'fallos.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        signup(c)
        yield c


def cuenta(client, texto="Cerré el turno y el descuadre salió de 4 kg sin motivo.",
           kind="fallo", desde="/descongelado"):
    pagina = client.get(f"/fallo?desde={desde}")
    assert pagina.status_code == 200
    return client.post("/fallo", data={"message": texto, "kind": kind, "screen": desde,
                                       "csrf": csrf_from(pagina.text)})


# ------------------------------------------------------------ contar un fallo
def test_anyone_can_report_from_the_screen_they_are_on(client):
    respuesta = cuenta(client)
    assert respuesta.status_code == 303

    with db.session_scope() as s:
        parte = s.query(BugReport).one()
        assert parte.screen == "/descongelado"
        assert parte.status == BugStatus.NEW and parte.kind == "fallo"
        assert "4 kg" in parte.message
        # Lo que hace falta para repetirlo, y nada más.
        assert "idioma es" in parte.detail and "Hotel Marina" in parte.detail
        assert parte.reporter.startswith("Albano")


def test_the_report_is_kept_even_when_there_is_no_mail(client):
    """El correo es el aviso, no el registro."""
    respuesta = cuenta(client)
    assert respuesta.headers["location"].startswith("/fallo?done=")
    with db.session_scope() as s:
        parte = s.query(BugReport).one()
        assert parte.mailed is False


def test_two_words_are_not_a_report(client):
    pagina = client.get("/fallo")
    respuesta = client.post("/fallo", data={"message": "no va", "kind": "fallo",
                                            "csrf": csrf_from(pagina.text)})
    assert respuesta.status_code == 303 and "error=" in respuesta.headers["location"]
    with db.session_scope() as s:
        assert s.query(BugReport).count() == 0


def test_an_assistant_can_report_too(client):
    """El que ve el fallo es el que está delante, y no suele ser el manager."""
    eva = add_user(client, email="eva@marina.com", name="Eva", role=Role.EMPLOYEE)
    respuesta = cuenta(eva, texto="La pantalla de merma se queda en blanco con mi teclado.")
    assert respuesta.status_code == 303
    with db.session_scope() as s:
        parte = s.query(BugReport).one()
        assert "Eva" in parte.reporter and "EMPLOYEE" in parte.reporter


def test_the_house_sees_what_it_reported_and_what_was_done(client):
    cuenta(client)
    with db.session_scope() as s:
        bugs.set_status(s, 1, BugStatus.FIXED, note="Arreglado en la versión de mayo.")

    pagina = client.get("/fallo").text
    assert "Arreglado" in pagina and "versión de mayo" in pagina


def test_a_report_travels_to_the_mailbox_when_there_is_one(client, monkeypatch):
    enviados = []
    monkeypatch.setenv("GRILL_BUGS_EMAIL", "fallos@plataforma.com")
    monkeypatch.setattr("thegrill.meat.mailer.send",
                        lambda subject, body, reply_to=None: enviados.append(
                            (subject, body, reply_to)) or True)

    cuenta(client, texto="El parte del día sale sin los turnos cerrados.")

    assert len(enviados) == 1
    asunto, cuerpo, responder = enviados[0]
    assert "fallo #1" in asunto
    assert "/descongelado" in cuerpo and "Hotel Marina" in cuerpo
    assert responder == "albano@marina.com"
    with db.session_scope() as s:
        assert s.query(BugReport).one().mailed is True


# --------------------------------------------------- la bandeja de la plataforma
def test_only_the_platform_reads_the_reports(client):
    cuenta(client)
    assert client.get("/admin/fallos").status_code == 403


def test_the_platform_reads_them_and_says_what_it_did(client):
    cuenta(client, texto="Al cerrar el inventario del local se cuenta el obrador.")
    dueno = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    login(dueno, "dueno@plataforma.com", "clave-plataforma-1")

    bandeja = dueno.get("/admin/fallos")
    assert bandeja.status_code == 200
    assert "Al cerrar el inventario" in bandeja.text
    assert "Hotel Marina" in bandeja.text

    guardado = dueno.post("/admin/fallos/1/estado",
                          data={"estado": "FIXED", "note": "Era la sede del conteo.",
                                "csrf": csrf_from(bandeja.text)})
    assert guardado.status_code == 303
    with db.session_scope() as s:
        parte = s.query(BugReport).one()
        assert parte.status == BugStatus.FIXED and "sede del conteo" in parte.note


def test_the_inbox_can_be_filtered_by_state(client):
    cuenta(client)
    cuenta(client, texto="Otra cosa distinta que tampoco va bien del todo.")
    with db.session_scope() as s:
        bugs.set_status(s, 1, BugStatus.FIXED)

    dueno = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    login(dueno, "dueno@plataforma.com", "clave-plataforma-1")
    nuevos = dueno.get("/admin/fallos?estado=NEW").text
    assert "Otra cosa distinta" in nuevos
    assert "Cerré el turno" not in nuevos


def test_the_button_is_on_every_screen(client):
    for ruta in ("/hoy", "/carne", "/maduracion", "/ventas", "/inventario"):
        assert "/fallo?desde=" in client.get(ruta).text


# ------------------------------------- volver al principio, que es lo primero
def test_the_name_always_goes_back_to_the_start(client):
    """Pulsar el nombre del programa y que no pase nada es perderse.

    En las páginas públicas el nombre era un rótulo y no un enlace, así que
    desde Precios o Cookies no había manera de volver sin la flecha del
    navegador. Dentro del programa pasaba lo mismo.
    """
    import re

    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    for ruta in ("/precios", "/cookies", "/solicitar"):
        html = fuera.get(ruta).text
        marca = re.search(r'<a class="brand" href="([^"]+)"', html)
        assert marca, f"{ruta}: el nombre no es un enlace"
        assert marca.group(1) == "/", ruta
        assert 'href="/">' in html            # y además hay un «Inicio» en la barra

    dentro = client.get("/carne").text
    marca = re.search(r'<a class="brand" href="([^"]+)"', dentro)
    assert marca and marca.group(1) == "/hoy"


def test_the_public_pages_link_to_each_other(client):
    """Desde cualquiera de ellas se llega a las demás sin volver atrás a ciegas."""
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    for ruta in ("/precios", "/cookies", "/solicitar"):
        html = fuera.get(ruta).text
        for destino in ("/precios", "/solicitar", "/login", "/cookies", "/"):
            assert f'href="{destino}"' in html, (ruta, destino)


def test_you_can_always_get_back_from_the_login_screen(client):
    """Entrar y arrepentirse: la pantalla de entrar también tiene salida."""
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    # En esta edición nadie se registra solo: /join lleva a /login, y es ahí
    # donde tiene que haber salida.
    assert 'href="/"' in fuera.get("/login").text


# ------------------------------------- el aviso de cookies, recordado de verdad
def test_the_notice_is_remembered_by_the_server_not_by_the_browser(client):
    """Lo que se guardaba en el navegador se borraba al cerrar y no pasaba de ventana.

    Con una cookie técnica de un año, «entendido» significa entendido: en esta
    página, en la siguiente y mañana.
    """
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    assert 'id="cookiebar"' in fuera.get("/").text

    visto = fuera.post("/cookies/visto", data={"next": "/precios"})
    assert visto.status_code == 303 and visto.headers["location"] == "/precios"

    for ruta in ("/", "/precios", "/cookies", "/login"):
        assert 'id="cookiebar"' not in fuera.get(ruta).text, ruta


def test_the_notice_does_not_need_javascript(client):
    """Un teléfono viejo o un navegador estricto también tienen que poder cerrarlo."""
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    html = fuera.get("/").text
    barra = html[html.index('id="cookiebar"'):html.index('id="cookiebar"') + 600]
    assert 'action="/cookies/visto"' in barra and "<script" not in barra


def test_the_way_back_cannot_be_sent_somewhere_else(client):
    """El «volver a donde estabas» no puede llevar a otra web."""
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    for malo in ("//evil.example", "https://evil.example", "javascript:alert(1)"):
        respuesta = fuera.post("/cookies/visto", data={"next": malo})
        assert respuesta.headers["location"] == "/", malo


def test_login_and_cookies_open_as_windows_on_the_public_pages(client):
    """Entrar sin salir de la portada, y el detalle de las cookies sin cambiar de página."""
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    portada = fuera.get("/").text
    assert 'id="entrar"' in portada and 'id="cookies-info"' in portada
    assert 'href="#entrar"' in portada
    # Y las direcciones de siempre siguen existiendo para quien llegue directo.
    assert fuera.get("/login").status_code == 200
    assert fuera.get("/cookies").status_code == 200


def test_every_public_page_wears_the_same_clothes(client):
    """Precios, Cookies, Solicitar y Entrar son la misma casa que la portada.

    Antes heredaban el diseño de la pantalla de trabajo y parecían otro
    programa, que es lo que hace dudar a quien va a escribir su contraseña.
    """
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    portada = fuera.get("/").text
    for ruta in ("/precios", "/cookies", "/solicitar", "/login"):
        html = fuera.get(ruta).text
        assert "--ember" in html, ruta                      # la misma paleta
        assert 'class="brand"' in html, ruta                # la misma cabecera
        assert "<footer>" in html, ruta                     # y el mismo pie
        assert 'href="/solicitar"' in html, ruta
    assert "--ember" in portada


# ------------------------- lo que se rellena solo y lo que se explica al pasar
def test_the_lot_and_the_piece_numbers_fill_themselves_in(client):
    """En el muelle, con el camión esperando, nadie inventa un código.

    Se proponen; se cogen de verdad al dar de alta, no al abrir la pantalla.
    """
    from thegrill import db
    from thegrill.meat import service as meat
    from thegrill.models import Primal, Restaurant, User

    pagina = client.get("/recepcion").text
    assert 'name="lot" value="L-' in pagina                # propuesto, no en blanco

    alta = client.post("/recepcion", data={
        "lot": "", "sku": "Striploin", "g:0": "9400", "price:0": "30",
        "g:1": "8200", "price:1": "30", "csrf": csrf_from(pagina)})
    assert alta.status_code in (200, 303), alta.text[:200]

    with db.session_scope() as s:
        piezas = s.query(Primal).order_by(Primal.id).all()
        assert len(piezas) == 2
        assert all(p.serial.isdigit() for p in piezas), [p.serial for p in piezas]
        assert int(piezas[1].serial) == int(piezas[0].serial) + 1   # la serie sigue
        assert piezas[0].lot and piezas[0].lot.startswith("L-")
        assert piezas[0].lot == piezas[1].lot                       # un lote común

        # Y la siguiente propuesta ya cuenta con las que acaban de entrar.
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        siguiente = meat.next_serials(s, rest.id, 1)[0]
        assert int(siguiente) == int(piezas[1].serial) + 1


def test_a_number_written_by_hand_wins_over_the_proposed_one(client):
    """Si la pieza viene numerada de fábrica, manda la etiqueta."""
    from thegrill import db
    from thegrill.models import Primal

    pagina = client.get("/recepcion").text
    client.post("/recepcion", data={"lot": "ALB-77", "sku": "Ribeye",
                                    "serial:0": "AUS-9001", "g:0": "9400", "price:0": "30",
                                    "g:1": "7100", "price:1": "30",
                                    "csrf": csrf_from(pagina)})
    with db.session_scope() as s:
        seriales = {p.serial for p in s.query(Primal)}
        assert "AUS-9001" in seriales and len(seriales) == 2
        assert s.query(Primal).filter_by(serial="AUS-9001").one().lot == "ALB-77"


def test_opening_the_screen_does_not_burn_a_number(client):
    """Abrir y cerrar la pantalla no puede dejar huecos en la serie."""
    from thegrill import db
    from thegrill.meat import service as meat
    from thegrill.models import Restaurant

    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        antes = meat.next_serials(s, rest.id, 1)[0]
    for _ in range(3):
        client.get("/recepcion")
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert meat.next_serials(s, rest.id, 1)[0] == antes


def test_every_column_of_the_butchery_explains_itself(client):
    """«Índice de valor» no lo entiende nadie la primera vez, y hay que decirlo."""
    html = client.get("/despiece").text
    for clave in ("m.tg.h_cut", "m.tg.h_article", "m.tg.h_pieces", "m.tg.h_grams",
                  "m.tg.h_by_weight", "m.tg.h_index", "m.tg.h_trim"):
        texto = i18n.t("es", clave)
        assert texto in html, clave
    # Al pasar por encima y también abierto, que en un móvil no se pasa por encima.
    assert 'title="' + i18n.t("es", "m.tg.h_index")[:20] in html
    assert i18n.t("es", "m.tg.what_is_what") in html


def test_the_butchery_history_says_what_came_out_and_how_many(client):
    """Una lista de despieces sin los cortes ni las piezas no dice nada."""
    from thegrill import db
    from thegrill.models import Ingredient, Primal, Restaurant, User
    from thegrill.meat import service as meat

    pagina = client.get("/recepcion").text
    client.post("/recepcion", data={"lot": "", "sku": "Striploin", "g:0": "9000",
                                    "price:0": "30", "use_by": "2026-12-31",
                                    "csrf": csrf_from(pagina)})
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        ana = s.query(User).filter_by(restaurant_id=rest.id).first()
        corte = meat.create_cut(s, ana, "Entrecot")
        articulo = meat.add_article(s, ana, corte, "Entrecot AUS")
        pieza = s.query(Primal).one()
        item_id, serial = articulo.id, pieza.serial

    despiece = client.get("/despiece").text
    client.post("/despiece", data={"tg": "TG-0001", "primal": serial, "before_g": "9000",
                                   "cut:0": "Entrecot", "item:0": str(item_id),
                                   "pieces:0": "12", "grams:0": "650",
                                   "waste_g": "1200", "csrf": csrf_from(despiece)})

    historia = client.get("/despiece").text
    assert "Entrecot (12)" in historia          # qué salió y cuántas piezas
    assert i18n.t("es", "m.tg.what_came_out") in historia
