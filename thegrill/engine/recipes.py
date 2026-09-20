"""Escandallo: coste de una receta, food cost y desglose por ingrediente.

Modelo de trabajo, el de cualquier cocina:

- Un **ingrediente base** es «leche». La marca y el precio viven en cada lote,
  así el escandallo no se rompe cuando cambia el proveedor.
- Una línea de receta lleva el **peso neto**, el que acaba en el plato, y su
  **merma de limpieza**. Lo que sale del almacén es el peso bruto:
  `bruto = neto / (1 - merma%)`.
- Una receta puede usar otra receta (un fondo, una salsa). Se explota hacia
  abajo hasta llegar a ingredientes base.
- El food cost se calcula sobre el precio SIN impuestos. Sobre el PVP saldría
  un número más bonito y falso.

El motor es puro: recibe los costes unitarios ya resueltos y no toca la base de
datos. Quien los resuelve es `web.costing`.
"""
from dataclasses import dataclass, field

EPSILON = 1e-9


class CircularRecipe(ValueError):
    """Una receta se incluye a sí misma. Sin esto el cálculo no terminaría."""


class UnknownCost(ValueError):
    """No hay precio para un ingrediente. Se avisa, nunca se cuenta como cero."""


class BadWaste(ValueError):
    """Una merma del 100 % dejaría el peso bruto en infinito."""


def gross_qty(net_qty: float, waste_pct: float) -> float:
    """Lo que hay que sacar del almacén para que quede `net_qty` en el plato."""
    if waste_pct < 0 or waste_pct >= 100:
        raise BadWaste(f"Merma fuera de rango: {waste_pct}%")
    return net_qty / (1 - waste_pct / 100)


@dataclass
class LineCost:
    label: str
    unit: str
    is_sub_recipe: bool
    net_qty: float
    waste_pct: float
    gross_qty: float
    unit_cost: float | None      # None = precio desconocido
    cost: float
    waste_cost: float            # lo que cuesta lo que se tira al limpiar
    share_pct: float = 0.0       # peso de esta línea en el coste del plato

    @property
    def known(self) -> bool:
        return self.unit_cost is not None


@dataclass
class RecipeCost:
    code: str
    name: str
    portions: int
    total_cost: float
    lines: list[LineCost] = field(default_factory=list)
    sale_price: float | None = None
    vat_pct: float = 0.0
    missing: list[str] = field(default_factory=list)

    @property
    def cost_per_portion(self) -> float:
        return round(self.total_cost / self.portions, 4) if self.portions else self.total_cost

    @property
    def net_price(self) -> float | None:
        """PVP sin impuestos. El food cost se mide contra esto."""
        if self.sale_price is None:
            return None
        return round(self.sale_price / (1 + self.vat_pct / 100), 4)

    @property
    def food_cost_pct(self) -> float | None:
        net = self.net_price
        if not net:
            return None
        return round(self.cost_per_portion / net * 100, 2)

    @property
    def margin_per_portion(self) -> float | None:
        net = self.net_price
        return round(net - self.cost_per_portion, 4) if net is not None else None

    @property
    def waste_cost(self) -> float:
        """Dinero que se va en las mermas de limpieza de esta receta."""
        return round(sum(l.waste_cost for l in self.lines), 4)

    @property
    def complete(self) -> bool:
        return not self.missing

    def worst_lines(self, n: int = 3) -> list[LineCost]:
        """Dónde se va el dinero: las líneas que más pesan."""
        return self.lines[:n]


# --------------------------------------------------------------- explosión
def explode(recipe, units: float = 1.0, _seen: tuple = ()) -> dict[int, float]:
    """Cantidades BRUTAS por ingrediente para producir `units` veces la receta.

    Es lo que hay que descontar del almacén. Las sub-recetas se explotan hasta
    llegar a ingredientes base.
    """
    if recipe.id in _seen:
        raise CircularRecipe(f"La receta «{recipe.name}» se incluye a sí misma")
    chain = _seen + (recipe.id,)

    totals: dict[int, float] = {}
    for line in recipe.lines:
        amount = gross_qty(line.qty, line.waste_pct) * units
        if line.sub_recipe_id and line.sub_recipe is not None:
            sub = line.sub_recipe
            produced = sub.yield_qty or 0
            if produced <= EPSILON:
                raise UnknownCost(f"La elaboración «{sub.name}» no dice cuánto produce")
            for ing_id, qty in explode(sub, amount / produced, chain).items():
                totals[ing_id] = totals.get(ing_id, 0.0) + qty
        elif line.ingredient_id:
            totals[line.ingredient_id] = totals.get(line.ingredient_id, 0.0) + amount
    return {k: round(v, 6) for k, v in totals.items()}


