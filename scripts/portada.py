"""La portada, medida en vez de mirada: bordes, tarjetas, desbordes y dedos.

Mirar una pantalla no es revisarla. Un titular que se sale tres píxeles por la
derecha se ve igual que uno que no se sale; una palabra a siete píxeles del
borde de su tarjeta parece centrada; y una rejilla que pide más sitio del que
hay no enseña barra ninguna: la tarjeta le tapa lo que sobra y lo que se pierde
no se sabe que está. Todo eso se ve si se mide, y no se ve si se mira.

Y hay que medirlo **en los siete idiomas**. Casi ningún fallo que encontró esto
la primera vez salía en español o en inglés, que son los dos más cortos: el
alemán y el neerlandés pegan palabras de 250 píxeles, el francés es un tercio
más largo, y el árabe va al revés. Medir solo en el idioma de uno es medir el
caso fácil.

Tres exámenes, cada uno con su regla:

**Maquetación.** Texto a menos de 14 px del borde de la pantalla; texto a menos
de 10 px del borde de su tarjeta; contenido que pide más ancho del que tiene su
caja; barra lateral en la página entera; cajas hermanas que se pisan; titulares
que se parten en cuatro líneas en un ordenador (cinco en un teléfono, donde es
normal); y columnas de texto absurdamente estrechas —seis líneas en 200 px
teniendo la caja de al lado medio vacía—, que es lo que pasa cuando un `flex:1`
no llega a saltar de fila nunca.

**Contraste.** Lo que pide la norma (WCAG 2.2): 4,5 para el texto normal y 3
para el grande, contra el fondo que tenga de verdad detrás, en claro y oscuro.

**Dedos.** Lo que se pulsa tiene que medir 44 px, que es lo que recomiendan
Apple y Google y lo que pide un dedo con guante de cámara. La norma se conforma
con 24 y eso también se dice, pero 24 en una cámara es una pulsación fallada.

Lo que sale no es «pasa» o «no pasa»: es cada hallazgo con su idioma, su ancho,
su ruta y su medida, para poder ir a verlo. Y nada se da por hallazgo a la
primera: lo que aparece se vuelve a medir, y solo cuenta si sale las dos veces.
Una medida tomada a mitad de un reajuste del navegador da números imposibles
—ocho píxeles de contenido en una caja de dos— y manda a buscar un fallo que
no existe.

    python -m scripts.portada                   # los siete idiomas, el examen entero
    python -m scripts.portada --rapido          # es+en, tres anchos: un minuto
    python -m scripts.portada --idiomas de,nl   # los que peor caben
    python -m scripts.portada --anchos 320,390  # solo teléfono
    python -m scripts.portada --solo dedos      # un examen de los tres
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import socket
import sys
import tempfile
import threading
import time

# Dónde está el navegador que trae el entorno. El segundo es el de las
# instalaciones viejas: si se pide sin ruta, Playwright se baja el suyo.
NAVEGADORES = ("/opt/pw-browsers/chromium-1243/chrome-linux64/chrome",
               "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")

RUTAS = ("/", "/precios", "/solicitar", "/cookies", "/login")
IDIOMAS = ("es", "en", "fr", "de", "nl", "ar", "hu")
# De teléfono estrecho a pantalla de escritorio. El 365 y el 768 están porque
# son escalones de la hoja de estilo; el resto son aparatos de verdad.
ANCHOS = (320, 360, 390, 414, 768, 900, 1024, 1440)

# El aire que se le exige al texto contra el borde de la pantalla y contra el
# borde de su tarjeta. Catorce no es un gusto: por debajo, en un teléfono la
# primera letra se mete debajo del redondeo del cristal.
AIRE_PANTALLA = 14
AIRE_TARJETA = 10
# Lo que se le pide a lo que se pulsa. La norma (WCAG 2.2, criterio 2.5.8) se
# conforma con 24; esto es lo que piden Apple y Google, y lo que pide un dedo.
DEDO = 44
DEDO_NORMA = 24


# --- lo que mide el navegador, que es quien sabe dónde acabó cada cosa -------

MEDIDA = r"""
(args) => {
  const [aire, dentro] = args;
  const out = {borde: [], desborde: [], apretado: [], solape: [], alto: [],
               estrecho: [], scroll: null};
  const vista = document.documentElement.clientWidth;
  out.scroll = [document.documentElement.scrollWidth, vista];

  const visible = el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const conTexto = el => {
    for (const n of el.childNodes) if (n.nodeType === 3 && n.textContent.trim()) return true;
    return false;
  };
  const nombre = el => {
    const id = el.id ? '#' + el.id : '';
    const cl = (el.className && typeof el.className === 'string')
      ? '.' + el.className.trim().split(/\s+/).slice(0,2).join('.') : '';
    return el.tagName.toLowerCase() + id + cl;
  };
  const texto = el => (el.textContent || '').trim().replace(/\s+/g,' ').slice(0, 44);

  // Lo que vive dentro de una caja que se desliza a lo ancho —una tabla con
  // `overflow-x:auto`— sobresale a propósito: la caja tiene su propia orilla y
  // se arrastra con el dedo. Medirlo contra el borde de la pantalla da un
  // falso positivo por cada celda.
  const enCarril = el => {
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const ox = getComputedStyle(a).overflowX;
      if (ox === 'auto' || ox === 'scroll') return true;
    }
    return false;
  };

  const todos = [...document.body.querySelectorAll('*')]
    .filter(el => visible(el) && !enCarril(el));

  for (const el of todos) {
    const r = el.getBoundingClientRect();

    // 1) texto pegado al borde de la pantalla. Se mide dónde acaban las
    //    letras y no dónde acaba la caja: en una barra de pestañas que va de
    //    lado a lado, la caja del nombre empieza a 5 px del borde pero la
    //    palabra va centrada dentro y no toca nada. Midiendo la caja salía un
    //    hallazgo por pestaña y por pantalla, y ninguno era verdad.
    if (conTexto(el)) {
      const rango = document.createRange();
      rango.selectNodeContents(el);
      let izq = Infinity, der = -Infinity;
      for (const rr of rango.getClientRects()) {
        if (rr.width < 1 || rr.height < 1) continue;
        if (rr.left < izq) izq = rr.left;
        if (rr.right > der) der = rr.right;
      }
      if (izq === Infinity) { izq = r.left; der = r.right; }
      if (izq < aire || vista - der < aire) {
        out.borde.push({que: nombre(el), texto: texto(el),
                        izq: Math.round(izq), der: Math.round(vista - der)});
      }
    }

    // 2) contenido que pide más ancho del que tiene su caja. Solo cuenta si la
    //    caja no enseña barra: si la enseña, se puede llegar a lo que sobra.
    if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'visible') {
      out.desborde.push({que: nombre(el), texto: texto(el),
                         mide: el.scrollWidth, cabe: el.clientWidth});
    }

    // 3) texto pegado al borde de su tarjeta. Solo en cajas que se vean como
    //    tales —con raya o con fondo—, que si no se mide contra un `div` que
    //    no pinta nada y el hueco no significa nada.
    if (conTexto(el)) {
      const caja = el.closest('.card, .mock, .banner, .ficha');
      if (caja && caja !== el) {
        const cs = getComputedStyle(caja);
        const esTarjeta = parseFloat(cs.borderLeftWidth) > 0 ||
          (cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)');
        if (!esTarjeta) continue;
        const c = caja.getBoundingClientRect();
        const hueco = Math.min(r.left - c.left, c.right - r.right);
        if (hueco < dentro) {
          out.apretado.push({que: nombre(el), en: nombre(caja), texto: texto(el),
                             hueco: Math.round(hueco)});
        }
      }
    }
  }

  // 4) títulos que se parten en demasiadas líneas. En un teléfono un titular de
  //    cuatro líneas es normal; en un ordenador es una escalera.
  const tope = vista < 500 ? 5 : 4;
  for (const el of document.querySelectorAll('h1,h2,h3,.kpi b,button,.btn')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    const linea = parseFloat(getComputedStyle(el).lineHeight) || 20;
    const lineas = Math.round(r.height / linea);
    if (lineas >= tope) out.alto.push({que: nombre(el), texto: texto(el), lineas});
  }

  // 5) hermanos que se pisan
  const cajas = [...document.querySelectorAll('.card, .kpi, .mock, header, footer, .ficha')]
    .filter(visible);
  for (let i = 0; i < cajas.length; i++) {
    for (let j = i + 1; j < cajas.length; j++) {
      const a = cajas[i], b = cajas[j];
      if (a.contains(b) || b.contains(a)) continue;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const x = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const y = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (x > 2 && y > 2) {
        out.solape.push({a: nombre(a), b: nombre(b), cuanto: Math.round(Math.min(x, y))});
      }
    }
  }

  // 6) columnas de texto absurdamente estrechas: un párrafo de seis líneas
  //    metido en 200 px teniendo la caja de al lado medio vacía. Es lo que pasa
  //    cuando un `flex:1` —que es `flex:1 1 0`, y parte de cero— nunca llega a
  //    «no caber», así que el `flex-wrap` de al lado no salta jamás.
  for (const el of document.querySelectorAll('p,div,li,span')) {
    if (!visible(el) || !conTexto(el)) continue;
    const r = el.getBoundingClientRect();
    const linea = parseFloat(getComputedStyle(el).lineHeight) || 20;
    const lineas = Math.round(r.height / linea);
    const padre = el.parentElement ? el.parentElement.getBoundingClientRect() : r;
    if (lineas >= 5 && r.width < 240 && padre.width - r.width > 60) {
      out.estrecho.push({que: nombre(el), texto: texto(el), lineas,
                         mide: Math.round(r.width), cabia: Math.round(padre.width)});
    }
  }
  return out;
}
"""

CONTRASTE = r"""() => {
  const rgb = s => { const m = s.match(/[\d.]+/g); return m ? m.slice(0,3).map(Number) : null; };
  const lin = c => { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
  const lum = c => 0.2126*lin(c[0]) + 0.7152*lin(c[1]) + 0.0722*lin(c[2]);
  const ratio = (a, b) => { const A = lum(a), B = lum(b);
    return (Math.max(A,B) + 0.05) / (Math.min(A,B) + 0.05); };
  // El fondo de verdad: el primero que vaya subiendo que pinte algo. Un texto
  // sobre una tarjeta no se lee contra el color del cuerpo.
  const fondo = el => {
    for (let e = el; e; e = e.parentElement) {
      const s = getComputedStyle(e);
      const c = rgb(s.backgroundColor);
      const alfa = parseFloat((s.backgroundColor.match(/[\d.]+/g) || [0,0,0,1])[3] ?? 1);
      if (c && alfa > 0.5) return c;
    }
    return rgb(getComputedStyle(document.body).backgroundColor) || [255,255,255];
  };
  const conTexto = el => {
    for (const n of el.childNodes) if (n.nodeType === 3 && n.textContent.trim()) return true;
    return false;
  };
  const malos = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (!conTexto(el)) continue;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity === 0) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    // «Grande» según la norma: 24 px, o 18,66 si además va en negrita.
    const px = parseFloat(s.fontSize), peso = parseInt(s.fontWeight) || 400;
    const pide = (px >= 24 || (px >= 18.66 && peso >= 700)) ? 3.0 : 4.5;
    const c = ratio(rgb(s.color), fondo(el));
    if (c < pide) malos.push({que: el.tagName.toLowerCase() + '.' + (el.className || '-'),
                              texto: (el.textContent||'').trim().slice(0,30),
                              ratio: Math.round(c*100)/100, pide, px: Math.round(px)});
  }
  return malos;
}"""

DEDOS = r"""(minimo) => {
  const malos = [];
  const visible = el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  for (const el of document.querySelectorAll(
      'a[href],button,input,select,textarea,summary,[role=button]')) {
    if (!visible(el)) continue;
    // Un enlace dentro de un párrafo se pulsa por la línea entera y no tiene
    // caja propia: pedirle 44 px de alto es pedirle que deje de ser un enlace.
    const enTexto = el.tagName === 'A' && el.parentElement &&
      ['P','LI','TD','DIV','SPAN','SMALL','FIGCAPTION'].includes(el.parentElement.tagName) &&
      getComputedStyle(el).display.startsWith('inline');
    if (enTexto) continue;
    // Un cuadro de marcar metido en su etiqueta se pulsa por la etiqueta
    // entera: el cuadro mide 26 px pero el dedo tiene toda la frase. Lo que
    // hay que medir es lo que responde al dedo, no lo que se ve.
    let caja = el;
    if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
      const etiqueta = el.closest('label') ||
        (el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null);
      if (etiqueta) caja = etiqueta;
    }
    const r = caja.getBoundingClientRect();
    if (r.height < minimo || r.width < minimo) {
      malos.push({que: el.tagName.toLowerCase() + '.' + (el.className || '-'),
                  texto: (el.textContent || el.value || '').trim().slice(0, 22),
                  alto: Math.round(r.height), ancho: Math.round(r.width)});
    }
  }
  return malos;
}"""


# --- el servidor de pruebas -------------------------------------------------

def _puerto_libre() -> int:
    """Uno que nadie esté usando, para no chocar con el que ya tengas puesto."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _levanta() -> tuple[str, object]:
    """Monta una casa vacía y la sirve. Vacía basta: aquí se mide el armazón."""
    from thegrill import db
    from thegrill.meat import app as meatapp
    import uvicorn

    db.init_engine(f"sqlite:///{pathlib.Path(tempfile.mkdtemp()) / 'portada.db'}")
    db.create_all()
    puerto = _puerto_libre()
    servidor = uvicorn.Server(uvicorn.Config(meatapp.app, host="127.0.0.1",
                                             port=puerto, log_level="error"))
    threading.Thread(target=servidor.run, daemon=True).start()
    for _ in range(200):
        if servidor.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("el servidor de pruebas no llegó a arrancar")
    return f"http://127.0.0.1:{puerto}", servidor


