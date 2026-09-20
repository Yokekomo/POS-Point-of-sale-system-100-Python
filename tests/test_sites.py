"""El obrador y los locales: la carne ya no está «en la casa», está en una sede.

Lo que se comprueba aquí es que la carne viaja con su número y con su dinero.
Una pieza que sale del obrador vale en el local lo mismo que valía al salir:
si el traslado abaratara o encareciera el kilo, el food cost del local sería
un cuento. Y cuando lo que viaja es parte de un lote, el lote se parte y lo
que sale nace con su propio número, para que se siga pudiendo seguir hasta el
plato.
"""
from datetime import date

import pytest

from thegrill import db
from thegrill.meat import service as meat
from thegrill.models import (IngredientLot, Primal, PrimalStatus, Site, SiteKind,
                             Transfer)
from thegrill.web import auth, sites

HOY = date(2026, 9, 20)


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'s.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Grupo Marina", "ana@a.com", "Ana",
                                           "clave-larga-1", language="es")
        luis = auth.join_restaurant(s, rest.join_code, "luis@a.com", "Luis", "clave-larga-2")
        yield s, rest, ana, luis


def pieza(s, rest, serial="8017", kg=9.0, precio=30.0, site_id=None) -> Primal:
    p = Primal(restaurant_id=rest.id, serial=serial, sku="RIBEYE_AUS", weight_kg=kg,
               received_date=HOY, landed_usd_per_kg=precio, piece_cost_usd=kg * precio,
               site_id=site_id)
    s.add(p); s.flush(); return p


def lote(s, rest, ana, serial="TG-0001·01", kg=6.0, coste=40.0, piezas=None,
         site_id=None) -> IngredientLot:
    corte = meat.create_cut(s, ana, f"Lomo {serial}")
    articulo = meat.add_article(s, ana, corte, f"Art {serial}")
    row = IngredientLot(restaurant_id=rest.id, item_id=articulo.id,
                        ingredient_id=corte.id, lot_code="TG-0001", serial=serial,
                        expiry=HOY, received=HOY, qty=kg, qty_remaining=kg, unit_cost=coste,
                        pieces=piezas, site_id=site_id)
    s.add(row); s.flush(); return row


# --------------------------------------------------------------- las sedes
def test_a_house_that_never_heard_of_sites_has_one(ctx):
    """Lo de antes sigue funcionando: sin configurar nada, hay obrador."""
    s, rest, ana, _ = ctx
    principal = sites.main(s, rest.id)

    assert principal.kind == SiteKind.WAREHOUSE
    assert sites.main(s, rest.id).id == principal.id          # no se crea otra
    assert [x.id for x in sites.all_sites(s, rest.id)] == [principal.id]


def test_meat_with_no_site_is_meat_in_the_main_one(ctx):
    s, rest, ana, _ = ctx
    p = pieza(s, rest)
    assert sites.where(s, rest.id, p).id == sites.main(s, rest.id).id


def test_two_sites_cannot_share_a_name(ctx):
    s, rest, ana, _ = ctx
    sites.create(s, ana, "Playa")
    with pytest.raises(sites.SiteError):
        sites.create(s, ana, "Playa")


def test_a_site_needs_a_name(ctx):
    s, rest, ana, _ = ctx
    with pytest.raises(sites.SiteError):
        sites.create(s, ana, "   ")


def test_the_main_site_cannot_be_closed(ctx):
    """Cerrar la única sede dejaría la carne en ningún sitio."""
    s, rest, ana, _ = ctx
    principal = sites.main(s, rest.id)
    with pytest.raises(sites.SiteError):
        sites.set_active(s, ana, principal.id, False)


