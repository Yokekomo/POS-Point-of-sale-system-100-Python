"""[00994] Autenticación y autorización.

- Contraseñas con PBKDF2-HMAC-SHA256 y sal por usuario (sin dependencias externas).
- Sesiones por cookie: el token viaja al navegador, en la base solo vive su hash.
- Dos roles: MANAGER (todo) y EMPLOYEE (solo registrar).
- CSRF por sesión en cada formulario.
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from thegrill.models import AuthSession, Restaurant, Role, User
from thegrill.web.i18n import DEFAULT_LANG, t

PBKDF2_ROUNDS = 240_000
SESSION_DAYS = 14
COOKIE_NAME = "grill_session"
MIN_PASSWORD_LEN = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """[00995] Credenciales inválidas, cuenta desactivada o código de acceso erróneo."""


class PermissionDenied(Exception):
    """[00996] El rol del usuario no permite esta acción."""


# ------------------------------------------------------------- contraseñas
def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS, lang: str = DEFAULT_LANG) -> str:
    """[00997] Guarda la contraseña de forma que no se pueda deshacer.

    PBKDF2 con muchas vueltas y una sal distinta para cada una: si alguien se
    lleva la tabla de usuarios, no se lleva las contraseñas. Las cortas se
    rechazan aquí, que es por donde pasan todas.
    """
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(t(lang, "auth.short_password", n=MIN_PASSWORD_LEN))
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), rounds)
    return f"pbkdf2_sha256${rounds}${salt}${dk.hex()}"


# [01015] Una contraseña que no vale para nadie, para gastar el mismo tiempo cuando el
# correo no existe.
DUMMY_HASH = ("pbkdf2_sha256$240000$0000000000000000000000000000000000000000000000000000000000000000$"
              "0000000000000000000000000000000000000000000000000000000000000000")


def verify_password(password: str, stored: str) -> bool:
    """[00998] Si esa contraseña es la guardada, sin decir nada de más.

    Se comparan los dos resúmenes en tiempo constante: una comparación normal
    tarda un poco más cuantas más letras coinciden, y ese «poco más» se puede
    medir letra a letra hasta sacar la contraseña entera. Un dato guardado que
    no se entiende es un «no», nunca un error a la cara.
    """
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), digest)


def set_password(session: Session, user: User, new: str, *, current: str | None = None,
                 close_others: str | None = None, lang: str = DEFAULT_LANG) -> None:
    """[00999] Cambia la contraseña de alguien y cierra sus otras sesiones.

    Si se pasa `current`, hay que acertarla: es el caso de uno cambiando la
    suya. Cuando la cambia un manager o el dueño no se pide, porque la persona
    precisamente no la sabe.

    Cambiar la contraseña echa de todas las sesiones abiertas menos de la que
    la está cambiando: si alguien había entrado con la vieja, deja de estar
    dentro. Eso es media razón para cambiarla.
    """
    if current is not None and not verify_password(current, user.password_hash or ""):
        raise AuthError(t(lang, "auth.wrong_current"))
    user.password_hash = hash_password(new, lang=lang)
    session.query(AuthSession).filter(
        AuthSession.user_id == user.id,
        AuthSession.token_hash != (_token_hash(close_others) if close_others else "")
    ).delete(synchronize_session=False)
    session.flush()


def normalize_email(email: str, lang: str = DEFAULT_LANG) -> str:
    """[01000] Deja el correo en minúsculas y sin espacios, y comprueba que lo parece.

    Mismo correo escrito de dos maneras es la misma persona: sin esto, quien
    se dio de alta con mayúsculas no puede volver a entrar.
    """
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError(t(lang, "auth.bad_email", email=email))
    return email


def new_join_code() -> str:
    """[01001] Código que el manager reparte para que su equipo se registre."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin caracteres ambiguos
    return "".join(secrets.choice(alphabet) for _ in range(8))


def unique_slug(session: Session, name: str) -> str:
    """[01002] El nombre corto de la casa para la URL, sin repetirse.

    Dos «La Parrilla» son dos casas distintas y no pueden compartir dirección,
    así que a la segunda se le pone un número detrás.
    """
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "restaurante"
    slug, n = base, 1
    while session.query(Restaurant).filter_by(slug=slug).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


# --------------------------------------------------------------- registro
def create_restaurant(session: Session, name: str, manager_email: str, manager_name: str,
                      password: str, timezone: str = "UTC", currency: str = "EUR",
                      language: str = DEFAULT_LANG) -> tuple[Restaurant, User]:
    """[01003] Alta de un restaurante nuevo con su primer manager (el dueño de la cuenta)."""
    email = normalize_email(manager_email, language)
    code = new_join_code()
    while session.query(Restaurant).filter_by(join_code=code).first():
        code = new_join_code()
    restaurant = Restaurant(name=name.strip(), slug=unique_slug(session, name),
                            join_code=code, timezone=timezone, currency=currency,
                            language=language)
    session.add(restaurant)
    session.flush()
    manager = User(restaurant_id=restaurant.id, email=email, name=manager_name.strip(),
                   role=Role.MANAGER, language=language,
                   password_hash=hash_password(password, lang=language))
    session.add(manager)
    session.flush()
    return restaurant, manager


