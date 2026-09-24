"""[00545] Lo que protege la puerta.

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
from datetime import datetime
from collections import defaultdict, deque

# [00556] Intentos de acceso: cuántos fallos seguidos se aguantan y cuánto se espera.
LOGIN_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 300
LOGIN_LOCK_SECONDS = 300

# [00557] Formulario público: cuántas solicitudes por dirección y en cuánto tiempo.
FORM_ATTEMPTS = 5
FORM_WINDOW_SECONDS = 3600

# [00558] Las cabeceras y la política viven en el motor compartido, que es donde
# tienen que estar: la otra edición se había quedado sin ellas justamente por
# estar aquí. Se reexportan para no tocar las catorce referencias de siempre.
from thegrill.web.seguridad import CABECERAS as SECURITY_HEADERS  # noqa: E402
from thegrill.web.seguridad import nuevo_nonce as new_nonce       # noqa: E402


from thegrill.web.seguridad import politica as content_policy  # noqa: E402


# [00559] El freno vive en la base de datos cuando hay una a mano, porque si vive en la
# memoria de un proceso deja de frenar en cuanto hay más de uno: cinco intentos
# por trabajador son veinte para quien prueba contraseñas. La memoria se queda
# como respaldo para lo que corre sin base de datos.
_failures: dict[str, deque] = defaultdict(deque)
_forms: dict[str, deque] = defaultdict(deque)


def _prune(marks: deque, window: float, now: float) -> None:
    """[00546] Tira los intentos que ya quedan fuera de la ventana de tiempo."""
    while marks and now - marks[0] > window:
        marks.popleft()


def _marks(session, kind: str, key: str, window: float, now: float) -> list[float]:
    """[00547] Los fallos de esa llave dentro de la ventana, y de paso barre los viejos."""
    from thegrill.models import AccessBrake
    session.query(AccessBrake).filter(AccessBrake.ts < now - max(window, LOGIN_LOCK_SECONDS) * 4
                                      ).delete(synchronize_session=False)
    filas = (session.query(AccessBrake.ts)
             .filter(AccessBrake.kind == kind, AccessBrake.key == key[:160],
                     AccessBrake.ts >= now - window)
             .order_by(AccessBrake.ts).all())
    return [f[0] for f in filas]


def locked_for(key: str, now: float | None = None, session=None) -> int:
    """[00548] Segundos que faltan para poder volver a intentarlo. Cero si se puede."""
    now = time.time() if now is None else now
    if session is not None:
        marks = _marks(session, "login", key, LOGIN_WINDOW_SECONDS, now)
    else:
        marks = _failures[key]
        _prune(marks, LOGIN_WINDOW_SECONDS, now)
    if len(marks) < LOGIN_ATTEMPTS:
        return 0
    # [00560] La espera cuenta desde el último fallo: insistir alarga el castigo.
    return max(0, int(marks[-1] + LOGIN_LOCK_SECONDS - now))


def note_failure(key: str, now: float | None = None, session=None) -> None:
    """[00549] Apunta un intento de entrar fallido, para frenar a quien prueba contraseñas.

    En memoria mientras hay un solo proceso; en la base cuando hay varios, que
    es lo que hay en un servidor de verdad: si cada proceso lleva su cuenta
    aparte, quien prueba contraseñas tiene tantos intentos como procesos.
    """
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
    """[00550] Un acceso bueno borra la cuenta de fallos."""
    if session is not None:
        from thegrill.models import AccessBrake
        session.query(AccessBrake).filter_by(kind="login", key=key[:160]).delete(
            synchronize_session=False)
        session.flush()
        return
    _failures.pop(key, None)


def form_allowed(key: str, now: float | None = None, session=None) -> bool:
    """[00551] Si esa dirección puede mandar otra solicitud."""
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
    """[00552] Para las pruebas: empezar de cero."""
    _failures.clear()
    _forms.clear()
    if session is not None:
        from thegrill.models import AccessBrake
        session.query(AccessBrake).delete(synchronize_session=False)
        session.flush()


# ------------------------------------------------- envíos que llegan dos veces
class AlreadyDone(Exception):
    """[00553] Ese envío ya se aplicó: no se vuelve a tocar nada."""


def first_time(session, key: str, restaurant_id: int | None = None,
               user_id: int | None = None, path: str | None = None) -> bool:
    """[00554] Apunta el envío y dice si es la primera vez que llega.

    Un teléfono sin cobertura reintenta, y a veces el primer intento sí había
    llegado —se cayó la respuesta, no la escritura—. Aquí se decide con la
    regla de la base de datos, que es la única que no se equivoca cuando los
    dos intentos entran a la vez: el primero apunta su número, el segundo choca
    y se le dice que ya estaba hecho.
    """
    from sqlalchemy.exc import IntegrityError

    from thegrill.models import Submission
    key = (key or "").strip()[:64]
    if not key:
        return True                  # sin número no hay nada que recordar
    try:
        session.add(Submission(key=key, restaurant_id=restaurant_id, user_id=user_id,
                               path=(path or "")[:96]))
        session.flush()
    except IntegrityError:
        session.rollback()
        return False
    return True


def forget_old_submissions(session, days: int = 30) -> int:
    """[00555] Los números viejos ya no hacen falta: ningún teléfono guarda un mes."""
    from datetime import timedelta

    from thegrill.models import Submission
    limite = datetime.utcnow() - timedelta(days=days)
    n = (session.query(Submission).filter(Submission.created_at < limite)
         .delete(synchronize_session=False))
    return n
