"""Los avisos de trabajo, en el idioma de quien los lee.

Cuando algo no cuadra —«de la pieza 8017 solo quedan 4 kg»— el programa lo
decía en español dentro de la propia función y la pantalla lo enseñaba tal
cual. Es el peor momento para cambiar de idioma: el carnicero de Ámsterdam o
el jefe de cocina de Dubái trabajan en el suyo hasta que algo sale mal, y
justo entonces la pantalla se les pone en un idioma que no leen, con el camión
en el muelle o la cámara abierta.

Lo que se comprueba aquí es que el aviso viaja con su clave hasta la pantalla
y se dice donde toca, y que no ha vuelto a colarse un texto suelto en español
en ninguna de las funciones que le hablan a quien está delante.
"""
import ast
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import i18n_meat
from thegrill.models import Primal, PrimalStatus
from thegrill.web import aging, i18n
from thegrill.web.i18n import Aviso, Avisos

from tests.meat_helpers import csrf_from, new_house  # noqa: E402

RAIZ = pathlib.Path(__file__).resolve().parents[1] / "thegrill"

# Las funciones de oficio: las que le contestan a quien está trabajando.
# Aquí dentro no puede quedar ni un aviso escrito en español a pelo.
DE_OFICIO = ("web/aging.py", "web/sites.py", "web/inventory.py", "web/waste.py",
             "web/butchery.py", "web/defrost.py", "web/costing.py", "meat/service.py")

ACENTOS = re.compile(r"[áéíóúñÁÉÍÓÚÑ¿¡]")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'avisos.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False) as c:
        yield c


def _claves_usadas() -> list[tuple[str, int, str]]:
    """Todas las claves con las que se construye un aviso en el programa."""
    fuera = []
    for fichero in sorted(RAIZ.rglob("*.py")):
        arbol = ast.parse(fichero.read_text())
        for nodo in ast.walk(arbol):
            if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)
                    and nodo.func.id == "Aviso" and nodo.args
                    and isinstance(nodo.args[0], ast.Constant)):
                fuera.append((fichero.name, nodo.lineno, nodo.args[0].value))
    return fuera


def test_every_key_a_warning_uses_is_actually_written():
    """Una clave mal escrita no revienta: enseña la clave. Por eso se mira aquí."""
    i18n_meat.install()
    usadas = _claves_usadas()
    assert len(usadas) > 90, f"solo he encontrado {len(usadas)} avisos: algo no se está leyendo"
    faltan = [(f, n, c) for f, n, c in usadas if c not in i18n.TRANSLATIONS["es"]]
    assert faltan == [], faltan


def test_the_seven_languages_say_every_warning():
    """Ninguno se queda en español por no estar escrito en su idioma."""
    i18n_meat.install()
    claves = {c for _, _, c in _claves_usadas()}
    sin = [(lang, c) for lang in i18n.LANGUAGES for c in claves
           if c not in i18n.TRANSLATIONS[lang]]
    assert sin == [], sin[:10]


def test_no_work_function_still_answers_in_spanish_by_hand():
    """Ni un `raise` con la frase escrita dentro: eso no hay quien lo traduzca."""
    sueltos = []
    for relativo in DE_OFICIO:
        fichero = RAIZ / relativo
        arbol = ast.parse(fichero.read_text())
        for nodo in ast.walk(arbol):
            if not (isinstance(nodo, ast.Raise) and isinstance(nodo.exc, ast.Call)):
                continue
            nombre = getattr(nodo.exc.func, "id", getattr(nodo.exc.func, "attr", ""))
            if not nombre.endswith("Error") or not nodo.exc.args:
                continue
            texto = ast.unparse(nodo.exc.args[0])
            if texto.startswith(("Aviso(", "Avisos(", "t(", "*")):
                continue
            if ACENTOS.search(texto):
                sueltos.append(f"{relativo}:{nodo.lineno}  {texto[:70]}")
    assert sueltos == [], sueltos


