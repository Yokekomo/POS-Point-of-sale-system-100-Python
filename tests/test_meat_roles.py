"""Niveles de acceso: quién puede entrar dónde y quién ve el dinero.

Tres niveles pensados desde el trabajo:

- **Manager**: todo, y es el único que ve el dinero y toca la carta.
- **Carnicero**: la carne entera —recibe, despieza, descongela, cuenta, hace
  inventario y apunta merma— y ve lo que queda de primales y los cortes de cada
  pieza. En kilos y en piezas; el dinero, no.
- **Ayudante**: mete los datos del día y ve el stock. Ni recibe ni despieza.

Lo que no se puede tocar tampoco se enseña, y la puerta se cierra en la ruta:
una barra sin enlace no es una puerta cerrada.
"""
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import perms
from thegrill.models import (Despiece, DespieceCut, DespiecePrimal, Ingredient,
                             IngredientItem, IngredientLot, Primal, PrimalStatus,
                             Restaurant, Role, Rotation, Unit, User)
from thegrill.web import auth, costing

HOY = date.today()

# Los niveles de la casa. El de la plataforma va por su cuenta, en su prueba.
ROLES_CASA = [Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE]

# Cada pantalla con los niveles que pueden abrirla.
PANTALLAS = {
    "/hoy": {Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE},
    "/carne": {Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE},
    "/cortes": {Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE},
    "/trazabilidad": {Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE},
    "/descongelado": {Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE},
    "/merma": {Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE},
    "/inventario": {Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE},
    "/maduracion": {Role.MANAGER, Role.BUTCHER, Role.EMPLOYEE},
    "/recepcion": {Role.MANAGER, Role.BUTCHER},
    "/recepcion/precios": {Role.MANAGER},      # el dinero, solo dirección
    "/despiece": {Role.MANAGER, Role.BUTCHER},
    "/carta": {Role.MANAGER},
    "/ingredientes": {Role.MANAGER},
    "/ventas": {Role.MANAGER},
    "/manager/equipo": {Role.MANAGER},
    "/manager/alertas": {Role.MANAGER},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'roles.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        helpers.signup(c)
        yield c


from tests import meat_helpers as helpers  # noqa: E402
from tests.meat_helpers import csrf_from  # noqa: E402

# El navegador de estas pruebas habla español: los textos que se comprueban
# abajo son los españoles. Quien llega sin decir nada recibe inglés.
SPANISH = {"accept-language": "es"}


def alta(client, email, name, role: Role):
    """Da de alta a alguien con el nivel que toca y devuelve su sesión."""
    if role == Role.MANAGER:        # el manager de la casa ya existe: es Albano
        sesion = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
        helpers.login(sesion)
        return sesion
    return helpers.add_user(client, email=email, name=name, role=role)


def con_carne(client):
    """Un primal recibido y despiezado, para que haya algo que mirar."""
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        ana = s.query(User).filter_by(restaurant_id=rest.id, role=Role.MANAGER).one()
        corte = Ingredient(restaurant_id=rest.id, name="Striploin steak", unit=Unit.KG,
                           rotation=Rotation.FEFO)
        s.add(corte); s.flush()
        item = IngredientItem(restaurant_id=rest.id, ingredient_id=corte.id,
                              name="Striploin AUS")
        s.add(item); s.flush()
        lot = costing.receive(s, ana, item, 5.6, 43.0, HOY + timedelta(days=10),
                              lot_code="TG-0001", on=HOY)
        lot.serial, lot.parent_serial, lot.pieces = "8017-01", "8017", 17
        s.add(Primal(restaurant_id=rest.id, serial="8017", sku="Striploin AUS",
                     weight_kg=9.4, lot="DXB1", landed_usd_per_kg=32.0,
                     piece_cost_usd=300.8, received_date=HOY,
                     frozen_use_by=HOY + timedelta(days=40),
                     status=PrimalStatus.CUT, status_ref="TG-0001", status_date=HOY))
        s.flush()
        despiece = Despiece(restaurant_id=rest.id, tg="TG-0001", date=HOY,
                            weight_before_kg=9.4, waste_kg=0.6, total_cuts_kg=5.6,
                            yield_pct=59.6, posted=True, country="AUS")
        despiece.primals.append(DespiecePrimal(serial="8017"))
        despiece.cuts.append(DespieceCut(cut_name="Striploin steak", item_id=item.id,
                                         pieces=17, weight_per_piece_g=330,
                                         total_kg=5.6, value_index=1.0, lot_id=lot.id))
        s.add(despiece)
        s.flush()


# -------------------------------------------------------- el mapa de puertas
@pytest.mark.parametrize("path,permitidos", sorted(PANTALLAS.items()))
def test_every_screen_opens_only_for_its_level(client, path, permitidos):
    con_carne(client)
    for role in ROLES_CASA:
        sesion = alta(client, f"{role.value.lower()}@marina.com", role.value, role)
        r = sesion.get(path)
        esperado = 200 if role in permitidos else 403
        assert r.status_code == esperado, f"{path} con {role.value}"


def test_a_closed_door_says_so_in_the_readers_language(client):
    carnicero = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)
    r = carnicero.get("/carta")
    assert r.status_code == 403
    assert "nivel de acceso" in r.text


