"""Que «añadir a pantalla de inicio» cree una aplicación y no un marcador.

Esto parece cosmética y es lo único que protege el trabajo que se apunta
dentro de la cámara.

Safari borra **todo** el almacenamiento que escribe un guion —incluido el
`localStorage` donde vive la cola de apuntes pendientes— a los siete días de
usar Safari sin que nadie entre en el sitio. Es política de Apple y no hay
forma de desactivarla. La salida que la propia Apple documenta es que **las
aplicaciones añadidas a la pantalla de inicio se libran**, porque llevan su
propio contador de días.

Pero para que iOS considere aquello una aplicación hacen falta dos cosas, y no
había ninguna: un manifiesto con `display: standalone`, y la etiqueta vieja
`apple-mobile-web-app-capable`, que sigue haciendo falta porque iOS no lee el
`display` del manifiesto en todas las versiones. Sin las dos, lo que se crea es
un marcador que abre Safari, y ese no tiene la exención.

Traducido: un móvil de cámara con iPhone que se queda ocho días en un cajón
—agosto, unas vacaciones, un local que cierra— volvía con la cola vacía y sin
decírselo a nadie. Es el mismo fallo que el `catch` vacío del almacén, con
dos semanas de retraso.

Se prueba desde el servidor porque no hay otra forma: el borrado de Safari no
se reproduce en ningún navegador de pruebas —el WebKit de Playwright es el
motor, no las políticas de Apple— y lo único que está en nuestra mano es
mandar lo que iOS necesita leer.
"""
import json

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp

IDIOMAS = ("es", "en", "fr", "de", "nl", "ar", "hu")


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'manifiesto.db'}")
    db.create_all()
    return TestClient(meatapp.app, follow_redirects=False)


def _pide(cliente, lang="es"):
    r = cliente.get("/manifest.webmanifest", headers={"accept-language": lang})
    assert r.status_code == 200, r.text[:200]
    return r


def test_the_home_screen_icon_opens_an_app_and_not_a_bookmark(cliente):
    """`standalone` es la palabra que separa una cosa de la otra."""
    d = _pide(cliente).json()
    assert d["display"] == "standalone", (
        "sin esto, iOS crea un marcador que abre Safari, y el marcador no tiene "
        "la exención al borrado de los siete días")
    assert d["start_url"].startswith("/"), d["start_url"]
    assert d["icons"], "un icono hace falta para que se pueda añadir"


def test_it_is_served_as_a_manifest_and_not_as_plain_json(cliente):
    """Algunos navegadores no lo leen si no viene con su tipo."""
    assert _pide(cliente).headers["content-type"].startswith("application/manifest+json")


@pytest.mark.parametrize("lang", IDIOMAS)
def test_the_name_under_the_icon_is_in_the_language_of_whoever_installs_it(cliente, lang):
    """Es lo que esa persona va a tener delante en su móvil todos los días."""
    d = _pide(cliente, lang).json()
    assert d["short_name"] and not d["short_name"].startswith("m.app."), d["short_name"]
    assert d["name"] and not d["name"].startswith("m.app."), d["name"]
    # Debajo de un icono no caben más de doce o trece letras sin que se corte.
    assert len(d["short_name"]) <= 13, f"{lang}: «{d['short_name']}» no cabe"
    assert d["lang"] == lang
    assert d["dir"] == ("rtl" if lang == "ar" else "ltr")


def test_both_surfaces_tell_ios_that_this_is_an_app(cliente, tmp_path):
    """El armazón de dentro **y** la portada: se instala desde las dos.

    Son dos plantillas distintas —`public_shell.html` fuera, `base.html`
    dentro— y hay que comprobar las dos por separado. Mirar solo la portada
    dejaría pasar que al armazón de dentro le falten las etiquetas, que es
    justamente donde trabaja el que va a instalarlo en su móvil.
    """
    from tests.meat_helpers import new_house

    new_house("Asador Marina", "albano@marina.com")
    dentro = TestClient(meatapp.app, follow_redirects=False,
                        headers={"accept-language": "es"})
    r = dentro.post("/login", data={"email": "albano@marina.com",
                                    "password": "clave-larga-1"})
    assert r.status_code == 303, r.text[:200]

    for quien, pagina in (("la portada", cliente.get("/", headers={"accept-language": "es"}).text),
                          ("el armazón de dentro", dentro.get("/hoy").text)):
        assert 'rel="manifest"' in pagina, f"{quien}: no enlaza el manifiesto"
        assert 'name="apple-mobile-web-app-capable" content="yes"' in pagina, (
            f"{quien}: falta la etiqueta vieja de Apple, y iOS no lee el "
            "`display` del manifiesto en todas las versiones")