# ------------------------------------------------------------------ coste
def unit_cost_of(recipe, costs: dict[int, float], _seen: tuple = ()) -> float | None:
    """Cuánto cuesta una unidad de una elaboración (por kg, litro o unidad)."""
    produced = recipe.yield_qty or 0
    if produced <= EPSILON:
        raise UnknownCost(f"La elaboración «{recipe.name}» no dice cuánto produce")
    result = cost_recipe(recipe, costs, _seen)
    return None if result.missing else round(result.total_cost / produced, 6)


def cost_recipe(recipe, costs: dict[int, float], _seen: tuple = ()) -> RecipeCost:
    """Coste de la receta con su desglose línea a línea.

    `costs` mapea id de ingrediente a precio por unidad base. Un ingrediente que
    no esté ahí sale como precio desconocido y se lista en `missing`.
    """
    if recipe.id in _seen:
        raise CircularRecipe(f"La receta «{recipe.name}» se incluye a sí misma")
    chain = _seen + (recipe.id,)

    out = RecipeCost(code=recipe.code, name=recipe.name,
                     portions=max(1, recipe.portions or 1), total_cost=0.0,
                     sale_price=recipe.sale_price, vat_pct=recipe.vat_pct or 0.0)

    for line in recipe.lines:
        gross = gross_qty(line.qty, line.waste_pct)
        if line.sub_recipe_id and line.sub_recipe is not None:
            sub = line.sub_recipe
            price = unit_cost_of(sub, costs, chain)
            label, unit, is_sub = sub.name, (sub.yield_unit.value if sub.yield_unit else ""), True
            if price is None:
                out.missing.append(sub.name)
        elif line.ingredient_id and line.ingredient is not None:
            ing = line.ingredient
            price = costs.get(ing.id)
            label, unit, is_sub = ing.name, ing.unit.value, False
            if price is None:
                out.missing.append(ing.name)
        else:
            continue

        cost = round(gross * price, 6) if price is not None else 0.0
        waste_cost = round((gross - line.qty) * price, 6) if price is not None else 0.0
        out.lines.append(LineCost(label=label, unit=unit, is_sub_recipe=is_sub,
                                  net_qty=line.qty, waste_pct=line.waste_pct,
                                  gross_qty=round(gross, 6), unit_cost=price,
                                  cost=cost, waste_cost=waste_cost))
        out.total_cost += cost

    out.total_cost = round(out.total_cost, 6)
    for line in out.lines:
        line.share_pct = round(line.cost / out.total_cost * 100, 2) if out.total_cost else 0.0
    out.lines.sort(key=lambda l: (-l.cost, l.label))     # primero donde más dinero hay
    return out


# ------------------------------------------------- comparativa de la carta
@dataclass
class MenuLine:
    code: str
    name: str
    cost_per_portion: float
    sale_price: float | None
    food_cost_pct: float | None
    margin: float | None
    complete: bool


def menu_ranking(costed: list[RecipeCost]) -> list[MenuLine]:
    """La carta ordenada por food cost: arriba lo que peor margen deja."""
    rows = [MenuLine(c.code, c.name, c.cost_per_portion, c.sale_price,
                     c.food_cost_pct, c.margin_per_portion, c.complete)
            for c in costed]
    rows.sort(key=lambda r: (r.food_cost_pct is None, -(r.food_cost_pct or 0)))
    return rows


# ================================================== árbol completo de costes
@dataclass
class TreeNode:
    """Un nodo del escandallo desplegado, con lo que cuesta dentro del plato.

    Ejemplo real: Cheese burger → Burger → Burger patty → Beef for burger.
    Cada nivel dice lo que aporta al plato, hasta llegar al ingrediente madre.
    """
    label: str
    kind: str                   # dish / prep / ingredient
    unit: str
    net_qty: float              # cantidad dentro del plato entero
    gross_qty: float
    waste_pct: float
    unit_cost: float | None
    cost: float
    share_pct: float = 0.0
    depth: int = 0
    children: list["TreeNode"] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def walk(self):
        """Recorre el árbol de arriba abajo, en orden de lectura."""
        yield self
        for child in self.children:
            yield from child.walk()


