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


def test_the_label_sheet_is_there_for_anyone_who_works_the_meat(client):
    """La hoja de etiquetas la imprime quien descarga la carne, no el manager."""
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)

    descargas = luis.get("/descargas").text
    assert "Etiquetas para la carne" in descargas
    assert "/descargas/etiquetas" in descargas

    hoja = luis.get("/descargas/etiquetas")
    assert hoja.status_code == 200
    # Veinticuatro etiquetas y los tres huecos que se rellenan a rotulador.
    assert hoja.text.count('class="etq"') == 24
    for hueco in ("Número de pieza", "Pieza", "Peso (kg)", "Fecha"):
        assert hueco in hoja.text, hueco
    # Y sin márgenes de impresora, que son los que descuadran la rejilla.
    assert "@page { size: A4; margin: 0 }" in hoja.text

    # Con guías para el que la imprime en folio normal y la recorta.
    assert "recortar" in luis.get("/descargas/etiquetas?recortar=1").text


def test_the_menu_says_which_dishes_are_tied_to_the_till(client):
    """Un plato sin atar a su artículo del POS no descuenta nada al venderse.

    La carta enseñaba un nombre debajo de cada plato lo estuviera o no —cuando
    faltaba, se caía al nombre del propio plato—, así que los dos casos se
    veían igual y los que no descontaban pasaban desapercibidos.
    """
    from thegrill.models import PosProduct, Recipe, RecipeKind

    ana = alta(client, "ana@marina.com", "Ana", Role.MANAGER)
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        for code, nombre in (("atado", "Entrecot a la brasa"),
                             ("suelto", "Chuletón a la brasa")):
            s.add(Recipe(restaurant_id=rest.id, code=code, name=nombre,
                         kind=RecipeKind.DISH, active=True, sale_price=28.0))
        s.flush()
        atado = s.query(Recipe).filter_by(code="atado").one()
        s.add(PosProduct(restaurant_id=rest.id, pos_code="1000",
                         pos_name="ENTRECOT", recipe_id=atado.id))
        s.flush()

    pantalla = ana.get("/carta").text

    # El que está atado enseña su número y el nombre con el que llega.
    assert "1000" in pantalla and "ENTRECOT" in pantalla
    # El que no, lo dice y lleva a arreglarlo.
    assert 'href="/carta/suelto">Sin emparejar</a>' in pantalla
    # Y ya no se inventa un nombre de POS para el que no lo tiene.
    assert pantalla.count("Chuletón a la brasa") == 1

    # Lo mismo al abrir el plato.
    assert "Sin emparejar" in ana.get("/carta/suelto").text
    suyo = ana.get("/carta/atado").text
    assert "Sin emparejar" not in suyo and "ENTRECOT" in suyo


def test_the_no_signal_screen_speaks_the_language_of_the_house(client):
    """Una casa en español con un Windows en inglés veía «No connection».

    El idioma de esa pantalla se adivinaba de la cabecera del navegador, que
    es lo único que hay cuando el ayudante se registra. Ahora lo dice la
    propia pantalla que lo registra, y va en la dirección.
    """
    alta(client, "ana@marina.com", "Ana", Role.MANAGER)

    # La página manda su idioma al registrar el ayudante.
    assert '/sw.js?idioma=es' in client.get("/hoy").text

    # Y con un navegador en inglés, el ayudante de una casa en español sigue
    # escribiendo su pantalla en español.
    guion = client.get("/sw.js?idioma=es",
                       headers={"Accept-Language": "en-GB,en;q=0.9"}).text
    assert "Sin conexión" in guion and "No connection" not in guion
    assert "Volver a intentarlo" in guion         # y se puede reintentar
    assert "Control de carnes" in guion           # con el nombre de la casa
    assert 'lang="es"' in guion


