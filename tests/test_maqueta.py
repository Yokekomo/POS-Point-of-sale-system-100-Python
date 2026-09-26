"""Que la maqueta no se rompa otra vez, y que se entere la suite y no un cliente.

Este repaso encontró veintiocho fallos de maquetación en las tres superficies
del programa, y dejó dos lecciones que no caben en un arreglo:

**Probar en tu idioma es probar el caso fácil.** De los fallos de la portada,
casi ninguno salía en español ni en inglés; de los de dentro, cuatro salían
solo en francés, neerlandés, árabe e inglés. El alemán y el neerlandés pegan
palabras de 250 px, el francés es un tercio más largo, y el árabe va al revés
y además escribe más corto de lo que la maqueta espera. Lo peor que se
encontró —media portada alemana cortada en un móvil de 320 px, sin barra ni
nada que lo delatara— es justo el fallo que un cliente no reporta: cierra la
página y ya está.

**Y arreglar rompe.** Tres de los fallos de este repaso los metió el propio
arreglo de otro: un `overflow-wrap` que se heredaba hasta partir «Desactivar»
letra a letra y dejar el botón en 38 × 217 px, y dos veces un `min-width` que
encogía justo lo que pretendía ensanchar. Los tres se cazaron **volviendo a
medir después de tocar**, ninguno razonándolo antes.

De ahí esto. `scripts/portada.py` y `scripts/adentro.py` miden a fondo y
tardan minutos: son para cuando se repasa una pantalla. Esto es la guardia
corta que va en la suite, con el mismo criterio —usa sus mismas medidas, para
que no haya dos varas— pero solo donde duele: los idiomas que peor caben, el
ancho más estrecho y las pantallas donde se trabaja.

Si esto se pone rojo, no hace falta adivinar: el aviso trae el idioma, el
ancho, la pantalla y la medida.
"""
import os
import socket
import threading
import time
from datetime import date

import pytest

from thegrill import bench, db
from scripts.portada import AIRE_PANTALLA, AIRE_TARJETA, CONTRASTE, DEDO, DEDOS, MEDIDA

HOY = date(2026, 9, 20)
# Los que peor caben, que son los que encontraron todo. El español y el inglés
# no están a propósito: si un fallo sale en ellos, sale también en estos.
DUROS = ("de", "fr", "nl", "ar")
ESTRECHO = 320          # el iPhone pequeño y los Android baratos de cocina
TELEFONO = 390          # el teléfono del delantal


def _puerto() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _sirve(app) -> tuple[str, object]:
    import uvicorn

    puerto = _puerto()
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=puerto,
                                             log_level="error"))
    threading.Thread(target=servidor.run, daemon=True).start()
    for _ in range(200):
        if servidor.started:
            break
        time.sleep(0.05)
    assert servidor.started, "el servidor de pruebas no arrancó"
    return f"http://127.0.0.1:{puerto}", servidor


@pytest.fixture(scope="module")
def casa(tmp_path_factory):
    """Una casa con trabajo dentro, servida por las dos ediciones a la vez.

    Con trabajo dentro porque una pantalla vacía no se rompe nunca: las tablas
    no tienen filas, las listas no tienen nombres largos y todo cabe. Y por las
    dos ediciones porque comparten la casa pero no el armazón: un arreglo en
    una no le llega a la otra.
    """
    pytest.importorskip("playwright.sync_api")
    from thegrill.meat import app as carne
    from thegrill.web import app as cocina

    db.init_engine(f"sqlite:///{tmp_path_factory.mktemp('maqueta') / 'maqueta.db'}")
    db.create_all()
    with db.session_scope() as session:
        bench.build(session, days=6, seed=5, until=HOY)
    _tutorial_visto()

    base_carne, srv_carne = _sirve(carne.app)
    base_cocina, srv_cocina = _sirve(cocina.app)
    yield base_carne, base_cocina
    srv_carne.should_exit = True
    srv_cocina.should_exit = True


def _tutorial_visto() -> None:
    """La ventana de bienvenida, dada por vista: aquí se mide la pantalla."""
    from thegrill.meat import tours, tutorial
    from thegrill.models import User

    with db.session_scope() as session:
        for correo in ("ana0@banco.com", "paco0@banco.com"):
            persona = session.query(User).filter_by(email=correo).first()
            if persona:
                for pantalla in tours.TOURS:
                    tutorial.marcar(session, persona, pantalla)


def _idioma(codigo: str) -> None:
    """El idioma se pone en la ficha, que es lo que manda para quien ha entrado."""
    from thegrill.models import User

    with db.session_scope() as session:
        for persona in session.query(User).all():
            persona.language = codigo