def join_restaurant(session: Session, join_code: str, email: str, name: str, password: str,
                    role: Role = Role.EMPLOYEE, language: str | None = None) -> User:
    """[01004] Alta de empleado con el código del restaurante."""
    lang = language or DEFAULT_LANG
    restaurant = session.query(Restaurant).filter_by(join_code=(join_code or "").strip().upper()).first()
    if restaurant is None or not restaurant.active:
        raise AuthError(t(lang, "join.bad_code"))
    lang = language or restaurant.language or DEFAULT_LANG
    email = normalize_email(email, lang)
    if session.query(User).filter_by(restaurant_id=restaurant.id, email=email).first():
        raise AuthError(t(lang, "join.email_taken"))
    user = User(restaurant_id=restaurant.id, email=email, name=name.strip(), role=role,
                language=language, password_hash=hash_password(password, lang=lang))
    session.add(user)
    session.flush()
    return user


# ---------------------------------------------------------------- sesiones
def _token_hash(token: str) -> str:
    """[01005] El resumen del testigo de sesión: en la base no se guarda el testigo.

    Quien lea la tabla no puede entrar con lo que lee.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(session: Session, email: str, password: str,
                 restaurant_id: int | None = None, lang: str = DEFAULT_LANG) -> User:
    """[01006] Entra con correo y contraseña, o no entra.

    El mismo mensaje para todo —no existe, contraseña mala, cuenta cerrada—:
    decir cuál de las tres es sería decirle a quien prueba correos cuáles
    existen. Y cuando el correo no existe se gasta el mismo trabajo que si
    existiera, porque contestar antes también lo diría.
    """
    try:
        email = normalize_email(email, lang)
    except ValueError:
        raise AuthError(t(lang, "login.bad_credentials")) from None
    q = session.query(User).filter_by(email=email)
    if restaurant_id is not None:
        q = q.filter_by(restaurant_id=restaurant_id)
    candidates = q.all()
    for user in candidates:
        if verify_password(password, user.password_hash):
            if not user.active:
                raise AuthError(t(user.language or lang, "login.inactive"))
            return user
    if not candidates:
        # [01016] Sin esto, un correo que no existe responde al instante y uno que sí
        # tarda lo que tarda comprobar la contraseña. Esa diferencia dice quién
        # tiene cuenta aquí, así que se gasta el mismo trabajo en ambos casos.
        verify_password(password, DUMMY_HASH)
    raise AuthError(t(lang, "login.bad_credentials"))


def needs_second_step(user: User) -> bool:
    """[01007] Si esa persona entra con contraseña y además con los seis dígitos."""
    return bool(getattr(user, "totp_enabled", False) and user.totp_secret)


def start_session(session: Session, user: User, days: int = SESSION_DAYS,
                  pending_2fa: bool = False) -> tuple[str, AuthSession]:
    """[01008] Abre la sesión y devuelve el testigo que se guarda en la cookie.

    El testigo solo existe aquí: en la base queda su resumen. Con el segundo
    factor pendiente la sesión nace a medias —sirve para pedir el código y
    para nada más— y por eso tampoco se apunta todavía la última entrada.
    """
    token = secrets.token_urlsafe(32)
    auth = AuthSession(token_hash=_token_hash(token), csrf=secrets.token_urlsafe(24),
                       user_id=user.id, expires_at=datetime.utcnow() + timedelta(days=days),
                       pending_2fa=bool(pending_2fa))
    session.add(auth)
    if not pending_2fa:
        user.last_login = datetime.utcnow()
    session.flush()
    return token, auth


def resolve_session(session: Session, token: str | None,
                    allow_pending: bool = False) -> tuple[User, AuthSession] | None:
    """[01009] Quién es el de esta sesión, si la sesión vale.

    Una sesión a la espera de los seis dígitos no vale para nada más que para
    la pantalla que los pide: hasta que se teclean, esa persona no ha entrado.
    """
    if not token:
        return None
    auth = session.query(AuthSession).filter_by(token_hash=_token_hash(token), revoked=False).first()
    if auth is None or auth.expires_at < datetime.utcnow():
        return None
    if auth.pending_2fa and not allow_pending:
        return None
    user = session.get(User, auth.user_id)
    if user is None or not user.active:
        return None
    return user, auth


def finish_second_step(session: Session, auth: AuthSession) -> None:
    """[01010] Los seis dígitos han valido: la sesión pasa a ser una sesión de verdad."""
    auth.pending_2fa = False
    user = session.get(User, auth.user_id)
    if user is not None:
        user.last_login = datetime.utcnow()
    session.flush()


def end_session(session: Session, token: str | None) -> None:
    """[01011] Cierra la sesión. Sin testigo no hay nada que cerrar."""
    if not token:
        return
    auth = session.query(AuthSession).filter_by(token_hash=_token_hash(token)).first()
    if auth:
        auth.revoked = True


def check_csrf(auth: AuthSession, submitted: str | None, lang: str = DEFAULT_LANG) -> None:
    """[01012] Comprueba que el formulario salió de nuestra pantalla.

    Sin esto, otra página abierta en el mismo navegador puede mandar
    formularios en nombre de quien está dentro —borrar un inventario, cambiar
    una contraseña— sin que se entere.
    """
    if not submitted or not hmac.compare_digest(auth.csrf, submitted):
        raise PermissionDenied(t(lang, "error.csrf"))


# ------------------------------------------------------------- permisos
def require_manager(user: User, lang: str = DEFAULT_LANG) -> None:
    """[01013] Cierra la puerta a quien no lleva la casa."""
    if user.role != Role.MANAGER:
        raise PermissionDenied(t(user.language or lang, "error.managers_only"))


def same_restaurant(user: User, restaurant_id: int, lang: str = DEFAULT_LANG) -> None:
    """[01014] Aislamiento entre restaurantes: nadie ve datos de otro."""
    if user.restaurant_id != restaurant_id:
        raise PermissionDenied(t(user.language or lang, "error.other_restaurant"))
