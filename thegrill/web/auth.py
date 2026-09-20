"""Autenticación y autorización.

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
    """Credenciales inválidas, cuenta desactivada o código de acceso erróneo."""


class PermissionDenied(Exception):
    """El rol del usuario no permite esta acción."""


# ------------------------------------------------------------- contraseñas
def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS, lang: str = DEFAULT_LANG) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(t(lang, "auth.short_password", n=MIN_PASSWORD_LEN))
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), rounds)
    return f"pbkdf2_sha256${rounds}${salt}${dk.hex()}"


# Una contraseña que no vale para nadie, para gastar el mismo tiempo cuando el
# correo no existe.
DUMMY_HASH = ("pbkdf2_sha256$240000$0000000000000000000000000000000000000000000000000000000000000000$"
              "0000000000000000000000000000000000000000000000000000000000000000")


def verify_password(password: str, stored: str) -> bool:
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
    """Cambia la contraseña de alguien y cierra sus otras sesiones.

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
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError(t(lang, "auth.bad_email", email=email))
    return email


def new_join_code() -> str:
    """Código que el manager reparte para que su equipo se registre."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin caracteres ambiguos
    return "".join(secrets.choice(alphabet) for _ in range(8))


def unique_slug(session: Session, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "restaurante"
    slug, n = base, 1
    while session.query(Restaurant).filter_by(slug=slug).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


# --------------------------------------------------------------- registro
def create_restaurant(session: Session, name: str, manager_email: str, manager_name: str,
                      password: str, timezone: str = "UTC", currency: str = "USD",
                      language: str = DEFAULT_LANG) -> tuple[Restaurant, User]:
    """Alta de un restaurante nuevo con su primer manager (el dueño de la cuenta)."""
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
    """Alta de empleado con el código del restaurante."""
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
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(session: Session, email: str, password: str,
                 restaurant_id: int | None = None, lang: str = DEFAULT_LANG) -> User:
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
        # Sin esto, un correo que no existe responde al instante y uno que sí
        # tarda lo que tarda comprobar la contraseña. Esa diferencia dice quién
        # tiene cuenta aquí, así que se gasta el mismo trabajo en ambos casos.
        verify_password(password, DUMMY_HASH)
    raise AuthError(t(lang, "login.bad_credentials"))


def needs_second_step(user: User) -> bool:
    """Si esa persona entra con contraseña y además con los seis dígitos."""
    return bool(getattr(user, "totp_enabled", False) and user.totp_secret)


def start_session(session: Session, user: User, days: int = SESSION_DAYS,
                  pending_2fa: bool = False) -> tuple[str, AuthSession]:
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
    """Quién es el de esta sesión, si la sesión vale.

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
    """Los seis dígitos han valido: la sesión pasa a ser una sesión de verdad."""
    auth.pending_2fa = False
    user = session.get(User, auth.user_id)
    if user is not None:
        user.last_login = datetime.utcnow()
    session.flush()


def end_session(session: Session, token: str | None) -> None:
    if not token:
        return
    auth = session.query(AuthSession).filter_by(token_hash=_token_hash(token)).first()
    if auth:
        auth.revoked = True


def check_csrf(auth: AuthSession, submitted: str | None, lang: str = DEFAULT_LANG) -> None:
    if not submitted or not hmac.compare_digest(auth.csrf, submitted):
        raise PermissionDenied(t(lang, "error.csrf"))


# ------------------------------------------------------------- permisos
def require_manager(user: User, lang: str = DEFAULT_LANG) -> None:
    if user.role != Role.MANAGER:
        raise PermissionDenied(t(user.language or lang, "error.managers_only"))


def same_restaurant(user: User, restaurant_id: int, lang: str = DEFAULT_LANG) -> None:
    """Aislamiento entre restaurantes: nadie ve datos de otro."""
    if user.restaurant_id != restaurant_id:
        raise PermissionDenied(t(user.language or lang, "error.other_restaurant"))
