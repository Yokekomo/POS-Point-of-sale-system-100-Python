"""Historia de un primal: qué salió de él, dónde fue y qué dejó.

Se mete el número de serie de la pieza y sale el árbol entero:

    Primal 8017 (lote DXB20260910, 10 kg, 200 USD)
      └ Despiece TG-0010  rendimiento 74 %
          ├ Striploin steak · serial 8017-01
          │    vendido 4,2 kg · queda 0,8 · merma 0,1
          └ Recorte · serial 8017-02
          └ merma del despiece 2,6 kg

Y debajo, el resumen: lo que costó la pieza, lo que se ingresó con ella y su
food cost real.

**Cómo se reparte el ingreso.** Cada venta de un plato reparte su precio sin
impuestos entre sus ingredientes en proporción a lo que cuesta cada uno dentro
de ese plato. Lo que cae sobre un corte de este primal es lo que se le atribuye.
Dicho de otro modo: un coste C dentro de un plato que convierte Cd de coste en
Pd de ingreso aporta C × Pd / Cd.
"""
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from thegrill.engine.recipes import cost_recipe
from thegrill.models import (Despiece, DespieceCut, DespiecePrimal, Ingredient,
                             IngredientLot, IngredientMovement, MovementKind, PosProduct,
                             Primal, PrimalStatus)
from thegrill.web import costing

EPSILON = 1e-9


class NotFound(LookupError):
    """No hay ninguna pieza con ese número de serie."""


@dataclass
class SaleLine:
    date: date
    dish: str
    kg: float
    cost: float
    revenue: float
    source: str          # pos o defrost


@dataclass
class CutNode:
    serial: str
    name: str
    unit: str
    pieces: int | None = None
    piece_weight_g: float | None = None      # medio real, no declarado
    nominal_piece_g: float | None = None     # a lo que se apuntaba
    grade: str | None = None
    origin: str | None = None
    produced_kg: float = 0.0
    cost: float = 0.0
    unit_cost: float = 0.0
    remaining_kg: float = 0.0
    sold_kg: float = 0.0
    sold_cost: float = 0.0
    revenue: float = 0.0
    waste_kg: float = 0.0
    adjust_kg: float = 0.0
    moved_kg: float = 0.0        # lo que salió a otra sede o del arcón, con su número
    is_trim: bool = False
    sales: list[SaleLine] = field(default_factory=list)

    @property
    def avg_piece_g(self) -> float | None:
        """Peso medio real: los kilos que salieron entre las piezas contadas."""
        if not self.pieces or self.pieces <= 0 or self.produced_kg <= 0:
            return self.piece_weight_g
        return round(self.produced_kg * 1000 / self.pieces, 1)

    @property
    def piece_gap_pct(self) -> float | None:
        """Cuánto se desvía el corte real del objetivo de la hoja."""
        real, target = self.avg_piece_g, self.nominal_piece_g
        if not real or not target:
            return None
        return round((real - target) / target * 100, 1)

    @property
    def label(self) -> str:
        """«330 g (~354 g · 31,8 % FC) · MB9+ · AUS».

        Delante el peso de carta, que es lo que se vende y lo que manda en el
        escandallo. Entre paréntesis la realidad: el promedio que salió y el
        food cost al que está saliendo el corte. El food cost solo aparece
        cuando ya se ha vendido algo: antes de eso no hay número que dar.
        """
        from thegrill.web.butchery import piece_label
        bits = []
        weight = piece_label(self.nominal_piece_g, self.avg_piece_g, self.food_cost_pct)
        if weight:
            bits.append(weight)
        if self.grade:
            bits.append(self.grade)
        if self.origin:
            bits.append(self.origin)
        return " · ".join(bits)

    @property
    def remaining_pieces(self) -> int | None:
        """Piezas que quedan, al peso medio. Es una estimación, no un recuento."""
        return self._pieces_of(self.remaining_kg)

    @property
    def sold_pieces(self) -> int | None:
        """Piezas vendidas, al mismo peso medio."""
        return self._pieces_of(self.sold_kg)

    @property
    def waste_pieces(self) -> int | None:
        return self._pieces_of(self.waste_kg)

    def _pieces_of(self, kg: float) -> int | None:
        average = self.avg_piece_g
        if not average or kg <= 0:
            return None
        return int(round(kg * 1000 / average))

    @property
    def food_cost_pct(self) -> float | None:
        if self.revenue <= EPSILON:
            return None
        return round(self.sold_cost / self.revenue * 100, 2)

    @property
    def unaccounted_kg(self) -> float:
        """Lo que ni se vendió, ni se tiró, ni queda. Debería ser cero."""
        return round(self.produced_kg - self.sold_kg - self.waste_kg
                     - self.remaining_kg + self.adjust_kg, 4)


