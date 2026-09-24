"""Lo congelado está en espera: no se vende hasta que alguien lo saca.

Un número congelado es carne que existe y que no se puede servir. Descontarle
una venta es apuntar que se ha servido un entrecot que sigue duro en el arcón:
el stock cuadra en el papel y no cuadra en la cámara, y el día que alguien vaya
a buscarlo no está.

Por eso el POS descuenta por número, y solo de los números descongelados. Lo
que despierta a un número congelado es la salida a descongelar, que es lo que
pasa de verdad en la cocina: alguien abre el arcón y saca unas piezas. Si saca
unas pocas, esas piezas salen con su propio número y el resto se queda dentro
esperando su turno.
"""
from datetime import date, timedelta

import pytest

from thegrill import db
from thegrill.meat import service as meat
from thegrill.models import (Alert, DefrostEntry, Ingredient, IngredientLot, Primal,
                             Storage)
from thegrill.web import aging, auth, costing, defrost

HOY = date(2026, 9, 20)


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'f.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Asador", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        yield s, rest, ana


def corte(s, ana, nombre="Entrecot"):
    cut = meat.create_cut(s, ana, nombre)
    return cut, meat.add_article(s, ana, cut, f"{nombre} AUS")


def lote(s, rest, cut, item, serial, kg, coste=40.0, frozen=False, piezas=None,
         caduca=None) -> IngredientLot:
    row = IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=cut.id,
                        lot_code="TG-0001", serial=serial, expiry=caduca or HOY + timedelta(days=20),
                        received=HOY, qty=kg, qty_remaining=kg, unit_cost=coste,
                        pieces=piezas, frozen=frozen)
    s.add(row); s.flush(); return row


def plato(s, ana, cut, gramos=300, pos="ENTRECOT"):
    return meat.add_dish(s, ana, "Entrecot a la brasa", cut.id, gramos,
                         sale_price=28.0, pos_code="1001", pos_name=pos)


# ------------------------------------------------- la venta y el congelador
def test_a_sale_never_comes_out_of_a_frozen_number(ctx):
    """Lo que está en el arcón no se ha servido: si solo hay eso, falta carne."""
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    congelado = lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True, piezas=20)
    plato(s, ana, cut)

    venta = costing.consume_sales(s, ana, [("ENTRECOT", 2)], on=HOY, lang="es")

    assert congelado.qty_remaining == 6.0           # sigue entero en el arcón
    assert [g.name for g in venta.shortfalls] == ["Entrecot"]
    assert venta.shortfalls[0].missing_qty == pytest.approx(0.6)
    assert venta.shortfalls[0].frozen_qty == pytest.approx(6.0)


def test_the_warning_says_it_is_frozen_and_not_that_it_is_missing(ctx):
    """No es lo mismo no tener carne que tenerla dura: se arregla distinto."""
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True)
    plato(s, ana, cut)

    costing.consume_sales(s, ana, [("ENTRECOT", 1)], on=HOY, lang="es")

    aviso = s.query(Alert).filter_by(code="stock.short").one()
    assert "congelados" in aviso.message and "descongelar" in aviso.message


def test_the_thawed_number_sells_even_if_the_frozen_one_expires_sooner(ctx):
    """FEFO ordena lo que se puede servir; lo congelado no entra en la cola."""
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    congelado = lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True,
                     caduca=HOY + timedelta(days=2))
    fresco = lote(s, rest, cut, item, "TG-0001·02", 6.0,
                  caduca=HOY + timedelta(days=15))
    plato(s, ana, cut)

    venta = costing.consume_sales(s, ana, [("ENTRECOT", 2)], on=HOY, lang="es")

    assert not venta.shortfalls
    assert congelado.qty_remaining == 6.0
    assert fresco.qty_remaining == pytest.approx(5.4)


def test_frozen_meat_counts_as_stock_even_if_it_does_not_sell(ctx):
    """Existe y vale dinero: lo que no hace es servir un plato de hoy."""
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True, coste=40.0)

    assert costing.stock_on_hand(s, rest.id)[cut.id] == pytest.approx(6.0)
    assert costing.frozen_on_hand(s, rest.id)[cut.id] == pytest.approx(6.0)
    assert costing.unit_costs(s, rest.id)[cut.id] == pytest.approx(40.0)


# ----------------------------------------------- sacar del arcón despierta
def test_taking_the_whole_number_out_wakes_it_up(ctx):
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    congelado = lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True, piezas=20)
    plato(s, ana, cut)

    entry = defrost.intake(s, ana, "TG-0001·01", 20, 6.0, on=HOY)

    assert entry.lot_serial == "TG-0001·01"         # el mismo número, ya despierto
    assert congelado.frozen is False
    venta = costing.consume_sales(s, ana, [("ENTRECOT", 2)], on=HOY, lang="es")
    assert not venta.shortfalls
    assert congelado.qty_remaining == pytest.approx(5.4)


def test_taking_part_out_splits_the_number_and_the_rest_stays_frozen(ctx):
    """Sale lo que sale: el resto sigue en el arcón, con su número."""
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    congelado = lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True, piezas=20,
                     coste=40.0)

    entry = defrost.intake(s, ana, "TG-0001·01", 5, 1.5, on=HOY)

    assert entry.lot_serial == "TG-0001·01·D1"
    hijo = s.query(IngredientLot).filter_by(serial="TG-0001·01·D1").one()
    assert hijo.frozen is False and congelado.frozen is True
    assert hijo.qty_remaining == 1.5 and congelado.qty_remaining == 4.5
    assert hijo.pieces == 5 and congelado.pieces == 15
    assert hijo.parent_serial == "TG-0001·01"       # se sigue hasta el plato
    # El kilo vale lo mismo dentro y fuera del arcón: descongelar no cuesta dinero.
    assert hijo.unit_cost == congelado.unit_cost == 40.0


