"""[00418] La cuenta: quién entra, qué paga y qué pasa si no paga.

La cadena es corta y a propósito:

1. una casa pide acceso desde la web pública y la solicitud se guarda;
2. el dueño de la plataforma la ve en su bandeja y le da de alta, creando de
   paso la cuenta de su manager;
3. el manager crea desde su pantalla las cuentas de sus carniceros y ayudantes;
4. si el recibo del mes falla, el dueño lo marca y la cuenta se bloquea: el
   manager ve por qué, y el resto ve que hable con su manager.

Nadie se da de alta solo: no hay registro abierto en esta edición.
"""
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from thegrill.meat import mailer, perms, privacy
from thegrill.models import (AccessRequest, AuditLog, Billing, Plan, RequestStatus,
                             Restaurant, Role, User)
from thegrill.web import auth, exacto

# [01694] Cómo se llama la pasarela en lo que queda escrito. Se escribe aquí y no
# se importa de `gateway` porque `gateway` importa este módulo.
PROVEEDOR = "stripe"

TRIAL_DAYS = 15        # quince días de prueba, sin cobrar


class BillingError(ValueError):
    """[00419] La cuenta no se puede tocar así."""


class CardNumberRefused(BillingError):
    """[00420] Alguien ha intentado meter un número de tarjeta donde no toca."""


