"""[01637] Lo que se gana, lo que hay que apartar y lo que queda limpio.

Un lomo que se compra a 300 € y se vende a 900 deja 600 de margen. Pero de
esos 600 no se lleva la casa 600: una parte se la lleva Hacienda por haberlos
ganado, y el día que llega el pago tiene que estar apartada. Un restaurante
que mira el margen bruto y gasta contra él va bien once meses y mal el
doceavo, siempre el mismo.

Así que el margen se parte en dos y las dos partes se enseñan juntas: lo que
hay que guardar y lo que queda de verdad.

**Qué no es esto.** No es el IVA: el IVA ya sale del precio de cada plato antes
de calcular nada, porque no es dinero de la casa —se cobra y se entrega—. Y no
es la declaración: el impuesto de verdad se paga sobre el beneficio de la
empresa entera, con el alquiler, las nóminas y la luz descontados, no sobre lo
que deja la carne. Esto es una reserva, y sirve para lo que sirve una reserva:
que el dinero esté cuando haya que pagarlo, no para saber cuánto se paga.

Dicho de otra manera: el número de abajo es **más** de lo que acabará
quedando, nunca menos. Como reserva, eso es exactamente lo que se quiere.
"""
from __future__ import annotations

from dataclasses import dataclass

from thegrill.web import exacto

# [01643] Nadie paga el doscientos por cien de lo que gana. Un número mayor que esto
# es un dedo, no un tipo impositivo, y se recorta antes de que salga en una
# pantalla como si fuera verdad.
TOPE = 100.0


@dataclass(frozen=True)
class Reparto:
    """[01638] El margen partido: lo ganado, lo que se aparta y lo que queda."""
    bruto: float
    impuesto: float
    limpio: float
    tipo: float

    @property
    def hay_impuesto(self) -> bool:
        """[01642] Si la casa ha dicho cuánto paga. Sin decirlo no se enseña nada."""
        return self.tipo > 0


def tipo_de(restaurant) -> float:
    """[01639] El tanto por ciento que paga esa casa. Sin poner, cero.

    Cero quiere decir «no lo he dicho», y entonces no se enseña ningún reparto:
    inventarse un tipo —el del país, el de la media— sería poner en pantalla un
    número que parece de la casa y no lo es, y sobre él se toman decisiones.
    """
    puesto = getattr(restaurant, "tax_pct", None) if restaurant is not None else None
    if not puesto or puesto <= 0:
        return 0.0
    return round(min(float(puesto), TOPE), 4)


def reparte(margen: float, tipo: float) -> Reparto:
    """[01640] Parte un margen en lo que hay que apartar y lo que queda.

    Un margen negativo —se vendió por debajo de lo que costó— no paga
    impuestos: no se ha ganado nada. Se devuelve entero como pérdida, que es lo
    que es, en vez de enseñar una devolución que nadie va a recibir por una
    pieza suelta.
    """
    # [01665] Al céntimo con la regla de siempre —la mitad sube—, y no con la que le
    # salga a la coma flotante: esto es dinero que alguien va a apartar, y la
    # cuenta tiene que dar lo mismo hecha con un lápiz.
    margen = exacto.euros(margen or 0.0) or 0.0
    # [01644] El tope también aquí y no solo al leerlo de la casa: esta función la
    # llama cualquiera con el número que tenga a mano, y un tipo del quinientos
    # por ciento dejaría el limpio en negativo con toda naturalidad.
    tipo = min(max(tipo or 0.0, 0.0), TOPE)
    if tipo <= 0 or margen <= 0:
        return Reparto(bruto=margen, impuesto=0.0, limpio=margen, tipo=max(tipo, 0.0))
    impuesto = exacto.euros(margen * tipo / 100.0) or 0.0
    # [01666] Lo que queda sale de restar, no de otra cuenta: así las dos partes suman
    # el margen **exacto** y no se pierde el céntimo entre las dos.
    return Reparto(bruto=margen, impuesto=impuesto,
                   limpio=round(margen - impuesto, 2), tipo=tipo)


def de_la_casa(restaurant, margen: float) -> Reparto:
    """[01641] El reparto de ese margen con el tipo que tenga puesto la casa."""
    return reparte(margen, tipo_de(restaurant))


# [01659] ------------------------------------------------- el IVA de lo que se compra
@dataclass(frozen=True)
class Soportado:
    """[01655] El IVA pagado en las compras de un periodo: lo que se puede descontar.

    Se llama soportado porque lo soporta quien compra, no quien vende. Al
    declarar, se resta del que se ha cobrado en las ventas, así que no es un
    gasto de la casa: es dinero adelantado a Hacienda que vuelve.
    """
    base: float          # lo que costó la carne, sin IVA
    iva: float           # lo que se pagó de IVA sobre esa base
    piezas: int          # de cuántas piezas sale

    @property
    def total(self) -> float:
        """[01657] Lo que se pagó de verdad por esa carne, IVA incluido."""
        return round(self.base + self.iva, 2)

    @property
    def hay(self) -> bool:
        """[01658] Si hay algo que descontar. Sin compras con IVA, no se enseña nada."""
        return self.iva > 0


def soportado_de(piezas) -> Soportado:
    """[01656] Suma el IVA de la compra de unas piezas.

    La base de cada pieza es lo que costó puesta en la cámara —el género más
    el transporte y la aduana— porque es sobre eso sobre lo que se paga el IVA
    de una importación. Una pieza sin IVA apuntado no suma nada: no se supone
    el tipo del país, que es distinto para la carne, para el pescado y para el
    vino, y suponerlo aquí acabaría en una declaración.
    """
    base = iva = 0.0
    cuantas = 0

    for pieza in piezas:
        tipo = getattr(pieza, "purchase_vat_pct", None)
        coste = getattr(pieza, "piece_cost_usd", None)
        if not tipo or tipo <= 0 or not coste or coste <= 0:
            continue
        cuantas += 1
        base += coste
        iva += coste * min(float(tipo), TOPE) / 100.0
    return Soportado(base=exacto.euros(base) or 0.0,
                     iva=exacto.euros(iva) or 0.0, piezas=cuantas)
