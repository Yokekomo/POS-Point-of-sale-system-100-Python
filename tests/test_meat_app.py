"""La edición solo carne: control de carnes para restaurantes y hoteles.

Lo que se comprueba es el recorrido entero de una pieza en esta app: llega en
un lote de recepción con su número y su coste, se despieza en cortes con serial
propio, sale a descongelar, se cuenta al cerrar el turno, se vende, se cuadra en
el inventario y se puede seguir su historia. Y que no hay ninguna puerta a la
cocina general: aquí solo hay carne.
"""
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.models import (Despiece, Ingredient, IngredientItem, IngredientLot,
                             IngredientMovement, MovementKind, Primal, PrimalStatus,
                             Recipe, Restaurant)

HOY = date.today()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'carne.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False) as c:
        yield c


def csrf_from(html):
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "la página no trae token CSRF"
    return m.group(1)


def signup(client, restaurant="Hotel Marina", email="albano@marina.com", name="Albano",
           language=""):
    r = client.post("/signup", data={"restaurant": restaurant, "name": name, "email": email,
                                     "password": "clave-larga-1", "language": language})
    assert r.status_code == 303 and r.headers["location"] == "/hoy"
    return r


def join_code(slug="hotel-marina"):
    with db.session_scope() as s:
        return s.query(Restaurant).filter_by(slug=slug).one().join_code


def join(client, code, email="marta@marina.com", name="Marta"):
    r = client.post("/join", data={"join_code": code, "name": name, "email": email,
                                   "password": "clave-larga-2"})
    assert r.status_code == 303 and r.headers["location"] == "/hoy"
    return r


# ------------------------------------------------------ puertas y encuadre
def test_the_front_door_leads_to_today_not_to_the_kitchen(client):
    assert client.get("/").headers["location"] == "/login"
    signup(client)
    assert client.get("/").headers["location"] == "/hoy"
    assert client.get("/hoy").status_code == 200


def test_this_edition_says_what_it_is(client):
    signup(client)
    html = client.get("/hoy").text
    assert "Control de carnes" in html
    assert "Gestión de cocina" not in html


def test_there_is_no_door_to_anything_that_is_not_meat(client):
    """Ni recetas de cocina, ni plantillas HACCP, ni registros generales."""
    signup(client)
    for path in ("/recetas", "/ingredientes", "/manager/plantillas", "/manager/registros",
                 "/app", "/app/mis-registros", "/manager"):
        assert client.get(path).status_code == 404, path


def test_the_bar_only_shows_meat(client):
    signup(client)
    nav = client.get("/hoy").text
    for path in ("/recepcion", "/despiece", "/carne", "/descongelado", "/inventario",
                 "/merma", "/trazabilidad", "/cortes", "/carta"):
        assert f'href="{path}"' in nav, path
    assert 'href="/recetas"' not in nav
    assert 'href="/ingredientes"' not in nav


def test_someone_who_already_has_an_account_can_come_back_in(client):
    """La puerta de casa: si esto se rompe, no entra nadie."""
    signup(client)
    client.cookies.clear()
    assert client.get("/hoy").headers["location"] == "/login"

    r = client.post("/login", data={"email": "albano@marina.com", "password": "clave-larga-1"})
    assert r.status_code == 303 and r.headers["location"] == "/hoy"
    assert client.get("/hoy").status_code == 200

    fuera = client.post("/logout")
    assert fuera.status_code == 303 and fuera.headers["location"] == "/login"
    client.cookies.clear()
    assert client.get("/hoy").headers["location"] == "/login"


def test_a_wrong_password_says_nothing_about_whether_the_email_exists(client):
    signup(client)
    client.cookies.clear()
    malo = client.post("/login", data={"email": "albano@marina.com", "password": "otra-clave"})
    nadie = client.post("/login", data={"email": "nadie@marina.com", "password": "otra-clave"})
    assert malo.status_code == nadie.status_code == 200
    assert "banner bad" in malo.text and "banner bad" in nadie.text
    assert client.get("/hoy").headers["location"] == "/login"


def test_an_employee_joins_with_the_code_and_lands_on_today(client):
    signup(client)
    client.cookies.clear()
    join(client, join_code())
    assert client.get("/hoy").status_code == 200


