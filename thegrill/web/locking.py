"""Dos personas a la vez sobre la misma carne.

En una casa nadie trabaja solo: mientras uno cuenta la cámara del local, otro
despieza en el obrador y un tercero da de alta la recepción del camión. Casi
siempre tocan cosas distintas y no pasa nada. El problema es el rato en que
tocan **la misma**: la misma pieza, la misma hoja de inventario, el mismo lote.

Leer, pensar y después escribir no sirve aquí. Entre la lectura y la escritura
cabe la otra persona, y entonces las dos creen que la pieza estaba entera y las
dos la despiezan: de un lomo de nueve kilos entran dieciocho en cámara y el
dinero se duplica. El número cuadra en cada pantalla por separado y está mal en
la base de datos.

La regla de este módulo es una sola: **el que escribe comprueba en la misma
orden**. No se pregunta «¿está entera?» y luego se escribe «cortada»; se
escribe «ponla cortada *si sigue entera*» y la base de datos dice si se ha
cogido o no. La que llega segunda se entera de que llega segunda, y se lo
decimos con nombres y apellidos en vez de dejarla pisar el trabajo de la otra.
"""
import random
import time

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class Busy(RuntimeError):
    """Otra persona se ha adelantado con esto mismo, hace un momento."""


def claim(session: Session, model, ident: int, expected: dict, values: dict) -> bool:
    """Cambia una fila **solo si sigue como la dejamos**. Dice si fue nuestra.

    Es un `UPDATE … WHERE` con el estado de antes metido en el `WHERE`: la base
    de datos lo resuelve dentro de su propio candado, así que de dos personas
    que lo intenten a la vez se lo lleva exactamente una. La otra recibe
    `False` y ya decide quien llama qué le cuenta al usuario.
    """
    taken = (session.query(model).filter_by(id=ident, **expected)
             .update(values, synchronize_session=False))
    if taken:
        # La fila en memoria se quedó con lo de antes; se pone al día para que
        # quien siga trabajando con el objeto vea lo que hay en la base.
        obj = session.get(model, ident)
        for field, value in values.items():
            setattr(obj, field, value)
    return bool(taken)


def take(session: Session, model, ident: int, field: str, qty: float,
         floor: float = 0.0) -> bool:
    """Descuenta kilos de una fila **solo si quedan**. Dice si se pudieron sacar.

    Restar en Python lo que se acaba de leer es la manera de sacar diez kilos
    de un lote de seis: dos personas leen seis, las dos restan cinco y las dos
    guardan uno. Aquí la resta la hace la base de datos sobre lo que hay de
    verdad en ese instante, y si no llega, no sale nada.

    El margen de una milésima de gramo no es un descuido: gastar un lote entero
    es lo más normal del mundo, y los decimales de una máquina no cuadran al
    último dígito. Sin ese margen, tirar los últimos cuatro kilos y ochocientos
    de un lote de cuatro kilos y ochocientos saldría a veces rechazado.
    """
    column = getattr(model, field)
    moved = (session.query(model)
             .filter(model.id == ident, column >= qty + floor - 1e-9)
             .update({field: column - qty}, synchronize_session=False))
    if moved:
        obj = session.get(model, ident)
        session.refresh(obj, [field])
    return bool(moved)


def retry(session: Session, escribir, arreglar, intentos: int = 6):
    """Escribe, y si el número ya estaba cogido, lo cambia y vuelve a escribir.

    Un número repetido no se ve mirando antes: los dos miraron cuando estaba
    libre y guardaron en el mismo segundo. Quien dice que no es la base de
    datos, al escribir, y ahí es donde hay que responderle.

    Deshacer aquí es deshacer **todo lo de esta pantalla**, que es justo lo que
    se quiere: una recepción o un despiece a medias no sirve de nada. Por eso
    `escribir` tiene que montar lo suyo entero cada vez, sin contar con lo que
    quedó de la vuelta anterior.

    Y entre intento e intento se espera un suspiro distinto cada vez: si los
    dos volvieran a la vez, volverían a chocar con el siguiente número, y
    después con el siguiente.
    """
    for intento in range(intentos):
        try:
            return escribir()
        except IntegrityError:
            session.rollback()
            if intento == intentos - 1:
                raise
            time.sleep(random.uniform(0.005, 0.05))
            arreglar()
    raise Busy("No hay manera de coger un número libre")
