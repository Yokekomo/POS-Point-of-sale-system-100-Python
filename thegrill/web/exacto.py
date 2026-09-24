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

import re

CENTIMOS = 100          # un euro
GRAMOS = 1000           # un kilo
GRAMOS_DECIMALES = 3    # las cifras que da una báscula de cocina
CENTIMOS_DECIMALES = 2  # las que da el dinero


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


# ------------------------------------------------------- guardar sin milésimas
def euros(valor: float | None) -> float | None:
    """Deja un importe en céntimos justos. Una factura no dice 543,3701."""
    if valor is None:
        return None
    return a_decimal(a_enteros(valor, CENTIMOS), CENTIMOS)


def kilos(valor: float | None) -> float | None:
    """Deja un peso en gramos justos. Una báscula no da milésimas de gramo."""
    if valor is None:
        return None
    return a_decimal(a_enteros(valor, GRAMOS), GRAMOS)


# ----------------------------------------------------- leer lo que escriben
# Un número escrito a mano no viene en un solo idioma. El mismo peso se teclea
# «1,250» en Madrid, «1.250» en Londres y «1 250» en Budapest, y el teclado
# del móvil pone el separador que le da la gana. Leerlo con un
# `replace(",", ".")` funciona hasta el día que alguien escribe los miles: ese
# día «1.250 €» entran como 1,25 € y nadie lo ve.
#
# Las reglas son, por orden, las que usa cualquiera que lea números de gente:
#
# 1. Si aparecen los dos separadores, el último es el decimal. «1.234,56» y
#    «1,234.56» son el mismo dinero.
# 2. Si aparece uno solo pero más de una vez, es el de los miles: «1.234.567»
#    no tiene decimales.
# 3. Si aparece uno solo y una sola vez, hay que decidir, y decide el campo.
#    Tres cifras detrás es a la vez lo que escribe una báscula de gramos y lo
#    que escribe quien separa los miles; sabiendo cuántos decimales mide ese
#    campo se acaba la duda. Una báscula da tres, así que «1,250 kg» es kilo y
#    cuarto. El dinero da dos, así que «1.250 €» son mil doscientos cincuenta.
#    Por eso `decimales` no es un detalle: es lo que distingue un kilo de una
#    tonelada.
#
# Detrás de un cero no hay miles —nadie escribe «0.500» por quinientos—, y un
# número con cuatro cifras delante tampoco los lleva, porque entonces se
# habrían escrito también: «1234.567» no es «1.234.567».

_ESPACIOS = re.compile(r"[\s\u00a0\u202f\u2009\u2007']")


class NoEsUnNumero(ValueError):
    """Ahí no hay un número. Lleva escrito lo que había, que es lo único que
    le sirve a quien lo escribió: «4 C» se arregla mirando la C."""

    def __init__(self, escrito: str):
        self.escrito = escrito
        super().__init__(f"no es un número: {escrito!r}")