def test_health_says_which_edition_it_is(client):
    assert client.get("/healthz").json() == {"status": "ok", "edition": "meat"}


def test_anonymous_gets_sent_to_login(client):
    for path in ("/hoy", "/recepcion", "/despiece", "/carne", "/descongelado",
                 "/inventario", "/merma", "/trazabilidad", "/cortes", "/carta", "/ventas"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/login", path


# -------------------------------------------------------------- recepción
def test_a_delivery_books_each_piece_with_its_own_number_and_cost(client):
    signup(client)
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "DXB20260910", "sku": "Striploin AUS",
        "grade": "MB9+", "origin": "AUS", "price_kg": "32", "use_by": str(HOY + timedelta(days=40)),
        "serial:0": "8017", "kg:0": "9,4",
        "serial:1": "8018", "kg:1": "10,2", "price:1": "34"})
    assert r.status_code == 200
    with db.session_scope() as s:
        piezas = s.query(Primal).order_by(Primal.serial).all()
        assert [p.serial for p in piezas] == ["8017", "8018"]
        assert all(p.lot == "DXB20260910" for p in piezas)      # el lote es común
        assert piezas[0].piece_cost_usd == round(9.4 * 32, 4)   # el coste, de cada una
        assert piezas[1].piece_cost_usd == round(10.2 * 34, 4)  # y puede ser distinto
        assert piezas[0].grade == "MB9+" and piezas[0].origin == "AUS"
        assert piezas[0].status == PrimalStatus.IN_STOCK


def test_two_pieces_can_never_share_a_number(client):
    signup(client)
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "price_kg": "30",
        "serial:0": "8017", "kg:0": "9",
        "serial:1": "8017", "kg:1": "9"})
    assert "8017" in r.text
    with db.session_scope() as s:
        assert s.query(Primal).count() == 0       # o entra el lote entero, o no entra nada


def test_a_piece_without_a_weight_is_refused(client):
    signup(client)
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={"csrf": csrf_from(form.text), "lot": "L1",
                                        "serial:0": "8017", "kg:0": ""})
    assert r.status_code == 200
    with db.session_scope() as s:
        assert s.query(Primal).count() == 0


def test_reception_needs_a_csrf_token(client):
    signup(client)
    assert client.post("/recepcion", data={"lot": "L1", "serial:0": "8017",
                                           "kg:0": "9"}).status_code == 403


# ---------------------------------------------------------------- cortes
def setup_cuts(client):
    """Los cortes que va a dar el striploin, con su artículo de cámara."""
    form = client.get("/cortes")
    token = csrf_from(form.text)
    ids = {}
    for name in ("Striploin steak", "Tiras de striploin", "Recorte de vacuno"):
        client.post("/cortes/nuevo", data={"csrf": token, "name": name, "rotation": "FEFO"})
    with db.session_scope() as s:
        for cut in s.query(Ingredient).all():
            ids[cut.name] = cut.id
    for name, cut_id in ids.items():
        client.post(f"/cortes/{cut_id}/articulo",
                    data={"csrf": token, "name": f"{name} AUS", "supplier": "Aussie Meat"})
    with db.session_scope() as s:
        return {i.ingredient.name: i.id for i in s.query(IngredientItem).all()}


def test_a_cut_groups_articles_of_any_origin_in_one_queue(client):
    signup(client)
    setup_cuts(client)
    with db.session_scope() as s:
        cut = s.query(Ingredient).filter_by(name="Striploin steak").one()
        assert cut.category == "carne"
        assert [i.name for i in cut.items] == ["Striploin steak AUS"]
    assert "Striploin steak" in client.get("/cortes").text


def test_an_employee_cannot_invent_cuts(client):
    """El catálogo lo ve todo el equipo; tocarlo, solo dirección."""
    signup(client)
    client.cookies.clear()
    join(client, join_code())

    pagina = client.get("/cortes")
    assert pagina.status_code == 200                       # verlos sí
    assert 'action="/cortes/nuevo"' not in pagina.text     # pero sin formulario

    token = csrf_from(client.get("/merma").text)           # con un token válido suyo
    assert client.post("/cortes/nuevo",
                       data={"csrf": token, "name": "Solomillo"}).status_code == 403


