"""El obrador y los locales: la carne ya no está «en la casa», está en una sede.

Lo que se comprueba aquí es que la carne viaja con su número y con su dinero.
Una pieza que sale del obrador vale en el local lo mismo que valía al salir:
si el traslado abaratara o encareciera el kilo, el food cost del local sería
un cuento. Y cuando lo que viaja es parte de un lote, el lote se parte y lo
que sale nace con su propio número, para que se siga pudiendo seguir hasta el
plato.
"""
from datetime import date, timedelta

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
        # Guardar contesta con una redirección, no con la pantalla: así
        # recargar no vuelve a mandar la pieza. El recado se lee en la
        # pantalla que viene detrás.
        assert enviado.status_code == 303
        assert "ya está en Playa" in client.get("/traslados").text
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


# ============================== el obrador manda, el local madura y corta
class TestWarehouseAndOutlets:
    """Lo que el obrador guarda y lo que el local recibe.

    En el obrador hay de todo: piezas frescas, congeladas y en curación, y una
    mesa donde se despieza. Del obrador salen para el local tres cosas
    distintas —un primal fresco para cortarlo allí, uno curado para seguir
    madurando o venderlo al peso, y cortes ya hechos— y cada una sigue
    contándose donde esté.
    """

    @pytest.fixture
    def grupo(self, ctx):
        from thegrill.models import Role

        s, rest, ana, luis = ctx
        obrador = sites.main(s, rest.id)
        playa = sites.create(s, ana, "Playa")
        luis.role = Role.BUTCHER
        sites.assign(s, ana, luis, playa.id)
        return s, rest, ana, luis, obrador, playa

    def test_the_warehouse_holds_frozen_and_ageing_at_the_same_time(self, grupo):
        from thegrill.models import Storage
        from thegrill.web import aging

        s, rest, ana, luis, obrador, playa = grupo
        pieza(s, rest, serial="8017")
        pieza(s, rest, serial="8018")
        pieza(s, rest, serial="8019")
        aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY)
        aging.move(s, ana, "8018", Storage.FROZEN, on=HOY)

        resumen = aging.summary(s, rest.id, on=HOY, site_id=obrador.id)
        assert resumen.aging_pieces == 1 and resumen.frozen_pieces == 1
        # Y la tercera sigue en cámara, esperando la mesa de despiece.
        assert [p.serial for p in meat.primals_in_stock(s, rest.id, site_id=obrador.id)
                if aging.where(p) == Storage.CHILLED] == ["8019"]

    def test_an_ageing_piece_that_moves_is_counted_by_the_outlet(self, grupo):
        """Se madura donde se sirve: la cuenta el que la tiene delante."""
        from thegrill.models import Storage
        from thegrill.web import aging

        s, rest, ana, luis, obrador, playa = grupo
        pieza(s, rest, serial="8017", kg=9.0)
        aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY)
        sites.send_primal(s, ana, "8017", playa.id, on=HOY)

        assert aging.pending_today(s, rest.id, on=HOY, site_id=obrador.id) == []
        assert aging.pending_today(s, rest.id, on=HOY, site_id=playa.id) == ["8017"]
        assert [l.site for l in aging.to_count(s, rest.id, on=HOY, site_id=playa.id)] == ["Playa"]

    def test_the_outlet_weighs_its_own_pieces_every_night(self, grupo):
        from thegrill.models import Storage
        from thegrill.web import aging

        s, rest, ana, luis, obrador, playa = grupo
        pieza(s, rest, serial="8017", kg=9.0)
        pieza(s, rest, serial="8018", kg=9.0)
        aging.move(s, ana, "8017", Storage.AGING, on=HOY)
        aging.move(s, ana, "8018", Storage.AGING, on=HOY)
        sites.send_primal(s, ana, "8017", playa.id, on=HOY)

        conteo = aging.count_day(s, luis, [("8017", 8.7), ("8018", 8.6)], on=HOY, lang="es")

        # Luis es de la playa: pesa la suya y la del obrador ni le aparece.
        assert conteo.counted == 1 and conteo.missing == []
        assert [l.serial for l in conteo.lines] == ["8017"]
        assert aging.pending_today(s, rest.id, on=HOY, site_id=obrador.id) == ["8018"]

    def test_the_shift_close_says_what_is_left_to_weigh(self, grupo):
        """Lo que madura se pesa todas las noches: si falta, el cierre lo dice."""
        from thegrill.models import Storage
        from thegrill.web import aging, defrost

        s, rest, ana, luis, obrador, playa = grupo
        pieza(s, rest, serial="8017", kg=9.0)
        aging.move(s, ana, "8017", Storage.AGING, on=HOY)
        sites.send_primal(s, ana, "8017", playa.id, on=HOY)

        cierre = defrost.close(s, luis, on=HOY, lang="es")

        assert cierre.aging_pending == ["8017"]
        assert any(a.code == "aging.uncounted" for a in cierre.alerts)

        aging.count_day(s, luis, [("8017", 8.7)], on=HOY, lang="es")
        assert defrost.close(s, luis, on=HOY, shift="noche", lang="es").aging_pending == []

    def test_an_outlet_cuts_the_primal_it_received(self, grupo):
        """El local también corta: lo que reciba se despieza allí y se queda allí."""
        s, rest, ana, luis, obrador, playa = grupo
        p = pieza(s, rest, serial="8017", kg=9.0, precio=30.0)
        sites.send_primal(s, ana, "8017", playa.id, on=HOY)
        cut = meat.create_cut(s, ana, "Lomo")
        item = meat.add_article(s, ana, cut, "Lomo AUS")
        p.expiry_label = HOY
        s.flush()

        meat.post_butchery(s, luis, tg="TG-0001", serials=["8017"], before_kg=9.0,
                           rows=[meat.CutRow(name="Lomo", item_id=item.id,
                                             pieces=6, grams=1200)],
                           waste_kg=1.8, on=HOY)

        assert {l.site_id for l in s.query(IngredientLot)} == {playa.id}

    def test_nobody_cuts_a_piece_that_is_in_the_other_site(self, grupo):
        s, rest, ana, luis, obrador, playa = grupo
        p = pieza(s, rest, serial="8017", kg=9.0)
        cut = meat.create_cut(s, ana, "Lomo")
        item = meat.add_article(s, ana, cut, "Lomo AUS")
        p.expiry_label = HOY
        s.flush()

        with pytest.raises(meat.MeatError):
            meat.post_butchery(s, luis, tg="TG-0001", serials=["8017"], before_kg=9.0,
                               rows=[meat.CutRow(name="Lomo", item_id=item.id,
                                                 pieces=6, grams=1200)],
                               waste_kg=1.8, on=HOY)

    def test_one_table_does_not_mix_two_chillers(self, grupo):
        s, rest, ana, luis, obrador, playa = grupo
        uno = pieza(s, rest, serial="8017", kg=9.0)
        dos = pieza(s, rest, serial="8018", kg=9.0)
        sites.send_primal(s, ana, "8018", playa.id, on=HOY)
        cut = meat.create_cut(s, ana, "Lomo")
        item = meat.add_article(s, ana, cut, "Lomo AUS")
        uno.expiry_label = dos.expiry_label = HOY
        s.flush()

        with pytest.raises(meat.MeatError):
            meat.post_butchery(s, ana, tg="TG-0001", serials=["8017", "8018"],
                               before_kg=18.0,
                               rows=[meat.CutRow(name="Lomo", item_id=item.id,
                                                 pieces=12, grams=1200)],
                               waste_kg=3.6, on=HOY)

    def test_an_aged_piece_at_the_outlet_is_sold_by_weight_there(self, grupo):
        """Lo curado llega al local y se corta al peso delante del cliente."""
        from thegrill.models import Storage
        from thegrill.web import aging

        s, rest, ana, luis, obrador, playa = grupo
        pieza(s, rest, serial="8017", kg=9.0, precio=30.0)
        aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY)
        sites.send_primal(s, ana, "8017", playa.id, on=HOY)

        venta = aging.sell_by_weight(s, luis, "8017", 400, price=60.0, on=HOY)

        assert venta.kg_left == pytest.approx(8.6)
        assert s.query(Primal).one().site_id == playa.id      # sigue siendo del local


