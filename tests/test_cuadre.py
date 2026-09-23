"""El cuadre: el residuo, la banda de cada corte y quién pesa de verdad.

Tres números que no estaban y que son los que se miran cuando el mes no sale.
Lo que se guarda aquí es que digan la verdad **y que no acusen de más**: una
banda que marca como rara una pieza normal, o un veredicto que llama tramposo
a quien tiene una báscula de cien gramos, hacen más daño que no tenerlos.
"""
import random
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import bench, db
from thegrill.meat import app as meatapp
from thegrill.models import Role
from thegrill.web import cuadre

from tests import meat_helpers as helpers

SPANISH = {"accept-language": "es"}
HOY = date(2026, 9, 20)


# ------------------------------------------------------------- 1. el residuo
def test_what_leaves_by_no_door_is_the_number_that_matters():
    """Entra, sale por sus puertas, y lo que no sale por ninguna es el hueco."""
    residuo = cuadre.Residuo(1, "Striploin", entrado_kg=100.0, vendido_kg=70.0,
                             tirado_kg=5.0, movido_kg=10.0, ajustado_kg=-12.0)
    assert residuo.explicado_kg == 85.0
    assert residuo.parte == 12.0                # doce kilos de cien: un 12 %


def test_a_house_with_nothing_missing_says_so():
    """Sin hueco no hay que inventar uno: cero es una respuesta."""
    vacio = cuadre.Cuadre(desde=HOY, hasta=HOY)
    assert vacio.sin_explicar_kg == 0 and vacio.peores == []


def test_the_ledger_is_what_tells_us_where_the_kilos_went(tmp_path):
    """Sale del libro de movimientos, no de una tabla nueva."""
    db.init_engine(f"sqlite:///{tmp_path/'cuadre.db'}")
    db.create_all()
    with db.session_scope() as session:
        casa = bench.build(session, days=20, seed=5, until=HOY)
        numeros = cuadre.cuadre(session, casa.restaurant_id,
                                HOY - timedelta(days=20), HOY)
        assert numeros.lineas, "la casa trabajó veinte días y el libro está vacío"
        entrado = sum(l.entrado_kg for l in numeros.lineas)
        salido = sum(l.explicado_kg for l in numeros.lineas)
        assert entrado > 0 and salido > 0

        # Lo que de verdad tiene que cuadrar es cada lote consigo mismo: lo que
        # entró, menos lo que salió por el libro, es lo que queda. Y cuadra.
        #
        # Lo que NO cuadra —y no es un fallo— es la suma global, porque el POS
        # puede vender carne que la cámara dice que no hay. Esas ventas se
        # apuntan sin lote y levantan su aviso «stock.short», así que salen del
        # libro sin salir de ninguna partida. Sumar todo y esperar el stock es
        # la trampa en la que cae cualquiera que mire estos números por encima.
        from collections import defaultdict

        from thegrill.models import IngredientLot, IngredientMovement
        fuera = defaultdict(float)
        for mv in session.query(IngredientMovement).filter_by(restaurant_id=casa.restaurant_id):
            if mv.lot_id and mv.kind.value != "IN":
                fuera[mv.lot_id] += mv.qty or 0.0
        descuadrados = []
        for lote in session.query(IngredientLot).filter_by(restaurant_id=casa.restaurant_id):
            esperado = (lote.qty or 0.0) + fuera.get(lote.id, 0.0)
            if abs(esperado - (lote.qty_remaining or 0.0)) > 0.005:
                descuadrados.append((lote.serial, esperado, lote.qty_remaining))
        assert not descuadrados, descuadrados


# --------------------------------------------------------------- 2. la banda
def test_the_band_catches_the_comma_that_moved():
    """Ochenta y cuatro kilos entre hermanas de diez: eso es una coma."""
    hermanas = [10.4, 10.1, 9.8, 11.2, 10.6, 9.9, 10.9, 10.3, 10.0, 11.0]
    banda = cuadre.banda(hermanas, "Striploin AUS")
    assert banda is not None
    assert banda.raro(84.0)
    assert banda.cuanto_se_sale(84.0) > 20     # no es raro: es de otro planeta