def test_a_closed_site_does_not_take_meat(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    sites.set_active(s, ana, playa.id, False)
    pieza(s, rest)
    with pytest.raises(sites.SiteError):
        sites.send_primal(s, ana, "8017", playa.id, on=HOY)


def test_goods_in_land_where_the_person_works(ctx):
    """Lo que da de alta el del local entra en el local, no en el obrador."""
    s, rest, ana, luis = ctx
    playa = sites.create(s, ana, "Playa")
    sites.assign(s, ana, luis, playa.id)

    meat.receive_primals(s, luis, lot="L-1", received=HOY,
                         rows=[meat.PrimalRow(serial="9001", sku="RIBEYE", kg=8.0,
                                              price_kg=25.0)])
    assert s.query(Primal).filter_by(serial="9001").one().site_id == playa.id


def test_without_a_site_a_person_works_with_the_whole_house(ctx):
    s, rest, ana, luis = ctx
    playa = sites.create(s, ana, "Playa")
    sites.assign(s, ana, luis, playa.id)
    sites.assign(s, ana, luis, None)
    assert luis.site_id is None and sites.of_user(s, luis) is None


# ------------------------------------------------------- traslado de piezas
def test_a_whole_primal_travels_with_its_number_and_its_money(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    p = pieza(s, rest, kg=9.0, precio=30.0)

    sent = sites.send_primal(s, ana, "8017", playa.id, on=HOY)

    assert sent.serial == "8017" and sent.to_site.id == playa.id
    assert sent.kg == 9.0 and sent.cost == pytest.approx(270.0)
    assert s.query(Primal).one().site_id == playa.id
    # Ni se abarata por el camino ni se encarece.
    assert p.landed_usd_per_kg == 30.0 and p.piece_cost_usd == pytest.approx(270.0)


def test_the_transfer_is_written_down_with_who_sent_it(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    obrador = sites.main(s, rest.id)
    pieza(s, rest)

    sites.send_primal(s, ana, "8017", playa.id, on=HOY, note="para el finde")

    row = s.query(Transfer).one()
    assert row.kind == sites.PRIMAL and row.serial == "8017"
    assert row.from_site_id == obrador.id and row.to_site_id == playa.id
    assert row.created_by == ana.id and row.note == "para el finde"


def test_a_primal_already_there_does_not_travel(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    pieza(s, rest)
    sites.send_primal(s, ana, "8017", playa.id, on=HOY)
    with pytest.raises(sites.SiteError):
        sites.send_primal(s, ana, "8017", playa.id, on=HOY)


def test_a_piece_that_is_gone_does_not_travel(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    p = pieza(s, rest)
    p.status = PrimalStatus.CUT
    s.flush()
    with pytest.raises(sites.SiteError):
        sites.send_primal(s, ana, "8017", playa.id, on=HOY)


def test_an_unknown_number_says_so(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    with pytest.raises(sites.SiteError):
        sites.send_primal(s, ana, "8099", playa.id, on=HOY)


def test_meat_does_not_travel_to_another_house(ctx):
    """La sede de otra casa no existe para esta."""
    s, rest, ana, _ = ctx
    otra, pedro = auth.create_restaurant(s, "Otro grupo", "pedro@b.com", "Pedro",
                                         "clave-larga-3", language="es")
    suya = sites.create(s, pedro, "Su local")
    pieza(s, rest)
    with pytest.raises(sites.SiteError):
        sites.send_primal(s, ana, "8017", suya.id, on=HOY)


# ------------------------------------------------------ traslado de cortes
def test_a_whole_lot_travels_keeping_its_number(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    row = lote(s, rest, ana, kg=6.0, coste=40.0)

    sent = sites.send_cut(s, ana, row.serial, 6.0, playa.id, on=HOY)

    assert sent.new_serial is None                 # entero: no hace falta partirlo
    assert s.query(IngredientLot).count() == 1
    assert row.site_id == playa.id and row.qty_remaining == 6.0
    assert sent.cost == pytest.approx(240.0)


def test_part_of_a_lot_splits_it_and_the_new_number_hangs_off_the_old(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    row = lote(s, rest, ana, serial="TG-0001·01", kg=6.0, coste=40.0)

    sent = sites.send_cut(s, ana, "TG-0001·01", 2.5, playa.id, on=HOY)

    assert sent.new_serial == "TG-0001·01·T1"
    hijo = s.query(IngredientLot).filter_by(serial="TG-0001·01·T1").one()
    assert hijo.parent_serial == "TG-0001·01"      # se sigue hasta el plato
    assert hijo.site_id == playa.id
    assert hijo.qty_remaining == 2.5 and row.qty_remaining == 3.5
    # El kilo vale lo mismo en los dos sitios: el dinero viaja con la carne.
    assert hijo.unit_cost == row.unit_cost == 40.0
    assert sent.cost == pytest.approx(100.0)


def test_two_splits_of_the_same_lot_get_different_numbers(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    lote(s, rest, ana, serial="TG-0001·01", kg=6.0)

    primero = sites.send_cut(s, ana, "TG-0001·01", 1.0, playa.id, on=HOY)
    segundo = sites.send_cut(s, ana, "TG-0001·01", 1.0, playa.id, on=HOY)
    assert (primero.new_serial, segundo.new_serial) == ("TG-0001·01·T1", "TG-0001·01·T2")


def test_pieces_travel_with_the_kilos(ctx):
    """Un lote de piezas que se parte reparte también las piezas."""
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    row = lote(s, rest, ana, kg=6.0, piezas=12)

    sites.send_cut(s, ana, row.serial, 3.0, playa.id, on=HOY)

    assert row.pieces == 6
    assert s.query(IngredientLot).filter_by(site_id=playa.id).one().pieces == 6


def test_you_cannot_send_more_than_there_is(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    row = lote(s, rest, ana, kg=6.0)
    with pytest.raises(sites.SiteError):
        sites.send_cut(s, ana, row.serial, 8.0, playa.id, on=HOY)
    assert row.qty_remaining == 6.0 and s.query(Transfer).count() == 0


def test_sending_nothing_is_not_a_transfer(ctx):
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    row = lote(s, rest, ana, kg=6.0)
    with pytest.raises(sites.SiteError):
        sites.send_cut(s, ana, row.serial, 0.0, playa.id, on=HOY)


# --------------------------------------------------------- lo que hay donde
def test_stock_tells_each_site_apart(ctx):
    """Ocho piezas en el obrador y ninguna en la playa no es cuatro y cuatro."""
    s, rest, ana, _ = ctx
    obrador = sites.main(s, rest.id)
    playa = sites.create(s, ana, "Playa")
    pieza(s, rest, serial="8017", kg=9.0, precio=30.0)
    pieza(s, rest, serial="8018", kg=8.0, precio=30.0)
    lote(s, rest, ana, serial="TG-0001·01", kg=6.0, coste=40.0)

    sites.send_primal(s, ana, "8018", playa.id, on=HOY)
    sites.send_cut(s, ana, "TG-0001·01", 2.0, playa.id, on=HOY)

    filas = {row.site.name: row for row in sites.stock(s, rest.id)}
    assert filas[obrador.name].primals == 1
    assert filas[obrador.name].primal_kg == pytest.approx(9.0)
    assert filas[obrador.name].cut_kg == pytest.approx(4.0)
    assert filas[obrador.name].value == pytest.approx(270.0 + 160.0)
    assert filas["Playa"].primals == 1
    assert filas["Playa"].kg == pytest.approx(10.0)
    assert filas["Playa"].value == pytest.approx(240.0 + 80.0)


def test_recent_transfers_read_with_names_and_can_be_filtered(ctx):
    s, rest, ana, _ = ctx
    obrador = sites.main(s, rest.id)
    playa = sites.create(s, ana, "Playa")
    sierra = sites.create(s, ana, "Sierra")
    pieza(s, rest, serial="8017")
    pieza(s, rest, serial="8018")
    sites.send_primal(s, ana, "8017", playa.id, on=HOY)
    sites.send_primal(s, ana, "8018", sierra.id, on=HOY)

    todos = sites.recent(s, rest.id)
    assert {m.to_name for m in todos} == {"Playa", "Sierra"}
    assert all(m.from_name == obrador.name for m in todos)

    solo = sites.recent(s, rest.id, site_id=playa.id)
    assert [m.serial for m in solo] == ["8017"]


def test_butchering_at_a_site_leaves_the_cuts_at_that_site(ctx):
    """Lo que sale del despiece se queda donde estaba la pieza."""
    s, rest, ana, _ = ctx
    playa = sites.create(s, ana, "Playa")
    p = pieza(s, rest, kg=9.0, precio=30.0)
    sites.send_primal(s, ana, "8017", playa.id, on=HOY)
    corte = meat.create_cut(s, ana, "Lomo")
    articulo = meat.add_article(s, ana, corte, "Ribeye AUS")
    p.expiry_label = HOY
    s.flush()

    meat.post_butchery(s, ana, tg="TG-0001", serials=["8017"], before_kg=9.0,
                       rows=[meat.CutRow(name="Lomo", item_id=articulo.id,
                                         pieces=6, grams=1200)],
                       waste_kg=1.8, on=HOY)

    assert {l.site_id for l in s.query(IngredientLot)} == {playa.id}


# =========================================================== las pantallas
class TestScreens:
    """Lo mismo, pero por la puerta por la que entra la gente."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from thegrill.meat import app as meatapp
        from tests.meat_helpers import SPANISH, signup

        monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
        db.init_engine(f"sqlite:///{tmp_path/'p.db'}")
        db.create_all()
        with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
            signup(c)
            yield c

    def csrf(self, client, url="/traslados"):
        from tests.meat_helpers import csrf_from
        return csrf_from(client.get(url).text)

    def test_a_house_with_one_site_is_told_so(self, client):
        page = client.get("/traslados")
        assert page.status_code == 200
        assert "Dónde está la carne" in page.text
        assert "una sola sede" in page.text

    def test_the_manager_opens_an_outlet_and_can_send_meat_to_it(self, client):
        from thegrill.models import Restaurant

        alta = client.post("/sedes/nueva", data={"name": "Playa", "kind": "OUTLET",
                                                 "csrf": self.csrf(client, "/sedes")})
        assert alta.status_code == 303
        assert "Playa" in client.get("/sedes").text

        with db.session_scope() as s:
            rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
            pieza(s, rest, serial="8017")

        enviado = client.post("/traslados/pieza",
                              data={"serial": "8017", "site": self._outlet_id(),
                                    "csrf": self.csrf(client)})
        assert enviado.status_code == 200
        assert "ya está en Playa" in enviado.text
        with db.session_scope() as s:
            assert s.query(Primal).one().site_id == self._outlet_id()

    def _outlet_id(self) -> int:
        with db.session_scope() as s:
            return s.query(Site).filter_by(kind=SiteKind.OUTLET).one().id

    def test_an_assistant_sees_where_the_meat_is_but_does_not_send_it(self, client):
        from thegrill.models import Role
        from tests.meat_helpers import add_user

        client.post("/sedes/nueva", data={"name": "Playa", "kind": "OUTLET",
                                          "csrf": self.csrf(client, "/sedes")})
        ayudante = add_user(client, email="eva@marina.com", name="Eva", role=Role.EMPLOYEE)
        page = ayudante.get("/traslados")
        assert page.status_code == 200 and "Dónde está la carne" in page.text
        assert "Mandar una pieza" not in page.text
        assert ayudante.post("/traslados/pieza",
                             data={"serial": "8017", "site": 2, "csrf": "x"}).status_code == 403
        assert ayudante.get("/sedes").status_code == 403

    def test_the_money_column_is_only_for_the_manager(self, client):
        from thegrill.models import Role
        from tests.meat_helpers import add_user

        carnicero = add_user(client, email="paco@marina.com", name="Paco", role=Role.BUTCHER)
        assert "Valor en cámara" in client.get("/traslados").text
        assert "Valor en cámara" not in carnicero.get("/traslados").text


# ===================================================== la venta, por su sede
class TestConsumption:
    """Lo que se vende en un local sale de los números que están en ese local.

    Descontar en la playa un lote que está en el obrador cuadra el papel y
    descuadra las dos cámaras: la de allí, que tiene carne que el programa ya
    ha dado por servida, y la de aquí, que no tiene la que dice tener.
    """

    @pytest.fixture
    def casa(self, ctx):
        from thegrill.models import Role

        s, rest, ana, luis = ctx
        obrador = sites.main(s, rest.id)
        playa = sites.create(s, ana, "Playa")
        luis.role = Role.BUTCHER
        sites.assign(s, ana, luis, playa.id)
        cut = meat.create_cut(s, ana, "Entrecot")
        item = meat.add_article(s, ana, cut, "Entrecot AUS")
        meat.add_dish(s, ana, "Entrecot a la brasa", cut.id, 300, sale_price=28.0,
                      pos_code="1001", pos_name="ENTRECOT")
        return s, rest, ana, luis, obrador, playa, cut, item

    def lote(self, s, rest, cut, item, serial, kg, site_id=None, frozen=False):
        row = IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=cut.id,
                            lot_code="TG-0001", serial=serial, expiry=HOY, received=HOY,
                            qty=kg, qty_remaining=kg, unit_cost=40.0, frozen=frozen,
                            site_id=site_id)
        s.add(row); s.flush(); return row

    def test_the_sale_of_an_outlet_comes_out_of_the_outlet(self, casa):
        from thegrill.web import costing

        s, rest, ana, luis, obrador, playa, cut, item = casa
        en_obrador = self.lote(s, rest, cut, item, "TG-0001·01", 6.0, site_id=obrador.id)
        en_playa = self.lote(s, rest, cut, item, "TG-0001·02", 6.0, site_id=playa.id)

        venta = costing.consume_sales(s, luis, [("ENTRECOT", 4)], on=HOY, lang="es")

        assert venta.site == "Playa"
        assert en_playa.qty_remaining == pytest.approx(4.8)
        assert en_obrador.qty_remaining == 6.0          # el obrador no se toca
        assert not venta.shortfalls

    def test_meat_in_another_site_is_not_missing_meat(self, casa):
        """Si la carne está en el obrador, lo que hace falta es un traslado."""
        from thegrill.models import Alert
        from thegrill.web import costing

        s, rest, ana, luis, obrador, playa, cut, item = casa
        self.lote(s, rest, cut, item, "TG-0001·01", 6.0, site_id=obrador.id)

        venta = costing.consume_sales(s, luis, [("ENTRECOT", 2)], on=HOY, lang="es")

        gap = venta.shortfalls[0]
        assert gap.missing_qty == pytest.approx(0.6)
        assert gap.elsewhere_qty == pytest.approx(6.0) and gap.elsewhere == "Principal"
        aviso = s.query(Alert).filter_by(code="stock.short").one()
        assert "Principal" in aviso.message and "traerla" in aviso.message

    def test_meat_with_no_site_is_the_main_site_meat(self, casa):
        """Lo de toda la vida: sin sede escrita, está en la principal."""
        from thegrill.web import costing

        s, rest, ana, luis, obrador, playa, cut, item = casa
        viejo = self.lote(s, rest, cut, item, "TG-0001·01", 6.0)      # sin sede
        en_playa = self.lote(s, rest, cut, item, "TG-0001·02", 6.0, site_id=playa.id)

        costing.consume_sales(s, ana, [("ENTRECOT", 2)], on=HOY, lang="es",
                              site_id=obrador.id)

        assert viejo.qty_remaining == pytest.approx(5.4)
        assert en_playa.qty_remaining == 6.0

    def test_the_manager_says_which_site_the_file_belongs_to(self, casa):
        """El manager sube el parte de cada local desde su despacho."""
        from thegrill.web import costing

        s, rest, ana, luis, obrador, playa, cut, item = casa
        en_obrador = self.lote(s, rest, cut, item, "TG-0001·01", 6.0, site_id=obrador.id)
        en_playa = self.lote(s, rest, cut, item, "TG-0001·02", 6.0, site_id=playa.id)

        venta = costing.consume_sales(s, ana, [("ENTRECOT", 2)], on=HOY, lang="es",
                                      site_id=playa.id)

        assert venta.site == "Playa"
        assert en_playa.qty_remaining == pytest.approx(5.4) and en_obrador.qty_remaining == 6.0

    def test_without_a_site_it_works_like_it_always_did(self, casa):
        """Un manager sin sede y una casa de una sola: la casa entera."""
        from thegrill.web import costing

        s, rest, ana, luis, obrador, playa, cut, item = casa
        en_obrador = self.lote(s, rest, cut, item, "TG-0001·01", 6.0, site_id=obrador.id)

        venta = costing.consume_sales(s, ana, [("ENTRECOT", 2)], on=HOY, lang="es")

        assert venta.site == "" and not venta.shortfalls
        assert en_obrador.qty_remaining == pytest.approx(5.4)

    def test_frozen_in_your_own_site_still_does_not_sell(self, casa):
        """Las dos reglas a la vez: ni de otra sede, ni del arcón."""
        from thegrill.web import costing

        s, rest, ana, luis, obrador, playa, cut, item = casa
        congelado = self.lote(s, rest, cut, item, "TG-0001·02", 6.0, site_id=playa.id,
                              frozen=True)

        venta = costing.consume_sales(s, luis, [("ENTRECOT", 2)], on=HOY, lang="es")

        assert congelado.qty_remaining == 6.0
        assert venta.shortfalls[0].frozen_qty == pytest.approx(6.0)

    def test_the_chamber_of_an_outlet_only_shows_its_own(self, casa):
        from thegrill.web import butchery

        s, rest, ana, luis, obrador, playa, cut, item = casa
        self.lote(s, rest, cut, item, "TG-0001·01", 6.0, site_id=obrador.id)
        self.lote(s, rest, cut, item, "TG-0001·02", 2.0, site_id=playa.id)
        pieza(s, rest, serial="8017", site_id=obrador.id)

        suyo = butchery.status(s, rest.id, on=HOY, site_id=playa.id)
        assert suyo.total_cut_kg == pytest.approx(2.0)
        assert suyo.total_primals == 0
        # Y el manager, sin sede, sigue viendo la casa entera.
        todo = butchery.status(s, rest.id, on=HOY)
        assert todo.total_cut_kg == pytest.approx(8.0) and todo.total_primals == 1

    def test_waste_comes_out_of_your_own_site(self, casa):
        from thegrill.web import waste

        s, rest, ana, luis, obrador, playa, cut, item = casa
        en_obrador = self.lote(s, rest, cut, item, "TG-0001·01", 6.0, site_id=obrador.id)
        en_playa = self.lote(s, rest, cut, item, "TG-0001·02", 6.0, site_id=playa.id)

        waste.record(s, luis, ingredient_id=cut.id, kg=1.0, reason="se cayó", on=HOY)

        assert en_playa.qty_remaining == pytest.approx(5.0)
        assert en_obrador.qty_remaining == 6.0