# ================================================== las puertas, no la barra
class TestDoors:
    """Una barra sin enlace no es una puerta cerrada.

    Las pantallas enseñan solo lo de cada sede, pero el número se puede
    escribir a mano. Estas son las puertas de verdad: las del motor.
    """

    @pytest.fixture
    def grupo(self, ctx):
        from thegrill.models import Role

        s, rest, ana, luis = ctx
        obrador = sites.main(s, rest.id)
        playa = sites.create(s, ana, "Playa")
        luis.role = Role.BUTCHER
        sites.assign(s, ana, luis, playa.id)
        return s, rest, ana, luis, obrador, playa

    def lote_en(self, s, rest, ana, site_id, serial="TG-0001·01", kg=6.0, frozen=False):
        cut = meat.create_cut(s, ana, f"Lomo {serial}")
        item = meat.add_article(s, ana, cut, f"Art {serial}")
        row = IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=cut.id,
                            lot_code="TG-0001", serial=serial, expiry=HOY, received=HOY,
                            qty=kg, qty_remaining=kg, unit_cost=40.0, pieces=20,
                            frozen=frozen, site_id=site_id)
        s.add(row); s.flush(); return row

    def test_nobody_weighs_a_piece_from_another_site(self, grupo):
        from thegrill.models import Storage
        from thegrill.web import aging

        s, rest, ana, luis, obrador, playa = grupo
        pieza(s, rest, serial="8017", kg=9.0)
        aging.move(s, ana, "8017", Storage.AGING, on=HOY)

        with pytest.raises(aging.AgingError):
            aging.weigh(s, luis, "8017", 8.7, on=HOY)
        assert s.query(Primal).one().weight_kg == 9.0

    def test_nobody_moves_or_sells_a_piece_from_another_site(self, grupo):
        from thegrill.models import Storage
        from thegrill.web import aging

        s, rest, ana, luis, obrador, playa = grupo
        pieza(s, rest, serial="8017", kg=9.0)

        with pytest.raises(aging.AgingError):
            aging.move(s, luis, "8017", Storage.AGING, on=HOY)
        with pytest.raises(aging.AgingError):
            aging.sell_by_weight(s, luis, "8017", 400, price=60.0, on=HOY)

    def test_nobody_throws_away_meat_from_another_site(self, grupo):
        from thegrill.web import waste

        s, rest, ana, luis, obrador, playa = grupo
        row = self.lote_en(s, rest, ana, obrador.id)

        with pytest.raises(waste.WasteError):
            waste.record(s, luis, kg=1.0, serial=row.serial, reason="se cayó", on=HOY)
        assert row.qty_remaining == 6.0

    def test_nobody_opens_the_freezer_of_another_site(self, grupo):
        from thegrill.web import defrost

        s, rest, ana, luis, obrador, playa = grupo
        row = self.lote_en(s, rest, ana, obrador.id, frozen=True)

        with pytest.raises(defrost.DefrostError):
            defrost.intake(s, luis, row.serial, 5, 1.5, on=HOY)
        assert row.frozen is True and row.qty_remaining == 6.0

    def test_nobody_sends_meat_that_is_not_theirs(self, grupo):
        """El traslado comprobaba el destino; ahora también el origen."""
        s, rest, ana, luis, obrador, playa = grupo
        pieza(s, rest, serial="8017", kg=9.0)
        row = self.lote_en(s, rest, ana, obrador.id)

        with pytest.raises(sites.SiteError):
            sites.send_primal(s, luis, "8017", playa.id, on=HOY)
        with pytest.raises(sites.SiteError):
            sites.send_cut(s, luis, row.serial, 2.0, playa.id, on=HOY)
        assert s.query(Primal).one().site_id is None
        assert row.qty_remaining == 6.0

    def test_each_bar_closes_its_own_shift(self, grupo):
        """El cierre del local no cuenta lo que se sacó en el obrador."""
        from thegrill.web import defrost

        s, rest, ana, luis, obrador, playa = grupo
        suyo = self.lote_en(s, rest, ana, playa.id, serial="TG-0001·02")
        ajeno = self.lote_en(s, rest, ana, obrador.id, serial="TG-0001·01")
        defrost.intake(s, luis, suyo.serial, 5, 1.5, on=HOY)
        defrost.intake(s, ana, ajeno.serial, 5, 1.5, on=HOY)

        mios = defrost.shift_states(s, rest.id, HOY, site_id=playa.id)
        assert [x.serial for x in mios] == [suyo.serial]
        assert len(defrost.shift_states(s, rest.id, HOY)) == 2      # el manager, los dos

    def test_the_manager_still_works_with_the_whole_house(self, grupo):
        """Quien no tiene sede no encuentra puertas: es su casa entera."""
        from thegrill.models import Storage
        from thegrill.web import aging

        s, rest, ana, luis, obrador, playa = grupo
        pieza(s, rest, serial="8017", kg=9.0)
        sites.send_primal(s, ana, "8017", playa.id, on=HOY)

        aging.move(s, ana, "8017", Storage.AGING, on=HOY)     # la del local, sin queja
        assert aging.weigh(s, ana, "8017", 8.7, on=HOY).kg == 8.7