# --------------------------------------------------------------- el carnicero
def test_the_butcher_can_do_the_whole_meat_round(client):
    """Recibir, despiezar, descongelar, contar y apuntar merma: su trabajo entero."""
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)

    form = luis.get("/cortes")
    assert form.status_code == 200
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        ana = s.query(User).filter_by(restaurant_id=rest.id, role=Role.MANAGER).one()
        corte = Ingredient(restaurant_id=rest.id, name="Striploin steak", unit=Unit.KG)
        s.add(corte); s.flush()
        item = IngredientItem(restaurant_id=rest.id, ingredient_id=corte.id, name="AUS")
        s.add(item); s.flush()
        item_id = item.id

    recepcion = luis.get("/recepcion")
    assert luis.post("/recepcion", data={
        "csrf": csrf_from(recepcion.text), "lot": "DXB1", "sku": "Striploin AUS",
        "use_by": str(HOY + timedelta(days=40)),
        "serial:0": "8017", "kg:0": "9,4"}).status_code == 200

    # El precio no es suyo: el carnicero descarga y apunta lo que llega, y la
    # pieza se queda esperando a que dirección la active. Hasta entonces no se
    # puede despiezar, porque el despiece reparte el coste entre los cortes.
    assert "8017" not in luis.get("/despiece").text
    precios = client.get("/recepcion/precios")
    assert client.post("/recepcion/precios", data={
        "csrf": csrf_from(precios.text), "serial": ["8017"],
        "all_price": "32"}).status_code == 200

    despiece = luis.get("/despiece")
    assert luis.post("/despiece", data={
        "csrf": csrf_from(despiece.text), "tg": "TG-0001", "before_kg": "9,4",
        "waste_kg": "0,6", "primal": "8017", "cut:0": "Striploin steak",
        "item:0": item_id, "pieces:0": "17", "grams:0": "330"}).status_code == 200

    with db.session_scope() as s:
        assert s.query(Primal).one().status.value == "CUT"
        assert s.query(IngredientLot).filter_by(serial="8017-01").one()

    descongelado = luis.get("/descongelado")
    assert luis.post("/descongelado/salida", data={
        "csrf": csrf_from(descongelado.text), "serial": "8017-01",
        "pieces": "8", "total_kg": "2,81"}).status_code == 303
    assert luis.post("/descongelado/recuento", data={
        "csrf": csrf_from(descongelado.text), "serial": "8017-01",
        "pieces": "2", "total_kg": "0,70"}).status_code == 303
    assert luis.post("/descongelado/cierre",
                     data={"csrf": csrf_from(descongelado.text)}).status_code == 200

    inventario = luis.get("/inventario")
    assert luis.post("/inventario/abrir", data={"csrf": csrf_from(inventario.text),
                                                "period": "MONTHLY"}).status_code == 303
    merma = luis.get("/merma")
    assert luis.post("/merma", data={"csrf": csrf_from(merma.text), "serial": "8017-01",
                                     "kg": "0,2", "pieces": "1"}).status_code == 200


