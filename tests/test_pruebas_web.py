"""Pasar las pruebas desde la propia web, en el servidor de verdad.

Las pruebas se pasan en el ordenador de quien programa y en el servidor de
integración. Las dos cosas comprueban **otra máquina**: otro Python, otro
disco, otro reloj, otra zona horaria, otra base de datos. Lo que solo se rompe
en el servidor que atiende a los clientes —una zona horaria que no está
instalada, una columna que no volvió sola al actualizar, un disco lleno— no se
ve de ninguna otra forma, y lo que llega es un cliente diciendo que «no va».

Lo que se vigila aquí, y las tres primeras son de seguridad:

- **Que corra en otro proceso.** Las pruebas montan su base con
  `db.init_engine`, que es global: llamarlas dentro del servidor le cambiaría
  la base de datos a todos los clientes a mitad de turno.
- **Que la base que vea el hijo sea de mentira**, por si alguna prueba se
  olvidara de montar la suya.
- **Que no se puedan lanzar dos a la vez** en la máquina que atiende clientes.
- **Y que cada fallo diga en qué número de comentario del programa se rompió.**
  Que una prueba falle en su línea 30 no dice nada; que el programa reviente en
  el 00667 lleva directo al sitio, y ese número no cambia aunque alguien añada
  tres renglones por encima.
"""
import re
import time

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import billing, pruebas

ROTA = "tests/test_rota_de_mentira.py"
CUERPO = '''"""La escribe una prueba y la borra al terminar: no vive en el repositorio."""


def test_que_falla_en_la_prueba():
    assert 1 == 2, "sabotaje, no un fallo"


def test_que_revienta_dentro_del_programa():
    import pathlib
    import tempfile

    from thegrill import db
    from thegrill.meat import tarifa
    from thegrill.models import User

    t = pathlib.Path(tempfile.mkdtemp())
    db.init_engine(f"sqlite:///{t}/x.db")
    db.create_all()
    with db.session_scope() as s:
        tarifa.guardar(s, User(id=1, restaurant_id=1, name="x", email="x", role=None),
                       mercado="MARTE", currency="EUR", per_outlet=10.0)
'''


@pytest.fixture
def consola(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'pruebas.db'}")
    db.create_all()
    with db.session_scope() as s:
        billing.bootstrap_owner(s, "yo@plataforma.com", "Albano", "clave-larga-1")
    c = TestClient(meatapp.app, follow_redirects=False, headers={"accept-language": "es"})
    r = c.post("/login", data={"email": "yo@plataforma.com", "password": "clave-larga-1"})
    assert r.status_code == 303, r.text[:200]
    yield c
    (pruebas.RAIZ / ROTA).unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def sin_tanda_previa():
    pruebas._ultima = None      # noqa: SLF001 — es el estado del módulo, y es suyo
    yield
    pruebas._ultima = None      # noqa: SLF001