def test_the_no_signal_screen_gets_itself_out_of_there(client):
    """Esa pantalla no puede ser un callejón sin salida.

    Se llega a ella abriendo el programa mientras el servidor todavía arranca,
    o después de pararlo; el que la ve se queda mirando un cartel que no
    cambia aunque el programa ya esté contestando. Así que se pregunta ella
    sola cada dos segundos y se va en cuanto hay respuesta.
    """
    guion = client.get("/sw.js?idioma=es").text

    assert "/healthz" in guion                    # pregunta por su cuenta
    assert "cache: 'no-store'" in guion           # y por la de verdad, no la copia
    assert "location.reload()" in guion
    assert "addEventListener('online'" in guion

    # Y antes de darla por perdida, la red se prueba dos veces: medio segundo
    # de wifi parpadeando no es quedarse sin cobertura.
    assert "setTimeout(listo, 900)" in guion

    # Un idioma que no existe no cuela: se cae a lo de siempre.
    assert "Sin conexión" in client.get("/sw.js?idioma=zz").text


def test_the_screen_says_which_version_it_is(client):
    """«Eso ya está arreglado» y «eso no se ha bajado» se parecen demasiado.

    Sin un número a la vista no hay forma de saber cuál de los dos es, y se
    pierde la tarde buscando un fallo que ya estaba corregido en una copia que
    no se había actualizado.
    """
    from thegrill import version

    ana = alta(client, "ana@marina.com", "Ana", Role.MANAGER)

    pantalla = ana.get("/configuracion").text
    assert "Versión" in pantalla
    assert version.actual() in pantalla


def test_every_money_figure_says_which_money_it_is(client):
    """«Valor en cámara: 1573» no dice si son euros, dólares o pesos.

    La casa elige su moneda una vez y su símbolo sale al lado de cada cifra de
    dinero, en el nombre del recuadro o de la columna, igual que ya se dicen
    los kilos. Aquí no se convierte nada: se cobra en una moneda y es la que
    se enseña.
    """
    ana = alta(client, "ana@marina.com", "Ana", Role.MANAGER)
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        s.add(Primal(restaurant_id=rest.id, serial="9031", sku="Ribeye AUS",
                     weight_kg=9.0, landed_usd_per_kg=30.0, piece_cost_usd=270.0,
                     received_date=HOY))
        s.flush()
    token = csrf_from(ana.get("/maduracion").text)
    ana.post("/maduracion/mover", data={"csrf": token, "serial": "9031",
                                        "storage": "AGING", "target_days": "45"})

    # De fábrica, euros, y el símbolo pegado al número y no en el nombre del
    # recuadro, donde en un móvil se queda solo al final de la segunda línea.
    portada = ana.get("/hoy").text
    assert '<span class="uni">€</span></b><span>Valor en cámara</span>' in portada

    token = csrf_from(ana.get("/configuracion").text)
    assert ana.post("/configuracion", data={
        "csrf": token, "language": "es", "restaurant_language": "es",
        "currency": "GBP"}).status_code == 303

    assert '<span class="uni">£</span></b><span>Valor en cámara</span>' in ana.get("/hoy").text
    # En una columna el símbolo va una vez, en su nombre, y no en cada fila.
    assert "Valor (£)" in ana.get("/maduracion").text
    assert "Precio por kilo (£/kg)" in ana.get("/recepcion").text
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert rest.currency == "GBP"

    # Una moneda que no existe no se traga: se queda la de antes.
    token = csrf_from(ana.get("/configuracion").text)
    ana.post("/configuracion", data={"csrf": token, "language": "es",
                                     "restaurant_language": "es", "currency": "XXX"})
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert rest.currency == "GBP"


def test_the_butcher_never_sees_the_money_symbol_either(client):
    """Quien no ve el dinero tampoco ve sus columnas, con símbolo o sin él."""
    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)
    pantalla = luis.get("/hoy").text
    assert "Valor en cámara" not in pantalla