@dataclass
class ButcheryNode:
    tg: str
    date: date
    weight_before_kg: float
    waste_kg: float
    trim_kg: float
    total_cuts_kg: float
    yield_pct: float | None
    shared_with: list[str] = field(default_factory=list)   # otros primales del mismo TG
    cuts: list[CutNode] = field(default_factory=list)


@dataclass
class Label:
    """Lo que venía escrito en la etiqueta del proveedor.

    Es el principio del recorrido: sin esto, la historia de una pieza empieza
    en el muelle —«llegó y costó tanto»— y no contesta de dónde salió la carne,
    que es lo que preguntan el día que hay un problema con un lote.
    """
    supplier_lot: str | None = None
    producer_plant: str | None = None
    est_code: str | None = None
    breed: str | None = None
    origin: str | None = None
    grade: str | None = None
    slaughter_date: date | None = None
    pack_date: date | None = None
    label_product: str | None = None
    halal: bool | None = None
    has_photo: bool = False

    def __bool__(self) -> bool:
        """Vacía si el proveedor no trajo etiqueta o nadie la copió."""
        return any([self.supplier_lot, self.producer_plant, self.est_code, self.breed,
                    self.origin, self.grade, self.slaughter_date, self.pack_date,
                    self.label_product, self.halal, self.has_photo])


@dataclass
class PrimalHistory:
    serial: str
    sku: str
    status: str
    lot: str | None
    weight_kg: float
    cost: float
    received: date | None
    suspect_phantom: bool = False
    awaiting_price: bool = False
    label: Label = field(default_factory=Label)
    butchery: ButcheryNode | None = None

    # --- resumen
    @property
    def sold_kg(self) -> float:
        return round(sum(c.sold_kg for c in self._cuts), 4)

    @property
    def sold_cost(self) -> float:
        return round(sum(c.sold_cost for c in self._cuts), 4)

    @property
    def revenue(self) -> float:
        return round(sum(c.revenue for c in self._cuts), 2)

    @property
    def remaining_kg(self) -> float:
        return round(sum(c.remaining_kg for c in self._cuts), 4)

    @property
    def waste_kg(self) -> float:
        """Merma del despiece más lo tirado después."""
        butchery_waste = self.butchery.waste_kg if self.butchery else 0.0
        return round(butchery_waste + sum(c.waste_kg for c in self._cuts), 4)

    @property
    def margin(self) -> float:
        """Lo ganado con lo ya vendido, descontando lo que costó esa parte."""
        return round(self.revenue - self.sold_cost, 2)

    @property
    def food_cost_pct(self) -> float | None:
        if self.revenue <= EPSILON:
            return None
        return round(self.sold_cost / self.revenue * 100, 2)

    @property
    def recovered_pct(self) -> float | None:
        """Qué parte del coste de la pieza se ha recuperado ya en ingresos."""
        if self.cost <= EPSILON:
            return None
        return round(self.revenue / self.cost * 100, 1)

    @property
    def sold_out(self) -> bool:
        """Agotada es haberla vendido, no no haberla cortado todavía.

        Sin despiece no hay cortes, así que «lo que queda» sale cero y una
        pieza recién recibida, entera y colgada en la cámara, se anunciaba como
        agotada.
        """
        if self.butchery is None:
            return False
        return self.remaining_kg <= EPSILON

    @property
    def _cuts(self) -> list[CutNode]:
        return self.butchery.cuts if self.butchery else []


