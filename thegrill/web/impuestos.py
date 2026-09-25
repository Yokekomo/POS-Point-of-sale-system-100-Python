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
    margen = round(margen or 0.0, 2)
    # [01644] El tope también aquí y no solo al leerlo de la casa: esta función la
    # llama cualquiera con el número que tenga a mano, y un tipo del quinientos
    # por ciento dejaría el limpio en negativo con toda naturalidad.
    tipo = min(max(tipo or 0.0, 0.0), TOPE)
    if tipo <= 0 or margen <= 0:
        return Reparto(bruto=margen, impuesto=0.0, limpio=margen, tipo=max(tipo, 0.0))
    impuesto = round(margen * tipo / 100.0, 2)
    return Reparto(bruto=margen, impuesto=impuesto,
                   limpio=round(margen - impuesto, 2), tipo=tipo)


def de_la_casa(restaurant, margen: float) -> Reparto:
    """[01641] El reparto de ese margen con el tipo que tenga puesto la casa."""
    return reparte(margen, tipo_de(restaurant))
