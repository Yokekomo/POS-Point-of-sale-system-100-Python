"""Escandallo: explosión de recetas, coste y desglose."""
import pytest

from thegrill.engine.recipes import (BadWaste, CircularRecipe, UnknownCost, cost_recipe,
                                     explode, gross_qty, menu_ranking)


# --- objetos mínimos con la misma forma que el modelo, para probar el cálculo solo
class Ing:
    def __init__(self, id, name, unit="KG"):
        self.id, self.name = id, name
        self.unit = type("U", (), {"value": unit})()


class Line:
    def __init__(self, qty, ingredient=None, sub=None, waste_pct=0.0):
        self.qty, self.waste_pct = qty, waste_pct
        self.ingredient = ingredient
        self.ingredient_id = ingredient.id if ingredient else None
        self.sub_recipe = sub
        self.sub_recipe_id = sub.id if sub else None


class Rec:
    def __init__(self, id, code, name, lines, portions=1, sale_price=None, vat_pct=0.0,
                 yield_qty=None, yield_unit="L"):
        self.id, self.code, self.name, self.lines = id, code, name, lines
        self.portions, self.sale_price, self.vat_pct = portions, sale_price, vat_pct
        self.yield_qty = yield_qty
        self.yield_unit = type("U", (), {"value": yield_unit})() if yield_qty else None


LECHE, HARINA, GAMBA = Ing(1, "Leche", "L"), Ing(2, "Harina"), Ing(3, "Gamba")
PRECIOS = {1: 0.80, 2: 1.20, 3: 18.00}


# --------------------------------------------------------------- peso bruto
def test_gross_weight_accounts_for_cleaning_waste():
    assert gross_qty(1.0, 0) == 1.0
    assert round(gross_qty(1.0, 20), 4) == 1.25      # limpiar deja el 80 %
    assert round(gross_qty(0.2, 50), 4) == 0.4
    for bad in (-1, 100, 150):
        with pytest.raises(BadWaste):
            gross_qty(1.0, bad)


# --------------------------------------------------------------- explosión
def test_a_recipe_explodes_into_gross_quantities():
    croqueta = Rec(1, "croq", "Croquetas", [Line(0.5, LECHE), Line(0.06, HARINA)], portions=10)
    assert explode(croqueta) == {1: 0.5, 2: 0.06}
    assert explode(croqueta, units=3) == {1: 1.5, 2: 0.18}


def test_cleaning_waste_comes_out_of_the_storeroom():
    plato = Rec(1, "p", "Gambas", [Line(0.15, GAMBA, waste_pct=40)])
    assert explode(plato) == {3: 0.25}               # pelar gambas cuesta el 40 %


def test_a_sub_recipe_explodes_down_to_mother_ingredients():
    bechamel = Rec(2, "bech", "Bechamel", [Line(1.0, LECHE), Line(0.1, HARINA)], yield_qty=1.05)
    croqueta = Rec(1, "croq", "Croquetas", [Line(0.21, sub=bechamel)], portions=10)
    got = explode(croqueta)
    assert round(got[1], 4) == 0.2 and round(got[2], 4) == 0.02


def test_the_same_ingredient_from_two_paths_adds_up():
    salsa = Rec(2, "s", "Salsa", [Line(0.5, LECHE)], yield_qty=0.5)
    plato = Rec(1, "p", "Plato", [Line(0.2, LECHE), Line(0.3, sub=salsa)])
    assert explode(plato) == {1: 0.5}


def test_a_recipe_inside_itself_is_caught():
    a = Rec(1, "a", "A", [])
    b = Rec(2, "b", "B", [Line(1, sub=a)], yield_qty=1)
    a.lines = [Line(1, sub=b)]
    a.yield_qty = 1
    with pytest.raises(CircularRecipe):
        explode(a)
    with pytest.raises(CircularRecipe):
        cost_recipe(a, PRECIOS)


def test_a_sub_recipe_without_yield_cannot_be_used():
    sin_rendimiento = Rec(2, "s", "Salsa", [Line(1, LECHE)])
    plato = Rec(1, "p", "Plato", [Line(0.1, sub=sin_rendimiento)])
    with pytest.raises(UnknownCost):
        explode(plato)


