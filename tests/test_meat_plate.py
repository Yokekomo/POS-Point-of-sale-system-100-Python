"""El emplatado: el food cost del plato, no solo el de la carne.

Un entrecot no llega solo a la mesa. Si el food cost solo cuenta la carne, el
número es bonito y falso. Aquí se comprueba que la guarnición entra en el coste,
que su precio se configura en un sitio y se aplica en todos los platos, y que lo
que no se controla en cámara —una patata— no se descuenta del almacén ni genera
falsos avisos de falta de stock.
"""
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import service as meat
from thegrill.models import (Alert, ConsumptionMode, Ingredient, IngredientLot,
                             IngredientMovement, MovementKind, Recipe, Restaurant, Unit)
from thegrill.web import auth, costing

HOY = date.today()


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'plate.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Hotel Marina", "albano@marina.com",
                                           "Albano", "clave-larga-1", language="es")
        yield s, rest, ana


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'web.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False) as c:
        c.post("/signup", data={"restaurant": "Hotel Marina", "name": "Albano",
                                "email": "albano@marina.com", "password": "clave-larga-1",
                                "language": "es"})
        yield c


def csrf_from(html):
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "la página no trae token CSRF"
    return m.group(1)


def entrecot(s, rest, ana, precio_kg=43.0, pvp=29.50):
    """Un striploin en cámara y un entrecot de 330 g en la carta."""
    corte = meat.create_cut(s, ana, "Striploin steak")
    art = meat.add_article(s, ana, corte, "Striploin AUS")
    costing.receive(s, ana, art, 10.0, precio_kg, HOY + timedelta(days=10))
    plato = meat.add_dish(s, ana, "Entrecot a la brasa", corte.id, 330, pvp, 10.0,
                          pos_code="1201", pos_name="ENTRECOT")
    return corte, plato


# ------------------------------------------ el coste de lo que no es carne
def test_the_cost_of_an_extra_is_configured_in_one_place(ctx):
    s, rest, ana = ctx
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20)
    assert meat.extra_cost(patata) == 1.20
    assert costing.unit_costs(s, rest.id)[patata.id] == 1.20   # sin stock y con precio

    meat.set_extra_cost(s, ana, patata.id, 1.55)
    assert meat.extra_cost(patata) == 1.55
    assert costing.unit_costs(s, rest.id)[patata.id] == 1.55


def test_changing_a_cost_moves_every_plate_that_carries_it(ctx):
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana)
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20)
    meat.add_plate_line(s, ana, plato, patata.id, 200)
    s.refresh(plato)
    antes = meat.plate(s, rest.id, plato).cost

    meat.set_extra_cost(s, ana, patata.id, 2.20)
    despues = meat.plate(s, rest.id, plato).cost
    assert round(despues - antes, 4) == 0.2                    # 200 g a un euro más el kilo


def test_an_extra_without_a_price_is_said_out_loud_never_counted_as_zero(ctx):
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana)
    salsa = meat.create_extra(s, ana, "Salsa de la casa", Unit.L)   # sin coste
    meat.add_plate_line(s, ana, plato, salsa.id, 30)
    s.refresh(plato)
    p = meat.plate(s, rest.id, plato)
    assert "Salsa de la casa" in p.missing_price
    linea = next(l for l in p.lines if l.name == "Salsa de la casa")
    assert linea.unit_cost is None and linea.cost == 0.0        # se dice, no se inventa


def test_an_extra_cost_is_never_negative(ctx):
    s, rest, ana = ctx
    with pytest.raises(meat.MeatError):
        meat.create_extra(s, ana, "Patata", Unit.KG, -1)
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.0)
    with pytest.raises(meat.MeatError):
        meat.set_extra_cost(s, ana, patata.id, -0.5)


# -------------------------------------------------- el plato, no solo la carne
def test_the_food_cost_is_the_whole_plates_not_just_the_meats(ctx):
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana, precio_kg=43.0, pvp=29.50)
    solo_carne = meat.plate(s, rest.id, plato)
    assert solo_carne.cost == pytest.approx(14.19, abs=0.001)   # 0,330 × 43
    assert solo_carne.food_cost_pct == pytest.approx(52.91, abs=0.01)

    for nombre, unidad, coste, cantidad in [("Patata", Unit.KG, 1.20, 200),      # gramos
                                            ("Salsa chimichurri", Unit.L, 8.50, 30),  # ml
                                            ("Pan de brioche", Unit.UNIT, 0.62, 1)]:
        extra = meat.create_extra(s, ana, nombre, unidad, coste)
        meat.add_plate_line(s, ana, plato, extra.id, cantidad)
    s.refresh(plato)

    completo = meat.plate(s, rest.id, plato)
    assert completo.cost == pytest.approx(14.19 + 0.24 + 0.255 + 0.62, abs=0.001)
    assert completo.food_cost_pct > solo_carne.food_cost_pct     # la verdad es peor
    assert completo.food_cost_pct == pytest.approx(57.07, abs=0.02)   # 15,305 sobre 26,82
    assert completo.margin == pytest.approx(29.50 / 1.1 - completo.cost, abs=0.01)


