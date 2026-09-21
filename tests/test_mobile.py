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


def test_it_also_works_with_the_phone_turned_sideways(browser):
    """La cámara se mira de lado tantas veces como de pie."""
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


# ================================= la edición de cocina, en las mismas manos
KITCHEN = ["/manager", "/app", "/carne", "/merma", "/inventario", "/recetas",
           "/ventas", "/ingredientes", "/manager/registros", "/configuracion"]


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
