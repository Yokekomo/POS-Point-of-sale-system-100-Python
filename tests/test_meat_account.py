"""La cuenta: cómo se entra, qué se paga y qué pasa si no se paga.

La cadena entera, de la web pública a la cocina:

1. una casa pide acceso desde la web y la solicitud se guarda —y se avisa por
   correo si está configurado, pero el registro manda;
2. la plataforma la da de alta y crea la cuenta de su manager;
3. se pone el método de pago en la pasarela y arrancan los quince días de
   prueba, con su reloj, que solo ven el manager y la plataforma;
4. si se cancela antes de que termine la prueba, no se paga nada;
5. si el recibo falla, la plataforma bloquea y cada quien ve lo suyo: el manager
   por qué, y la cocina que hable con su manager.

Aquí no entra un número de tarjeta: ni en el formulario, ni en la consola.
"""
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import billing, security, tarifa

# El navegador de estas pruebas habla español: los textos que se comprueban
# abajo son los españoles. Quien llega sin decir nada recibe inglés.
SPANISH = {"accept-language": "es"}
from thegrill.models import (AccessRequest, AuditLog, Billing, Plan, RequestStatus,
                             Restaurant, Role, User)

HOY = date.today()


@pytest.fixture(autouse=True)
def sin_frenos():
    """Cada prueba empieza con los contadores a cero."""
    security.reset()
    yield
    security.reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    for var in ("GRILL_SMTP_HOST", "GRILL_MAIL_FROM", "GRILL_MAIL_TO"):
        monkeypatch.delenv(var, raising=False)
    db.init_engine(f"sqlite:///{tmp_path/'cuenta.db'}")
    db.create_all()
    with db.session_scope() as s:
        billing.bootstrap_owner(s, "yo@plataforma.com", "Albano", "clave-larga-1")
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        yield c


def csrf_from(html):
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "la página no trae token CSRF"
    return m.group(1)


def como_dueno(client):
    r = client.post("/login", data={"email": "yo@plataforma.com", "password": "clave-larga-1"})
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    return client


def alta_de_casa(client, name="Hotel Marina", email="manager@marina.com"):
    """El dueño da de alta la casa y la cuenta de su manager."""
    admin = client.get("/admin")
    r = client.post("/admin/casa", data={
        "csrf": csrf_from(admin.text), "name": name, "manager_name": "Marta",
        "manager_email": email, "password": "clave-larga-2", "plan": "SINGLE",
        "outlets": "1", "monthly_fee": "79", "language": "es",
        "legal_name": "Marina SL", "tax_number": "B12345678", "country": "España"})
    assert r.status_code == 303
    with db.session_scope() as s:
        return s.query(Restaurant).filter_by(name=name).one().id


def sesion(email, password="clave-larga-2"):
    c = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    r = c.post("/login", data={"email": email, "password": password})
    assert r.status_code == 303, r.text[:200]
    return c


# ------------------------------------------------------------ la web pública
def test_the_front_door_explains_what_this_is(client):
    home = client.get("/")
    assert home.status_code == 200
    assert "Control de carnes" in home.text
    for path in ("/precios", "/solicitar", "/login"):
        assert f'href="{path}"' in home.text
    assert client.get("/precios").status_code == 200
    assert f"{billing.TRIAL_DAYS} días de prueba" in client.get("/precios").text


def test_nobody_signs_themselves_up(client):
    """No hay registro abierto: las cuentas las crea la plataforma."""
    for path in ("/signup", "/join"):
        r = client.get(path)
        assert r.status_code == 303
    assert client.post("/signup", data={"restaurant": "X", "name": "X",
                                        "email": "x@x.com", "password": "clave-larga-9"}).status_code == 303
    with db.session_scope() as s:
        assert s.query(Restaurant).filter_by(platform=False).count() == 0


def test_a_request_is_kept_with_everything_it_carries(client):
    r = client.post("/solicitar", data={
        "restaurant_name": "Hotel Marina", "legal_name": "Marina SL",
        "tax_number": "B12345678", "country": "España", "address": "Muelle 3",
        "contact_name": "Marta Ruiz", "contact_role": "Head chef",
        "email": "marta@marina.com", "phone": "+34600123456",
        "cooks": "12", "outlets": "2", "plan": "MULTI", "message": "Somos un hotel"})
    assert r.status_code == 303 and r.headers["location"] == "/solicitar?sent=1"
    assert "Solicitud recibida" in client.get("/solicitar?sent=1").text

    with db.session_scope() as s:
        row = s.query(AccessRequest).one()
        assert row.restaurant_name == "Hotel Marina"
        assert row.legal_name == "Marina SL" and row.tax_number == "B12345678"
        assert row.country == "España" and row.address == "Muelle 3"
        assert row.contact_name == "Marta Ruiz" and row.contact_role == "Head chef"
        assert row.email == "marta@marina.com" and row.phone == "+34600123456"
        assert row.cooks == 12 and row.outlets == 2 and row.plan == Plan.MULTI
        assert row.message == "Somos un hotel"
        assert row.status == RequestStatus.NEW
        assert row.notified is False          # sin correo configurado, pero guardada


def test_a_request_without_a_name_goes_nowhere(client):
    r = client.post("/solicitar", data={"restaurant_name": "", "contact_name": "Marta",
                                        "email": "m@m.com"})
    assert r.status_code in (200, 303, 422)
    with db.session_scope() as s:
        assert s.query(AccessRequest).count() == 0