def _looks_like_a_card(value: str) -> bool:
    """[00421] Trece a diecinueve dígitos seguidos, con o sin espacios: eso es un PAN."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if not 13 <= len(digits) <= 19:
        return False
    # [00446] Luhn. Un número largo cualquiera no lo pasa; una tarjeta, sí.
    total, alt = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def refuse_card_numbers(**fields) -> None:
    """[00422] Esta plataforma no toca números de tarjeta: ni de paso, ni por error.

    El cobro va por una pasarela certificada, que es quien puede guardarlos. Si
    uno aparece en un formulario nuestro, se rechaza el formulario entero antes
    de escribir nada: lo que no entra no se puede filtrar.
    """
    for name, value in fields.items():
        if isinstance(value, str) and _looks_like_a_card(value):
            raise CardNumberRefused(
                "Aquí no se escriben números de tarjeta. El pago se da de alta en la "
                "pasarela, que es la que puede guardarlos.")


# ------------------------------------------------------- solicitudes de acceso
FIELDS = ("restaurant_name", "legal_name", "tax_number", "country", "address",
          "contact_name", "contact_role", "email", "phone", "message")


def request_access(session: Session, *, restaurant_name: str, contact_name: str,
                   email: str, plan: Plan = Plan.SINGLE, outlets: int = 1,
                   cooks: int | None = None, **extra) -> AccessRequest:
    """[00423] Guarda la solicitud y avisa por correo. El registro manda, el correo avisa."""
    if not restaurant_name.strip():
        raise BillingError("Hace falta el nombre del restaurante")
    if not contact_name.strip():
        raise BillingError("Hace falta el nombre de la persona a cargo")
    email = auth.normalize_email(email)

    refuse_card_numbers(**{k: v for k, v in extra.items() if isinstance(v, str)})
    row = AccessRequest(restaurant_name=restaurant_name.strip(),
                        contact_name=contact_name.strip(), email=email,
                        plan=plan, outlets=max(1, outlets or 1), cooks=cooks,
                        **{k: (v.strip() if isinstance(v, str) else v)
                           for k, v in extra.items() if k in FIELDS and v})
    aviso = summary(row)          # el correo sale con los datos en claro
    destino = row.email
    privacy.protect_request(row)  # y en la base se guardan protegidos
    session.add(row)
    session.flush()
    row.notified = mailer.send(f"Solicitud de acceso · {row.restaurant_name}",
                               aviso, reply_to=destino)
    session.flush()
    return row


def summary(row: AccessRequest) -> str:
    """[00424] La solicitud entera en texto, que es lo que llega al correo."""
    ver = privacy.reveal
    lines = [
        f"Restaurante:      {row.restaurant_name}",
        f"Nombre fiscal:    {ver(row.legal_name) or '—'}",
        f"Número fiscal:    {ver(row.tax_number) or '—'}",
        f"País:             {row.country or '—'}",
        f"Dirección:        {(ver(row.address) or '—').replace(chr(10), ' / ')}",
        f"Persona a cargo:  {ver(row.contact_name)} ({row.contact_role or '—'})",
        f"Correo:           {ver(row.email)}",
        f"Teléfono:         {ver(row.phone) or '—'}",
        f"Cocineros:        {row.cooks if row.cooks is not None else '—'}",
        f"Locales:          {row.outlets or 1}",
        f"Plan:             {row.plan.value if row.plan else '—'}",
        f"Mensaje:          {(row.message or '—').replace(chr(10), ' / ')}",
        f"Recibida:         {(row.created_at or datetime.utcnow()):%d/%m/%Y %H:%M} UTC",
    ]
    return "\n".join(lines)


def requests(session: Session, status: RequestStatus | None = None,
             limit: int = 200) -> list[AccessRequest]:
    """[00425] Las solicitudes de acceso, de la más nueva a la más vieja."""
    query = session.query(AccessRequest)
    if status is not None:
        query = query.filter_by(status=status)
    return query.order_by(AccessRequest.created_at.desc()).limit(limit).all()


def set_request_status(session: Session, request_id: int,
                       status: RequestStatus) -> AccessRequest:
    """[00426] Cambia el estado de una solicitud: atendida, rechazada, pendiente."""
    row = session.get(AccessRequest, request_id)
    if row is None:
        raise BillingError("Esa solicitud no existe")
    row.status = status
    session.flush()
    return row


# ------------------------------------------------------------ alta de la casa
def create_account(session: Session, *, name: str, manager_name: str, manager_email: str,
                   password: str, plan: Plan = Plan.SINGLE, outlets: int = 1,
                   monthly_fee: float | None = None, language: str = "es",
                   request_id: int | None = None, group: str | None = None,
                   **fiscal) -> tuple[Restaurant, User]:
    """[00427] Da de alta el restaurante y la cuenta de su manager.

    Es el único camino: en esta edición nadie se registra solo.
    """
    restaurant, manager = auth.create_restaurant(session, name, manager_email,
                                                 manager_name, password, language=language)
    restaurant.plan = plan
    restaurant.group_name = (group or "").strip() or None
    restaurant.outlets = max(1, outlets or 1)
    restaurant.monthly_fee = monthly_fee
    # [00447] Sin método de pago no empieza la prueba: primero la tarjeta en la pasarela.
    restaurant.billing = Billing.SETUP
    for field in ("legal_name", "tax_number", "address", "country", "contact_name",
                  "contact_role", "contact_phone", "billing_email", "cooks"):
        if fiscal.get(field) not in (None, ""):
            setattr(restaurant, field, fiscal[field])
    if request_id is not None:
        row = session.get(AccessRequest, request_id)
        if row is not None:
            row.status = RequestStatus.ACCEPTED
            row.restaurant_id = restaurant.id
    session.flush()
    return restaurant, manager


def timedelta_days(days: int):
    """[00428] Tantos días, para sumarlos o restarlos a una fecha."""
    from datetime import timedelta
    return timedelta(days=days)


def attach_payment_method(session: Session, actor: User, restaurant: Restaurant, *,
                          provider: str, reference: str, brand: str | None = None,
                          last4: str | None = None, expiry: str | None = None,
                          on: date | None = None) -> Restaurant:
    """[00429] Apunta el método de pago que ha devuelto la pasarela y arranca la prueba.

    Lo que se guarda es su referencia y los cuatro últimos dígitos, que es lo
    que la pasarela deja ver. El número entero no pasa por aquí.
    """
    if not reference.strip():
        raise BillingError("La pasarela tiene que devolver una referencia")
    refuse_card_numbers(reference=reference, brand=brand or "", last4="")
    if last4 and (len(last4) != 4 or not last4.isdigit()):
        raise BillingError("De la tarjeta solo se guardan los cuatro últimos dígitos")
    restaurant.payment_provider = provider.strip()
    restaurant.payment_ref = reference.strip()
    restaurant.payment_brand = (brand or "").strip() or None
    restaurant.payment_last4 = last4 or None
    restaurant.payment_expiry = (expiry or "").strip() or None
    if restaurant.billing in (None, Billing.SETUP):
        today = on or date.today()
        restaurant.billing = Billing.TRIAL
        restaurant.trial_ends = today + timedelta_days(TRIAL_DAYS)
        restaurant.paid_until = restaurant.trial_ends
    audit(session, actor, restaurant.id, restaurant.slug, "PAYMENT",
          f"{provider} {brand or ''} ····{last4 or '????'}".strip())
    return restaurant


def cancel(session: Session, actor: User, restaurant: Restaurant,
           reason: str | None = None, on: date | None = None,
           para_el_cobro=None) -> Restaurant:
    """[00430] Cancela la cuenta. Durante la prueba no se cobra nada.

    Y para el cobro en la pasarela, que es la mitad que faltaba. Poner la casa
    en `CANCELLED` la deja fuera del programa, pero la suscripción seguía viva
    en la pasarela: la tarjeta se pasaba el mes siguiente, y el siguiente, a
    alguien que ya se había ido. Eso no es un descuido de facturación, es
    cobrar por algo que no se está dando.

    La baja no depende de que la pasarela conteste. Si no se puede confirmar
    que el cobro quedó parado, la casa queda cancelada igual y se enciende
    `subscription_open`, que sale en rojo en la consola del dueño hasta que se
    pare a mano. Al revés —no dejar cancelar si la pasarela está caída— sería
    retener a un cliente por una avería nuestra.

    `para_el_cobro` se puede sustituir en las pruebas: así se prueba sin salir
    a internet, que es lo único que no se puede probar de verdad aquí.
    """
    # [01692] Aquí dentro y no arriba: `gateway` importa este módulo, y al revés
    # sería un círculo. Es la única llamada que va en esa dirección.
    if para_el_cobro is None:
        from thegrill.meat.gateway import stop_subscription as para_el_cobro

    today = on or date.today()
    en_prueba = restaurant.billing == Billing.TRIAL and (
        restaurant.trial_ends is None or today <= restaurant.trial_ends)
    restaurant.billing = Billing.CANCELLED
    restaurant.cancelled_at = datetime.utcnow()
    restaurant.billing_note = reason or None

    parado, porque = para_el_cobro(restaurant)
    restaurant.subscription_open = not parado

    audit(session, actor, restaurant.id, restaurant.slug, "CANCELLED",
          ("en prueba, sin cobrar" if en_prueba else "fuera de prueba") +
          (f" · {reason}" if reason else "") + f" · {PROVEEDOR}: {porque}")
    if not parado:
        # [01693] El dueño tiene que verlo sin buscarlo: es dinero de otro.
        audit(session, actor, restaurant.id, restaurant.slug, "CHARGE_OPEN", porque)
    return restaurant


def free_cancellation(restaurant: Restaurant, on: date | None = None) -> bool:
    """[00431] Si cancelar hoy sale gratis: se está en prueba y no ha terminado."""
    if restaurant.billing != Billing.TRIAL:
        return False
    return restaurant.trial_ends is None or (on or date.today()) <= restaurant.trial_ends


def audit(session: Session, actor: User, restaurant_id: int, key: str,
          action: str, detail: str) -> None:
    """[00432] Lo que toca la cuenta queda escrito: quién, qué y por qué."""
    session.add(AuditLog(restaurant_id=restaurant_id, actor=f"{actor.name} <{actor.email}>",
                         table="account", key=key, action=action, detail=detail))
    session.flush()


def trial_left(restaurant: Restaurant, on: date | None = None) -> int | None:
    """[00433] Días de prueba que quedan. None si no está de prueba.

    Es el reloj que ven el manager y la plataforma, y nadie más: la cocina no
    tiene por qué enterarse de cómo va el recibo.
    """
    if restaurant.billing != Billing.TRIAL or restaurant.trial_ends is None:
        return None
    return (restaurant.trial_ends - (on or date.today())).days


def create_user(session: Session, manager: User, *, name: str, email: str,
                password: str, role: Role = Role.BUTCHER,
                language: str | None = None) -> User:
    """[00434] El manager crea las cuentas de su gente.

    El manager general —el de la casa entera— da de alta también a los
    managers de cada local: un grupo con obrador y tres locales no lo lleva
    una sola persona, y pedirle la cuenta a la plataforma cada vez no es
    manera. Al dueño de la plataforma no lo crea nadie desde aquí.
    """
    if role not in perms.grantable_roles(manager):
        raise BillingError("Ese nivel no lo puedes repartir tú")
    email = auth.normalize_email(email)
    if (session.query(User)
            .filter_by(restaurant_id=manager.restaurant_id, email=email).first()):
        raise BillingError(f"Ya hay alguien con el correo {email} en esta casa")
    if len(password or "") < 8:
        raise BillingError("La contraseña necesita al menos 8 caracteres")
    user = User(restaurant_id=manager.restaurant_id, email=email, name=name.strip(),
                role=role, language=language or manager.language,
                password_hash=auth.hash_password(password))
    session.add(user)
    session.flush()
    return user


# ------------------------------------------------------------------- el recibo
@dataclass
class Account:
    restaurant: Restaurant
    users: int
    managers: int

    @property
    def blocked(self) -> bool:
        """[00442] Si esa casa no puede entrar."""
        return self.restaurant.blocked


@dataclass
class Group:
    """[00435] Varias casas de la misma empresa, para verlas y cobrarlas juntas."""
    name: str | None
    houses: list["Account"] = field(default_factory=list)

    @property
    def outlets(self) -> int:
        """[00443] Cuántas casas tiene el grupo."""
        return len(self.houses)

    @property
    def monthly(self) -> float:
        """[00444] Lo que paga el grupo al mes, sumando todas sus casas."""
        return exacto.eur(sum(h.restaurant.monthly_fee or 0.0 for h in self.houses))

    @property
    def needs_attention(self) -> bool:
        """[00445] Si alguna casa del grupo debe o está bloqueada."""
        return any(h.restaurant.needs_attention or h.blocked for h in self.houses)


def grouped(session: Session) -> list[Group]:
    """[00436] Las casas por grupo. Las que van solas quedan en un grupo de una."""
    groups: dict[str | None, Group] = {}
    for account in accounts(session):
        key = account.restaurant.group_name or None
        groups.setdefault(key, Group(name=key)).houses.append(account)
    # [00448] Primero los grupos de verdad, y dentro por nombre de casa.
    return sorted(groups.values(),
                  key=lambda g: (g.name is None, g.name or "",))


def accounts(session: Session) -> list[Account]:
    """[00437] Todas las casas con su estado de cuenta. Solo para el dueño."""
    rows = []
    for restaurant in (session.query(Restaurant)
                       .filter(Restaurant.platform.isnot(True))
                       .order_by(Restaurant.name)):
        users = session.query(User).filter_by(restaurant_id=restaurant.id, active=True).all()
        rows.append(Account(restaurant=restaurant, users=len(users),
                            managers=len([u for u in users if u.role == Role.MANAGER])))
    return rows


def mark_paid(session: Session, owner: User, restaurant: Restaurant,
              until: date | None = None, note: str | None = None) -> Restaurant:
    """[00438] El recibo del mes está pagado: la cuenta queda al día y se desbloquea."""
    restaurant.billing = Billing.ACTIVE
    restaurant.paid_until = until or restaurant.paid_until
    restaurant.billing_note = note or None
    audit(session, owner, restaurant.id, restaurant.slug, "PAID",
          f"al día hasta {restaurant.paid_until or '—'}")
    return restaurant


def mark_unpaid(session: Session, owner: User, restaurant: Restaurant,
                block: bool = False, note: str | None = None) -> Restaurant:
    """[00439] El recibo ha fallado. Primero se avisa; bloquear es la decisión siguiente."""
    restaurant.billing = Billing.BLOCKED if block else Billing.PAST_DUE
    restaurant.billing_note = note or None
    audit(session, owner, restaurant.id, restaurant.slug,
          "BLOCKED" if block else "PAST_DUE", note or "")
    return restaurant


def owner_exists(session: Session) -> bool:
    """[00440] Si ya hay dueño de la plataforma. Solo se da de alta la primera vez."""
    return session.query(User).filter_by(role=Role.OWNER).first() is not None


def bootstrap_owner(session: Session, email: str, name: str, password: str,
                    language: str = "es") -> User:
    """[00441] Crea al dueño de la plataforma la primera vez. Solo puede haber uno."""
    if owner_exists(session):
        raise BillingError("El dueño de la plataforma ya está dado de alta")
    house = Restaurant(name="Plataforma", slug=auth.unique_slug(session, "plataforma"),
                       join_code=auth.new_join_code(), language=language, platform=True,
                       billing=Billing.ACTIVE, created_at=datetime.utcnow())
    session.add(house)
    session.flush()
    owner = User(restaurant_id=house.id, email=auth.normalize_email(email),
                 name=name.strip(), role=Role.OWNER, language=language,
                 password_hash=auth.hash_password(password))
    session.add(owner)
    session.flush()
    return owner