def cost_tree(recipe, costs: dict[int, float], units: float = 1.0,
              _seen: tuple = (), _depth: int = 0) -> TreeNode:
    """Despliega la receta entera con el coste de cada nivel."""
    if recipe.id in _seen:
        raise CircularRecipe(f"La receta «{recipe.name}» se incluye a sí misma")
    chain = _seen + (recipe.id,)

    kind = "prep" if _depth else "dish"
    unit = recipe.yield_unit.value if getattr(recipe, "yield_unit", None) else ""
    node = TreeNode(label=recipe.name, kind=kind, unit=unit, net_qty=units, gross_qty=units,
                    waste_pct=0.0, unit_cost=None, cost=0.0, depth=_depth)

    for line in recipe.lines:
        gross = gross_qty(line.qty, line.waste_pct) * units
        if line.sub_recipe_id and line.sub_recipe is not None:
            sub = line.sub_recipe
            produced = sub.yield_qty or 0
            if produced <= EPSILON:
                raise UnknownCost(f"La elaboración «{sub.name}» no dice cuánto produce")
            child = cost_tree(sub, costs, gross / produced, chain, _depth + 1)
            child.label = sub.name
            child.net_qty = round(line.qty * units, 6)
            child.gross_qty = round(gross, 6)
            child.waste_pct = line.waste_pct
            child.unit = sub.yield_unit.value if sub.yield_unit else ""
            child.unit_cost = (round(child.cost / gross, 6) if gross > EPSILON else None)
            node.children.append(child)
            node.cost += child.cost
        elif line.ingredient_id and line.ingredient is not None:
            ing = line.ingredient
            price = costs.get(ing.id)
            cost = round(gross * price, 6) if price is not None else 0.0
            node.children.append(TreeNode(
                label=ing.name, kind="ingredient", unit=ing.unit.value,
                net_qty=round(line.qty * units, 6), gross_qty=round(gross, 6),
                waste_pct=line.waste_pct, unit_cost=price, cost=cost, depth=_depth + 1))
            node.cost += cost

    node.cost = round(node.cost, 6)
    if _depth == 0:
        _assign_shares(node, node.cost)
    return node


def _assign_shares(node: TreeNode, total: float) -> None:
    for child in node.walk():
        child.share_pct = round(child.cost / total * 100, 2) if total else 0.0


@dataclass
class RollupLine:
    """Un ingrediente madre sumado por todos los caminos por los que entra."""
    name: str
    unit: str
    gross_qty: float
    cost: float
    waste_cost: float
    share_pct: float = 0.0
    paths: int = 1        # por cuántas ramas distintas entra al plato


def ingredient_rollup(root: TreeNode) -> list[RollupLine]:
    """Coste total por ingrediente madre en todo el árbol, de mayor a menor.

    Es la respuesta a «dónde se pierde el dinero»: si la carne entra por la
    hamburguesa y otra vez por la salsa, aquí sale sumada una sola vez.
    """
    acc: dict[tuple[str, str], RollupLine] = {}
    for node in root.walk():
        if node.kind != "ingredient":
            continue
        key = (node.label, node.unit)
        waste = round((node.gross_qty - node.net_qty) * node.unit_cost, 6) \
            if node.unit_cost is not None else 0.0
        if key in acc:
            row = acc[key]
            row.gross_qty = round(row.gross_qty + node.gross_qty, 6)
            row.cost = round(row.cost + node.cost, 6)
            row.waste_cost = round(row.waste_cost + waste, 6)
            row.paths += 1
        else:
            acc[key] = RollupLine(node.label, node.unit, node.gross_qty, node.cost, waste)
    rows = sorted(acc.values(), key=lambda r: (-r.cost, r.name))
    total = sum(r.cost for r in rows)
    for row in rows:
        row.share_pct = round(row.cost / total * 100, 2) if total else 0.0
    return rows


def prep_costs(root: TreeNode) -> list[TreeNode]:
    """Las elaboraciones que intervienen, de la más cara a la más barata."""
    preps = [n for n in root.walk() if n.kind == "prep"]
    return sorted(preps, key=lambda n: (-n.cost, n.label))
