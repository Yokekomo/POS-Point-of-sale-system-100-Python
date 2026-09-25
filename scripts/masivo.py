"""Un mes de trabajo, muchas veces, y después la cuenta de cada gramo y cada euro.

Las pruebas de `tests/test_matematicas.py` miran una operación cada vez: una
limpieza, un traslado, un recuento. Están bien y hacen falta, pero no contestan
a la pregunta que importa el día que esto se vende: **después de un mes entero
con toda la casa trabajando encima —recepciones, despieces, maduración,
traslados, ventas, merma, inventarios y la torpeza de cada día— ¿siguen
cuadrando los números?**

Eso es lo que hace esto. Para cada semilla:

1. `bench.build()` levanta una casa y la hace trabajar treinta días, con sus
   cinco personas y cada una haciendo lo suyo.
2. `bench.hammer()` le da encima unos cientos de operaciones al azar, la mitad
   de ellas de las que tienen que fallar.
3. `bench.audit()` pasa la lista de lo que nunca puede pasar.
4. Y aquí se añade lo que `audit` no mira: la **conservación**. Un primal que
   entró por 300,80 € tiene que seguir valiendo 300,80 € repartido entre todo
   lo que salió de él, y los kilos que se le sacaron tienen que aparecer
   —vendidos, tirados, trasladados o en la cámara— hasta el último gramo.

Lo que se enseña al final no es «pasa» o «no pasa»: es **el peor desvío de cada
cuenta**, con la semilla y la pieza donde salió. Un céntimo de desvío en una
casa es ruido; el mismo céntimo repetido en cada pieza durante un año es una
reclamación. Y una semilla es un mes entero reproducible: se vuelve a montar
igual con `--semilla`.

    python -m scripts.masivo                  # 12 casas, un mes cada una
    python -m scripts.masivo --casas 100      # el examen largo
    python -m scripts.masivo --semilla 37     # repetir exactamente ese mes
    python -m scripts.masivo --dias 90        # un trimestre
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import date

# Un céntimo y un gramo: por debajo de eso no hay báscula que lo mida ni
# factura que lo refleje. Por encima, alguien lo va a ver.
CENTIMO = 0.01
GRAMO = 0.001

# La holgura con la que se da por buena una cuenta. No es el tope de arriba:
# es lo que se admite acumulado tras un mes de operaciones encadenadas, donde
# cada una redondea a gramo. Cinco gramos son el redondeo de una balanza de
# muelle; cinco céntimos, lo que no cambia ninguna decisión.
HOLGURA_KG = 0.005
HOLGURA_EUR = 0.05


@dataclass
class Desvio:
    """El peor desvío de una cuenta, con dónde salió para poder ir a verlo."""
    cuanto: float = 0.0
    unidad: str = "EUR"
    donde: str = ""
    semilla: int = 0

    def apunta(self, cuanto: float, donde: str, semilla: int) -> None:
        """Se queda con el peor, que es el único que dice algo."""
        if cuanto > self.cuanto:
            self.cuanto, self.donde, self.semilla = cuanto, donde, semilla


@dataclass
class Parte:
    """Lo que ha salido de todas las casas juntas."""
    casas: int = 0
    dias: int = 0
    piezas: int = 0
    lotes: int = 0
    movimientos: int = 0
    rechazos: int = 0
    hallazgos: list[str] = field(default_factory=list)
    reventones: list[str] = field(default_factory=list)
    peores: dict[str, Desvio] = field(default_factory=dict)

    def peor(self, cuenta: str, unidad: str) -> Desvio:
        """El registro del peor desvío de esa cuenta, creándolo si es el primero."""
        if cuenta not in self.peores:
            self.peores[cuenta] = Desvio(unidad=unidad)
        return self.peores[cuenta]


# ------------------------------------------------------------ las cuentas
def _conservacion(session, restaurant_id: int, semilla: int, parte: Parte) -> None:
    """Lo que `audit` no mira: que nada se invente y nada se pierda.

    Se pregunta pieza por pieza, por el mismo camino por el que lo mira un
    hostelero: la pantalla de trazabilidad. Si la historia de una pieza no
    cuadra ahí, no cuadra en ningún sitio, porque esa es la pantalla que se
    enseña el día que llaman para retirar un lote.
    """
    from thegrill.models import (IngredientLot, IngredientMovement, MovementKind,
                                 Primal)
    from thegrill.web import tracing

    for pieza in session.query(Primal).filter_by(restaurant_id=restaurant_id):
        try:
            historia = tracing.history(session, restaurant_id, pieza.serial)
        except tracing.NotFound:
            continue
        except Exception as e:                                   # noqa: BLE001
            parte.reventones.append(
                f"semilla {semilla} · trazabilidad de {pieza.serial}: "
                f"{type(e).__name__}: {e}")
            continue

        # [kilos] Lo que salió de un corte tiene que estar en algún sitio:
        # vendido, tirado, trasladado o todavía en la cámara. Lo que no
        # aparece en ninguno de los cuatro no se ha ido a ninguna parte: es
        # un agujero en la cuenta.
        for nodo in historia.todo:
            hueco = abs(nodo.unaccounted_kg)
            parte.peor("kilos que no aparecen", "kg").apunta(
                hueco, f"{pieza.serial} · {nodo.serial}", semilla)
            if hueco > HOLGURA_KG:
                parte.hallazgos.append(
                    f"[kilos_sin_explicar] semilla {semilla} · {pieza.serial} · "
                    f"{nodo.serial}: {hueco:.6g} kg sin explicación "
                    f"(salieron {nodo.produced_kg:.6g}, vendidos {nodo.sold_kg:.6g}, "
                    f"tirados {nodo.waste_kg:.6g}, movidos {nodo.moved_kg:.6g}, "
                    f"quedan {nodo.remaining_kg:.6g})")

        # [dinero] Lo que costó la pieza se reparte entre sus cortes y no se
        # crea ni se destruye por el camino. Solo se mira cuando la pieza
        # tiene precio y se ha despiezado: una pieza entera en la cámara no
        # tiene nada que repartir todavía.
        if historia.butchery is None or not historia.cost:
            continue
        # Lo que valían los cortes **al nacer**, no lo que valen hoy. El kilo
        # de un lote sube cuando se tira parte de él —lo que sobrevive paga lo
        # que se fue a la basura— y eso no es dinero nuevo: multiplicar los
        # kilos de hoy por el precio de hoy daba piezas que parecían valer el
        # triple de lo que costaron. Lo que se reparte en el despiece es lo
        # que se apunta en el movimiento de entrada de cada lote.
        #
        # Y los cortes son los del despiece entero, que puede llevar tres
        # piezas dentro: de todo lo que salió, a esta le toca su parte.
        nacidos = {c.serial for c in historia.butchery.cuts}
        del_despiece = round(sum(
            mv.cost or 0.0 for mv in session.query(IngredientMovement)
            .filter_by(restaurant_id=restaurant_id, kind=MovementKind.IN)
            .join(IngredientLot, IngredientLot.id == IngredientMovement.lot_id)
            .filter(IngredientLot.serial.in_(nacidos))), 4)
        if del_despiece <= 0:
            continue
        suyos = round(del_despiece * historia.share, 4)
        gap = abs(suyos - historia.cost)
        parte.peor("el dinero de la pieza", "EUR").apunta(gap, pieza.serial, semilla)
        if gap > max(HOLGURA_EUR, historia.cost * 0.02):
            parte.hallazgos.append(
                f"[dinero_de_la_pieza] semilla {semilla} · {pieza.serial}: costó "
                f"{historia.cost:.2f} y le tocan {suyos:.2f} de los "
                f"{del_despiece:.2f} que se repartieron en {historia.butchery.tg}")

        # [kilos] Y lo mismo con el peso: lo que entró al despiece sale en
        # cortes, en recortes y en merma. Ni un gramo más.
        b = historia.butchery
        salido = round(b.total_cuts_kg + b.waste_kg, 6)
        if b.weight_before_kg > 0:
            gap = abs(salido - b.weight_before_kg)
            parte.peor("el peso del despiece", "kg").apunta(gap, b.tg, semilla)
            if gap > HOLGURA_KG:
                parte.hallazgos.append(
                    f"[peso_del_despiece] semilla {semilla} · {b.tg}: entraron "
                    f"{b.weight_before_kg:.6g} kg y salieron {salido:.6g}")


def _libro(session, restaurant_id: int, semilla: int, parte: Parte) -> None:
    """Que el libro de cada lote diga lo mismo que el lote.

    `audit` ya lo mira y lo da por bueno o por malo. Aquí además se guarda
    **cuánto** se desvía el peor: un descuadre que crece con los meses se ve
    en ese número mucho antes de pasarse de la holgura.
    """
    from thegrill.models import IngredientLot, IngredientMovement, MovementKind

    movidos: dict[int, float] = {}
    for mv in session.query(IngredientMovement).filter_by(restaurant_id=restaurant_id):
        if mv.kind != MovementKind.IN and mv.lot_id:
            movidos[mv.lot_id] = round(movidos.get(mv.lot_id, 0.0) + (mv.qty or 0.0), 6)
        parte.movimientos += 1
    for lote in session.query(IngredientLot).filter_by(restaurant_id=restaurant_id):
        parte.lotes += 1
        esperado = round((lote.qty or 0.0) + movidos.get(lote.id, 0.0), 6)
        parte.peor("el libro de cada lote", "kg").apunta(
            abs(esperado - (lote.qty_remaining or 0.0)),
            lote.serial or str(lote.id), semilla)


def _dinero_de_cada_lote(session, restaurant_id: int, semilla: int, parte: Parte) -> None:
    """Que el dinero de un lote no se cree ni se pierda en todo el mes.

    Un lote nace valiendo lo que dice su movimiento de entrada. A partir de
    ahí, cada kilo que sale se lleva su parte —vendido, tirado o trasladado— y
    lo que queda vale el resto. La suma de las tres cosas tiene que dar lo que
    valía al nacer, después de un mes entero de ventas, mermas y traslados.

    Lo que se tira es el caso interesante: su dinero **no desaparece**, se
    queda sobre lo que sobrevive —el kilo sube— y por eso aparece a la vez
    como coste de la merma y dentro de lo que queda. Se descuenta una vez, que
    es lo que dice la cuenta.
    """
    from thegrill.models import IngredientLot, IngredientMovement, MovementKind

    salidas: dict[int, float] = {}
    for mv in (session.query(IngredientMovement)
               .filter_by(restaurant_id=restaurant_id)):
        if mv.kind in (MovementKind.SALE, MovementKind.MOVE) and mv.lot_id:
            salidas[mv.lot_id] = round(salidas.get(mv.lot_id, 0.0) + (mv.cost or 0.0), 6)

    for lote in session.query(IngredientLot).filter_by(restaurant_id=restaurant_id):
        nacio = round(sum(
            mv.cost or 0.0 for mv in session.query(IngredientMovement)
            .filter_by(restaurant_id=restaurant_id, kind=MovementKind.IN,
                       lot_id=lote.id)), 6)
        if nacio <= 0:
            continue
        queda = round((lote.qty_remaining or 0.0) * (lote.unit_cost or 0.0), 6)
        gap = abs(nacio - (salidas.get(lote.id, 0.0) + queda))
        parte.peor("el dinero de cada lote", "EUR").apunta(
            gap, lote.serial or str(lote.id), semilla)
        if gap > max(HOLGURA_EUR, nacio * 0.02):
            parte.hallazgos.append(
                f"[dinero_del_lote] semilla {semilla} · {lote.serial}: nació "
                f"valiendo {nacio:.2f}, salieron {salidas.get(lote.id, 0.0):.2f} y "
                f"quedan {queda:.2f}")


def _una_casa(semilla: int, dias: int, golpes: int, parte: Parte) -> None:
    """Monta una casa, la hace trabajar un mes, le da encima y le pasa cuentas."""
    from thegrill import bench, db
    from thegrill.models import Primal

    carpeta = pathlib.Path(tempfile.mkdtemp(prefix="masivo-"))
    db.init_engine(f"sqlite:///{carpeta/'casa.db'}")
    db.create_all()
    with db.session_scope() as session:
        casa = bench.build(session, days=dias, seed=semilla,
                           multisite=bool(semilla % 3), index=semilla,
                           until=date(2026, 9, 20))
        rid = casa.restaurant_id

    with db.session_scope() as session:
        # Y encima, lo que no está en el guion: operaciones al azar, la mitad
        # de ellas imposibles. Lo interesante no es que funcionen, es que lo
        # que el programa rechace lo rechace por su motivo y deje la casa
        # igual que estaba.
        parte.rechazos += len(bench.hammer(session, casa, rounds=golpes,
                                           seed=semilla * 7 + 1))

    with db.session_scope() as session:
        for hallazgo in bench.audit(session, rid):
            parte.hallazgos.append(f"semilla {semilla} · {hallazgo}")
        _conservacion(session, rid, semilla, parte)
        _libro(session, rid, semilla, parte)
        _dinero_de_cada_lote(session, rid, semilla, parte)
        parte.piezas += session.query(Primal).filter_by(restaurant_id=rid).count()
    parte.casas += 1
    parte.dias += dias


def correr(casas: int, dias: int, golpes: int, desde: int) -> Parte:
    """Todas las casas, una detrás de otra, con el parte de lo que salga."""
    parte = Parte()
    for i in range(casas):
        semilla = desde + i
        arranca = time.monotonic()
        try:
            _una_casa(semilla, dias, golpes, parte)
        except Exception:                                        # noqa: BLE001
            # Una casa que revienta no para el examen: se apunta entera —con
            # su traza— y se sigue con la siguiente. Lo que hay que saber al
            # final es cuántas de cien revientan, no cuál fue la primera.
            parte.reventones.append(f"semilla {semilla}:\n{traceback.format_exc()}")
        print(f"  casa {i + 1}/{casas} (semilla {semilla}) · "
              f"{time.monotonic() - arranca:.0f}s · "
              f"{len(parte.hallazgos)} hallazgos", flush=True)
    return parte


def enseña(parte: Parte) -> int:
    """El parte final. Devuelve 1 si hay algo que mirar."""
    print()
    print(f"  {parte.casas} casas · {parte.dias} días de trabajo · "
          f"{parte.piezas} piezas · {parte.lotes} lotes · "
          f"{parte.movimientos} movimientos")
    print(f"  {parte.rechazos} operaciones rechazadas por el programa "
          f"(de las que tenían que fallar)")
    print()
    print("  El peor desvío de cada cuenta")
    print("  " + "-" * 74)
    for cuenta, d in sorted(parte.peores.items(), key=lambda kv: -kv[1].cuanto):
        tope = HOLGURA_KG if d.unidad == "kg" else HOLGURA_EUR
        marca = "  " if d.cuanto <= tope else "<-"
        print(f"  {marca} {cuenta:<26} {d.cuanto:>12.8f} {d.unidad:<4} "
              f"{d.donde or '—'} (semilla {d.semilla})")
    print()

    if parte.reventones:
        print(f"  {len(parte.reventones)} casas reventaron:")
        for r in parte.reventones[:5]:
            print("   ", r.replace("\n", "\n    "))
        print()
    if parte.hallazgos:
        print(f"  {len(parte.hallazgos)} hallazgos:")
        for h in parte.hallazgos[:40]:
            print("   ", h)
        if len(parte.hallazgos) > 40:
            print(f"    … y {len(parte.hallazgos) - 40} más")
        return 1
    print("  Sin hallazgos.")
    return 1 if parte.reventones else 0


def main(argv: list[str] | None = None) -> int:
    """La línea de órdenes del examen masivo."""
    p = argparse.ArgumentParser(prog="masivo", description=__doc__)
    p.add_argument("--casas", type=int, default=12, help="cuántas casas (semillas)")
    p.add_argument("--dias", type=int, default=30, help="cuántos días trabaja cada una")
    p.add_argument("--golpes", type=int, default=200,
                   help="operaciones al azar encima de cada casa")
    p.add_argument("--semilla", type=int, default=1, help="la primera semilla")
    args = p.parse_args(argv)

    print()
    print(f"  Examen masivo: {args.casas} casas × {args.dias} días, "
          f"desde la semilla {args.semilla}")
    print()
    arranca = time.monotonic()
    parte = correr(args.casas, args.dias, args.golpes, args.semilla)
    salida = enseña(parte)
    print(f"  {time.monotonic() - arranca:.0f}s")
    return salida


if __name__ == "__main__":
    sys.exit(main())
