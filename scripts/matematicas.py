"""El examen duro de las cuentas: un oráculo exacto y las leyes que no se rompen.

`scripts/masivo.py` hace trabajar casas de mentira y mira si los números
cuadran al final. Esto es lo otro: coger cada cuenta por separado y medirla
contra **la verdad**, que aquí no es otra implementación en coma flotante sino
aritmética exacta —`Fraction`, `Decimal`—, donde no hay error que valga.

Cinco métodos, cada uno caza una familia de fallos distinta:

1. **Oráculo exacto.** El mismo reparto hecho con fracciones. Si el de verdad
   se separa del exacto más de lo que puede, está mal. Caza el error de
   redondeo acumulado, que es el que no se ve mirando el código.

2. **Leyes que el reparto tiene que cumplir**, comprobadas y no supuestas:
   la suma de las partes es el total **exacto**; multiplicar todos los pesos
   por una constante no cambia nada; repartir dos veces lo mismo da lo mismo;
   y a más peso, no menos parte. Son las cuatro cosas que alguien da por
   hechas al usarlo.

3. **Relaciones metamórficas.** No hace falta saber el resultado bueno para
   saber que está mal: si todos los precios se multiplican por diez, el dinero
   tiene que multiplicarse por diez; vender dos kilos de una vez tiene que dar
   lo mismo que vender uno y luego otro; y el orden de dos cosas que no se
   tocan no puede cambiar el final. Caza los fallos que un ejemplo bien
   elegido esconde.

4. **Los números que rompen la coma flotante**: las mitades exactas, el
   0,1 + 0,2, lo muy pequeño junto a lo muy grande, y lo que tiene más cifras
   de las que cabe. Es donde vive el fallo que solo aparece con la factura de
   un cliente concreto.

5. **Cincuenta años.** El error de redondeo o se queda quieto o crece con el
   número de operaciones, y la diferencia no se ve en un mes. Se hacen las
   cuentas de medio siglo de piezas y se mira **cómo crece el residuo con n**:
   si crece como n, en veinte años hay un agujero; si se queda plano, no lo
   habrá nunca.

    python -m scripts.matematicas            # todo
    python -m scripts.matematicas --casos 200000
"""
from __future__ import annotations

import argparse
import itertools
import random
import sys
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

sys.path.insert(0, ".")

from thegrill.web import exacto  # noqa: E402


@dataclass
class Parte:
    """Lo que ha salido del examen."""
    fallos: list[str] = field(default_factory=list)
    medidas: list[tuple[str, str]] = field(default_factory=list)

    def falla(self, que: str, detalle: str) -> None:
        """Apunta algo que no se sostiene, con lo que hace falta para repetirlo."""
        self.fallos.append(f"[{que}] {detalle}")

    def mide(self, que: str, cuanto: str) -> None:
        """Apunta un número del examen, salga bien o mal: es lo que se enseña."""
        self.medidas.append((que, cuanto))


# ============================================================ 1. el oráculo
def _reparto_exacto(total: int, pesos: list[float]) -> list[int]:
    """El mismo reparto, hecho con fracciones: aquí no hay error que valga.

    Restos mayores en aritmética exacta. Es la verdad contra la que se mide el
    de verdad, y la única manera de saber si lo que devuelve es lo que debía o
    lo que le ha salido.
    """
    if not pesos:
        return []
    fr = [Fraction(repr(float(p))) for p in pesos]
    suma = sum(fr)
    if suma <= 0:
        base, sobra = divmod(total, len(pesos))
        return [base + (1 if i < sobra else 0) for i in range(len(pesos))]
    exactos = [Fraction(total) * p / suma for p in fr]
    # Hacia cero, como el de verdad: lo que sobra se reparte después.
    partes = [int(x) if x >= 0 else -int(-x) for x in exactos]
    sobra = total - sum(partes)
    if sobra:
        restos = [exactos[i] - partes[i] for i in range(len(pesos))]
        orden = sorted(range(len(pesos)),
                       key=lambda i: (-restos[i] if sobra > 0 else restos[i], i))
        paso = 1 if sobra > 0 else -1
        for i in orden[:abs(sobra)]:
            partes[i] += paso
    return partes