def test_the_sale_comes_out_of_the_part_that_was_taken_out(ctx):
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    congelado = lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True, piezas=20)
    plato(s, ana, cut)
    defrost.intake(s, ana, "TG-0001·01", 5, 1.5, on=HOY)

    venta = costing.consume_sales(s, ana, [("ENTRECOT", 3)], on=HOY, lang="es")

    hijo = s.query(IngredientLot).filter_by(serial="TG-0001·01·D1").one()
    assert not venta.shortfalls
    assert hijo.qty_remaining == pytest.approx(0.6)
    assert congelado.qty_remaining == 4.5           # el arcón no se ha tocado


def test_two_takings_get_their_own_numbers(ctx):
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True, piezas=20)

    primero = defrost.intake(s, ana, "TG-0001·01", 5, 1.5, on=HOY)
    segundo = defrost.intake(s, ana, "TG-0001·01", 5, 1.5, on=HOY)
    assert (primero.lot_serial, segundo.lot_serial) == ("TG-0001·01·D1", "TG-0001·01·D2")


def test_taking_meat_out_without_its_weight_is_refused(ctx):
    """Sin peso no se sabe qué deja de estar en espera, y eso se pregunta."""
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True, piezas=20)

    with pytest.raises(defrost.DefrostError):
        defrost.intake(s, ana, "TG-0001·01", 5, 0.0, on=HOY)
    assert s.query(DefrostEntry).count() == 0


def test_a_number_that_was_never_frozen_is_not_split(ctx):
    """Lo de siempre sigue igual: sacar carne fresca no inventa números."""
    s, rest, ana = ctx
    cut, item = corte(s, ana)
    fresco = lote(s, rest, cut, item, "TG-0001·01", 6.0, piezas=20)

    entry = defrost.intake(s, ana, "TG-0001·01", 5, 1.5, on=HOY)

    assert entry.lot_serial == "TG-0001·01"
    assert s.query(IngredientLot).count() == 1
    assert fresco.qty_remaining == 6.0              # el apunte no descuenta


# ------------------------------------------------------- la pieza congelada
def test_a_frozen_piece_is_not_sold_by_weight(ctx):
    """Una pieza del congelador no se corta al peso: primero sale del arcón."""
    s, rest, ana = ctx
    p = Primal(restaurant_id=rest.id, serial="8017", sku="RIBEYE", weight_kg=9.0,
               received_date=HOY, landed_usd_per_kg=30.0, piece_cost_usd=270.0)
    s.add(p); s.flush()
    aging.move(s, ana, "8017", Storage.FROZEN, on=HOY)

    with pytest.raises(aging.AgingError):
        aging.sell_by_weight(s, ana, "8017", 400, price=50.0, on=HOY)
    assert s.query(Primal).one().weight_kg == 9.0


def test_the_chamber_tells_frozen_kilos_apart(ctx):
    """El manager ve que esos kilos están, pero que no sirven para servir hoy."""
    from thegrill.web import butchery

    s, rest, ana = ctx
    cut, item = corte(s, ana)
    lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True)
    lote(s, rest, cut, item, "TG-0001·02", 2.0)

    fila = [c for c in butchery.status(s, rest.id, on=HOY).cuts if c.name == "Entrecot"][0]
    assert fila.kg == pytest.approx(8.0)
    assert fila.frozen_kg == pytest.approx(6.0)


# =========================================================== la pantalla
class TestScreens:
    """Lo mismo, por la puerta por la que entra el cocinero."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from thegrill.meat import app as meatapp
        from tests.meat_helpers import SPANISH, signup

        monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
        db.init_engine(f"sqlite:///{tmp_path/'pf.db'}")
        db.create_all()
        with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
            signup(c)
            yield c

    def carne_congelada(self):
        from thegrill.models import Restaurant, User
        with db.session_scope() as s:
            rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
            ana = s.query(User).filter_by(restaurant_id=rest.id).first()
            cut, item = corte(s, ana)
            lote(s, rest, cut, item, "TG-0001·01", 6.0, frozen=True, piezas=20)

    def test_taking_part_of_a_frozen_number_out_says_with_which_number(self, client):
        from tests.meat_helpers import csrf_from

        self.carne_congelada()
        pagina = client.get("/descongelado")
        assert "en espera" in pagina.text                 # lo dice antes de tocar nada

        salida = client.post("/descongelado/salida",
                             data={"serial": "TG-0001·01", "pieces": 5, "total_g": "1500",
                                   "shift": "", "csrf": csrf_from(pagina.text)})
        assert salida.status_code == 303
        # El número con el que sale del arcón se dice en la pantalla de
        # detrás, no en la barra de direcciones: un recado no vive en la URL.
        assert salida.headers["location"].startswith("/descongelado")
        assert "TG-0001·01·D1" in client.get("/descongelado").text

        with db.session_scope() as s:
            hijo = s.query(IngredientLot).filter_by(serial="TG-0001·01·D1").one()
            padre = s.query(IngredientLot).filter_by(serial="TG-0001·01").one()
            assert hijo.frozen is False and padre.frozen is True

    def test_the_chamber_shows_the_frozen_kilos_apart(self, client):
        self.carne_congelada()
        pagina = client.get("/carne")
        assert "congelados, en espera" in pagina.text
