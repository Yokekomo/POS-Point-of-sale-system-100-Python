"""El tutorial guiado: sale una vez, lo ve quien le toca, y no vuelve.

La primera vez que alguien entra en un apartado le salen dos, tres o cuatro
globos que le dicen qué hacer allí. Lo que se comprueba aquí es lo que se
rompe de verdad con esto:

- Que salga una vez y **no vuelva**, ni al recargar ni al día siguiente.
- Que la marca sea **de la persona y no del aparato**: en una cocina el móvil
  y la tablet se comparten, y si la marca viviera en el navegador el primero
  que entrara se llevaría el tutorial de todos los demás.
- Que un paso que habla de dinero **no llegue** al carnicero. Ni escondido:
  ni en el HTML ni en el JSON, que el código fuente de una página lo lee
  cualquiera desde el móvil.
- Que se pueda **saltar** siempre, con el dedo y con guante.
- Que funcione **sin línea**, que es donde está la cámara.
"""
import json
import re
import socket
import threading
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from thegrill import bench, db
from thegrill.meat import app as meatapp
from thegrill.meat import tours, tutorial
from thegrill.models import Role, TourVisto, User
from thegrill.web import i18n

from tests import meat_helpers as helpers
from tests.meat_helpers import csrf_from

SPANISH = {"accept-language": "es"}
HOY = date(2026, 9, 20)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'tutorial.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        helpers.signup(c)
        yield c


def pasos_de(html: str):
    """Los pasos que el servidor le ha mandado de verdad a esa página."""
    trozo = re.search(r"var PASOS = (\[.*?\]);", html, re.S)
    return json.loads(trozo.group(1)) if trozo else None


def token(client) -> str:
    """Un token de la sesión. `/merma` la abre cualquiera y trae formulario."""
    return csrf_from(client.get("/merma").text)


def quien(email="albano@marina.com") -> int:
    with db.session_scope() as session:
        return session.query(User).filter_by(email=email).one().id


# ------------------------------------------------------- sale una vez y no vuelve
def test_the_first_time_someone_opens_a_screen_the_tutorial_is_there(client):
    """Nadie lee un manual antes de empezar: el manual sale solo, encima."""
    pasos = pasos_de(client.get("/recepcion").text)
    assert pasos, "la primera visita a recepción no trajo tutorial"
    assert [p["selector"] for p in pasos] == ["lote", "pieza", "llegada"]
    assert pasos[0]["titulo"] == "Primero, el lote"


def test_once_you_have_seen_it_it_does_not_come_back(client):
    """Salir cada mañana al entrar en recepción sería un castigo."""
    assert pasos_de(client.get("/recepcion").text)
    visto = client.post("/tour/visto", data={
        "pantalla": "recepcion", "completo": "1",
        "csrf": csrf_from(client.get("/recepcion").text)})
    assert visto.status_code == 200, visto.text[:200]
    assert pasos_de(client.get("/recepcion").text) is None


def test_asking_for_it_again_brings_it_back(client):
    """El botón «?»: el que quiere repasar no tiene que borrar nada."""
    marcar(client, "recepcion")
    assert pasos_de(client.get("/recepcion").text) is None
    assert pasos_de(client.get("/recepcion?tour=1").text)


def test_a_new_version_of_the_tutorial_shows_up_again(client, monkeypatch):
    """Si cambia la pantalla, el tutorial viejo ya no sirve de nada."""
    marcar(client, "recepcion")
    assert pasos_de(client.get("/recepcion").text) is None
    viejo = tours.TOURS["recepcion"]
    monkeypatch.setitem(tours.TOURS, "recepcion",
                        tours.Tour(pantalla="recepcion", version=viejo.version + 1,
                                   pasos=viejo.pasos))
    assert pasos_de(client.get("/recepcion").text), "una versión nueva tiene que volver a salir"


