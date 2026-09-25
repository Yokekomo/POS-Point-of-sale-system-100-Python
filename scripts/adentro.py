"""Las pantallas de dentro, medidas igual que la portada.

La portada la mira quien está pensando si compra. Estas las mira quien está de
turno, con el móvil en el bolsillo del delantal, el guante puesto y las manos
frías. Un botón de veintidós píxeles en la portada es fealdad; en la cámara es
una pulsación fallada y un dato que no se apunta, y lo que no se apunta no
cuadra a fin de mes.

Es la misma vara de medir que `scripts.portada` —de hecho reutiliza sus
medidas, para que no haya dos criterios— aplicada a lo que hay detrás de
entrar. Lo que cambia es lo que hay que montar antes:

**Una casa con trabajo dentro.** Una pantalla vacía no se rompe nunca: las
tablas no tienen filas, las listas no tienen nombres largos y todo cabe. Se
levanta una casa con `bench.build()` —seis días de trabajo, sus piezas, sus
despieces, su maduración y sus ventas— para medir pantallas con datos, que son
las que se ven en un turno.

**Tres oficios.** Ana lleva la casa, Paco es el carnicero y Leo está en la
barra. No ven lo mismo: hay pantallas que a uno le salen enteras y a otro le
salen recortadas, y una tabla de manager en un teléfono de carnicero es
justamente de las cosas que se rompen. Lo que a alguien no le toca se salta
solo: si la pantalla contesta con otra dirección, es que no era suya.

**El idioma, por la ficha de cada uno.** Aquí no vale la cookie: a quien ha
entrado se le habla en el idioma que eligió en su cuenta. Se cambia donde se
cambia de verdad y se vuelve a mirar.

**Y el tutorial, dado por visto.** Quien lleva una semana trabajando no tiene
la ventana de bienvenida encima. Medir con ella puesta es medir la ventana.

    python -m scripts.adentro                 # los siete idiomas, tres oficios
    python -m scripts.adentro --rapido        # es+en, un ancho, un oficio
    python -m scripts.adentro --oficios ana   # solo lo que ve quien lleva la casa
    python -m scripts.adentro --anchos 390    # como se trabaja: el teléfono
    python -m scripts.adentro --solo dedos    # lo que se pulsa, que es lo que más importa aquí
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import sys
import tempfile
import threading
import time
from datetime import date

from scripts.portada import (AIRE_PANTALLA, AIRE_TARJETA, CONTRASTE, DEDO, DEDO_NORMA,
                             DEDOS, MEDIDA, _hay_algo, _navegador, _puerto_libre, enseña)

# El día que se da por hoy. Fijo, para que dos pasadas midan la misma casa: con
# la fecha de verdad, la maduración avanza y las pantallas cambian solas.
HOY = date(2026, 9, 20)
IDIOMAS = ("es", "en", "fr", "de", "nl", "ar", "hu")
# Como se trabaja: el teléfono del delantal, la tablet del pase y el ordenador
# de la oficina.
ANCHOS = (390, 820, 1280)

# Quién mira, con su correo y lo que es en la casa. Los tres de `bench.build`.
OFICIOS = {
    "ana": ("ana0@banco.com", "lleva la casa"),
    "paco": ("paco0@banco.com", "carnicero"),
    "leo": ("leo0@banco.com", "barra"),
}

# Donde se trabaja. Sin las públicas, que ya las mide `scripts.portada`, y sin
# las que no son pantallas (la API, el latido, el icono).
PANTALLAS = (
    "/hoy", "/carne", "/recepcion", "/recepcion/precios", "/despiece", "/cortes",
    "/maduracion", "/descongelado", "/descongelado/recuento", "/merma", "/inventario",
    "/traslados", "/ventas", "/parte", "/cuadre", "/trazabilidad", "/carta",
    "/ingredientes", "/notificaciones", "/cuenta", "/configuracion", "/descargas",
    "/descargas/etiquetas", "/sedes", "/manager/equipo", "/manager/alertas",
    "/admin", "/admin/fallos", "/fallo",
)


# --- la casa, servida --------------------------------------------------------

def _levanta_casa():
    """Una casa con seis días de trabajo dentro, servida de verdad.

    Seis días y no treinta: bastan para que haya piezas en cámara, cortes
    hechos, carne madurando y ventas apuntadas, que es lo que llena las
    pantallas. Treinta tardan cinco veces más en montarse y no enseñan una
    tabla más ancha.
    """
    import uvicorn

    from thegrill import bench, db
    from thegrill.meat import app as meatapp

    db.init_engine(f"sqlite:///{pathlib.Path(tempfile.mkdtemp()) / 'adentro.db'}")
    db.create_all()
    with db.session_scope() as session:
        bench.build(session, days=6, seed=5, until=HOY)
    _tutorial_visto()

    puerto = _puerto_libre()
    servidor = uvicorn.Server(uvicorn.Config(meatapp.app, host="127.0.0.1",
                                             port=puerto, log_level="error"))
    threading.Thread(target=servidor.run, daemon=True).start()
    for _ in range(300):
        if servidor.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("el servidor de pruebas no llegó a arrancar")
    return f"http://127.0.0.1:{puerto}", servidor


def _tutorial_visto() -> None:
    """La ventana de bienvenida, dada por vista para los tres.

    Quien lleva una semana trabajando no la tiene encima, y midiendo con ella
    puesta se mide la ventana y no la pantalla. El tutorial tiene sus propias
    pruebas en `tests/test_tutorial.py`.
    """
    from thegrill import db
    from thegrill.meat import tours, tutorial
    from thegrill.models import User

    with db.session_scope() as session:
        for correo, _ in OFICIOS.values():
            persona = session.query(User).filter_by(email=correo).first()
            if not persona:
                continue
            for pantalla in tours.TOURS:
                tutorial.marcar(session, persona, pantalla)


def _pon_idioma(idioma: str) -> None:
    """El idioma se cambia en la ficha de cada uno, que es donde manda.

    A quien ha entrado no se le habla por la cookie: se le habla en el idioma
    que eligió en su cuenta, y ese gana a todo lo demás. Cambiarlo por la
    dirección de idioma dejaría la pantalla en español y la medida sería
    mentira.
    """
    from thegrill import db
    from thegrill.models import User

    with db.session_scope() as session:
        for correo, _ in OFICIOS.values():
            persona = session.query(User).filter_by(email=correo).first()
            if persona:
                persona.language = idioma


def _entra(pg, base: str, correo: str) -> bool:
    """Entrar como quien sea. Devuelve si se llegó a entrar."""
    from thegrill import bench

    pg.goto(f"{base}/login")
    pg.fill("input[name=email]", correo)
    pg.fill("input[name=password]", bench.PASSWORD)
    pg.click("button[type=submit]")
    try:
        pg.wait_for_url(f"{base}/hoy", timeout=15000)
    except Exception:
        return False
    return True


# --- medir -------------------------------------------------------------------

def _mide(pg, base: str, ruta: str):
    """Mide una pantalla. Devuelve None si esa pantalla no es de quien mira.

    Cuando alguien pide una pantalla que no le toca, el programa lo lleva a
    otra: eso no es un hallazgo, es el permiso haciendo su trabajo. Se sabe
    porque la dirección donde se acaba no es la que se pidió.
    """
    pg.goto(f"{base}{ruta}")
    pg.wait_for_load_state("networkidle")
    if not pg.url.endswith(ruta):
        return None
    pg.wait_for_timeout(120)
    primera = pg.evaluate(MEDIDA, [AIRE_PANTALLA, AIRE_TARJETA])
    if not _hay_algo(primera):
        return primera
    # Lo mismo que en la portada: lo que sale una vez puede ser una medida
    # tomada a mitad de un reajuste. Solo cuenta lo que sale las dos.
    pg.wait_for_timeout(400)
    segunda = pg.evaluate(MEDIDA, [AIRE_PANTALLA, AIRE_TARJETA])
    firme = {"scroll": segunda["scroll"]}
    for cuenta in ("borde", "desborde", "apretado", "solape", "alto", "estrecho"):
        antes = {repr(sorted(x.items())) for x in primera[cuenta]}
        firme[cuenta] = [x for x in segunda[cuenta] if repr(sorted(x.items())) in antes]
    return firme


def _contexto(nav, ancho: int):
    """Teléfono con dedo por debajo de 500; de ahí para arriba, pantalla y ratón."""
    telefono = ancho < 500
    return nav.new_context(
        viewport={"width": ancho, "height": 900}, device_scale_factor=1,
        has_touch=ancho < 900,
        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15") if telefono else None)


def recorre(nav, base: str, idiomas, anchos, pantallas, oficios, exámenes):
    """Todas las pantallas, por oficio, ancho e idioma."""
    cuenta: collections.Counter = collections.Counter()
    detalle: dict[str, set[str]] = collections.defaultdict(set)
    vistas = saltadas = 0

    def apunta(clave: str, línea: str) -> None:
        cuenta[clave] += 1
        detalle[clave].add(línea)

    for idioma in idiomas:
        _pon_idioma(idioma)
        for oficio in oficios:
            correo, _papel = OFICIOS[oficio]
            for ancho in anchos:
                ctx = _contexto(nav, ancho)
                pg = ctx.new_page()
                if not _entra(pg, base, correo):
                    apunta("no se pudo entrar", f"{idioma} {oficio} {ancho}")
                    ctx.close()
                    continue
                for ruta in pantallas:
                    dónde = f"{idioma} {oficio} {ancho} {ruta}"
                    if "maqueta" in exámenes:
                        r = _mide(pg, base, ruta)
                        if r is None:
                            saltadas += 1
                            continue
                        vistas += 1
                        if r["scroll"][0] > r["scroll"][1] + 1:
                            apunta("barra lateral en la página",
                                   f"{dónde} ({r['scroll'][0]}>{r['scroll'][1]})")
                        for x in r["borde"]:
                            apunta("texto pegado al borde",
                                   f"{dónde} · {x['que']} «{x['texto'][:26]}» "
                                   f"izq={x['izq']} der={x['der']}")
                        for x in r["desborde"]:
                            apunta("se sale de su caja",
                                   f"{dónde} · {x['que']} «{x['texto'][:26]}» "
                                   f"{x['mide']}>{x['cabe']}")
                        for x in r["apretado"]:
                            apunta("pegado a la tarjeta",
                                   f"{dónde} · {x['que']} en {x['en']} "
                                   f"«{x['texto'][:22]}» hueco={x['hueco']}")
                        for x in r["solape"]:
                            apunta("se pisan",
                                   f"{dónde} · {x['a']} × {x['b']} ({x['cuanto']}px)")
                        for x in r["alto"]:
                            apunta("título de demasiadas líneas",
                                   f"{dónde} · {x['que']} «{x['texto'][:30]}» "
                                   f"{x['lineas']} líneas")
                        for x in r["estrecho"]:
                            apunta("columna de texto muy estrecha",
                                   f"{dónde} · {x['que']} «{x['texto'][:20]}» "
                                   f"{x['lineas']} líneas en {x['mide']}px de {x['cabia']}")
                    else:
                        pg.goto(f"{base}{ruta}")
                        pg.wait_for_load_state("networkidle")
                        if not pg.url.endswith(ruta):
                            saltadas += 1
                            continue
                        vistas += 1

                    # Lo que se pulsa: solo con dedo, que es cuando la hoja de
                    # estilo agranda lo que hay que agrandar.
                    if "dedos" in exámenes and ancho < 900:
                        for m in pg.evaluate(DEDOS, DEDO):
                            clave = ("por debajo de lo que exige la norma"
                                     if min(m["alto"], m["ancho"]) < DEDO_NORMA
                                     else "más pequeño de lo que pide un dedo")
                            apunta(clave, f"{dónde} · {m['que'][:30]} "
                                          f"«{m['texto']}» {m['ancho']}x{m['alto']}")
                    if "contraste" in exámenes:
                        for m in pg.evaluate(CONTRASTE):
                            apunta("contraste por debajo de la norma",
                                   f"{dónde} · {m['que'][:28]} «{m['texto'][:20]}» "
                                   f"{m['ratio']} < {m['pide']} ({m['px']}px)")
                ctx.close()
    return {"cuenta": cuenta, "detalle": detalle}, vistas, saltadas


def _lista(texto: str, todos, entero=False):
    """«de,nl» o «390,820». Vacío es todos."""
    if not texto:
        return list(todos)
    partes = [p.strip() for p in texto.split(",") if p.strip()]
    return [int(p) for p in partes] if entero else partes


def main(argv: list[str] | None = None) -> int:
    """La línea de órdenes del repaso de las pantallas de dentro."""
    p = argparse.ArgumentParser(prog="adentro", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--idiomas", default="", help="cuáles, separados por comas (todos)")
    p.add_argument("--anchos", default="", help="cuáles, separados por comas (todos)")
    p.add_argument("--pantallas", default="", help="cuáles, separadas por comas (todas)")
    p.add_argument("--oficios", default="", help="ana, paco, leo (los tres)")
    p.add_argument("--solo", choices=("maqueta", "contraste", "dedos"), default=None,
                   help="un examen de los tres")
    p.add_argument("--rapido", action="store_true",
                   help="es+en, un ancho, un oficio: para mirar mientras se trabaja")
    args = p.parse_args(argv)

    idiomas = _lista(args.idiomas, IDIOMAS)
    anchos = _lista(args.anchos, ANCHOS, entero=True)
    pantallas = _lista(args.pantallas, PANTALLAS)
    oficios = [o for o in _lista(args.oficios, OFICIOS) if o in OFICIOS]
    if not oficios:
        print("  Oficios: ana, paco o leo.")
        return 2
    if args.rapido:
        idiomas = [i for i in ("es", "en") if i in idiomas] or idiomas[:2]
        anchos = [a for a in (390,) if a in anchos] or anchos[:1]
        oficios = oficios[:1]
    exámenes = {args.solo} if args.solo else {"maqueta", "contraste", "dedos"}

    print()
    print(f"  Pantallas de dentro: {len(pantallas)} pantallas × {len(anchos)} anchos × "
          f"{len(idiomas)} idiomas × {len(oficios)} oficios")
    print("  Montando la casa…")
    arranca = time.monotonic()

    base, servidor = _levanta_casa()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            nav = _navegador(pw)
            try:
                parte, vistas, saltadas = recorre(nav, base, idiomas, anchos,
                                                  pantallas, oficios, exámenes)
            finally:
                nav.close()
    finally:
        servidor.should_exit = True

    salida = enseña([parte], vistas)
    if saltadas:
        print(f"  ({saltadas} pantallas saltadas: no le tocaban a quien miraba)")
    print(f"  {time.monotonic() - arranca:.0f}s")
    return salida


if __name__ == "__main__":
    sys.exit(main())