def test_the_pending_list_names_the_pieces_and_links_to_them(client):
    """«2 cortes por debajo del mínimo» no dice cuáles ni dónde están.

    Así hay que ir a buscarlos a mano por otra pantalla. Cada línea se abre y
    enseña de qué habla, con el número de cada pieza, y cada uno lleva a donde
    se arregla.
    """
    ana = alta(client, "ana@marina.com", "Ana", Role.MANAGER)
    with db.session_scope() as s:
        rest = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        for serial in ("9021", "9022"):
            s.add(Primal(restaurant_id=rest.id, serial=serial, sku="Ribeye AUS",
                         weight_kg=9.0, received_date=HOY))       # sin precio
        s.flush()

    pantalla = ana.get("/hoy").text

    # La línea habla de dos piezas y las dos salen con su número.
    assert '<a class="cual" href="/recepcion/precios"><b>9021</b>' in pantalla
    assert '<a class="cual" href="/recepcion/precios"><b>9022</b>' in pantalla
    # Y la línea entera lleva a su pantalla.
    assert 'Precios pendientes &rsaquo;' in pantalla


def test_the_bottom_bar_carries_the_work_each_person_does(client):
    """En el móvil el pulgar llega a cuatro sitios: que sean los suyos.

    La barra llevaba siempre las mismas pantallas —cámara, descongelado,
    piezas— y dejaba fuera recibir y despiezar, que es lo que hace el
    carnicero todo el día. Y al ayudante, que ni recibe ni despieza, le salían
    puertas que no puede abrir. Ahora se llenan los cuatro huecos con lo
    primero de la lista que esta persona sí pueda hacer.
    """
    import re

    def barra(texto):
        trozo = re.search(r'<nav class="tabs".*?</nav>', texto, re.S).group(0)
        return re.findall(r'href="([^"]+)"', trozo)

    luis = alta(client, "luis@marina.com", "Luis", Role.BUTCHER)
    suya = barra(luis.get("/hoy").text)
    assert suya == ["/hoy", "/recepcion", "/despiece", "/descongelado", "#menu"], suya

    eva = alta(client, "eva@marina.com", "Eva", Role.EMPLOYEE)
    suya = barra(eva.get("/hoy").text)
    assert "/recepcion" not in suya and "/despiece" not in suya, suya
    assert suya == ["/hoy", "/descongelado", "/merma", "/carne", "#menu"], suya


def test_the_menu_is_ordered_by_where_the_meat_goes(client):
    """Los grupos del menú siguen el recorrido, y cada uno dice lo que trae.

    «El día» mezclaba el trabajo con las pantallas de consulta, y «Control»
    era un cajón con el inventario, la merma, los traslados, la trazabilidad,
    el parte y los precios: seis cosas que no se parecen en nada.
    """
    import re

    ana = alta(client, "ana@marina.com", "Ana", Role.MANAGER)
    pantalla = ana.get("/hoy").text
    lado = re.search(r'<aside class="side".*?</aside>', pantalla, re.S).group(0)
    grupos = re.findall(r'<summary class="group">([^<]+)</summary>', lado)
    assert grupos == ["El día", "La carne", "Los números", "Catálogo", "La casa"], grupos

    def bajo(titulo):
        trozo = lado.split(f'<summary class="group">{titulo}</summary>')[1]
        return re.findall(r'a class="item[^"]*" href="([^"]+)"',
                          trozo.split("</details>")[0])

    assert bajo("El día") == ["/hoy", "/recepcion", "/despiece",
                              "/descongelado", "/merma"]
    # La trazabilidad es de lo que va el programa: va con la carne, no
    # enterrada entre el inventario y el parte del día.
    assert bajo("La carne") == ["/maduracion", "/carne", "/traslados",
                                "/trazabilidad"]
    assert bajo("Los números") == ["/inventario", "/recepcion/precios",
                                   "/ventas", "/parte"]
    assert bajo("Catálogo") == ["/cortes", "/carta", "/ingredientes"]

    # «Alertas» y «Avisos» eran la misma palabra dos veces en el mismo menú.
    assert "Incidencias" in lado and "Alertas" not in lado


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