def csrf(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def espera(segundos=120):
    for _ in range(segundos * 2):
        if not pruebas.corriendo():
            return pruebas.ultima()
        time.sleep(0.5)
    raise AssertionError("la pasada no terminó")


def grupo_de_mentira(codigo, *args, minutos=1):
    pruebas.GRUPOS[codigo] = pruebas.Grupo(codigo, args, minutos)
    return codigo


# --------------------------------------------------------- que sea seguro
MIRA_LA_BASE = '''"""Una prueba que mira con qué base de datos la han arrancado."""
import os


def test_la_base_que_me_dan_es_de_mentira():
    cual = os.environ.get("GRILL_DB", "")
    assert cual, "al hijo no le dijeron ninguna base: se quedaría con la del servidor"
    assert "sqlite" in cual, cual
    assert "/tmp" in cual or "pruebas.db" in cual, cual
'''


def test_the_tests_never_touch_the_real_database(consola, tmp_path):
    """Es toda la seguridad de esto: otro proceso y una base de mentira.

    Y se comprueba **desde dentro del hijo**, que es donde se ve: una prueba
    que mira con qué base la han arrancado. Comprobarlo desde fuera —mirando que
    la del servidor sigue siendo la del servidor— no vale, porque eso sale bien
    aunque no se le pase ninguna: el hijo tiene sus propias variables globales
    y no puede tocar las de aquí ni queriendo.
    """
    antes = str(db._engine.url) if db._engine else ""   # noqa: SLF001
    (pruebas.RAIZ / ROTA).write_text(MIRA_LA_BASE, encoding="utf-8")
    grupo_de_mentira("mira", ROTA)
    pruebas.lanzar("mira", "yo@plataforma.com")
    tanda = espera()

    assert tanda.total > 0, tanda.error
    assert not tanda.fallos, [f.porque for f in tanda.fallos]
    # Y la del servidor sigue siendo la del servidor.
    assert str(db._engine.url) == antes                 # noqa: SLF001
    with db.session_scope() as s:
        assert billing.owner_exists(s), "las pruebas se llevaron por delante la casa"


def test_two_runs_at_once_are_not_allowed(consola):
    """Dos pasadas de la suite a la vez en la máquina que atiende clientes, no."""
    grupo_de_mentira("humo", "tests/test_i18n.py")
    primera = pruebas.lanzar("humo", "yo@plataforma.com")
    segunda = pruebas.lanzar("humo", "otro@plataforma.com")
    assert segunda is primera, "arrancó una segunda pasada encima de la primera"
    espera()


def test_only_the_owner_gets_in(consola, tmp_path):
    """La pantalla es del dueño de la plataforma y de nadie más."""
    from tests.meat_helpers import new_house

    new_house("Asador Marina", "albano@marina.com")
    otro = TestClient(meatapp.app, follow_redirects=False, headers={"accept-language": "es"})
    otro.post("/login", data={"email": "albano@marina.com", "password": "clave-larga-1"})
    # Al que no es de la plataforma se le dice que eso no existe, no que no puede.
    assert otro.get("/admin/pruebas").status_code == 404
    assert TestClient(meatapp.app, follow_redirects=False).get(
        "/admin/pruebas").status_code == 303


# ------------------------------------------------- que diga dónde se rompió
def test_a_failure_says_which_numbered_comment_it_broke_at(consola):
    """Lo que se pidió: el número de la función, no el fichero y la línea."""
    (pruebas.RAIZ / ROTA).write_text(CUERPO, encoding="utf-8")
    grupo_de_mentira("rota", ROTA)
    pruebas.lanzar("rota", "yo@plataforma.com")
    tanda = espera()

    assert len(tanda.fallos) == 2, [f.prueba for f in tanda.fallos]
    dentro = [f for f in tanda.fallos if f.fichero.startswith("thegrill/")]
    assert dentro, "no supo que el fallo estaba dentro del programa"
    roto = dentro[0]
    assert roto.numero and roto.numero.isdigit(), roto
    assert roto.fichero == "thegrill/meat/tarifa.py", roto.fichero
    assert "MARTE" in roto.porque or "mercado" in roto.porque, roto.porque

    # Y ese número lleva a un comentario que existe de verdad.
    fuente = (pruebas.RAIZ / roto.fichero).read_text(encoding="utf-8")
    assert f"[{roto.numero}]" in fuente


def test_the_screen_shows_the_number_next_to_the_failure(consola):
    (pruebas.RAIZ / ROTA).write_text(CUERPO, encoding="utf-8")
    grupo_de_mentira("rota", ROTA)
    pagina = consola.get("/admin/pruebas")
    assert consola.post("/admin/pruebas", data={
        "csrf": csrf(pagina.text), "grupo": "rota"}).status_code == 303
    tanda = espera()

    texto = consola.get("/admin/pruebas").text
    for fallo in tanda.fallos:
        if fallo.numero:
            assert fallo.numero in texto, f"el número {fallo.numero} no sale en pantalla"
    assert "thegrill/meat/tarifa.py" in texto


def test_when_everything_passes_it_says_so(consola):
    grupo_de_mentira("humo", "tests/test_i18n.py")
    pruebas.lanzar("humo", "yo@plataforma.com")
    tanda = espera()
    assert tanda.bien
    assert "Todo pasa en este servidor" in consola.get("/admin/pruebas").text


# ------------------------------------------------ el número del comentario
def test_the_number_is_the_nearest_comment_above():
    """Todo el programa está numerado para esto, y así no hay que contar líneas."""
    fuente = (pruebas.RAIZ / "thegrill/meat/tarifa.py").read_text(encoding="utf-8")
    lineas = fuente.splitlines()
    cual = next(i for i, l in enumerate(lineas, 1) if "[00660]" in l)

    assert pruebas.numero_de("thegrill/meat/tarifa.py", cual) == "00660"
    assert pruebas.numero_de("thegrill/meat/tarifa.py", cual + 3) == "00660"
    assert pruebas.numero_de("no/existe.py", 10) == ""
    assert pruebas.numero_de("", 0) == ""


def test_the_frame_that_counts_is_the_deepest_one_inside_the_program():
    """La traza empieza en la prueba y baja hasta donde se rompió de verdad.

    Se mira el cuadro más profundo que esté dentro de `thegrill/`, y no el
    último de todos: pytest a veces cierra la traza con el renglón de la propia
    prueba, y entonces el número que saldría sería el de la prueba —que no
    dice nada— en vez del sitio del programa donde reventó.
    """
    traza = ("tests/test_algo.py:30: in test_algo\n"
             "    service.mete_la_pieza(s, pieza)\n"
             "thegrill/meat/service.py:177: in mete_la_pieza\n"
             "    raise MeatError(...)\n"
             "tests/test_algo.py:31: MeatError\n")
    fichero, linea = pruebas._donde_se_rompio(traza, {"classname": "tests.test_algo"})
    assert (fichero, linea) == ("thegrill/meat/service.py", 177)


def test_a_test_that_is_wrong_all_by_itself_still_says_where():
    """Sin ningún cuadro dentro del programa, se enseña el de la prueba."""
    traza = "tests/test_algo.py:30: in test_algo\n    assert 1 == 2\ntests/test_algo.py:30: AssertionError\n"
    fichero, linea = pruebas._donde_se_rompio(traza, {"classname": "tests.test_algo"})
    assert fichero == "tests/test_algo.py" and linea == 30
