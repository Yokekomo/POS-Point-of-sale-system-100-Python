"""[01310] El peso se escribe en gramos, que son números enteros y no llevan coma.

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
    """[01311] Gramos con coma: quien lo escribió seguía pensando en kilos.

    «9,4» en una casilla de gramos son nueve gramos y pico, que no es carne
    que exista. Casi siempre son 9,4 kg mal puestos. No se adivina ni se
    corrige por la cara: se dice qué pasa y cuánto habría que escribir, que es
    lo único que no puede acabar en un peso equivocado guardado en silencio.
    """

    def __init__(self, escrito: str, como_kilos: float):
        """[01321] Lo escrito, y la cifra en gramos que hay que poner en su lugar."""
        # [01322] Lo que se tecleó, leído como lo que casi seguro quiso decir —kilos—,
        # y los gramos que habría que escribir para decir eso mismo. El aviso
        # no se queda en «está mal»: dice la cifra que hay que poner.
        self.escrito, self.como_kilos = escrito, como_kilos
        self.gramos = int(round(como_kilos * GRAMO))
        super().__init__(escrito)


def leer(raw: object, default: float | None = None) -> float | None:
    """[01312] Los gramos que se escribieron, devueltos en kilos.

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
    """[01313] Los kilos de la base, para volver a pintarlos en su casilla."""
    if kilos is None:
        return ""
    return str(int(round(kilos * GRAMO)))


def del_formulario(form, nombre: str, viejo: str | None = None,
                   default: float | None = None) -> float | None:
    """[01314] El peso de un campo, en kilos, mirando también el nombre de antes.

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


class Falta(ValueError):
    """[01315] La casilla del peso vino vacía. Sin peso no hay apunte que valga."""


def de_dos(gramos: object, kilos: object, default: float | None = None) -> float | None:
    """[01316] El peso, venga con el nombre de ahora o con el de antes.

    Lo mismo que `del_formulario`, para las rutas que reciben los campos ya
    sueltos en vez de un formulario entero.
    """
    if gramos not in (None, ""):
        return leer(gramos, default=default)
    if kilos not in (None, ""):
        return exacto.leer(kilos, default=default)
    return default


# [01323] --------------------------------------------- de gramos a lo que sea
#
# Un huevo pesa 55 g y un litro de aceite, 916. Con ese número puesto en el
# ingrediente, todo se puede escribir en gramos y guardarse en la unidad en la
# que vive el stock: la cámara sigue contando bandejas y garrafas, que es como
# llegan y como se cuentan, y la receta se escribe pesando, que es lo único
# honesto cuando un huevo L pesa 68 g y uno M, 58.
#
# Lo que no se hace es adivinar ese número. Sin él no se convierte nada.
GRAMOS_POR_KILO = float(GRAMO)


def se_pesa(ingrediente) -> bool:
    """[01317] ¿Se puede escribir este ingrediente en gramos?

    Si su unidad ya es el kilo, sí y sin más cuentas. Si no, solo cuando
    alguien ha dicho lo que pesa una unidad.
    """
    if ingrediente is None:
        return False
    unidad = getattr(getattr(ingrediente, "unit", None), "value", None)
    if unidad == "KG":
        return True
    return bool(getattr(ingrediente, "grams_per_unit", None))


def por_unidad(ingrediente) -> float | None:
    """[01318] Los gramos que pesa una unidad de este ingrediente, si se sabe.

    El kilo manda sobre lo que haya escrito en la casilla: un kilo pesa mil
    gramos y eso no lo cambia nadie. Si en un ingrediente que ya va en kilos
    alguien escribe 250 —pensando que era «gramos por ración», que es lo que
    parece—, 5000 g de entrecot entraban en cámara como **veinte kilos**.
    Cuatro veces la carne, y con su coste detrás.
    """
    if ingrediente is None:
        return None
    unidad = getattr(getattr(ingrediente, "unit", None), "value", None)
    if unidad == "KG":
        return GRAMOS_POR_KILO
    puesto = getattr(ingrediente, "grams_per_unit", None)
    return float(puesto) if puesto else None


def en_su_unidad(kilos: float | None, ingrediente) -> float | None:
    """[01319] El peso escrito, pasado a la unidad en la que vive el stock.

    Entra lo que devuelve `leer` —kilos, que es como sale de una casilla de
    gramos— y sale la cantidad en la unidad del ingrediente: 110 g de huevo
    son dos huevos justos si uno pesa 55. La división deja decimales a
    propósito: media garrafa de aceite es media garrafa, y redondear a la
    unidad de arriba sería inventarse medio litro.
    """
    if kilos is None:
        return None
    cuanto = por_unidad(ingrediente)
    if not cuanto:
        return kilos               # sin el número, lo escrito es lo que vale
    return kilos * GRAMOS_POR_KILO / cuanto


def a_gramos(cantidad: float | None, ingrediente) -> float | None:
    """[01320] Y de vuelta, para volver a pintar en la casilla lo que ya estaba."""
    if cantidad is None:
        return None
    cuanto = por_unidad(ingrediente)
    if not cuanto:
        return None
    return cantidad * cuanto