def test_the_band_does_not_cry_wolf_over_a_normal_piece():
    """Una pieza un poco más grande es una pieza, no un error."""
    hermanas = [10.4, 10.1, 9.8, 11.2, 10.6, 9.9, 10.9, 10.3, 10.0, 11.0]
    banda = cuadre.banda(hermanas, "Striploin AUS")
    for peso in (9.7, 10.7, 11.3, 10.0):
        assert not banda.raro(peso), peso


def test_one_monster_does_not_widen_the_band_for_everyone():
    """Con media y desviación típica, un 84 se traga el aviso del siguiente.

    Por eso se usa mediana y MAD: la mediana no se entera del bicho, así que
    la banda sigue siendo la de las piezas de verdad y el segundo error salta
    igual que el primero.
    """
    con_bicho = [10.4, 10.1, 9.8, 11.2, 10.6, 9.9, 10.9, 10.3, 10.0, 84.0]
    banda = cuadre.banda(con_bicho, "Striploin AUS")
    assert banda.raro(84.0), "el bicho se ha comido su propio aviso"
    assert banda.alto < 20, f"la banda se ha estirado hasta {banda.alto}"


def test_without_history_the_band_says_nothing():
    """Con tres piezas no se sabe qué es normal, y callarse es lo correcto."""
    assert cuadre.banda([10.0, 10.5, 9.8], "Striploin") is None


# ---------------------------------------------------------------- 3. el dedo
def test_someone_who_weighs_leaves_scattered_decimals():
    azar = random.Random(3)
    pesa = [round(azar.uniform(8, 12), 3) for _ in range(60)]
    assert cuadre.dedo(pesa, 1, "Paco").veredicto == "pesa"


def test_someone_who_guesses_in_half_kilos_shows_up():
    azar = random.Random(4)
    ojo = [azar.choice([8.0, 8.5, 9.0, 9.5, 10.0, 10.5]) for _ in range(60)]
    veredicto = cuadre.dedo(ojo, 2, "Leo")
    assert veredicto.veredicto == "no_pesa"
    assert veredicto.redondos > 90


def test_a_coarse_scale_is_not_a_liar():
    """Una báscula de cien gramos da siempre el mismo último dígito.

    Quien la tiene no está inventando: es su aparato. Acusarle sería el peor
    fallo que puede tener esta pantalla, porque rompe la confianza del equipo
    con el programa y ya no se apunta nada.
    """
    azar = random.Random(5)
    basta = [round(azar.uniform(8, 12), 1) for _ in range(60)]
    veredicto = cuadre.dedo(basta, 3, "Eva")
    assert veredicto.resolucion == 0.1
    assert veredicto.veredicto == "pesa", veredicto


def test_with_few_weighings_nobody_is_judged():
    assert cuadre.dedo([8.5] * 10, 4, "Nadie") is None


# ------------------------------------------------------------- la pantalla
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'pantalla.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        helpers.signup(c)
        yield c


def test_the_screen_is_only_for_whoever_sees_the_money(client):
    """Dice dinero, y al final dice quién pesa. No es para colgar en el pase."""
    assert client.get("/cuadre").status_code == 200
    for email, role in (("paco@marina.com", Role.BUTCHER), ("leo@marina.com", Role.EMPLOYEE)):
        otro = helpers.add_user(client, email=email, name=email[:4], role=role)
        assert otro.get("/cuadre").status_code == 403, role


def test_the_screen_says_the_three_things(client):
    pantalla = client.get("/cuadre").text
    assert "Lo que no sabemos dónde ha ido" in pantalla
    assert "Lo normal en esta casa" in pantalla
    assert "Quién pesa y quién calcula" in pantalla
    # Y lo dice antes de juzgar a nadie: el aviso de que esto no acusa.
    assert "no acusa a nadie" in pantalla


def test_an_empty_house_does_not_show_an_empty_table(client):
    """Una casa recién abierta no tiene historia: se dice, no se enseña vacío."""
    pantalla = client.get("/cuadre").text
    assert "Todavía no hay piezas suficientes" in pantalla
    assert "Aún no hay pesadas suficientes" in pantalla
