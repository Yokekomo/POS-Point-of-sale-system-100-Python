"""Que un fallo se cuente, y no se le enseñe a nadie el suelo del framework.

Tres cosas que salían mal y las tres acababan en una llamada de teléfono:

**El fallo no previsto.** Sin manejador de `Exception`, FastAPI contesta
«Internal Server Error»: veintiún bytes de texto plano, en inglés, sin la
marca, sin botón de volver y sin nada que darle a quien atiende. Igual para el
húngaro que para el español. Ahora sale la pantalla de siempre, en su idioma, y
con un número de cuatro cifras que encabeza la traza del registro: cuando
alguien dice «me ha salido el 4417», eso es todo lo que hace falta para saber
qué pasó.

**La casilla que falta.** Sin manejador de `RequestValidationError` se contesta
`{"detail":[{"type":"missing","loc":["body","serial"]…}]}`, 422 y en inglés. Y
hay un motivo de más para que eso no pase: la cola del teléfono aparta a «sin
mandar» todo lo que llega con 4xx, así que ese JSON acababa siendo lo que una
persona abría para ver por qué no había entrado su recuento.

**El aviso de otra casa.** `/manager/alertas/{id}/cerrar` era la única ruta de
treinta y cinco que devolvía un 500 de verdad: `service.acknowledge_alert`
levanta `PermissionError` y el `except` de la ruta solo miraba `ValueError` y
`ValidationError`.
"""
import logging
import re

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.models import Alert, AlertSeverity, Role
from tests.meat_helpers import SPANISH, new_house


@pytest.fixture
def casa(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'errores.db'}")
    db.create_all()
    mia = new_house("Asador Marina", "albano@marina.com")
    suya = new_house("Asador Vecino", "vecina@vecino.com", manager="Nora")
    return mia, suya


@pytest.fixture(autouse=True)
def sin_ruido():
    """La traza del fallo se escribe a propósito; aquí no hace falta verla."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def cliente(lang="es"):
    return TestClient(meatapp.app, follow_redirects=False,
                      raise_server_exceptions=False, headers={"accept-language": lang})


def entra(lang="es", correo="albano@marina.com"):
    c = cliente(lang)
    r = c.post("/login", data={"email": correo, "password": "clave-larga-1"})
    assert r.status_code == 303, r.text[:200]
    return c


def _texto(html: str) -> str:
    limpio = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", limpio)).strip()


@meatapp.app.get("/reventar-a-proposito")
def _reventar():
    """Una ruta que revienta. Es la única forma de probar esto de verdad."""
    raise RuntimeError("esto es la prueba, no un fallo")


# ------------------------------------------------------ el fallo no previsto
@pytest.mark.parametrize("lang", ["es", "hu", "ar"])
def test_an_unexpected_failure_is_a_page_and_not_twenty_one_bytes(casa, lang):
    r = cliente(lang).get("/reventar-a-proposito")

    assert r.status_code == 500
    assert r.headers["content-type"].startswith("text/html"), "salió el suelo del framework"
    assert len(r.text) > 1000, "veintiún bytes de texto plano otra vez"
    texto = _texto(r.text)
    assert "Internal Server Error" not in texto
    assert re.search(r"#\d{4}", texto), f"sin número que dictar por teléfono: {texto[:120]}"


def test_the_number_on_screen_is_the_number_in_the_log(casa, caplog):
    """De nada sirve el número si no se puede buscar."""
    logging.disable(logging.NOTSET)
    with caplog.at_level(logging.ERROR):
        r = cliente().get("/reventar-a-proposito")
    numero = re.search(r"#(\d{4})", _texto(r.text)).group(1)
    assert any(f"FALLO {numero}" in linea.getMessage() for linea in caplog.records), \
        f"el {numero} no aparece en el registro: no hay por dónde empezar a mirar"


def test_the_page_speaks_the_language_of_whoever_is_in_front(casa):
    """Un fallo en húngaro no se cuenta en inglés."""
    assert "hiba" in _texto(cliente("hu").get("/reventar-a-proposito").text)


# ------------------------------------------------------- la casilla que falta
def test_a_missing_field_is_not_a_lump_of_json(casa):
    """La cola aparta lo que llega con 4xx: esto es lo que abre una persona."""
    r = entra().post("/maduracion/pesar", data={})

    assert r.headers["content-type"].startswith("text/html"), r.text[:200]
    texto = _texto(r.text)
    assert '"loc"' not in texto and "Field required" not in texto, texto[:200]
    assert r.status_code == 400


# --------------------------------------------------- el aviso de la otra casa
def test_closing_someone_elses_alert_is_not_a_crash(casa):
    """Era la única ruta de treinta y cinco que daba un 500 de verdad."""
    mia, suya = casa
    with db.session_scope() as s:
        # Uno en cada casa: el mío, para que la pantalla dibuje su formulario y
        # de ahí salga el token; el de al lado, que es el que se intenta cerrar.
        s.add(Alert(restaurant_id=mia, code="prueba", message="de la mía",
                    severity=AlertSeverity.INFO))
        ajeno = Alert(restaurant_id=suya, code="prueba", message="de la otra casa",
                      severity=AlertSeverity.INFO)
        s.add(ajeno)
        s.flush()
        ajeno_id = ajeno.id

    c = entra()
    pagina = c.get("/manager/alertas")
    csrf = re.search(r'name="csrf" value="([^"]+)"', pagina.text).group(1)
    r = c.post(f"/manager/alertas/{ajeno_id}/cerrar",
               data={"csrf": csrf, "resolution": "lo cierro yo"})

    assert r.status_code == 404, f"volvió a dar {r.status_code}"
    assert "Internal Server Error" not in r.text
    with db.session_scope() as s:
        assert s.get(Alert, ajeno_id).acknowledged_at is None, "cerró el aviso de otra casa"