# ------------------------------------------------------------------- coste
def test_cost_breakdown_says_where_the_money_goes():
    plato = Rec(1, "p", "Plato", [Line(0.15, GAMBA, waste_pct=40), Line(0.2, LECHE)],
                portions=1, sale_price=11.0, vat_pct=10)
    c = cost_recipe(plato, PRECIOS)
    gamba, leche = c.lines
    assert gamba.label == "Gamba"                    # primero lo que más cuesta
    assert gamba.gross_qty == 0.25 and gamba.cost == 4.5
    assert gamba.waste_cost == 1.8                   # lo que cuesta lo que se tira al pelar
    assert leche.cost == 0.16
    assert c.total_cost == 4.66
    assert gamba.share_pct == 96.57 and leche.share_pct == 3.43
    assert c.waste_cost == 1.8


def test_food_cost_is_measured_against_the_price_without_tax():
    plato = Rec(1, "p", "Plato", [Line(1.0, HARINA)], portions=1, sale_price=11.0, vat_pct=10)
    c = cost_recipe(plato, PRECIOS)
    assert c.cost_per_portion == 1.2
    assert c.net_price == 10.0                       # 11 con un 10 % de impuesto
    assert c.food_cost_pct == 12.0                   # sobre el PVP daría 10.9, y sería falso
    assert c.margin_per_portion == 8.8


def test_cost_per_portion_divides_by_the_yield():
    croqueta = Rec(1, "c", "Croquetas", [Line(0.5, LECHE), Line(0.06, HARINA)], portions=10)
    c = cost_recipe(croqueta, PRECIOS)
    assert c.total_cost == 0.472 and c.cost_per_portion == 0.0472


def test_a_sub_recipe_costs_what_it_produces():
    bechamel = Rec(2, "b", "Bechamel", [Line(1.0, LECHE), Line(0.1, HARINA)], yield_qty=1.05)
    croqueta = Rec(1, "c", "Croquetas", [Line(0.21, sub=bechamel)], portions=10)
    c = cost_recipe(croqueta, PRECIOS)
    line = c.lines[0]
    assert line.is_sub_recipe and line.label == "Bechamel" and line.unit == "L"
    assert line.unit_cost == round(0.92 / 1.05, 6)
    assert c.total_cost == round(0.21 * 0.92 / 1.05, 6)


def test_an_ingredient_without_price_is_reported_never_counted_as_zero():
    azafran = Ing(9, "Azafrán")
    plato = Rec(1, "p", "Plato", [Line(1.0, HARINA), Line(0.001, azafran)])
    c = cost_recipe(plato, PRECIOS)
    assert c.missing == ["Azafrán"] and not c.complete
    assert c.total_cost == 1.2                       # solo lo que sí se sabe
    assert [l.known for l in c.lines] == [True, False]


def test_menu_ranking_puts_the_worst_margin_first():
    barato = cost_recipe(Rec(1, "a", "Barato", [Line(0.1, HARINA)], sale_price=10.0), PRECIOS)
    caro = cost_recipe(Rec(2, "b", "Caro", [Line(0.3, GAMBA)], sale_price=12.0), PRECIOS)
    sin_precio = cost_recipe(Rec(3, "c", "Sin precio", [Line(0.1, HARINA)]), PRECIOS)
    rows = menu_ranking([barato, caro, sin_precio])
    assert [r.code for r in rows] == ["b", "a", "c"]
    assert rows[0].food_cost_pct == 45.0
    assert rows[-1].food_cost_pct is None


# =========================== el caso real: chunk beef → patty → burger → plato
CHUNK = Ing(10, "Beef for burger")          # dos marcas de chunk beef bajo la misma madre
PAN = Ing(11, "Pan de burger", "UNIT")
QUESO = Ing(12, "Queso cheddar")
LECHUGA = Ing(13, "Lechuga")
SALSA_ING = Ing(14, "Mayonesa")
PRECIOS_BURGER = {10: 9.50, 11: 0.35, 12: 8.00, 13: 2.20, 14: 3.10}