@pytest.fixture(scope="module")
def navegador(casa):
    """El navegador con el que se mide.

    Dónde buscarlo, y qué hacer si no está, lo decide `conftest.abre_navegador`:
    en el ordenador de alguien se salta, y en el servidor de integración se
    cae. Estas cinco pruebas saltándose calladas es exactamente el verde falso
    que no puede pasar.
    """
    from conftest import abre_navegador
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        nav = abre_navegador(pw)
        yield nav
        nav.close()


def _pantalla(nav, ancho: int, tema: str = "light"):
    """Un teléfono con dedo: es lo que enciende las reglas de pantalla táctil."""
    return nav.new_context(viewport={"width": ancho, "height": 900}, has_touch=True,
                           color_scheme=tema,
                           user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like "
                                      "Mac OS X) AppleWebKit/605.1.15")


def _entra(pg, base: str, correo: str = "ana0@banco.com") -> None:
    pg.goto(f"{base}/login")
    pg.fill("input[name=email]", correo)
    pg.fill("input[name=password]", bench.PASSWORD)
    pg.click("button[type=submit]")
    pg.wait_for_url(lambda u: not str(u).rstrip("/").endswith("/login"), timeout=15000)
    pg.wait_for_load_state("networkidle")


def _mide(pg, base: str, ruta: str) -> list[str]:
    """Lo que esa pantalla tenga de malo, ya escrito para poder ir a verlo."""
    pg.goto(f"{base}{ruta}")
    pg.wait_for_load_state("networkidle")
    if not pg.url.endswith(ruta):
        return []                      # esa pantalla no es de quien mira
    pg.wait_for_timeout(120)
    r = pg.evaluate(MEDIDA, [AIRE_PANTALLA, AIRE_TARJETA])
    malo = []
    if r["scroll"][0] > r["scroll"][1] + 1:
        malo.append(f"la página se desliza a los lados ({r['scroll'][0]}>{r['scroll'][1]})")
    for x in r["borde"]:
        malo.append(f"{x['que']} «{x['texto'][:26]}» pegado al borde "
                    f"(izq={x['izq']} der={x['der']})")
    for x in r["desborde"]:
        malo.append(f"{x['que']} «{x['texto'][:26]}» se sale de su caja "
                    f"({x['mide']}>{x['cabe']})")
    for x in r["apretado"]:
        malo.append(f"{x['que']} «{x['texto'][:22]}» a {x['hueco']}px del borde "
                    f"de {x['en']}")
    for x in r["solape"]:
        malo.append(f"{x['a']} y {x['b']} se pisan ({x['cuanto']}px)")
    return malo


# ------------------------------------------------- la portada, en los duros
def test_no_language_pushes_a_public_page_off_the_screen(navegador, casa):
    """En alemán, media portada estaba cortada en un móvil y no se veía.

    La tabla de la maqueta pedía 322 px donde hay 238, y una casilla de rejilla
    no encoge por debajo de su contenido mientras no se le diga: la portada se
    ensanchaba a 380 px y el titular, la entradilla y los botones se salían
    sesenta por la derecha. Y `.hero` recorta lo que sobra, así que no había ni
    barra que lo dijera. En español no salía.
    """
    base, _ = casa
    fallos = []
    for idioma in DUROS:
        ctx = _pantalla(navegador, ESTRECHO)
        pg = ctx.new_page()
        pg.goto(f"{base}/idioma/{idioma}?next=/")
        for ruta in ("/", "/precios", "/solicitar"):
            for malo in _mide(pg, base, ruta):
                fallos.append(f"{idioma} {ESTRECHO}px {ruta}: {malo}")
        ctx.close()
    assert not fallos, "la portada se rompe:\n  " + "\n  ".join(fallos)


# --------------------------------------------- las pantallas donde se trabaja
@pytest.mark.parametrize("edicion", ["carne", "cocina"])
def test_no_language_pushes_a_work_screen_off_the_screen(navegador, casa, edicion):
    """Aquí no es fealdad: es un turno con el móvil en la mano.

    Dos pantallas se deslizaban a los lados en un teléfono —la de configuración,
    por la dirección `otpauth://` de la verificación en dos pasos, que mide 813
    px y se salía 461 de su tarjeta; y la hoja de etiquetas, que es un A4 a
    tamaño real—. Y los atajos del día se salían de su caja en neerlandés.
    """
    base_carne, base_cocina = casa
    base = base_carne if edicion == "carne" else base_cocina
    rutas = (("/hoy", "/recepcion", "/despiece", "/configuracion", "/ventas",
              "/descargas/etiquetas", "/manager/equipo", "/inventario")
             if edicion == "carne" else
             ("/app", "/manager", "/carne", "/merma", "/ventas", "/inventario"))
    fallos = []
    for idioma in DUROS:
        _idioma(idioma)
        ctx = _pantalla(navegador, TELEFONO)
        pg = ctx.new_page()
        _entra(pg, base)
        for ruta in rutas:
            for malo in _mide(pg, base, ruta):
                fallos.append(f"{edicion} {idioma} {TELEFONO}px {ruta}: {malo}")
        ctx.close()
    _idioma("es")
    assert not fallos, f"las pantallas de {edicion} se rompen:\n  " + "\n  ".join(fallos)


