"""Alta, acceso y sesiones."""
import pytest

from thegrill import db
from thegrill.models import Role
from thegrill.web import auth


@pytest.fixture
def session(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'a.db'}")
    db.create_all()
    with db.session_scope() as s:
        yield s


def test_password_hash_is_salted_and_verifiable():
    h1 = auth.hash_password("clave-secreta", rounds=1000)
    h2 = auth.hash_password("clave-secreta", rounds=1000)
    assert h1 != h2                                   # sal distinta por usuario
    assert "clave-secreta" not in h1                  # nunca en claro
    assert auth.verify_password("clave-secreta", h1)
    assert not auth.verify_password("clave-secretA", h1)
    assert not auth.verify_password("clave-secreta", "basura")


def test_short_password_rejected():
    with pytest.raises(ValueError):
        auth.hash_password("corta")


def test_invalid_email_rejected():
    with pytest.raises(ValueError):
        auth.normalize_email("no-es-un-email")
    assert auth.normalize_email("  Ana@Casa.COM ") == "ana@casa.com"


def test_signup_creates_manager_with_join_code(session):
    rest, manager = auth.create_restaurant(session, "Casa Pepe", "ana@casa.com", "Ana", "clave-larga-1")
    assert manager.role == Role.MANAGER
    assert rest.slug == "casa-pepe" and len(rest.join_code) == 8


def test_slug_is_unique_across_restaurants(session):
    a, _ = auth.create_restaurant(session, "Casa Pepe", "ana@casa.com", "Ana", "clave-larga-1")
    b, _ = auth.create_restaurant(session, "Casa Pepe", "eva@otra.com", "Eva", "clave-larga-2")
    assert a.slug != b.slug


def test_join_with_code_creates_employee(session):
    rest, _ = auth.create_restaurant(session, "Casa Pepe", "ana@casa.com", "Ana", "clave-larga-1")
    luis = auth.join_restaurant(session, rest.join_code.lower(), "luis@casa.com", "Luis", "clave-larga-2")
    assert luis.role == Role.EMPLOYEE and luis.restaurant_id == rest.id


def test_join_with_bad_code_fails(session):
    with pytest.raises(auth.AuthError):
        auth.join_restaurant(session, "NOEXISTE", "x@y.com", "X", "clave-larga-1")


def test_same_email_in_two_restaurants_is_allowed(session):
    a, _ = auth.create_restaurant(session, "A", "ana@casa.com", "Ana", "clave-larga-1")
    b, _ = auth.create_restaurant(session, "B", "eva@b.com", "Eva", "clave-larga-2")
    auth.join_restaurant(session, a.join_code, "luis@casa.com", "Luis", "clave-larga-3")
    auth.join_restaurant(session, b.join_code, "luis@casa.com", "Luis", "clave-larga-4")
    with pytest.raises(auth.AuthError):
        auth.join_restaurant(session, a.join_code, "luis@casa.com", "Luis", "clave-larga-5")


def test_deactivated_user_cannot_log_in(session):
    rest, _ = auth.create_restaurant(session, "Casa Pepe", "ana@casa.com", "Ana", "clave-larga-1")
    luis = auth.join_restaurant(session, rest.join_code, "luis@casa.com", "Luis", "clave-larga-2")
    luis.active = False
    session.flush()
    with pytest.raises(auth.AuthError):
        auth.authenticate(session, "luis@casa.com", "clave-larga-2")


def test_session_lifecycle(session):
    _, manager = auth.create_restaurant(session, "Casa Pepe", "ana@casa.com", "Ana", "clave-larga-1")
    token, auth_session = auth.start_session(session, manager)
    assert auth.resolve_session(session, token)[0].id == manager.id
    assert auth_session.token_hash != token            # el token en claro no se guarda
    auth.end_session(session, token)
    assert auth.resolve_session(session, token) is None
    assert auth.resolve_session(session, "inventado") is None


def test_expired_session_is_rejected(session):
    _, manager = auth.create_restaurant(session, "Casa Pepe", "ana@casa.com", "Ana", "clave-larga-1")
    token, _ = auth.start_session(session, manager, days=-1)
    assert auth.resolve_session(session, token) is None


def test_csrf_and_role_guards(session):
    rest, manager = auth.create_restaurant(session, "Casa Pepe", "ana@casa.com", "Ana", "clave-larga-1")
    luis = auth.join_restaurant(session, rest.join_code, "luis@casa.com", "Luis", "clave-larga-2")
    _, auth_session = auth.start_session(session, manager)
    auth.check_csrf(auth_session, auth_session.csrf)
    for bad in (None, "", "otro"):
        with pytest.raises(auth.PermissionDenied):
            auth.check_csrf(auth_session, bad)
    auth.require_manager(manager)
    with pytest.raises(auth.PermissionDenied):
        auth.require_manager(luis)
    with pytest.raises(auth.PermissionDenied):
        auth.same_restaurant(luis, rest.id + 99)


# ------------------------------------- el token, distinto en cada pantalla
def test_the_form_token_is_written_differently_on_every_screen():
    """El token no cambia en toda la sesión, pero no se escribe dos veces igual.

    Un secreto fijo repetido en cada pantalla, comprimido junto con texto que
    viene de fuera y servido por HTTPS, son las tres condiciones de BREACH: se
    mide cuánto encoge la respuesta y el secreto sale letra a letra. El proxy
    comprime el HTML —y tiene que hacerlo: son veinticuatro kilobytes que en la
    cámara son cinco—, así que lo que se quita es la repetición.

    Lo que se comprueba es la propiedad que mata al medidor: de lo escrito no
    se puede sacar ni un trozo del token.
    """
    token = "abcdefghij-KLMNOP_0123456789"
    vistas = {auth.enmascarar(token) for _ in range(200)}
    assert len(vistas) == 200, "dos pantallas escribieron lo mismo"
    for escrito in vistas:
        assert auth.desenmascarar(escrito) == token       # y vuelve el de siempre
        for largo in (4, 6, 8):
            for i in range(len(token) - largo):
                assert token[i:i + largo] not in escrito


def test_the_token_still_works_masked_or_not(tmp_path):
    """Sin mezclar también entra: en la cola de un teléfono puede haber uno viejo."""
    db.init_engine(f"sqlite:///{tmp_path/'t.db'}")
    db.create_all()
    with db.session_scope() as session:
        _, ana = auth.create_restaurant(session, "Casa", "ana@casa.com", "Ana",
                                        "clave-larga-1")
        _, sesion = auth.start_session(session, ana)
        auth.check_csrf(sesion, auth.enmascarar(sesion.csrf))     # el de hoy
        auth.check_csrf(sesion, sesion.csrf)                      # el de la cola vieja
        for malo in ("", None, "inventado", "!!!!", "a", auth.enmascarar("otra-cosa")):
            with pytest.raises(auth.PermissionDenied):
                auth.check_csrf(sesion, malo)


def test_unmasking_rubbish_never_blows_up():
    """Lo que llega de fuera no siempre es un token: no puede reventar la pantalla."""
    for basura in ("", "a", "!!!", "====", "a" * 3, "\x00\x01", "ñ", "AAAA", "_-_-"):
        assert auth.desenmascarar(basura) in (None, "") or isinstance(
            auth.desenmascarar(basura), str)
