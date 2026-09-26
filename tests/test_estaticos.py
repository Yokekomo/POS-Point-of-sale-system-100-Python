"""El estilo y el guion, fuera del HTML y bajados una sola vez.

Iban dentro de cada pantalla: treinta y siete kilobytes de estilos y treinta y
ocho de guiones, escritos enteros otra vez en **cada** página que se abre. De
los veintiséis kilobytes que pesaba una pantalla de trabajo comprimida,
veintidós eran lo mismo de siempre.

En un ordenador no se nota. En la cámara sí: el móvil del carnicero con una
raya de cobertura se baja lo que ya tiene cada vez que toca una pestaña.
"""
import gzip
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.web import app as webapp
from thegrill.web import estaticos

from tests.meat_helpers import SPANISH, login, new_house

RAIZ = pathlib.Path(__file__).resolve().parents[1]
PANTALLAS = ("/hoy", "/carne", "/maduracion", "/recepcion", "/despiece", "/merma",
             "/inventario", "/traslados", "/ventas", "/parte")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'e.db'}")
    db.create_all()
    new_house(language="es")
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        login(c)
        yield c


# --------------------------------------------- lo que ya no se baja cada vez
def test_a_work_screen_no_longer_carries_the_whole_stylesheet(client):
    """El armazón —lo que se repite en las dieciocho pantallas— ya no va dentro.

    Lo que sí puede quedarse es lo propio de una pantalla: el guion de la
    recepción solo se baja al abrir la recepción, y son dos kilobytes. Lo que
    no podía quedarse eran los setenta y cinco que iban en todas.
    """
    for ruta in PANTALLAS:
        html = client.get(ruta).text
        assert "<style>" not in html, ruta
        enlinea = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
        mayor = max((len(x) for x in enlinea), default=0)
        assert mayor < 4500, (ruta, [len(x) for x in enlinea])


def test_ten_screens_weigh_a_fraction_of_what_they_weighed(client):
    """El número que importa: lo que se baja de verdad, comprimido."""
    total = sum(len(gzip.compress(client.get(r).content, 6)) for r in PANTALLAS)
    por_pantalla = total / len(PANTALLAS) / 1024
    assert por_pantalla < 9, f"{por_pantalla:.1f} kB por pantalla"


# --------------------------------------------------- y cómo se sirve lo de fuera
def test_what_is_taken_out_is_served_and_kept_for_a_year(client):
    """Guardarlo un año se puede porque el nombre lleva la huella del contenido."""
    direccion = estaticos.url("carne.css")
    r = client.get(direccion)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert len(r.content) > 20_000


def test_an_old_address_still_works_but_is_not_kept_forever(client):
    """Quien tenga abierta una pantalla de antes de un despliegue no se queda sin estilo.

    Se le sirve lo de ahora, que es lo correcto, pero una hora y no un año:
    bajo un nombre que ya no significa nada no se le clava nada a nadie.
    """
    r = client.get("/estatico/carne.000000000000.css")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=3600"


def test_a_file_that_is_not_ours_is_not_served(client):
    """Aquí no hay nada de nadie, y tampoco se sale de la carpeta."""
    assert client.get("/estatico/inventado.000000000000.css").status_code == 404
    assert client.get("/estatico/carne.css").status_code == 404          # sin huella
    assert client.get("/estatico/../../secreto.css").status_code in (404, 400)


def test_the_address_changes_when_what_is_inside_changes(tmp_path, monkeypatch):
    """Si no cambiara, un arreglo urgente no le llegaría a quien tenga la vieja."""
    carpeta = tmp_path / "estatico"
    carpeta.mkdir()
    (carpeta / "prueba.css").write_text("a{color:red}")
    monkeypatch.setattr(estaticos, "RAIZ", carpeta)
    monkeypatch.setattr(estaticos, "_guardado", {})
    antes = estaticos.url("prueba.css")
    (carpeta / "prueba.css").write_text("a{color:blue}")
    import os
    os.utime(carpeta / "prueba.css", (0, 0))       # otra fecha: se relee
    assert estaticos.url("prueba.css") != antes


# ------------------------------------------ y que no se quede nadie sin estilo
def test_every_file_the_templates_ask_for_is_really_there():
    """Una errata en el nombre deja la pantalla sin estilo y no falla nada.

    El navegador se lleva un 404 en la hoja y pinta la página en blanco y
    negro con las letras de Times. Nadie se entera hasta que lo ve un cliente.
    """
    pedidos = set()
    for plantilla in sorted((RAIZ / "thegrill").rglob("*.html")):
        pedidos |= set(re.findall(r'estatico\("([^"]+)"\)', plantilla.read_text()))
    assert pedidos, "no he encontrado ni una plantilla que pida un fichero"
    faltan = [x for x in pedidos if not (estaticos.RAIZ / x).is_file()]
    assert faltan == [], faltan


def test_nothing_is_left_lying_in_the_folder_that_nobody_asks_for():
    """Y al revés: un fichero que ya no usa nadie se queda ocupando sitio."""
    pedidos = set()
    for plantilla in sorted((RAIZ / "thegrill").rglob("*.html")):
        pedidos |= set(re.findall(r'estatico\("([^"]+)"\)', plantilla.read_text()))
    pedidos |= set(meatapp.ESTATICOS_DE_MANO)
    hay = {f.name for f in estaticos.RAIZ.iterdir() if f.is_file()}
    assert hay - pedidos == set(), hay - pedidos