def test_the_butcher_sees_the_meat_but_never_the_money(client):
    con_carne(client)
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)

    camara = luis.get("/carne").text
    assert "Striploin steak" in camara          # lo que queda, sí
    assert "43.00" not in camara                # el precio, no

    cortes = luis.get("/cortes").text
    assert "Striploin steak" in cortes
    assert "/ kg" not in cortes

    hoy = luis.get("/hoy").text
    assert "Valor en cámara" not in hoy

    recepcion = luis.get("/recepcion").text
    assert "8017" in recepcion                  # la pieza y sus kilos
    assert "Coste de la pieza" not in recepcion  # su coste, no


def test_the_butcher_sees_the_cuts_of_a_piece_without_its_money(client):
    con_carne(client)
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)
    historia = luis.get("/trazabilidad?serial=8017").text
    assert "8017-01" in historia                # los cortes de esa pieza
    assert "Salió" in historia                  # y sus kilos, que son su trabajo
    assert "Ingresó" not in historia            # lo que dejó, no
    assert "Ganado" not in historia
    assert "Food cost" not in historia
    # El precio del kilo del corte se colaba en la cabecera de cada corte, sin
    # mirar quién estaba delante: es dinero y no es suyo.
    assert "43.0000" not in historia
    assert "/ KG" not in historia
    # Y el food cost iba escondido dentro de la etiqueta verde del corte
    # —«330 g (28 % FC) · MB9+»—, que se pintaba igual para todos. El peso y la
    # calidad sí son suyos; el porcentaje al que sale el corte, no.
    assert "% FC" not in historia


def test_the_manager_sees_what_each_cut_of_the_piece_left(client):
    """De un mismo primal, el filete deja dinero y el recorte se lo come.

    Por eso el desglose lleva lo ganado **por corte**, y no solo el de la pieza
    entera: sin eso se comparan dos cortes por su food cost sin saber cuál de
    los dos paga el primal.
    """
    con_carne(client)
    historia = client.get("/trazabilidad?serial=8017").text
    assert "8017-01" in historia
    for cifra in ("Salió", "Vendido", "Queda", "Ingresó", "Coste", "Ganado"):
        assert cifra in historia, cifra
    assert "43.0000" in historia                # el coste del kilo, para quien lo ve
    # Y el food cost todavía no: de este corte no se ha vendido nada, y un
    # porcentaje sobre cero no es un número que dar.
    assert "Food cost" not in historia


def test_the_manager_does_see_the_money(client):
    con_carne(client)
    hoy = client.get("/hoy").text
    assert "Valor en cámara" in hoy
    assert "Coste de la pieza" in client.get("/recepcion").text
    assert "/ kg" in client.get("/cortes").text


# ---------------------------------------------------------------- el ayudante
def test_the_assistant_records_the_day_but_does_not_open_the_chamber(client):
    marta = alta(client, "marta@marina.com", "Marta", Role.EMPLOYEE)
    assert marta.get("/descongelado").status_code == 200
    assert marta.get("/merma").status_code == 200
    assert marta.get("/inventario").status_code == 200
    assert marta.get("/recepcion").status_code == 403
    assert marta.get("/despiece").status_code == 403


def test_the_assistant_cannot_open_or_close_an_inventory(client):
    marta = alta(client, "marta@marina.com", "Marta", Role.EMPLOYEE)
    token = csrf_from(marta.get("/configuracion").text)
    assert marta.post("/inventario/abrir", data={"csrf": token,
                                                 "period": "MONTHLY"}).status_code == 403
    assert marta.post("/inventario/cerrar", data={"csrf": token}).status_code == 403


