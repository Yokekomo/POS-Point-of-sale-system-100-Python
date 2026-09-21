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
