"""El precio que se publica en la web de venta.

Se cambia desde la consola de la plataforma, no tocando el código: mientras no
haya clientes suficientes conviene enseñar una rebaja, y encender o apagar esa
rebaja no puede exigir un despliegue.

Lo que hay aquí es lo que **dice** la web. Lo que se **cobra** lo lleva la
pasarela, y son dos cosas distintas a propósito: cambiar el escaparate no
cambia lo que paga quien ya está dentro.
"""
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from thegrill.models import Tarifa, User

# Lo que se enseña mientras nadie haya dicho otra cosa.
POR_DEFECTO = 99.0
MESES_AL_AÑO = 12


class TarifaError(ValueError):
    """El precio que se quiere publicar no se puede publicar."""


@dataclass
class Publicada:
    """La tarifa tal y como la va a leer quien entre en la web."""
    currency: str
    normal: float                    # lo que cuesta un local al mes
    extra: float | None              # y cada local a partir del segundo
    price: float                     # lo que se paga hoy: la rebaja, si la hay
    on_sale: bool
    label: str
    until: date | None
    yearly_on: bool
    yearly_months: float

    @property
    def yearly(self) -> float | None:
        """Lo que cuesta el año pagado por adelantado."""
        if not self.yearly_on:
            return None
        return round(self.price * self.yearly_months, 2)

    @property
    def yearly_saving(self) -> float | None:
        """Y cuánto se ahorra frente a pagarlo mes a mes."""
        if not self.yearly_on:
            return None
        return round(self.price * (MESES_AL_AÑO - self.yearly_months), 2)

    @property
    def discount_pct(self) -> int | None:
        """Cuánto baja la rebaja, en porcentaje redondo, para el cartel."""
        if not self.on_sale or self.normal <= 0:
            return None
        return int(round((1 - self.price / self.normal) * 100))


def fila(session: Session) -> Tarifa:
    """La tarifa de la casa. Si todavía no hay ninguna, se crea la de partida."""
    row = session.query(Tarifa).order_by(Tarifa.id).first()
    if row is None:
        row = Tarifa(currency="EUR", per_outlet=POR_DEFECTO)
        session.add(row)
        session.flush()
    return row


def publicada(session: Session, on: date | None = None) -> Publicada:
    """Lo que hay que enseñar hoy en la web de venta.

    Una rebaja con fecha de fin se apaga sola el día siguiente: si hay que
    acordarse de quitarla a mano, un día se queda puesta.
    """
    row = fila(session)
    hoy = on or date.today()
    rebajada = bool(row.sale_on and row.sale_price
                    and (row.sale_until is None or hoy <= row.sale_until))
    return Publicada(
        currency=row.currency or "EUR",
        normal=round(row.per_outlet or POR_DEFECTO, 2),
        extra=round(row.extra_outlet, 2) if row.extra_outlet else None,
        price=round(row.sale_price if rebajada else (row.per_outlet or POR_DEFECTO), 2),
        on_sale=rebajada,
        label=(row.sale_label or "").strip(),
        until=row.sale_until if rebajada else None,
        yearly_on=bool(row.yearly_on),
        yearly_months=round(row.yearly_months or 10.0, 2))


def guardar(session: Session, user: User, *, currency: str, per_outlet: float,
            extra_outlet: float | None = None, sale_on: bool = False,
            sale_price: float | None = None, sale_label: str = "",
            sale_until: date | None = None, yearly_on: bool = False,
            yearly_months: float = 10.0) -> Tarifa:
    """Publica una tarifa nueva. Lo que no cuadra no se guarda."""
    if per_outlet <= 0:
        raise TarifaError("El precio por local tiene que ser mayor que cero")
    if extra_outlet is not None and extra_outlet <= 0:
        raise TarifaError("El precio de los locales de más tiene que ser mayor que cero")
    if sale_on:
        if not sale_price or sale_price <= 0:
            raise TarifaError("Una rebaja necesita su precio")
        # Una «rebaja» que sube el precio no es una rebaja, y en el escaparate
        # quedaría un precio tachado más bajo que el que se pide.
        if sale_price >= per_outlet:
            raise TarifaError("La rebaja tiene que quedar por debajo del precio normal")
        if sale_until is not None and sale_until < date.today():
            raise TarifaError("Esa rebaja termina antes de empezar")
    if yearly_on and not 1 <= yearly_months < MESES_AL_AÑO:
        raise TarifaError("El año se paga con entre una y once mensualidades")

    row = fila(session)
    row.currency = (currency or "EUR").upper()[:3]
    row.per_outlet = round(per_outlet, 2)
    row.extra_outlet = round(extra_outlet, 2) if extra_outlet else None
    row.sale_on = bool(sale_on)
    row.sale_price = round(sale_price, 2) if (sale_on and sale_price) else None
    row.sale_label = (sale_label or "").strip()[:64] or None
    row.sale_until = sale_until if sale_on else None
    row.yearly_on = bool(yearly_on)
    row.yearly_months = round(yearly_months, 2)
    row.updated_at = datetime.utcnow()
    row.updated_by = user.id
    session.flush()
    return row
