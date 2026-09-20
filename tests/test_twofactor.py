"""Verificación en dos pasos: los seis dígitos después de la contraseña.

Quien puede bloquear una casa o ver el dinero de todas no debería entrar solo
con una contraseña. Lo que se comprueba aquí: que el motor de los códigos es
el de siempre (TOTP), que con la contraseña sola no se entra a ninguna parte
mientras falten los dígitos, que los códigos de repuesto sirven una vez, y que
perder el teléfono no deja a nadie fuera para siempre.
"""
import time

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.models import AuthSession, Role, User
from thegrill.web import auth, twofactor

SPANISH = {"accept-language": "es"}

from tests import meat_helpers as helpers  # noqa: E402
from tests.meat_helpers import csrf_from  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'tfa.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        helpers.signup(c)
        yield c


# ----------------------------------------------------------------- el motor
def test_the_code_is_the_usual_one_and_lasts_half_a_minute():
    secreto = twofactor.new_secret()
    ahora = 1_700_000_000
    codigo = twofactor.code_at(secreto, ahora)
    assert len(codigo) == 6 and codigo.isdigit()
    assert twofactor.verify(secreto, codigo, when=ahora)
    assert twofactor.verify(secreto, codigo, when=ahora + 5)         # el mismo tramo
    assert twofactor.verify(secreto, codigo, when=ahora + 35)        # el reloj baila un poco
    assert not twofactor.verify(secreto, codigo, when=ahora + 300)   # cinco minutos, no


def test_a_code_from_another_secret_does_not_open_anything():
    uno, otro = twofactor.new_secret(), twofactor.new_secret()
    assert not twofactor.verify(uno, twofactor.code_at(otro))
    assert not twofactor.verify(uno, "000000")
    assert not twofactor.verify(uno, "")


def test_the_uri_is_what_the_phone_app_reads():
    secreto = twofactor.new_secret()
    uri = twofactor.uri(secreto, "ana@marina.com", issuer="Control de carnes")
    assert uri.startswith("otpauth://totp/")
    assert secreto in uri and "period=30" in uri and "digits=6" in uri


def test_a_recovery_code_works_once():
    codigos = twofactor.new_recovery_codes()
    guardado = twofactor.store_recovery(codigos)
    assert twofactor.recovery_left(guardado) == 6
    assert codigos[0] not in guardado          # se guarda la huella, no el código

    valia, guardado = twofactor.spend_recovery(guardado, codigos[0])
    assert valia and twofactor.recovery_left(guardado) == 5
    assert twofactor.spend_recovery(guardado, codigos[0])[0] is False   # de un solo uso
    assert twofactor.spend_recovery(guardado, "NOEXISTE12")[0] is False


# ------------------------------------------------------------- la entrada
def enciende(client, email="albano@marina.com"):
    """Activa los dos pasos para esa cuenta y devuelve su secreto."""
    pantalla = client.get("/configuracion")
    with db.session_scope() as s:
        secreto = s.query(User).filter_by(email=email).one().totp_secret
    r = client.post("/configuracion/2fa/activar",
                    data={"csrf": csrf_from(pantalla.text),
                          "code": twofactor.code_at(secreto)})
    assert r.status_code == 303 and "codes=" in r.headers["location"]
    return secreto, r.headers["location"].split("codes=")[1].split("-")


def test_turning_it_on_needs_a_code_from_the_phone(client):
    pantalla = client.get("/configuracion")
    assert "Verificación en dos pasos" in pantalla.text

    malo = client.post("/configuracion/2fa/activar",
                       data={"csrf": csrf_from(pantalla.text), "code": "000000"})
    assert "error=" in malo.headers["location"]
    with db.session_scope() as s:
        assert not s.query(User).filter_by(email="albano@marina.com").one().totp_enabled

    secreto, codigos = enciende(client)
    assert len(codigos) == 6
    with db.session_scope() as s:
        usuario = s.query(User).filter_by(email="albano@marina.com").one()
        assert usuario.totp_enabled and usuario.totp_secret == secreto