def test_the_shared_tablet_does_not_steal_anyones_tutorial(client):
    """En la cocina hay una tablet y seis manos: la marca es de la persona."""
    paco = helpers.add_user(client, email="paco@marina.com", name="Paco",
                            role=Role.BUTCHER)
    marcar(client, "recepcion")                      # el manager ya lo ha visto
    assert pasos_de(client.get("/recepcion").text) is None
    assert pasos_de(paco.get("/recepcion").text), "a Paco le tenía que salir el suyo"


def marcar(client, pantalla, completo="1"):
    """Dar por visto un tutorial, como hace el navegador al terminarlo."""
    respuesta = client.post("/tour/visto", data={
        "pantalla": pantalla, "completo": completo, "csrf": token(client)})
    assert respuesta.status_code == 200, respuesta.text[:200]
    return respuesta


# ---------------------------------------------------- el dinero no se le escapa
def test_the_butcher_never_receives_a_step_that_talks_about_money(client):
    """No basta con no enseñárselo: no puede ni llegarle."""
    paco = helpers.add_user(client, email="paco@marina.com", name="Paco",
                            role=Role.BUTCHER)
    pagina = paco.get("/merma").text
    pasos = pasos_de(pagina)
    assert [p["selector"] for p in pasos] == ["apuntar"], pasos
    # Y el texto del paso de dinero tampoco está escondido en el código fuente.
    assert i18n.t("es", "m.tour.mer2.b") not in pagina
    assert "coste" not in json.dumps(pasos, ensure_ascii=False)


def test_the_manager_does_get_the_money_step(client):
    """Y quien lleva la casa sí: es la mitad de lo que hay que entender."""
    pasos = pasos_de(client.get("/merma").text)
    assert [p["selector"] for p in pasos] == ["apuntar", "coste"]


def test_the_assistant_gets_the_plain_steps_and_nothing_else(client):
    """El ayudante apunta el día; de lo que cuesta, no se le habla."""
    leo = helpers.add_user(client, email="leo@marina.com", name="Leo",
                           role=Role.EMPLOYEE)
    assert [p["selector"] for p in pasos_de(leo.get("/merma").text)] == ["apuntar"]
    assert [p["selector"] for p in pasos_de(leo.get("/inventario").text)] == ["contar", "ados"]


def test_a_tutorial_made_only_of_money_steps_never_appears_to_anyone_else(client):
    """Precios es toda dinero: al que no ve dinero no le sale ni el globo."""
    con_dinero = tours.pasos_para(tours.TOURS["precios"], Role.MANAGER)
    sin_dinero = tours.pasos_para(tours.TOURS["precios"], Role.BUTCHER)
    assert con_dinero and not sin_dinero
    with db.session_scope() as session:
        paco = User(restaurant_id=1, name="Paco", email="p@x.com", role=Role.BUTCHER)
        assert tutorial.para(session, paco, "/recepcion/precios", "es") is None


# ---------------------------------------------------------------- la puerta
def test_the_mark_does_not_travel_without_its_token(client):
    """Sin token no se escribe nada: ni esto, que parece inofensivo."""
    respuesta = client.post("/tour/visto", data={"pantalla": "recepcion", "csrf": "no"})
    assert respuesta.status_code == 403
    assert pasos_de(client.get("/recepcion").text), "no se tenía que haber marcado"


def test_nobody_marks_a_tutorial_for_somebody_else(client):
    """La persona sale de la sesión, no de lo que mande el navegador."""
    paco = helpers.add_user(client, email="paco@marina.com", name="Paco",
                            role=Role.BUTCHER)
    marcar(paco, "recepcion")
    with db.session_scope() as session:
        marcas = session.query(TourVisto).filter_by(pantalla="recepcion").all()
        assert [m.user_id for m in marcas] == [quien("paco@marina.com")]
    assert pasos_de(client.get("/recepcion").text), "el manager no lo ha visto todavía"


def test_a_screen_that_does_not_exist_cannot_be_marked(client):
    """Si no, cualquiera llena la tabla con nombres inventados."""
    respuesta = client.post("/tour/visto", data={
        "pantalla": "la-luna", "csrf": token(client)})
    assert respuesta.status_code == 404
    with db.session_scope() as session:
        assert session.query(TourVisto).count() == 0


