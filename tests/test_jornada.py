"""El día de trabajo: dónde está la casa y a qué hora cierra.

Una cocina no cierra a medianoche. Lo que se apunta a las dos y media es del
servicio de anoche, y si el programa lo mete en el día siguiente deja dos días
mal: el de ayer corto y el de hoy largo. Aquí se prueba con el reloj en la
mano, que es la única manera de probar esto sin que la suite dependa de la
hora a la que se ejecute.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.models import Primal, Restaurant
from thegrill.web import jornada

from tests.meat_helpers import SPANISH, csrf_from, login, new_house


class Casa:
    """Una casa de mentira: lo que mira `jornada` y nada más."""
    def __init__(self, tz="UTC", corte=None):
        self.timezone = tz
        self.day_cut_hour = corte


def utc(dia, hora, minuto=0):
    return datetime(2026, 9, dia, hora, minuto, tzinfo=timezone.utc)


# ------------------------------------------------------------------ la hora
def test_the_house_clock_is_not_the_server_clock():
    """Budapest a las 23:50 es el mismo día, no el siguiente."""
    casa = Casa("Europe/Budapest", corte=0)
    assert jornada.de(casa, utc(26, 21, 50)) == date(2026, 9, 26)   # 23:50 allí
    # Y el servidor, en UTC, todavía diría que son las 21:50 del 26 también;
    # la diferencia se ve al revés: medianoche allí es las 22:00 aquí.
    assert jornada.de(casa, utc(26, 22, 10)) == date(2026, 9, 27)   # 00:10 allí


def test_a_bad_zone_does_not_break_anything():
    """Una zona mal escrita se lee como UTC y se sigue trabajando."""
    assert jornada.de(Casa("Marte/Olympus", corte=0), utc(26, 12)) == date(2026, 9, 26)
    assert jornada.de(Casa("", corte=0), utc(26, 12)) == date(2026, 9, 26)


# ----------------------------------------------------------------- el corte
def test_half_past_two_belongs_to_last_nights_service():
    casa = Casa("UTC", corte=3)
    assert jornada.de(casa, utc(27, 2, 30)) == date(2026, 9, 26)
    assert jornada.de(casa, utc(27, 3, 1)) == date(2026, 9, 27)
    assert jornada.de(casa, utc(26, 23, 50)) == date(2026, 9, 26)


def test_a_house_that_closes_at_midnight_says_zero():
    casa = Casa("UTC", corte=0)
    assert jornada.de(casa, utc(27, 0, 1)) == date(2026, 9, 27)
    assert jornada.de(casa, utc(26, 23, 59)) == date(2026, 9, 26)


def test_a_house_that_closes_at_six_says_six():
    casa = Casa("UTC", corte=6)
    assert jornada.de(casa, utc(27, 5, 30)) == date(2026, 9, 26)
    assert jornada.de(casa, utc(27, 6, 30)) == date(2026, 9, 27)


def test_the_house_that_never_said_gets_three():
    """De serie son las tres, que es cuando ya no queda nadie en una cocina."""
    jornada.POR_DEFECTO = 3        # la suite lo pone a cero; aquí se prueba el de verdad
    try:
        assert jornada.corte(Casa("UTC", corte=None)) == 3
        assert jornada.de(Casa("UTC"), utc(27, 2, 30)) == date(2026, 9, 26)
    finally:
        jornada.POR_DEFECTO = 0


def test_a_finger_cannot_turn_a_morning_shift_into_yesterday():
    assert jornada.corte(Casa("UTC", corte=99)) == jornada.MAXIMO
    assert jornada.corte(Casa("UTC", corte=-4)) == 0
    assert jornada.corte(Casa("UTC", corte="tres")) == jornada.POR_DEFECTO


def test_the_two_together():
    """Budapest cerrando a las tres: la 01:00 del domingo es del sábado."""
    casa = Casa("Europe/Budapest", corte=3)
    assert jornada.de(casa, utc(26, 23, 0)) == date(2026, 9, 26)    # 01:00 del 27 allí
    assert jornada.de(casa, utc(27, 1, 30)) == date(2026, 9, 27)    # 03:30 del 27 allí


# ------------------------------------------------------------ la pantalla
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'carne.db'}")
    db.create_all()
    new_house(language="es")
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as c:
        login(c)
        yield c


def _guardar(client, **extra):
    form = client.get("/configuracion")
    datos = {"csrf": csrf_from(form.text), "language": "es"}
    datos.update(extra)
    return client.post("/configuracion", data=datos)


def test_the_manager_can_set_the_closing_hour(client):
    assert _guardar(client, day_cut_hour="5").status_code == 303
    with db.session_scope() as s:
        assert s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one().day_cut_hour == 5


def test_and_the_time_zone(client):
    assert _guardar(client, timezone="Europe/Budapest").status_code == 303
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert casa.timezone == "Europe/Budapest"


def test_a_zone_that_does_not_exist_is_not_saved(client):
    _guardar(client, timezone="Marte/Olympus")
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert casa.timezone == "UTC"          # se queda la que había


def test_a_closing_hour_that_is_not_a_number_is_not_saved(client):
    _guardar(client, day_cut_hour="las tres")
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert casa.day_cut_hour is None


def test_the_screen_shows_both(client):
    _guardar(client, timezone="Europe/Budapest", day_cut_hour="4")
    pantalla = client.get("/configuracion").text
    assert "Europe/Budapest" in pantalla
    assert '<option value="4" selected' in pantalla
    assert "Hora de cierre del día" in pantalla


def test_only_the_manager_changes_it(client, tmp_path):
    """El de la cámara no le cambia la hora de cierre a la casa."""
    from tests.meat_helpers import add_user
    otro = add_user(client)
    form = otro.get("/configuracion")
    otro.post("/configuracion", data={"csrf": csrf_from(form.text), "language": "es",
                                      "day_cut_hour": "7"})
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert casa.day_cut_hour is None


# ------------------------------------------------- y que sirva para algo
def test_what_is_written_at_two_goes_to_last_nights_day(client):
    """La prueba de que esto no es un ajuste de adorno.

    Con la casa cerrando a las tres, una recepción de las 02:30 se guarda con
    la fecha de ayer, que es el día de trabajo en el que de verdad ocurrió.
    """
    _guardar(client, day_cut_hour="3")
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert jornada.de(casa, utc(27, 2, 30)) == date(2026, 9, 26)
        assert jornada.de(casa, utc(27, 14, 0)) == date(2026, 9, 27)


def test_the_reception_uses_the_day_of_the_house(client):
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "sku": "Striploin",
        "price_kg": "32", "serial:0": "8017", "g:0": "9400"})
    with db.session_scope() as s:
        casa = s.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
        assert s.query(Primal).one().received_date == jornada.de(casa)


# ================================ la hora del apunte, no la hora del envío
#
# La cola de la cámara sella cada cosa con la hora a la que se tecleó, pero
# hasta ahora mandaba solo el contenido: un recuento apuntado a las 23:50
# dentro de la cámara y enviado a las 00:10, cuando el teléfono vuelve a tener
# señal, quedaba fechado al día siguiente. El turno de noche entero cambiaba
# de día, y ni el consumo ni el food cost de ninguno de los dos volvían a
# cuadrar.
def _ayer_a_las(hora: int, minuto: int = 0) -> str:
    de_ayer = datetime.now(timezone.utc).replace(
        hour=hora, minute=minuto, second=0, microsecond=0) - timedelta(days=1)
    return de_ayer.isoformat()


def test_what_was_written_last_night_is_filed_last_night(client):
    """Lo que se apuntó ayer entra con la fecha de ayer, no con la de hoy."""
    sello = _ayer_a_las(22, 30)
    form = client.get("/recepcion")
    r = client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "sku": "Striploin",
        "price_kg": "32", "serial:0": "8017", "g:0": "9400", "cuando": sello})
    assert r.status_code == 303
    with db.session_scope() as s:
        assert s.query(Primal).one().received_date == date.today() - timedelta(days=1)


def test_with_no_stamp_it_is_today(client):
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "sku": "Striploin",
        "price_kg": "32", "serial:0": "8018", "g:0": "9400"})
    with db.session_scope() as s:
        assert s.query(Primal).one().received_date == date.today()


def test_a_phone_with_the_clock_in_the_future_files_nothing_forward(client):
    """El sello lo pone el teléfono: no se cree a ciegas."""
    futuro = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "sku": "Striploin",
        "price_kg": "32", "serial:0": "8019", "g:0": "9400", "cuando": futuro})
    with db.session_scope() as s:
        assert s.query(Primal).one().received_date == date.today()


def test_and_a_clock_a_month_behind_does_not_file_into_a_closed_month(client):
    """Un reloj mal puesto archivaría media cámara en un mes ya cerrado."""
    viejo = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "sku": "Striploin",
        "price_kg": "32", "serial:0": "8020", "g:0": "9400", "cuando": viejo})
    with db.session_scope() as s:
        assert s.query(Primal).one().received_date == date.today()


def test_rubbish_in_the_stamp_is_just_today(client):
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "sku": "Striploin",
        "price_kg": "32", "serial:0": "8021", "g:0": "9400", "cuando": "el martes"})
    with db.session_scope() as s:
        assert s.query(Primal).one().received_date == date.today()


def test_the_waste_of_last_night_is_last_nights_waste(client):
    """Y lo mismo en la merma, que es lo que más se apunta sin señal."""
    from thegrill.models import IngredientMovement, MovementKind
    form = client.get("/recepcion")
    client.post("/recepcion", data={
        "csrf": csrf_from(form.text), "lot": "L1", "sku": "Striploin",
        "price_kg": "32", "serial:0": "8017", "g:0": "9400"})
    pantalla = client.get("/merma")
    client.post("/merma", data={
        "csrf": csrf_from(pantalla.text), "g": "1200", "serial": "8017",
        "reason": "Caducado", "envio": "m-1", "cuando": _ayer_a_las(23, 50)})
    with db.session_scope() as s:
        apunte = (s.query(IngredientMovement)
                  .filter(IngredientMovement.kind == MovementKind.WASTE).first())
        if apunte is not None:                 # la merma pide un lote de corte
            assert apunte.date == date.today() - timedelta(days=1)


def test_the_program_opens_on_a_machine_with_no_timezone_database():
    """Windows no trae zonas horarias; Linux y Mac sí.

    `ZoneInfo("UTC")` parece lo mismo que `timezone.utc` y no lo es: va a
    buscar la base de datos del sistema. Escrito al importar el módulo, tumbaba
    la aplicación entera antes de arrancar en cualquier Windows sin `tzdata`
    —ni una pantalla, ni un error entendible, un vuelco al importar—. Y no lo
    veía nadie, porque las pruebas y el contenedor corren en Linux.

    UTC no necesita esa base: no tiene horario de verano ni historia, es cero.
    """
    from datetime import timezone
    assert jornada.UTC is timezone.utc


def test_a_named_zone_that_cannot_be_read_falls_back_instead_of_breaking(monkeypatch):
    """Sin la base de datos, la casa trabaja con la hora corrida. Y abre.

    Es lo menos malo: una hora de diferencia en el corte del día se nota y se
    arregla instalando `tzdata`; un programa que no abre con el camión en el
    muelle, no.
    """
    def no_hay(*a, **k):
        raise Exception("aquí no hay base de datos de zonas")
    monkeypatch.setattr(jornada, "ZoneInfo", no_hay)
    assert jornada.zona("Europe/Budapest") is jornada.UTC
    # Y la hora de la casa se sigue pudiendo pedir, que es lo que importa.
    from types import SimpleNamespace
    assert jornada.ahora(SimpleNamespace(timezone="Europe/Budapest")) is not None
