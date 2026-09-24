"""[01059] Escribir los números como los lee quien los va a leer.

Una casa española que abre el programa en español ve «9.400 kg». Lo lee nueve
mil cuatrocientos kilos y tarda un segundo en darse cuenta de que son nueve
coma cuatro, y ese segundo se repite cincuenta veces al día. Peor: cuando se
dude de una cifra, la duda va a caer sobre el programa, y un programa del que
se duda no se usa.

El arreglo no es tocar doscientas plantillas. El filtro `format` de Jinja no es
más que el `%` de Python, así que se cambia por uno que hace lo mismo y
después pone el separador del idioma. Una función, y con ella todos los
números de las dos ediciones.

Se toca **solo** lo que es un número y nada más que un número: si de ahí sale
un nombre, una fecha o una frase, se devuelve tal cual. Por eso hay una
comprobación con expresión regular en medio y no una confianza.
"""
from __future__ import annotations

import re

from jinja2 import pass_context

# [01064] Quién escribe los decimales con coma. El árabe de estas casas usa cifras
# occidentales con punto, así que se queda fuera.
COMA = {"es", "fr", "de", "nl", "hu"}

# [01065] Un número y nada más: signo, cifras, un punto decimal y, como mucho, la
# unidad pegada detrás —«12.345 g», «7 %», «4 °C»—. Si sale cualquier otra
# cosa —un nombre, una fecha, un serial, algo ya agrupado por miles— no se
# toca. La comprobación es esta y no una confianza.
SOLO_NUMERO = re.compile(r"^[-+−]?\d+(?:\.\d+)?(?:\s?(?:%|[A-Za-z°]{1,3}))?$")


def con_coma(texto: str) -> str:
    """[01060] El punto decimal, en coma. Solo si eso es todo lo que hay."""
    return texto.replace(".", ",") if SOLO_NUMERO.match(texto) else texto


def local(valor, lang: str):
    """[01061] Un valor que va dentro de una frase traducida, con su separador.

    Los números de las pantallas pasan por el filtro de las plantillas, pero
    los que van metidos dentro de un texto —«8017 ha perdido 0,412 kg»— los
    arma Python antes de que Jinja los vea, y salían siempre con punto. Se
    arreglan aquí, en `t()`, que es por donde pasan todos.
    """
    if lang not in COMA:
        return valor
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        return valor
    return con_coma(valor if isinstance(valor, str) else f"{valor:.10g}")


@pass_context
def formato(ctx, plantilla, *args):
    """[01062] Lo mismo que el `format` de Jinja, y después el separador del idioma."""
    texto = plantilla % (args[0] if len(args) == 1 else args)
    return con_coma(texto) if ctx.get("lang") in COMA else texto


def enganchar(entorno) -> None:
    """[01063] Deja el filtro puesto en un entorno de plantillas. Idempotente."""
    entorno.filters["format"] = formato
