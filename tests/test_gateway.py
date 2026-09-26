"""Lo que cuenta la pasarela de pago, y qué se hace con ello.

El cobro lo hace la pasarela; lo que hace falta aquí es enterarse, porque de
eso depende si la casa entra mañana. Lo que se comprueba: que sin firma buena
no se toca nada, que un evento repetido no se aplica dos veces, y que cada
tipo de evento deja la cuenta como tiene que quedar.
"""
import json
import time
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import billing, gateway
from thegrill.models import (AuditLog, Billing, GatewayEvent, Plan, Restaurant,
                             Role, User)

SECRETO = "whsec_prueba"
SPANISH = {"accept-language": "es"}


@pytest.fixture
def casa(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRETO)
    db.init_engine(f"sqlite:///{tmp_path/'pago.db'}")
    db.create_all()
    with db.session_scope() as s:
        billing.bootstrap_owner(s, "albano@grill.com", "Albano", "clave-larga-9")
        rest, ana = billing.create_account(s, name="Asador Marina", manager_name="Ana",
                                           manager_email="ana@marina.com",
                                           password="clave-larga-1", plan=Plan.SINGLE,
                                           monthly_fee=89.0, language="es")
        rest.payment_ref = "cus_marina"
        rest.payment_provider = "stripe"
        rest.billing = Billing.ACTIVE
        s.flush()
        yield s, rest, ana


def evento(tipo, objeto, event_id="evt_1"):
    return json.dumps({"id": event_id, "type": tipo, "data": {"object": objeto}}).encode()


def firmado(payload, key=SECRETO, now=None):
    return gateway.sign(payload, key, now=now)


# --------------------------------------------------------------- la firma
def test_without_a_good_signature_nothing_is_touched(casa):
    s, rest, ana = casa
    payload = evento("invoice.payment_failed", {"customer": "cus_marina"})

    with pytest.raises(gateway.GatewayError):
        gateway.apply(s, payload, "")                                   # sin firma
    with pytest.raises(gateway.GatewayError):
        gateway.apply(s, payload, firmado(payload, key="otro-secreto"))  # firmada por otro
    with pytest.raises(gateway.GatewayError):
        gateway.apply(s, payload, firmado(payload, now=time.time() - 3600))  # vieja

    assert rest.billing == Billing.ACTIVE
    assert s.query(GatewayEvent).count() == 0


def test_a_tampered_body_does_not_pass(casa):
    s, rest, ana = casa
    payload = evento("invoice.payment_failed", {"customer": "cus_marina"})
    header = firmado(payload)
    with pytest.raises(gateway.GatewayError):
        gateway.apply(s, payload.replace(b"cus_marina", b"cus_otro__"), header)


# ------------------------------------------------------------ los recibos
def test_a_paid_invoice_leaves_the_account_up_to_date(casa):
    s, rest, ana = casa
    rest.billing = Billing.PAST_DUE
    s.flush()
    fin = int(datetime(2026, 11, 30).timestamp())
    payload = evento("invoice.paid", {"customer": "cus_marina", "number": "F-2026-31",
                                      "period_end": fin})
    result = gateway.apply(s, payload, firmado(payload))

    assert result.changed and result.now == Billing.ACTIVE
    assert rest.billing == Billing.ACTIVE
    assert rest.paid_until == date(2026, 11, 30)
    assert "F-2026-31" in (rest.billing_note or "")
    assert result.alerts and "billing.gateway" == result.alerts[0].code


def test_a_failed_invoice_warns_but_lets_them_work(casa):
    s, rest, ana = casa
    payload = evento("invoice.payment_failed", {"customer": "cus_marina", "id": "in_9"})
    result = gateway.apply(s, payload, firmado(payload))

    assert rest.billing == Billing.PAST_DUE        # avisa; bloquear es otra decisión
    assert result.now == Billing.PAST_DUE
    assert result.alerts[0].severity.value == "CRITICAL"


def test_a_cancelled_subscription_closes_the_account(casa):
    s, rest, ana = casa
    payload = evento("customer.subscription.deleted", {"customer": "cus_marina"})
    gateway.apply(s, payload, firmado(payload))
    assert rest.billing == Billing.CANCELLED and rest.cancelled_at is not None


def test_the_subscription_status_is_followed(casa):
    s, rest, ana = casa
    rest.billing = Billing.ACTIVE
    s.flush()
    payload = evento("customer.subscription.updated",
                     {"customer": "cus_marina", "status": "past_due"}, event_id="evt_p")
    gateway.apply(s, payload, firmado(payload))
    assert rest.billing == Billing.PAST_DUE

    fin = int(datetime(2026, 12, 31).timestamp())
    vuelta = evento("customer.subscription.updated",
                    {"customer": "cus_marina", "status": "active",
                     "current_period_end": fin}, event_id="evt_a")
    gateway.apply(s, vuelta, firmado(vuelta))
    assert rest.billing == Billing.ACTIVE and rest.paid_until == date(2026, 12, 31)