def cheese_burger():
    """Cheese burger = emplatado → Burger → Burger patty → Beef for burger."""
    patty = Rec(30, "patty", "Burger patty",
                [Line(0.16, CHUNK, waste_pct=5)], yield_qty=1, yield_unit="UNIT")
    burger = Rec(20, "burger", "Burger",
                 [Line(1, sub=patty), Line(1, PAN)], yield_qty=1, yield_unit="UNIT")
    plato = Rec(10, "cheese", "Cheese burger",
                [Line(1, sub=burger), Line(0.04, QUESO),
                 Line(0.02, LECHUGA, waste_pct=25), Line(0.015, SALSA_ING)],
                portions=1, sale_price=13.50, vat_pct=10)
    return plato, burger, patty


def test_the_tree_shows_the_cost_at_every_level():
    from thegrill.engine.recipes import cost_tree
    plato, _, _ = cheese_burger()
    root = cost_tree(plato, PRECIOS_BURGER)

    assert root.label == "Cheese burger" and root.kind == "dish"
    names = [(n.label, n.depth, n.kind) for n in root.walk()]
    assert names[:3] == [("Cheese burger", 0, "dish"), ("Burger", 1, "prep"),
                         ("Burger patty", 2, "prep")]
    assert ("Beef for burger", 3, "ingredient") in names

    carne = next(n for n in root.walk() if n.label == "Beef for burger")
    assert carne.gross_qty == round(0.16 / 0.95, 6)          # 5 % se pierde al formar
    assert carne.cost == pytest.approx(0.16 / 0.95 * 9.50, abs=1e-5)

    burger = next(n for n in root.walk() if n.label == "Burger")
    patty = next(n for n in root.walk() if n.label == "Burger patty")
    assert patty.cost == carne.cost                           # la patty es solo carne
    assert burger.cost == round(patty.cost + 0.35, 6)         # más el pan
    assert root.cost == pytest.approx(burger.cost + 0.04 * 8.0
                                      + (0.02 / 0.75) * 2.20 + 0.015 * 3.10, abs=1e-5)


def test_each_level_says_what_it_costs_per_unit():
    from thegrill.engine.recipes import cost_tree
    plato, _, _ = cheese_burger()
    root = cost_tree(plato, PRECIOS_BURGER)
    patty = next(n for n in root.walk() if n.label == "Burger patty")
    assert patty.unit == "UNIT"
    assert patty.unit_cost == round(patty.cost / 1, 6)        # coste de una patty


def test_shares_add_up_to_the_whole_dish():
    from thegrill.engine.recipes import cost_tree
    plato, _, _ = cheese_burger()
    root = cost_tree(plato, PRECIOS_BURGER)
    assert root.share_pct == 100.0
    top_level = sum(c.share_pct for c in root.children)
    assert abs(top_level - 100.0) < 0.05


def test_the_rollup_answers_where_the_money_goes():
    from thegrill.engine.recipes import cost_tree, ingredient_rollup
    plato, _, _ = cheese_burger()
    rows = ingredient_rollup(cost_tree(plato, PRECIOS_BURGER))
    assert [r.name for r in rows][0] == "Beef for burger"     # la carne manda
    assert rows[0].share_pct > 60                             # dos tercios del plato
    assert {r.name for r in rows} == {"Beef for burger", "Pan de burger",
                                      "Queso cheddar", "Lechuga", "Mayonesa"}
    lechuga = next(r for r in rows if r.name == "Lechuga")
    assert lechuga.waste_cost == round((0.02 / 0.75 - 0.02) * 2.20, 6)


def test_an_ingredient_entering_by_two_paths_is_added_once():
    from thegrill.engine.recipes import cost_tree, ingredient_rollup
    salsa = Rec(40, "s", "Salsa", [Line(0.05, SALSA_ING)], yield_qty=0.05, yield_unit="KG")
    plato = Rec(10, "p", "Plato", [Line(0.01, SALSA_ING), Line(0.05, sub=salsa)])
    rows = ingredient_rollup(cost_tree(plato, PRECIOS_BURGER))
    assert len(rows) == 1
    mayo = rows[0]
    assert mayo.name == "Mayonesa" and mayo.paths == 2
    assert mayo.gross_qty == 0.06 and mayo.cost == pytest.approx(0.06 * 3.10, abs=1e-5)