def test_the_assistant_does_not_close_the_shift(client):
    """Cerrar el turno descuenta stock: eso no lo hace un ayudante."""
    marta = alta(client, "marta@marina.com", "Marta", Role.EMPLOYEE)
    token = csrf_from(marta.get("/descongelado").text)
    assert marta.post("/descongelado/cierre", data={"csrf": token}).status_code == 403


# ------------------------------------------------- la barra dice la verdad
def test_the_bar_only_shows_the_doors_that_open(client):
    con_carne(client)
    for role, dentro, fuera in [
            (Role.BUTCHER, ["/recepcion", "/despiece", "/carne", "/merma"],
             ["/carta", "/ingredientes", "/ventas", "/manager/equipo"]),
            (Role.EMPLOYEE, ["/descongelado", "/merma", "/carne"],
             ["/recepcion", "/despiece", "/carta", "/ventas"])]:
        sesion = alta(client, f"{role.value.lower()}@marina.com", role.value, role)
        barra = sesion.get("/hoy").text
        for path in dentro:
            assert f'href="{path}"' in barra, f"{role.value} debería ver {path}"
        for path in fuera:
            assert f'href="{path}"' not in barra, f"{role.value} no debería ver {path}"


def test_a_manager_can_hand_someone_the_butchers_level(client):
    marta = alta(client, "marta@marina.com", "Marta", Role.EMPLOYEE)
    assert marta.get("/despiece").status_code == 403

    equipo = client.get("/manager/equipo")
    assert "Carnicero" in equipo.text                # el nivel se ofrece
    with db.session_scope() as s:
        user_id = s.query(User).filter_by(email="marta@marina.com").one().id
    r = client.post(f"/manager/equipo/{user_id}/rol",
                    data={"csrf": csrf_from(equipo.text), "role": "BUTCHER"})
    assert r.status_code == 303
    assert marta.get("/despiece").status_code == 200  # y se nota al momento


def test_nobody_can_hand_themselves_a_level(client):
    token = csrf_from(client.get("/configuracion").text)
    with db.session_scope() as s:
        propio = s.query(User).filter_by(email="albano@marina.com").one().id
    r = client.post(f"/manager/equipo/{propio}/rol", data={"csrf": token, "role": "EMPLOYEE"})
    assert r.status_code == 400
    with db.session_scope() as s:
        assert s.query(User).filter_by(id=propio).one().role == Role.MANAGER


# ------------------------------------------------------------- el reparto
def test_the_levels_are_a_ladder_with_nothing_lost_on_the_way_up(client):
    """Todo lo que puede el ayudante lo puede el carnicero, y el manager todo."""
    assert perms.EMPLOYEE_CAPS < perms.BUTCHER_CAPS < perms.MANAGER_CAPS


def test_only_the_manager_touches_the_money_the_menu_and_the_team(client):
    for capability in (perms.MONEY, perms.MENU, perms.TEAM, perms.CATALOGUE, perms.FIX):
        assert capability in perms.MANAGER_CAPS
        assert capability not in perms.BUTCHER_CAPS
        assert capability not in perms.EMPLOYEE_CAPS


def test_the_team_screen_actually_lists_the_team(client):
    """La pantalla de equipo tiene que enseñar al equipo, no una tabla vacía."""
    alta(client, "luis@marina.com", "Luis", Role.BUTCHER)
    alta(client, "marta@marina.com", "Marta", Role.EMPLOYEE)
    equipo = client.get("/manager/equipo").text
    for nombre in ("Albano", "Luis", "Marta"):
        assert nombre in equipo
    assert 'action="/manager/equipo/' in equipo      # y con su formulario de nivel


def test_the_butcher_opens_and_closes_inventories_but_does_not_resurrect_pieces(client):
    """Contar y cuadrar, sí. Devolver al stock una pieza dada por perdida, no."""
    con_carne(client)
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)

    inventario = luis.get("/inventario")
    assert 'action="/inventario/abrir"' in inventario.text      # abrirlo, sí
    assert 'action="/inventario/recuperar"' not in inventario.text   # resucitar, no

    token = csrf_from(inventario.text)
    assert luis.post("/inventario/recuperar",
                     data={"csrf": token, "serial": "8017"}).status_code == 403
    assert 'action="/inventario/recuperar"' in client.get("/inventario").text