# ------------------------------------------------- reparto de los ingresos
def revenue_ratios(session: Session, restaurant_id: int) -> dict[tuple[str, int], float]:
    """Cuánto ingreso aporta cada euro de coste de un ingrediente en cada plato.

    Es `precio sin impuestos / coste por ración` del plato. Un plato con un food
    cost del 25 % devuelve 4: cada euro de materia prima trae cuatro de ingreso.
    """
    costs = costing.unit_costs(session, restaurant_id)
    ratios: dict[tuple[str, int], float] = {}
    for product in session.query(PosProduct).filter_by(restaurant_id=restaurant_id):
        recipe = product.recipe
        if recipe is None or not recipe.sale_price:
            continue
        costed = cost_recipe(recipe, costs)
        net = costed.net_price
        per_portion = costed.cost_per_portion
        if not net or per_portion <= EPSILON:
            continue
        ratio = net / per_portion
        for ingredient_id in _ingredients_of(recipe):
            ratios[(product.pos_name, ingredient_id)] = ratio
    return ratios


def _ingredients_of(recipe, _seen=()) -> set[int]:
    if recipe.id in _seen:
        return set()
    chain = _seen + (recipe.id,)
    found: set[int] = set()
    for line in recipe.lines:
        if line.ingredient_id:
            found.add(line.ingredient_id)
        elif line.sub_recipe is not None:
            found |= _ingredients_of(line.sub_recipe, chain)
    return found


def day_ratio(ratios: dict, ingredient_id: int) -> float | None:
    """Para las ventas que no dicen el plato (el conteo de descongelado),
    la media de los platos que usan ese ingrediente."""
    values = [v for (_, ing), v in ratios.items() if ing == ingredient_id]
    return round(sum(values) / len(values), 6) if values else None


# ----------------------------------------------------------------- el árbol
def history(session: Session, restaurant_id: int, serial: str) -> PrimalHistory:
    """Todo lo que ha pasado con una pieza, desde que llegó."""
    primal = (session.query(Primal)
              .filter_by(restaurant_id=restaurant_id, serial=serial.strip()).first())
    if primal is None:
        raise NotFound(f"No hay ningún primal con el serial {serial}")

    from thegrill.web.butchery import primal_cost
    out = PrimalHistory(serial=primal.serial, sku=primal.sku, status=primal.status.value,
                        lot=primal.lot, weight_kg=primal.weight_kg or 0.0,
                        cost=primal_cost(primal) or 0.0, received=primal.received_date,
                        suspect_phantom=primal.suspect_phantom,
                        awaiting_price=primal.landed_usd_per_kg is None,
                        label=Label(
                            supplier_lot=primal.supplier_lot,
                            producer_plant=primal.producer_plant,
                            est_code=primal.est_code, breed=primal.breed,
                            origin=primal.origin, grade=primal.grade,
                            slaughter_date=primal.slaughter_date,
                            pack_date=primal.pack_date,
                            label_product=primal.label_product, halal=primal.halal,
                            has_photo=bool(primal.photo_ref)))

    link = (session.query(DespiecePrimal)
            .filter_by(serial=primal.serial)
            .join(Despiece, Despiece.id == DespiecePrimal.despiece_id)
            .filter(Despiece.restaurant_id == restaurant_id).first())
    if link is None:
        return out                      # todavía entero, no hay más historia

    despiece = session.get(Despiece, link.despiece_id)
    out.butchery = ButcheryNode(
        tg=despiece.tg, date=despiece.date, weight_before_kg=despiece.weight_before_kg,
        waste_kg=despiece.waste_kg, trim_kg=despiece.trim_kg,
        total_cuts_kg=despiece.total_cuts_kg, yield_pct=despiece.yield_pct,
        shared_with=[p.serial for p in despiece.primals
                     if p.serial and p.serial != primal.serial])

    ratios = revenue_ratios(session, restaurant_id)
    names = {i.id: i for i in session.query(Ingredient).filter_by(restaurant_id=restaurant_id)}
    for cut in sorted(despiece.cuts, key=lambda c: c.cut_name):
        lot = session.get(IngredientLot, cut.lot_id) if cut.lot_id else None
        if lot is None:
            continue
        ingredient = names.get(lot.ingredient_id)
        node = CutNode(serial=lot.serial or cut.cut_name,
                       name=ingredient.name if ingredient else cut.cut_name,
                       unit=ingredient.unit.value if ingredient else "KG",
                       pieces=lot.pieces, piece_weight_g=lot.piece_weight_g,
                       nominal_piece_g=lot.nominal_piece_g,
                       grade=lot.grade, origin=lot.origin,
                       produced_kg=round(lot.qty, 4), cost=round(lot.qty * lot.unit_cost, 4),
                       unit_cost=lot.unit_cost, remaining_kg=round(lot.qty_remaining, 4),
                       is_trim=cut.is_trim)
        _fill_movements(session, node, lot, ratios)
        out.butchery.cuts.append(node)
    return out


