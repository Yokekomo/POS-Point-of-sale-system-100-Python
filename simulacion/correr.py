"""El motor: monta las casas, mete las equivocaciones y mira quién las ve.

    python3 -m simulacion.correr --casas 1000 --dias 45
    python3 -m simulacion.correr --casas 20 --dias 365      # un año de verdad

Cada casa trabaja sus días con `bench`, que es el mismo código que usa la demo.
Después, en unas cuantas, alguien se equivoca: por torpeza o por dejadez. La
equivocación se mete por el camino por el que la metería de verdad, y luego se
le pregunta al programa lo mismo que le preguntaría el manager: ¿lo ves?

Tres respuestas posibles, y las tres valen:

- **RECHAZADA**: el programa no la dejó entrar. Es lo mejor que puede pasar.
- **VISTA**: entró, pero el manager la tiene delante sin buscar nada.
- **CIEGA**: entró y nadie la enseña. Eso no es culpa del empleado.

Y además los supervisores: `bench.audit` repasa cada casa buscando lo que nunca
puede pasar —kilos negativos, lotes que crecen, carne despiezada sin coste—.
Eso es el que vigila que no haya trampas.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from multiprocessing import Pool

HOY = date(2026, 9, 20)


@dataclass
class Caso:
    casa: str
    clave: str
    quien: str
    estado: str                  # "rechazada" | "vista" | "ciega" | "no_cabia"
    donde: str = ""
    dicho: str = ""
    porque: str = ""


@dataclass
class Tanda:
    casas: int = 0
    dias: int = 0
    casos: list = field(default_factory=list)
    vigilancia: list = field(default_factory=list)   # lo que nunca puede pasar
    rotos: list = field(default_factory=list)        # lo que el programa rechazó al trabajar
    segundos: float = 0.0


def _una_tanda(argumentos) -> dict:
    """Un puñado de casas en su propia base de datos, en su propio proceso."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    desde, cuantas, dias, semilla, carpeta = argumentos
    from thegrill import bench, db
    from simulacion import torpeza

    ruta = os.path.join(carpeta, f"tanda-{desde}.db")
    db.init_engine(f"sqlite:///{ruta}")
    db.create_all()

    casos, vigilancia, rotos = [], [], []
    empieza = time.perf_counter()
    for i in range(cuantas):
        indice = desde + i
        with db.session_scope() as session:
            # Las sedes se reparten por bloques y no por pares. Con pares, la
            # equivocación que hace falta un local —mandar una pieza y que
            # nadie la toque— caía siempre en casas de una sola sede y no se
            # podía meter nunca: salía «no cabía» en las mil casas y parecía
            # probada cuando no se había probado.
            casa = bench.build(session, days=dias, seed=semilla * 100000 + indice,
                               until=HOY, index=indice,
                               multisite=(indice // len(torpeza.CATALOGO)) % 2 == 0)
            nombre = casa.name or f"casa-{indice}"
            rotos.extend(f"{nombre}: {e}" for e in casa.errors[:3])

            # No todos los restaurantes tienen un torpe, pero muchos sí. A cada
            # casa le toca una equivocación distinta, y así el catálogo entero
            # se prueba tantas veces como casas haya.
            equivocacion = torpeza.CATALOGO[indice % len(torpeza.CATALOGO)]
            try:
                metida = equivocacion.mete(session, casa, HOY)
            except Exception as e:                           # noqa: BLE001
                casos.append(asdict(Caso(nombre, equivocacion.clave, equivocacion.quien,
                                         "rechazada", porque=f"reventó al meterla: {str(e)[:100]}")))
                metida = None
            if metida is None:
                casos.append(asdict(Caso(nombre, equivocacion.clave, equivocacion.quien,
                                         "no_cabia")))
            elif metida.rechazada:
                casos.append(asdict(Caso(nombre, equivocacion.clave, equivocacion.quien,
                                         "rechazada", porque=metida.porque, dicho=metida.dicho)))
            else:
                try:
                    visto = equivocacion.ve(session, casa, metida, HOY)
                except Exception as e:                       # noqa: BLE001
                    visto = torpeza.Visto(False, f"reventó al mirarlo: {str(e)[:100]}")
                casos.append(asdict(Caso(
                    nombre, equivocacion.clave, equivocacion.quien,
                    "vista" if visto.visto else "ciega",
                    donde=visto.donde, dicho=metida.dicho)))

            # Y el que vigila que no haya trampas.
            for hallazgo in bench.audit(session, casa.restaurant_id):
                vigilancia.append(f"{nombre}: {hallazgo}")

    try:
        os.remove(ruta)
        for sufijo in ("-wal", "-shm"):
            if os.path.exists(ruta + sufijo):
                os.remove(ruta + sufijo)
    except OSError:
        pass
    return {"casos": casos, "vigilancia": vigilancia, "rotos": rotos,
            "segundos": time.perf_counter() - empieza, "casas": cuantas}


def correr(casas: int = 100, dias: int = 30, semilla: int = 7,
           procesos: int = 3, por_tanda: int = 10) -> Tanda:
    """Reparte las casas entre varios procesos: una base de datos por tanda."""
    carpeta = tempfile.mkdtemp(prefix="simulacion-")
    trozos = [(desde, min(por_tanda, casas - desde), dias, semilla, carpeta)
              for desde in range(0, casas, por_tanda)]
    salida = Tanda(casas=casas, dias=dias)
    empieza = time.perf_counter()
    hechas = 0
    with Pool(processes=procesos) as pool:
        for resultado in pool.imap_unordered(_una_tanda, trozos):
            salida.casos.extend(resultado["casos"])
            salida.vigilancia.extend(resultado["vigilancia"])
            salida.rotos.extend(resultado["rotos"])
            hechas += resultado["casas"]
            print(f"  {hechas}/{casas} casas · {time.perf_counter() - empieza:6.0f}s",
                  flush=True)
    salida.segundos = time.perf_counter() - empieza
    try:
        os.rmdir(carpeta)
    except OSError:
        pass
    return salida


def informe(tanda: Tanda) -> str:
    """Lo que hay que leer: qué equivocación se ve y cuál no."""
    from simulacion import torpeza

    por_clave = defaultdict(Counter)
    donde = defaultdict(Counter)
    for caso in tanda.casos:
        por_clave[caso["clave"]][caso["estado"]] += 1
        if caso["estado"] == "vista" and caso["donde"]:
            donde[caso["clave"]][caso["donde"][:70]] += 1
        if caso["estado"] == "ciega" and caso["donde"]:
            donde[caso["clave"]]["CIEGA: " + caso["donde"][:60]] += 1

    lineas = [
        "=" * 78,
        f"  {tanda.casas} restaurantes · {tanda.dias} días cada uno "
        f"· {tanda.casas * tanda.dias:,} días de trabajo".replace(",", "."),
        f"  {tanda.segundos / 60:.1f} minutos",
        "=" * 78, "",
        "QUÉ PASA CUANDO ALGUIEN SE EQUIVOCA", "",
    ]
    ciegas = []
    for eq in torpeza.CATALOGO:
        c = por_clave.get(eq.clave)
        if not c:
            continue
        total = sum(c.values()) - c.get("no_cabia", 0)
        if not total:
            continue
        vista = c.get("vista", 0) + c.get("rechazada", 0)
        pinta = "✔" if vista == total else ("✗" if vista == 0 else "~")
        lineas.append(f"{pinta} {eq.clave:22} {eq.quien:8} "
                      f"rechazada {c.get('rechazada', 0):4}  vista {c.get('vista', 0):4}  "
                      f"CIEGA {c.get('ciega', 0):4}")
        lineas.append(f"    «{eq.cuenta}»")
        for texto, veces in donde[eq.clave].most_common(2):
            lineas.append(f"    {veces:4}× {texto}")
        if c.get("ciega", 0):
            ciegas.append((eq, c.get("ciega", 0), total))
        lineas.append("")

    lineas += ["=" * 78, "", "LO QUE EL MANAGER NO PUEDE VER (defectos)", ""]
    if not ciegas:
        lineas.append("  Ninguna: todas las equivocaciones se rechazan o se enseñan.")
    for eq, veces, total in sorted(ciegas, key=lambda x: -x[1]):
        lineas.append(f"  · {eq.clave} — {veces} de {total} casas."
                      f"{'  [CRÍTICA]' if eq.critica else ''}")
        lineas.append(f"    {eq.cuenta}")

    lineas += ["", "=" * 78, "", "EL QUE VIGILA QUE NO HAYA TRAMPAS", ""]
    if tanda.vigilancia:
        reglas = Counter(v.split("[")[1].split("]")[0] for v in tanda.vigilancia if "[" in v)
        lineas.append(f"  {len(tanda.vigilancia)} cosas que nunca pueden pasar, y pasaron:")
        for regla, veces in reglas.most_common(12):
            lineas.append(f"    {veces:5}× {regla}")
        for v in tanda.vigilancia[:8]:
            lineas.append(f"      p.ej. {v[:110]}")
    else:
        lineas.append("  Nada. Ni kilos negativos, ni lotes que crecen, ni carne sin coste.")

    if tanda.rotos:
        reventados = Counter(r.split(": ", 1)[-1][:70] for r in tanda.rotos)
        lineas += ["", "=" * 78, "", "LO QUE EL PROGRAMA RECHAZÓ MIENTRAS SE TRABAJABA", ""]
        for texto, veces in reventados.most_common(10):
            lineas.append(f"  {veces:5}× {texto}")
    return "\n".join(lineas)


def main() -> None:
    p = argparse.ArgumentParser(description="Mil restaurantes, y en muchos alguien torpe")
    p.add_argument("--casas", type=int, default=100)
    p.add_argument("--dias", type=int, default=30)
    p.add_argument("--semilla", type=int, default=7)
    p.add_argument("--procesos", type=int, default=3)
    p.add_argument("--por-tanda", type=int, default=10)
    p.add_argument("--guardar", default="")
    args = p.parse_args()

    print(f"Montando {args.casas} restaurantes de {args.dias} días "
          f"({args.casas * args.dias:,} días de trabajo)…".replace(",", "."), flush=True)
    tanda = correr(args.casas, args.dias, args.semilla, args.procesos, args.por_tanda)
    texto = informe(tanda)
    print("\n" + texto)
    if args.guardar:
        with open(args.guardar, "w", encoding="utf-8") as f:
            f.write(texto + "\n")
        with open(args.guardar.replace(".txt", ".json"), "w", encoding="utf-8") as f:
            json.dump({"casas": tanda.casas, "dias": tanda.dias,
                       "casos": tanda.casos, "vigilancia": tanda.vigilancia[:500],
                       "rotos": tanda.rotos[:500]}, f, ensure_ascii=False, indent=1)
        print(f"\nGuardado en {args.guardar}")


if __name__ == "__main__":
    main()