# --------------------------------------------------- maduración y congelador
def test_the_butcher_moves_and_weighs_but_does_not_sell_by_weight(client):
    """La pieza la mueve y la pesa el carnicero; el precio es cosa del manager."""
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        s.add(Primal(restaurant_id=rest.id, serial="9001", sku="Ribeye AUS", weight_kg=9.0,
                     landed_usd_per_kg=30.0, piece_cost_usd=270.0, received_date=HOY))
        s.flush()

    pantalla = luis.get("/maduracion")
    assert pantalla.status_code == 200
    token = csrf_from(pantalla.text)

    assert luis.post("/maduracion/mover", data={
        "csrf": token, "serial": "9001", "storage": "AGING",
        "target_days": "45"}).status_code == 303
    pesada = luis.post("/maduracion/pesar", data={"csrf": token, "serial": "9001", "kg": "7,6"})
    assert pesada.status_code == 200

    with db.session_scope() as s:
        pieza = s.query(Primal).filter_by(serial="9001").one()
        assert pieza.weight_kg == 7.6
        assert pieza.piece_cost_usd == 270.0          # el dinero sigue en la pieza

    # Ve los kilos y los días; el coste del kilo, no.
    texto = luis.get("/maduracion").text
    assert "7.600" in texto and "45" in texto
    assert "35.53" not in texto
    assert luis.post("/maduracion/venta", data={"csrf": token, "serial": "9001",
                                                "grams": "400", "price": "52"}).status_code == 403


def test_the_piece_list_groups_by_where_it_is_and_says_what_tells_them_apart(client):
    """En una lista donde todo madura, poner «Maduración» en cada línea no sirve.

    Lo que se busca al desplegar es una pieza concreta, así que cada línea
    lleva sus kilos y sus días —que son distintos en cada una— y el sitio se
    dice una vez, en el encabezado del grupo.
    """
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        for serial in ("9011", "9012"):
            s.add(Primal(restaurant_id=rest.id, serial=serial, sku="Ribeye AUS",
                         weight_kg=9.0, landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                         received_date=HOY))
        s.flush()
    token = csrf_from(luis.get("/maduracion").text)
    for serial in ("9011", "9012"):
        luis.post("/maduracion/mover", data={"csrf": token, "serial": serial,
                                             "storage": "AGING", "target_days": "45"})

    pantalla = luis.get("/maduracion").text

    assert '<optgroup label="Maduración">' in pantalla
    # Las dos piezas salen con sus kilos, no con el estado repetido.
    assert pantalla.count('<option value="9011">9011 · Ribeye AUS · 9.000 kg') >= 1
    assert "9011 · Ribeye AUS · Maduración" not in pantalla
    assert "9012 · Ribeye AUS · Maduración" not in pantalla


def test_the_whole_piece_screen_says_what_it_holds(client):
    """El menú decía «Maduración» y dentro estaba también mover, pesar y limpiar.

    Mover una pieza al congelador no es madurarla, así que media pantalla
    quedaba donde nadie la iba a buscar. La pantalla se llama por lo que hay
    —las piezas enteras— y cada parte lleva su título: la pizarra de
    maduración por un lado y las fichas de trabajo por otro.
    """
    ana = alta(client, "ana@marina.com", "Ana", Role.MANAGER)

    pantalla = ana.get("/maduracion").text

    assert "<h1>Piezas enteras</h1>" in pantalla
    assert "Piezas enteras" in pantalla and "Maduración" in pantalla
    # La maduración sigue teniendo su sitio, ahora como una parte con nombre.
    assert "<h2>Maduración y congelador</h2>" in pantalla
    # Y las fichas de trabajo se anuncian antes de aparecer.
    assert (pantalla.index("Qué se le hace a una pieza")
            < pantalla.index("<b>Mover una pieza</b>"))