def test_the_card_arriving_starts_the_trial(casa):
    s, rest, ana = casa
    rest.billing = Billing.SETUP
    rest.payment_ref = None
    rest.trial_ends = None
    s.flush()
    payload = evento("checkout.session.completed",
                     {"customer": "cus_marina", "metadata": {"restaurant_id": rest.id},
                      "card": {"brand": "visa", "last4": "4242",
                               "exp_month": 12, "exp_year": 2030}})
    gateway.apply(s, payload, firmado(payload))

    assert rest.billing == Billing.TRIAL
    assert rest.trial_ends == date.today() + timedelta(days=billing.TRIAL_DAYS)
    assert rest.payment_brand == "VISA" and rest.payment_last4 == "4242"
    assert rest.payment_expiry == "12/2030"


# ------------------------------------------------------------ una sola vez
def test_the_same_event_is_applied_once(casa):
    """La pasarela reintenta: cobrar dos veces el mismo recibo no se disculpa."""
    s, rest, ana = casa
    rest.billing = Billing.PAST_DUE
    s.flush()
    payload = evento("invoice.paid", {"customer": "cus_marina",
                                      "period_end": int(datetime(2026, 11, 30).timestamp())})
    header = firmado(payload)
    primero = gateway.apply(s, payload, header)
    segundo = gateway.apply(s, payload, header)

    assert primero.changed and primero.ignored is None
    assert segundo.ignored == "repetido"
    assert s.query(GatewayEvent).count() == 1


def test_an_event_about_a_house_we_do_not_know_is_kept_and_ignored(casa):
    s, rest, ana = casa
    payload = evento("invoice.paid", {"customer": "cus_de_otro"})
    result = gateway.apply(s, payload, firmado(payload))
    assert result.ignored == "sin casa" and rest.billing == Billing.ACTIVE
    assert s.query(GatewayEvent).one().restaurant_id is None


def test_an_event_we_do_not_care_about_changes_nothing(casa):
    s, rest, ana = casa
    payload = evento("customer.updated", {"customer": "cus_marina"})
    result = gateway.apply(s, payload, firmado(payload))
    assert result.ignored == "no interesa" and rest.billing == Billing.ACTIVE


# --------------------------------------------------------------- la puerta
def test_the_door_answers_the_gateway_and_nobody_else(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRETO)
    db.init_engine(f"sqlite:///{tmp_path/'puerta.db'}")
    db.create_all()
    with db.session_scope() as s:
        billing.bootstrap_owner(s, "albano@grill.com", "Albano", "clave-larga-9")
        rest, ana = billing.create_account(s, name="Asador", manager_name="Ana",
                                           manager_email="ana@a.com",
                                           password="clave-larga-1", plan=Plan.SINGLE,
                                           monthly_fee=89.0, language="es")
        rest.payment_ref = "cus_x"
        rest.billing = Billing.PAST_DUE
        s.flush()

    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        payload = evento("invoice.paid", {"customer": "cus_x"})
        mala = c.post("/pasarela/stripe", content=payload,
                      headers={"stripe-signature": "t=1,v1=nada"})
        assert mala.status_code == 400

        buena = c.post("/pasarela/stripe", content=payload,
                       headers={"stripe-signature": firmado(payload)})
        assert buena.status_code == 200 and buena.json()["applied"] is True

    with db.session_scope() as s:
        assert s.query(Restaurant).filter_by(slug="asador").one().billing == Billing.ACTIVE


