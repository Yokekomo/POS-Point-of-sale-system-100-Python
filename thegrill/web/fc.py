"""[01194] El food cost de ayer: el que dice la carta y el que dijo la cámara.

En cocina se pone un objetivo —330 g de steak— y al cortar nunca salen 330
exactos. Esa diferencia es el negocio, y hasta ahora el programa solo sabía
decir la mitad: el food cost **teórico**, el del escandallo, que es el que
sale si todo se corta al gramo y no se cae nada al suelo.

Aquí sale la otra mitad. El food cost **real** es lo que la cámara entregó de
verdad para servir lo que se vendió: lo que salió por ventas, más lo que se
tiró y se apuntó. Dividido entre lo que se ingresó sin impuestos, que es
contra lo que se mide siempre.

Y la resta de los dos, que es la única cifra que hace cambiar algo:

    perdido = coste real − coste teórico

Si el steak sale a 345 g en vez de 330, ahí está. Si se tira una caja y nadie
la apunta, no está aquí: está en el residuo del cuadre, que es otra pantalla y
otra conversación.

Se mira **de ayer**, no de hoy: el turno de hoy no se ha cerrado, el recuento
no se ha hecho y la mitad de los números todavía no existen. Un food cost a
media tarde no es un food cost, es una corazonada.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from thegrill.models import (IngredientMovement, MovementKind, PosProduct,
                             Recipe, RecipeKind, SalesByProduct)
from thegrill.web import costing, jornada

NADA = 0.005


@dataclass
class Plato:
    """[01195] Un plato vendido ayer, con lo que la carta decía que costaba."""
    nombre: str
    unidades: int
    ingresos: float          # sin impuestos
    coste_teorico: float
    atado: bool = True       # si el artículo del POS apunta a un plato de la carta

    @property
    def fc_teorico(self) -> float | None:
        """[01201] A qué food cost debería salir el plato, según el escandallo."""
        return round(self.coste_teorico / self.ingresos * 100, 1) if self.ingresos > NADA else None


@dataclass
class Dia:
    """[01196] El día entero: lo que se vendió, lo que decía la carta y lo que costó."""
    fecha: date
    platos: list = field(default_factory=list)
    coste_real: float = 0.0          # lo que salió de la cámara por ventas
    merma: float = 0.0               # lo tirado y apuntado
    sin_atar: int = 0                # artículos del POS que no son ningún plato

    @property
    def ingresos(self) -> float:
        """[01202] Lo ingresado en el día, sumando todos los platos."""
        return round(sum(p.ingresos for p in self.platos), 2)

    @property
    def coste_teorico(self) -> float:
        """[01203] Lo que debería haber costado la materia prima del día."""
        return round(sum(p.coste_teorico for p in self.platos), 2)

    @property
    def fc_teorico(self) -> float | None:
        """[01204] El food cost teórico del día entero.

        El que se compara con el real: la diferencia entre los dos es lo que se
        escapa por la cocina, y es la conversación de la reunión del lunes.
        """
        return round(self.coste_teorico / self.ingresos * 100, 1) if self.ingresos > NADA else None

    @property
    def fc_real(self) -> float | None:
        """[01205] Con la merma dentro: lo que se tira lo paga el plato que se vendió."""
        if self.ingresos <= NADA:
            return None
        return round((self.coste_real + self.merma) / self.ingresos * 100, 1)

    @property
    def perdido(self) -> float:
        """[01206] Lo que se ha ido entre el papel y la cámara, en dinero."""
        return round(self.coste_real + self.merma - self.coste_teorico, 2)

    @property
    def puntos(self) -> float | None:
        """[01207] Y en puntos de food cost, que es como se habla de esto."""
        if self.fc_real is None or self.fc_teorico is None:
            return None
        return round(self.fc_real - self.fc_teorico, 1)

    @property
    def peores(self) -> list:
        """[01208] Los platos que más dinero mueven, que es por donde se empieza."""
        return sorted(self.platos, key=lambda p: -p.ingresos)


def _vendido(session: Session, restaurant_id: int, on: date) -> list[SalesByProduct]:
    """[01197] Lo que dice la caja que se vendió ese día, plato a plato."""
    return (session.query(SalesByProduct)
            .filter_by(restaurant_id=restaurant_id, op_date=on).all())


def _salido_de_camara(session: Session, restaurant_id: int, on: date) -> tuple[float, float]:
    """[01198] Lo que la cámara entregó ese día: por ventas y por merma, en dinero.

    Se lee del libro de movimientos, que es donde queda apuntado cada gramo
    con su coste. Las salidas vienen en negativo; aquí se cuentan en positivo
    porque lo que se lee es «cuánto costó lo que salió».
    """
    ventas = merma = 0.0
    for mv in (session.query(IngredientMovement)
               .filter(IngredientMovement.restaurant_id == restaurant_id,
                       IngredientMovement.date == on)):
        if mv.kind == MovementKind.SALE:
            ventas += abs(mv.cost or 0.0)
        elif mv.kind == MovementKind.WASTE:
            merma += abs(mv.cost or 0.0)
    return round(ventas, 2), round(merma, 2)


def dia(session: Session, restaurant_id: int, on: date | None = None) -> Dia:
    """[01199] El food cost de ese día: el de la carta y el de la cámara."""
    on = on or (jornada.hoy(session, restaurant_id) - timedelta(days=1))
    salida = Dia(fecha=on)

    # [01209] Qué plato es cada artículo del POS. Lo que no esté atado se cuenta
    # aparte: sus ingresos no se pueden repartir contra ningún escandallo, y
    # meterlos en el total haría que el food cost saliera bajo por arte de
    # magia, que es justo el error que esta pantalla viene a quitar.
    por_pos = {p.pos_name: p.recipe_id for p in
               session.query(PosProduct).filter_by(restaurant_id=restaurant_id)}
    recetas = {r.id: r for r in session.query(Recipe)
               .filter_by(restaurant_id=restaurant_id, kind=RecipeKind.DISH)}
    costes = costing.unit_costs(session, restaurant_id)

    for linea in _vendido(session, restaurant_id, on):
        receta = recetas.get(por_pos.get(linea.pos_name or ""))
        if receta is None:
            salida.sin_atar += linea.units or 0
            continue
        cuenta = costing.cost_of(session, receta, costes)
        unidades = linea.units or 0
        neto = cuenta.net_price
        ingresos = round((neto or 0.0) * unidades, 2)
        if linea.amount and neto is None:
            ingresos = round(linea.amount, 2)
        salida.platos.append(Plato(
            nombre=receta.name, unidades=unidades, ingresos=ingresos,
            coste_teorico=round(cuenta.cost_per_portion * unidades, 2)))

    salida.coste_real, salida.merma = _salido_de_camara(session, restaurant_id, on)
    return salida


def ayer(session: Session, restaurant_id: int, hoy: date | None = None) -> Dia:
    """[01200] Lo de ayer, que es el último día con todos los números hechos."""
    hoy = hoy or jornada.hoy(session, restaurant_id)
    return dia(session, restaurant_id, hoy - timedelta(days=1))
