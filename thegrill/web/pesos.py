"""El peso se escribe en gramos, que son números enteros y no llevan coma.

Una báscula de cocina da «9,435 kg» y un teclado de móvil no tiene coma en la
fila de números: hay que cambiar de teclado, buscar el separador y acertar
cuál de los dos quiere este programa. Con guantes, con el camión en el muelle
y con la pieza en la otra mano, eso son tres toques y una duda por bolsa.

En gramos no hay duda posible: **9435**. Cuatro dígitos seguidos, teclado
numérico, ni un separador que pueda entenderse al revés. Es el mismo truco
que se lleva usando toda la vida con el dinero —se cuenta en céntimos y se
divide una sola vez al final— y aquí además se cuenta en la unidad en la que
ya reparte `exacto`: el gramo.

Lo que se guarda siguen siendo kilos, porque en kilos están las básculas, los
albaranes, el 853/2004 y los diez mil apuntes que ya hay en la base. Lo que
cambia es **lo que se teclea**. La conversión pasa una sola vez, aquí, en el
borde.

Debajo de cada casilla va el eco —«9435 g» → «9,435 kg»— y ese eco no es un
adorno: es lo que deja ver un cero de más antes de darle al botón. Un cero de
más en gramos es un error de diez veces, y un error de diez veces en un
inventario de carne son varios miles de euros. El eco es la única defensa que
funciona sin leer nada, porque la cifra en kilos es la que esa persona lleva
toda la vida mirando y sabe de memoria cuánto pesa un lomo.
"""
from __future__ import annotations

from . import exacto

GRAMO = exacto.GRAMOS          # los gramos que tiene un kilo


class ConDecimales(ValueError):
    """Gramos con coma: quien lo escribió seguía pensando en kilos.

    «9,4» en una casilla de gramos son nueve gramos y pico, que no es carne
    que exista. Casi siempre son 9,4 kg mal puestos. No se adivina ni se
    corrige por la cara: se dice qué pasa y cuánto habría que escribir, que es
    lo único que no puede acabar en un peso equivocado guardado en silencio.
    """

    def __init__(self, escrito: str, como_kilos: float):
        # Lo que se tecleó, leído como lo que casi seguro quiso decir —kilos—,
        # y los gramos que habría que escribir para decir eso mismo. El aviso
        # no se queda en «está mal»: dice la cifra que hay que poner.
        self.escrito, self.como_kilos = escrito, como_kilos
        self.gramos = int(round(como_kilos * GRAMO))
        super().__init__(escrito)


def leer(raw: object, default: float | None = None) -> float | None:
    """Los gramos que se escribieron, devueltos en kilos.

    Vacío devuelve `default`. Lo que no es un número levanta `NoEsUnNumero`,
    igual que en cualquier otra casilla. Y lo que trae coma levanta
    `ConDecimales`, que sabe decir en qué se queda.

    El separador de miles sí se admite —«9.435» y «9 435» son 9435 gramos—
    porque es lo que sale de copiar una cifra de un albarán, y ahí no hay
    ambigüedad: en gramos, tres dígitos detrás del separador nunca son
    decimales de nada.
    """
    valor = exacto.leer(raw, default=None, decimales=0)
    if valor is None:
        return default
    entero = round(valor)
    if abs(valor - entero) > 1e-9:
        raise ConDecimales(str(raw)[:32], valor)
    return entero / GRAMO


def escribir(kilos: float | None) -> str:
    """Los kilos de la base, para volver a pintarlos en su casilla."""
    if kilos is None:
        return ""
    return str(int(round(kilos * GRAMO)))


def del_formulario(form, nombre: str, viejo: str | None = None,
                   default: float | None = None) -> float | None:
    """El peso de un campo, en kilos, mirando también el nombre de antes.

    Las casillas de peso se llamaban `kg` y ahora se llaman `g`. Eso importa
    más de lo que parece: un teléfono sin cobertura guarda el formulario tal
    cual en su cola y lo manda cuando vuelve la señal, que pueden ser horas o
    días. Si ese envío viejo trae `kg=9,4` y el servidor nuevo lo leyera como
    gramos, apuntaría nueve gramos y pico sin decir nada. Mil veces menos, en
    silencio, y descubierto en el cuadre de fin de mes.

    Por eso el nombre cambia: un envío de antes no trae `g` y no puede
    confundirse con uno de ahora. Y por eso se sigue leyendo `kg` cuando
    llega: significaba kilos, se lee como kilos y la pieza queda bien
    apuntada. Es el nombre el que dice en qué unidad viene, no la casilla.
    """
    escrito = form.get(nombre)
    if escrito not in (None, ""):
        return leer(escrito, default=default)
    if viejo:
        de_antes = form.get(viejo)
        if de_antes not in (None, ""):
            return exacto.leer(de_antes, default=default)
    return default
