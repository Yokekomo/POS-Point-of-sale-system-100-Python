"""El parte de ventas del POS, leído de un fichero.

Cada caja exporta lo suyo. Lo que se comprueba aquí es que da igual: que se
entiende la cabecera en español o en inglés, el punto y coma o la coma, y los
decimales como los escriba. Y sobre todo, que leer no descuenta nada: primero
se enseña lo entendido y solo después, si se confirma, se toca el almacén.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import service as meat
from thegrill.models import IngredientLot, Restaurant, Role, User
from thegrill.web import pos_import

# El navegador de estas pruebas habla español: los textos que se comprueban
# abajo son los españoles. Quien llega sin decir nada recibe inglés.
SPANISH = {"accept-language": "es"}

HOY = date.today()

from tests import meat_helpers as helpers  # noqa: E402
from tests.meat_helpers import csrf_from  # noqa: E402


# ------------------------------------------------------------- el lector
def test_it_reads_a_spanish_export_with_semicolons_and_comma_decimals():
    datos = ("Código;Producto;Uds;Peso (kg);Importe\n"
             "1401;LOMO MADURADO;3;1,236;159,44\n"
             "1402;CHULETON;12;;648,00\n").encode()
    leido = pos_import.parse(datos, "ventas.csv")

    assert leido.columns["code"] == "Código" and leido.columns["kg"] == "Peso (kg)"
    assert [r.code for r in leido.rows] == ["1401", "1402"]
    assert leido.rows[0].units == 3 and leido.rows[0].kg == 1.236
    assert leido.rows[0].amount == 159.44
    assert leido.rows[1].kg is None
    assert leido.units == 15 and leido.kg == 1.236


def test_it_reads_an_english_export_with_commas_and_dot_decimals():
    datos = ("item code,Product Name,Quantity,Weight,Total\n"
             "1401,DRY AGED RIBEYE,2,0.86,112.50\n").encode()
    leido = pos_import.parse(datos, "sales.csv")
    assert leido.rows[0].name == "DRY AGED RIBEYE"
    assert leido.rows[0].units == 2 and leido.rows[0].kg == 0.86


def test_grams_are_accepted_and_turned_into_kilos():
    datos = "producto,uds,gramos\nLOMO,1,412\n".encode()
    leido = pos_import.parse(datos, "x.csv")
    assert leido.rows[0].kg == 0.412


def test_the_decimal_separator_is_decided_looking_at_the_whole_file():
    """«1,236» es kilo y pico o son mil, pero en el mismo fichero no es las dos."""
    claro = "producto;uds;peso\nLOMO;1;1,236\nCHULETON;2;12,5\n".encode()
    leido = pos_import.parse(claro, "x.csv")
    assert [r.kg for r in leido.rows] == [1.236, 12.5]
    assert leido.warnings == []

    dudoso = "producto;uds;importe\nLOMO;2;1,236\n".encode()
    duda = pos_import.parse(dudoso, "x.csv")
    assert duda.rows[0].amount == 1236
    assert duda.warnings and "decimales" in duda.warnings[0]


def test_lines_without_units_or_article_are_skipped_not_guessed():
    datos = ("codigo;producto;uds\n"
             "1401;LOMO;2\n"
             ";;\n"
             "1402;CHULETON;0\n"
             ";;5\n").encode()
    leido = pos_import.parse(datos, "x.csv")
    assert len(leido.rows) == 1 and leido.skipped == 2


def test_a_file_it_cannot_read_says_why():
    with pytest.raises(pos_import.ImportError_):
        pos_import.parse(b"", "x.csv")
    with pytest.raises(pos_import.ImportError_):
        pos_import.parse(b"una lista de la compra\nsin cabecera\n", "x.csv")
    with pytest.raises(pos_import.ImportError_):
        pos_import.parse("producto;precio\nLOMO;12\n".encode(), "x.csv")   # sin unidades


def test_it_reads_an_excel_the_same_way(tmp_path):
    from openpyxl import Workbook
    book = Workbook()
    hoja = book.active
    hoja.append(["Artículo", "Nombre", "Cantidad", "Peso"])
    hoja.append(["1401", "LOMO MADURADO", 3, 1.236])
    fichero = tmp_path / "ventas.xlsx"
    book.save(fichero)

    leido = pos_import.parse(fichero.read_bytes(), "ventas.xlsx")
    assert leido.rows[0].units == 3 and leido.rows[0].kg == 1.236


# ------------------------------------------------------- la pantalla entera
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'pos.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        helpers.signup(c)
        yield c


def carta(client):
    """Un corte con stock y dos platos: uno por ración y otro a peso."""
    from thegrill.web import costing
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        ana = s.query(User).filter_by(restaurant_id=rest.id, role=Role.MANAGER).one()
        chuleton = meat.create_cut(s, ana, "Chuletón")
        lomo = meat.create_cut(s, ana, "Lomo madurado", sold_by_weight=True)
        art_c = meat.add_article(s, ana, chuleton, "Ribeye AUS")
        art_l = meat.add_article(s, ana, lomo, "Ribeye AUS entero")
        costing.receive(s, ana, art_c, 8.4, 32.0, HOY + timedelta(days=10),
                        lot_code="TG-1", on=HOY)
        costing.receive(s, ana, art_l, 6.4, 42.0, HOY + timedelta(days=10),
                        lot_code="TG-2", on=HOY)
        meat.add_dish(s, ana, "Chuletón a la brasa", chuleton.id, 420, sale_price=54.0,
                      vat_pct=10.0, pos_code="1402", pos_name="CHULETON")
        meat.add_dish(s, ana, "Lomo madurado al corte", lomo.id, 300, by_weight=True,
                      price_per_kg=129.0, vat_pct=10.0, pos_code="1401",
                      pos_name="LOMO MADURADO")


def test_reading_the_file_shows_what_it_understood_without_touching_the_stock(client):
    carta(client)
    datos = ("Código;Producto;Uds;Peso (kg);Importe\n"
             "1401;LOMO MADURADO;2;0,824;106,30\n"
             "1402;CHULETON;3;;162,00\n"
             "9999;POSTRE DEL DIA;5;;30,00\n").encode()
    pantalla = client.get("/ventas")
    r = client.post("/ventas/fichero",
                    data={"csrf": csrf_from(pantalla.text), "business_date": str(HOY)},
                    files={"file": ("ventas.csv", datos, "text/csv")})

    assert r.status_code == 200
    assert "Lomo madurado al corte" in r.text and "Chuletón a la brasa" in r.text
    assert "POSTRE DEL DIA" in r.text and "Sin plato" in r.text     # dice lo que no sabe
    assert "824" in r.text                                          # los gramos leídos

    with db.session_scope() as s:
        quedan = {l.lot_code: l.qty_remaining for l in s.query(IngredientLot)}
    assert quedan == {"TG-1": 8.4, "TG-2": 6.4}        # leer no ha descontado nada


def test_confirming_deducts_what_has_a_dish_and_only_that(client):
    carta(client)
    datos = ("Código;Producto;Uds;Peso (kg)\n"
             "1401;LOMO MADURADO;2;0,824\n"
             "1402;CHULETON;3;\n"
             "9999;POSTRE DEL DIA;5;\n").encode()
    pantalla = client.get("/ventas")
    leido = client.post("/ventas/fichero", data={"csrf": csrf_from(pantalla.text)},
                        files={"file": ("ventas.csv", datos, "text/csv")})

    import re
    filas = re.search(r'name="rows" value="([^"]*)"', leido.text).group(1)
    assert "9999" not in filas and "POSTRE" not in filas    # lo que no tiene plato no viaja

    hecho = client.post("/ventas", data={"csrf": csrf_from(pantalla.text),
                                         "rows": filas.replace("&#34;", '"'),
                                         "business_date": str(HOY)})
    assert hecho.status_code == 303

    with db.session_scope() as s:
        quedan = {l.lot_code: round(l.qty_remaining, 4) for l in s.query(IngredientLot)}
    assert quedan["TG-2"] == pytest.approx(5.576)   # el peso real del parte, no la ración
    assert quedan["TG-1"] == pytest.approx(7.14)    # tres chuletones de 420 g


def test_a_file_that_cannot_be_read_says_so_and_changes_nothing(client):
    carta(client)
    pantalla = client.get("/ventas")
    r = client.post("/ventas/fichero", data={"csrf": csrf_from(pantalla.text)},
                    files={"file": ("lista.csv", b"esto no es un parte\n", "text/csv")})
    assert r.status_code == 200 and "cabecera" in r.text
    with db.session_scope() as s:
        assert {l.qty_remaining for l in s.query(IngredientLot)} == {8.4, 6.4}


def test_without_a_file_it_says_so(client):
    pantalla = client.get("/ventas")
    r = client.post("/ventas/fichero", data={"csrf": csrf_from(pantalla.text)})
    assert r.status_code == 200 and "ningún fichero" in r.text