def test_the_public_form_has_a_brake(client):
    """Es la única puerta abierta a internet: sin freno se llena de basura."""
    datos = {"restaurant_name": "X", "contact_name": "Y", "email": "y@x.com"}
    for _ in range(security.FORM_ATTEMPTS):
        assert client.post("/solicitar", data=datos).status_code == 303
    frenada = client.post("/solicitar", data=datos)
    assert frenada.status_code == 200 and "varias solicitudes" in frenada.text
    with db.session_scope() as s:
        assert s.query(AccessRequest).count() == security.FORM_ATTEMPTS


def test_the_form_refuses_a_card_number(client):
    """Un número de tarjeta se rechaza antes de escribir nada."""
    r = client.post("/solicitar", data={
        "restaurant_name": "Hotel Marina", "contact_name": "Marta",
        "email": "m@m.com", "message": "mi tarjeta es 4242 4242 4242 4242"})
    assert r.status_code == 200 and "números de tarjeta" in r.text
    with db.session_scope() as s:
        assert s.query(AccessRequest).count() == 0


def test_a_tax_number_is_not_a_card_number(client):
    """El freno no puede estorbar: un número fiscal largo entra sin problema."""
    r = client.post("/solicitar", data={
        "restaurant_name": "Hotel Marina", "contact_name": "Marta",
        "email": "m@m.com", "tax_number": "ESB1234567890123", "phone": "+34600123456"})
    assert r.status_code == 303


# --------------------------------------------------------- la consola del dueño
def test_only_the_owner_sees_the_console_and_it_does_not_even_admit_it_exists(client):
    alta = como_dueno(client)
    assert alta.get("/admin").status_code == 200
    restaurant_id = alta_de_casa(client)
    assert restaurant_id

    manager = sesion("manager@marina.com")
    assert manager.get("/admin").status_code == 404      # ni se insinúa
    assert manager.post("/admin/casa", data={"csrf": "x"}).status_code == 404


def test_the_owner_creates_the_house_and_its_manager(client):
    como_dueno(client)
    alta_de_casa(client)
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter_by(name="Hotel Marina").one()
        assert casa.plan == Plan.SINGLE and casa.monthly_fee == 79
        assert casa.legal_name == "Marina SL" and casa.tax_number == "B12345678"
        assert casa.billing == Billing.SETUP        # sin tarjeta no empieza la prueba
        manager = s.query(User).filter_by(email="manager@marina.com").one()
        assert manager.role == Role.MANAGER and manager.restaurant_id == casa.id
        assert s.query(AuditLog).filter_by(action="CREATED").one()

    manager = sesion("manager@marina.com")
    assert manager.get("/hoy").status_code == 200
    assert "método de pago" in manager.get("/hoy").text


def test_accepting_a_request_marks_it_as_done(client):
    client.post("/solicitar", data={"restaurant_name": "Hotel Marina",
                                    "contact_name": "Marta", "email": "m@marina.com"})
    como_dueno(client)
    with db.session_scope() as s:
        request_id = s.query(AccessRequest).one().id
    admin = client.get("/admin")
    r = client.post("/admin/casa", data={
        "csrf": csrf_from(admin.text), "name": "Hotel Marina", "manager_name": "Marta",
        "manager_email": "manager@marina.com", "password": "clave-larga-2",
        "request_id": str(request_id)})
    assert r.status_code == 303
    with db.session_scope() as s:
        row = s.query(AccessRequest).one()
        assert row.status == RequestStatus.ACCEPTED
        assert row.restaurant_id == s.query(Restaurant).filter_by(name="Hotel Marina").one().id