def test_without_the_secret_the_door_says_it_is_not_set_up(tmp_path, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    db.init_engine(f"sqlite:///{tmp_path/'sin.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        r = c.post("/pasarela/stripe", content=b"{}",
                   headers={"stripe-signature": "t=1,v1=x"})
        assert r.status_code == 503


# ============================================ cancelar aquí y allí (blq. 2)
# Dos agujeros de la misma cosa, y los dos cuestan dinero de otro:
#
# 1. `_paid` ponía la cuenta en activo sin mirar en qué estado estaba, así que
#    un recibo que llegara después de la baja —el último prorrateo, el
#    reintento de uno viejo— resucitaba la casa: dentro otra vez y pagando.
# 2. cancelar aquí no tocaba la pasarela: la suscripción seguía viva y la
#    tarjeta se pasaba el mes siguiente, y el siguiente.


def test_a_paid_invoice_never_brings_a_cancelled_house_back(casa):
    """Una baja la deshace una persona, no un aviso del banco."""
    s, rest, ana = casa
    rest.billing = Billing.CANCELLED
    rest.cancelled_at = datetime.utcnow()
    s.flush()
    fin = int(datetime(2026, 11, 30).timestamp())
    payload = evento("invoice.paid", {"customer": "cus_marina", "number": "F-2026-40",
                                      "period_end": fin})
    result = gateway.apply(s, payload, firmado(payload))

    assert rest.billing == Billing.CANCELLED, "un recibo resucitó una casa cancelada"
    assert result.ignored, "se aplicó sin decir que no se aplicaba"
    # Y queda escrito, porque es dinero que hay que devolver.
    assert rest.subscription_open is True
    apuntes = [a.action for a in s.query(AuditLog).all()]
    assert "PAID_AFTER_CANCEL" in apuntes


def test_cancelling_here_stops_the_charge_over_there(casa):
    """La mitad que faltaba: la casa se va y deja de pagar."""
    s, rest, ana = casa
    llamadas = []

    def pasarela(restaurante):
        llamadas.append(restaurante.payment_ref)
        return True, "parada: sub_1"

    billing.cancel(s, ana, rest, "nos mudamos", para_el_cobro=pasarela)

    assert llamadas == ["cus_marina"], "canceló sin tocar la pasarela"
    assert rest.billing == Billing.CANCELLED
    assert not rest.subscription_open


def test_a_gateway_that_does_not_answer_never_holds_a_cancellation(casa):
    """Nadie se queda atrapado porque un servidor de otro esté caído."""
    s, rest, ana = casa
    billing.cancel(s, ana, rest, None,
                   para_el_cobro=lambda r: (False, "la pasarela no contestó"))

    assert rest.billing == Billing.CANCELLED, "una avería nuestra retuvo al cliente"
    assert rest.subscription_open is True, "se dio por parado un cobro que sigue vivo"
    apuntes = [a.action for a in s.query(AuditLog).all()]
    assert "CHARGE_OPEN" in apuntes, "el dueño no se iba a enterar"


def test_without_a_key_the_charge_is_never_reported_as_stopped(casa, monkeypatch):
    """Sin clave no se puede parar nada, y eso se dice en vez de suponerlo."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    parado, porque = gateway.stop_subscription(rest_con_ref("cus_x"))
    assert not parado and "STRIPE_SECRET_KEY" in porque


def test_a_house_that_never_paid_has_nothing_to_stop(casa):
    """Sin referencia no hay suscripción: no es un fallo, es que no hay nada."""
    parado, _ = gateway.stop_subscription(rest_con_ref(None))
    assert parado


def test_every_live_subscription_of_the_customer_is_cancelled(casa, monkeypatch):
    """Una casa puede tener más de una; las muertas no se tocan."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_prueba")
    hechas = []

    def falsa(method, path, params=None):
        hechas.append((method, path))
        if method == "GET":
            return {"data": [{"id": "sub_viva", "status": "active"},
                             {"id": "sub_otra", "status": "trialing"},
                             {"id": "sub_muerta", "status": "canceled"}]}
        return {"status": "canceled"}

    monkeypatch.setattr(gateway, "_pide", falsa)
    parado, porque = gateway.stop_subscription(rest_con_ref("cus_marina"))

    assert parado, porque
    assert ("DELETE", "/subscriptions/sub_viva") in hechas
    assert ("DELETE", "/subscriptions/sub_otra") in hechas
    assert ("DELETE", "/subscriptions/sub_muerta") not in hechas


def test_a_subscription_that_survives_the_call_is_not_reported_as_stopped(casa, monkeypatch):
    """Si la pasarela dice que sigue viva, sigue viva. No se da por bueno."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_prueba")
    monkeypatch.setattr(gateway, "_pide",
                        lambda m, p, params=None: {"status": "active"})
    parado, porque = gateway.stop_subscription(rest_con_ref("sub_terca"))
    assert not parado and "sub_terca" in porque


def test_the_gateway_blowing_up_is_never_an_exception_here(casa, monkeypatch):
    """Quien llama está cancelando su cuenta: esto no puede levantar nada."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_prueba")

    def revienta(method, path, params=None):
        raise OSError("la red se fue")

    monkeypatch.setattr(gateway, "_pide", revienta)
    parado, porque = gateway.stop_subscription(rest_con_ref("sub_1"))
    assert not parado and "la red se fue" in porque


def rest_con_ref(ref):
    """Una casa de mentira con —o sin— referencia en la pasarela."""
    return Restaurant(name="X", slug="x", payment_ref=ref)
