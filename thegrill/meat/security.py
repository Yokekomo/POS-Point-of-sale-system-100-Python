"""Lo que protege la puerta.

Tres cosas, y ninguna es decorativa:

- **Cabeceras**: el navegador recibe instrucciones de no dejarse embeber, no
  adivinar tipos de contenido, no filtrar la dirección a terceros y no cargar
  nada de fuera. Esta web no carga recursos externos, así que la política puede
  ser estricta de verdad.
- **Freno al probar contraseñas**: una cuenta aguanta unos cuantos fallos
  seguidos y luego espera. Sin freno, una contraseña corta se adivina en una
  tarde.
- **Freno al formulario público**: es la única puerta abierta a internet, y una
  puerta abierta sin freno se llena de basura.

Los frenos viven en memoria: sobran para un proceso y no añaden una base de
datos que mantener. Con varios procesos detrás de un balanceador, el freno es
por proceso; conviene saberlo antes que creerse protegido.

Lo que **no** hay aquí, a propósito: números de tarjeta. Esta plataforma no los
pide, no los guarda y no los ve. El cobro va por transferencia o por una
pasarela que se encargue de eso, que para eso está certificada.
"""
import secrets
import time
from collections import defaultdict, deque

# Intentos de acceso: cuántos fallos seguidos se aguantan y cuánto se espera.
LOGIN_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 300
LOGIN_LOCK_SECONDS = 300

# Formulario público: cuántas solicitudes por dirección y en cuánto tiempo.
FORM_ATTEMPTS = 5
FORM_WINDOW_SECONDS = 3600

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def new_nonce() -> str:
    """Un número distinto en cada respuesta para marcar nuestros scripts."""
    return secrets.token_urlsafe(16)


def content_policy(nonce: str) -> str:
    """Qué puede cargar y ejecutar el navegador en esta página.

    Los scripts se marcan con el número de esta respuesta: así el navegador
    ejecuta los nuestros y no uno que alguien consiga colar en la página. Con
    `unsafe-inline` puesto, cualquier script inyectado se ejecutaría igual.

    Los estilos sí llevan `unsafe-inline`, porque el HTML usa `style=` en
    muchos sitios; un estilo inyectado no ejecuta código.
    """
    return (f"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            f"script-src 'self' 'nonce-{nonce}'; form-action 'self'; "
            f"frame-ancestors 'none'; base-uri 'self'; object-src 'none'; "
            f"connect-src 'self'")

# El freno vive en la base de datos cuando hay una a mano, porque si vive en la
# memoria de un proceso deja de frenar en cuanto hay más de uno: cinco intentos
# por trabajador son veinte para quien prueba contraseñas. La memoria se queda
# como respaldo para lo que corre sin base de datos.
_failures: dict[str, deque] = defaultdict(deque)
_forms: dict[str, deque] = defaultdict(deque)


def _prune(marks: deque, window: float, now: float) -> None:
    while marks and now - marks[0] > window:
        marks.popleft()


def _marks(session, kind: str, key: str, window: float, now: float) -> list[float]:
    """Los fallos de esa llave dentro de la ventana, y de paso barre los viejos."""
    from thegrill.models import AccessBrake
    session.query(AccessBrake).filter(AccessBrake.ts < now - max(window, LOGIN_LOCK_SECONDS) * 4
                                      ).delete(synchronize_session=False)
    filas = (session.query(AccessBrake.ts)
             .filter(AccessBrake.kind == kind, AccessBrake.key == key[:160],
                     AccessBrake.ts >= now - window)
             .order_by(AccessBrake.ts).all())
    return [f[0] for f in filas]


def locked_for(key: str, now: float | None = None, session=None) -> int:
    """Segundos que faltan para poder volver a intentarlo. Cero si se puede."""
    now = time.time() if now is None else now
    if session is not None:
        marks = _marks(session, "login", key, LOGIN_WINDOW_SECONDS, now)
    else:
        marks = _failures[key]
        _prune(marks, LOGIN_WINDOW_SECONDS, now)
    if len(marks) < LOGIN_ATTEMPTS:
        return 0
    # La espera cuenta desde el último fallo: insistir alarga el castigo.
    return max(0, int(marks[-1] + LOGIN_LOCK_SECONDS - now))


def note_failure(key: str, now: float | None = None, session=None) -> None:
    now = time.time() if now is None else now
    if session is not None:
        from thegrill.models import AccessBrake
        session.add(AccessBrake(kind="login", key=key[:160], ts=now))
        session.flush()
        return
    marks = _failures[key]
    _prune(marks, LOGIN_WINDOW_SECONDS, now)
    marks.append(now)


def clear(key: str, session=None) -> None:
    """Un acceso bueno borra la cuenta de fallos."""
    if session is not None:
        from thegrill.models import AccessBrake
        session.query(AccessBrake).filter_by(kind="login", key=key[:160]).delete(
            synchronize_session=False)
        session.flush()
        return
    _failures.pop(key, None)


def form_allowed(key: str, now: float | None = None, session=None) -> bool:
    """Si esa dirección puede mandar otra solicitud."""
    now = time.time() if now is None else now
    if session is not None:
        from thegrill.models import AccessBrake
        marks = _marks(session, "form", key, FORM_WINDOW_SECONDS, now)
        if len(marks) >= FORM_ATTEMPTS:
            return False
        session.add(AccessBrake(kind="form", key=key[:160], ts=now))
        session.flush()
        return True
    marks = _forms[key]
    _prune(marks, FORM_WINDOW_SECONDS, now)
    if len(marks) >= FORM_ATTEMPTS:
        return False
    marks.append(now)
    return True


def reset(session=None) -> None:
    """Para las pruebas: empezar de cero."""
    _failures.clear()
    _forms.clear()
    if session is not None:
        from thegrill.models import AccessBrake
        session.query(AccessBrake).delete(synchronize_session=False)
        session.flush()