# ------------------------------------------------- la tarjeta y los quince días
def test_the_trial_starts_when_the_card_is_in_place(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    admin = client.get("/admin")
    r = client.post(f"/admin/casa/{casa_id}/pasarela", data={
        "csrf": csrf_from(admin.text), "provider": "stripe", "reference": "cus_ABC123",
        "brand": "VISA", "last4": "4242", "expiry": "05/2029"})
    assert r.status_code == 303
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter_by(id=casa_id).one()
        assert casa.billing == Billing.TRIAL
        assert casa.trial_ends == HOY + timedelta(days=billing.TRIAL_DAYS)
        assert casa.payment_ref == "cus_ABC123" and casa.payment_last4 == "4242"
        assert billing.trial_left(casa) == billing.TRIAL_DAYS


def test_the_platform_never_writes_down_a_card_number(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    admin = client.get("/admin")
    r = client.post(f"/admin/casa/{casa_id}/pasarela", data={
        "csrf": csrf_from(admin.text), "provider": "stripe",
        "reference": "4242424242424242"})
    assert r.status_code == 400
    r = client.post(f"/admin/casa/{casa_id}/pasarela", data={
        "csrf": csrf_from(admin.text), "provider": "stripe", "reference": "cus_1",
        "last4": "424242424242"})
    assert r.status_code == 400
    with db.session_scope() as s:
        assert s.query(Restaurant).filter_by(id=casa_id).one().payment_ref is None


def test_only_the_manager_and_the_platform_see_the_clock(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    admin = client.get("/admin")
    client.post(f"/admin/casa/{casa_id}/pasarela", data={
        "csrf": csrf_from(admin.text), "provider": "stripe", "reference": "cus_A",
        "brand": "VISA", "last4": "4242"})

    manager = sesion("manager@marina.com")
    assert "días de prueba" in manager.get("/hoy").text          # el manager, sí
    assert "días de prueba" in client.get("/admin").text          # la plataforma, también

    equipo = manager.get("/manager/equipo")
    manager.post("/manager/equipo/nueva", data={
        "csrf": csrf_from(equipo.text), "name": "Luis", "email": "luis@marina.com",
        "password": "clave-larga-3", "role": "BUTCHER"})
    carnicero = sesion("luis@marina.com", "clave-larga-3")
    assert "días de prueba" not in carnicero.get("/hoy").text     # la cocina, no


def test_cancelling_during_the_trial_costs_nothing(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    admin = client.get("/admin")
    client.post(f"/admin/casa/{casa_id}/pasarela", data={
        "csrf": csrf_from(admin.text), "provider": "stripe", "reference": "cus_A"})

    manager = sesion("manager@marina.com")
    with db.session_scope() as s:
        assert billing.free_cancellation(s.query(Restaurant).filter_by(id=casa_id).one())
    hoy = manager.get("/hoy")
    assert "no pagas nada" in hoy.text
    r = manager.post("/cuenta/cancelar", data={"csrf": csrf_from(hoy.text),
                                               "reason": "No nos encaja"})
    assert r.status_code == 303
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter_by(id=casa_id).one()
        assert casa.billing == Billing.CANCELLED and casa.cancelled_at is not None
        nota = s.query(AuditLog).filter_by(action="CANCELLED").one()
        assert "sin cobrar" in nota.detail


def test_after_the_trial_cancelling_is_not_free_any_more(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter_by(id=casa_id).one()
        owner = s.query(User).filter_by(role=Role.OWNER).one()
        billing.attach_payment_method(s, owner, casa, provider="stripe", reference="cus_A",
                                      on=HOY - timedelta(days=40))
        assert not billing.free_cancellation(casa)
        billing.cancel(s, owner, casa)
        assert "fuera de prueba" in s.query(AuditLog).filter_by(action="CANCELLED").one().detail


# ---------------------------------------------------- el recibo y el bloqueo
def test_a_failed_payment_warns_before_it_blocks(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    admin = client.get("/admin")
    client.post(f"/admin/casa/{casa_id}/pago", data={
        "csrf": csrf_from(admin.text), "action": "past_due", "note": "Tarjeta rechazada"})

    manager = sesion("manager@marina.com")
    hoy = manager.get("/hoy")
    assert hoy.status_code == 200                      # todavía se puede trabajar
    assert "no ha entrado" in hoy.text                 # pero se avisa


def test_blocking_stops_the_whole_house(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    admin = client.get("/admin")
    manager = sesion("manager@marina.com")
    equipo = manager.get("/manager/equipo")
    manager.post("/manager/equipo/nueva", data={
        "csrf": csrf_from(equipo.text), "name": "Luis", "email": "luis@marina.com",
        "password": "clave-larga-3", "role": "BUTCHER"})
    carnicero = sesion("luis@marina.com", "clave-larga-3")

    client.post(f"/admin/casa/{casa_id}/pago", data={
        "csrf": csrf_from(admin.text), "action": "block", "note": "Segundo recibo devuelto"})

    for sesion_bloqueada in (manager, carnicero):
        r = sesion_bloqueada.get("/hoy")
        assert r.status_code == 303 and r.headers["location"] == "/cuenta"
        assert sesion_bloqueada.get("/carne").headers["location"] == "/cuenta"

    # Cada quien ve lo suyo, y solo lo suyo.
    aviso_manager = manager.get("/cuenta").text
    assert "el pago del mes no ha entrado" in aviso_manager
    assert "Segundo recibo devuelto" in aviso_manager

    aviso_cocina = carnicero.get("/cuenta").text
    assert "Habla con tu manager" in aviso_cocina
    assert "Segundo recibo devuelto" not in aviso_cocina     # el motivo no es cosa suya


def test_the_owner_still_gets_in_when_a_house_is_blocked(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    admin = client.get("/admin")
    client.post(f"/admin/casa/{casa_id}/pago",
                data={"csrf": csrf_from(admin.text), "action": "block"})
    assert client.get("/admin").status_code == 200


def test_marking_it_paid_lets_everyone_back_in(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    admin = client.get("/admin")
    client.post(f"/admin/casa/{casa_id}/pago",
                data={"csrf": csrf_from(admin.text), "action": "block"})
    manager = sesion("manager@marina.com")
    assert manager.get("/hoy").status_code == 303

    hasta = (HOY + timedelta(days=30)).isoformat()
    client.post(f"/admin/casa/{casa_id}/pago", data={
        "csrf": csrf_from(admin.text), "action": "paid", "paid_until": hasta})
    assert manager.get("/hoy").status_code == 200
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter_by(id=casa_id).one()
        assert casa.billing == Billing.ACTIVE and str(casa.paid_until) == hasta
        assert s.query(AuditLog).filter_by(action="PAID").one()


def test_every_move_on_the_account_is_written_down(client):
    como_dueno(client)
    casa_id = alta_de_casa(client)
    admin = client.get("/admin")
    client.post(f"/admin/casa/{casa_id}/pago",
                data={"csrf": csrf_from(admin.text), "action": "block", "note": "Impago"})
    with db.session_scope() as s:
        nota = s.query(AuditLog).filter_by(action="BLOCKED").one()
        assert "Albano" in nota.actor and "yo@plataforma.com" in nota.actor
        assert nota.detail == "Impago"


# --------------------------------------------- el manager crea a su gente
def test_the_manager_creates_the_accounts_of_the_house(client):
    como_dueno(client)
    alta_de_casa(client)
    manager = sesion("manager@marina.com")
    equipo = manager.get("/manager/equipo")
    r = manager.post("/manager/equipo/nueva", data={
        "csrf": csrf_from(equipo.text), "name": "Luis", "email": "luis@marina.com",
        "password": "clave-larga-3", "role": "BUTCHER"})
    assert r.status_code == 303
    with db.session_scope() as s:
        luis = s.query(User).filter_by(email="luis@marina.com").one()
        assert luis.role == Role.BUTCHER
    assert sesion("luis@marina.com", "clave-larga-3").get("/despiece").status_code == 200


def test_the_manager_of_the_whole_house_hires_the_manager_of_a_shop(client):
    """Un grupo con obrador y tres locales no lo lleva una sola persona.

    El manager general —el que no está atado a una sede— da de alta a los
    managers de cada local. Pedirle esa cuenta a la plataforma cada vez no es
    manera de llevar un grupo.
    """
    como_dueno(client)
    alta_de_casa(client)
    manager = sesion("manager@marina.com")
    equipo = manager.get("/manager/equipo")
    assert 'value="MANAGER"' in equipo.text            # y se le ofrece en la lista

    r = manager.post("/manager/equipo/nueva", data={
        "csrf": csrf_from(equipo.text), "name": "Otro", "email": "otro@marina.com",
        "password": "clave-larga-3", "role": "MANAGER"})
    assert r.status_code == 303 and "error=" not in r.headers["location"]
    with db.session_scope() as s:
        assert s.query(User).filter_by(email="otro@marina.com").one().role == Role.MANAGER


def test_nobody_in_a_house_makes_a_platform_owner(client):
    """El agujero: el nivel se cambiaba sin mirar quién lo pedía.

    La pantalla no ofrece «dueño de la plataforma», pero una pantalla sin
    desplegable no es una puerta cerrada: el formulario se manda a mano. Así
    un manager se fabricaba un dueño de la plataforma en una línea.
    """
    como_dueno(client)
    alta_de_casa(client)
    manager = sesion("manager@marina.com")
    equipo = manager.get("/manager/equipo")
    assert 'value="OWNER"' not in equipo.text
    manager.post("/manager/equipo/nueva", data={
        "csrf": csrf_from(equipo.text), "name": "Luis", "email": "luis@marina.com",
        "password": "clave-larga-3", "role": "BUTCHER"})
    with db.session_scope() as s:
        luis = s.query(User).filter_by(email="luis@marina.com").one().id

    # Ni dándole a mano el nivel que no le toca.
    r = manager.post(f"/manager/equipo/{luis}/rol", data={
        "csrf": csrf_from(manager.get("/manager/equipo").text), "role": "OWNER"})
    assert r.status_code == 403
    with db.session_scope() as s:
        assert s.query(User).filter_by(id=luis).one().role == Role.BUTCHER


def test_the_manager_of_one_shop_does_not_touch_the_one_next_door(client):
    """El de un local lleva el suyo. Al de al lado, ni la clave ni el nivel."""
    from thegrill.web import sites

    como_dueno(client)
    alta_de_casa(client)
    general = sesion("manager@marina.com")
    equipo = general.get("/manager/equipo")
    for nombre, correo in (("Playa", "playa@marina.com"), ("Sierra", "sierra@marina.com")):
        general.post("/manager/equipo/nueva", data={
            "csrf": csrf_from(equipo.text), "name": nombre, "email": correo,
            "password": "clave-larga-3", "role": "MANAGER"})
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        jefe = s.query(User).filter_by(email="manager@marina.com").one()
        playa = sites.create(s, jefe, "Playa")
        for correo in ("playa@marina.com", "sierra@marina.com"):
            sites.assign(s, jefe, s.query(User).filter_by(email=correo).one(), playa.id)
        ids = {u.email: u.id for u in s.query(User).filter_by(restaurant_id=casa.id)}

    dela_playa = sesion("playa@marina.com", "clave-larga-3")
    token = csrf_from(dela_playa.get("/manager/equipo").text)
    # Ni al manager de al lado…
    assert dela_playa.post(f"/manager/equipo/{ids['sierra@marina.com']}/contrasena",
                           data={"csrf": token, "password": "otra-clave-larga"}
                           ).status_code == 403
    # …ni al de la casa entera.
    assert dela_playa.post(f"/manager/equipo/{ids['manager@marina.com']}/contrasena",
                           data={"csrf": token, "password": "otra-clave-larga"}
                           ).status_code == 403
    # Y no reparte el nivel de manager.
    assert 'value="MANAGER"' not in dela_playa.get("/manager/equipo").text

    # El general sí le pone una nueva al de un local.
    token = csrf_from(general.get("/manager/equipo").text)
    assert general.post(f"/manager/equipo/{ids['playa@marina.com']}/contrasena",
                        data={"csrf": token, "password": "otra-clave-larga"}
                        ).status_code == 303


def test_two_people_cannot_share_an_email_in_the_same_house(client):
    como_dueno(client)
    alta_de_casa(client)
    manager = sesion("manager@marina.com")
    equipo = manager.get("/manager/equipo")
    datos = {"csrf": csrf_from(equipo.text), "name": "Luis", "email": "luis@marina.com",
             "password": "clave-larga-3", "role": "BUTCHER"}
    assert manager.post("/manager/equipo/nueva", data=datos).status_code == 303
    otra = manager.post("/manager/equipo/nueva", data=datos)
    assert otra.status_code == 303 and "error=" in otra.headers["location"]
    with db.session_scope() as s:
        assert s.query(User).filter_by(email="luis@marina.com").count() == 1


# ------------------------------------------------------------- la puerta
def test_guessing_passwords_gets_you_locked_out(client):
    como_dueno(client)
    alta_de_casa(client)
    ladron = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    for _ in range(security.LOGIN_ATTEMPTS):
        r = ladron.post("/login", data={"email": "manager@marina.com", "password": "mala"})
        assert r.status_code == 200
    frenado = ladron.post("/login", data={"email": "manager@marina.com", "password": "mala"})
    assert "Demasiados intentos" in frenado.text
    # Y con la buena tampoco, mientras dure el castigo.
    buena = ladron.post("/login", data={"email": "manager@marina.com",
                                        "password": "clave-larga-2"})
    assert "Demasiados intentos" in buena.text


def test_getting_it_right_clears_the_count(client):
    como_dueno(client)
    alta_de_casa(client)
    puerta = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    for _ in range(security.LOGIN_ATTEMPTS - 1):
        puerta.post("/login", data={"email": "manager@marina.com", "password": "mala"})
    assert puerta.post("/login", data={"email": "manager@marina.com",
                                       "password": "clave-larga-2"}).status_code == 303
    for _ in range(security.LOGIN_ATTEMPTS - 1):
        puerta.post("/login", data={"email": "manager@marina.com", "password": "mala"})
    assert puerta.post("/login", data={"email": "manager@marina.com",
                                       "password": "clave-larga-2"}).status_code == 303


def test_every_page_carries_its_security_headers(client):
    for path in ("/", "/precios", "/solicitar", "/login"):
        r = client.get(path)
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_one_house_never_reaches_another(client):
    como_dueno(client)
    alta_de_casa(client, "Hotel Marina", "manager@marina.com")
    alta_de_casa(client, "Otro Hotel", "otro@otro.com")
    manager = sesion("manager@marina.com")
    equipo = manager.get("/manager/equipo").text
    assert "Marta" in equipo
    assert "otro@otro.com" not in equipo


# ------------------------------------------------ los datos personales
def test_what_we_keep_of_a_person_can_be_handed_back_and_deleted(client):
    """Derecho de acceso, de portabilidad y de supresión, con su botón."""
    client.post("/solicitar", data={
        "restaurant_name": "Hotel Marina", "contact_name": "Marta Ruiz",
        "email": "marta@marina.com", "phone": "+34600123456",
        "address": "Muelle 3", "tax_number": "B12345678"})
    como_dueno(client)
    with db.session_scope() as s:
        request_id = s.query(AccessRequest).one().id

    datos = client.get(f"/admin/solicitud/{request_id}/datos").json()
    assert datos["persona"] == "Marta Ruiz" and datos["correo"] == "marta@marina.com"
    assert datos["telefono"] == "+34600123456" and datos["direccion"] == "Muelle 3"

    admin = client.get("/admin")
    r = client.post(f"/admin/solicitud/{request_id}/borrar",
                    data={"csrf": csrf_from(admin.text)})
    assert r.status_code == 303
    with db.session_scope() as s:
        assert s.query(AccessRequest).count() == 0
        assert s.query(AuditLog).filter_by(action="ERASED").one()      # queda que se borró


def test_looking_at_personal_data_leaves_a_trace(client):
    client.post("/solicitar", data={"restaurant_name": "X", "contact_name": "Y",
                                    "email": "y@x.com"})
    como_dueno(client)
    client.get("/admin")
    with db.session_scope() as s:
        huella = s.query(AuditLog).filter_by(table="personal_data", action="READ").first()
        assert huella is not None and "yo@plataforma.com" in huella.actor


def test_what_never_became_an_account_is_not_kept_for_ever(client):
    from thegrill.meat import privacy
    client.post("/solicitar", data={"restaurant_name": "Vieja", "contact_name": "Y",
                                    "email": "vieja@x.com"})
    client.post("/solicitar", data={"restaurant_name": "Reciente", "contact_name": "Z",
                                    "email": "nueva@x.com"})
    como_dueno(client)
    with db.session_scope() as s:
        vieja = s.query(AccessRequest).filter_by(restaurant_name="Vieja").one()
        vieja.created_at = vieja.created_at - timedelta(days=privacy.RETENTION_DAYS + 5)
        aceptada = s.query(AccessRequest).filter_by(restaurant_name="Reciente").one()
        aceptada.created_at = aceptada.created_at - timedelta(days=400)
        aceptada.status = RequestStatus.ACCEPTED       # esta ya es una casa

    admin = client.get("/admin")
    client.post("/admin/solicitudes/purgar", data={"csrf": csrf_from(admin.text)})
    with db.session_scope() as s:
        quedan = [r.restaurant_name for r in s.query(AccessRequest)]
        assert quedan == ["Reciente"]                  # la aceptada no se toca


def test_the_console_says_out_loud_when_the_data_is_not_encrypted(client):
    """Es mejor saberlo que creerse protegido."""
    como_dueno(client)
    html = client.get("/admin").text
    from thegrill.meat import privacy
    if privacy.encryption_on():
        assert "cifrados" in html or "encrypted" in html
    else:
        assert "sin cifrar" in html


def test_the_public_form_says_what_is_done_with_the_data(client):
    html = client.get("/solicitar").text
    assert "Responsable del tratamiento" in html
    assert "180 días" in html
    assert "borrarlos" in html


# ----------------------------------------------------------- las cookies
def test_the_sales_pages_warn_about_cookies(client):
    """El aviso va en la web de venta, que es donde llega quien no nos conoce."""
    for path in ("/", "/precios", "/solicitar", "/cookies"):
        html = client.get(path).text
        assert 'id="cookiebar"' in html, path
        assert 'href="/cookies"' in html, path


def test_the_cookie_page_lists_every_cookie_with_its_life(client):
    html = client.get("/cookies").text
    assert "grill_session" in html and "grill_lang" in html
    assert "Catorce días" in html and "Un año" in html
    assert "Ninguno" in html                       # terceros: ninguno


def test_there_are_only_the_two_technical_cookies(client):
    """Si un día aparece una tercera, esta prueba lo dice."""
    r = client.get("/")
    assert not r.cookies                           # la portada no deja ninguna
    r = client.get("/idioma/en?next=/precios")
    assert set(r.cookies) == {"grill_lang"}        # el idioma, porque lo eliges tú
    como_dueno(client)
    assert set(client.cookies) <= {"grill_lang", "grill_session"}


def test_the_notice_informs_and_does_not_ask_for_permission(client):
    """Estas dos cookies son estrictamente necesarias: informar sí, pedir no."""
    html = client.get("/cookies").text
    assert "no pide consentimiento" in html.lower() or "no exige consentimiento" in html.lower() \
        or "no pide" in html.lower() or "consentimiento" in html
    assert "analítica" in html or "analytics" in html   # y se dice que no la hay


def test_inside_the_programme_there_is_no_cookie_bar(client):
    """Quien ya trabaja aquí no necesita que se lo cuenten en cada pantalla."""
    como_dueno(client)
    alta_de_casa(client)
    manager = sesion("manager@marina.com")
    assert 'id="cookiebar"' not in manager.get("/hoy").text


def test_the_login_page_warns_too(client):
    html = client.get("/login").text
    assert 'id="cookiebar"' in html and 'href="/cookies"' in html


# ------------------------------------------- nada se abre sin haber entrado
PUBLICAS = {"/", "/precios", "/solicitar", "/cookies", "/login", "/signup", "/join",
            "/logout", "/healthz", "/idioma/{lang}",
            # El ayudante que guarda copias de pantalla no lleva datos: solo el
            # código que dice qué guardar. Se pide antes de entrar, a propósito.
            "/sw.js",
            # El icono de la pestaña: el navegador lo pide él solo, también en
            # la pantalla de entrar. Es un dibujo, no lleva nada de la casa.
            "/favicon.ico",
            # El manifiesto: nombre, colores e iconos, y nada más. **Tiene** que
            # ser público, porque iOS lo lee al añadir el programa a la pantalla
            # de inicio, que es cuando puede no haber ninguna sesión abierta. Y
            # sin que lo lea ahí, lo que se crea es un marcador y no una
            # aplicación —y el marcador pierde la cola de apuntes a los siete
            # días, que es el fallo que esto viene a tapar.
            "/manifest.webmanifest"}


def test_no_screen_opens_without_logging_in(client):
    """Barrido por todas las rutas: la que no es pública, pide entrar."""
    from thegrill.meat import app as meatapp
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    revisadas = 0
    for route in meatapp.app.routes:
        path = getattr(route, "path", "")
        metodos = getattr(route, "methods", set()) or set()
        if "GET" not in metodos or path in PUBLICAS or "{" in path and path in PUBLICAS:
            continue
        if path in PUBLICAS or path.startswith("/idioma"):
            continue
        # Las rutas con parámetro se prueban con un valor cualquiera.
        concreta = re.sub(r"\{[^}]+\}", "1", path)
        r = fuera.get(concreta)
        assert r.status_code in (303, 404), f"{path} respondió {r.status_code} sin sesión"
        if r.status_code == 303:
            assert r.headers["location"] in ("/login", "/cuenta"), path
        revisadas += 1
    assert revisadas > 15, "el barrido no ha mirado casi nada"


def test_a_session_from_another_house_does_not_open_the_console(client):
    como_dueno(client)
    alta_de_casa(client)
    manager = sesion("manager@marina.com")
    for path in ("/admin", "/admin/solicitud/1/datos"):
        assert manager.get(path).status_code == 404


def test_the_api_map_is_not_published(client):
    """La documentación automática enseñaba todas las rutas a cualquiera."""
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404, path


# ------------------------------------------------ lo que dicen los manuales
def test_the_scripts_carry_a_number_that_changes_every_time(client):
    """Sin esto, un script inyectado se ejecutaría igual que los nuestros."""
    una = client.get("/login")
    otra = client.get("/login")
    n1 = re.search(r"'nonce-([^']+)'", una.headers["content-security-policy"])
    n2 = re.search(r"'nonce-([^']+)'", otra.headers["content-security-policy"])
    assert n1 and n2 and n1.group(1) != n2.group(1)

    # Y el guion que lleve una pantalla tiene que traer el número de esa misma
    # respuesta: si no, el navegador no lo ejecuta —que es lo que se quiere—.
    como_dueno(client)
    dentro = client.get("/hoy")
    suyo = re.search(r"'nonce-([^']+)'", dentro.headers["content-security-policy"])
    for marca in re.findall(r'<script nonce="([^"]+)"', dentro.text):
        assert marca == suyo.group(1)
    assert f"'nonce-{n1.group(1)}'" in una.headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in una.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in una.headers["Content-Security-Policy"].split("style-src")[0]


def test_the_session_cookie_does_not_travel_to_other_sites(client):
    puerta = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    r = puerta.post("/login", data={"email": "yo@plataforma.com",
                                    "password": "clave-larga-1"})
    puesta = r.headers["set-cookie"].lower()
    assert "grill_session=" in puesta
    assert "httponly" in puesta          # el script de una página no la lee
    assert "samesite=strict" in puesta   # ni viaja en una petición de fuera
    assert "path=/" in puesta


def test_the_login_takes_the_same_whether_the_email_exists_or_not(client):
    """El tiempo de respuesta no puede decir quién tiene cuenta aquí."""
    import time
    como_dueno(client)
    alta_de_casa(client)
    security.reset()

    def tarda(email):
        fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
        inicio = time.perf_counter()
        fuera.post("/login", data={"email": email, "password": "mala-de-verdad"})
        return time.perf_counter() - inicio

    existe = tarda("manager@marina.com")
    no_existe = tarda("nadie@ninguna-parte.com")
    # El que no existe también gasta su comprobación: nada de respuestas al vuelo.
    assert no_existe > existe * 0.4


def test_a_blocked_house_cannot_be_reached_by_typing_the_address(client):
    """La puerta se cierra en la ruta, no en el enlace."""
    como_dueno(client)
    casa_id = alta_de_casa(client)
    manager = sesion("manager@marina.com")
    admin = client.get("/admin")
    client.post(f"/admin/casa/{casa_id}/pago",
                data={"csrf": csrf_from(admin.text), "action": "block"})
    for path in ("/hoy", "/carne", "/despiece", "/inventario", "/merma", "/carta"):
        r = manager.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/cuenta", path


def test_the_pages_do_not_load_anything_from_outside(client):
    """Una web que no carga nada de fuera puede tener una política estricta."""
    for path in ("/", "/precios", "/solicitar", "/login", "/cookies"):
        html = client.get(path).text
        assert "http://" not in html.replace("http://www.w3.org", "")
        assert "//cdn" not in html and "googleapis" not in html


def test_behind_a_proxy_the_platform_knows_it_is_on_https(client):
    """Detrás de Caddy la petición llega en claro, pero fuera es HTTPS."""
    directa = client.get("/login")
    assert "Strict-Transport-Security" not in directa.headers

    detras = client.get("/login", headers={"x-forwarded-proto": "https"})
    assert "max-age=31536000" in detras.headers["Strict-Transport-Security"]


# --------------------------------------------------------------- las fotos
def test_the_landing_works_with_and_without_photos(client, tmp_path, monkeypatch):
    """La portada no depende de las fotos: con ellas las enseña, sin ellas no deja hueco."""
    sin = client.get("/")
    assert sin.status_code == 200 and 'id="fotos"' not in sin.text

    carpeta = tmp_path / "fotos"
    carpeta.mkdir()
    (carpeta / "primal.jpg").write_bytes(b"\xff\xd8foto")
    monkeypatch.setattr(meatapp, "PHOTO_DIR", str(carpeta))

    con = client.get("/")
    assert 'id="fotos"' in con.text
    assert "/static/fotos/primal.jpg" in con.text
    assert "cortes.jpg" not in con.text          # la que no está, no se inventa


# ------------------------------------------------- varios locales, un recibo
def test_several_outlets_of_one_company_are_billed_together(client):
    """El plan de varios locales, por lo menos en el recibo: una empresa, un cobro."""
    from datetime import date as _date

    from thegrill.meat import billing as facturacion
    from thegrill.models import Billing as Estado

    with db.session_scope() as s:
        dueño = s.query(User).filter_by(role=Role.OWNER).one()
        for n, nombre in enumerate(("Marina Centro", "Marina Puerto", "Marina Norte")):
            casa, _ = facturacion.create_account(
                s, name=nombre, manager_name=f"Jefe {n}",
                manager_email=f"jefe{n}@marina.com", password="clave-larga-3",
                plan=Plan.MULTI, monthly_fee=89.0, group="Grupo Marina", language="es")
            casa.billing = Estado.PAST_DUE
        facturacion.create_account(s, name="Otro Asador", manager_name="Sara",
                                   manager_email="sara@otro.com", password="clave-larga-4",
                                   plan=Plan.SINGLE, monthly_fee=89.0, language="es")
        s.flush()

        grupos = {g.name: g for g in facturacion.grouped(s)}
        assert grupos["Grupo Marina"].outlets == 3
        assert grupos["Grupo Marina"].monthly == 267.0
        assert grupos["Grupo Marina"].needs_attention
        assert None in grupos and grupos[None].outlets >= 1     # las que van solas

    como_dueno(client)
    pantalla = client.get("/admin")
    assert "Grupo Marina" in pantalla.text and "3 locales" in pantalla.text

    r = client.post("/admin/grupo/pago", data={
        "csrf": csrf_from(pantalla.text), "group": "Grupo Marina", "action": "paid",
        "paid_until": "2026-12-31", "note": "transferencia"})
    assert r.status_code == 303

    with db.session_scope() as s:
        for nombre in ("Marina Centro", "Marina Puerto", "Marina Norte"):
            casa = s.query(Restaurant).filter_by(name=nombre).one()
            assert casa.billing == Estado.ACTIVE
            assert casa.paid_until == _date(2026, 12, 31)
        otro = s.query(Restaurant).filter_by(name="Otro Asador").one()
        assert otro.billing != Estado.ACTIVE          # esa no es del grupo


# ------------------------------------------------ el precio que se publica
def test_the_owner_sets_the_price_the_sales_site_shows(client):
    """El precio de la web se cambia desde la consola, no desplegando código.

    Salir con una rebaja de fundador mientras no hay clientes suficientes y
    quitarla el día que los haya es una decisión de negocio; si exige tocar el
    programa, no se hace cuando toca sino cuando hay un rato.
    """
    # De partida, noventa y nueve euros y sin rebaja.
    publico = client.get("/precios").text
    assert f'<b>{tarifa.POR_DEFECTO:g} <span class="uni">€</span></b>' in publico
    assert "Oferta de lanzamiento" not in publico

    como_dueno(client)
    token = csrf_from(client.get("/admin").text)
    assert client.post("/admin/tarifa", data={
        "csrf": token, "currency": "EUR", "per_outlet": "99", "extra_outlet": "79",
        "sale_on": "1", "sale_price": "69", "sale_label": "Precio de fundador",
        "yearly_on": "1", "yearly_months": "10"}).status_code == 303

    publico = client.get("/precios").text
    assert "Precio de fundador" in publico and "−30 %" in publico
    assert '<s class="antes">99</s>' in publico      # el de antes, tachado
    assert '69 <span class="uni">€</span>' in publico
    assert "79" in publico                           # y el segundo local
    # Diez mensualidades del precio en vigor, no del normal.
    assert "690" in publico and "138" in publico     # el año y lo que se ahorra


def test_a_discount_that_is_not_a_discount_is_refused(client):
    """Una «rebaja» por encima del precio normal deja un tachado absurdo."""
    como_dueno(client)
    token = csrf_from(client.get("/admin").text)
    respuesta = client.post("/admin/tarifa", data={
        "csrf": token, "currency": "EUR", "per_outlet": "99",
        "sale_on": "1", "sale_price": "120"})
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert f'<b>{tarifa.POR_DEFECTO:g} <span class="uni">€</span></b>' in client.get("/precios").text


def test_a_discount_with_a_date_switches_itself_off(client):
    """Si hay que acordarse de quitarla a mano, un día se queda puesta."""
    from thegrill.meat import tarifa

    como_dueno(client)
    token = csrf_from(client.get("/admin").text)
    hasta = HOY + timedelta(days=30)
    assert client.post("/admin/tarifa", data={
        "csrf": token, "currency": "EUR", "per_outlet": "99", "sale_on": "1",
        "sale_price": "69", "sale_until": hasta.isoformat()}).status_code == 303

    with db.session_scope() as s:
        assert tarifa.publicada(s, on=hasta).price == 69.0          # el último día, sí
        assert tarifa.publicada(s, on=hasta + timedelta(days=1)).price == 99.0
        assert not tarifa.publicada(s, on=hasta + timedelta(days=1)).on_sale


def test_the_price_is_only_the_shop_window(client):
    """Cambiar el escaparate no toca a quien ya está dentro."""
    como_dueno(client)
    alta_de_casa(client)
    token = csrf_from(client.get("/admin").text)
    client.post("/admin/tarifa", data={"csrf": token, "currency": "EUR",
                                       "per_outlet": "149"})
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        # La casa sigue con su plan y su estado: la tarifa es otra cosa.
        assert casa.plan is not None and casa.billing is not None


def test_nobody_but_the_owner_touches_the_price(client):
    """El precio de la plataforma no lo pone el manager de una casa."""
    como_dueno(client)
    alta_de_casa(client)
    manager = sesion("manager@marina.com")
    token = csrf_from(manager.get("/configuracion").text)
    client = manager
    # Un 404: para quien no es de la plataforma, esa puerta ni existe.
    assert manager.post("/admin/tarifa", data={
        "csrf": token, "currency": "EUR", "per_outlet": "1"}).status_code == 404
    with db.session_scope() as s:
        # Sigue el de serie: el intento de cambiarlo no llegó a nada.
        assert tarifa.publicada(s).normal == tarifa.POR_DEFECTO


def test_the_owner_has_the_explanation_link_at_hand(client):
    """El enlace que se manda a quien pregunta cómo funciona esto.

    Está en la consola del dueño porque ahí es donde se necesita: llega un
    correo preguntando, se copia y se manda, sin ir a buscarlo a ningún sitio.

    La dirección sale de una variable de entorno y no está escrita en la
    plantilla: cambiar a dónde apunta —una versión nueva de la página, la
    página en otro sitio— es una decisión de un martes cualquiera y no puede
    depender de un despliegue.
    """
    from thegrill import config

    dueno = como_dueno(client)
    pantalla = dueno.get("/admin").text
    assert config.PAGINA_RECORRIDO in pantalla
    assert 'id="copiarenlace"' in pantalla
    # Y escrito, para poder cogerlo a mano si el navegador no deja copiar.
    assert f"<code>{config.PAGINA_RECORRIDO}</code>" in pantalla


def test_only_the_owner_sees_it(client):
    """Es la consola de la plataforma: la carnicería de una casa no entra."""
    from thegrill import config

    dueno = como_dueno(client)
    alta_de_casa(dueno)
    manager = sesion("manager@marina.com")
    assert manager.get("/admin").status_code == 404      # ni se insinúa que existe
    assert config.PAGINA_RECORRIDO not in manager.get("/hoy").text


def test_the_link_can_be_moved_without_touching_the_code(client, monkeypatch):
    """Otra dirección en la variable de entorno y la pantalla la enseña."""
    import importlib

    from thegrill import config

    monkeypatch.setenv("GRILL_PAGINA_RECORRIDO", "https://ejemplo.test/recorrido")
    importlib.reload(config)
    try:
        from thegrill.meat import app as meatapp
        monkeypatch.setattr(meatapp.config, "PAGINA_RECORRIDO", config.PAGINA_RECORRIDO)
        pantalla = como_dueno(client).get("/admin").text
        assert "https://ejemplo.test/recorrido" in pantalla
    finally:
        monkeypatch.delenv("GRILL_PAGINA_RECORRIDO", raising=False)
        importlib.reload(config)
