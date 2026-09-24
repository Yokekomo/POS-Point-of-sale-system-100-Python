"""Verificación en dos pasos, la de los seis dígitos que cambian cada medio minuto.

Quien puede bloquear una casa entera o ver el dinero de todas no debería entrar
solo con una contraseña, porque una contraseña se apunta en un papel, se repite
en otra web y se la lleva quien mire por encima del hombro. Los seis dígitos
cambian cada treinta segundos y viven en el teléfono de esa persona.

Es el TOTP de siempre (RFC 6238), escrito aquí en veinte líneas para no meter
una librería más por esto: un secreto en base32, el reloj en tramos de treinta
segundos, y un HMAC del que se sacan seis cifras. Cualquier aplicación —Google
Authenticator, Aegis, 1Password— lo lee del código o del texto.

Y con los códigos de repuesto: seis, de un solo uso, que se enseñan una vez al
activarlo. Sin ellos, perder el teléfono es perder la cuenta.
"""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time

DIGITS = 6
STEP = 30              # segundos que dura cada código
WINDOW = 1             # se acepta el de antes y el de después: los relojes bailan
RECOVERY_CODES = 6
RECOVERY_LENGTH = 10   # letras y números, suficientes para no adivinarlos


def new_secret() -> str:
    """Un secreto nuevo, en base32, que es como lo leen las aplicaciones."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, when: float | None = None, offset: int = 0) -> str:
    """Los seis dígitos que valen en ese momento."""
    counter = int((time.time() if when is None else when) // STEP) + offset
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    start = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[start:start + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, when: float | None = None) -> bool:
    """Si ese código es uno de los que valen ahora mismo."""
    typed = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(typed) != DIGITS or not secret:
        return False
    for offset in range(-WINDOW, WINDOW + 1):
        if hmac.compare_digest(code_at(secret, when, offset), typed):
            return True
    return False


def uri(secret: str, email: str, issuer: str = "Control de carnes") -> str:
    """Lo que se escanea o se pega en la aplicación del teléfono."""
    from urllib.parse import quote
    label = quote(f"{issuer}:{email}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


# ------------------------------------------------------ códigos de repuesto
def new_recovery_codes(n: int = RECOVERY_CODES) -> list[str]:
    """Códigos de un solo uso, para el día que el teléfono se pierda o se rompa."""
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # sin letras que se confunden
    return ["".join(secrets.choice(alfabeto) for _ in range(RECOVERY_LENGTH))
            for _ in range(n)]


def _fingerprint(code: str) -> str:
    """Se guarda la huella, no el código: aunque se vea la base, no sirven."""
    clean = "".join(ch for ch in str(code or "") if ch.isalnum()).upper()
    return hashlib.sha256(f"thegrill/recovery/{clean}".encode()).hexdigest()


def store_recovery(codes: list[str]) -> str:
    """Guarda los códigos de recuperación sin guardar los códigos.

    En la base queda su huella: quien lea la tabla no puede entrar con ella.
    """
    return json.dumps([_fingerprint(c) for c in codes])


def spend_recovery(stored: str | None, code: str) -> tuple[bool, str]:
    """Gasta un código de repuesto. Devuelve si valía y lo que queda guardado."""
    try:
        huellas = json.loads(stored or "[]")
    except ValueError:
        return False, stored or "[]"
    mine = _fingerprint(code)
    for index, saved in enumerate(huellas):
        if hmac.compare_digest(str(saved), mine):
            huellas.pop(index)                 # de un solo uso: se gasta
            return True, json.dumps(huellas)
    return False, stored or "[]"


def recovery_left(stored: str | None) -> int:
    """Cuántos códigos de recuperación quedan sin gastar."""
    try:
        return len(json.loads(stored or "[]"))
    except ValueError:
        return 0
