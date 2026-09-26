"""[01351] Lo que puede ser y lo que no. Un número imposible no se guarda.

Un registro sanitario mal relleno es peor que no tenerlo: queda completo,
queda firmado, y es mentira. El día de la inspección nadie lo mira dos veces,
y el día del brote no sirve de nada. Por eso aquí hay dos cosas distintas y no
se confunden:

**Imposible** es un número que no puede describir lo que pasó. Un solomillo no
pesa 1370 kg y un termómetro de cámara no marca 240 °C: son el dedo, que ha
resbalado en la tecla. Eso no se guarda. Se para la pantalla y se dice lo que
se esperaba, con el rango delante, para que quien lo escribió lo vea sin
pensar.

**Fuera de norma** es un número que sí puede ser y que está mal. Carne
refrigerada que baja del camión a 12 °C es un hecho, y es un hecho grave: hay
que **guardarlo**, porque es la prueba de que ese camión vino caliente, y hay
que avisar al responsable el mismo día. Rechazarlo sería borrar la única
anotación que importa.

Las bandas de llegada las pone la casa, y conviene decir de dónde salen las
que trae de serie: **no de una ley**. El Reglamento (CE) 852/2004, que es el que
le aplica a un restaurante, no contiene ni una sola cifra de temperatura; las
del 853/2004 —3 °C para despojos, 7 °C para el resto— obligan al matadero, y su
artículo 1(5)(a) dice expresamente que ese reglamento no se aplica al comercio
al por menor. Quien fija el número es el plan de autocontrol de la casa, con su
guía sectorial y su norma nacional delante.

Así que 5 °C y −12 °C son lo que son: defectos prudentes, más estrictos que
cualquier cifra que se encuentre por ahí, y puestos para que la casa los apriete
o los afloje en su configuración. Los límites de lo imposible tampoco vienen de
ninguna ley: vienen de lo que cabe en una cámara y de lo que marca una sonda.
"""
from __future__ import annotations

from dataclasses import dataclass

from thegrill.models import Storage
from thegrill.web.i18n import t

# [01363] --------------------------------------------------------------- lo imposible
# Mínimo y máximo de cada magnitud. Anchos a propósito: esto no es el control
# de calidad, es el filtro del dedo gordo. Lo que pasa por aquí y aun así es
# raro lo dice el aviso de más abajo, no el rechazo.
PESO_PIEZA = (0.1, 250.0)        # de una pularda a un cuarto de vacuno entero
PESO_CORTE = (0.0, 250.0)        # contar cero es contar; 1370 no es contar
TEMPERATURA = (-60.0, 60.0)      # fuera de esto no hay sonda, hay un teclazo
PRECIO_KG = (0.01, 2000.0)       # el wagyu sube, pero no tanto
GRAMOS_RACION = (1.0, 5000.0)

# [01364] ---------------------------------------------------------- lo que es raro
# Posible, pero merece que alguien lo mire. No para la pantalla: deja el
# número y levanta un aviso.
PIEZA_PESADA = 80.0              # más que esto ya no lo sube una persona

# [01365] --------------------------------------------------------- la banda de la casa
# A cuánto tiene que bajar del camión cada cosa. La carne que llega fuera de
# esto se apunta igual —es la prueba de que ese camión vino como vino— y sale
# en los avisos del manager el mismo día.
#
# Refrigerado a 5 °C: por debajo de cualquier cifra que se encuentre escrita
# —el 7 °C del 853/2004 obliga al matadero, no a la casa— y cómodo de cumplir
# con una cámara que funcione.
#
# Y congelado hasta −12 °C. Ese número **no sale de ninguna norma**: ni el
# 852/2004 ni el 853/2004 dan una cifra para congelar, y Australia lo dice con
# todas las letras —«frozen hard», y el número lo pacta la casa con su
# proveedor—. Está aquí por lo que significa: a cero grados una pieza no está
# congelada, está descongelándose, y aceptarla sin avisar es firmar que llegó
# bien.
# El suelo del congelado no es una norma: es un detector de sondas rotas. En
# congelado, más frío nunca es un peligro —lo que estropea la carne es que
# suba, no que baje—, y un contenedor va a −18 o −22 °C de serie. Con el suelo
# en −20, cada camión de congelado normal disparaba una alarma crítica
# diciendo «se ha congelado en el viaje», que además no significa nada: ya
# venía congelado. Una alarma que salta siempre enseña a no mirarlas, y eso es
# peor que no tenerla.
#
# Se deja en −30 para lo que sí es un aviso de verdad: una sonda que devuelve
# −80, o un dedo que escribe −220 en vez de −22. Quien quiera apretarlo en su
# casa lo tiene en la configuración.
LEGAL = {
    Storage.CHILLED: (-5.0, 5.0),
    Storage.FROZEN: (-30.0, -12.0),
}

# [01366] Cómo se llama cada límite en la casa, para leerlo y para escribirlo.
CAMPOS = {
    Storage.CHILLED: ("chilled_min_c", "chilled_max_c"),
    Storage.FROZEN: ("frozen_min_c", "frozen_max_c"),
}


