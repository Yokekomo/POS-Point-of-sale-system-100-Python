"""Lo que la pasarela de pago cuenta, y qué se hace con ello.

El cobro del mes lo hace la pasarela: ella tiene la tarjeta, ella la pasa y
ella reintenta cuando falla. Lo que hace falta aquí es enterarse, porque de
eso depende si la casa entra mañana o no: un recibo pagado deja la cuenta al
día, uno fallado la pone en aviso, y una suscripción cancelada la cierra.

Eso llega por webhook: la pasarela llama a `/pasarela/stripe` con un evento
firmado. Aquí se comprueba la firma —sin firma buena no se toca nada, que esa
dirección es pública—, se mira si ese evento ya se había procesado y, si es
nuevo, se aplica.

Lo que se guarda de la tarjeta sigue siendo lo mismo de siempre: la referencia
del cliente en la pasarela y los cuatro últimos dígitos. El número no pasa por
aquí ni pasará.

Sin `STRIPE_WEBHOOK_SECRET` configurado, la dirección responde que no está
puesta en marcha en vez de fingir que sí.
"""
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from thegrill.meat import billing
from thegrill.models import (Alert, AlertSeverity, Billing, GatewayEvent, Restaurant, Role,
                             User)
from thegrill.web import service
from thegrill.web.i18n import t

TOLERANCE_SECONDS = 300      # un evento con más de cinco minutos no se acepta
PROVIDER = "stripe"


class GatewayError(ValueError):
    """El evento no se puede aceptar, y se dice por qué."""


@dataclass
class Applied:
    """Lo que ha hecho un evento."""
    event_id: str
    kind: str
    restaurant: Restaurant | None = None
    was: Billing | None = None
    now: Billing | None = None
    paid_until: date | None = None
    ignored: str | None = None          # por qué no se ha hecho nada
    alerts: list[Alert] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.now is not None and self.now != self.was


def secret() -> str | None:
    return os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip() or None


# ------------------------------------------------------------------- firma
def check_signature(payload: bytes, header: str, key: str | None = None,
                    now: float | None = None) -> None:
    """La firma de la pasarela, comprobada como manda su documentación.

    La cabecera trae la hora y una o varias firmas: `t=1699999999,v1=abc…`. Se
    firma «hora.cuerpo» con el secreto y se compara en tiempo constante. Una
    hora vieja no vale, para que nadie reenvíe un evento de hace meses.
    """
    key = key or secret()
    if not key:
        raise GatewayError("La pasarela no está configurada en este servidor")
    partes = dict(p.split("=", 1) for p in (header or "").split(",") if "=" in p)
    marca, firma = partes.get("t"), partes.get("v1")
    if not marca or not firma:
        raise GatewayError("El evento no viene firmado")
    try:
        cuando = float(marca)
    except ValueError:
        raise GatewayError("La firma no trae una hora válida") from None
    if abs((time.time() if now is None else now) - cuando) > TOLERANCE_SECONDS:
        raise GatewayError("El evento es viejo: no se acepta")

    esperada = hmac.new(key.encode(), f"{marca}.".encode() + payload,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperada, firma):
        raise GatewayError("La firma no cuadra")


