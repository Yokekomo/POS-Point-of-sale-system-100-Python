"""Los números salen escritos como los escribe el país que los lee."""
import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.web import cifras

from tests.meat_helpers import csrf_from, new_house, login


@pytest.mark.parametrize("texto,esperado", [
    ("9.400", "9,400"), ("1234.56", "1234,56"), ("-1.5", "-1,5"),
    ("+2.50", "+2,50"), ("0.0", "0,0"), ("12.5 %", "12,5 %"),
])
def test_a_number_gets_the_comma(texto, esperado):
    assert cifras.con_coma(texto) == esperado


@pytest.mark.parametrize("texto", [
    "8017-01",              # un serial no es un número
    "20/09/2026",           # ni una fecha
    "Striploin AUS",        # ni un nombre
    "1.234.567",            # ni algo que ya venga agrupado
    "DXB20260910",
])
def test_and_nothing_else_gets_touched(texto):
    assert cifras.con_coma(texto) == texto


def _casa(tmp_path, monkeypatch, lang):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'carne.db'}")
    db.create_all()
    new_house(language=lang)
    return TestClient(meatapp.app, follow_redirects=False,
                      headers={"accept-language": lang})


def _recibir(client, gramos):
    """El peso se escribe en gramos; lo que cambia de idioma es cómo sale."""
    form = client.get("/recepcion")
    return client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "sku": "Striploin",
        "price:0": "32", "serial:0": "8017", "g:0": gramos})


def test_the_chamber_speaks_the_language_of_the_house(tmp_path, monkeypatch):
    """Lo que sale de una plantilla: «12,3 kg» y no «12.3 kg»."""
    with _casa(tmp_path, monkeypatch, "es") as c:
        login(c)
        assert _recibir(c, "12345").status_code == 303
        pagina = c.get("/carne").text
        assert "12,3" in pagina and ">12.3" not in pagina


def test_and_english_keeps_the_point(tmp_path, monkeypatch):
    with _casa(tmp_path, monkeypatch, "en") as c:
        login(c)
        assert _recibir(c, "12345").status_code == 303
        assert "12.3" in c.get("/carne").text


def test_a_number_inside_a_sentence_too(tmp_path, monkeypatch):
    """Lo que arma Python antes de que Jinja lo vea: el aviso de la recepción.

    Este era el que se escapaba. El filtro de las plantillas no lo ve, porque
    cuando llega ya es una frase hecha.
    """
    with _casa(tmp_path, monkeypatch, "es") as c:
        login(c)
        _recibir(c, "12345")
        recado = c.get("/recepcion").text
        assert "12,345" in recado and "12.345" not in recado


def test_and_that_sentence_keeps_the_point_in_english(tmp_path, monkeypatch):
    with _casa(tmp_path, monkeypatch, "en") as c:
        login(c)
        _recibir(c, "12345")
        recado = c.get("/recepcion").text
        assert "12.345" in recado and "12,345" not in recado


def test_a_serial_never_turns_into_a_number(tmp_path, monkeypatch):
    """El 8017-01 se escribe igual en los siete idiomas."""
    with _casa(tmp_path, monkeypatch, "es") as c:
        login(c)
        _recibir(c, "12345")
        assert "8017" in c.get("/carne").text