def test_the_assistant_cannot_move_a_piece(client):
    ayudante = alta(client, "eva@marina.com", "Eva", Role.EMPLOYEE)
    # En su pantalla no hay ni formulario: el token se trae de otra.
    assert "/maduracion/mover" not in ayudante.get("/maduracion").text
    token = csrf_from(ayudante.get("/merma").text)
    assert ayudante.post("/maduracion/mover", data={"csrf": token, "serial": "9001",
                                                    "storage": "FROZEN"}).status_code == 403


def test_the_manager_sells_by_weight_and_sees_the_food_cost(client):
    ana = alta(client, "ana@marina.com", "Ana", Role.MANAGER)
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        s.add(Primal(restaurant_id=rest.id, serial="9002", sku="Ribeye AUS", weight_kg=7.6,
                     landed_usd_per_kg=35.526316, piece_cost_usd=270.0, received_date=HOY))
        s.flush()
    token = csrf_from(ana.get("/maduracion").text)
    ana.post("/maduracion/mover", data={"csrf": token, "serial": "9002", "storage": "AGING"})

    venta = ana.post("/maduracion/venta", data={"csrf": token, "serial": "9002",
                                                "grams": "420", "price": "52",
                                                "dish": "Chuleta madurada"})
    assert venta.status_code == 200
    assert "29 %" in venta.text                  # food cost de esa venta, redondeado arriba
    assert "Chuleta madurada" in ana.get("/maduracion").text


def test_the_waste_screen_shows_both_sources_and_hides_the_money(client):
    """Todo lo tirado en una lista; el dinero, solo para el manager."""
    from datetime import date as _date
    ana = alta(client, "ana@marina.com", "Ana", Role.MANAGER)
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)
    con_carne(client)
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        gerente = s.query(User).filter_by(restaurant_id=rest.id, role=Role.MANAGER).one()
        s.add(Primal(restaurant_id=rest.id, serial="9100", sku="Ribeye AUS", weight_kg=10.0,
                     landed_usd_per_kg=30.0, piece_cost_usd=300.0, received_date=HOY,
                     expiry_label=HOY + timedelta(days=30)))
        s.flush()
        from thegrill.web import aging, waste
        aging.trim(s, gerente, "9100", removed_kg=1.2, on=HOY)
        waste.record(s, gerente, kg=0.2, serial="8017-01", reason="Caducado", on=HOY)

    del_manager = ana.get("/merma").text
    assert "De limpieza" in del_manager and "De cámara" in del_manager
    assert "9100" in del_manager
    assert "36.00" in del_manager          # 1,2 kg a 30 €, el coste de lo tirado

    del_carnicero = luis.get("/merma").text
    assert "De limpieza" in del_carnicero and "1.200" in del_carnicero
    assert "36.00" not in del_carnicero    # los kilos sí, el dinero no


def test_the_daily_count_of_the_aging_fridge_is_the_butchers_job(client):
    """Pesar lo que madura es contar: lo hace quien cuenta, sin ver el dinero."""
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        s.add(Primal(restaurant_id=rest.id, serial="9300", sku="Ribeye AUS", weight_kg=9.0,
                     landed_usd_per_kg=30.0, piece_cost_usd=270.0, received_date=HOY))
        s.flush()
    token = csrf_from(luis.get("/maduracion").text)
    luis.post("/maduracion/mover", data={"csrf": token, "serial": "9300",
                                         "storage": "AGING", "target_days": "45"})

    pantalla = luis.get("/maduracion")
    assert "Conteo diario" in pantalla.text and 'name="kg:9300"' in pantalla.text

    hecho = luis.post("/maduracion/conteo", data={"csrf": token, "kg:9300": "8,7"})
    assert hecho.status_code == 200
    assert "Agua evaporada" in hecho.text       # los kilos de hoy, sí
    assert "Se pierde hoy" not in hecho.text    # el dinero de esos kilos, no

    with db.session_scope() as s:
        assert s.query(Primal).filter_by(serial="9300").one().weight_kg == 8.7