# ------------------------------------------------------- lo que se pulsa
@pytest.mark.parametrize("edicion", ["carne", "cocina"])
def test_everything_you_tap_is_big_enough_with_a_glove(navegador, casa, edicion):
    """Un objetivo pequeño en la cámara es un dato que no se apunta.

    Se piden 44 px, que es lo que recomiendan Apple y Google y lo que pide un
    dedo con guante, y no los 24 del mínimo de la norma (WCAG 2.2, criterio
    2.5.8). No es exigir de más: es que casi todo lo que se encontró estaba
    **entre los dos números** —la campana de los avisos a 24 justos en todas las
    pantallas, el nombre de la casa a 39, el nombre de la pieza en el despiece
    a 21— y con el listón en 24 la mitad de este repaso podría deshacerse sin
    que nadie se enterara. El programa cumple hoy los 44 en las dos ediciones y
    en los siete idiomas; esto es para que lo siga cumpliendo.

    Lo peor que se encontró: el cuadro de marcar a 13 px, la mitad de lo que
    hace falta, porque un `width:auto` puesto para otra cosa se llevaba por
    delante la regla del dedo. Y son los de halal y «llegó congelado», que se
    marcan en el muelle con el guante puesto.
    """
    base_carne, base_cocina = casa
    base = base_carne if edicion == "carne" else base_cocina
    rutas = (("/hoy", "/recepcion", "/despiece", "/ventas", "/inventario", "/merma")
             if edicion == "carne" else
             ("/app", "/manager", "/carne", "/merma", "/ventas"))
    fallos = []
    for idioma in ("de", "ar"):
        _idioma(idioma)
        ctx = _pantalla(navegador, TELEFONO)
        pg = ctx.new_page()
        _entra(pg, base)
        for ruta in rutas:
            pg.goto(f"{base}{ruta}")
            pg.wait_for_load_state("networkidle")
            if not pg.url.endswith(ruta):
                continue
            for m in pg.evaluate(DEDOS, DEDO):
                fallos.append(f"{edicion} {idioma} {ruta}: {m['que'][:30]} "
                              f"«{m['texto']}» mide {m['ancho']}x{m['alto']}")
        ctx.close()
    _idioma("es")
    assert not fallos, ("hay cosas que se pulsan más pequeñas de lo que pide un dedo:"
                        "\n  " + "\n  ".join(fallos))


def _un_aviso_sin_leer() -> None:
    """La chapa roja solo existe si hay algo sin leer.

    Sin esto la prueba mide una pantalla en la que la chapa va `hidden`, pasa
    en verde y no ha mirado justo lo que se rompió. Es el mismo fallo, un piso
    más abajo.
    """
    from thegrill.models import AlertSeverity, Notification, User

    with db.session_scope() as session:
        ana = session.query(User).filter_by(email="ana0@banco.com").one()
        session.add(Notification(restaurant_id=ana.restaurant_id, user_id=ana.id,
                                 kind="prueba", severity=AlertSeverity.CRITICAL,
                                 title="aviso de prueba", body="para que salga la chapa"))


# ----------------------------------------------- lo que se lee, en los dos temas
@pytest.mark.parametrize("tema", ["light", "dark"])
def test_everything_can_be_read_in_both_themes(navegador, casa, tema):
    """El tema oscuro de dentro no se había medido nunca. Ni una vez.

    `scripts/adentro.py` abría el navegador sin decirle el tema, y un navegador
    sin tema abre en claro. Así que las dos mil ochocientas pantallas que se
    midieron de las dos ediciones se midieron solo en claro, y esta guardia daba
    verde sin haber mirado la mitad.

    Lo que había debajo: la chapa roja con el número de avisos —blanco sobre el
    rojo del tema, que en oscuro es un rosa claro— daba **2,47 : 1** donde la
    norma pide 4,5 para letra de 11 px. Salía en todas las pantallas de la casa
    que tuvieran un aviso sin leer. Un verde que no ha comprobado nada es peor
    que un rojo: el rojo se arregla.
    """
    base, _ = casa
    _un_aviso_sin_leer()          # si no, la chapa va `hidden` y no se mide nada
    ctx = _pantalla(navegador, TELEFONO, tema)
    pg = ctx.new_page()
    _entra(pg, base)
    fallos = []
    for ruta in ("/hoy", "/notificaciones", "/recepcion", "/inventario", "/manager/alertas"):
        pg.goto(f"{base}{ruta}")
        pg.wait_for_load_state("networkidle")
        if not pg.url.endswith(ruta):
            continue
        pg.wait_for_timeout(120)
        for m in pg.evaluate(CONTRASTE):
            fallos.append(f"{tema} {ruta}: {m['que'][:26]} «{m['texto'][:22]}» "
                          f"{m['ratio']} sobre {m['pide']}")
    ctx.close()
    assert not fallos, ("hay texto que no se lee en tema " + tema + ":\n  "
                        + "\n  ".join(fallos))


