"""Las puertas que la auditoría encontró abiertas. Que no se vuelvan a abrir.

Todas tienen la misma forma: una ruta que hace lo mismo que sus hermanas pero
a la que se le olvidó la línea que comprueba quién llama. No se ven leyendo el
código de una en una —cada una parece razonable—; se ven comparándolas.

Por eso esto no prueba «que el manager pueda»: prueba **que el de al lado no
pueda**, que es lo que nadie escribe hasta que pasa.
"""
import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.models import Billing, Restaurant, Role, Site, User
from thegrill.web import inventory, sites

from tests import meat_helpers as helpers
from tests.meat_helpers import csrf_from

SPANISH = {"accept-language": "es"}


@pytest.fixture
def casa(tmp_path, monkeypatch):
    """Un grupo con obrador y un local, su jefa y el encargado del local."""
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'puertas.db'}")
    db.create_all()
    with TestClient(meatapp.app, follow_redirects=False, headers=SPANISH) as jefa:
        helpers.signup(jefa)
        local = helpers.add_user(jefa, email="encargado@marina.com", name="Encargado",
                                 role=Role.MANAGER)
        with db.session_scope() as session:
            rest = session.query(Restaurant).filter(Restaurant.platform.isnot(True)).one()
            general = session.query(User).filter_by(email="albano@marina.com").one()
            playa = sites.create(session, general, "Playa")
            encargado = session.query(User).filter_by(email="encargado@marina.com").one()
            sites.assign(session, general, encargado, playa.id)
            datos = {"casa": rest.id, "sede": playa.id,
                     "jefa_id": general.id, "encargado_id": encargado.id}
        yield jefa, local, datos


def token(client) -> str:
    return csrf_from(client.get("/merma").text)


def test_a_local_manager_cannot_promote_himself_by_dropping_his_site(casa):
    """«Manager general» es un manager sin sede, así que quitarse la sede era
    ascender. Tres peticiones y la casa cambiaba de manos."""
    jefa, local, datos = casa
    r = local.post(f"/manager/equipo/{datos['encargado_id']}/sede",
                   data={"site": "", "csrf": token(local)})
    assert r.status_code == 403, r.status_code
    with db.session_scope() as session:
        sigue = session.get(User, datos["encargado_id"])
        assert sigue.site_id == datos["sede"], "se ha quitado la sede"


def test_a_local_manager_cannot_exile_the_boss_to_an_outlet(casa):
    """Y el segundo paso del mismo ataque: meter a la jefa en un local."""
    jefa, local, datos = casa
    r = local.post(f"/manager/equipo/{datos['jefa_id']}/sede",
                   data={"site": str(datos["sede"]), "csrf": token(local)})
    assert r.status_code == 403
    with db.session_scope() as session:
        assert session.get(User, datos["jefa_id"]).site_id is None


def test_a_local_manager_cannot_cancel_the_whole_group(casa):
    """La acción más cara del programa la pedía cualquier manager."""
    jefa, local, datos = casa
    r = local.post("/cuenta/cancelar", data={"reason": "adiós", "csrf": token(local)})
    assert r.status_code == 403
    with db.session_scope() as session:
        assert session.get(Restaurant, datos["casa"]).billing != Billing.CANCELLED


def test_the_general_manager_can_still_cancel(casa):
    """Y quien lleva la casa entera, sí: si no, esto no es una puerta, es un muro."""
    jefa, local, datos = casa
    r = jefa.post("/cuenta/cancelar", data={"reason": "cerramos", "csrf": token(jefa)})
    assert r.status_code == 303
    with db.session_scope() as session:
        assert session.get(Restaurant, datos["casa"]).billing == Billing.CANCELLED


def test_nobody_changes_their_own_password_without_knowing_the_old_one(casa):
    """Una tablet olvidada encima del pase era la cuenta entera.

    Por `/manager/equipo` no se pide la contraseña de antes —está pensada para
    que el manager le ponga una nueva a quien la ha olvidado—, así que uno
    sobre sí mismo no es el caso fácil: es el peligroso. Para lo de uno mismo
    está `/configuracion`, que sí la pide.
    """
    jefa, local, datos = casa
    r = jefa.post(f"/manager/equipo/{datos['jefa_id']}/contrasena",
                  data={"password": "otra-clave-larga-9", "csrf": token(jefa)})
    assert r.status_code == 403
    r = jefa.post(f"/manager/equipo/{datos['jefa_id']}/2fa",
                  data={"csrf": token(jefa)})
    assert r.status_code == 403


def test_a_manager_can_still_reset_the_password_of_his_people(casa):
    """Lo que sí es su trabajo sigue funcionando."""
    jefa, local, datos = casa
    r = jefa.post(f"/manager/equipo/{datos['encargado_id']}/contrasena",
                  data={"password": "clave-nueva-larga-3", "csrf": token(jefa)})
    assert r.status_code == 303


def test_one_house_cannot_open_a_count_in_another_houses_chiller(casa):
    """Y lo peor no era el cruce: al cerrar no encontraba nada que ajustar, así
    que se contaba la cámara entera y no se movía un kilo."""
    jefa, local, datos = casa
    otra = helpers.new_house("Otro Asador", "otra@asador.com", "Otra",
                             "clave-larga-7", "es")
    with db.session_scope() as session:
        ajena = sites.main(session, otra)          # la cámara de la otra casa
        general = session.query(User).filter_by(email="albano@marina.com").one()
        assert ajena.restaurant_id != general.restaurant_id
        with pytest.raises(sites.SiteError):
            inventory.open_count(session, general, site_id=ajena.id)


def test_the_recovery_codes_never_travel_in_the_address_bar(casa):
    """Cada código vale como segundo factor entero. Por la barra acababan en el
    historial de la tablet de cocina y en el registro del proxy, en claro."""
    from thegrill.web import twofactor

    jefa, local, datos = casa
    pantalla = jefa.get("/configuracion")
    with db.session_scope() as session:
        secreto = session.get(User, datos["jefa_id"]).totp_secret
    r = jefa.post("/configuracion/2fa/activar",
                  data={"csrf": csrf_from(pantalla.text),
                        "code": twofactor.code_at(secreto)})
    assert r.status_code == 200, "ya no redirige: los pinta la propia respuesta"
    assert "codes=" not in r.text and "codes=" not in r.headers.get("location", "")
    # Y por la barra tampoco entran: el GET ya no los mira.
    assert "INVENTADO12" not in jefa.get("/configuracion?codes=INVENTADO12").text


def test_the_kitchen_edition_has_the_same_headers_as_the_meat_one(casa):
    """La otra edición no mandaba ninguna, y escribía `nonce=""`: parecía que
    había política de contenido y no había ninguna."""
    from thegrill.web import app as webapp

    with TestClient(webapp.app) as cocina:
        r = cocina.get("/login")
    for cabecera in ("Content-Security-Policy", "X-Content-Type-Options",
                     "X-Frame-Options", "Referrer-Policy"):
        assert r.headers.get(cabecera), f"falta {cabecera}"
    assert 'nonce=""' not in r.text, "la política bloquearía sus propios guiones"