def sign(payload: bytes, key: str, now: float | None = None) -> str:
    """La cabecera que mandaría la pasarela. Sirve para las pruebas y para probar el enganche."""
    marca = int(time.time() if now is None else now)
    firma = hmac.new(key.encode(), f"{marca}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={marca},v1={firma}"


# ------------------------------------------------------------------ evento
def apply(session: Session, payload: bytes, header: str, key: str | None = None,
          now: float | None = None) -> Applied:
    """Comprueba el evento y lo aplica una sola vez."""
    check_signature(payload, header, key=key, now=now)
    try:
        event = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise GatewayError("El evento no es un JSON que se pueda leer") from None

    event_id = str(event.get("id") or "").strip()
    kind = str(event.get("type") or "").strip()
    if not event_id or not kind:
        raise GatewayError("El evento no dice ni qué es ni cuál es")

    # La pasarela reintenta hasta que le contestas bien, así que el mismo
    # evento llega varias veces: se aplica una.
    if session.query(GatewayEvent).filter_by(provider=PROVIDER, event_id=event_id).first():
        return Applied(event_id=event_id, kind=kind, ignored="repetido")

    data = (event.get("data") or {}).get("object") or {}
    restaurant = _whose(session, data)
    result = Applied(event_id=event_id, kind=kind, restaurant=restaurant)
    session.add(GatewayEvent(provider=PROVIDER, event_id=event_id, kind=kind,
                             restaurant_id=restaurant.id if restaurant else None,
                             received_at=datetime.utcnow()))
    if restaurant is None:
        result.ignored = "sin casa"
        session.flush()
        return result

    result.was = restaurant.billing
    handler = HANDLERS.get(kind)
    if handler is None:
        result.ignored = "no interesa"
    else:
        handler(session, restaurant, data, result)
    result.now = restaurant.billing
    session.flush()
    if result.changed:
        _announce(session, restaurant, result)
    return result


def _whose(session: Session, data: dict) -> Restaurant | None:
    """De qué casa habla el evento: por su referencia o por lo que lleve escrito."""
    query = session.query(Restaurant)
    metadata = data.get("metadata") or {}
    for campo in ("restaurant_id", "house_id"):
        if metadata.get(campo):
            found = session.get(Restaurant, _int(metadata[campo]))
            if found is not None:
                return found
    for valor in (data.get("customer"), data.get("client_reference_id"),
                  (data.get("subscription") if isinstance(data.get("subscription"), str) else None)):
        if not valor:
            continue
        found = query.filter(Restaurant.payment_ref == str(valor)).first()
        if found is not None:
            return found
    return None


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ------------------------------------------------------- qué hace cada uno
def _paid(session: Session, restaurant: Restaurant, data: dict, result: Applied) -> None:
    """Recibo cobrado: la cuenta queda al día hasta el final del periodo."""
    hasta = _period_end(data)
    billing.mark_paid(session, _platform(session), restaurant, until=hasta,
                      note=f"{PROVIDER}: {data.get('number') or data.get('id') or ''}".strip())
    result.paid_until = restaurant.paid_until


def _failed(session: Session, restaurant: Restaurant, data: dict, result: Applied) -> None:
    """Recibo fallado: avisa, pero deja trabajar. Bloquear es otra decisión."""
    billing.mark_unpaid(session, _platform(session), restaurant,
                        note=f"{PROVIDER}: {data.get('id') or ''}".strip())


def _cancelled(session: Session, restaurant: Restaurant, data: dict, result: Applied) -> None:
    restaurant.billing = Billing.CANCELLED
    restaurant.cancelled_at = datetime.utcnow()
    restaurant.billing_note = f"{PROVIDER}: suscripción cancelada"


def _subscription(session: Session, restaurant: Restaurant, data: dict, result: Applied) -> None:
    """La suscripción cambia de estado: se sigue lo que diga la pasarela."""
    estado = str(data.get("status") or "").lower()
    if estado in ("active", "trialing"):
        if restaurant.billing in (Billing.PAST_DUE, Billing.BLOCKED, None, Billing.SETUP):
            billing.mark_paid(session, _platform(session), restaurant,
                              until=_period_end(data), note=f"{PROVIDER}: {estado}")
            result.paid_until = restaurant.paid_until
    elif estado in ("past_due", "unpaid", "incomplete"):
        billing.mark_unpaid(session, _platform(session), restaurant,
                            note=f"{PROVIDER}: {estado}")
    elif estado == "canceled":
        _cancelled(session, restaurant, data, result)


def _method(session: Session, restaurant: Restaurant, data: dict, result: Applied) -> None:
    """La tarjeta ya está puesta en la pasarela: se apunta y arranca la prueba."""
    tarjeta = ((data.get("payment_method_details") or {}).get("card")
               or (data.get("card") or {}))
    referencia = str(data.get("customer") or restaurant.payment_ref or "").strip()
    if not referencia:
        result.ignored = "sin referencia de cliente"
        return
    billing.attach_payment_method(
        session, _platform(session), restaurant, provider=PROVIDER, reference=referencia,
        brand=(tarjeta.get("brand") or "").upper() or None,
        last4=tarjeta.get("last4"),
        expiry=(f"{tarjeta['exp_month']:02d}/{tarjeta['exp_year']}"
                if tarjeta.get("exp_month") and tarjeta.get("exp_year") else None))


HANDLERS = {
    "invoice.paid": _paid,
    "invoice.payment_succeeded": _paid,
    "invoice.payment_failed": _failed,
    "customer.subscription.deleted": _cancelled,
    "customer.subscription.updated": _subscription,
    "customer.subscription.created": _subscription,
    "checkout.session.completed": _method,
    "setup_intent.succeeded": _method,
}


def _period_end(data: dict) -> date | None:
    for campo in ("period_end", "current_period_end"):
        if data.get(campo):
            try:
                return datetime.utcfromtimestamp(int(data[campo])).date()
            except (TypeError, ValueError, OSError):
                return None
    lines = ((data.get("lines") or {}).get("data") or [])
    if lines:
        periodo = (lines[0] or {}).get("period") or {}
        if periodo.get("end"):
            try:
                return datetime.utcfromtimestamp(int(periodo["end"])).date()
            except (TypeError, ValueError, OSError):
                return None
    return None


def _platform(session: Session) -> User:
    """Quien firma estos cambios es la plataforma, no una persona."""
    owner = session.query(User).filter_by(role=Role.OWNER).first()
    if owner is not None:
        return owner
    house = session.query(Restaurant).filter_by(platform=True).first()
    return User(id=None, restaurant_id=house.id if house else None,
                name=PROVIDER, email="", role=Role.OWNER)


def _announce(session: Session, restaurant: Restaurant, result: Applied) -> None:
    """Al manager de la casa se le dice, porque le cambia el día."""
    lang = restaurant.language or "es"
    severity = (AlertSeverity.CRITICAL if result.now in (Billing.BLOCKED, Billing.PAST_DUE)
                else AlertSeverity.INFO)
    alert = Alert(restaurant_id=restaurant.id, code="billing.gateway",
                  message=t(lang, "billing.changed", state=t(lang, f"billing.{result.now.value}")),
                  severity=severity, created_at=datetime.utcnow())
    session.add(alert)
    session.flush()
    result.alerts.append(alert)
    service.notify(session, restaurant.id, service.manager_ids(session, restaurant.id),
                   title=t(lang, "acct.title"), body=alert.message, severity=severity,
                   alert_id=alert.id)