# ================================================= el inventario, cámara a cámara
class TestInventory:
    """Cada cámara se cuenta por su cuenta.

    Contar el obrador y el local en la misma hoja no cuadra nada: nadie pesa
    dos cámaras que están a veinte kilómetros, y lo que no se mira sale como
    faltante. Así que cada sede abre la suya, y pueden estar contando a la vez.
    """

    @pytest.fixture
    def grupo(self, ctx):
        from thegrill.models import Role

        s, rest, ana, luis = ctx
        obrador = sites.main(s, rest.id)
        playa = sites.create(s, ana, "Playa")
        luis.role = Role.BUTCHER
        sites.assign(s, ana, luis, playa.id)
        cut = meat.create_cut(s, ana, "Entrecot")
        item = meat.add_article(s, ana, cut, "Entrecot AUS")
        for serial, site in (("TG-0001·01", obrador.id), ("TG-0001·02", playa.id)):
            s.add(IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=cut.id,
                                lot_code="TG-0001", serial=serial, expiry=HOY, received=HOY,
                                qty=6.0, qty_remaining=6.0, unit_cost=40.0, site_id=site))
        pieza(s, rest, serial="8017", site_id=obrador.id)
        s.flush()
        return s, rest, ana, luis, obrador, playa

    def test_each_site_counts_only_its_own_chiller(self, grupo):
        from thegrill.web import inventory

        s, rest, ana, luis, obrador, playa = grupo
        assert {e.serial for e in inventory.expected_now(s, rest.id, playa.id)} == {"TG-0001·02"}
        assert {e.serial for e in inventory.expected_now(s, rest.id, obrador.id)} == {
            "TG-0001·01", "8017"}
        # El manager, sin sede, sigue pudiendo contar la casa entera.
        assert len(inventory.expected_now(s, rest.id)) == 3

    def test_two_sites_can_be_counting_at_the_same_time(self, grupo):
        from thegrill.web import inventory

        s, rest, ana, luis, obrador, playa = grupo
        suyo = inventory.open_count(s, luis, on=HOY)                     # la playa
        del_obrador = inventory.open_count(s, ana, on=HOY, site_id=obrador.id)

        assert suyo.site_id == playa.id and del_obrador.site_id == obrador.id
        assert [l.serial for l in suyo.lines] == ["TG-0001·02"]
        assert inventory.open_now(s, rest.id, playa.id).id == suyo.id

    def test_the_same_chiller_does_not_get_two_sheets(self, grupo):
        from thegrill.web import inventory

        s, rest, ana, luis, obrador, playa = grupo
        inventory.open_count(s, luis, on=HOY)
        with pytest.raises(inventory.InventoryError):
            inventory.open_count(s, luis, on=HOY)

    def test_closing_the_outlet_sheet_does_not_touch_the_warehouse(self, grupo):
        """Lo que no se ha mirado no puede salir como faltante."""
        from thegrill.web import inventory

        s, rest, ana, luis, obrador, playa = grupo
        hoja = inventory.open_count(s, luis, on=HOY)
        inventory.record(s, luis, hoja, "TG-0001·02", 5.6)

        cierre = inventory.close_count(s, luis, hoja, lang="es")

        assert cierre.summary.complete                       # se contó todo lo suyo
        assert not cierre.phantoms
        lotes = {l.serial: l.qty_remaining for l in s.query(IngredientLot)}
        assert lotes["TG-0001·02"] == pytest.approx(5.6)     # re-anclado
        assert lotes["TG-0001·01"] == 6.0                    # el obrador, intacto
        assert s.query(Primal).one().suspect_phantom is False

    def test_the_month_is_due_per_site(self, grupo):
        from thegrill.web import inventory

        s, rest, ana, luis, obrador, playa = grupo
        hoja = inventory.open_count(s, luis, on=HOY)
        inventory.record(s, luis, hoja, "TG-0001·02", 6.0)
        inventory.close_count(s, luis, hoja, lang="es")

        assert inventory.monthly_status(s, rest.id, on=HOY, site_id=playa.id).done
        assert not inventory.monthly_status(s, rest.id, on=HOY, site_id=obrador.id).done