def test_the_version_comes_from_the_registry_not_from_the_browser(client):
    """Mandar «versión 99» no puede callar un tutorial para siempre."""
    client.post("/tour/visto", data={
        "pantalla": "recepcion", "version": "99", "csrf": token(client)})
    with db.session_scope() as session:
        marca = session.query(TourVisto).filter_by(pantalla="recepcion").one()
        assert marca.version == tours.TOURS["recepcion"].version


def test_nobody_gets_a_tutorial_before_logging_in(client):
    """Y quien no ha entrado no recibe nada de la casa."""
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    assert pasos_de(fuera.get("/login").text) is None


# -------------------------------------------------------------- el registro
def test_no_tutorial_is_longer_than_four_steps(client):
    """Cinco globos ya no es un tutorial: es un manual, y no se lee de pie."""
    largos = {n: len(t.pasos) for n, t in tours.TOURS.items()
              if len(t.pasos) > tours.MAX_PASOS}
    assert not largos, largos


def test_every_tutorial_text_is_written_in_every_language(client):
    """Una casa en árabe no puede recibir un globo en español."""
    claves = sorted({c for t in tours.TOURS.values()
                     for p in t.pasos for c in (p.titulo, p.texto)})
    faltan = {lang: [c for c in claves if c not in i18n.TRANSLATIONS[lang]]
              for lang in i18n.TRANSLATIONS}
    assert not any(faltan.values()), {k: v for k, v in faltan.items() if v}


# La plantilla que dibuja cada pantalla. Escrito a mano y no adivinado: si
# mañana un tutorial se queda sin sitio donde agarrarse, esta lista es lo que
# lo dice.
PLANTILLAS = {
    "recepcion": "meat/reception.html", "precios": "meat/prices.html",
    "despiece": "meat/butchery.html", "maduracion": "meat/aging.html",
    "carne": "meat/chamber.html", "descongelado": "meat/defrost.html",
    "recuento": "meat/defrost.html", "merma": "web/waste.html",
    "traslados": "meat/transfers.html", "inventario": "web/inventory.html",
    "trazabilidad": "web/tracing.html", "parte": "meat/report.html",
    "carta": "meat/menu.html",
}


def test_every_step_points_at_something_that_exists_on_its_screen(client):
    """Un paso que ilumina un elemento que no está deja el tutorial cojo.

    Esto caza un selector renombrado o un elemento quitado, que es lo que más
    pasa. Lo que **no** puede ver es un `data-tour` metido dentro de un
    `{% if %}`: está en el fichero y no está en la pantalla hasta que la casa
    tiene una hoja de inventario abierta o piezas esperando precio. Eso es
    legítimo, y lo que hay que garantizar entonces —que el tutorial no se dé
    por visto el día que no se pudo enseñar— lo comprueba
    `test_a_tutorial_that_could_not_be_shown_is_not_burned`, con navegador.
    """
    assert sorted(PLANTILLAS) == sorted(tours.TOURS), "falta decir qué plantilla dibuja qué"
    raiz = helpers.__file__.rsplit("/tests/", 1)[0] + "/thegrill/"
    huerfanos = []
    for pantalla, tour in sorted(tours.TOURS.items()):
        plantilla = open(raiz + PLANTILLAS[pantalla].replace("/", "/templates/"),
                         encoding="utf-8").read()
        for paso in tour.pasos:
            if f'data-tour="{paso.selector}"' not in plantilla:
                huerfanos.append(f"{pantalla} -> {paso.selector}")
    assert not huerfanos, huerfanos


def test_every_screen_with_a_tutorial_is_a_screen_that_opens(client):
    """Una ruta mal escrita en el registro es un tutorial que no sale nunca."""
    rotas = [ruta for ruta in sorted(tours.RUTAS)
             if client.get(ruta).status_code not in (200, 303)]
    assert not rotas, rotas


