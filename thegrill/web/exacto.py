"""Repartir sin perder un céntimo ni un gramo. Nunca, ni en el peor caso.

Repartir el coste de un primal entre sus cortes es una división, y una
división casi nunca cae exacta: 300,80 € entre tres cortes son 100,2666… cada
uno. Redondeando cada parte por su cuenta, la suma da 300,80 € **casi**
siempre — y ese «casi» es lo que aparece tres meses después en un cuadre que
no sale y que nadie sabe explicar.

Aquí no se redondea cada parte y se cruzan los dedos. Se hace lo que hace
cualquier repartidor honrado:

1. Se pasa todo a la unidad más pequeña que existe de verdad —el céntimo, el
   gramo—, que son números enteros y con enteros no hay error.
2. Se le da a cada uno su parte entera.
3. Lo que sobra —que siempre es menos de una unidad por parte— se reparte de
   uno en uno, empezando por quien más cerca estuvo de merecer el siguiente.

Así la suma de las partes es **idéntica** al total. No parecida: idéntica.
Esto se llama reparto por restos mayores, y es lo que se usa para repartir
escaños; aquí se reparten céntimos, que para un restaurante es más serio.

Sobre la coma flotante: el programa guarda los números como `float`, y un
`float` no puede representar 300,80 exactamente. Pero sí representa sin error
cualquier número entero hasta nueve mil billones, así que mientras las cuentas
se hagan **en enteros** y solo se convierta en los bordes, el resultado es
exacto. La coma flotante es el camión, no la báscula.
"""
from __future__ import annotations

CENTIMOS = 100          # un euro
GRAMOS = 1000           # un kilo


def a_enteros(valor: float, por: int = CENTIMOS) -> int:
    """De euros a céntimos, o de kilos a gramos. Con redondeo al más cercano."""
    return int(round((valor or 0.0) * por))


def a_decimal(entero: int, por: int = CENTIMOS) -> float:
    """De vuelta. Se divide una sola vez y al final, no por el camino."""
    return entero / por


def repartir(total: int, pesos: list[float]) -> list[int]:
    """Reparte `total` unidades entre los pesos dados, sin perder ninguna.

    Devuelve enteros cuya suma es **exactamente** `total`. Si todos los pesos
    son cero, se reparte a partes iguales, que es lo único razonable cuando no
    hay nada que diga quién merece más.

    Lo que sobra va por resto mayor, y en el empate gana el primero: así dos
    despieces iguales dan el mismo resultado siempre, que es lo que permite
    repetir un cálculo y que salga lo mismo.
    """
    if not pesos:
        return []
    suma = sum(pesos)
    if suma <= 0:
        # Sin pesos, a partes iguales y el resto a los primeros.
        base, sobra = divmod(total, len(pesos))
        return [base + (1 if i < sobra else 0) for i in range(len(pesos))]

    exactos = [total * p / suma for p in pesos]
    partes = [int(x) if x >= 0 else -int(-x) for x in exactos]   # hacia cero
    sobra = total - sum(partes)
    if sobra:
        # Quien más parte decimal tiene, primero. El signo importa: repartiendo
        # un número negativo —una devolución, un ajuste a la baja— lo que sobra
        # también es negativo y va a quien menos le falta.
        orden = sorted(range(len(pesos)),
                       key=lambda i: (-(exactos[i] - partes[i]) if sobra > 0
                                      else (exactos[i] - partes[i]), i))
        paso = 1 if sobra > 0 else -1
        for i in orden[:abs(sobra)]:
            partes[i] += paso
    return partes


def repartir_dinero(total_eur: float, pesos: list[float]) -> list[float]:
    """El coste de una pieza entre sus cortes, al céntimo y sin perder nada."""
    centimos = repartir(a_enteros(total_eur, CENTIMOS), pesos)
    return [a_decimal(c, CENTIMOS) for c in centimos]


def repartir_kilos(total_kg: float, pesos: list[float]) -> list[float]:
    """Unos kilos entre varias partes, al gramo y sin perder ninguno."""
    gramos = repartir(a_enteros(total_kg, GRAMOS), pesos)
    return [a_decimal(g, GRAMOS) for g in gramos]


def cuadra(partes: list[float], total: float, por: int = CENTIMOS) -> bool:
    """¿Suman las partes exactamente el total, en su unidad más pequeña?"""
    return sum(a_enteros(p, por) for p in partes) == a_enteros(total, por)