# ================================================== los mínimos de cada sede
class TestPars:
    """La playa en agosto y la sierra en enero no quieren el mismo mínimo."""

    @pytest.fixture
    def grupo(self, ctx):
        s, rest, ana, luis = ctx
        obrador = sites.main(s, rest.id)
        playa = sites.create(s, ana, "Playa")
        cut = meat.create_cut(s, ana, "Entrecot", min_stock=10.0)
        item = meat.add_article(s, ana, cut, "Entrecot AUS")
        for serial, site in (("TG-0001·01", obrador.id), ("TG-0001·02", playa.id)):
            s.add(IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=cut.id,
                                lot_code="TG-0001", serial=serial, expiry=HOY, received=HOY,
                                qty=6.0, qty_remaining=6.0, unit_cost=40.0, site_id=site))
        s.flush()
        return s, rest, ana, obrador, playa, cut

    def test_the_site_minimum_beats_the_house_one(self, grupo):
        from thegrill.web import butchery

        s, rest, ana, obrador, playa, cut = grupo
        # Con el de la casa (10 kg), los 6 de la playa están por debajo.
        assert butchery.status(s, rest.id, on=HOY, site_id=playa.id).cuts[0].below_par

        sites.set_par(s, ana, playa.id, ingredient_id=cut.id, minimum=4.0)

        fila = butchery.status(s, rest.id, on=HOY, site_id=playa.id).cuts[0]
        assert fila.min_stock == 4.0 and not fila.below_par
        # Y el obrador sigue con el de la casa.
        assert butchery.status(s, rest.id, on=HOY, site_id=obrador.id).cuts[0].min_stock == 10.0

    def test_an_empty_minimum_gives_the_house_one_back(self, grupo):
        from thegrill.web import butchery

        s, rest, ana, obrador, playa, cut = grupo
        sites.set_par(s, ana, playa.id, ingredient_id=cut.id, minimum=4.0)
        sites.set_par(s, ana, playa.id, ingredient_id=cut.id, minimum=None)

        assert butchery.status(s, rest.id, on=HOY, site_id=playa.id).cuts[0].min_stock == 10.0
        assert sites.pars_of(s, rest.id, playa.id).cuts == {}

    def test_a_site_sets_its_own_minimum_of_primals(self, grupo):
        from thegrill.web import butchery

        s, rest, ana, obrador, playa, cut = grupo
        pieza(s, rest, serial="8017", site_id=playa.id)
        sites.set_par(s, ana, playa.id, sku="RIBEYE_AUS", minimum=3)

        fila = [p for p in butchery.status(s, rest.id, on=HOY, site_id=playa.id).primals
                if p.sku == "RIBEYE_AUS"][0]
        assert fila.min_pieces == 3 and fila.pieces == 1

    def test_a_minimum_needs_to_be_of_something(self, grupo):
        s, rest, ana, obrador, playa, cut = grupo
        with pytest.raises(sites.SiteError):
            sites.set_par(s, ana, playa.id, minimum=4.0)

    def test_the_screen_saves_what_is_typed(self, tmp_path, monkeypatch):
        """Por la puerta por la que entra el manager."""
        from fastapi.testclient import TestClient

        from thegrill.meat import app as meatapp
        from thegrill.models import Restaurant, SitePar, User
        from tests.meat_helpers import SPANISH, csrf_from, signup

        monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
        db.init_engine(f"sqlite:///{tmp_path/'mi.db'}")
        db.create_all()
        with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as client:
            signup(client)
            with db.session_scope() as s:
                rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
                ana = s.query(User).filter_by(restaurant_id=rest.id).first()
                playa = sites.create(s, ana, "Playa")
                corte = meat.create_cut(s, ana, "Entrecot", min_stock=10.0)
                ids = (playa.id, corte.id)

            pagina = client.get(f"/sedes/{ids[0]}/minimos")
            assert pagina.status_code == 200 and "Entrecot" in pagina.text

            guardado = client.post(f"/sedes/{ids[0]}/minimos",
                                   data={f"cut:{ids[1]}": "4,5",
                                         "csrf": csrf_from(pagina.text)})
            assert guardado.status_code == 303
            with db.session_scope() as s:
                fila = s.query(SitePar).one()
                assert fila.site_id == ids[0] and fila.min_stock == 4.5


