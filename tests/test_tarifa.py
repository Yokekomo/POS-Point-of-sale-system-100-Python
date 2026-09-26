"""Un precio por mercado, y la oferta del mes que se renueva sola.

Había **una sola fila**: un precio y una moneda para todo el planeta. Con
veinticinco monedas dentro del programa, el escaparate enseñaba una. Eso no es
una simplificación, es enseñarle 149 € a un asador de Burgos y los mismos
149 € a un grupo hotelero de Dubái, donde un precio bajo descalifica antes de
que nadie lea qué hace el programa. Y al revés en Buenos Aires.

Lo que se vigila aquí:

- **Que cada mercado tenga el suyo**, y que una base que ya estaba funcionando
  con la tabla vieja —una fila sin mercado escrito— no se quede sin precio al
  actualizar.
- **Que el mercado se decida por lo que elige la persona**, y si no, por su
  idioma o por el país de su casa. Nunca por la dirección de red: ese dato se
  equivoca con cualquier red de empresa, y enseñar un precio distinto según
  dónde pareces estar, sin poder cambiarlo, es lo que uno no quiere que le
  hagan.
- **Que la oferta del mes se renueve sola.** Sin fecha, llega al último día del
  mes en curso. Una rebaja que hay que acordarse de renovar cada treinta días
  se queda apagada un martes cualquiera; una que hay que acordarse de quitar se
  queda puesta para siempre.
- **Que tocar un mercado no toque los otros seis.**
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import billing, tarifa
from thegrill.models import Tarifa


@pytest.fixture
def sesion(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'tarifa.db'}")
    db.create_all()
    with db.session_scope() as s:
        yield s


@pytest.fixture
def consola(tmp_path, monkeypatch):
    """La consola del dueño, ya dentro."""
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'tarifa.db'}")
    db.create_all()
    with db.session_scope() as s:
        billing.bootstrap_owner(s, "yo@plataforma.com", "Albano", "clave-larga-1")
    c = TestClient(meatapp.app, follow_redirects=False, headers={"accept-language": "es"})
    r = c.post("/login", data={"email": "yo@plataforma.com", "password": "clave-larga-1"})
    assert r.status_code == 303, r.text[:200]
    return c


def csrf(html):
    import re
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


# --------------------------------------------------- un precio por mercado
def test_every_market_has_its_own_price_and_its_own_currency(sesion):
    """Lo que había antes era uno solo, en euros, para Burgos y para Dubái."""
    vistos = {}
    for codigo in tarifa.MERCADOS:
        p = tarifa.publicada(sesion, codigo)
        assert p.mercado == codigo
        vistos[codigo] = (p.normal, p.currency)

    assert vistos["ES"][1] == "EUR" and vistos["UK"][1] == "GBP"
    assert vistos["AU"][1] == "AUD" and vistos["US"][1] == "USD"
    # El Golfo paga más que España: allí un precio bajo descalifica.
    assert vistos["GULF"][0] > vistos["ES"][0]
    assert len({m for _, m in vistos.values()}) >= 3, "sigue habiendo una sola moneda"


def test_the_extra_outlet_is_cheaper_than_the_first(sesion):
    """Dar de alta una casa se hace una vez; el segundo local no cuesta el doble."""
    for codigo in tarifa.MERCADOS:
        p = tarifa.publicada(sesion, codigo)
        assert p.extra and p.extra < p.normal, codigo


def test_the_year_paid_up_front_is_ten_months(sesion):
    """Dos meses de regalo: quien ha pagado el año no se va en febrero."""
    for codigo in tarifa.MERCADOS:
        p = tarifa.publicada(sesion, codigo)
        assert p.yearly_on and p.yearly_months == 10.0, codigo
        assert p.yearly == pytest.approx(p.price * 10, abs=0.01)
        assert p.yearly_saving == pytest.approx(p.price * 2, abs=0.01)


def test_touching_one_market_leaves_the_other_six_alone(sesion):
    """El fallo caro de una tabla con una sola fila, dicho al revés."""
    antes = {m: tarifa.publicada(sesion, m).normal for m in tarifa.MERCADOS}
    from thegrill.models import User
    quien = User(id=1, restaurant_id=1, name="x", email="x", role=None)

    tarifa.guardar(sesion, quien, mercado="AU", currency="AUD", per_outlet=500.0)
    for codigo, valor in antes.items():
        ahora = tarifa.publicada(sesion, codigo).normal
        if codigo == "AU":
            assert ahora == 500.0
        else:
            assert ahora == valor, f"{codigo} cambió al tocar Australia"


def test_a_row_from_before_the_markets_existed_is_the_default_one(sesion):
    """Una base que ya estaba funcionando no se queda sin precio al actualizar."""
    sesion.query(Tarifa).delete()
    sesion.add(Tarifa(mercado=None, currency="EUR", per_outlet=88.0))
    sesion.flush()

    p = tarifa.publicada(sesion, tarifa.MERCADO_POR_DEFECTO)
    assert p.normal == 88.0, "la fila vieja se quedó huérfana"
    # Y la página de venta no la toca: es pública, y mirar un precio no escribe.
    assert sesion.query(Tarifa).filter(Tarifa.mercado.is_(None)).count() == 1
    # Quien la adopta es la pantalla del dueño, la primera vez que entra.
    tarifa.fila(sesion, tarifa.MERCADO_POR_DEFECTO)
    assert sesion.query(Tarifa).filter(Tarifa.mercado.is_(None)).count() == 0


# ------------------------------------------------- de dónde sale el mercado
def test_what_the_person_chooses_wins_over_everything_else():
    assert tarifa.de_donde(elegido="AU", lang="es", pais="España") == "AU"


def test_the_country_of_the_house_comes_before_the_language():
    assert tarifa.de_donde(lang="es", pais="United Arab Emirates") == "GULF"
    assert tarifa.de_donde(lang="en", pais="México") == "LATAM"


def test_the_language_is_the_last_resort_and_still_better_than_nothing():
    assert tarifa.de_donde(lang="es") == "ES"
    assert tarifa.de_donde(lang="ar") == "GULF"
    assert tarifa.de_donde(lang="hu") == "EU"


def test_something_we_do_not_know_falls_back_and_does_not_blow_up():
    assert tarifa.de_donde(elegido="MARTE", lang="xx", pais="Narnia") == \
        tarifa.MERCADO_POR_DEFECTO
    assert tarifa.de_donde() == tarifa.MERCADO_POR_DEFECTO


# ---------------------------------------------------- la oferta del mes
def test_the_offer_of_the_month_runs_to_the_end_of_the_month(sesion):
    """Sin fecha escrita, hasta el último día del mes. Y al siguiente, no."""
    p = tarifa.publicada(sesion, "ES", on=date(2026, 9, 10))
    assert p.on_sale and p.until == date(2026, 9, 30)
    assert p.discount_pct == tarifa.OFERTA_PCT
    assert p.price == pytest.approx(p.normal / 2, abs=0.01)


def test_the_offer_renews_itself_next_month(sesion):
    """Una rebaja que hay que acordarse de renovar se queda apagada un martes."""
    for cuando, ultimo in ((date(2026, 9, 10), date(2026, 9, 30)),
                           (date(2026, 10, 1), date(2026, 10, 31)),
                           (date(2026, 2, 3), date(2026, 2, 28)),
                           (date(2024, 2, 3), date(2024, 2, 29)),   # bisiesto
                           (date(2026, 12, 20), date(2026, 12, 31))):
        p = tarifa.publicada(sesion, "ES", on=cuando)
        assert p.on_sale and p.until == ultimo, cuando


def test_a_date_written_by_hand_switches_the_offer_off(sesion):
    """Y una que hay que acordarse de quitar se queda puesta para siempre."""
    from thegrill.models import User
    quien = User(id=1, restaurant_id=1, name="x", email="x", role=None)
    # Una fecha por delante de hoy: `guardar` rechaza una rebaja que termina
    # antes de empezar, y con razón.
    hasta = date.today() + timedelta(days=20)
    tarifa.guardar(sesion, quien, mercado="ES", currency="EUR", per_outlet=149.0,
                   sale_on=True, sale_price=99.0, sale_until=hasta)

    assert tarifa.publicada(sesion, "ES", on=hasta).on_sale
    assert not tarifa.publicada(sesion, "ES", on=hasta + timedelta(days=1)).on_sale


# ------------------------------------------------------ lo que se ve en la web
def test_the_sales_page_shows_the_price_of_your_market(consola):
    """Y deja cambiarlo: el selector está a la vista, no escondido."""
    espanol = consola.get("/precios", headers={"accept-language": "es"}).text
    assert f'{tarifa.MERCADOS["ES"].por_local:g}' in espanol
    assert 'name="mercado"' in espanol, "sin selector no se puede corregir la suposición"

    golfo = consola.get("/precios?mercado=GULF").text
    assert f'{tarifa.MERCADOS["GULF"].por_local:g}' in golfo
    assert "$" in golfo


def test_what_you_chose_is_remembered(consola):
    """Elegir el mercado en cada visita no lo hace nadie."""
    r = consola.get("/precios?mercado=AU")
    assert r.cookies.get("grill_mercado") == "AU" or "grill_mercado" in r.headers.get(
        "set-cookie", ""), r.headers.get("set-cookie")
    # Y en la siguiente visita, sin decir nada, sigue siendo el suyo.
    assert f'{tarifa.MERCADOS["AU"].por_local:g}' in consola.get("/precios").text


def test_the_owner_configures_the_seven_markets_from_the_settings_page(consola):
    """El configurador va en configuración, no en la consola de cuentas."""
    pagina = consola.get("/configuracion?mercado=GULF").text
    for codigo in tarifa.MERCADOS:
        assert f'value="{codigo}"' in pagina, f"{codigo} no sale en el configurador"
    assert 'name="volver" value="configuracion"' in pagina

    r = consola.post("/admin/tarifa", data={
        "csrf": csrf(pagina), "mercado": "GULF", "volver": "configuracion",
        "currency": "USD", "per_outlet": "399", "extra_outlet": "199",
        "yearly_on": "1", "yearly_months": "10"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/configuracion"), r.headers["location"]
    with db.session_scope() as s:
        assert tarifa.publicada(s, "GULF").normal == 399.0


def test_a_market_that_does_not_exist_is_refused(consola):
    """Escrito a mano en la barra de direcciones, o en un formulario tocado."""
    pagina = consola.get("/configuracion").text
    r = consola.post("/admin/tarifa", data={
        "csrf": csrf(pagina), "mercado": "MARTE", "currency": "EUR", "per_outlet": "10"})
    assert r.status_code == 303 and "error=" in r.headers["location"]


# ------------------------------------------- mirar un precio no escribe nada
def test_looking_at_the_price_page_never_writes_a_row(sesion):
    """La página de precios es pública: cualquiera la abre desde internet.

    Al no encontrar la tarifa de su mercado se la creaba: un `INSERT` por una
    visita de alguien que solo está mirando. Dos cosas malas de una vez. Una,
    que cualquiera de fuera hace escribir a la base abriendo una dirección.
    Y dos, que si en ese momento hay alguien de casa guardando su trabajo, la
    base está cogida y la página de venta se queda esperando a que la suelte
    —hasta medio minuto—, con el visitante delante.
    """
    sesion.query(Tarifa).delete()
    sesion.flush()
    for mercado in tarifa.MERCADOS:
        for _ in range(3):
            p = tarifa.publicada(sesion, mercado)
            assert p.mercado == mercado
            assert p.normal == tarifa.MERCADOS[mercado].por_local
            assert p.on_sale, "la oferta del mes tiene que verse aunque no haya fila"
    assert sesion.query(Tarifa).count() == 0, "una visita ha escrito en la base"
