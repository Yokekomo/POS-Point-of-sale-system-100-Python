"""[01446] Historia de un primal: qué salió de él, dónde fue y qué dejó.

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
                             Primal, PrimalStatus, Site, WeightSale)
from thegrill.web import costing, exacto

EPSILON = 1e-9


class NotFound(LookupError):
    """[01447] No hay ninguna pieza con ese número de serie."""


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
    # [01494] Dónde está, y lo que salió de él con su propio número. Un corte que viaja
    # al local de la playa o unas piezas que salen del arcón nacen con su
    # número colgando de este: si no se les sigue, la historia de la pieza se
    # acaba en el muelle del obrador y el día que hay que retirar un lote no
    # se sabe a qué local llamar.
    site: str | None = None
    children: list["CutNode"] = field(default_factory=list)

    @property
    def rama(self) -> list["CutNode"]:
        """[01457] Este nodo y todo lo que salió de él, de cualquier profundidad."""
        out = [self]
        for hijo in self.children:
            out.extend(hijo.rama)
        return out

    @property
    def avg_piece_g(self) -> float | None:
        """[01458] Peso medio real: los kilos que salieron entre las piezas contadas."""
        if not self.pieces or self.pieces <= 0 or self.produced_kg <= 0:
            return self.piece_weight_g
        return round(self.produced_kg * 1000 / self.pieces, 1)

    @property
    def piece_gap_pct(self) -> float | None:
        """[01459] Cuánto se desvía el corte real del objetivo de la hoja."""
        real, target = self.avg_piece_g, self.nominal_piece_g
        if not real or not target:
            return None
        return round((real - target) / target * 100, 1)

    def label_with(self, money: bool = True) -> str:
        """[01460] «330 g (~354 g · 31,8 % FC) · MB9+ · AUS».

        Delante el peso de carta, que es lo que se vende y lo que manda en el
        escandallo. Entre paréntesis la realidad: el promedio que salió y el
        food cost al que está saliendo el corte. El food cost solo aparece
        cuando ya se ha vendido algo: antes de eso no hay número que dar.

        Y solo para quien ve dinero. El food cost iba dentro de esta etiqueta,
        que se pintaba igual para todos: el carnicero tiene que saber el peso
        de sus piezas y su calidad, pero a qué porcentaje sale el corte no es
        cosa suya, igual que no lo es el precio del kilo.
        """
        from thegrill.web.butchery import piece_label
        bits = []
        weight = piece_label(self.nominal_piece_g, self.avg_piece_g,
                             self.food_cost_pct if money else None)
        if weight:
            bits.append(weight)
        if self.grade:
            bits.append(self.grade)
        if self.origin:
            bits.append(self.origin)
        return " · ".join(bits)

    @property
    def label(self) -> str:
        """[01461] La etiqueta completa, con el food cost. Para quien ve dinero."""
        return self.label_with(True)

    @property
    def remaining_pieces(self) -> int | None:
        """[01462] Piezas que quedan, al peso medio. Es una estimación, no un recuento."""
        return self._pieces_of(self.remaining_kg)

    @property
    def sold_pieces(self) -> int | None:
        """[01463] Piezas vendidas, al mismo peso medio."""
        return self._pieces_of(self.sold_kg)

    @property
    def waste_pieces(self) -> int | None:
        """[01464] Piezas tiradas, al peso medio. Estimación, como las demás."""
        return self._pieces_of(self.waste_kg)

    def _pieces_of(self, kg: float) -> int | None:
        """[01465] Cuántas piezas son esos kilos, al peso medio del corte.

        Sin peso medio no hay cuenta que hacer: un corte que se vende entero no
        sale en piezas y devuelve nada en vez de un cero que engañaría.
        """
        average = self.avg_piece_g
        if not average or kg <= 0:
            return None
        return int(round(kg * 1000 / average))

    @property
    def food_cost_pct(self) -> float | None:
        """[01466] A qué porcentaje sale el corte: lo que costó lo vendido sobre lo cobrado.

        Sin ingreso no hay porcentaje —dividir por cero— y se devuelve nada: un
        corte recién hecho no tiene food cost todavía, no lo tiene del 0 %.
        """
        if self.revenue <= EPSILON:
            return None
        return round(self.sold_cost / self.revenue * 100, 2)

    @property
    def by_dish(self) -> list[tuple[str, float, float]]:
        """[01467] En qué platos ha acabado este corte: plato, kilos e ingreso.

        Las ventas están una a una, con su día, y así se ve el detalle pero no
        se ve lo importante. La pregunta que se hace de verdad es «¿dónde ha
        ido?» —sobre todo con lo aprovechado: el recorte de un lomo de cien
        euros el kilo acaba en la hamburguesa o en el tartar, y saber en cuál
        de los dos es lo que dice si ese recorte se está pagando—.
        """
        juntos: dict[str, list[float]] = {}
        for line in self.sales:
            fila = juntos.setdefault(line.dish or "—", [0.0, 0.0])
            fila[0] = round(fila[0] + line.kg, 6)
            fila[1] = round(fila[1] + line.revenue, 4)
        return [(plato, kg, exacto.eur(ingreso))
                for plato, (kg, ingreso) in sorted(juntos.items(),
                                                   key=lambda x: -x[1][0])]

    @property
    def margin(self) -> float:
        """[01468] Lo ganado con este corte: lo cobrado menos lo que costó esa parte.

        Por corte y no solo por pieza, que es donde se ve la verdad: de un
        mismo primal, el filete deja dinero y el recorte se lo come. Sin esta
        columna se compara el food cost de dos cortes sin saber cuál de los
        dos paga el primal.
        """
        return exacto.eur(self.revenue - self.sold_cost)

    @property
    def unaccounted_kg(self) -> float:
        """[01469] Lo que ni se vendió, ni se tiró, ni se fue, ni queda. Debe ser cero.

        Lo que se fue cuenta: un corte que viaja al local de la playa, o unas
        piezas que salen del arcón con su propio número, dejan de estar aquí
        pero están perfectamente seguidas —tienen su número y su fila—. Sin
        restarlo, toda la carne trasladada salía marcada en rojo como si
        hubiera desaparecido, y una columna que avisa de lo que sí cuadra deja
        de mirarse a la semana.
        """
        return round(self.produced_kg - self.sold_kg - self.waste_kg
                     - self.moved_kg - self.remaining_kg + self.adjust_kg, 4)


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
    # [01495] Qué parte de este despiece es de esta pieza. Con una sola, todo. Con
    # tres, lo que pesaba ella entre lo que pesaban las tres: los cortes salen
    # revueltos y nadie puede decir de cuál de las tres salió este filete, pero
    # apuntarle a cada una el despiece entero era peor —tres piezas de nueve
    # kilos vendiendo veintisiete cada una— y dejaba el food cost de la pieza a
    # un tercio de la verdad.
    share: float = 1.0

    @property
    def shared(self) -> bool:
        """[01470] Si el despiece llevaba más de una pieza dentro."""
        return bool(self.shared_with)

    @property
    def share_pct(self) -> float:
        """[01471] La parte de este despiece que es de esta pieza, en tanto por ciento."""
        return round(self.share * 100, 1)

    @property
    def pieces(self) -> int:
        """[01472] Cuántas raciones salieron de la pieza, sumando todos los cortes.

        Es el número con el que se mira un despiece de un vistazo: de nueve
        kilos y medio salieron treinta y ocho raciones, y eso es lo que se
        compara con el de la semana pasada.
        """
        return sum(c.pieces or 0 for c in self.cuts)

    @property
    def avg_piece_g(self) -> float | None:
        """[01473] A cuántos gramos salió la ración media de este despiece.

        Solo cuenta lo que sale en raciones: un corte que sale entero para
        cortarlo delante del cliente no tiene piezas, y meter sus kilos en la
        media la hunde sin que nadie haya cortado ancho ni estrecho.
        """
        piezas = self.pieces
        if not piezas:
            return None
        kg = sum(c.produced_kg for c in self.cuts if c.pieces)
        return round(kg * 1000 / piezas, 1) if kg > 0 else None


@dataclass
class Label:
    """[01448] Lo que venía escrito en la etiqueta del proveedor.

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
    # [01496] Cómo bajó del camión: no es lo mismo llegar congelada que llegar fresca
    # y acabar en el arcón el mismo día.
    arrival: str | None = None
    arrival_c: float | None = None
    frozen_on_arrival: bool | None = None

    def __bool__(self) -> bool:
        """[01474] Vacía si el proveedor no trajo etiqueta o nadie la copió."""
        return any([self.supplier_lot, self.producer_plant, self.est_code, self.breed,
                    self.origin, self.grade, self.slaughter_date, self.pack_date,
                    self.label_product, self.halal, self.has_photo,
                    self.arrival, self.arrival_c, self.frozen_on_arrival])


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
    # [01497] Lo que se limpió de la pieza antes de cortarla —o en vez de cortarla—.
    # Cuelga del primal y no del despiece: una pieza que se limpia y se vende
    # entera no tiene despiece, y su historia se acababa en «llegó».
    trims: list[CutNode] = field(default_factory=list)
    # [01498] Y lo que se cortó y se cobró al peso, que no pasa por el escandallo ni
    # deja lote: sin esto, una pieza madurada vendida entera al corte salía en
    # la trazabilidad como carne que no se vendió nunca.
    weight_sales: list[SaleLine] = field(default_factory=list)

    # [01499] --- resumen
    #
    # Lo del despiece se cuenta por la parte que le toca a esta pieza; lo suyo
    # —limpiezas y ventas al peso— entero, porque es suyo y de nadie más.
    @property
    def share(self) -> float:
        """[01475] Qué parte del despiece le toca. Sin despiece, todo lo suyo es suyo."""
        return self.butchery.share if self.butchery else 1.0

    def _suma(self, campo: str) -> float:
        """[01476] Suma un campo de todos los cortes, cada cosa con su parte.

        Lo que salió del despiece se apunta por la parte que le toca a esta pieza
        —un despiece de tres piezas no vendió tres veces lo mismo—; lo suyo
        —limpiezas y lo que colgó de ellas— entero, porque no lo comparte.
        """
        del_despiece = sum(getattr(c, campo) for c in self._branch)
        propio = sum(getattr(c, campo) for c in self._propios)
        return del_despiece * self.share + propio

    @property
    def sold_kg(self) -> float:
        """[01477] Kilos vendidos de esta pieza, por escandallo y al peso."""
        return round(self._suma("sold_kg") + self.weight_sold_kg, 4)

    @property
    def sold_cost(self) -> float:
        """[01478] Lo que costó la carne vendida de esta pieza."""
        return round(self._suma("sold_cost") + self.weight_cost, 4)

    @property
    def revenue(self) -> float:
        """[01479] Lo ingresado con esta pieza: su parte de los platos más el corte al peso."""
        return exacto.eur(self._suma("revenue") + self.weight_revenue)

    @property
    def remaining_kg(self) -> float:
        """[01480] Lo que queda en cámara de esta pieza, sumando todos sus cortes."""
        return round(self._suma("remaining_kg"), 4)

    @property
    def moved_kg(self) -> float:
        """[01481] Lo que se fue a otra sede o salió del arcón con su propio número."""
        return round(self._suma("moved_kg"), 4)

    @property
    def waste_kg(self) -> float:
        """[01482] Merma del despiece más lo tirado después."""
        del_despiece = (self.butchery.waste_kg if self.butchery else 0.0)
        return round((del_despiece + sum(c.waste_kg for c in self._branch)) * self.share
                     + sum(c.waste_kg for c in self._propios), 4)

    # [01500] --- la venta al corte
    @property
    def weight_sold_kg(self) -> float:
        """[01483] Kilos cortados y cobrados al peso, sin pasar por el escandallo."""
        return round(sum(v.kg for v in self.weight_sales), 4)

    @property
    def weight_revenue(self) -> float:
        """[01484] Lo cobrado en la venta al corte."""
        return exacto.eur(sum(v.revenue for v in self.weight_sales))

    @property
    def weight_cost(self) -> float:
        """[01485] Lo que costó la carne que se vendió al corte."""
        return round(sum(v.cost for v in self.weight_sales), 4)

    @property
    def margin(self) -> float:
        """[01486] Lo ganado con lo ya vendido, descontando lo que costó esa parte."""
        return exacto.eur(self.revenue - self.sold_cost)

    @property
    def food_cost_pct(self) -> float | None:
        """[01487] El food cost real de la pieza: lo que costó lo vendido sobre lo cobrado.

        Mientras no se haya vendido nada no hay número que dar, y se devuelve
        nada en vez de inventarse un cero.
        """
        if self.revenue <= EPSILON:
            return None
        return round(self.sold_cost / self.revenue * 100, 2)

    @property
    def recovered_pct(self) -> float | None:
        """[01488] Qué parte del coste de la pieza se ha recuperado ya en ingresos."""
        if self.cost <= EPSILON:
            return None
        return round(self.revenue / self.cost * 100, 1)

    @property
    def sold_out(self) -> bool:
        """[01489] Agotada es haberla vendido, no no haberla cortado todavía.

        Sin despiece no hay cortes, así que «lo que queda» sale cero y una
        pieza recién recibida, entera y colgada en la cámara, se anunciaba como
        agotada.
        """
        if self.butchery is None:
            return False
        return self.remaining_kg <= EPSILON

    @property
    def _cuts(self) -> list[CutNode]:
        """[01490] Los cortes de primer nivel del despiece, si lo hubo."""
        return self.butchery.cuts if self.butchery else []

    @property
    def _branch(self) -> list[CutNode]:
        """[01491] Los cortes del despiece y todo lo que salió de ellos."""
        return [n for c in self._cuts for n in c.rama]

    @property
    def _propios(self) -> list[CutNode]:
        """[01492] Las limpiezas de esta pieza y lo que salió de ellas."""
        return [n for t in self.trims for n in t.rama]

    @property
    def todo(self) -> list[CutNode]:
        """[01493] Todo lo que lleva el número de esta pieza, para recorrerlo."""
        return self._branch + self._propios