# -------------------------------------------------------------- despiece
def deliver(client, serials=("8017",), kg=10.0, price=30.0):
    form = client.get("/recepcion")
    data = {"csrf": csrf_from(form.text), "lot": "DXB20260910", "sku": "Striploin AUS",
            "grade": "MB9+", "origin": "AUS", "price_kg": str(price),
            "use_by": str(HOY + timedelta(days=40))}
    for i, serial in enumerate(serials):
        data[f"serial:{i}"] = serial
        data[f"kg:{i}"] = str(kg)
    assert client.post("/recepcion", data=data).status_code == 200


def butcher(client, items, tg="TG-0001", before=10.0, waste="0,6"):
    form = client.get("/despiece")
    data = {"csrf": csrf_from(form.text), "tg": tg, "date": str(HOY),
            "staff": "Albano", "before_kg": str(before), "waste_kg": waste,
            "primal": "8017",
            "cut:0": "Striploin steak", "item:0": items["Striploin steak"],
            "pieces:0": "20", "grams:0": "250", "index:0": "1,6",
            "cut:1": "Tiras", "item:1": items["Tiras de striploin"],
            "pieces:1": "8", "grams:1": "150", "index:1": "1",
            "cut:2": "Recorte", "item:2": items["Recorte de vacuno"],
            "pieces:2": "12", "grams:2": "200", "index:2": "0,45", "trim:2": "1"}
    return client.post("/despiece", data=data)