def _navegador(pw):
    """El Chromium del entorno si está; si no, el que se haya bajado Playwright."""
    for ruta in NAVEGADORES:
        if pathlib.Path(ruta).exists():
            return pw.chromium.launch(executable_path=ruta)
    return pw.chromium.launch()


def _contexto(nav, ancho: int, tema: str):
    """Una pantalla. Por debajo de 500 px se hace pasar por teléfono, con dedo:
    es lo que enciende las reglas de `pointer:coarse` de la hoja de estilo."""
    telefono = ancho < 500
    return nav.new_context(
        viewport={"width": ancho, "height": 900}, device_scale_factor=1,
        color_scheme=tema, has_touch=telefono,
        user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15") if telefono else None)


def _hay_algo(r: dict) -> bool:
    return bool(r["borde"] or r["desborde"] or r["apretado"] or r["solape"]
                or r["alto"] or r["estrecho"] or r["scroll"][0] > r["scroll"][1] + 1)


def _mide(pg, base: str, ruta: str) -> dict:
    """Mide una pantalla, y si sale algo la vuelve a medir.

    Una medida tomada a mitad de un reajuste del navegador da números
    imposibles —ocho píxeles de contenido dentro de una caja de dos— y manda a
    buscar un fallo que no existe. Lo que sale las dos veces es real; lo que
    sale una, ruido.
    """
    pg.goto(f"{base}{ruta}")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(120)
    primera = pg.evaluate(MEDIDA, [AIRE_PANTALLA, AIRE_TARJETA])
    if not _hay_algo(primera):
        return primera
    pg.wait_for_timeout(400)
    segunda = pg.evaluate(MEDIDA, [AIRE_PANTALLA, AIRE_TARJETA])
    firme = {"scroll": segunda["scroll"]}
    for cuenta in ("borde", "desborde", "apretado", "solape", "alto", "estrecho"):
        antes = {repr(sorted(x.items())) for x in primera[cuenta]}
        firme[cuenta] = [x for x in segunda[cuenta] if repr(sorted(x.items())) in antes]
    return firme


# --- los tres exámenes ------------------------------------------------------

def maqueta(nav, base: str, idiomas, anchos, rutas, temas) -> tuple[dict, int]:
    """Bordes, tarjetas, desbordes, solapes, titulares y columnas."""
    cuenta: collections.Counter = collections.Counter()
    detalle: dict[str, set[str]] = collections.defaultdict(set)
    pantallas = 0
    for idioma in idiomas:
        for tema in temas:
            for ancho in anchos:
                ctx = _contexto(nav, ancho, tema)
                pg = ctx.new_page()
                pg.goto(f"{base}/idioma/{idioma}?next=/")
                for ruta in rutas:
                    pantallas += 1
                    r = _mide(pg, base, ruta)
                    dónde = f"{idioma} {ancho} {ruta}"
                    if r["scroll"][0] > r["scroll"][1] + 1:
                        cuenta["barra lateral en la página"] += 1
                        detalle["barra lateral en la página"].add(
                            f"{dónde} ({r['scroll'][0]}>{r['scroll'][1]})")
                    for x in r["borde"]:
                        cuenta["texto pegado al borde"] += 1
                        detalle["texto pegado al borde"].add(
                            f"{dónde} · {x['que']} «{x['texto'][:28]}» "
                            f"izq={x['izq']} der={x['der']}")
                    for x in r["desborde"]:
                        cuenta["se sale de su caja"] += 1
                        detalle["se sale de su caja"].add(
                            f"{dónde} · {x['que']} «{x['texto'][:28]}» {x['mide']}>{x['cabe']}")
                    for x in r["apretado"]:
                        cuenta["pegado a la tarjeta"] += 1
                        detalle["pegado a la tarjeta"].add(
                            f"{dónde} · {x['que']} en {x['en']} «{x['texto'][:24]}» "
                            f"hueco={x['hueco']}")
                    for x in r["solape"]:
                        cuenta["se pisan"] += 1
                        detalle["se pisan"].add(
                            f"{dónde} · {x['a']} × {x['b']} ({x['cuanto']}px)")
                    for x in r["alto"]:
                        cuenta["título de demasiadas líneas"] += 1
                        detalle["título de demasiadas líneas"].add(
                            f"{dónde} · {x['que']} «{x['texto'][:34]}» {x['lineas']} líneas")
                    for x in r["estrecho"]:
                        cuenta["columna de texto muy estrecha"] += 1
                        detalle["columna de texto muy estrecha"].add(
                            f"{dónde} · {x['que']} «{x['texto'][:22]}» {x['lineas']} líneas "
                            f"en {x['mide']}px de {x['cabia']}")
                ctx.close()
    return {"cuenta": cuenta, "detalle": detalle}, pantallas


def contraste(nav, base: str, rutas, temas) -> tuple[dict, int]:
    """El contraste no cambia con el idioma ni con el ancho: solo con el tema."""
    cuenta: collections.Counter = collections.Counter()
    detalle: dict[str, set[str]] = collections.defaultdict(set)
    pantallas = 0
    for tema in temas:
        ctx = _contexto(nav, 1280, tema)
        pg = ctx.new_page()
        for ruta in rutas:
            pantallas += 1
            pg.goto(f"{base}{ruta}")
            pg.wait_for_load_state("networkidle")
            for m in pg.evaluate(CONTRASTE):
                cuenta["contraste por debajo de la norma"] += 1
                detalle["contraste por debajo de la norma"].add(
                    f"{tema} {ruta} · {m['que'][:30]} «{m['texto'][:24]}» "
                    f"{m['ratio']} < {m['pide']} ({m['px']}px)")
        ctx.close()
    return {"cuenta": cuenta, "detalle": detalle}, pantallas


def dedos(nav, base: str, rutas, anchos) -> tuple[dict, int]:
    """Lo que se pulsa. Solo en los anchos de teléfono y tableta, con dedo."""
    cuenta: collections.Counter = collections.Counter()
    detalle: dict[str, set[str]] = collections.defaultdict(set)
    pantallas = 0
    for ancho in [a for a in anchos if a <= 820] or [390]:
        ctx = nav.new_context(viewport={"width": ancho, "height": 900}, has_touch=True,
                              user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 "
                                         "like Mac OS X) AppleWebKit/605.1.15")
        pg = ctx.new_page()
        for ruta in rutas:
            pantallas += 1
            pg.goto(f"{base}{ruta}")
            pg.wait_for_load_state("networkidle")
            for m in pg.evaluate(DEDOS, DEDO):
                # El que no llega ni a lo que exige la norma va aparte: ese no
                # es una mejora, es un incumplimiento.
                clave = ("por debajo de lo que exige la norma"
                         if min(m["alto"], m["ancho"]) < DEDO_NORMA
                         else "más pequeño de lo que pide un dedo")
                cuenta[clave] += 1
                detalle[clave].add(f"{ancho} {ruta} · {m['que'][:34]} «{m['texto']}» "
                                   f"{m['ancho']}x{m['alto']}")
        ctx.close()
    return {"cuenta": cuenta, "detalle": detalle}, pantallas


# --- la salida --------------------------------------------------------------

def enseña(partes: list[dict], pantallas: int) -> int:
    """Cada hallazgo con su sitio, para poder ir a verlo. Devuelve 1 si hay algo."""
    cuenta: collections.Counter = collections.Counter()
    detalle: dict[str, set[str]] = collections.defaultdict(set)
    for p in partes:
        cuenta.update(p["cuenta"])
        for k, v in p["detalle"].items():
            detalle[k] |= v

    print()
    if not cuenta:
        print(f"  Sin hallazgos en {pantallas} pantallas.")
        print()
        return 0
    for k, n in cuenta.most_common():
        print(f"  {k}: {n}")
        for d in sorted(detalle[k])[:14]:
            print("     ", d)
        if len(detalle[k]) > 14:
            print(f"      … y {len(detalle[k]) - 14} más")
        print()
    print(f"  {sum(cuenta.values())} hallazgos en {pantallas} pantallas.")
    print()
    return 1


def _lista(texto: str, todos, entero=False):
    """«de,nl» o «320,390». Vacío es todos."""
    if not texto:
        return list(todos)
    partes = [p.strip() for p in texto.split(",") if p.strip()]
    return [int(p) for p in partes] if entero else partes


def main(argv: list[str] | None = None) -> int:
    """La línea de órdenes del repaso de la portada."""
    p = argparse.ArgumentParser(prog="portada", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--idiomas", default="", help="cuáles, separados por comas (todos)")
    p.add_argument("--anchos", default="", help="cuáles, separados por comas (todos)")
    p.add_argument("--rutas", default="", help="cuáles, separadas por comas (todas)")
    p.add_argument("--solo", choices=("maqueta", "contraste", "dedos"), default=None,
                   help="un examen de los tres")
    p.add_argument("--rapido", action="store_true",
                   help="es+en, tres anchos, un tema: para mirar mientras se trabaja")
    args = p.parse_args(argv)

    idiomas = _lista(args.idiomas, IDIOMAS)
    anchos = _lista(args.anchos, ANCHOS, entero=True)
    rutas = _lista(args.rutas, RUTAS)
    # El tema no cambia dónde cae nada, solo los colores: para la maqueta basta
    # con uno, y el oscuro se repasa en los idiomas que peor caben.
    temas_maqueta = ["light", "dark"]
    if args.rapido:
        idiomas = [i for i in ("es", "en") if i in idiomas] or idiomas[:2]
        anchos = [a for a in (320, 390, 1024) if a in anchos] or anchos[:3]
        temas_maqueta = ["light"]

    print()
    print(f"  Repaso de la portada: {len(rutas)} rutas × {len(anchos)} anchos × "
          f"{len(idiomas)} idiomas")
    print()
    arranca = time.monotonic()

    base, servidor = _levanta()
    partes, pantallas = [], 0
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            nav = _navegador(pw)
            try:
                if args.solo in (None, "maqueta"):
                    parte, n = maqueta(nav, base, idiomas, anchos, rutas, temas_maqueta)
                    partes.append(parte); pantallas += n
                if args.solo in (None, "contraste"):
                    parte, n = contraste(nav, base, rutas, ["light", "dark"])
                    partes.append(parte); pantallas += n
                if args.solo in (None, "dedos"):
                    parte, n = dedos(nav, base, rutas, anchos)
                    partes.append(parte); pantallas += n
            finally:
                nav.close()
    finally:
        servidor.should_exit = True

    salida = enseña(partes, pantallas)
    print(f"  {time.monotonic() - arranca:.0f}s")
    return salida


if __name__ == "__main__":
    sys.exit(main())
