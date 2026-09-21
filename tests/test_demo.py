"""La demo: que se monte sola y que se pueda entrar con lo que dice el papel.

Una demo que no arranca es peor que no tener demo. Lo que se comprueba aquí es
lo que hace quien la prueba por primera vez: lanzarla, leer las claves de la
pantalla y entrar con una de ellas.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from thegrill import bench, db
from thegrill.meat import app as meatapp
from thegrill.models import Primal, Restaurant, Role, User

SPANISH = {"accept-language": "es"}
HOY = date(2026, 9, 20)


@pytest.fixture
def montada(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'demo.db'}")
    db.create_all()
    with db.session_scope() as session:
        cuentas = bench.demo(session, days=8, seed=21, until=HOY)
    return cuentas


def test_the_demo_comes_with_a_month_of_work_already_inside(montada):
    """Quien la abre no tiene que escribir nada para ver algo."""
    with db.session_scope() as session:
        casas = (session.query(Restaurant).filter(Restaurant.platform.isnot(True))
                 .order_by(Restaurant.id).all())
        assert len(casas) == 2
        assert casas[0].name.startswith("Grupo") and casas[1].name.startswith("Asador")
        assert session.query(Primal).count() > 5


def test_the_keys_it_prints_are_the_keys_that_work(montada):
    """Cada línea de la pantalla de arranque tiene que dejar entrar."""
    assert len(montada) == 11        # el dueño y cinco personas en cada casa
    for cuenta in montada:
        client = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
        respuesta = client.post("/login", data={"email": cuenta.email,
                                                "password": cuenta.password})
        assert respuesta.status_code == 303, f"{cuenta.email}: {respuesta.text[:120]}"
        assert client.get("/hoy").status_code == 200


def test_it_shows_both_kinds_of_house(montada):
    """Un grupo con sedes y un asador sin ellas: lo normal y lo de varios locales."""
    correos = [c.email for c in montada]
    assert "ana0@demo.com" in correos and "ana1@demo.com" in correos
    assert any("Playa" in c.sees for c in montada)          # alguien de un local
    assert any("la casa entera" in c.sees for c in montada)


def test_the_platform_owner_can_read_the_reported_problems(montada):
    dueno = [c for c in montada if "plataforma" in c.who.lower()][0]
    client = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    client.post("/login", data={"email": dueno.email, "password": dueno.password})
    assert client.get("/admin/fallos").status_code == 200
    assert client.get("/admin").status_code == 200


def test_running_it_twice_does_not_build_it_twice(montada):
    """La segunda vez solo vuelve a decir las claves: el trabajo sigue ahí."""
    with db.session_scope() as session:
        piezas = session.query(Primal).count()
        otra_vez = bench.demo_accounts(session)
        assert session.query(Primal).count() == piezas
    assert [c.email for c in otra_vez] == [c.email for c in montada]


def test_a_butcher_of_the_demo_sees_meat_but_not_money(montada):
    """La gracia de la demo es ver lo que ve cada uno."""
    with db.session_scope() as session:
        paco = session.query(User).filter_by(email="paco0@demo.com").one()
        assert paco.role == Role.BUTCHER
    client = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    client.post("/login", data={"email": "paco0@demo.com", "password": bench.DEMO_PASSWORD})
    pagina = client.get("/hoy").text
    assert "Valor en cámara" not in pagina
    assert client.get("/carne").status_code == 200