def test_the_replay_button_is_only_where_there_is_something_to_replay(client):
    """El «?» en una pantalla sin tutorial sería un botón que no hace nada."""
    assert 'class="tour-otra-vez"' in client.get("/recepcion").text
    assert 'class="tour-otra-vez"' not in client.get("/configuracion").text


def test_the_library_only_travels_to_the_screens_that_use_it(client):
    """Veintiún kilobytes no se bajan en una pantalla que no los usa."""
    assert "/static/tour/driver.js" in client.get("/recepcion").text
    assert "/static/tour/driver.js" not in client.get("/configuracion").text


def test_the_library_is_ours_and_comes_with_its_licence(client):
    """Nada de fuera: en la cámara no hay línea, y una CDN es una fuga."""
    guion = client.get("/static/tour/driver.js")
    assert guion.status_code == 200 and len(guion.content) > 5000
    assert client.get("/static/tour/driver.css").status_code == 200
    licencia = (helpers.__file__.rsplit("/tests/", 1)[0]
                + "/thegrill/meat/static/tour/LICENSE")
    assert "MIT" in open(licencia, encoding="utf-8").read()


def test_the_tutorial_is_kept_for_when_there_is_no_signal(client):
    """La cámara no tiene cobertura: si no está guardado, no abre."""
    sw = client.get("/sw.js").text
    assert "/static/tour/driver.js" in sw and "/static/tour/driver.css" in sw


# ============================================================ en el teléfono
# Lo de arriba comprueba lo que manda el servidor. Esto abre un navegador de
# verdad y mira lo que le pasa a quien lo tiene en la mano: que el globo salga,
# que se pueda saltar con el guante puesto, que no vuelva, y que en la cámara
# —sin línea— siga funcionando.
PHONE = {"width": 390, "height": 844}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servidor(tmp_path_factory):
    """La casa de mentira, servida por su propio servidor."""
    import uvicorn

    playwright = pytest.importorskip("playwright.sync_api")
    ruta = tmp_path_factory.mktemp("tutorial") / "tutorial.db"
    db.init_engine(f"sqlite:///{ruta}")
    db.create_all()
    with db.session_scope() as session:
        bench.build(session, days=4, seed=5, until=HOY)

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
    """El navegador. Dónde buscarlo y qué hacer si no está lo decide
    `conftest.abre_navegador`: en el ordenador de alguien se salta, y en el
    servidor de integración se cae. Antes se buscaba en una sola ruta escrita
    a mano —la de la máquina donde se escribió— y en cualquier otra estas
    pruebas se saltaban calladas, con la suite en verde.
    """
    from conftest import abre_navegador

    base, playwright_module = servidor
    with playwright_module.sync_playwright() as pw:
        chromium = abre_navegador(pw)
        yield base, chromium
        chromium.close()


def telefono(chromium, **extra):
    return chromium.new_context(viewport=PHONE, device_scale_factor=3, has_touch=True,
                                **extra)


def entra(page, base: str, correo="ana0@banco.com"):
    page.goto(f"{base}/login")
    page.fill("input[name=email]", correo)
    page.fill("input[name=password]", "clave-larga-1")
    page.click("button[type=submit]")
    page.wait_for_url(f"{base}/hoy")


