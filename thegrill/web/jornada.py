"""Cuándo es hoy. Que no es lo mismo que qué día es.

Una cocina no cierra a medianoche. A la una y media se sigue sirviendo, a las
dos se cierra la caja y a las dos y media alguien apunta la merma del
servicio. Todo eso es **la noche del sábado**, aunque el reloj ya diga domingo.
Si el programa lo apunta en domingo pasan tres cosas, y las tres son malas: el
consumo del sábado sale corto, el del domingo sale largo, y el food cost de
los dos días queda mal para siempre, porque nadie va a volver a mirarlo.

Por eso la casa elige a qué hora se cierra el día. De serie a las tres de la
mañana, que es cuando ya no queda nadie en ninguna cocina; quien cierra antes
o después lo cambia en su configuración. Lo que se apunta antes de esa hora es
del día anterior.

Y antes de la hora está el sitio. `date.today()` es el reloj del servidor, que
puede estar en cualquier parte: una casa en Budapest apuntando a las 23:50 lo
veía caer en el día siguiente porque el servidor iba en UTC. La casa tiene su
zona horaria desde el primer día y hasta ahora no la leía nadie.

Las dos cosas juntas son una resta y una fecha:

    lo que marca el reloj de la casa − la hora de cierre → el día de trabajo

Una casa que cierra a las tres, a las 02:30 del domingo está en el sábado; a
las 03:01 ya está en el domingo. Una que pone cero cierra a medianoche, como
una oficina.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# La hora de cierre de una casa que no ha dicho la suya. Es un valor de
# módulo y no una constante escrita en el modelo para que la casa que viene de
# antes —la que tiene la columna a vacío— no cambie de día de repente el día
# que se actualiza el programa: se decide aquí, en un sitio, y se ve.
POR_DEFECTO = 3

# Más allá del mediodía ya no es «la noche anterior», es otra cosa. El tope
# está para que un dedo no convierta un turno de mañana en el día de ayer.
MAXIMO = 11

UTC = ZoneInfo("UTC")


def zona(nombre: str | None) -> ZoneInfo:
    """La zona horaria de la casa. Si el nombre no existe, UTC y a seguir."""
    try:
        return ZoneInfo(nombre) if nombre else UTC
    except Exception:                       # noqa: BLE001  (zona desconocida)
        return UTC


def corte(restaurant) -> int:
    """A qué hora cierra el día esta casa."""
    if restaurant is None:
        return POR_DEFECTO
    hora = getattr(restaurant, "day_cut_hour", None)
    if hora is None:
        return POR_DEFECTO
    try:
        return max(0, min(MAXIMO, int(hora)))
    except (TypeError, ValueError):
        return POR_DEFECTO


def ahora(restaurant, momento: datetime | None = None) -> datetime:
    """Qué hora es en la casa, no en el servidor."""
    momento = momento or datetime.now(timezone.utc)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(zona(getattr(restaurant, "timezone", None)))


def de(restaurant, momento: datetime | None = None) -> date:
    """El día de trabajo al que pertenece ese instante."""
    return (ahora(restaurant, momento) - timedelta(hours=corte(restaurant))).date()


def hoy(session, restaurant_id: int | None, momento: datetime | None = None) -> date:
    """El día de trabajo de esa casa ahora mismo. Es lo que se usa por ahí."""
    from thegrill.models import Restaurant
    restaurant = session.get(Restaurant, restaurant_id) if restaurant_id else None
    return de(restaurant, momento)


def del_usuario(session, user, momento: datetime | None = None) -> date:
    """Lo mismo, cuando lo que hay a mano es la persona."""
    return hoy(session, getattr(user, "restaurant_id", None), momento)


# Lo que puede haber esperado un apunte en la cola de un teléfono. La cola
# reintenta cada pocos segundos y en cada pantalla que se abre, así que una
# semana es holgadísimo: lo que venga fechado más atrás no es un turno que
# tardó en salir, es un reloj mal puesto.
MARGEN = timedelta(days=7)


def apuntado(session, user, cuando: str | None, ahora: datetime | None = None) -> date:
    """El día de trabajo del instante en que se **escribió**, no del envío.

    Un recuento apuntado a las 23:50 dentro de la cámara y mandado a las 00:10,
    cuando el teléfono vuelve a tener señal, quedaba fechado al día siguiente.
    El turno de noche entero cambiaba de día y ni el consumo ni el food cost de
    ninguno de los dos volvían a cuadrar, y nadie podía saber por qué.

    El sello lo pone el teléfono, así que no se cree a ciegas: se acepta si cae
    dentro de la última semana y no está en el futuro. Un reloj mal puesto —que
    los hay— archivaría media cámara en un mes que ya se cerró, y eso no se
    arregla mirando.
    """
    ahora = ahora or datetime.now(timezone.utc)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=timezone.utc)
    if not cuando:
        return hoy(session, getattr(user, "restaurant_id", None), ahora)
    try:
        momento = datetime.fromisoformat(str(cuando).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return hoy(session, getattr(user, "restaurant_id", None), ahora)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    if momento > ahora or (ahora - momento) > MARGEN:
        return hoy(session, getattr(user, "restaurant_id", None), ahora)
    return hoy(session, getattr(user, "restaurant_id", None), momento)


def etiqueta(restaurant) -> str:
    """«03:00», para enseñarlo en la configuración."""
    return f"{corte(restaurant):02d}:00"