# ============================== el cuadre guardado y el parte del día
class TestReport:
    """Lo que se cierra queda escrito, y el día se puede colgar en el pase."""

    @pytest.fixture
    def casa(self, ctx):
        s, rest, ana, luis = ctx
        cut = meat.create_cut(s, ana, "Entrecot")
        item = meat.add_article(s, ana, cut, "Entrecot AUS")
        row = IngredientLot(restaurant_id=rest.id, item_id=item.id, ingredient_id=cut.id,
                            lot_code="TG-0001", serial="TG-0001·01", expiry=HOY,
                            received=HOY, qty=6.0, qty_remaining=6.0, unit_cost=40.0,
                            pieces=20)
        s.add(row); s.flush()
        return s, rest, ana, cut, row

    def test_each_close_writes_its_reconciliation(self, casa):
        from thegrill.models import ShiftClosure
        from thegrill.web import defrost

        s, rest, ana, cut, row = casa
        defrost.intake(s, ana, row.serial, 5, 1.5, on=HOY, shift="noche")
        defrost.count(s, ana, row.serial, 2, 0.6, on=HOY, shift="noche")

        defrost.close(s, ana, on=HOY, shift="noche", lang="es")

        fila = s.query(ShiftClosure).one()
        assert fila.date == HOY and fila.shift == "noche" and fila.pieces == 1
        assert fila.closed_by == ana.id

    def test_closing_the_same_shift_again_does_not_duplicate_it(self, casa):
        from thegrill.models import ShiftClosure
        from thegrill.web import defrost

        s, rest, ana, cut, row = casa
        defrost.intake(s, ana, row.serial, 5, 1.5, on=HOY, shift="noche")
        defrost.count(s, ana, row.serial, 2, 0.6, on=HOY, shift="noche")
        defrost.close(s, ana, on=HOY, shift="noche", lang="es")
        defrost.close(s, ana, on=HOY, shift="noche", lang="es")

        assert s.query(ShiftClosure).count() == 1      # un turno, un cuadre

    def test_the_month_adds_itself_up(self, casa):
        from thegrill.web import defrost

        s, rest, ana, cut, row = casa
        defrost.intake(s, ana, row.serial, 5, 1.5, on=HOY, shift="tarde")
        defrost.count(s, ana, row.serial, 2, 0.6, on=HOY, shift="tarde")
        defrost.close(s, ana, on=HOY, shift="tarde", lang="es")

        mes = defrost.month_so_far(s, rest.id, on=HOY)
        assert mes.shifts == 1 and mes.month == HOY.month
        # Lo que se ha ido es el desvío contra la carta, y **nada más**. El
        # agua del descongelado es la parte de ese mismo desvío que pasa de lo
        # que dice la carta, así que sumarla aparte era contar dos veces el
        # mismo kilo: un turno pasado de uno salía a cincuenta y dos euros
        # donde hubo veintiséis.
        #
        # Antes esto comprobaba `total_loss == loss_cost + drip_cost`, o sea
        # la fórmula y no la verdad: una prueba que garantizaba el error en
        # vez de cazarlo.
        assert mes.drip_kg >= 0
        assert mes.total_loss == pytest.approx(round(mes.loss_cost, 2))
        assert mes.drip_cost <= max(mes.loss_cost, 0) + 1e-6, (
            "el agua no puede ser más que el desvío del que forma parte")

    def test_the_daily_report_gathers_the_day(self, casa):
        from thegrill.models import Storage
        from thegrill.web import aging, defrost, waste

        s, rest, ana, cut, row = casa
        pieza(s, rest, serial="8017", kg=9.0)
        aging.move(s, ana, "8017", Storage.AGING, target_days=45, on=HOY)
        aging.count_day(s, ana, [("8017", 8.7)], on=HOY, lang="es")
        waste.record(s, ana, kg=0.4, serial=row.serial, reason="se cayó", on=HOY)
        defrost.intake(s, ana, row.serial, 5, 1.5, on=HOY)

        parte = meat.daily_report(s, rest.id, on=HOY, lang="es")

        assert [l.serial for l in parte.counted] == ["8017"]
        assert parte.to_weigh == []
        assert parte.waste_kg == pytest.approx(0.4)
        assert parte.waste_cost == pytest.approx(16.0)
        assert [st.serial for st in parte.thawing] == [row.serial]
        assert parte.day_loss >= parte.waste_cost

    def test_the_report_of_a_past_day_still_carries_its_waste(self, casa):
        """El parte de ayer se imprime hoy, y la merma de ayer tiene que salir.

        La ventana de las mermas se contaba siempre desde hoy, así que en
        cuanto el parte era de un día anterior la merma de ese día se quedaba
        fuera y el parte salía en cero.
        """
        from thegrill.web import waste

        s, rest, ana, cut, row = casa
        ayer = HOY - timedelta(days=1)
        waste.record(s, ana, kg=0.4, serial=row.serial, reason="se cayó", on=ayer)

        parte = meat.daily_report(s, rest.id, on=ayer, lang="es")

        assert parte.waste_kg == pytest.approx(0.4)
        assert meat.daily_report(s, rest.id, on=HOY, lang="es").waste_kg == 0

    def test_the_report_page_prints_the_day(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from thegrill.meat import app as meatapp
        from tests.meat_helpers import SPANISH, signup

        monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
        db.init_engine(f"sqlite:///{tmp_path/'rp.db'}")
        db.create_all()
        with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as client:
            signup(client)
            pagina = client.get("/parte")
            assert pagina.status_code == 200
            assert "Parte de carne del día" in pagina.text
            assert "@media print" in pagina.text           # sale igual en papel
            assert client.get("/parte?fecha=2026-01-15").status_code == 200
            assert client.get("/parte?fecha=nada").status_code == 200   # no revienta


# ================================ varias cámaras dentro de la misma sede
class TestChambers:
    """Una sede grande no tiene una cámara: tiene tres y un arcón."""

    def test_the_piece_says_which_chiller_it_is_in(self, ctx):
        s, rest, ana, luis = ctx
        pieza(s, rest, serial="8017")

        numero, nombre = sites.set_chamber(s, ana, "8017", "Cámara 2")

        assert (numero, nombre) == ("8017", "Cámara 2")
        assert s.query(Primal).one().chamber == "Cámara 2"

    def test_an_empty_name_leaves_it_unsaid(self, ctx):
        s, rest, ana, luis = ctx
        pieza(s, rest, serial="8017")
        sites.set_chamber(s, ana, "8017", "Cámara 2")
        sites.set_chamber(s, ana, "8017", "  ")
        assert s.query(Primal).one().chamber is None

    def test_goods_in_land_in_the_chiller_they_are_put_in(self, ctx):
        s, rest, ana, luis = ctx
        meat.receive_primals(s, ana, lot="L-1", received=HOY, chamber="Maduración",
                             rows=[meat.PrimalRow(serial="9001", sku="RIBEYE", kg=8.0,
                                                  price_kg=25.0)])
        assert s.query(Primal).filter_by(serial="9001").one().chamber == "Maduración"

    def test_the_cuts_stay_in_the_chiller_of_the_piece(self, ctx):
        s, rest, ana, luis = ctx
        p = pieza(s, rest, serial="8017", kg=9.0)
        p.chamber = "Cámara 2"
        p.expiry_label = HOY
        s.flush()
        cut = meat.create_cut(s, ana, "Lomo")
        item = meat.add_article(s, ana, cut, "Lomo AUS")

        meat.post_butchery(s, ana, tg="TG-0001", serials=["8017"], before_kg=9.0,
                           rows=[meat.CutRow(name="Lomo", item_id=item.id,
                                             pieces=6, grams=1200)],
                           waste_kg=1.8, on=HOY)

        assert {l.chamber for l in s.query(IngredientLot)} == {"Cámara 2"}

    def test_the_house_sees_which_chillers_are_in_use(self, ctx):
        s, rest, ana, luis = ctx
        pieza(s, rest, serial="8017")
        pieza(s, rest, serial="8018")
        sites.set_chamber(s, ana, "8017", "Cámara 2")
        sites.set_chamber(s, ana, "8018", "Cámara 2")

        assert sites.chambers(s, rest.id) == [("Cámara 2", 2)]

    def test_nobody_names_the_chiller_of_another_site(self, ctx):
        from thegrill.models import Role

        s, rest, ana, luis = ctx
        playa = sites.create(s, ana, "Playa")
        luis.role = Role.BUTCHER
        sites.assign(s, ana, luis, playa.id)
        pieza(s, rest, serial="8017")

        with pytest.raises(sites.SiteError):
            sites.set_chamber(s, luis, "8017", "Cámara 2")