def test_with_the_password_alone_you_are_still_outside(client):
    """La contraseña abre la puerta de los dígitos y nada más."""
    secreto, _ = enciende(client)
    client.cookies.clear()

    entrada = client.post("/login", data={"email": "albano@marina.com",
                                          "password": "clave-larga-1"})
    assert entrada.status_code == 303
    assert entrada.headers["location"] == "/acceso/verificacion"

    # La sesión existe, pero no sirve para nada más.
    assert client.get("/hoy").headers["location"] == "/login"
    assert client.get("/carne").headers["location"] == "/login"
    with db.session_scope() as s:
        assert s.query(AuthSession).filter_by(pending_2fa=True).count() == 1

    pantalla = client.get("/acceso/verificacion")
    assert pantalla.status_code == 200 and "Código" in pantalla.text
    dentro = client.post("/acceso/verificacion",
                         data={"csrf": csrf_from(pantalla.text),
                               "code": twofactor.code_at(secreto)})
    assert dentro.status_code == 303 and dentro.headers["location"] == "/hoy"
    assert client.get("/hoy").status_code == 200


def test_a_wrong_code_does_not_let_anybody_in(client):
    secreto, _ = enciende(client)
    client.cookies.clear()
    client.post("/login", data={"email": "albano@marina.com", "password": "clave-larga-1"})
    pantalla = client.get("/acceso/verificacion")
    r = client.post("/acceso/verificacion",
                    data={"csrf": csrf_from(pantalla.text), "code": "123456"})
    assert r.status_code == 200 and "no vale" in r.text
    assert client.get("/hoy").headers["location"] == "/login"


def test_a_recovery_code_gets_you_in_when_the_phone_is_gone(client):
    secreto, codigos = enciende(client)
    client.cookies.clear()
    client.post("/login", data={"email": "albano@marina.com", "password": "clave-larga-1"})
    pantalla = client.get("/acceso/verificacion")

    dentro = client.post("/acceso/verificacion",
                         data={"csrf": csrf_from(pantalla.text), "code": codigos[0]})
    assert dentro.status_code == 303 and client.get("/hoy").status_code == 200
    with db.session_scope() as s:
        usuario = s.query(User).filter_by(email="albano@marina.com").one()
        assert twofactor.recovery_left(usuario.recovery_codes) == 5   # se ha gastado


def test_taking_it_off_needs_the_password(client):
    enciende(client)
    pantalla = client.get("/configuracion")
    malo = client.post("/configuracion/2fa/quitar",
                       data={"csrf": csrf_from(pantalla.text), "password": "no-es-esta"})
    assert "error=" in malo.headers["location"]
    with db.session_scope() as s:
        assert s.query(User).filter_by(email="albano@marina.com").one().totp_enabled

    bueno = client.post("/configuracion/2fa/quitar",
                        data={"csrf": csrf_from(pantalla.text), "password": "clave-larga-1"})
    assert "off=1" in bueno.headers["location"]
    with db.session_scope() as s:
        usuario = s.query(User).filter_by(email="albano@marina.com").one()
        assert not usuario.totp_enabled and usuario.recovery_codes is None


def test_the_manager_can_clear_it_for_whoever_lost_the_phone(client):
    """Perder el teléfono no puede dejar a nadie fuera para siempre."""
    luis = helpers.add_user(client, email="luis@marina.com", name="Luis", role=Role.BUTCHER)
    secreto, _ = enciende(luis, email="luis@marina.com")
    with db.session_scope() as s:
        luis_id = s.query(User).filter_by(email="luis@marina.com").one().id

    equipo = client.get("/manager/equipo")
    assert "Quitar 2 pasos" in equipo.text
    r = client.post(f"/manager/equipo/{luis_id}/2fa", data={"csrf": csrf_from(equipo.text)})
    assert r.status_code == 303 and "done=" in r.headers["location"]

    with db.session_scope() as s:
        usuario = s.get(User, luis_id)
        assert not usuario.totp_enabled and usuario.totp_secret is None


def test_guessing_the_six_digits_gets_you_braked(client):
    secreto, _ = enciende(client)
    client.cookies.clear()
    client.post("/login", data={"email": "albano@marina.com", "password": "clave-larga-1"})
    pantalla = client.get("/acceso/verificacion")
    token = csrf_from(pantalla.text)

    from thegrill.meat import security
    for _ in range(security.LOGIN_ATTEMPTS):
        client.post("/acceso/verificacion", data={"csrf": token, "code": "123456"})
    frenado = client.post("/acceso/verificacion",
                          data={"csrf": token, "code": twofactor.code_at(secreto)})
    assert "Demasiados intentos" in frenado.text or "intentos" in frenado.text
    assert client.get("/hoy").headers["location"] == "/login"
