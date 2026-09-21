"""El programa en el teléfono y en la tablet, que es donde se trabaja.

El carnicero no lleva un ordenador a la cámara: lleva el móvil en el bolsillo
del delantal y lo toca con el guante puesto. El manager sí está delante de un
ordenador. Las dos cosas tienen que ir bien, y lo que se comprueba aquí es lo
que se rompe de verdad en una pantalla pequeña: que no haya que hacer scroll
lateral para leer, que lo que se toca se pueda tocar, que el teclado del móvil
salga con números cuando toca y que el iPhone no haga zoom al escribir.

Se abre un navegador de verdad —Chromium— en dos tamaños: un teléfono y una
tablet. Si no está instalado, la prueba se salta en vez de fallar.
"""
import socket
import threading
import time
from datetime import date

import pytest

from thegrill import bench, db

PHONE = {"width": 390, "height": 844}        # un iPhone de los normales
TABLET = {"width": 820, "height": 1180}      # un iPad en vertical
CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
HOY = date(2026, 9, 20)

# Las pantallas donde se trabaja con las manos ocupadas.
SCREENS = ["/hoy", "/carne", "/maduracion", "/descongelado", "/recepcion", "/despiece",
           "/merma", "/inventario", "/traslados", "/ventas", "/parte", "/fallo"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servidor(tmp_path_factory):
    """La casa de mentira, servida de verdad por su propio servidor."""
    import uvicorn

    from thegrill.meat import app as meatapp

    playwright = pytest.importorskip("playwright.sync_api")
    ruta = tmp_path_factory.mktemp("movil") / "movil.db"
    db.init_engine(f"sqlite:///{ruta}")
    db.create_all()
    with db.session_scope() as session:
        bench.build(session, days=6, seed=5, until=HOY)

    port = free_port()
    config = uvicorn.Config(meatapp.app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    hilo = threading.Thread(target=server.run, daemon=True)
    hilo.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "el servidor de pruebas no arrancó"
    yield f"http://127.0.0.1:{port}", playwright
    server.should_exit = True
    hilo.join(timeout=5)


@pytest.fixture(scope="module")
def browser(servidor):
    """Un solo navegador para todas: dos a la vez no se llevan bien."""
    import os

    base, playwright_module = servidor
    if not os.path.exists(CHROMIUM):
        pytest.skip("no hay navegador instalado en esta máquina")
    with playwright_module.sync_playwright() as pw:
        chromium = pw.chromium.launch(executable_path=CHROMIUM)
        yield base, chromium
        chromium.close()


def telefono(chromium):
    """Un contexto de teléfono, con su pantalla y sus dedos.

    Sin `is_mobile`: ese modo del navegador de pruebas inventa una ventana
    interior más alta que la visible, y lo que está pegado abajo aparece fuera
    de la pantalla. Lo que se quiere probar —dedo, ancho y reglas de móvil— lo
    dan el tamaño y `has_touch`.
    """
    return chromium.new_context(viewport=PHONE, device_scale_factor=3, has_touch=True,
                                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like "
                                           "Mac OS X) AppleWebKit/605.1.15")


def entra(page, base: str):
    page.goto(f"{base}/login")
    page.fill("input[name=email]", "ana0@banco.com")
    page.fill("input[name=password]", "clave-larga-1")
    page.click("button[type=submit]")
    page.wait_for_url(f"{base}/hoy")


@pytest.fixture(scope="module")
def phone_pages(browser):
    """Una pantalla de teléfono, ya dentro, para mirarla de arriba abajo."""
    base, chromium = browser
    context = telefono(chromium)
    page = context.new_page()
    entra(page, base)
    yield base, page
    context.close()


# ------------------------------------------------- nada se sale de la pantalla
def test_no_screen_needs_sideways_scrolling_on_a_phone(phone_pages):
    """Leer un número no puede obligar a arrastrar la página de lado."""
    base, page = phone_pages
    anchos = {}
    for ruta in SCREENS:
        page.goto(f"{base}{ruta}")
        anchos[ruta] = page.evaluate(
            "() => [document.documentElement.scrollWidth, window.innerWidth]")
    desbordadas = {r: v for r, v in anchos.items() if v[0] > v[1] + 1}
    assert not desbordadas, desbordadas


def test_the_tables_scroll_inside_their_card_not_the_whole_page(phone_pages):
    """Una tabla ancha se desliza sola: la página se queda quieta."""
    base, page = phone_pages
    page.goto(f"{base}/carne")
    assert page.locator(".scroll").count() > 0
    desbordadas = page.evaluate("""() => {
        const fuera = [];
        for (const t of document.querySelectorAll('table')) {
            const caja = t.closest('.scroll');
            if (!caja) fuera.push(t.parentElement.className || 'sin caja');
        }
        return fuera;
    }""")
    assert desbordadas == [], desbordadas


# ------------------------------------------------------ se puede tocar con dedos
def test_everything_you_tap_is_big_enough_for_a_finger(phone_pages):
    """Con guante y prisa, un enlace de treinta píxeles no se acierta."""
    base, page = phone_pages
    pequeños = []
    for ruta in ("/hoy", "/carne", "/descongelado", "/maduracion"):
        page.goto(f"{base}{ruta}")
        pequeños += page.evaluate("""() => {
            const malos = [];
            for (const el of document.querySelectorAll('nav a, button, .btn, .tile')) {
                const caja = el.getBoundingClientRect();
                if (caja.width === 0 && caja.height === 0) continue;   // oculto
                if (caja.height < 40) malos.push(el.tagName + ':' + el.innerText.slice(0, 18)
                                                 + ':' + Math.round(caja.height));
            }
            return malos;
        }""")
    assert pequeños == [], pequeños[:8]


def test_the_keyboard_comes_up_with_numbers_where_kilos_are_typed(phone_pages):
    """Escribir 8,340 con el teclado de letras es perder el turno."""
    base, page = phone_pages
    malos = []
    for ruta in ("/recepcion", "/descongelado", "/maduracion", "/merma"):
        page.goto(f"{base}{ruta}")
        malos += page.evaluate("""() => {
            const malos = [];
            for (const el of document.querySelectorAll('input')) {
                const nombre = (el.name || '').toLowerCase();
                const numerico = /kg|peso|pieces|piezas|grams|gramos|price|precio|index/.test(nombre);
                const tipo = (el.type || '').toLowerCase();
                if (numerico && !el.inputMode && tipo !== 'number') malos.push(nombre);
            }
            return malos;
        }""")
    assert malos == [], malos[:8]


def test_the_iphone_does_not_zoom_in_when_you_start_typing(phone_pages):
    """Con menos de 16 píxeles, Safari hace zoom y descoloca la pantalla."""
    base, page = phone_pages
    page.goto(f"{base}/recepcion")
    pequeñas = page.evaluate("""() => {
        const malas = [];
        for (const el of document.querySelectorAll('input, select, textarea')) {
            const px = parseFloat(getComputedStyle(el).fontSize);
            if (px < 16) malas.push((el.name || el.tagName) + ':' + px);
        }
        return malas;
    }""")
    assert pequeñas == [], pequeñas[:8]


# ------------------------------------------------------------ y se puede trabajar
def test_a_butcher_can_actually_work_from_the_phone(browser):
    """La prueba de verdad: dar de alta una pieza con el pulgar."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        page = context.new_page()
        entra(page, base)

        page.goto(f"{base}/recepcion")
        page.fill("input[name=lot]", "MOVIL-1")
        page.fill("input[name='serial:0']", "7777")
        page.fill("input[name='kg:0']", "9,4")
        page.fill("input[name='price:0']", "30")
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")

        assert "7777" in page.content()
        with db.session_scope() as session:
            from thegrill.models import Primal
            pieza = session.query(Primal).filter_by(serial="7777").one()
            assert pieza.weight_kg == 9.4
    finally:
        context.close()


def test_the_manager_screens_hold_up_on_a_tablet(browser):
    """El manager cierra el mes en la tablet, no en el móvil."""
    base, chromium = browser
    context = chromium.new_context(viewport=TABLET, has_touch=True)
    try:
        page = context.new_page()
        entra(page, base)
        for ruta in ("/hoy", "/inventario", "/ventas", "/sedes", "/parte"):
            page.goto(f"{base}{ruta}")
            ancho, ventana = page.evaluate(
                "() => [document.documentElement.scrollWidth, window.innerWidth]")
            assert ancho <= ventana + 1, (ruta, ancho, ventana)
    finally:
        context.close()


# ------------------------------------------- lo que dicen las buenas prácticas
def test_pinch_to_zoom_is_never_blocked(phone_pages):
    """Quien no ve bien tiene que poder acercar. Bloquearlo es dejarle fuera."""
    base, page = phone_pages
    page.goto(f"{base}/hoy")
    meta = page.get_attribute("meta[name=viewport]", "content")
    assert "user-scalable=no" not in meta and "maximum-scale" not in meta
    assert "width=device-width" in meta
    assert "viewport-fit=cover" in meta          # para que las zonas seguras existan


def test_the_notch_and_the_home_bar_do_not_eat_the_screen(phone_pages):
    """Con `viewport-fit=cover` hay que apartarse de la muesca a mano."""
    base, page = phone_pages
    page.goto(f"{base}/hoy")
    hoja = page.evaluate("() => [...document.querySelectorAll('style')].map(s => s.textContent).join('')")
    assert "env(safe-area-inset-left)" in hoja and "env(safe-area-inset-bottom)" in hoja


def test_tapping_has_no_three_hundred_millisecond_wait(phone_pages):
    """El retardo del doble toque se quita diciendo que no se hace zoom al tocar."""
    base, page = phone_pages
    page.goto(f"{base}/hoy")
    assert page.eval_on_selector("nav a", "el => getComputedStyle(el).touchAction") == "manipulation"
    assert page.eval_on_selector(
        "html", "el => getComputedStyle(el).webkitTextSizeAdjust") == "100%"


def test_the_serial_column_stays_put_while_the_table_scrolls(phone_pages):
    """Los kilos de la derecha no dicen nada si no se ve de qué pieza son."""
    base, page = phone_pages
    page.goto(f"{base}/maduracion")
    quieta = page.evaluate("""() => {
        for (const caja of document.querySelectorAll('.scroll')) {
            const fila = caja.querySelector('table tr:nth-child(2) td:first-child');
            if (!fila || caja.scrollWidth <= caja.clientWidth + 4) continue;
            const antes = fila.getBoundingClientRect().left;
            caja.scrollLeft = 220;
            const despues = fila.getBoundingClientRect().left;
            return {antes, despues, izquierda: caja.getBoundingClientRect().left};
        }
        return null;
    }""")
    if quieta is None:
        pytest.skip("hoy no hay ninguna tabla más ancha que la pantalla")
    assert quieta["despues"] >= quieta["izquierda"] - 1


def test_turned_sideways_the_menu_moves_to_the_side(browser):
    """Tumbado sobra ancho y falta alto: la barra de abajo estorba, la columna no.

    Es la misma pantalla girada, y cada postura pide una cosa: de pie manda el
    pulgar y sobra ancho; tumbado es al revés.
    """
    base, chromium = browser
    context = chromium.new_context(viewport={"width": 844, "height": 390}, has_touch=True)
    try:
        page = context.new_page()
        entra(page, base)
        for ruta in ("/hoy", "/carne", "/descongelado"):
            page.goto(f"{base}{ruta}")
            ancho, ventana = page.evaluate(
                "() => [document.documentElement.scrollWidth, window.innerWidth]")
            assert ancho <= ventana + 1, (ruta, ancho, ventana)
        assert page.locator("aside.side").is_visible(), "tumbado no sale la columna"
        assert not page.locator("nav.tabs").is_visible(), "tumbado sobra la barra de abajo"
        # Y la columna se estrecha: en 390 píxeles de alto no caben lujos.
        assert page.locator("aside.side").bounding_box()["width"] <= 200
    finally:
        context.close()


def test_a_tablet_standing_up_gets_the_bottom_bar_and_lying_down_the_column(browser):
    """La tablet del pase se gira cada dos por tres."""
    base, chromium = browser
    for medidas, de_pie in (({"width": 820, "height": 1180}, True),
                            ({"width": 1180, "height": 820}, False)):
        context = chromium.new_context(viewport=medidas, has_touch=True)
        try:
            page = context.new_page()
            entra(page, base)
            page.goto(f"{base}/carne")
            assert page.locator("nav.tabs").is_visible() == de_pie, medidas
            assert page.locator("aside.side").is_visible() != de_pie, medidas
        finally:
            context.close()


def test_the_menu_on_a_phone_is_a_bottom_bar_and_a_drawer(phone_pages):
    """Veinte enlaces amontonados tapan la pantalla.

    Lo que hace todo el mundo y funciona: abajo lo de todos los días, donde
    llega el pulgar, y el resto en un cajón que se abre desde «Más».
    """
    base, page = phone_pages
    page.goto(f"{base}/hoy")

    barra = page.locator("nav.tabs")
    assert barra.is_visible(), "no hay barra de abajo"
    enlaces = barra.locator("a")
    assert 3 <= enlaces.count() <= 5, enlaces.count()
    caja = barra.bounding_box()
    assert caja["y"] + caja["height"] >= PHONE["height"] - 2, "la barra no está abajo"
    for i in range(enlaces.count()):
        assert enlaces.nth(i).bounding_box()["height"] >= 44

    # La cabecera no se come la pantalla.
    assert page.locator(".topbar").bounding_box()["height"] < 120

    cajon = page.locator("#menu")
    assert not cajon.is_visible()
    page.click("nav.tabs a[href='#menu']")
    assert cajon.is_visible(), "el cajón no se abre"
    assert cajon.locator("a.item").count() >= 10, "el cajón no trae el resto de pantallas"
    page.click("#menu .close")
    assert not cajon.is_visible(), "el cajón no se cierra"


def test_the_menu_on_a_computer_is_a_column_on_the_left(browser):
    """El manager trabaja en pantalla grande: las secciones, a la vista y agrupadas."""
    base, chromium = browser
    context = chromium.new_context(viewport={"width": 1280, "height": 860})
    try:
        page = context.new_page()
        entra(page, base)
        page.goto(f"{base}/carne")

        lado = page.locator("aside.side")
        assert lado.is_visible(), "no hay columna de menú"
        assert lado.locator("a.item").count() >= 12
        assert lado.locator(".group").count() >= 3, "las secciones no están agrupadas"
        assert not page.locator("nav.tabs").is_visible(), "la barra de abajo sobra aquí"

        # La pantalla en la que estás se ve marcada, que es la mitad de un menú.
        activo = page.locator("aside.side a.item.on")
        assert activo.count() == 1 and "/carne" in activo.get_attribute("href")
    finally:
        context.close()


# ------------------------------------- el color, el que diga el dispositivo
def test_the_public_pages_follow_the_phone_theme(browser):
    """Una pantalla de entrar negra en un móvil en claro parece otro programa.

    El programa por dentro ya seguía al dispositivo; la portada y las páginas
    públicas iban forzadas a oscuro.
    """
    base, chromium = browser
    for esquema, fondo_claro in (("light", True), ("dark", False)):
        context = chromium.new_context(viewport=PHONE, has_touch=True,
                                       color_scheme=esquema)
        try:
            page = context.new_page()
            for ruta in ("/", "/login", "/precios", "/cookies"):
                page.goto(f"{base}{ruta}")
                fondo = page.eval_on_selector("body", "el => getComputedStyle(el).backgroundColor")
                claro = sum(int(x) for x in fondo.strip("rgb()").split(",")[:3]) > 380
                assert claro == fondo_claro, (esquema, ruta, fondo)
                # Y lo escrito se lee: nada de texto claro sobre fondo claro.
                texto = page.eval_on_selector("h1", "el => getComputedStyle(el).color")
                assert (sum(int(x) for x in texto.strip("rgb()").split(",")[:3]) > 380) != claro
        finally:
            context.close()


def test_the_work_screens_follow_it_too(browser):
    """Lo de dentro ya lo hacía, y tiene que seguir haciéndolo."""
    base, chromium = browser
    for esquema, fondo_claro in (("light", True), ("dark", False)):
        context = chromium.new_context(viewport=TABLET, has_touch=True, color_scheme=esquema)
        try:
            page = context.new_page()
            entra(page, base)
            page.goto(f"{base}/carne")
            fondo = page.eval_on_selector("body", "el => getComputedStyle(el).backgroundColor")
            assert (sum(int(x) for x in fondo.strip("rgb()").split(",")[:3]) > 380) == fondo_claro
        finally:
            context.close()


# ============================ contar en la cámara, donde no hay cobertura
def test_a_count_survives_having_no_signal_in_the_chiller(browser):
    """Dentro de una cámara no hay señal, y eso no puede costar un recuento.

    Lo que se teclea se guarda en el propio teléfono, y cuando vuelve la
    cobertura se manda solo. Nadie cuenta dos veces por una raya de menos.
    """
    from thegrill import db
    from thegrill.models import IngredientLot, MeatCount, Restaurant, User
    from thegrill.web import inventory

    base, chromium = browser
    with db.session_scope() as session:
        rest = (session.query(Restaurant).filter(Restaurant.platform.isnot(True))
                .order_by(Restaurant.id).first())
        ana = session.query(User).filter_by(email="ana0@banco.com").one()
        abierto = inventory.open_now(session, rest.id)
        if abierto is None:
            abierto = inventory.open_count(session, ana, on=HOY)
        serial = abierto.lines[0].serial if abierto.lines else None
    if not serial:
        pytest.skip("la casa de pruebas no tiene nada que contar")

    context = telefono(chromium)
    try:
        page = context.new_page()
        entra(page, base)
        page.goto(f"{base}/inventario")
        page.wait_for_timeout(600)          # que el ayudante guarde su copia
        campo = page.locator(f"input[name='kg:{serial}']")
        assert campo.count() == 1, "no hay hoja de recuento abierta"

        # Se entra en la cámara: se acaba la cobertura.
        context.set_offline(True)
        campo.fill("7.5")          # el campo es numérico: punto, como el teclado
        page.click("form[data-keep] button[type=submit]")
        # El recuento se da por hecho aunque no haya red: se apunta en la cola
        # del propio teléfono y la pantalla sigue adelante.
        page.wait_for_timeout(200)
        assert page.evaluate("() => JSON.parse(localStorage.grill_cola || '[]').length") == 1
        assert page.locator("#colapanel").is_visible()

        # La pantalla se vuelve a abrir sin señal —el móvil se bloqueó y el
        # navegador tiró la pestaña— y se sirve la copia guardada, con lo que
        # está esperando a la vista.
        page.goto(f"{base}/inventario")
        page.wait_for_timeout(300)
        assert page.locator(f"input[name='kg:{serial}']").count() == 1, (
            "sin señal no se sirvió la copia: " + page.title() + " · " +
            page.evaluate("() => navigator.serviceWorker.controller ? 'con ayudante' : 'sin ayudante'"))
        assert page.locator("#colapanel").is_visible()

        # Se sale de la cámara y vuelve la señal: se manda solo, sin que nadie
        # se acuerde de volver a darle al botón.
        assert page.evaluate("() => JSON.parse(localStorage.grill_cola || '[]').length") == 1
        context.set_offline(False)
        # El aviso de «ya hay red» se da varias veces si hace falta: en una
        # máquina cargada, el primero puede llegar cuando el navegador todavía
        # no tiene la red lista, y entonces el envío falla y se queda esperando
        # —que es justo lo que tiene que hacer—. Un cocinero abre la pantalla
        # otra vez; aquí se repite el aviso.
        for intento in range(6):
            page.evaluate("() => window.dispatchEvent(new Event('online'))")
            try:
                page.wait_for_function(
                    "() => JSON.parse(localStorage.grill_cola || '[]').length === 0",
                    timeout=5000)
                break
            except Exception:
                if intento == 5:
                    pendiente = page.evaluate(
                        "() => localStorage.grill_cola")
                    aviso = page.locator("#sinred").text_content()
                    raise AssertionError(
                        f"el recuento no se mandó al volver la red: {pendiente} · {aviso}")
        with db.session_scope() as session:
            from thegrill.models import MeatCountLine
            contadas = [l.counted_kg for l in session.query(MeatCountLine)
                        .filter_by(serial=serial) if l.counted_kg is not None]
            assert pytest.approx(7.5) in contadas, ("el recuento no llegó", contadas)
    finally:
        context.set_offline(False)
        context.close()


def test_the_helper_that_makes_screens_open_inside_the_chiller(browser):
    """Sin él, volver a abrir la pantalla sin señal da la del dinosaurio."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        page = context.new_page()
        entra(page, base)
        page.wait_for_timeout(600)
        assert page.evaluate("() => !!navigator.serviceWorker.controller") or \
            page.evaluate("() => navigator.serviceWorker.getRegistrations().then(r => r.length > 0)")
        # Y lo que se manda nunca se guarda: un POST sin red falla, como debe.
        codigo = page.evaluate("async () => (await (await fetch('/sw.js')).text())")
        assert "req.method !== 'GET'" in codigo
    finally:
        context.close()


# ================================= la edición de cocina, en las mismas manos
KITCHEN = ["/manager", "/app", "/carne", "/merma", "/inventario", "/recetas",
           "/ventas", "/ingredientes", "/manager/registros", "/configuracion"]


def test_a_delivery_is_booked_one_piece_at_a_time_with_its_label(browser):
    """En el muelle se coge una bolsa, se le hace la foto y se apunta.

    La pantalla de antes abría con ocho líneas y un botón de añadir ocho más.
    Nadie descarga así. Lo que se comprueba aquí es el recorrido entero de una
    pieza: la foto se ve antes de guardar —para saber que ha salido legible—,
    la pieza entra con su foto pegada, y lo del camión se queda escrito para la
    siguiente, que es lo que evita teclear el matadero veinte veces.
    """
    import os
    import tempfile

    from thegrill.models import Primal

    base, chromium = browser
    context = telefono(chromium)
    page = context.new_page()
    entra(page, base)
    page.goto(f"{base}/recepcion")
    page.wait_for_selector("input[name='kg:0']")

    # Una pieza cada vez, y sin botón de añadir líneas.
    assert page.locator("#piezas, [name='serial:1']").count() == 0
    assert page.locator("#masfilas").count() == 0

    # La foto se ve en cuanto se hace, sin salir de la pantalla.
    foto = os.path.join(tempfile.mkdtemp(), "etiqueta.png")
    with open(foto, "wb") as fh:
        fh.write(PNG_DE_UN_PIXEL)
    assert page.locator("#fotovista").is_hidden()
    page.set_input_files("#lafoto", foto)
    page.wait_for_selector("#fotovista", state="visible", timeout=3000)
    assert page.locator("#fotodicho").is_visible()

    page.locator("details:has(#producer_plant)").evaluate("d => d.open = true")
    page.fill("#lot", "L-MOVIL")
    page.fill("#sku", "Ribeye AUS")
    page.fill("#producer_plant", "Teys Biloela")
    page.fill("#price_kg", "32")
    page.fill("input[name='serial:0']", "9300")
    page.fill("input[name='kg:0']", "9.2")
    page.click("form[action='/recepcion'] button[type=submit]")
    page.wait_for_selector(".banner.ok", timeout=8000)

    with db.session_scope() as session:
        pieza = session.query(Primal).filter_by(serial="9300").one()
        assert pieza.producer_plant == "Teys Biloela"
        assert pieza.photo_ref, "la foto no viajó con la pieza"

    # Y lo del camión sigue escrito para la siguiente bolsa.
    assert page.input_value("#lot") == "L-MOVIL"
    assert page.input_value("#producer_plant") == "Teys Biloela"
    assert page.input_value("input[name='kg:0']") == ""      # la pieza, en blanco
    context.close()


# Un PNG de un píxel: lo justo para que el navegador lo dé por foto.
PNG_DE_UN_PIXEL = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def test_the_butchery_sheet_shows_only_the_boxes_that_apply(browser):
    """«A peso» era una casilla con dos columnas a cada lado, y solo valía una.

    La hoja abría con diez filas por ocho columnas y cuatro huecos por corte de
    los que la mitad sobraban según cómo saliera ese corte. Nadie sabía cuáles.
    Ahora se elige cómo sale y se enseña solo la pareja que toca; la casilla de
    verdad —la que viaja en el envío— la mueve el desplegable.
    """
    base, chromium = browser
    context = telefono(chromium)
    page = context.new_page()
    entra(page, base)
    page.goto(f"{base}/despiece")
    page.wait_for_selector("#cortes .corte")

    # Tres bloques a la vista, no diez huecos vacíos.
    assert page.locator("#cortes .corte").count() == 3

    primero = page.locator("#cortes .corte").first
    # En raciones: piezas y gramos; los kilos, escondidos.
    assert primero.locator("input[name='pieces:0']").is_visible()
    assert primero.locator("input[name='grams:0']").is_visible()
    assert primero.locator("input[name='kg:0']").is_hidden()
    assert not primero.locator("input[name='weight:0']").is_checked()

    primero.locator(".comosale").select_option("peso")
    assert primero.locator("input[name='kg:0']").is_visible()
    assert primero.locator("input[name='pieces:0']").is_hidden()
    assert primero.locator("input[name='grams:0']").is_hidden()
    # Y la casilla que de verdad se manda ha seguido al desplegable.
    assert primero.locator("input[name='weight:0']").is_checked()

    primero.locator(".comosale").select_option("piezas")
    assert not primero.locator("input[name='weight:0']").is_checked()

    # Y se añaden bloques sin perder lo escrito.
    primero.locator("input[name='cut:0']").fill("Entrecot")
    page.click("#mascortes")
    assert page.locator("#cortes .corte").count() == 4
    assert page.input_value("input[name='cut:0']") == "Entrecot"
    assert page.input_value("input[name='cut:3']") == ""     # el nuevo, en blanco
    context.close()


def test_you_can_keep_working_with_no_signal(browser):
    """Sin señal se sigue trabajando: se apunta todo y se manda solo después.

    En un restaurante la señal falla a ratos, y el trabajo no espera. Lo que se
    comprueba aquí es el caso entero: se corta la red, se hacen **dos** cosas
    seguidas —una merma y otra merma— sin que la pantalla se quede colgada,
    vuelve la señal y las dos llegan a la base de datos, en orden y una sola
    vez cada una.
    """
    from thegrill.models import IngredientMovement, MovementKind

    base, chromium = browser
    context = telefono(chromium)
    page = context.new_page()
    entra(page, base)
    page.goto(f"{base}/merma")
    page.wait_for_selector("form[data-cola]")

    with db.session_scope() as session:
        antes = (session.query(IngredientMovement)
                 .filter_by(kind=MovementKind.WASTE, source="waste").count())

    context.set_offline(True)
    corte = page.locator("#ingredient_id option").nth(1).get_attribute("value")
    for kg, razon in (("0.05", "hueso"), ("0.03", "grasa")):
        page.select_option("#ingredient_id", corte)
        page.fill("input[name=kg]", kg)
        page.fill("input[name=reason]", razon)
        page.click("form[data-cola] button[type=submit]")
        page.wait_for_timeout(150)
    # Se ve lo que está esperando, con su hora: nadie se queda a ciegas.
    assert page.locator("#colapanel").is_visible()
    assert page.evaluate("() => JSON.parse(localStorage.grill_cola).length") == 2

    context.set_offline(False)
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    for _ in range(8):
        page.wait_for_timeout(1000)
        if page.evaluate("() => JSON.parse(localStorage.grill_cola || '[]').length") == 0:
            break
    pendientes = page.evaluate("() => JSON.parse(localStorage.grill_cola || '[]')")
    assert pendientes == [], f"quedó algo sin mandar: {pendientes}"

    with db.session_scope() as session:
        ahora = (session.query(IngredientMovement)
                 .filter_by(kind=MovementKind.WASTE, source="waste").all())
        razones = [m.source_ref or "" for m in ahora]
        assert len(ahora) == antes + 2, razones[-4:]
        assert sum("hueso" in r for r in razones) == 1
        assert sum("grasa" in r for r in razones) == 1
    context.close()


def test_losing_the_signal_says_so_and_getting_it_back_says_so_too(browser):
    """Quedarse sin señal se avisa arriba; volver se avisa y el aviso se va.

    Quien está contando tiene que enterarse **antes** de escribir veinte pesos,
    no al darle a guardar. Y cuando vuelve la cobertura, un cartel fijo
    diciendo que todo va bien estorba: lo dice y se quita.
    """
    base, chromium = browser
    context = telefono(chromium)
    page = context.new_page()
    entra(page, base)
    page.goto(f"{base}/carne")

    assert page.locator("#avisored.se-ve").count() == 0      # con red, ni se ve

    context.set_offline(True)
    page.evaluate("() => window.dispatchEvent(new Event('offline'))")
    aviso = page.locator("#avisored")
    aviso.wait_for(state="visible", timeout=3000)
    assert "se-ve" in (aviso.get_attribute("class") or "")
    assert "malo" in (aviso.get_attribute("class") or "")
    assert aviso.inner_text().strip()

    # Y se queda puesto: no hay señal, y eso no es una noticia de dos segundos.
    page.wait_for_timeout(1200)
    assert "se-ve" in (aviso.get_attribute("class") or "")

    context.set_offline(False)
    page.evaluate("() => window.dispatchEvent(new Event('online'))")
    page.wait_for_function(
        "() => document.getElementById('avisored').classList.contains('bien')",
        timeout=3000)
    # Y se va solo.
    page.wait_for_function(
        "() => !document.getElementById('avisored').classList.contains('se-ve')",
        timeout=8000)
    context.close()


def test_the_same_submission_arriving_twice_is_written_once(browser):
    """El reintento de un envío que sí llegó no puede tirar kilos dos veces."""
    from thegrill.models import IngredientMovement, MovementKind

    base, chromium = browser
    context = telefono(chromium)
    page = context.new_page()
    entra(page, base)
    page.goto(f"{base}/merma")

    with db.session_scope() as session:
        antes = (session.query(IngredientMovement)
                 .filter_by(kind=MovementKind.WASTE, source="waste").count())

    # El mismo envío, con su mismo número, mandado dos veces: es lo que hace
    # un teléfono cuando la primera respuesta se pierde por el camino.
    enviado = page.evaluate("""async () => {
      const csrf = document.querySelector("input[name=csrf]").value;
      const corte = document.querySelector("#ingredient_id option:nth-child(2)").value;
      const cuerpo = new URLSearchParams({csrf: csrf, kg: "0.04", reason: "repetida",
                                          ingredient_id: corte,
                                          envio: "prueba-mismo-numero"});
      const uno = await fetch("/merma", {method: "POST", body: cuerpo,
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        credentials: "same-origin"});
      const dos = await fetch("/merma", {method: "POST", body: cuerpo,
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        credentials: "same-origin"});
      return [uno.status, dos.status];
    }""")
    assert all(200 <= c < 400 for c in enviado), enviado

    with db.session_scope() as session:
        ahora = (session.query(IngredientMovement)
                 .filter_by(kind=MovementKind.WASTE, source="waste").all())
        repetidas = [m for m in ahora if "repetida" in (m.source_ref or "")]
        assert len(ahora) == antes + 1, [m.source_ref for m in ahora[-3:]]
        assert len(repetidas) == 1
    context.close()


def test_the_menu_remembers_how_you_left_it(browser):
    """Plegar un grupo tiene que durar más que hasta el próximo clic."""
    base, chromium = browser
    context = chromium.new_context(viewport={"width": 1280, "height": 700})
    page = context.new_page()
    entra(page, base)

    dia = page.locator("details.grp").first
    assert dia.evaluate("g => g.open")                 # el del día viene abierto
    control = page.locator("details.grp").nth(1)
    assert not control.evaluate("g => g.open")         # los demás, recogidos

    control.locator("summary").click()
    assert control.evaluate("g => g.open")
    page.goto(f"{base}/carne")                         # otra pantalla, otro grupo
    assert page.locator("details.grp").nth(1).evaluate("g => g.open"), (
        "el menú se olvidó de que ese grupo quedó abierto")

    # Y al plegarlo, también se acuerda.
    page.locator("details.grp").nth(1).locator("summary").click()
    page.goto(f"{base}/hoy")
    assert not page.locator("details.grp").nth(1).evaluate("g => g.open")
    context.close()


def test_the_whole_menu_fits_without_a_scrollbar(browser):
    """Con los grupos recogidos, el menú entra en una ventana de portátil."""
    base, chromium = browser
    context = chromium.new_context(viewport={"width": 1280, "height": 620})
    page = context.new_page()
    entra(page, base)
    sobra = page.evaluate("() => {const s = document.querySelector('.side');"
                          "return s.scrollHeight - s.clientHeight}")
    assert sobra <= 0, f"al menú le sobran {sobra} px y sale la barra"
    assert page.locator(".side .foot button").is_visible()     # salir, a la vista
    context.close()


# Esta va **antes** de la edición de cocina a propósito: aquella monta su
# propia base de datos y este proceso solo sabe hablar con una, así que a
# partir de ahí la casa de carnes ya no existe y no se puede ni entrar.
def test_the_button_for_browser_alerts_really_does_something(browser):
    """Pulsar «Activar» tiene que pedir el permiso de verdad.

    Aquí no vale mirar el HTML: el guion iba sin el número que exige la
    política de seguridad, así que el navegador lo tiraba sin decir nada y el
    botón se quedaba muerto. La página se veía perfecta. Solo se ve pulsando.

    El permiso se finge —el de verdad lo concede el navegador y no se puede
    dejar «sin decidir» a voluntad—, así que lo que se comprueba es lo que
    falló: que el guion se ejecuta, que escribe debajo, que el botón llama a
    pedir el permiso y que después se quita solo.
    """
    base, chromium = browser
    context = telefono(chromium)          # su propia ventana: no arrastra nada
    page = context.new_page()
    fallos = []
    page.on("console", lambda m: fallos.append(m.text) if m.type == "error" else None)
    page.add_init_script("""
        window.__pedido = false;
        window.Notification = {
          permission: "default",
          requestPermission: function () {
            window.__pedido = true;
            window.Notification.permission = "granted";
            return Promise.resolve("granted");
          }
        };
    """)
    entra(page, base)
    page.goto(f"{base}/notificaciones")

    page.wait_for_selector("#askperm", state="visible", timeout=5000)
    assert page.locator("#permstate").inner_text().strip(), "el guion no se ha ejecutado"

    page.click("#askperm")
    page.wait_for_function("() => window.__pedido === true", timeout=5000)
    page.wait_for_selector("#askperm", state="hidden", timeout=5000)
    assert page.locator("#permstate").inner_text().strip()

    bloqueado = [f for f in fallos if "Content Security Policy" in f]
    assert not bloqueado, f"el navegador tiró el guion: {bloqueado[:1]}"
    context.close()


def _pasa_algo(texto_lote="L-AVISO", kind="RECEPCION"):
    """Alguien **de otra pantalla** da de alta carne. Escrito a mano en la base
    porque lo que se prueba aquí es el aviso, no la recepción."""
    from thegrill.models import Novedad, Restaurant, User

    with db.session_scope() as session:
        casa = (session.query(Restaurant)
                .filter(Restaurant.platform.isnot(True)).first())
        yo = session.query(User).filter_by(email="ana0@banco.com").one()
        otro = (session.query(User)
                .filter(User.restaurant_id == casa.id, User.id != yo.id).first())
        fila = Novedad(restaurant_id=casa.id, kind=kind, ref=texto_lote,
                       label="Ribeye AUS", pieces=6, kg=54.2, site_id=None,
                       by_user_id=(otro.id if otro else yo.id + 999),
                       by_name="Marta")
        session.add(fila)
        session.flush()
        return fila.id


# Esta también va antes de la edición de cocina, por lo mismo: a partir de ahí
# la casa de carnes ya no existe en este proceso.
def test_what_just_happened_shows_up_and_the_x_puts_it_away(browser):
    """Ha entrado carne mientras estabas en otra pantalla: te enteras arriba.

    Y te enteras **con una X**: se lee, se quita, y no vuelve ni al recargar.
    Un aviso que resucita cada vez que cambias de pantalla se deja de mirar a
    los diez minutos, y entonces ya no avisa de nada.
    """
    base, chromium = browser
    context = telefono(chromium)
    page = context.new_page()
    entra(page, base)                     # la primera entrada pone el punto de partida
    page.wait_for_function("() => localStorage.getItem('grill_novedad_suelo') !== null",
                           timeout=5000)

    _pasa_algo()
    page.goto(f"{base}/inventario")       # otra pantalla cualquiera: el aviso va en todas
    aviso = page.locator(".aviso-red.nuevo")
    aviso.wait_for(state="visible", timeout=5000)
    assert "Ribeye AUS" in aviso.inner_text()
    assert "L-AVISO" in aviso.inner_text()      # con qué lote: es lo que manda en FEFO
    assert "Marta" in aviso.inner_text()        # y quién lo metió

    # Y no tapa el título de la pantalla: el contenido se aparta lo que mide.
    hueco = page.evaluate("() => parseInt(getComputedStyle(document.querySelector('main'))"
                          ".paddingTop, 10)")
    caja = aviso.bounding_box()
    assert hueco >= caja["height"], f"el aviso mide {caja['height']} y solo aparta {hueco}"

    page.click(".aviso-red.nuevo .x")
    aviso.wait_for(state="hidden", timeout=3000)
    # Al quitarlo, la pantalla recupera su sitio.
    page.wait_for_function("() => !document.body.classList.contains('con-aviso')",
                           timeout=3000)

    page.reload()
    page.wait_for_timeout(700)
    assert page.locator(".aviso-red.nuevo").count() == 0, "el aviso cerrado ha vuelto"
    context.close()


def test_the_signal_notice_and_the_news_do_not_cover_each_other(browser):
    """Dos carteles fijos en el mismo sitio se tapan y no se lee ninguno.

    Van en la misma columna, uno debajo de otro, y el de la señal arriba: sin
    cobertura, lo que hay que ver primero es que no hay cobertura.
    """
    base, chromium = browser
    context = telefono(chromium)
    page = context.new_page()
    entra(page, base)
    page.wait_for_function("() => localStorage.getItem('grill_novedad_suelo') !== null",
                           timeout=5000)

    _pasa_algo(texto_lote="L-JUNTOS")
    page.goto(f"{base}/carne")
    page.locator(".aviso-red.nuevo").wait_for(state="visible", timeout=5000)

    page.evaluate("() => window.dispatchEvent(new Event('offline'))")
    page.wait_for_selector("#avisored.se-ve", timeout=3000)

    señal = page.locator("#avisored").bounding_box()
    nueva = page.locator(".aviso-red.nuevo").bounding_box()
    assert señal["y"] + señal["height"] <= nueva["y"] + 1, (señal, nueva)

    # Y la pantalla se aparta lo que miden los dos, no lo que mide uno.
    hueco = page.evaluate("() => parseInt(getComputedStyle(document.querySelector('main'))"
                          ".paddingTop, 10)")
    assert hueco >= señal["height"] + nueva["height"], hueco
    context.close()


@pytest.fixture(scope="module")
def cocina(tmp_path_factory):
    """La plataforma de cocina, servida aparte: tiene su propia plantilla."""
    import uvicorn

    from thegrill.web import app as webapp

    playwright_module = pytest.importorskip("playwright.sync_api")
    ruta = tmp_path_factory.mktemp("cocina") / "cocina.db"
    db.init_engine(f"sqlite:///{ruta}")
    db.create_all()
    port = free_port()
    config = uvicorn.Config(webapp.app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    hilo = threading.Thread(target=server.run, daemon=True)
    hilo.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "el servidor de cocina no arrancó"

    import httpx
    alta = httpx.post(f"http://127.0.0.1:{port}/signup",
                      data={"restaurant": "Casa Pepe", "name": "Ana",
                            "email": "ana@casa.com", "password": "clave-larga-1"},
                      follow_redirects=False)
    assert alta.status_code == 303, alta.text[:200]
    yield f"http://127.0.0.1:{port}", playwright_module
    server.should_exit = True
    hilo.join(timeout=5)


@pytest.fixture(scope="module")
def cocina_phone(cocina, browser):
    base_cocina, _ = cocina
    _, chromium = browser
    context = telefono(chromium)
    page = context.new_page()
    page.goto(f"{base_cocina}/login")
    page.fill("input[name=email]", "ana@casa.com")
    page.fill("input[name=password]", "clave-larga-1")
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    yield base_cocina, page
    context.close()


def test_the_kitchen_edition_also_fits_in_a_phone(cocina_phone):
    """La otra edición tiene su propia plantilla: los mismos arreglos o ninguno."""
    base, page = cocina_phone
    desbordadas = {}
    for ruta in KITCHEN:
        page.goto(f"{base}{ruta}")
        ancho, ventana = page.evaluate(
            "() => [document.documentElement.scrollWidth, window.innerWidth]")
        if ancho > ventana + 1:
            desbordadas[ruta] = (ancho, ventana)
    assert not desbordadas, desbordadas


def test_the_kitchen_edition_can_be_tapped_with_a_finger(cocina_phone):
    base, page = cocina_phone
    pequeños = []
    for ruta in ("/manager", "/carne", "/merma"):
        page.goto(f"{base}{ruta}")
        pequeños += page.evaluate("""() => {
            const malos = [];
            for (const el of document.querySelectorAll('nav a, button, .btn, .tile')) {
                const caja = el.getBoundingClientRect();
                if (caja.width === 0 && caja.height === 0) continue;
                if (caja.height < 40) malos.push(el.tagName + ':' + el.innerText.slice(0, 18));
            }
            return malos;
        }""")
    assert pequeños == [], pequeños[:8]


def test_the_kitchen_edition_keeps_the_same_rules(cocina_phone):
    """Zonas seguras, sin retardo al tocar y zoom permitido: lo mismo que la otra."""
    base, page = cocina_phone
    page.goto(f"{base}/manager")
    meta = page.get_attribute("meta[name=viewport]", "content")
    assert "user-scalable=no" not in meta and "maximum-scale" not in meta
    hoja = page.evaluate(
        "() => [...document.querySelectorAll('style')].map(s => s.textContent).join('')")
    assert "env(safe-area-inset-left)" in hoja and "pointer:coarse" in hoja
    assert page.eval_on_selector("nav a", "el => getComputedStyle(el).touchAction") == "manipulation"


# --------------------------------------------- el aviso de cookies, que se va
def test_the_cookie_notice_goes_away_and_stays_away(browser):
    """Pulsar «Entendido» y que el aviso vuelva en la siguiente página es lo mismo
    que no tener botón.

    Pasaron dos cosas: la regla de estilo del aviso le ganaba al atributo que lo
    esconde, y luego lo que se recordaba vivía en el navegador —se borraba al
    cerrar y no viajaba entre ventanas—. Ahora lo recuerda el servidor con una
    cookie técnica, que es justo para lo que sirven.
    """
    base, chromium = browser
    context = telefono(chromium)
    try:
        page = context.new_page()
        page.goto(f"{base}/")
        assert page.locator("#cookiebar").is_visible(), "el aviso no aparece la primera vez"

        page.click("#cookiebar button")
        page.wait_for_load_state("networkidle")
        assert page.locator("#cookiebar").count() == 0, "el aviso no se va al pulsar"

        for ruta in ("/", "/precios", "/cookies", "/login"):
            page.goto(f"{base}{ruta}")
            assert page.locator("#cookiebar").count() == 0, ruta

        # Y en una ventana nueva del mismo navegador tampoco vuelve.
        otra = context.new_page()
        otra.goto(f"{base}/")
        assert otra.locator("#cookiebar").count() == 0, "vuelve en una ventana nueva"
    finally:
        context.close()


def test_login_opens_as_a_window_without_leaving_the_page(browser):
    """Entrar sin perder de vista lo que estabas leyendo."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        page = context.new_page()
        page.goto(f"{base}/")
        ventana = page.locator("#entrar")
        assert not ventana.is_visible(), "la ventana de entrar sale sola"

        page.click("header nav a[href='#entrar']")
        assert ventana.is_visible(), "no se abre al pulsar Entrar"
        assert page.locator("#entrar input[name=email]").is_visible()

        page.click("#entrar .close")
        assert not ventana.is_visible(), "no se cierra con la equis"

        # Y la página de siempre sigue ahí para quien llegue directo.
        page.goto(f"{base}/login")
        assert page.locator("#password").is_visible()      # el de la página, no el de la ventana
    finally:
        context.close()


def test_the_notice_fits_and_can_be_tapped_on_a_phone(browser):
    base, chromium = browser
    context = telefono(chromium)
    try:
        page = context.new_page()
        page.goto(f"{base}/")
        caja = page.locator("#cookiebar").bounding_box()
        boton = page.locator("#cookiebar button").bounding_box()
        assert caja["width"] <= PHONE["width"], caja
        assert boton["height"] >= 40, boton
        ancho, ventana = page.evaluate(
            "() => [document.documentElement.scrollWidth, window.innerWidth]")
        assert ancho <= ventana + 1
    finally:
        context.close()