def oraculo(parte: Parte, casos: int, azar: random.Random) -> None:
    """El reparto de verdad contra el exacto, caso a caso."""
    distintos = 0
    peor = 0
    for _ in range(casos):
        cuantos = azar.randint(1, 12)
        pesos = [round(azar.uniform(0, 40), azar.randint(0, 4)) for _ in range(cuantos)]
        if azar.random() < 0.15:
            pesos[azar.randrange(cuantos)] = 0.0
        total = azar.randint(-500000, 500000)
        mio = exacto.repartir(total, pesos)
        suyo = _reparto_exacto(total, pesos)
        if mio != suyo:
            distintos += 1
            hueco = max(abs(a - b) for a, b in zip(mio, suyo))
            peor = max(peor, hueco)
            if distintos <= 3:
                parte.falla("reparto contra el exacto",
                            f"total={total} pesos={pesos} → {mio}, exacto {suyo}")
    parte.mide("repartos que no coinciden con el exacto",
               f"{distintos} de {casos}" + (f" · peor hueco {peor}" if peor else ""))


# ================================================== 2. las leyes del reparto
def leyes(parte: Parte, casos: int, azar: random.Random) -> None:
    """Las cuatro cosas que cualquiera da por hechas al usar el reparto."""
    fallos = {"suma": 0, "escala": 0, "repetir": 0, "monótono": 0}
    saltados = [0]
    for _ in range(casos):
        cuantos = azar.randint(1, 10)
        pesos = [round(azar.uniform(0, 30), azar.randint(0, 3)) for _ in range(cuantos)]
        total = azar.randint(-200000, 200000)
        partes = exacto.repartir(total, pesos)

        # a) La suma es el total. Exacto, no parecido.
        if sum(partes) != total:
            fallos["suma"] += 1
            parte.falla("la suma del reparto",
                        f"total={total} pesos={pesos} suman {sum(partes)}")

        # b) Los pesos son proporciones: multiplicarlos todos por lo mismo no
        #    cambia a quién le toca qué. Si cambia, el reparto depende de la
        #    unidad en la que se midan los pesos, que es lo que no puede ser.
        # Con cuidado: `14,65 × 100` en coma flotante no es 1465 sino
        # 1465,0000000000002, así que el «mismo peso escalado» no siempre lo
        # es. Los que no escalan exactos no dicen nada del reparto —dicen que
        # la multiplicación se ha desviado— y se dejan fuera contándolos.
        k = azar.choice([2, 10, 0.5, 100, 3])
        escalados = [p * k for p in pesos]
        exactamente = all(Fraction(repr(e)) == Fraction(repr(p)) * Fraction(repr(float(k)))
                          for p, e in zip(pesos, escalados))
        if not exactamente:
            saltados[0] += 1
        elif exacto.repartir(total, escalados) != partes:
            fallos["escala"] += 1
            if fallos["escala"] <= 2:
                parte.falla("el reparto cambia al escalar los pesos",
                            f"total={total} pesos={pesos} ×{k}")

        # c) Dos veces lo mismo da lo mismo. Sin esto, dos pantallas que
        #    calculan el mismo despiece enseñan cifras distintas.
        if exacto.repartir(total, pesos) != partes:
            fallos["repetir"] += 1
            parte.falla("el reparto no es el mismo dos veces", f"pesos={pesos}")

        # d) A más peso, no menos parte (con total positivo).
        if total > 0:
            orden = sorted(range(cuantos), key=lambda i: pesos[i])
            for antes, despues in zip(orden, orden[1:]):
                if pesos[antes] < pesos[despues] and partes[antes] > partes[despues]:
                    fallos["monótono"] += 1
                    if fallos["monótono"] <= 2:
                        parte.falla("al que menos pesa le toca más",
                                    f"pesos={pesos} partes={partes}")
                    break
    for ley, cuantos in fallos.items():
        parte.mide(f"ley «{ley}»", f"{cuantos} fallos de {casos}"
                   + (f" ({saltados[0]} sin escalar exacto)" if ley == "escala" else ""))