def test_the_preps_are_listed_from_dearest_to_cheapest():
    from thegrill.engine.recipes import cost_tree, prep_costs
    plato, _, _ = cheese_burger()
    preps = prep_costs(cost_tree(plato, PRECIOS_BURGER))
    assert [p.label for p in preps] == ["Burger", "Burger patty"]
    assert preps[0].cost > preps[1].cost


def test_food_cost_of_the_finished_plate():
    plato, _, _ = cheese_burger()
    c = cost_recipe(plato, PRECIOS_BURGER)
    assert c.net_price == round(13.50 / 1.1, 4)
    assert 0 < c.food_cost_pct < 40
    assert c.margin_per_portion == round(c.net_price - c.cost_per_portion, 4)
    # la línea de mayor coste del primer nivel es la hamburguesa entera
    assert c.lines[0].label == "Burger" and c.lines[0].is_sub_recipe


def test_the_explosion_matches_the_exact_arithmetic():
    """Lo que hay que sacar del almacén, contra la cuenta hecha en fracciones.

    Una receta con elaboraciones dentro de elaboraciones es una cadena de
    divisiones: la merma divide —160 g con un 5 % son 168,42—, y cada nivel
    divide otra vez por lo que produce la elaboración. Cinco niveles son cinco
    divisiones encadenadas, y ahí es donde el error de la coma flotante se
    multiplica en vez de sumarse.

    Se compara con la misma explosión hecha en `Fraction`, donde no hay error
    que valga, sobre árboles de hasta cinco niveles y hasta doscientas veces la
    receta. Lo que se mide es el peor desvío en kilos, que es lo que acaba
    faltando en la cámara.
    """
    import random
    from fractions import Fraction

    from thegrill.engine import recipes as R

    class Linea:
        def __init__(self, qty, waste, ing=None, sub=None):
            self.qty, self.waste_pct = qty, waste
            self.ingredient_id = ing
            self.sub_recipe_id = getattr(sub, "id", None)
            self.sub_recipe = sub
            self.ingredient = None

    class Receta:
        n = 0

        def __init__(self, lineas, produce):
            Receta.n += 1
            self.id, self.name = Receta.n, f"R{Receta.n}"
            self.code, self.lines = self.name, lineas
            self.yield_qty, self.yield_unit = produce, None
            self.portions, self.sale_price, self.vat_pct = 1, None, 0.0

    def exacta(receta, veces):
        fuera = {}
        for l in receta.lines:
            q = Fraction(repr(float(l.qty)))
            w = Fraction(repr(float(l.waste_pct)))
            bruto = q / (1 - w / 100) * veces
            if l.sub_recipe is not None:
                produce = Fraction(repr(float(l.sub_recipe.yield_qty)))
                for k, v in exacta(l.sub_recipe, bruto / produce).items():
                    fuera[k] = fuera.get(k, Fraction(0)) + v
            elif l.ingredient_id:
                fuera[l.ingredient_id] = fuera.get(l.ingredient_id, Fraction(0)) + bruto
        return fuera

    azar = random.Random(41)
    peor = 0.0
    for _ in range(1500):
        actual = Receta([Linea(round(azar.uniform(0.01, 2), 3),
                               round(azar.uniform(0, 60), 1), ing=i + 1)
                         for i in range(azar.randint(1, 4))],
                        round(azar.uniform(0.5, 10), 3))
        for _ in range(azar.randint(1, 5)):
            actual = Receta([Linea(round(azar.uniform(0.01, 2), 3),
                                   round(azar.uniform(0, 60), 1), sub=actual),
                             Linea(round(azar.uniform(0.01, 2), 3),
                                   round(azar.uniform(0, 60), 1), ing=99)],
                            round(azar.uniform(0.5, 10), 3))
        veces = round(azar.uniform(1, 200), 2)
        mio = R.explode(actual, veces)
        for k, v in exacta(actual, Fraction(repr(veces))).items():
            peor = max(peor, abs(mio.get(k, 0.0) - float(v)))
    # Un miligramo. Por debajo de eso no hay báscula de cocina que llegue, y
    # `explode` redondea a la millonésima de kilo a propósito.
    assert peor < 0.001, f"la explosión se separa {peor:.10g} kg de la cuenta exacta"
