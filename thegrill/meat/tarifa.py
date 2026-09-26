"""[00657] El precio que se publica en la web de venta.

Se cambia desde la consola de la plataforma, no tocando el código: mientras no
haya clientes suficientes conviene enseñar una rebaja, y encender o apagar esa
rebaja no puede exigir un despliegue.

Lo que hay aquí es lo que **dice** la web. Lo que se **cobra** lo lleva la
pasarela, y son dos cosas distintas a propósito: cambiar el escaparate no
cambia lo que paga quien ya está dentro.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from thegrill.models import Tarifa, User
from thegrill.web import exacto

MESES_AL_AÑO = 12

# [01753] --------------------------------------------------- un precio por mercado
#
# Había uno solo, y una moneda sola, para todo el planeta. Eso no es una
# simplificación: es enseñarle 149 € a un asador de Burgos y los mismos 149 € a
# un grupo hotelero de Dubái, donde un precio bajo descalifica antes de que
# nadie lea lo que hace el programa. Y al revés en Buenos Aires.
#
# Los números de partida no me los he inventado: salen de mirar lo que cobra
# quien vende algo parecido en cada sitio —en España, el más barato de los
# comparables está en 149 al mes y no reparte el coste de un primal entre sus
# cortes, ni pesa la maduración, ni cuadra el residuo—. Son **el punto de
# partida**, no la verdad: se cambian desde la consola sin tocar el código, que
# es para lo que existe esta tabla.
#
# El local de más va a la mitad a propósito. El trabajo de dar de alta una casa
# se hace una vez; el segundo local no cuesta el doble de servir y sí cuesta el
# doble de convencer.
#
# Y el año por adelantado, diez mensualidades: dos meses de regalo. No es
# generosidad, es que la baja de febrero —la del que se apuntó en enero con
# ganas— es la que más duele, y quien ha pagado el año no se va en el segundo mes.


@dataclass(frozen=True)
class Mercado:
    """[01754] Un sitio donde se vende, con su moneda y su precio de partida."""
    codigo: str
    moneda: str
    por_local: float
    local_extra: float
    idiomas: tuple[str, ...] = ()     # qué idioma lo sugiere, si no se ha elegido
    meses_año: float = 10.0


MERCADOS: dict[str, Mercado] = {
    "ES": Mercado("ES", "EUR", 149.0, 75.0, ("es",)),
    "EU": Mercado("EU", "EUR", 149.0, 75.0, ("fr", "de", "nl", "hu")),
    "UK": Mercado("UK", "GBP", 179.0, 90.0, ()),
    "US": Mercado("US", "USD", 249.0, 125.0, ("en",)),
    # El Golfo paga tres veces lo de España y allí un precio bajo es una señal
    # mala. En dólares y no en dirhams porque es la moneda en la que se habla de
    # precio entre países del Golfo.
    "GULF": Mercado("GULF", "USD", 349.0, 175.0, ("ar",)),
    # En dólares a propósito: las monedas de la región se mueven, y un precio
    # que hay que revisar cada trimestre no lo revisa nadie.
    "LATAM": Mercado("LATAM", "USD", 149.0, 75.0, ()),
    "AU": Mercado("AU", "AUD", 379.0, 190.0, ()),
}

MERCADO_POR_DEFECTO = "EU"

# Lo que cuesta un local al mes si alguien pregunta sin decir dónde está.
POR_DEFECTO = MERCADOS[MERCADO_POR_DEFECTO].por_local

# [01755] La oferta del mes: la mitad, y se apaga sola el último día del mes.
#
# Va encendida de serie porque mientras no haya clientes hace más falta que el
# margen. Y **sin fecha escrita**: cuando no la hay, la oferta llega hasta el
# final del mes en curso y se renueva sola. Una rebaja que hay que acordarse de
# renovar cada treinta días se queda apagada un martes cualquiera; una que hay
# que acordarse de quitar se queda puesta para siempre. Con esto, ni una cosa
# ni la otra: la quita quien escribe una fecha de fin.
OFERTA_PCT = 50


class TarifaError(ValueError):
    """[00658] El precio que se quiere publicar no se puede publicar."""


@dataclass
class Publicada:
    """[00659] La tarifa tal y como la va a leer quien entre en la web."""
    mercado: str
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
        """[00663] Lo que cuesta el año pagado por adelantado."""
        if not self.yearly_on:
            return None
        return exacto.eur(self.price * self.yearly_months)

    @property
    def yearly_saving(self) -> float | None:
        """[00664] Y cuánto se ahorra frente a pagarlo mes a mes."""
        if not self.yearly_on:
            return None
        return exacto.eur(self.price * (MESES_AL_AÑO - self.yearly_months))

    @property
    def discount_pct(self) -> int | None:
        """[00665] Cuánto baja la rebaja, en porcentaje redondo, para el cartel."""
        if not self.on_sale or self.normal <= 0:
            return None
        return int(round((1 - self.price / self.normal) * 100))


def es_mercado(codigo: str | None) -> bool:
    """[01756] Si eso es un mercado de los que hay."""
    return bool(codigo) and codigo.upper() in MERCADOS


def de_donde(*, elegido: str | None = None, lang: str | None = None,
             pais: str | None = None) -> str:
    """[01757] Qué precio enseñarle a quien acaba de entrar.

    Por orden: lo que haya elegido esa persona, el país de su casa si ya es
    cliente, y si no, lo que sugiere su idioma. El idioma es el peor de los tres
    y aun así es mejor que nada: un español que entra en español es casi seguro
    de España, y un árabe, casi seguro del Golfo.

    Lo que **no** se hace es adivinar por la dirección de red. Dos motivos: el
    dato se equivoca con cualquier red de empresa, y decirle a alguien un precio
    distinto por dónde parece estar sin que pueda cambiarlo es exactamente lo
    que uno no quiere que le hagan. Por eso siempre hay un selector a la vista.
    """
    if es_mercado(elegido):
        return elegido.upper()
    if pais:
        limpio = pais.strip().upper()
        for codigo, mercado in MERCADOS.items():
            if limpio in (codigo, mercado.moneda):
                return codigo
        # Nombres de país escritos a mano, que es como llegan del formulario.
        for aguja, codigo in PAISES.items():
            if aguja in limpio:
                return codigo
    if lang:
        for codigo, mercado in MERCADOS.items():
            if lang in mercado.idiomas:
                return codigo
    return MERCADO_POR_DEFECTO


# Los países escritos a mano que llegan del formulario de acceso, con lo que
# caben en varios idiomas. No pretende ser una lista completa: lo que no esté
# aquí cae en el mercado de partida y la persona lo cambia con el selector.
PAISES = {
    "ESPA": "ES", "SPAIN": "ES",
    "UNITED KINGDOM": "UK", "REINO UNIDO": "UK", "ENGLAND": "UK", "SCOTLAND": "UK",
    "UNITED STATES": "US", "ESTADOS UNIDOS": "US", "USA": "US", "EE.UU": "US",
    "AUSTRALIA": "AU", "NEW ZEALAND": "AU", "NUEVA ZELANDA": "AU",
    "EMIRAT": "GULF", "SAUDI": "GULF", "ARABIA": "GULF", "QATAR": "GULF",
    "CATAR": "GULF", "KUWAIT": "GULF", "BAHR": "GULF", "OMAN": "GULF", "OMÁN": "GULF",
    "MEXIC": "LATAM", "MÉXIC": "LATAM", "ARGENTIN": "LATAM", "CHILE": "LATAM",
    "COLOMBIA": "LATAM", "PERU": "LATAM", "PERÚ": "LATAM", "BRASIL": "LATAM",
    "BRAZIL": "LATAM", "URUGUAY": "LATAM", "ECUADOR": "LATAM", "PANAM": "LATAM",
}


def fila(session: Session, mercado: str | None = None) -> Tarifa:
    """[00660] La tarifa de un mercado. Si todavía no hay ninguna, se crea la de partida.

    Una fila sin mercado escrito es la del mercado de partida: así una base que
    ya estaba funcionando con la tabla vieja —cuando solo había un precio para
    todo el planeta— no se queda sin precio al actualizar.
    """
    codigo = (mercado or MERCADO_POR_DEFECTO).upper()
    if codigo not in MERCADOS:
        codigo = MERCADO_POR_DEFECTO
    row = (session.query(Tarifa).filter(Tarifa.mercado == codigo)
           .order_by(Tarifa.id).first())
    if row is None and codigo == MERCADO_POR_DEFECTO:
        row = (session.query(Tarifa).filter(Tarifa.mercado.is_(None))
               .order_by(Tarifa.id).first())
        if row is not None:
            row.mercado = codigo
            session.flush()
    if row is None:
        base = MERCADOS[codigo]
        row = Tarifa(mercado=codigo, currency=base.moneda, per_outlet=base.por_local,
                     extra_outlet=base.local_extra,
                     yearly_on=True, yearly_months=base.meses_año,
                     # La oferta del mes, encendida y sin fecha: hasta fin de mes
                     # y renovándose sola. Ver el comentario de `OFERTA_PCT`.
                     sale_on=True, sale_price=exacto.eur(base.por_local * (100 - OFERTA_PCT) / 100))
        session.add(row)
        session.flush()
    return row


def fin_de_mes(hoy: date) -> date:
    """[01758] El último día del mes en el que estamos."""
    if hoy.month == 12:
        return date(hoy.year, 12, 31)
    return date(hoy.year, hoy.month + 1, 1) - timedelta(days=1)


def publicada(session: Session, mercado: str | None = None,
              on: date | None = None) -> Publicada:
    """[00661] Lo que hay que enseñar hoy en la web de venta, en su mercado.

    Una rebaja con fecha de fin se apaga sola el día siguiente: si hay que
    acordarse de quitarla a mano, un día se queda puesta.

    Y una rebaja **sin** fecha de fin es la oferta del mes: llega hasta el
    último día del mes en curso y se renueva sola. Ver `OFERTA_PCT`.
    """
    row = fila(session, mercado)
    hoy = on or date.today()
    hasta = row.sale_until or fin_de_mes(hoy)
    rebajada = bool(row.sale_on and row.sale_price and hoy <= hasta)
    return Publicada(
        mercado=row.mercado or MERCADO_POR_DEFECTO,
        currency=row.currency or "EUR",
        normal=exacto.eur(row.per_outlet or POR_DEFECTO),
        extra=exacto.eur(row.extra_outlet) if row.extra_outlet else None,
        price=exacto.eur(row.sale_price if rebajada else (row.per_outlet or POR_DEFECTO)),
        on_sale=rebajada,
        label=(row.sale_label or "").strip(),
        until=hasta if rebajada else None,
        yearly_on=bool(row.yearly_on),
        yearly_months=round(row.yearly_months or 10.0, 2))


def guardar(session: Session, user: User, *, mercado: str, currency: str, per_outlet: float,
            extra_outlet: float | None = None, sale_on: bool = False,
            sale_price: float | None = None, sale_label: str = "",
            sale_until: date | None = None, yearly_on: bool = False,
            yearly_months: float = 10.0) -> Tarifa:
    """[00662] Publica una tarifa nueva. Lo que no cuadra no se guarda."""
    if per_outlet <= 0:
        raise TarifaError("El precio por local tiene que ser mayor que cero")
    if extra_outlet is not None and extra_outlet <= 0:
        raise TarifaError("El precio de los locales de más tiene que ser mayor que cero")
    if sale_on:
        if not sale_price or sale_price <= 0:
            raise TarifaError("Una rebaja necesita su precio")
        # [00667] Una «rebaja» que sube el precio no es una rebaja, y en el escaparate
        # quedaría un precio tachado más bajo que el que se pide.
        if sale_price >= per_outlet:
            raise TarifaError("La rebaja tiene que quedar por debajo del precio normal")
        if sale_until is not None and sale_until < date.today():
            raise TarifaError("Esa rebaja termina antes de empezar")
    if yearly_on and not 1 <= yearly_months < MESES_AL_AÑO:
        raise TarifaError("El año se paga con entre una y once mensualidades")

    if not es_mercado(mercado):
        raise TarifaError("Ese mercado no existe")
    row = fila(session, mercado)
    row.currency = (currency or "EUR").upper()[:3]
    row.per_outlet = exacto.eur(per_outlet)
    row.extra_outlet = exacto.eur(extra_outlet) if extra_outlet else None
    row.sale_on = bool(sale_on)
    row.sale_price = exacto.eur(sale_price) if (sale_on and sale_price) else None
    row.sale_label = (sale_label or "").strip()[:64] or None
    row.sale_until = sale_until if sale_on else None
    row.yearly_on = bool(yearly_on)
    row.yearly_months = round(yearly_months, 2)
    row.updated_at = datetime.utcnow()
    row.updated_by = user.id
    session.flush()
    return row