def test_the_tutorial_opens_by_itself_and_can_be_skipped_with_a_glove(browser):
    """Sale solo, se lee, y para quitárselo de encima basta un dedo."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        page = context.new_page()
        entra(page, base)
        page.goto(f"{base}/recepcion")
        globo = page.locator(".driver-popover")
        globo.wait_for(state="visible", timeout=5000)
        assert "Primero, el lote" in globo.inner_text()
        # Por dónde va: un tutorial sin cuenta atrás no se sabe si acaba.
        assert "1" in page.locator(".driver-popover-progress-text").inner_text()

        # Saltar está siempre a la vista, desde el primer paso, y se puede
        # tocar con guante: cuarenta y ocho píxeles de alto.
        saltar = page.locator(".driver-popover-close-btn")
        assert saltar.is_visible() and "Saltar" in saltar.inner_text()
        for boton in page.locator(".driver-popover-footer button").all():
            assert boton.bounding_box()["height"] >= 48, boton.inner_text()

        saltar.click()
        page.wait_for_selector(".driver-popover", state="detached", timeout=3000)
        page.wait_for_timeout(400)          # que le dé tiempo a decírselo al servidor

        # Y no vuelve: ni al recargar ni mañana.
        page.goto(f"{base}/recepcion")
        page.wait_for_timeout(500)
        assert page.locator(".driver-popover").count() == 0
    finally:
        context.close()


def test_the_question_mark_brings_it_back_for_whoever_wants_a_second_look(browser):
    """Lo de arriba lo quitó para siempre; el «?» lo devuelve cuando hace falta."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        page = context.new_page()
        entra(page, base, correo="paco0@banco.com")
        page.goto(f"{base}/merma")
        page.locator(".driver-popover-close-btn").click()
        page.wait_for_timeout(400)
        page.goto(f"{base}/merma")
        page.wait_for_timeout(400)
        assert page.locator(".driver-popover").count() == 0
        page.locator(".tour-otra-vez").click()
        page.wait_for_selector(".driver-popover", state="visible", timeout=5000)
    finally:
        context.close()


def test_in_the_chamber_without_signal_the_tutorial_still_works(browser):
    """La cámara no tiene cobertura, y es donde más falta hace saber qué hacer."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        page = context.new_page()
        entra(page, base, correo="eva0@banco.com")
        page.goto(f"{base}/hoy")
        page.wait_for_timeout(900)          # que el ayudante guarde su copia

        context.set_offline(True)
        page.goto(f"{base}/traslados")
        globo = page.locator(".driver-popover")
        globo.wait_for(state="visible", timeout=5000)
        page.locator(".driver-popover-close-btn").click()
        page.wait_for_timeout(300)
        # No se pierde: se apunta en la cola del teléfono y sale cuando vuelva
        # la línea. Si no, el tutorial saldría otra vez mañana.
        cola = page.evaluate("() => JSON.parse(localStorage.grill_cola || '[]')")
        assert [c["accion"] for c in cola] == ["/tour/visto"], cola
        assert cola[0]["datos"]["pantalla"] == "traslados"
    finally:
        context.set_offline(False)
        context.close()


def test_in_arabic_the_tutorial_reads_from_the_right(browser):
    """Una casa en árabe lee de derecha a izquierda, y el globo también."""
    base, chromium = browser
    with db.session_scope() as session:
        session.query(User).filter_by(email="leo0@banco.com").one().language = "ar"
    context = telefono(chromium, locale="ar")
    try:
        page = context.new_page()
        entra(page, base, correo="leo0@banco.com")
        page.goto(f"{base}/merma")
        page.wait_for_selector(".driver-popover", state="visible", timeout=5000)
        assert page.evaluate("() => document.documentElement.dir") == "rtl"
        assert page.evaluate(
            "() => getComputedStyle(document.querySelector('.driver-popover')).direction"
        ) == "rtl"
        # Y el «siguiente» no se queda donde el ojo busca el «atrás».
        assert page.evaluate(
            "() => getComputedStyle(document.querySelector('.driver-popover-footer'))"
            ".flexDirection") == "row-reverse"
    finally:
        context.close()


def test_the_tutorial_is_not_printed_on_the_sheet_that_hangs_in_the_pass(browser):
    """El parte del día se cuelga en el pase: sin globos encima."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        page = context.new_page()
        entra(page, base, correo="marta0@banco.com")
        page.goto(f"{base}/parte")
        page.wait_for_selector(".driver-popover", state="visible", timeout=5000)
        page.emulate_media(media="print")
        assert not page.locator(".driver-popover").is_visible()
        assert not page.locator(".tour-otra-vez").is_visible()
        page.emulate_media(media="screen")
    finally:
        context.close()


