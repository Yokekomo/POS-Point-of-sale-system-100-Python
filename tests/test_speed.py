"""Cuánto tarda cada pantalla, y que no tarde más cada mes que pasa.

Un programa de cocina se usa con las manos mojadas y el servicio empezando: si
la pantalla de hoy tarda medio segundo en abrir, nadie la abre. Y hay una
manera muy fácil de que eso pase sin enterarse —pedirle a la base de datos toda
la historia de la casa para enseñar lo que hay hoy—, porque el primer mes va
rápido y el problema aparece al año, ya en casa del cliente.

Por eso aquí se mide dos veces: con la casa recién puesta y con la misma casa
cargada de historia. Lo que se comprueba no es solo que sea rápida, sino que
**la historia no le pese**: las mismas consultas y prácticamente el mismo
tiempo con veinte mil pesadas dentro que con cien.
"""
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, insert

from thegrill import bench, db
from thegrill.meat import app as meatapp
from thegrill.models import LossKind, Primal, PrimalStatus, PrimalWeighing, Storage

HOY = date(2026, 9, 20)

# Lo que se abre todos los días, con lo que cuesta abrirlo. El presupuesto es
# ancho a propósito —la máquina que lo mida puede ser cualquiera— y aun así
# deja ver el desastre: cuando esto se escribió, la portada tardaba 199 ms y
# la cámara 376. Lo que no puede volver a pasar es el medio segundo.
PRESUPUESTO_MS = 400

# Y cuántas consultas puede hacer cada una. Esto sí es exacto: una consulta de
# más por pieza —el fallo clásico— se ve aquí y no en la cocina del cliente.
CONSULTAS = {
    "/hoy": 40,
    "/carne": 24,
    "/maduracion": 26,
    "/descongelado": 26,
    "/inventario": 22,
    "/parte": 60,
    "/traslados": 22,
    "/cortes": 20,
    "/ventas": 18,
}


@pytest.fixture(scope="module")
def casa(tmp_path_factory):
    """Una casa con su mes de trabajo dentro, servida por la app de verdad."""
    parche = pytest.MonkeyPatch()
    parche.setenv("GRILL_INSECURE_COOKIE", "1")     # aquí no hay certificado
    ruta = tmp_path_factory.mktemp("velocidad") / "v.db"
    db.init_engine(f"sqlite:///{ruta}")
    db.create_all()
    with db.session_scope() as session:
        bench.build(session, days=20, seed=5, until=HOY, multisite=True)
    with TestClient(meatapp.app, follow_redirects=False,
                    headers={"accept-language": "es"}) as client:
        respuesta = client.post("/login", data={"email": "ana0@banco.com",
                                                "password": bench.PASSWORD})
        assert respuesta.status_code == 303, respuesta.text[:200]
        yield client
    parche.undo()


def cronometra(client: TestClient, ruta: str, veces: int = 3) -> tuple[float, int]:
    """Los milisegundos que tarda y las consultas que hace, por visita."""
    client.get(ruta)                       # una de calentamiento: aquí no se mide
    cuenta = {"n": 0}
    contar = lambda *a, **k: cuenta.__setitem__("n", cuenta["n"] + 1)   # noqa: E731
    event.listen(db._engine, "before_cursor_execute", contar)
    try:
        empieza = time.perf_counter()
        for _ in range(veces):
            respuesta = client.get(ruta)
            assert respuesta.status_code == 200, f"{ruta}: {respuesta.status_code}"
        ms = (time.perf_counter() - empieza) / veces * 1000
    finally:
        event.remove(db._engine, "before_cursor_execute", contar)
    return ms, cuenta["n"] // veces


def carga_de_historia(pesadas: int = 20000) -> int:
    """Le mete a la casa años de pesadas, como las tendría a los tres años.

    No se simulan tres años día a día —eso son minutos de prueba—: se escriben
    las pesadas directamente, que es lo que la casa tendría dentro.

    Van colgadas de piezas **ya despiezadas**, que es como envejece una casa de
    verdad: la carne entra, madura, se corta y se va, y lo que queda son sus
    pesadas. Por eso la cámara de hoy sigue teniendo las mismas piezas y las
    pantallas del día no tienen ninguna excusa para tardar más.
    """
    with db.session_scope() as session:
        piezas = (session.query(Primal.id, Primal.serial, Primal.weight_kg,
                                Primal.restaurant_id)
                  .filter(Primal.status != PrimalStatus.IN_STOCK).all())
        assert piezas, "la casa de pruebas no tiene piezas despiezadas"
        filas = []
        for vuelta in range(pesadas // len(piezas) + 1):
            for pieza in piezas:
                filas.append({
                    "restaurant_id": pieza.restaurant_id, "primal_id": pieza.id,
                    "serial": pieza.serial, "date": HOY - timedelta(days=400 + vuelta),
                    "storage": Storage.AGING, "kind": LossKind.EVAPORATION,
                    "previous_kg": (pieza.weight_kg or 9.0) + 0.01,
                    "kg": pieza.weight_kg or 9.0, "loss_kg": 0.01,
                    "days": vuelta + 1, "source": "manual",
                })
        session.execute(insert(PrimalWeighing), filas)
        return len(filas)


# ----------------------------------------------------------------- lo de hoy
def test_every_screen_opens_before_anyone_notices(casa):
    """Ninguna pantalla de trabajo puede hacerse esperar."""
    lentas = []
    for ruta in CONSULTAS:
        ms, _ = cronometra(casa, ruta)
        if ms > PRESUPUESTO_MS:
            lentas.append(f"{ruta}: {ms:.0f} ms")
    assert not lentas, "por encima del presupuesto: " + ", ".join(lentas)


def test_no_screen_asks_the_same_thing_once_per_piece(casa):
    """Una consulta por pieza es el fallo que no se ve hasta que la casa crece."""
    pasadas = {}
    for ruta, techo in CONSULTAS.items():
        _, consultas = cronometra(casa, ruta)
        if consultas > techo:
            pasadas[ruta] = f"{consultas} > {techo}"
    assert not pasadas, f"demasiadas consultas: {pasadas}"


# ------------------------------------------------- y dentro de tres años, igual
def test_the_history_of_the_house_does_not_weigh_on_the_screens(casa):
    """La misma casa con años de pesadas dentro tiene que ir igual de rápida.

    Es la prueba que de verdad importa. Lo fácil es leer toda la historia para
    enseñar lo de hoy: el primer mes no se nota, y al año el cliente dice que
    el programa «se ha puesto lento». Aquí se le meten veinte mil pesadas de
    golpe y se exige lo mismo de antes: las mismas consultas, y nada de
    tiempos que se multipliquen.
    """
    antes = {ruta: cronometra(casa, ruta) for ruta in CONSULTAS}
    escritas = carga_de_historia()
    assert escritas >= 20000

    peores = []
    for ruta, (ms_antes, consultas_antes) in antes.items():
        ms, consultas = cronometra(casa, ruta)
        assert consultas == consultas_antes, (
            f"{ruta}: con historia hace {consultas} consultas y antes hacía "
            f"{consultas_antes}. Algo está leyendo la historia entera.")
        # Un poco más sí: la base de datos tiene más filas donde buscar. El
        # doble, no: eso es que la pantalla se está leyendo la historia. Y el
        # margen fijo es para que una pantalla que ya tardaba cuatro
        # milisegundos no salte por un suspiro de la máquina.
        if ms > ms_antes * 2 + 15:
            peores.append(f"{ruta}: {ms_antes:.0f} ms -> {ms:.0f} ms")
    assert not peores, "la historia les pesa: " + ", ".join(peores)