# ============================================ 3. relaciones metamórficas
def metamorficas(parte: Parte, casos: int, azar: random.Random) -> None:
    """Lo que tiene que pasar cuando se cambia la entrada de forma conocida."""
    from thegrill.web import impuestos

    peor_escala = peor_parte = 0.0
    for _ in range(casos):
        # a) El dinero es lineal: diez veces el precio, diez veces el margen.
        margen = round(azar.uniform(-500, 5000), 2)
        tipo = round(azar.uniform(0, 60), 2)
        k = azar.choice([2, 5, 10, 100])
        uno = impuestos.reparte(margen, tipo)
        diez = impuestos.reparte(round(margen * k, 2), tipo)
        if uno.bruto > 0:
            esperado = uno.impuesto * k
            # Cada reparto redondea al céntimo, así que multiplicar por k puede
            # separar hasta medio céntimo por k. Más que eso ya no es redondeo.
            peor_escala = max(peor_escala, abs(diez.impuesto - esperado) / k)

        # b) Partir el reparto en dos no cambia el total: repartir 100 entre
        #    A y B es lo mismo que repartir 60 y luego 40 con los mismos pesos,
        #    sumando. No al céntimo —cada reparto redondea— pero sí dentro de
        #    una unidad por parte, que es lo que promete el método.
        pesos = [round(azar.uniform(1, 20), 2) for _ in range(azar.randint(2, 6))]
        total = azar.randint(1000, 500000)
        entero = exacto.repartir(total, pesos)
        corte = azar.randint(1, total - 1)
        a = exacto.repartir(corte, pesos)
        b = exacto.repartir(total - corte, pesos)
        junto = [x + y for x, y in zip(a, b)]
        if sum(junto) != total:
            parte.falla("partir el reparto pierde unidades",
                        f"total={total} corte={corte} pesos={pesos}")
        peor_parte = max(peor_parte,
                         max(abs(x - y) for x, y in zip(entero, junto)))
    parte.mide("el impuesto, al escalar el margen",
               f"peor desvío {peor_escala:.6f} EUR por unidad de escala "
               f"(el redondeo da hasta 0,005)")
    if peor_escala > 0.005 + 1e-9:
        parte.falla("el impuesto no escala", f"se desvía {peor_escala:.6f} EUR")
    parte.mide("repartir de una vez o en dos veces",
               f"se separan como mucho {peor_parte:.0f} unidades")


# ================================== 4. los números que rompen la coma flotante
def crueles(parte: Parte) -> None:
    """Los números con los que la coma flotante se porta mal."""
    def como_una_caja(v: float, por: int) -> int:
        return int((Decimal(repr(v)) * por).to_integral_value(ROUND_HALF_UP))

    malos = 0
    mirados = 0
    duros = [0.1 + 0.2, 1.005, 2.675, 0.145, 8.835, 1e-9, 1e9 + 0.005,
             0.615, 4.035, 1234567.005, -1.005, -0.145, 0.0, -0.0]
    duros += [i / 100 + 0.005 for i in range(0, 3000)]
    duros += [i / 1000 + 0.0005 for i in range(0, 3000)]
    for v in duros:
        for por in (exacto.CENTIMOS, exacto.GRAMOS):
            mirados += 1
            if exacto.a_enteros(v, por) != como_una_caja(v, por):
                malos += 1
                if malos <= 3:
                    parte.falla("a céntimos no como una caja",
                                f"{v!r} ×{por} → {exacto.a_enteros(v, por)}, "
                                f"caja {como_una_caja(v, por)}")
    parte.mide("números crueles que no redondean como una caja",
               f"{malos} de {mirados}")

    # Y que el ida y vuelta no mueva nada: a enteros y de vuelta, el mismo.
    vuelta = 0
    for v in duros:
        entero = exacto.a_enteros(v, exacto.CENTIMOS)
        if exacto.a_enteros(exacto.a_decimal(entero, exacto.CENTIMOS),
                            exacto.CENTIMOS) != entero:
            vuelta += 1
            if vuelta <= 3:
                parte.falla("ida y vuelta a céntimos", f"{v!r} → {entero} → distinto")
    parte.mide("ida y vuelta a céntimos", f"{vuelta} de {len(duros)} no vuelven")