def test_the_tutorial_does_not_break_the_wall_the_page_puts_up(browser):
    """La página solo deja correr sus propios guiones: el tutorial es uno suyo."""
    base, chromium = browser
    context = telefono(chromium)
    quejas = []
    try:
        page = context.new_page()
        page.on("console", lambda m: quejas.append(m.text)
                if "Content Security Policy" in m.text else None)
        page.on("pageerror", lambda e: quejas.append(str(e)))
        entra(page, base)
        for ruta in ("/carne", "/maduracion", "/despiece", "/inventario"):
            page.goto(f"{base}{ruta}")
            page.wait_for_timeout(400)
        assert quejas == [], quejas[:4]
    finally:
        context.close()


# ============================================ que no se gaste el que no se vio
# Tres fallos de la misma raíz, encontrados midiendo una casa nueva y vacía:
#
# 1. Si ninguno de los elementos que el tutorial ilumina está en la página —no
#    hay inventario abierto, no hay nada que trasladar—, se apuntaba como visto
#    y no volvía nunca. En una casa nueva se quemaban así dos tutoriales
#    enteros el primer día, sin que los viera nadie.
# 2. Si estaban algunos pero no todos, salía recortado y se apuntaba completo
#    igual. En `/maduracion` se perdía para siempre el paso que explica que lo
#    aprovechado lleva coste y lo tirado no.
# 3. «Saltar» y «Entendido» se apuntaban los dos como terminado, aunque la
#    columna `completo` lleva desde el principio el comentario «falso: lo saltó».

def de_cero(correo: str, pantalla: str) -> None:
    """Freno de contraseñas a cero y ese tutorial sin ver.

    Las pruebas de este fichero comparten servidor y base: sin esto, una prueba
    de más arriba deja el tutorial ya marcado y la de abajo mide otra cosa.
    """
    from thegrill.meat import security

    with db.session_scope() as session:
        # Con la sesión: el freno tiene una parte en memoria y otra en la base,
        # y sin la segunda los intentos acumulados del módulo siguen contando.
        security.reset(session=session)
        if pantalla:
            quien = session.query(User).filter_by(email=correo).one()
            (session.query(TourVisto)
             .filter_by(user_id=quien.id, pantalla=pantalla).delete())


def test_a_tutorial_that_could_not_be_shown_is_not_burned(browser):
    """Un día sin nada que enseñar no puede gastar el tutorial de esa pantalla."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        de_cero("paco0@banco.com", "merma")
        page = context.new_page()
        entra(page, base, correo="paco0@banco.com")
        # Se le quitan a la página los anclajes del tutorial, tachándolos del
        # HTML antes de que llegue al navegador. Es exactamente lo que pasa
        # cuando la pantalla está vacía: el `{% if %}` no los dibuja. Se hace
        # así y no con un guion porque el del tutorial va en línea y corre
        # antes de que ningún guion nuestro pueda tocar nada.
        def sin_anclajes(ruta):
            respuesta = ruta.fetch()
            # Solo fuera de los `<script>`: dentro está la cadena
            # `'[data-tour="'` con la que el guion busca los anclajes, y
            # renombrarla también dejaría el tutorial funcionando igual —que es
            # justo el agujero en el que cayó la primera versión de esta prueba.
            trozos = re.split(r"(<script\b.*?</script>)", respuesta.text(), flags=re.S)
            cuerpo = "".join(t if t.startswith("<script")
                             else t.replace('data-tour="', 'data-nada="')
                             for t in trozos)
            ruta.fulfill(response=respuesta, body=cuerpo)

        page.route("**/merma", sin_anclajes)
        page.goto(f"{base}/merma")
        page.wait_for_timeout(700)
        assert page.locator(".driver-popover").count() == 0, "salió sin sus anclajes"

        with db.session_scope() as session:
            paco = session.query(User).filter_by(email="paco0@banco.com").one()
            fila = (session.query(TourVisto)
                    .filter_by(user_id=paco.id, pantalla="merma").first())
        assert fila is None, "se dio por visto un tutorial que no se pudo enseñar"
    finally:
        context.close()


def test_skipping_is_written_down_as_skipping(browser):
    """La columna existe desde el principio y nunca se escribía en falso."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        de_cero("ana0@banco.com", "despiece")
        page = context.new_page()
        entra(page, base, correo="ana0@banco.com")
        page.goto(f"{base}/despiece")            # cuatro pasos: hay dónde saltar
        page.wait_for_selector(".driver-popover", state="visible", timeout=5000)
        page.locator(".driver-popover-close-btn").click()   # saltar en el primero
        page.wait_for_timeout(500)

        with db.session_scope() as session:
            ana = session.query(User).filter_by(email="ana0@banco.com").one()
            fila = (session.query(TourVisto)
                    .filter_by(user_id=ana.id, pantalla="despiece").one())
        assert fila.completo is False, "saltar se apuntó como terminado"
    finally:
        context.close()