def _fill_movements(session: Session, node: CutNode, lot: IngredientLot, ratios: dict) -> None:
    fallback = day_ratio(ratios, lot.ingredient_id)
    for mv in (session.query(IngredientMovement)
               .filter_by(restaurant_id=lot.restaurant_id, lot_id=lot.id)
               .order_by(IngredientMovement.date, IngredientMovement.id)):
        if mv.kind == MovementKind.SALE:
            kg = round(-mv.qty, 6)
            cost = round(mv.cost or 0.0, 6)
            ratio = ratios.get((mv.source_ref, lot.ingredient_id))
            if ratio is None:
                ratio = fallback
            revenue = round(cost * ratio, 4) if ratio else 0.0
            node.sold_kg = round(node.sold_kg + kg, 6)
            node.sold_cost = round(node.sold_cost + cost, 6)
            node.revenue = round(node.revenue + revenue, 4)
            node.sales.append(SaleLine(date=mv.date, dish=mv.source_ref or "—", kg=kg,
                                       cost=cost, revenue=revenue, source=mv.source))
        elif mv.kind == MovementKind.WASTE:
            node.waste_kg = round(node.waste_kg + -mv.qty, 6)
        elif mv.kind == MovementKind.ADJUST:
            node.adjust_kg = round(node.adjust_kg + mv.qty, 6)
        elif mv.kind == MovementKind.MOVE:
            # Ni venta ni merma: cambió de sitio o de número, y se sigue por él.
            node.moved_kg = round(node.moved_kg + -mv.qty, 6)


def search(session: Session, restaurant_id: int, term: str) -> list[Primal]:
    """Busca primales por serial o por lote de recepción."""
    term = (term or "").strip()
    if not term:
        return []
    like = f"%{term}%"
    # También por lo que trae la etiqueta del proveedor: quien llama para
    # retirar algo no dice nuestro número de pieza —no lo conoce—, dice su
    # lote, o el matadero, o el número de registro.
    return (session.query(Primal)
            .filter(Primal.restaurant_id == restaurant_id,
                    (Primal.serial.ilike(like)) | (Primal.lot.ilike(like))
                    | (Primal.supplier_lot.ilike(like))
                    | (Primal.producer_plant.ilike(like))
                    | (Primal.est_code.ilike(like)))
            .order_by(Primal.serial).limit(50).all())
