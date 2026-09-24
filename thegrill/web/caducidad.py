"""[01053] Cuánto dura una cosa desde que deja de estar como estaba.

Un lomo congelado caduca dentro de diez meses. El mismo lomo, sacado del arcón
el martes, caduca el viernes. Es la misma carne y son dos fechas distintas, y
la que manda es siempre la segunda.

Esto no era un detalle: el programa le dejaba al descongelado la fecha del
congelador, y como la rotación va por fecha —lo que antes caduca, antes sale—
lo descongelado se iba **al final de la cola**. O sea, exactamente al revés de
lo que hay que hacer: la bandeja que hay que gastar esta semana esperando
detrás de la que aguanta hasta el año que viene, hasta que alguien la
encuentra mala. Y en el papel todo cuadraba.

Tres días de serie, que es lo que pone casi todo plan de autocontrol para
carne descongelada que se mantiene a temperatura de refrigeración. La casa
pone los suyos en su configuración, porque eso lo decide su plan y no un
programa.

Un apunte sobre el día y la hora: la fecha de consumo es un día y no un
instante, porque es lo que se escribe en una etiqueta y lo que mira quien abre
la cámara.

Una regla, y es la que hace que no se pueda estropear: **la fecha nueva nunca
puede ser más tarde que la que ya tenía**. Descongelar no alarga nada. Si la
etiqueta decía que caducaba pasado mañana, caduca pasado mañana.
"""
from __future__ import annotations

from datetime import date, timedelta

# [01057] Los días que aguanta lo descongelado en una casa que no ha dicho los suyos.
POR_DEFECTO = 3

# [01058] Dos semanas. Más allá de esto ya no es carne descongelada, es otra cosa, y
# el tope está para que un dedo no le dé un mes de vida a una bandeja.
MAXIMO = 14


def dias(restaurant) -> int:
    """[01054] Cuántos días aguanta lo descongelado en esta casa."""
    if restaurant is None:
        return POR_DEFECTO
    cuantos = getattr(restaurant, "thaw_days", None)
    if cuantos is None:
        return POR_DEFECTO
    try:
        return max(1, min(MAXIMO, int(cuantos)))
    except (TypeError, ValueError):
        return POR_DEFECTO


def tras_descongelar(actual: date | None, on: date, restaurant=None) -> date:
    """[01055] La fecha de consumo de algo que se acaba de sacar del congelador.

    `actual` es la que traía. Se devuelve la más cercana de las dos, porque
    descongelar no alarga la vida de nada.
    """
    nueva = on + timedelta(days=dias(restaurant))
    return min(actual, nueva) if actual else nueva


def de_la_casa(session, restaurant_id: int | None):
    """[01056] La casa, para quien solo tiene el número a mano."""
    from thegrill.models import Restaurant
    return session.get(Restaurant, restaurant_id) if restaurant_id else None