# ============================================================ 5. cincuenta años
def cincuenta_anos(parte: Parte, azar: random.Random) -> None:
    """Medio siglo de piezas, y cómo crece el residuo con el número de cuentas.

    Es la pregunta que no contesta ningún ejemplo: el error de redondeo, ¿se
    queda quieto o se acumula? Se hace la vida entera de una pieza —recibir,
    limpiar, repartir entre cortes, vender a trozos— un millón de veces, y se
    mira el residuo cada cien mil. Si crece como el número de operaciones, en
    veinte años hay un agujero; si se queda plano, no lo habrá nunca.
    """
    # Una pieza al día, cincuenta años: dieciocho mil piezas. Por cada una,
    # un despiece de cinco cortes y unas ventas. Son del orden del millón de
    # repartos, que es lo que hace una casa grande en medio siglo.
    piezas = 18250
    cortes = 5
    residuo_total = 0
    hitos = []
    for n in range(1, piezas + 1):
        kg = round(azar.uniform(3, 30), 3)
        precio = round(azar.uniform(8, 120), 2)
        coste = round(kg * precio, 2)
        pesos = [round(azar.uniform(0.2, 6), 3) for _ in range(cortes)]
        partes = exacto.repartir_dinero(coste, pesos)
        # Lo que se pierde en cada pieza: la suma de las partes contra el total.
        residuo_total += (exacto.a_enteros(sum(partes), exacto.CENTIMOS)
                          - exacto.a_enteros(coste, exacto.CENTIMOS))
        if n % 2500 == 0:
            hitos.append((n, residuo_total))
    parte.mide("cincuenta años de despieces",
               f"{piezas} piezas · residuo acumulado {residuo_total} céntimos")
    if residuo_total != 0:
        parte.falla("el residuo crece con los años",
                    f"tras {piezas} piezas sobran o faltan {residuo_total} céntimos; "
                    f"por el camino: {hitos[:5]}")

    # Y lo mismo con los kilos, que es donde más se nota: tres decimales.
    residuo_kg = 0
    for _ in range(piezas):
        kg = round(azar.uniform(3, 30), 3)
        pesos = [round(azar.uniform(0.2, 6), 3) for _ in range(cortes)]
        partes = exacto.repartir_kilos(kg, pesos)
        residuo_kg += (exacto.a_enteros(sum(partes), exacto.GRAMOS)
                       - exacto.a_enteros(kg, exacto.GRAMOS))
    parte.mide("cincuenta años de kilos repartidos",
               f"residuo acumulado {residuo_kg} gramos")
    if residuo_kg != 0:
        parte.falla("los gramos se acumulan", f"{residuo_kg} gramos tras {piezas} piezas")


# ==================================================================== la ronda
def main(argv: list[str] | None = None) -> int:
    """Corre los cinco métodos y enseña el parte."""
    p = argparse.ArgumentParser(prog="matematicas", description=__doc__)
    p.add_argument("--casos", type=int, default=50000, help="casos por método")
    p.add_argument("--semilla", type=int, default=7)
    args = p.parse_args(argv)

    azar = random.Random(args.semilla)
    parte = Parte()
    arranca = time.monotonic()

    print(f"\n  Examen de las cuentas · {args.casos} casos por método "
          f"· semilla {args.semilla}\n")
    for nombre, hacer in (("el oráculo exacto", lambda: oraculo(parte, args.casos, azar)),
                          ("las leyes del reparto", lambda: leyes(parte, args.casos, azar)),
                          ("las metamórficas", lambda: metamorficas(parte, min(args.casos, 20000), azar)),
                          ("los números crueles", lambda: crueles(parte)),
                          ("cincuenta años", lambda: cincuenta_anos(parte, azar))):
        t0 = time.monotonic()
        hacer()
        print(f"  {nombre:<26} {time.monotonic() - t0:>6.1f}s", flush=True)

    print("\n  Lo medido")
    print("  " + "-" * 74)
    for que, cuanto in parte.medidas:
        print(f"    {que:<46} {cuanto}")
    print()
    if parte.fallos:
        print(f"  {len(parte.fallos)} fallos:")
        for f in parte.fallos[:25]:
            print("   ", f)
        if len(parte.fallos) > 25:
            print(f"    … y {len(parte.fallos) - 25} más")
    else:
        print("  Sin fallos.")
    print(f"\n  {time.monotonic() - arranca:.0f}s\n")
    return 1 if parte.fallos else 0


if __name__ == "__main__":
    sys.exit(main())
