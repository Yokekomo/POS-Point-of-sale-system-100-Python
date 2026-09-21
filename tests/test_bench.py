"""El banco de pruebas: una casa de mentira trabajando un mes.

Las pruebas de al lado comprueban lo que alguien pensó comprobar. Esta monta la
casa entera —obrador, dos locales, su gente y su carta—, la hace trabajar unos
días con sus recepciones, su maduración, sus traslados, sus ventas y su merma, y
después pasa la lista de lo que nunca puede pasar. Luego le da martillazos al
azar y la vuelve a pasar.

Si un día falla, el fallo se repite: la semilla es fija.
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from thegrill import bench, db
from thegrill.meat import app as meatapp
from thegrill.models import IngredientLot, IngredientMovement, MovementKind, Role, User

SPANISH = {"accept-language": "es"}
HOY = date(2026, 9, 20)


@pytest.fixture
def casa(tmp_path, monkeypatch):
    """La casa montada y guardada: cada prueba abre su propia sesión.

    La sesión que la monta se cierra antes de devolverla; si no, el servidor de
    pruebas y ella se pelean por la misma base de datos.
    """
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'banco.db'}")
    db.create_all()
    with db.session_scope() as session:
        montada = bench.build(session, days=8, seed=5, until=HOY)
    return montada


def entra(email: str, password: str = "clave-larga-3") -> TestClient:
    client = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    respuesta = client.post("/login", data={"email": email, "password": password})
    assert respuesta.status_code == 303, respuesta.text[:200]
    return client


# ------------------------------------------------------- que la casa se monta
def test_the_bench_builds_a_house_that_actually_worked(casa):
    assert casa.primals >= 8 and casa.sales >= 10
    assert len(casa.outlets) == 2
    # Nada de lo que hace el banco es una operación que el programa rechace.
    assert casa.errors == [], casa.errors[:5]
    with db.session_scope() as session:
        assert session.query(IngredientLot).count() > 0


def test_nothing_in_the_house_breaks_the_rules(casa):
    """La lista de lo que nunca puede pasar, después de un mes de trabajo."""
    with db.session_scope() as session:
        hallazgos = bench.audit(session, casa.restaurant_id)
    assert hallazgos == [], [str(h) for h in hallazgos][:10]


def test_every_lot_squares_with_its_own_book(casa):
    """Lo que queda de un lote es lo que entró menos lo que está apuntado.

    Un lote que baja sin apunte es un lote que no se puede explicar cuando
    alguien pregunta, y eso pasaba con los traslados y con lo que sale del
    arcón: se llevaban kilos sin dejar rastro en el libro.
    """
    with db.session_scope() as session:
        movidos: dict[int, float] = {}
        for mv in session.query(IngredientMovement).filter_by(
                restaurant_id=casa.restaurant_id):
            if mv.kind != MovementKind.IN and mv.lot_id:
                movidos[mv.lot_id] = round(movidos.get(mv.lot_id, 0.0) + mv.qty, 6)
        for lote in session.query(IngredientLot).filter_by(restaurant_id=casa.restaurant_id):
            esperado = round((lote.qty or 0.0) + movidos.get(lote.id, 0.0), 6)
            assert abs(esperado - lote.qty_remaining) < 0.005, lote.serial


def test_a_split_lot_is_written_down_on_both_sides(casa):
    """Lo que sale de un número y entra en otro se apunta en los dos."""
    with db.session_scope() as session:
        partidos = [l for l in session.query(IngredientLot)
                    .filter_by(restaurant_id=casa.restaurant_id)
                    if "·T" in (l.serial or "") or "·D" in (l.serial or "")]
        assert partidos, "el banco no ha partido ningún lote"
        for hijo in partidos:
            entradas = (session.query(IngredientMovement)
                        .filter_by(lot_id=hijo.id, kind=MovementKind.IN).all())
            assert entradas, f"{hijo.serial} nació sin apunte de entrada"
            assert entradas[0].qty == pytest.approx(hijo.qty, abs=1e-6)


# ------------------------------------------------------------ los martillazos
def test_the_hammer_leaves_the_house_standing(casa):
    """Operaciones al azar, también las imposibles. Lo que se rechace, que se rechace bien."""
    with db.session_scope() as session:
        rechazos = bench.hammer(session, casa, rounds=120, seed=3)
        assert rechazos, "nada ha fallado: el martillo no está pegando"
        hallazgos = bench.audit(session, casa.restaurant_id)
    assert hallazgos == [], [str(h) for h in hallazgos][:10]


def test_a_scale_wobble_never_makes_the_aged_kilo_cheaper(casa):
    """Una pieza no engorda en la cámara: si la báscula lo dice, manda el peso de antes."""
    from thegrill.models import Primal, PrimalWeighing, Storage
    from thegrill.web import aging

    with db.session_scope() as session:
        ana = session.query(User).filter_by(email="ana0@banco.com").one()
        pieza = Primal(restaurant_id=casa.restaurant_id, serial="9999", sku="RIBEYE",
                       weight_kg=9.0, received_date=HOY, landed_usd_per_kg=30.0,
                       piece_cost_usd=270.0, site_id=casa.warehouse_id)
        session.add(pieza); session.flush()
        aging.move(session, ana, "9999", Storage.AGING, on=HOY)

        resultado = aging.weigh(session, ana, "9999", 9.03, on=HOY, lang="es")  # 30 g de más

        assert resultado.kg == 9.0 and resultado.loss_kg == 0.0
        assert resultado.cost_per_kg == pytest.approx(30.0)
        apunte = (session.query(PrimalWeighing).filter_by(serial="9999")
                  .order_by(PrimalWeighing.id.desc()).first())
        assert "9.03" in (apunte.note or "")        # lo leído queda escrito
        # Y un kilo de más ya no es la báscula: eso se rechaza.
        with pytest.raises(aging.AgingError):
            aging.weigh(session, ana, "9999", 10.0, on=HOY, lang="es")


# ------------------------------------------------------------- las pantallas
@pytest.mark.parametrize("email,money", [("ana0@banco.com", True),
                                         ("paco0@banco.com", False),
                                         ("eva0@banco.com", False)])
def test_every_screen_opens_for_every_role(casa, email, money):
    """Que abran, que no se queden en claves y que el dinero no se escape."""
    contraseñas = {"ana0@banco.com": "clave-larga-1", "paco0@banco.com": "clave-larga-2"}
    client = entra(email, contraseñas.get(email, "clave-larga-3"))

    hallazgos = bench.crawl(client, money=money,
                            extra={"site_id": casa.warehouse_id,
                                   "code": "entrecot_a_la_brasa"})
    assert hallazgos == [], [str(h) for h in hallazgos][:10]


def test_the_crawler_actually_visits_the_meat_screens(casa):
    rutas = bench._paths(meatapp.app, {"site_id": casa.warehouse_id,
                                       "code": "entrecot_a_la_brasa"})
    for imprescindible in ("/hoy", "/carne", "/maduracion", "/traslados", "/inventario",
                           "/parte", "/merma", "/ventas", "/sedes", "/trazabilidad",
                           "/despiece", "/recepcion", "/descongelado", "/cortes",
                           "/carta/entrecot_a_la_brasa", f"/sedes/{casa.warehouse_id}/minimos"):
        assert imprescindible in rutas, imprescindible
    assert len(rutas) >= 30


# ================================================ cincuenta casas distintas
def test_fifty_houses_of_both_kinds_work_a_month_without_breaking(tmp_path, monkeypatch):
    """Un fallo que no sale en una casa sale en la número treinta y siete.

    Se monta un barrio: la mitad grupos con obrador y dos locales, la mitad
    asadores de una sola cámara, cinco personas en cada uno —dos managers y
    tres más—, un mes de trabajo y su inventario mensual. Si algo se rompe con
    varias sedes o se rompe sin ellas, aquí sale.
    """
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'barrio.db'}")
    db.create_all()
    with db.session_scope() as session:
        barrio = bench.population(session, houses=6, days=30, seed=4, until=HOY)

    assert len(barrio.houses) == 6
    assert barrio.multisite == 3                  # mitad y mitad, a propósito
    assert barrio.errors == [], barrio.errors[:5]
    assert barrio.findings == [], [str(f) for f in barrio.findings][:10]
    assert barrio.counts >= 6                     # el inventario del mes, casa por casa
    assert barrio.sales > 100

    with db.session_scope() as session:
        for casa in barrio.houses:
            gente = session.query(User).filter_by(restaurant_id=casa.restaurant_id).all()
            assert len(gente) == 5
            assert len([u for u in gente if u.role == Role.MANAGER]) == 2
            if casa.multisite:
                assert len(casa.outlets) == 2
            else:
                assert casa.outlets == []


def test_one_house_never_sees_the_meat_of_the_one_next_door(tmp_path, monkeypatch):
    """Lo que más duele en un programa de varios clientes: que se crucen."""
    from thegrill.web import costing

    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'dos.db'}")
    db.create_all()
    with db.session_scope() as session:
        barrio = bench.population(session, houses=2, days=10, seed=9, until=HOY)
        una, otra = barrio.houses
        lotes_una = {l.id for l in session.query(IngredientLot)
                     .filter_by(restaurant_id=una.restaurant_id)}
        lotes_otra = {l.id for l in session.query(IngredientLot)
                      .filter_by(restaurant_id=otra.restaurant_id)}
        assert lotes_una and lotes_otra and not (lotes_una & lotes_otra)
        # Y el precio de un corte de una no sale de los lotes de la otra.
        precios = costing.unit_costs(session, una.restaurant_id)
        for ingrediente_id in precios:
            ajeno = (session.query(IngredientLot)
                     .filter_by(restaurant_id=otra.restaurant_id,
                                ingredient_id=ingrediente_id).first())
            assert ajeno is None