def test_the_helper_in_the_chiller_keeps_the_style_too(client):
    """Una pantalla guardada que abre sin estilo parece un programa roto.

    Antes el estilo iba dentro de la pantalla, así que guardar la pantalla era
    guardarlo todo. Ahora son ficheros aparte: si el ayudante no los guarda, en
    la cámara sale la página en blanco y negro.
    """
    guion = client.get("/sw.js").text
    # Lo que hace falta no es la lista que tenga escrita el programa: es lo que
    # la pantalla pide de verdad. Mirar la lista contra sí misma daba verde
    # aunque se quedara vacía, que es justo la avería que hay que coger.
    pide = set(re.findall(r'(/estatico/[^"\']+)', client.get("/hoy").text))
    assert pide, "la pantalla no pide ningún fichero de fuera"
    faltan = [x for x in pide if x not in guion]
    assert faltan == [], faltan
    # Y van al armazón, que no se borra al cerrar la sesión.
    assert "/estatico/" in guion.split("DEPOSITO")[1][:200]


def test_the_screen_says_its_theme_before_the_stylesheet_arrives(client):
    """La hoja llega unas décimas después; en esas décimas el tema lo dice esto."""
    for ruta in PANTALLAS:
        html = client.get(ruta).text
        cabeza = html.split("</head>")[0]
        assert '<meta name="color-scheme" content="light dark">' in cabeza, ruta
        assert cabeza.index('name="color-scheme"') < cabeza.index("/estatico/"), ruta


def test_the_kitchen_edition_got_the_same(tmp_path, monkeypatch):
    """Las dos ediciones, no una: comparten motor y comparten el problema."""
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'c.db'}")
    db.create_all()
    with TestClient(webapp.app, follow_redirects=False) as c:
        html = c.get("/login").text
        assert "<style>" not in html
        assert estaticos.url("cocina.css") in html
        assert c.get(estaticos.url("cocina.css")).status_code == 200


# ------------------------------------------------ lo que de verdad va por el cable
def test_the_style_and_the_script_travel_squeezed(client):
    """Sacarlos fuera sirve de poco si van crudos por el cable.

    Ochenta y siete kilobytes de estilo y guion salían sin comprimir: el móvil
    con una raya se los bajaba enteros. Comprimidos son treinta, y son los
    mismos bytes: se comprime una vez al leerlos, no en cada petición.
    """
    pantalla = client.get("/recepcion").text
    direcciones = re.findall(r'(?:href|src)="(/estatico/[^"]+)"', pantalla)
    assert direcciones, "la pantalla no enlaza nada de fuera"
    crudo = apretado = 0
    for direccion in direcciones:
        entero = client.get(direccion, headers={"accept-encoding": "identity"})
        corto = client.get(direccion, headers={"accept-encoding": "gzip"})
        assert corto.headers["content-encoding"] == "gzip"
        # Sin esto, la caché del hotel le da la copia comprimida a quien no la pidió.
        assert corto.headers["vary"] == "Accept-Encoding"
        assert corto.text == entero.text          # los mismos bytes al llegar
        crudo += int(entero.headers["content-length"])
        apretado += int(corto.headers["content-length"])
    assert apretado < crudo * 0.45, f"{crudo} -> {apretado} bytes"


def test_the_html_is_not_squeezed_and_that_is_on_purpose(client):
    """El HTML lleva el token del formulario y lo que acaba de teclear alguien.

    Comprimir las dos cosas juntas es la receta de BREACH: quien puede meter
    texto en la pantalla mide cuánto encoge la respuesta y va sacando el token
    letra a letra. Un estilo no lleva ni lo uno ni lo otro, y por eso ese sí.
    """
    pantalla = client.get("/recepcion", headers={"accept-encoding": "gzip, br"})
    assert "content-encoding" not in pantalla.headers
    assert 'name="csrf"' in pantalla.text          # el secreto que no se comprime


def test_what_the_browser_already_has_is_not_sent_again(client):
    """La huella vieja solo dura una hora: sin un 304, cada hora se baja entera."""
    direccion = estaticos.url("carne.css")
    etag = client.get(direccion).headers["etag"]
    assert etag

    for cabecera in (etag, f"W/{etag}", "*", f'"otra-cosa", {etag}'):
        vuelta = client.get(direccion, headers={"if-none-match": cabecera})
        assert vuelta.status_code == 304, cabecera
        assert not vuelta.content
        assert vuelta.headers["etag"] == etag

    # Y el que tiene otra versión sí se la baja.
    otra = client.get(direccion, headers={"if-none-match": '"aaaaaaaaaaaa"'})
    assert otra.status_code == 200 and len(otra.content) > 20_000


def test_the_squeezed_copy_is_the_same_on_every_server(tmp_path, monkeypatch):
    """Dos servidores del mismo despliegue tienen que dar los mismos bytes.

    El formato lleva un hueco para la hora. Si se dejara puesta, el mismo
    estilo saldría distinto en cada arranque y las cachés de por medio no
    podrían darlo por el mismo.
    """
    carpeta = tmp_path / "estatico"
    carpeta.mkdir()
    (carpeta / "prueba.css").write_text("body{color:#111}\n" * 200)
    monkeypatch.setattr(estaticos, "RAIZ", carpeta)
    monkeypatch.setattr(estaticos, "_guardado", {})
    uno = estaticos._apretado("prueba.css")
    monkeypatch.setattr(estaticos, "_guardado", {})
    otro = estaticos._apretado("prueba.css")
    assert uno == otro
    assert gzip.decompress(uno) == (carpeta / "prueba.css").read_bytes()
