"""La cola de la cámara: lo que se apunta sin señal y sale cuando la hay.

Es la pieza por la que se compra este programa —una cámara no tiene
cobertura— y la que más daño hace cuando falla, porque falla en silencio: el
teléfono dice «enviado», el carnicero sigue trabajando, y el recuento no está.

Lo que se guarda aquí es lo que la auditoría encontró roto:

- Que **no se dé por enviado lo que no se guardó**. El `fetch` seguía solo la
  redirección a la pantalla de entrar, recibía su 200 y borraba el apunte.
- Que **un rechazo no tape a los de detrás**. Un 403 de CSRF —móvil compartido
  entre el turno de mañana y el de tarde— congelaba el turno entero.
- Que la **llave contra duplicados viaje siempre**, no solo sin cobertura, que
  es justo el caso raro: con red, dos toques con guante apuntaban dos mermas.
"""
import socket
import threading
import time
from datetime import date

import pytest

from thegrill import bench, db

CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
MOVIL = {"width": 390, "height": 844}
HOY = date(2026, 9, 20)


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servidor(tmp_path_factory):
    import uvicorn

    from thegrill.meat import app as meatapp

    playwright = pytest.importorskip("playwright.sync_api")
    ruta = tmp_path_factory.mktemp("cola") / "cola.db"
    db.init_engine(f"sqlite:///{ruta}")
    db.create_all()
    with db.session_scope() as session:
        bench.build(session, days=5, seed=9, until=HOY)

    port = puerto_libre()
    config = uvicorn.Config(meatapp.app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    hilo = threading.Thread(target=server.run, daemon=True)
    hilo.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started
    yield f"http://127.0.0.1:{port}", playwright
    server.should_exit = True
    hilo.join(timeout=5)


@pytest.fixture(scope="module")
def navegador(servidor):
    import os

    base, playwright_module = servidor
    if not os.path.exists(CHROMIUM):
        pytest.skip("no hay navegador instalado en esta máquina")
    with playwright_module.sync_playwright() as pw:
        chromium = pw.chromium.launch(executable_path=CHROMIUM)
        yield base, chromium
        chromium.close()


def entra(page, base, correo="ana0@banco.com"):
    page.goto(f"{base}/login")
    page.fill("input[name=email]", correo)
    page.fill("input[name=password]", "clave-larga-1")
    page.click("button[type=submit]")
    page.wait_for_url(f"{base}/hoy")
    from thegrill.meat import tours, tutorial
    from thegrill.models import User
    with db.session_scope() as session:
        persona = session.query(User).filter_by(email=correo).one()
        for pantalla in tours.TOURS:
            tutorial.marcar(session, persona, pantalla)


def test_every_form_carries_its_key_against_duplicates(navegador):
    """Dos toques con guante no pueden apuntar dos mermas.

    El freno estaba puesto en el servidor y la llave solo se generaba en el
    camino de la cola: o sea, existía y estaba desconectada justo en el caso
    normal, que es el que pasa cien veces al día.
    """
    base, chromium = navegador
    contexto = chromium.new_context(viewport=MOVIL, has_touch=True)
    try:
        page = contexto.new_page()
        entra(page, base)
        page.goto(f"{base}/merma")
        page.wait_for_timeout(400)
        llave = page.evaluate(
            """() => {
                const f = document.querySelector('form[data-cola]');
                const e = f && f.querySelector('input[name=envio]');
                return e ? e.value : null;
            }""")
        assert llave, "el formulario no lleva llave contra duplicados"
        assert len(llave) > 6, llave
    finally:
        contexto.close()


def test_an_expired_session_does_not_swallow_what_was_noted(navegador):
    """Lo peor que hacía: decir «enviado» sin haber guardado nada.

    El `fetch` seguía solo la redirección a `/login`, recibía el 200 de esa
    pantalla y borraba el apunte de la cola. El recuento de la cámara
    desaparecía y nadie se enteraba hasta el cuadre del mes.
    """
    base, chromium = navegador
    contexto = chromium.new_context(viewport=MOVIL, has_touch=True)
    try:
        page = contexto.new_page()
        entra(page, base)
        page.goto(f"{base}/merma")
        page.wait_for_timeout(300)

        # Primero se tira la sesión —el móvil lleva horas en el bolsillo del
        # delantal— y después se apunta: apuntar intenta mandar en el acto, así
        # que al revés el envío salía con la sesión todavía buena.
        contexto.clear_cookies()
        page.evaluate("""() => window.cola.apuntar("/merma",
            {csrf: "daIgual", kg: "0.4", reason: "prueba"})""")
        page.wait_for_timeout(1500)

        quedan = page.evaluate("() => window.cola.pendientes()")
        apartados = page.evaluate("() => window.cola.apartados().length")
        assert quedan == 1, f"se ha tragado el apunte (quedan {quedan}, apartados {apartados})"
    finally:
        contexto.close()


def test_one_rejected_note_does_not_freeze_the_whole_shift(navegador):
    """Un 403 de CSRF —móvil compartido entre turnos— congelaba el turno entero.

    Se quedaba el primero de la fila y nada volvía a salir: mermas,
    descongelados y recepciones del resto del día se quedaban dentro del
    teléfono, con la wifi puesta. La única salida era borrar el almacén.
    """
    base, chromium = navegador
    contexto = chromium.new_context(viewport=MOVIL, has_touch=True)
    try:
        page = contexto.new_page()
        entra(page, base)
        page.goto(f"{base}/merma")
        page.wait_for_timeout(300)

        # Un rechazo de verdad. Ya no vale usar un token caducado: ahora la
        # cola coge el de ahora al mandar, así que ese caso —el más frecuente—
        # ha dejado de existir. El que queda es el otro: lo que el servidor no
        # puede aceptar por lo que es, no por quién lo manda.
        page.evaluate("""() => window.cola.apuntar("/pantalla-que-no-existe",
            {csrf: "x", kg: "0.4", reason: "rechazado"})""")
        page.wait_for_timeout(1800)

        assert page.evaluate("() => window.cola.apartados().length") == 1, \
            "el rechazado tenía que apartarse"
        assert page.evaluate("() => window.cola.pendientes()") == 0, \
            "y salir de la cola, para no tapar a los de detrás"

        # Y el panel lo enseña con sus botones, no como texto muerto.
        page.wait_for_timeout(300)
        assert page.locator("#colapanel [data-vuelve]").count() == 1
        assert page.locator("#colapanel [data-tira]").count() == 1
        page.locator("#colapanel [data-tira]").click()
        page.wait_for_timeout(200)
        assert page.evaluate("() => window.cola.apartados().length") == 0
    finally:
        contexto.close()


def test_the_butchery_sheet_survives_having_no_signal(navegador):
    """La hoja más cara de rellenar del programa, y se hace donde no hay señal.

    Peso de antes, merma y hasta diez cortes con sus kilos. Se guardaba para
    poder **abrirla** sin cobertura, pero no para mandarla: al darle a volcar
    se perdía entera. La segunda vez que le pasa, el carnicero coge un papel y
    ya no vuelve.
    """
    base, chromium = navegador
    contexto = chromium.new_context(viewport=MOVIL, has_touch=True)
    try:
        page = contexto.new_page()
        entra(page, base)
        page.goto(f"{base}/despiece")
        page.wait_for_timeout(400)
        assert page.locator("form[data-cola]").count() >= 1, \
            "la hoja de despiece no tiene cola"
        assert page.evaluate(
            """() => {
                const f = document.querySelector('form[data-cola]');
                const e = f && f.querySelector('input[name=envio]');
                return !!(e && e.value);
            }"""), "y sin llave, reintentar volcaría los cortes dos veces"
    finally:
        contexto.close()


def test_transfers_survive_having_no_signal(navegador):
    """El obrador manda carne al local desde el muelle, que es donde peor se ve
    la wifi de toda la casa."""
    base, chromium = navegador
    contexto = chromium.new_context(viewport=MOVIL, has_touch=True)
    try:
        page = contexto.new_page()
        entra(page, base)
        page.goto(f"{base}/traslados")
        page.wait_for_timeout(400)
        formularios = page.locator("form[data-cola]")
        assert formularios.count() >= 1, "los traslados no tienen cola"
        assert page.evaluate(
            """() => [...document.querySelectorAll('form[data-cola]')]
                     .every(f => f.querySelector('input[name=envio]'))"""), \
            "a algún formulario le falta la llave contra duplicados"
    finally:
        contexto.close()


def test_a_send_that_hangs_is_noted_and_the_person_keeps_working(navegador):
    """El caso de la cámara de verdad, y el que faltaba.

    `navigator.onLine` no dice si hay red: dice si hay una interfaz conectada
    a algo. En una cámara el punto de acceso se ve desde dentro y no se llega
    a ninguna parte, así que el navegador contesta que sí hay red, el envío
    sale y se queda colgado. La pantalla se quedaba quieta, sin decir nada, y
    la persona sin saber si lo suyo se había guardado —que es exactamente lo
    que este programa no se puede permitir—.

    Ahora el envío lleva un reloj: si no contesta, se corta, se apunta en la
    cola y quien está delante sigue trabajando.
    """
    base, chromium = navegador
    contexto = chromium.new_context(viewport=MOVIL, has_touch=True)
    try:
        page = contexto.new_page()
        entra(page, base)
        page.goto(f"{base}/merma")
        page.wait_for_timeout(400)

        # El punto de acceso se ve y no se llega: la petición sale y nadie
        # contesta nunca. El navegador sigue creyendo que hay red.
        contexto.route("**/merma", lambda ruta: None)
        assert page.evaluate("() => navigator.onLine") is True

        page.fill("input[name=kg]", "1,2")
        page.fill("input[name=reason]", "Caducado")
        page.click("form[data-cola] button[type=submit]")

        page.wait_for_function(
            "() => JSON.parse(localStorage.grill_cola || '[]').length === 1",
            timeout=15000)
        apuntado = page.evaluate("() => JSON.parse(localStorage.grill_cola)[0]")
        assert apuntado["accion"] == "/merma"
        assert apuntado["datos"]["kg"] == "1,2"
        assert apuntado["datos"]["reason"] == "Caducado"
        assert apuntado["datos"]["envio"], "sin llave no se puede repetir sin miedo"

        # Y se le dice, que es la mitad del arreglo: una pantalla quieta no
        # es una respuesta.
        page.wait_for_function(
            "() => (document.body.innerText || '').toLowerCase().includes('señal')"
            " || (document.body.innerText || '').toLowerCase().includes('apuntado')",
            timeout=15000)
        # Y el formulario queda limpio para la siguiente merma.
        assert page.input_value("input[name=kg]") == ""
    finally:
        contexto.close()


def test_and_when_the_link_is_good_nothing_changes(navegador):
    """El reloj no puede estorbar al noventa y nueve por ciento de las veces."""
    base, chromium = navegador
    contexto = chromium.new_context(viewport=MOVIL, has_touch=True)
    try:
        page = contexto.new_page()
        entra(page, base)
        page.goto(f"{base}/merma")
        page.wait_for_timeout(400)
        page.fill("input[name=kg]", "0,4")
        page.fill("input[name=reason]", "Prueba")
        page.click("form[data-cola] button[type=submit]")
        page.wait_for_timeout(1500)
        # Ha ido directo: nada en la cola y la pantalla ha contestado.
        assert page.evaluate("() => JSON.parse(localStorage.grill_cola || '[]').length") == 0
    finally:
        contexto.close()
