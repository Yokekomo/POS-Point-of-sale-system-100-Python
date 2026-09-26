"""[01846] Los estilos y los guiones, fuera del HTML.

Iban dentro de cada pantalla: treinta y siete kilobytes de estilos y treinta y
ocho de guiones, escritos otra vez, enteros, en **cada** página que se abre.
De los ochenta y cuatro que pesaba una pantalla de trabajo, nueve eran la
pantalla y setenta y cinco eran lo mismo de siempre.

En un ordenador no se nota. En la cámara sí: el móvil del carnicero con una
raya de cobertura se baja setenta y cinco kilobytes que ya tiene, cada vez que
toca una pestaña. Y en un plan de datos de los que pagan las casas, eso son
megas al día por cada persona.

Sacados a un fichero aparte se bajan **una vez** y el navegador los guarda. Lo
que hace falta para que eso funcione de verdad es que el nombre cambie cuando
cambia el contenido: si no, o se guarda poco tiempo —y no sirve de nada— o se
guarda mucho y un arreglo urgente no le llega a quien ya tiene la versión
vieja. El nombre lleva la huella de lo que hay dentro, así que un cambio es un
nombre nuevo y lo viejo no se sirve a nadie más.
"""
from __future__ import annotations

import hashlib
import pathlib
import re

from fastapi import HTTPException
from fastapi.responses import Response

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "estatico"

TIPOS = {".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8"}

# Un año y «immutable»: el navegador ni pregunta. Se puede porque el nombre
# lleva la huella del contenido; el día que el contenido cambie, el nombre es
# otro y este fichero deja de pedirse.
PARA_SIEMPRE = "public, max-age=31536000, immutable"
# Y una hora para una huella que no es la de ahora. Pasa cuando alguien tiene
# abierta una pantalla de antes de un despliegue: se le sirve lo de ahora, que
# es lo correcto, pero no se le deja clavado para siempre bajo un nombre que ya
# no significa nada.
UN_RATO = "public, max-age=3600"

NOMBRE = re.compile(r"^(?P<base>[a-z0-9_-]+)\.(?P<huella>[0-9a-f]{12})(?P<ext>\.css|\.js)$")

_guardado: dict[str, tuple[float, str, bytes]] = {}      # nombre → (mtime, huella, bytes)


class NoEstá(FileNotFoundError):
    """[01847] Se ha pedido un fichero que no está en la carpeta. Es un fallo nuestro."""


def _leer(nombre: str) -> tuple[str, bytes]:
    """[01848] El contenido y su huella, releyéndolo si ha cambiado en disco.

    Se mira la fecha del fichero y no se relee a ciegas: en el servidor no
    cambia nunca y esto se llama en cada pantalla. En un ordenador de trabajo
    sí cambia, y ahí lo que no se puede es tener que reiniciar para ver un
    estilo nuevo.
    """
    camino = RAIZ / nombre
    try:
        marca = camino.stat().st_mtime
    except OSError as porque:
        raise NoEstá(f"falta «{nombre}» en {RAIZ}") from porque
    tenía = _guardado.get(nombre)
    if tenía is None or tenía[0] != marca:
        datos = camino.read_bytes()
        _guardado[nombre] = (marca, hashlib.sha256(datos).hexdigest()[:12], datos)
    return _guardado[nombre][1], _guardado[nombre][2]


def url(nombre: str) -> str:
    """[01849] La dirección de un fichero, con la huella de lo que hay dentro."""
    huella, _ = _leer(nombre)
    base, punto, ext = nombre.rpartition(".")
    return f"/estatico/{base}.{huella}{punto}{ext}"


def responder(fichero: str) -> Response:
    """[01850] Sirve uno de estos ficheros. Nada más: aquí no hay nada de nadie."""
    partes = NOMBRE.match(fichero)
    if partes is None:
        raise HTTPException(status_code=404, detail="")
    nombre = partes["base"] + partes["ext"]
    try:
        huella, datos = _leer(nombre)
    except NoEstá:
        raise HTTPException(status_code=404, detail="") from None
    return Response(datos, media_type=TIPOS[partes["ext"]], headers={
        "Cache-Control": PARA_SIEMPRE if partes["huella"] == huella else UN_RATO,
        "ETag": f'"{huella}"'})


def enganchar(app, plantillas) -> None:
    """[01851] Deja la ruta puesta y la función al alcance de las plantillas.

    En la plantilla se escribe `{{ estatico("carne.css") }}` y sale la
    dirección con la huella de hoy. Que la huella la ponga el programa y no una
    persona es lo que evita el fallo de toda la vida: cambiar el fichero y
    olvidarse de cambiar el número.
    """
    plantillas.env.globals["estatico"] = url

    @app.get("/estatico/{fichero}", include_in_schema=False)
    def estatico(fichero: str):                              # noqa: ANN202
        """[01852] Un estilo o un guion, con su año de caducidad puesto."""
        return responder(fichero)