def test_a_warning_carries_its_key_and_still_reads_as_spanish():
    """Por fuera es el texto de siempre; por dentro se acuerda de dónde salió."""
    aviso = Aviso("err.ag.sale_short", kg="5", serial="8017", queda="4")
    assert aviso.startswith("Se quieren cortar 5 kg de la pieza 8017")
    assert aviso.clave == "err.ag.sale_short"
    assert i18n.en(aviso, "nl") != str(aviso)
    assert "8017" in i18n.en(aviso, "nl")


def test_a_failure_with_no_key_is_shown_as_it_came():
    """Más vale un texto en español que una pantalla en blanco."""
    assert i18n.en(ValueError("la báscula está desenchufada"), "de") == \
        "la báscula está desenchufada"
    assert i18n.en("texto suelto", "de") == "texto suelto"


def test_several_warnings_at_once_are_all_translated():
    """El repaso de un despiece dice todo lo que falta, y todo en el mismo idioma."""
    juntos = Avisos([Aviso("err.bu.no_primals", tg="TG-7"),
                     Aviso("err.bu.no_cuts_out", tg="TG-7")])
    de = i18n.en(juntos, "de")
    assert de.count(";") == 1 and de != str(juntos)
    assert de.count("TG-7") == 2


def test_the_butcher_who_works_in_arabic_is_told_in_arabic(client):
    """La pantalla de maduración, con la pieza que no llega: en árabe."""
    casa = new_house(language="ar")
    with db.session_scope() as s:
        s.add(Primal(restaurant_id=casa, serial="8017", sku="RIBEYE",
                     weight_kg=4.0, received_kg=4.0, landed_usd_per_kg=30.0,
                     piece_cost_usd=120.0, status=PrimalStatus.IN_STOCK))
    client.post("/login", data={"email": "albano@marina.com",
                                "password": "clave-larga-1"})
    csrf = csrf_from(client.get("/maduracion").text)
    respuesta = client.post("/maduracion/venta",
                            data={"serial": "8017", "grams": "9000", "price": "40",
                                  "csrf": csrf})
    assert respuesta.status_code == 200
    # Ni una palabra del aviso en español, y los kilos que faltan, dichos.
    assert "Se quieren cortar" not in respuesta.text
    assert i18n.t("ar", "err.ag.sale_short", kg="9", serial="8017",
                  queda="4")[:30] in respuesta.text


def test_the_same_thing_said_in_hungarian(client):
    """Y la misma pieza, para una casa que trabaja en húngaro."""
    casa = new_house(language="hu")
    with db.session_scope() as s:
        s.add(Primal(restaurant_id=casa, serial="8017", sku="RIBEYE",
                     weight_kg=4.0, received_kg=4.0, landed_usd_per_kg=30.0,
                     piece_cost_usd=120.0, status=PrimalStatus.IN_STOCK))
    client.post("/login", data={"email": "albano@marina.com",
                                "password": "clave-larga-1"})
    csrf = csrf_from(client.get("/maduracion").text)
    respuesta = client.post("/maduracion/venta",
                            data={"serial": "8017", "grams": "9000", "price": "40",
                                  "csrf": csrf})
    assert "Se quieren cortar" not in respuesta.text
    assert i18n.t("hu", "err.ag.sale_short", kg="9", serial="8017",
                  queda="4")[:30] in respuesta.text


def test_the_warning_of_a_piece_from_another_chiller_keeps_its_key():
    """El aviso de la sede se pasa entero cuando lo recoge maduración."""
    aviso = Aviso("err.st.elsewhere", donde="Playa", mia="Obrador")
    try:
        raise aging.AgingError(*aging.sites.SiteError(aviso).args)
    except aging.AgingError as e:
        assert i18n.en(e, "fr") != str(e)
        assert "Playa" in i18n.en(e, "fr")
