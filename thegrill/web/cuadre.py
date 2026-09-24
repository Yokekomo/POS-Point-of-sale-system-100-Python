"""Tres números que un restaurante no tiene y necesita para cuadrar.

El programa ya dice cuánto entró, cuánto se vendió y cuánto se tiró. Lo que no
decía es lo de después, que es lo que se mira cuando algo no sale:

1. **El residuo.** Todo kilo que entra tiene que salir por alguna puerta:
   vendido, tirado, elaborado, trasladado, o seguir ahí. Lo que no sale por
   ninguna se llama ajuste, porque al contar hubo que forzar el número para
   que cuadrara. Ese ajuste es la cifra honesta: **los kilos que no sabemos
   dónde han ido**. Estaba repartido entre los totales y no se veía.

2. **La banda de cada corte.** Un aviso de «merma por encima del 5 %» está mal
   puesto siempre: para el solomillo es un escándalo y para la falda es
   martes. Lo que sirve es medir contra la historia de esa casa y ese corte,
   con mediana y MAD —no con media y desviación típica—, porque en una cocina
   hay barbaridades sueltas y la media se las traga mientras que la mediana
   las ignora. Es lo que caza la pieza de 84 kilos entre hermanas de 10,4.

3. **El dedo.** Quien pesa de verdad deja decimales repartidos: 8,43 · 9,17 ·
   7,86. Quien no pesa y calcula a ojo deja 8,5 · 9,0 · 7,5, y eso se nota
   contando el último dígito. No hace falta acusar a nadie: el número lo dice
   solo, y un peso inventado arrastra el coste, el rendimiento y la
   trazabilidad detrás.

Nada de esto necesita una tabla nueva: sale del libro de movimientos y de las
pesadas que ya se guardan.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from thegrill.models import (Ingredient, IngredientLot, IngredientMovement,
                             MovementKind, Primal, PrimalWeighing, User)
from thegrill.web import jornada

# Cinco gramos: el juego de una báscula de muelle, no un agujero.
NADA = 0.005


# ============================================================ 1. el residuo
@dataclass
class Residuo:
    """Lo que ha pasado con un artículo, y lo que no se explica."""
    ingredient_id: int
    nombre: str
    entrado_kg: float = 0.0
    vendido_kg: float = 0.0
    tirado_kg: float = 0.0
    elaborado_kg: float = 0.0
    movido_kg: float = 0.0
    ajustado_kg: float = 0.0        # el residuo: lo que hubo que forzar al contar
    ajustado_eur: float = 0.0

    @property
    def explicado_kg(self) -> float:
        """Los kilos que sí tienen explicación: vendidos, tirados, elaborados o movidos.

        Lo que sobra de aquí es lo que no cuadra, y es el número del cuadre.
        """
        return round(self.vendido_kg + self.tirado_kg + self.elaborado_kg + self.movido_kg, 6)

    @property
    def parte(self) -> float | None:
        """Qué parte de lo que entró no se sabe dónde fue, en tanto por ciento."""
        if self.entrado_kg <= NADA:
            return None
        return round(abs(self.ajustado_kg) / self.entrado_kg * 100.0, 2)


@dataclass
class Cuadre:
    desde: date
    hasta: date
    lineas: list[Residuo] = field(default_factory=list)

    @property
    def sin_explicar_kg(self) -> float:
        """Los kilos que no tienen explicación en todo el cuadre."""
        return round(sum(l.ajustado_kg for l in self.lineas), 3)

    @property
    def sin_explicar_eur(self) -> float:
        """Lo que cuestan esos kilos. Es el número que mira quien lleva la casa."""
        return round(sum(l.ajustado_eur for l in self.lineas), 2)

    @property
    def peores(self) -> list[Residuo]:
        """Los que más kilos se han comido, que son por los que se empieza."""
        return sorted([l for l in self.lineas if abs(l.ajustado_kg) > NADA],
                      key=lambda l: l.ajustado_kg)


# Cada clase de movimiento, a su columna. Lo que no esté aquí no se cuenta:
# más vale una columna de menos que un kilo contado dos veces.
_COLUMNAS = {
    MovementKind.IN: "entrado_kg",
    MovementKind.SALE: "vendido_kg",
    MovementKind.WASTE: "tirado_kg",
    MovementKind.PRODUCTION: "elaborado_kg",
    MovementKind.MOVE: "movido_kg",
    MovementKind.ADJUST: "ajustado_kg",
}


def cuadre(session: Session, restaurant_id: int, desde: date, hasta: date) -> Cuadre:
    """Lo que entró, por dónde salió, y lo que no se sabe."""
    nombres = {i.id: i.name for i in
               session.query(Ingredient).filter_by(restaurant_id=restaurant_id)}
    por_articulo: dict[int, Residuo] = {}
    movimientos = (session.query(IngredientMovement)
                   .filter(IngredientMovement.restaurant_id == restaurant_id,
                           IngredientMovement.date >= desde,
                           IngredientMovement.date <= hasta))
    for mv in movimientos:
        columna = _COLUMNAS.get(mv.kind)
        if columna is None:
            continue
        linea = por_articulo.get(mv.ingredient_id)
        if linea is None:
            linea = por_articulo[mv.ingredient_id] = Residuo(
                ingredient_id=mv.ingredient_id,
                nombre=nombres.get(mv.ingredient_id, f"#{mv.ingredient_id}"))
        # Las salidas vienen en negativo en el libro. Aquí se cuentan en
        # positivo porque lo que se lee es «cuántos kilos se fueron por ahí»,
        # y un número con signo en una tabla de cocina se lee mal.
        cuanto = mv.qty or 0.0
        valor = round(cuanto if columna == "entrado_kg" else -cuanto, 6)
        if columna == "ajustado_kg":
            # El ajuste sí lleva signo: negativo falta, positivo sobra. Esa es
            # justo la información, y esconderla sería mentir.
            valor = round(cuanto, 6)
            linea.ajustado_eur = round(linea.ajustado_eur + (mv.cost or 0.0), 6)
        setattr(linea, columna, round(getattr(linea, columna) + valor, 6))
    return Cuadre(desde=desde, hasta=hasta, lineas=list(por_articulo.values()))


def del_mes(session: Session, restaurant_id: int, on: date | None = None) -> Cuadre:
    """El cuadre del mes que corre, que es el que se mira."""
    on = on or jornada.hoy(session, restaurant_id)
    return cuadre(session, restaurant_id, on.replace(day=1), on)


# ====================================================== 2. la banda del corte
@dataclass
class Banda:
    """Lo normal para ese corte en esta casa, con su mediana y su MAD."""
    que: str
    n: int
    mediana: float
    mad: float
    bajo: float
    alto: float

    def raro(self, valor: float) -> bool:
        """Si ese valor se sale de lo normal por arriba o por abajo."""
        return valor < self.bajo or valor > self.alto

    def cuanto_se_sale(self, valor: float) -> float:
        """Cuántos MAD de distancia. Cero es justo la mediana."""
        if self.mad <= 1e-9:
            return 0.0
        return round(abs(valor - self.mediana) / self.mad, 1)


def mediana(valores: list[float]) -> float:
    """El valor de en medio. Sin nada que ordenar, cero.

    La mediana y no la media: un día con una merma de cuarenta kilos por una
    cámara que se paró tira de la media de todo el mes y hace que ningún otro
    día parezca raro. A la de en medio ese día no la mueve.
    """
    if not valores:
        return 0.0
    orden = sorted(valores)
    medio = len(orden) // 2
    if len(orden) % 2:
        return orden[medio]
    return (orden[medio - 1] + orden[medio]) / 2.0


def mad(valores: list[float], centro: float | None = None) -> float:
    """Desviación absoluta mediana: la desviación que no se traga los bichos."""
    if not valores:
        return 0.0
    centro = mediana(valores) if centro is None else centro
    return mediana([abs(v - centro) for v in valores])


# Con 1,4826 el MAD de una campana coincide con su desviación típica, así que
# «tres MAD» se lee igual que «tres sigmas» de toda la vida.
A_SIGMA = 1.4826
MINIMO = 8          # con menos piezas no hay historia: no se dice nada
K = 3.0             # tres desviaciones: lo raro de verdad, no lo poco común


def banda(valores: list[float], que: str = "", k: float = K,
          minimo: int = MINIMO) -> Banda | None:
    """La banda de lo normal, o nada si no hay historia suficiente."""
    limpios = [v for v in valores if v is not None and v > 0]
    if len(limpios) < minimo:
        return None
    centro = mediana(limpios)
    dispersion = mad(limpios, centro) * A_SIGMA
    if dispersion <= 1e-9:
        # Todas iguales: cualquier cosa distinta es rara, pero no por poco.
        dispersion = max(centro * 0.05, 1e-6)
    return Banda(que=que, n=len(limpios), mediana=round(centro, 4),
                 mad=round(dispersion, 4),
                 bajo=round(centro - k * dispersion, 4),
                 alto=round(centro + k * dispersion, 4))


def banda_del_corte(session: Session, restaurant_id: int, sku: str,
                    k: float = K) -> Banda | None:
    """Lo que pesa normalmente una pieza de ese corte en esta casa."""
    pesos = [p.received_kg or p.weight_kg for p in
             session.query(Primal).filter_by(restaurant_id=restaurant_id, sku=sku)]
    return banda([p for p in pesos if p], que=sku, k=k)


def peso_raro(session: Session, restaurant_id: int, sku: str, kg: float,
              k: float = K) -> Banda | None:
    """La banda si ese peso se sale de ella; nada si es normal o no hay historia.

    Esto es lo que tiene que saltar con la bolsa todavía en la báscula, no tres
    meses después: una pieza de 84 kg entre hermanas de 10,4 es una coma mal
    puesta, y a los tres meses ya no se sabe de qué día era.
    """
    la_banda = banda_del_corte(session, restaurant_id, sku, k=k)
    if la_banda is None or not la_banda.raro(kg):
        return None
    return la_banda


# ============================================================== 3. el dedo
@dataclass
class Dedo:
    """Si esa persona pesa o calcula a ojo, según el último dígito."""
    user_id: int
    nombre: str
    n: int
    resolucion: float           # a cuánto llega su báscula: 0,001 · 0,01 · 0,1
    chi2: float
    redondos: float             # qué parte acaba en 0 o en 5, en tanto por ciento
    veredicto: str              # "pesa" · "mira" · "no_pesa"

    @property
    def sospechoso(self) -> bool:
        """Si ese número no parece pesado, sino escrito a ojo."""
        return self.veredicto != "pesa"


# Chi cuadrado con nueve grados de libertad —diez dígitos menos uno—. Por
# encima del 99,9 % es que no es casualidad: nadie tiene esa suerte.
CHI_95, CHI_999 = 16.919, 27.877
DIGITOS_MINIMOS = 30


def _resolucion(valores: list[float]) -> float:
    """Hasta dónde llega la báscula de esa persona, mirando lo que escribe.

    Sin esto se acusa a quien tiene una báscula de cien gramos: sus pesos
    acaban todos en cero porque su aparato no da más, no porque no pese.

    Se busca el paso **más grueso** que divide a todos los pesos, que es el de
    su báscula. Al revés no vale: todo múltiplo de cien gramos lo es también de
    un gramo, así que empezando por el fino sale siempre el fino y no se
    distingue una báscula basta de un dedo perezoso.
    """
    for paso in (0.1, 0.01, 0.001):
        if all(abs(v / paso - round(v / paso)) < 1e-6 for v in valores):
            return paso
    return 0.001


def _ultimo_digito(valor: float, resolucion: float) -> int:
    """El último dígito de un peso, a la resolución de la báscula.

    Una báscula que pesa de cinco en cinco gramos reparte los dígitos sola. Una
    persona que escribe a ojo, no: le salen ceros y cincos de más. Eso es lo
    que se cuenta después.
    """
    return int(round(valor / resolucion)) % 10


def dedo(valores: list[float], user_id: int = 0, nombre: str = "") -> Dedo | None:
    """El veredicto de una persona, o nada si no ha pesado lo bastante."""
    limpios = [v for v in valores if v and v > 0]
    if len(limpios) < DIGITOS_MINIMOS:
        return None
    resolucion = _resolucion(limpios)
    cuenta = [0] * 10
    for v in limpios:
        cuenta[_ultimo_digito(v, resolucion)] += 1
    esperado = len(limpios) / 10.0
    chi2 = sum((c - esperado) ** 2 / esperado for c in cuenta)
    redondos = (cuenta[0] + cuenta[5]) / len(limpios) * 100.0
    if chi2 > CHI_999 and redondos > 45.0:
        veredicto = "no_pesa"
    elif chi2 > CHI_95:
        veredicto = "mira"
    else:
        veredicto = "pesa"
    return Dedo(user_id=user_id, nombre=nombre, n=len(limpios), resolucion=resolucion,
                chi2=round(chi2, 1), redondos=round(redondos, 1), veredicto=veredicto)


def dedos(session: Session, restaurant_id: int, desde: date | None = None,
          hasta: date | None = None) -> list[Dedo]:
    """Quién pesa y quién calcula a ojo, de las pesadas de piezas enteras."""
    hasta = hasta or jornada.hoy(session, restaurant_id)
    desde = desde or (hasta - timedelta(days=90))
    nombres = {u.id: u.name for u in
               session.query(User).filter_by(restaurant_id=restaurant_id)}
    por_persona: dict[int, list[float]] = defaultdict(list)
    for p in (session.query(PrimalWeighing)
              .filter(PrimalWeighing.restaurant_id == restaurant_id,
                      PrimalWeighing.date >= desde, PrimalWeighing.date <= hasta)):
        if p.created_by and p.kg:
            por_persona[p.created_by].append(p.kg)
    salida = []
    for user_id, valores in por_persona.items():
        veredicto = dedo(valores, user_id, nombres.get(user_id, f"#{user_id}"))
        if veredicto is not None:
            salida.append(veredicto)
    # Primero el que más canta: es por quien hay que empezar a preguntar.
    return sorted(salida, key=lambda d: -d.chi2)