def test_butchering_turns_a_primal_into_cuts_with_their_own_serials(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    r = butcher(client, items)
    assert r.status_code == 200 and "TG-0001" in r.text

    with db.session_scope() as s:
        lots = s.query(IngredientLot).order_by(IngredientLot.serial).all()
        assert [l.serial for l in lots] == ["8017-01", "8017-02", "8017-03"]
        assert all(l.parent_serial == "8017" for l in lots)     # de dónde salió cada uno
        assert all(l.lot_code == "TG-0001" for l in lots)
        assert all(l.grade == "MB9+" and l.origin == "AUS" for l in lots)
        # La pieza queda marcada como cortada, y solo por el despiece que lo dice.
        assert s.query(Primal).one().status == PrimalStatus.CUT
        assert s.query(Despiece).one().posted


def test_the_cost_of_the_primal_lands_on_the_cuts_and_the_waste_pays_nothing(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client, kg=10.0, price=30.0)                   # 300 de coste
    butcher(client, items)
    with db.session_scope() as s:
        lots = s.query(IngredientLot).all()
        repartido = round(sum(l.qty * l.unit_cost for l in lots), 2)
        assert repartido == 300.0                          # no se pierde ni un céntimo
        filete = next(l for l in lots if l.serial == "8017-01")
        recorte = next(l for l in lots if l.serial == "8017-03")
        assert filete.unit_cost > recorte.unit_cost        # el filete vale más por kilo


def test_a_butchery_number_is_never_reused(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client, serials=("8017", "8018"))
    butcher(client, items)
    r = butcher(client, items, tg="TG-0001")
    assert "TG-0001" in r.text
    with db.session_scope() as s:
        assert s.query(Despiece).count() == 1


def test_a_butchery_that_does_not_reconcile_leaves_nothing_behind(client):
    """Si el motor lo rechaza, no se queda un despiece a medias en la base."""
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    form = client.get("/despiece")
    r = client.post("/despiece", data={
        "csrf": csrf_from(form.text), "tg": "TG-0009", "before_kg": "10",
        "primal": "9999",                                  # esa pieza no existe
        "cut:0": "Steak", "item:0": items["Striploin steak"],
        "pieces:0": "20", "grams:0": "250"})
    assert r.status_code == 200
    with db.session_scope() as s:
        assert s.query(Despiece).count() == 0
        assert s.query(IngredientLot).count() == 0


def test_a_rejected_butchery_never_loses_the_primal(client):
    """Lo que se tira es el papel, no la pieza: sigue entera y se puede volver a cortar."""
    signup(client)
    items = setup_cuts(client)
    deliver(client)

    form = client.get("/despiece")
    r = client.post("/despiece", data={
        "csrf": csrf_from(form.text), "tg": "TG-0007", "before_kg": "10", "primal": "8017",
        "cut:0": "Steak", "item:0": "", "pieces:0": "20", "grams:0": "250"})   # sin artículo
    assert r.status_code == 200

    with db.session_scope() as s:
        pieza = s.query(Primal).filter_by(serial="8017").one()
        assert pieza.status == PrimalStatus.IN_STOCK     # entera, en su sitio
        assert pieza.weight_kg == 10.0                   # con su peso
        assert pieza.piece_cost_usd == 300.0             # y con su coste
        assert s.query(Despiece).count() == 0            # solo se ha ido el papel

    # Y vuelve a estar en la lista, lista para despiezar otra vez.
    assert "8017" in client.get("/despiece").text
    assert butcher(client, items, tg="TG-0008").status_code == 200
    with db.session_scope() as s:
        assert s.query(Primal).filter_by(serial="8017").one().status == PrimalStatus.CUT
        assert s.query(IngredientLot).count() == 3


def test_only_a_posted_butchery_marks_a_primal_as_cut(client):
    """Nunca se da una pieza por cortada por inferencia: lo dice el despiece o nada."""
    signup(client)
    items = setup_cuts(client)
    deliver(client, serials=("8017", "8018", "8019"))
    butcher(client, items)                                # solo entra la 8017
    with db.session_scope() as s:
        estados = {p.serial: p.status for p in s.query(Primal)}
        assert estados["8017"] == PrimalStatus.CUT
        assert estados["8018"] == PrimalStatus.IN_STOCK
        assert estados["8019"] == PrimalStatus.IN_STOCK
    pantalla = client.get("/despiece").text
    assert "8018" in pantalla and "8019" in pantalla      # las otras dos siguen ahí


def test_a_piece_already_cut_cannot_be_butchered_twice(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    butcher(client, items)
    r = butcher(client, items, tg="TG-0050")
    assert r.status_code == 200 and "8017" in r.text
    with db.session_scope() as s:
        assert s.query(Despiece).count() == 1
        assert s.query(IngredientLot).count() == 3        # no se han duplicado los cortes


def test_a_butchery_whose_weight_does_not_add_up_is_posted_with_a_warning(client):
    """Un descuadre de masa avisa, pero no bloquea: la carne ya está cortada."""
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    form = client.get("/despiece")
    r = client.post("/despiece", data={
        "csrf": csrf_from(form.text), "tg": "TG-0011", "before_kg": "10", "waste_kg": "0",
        "primal": "8017",
        "cut:0": "Striploin steak", "item:0": items["Striploin steak"],
        "pieces:0": "10", "grams:0": "250"})              # 2,5 kg de 10: faltan 7,5
    assert r.status_code == 200
    assert "banner warn" in r.text                        # lo dice
    with db.session_scope() as s:
        assert s.query(Despiece).one().posted             # pero lo vuelca
        assert s.query(Primal).one().status == PrimalStatus.CUT


def test_a_butchery_can_never_borrow_another_kitchens_article(client):
    """Sin esto, un formulario manipulado cuelga un lote del corte del vecino."""
    signup(client)
    setup_cuts(client)
    deliver(client)
    client.cookies.clear()
    signup(client, restaurant="Otro hotel", email="bea@otro.com", name="Bea")
    ajeno = setup_cuts(client)["Striploin steak"]          # artículo de la otra casa
    client.cookies.clear()
    client.post("/login", data={"email": "albano@marina.com", "password": "clave-larga-1"})

    form = client.get("/despiece")
    r = client.post("/despiece", data={
        "csrf": csrf_from(form.text), "tg": "TG-0012", "before_kg": "10", "primal": "8017",
        "cut:0": "Steak", "item:0": ajeno, "pieces:0": "20", "grams:0": "250"})
    assert r.status_code == 200
    with db.session_scope() as s:
        assert s.query(IngredientLot).count() == 0
        assert s.query(Primal).filter_by(serial="8017").one().status == PrimalStatus.IN_STOCK


def test_a_cut_without_its_article_is_refused(client):
    signup(client)
    setup_cuts(client)
    deliver(client)
    form = client.get("/despiece")
    r = client.post("/despiece", data={
        "csrf": csrf_from(form.text), "tg": "TG-0002", "before_kg": "10", "primal": "8017",
        "cut:0": "Steak", "item:0": "", "pieces:0": "20", "grams:0": "250"})
    assert r.status_code == 200
    with db.session_scope() as s:
        assert s.query(Despiece).count() == 0


# ---------------------------------------------------------- descongelado
def test_the_shift_count_turns_into_real_consumption(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    butcher(client, items)

    form = client.get("/descongelado")
    token = csrf_from(form.text)
    client.post("/descongelado/salida", data={"csrf": token, "serial": "8017-01",
                                              "pieces": "10", "total_kg": "2,5"})
    client.post("/descongelado/recuento", data={"csrf": token, "serial": "8017-01",
                                                "pieces": "4", "total_kg": "1,0"})
    estado = client.get("/descongelado").text
    assert "8017-01" in estado

    r = client.post("/descongelado/cierre", data={"csrf": token})
    assert r.status_code == 200
    with db.session_scope() as s:
        lot = s.query(IngredientLot).filter_by(serial="8017-01").one()
        assert lot.qty_remaining == pytest.approx(5.0 - 1.5, abs=0.001)   # salió 2,5, quedó 1,0


def test_what_went_out_but_was_not_counted_is_flagged_on_the_home_screen(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    butcher(client, items)
    form = client.get("/descongelado")
    client.post("/descongelado/salida", data={"csrf": csrf_from(form.text), "serial": "8017-01",
                                              "pieces": "6", "total_kg": "1,5"})
    assert "recuento de cierre" in client.get("/hoy").text.lower()


# ---------------------------------------------------------------- carta
def test_a_meat_dish_is_a_cut_and_some_grams_tied_to_the_pos(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    butcher(client, items)
    with db.session_scope() as s:
        cut_id = s.query(Ingredient).filter_by(name="Striploin steak").one().id

    form = client.get("/carta")
    r = client.post("/carta/nuevo", data={
        "csrf": csrf_from(form.text), "name": "Entrecot a la brasa", "cut_id": cut_id,
        "grams": "330", "sale_price": "29,50", "vat_pct": "10",
        "pos_code": "1201", "pos_name": "ENTRECOT"})
    assert r.status_code == 303

    html = client.get("/carta").text
    assert "Entrecot a la brasa" in html and "1201" in html
    with db.session_scope() as s:
        dish = s.query(Recipe).one()
        assert len(dish.lines) == 1
        assert dish.lines[0].qty == 0.33            # los gramos, en kilos
        assert dish.lines[0].ingredient_id == cut_id


def test_selling_takes_it_off_the_chiller_and_says_which_dish_it_went_to(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    butcher(client, items)
    with db.session_scope() as s:
        cut_id = s.query(Ingredient).filter_by(name="Striploin steak").one().id
    form = client.get("/carta")
    client.post("/carta/nuevo", data={
        "csrf": csrf_from(form.text), "name": "Entrecot", "cut_id": cut_id, "grams": "330",
        "sale_price": "29,50", "vat_pct": "10", "pos_code": "1201", "pos_name": "ENTRECOT"})

    ventas = client.get("/ventas")
    assert "ENTRECOT" in ventas.text
    r = client.post("/ventas", data={"csrf": csrf_from(ventas.text),
                                     "business_date": str(HOY), "units:ENTRECOT": "6"})
    assert r.status_code == 303
    with db.session_scope() as s:
        lot = s.query(IngredientLot).filter_by(serial="8017-01").one()
        assert lot.qty_remaining == pytest.approx(5.0 - 6 * 0.33, abs=0.001)
        # Cada salida deja escrito a qué plato fue: sin eso no hay food cost por pieza.
        salidas = s.query(IngredientMovement).filter_by(kind=MovementKind.SALE).all()
        assert salidas and all(m.source_ref == "ENTRECOT" for m in salidas)


# ------------------------------------------------------------ trazabilidad
def test_the_history_of_a_piece_reads_from_delivery_to_plate(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    butcher(client, items)
    html = client.get("/trazabilidad?serial=8017").text
    assert "TG-0001" in html
    assert "8017-01" in html


# --------------------------------------------------------------- aislamiento
def test_one_kitchen_never_sees_another_hotels_meat(client):
    signup(client)
    items = setup_cuts(client)
    deliver(client)
    butcher(client, items)

    client.cookies.clear()
    signup(client, restaurant="Otro hotel", email="bea@otro.com", name="Bea")
    assert "8017" not in client.get("/recepcion").text
    assert "8017-01" not in client.get("/carne").text
    assert client.get("/trazabilidad?serial=8017").text.count("8017-01") == 0


# ---------------------------------------------------------------- idiomas
def test_the_meat_screens_speak_the_six_languages(client):
    signup(client, language="de")
    html = client.get("/hoy").text
    assert "Fleischkontrolle" in html
    assert "Zerlegung" in client.get("/despiece").text
    assert 'dir="ltr"' in html


def test_arabic_turns_the_whole_thing_right_to_left(client):
    signup(client, language="ar")
    html = client.get("/hoy").text
    assert 'dir="rtl"' in html
    assert "مراقبة اللحوم" in html


def test_no_meat_screen_leaks_spanish_when_the_language_is_english(client):
    signup(client, restaurant="Marina Hotel", email="ann@marina.com", name="Ann",
           language="en")
    spanish = ["Recepción", "Despiece", "Cámara", "Descongelado", "Cortes",
               "Configuración", "Salir", "Avisos", "Inventario", "Merma",
               "Trazabilidad", "Descargas"]
    for path in ("/hoy", "/recepcion", "/despiece", "/carne", "/descongelado",
                 "/inventario", "/merma", "/trazabilidad", "/cortes", "/carta",
                 "/ventas", "/descargas", "/configuracion", "/manager/equipo"):
        html = client.get(path).text
        leaked = [w for w in spanish if w in html]
        assert not leaked, f"{path} deja en español: {leaked}"


# --------------------------------------------------------------- descargas
def test_the_printable_sheets_are_meat_sheets(client):
    import io

    from openpyxl import load_workbook
    signup(client)
    html = client.get("/descargas").text
    for code in ("recepcion", "despiece", "descongelado", "inventario", "merma"):
        assert f"/descargas/{code}.xlsx" in html
        payload = client.get(f"/descargas/{code}.xlsx").content
        ws = load_workbook(io.BytesIO(payload)).active
        assert ws.page_setup.fitToWidth == 1
        assert ws.print_title_rows.replace("$", "") == "1:8"


def test_everything_in_one_workbook_has_one_tab_per_sheet(client):
    import io

    from openpyxl import load_workbook
    signup(client)
    wb = load_workbook(io.BytesIO(client.get("/descargas/todo.xlsx").content))
    assert len(wb.sheetnames) == 5


def test_an_unknown_sheet_is_not_a_download(client):
    signup(client)
    assert client.get("/descargas/recetas.xlsx").status_code == 404


# ------------------------------------------------- los seis idiomas, enteros
def test_no_language_is_left_with_untranslated_meat_keys():
    """Añadir un idioma es copiar el español y traducirlo. Nada a medias."""
    from thegrill.meat import i18n_meat
    reference = set(i18n_meat.ES)
    for code, table in i18n_meat.CATALOGUES.items():
        assert set(table) == reference, f"{code} no cuadra con el español"
        assert all(str(v).strip() for v in table.values()), f"{code} deja textos vacíos"


def test_a_placeholder_in_one_language_is_a_placeholder_in_all_of_them():
    from thegrill.meat import i18n_meat
    for key, spanish in i18n_meat.ES.items():
        marks = set(re.findall(r"\{(\w+)\}", spanish))
        for code, table in i18n_meat.CATALOGUES.items():
            assert set(re.findall(r"\{(\w+)\}", table[key])) == marks, f"{code} · {key}"


def test_the_meat_texts_do_not_step_on_the_kitchen_ones():
    """Las claves de carne van con su prefijo: no pisan nada de la cocina."""
    from thegrill.meat import i18n_meat
    assert all(k.startswith("m.") for k in i18n_meat.ES)


def test_the_front_page_says_which_program_this_is_before_logging_in(client):
    """Quien abre el enlace tiene que saber dónde entra, y en su idioma."""
    for path in ("/login", "/signup", "/join"):
        html = client.get(path).text
        assert "Control de carnes" in html, path
        assert "Gestión de cocina" not in html, path
    ingles = client.get("/login", headers={"accept-language": "en"}).text
    assert "Meat control" in ingles and "Kitchen management" not in ingles