def leer(raw: object, default: float | None = None,
         decimales: int = GRAMOS_DECIMALES) -> float | None:
    """El número que quiso escribir quien lo escribió, venga como venga.

    `decimales` es la resolución del campo: 3 para kilos, 2 para dinero, 0
    para contar unidades. Solo se usa para resolver el caso dudoso.

    Levanta `ValueError` si aquello no es un número, igual que `float`.
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise NoEsUnNumero(str(raw)[:32])
    if isinstance(raw, (int, float)):
        return float(raw)
    texto = _ESPACIOS.sub("", str(raw))
    if not texto:
        return default
    negativo = texto[0] in "-\u2212"
    cuerpo = texto[1:] if texto[0] in "+-\u2212" else texto
    if not cuerpo or any(c not in "0123456789,." for c in cuerpo):
        raise NoEsUnNumero(str(raw)[:32])

    comas, puntos = cuerpo.count(","), cuerpo.count(".")
    if comas and puntos:                       # 1. el último manda
        decimal = "," if cuerpo.rfind(",") > cuerpo.rfind(".") else "."
    elif comas + puntos > 1:                   # 2. repetido: son los miles
        decimal = ""
    elif comas + puntos == 1:                  # 3. uno solo: decide el campo
        sep = "," if comas else "."
        corte = cuerpo.rfind(sep)
        delante, detras = cuerpo[:corte], len(cuerpo) - corte - 1
        son_miles = (detras == 3 and delante.isdigit() and 1 <= len(delante) <= 3
                     and not delante.startswith("0")
                     and decimales < GRAMOS_DECIMALES)
        decimal = "" if son_miles else sep
    else:
        decimal = ""

    limpio = cuerpo
    for sep in (",", "."):
        if sep != decimal:
            limpio = limpio.replace(sep, "")
    if decimal:
        limpio = limpio.replace(decimal, ".")
    if not any(c.isdigit() for c in limpio):
        raise NoEsUnNumero(str(raw)[:32])
    valor = float(limpio)
    return -valor if negativo else valor


def es_justo(valor: float | None, por: int = CENTIMOS) -> bool:
    """¿Es ese número un múltiplo exacto de su unidad más pequeña?

    Se compara con margen porque la coma flotante no guarda 300,80 sino
    300,79999999999998863… Lo que se pregunta no es si el número es bonito,
    sino si al pasarlo a enteros y volver sale el mismo: si sale, el entero se
    puede recuperar siempre y las cuentas se hacen con él.
    """
    if valor is None:
        return True
    return abs(valor * por - round(valor * por)) < 1e-6


# Qué se guarda en cada tabla y con qué unidad. Escrito a mano: una lista
# adivinada por el nombre de la columna deja fuera justo la que falla.
#
# Aquí solo van los **importes**: lo que alguien paga, cobra o apunta en el
# libro. Los **precios por kilo** NO están y no pueden estar: 158,22 € entre
# 5,6 kg son 28,253571… €/kg, y redondear eso a 28,25 y volver a multiplicar
# pierde dos céntimos por lote. Un ratio se guarda con todos sus decimales y
# no se usa nunca para reconstruir un total; para eso está el importe.
# Y el coste de un apunte del libro tampoco está, aunque lo parezca. Una
# salida de cámara vale `kilos × precio por kilo`: es un derivado, no lo que
# nadie paga. Cuadrándolo al céntimo línea a línea, un lote de 20 kg
# descontado en cincuenta veces dejaba hasta medio euro de diferencia entre
# el libro y lo que queda en el lote —y el residuo del cuadre no llegaba
# nunca a cero por mucho que se contara—. El céntimo se redondea donde se
# enseña el total, no en cada apunte.
A_CENTIMOS = {
    "primals": ("piece_cost_usd",),
}
# Y en los pesos, la misma distinción que en el dinero, por el mismo motivo.
#
# Aquí van los pesos **medidos**: lo que ha dicho una báscula. Una báscula da
# gramos, así que una pieza no pesa 8,4295 kg ni lo ha pesado nunca nadie.
#
# Los pesos **calculados** no están y no pueden estar. Una ración de 160 g con
# un 95 % de rendimiento consume 168,42 g. Cuadrarlo a 168 pierde cuatro
# décimas de gramo por ración —cuatrocientos gramos cada mil— y siempre hacia
# el mismo lado: eso no es exactitud, es un sesgo. Lo que se calcula se guarda
# con todos sus decimales, y lo que se reparte se reparte en gramos enteros
# con `repartir_kilos`, que es otra cosa y esa sí conserva.
A_GRAMOS = {
    "primals": ("weight_kg", "received_kg", "aging_start_kg"),
    "ingredient_lots": ("qty",),
    "meat_count_lines": ("counted_kg",),
    "primal_weighings": ("kg", "previous_kg", "kept_kg", "waste_kg"),
}


def revisar(session, restaurant_id: int | None = None, tope: int = 40) -> list[str]:
    """Busca en la base de datos números que no caen en céntimo ni en gramo.

    Es el vigilante de la exactitud: si algún camino nuevo guarda 8,4295 kg o
    12,3456 €, aquí sale con su tabla, su columna y su valor. Un número así no
    rompe nada el día que se escribe; rompe el cuadre tres meses después.
    """
    from sqlalchemy import text

    fallos: list[str] = []
    for tabla, columnas, unidad, nombre in (
            [(t, c, CENTIMOS, "céntimo") for t, c in A_CENTIMOS.items()]
            + [(t, c, GRAMOS, "gramo") for t, c in A_GRAMOS.items()]):
        for columna in columnas:
            donde = f" WHERE restaurant_id = {int(restaurant_id)}" if restaurant_id else ""
            try:
                filas = session.execute(text(
                    f'SELECT id, "{columna}" FROM "{tabla}"{donde}')).all()
            except Exception:                                # noqa: BLE001
                continue
            for fila_id, valor in filas:
                if not es_justo(valor, unidad):
                    fallos.append(f"{tabla}.{columna} #{fila_id} = {valor!r} "
                                  f"(no cae en {nombre})")
                    if len(fallos) >= tope:
                        return fallos
    return fallos


# ------------------------------------------------- que no se pueda guardar mal
def _cuadrar(objeto) -> None:
    """Deja los importes de ese objeto en céntimos y sus pesos en gramos."""
    tabla = getattr(objeto, "__tablename__", None)
    if tabla is None:
        return
    for columna in A_CENTIMOS.get(tabla, ()):
        valor = getattr(objeto, columna, None)
        if valor is not None and not es_justo(valor, CENTIMOS):
            setattr(objeto, columna, euros(valor))
    for columna in A_GRAMOS.get(tabla, ()):
        valor = getattr(objeto, columna, None)
        if valor is not None and not es_justo(valor, GRAMOS):
            setattr(objeto, columna, kilos(valor))


_enganchado = False


def enganchar(session_class) -> None:
    """Hace imposible guardar un importe con milésimas o un peso con miligramos.

    Podría redondearse en los trece sitios donde hoy se escribe un apunte, pero
    entonces el catorceavo —el que se escriba el mes que viene— volvería a
    guardar 87,332066 €. Así que se hace donde no se puede olvidar: justo antes
    de que la sesión escriba, se repasa lo que va a guardar.

    Redondea solo lo que no cae justo, así que a una casa que ya funciona no le
    toca una sola fila mientras nadie la modifique. Los precios por kilo no se
    tocan nunca: son ratios y necesitan sus decimales.
    """
    global _enganchado
    if _enganchado:
        # Se llama una vez por arranque de motor, y en las pruebas eso son
        # cientos: sin esto se apilaría un vigilante por cada uno y el mismo
        # objeto se repasaría cien veces antes de cada guardado.
        return
    _enganchado = True

    from sqlalchemy import event

    @event.listens_for(session_class, "before_flush")
    def _antes_de_guardar(session, _contexto, _instancias):   # noqa: ANN001
        for objeto in session.new:
            _cuadrar(objeto)
        for objeto in session.dirty:
            _cuadrar(objeto)
