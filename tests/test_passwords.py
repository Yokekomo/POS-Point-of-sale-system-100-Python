"""Cambiar la contraseña: la propia, y la de quien la ha olvidado.

Una contraseña que no se puede cambiar es una cuenta que se abandona el día
que alguien la olvida. Aquí se comprueban las tres cosas que hacen falta: que
uno pueda cambiar la suya sabiendo la de antes, que el manager pueda ponerle
una nueva a su gente, y que cambiarla eche de las sesiones abiertas — porque
media razón para cambiarla es que alguien entró con la vieja.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.models import AuthSession, Restaurant, Role, User
from thegrill.web import auth

# El navegador de estas pruebas habla español: los textos que se comprueban
# abajo son los españoles. Quien llega sin decir nada recibe inglés.
SPANISH = {"accept-language": "es"}

from tests import meat_helpers as helpers  # noqa: E402
from tests.meat_helpers import csrf_from  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'pass.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        helpers.signup(c)
        yield c


# ------------------------------------------------------------- el motor
def test_changing_it_needs_the_old_one(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'a.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Asador", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        with pytest.raises(auth.AuthError):
            auth.set_password(s, ana, "clave-larga-2", current="la-que-no-es")
        auth.set_password(s, ana, "clave-larga-2", current="clave-larga-1")
        assert auth.verify_password("clave-larga-2", ana.password_hash)


def test_a_short_password_is_refused(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'b.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Asador", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        antes = ana.password_hash
        with pytest.raises(ValueError):
            auth.set_password(s, ana, "corta", current="clave-larga-1")
        assert ana.password_hash == antes


def test_changing_it_throws_the_other_sessions_out(tmp_path):
    """Si alguien había entrado con la vieja, deja de estar dentro."""
    db.init_engine(f"sqlite:///{tmp_path/'c.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Asador", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        mia, _ = auth.start_session(s, ana)
        ajena, _ = auth.start_session(s, ana)
        assert s.query(AuthSession).filter_by(user_id=ana.id).count() == 2

        auth.set_password(s, ana, "clave-larga-2", current="clave-larga-1",
                          close_others=mia)
        assert s.query(AuthSession).filter_by(user_id=ana.id).count() == 1
        assert auth.resolve_session(s, ajena) is None      # la otra, fuera
        assert auth.resolve_session(s, mia) is not None    # la que la cambió, dentro


# ---------------------------------------------------------- las pantallas
def test_anyone_can_change_their_own_from_settings(client):
    luis = helpers.add_user(client, email="luis@marina.com", name="Luis", role=Role.BUTCHER)
    pantalla = luis.get("/configuracion")
    assert "Contraseña actual" in pantalla.text

    r = luis.post("/configuracion/contrasena",
                  data={"csrf": csrf_from(pantalla.text), "current": "clave-larga-2",
                        "new": "clave-nueva-9", "repeat": "clave-nueva-9"})
    assert r.status_code == 303 and "changed=1" in r.headers["location"]

    with db.session_scope() as s:
        usuario = s.query(User).filter_by(email="luis@marina.com").one()
        assert auth.verify_password("clave-nueva-9", usuario.password_hash)


def test_the_wrong_current_one_changes_nothing(client):
    luis = helpers.add_user(client, email="luis@marina.com", name="Luis", role=Role.BUTCHER)
    pantalla = luis.get("/configuracion")
    r = luis.post("/configuracion/contrasena",
                  data={"csrf": csrf_from(pantalla.text), "current": "no-es-esta",
                        "new": "clave-nueva-9", "repeat": "clave-nueva-9"})
    assert r.status_code == 303 and "error=" in r.headers["location"]
    with db.session_scope() as s:
        usuario = s.query(User).filter_by(email="luis@marina.com").one()
        assert auth.verify_password("clave-larga-2", usuario.password_hash)


def test_two_different_new_ones_do_nothing(client):
    luis = helpers.add_user(client, email="luis@marina.com", name="Luis", role=Role.BUTCHER)
    pantalla = luis.get("/configuracion")
    r = luis.post("/configuracion/contrasena",
                  data={"csrf": csrf_from(pantalla.text), "current": "clave-larga-2",
                        "new": "clave-nueva-9", "repeat": "clave-nueva-8"})
    assert "error=" in r.headers["location"]
    with db.session_scope() as s:
        usuario = s.query(User).filter_by(email="luis@marina.com").one()
        assert auth.verify_password("clave-larga-2", usuario.password_hash)


def test_the_manager_sets_a_new_one_for_whoever_forgot_it(client):
    helpers.add_user(client, email="luis@marina.com", name="Luis", role=Role.BUTCHER)
    with db.session_scope() as s:
        luis_id = s.query(User).filter_by(email="luis@marina.com").one().id

    equipo = client.get("/manager/equipo")
    assert "Ponerla" in equipo.text
    r = client.post(f"/manager/equipo/{luis_id}/contrasena",
                    data={"csrf": csrf_from(equipo.text), "password": "clave-puesta-7"})
    assert r.status_code == 303 and "done=" in r.headers["location"]

    with db.session_scope() as s:
        luis = s.get(User, luis_id)
        assert auth.verify_password("clave-puesta-7", luis.password_hash)
        assert s.query(AuthSession).filter_by(user_id=luis_id).count() == 0   # fuera


def test_a_butcher_cannot_set_anybody_elses(client):
    luis = helpers.add_user(client, email="luis@marina.com", name="Luis", role=Role.BUTCHER)
    with db.session_scope() as s:
        ana_id = s.query(User).filter_by(role=Role.MANAGER).one().id
    token = csrf_from(luis.get("/merma").text)
    r = luis.post(f"/manager/equipo/{ana_id}/contrasena",
                  data={"csrf": token, "password": "me-la-quedo-1"})
    assert r.status_code == 403
    with db.session_scope() as s:
        assert auth.verify_password("clave-larga-1", s.get(User, ana_id).password_hash)


def test_the_login_screen_says_what_to_do_if_you_forgot_it(client):
    client.cookies.clear()
    acceso = client.get("/login")
    assert "Olvidaste la contraseña" in acceso.text
    assert "manager" in acceso.text


# ------------------------------------------------- el freno, para todos igual
def test_the_brake_is_shared_by_every_worker(tmp_path):
    """Cinco intentos son cinco, haya un proceso o haya cuatro.

    Antes la cuenta de fallos vivía en la memoria de cada proceso: con cuatro
    trabajadores detrás del mismo servidor, cinco intentos eran veinte. Ahora
    los fallos se apuntan en la base de datos, que es la misma para todos, y
    aquí se comprueba usando dos sesiones distintas como si fueran dos.
    """
    from thegrill.meat import security
    from thegrill.models import AccessBrake

    db.init_engine(f"sqlite:///{tmp_path/'freno.db'}")
    db.create_all()
    clave = "ana@a.com|10.0.0.7"

    with db.session_scope() as uno:
        security.reset(uno)
        for _ in range(security.LOGIN_ATTEMPTS - 1):
            security.note_failure(clave, session=uno)
        assert security.locked_for(clave, session=uno) == 0

    # Otro proceso, otra sesión: el fallo que falta lo pone él y frena a los dos.
    with db.session_scope() as dos:
        security.note_failure(clave, session=dos)
        assert security.locked_for(clave, session=dos) > 0
    with db.session_scope() as uno:
        assert security.locked_for(clave, session=uno) > 0
        assert uno.query(AccessBrake).filter_by(kind="login").count() == security.LOGIN_ATTEMPTS

        security.clear(clave, session=uno)          # un acceso bueno lo borra
        assert security.locked_for(clave, session=uno) == 0


def test_the_public_form_brake_is_shared_too(tmp_path):
    from thegrill.meat import security

    db.init_engine(f"sqlite:///{tmp_path/'freno2.db'}")
    db.create_all()
    with db.session_scope() as s:
        security.reset(s)
        for _ in range(security.FORM_ATTEMPTS):
            assert security.form_allowed("10.0.0.9", session=s) is True
    with db.session_scope() as otro:
        assert security.form_allowed("10.0.0.9", session=otro) is False


def test_the_lock_file_pins_every_dependency():
    """Lo que se instala en el servidor no puede cambiar solo."""
    import pathlib
    import re

    from scripts.lock import direct

    lock = pathlib.Path("requirements.lock").read_text()
    fijadas = dict(re.findall(r"^([a-z0-9._-]+)==([\d.]+)$", lock, re.M))
    assert fijadas, "el lock no fija nada"
    for nombre in direct():
        assert nombre.lower().replace("_", "-") in fijadas, nombre

    docker = pathlib.Path("Dockerfile").read_text()
    assert "requirements.lock" in docker and "-r requirements.txt" not in docker