def test_the_plate_says_where_the_money_goes(ctx):
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana)
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20)
    meat.add_plate_line(s, ana, plato, patata.id, 200)
    s.refresh(plato)

    p = meat.plate(s, rest.id, plato)
    carne = p.meat
    assert carne is not None and carne.is_meat and carne.name == "Striploin steak"
    assert [l.name for l in p.extras] == ["Patata"]
    assert carne.share_pct > 90                                  # la carne es el plato
    assert round(sum(l.share_pct for l in p.lines), 1) == 100.0   # y el reparto cuadra


def test_cleaning_waste_on_a_garnish_counts_in_the_cost(ctx):
    """Se pelan 250 g de patata para que lleguen 200 al plato."""
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana)
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20)
    meat.add_plate_line(s, ana, plato, patata.id, 200, waste_pct=20)
    s.refresh(plato)

    linea = next(l for l in meat.plate(s, rest.id, plato).lines if l.name == "Patata")
    assert linea.qty_small == 200 and linea.small == "g"          # se lee en gramos
    assert linea.qty == 0.2                                       # se guarda en kilos
    assert linea.gross_small == pytest.approx(250, abs=0.01)      # 200 / (1 - 0,20)
    assert linea.cost == pytest.approx(0.30, abs=0.0001)          # lo que sale la porción


def test_the_grams_of_meat_can_be_corrected_and_the_cost_follows(ctx):
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana, precio_kg=43.0)
    meat.set_plate_grams(s, ana, plato, 250)
    s.refresh(plato)
    p = meat.plate(s, rest.id, plato)
    assert p.meat.qty == 0.25
    assert p.cost == pytest.approx(10.75, abs=0.001)


def test_the_meat_is_never_taken_off_the_plate(ctx):
    """Un plato de carne sin carne no es un plato de carne."""
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana)
    carne = meat.meat_line(plato, s)
    with pytest.raises(meat.MeatError):
        meat.remove_plate_line(s, ana, plato, carne.id)
    assert meat.meat_line(plato, s) is not None


def test_a_garnish_can_be_taken_off_the_plate(ctx):
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana)
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20)
    linea = meat.add_plate_line(s, ana, plato, patata.id, 200)
    s.refresh(plato)
    meat.remove_plate_line(s, ana, plato, linea.id)
    s.refresh(plato)
    assert meat.plate(s, rest.id, plato).extras == []


def test_a_plate_cannot_borrow_another_hotels_ingredient(ctx):
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana)
    otro, bea = auth.create_restaurant(s, "Otro hotel", "bea@otro.com", "Bea", "clave-larga-2")
    ajeno = meat.create_extra(s, bea, "Patata de la vecina", Unit.KG, 1.0)
    with pytest.raises(meat.MeatError, match="restaurante"):
        meat.add_plate_line(s, ana, plato, ajeno.id, 200)


# --------------------------------- la guarnición no se cuenta en cámara
def test_a_garnish_is_costed_but_never_deducted_from_the_chiller(ctx):
    """Aquí no se cuentan patatas: su coste cuenta, su stock no se controla."""
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana)
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20)
    meat.add_plate_line(s, ana, plato, patata.id, 200)
    s.refresh(plato)
    assert patata.consumption == ConsumptionMode.COUNT

    result = costing.consume_sales(s, ana, [("ENTRECOT", 6)], on=HOY)

    lote = s.query(IngredientLot).filter_by(ingredient_id=corte.id).one()
    assert lote.qty_remaining == pytest.approx(10 - 6 * 0.33, abs=0.001)   # la carne sí
    assert patata.id not in result.consumed                                # la patata no
    assert result.theoretical[patata.id] == pytest.approx(1.2, abs=0.001)  # pero queda apuntada
    assert not result.shortfalls                                           # sin falsos avisos
    assert not s.query(Alert).filter_by(code="stock.short").all()
    salidas = s.query(IngredientMovement).filter_by(kind=MovementKind.SALE).all()
    assert {m.ingredient_id for m in salidas} == {corte.id}


def test_a_garnish_is_not_a_cut_and_does_not_show_up_in_the_chiller(ctx):
    s, rest, ana = ctx
    corte, _ = entrecot(s, rest, ana)
    meat.create_extra(s, ana, "Patata", Unit.KG, 1.20)
    assert [c.name for c in meat.cuts(s, rest.id)] == ["Striploin steak"]
    assert [e.name for e in meat.extras(s, rest.id)] == ["Patata"]