# ------------------------------------------- lo que hace falta para no ver
# Tres cosas que esta guardia no miraba y que impiden usar el programa a quien
# no ve la pantalla o no usa el ratón. Las tres salieron de una auditoría contra
# WCAG 2.2 nivel AA, y las tres estaban ahí desde el principio:
#
# - El foco tapado por la barra de pestañas (criterio 2.4.11). La barra es fija
#   y el navegador dejaba el campo justo debajo: se escribía un peso en una
#   casilla que no se veía. La portada ya lo tenía resuelto y al armazón de
#   dentro no había llegado.
# - Casillas de tabla sin nombre (4.1.2). Cuatro campos de unidades seguidos en
#   el parte de ventas, once pesos en maduración: para un lector de pantalla,
#   cuadros vacíos idénticos.
# - Carteles que no se anuncian (4.1.3). El error salía arriba y el foco se iba
#   al primer campo, por debajo del cartel.

TAPADO = """
(barra) => {
  const fijo = document.querySelector('.tabs');
  if (!fijo) return null;
  const b = fijo.getBoundingClientRect();
  if (!b.height) return null;
  const e = document.activeElement;
  if (!e || e === document.body) return null;
  // Los enlaces de la propia barra están dentro de la barra: eso no es taparlos.
  if (fijo.contains(e)) return null;
  const c = e.getBoundingClientRect();
  if (!c.height) return null;
  // Entero por debajo del borde de arriba de la barra: no se ve nada de él.
  if (c.top >= b.top) {
    return {que: e.tagName + (e.name ? '[' + e.name + ']' : ''),
            arriba: Math.round(c.top), barra: Math.round(b.top)};
  }
  return null;
}
"""

SIN_NOMBRE = """
() => Array.from(document.querySelectorAll('input, select, textarea'))
  .filter(e => e.type !== 'hidden' && e.offsetParent !== null)
  .filter(e => !(e.getAttribute('aria-label') || '').trim()
            && !(e.getAttribute('aria-labelledby') || '').trim()
            && !(e.getAttribute('title') || '').trim()
            && !(e.id && document.querySelector('label[for="' + CSS.escape(e.id) + '"]'))
            && !e.closest('label'))
  .map(e => e.tagName + (e.name ? '[' + e.name + ']' : ''))
"""


def test_the_tab_bar_never_hides_the_field_you_just_jumped_to(navegador, casa):
    """Con el teclado, en el móvil, se escribía en una casilla que no se veía."""
    base, _ = casa
    ctx = _pantalla(navegador, TELEFONO)
    pg = ctx.new_page()
    _entra(pg, base)
    fallos = []
    for ruta in ("/recepcion", "/inventario", "/ventas", "/merma"):
        pg.goto(f"{base}{ruta}")
        pg.wait_for_load_state("networkidle")
        if not pg.url.endswith(ruta):
            continue
        for _ in range(45):
            pg.keyboard.press("Tab")
            tapado = pg.evaluate(TAPADO)
            if tapado:
                fallos.append(f"{ruta}: {tapado['que']} a {tapado['arriba']}px, "
                              f"la barra empieza en {tapado['barra']}")
                break
    ctx.close()
    assert not fallos, ("la barra de abajo tapa entera la casilla que tiene el foco:"
                        "\n  " + "\n  ".join(fallos))


@pytest.mark.parametrize("edicion", ["carne"])
def test_everything_you_fill_in_has_a_name(navegador, casa, edicion):
    """Un campo sin nombre, para quien no ve, es un cuadro vacío más."""
    base_carne, _ = casa
    ctx = _pantalla(navegador, TELEFONO)
    pg = ctx.new_page()
    _entra(pg, base_carne)
    fallos = []
    for ruta in ("/recepcion", "/despiece", "/ventas", "/maduracion",
                 "/inventario", "/merma", "/manager/equipo"):
        pg.goto(f"{base_carne}{ruta}")
        pg.wait_for_load_state("networkidle")
        if not pg.url.endswith(ruta):
            continue
        for que in pg.evaluate(SIN_NOMBRE):
            fallos.append(f"{ruta}: {que}")
    ctx.close()
    assert not fallos, ("hay casillas sin nombre: quien no ve la pantalla no sabe "
                        "qué está rellenando:\n  " + "\n  ".join(fallos))