# ------------------------------------------------- reparto de los ingresos
def revenue_ratios(session: Session, restaurant_id: int) -> dict[tuple[str, int], float]:
    """[01449] Cuánto ingreso aporta cada euro de coste de un ingrediente en cada plato.

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
    """[01450] Los ingredientes de una receta, entrando en las subrecetas.

    Lleva la cuenta de por dónde ha pasado: una salsa que se llama a sí misma
    —o dos que se llaman la una a la otra— colgaría el reparto de ingresos en
    un bucle sin fin, y con las recetas las hace cualquiera sin darse cuenta.
    """
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
    """[01451] Para las ventas que no dicen el plato (el conteo de descongelado),
    la media de los platos que usan ese ingrediente."""
    values = [v for (_, ing), v in ratios.items() if ing == ingredient_id]
    return round(sum(values) / len(values), 6) if values else None


# ----------------------------------------------------------------- el árbol
def history(session: Session, restaurant_id: int, serial: str) -> PrimalHistory:
    """[01452] Todo lo que ha pasado con una pieza, desde que llegó."""
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
                            has_photo=bool(primal.photo_ref),
                            arrival=(primal.arrival.value if primal.arrival else None),
                            arrival_c=primal.arrival_c,
                            frozen_on_arrival=primal.frozen_on_arrival))

    ratios = revenue_ratios(session, restaurant_id)
    names = {i.id: i for i in session.query(Ingredient).filter_by(restaurant_id=restaurant_id)}
    sedes = {s.id: s.name for s in session.query(Site).filter_by(restaurant_id=restaurant_id)}

    # [01501] Lo que se cortó y se cobró al peso. No deja lote ni pasa por el
    # escandallo —cada trozo pesa lo que pesa—, así que si no se lee de su
    # propia tabla no aparece en ninguna parte: una pieza madurada vendida
    # entera al corte salía como carne que no se vendió nunca.
    out.weight_sales = [
        SaleLine(date=v.date, dish=v.dish or "—", kg=round(v.grams / 1000, 4),
                 cost=round(v.cost or 0.0, 4), revenue=round(v.price or 0.0, 4),
                 source="weight")
        for v in (session.query(WeightSale)
                  .filter_by(restaurant_id=restaurant_id, serial=primal.serial)
                  .order_by(WeightSale.date, WeightSale.id))]

    link = (session.query(DespiecePrimal)
            .filter_by(serial=primal.serial)
            .join(Despiece, Despiece.id == DespiecePrimal.despiece_id)
            .filter(Despiece.restaurant_id == restaurant_id).first())

    de_cortes: set[int] = set()
    if link is not None:
        despiece = session.get(Despiece, link.despiece_id)
        out.butchery = ButcheryNode(
            tg=despiece.tg, date=despiece.date, weight_before_kg=despiece.weight_before_kg,
            waste_kg=despiece.waste_kg, trim_kg=despiece.trim_kg,
            total_cuts_kg=despiece.total_cuts_kg, yield_pct=despiece.yield_pct,
            shared_with=[p.serial for p in despiece.primals
                         if p.serial and p.serial != primal.serial],
            share=_share(session, despiece, primal))

        for cut in sorted(despiece.cuts, key=lambda c: c.cut_name):
            lot = session.get(IngredientLot, cut.lot_id) if cut.lot_id else None
            if lot is None:
                continue
            de_cortes.add(lot.id)
            out.butchery.cuts.append(
                _node(session, lot, ratios, names, sedes,
                      nombre=cut.cut_name, is_trim=cut.is_trim))

    # [01502] Las limpiezas: lo que se quitó de la pieza y se guardó con su número.
    # Cuelgan del primal, no del despiece, porque una pieza se limpia antes de
    # cortarla y a veces en vez de cortarla.
    for lot in (session.query(IngredientLot)
                .filter_by(restaurant_id=restaurant_id, parent_serial=primal.serial)
                .order_by(IngredientLot.serial)):
        if lot.id in de_cortes or "·" in (lot.serial or ""):
            continue                     # un corte, o algo que salió de un corte
        out.trims.append(_node(session, lot, ratios, names, sedes, is_trim=True))
    return out


def _share(session: Session, despiece, primal) -> float:
    """[01453] Qué parte de un despiece de varias piezas es de esta.

    Por peso, que es lo único que las distingue: los cortes salen revueltos y
    nadie puede decir de cuál de las tres salió este filete. Sin esto, cada
    pieza de un despiece de tres se apuntaba el despiece **entero** —el triple
    de kilos vendidos y el triple de ingreso—, y el food cost de cada una salía
    a un tercio de la verdad.
    """
    cuantas = len(despiece.primals)
    if cuantas <= 1:
        return 1.0
    a_partes_iguales = round(1 / cuantas, 6)

    pesos: dict[str, float] = {}
    for fila in despiece.primals:
        if not fila.serial:
            continue
        kg = fila.label_kg or 0.0
        if kg <= EPSILON:
            pieza = (session.query(Primal)
                     .filter_by(restaurant_id=despiece.restaurant_id,
                                serial=fila.serial).first())
            kg = (pieza.received_kg or pieza.weight_kg or 0.0) if pieza else 0.0
        pesos[fila.serial] = kg

    total = sum(pesos.values())
    mia = pesos.get(primal.serial, 0.0)
    if total <= EPSILON or mia <= EPSILON:
        # [01503] Sin pesos no hay reparto posible: a partes iguales, que es lo único
        # que no favorece a ninguna.
        return a_partes_iguales
    return round(mia / total, 6)


def _node(session: Session, lot: IngredientLot, ratios: dict, names: dict,
          sedes: dict, nombre: str | None = None, is_trim: bool = False,
          hondo: int = 0) -> CutNode:
    """[01454] Un lote con su historia y, colgando, lo que salió de él con otro número."""
    ingredient = names.get(lot.ingredient_id)
    node = CutNode(serial=lot.serial or nombre or "—",
                   name=ingredient.name if ingredient else (nombre or lot.serial or "—"),
                   unit=ingredient.unit.value if ingredient else "KG",
                   pieces=lot.pieces, piece_weight_g=lot.piece_weight_g,
                   nominal_piece_g=lot.nominal_piece_g,
                   grade=lot.grade, origin=lot.origin,
                   produced_kg=round(lot.qty, 4), cost=round(lot.qty * lot.unit_cost, 4),
                   unit_cost=lot.unit_cost, remaining_kg=round(lot.qty_remaining, 4),
                   is_trim=is_trim, site=sedes.get(lot.site_id))
    _fill_movements(session, node, lot, ratios)
    if hondo >= 6 or not lot.serial:
        return node                       # una cadena así no existe en una cocina
    for hijo in (session.query(IngredientLot)
                 .filter(IngredientLot.restaurant_id == lot.restaurant_id,
                         IngredientLot.serial.like(f"{lot.serial}·%"),
                         IngredientLot.id != lot.id)
                 .order_by(IngredientLot.serial)):
        # [01504] Solo los hijos directos: «8017-01·T1» sí, «8017-01·T1·D1» cuelga de
        # aquel y ya lo recoge él.
        if "·" in hijo.serial[len(lot.serial) + 1:]:
            continue
        node.children.append(
            _node(session, hijo, ratios, names, sedes, is_trim=is_trim, hondo=hondo + 1))
    return node


def _fill_movements(session: Session, node: CutNode, lot: IngredientLot, ratios: dict) -> None:
    """[01455] Vuelca sobre el nodo lo que pasó con ese lote: ventas, merma y traslados.

    Cada venta se lleva su parte del ingreso por el plato en el que acabó. Si
    la venta no dice el plato —el conteo del descongelado no lo dice— se usa
    la media de los platos que llevan ese ingrediente, que es mejor que
    apuntarle cero ingreso a carne que se vendió de verdad.
    """
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
            # [01505] Ni venta ni merma: cambió de sitio o de número, y se sigue por él.
            node.moved_kg = round(node.moved_kg + -mv.qty, 6)


def search(session: Session, restaurant_id: int, term: str) -> list[Primal]:
    """[01456] Busca primales por serial o por lote de recepción."""
    term = (term or "").strip()
    if not term:
        return []
    like = f"%{term}%"
    # [01506] También por lo que trae la etiqueta del proveedor: quien llama para
    # retirar algo no dice nuestro número de pieza —no lo conoce—, dice su
    # lote, o el matadero, o el número de registro.
    return (session.query(Primal)
            .filter(Primal.restaurant_id == restaurant_id,
                    (Primal.serial.ilike(like)) | (Primal.lot.ilike(like))
                    | (Primal.supplier_lot.ilike(like))
                    | (Primal.producer_plant.ilike(like))
                    | (Primal.est_code.ilike(like)))
            .order_by(Primal.serial).limit(50).all())