def test_the_same_name_is_not_used_twice(ctx):
    s, rest, ana = ctx
    meat.create_extra(s, ana, "Patata", Unit.KG, 1.0)
    with pytest.raises(meat.MeatError):
        meat.create_extra(s, ana, "Patata", Unit.KG, 1.0)
    with pytest.raises(meat.MeatError):
        meat.create_cut(s, ana, "Patata")


def test_it_says_how_many_plates_each_ingredient_reaches(ctx):
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana)
    otro = meat.add_dish(s, ana, "Tomahawk", corte.id, 600, 49.0, 10.0, pos_name="TOMAHAWK")
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20)
    meat.add_plate_line(s, ana, plato, patata.id, 200)
    meat.add_plate_line(s, ana, otro, patata.id, 300)
    assert meat.extras_usage(s, rest.id)[patata.id] == 2


# ------------------------------------------------------------ por la web
def test_the_plate_can_be_built_from_the_screen(client):
    form = client.get("/cortes")
    client.post("/cortes/nuevo", data={"csrf": csrf_from(form.text), "name": "Striploin steak"})
    with db.session_scope() as s:
        cut_id = s.query(Ingredient).filter_by(name="Striploin steak").one().id

    form = client.get("/carta")
    client.post("/carta/nuevo", data={
        "csrf": csrf_from(form.text), "name": "Entrecot a la brasa", "cut_id": cut_id,
        "grams": "330", "sale_price": "29,50", "vat_pct": "10", "pos_name": "ENTRECOT"})

    form = client.get("/ingredientes")
    client.post("/ingredientes/nuevo", data={"csrf": csrf_from(form.text), "name": "Patata",
                                             "unit": "KG", "cost": "1,20"})
    assert "1.20" in client.get("/ingredientes").text

    detalle = client.get("/carta/entrecot_a_la_brasa")
    assert detalle.status_code == 200
    assert "Striploin steak" in detalle.text
    assert "banner warn" in detalle.text                  # avisa de que solo lleva carne

    with db.session_scope() as s:
        patata_id = s.query(Ingredient).filter_by(name="Patata").one().id
    r = client.post("/carta/entrecot_a_la_brasa/linea",
                    data={"csrf": csrf_from(detalle.text), "ingredient_id": patata_id,
                          "qty": "200", "waste_pct": "20"})
    assert r.status_code == 303
    detalle = client.get("/carta/entrecot_a_la_brasa").text
    assert "Patata" in detalle
    assert "banner warn" not in detalle                   # ya no está sola
    assert "+1" in client.get("/carta").text              # la carta lo dice


def test_an_employee_cannot_reprice_the_garnish(client):
    form = client.get("/ingredientes")
    client.post("/ingredientes/nuevo", data={"csrf": csrf_from(form.text), "name": "Patata",
                                             "unit": "KG", "cost": "1,20"})
    with db.session_scope() as s:
        rest = s.query(Restaurant).one()
        code, patata_id = rest.join_code, s.query(Ingredient).one().id
    client.cookies.clear()
    client.post("/join", data={"join_code": code, "name": "Marta", "email": "m@marina.com",
                               "password": "clave-larga-2"})

    pagina = client.get("/ingredientes")
    assert pagina.status_code == 200 and "Patata" in pagina.text     # verlo sí
    assert 'action="/ingredientes/nuevo"' not in pagina.text          # tocarlo no
    token = csrf_from(client.get("/merma").text)
    assert client.post(f"/ingredientes/{patata_id}/coste",
                       data={"csrf": token, "cost": "9"}).status_code == 403
    with db.session_scope() as s:
        assert meat.extra_cost(s.query(Ingredient).one()) == 1.20


def test_the_plate_screen_speaks_every_language(client):
    form = client.get("/cortes")
    client.post("/cortes/nuevo", data={"csrf": csrf_from(form.text), "name": "Striploin"})
    with db.session_scope() as s:
        cut_id = s.query(Ingredient).one().id
    form = client.get("/carta")
    client.post("/carta/nuevo", data={"csrf": csrf_from(form.text), "name": "Entrecot",
                                      "cut_id": cut_id, "grams": "330", "pos_name": "ENTRECOT"})
    ajustes = client.get("/configuracion")
    client.post("/configuracion", data={"csrf": csrf_from(ajustes.text), "language": "nl"})

    for path in ("/ingredientes", "/carta/entrecot"):
        html = client.get(path).text
        for palabra in ("Emplatado", "Ingrediente", "Cantidad", "Coste por unidad", "Quitar"):
            assert palabra not in html, f"{path} deja en español: {palabra}"


