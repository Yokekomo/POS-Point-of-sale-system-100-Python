"""Las cabeceras que el navegador tiene que recibir, en las dos ediciones.

Estaban solo en la edición de carne. La de cocina no mandaba ninguna: ni
política de contenido, ni «no adivines el tipo», ni «no te dejes embeber». Y
lo peor no era la falta: era que sus plantillas escribían `<script nonce="">`,
así que al leerlas parecía que había política y no había ninguna.

Vive aquí —en el motor compartido— y no en una de las dos, que es lo que hizo
que se quedara a medias. Enganchar esto es una línea por aplicación; no
engancharlo se nota cuando ya es tarde.
"""
from __future__ import annotations

import secrets

from fastapi import Request

CABECERAS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def nuevo_nonce() -> str:
    """Un número distinto en cada respuesta para marcar nuestros guiones."""
    return secrets.token_urlsafe(16)


def politica(nonce: str) -> str:
    """Qué puede cargar y ejecutar el navegador en esta página.

    Los guiones se marcan con el número de esta respuesta: así el navegador
    ejecuta los nuestros y no uno que alguien consiga colar. Con
    `unsafe-inline` puesto, un guion inyectado se ejecutaría igual.

    Los estilos sí llevan `unsafe-inline`, porque el HTML usa `style=` en
    muchos sitios; un estilo inyectado no ejecuta código.

    `blob:` en las imágenes: la recepción enseña la foto de la etiqueta recién
    hecha antes de mandarla, para saber que ha salido legible. Esa vista la
    crea la propia página con el fichero elegido; no trae nada de fuera.
    """
    return (f"default-src 'self'; img-src 'self' data: blob:; "
            f"style-src 'self' 'unsafe-inline'; "
            f"script-src 'self' 'nonce-{nonce}'; form-action 'self'; "
            f"frame-ancestors 'none'; base-uri 'self'; object-src 'none'; "
            f"connect-src 'self'")


def por_https(request: Request) -> bool:
    """Si la petición llegó por HTTPS, mirando también lo que dice el proxy."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def enganchar(app) -> None:
    """Pone las cabeceras en todas las respuestas, también en las de error."""
    @app.middleware("http")
    async def _cabeceras(request: Request, call_next):        # noqa: ANN001
        """Pone las cabeceras de seguridad en todas las respuestas.

        Un nonce distinto por petición para que solo corra el javascript nuestro, y
        HSTS solo cuando se entra por https: ponerlo en una prueba local dejaría el
        navegador sin poder volver a entrar por http durante un año.
        """
        request.state.nonce = nuevo_nonce()
        response = await call_next(request)
        for cabecera, valor in CABECERAS.items():
            response.headers.setdefault(cabecera, valor)
        response.headers.setdefault("Content-Security-Policy",
                                    politica(request.state.nonce))
        if por_https(request):
            response.headers.setdefault("Strict-Transport-Security",
                                        "max-age=31536000; includeSubDomains")
        return response