def banda(almacen: Storage, restaurant=None) -> tuple[float, float] | None:
    """[01352] Entre qué dos temperaturas tiene que bajar del camión, en esta casa.

    Lo que diga la casa manda sobre lo de serie, límite a límite: una que
    aprieta el máximo de refrigerado a 4 °C y deja el mínimo como estaba no
    tiene por qué volver a escribir los dos.
    """
    de_serie = LEGAL.get(almacen)
    if de_serie is None or restaurant is None:
        return de_serie
    minimo, maximo = de_serie
    campo_min, campo_max = CAMPOS[almacen]
    suyo_min = getattr(restaurant, campo_min, None)
    suyo_max = getattr(restaurant, campo_max, None)
    try:
        if suyo_min is not None:
            minimo = float(suyo_min)
        if suyo_max is not None:
            maximo = float(suyo_max)
    except (TypeError, ValueError):
        return de_serie
    # [01367] Del revés no mide nada: si alguien cruza los dos números, se enderezan.
    return (minimo, maximo) if minimo <= maximo else (maximo, minimo)


class FueraDeRango(ValueError):
    """[01353] El número no puede describir lo que pasó: no se guarda."""


@dataclass(frozen=True)
class Aviso:
    """[01354] El número sí puede ser, y está mal. Se guarda y se avisa."""
    code: str
    message: str


def _dentro(valor: float, limites: tuple[float, float]) -> bool:
    """[01355] Si el valor cae dentro de esos dos límites, los dos incluidos."""
    return limites[0] <= valor <= limites[1]


def _numero(valor: float) -> str:
    """[01356] Sin ceros de adorno: 1370 y no 1370.000000."""
    return f"{valor:.10g}"


def peso_pieza(kg: float | None, lang: str = "es", serial: str = "") -> None:
    """[01357] El peso de un primal al entrar. Levanta si es imposible."""
    if kg is None or _dentro(kg, PESO_PIEZA):
        return
    raise FueraDeRango(t(lang, "rango.peso", serial=serial or "—", kg=_numero(kg),
                         min=_numero(PESO_PIEZA[0]), max=_numero(PESO_PIEZA[1])))


def peso_corte(kg: float | None, lang: str = "es") -> None:
    """[01358] Kilos de un corte, una merma, un recuento. Levanta si es imposible."""
    if kg is None or _dentro(kg, PESO_CORTE):
        return
    raise FueraDeRango(t(lang, "rango.peso_corte", kg=_numero(kg),
                         min=_numero(PESO_CORTE[0]), max=_numero(PESO_CORTE[1])))


def temperatura(grados: float | None, lang: str = "es") -> None:
    """[01359] Lo que marca la sonda. Levanta si no lo puede marcar ninguna sonda."""
    if grados is None or _dentro(grados, TEMPERATURA):
        return
    raise FueraDeRango(t(lang, "rango.temp", c=_numero(grados),
                         min=_numero(TEMPERATURA[0]), max=_numero(TEMPERATURA[1])))


def precio_kg(eur: float | None, lang: str = "es") -> None:
    """[01360] El precio de la factura. Levanta si es imposible."""
    if eur is None or _dentro(eur, PRECIO_KG):
        return
    raise FueraDeRango(t(lang, "rango.precio", eur=_numero(eur),
                         min=_numero(PRECIO_KG[0]), max=_numero(PRECIO_KG[1])))


def gramos_racion(gramos: float | None, lang: str = "es") -> None:
    """[01361] Los gramos que se ponen en el plato. Levanta si es imposible."""
    if gramos is None or _dentro(gramos, GRAMOS_RACION):
        return
    raise FueraDeRango(t(lang, "rango.gramos", g=_numero(gramos),
                         min=_numero(GRAMOS_RACION[0]), max=_numero(GRAMOS_RACION[1])))


def llegada(grados: float | None, almacen: Storage, serial: str,
            kg: float | None = None, lang: str = "es",
            restaurant=None) -> list[Aviso]:
    """[01362] Lo que hay que mirar de una pieza que acaba de bajar del camión.

    Devuelve avisos, no excepciones: todo lo que llega aquí ya pasó el filtro
    de lo imposible, así que es verdad y se guarda. Lo que sale de esta
    función es lo que el manager ve hoy en sus alertas, no dentro de un mes en
    un cuadre.
    """
    fuera: list[Aviso] = []
    suya = banda(almacen, restaurant)
    if grados is not None and suya and not _dentro(grados, suya):
        frio = almacen == Storage.FROZEN
        caliente = grados > suya[1]
        clave = ("haccp.arrival_warm" if caliente else "haccp.arrival_cold")
        fuera.append(Aviso(clave, t(
            lang, "alert." + ("frozen_warm" if frio and caliente else
                              "chilled_warm" if caliente else "arrival_cold"),
            serial=serial, c=_numero(grados),
            limit=_numero(suya[1] if caliente else suya[0]))))
    if kg is not None and kg > PIEZA_PESADA:
        fuera.append(Aviso("meat.heavy_piece", t(
            lang, "alert.heavy_piece", serial=serial, kg=_numero(kg),
            limit=_numero(PIEZA_PESADA))))
    return fuera