def test_a_cut_says_where_its_consumption_comes_from(client):
    """Descontar por venta y por conteo a la vez sería contar el doble."""
    form = client.get("/cortes")
    client.post("/cortes/nuevo", data={"csrf": csrf_from(form.text), "name": "Por venta",
                                       "rotation": "FEFO", "consumption": "RECIPE"})
    client.post("/cortes/nuevo", data={"csrf": csrf_from(form.text), "name": "Por conteo",
                                       "rotation": "FEFO", "consumption": "COUNT"})
    with db.session_scope() as s:
        modos = {c.name: c.consumption for c in s.query(Ingredient)}
        assert modos["Por venta"] == ConsumptionMode.RECIPE
        assert modos["Por conteo"] == ConsumptionMode.COUNT
    assert "Al cerrar turno, por conteo" in client.get("/cortes").text


# ------------------------------------------- gramos, precio por kilo, ración
def test_the_kitchen_writes_grams_and_reads_what_the_portion_costs(ctx):
    """Nadie en una cocina dice «0,2 kg de patata»: dice doscientos gramos."""
    s, rest, ana = ctx
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20, portion_g=200)
    assert patata.portion_g == 200
    assert meat.extra_cost(patata) == 1.20            # el precio sigue por kilo
    assert meat.portion_cost(patata) == 0.24          # y la ración sale a 24 céntimos


def test_a_portion_works_the_same_in_millilitres_and_in_units(ctx):
    s, rest, ana = ctx
    salsa = meat.create_extra(s, ana, "Salsa", Unit.L, 8.50, portion_g=30)
    pan = meat.create_extra(s, ana, "Pan", Unit.UNIT, 0.62, portion_g=1)
    assert meat.portion_cost(salsa) == 0.255          # 30 ml de un litro a 8,50
    assert meat.portion_cost(pan) == 0.62             # una unidad es una unidad
    assert meat.small_unit(Unit.L) == "ml"
    assert meat.small_unit(Unit.KG) == "g"


def test_without_a_portion_there_is_no_portion_cost_to_show(ctx):
    s, rest, ana = ctx
    sin_porcion = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20)
    sin_precio = meat.create_extra(s, ana, "Salsa", Unit.L, portion_g=30)
    assert meat.portion_cost(sin_porcion) is None
    assert meat.portion_cost(sin_precio) is None      # sin precio no se inventa


def test_the_portion_can_be_corrected_without_touching_the_price(ctx):
    s, rest, ana = ctx
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20, portion_g=200)
    meat.set_extra_cost(s, ana, patata.id, 1.20, portion_g=250)
    assert patata.portion_g == 250
    assert meat.portion_cost(patata) == 0.30
    with pytest.raises(meat.MeatError):
        meat.create_extra(s, ana, "Otra", Unit.KG, 1.0, portion_g=0)


def test_the_plate_reads_in_grams_and_prices_in_kilos(ctx):
    s, rest, ana = ctx
    corte, plato = entrecot(s, rest, ana, precio_kg=43.0)
    patata = meat.create_extra(s, ana, "Patata", Unit.KG, 1.20, portion_g=200)
    meat.add_plate_line(s, ana, plato, patata.id, patata.portion_g)
    s.refresh(plato)

    p = meat.plate(s, rest.id, plato)
    carne = p.meat
    assert carne.qty_small == 330 and carne.small == "g"      # la carne, en gramos
    assert carne.unit_cost == 43.0                            # su precio, por kilo
    assert carne.cost == pytest.approx(14.19, abs=0.001)      # y la porción, en dinero
    guarnicion = p.extras[0]
    assert guarnicion.qty_small == 200
    assert guarnicion.cost == pytest.approx(0.24, abs=0.0001)


def test_the_screen_shows_grams_the_price_per_kilo_and_the_portion(client):
    form = client.get("/ingredientes")
    client.post("/ingredientes/nuevo", data={"csrf": csrf_from(form.text), "name": "Patata",
                                             "unit": "KG", "cost": "1,20", "portion": "200"})
    html = client.get("/ingredientes").text
    assert "1.2" in html and "200 g" in html and "0.240" in html   # precio, gramos y ración

    form = client.get("/cortes")
    client.post("/cortes/nuevo", data={"csrf": csrf_from(form.text), "name": "Striploin"})
    with db.session_scope() as s:
        cut_id = s.query(Ingredient).filter_by(name="Striploin").one().id
        patata_id = s.query(Ingredient).filter_by(name="Patata").one().id
    form = client.get("/carta")
    client.post("/carta/nuevo", data={"csrf": csrf_from(form.text), "name": "Entrecot",
                                      "cut_id": cut_id, "grams": "330", "pos_name": "ENTRECOT"})
    detalle = client.get("/carta/entrecot")
    client.post("/carta/entrecot/linea", data={"csrf": csrf_from(detalle.text),
                                               "ingredient_id": patata_id, "qty": "200"})
    html = client.get("/carta/entrecot").text
    assert "330 g" in html and "200 g" in html
    assert "0,200" not in html and "0.2 KG" not in html     # nada de kilos con decimales
