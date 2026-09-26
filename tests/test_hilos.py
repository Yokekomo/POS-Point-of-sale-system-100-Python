"""Que una persona guardando no deje a la casa entera sin pantallas.

Una ruta escrita con `async def` no corre en un hilo suyo: corre en **el** hilo
del servidor, el único que atiende a todo el mundo. Y dentro de esas rutas se
escribe en la base. Cuando dos guardan a la vez, la base hace esperar a la
segunda —hasta treinta segundos, que es la espera que tiene puesta—, y durante
esa espera el hilo del servidor está parado. No parado para quien esperaba:
parado para todos. Cuatro guardando a la vez y la casa se queda sin pantallas
un minuto largo, con el camión en el muelle y la cámara abierta.

Lo peor es que no sale en ningún registro. Nadie ha fallado: solo que nada
responde. Quien está delante le da otra vez, y otra, y acaba llamando por
teléfono.
"""
import inspect
import socket
import threading
import time

import pytest

from thegrill import db


def _rutas_async_con_base(app):
    """Las rutas que se atienden en el hilo del servidor y además tocan la base."""
    fuera = []
    for ruta in app.routes:
        funcion = getattr(ruta, "endpoint", None)
        if funcion is None or not inspect.iscoroutinefunction(funcion):
            continue
        parametros = set(inspect.signature(funcion).parameters)
        if {"session", "ctx"} & parametros:
            fuera.append(f"{getattr(ruta, 'path', '?')} ({funcion.__name__})")
    return fuera


def test_no_screen_of_the_meat_edition_writes_on_the_server_thread():
    """Ninguna ruta de carne se queda a trabajar en el hilo que atiende."""
    from thegrill.meat import app as programa
    assert _rutas_async_con_base(programa.app) == []


def test_no_screen_of_the_kitchen_edition_writes_on_the_server_thread():
    """Y ninguna de cocina, que comparte la misma base."""
    from thegrill.web import app as programa
    assert _rutas_async_con_base(programa.app) == []


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servido(tmp_path):
    """La edición de carne servida de verdad, con su hilo y su puerto."""
    import uvicorn

    from thegrill.meat import app as programa

    fichero = tmp_path / "hilos.db"
    db.init_engine(f"sqlite:///{fichero}")
    db.create_all()
    puerto = _puerto_libre()
    servidor = uvicorn.Server(uvicorn.Config(programa.app, host="127.0.0.1",
                                             port=puerto, log_level="error"))
    threading.Thread(target=servidor.run, daemon=True).start()
    for _ in range(300):
        if servidor.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("el servidor de pruebas no llegó a arrancar")
    yield f"http://127.0.0.1:{puerto}", fichero
    servidor.should_exit = True


def test_someone_waiting_to_write_does_not_stop_the_other_screens(servido):
    """Con la base cogida por otro, la portada sigue contestando al momento.

    Se coge la base desde fuera —como hace la persona que está guardando en ese
    instante— y se manda una petición que quiere escribir: esa se va a quedar
    esperando, y está bien que se quede. Lo que no puede pasar es que se lleve
    por delante a quien solo está mirando una pantalla.
    """
    import sqlite3

    import httpx

    base, fichero = servido
    otro = sqlite3.connect(fichero, timeout=30)
    otro.execute("PRAGMA journal_mode=WAL")
    otro.execute("BEGIN IMMEDIATE")          # la base, cogida
    try:
        lento = threading.Thread(
            target=lambda: httpx.post(f"{base}/solicitar", timeout=30,
                                      data={"restaurant_name": "Marina",
                                            "contact_name": "Albano",
                                            "email": "a@marina.com"}),
            daemon=True)
        lento.start()
        time.sleep(1.0)                      # tiempo de sobra para que se atasque

        empezo = time.monotonic()
        respuesta = httpx.get(f"{base}/precios", timeout=10)
        tardo = time.monotonic() - empezo
        assert respuesta.status_code == 200
        assert tardo < 5, (
            f"la página de precios ha tardado {tardo:.1f} s con la base cogida "
            "por otra persona: el hilo del servidor se ha quedado esperando y la "
            "casa entera se queda sin pantallas")
    finally:
        otro.rollback()
        otro.close()
