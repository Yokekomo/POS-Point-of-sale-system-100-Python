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

import gzip
import hashlib
import pathlib
import re

from fastapi import HTTPException, Request
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

# [01886] Comprimido, y **solo aquí**. Un estilo y un guion son iguales para todo el
# mundo: no llevan el token del formulario ni nada que haya escrito nadie, así
# que comprimirlos no le enseña nada a quien mira el cable. El HTML sí lleva las
# dos cosas a la vez —el token y lo que se acaba de teclear—, y comprimir eso es
# la receta de BREACH: quien puede meter texto en la pantalla mide cuánto
# encoge la respuesta y va sacando el token letra a letra. Por eso el HTML se
# sirve tal cual y estos ficheros no.
#
# Se comprime una vez, al leerlo, y no en cada petición: son los mismos bytes
# siempre. Nivel 9 porque se paga una vez y se ahorra en todas.
NIVEL = 9

_guardado: dict[str, tuple[float, str, bytes, bytes]] = {}   # nombre → (mtime, huella, bytes, apretado)


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
        # mtime=0 para que el fichero comprimido salga igual byte a byte en cada
        # arranque: si llevara la hora, dos servidores del mismo despliegue
        # darían dos ETags distintos para el mismo estilo.
        apretado = gzip.compress(datos, NIVEL, mtime=0)
        _guardado[nombre] = (marca, hashlib.sha256(datos).hexdigest()[:12], datos, apretado)
    guardado = _guardado[nombre]
    return guardado[1], guardado[2]


def _apretado(nombre: str) -> bytes:
    """[01887] Los mismos bytes, comprimidos una sola vez."""
    _leer(nombre)
    return _guardado[nombre][3]


def url(nombre: str) -> str:
    """[01849] La dirección de un fichero, con la huella de lo que hay dentro."""
    huella, _ = _leer(nombre)
    base, punto, ext = nombre.rpartition(".")
    return f"/estatico/{base}.{huella}{punto}{ext}"


def quiere_apretado(cabecera: str | None) -> bool:
    """[01888] Si quien pide sabe descomprimir. Todos saben, pero se pregunta igual."""
    return "gzip" in (cabecera or "").lower()


def ya_lo_tiene(cabecera: str | None, huella: str) -> bool:
    """[01889] Si el navegador ya tiene esta versión, por su ETag.

    `If-None-Match` puede traer varios y puede traer el débil de delante
    (`W/"..."`), así que se parte y se compara la huella pelada. Un `*` es
    «cualquiera que tengas», y lo que hay es esta.
    """
    for trozo in (cabecera or "").split(","):
        trozo = trozo.strip()
        if trozo == "*":
            return True
        if trozo.startswith("W/"):
            trozo = trozo[2:]
        if trozo.strip('"') == huella:
            return True
    return False


def responder(fichero: str, acepta: str | None = None, tiene: str | None = None) -> Response:
    """[01850] Sirve uno de estos ficheros. Nada más: aquí no hay nada de nadie."""
    partes = NOMBRE.match(fichero)
    if partes is None:
        raise HTTPException(status_code=404, detail="")
    nombre = partes["base"] + partes["ext"]
    try:
        huella, datos = _leer(nombre)
    except NoEstá:
        raise HTTPException(status_code=404, detail="") from None
    cabeceras = {
        "Cache-Control": PARA_SIEMPRE if partes["huella"] == huella else UN_RATO,
        "ETag": f'"{huella}"',
        # [01890] Sin esto, una caché compartida —la del hotel, la del operador— se
        # queda con la copia comprimida y se la da a quien no la pidió.
        "Vary": "Accept-Encoding"}
    # [01891] Ya lo tiene: se le dice que sí y no se le manda nada. Pasa con la huella
    # vieja, la que solo dura una hora: sin esto, el móvil del carnicero se
    # rebajaba los cuarenta kilobytes enteros cada hora para recibir lo mismo.
    if ya_lo_tiene(tiene, huella):
        return Response(status_code=304, headers=cabeceras)
    if quiere_apretado(acepta):
        cabeceras["Content-Encoding"] = "gzip"
        return Response(_apretado(nombre), media_type=TIPOS[partes["ext"]], headers=cabeceras)
    return Response(datos, media_type=TIPOS[partes["ext"]], headers=cabeceras)


def enganchar(app, plantillas) -> None:
    """[01851] Deja la ruta puesta y la función al alcance de las plantillas.

    En la plantilla se escribe `{{ estatico("carne.css") }}` y sale la
    dirección con la huella de hoy. Que la huella la ponga el programa y no una
    persona es lo que evita el fallo de toda la vida: cambiar el fichero y
    olvidarse de cambiar el número.
    """
    plantillas.env.globals["estatico"] = url

    @app.get("/estatico/{fichero}", include_in_schema=False)
    def estatico(fichero: str, request: Request):            # noqa: ANN202
        """[01852] Un estilo o un guion, con su año de caducidad puesto."""
        return responder(fichero, request.headers.get("accept-encoding"),
                         request.headers.get("if-none-match"))