def test_finishing_is_written_down_as_finishing(browser):
    """Y quien llega al final sí lo ha visto entero: no se le llama saltador."""
    base, chromium = browser
    context = telefono(chromium)
    try:
        de_cero("ana0@banco.com", "trazabilidad")
        page = context.new_page()
        entra(page, base, correo="ana0@banco.com")
        page.goto(f"{base}/trazabilidad")        # un solo paso: el primero es el último
        page.wait_for_selector(".driver-popover", state="visible", timeout=5000)
        page.locator(".driver-popover-footer button").last.click()   # «Entendido»
        page.wait_for_timeout(500)

        with db.session_scope() as session:
            ana = session.query(User).filter_by(email="ana0@banco.com").one()
            fila = (session.query(TourVisto)
                    .filter_by(user_id=ana.id, pantalla="trazabilidad").one())
        assert fila.completo is True, "terminarlo se apuntó como saltado"
        assert fila.pasos == 1
    finally:
        context.close()


def test_a_tutorial_shown_short_comes_back_when_the_screen_is_fuller(client):
    """Lo de `/maduracion`: un paso de dos, apuntado como completo, perdido.

    Se prueba contra la función y no con el navegador porque lo que decide es
    el servidor: con los dos pasos que tocan y solo uno visto, tiene que volver
    a mandarlo. Antes le bastaba con que existiera la fila.
    """
    helpers.signup(client)
    with db.session_scope() as session:
        ana = session.query(User).filter_by(role=Role.MANAGER).first()
        tour = tours.TOURS["maduracion"]
        cuantos = len(tours.pasos_para(tour, ana.role))
        assert cuantos >= 2, "esta prueba necesita un tutorial de dos o más pasos"

        tutorial.marcar(session, ana, "maduracion", completo=True, pasos=1)
        assert not tutorial.visto(session, ana, "maduracion", tour.version, cuantos), \
            "se dio por visto entero un tutorial que salió recortado"
        assert tutorial.para(session, ana, "/maduracion", "es") is not None

        tutorial.marcar(session, ana, "maduracion", completo=True, pasos=cuantos)
        assert tutorial.visto(session, ana, "maduracion", tour.version, cuantos)
        assert tutorial.para(session, ana, "/maduracion", "es") is None


def test_an_old_row_without_the_count_is_left_alone(client):
    """A quien ya lo vio no se le saca otra vez por una actualización."""
    helpers.signup(client)
    with db.session_scope() as session:
        ana = session.query(User).filter_by(role=Role.MANAGER).first()
        tour = tours.TOURS["maduracion"]
        tutorial.marcar(session, ana, "maduracion")
        fila = (session.query(TourVisto)
                .filter_by(user_id=ana.id, pantalla="maduracion").one())
        fila.pasos = None                 # como las filas de antes del arreglo
        session.flush()
        assert tutorial.visto(session, ana, "maduracion", tour.version, 99)
