"""La persona que deja la casa: qué se borra y qué se queda.

Aquí chocan dos leyes y hay que saber por dónde pasa la raya. El (UE) 931/2011
y el (CE) 852/2004 obligan al restaurante a poder decir quién hizo cada cosa
con cada pieza: quién la pesó, quién la despiezó, quién firmó el parte. Y el
RGPD da derecho a que le borren a uno lo suyo, salvo —artículo 17.3.b— lo que
haya que conservar por una obligación legal.

Así que el nombre y el trabajo se quedan, y el correo, la contraseña, el
segundo factor y los códigos de repuesto se van. Se iban a quedar para
siempre: en la base, en las treinta copias y en el fichero que se descarga el
cliente. Un cocinero de hace dos años seguía teniendo su correo y su secreto
de dos pasos guardados en un restaurante en el que ya no trabaja.
"""
import pytest
from fastapi.testclient import TestClient

from thegrill import db
from thegrill.meat import app as meatapp
from thegrill.meat import privacy
from thegrill.models import AuditLog, AuthSession, Primal, PrimalStatus, Role, User
from thegrill.web import aging

from tests.meat_helpers import SPANISH, add_user, csrf_from, login, new_house


@pytest.fixture
def casa(tmp_path, monkeypatch):
    monkeypatch.setenv("GRILL_INSECURE_COOKIE", "1")
    db.init_engine(f"sqlite:///{tmp_path/'o.db'}")
    db.create_all()
    new_house("Asador Marina", "albano@marina.com")
    jefe = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    login(jefe)
    return jefe


def _un_cocinero(correo="luis@marina.com"):
    """Uno que trabaja, deja su firma en una pieza, y se va."""
    suyo = add_user("albano@marina.com", correo, "Luis", role=Role.BUTCHER)
    with db.session_scope() as s:
        luis = s.query(User).filter_by(email=correo).one()
        s.add(Primal(restaurant_id=luis.restaurant_id, serial="8017", sku="RIB",
                     weight_kg=9.0, received_kg=9.0, landed_usd_per_kg=30.0,
                     piece_cost_usd=270.0, status=PrimalStatus.IN_STOCK))
        s.flush()
        aging.weigh(s, luis, "8017", 8.6)
        luis.active = False
        return luis.id, suyo


def _fila(user_id):
    with db.session_scope() as s:
        quien = s.get(User, user_id)
        return {"email": quien.email, "clave": quien.password_hash,
                "totp": quien.totp_secret, "codigos": quien.recovery_codes,
                "nombre": quien.name, "activo": quien.active}


def _olvidar(jefe, user_id):
    token = csrf_from(jefe.get("/manager/equipo").text)
    return jefe.post(f"/manager/equipo/{user_id}/olvidar", data={"csrf": token})


# ------------------------------------------------------- lo que se va y lo que no
def test_what_goes_is_the_contact_and_the_keys(casa):
    """Correo, contraseña, segundo factor y códigos: fuera."""
    luis, _ = _un_cocinero()
    with db.session_scope() as s:
        quien = s.get(User, luis)
        quien.totp_secret, quien.totp_enabled = "SECRETO", True
        quien.recovery_codes = "uno,dos,tres"
    antes = _fila(luis)
    assert antes["email"] == "luis@marina.com" and antes["totp"] == "SECRETO"

    assert _olvidar(casa, luis).status_code == 303
    ahora = _fila(luis)
    assert ahora["email"].startswith(privacy.BORRADO) and "@invalid" in ahora["email"]
    assert ahora["totp"] is None and ahora["codigos"] is None
    assert ahora["clave"] != antes["clave"]
    assert ahora["activo"] is False


def test_what_stays_is_the_name_and_the_work(casa):
    """El libro de firmas de una casa de carne hay que conservarlo."""
    from thegrill.models import PrimalWeighing

    luis, _ = _un_cocinero()
    _olvidar(casa, luis)
    with db.session_scope() as s:
        assert s.get(User, luis).name == "Luis"
        pesada = s.query(PrimalWeighing).filter_by(serial="8017").one()
        assert pesada.created_by == luis, "se perdió quién pesó la pieza"
        assert round(pesada.kg, 6) == 8.6


def test_the_erased_account_no_longer_opens(casa):
    """Ni con la contraseña de antes, ni con el correo de antes."""
    luis, suyo = _un_cocinero()
    _olvidar(casa, luis)
    fuera = TestClient(meatapp.app, follow_redirects=False, headers=SPANISH)
    assert fuera.post("/login", data={"email": "luis@marina.com",
                                      "password": "clave-larga-2"}).status_code != 303
    # Y la sesión que tenía abierta se ha cerrado sola.
    assert suyo.get("/hoy").status_code in (303, 403)
    with db.session_scope() as s:
        assert s.query(AuthSession).filter_by(user_id=luis).count() == 0


def test_it_is_written_down_that_it_was_done_and_by_whom(casa):
    """Lo pide la ley: borrar datos personales deja huella."""
    luis, _ = _un_cocinero()
    _olvidar(casa, luis)
    with db.session_scope() as s:
        huella = (s.query(AuditLog).filter_by(table="personal_data",
                                              key=f"user:{luis}").one())
        assert huella.action == "FORGOTTEN"
        assert "Luis" in (huella.detail or "")
        assert "albano@marina.com" in huella.actor


# ------------------------------------------------------------- y las puertas
def test_you_cannot_erase_yourself(casa):
    """Lo haría quien entrara con tu cuenta, no tú."""
    with db.session_scope() as s:
        jefe = s.query(User).filter_by(email="albano@marina.com").one()
        mio = jefe.id
    # Se rechaza por dos sitios —ni se administra uno a sí mismo, ni se olvida
    # a uno mismo—, y da igual por cuál: lo que importa es que no pasa.
    assert _olvidar(casa, mio).status_code in (400, 403)
    with db.session_scope() as s:
        assert s.get(User, mio).email == "albano@marina.com"


def test_nobody_erases_somebody_from_another_house(casa, tmp_path):
    """La casa de al lado no se toca."""
    otra = new_house("Asador Vecino", "vecina@vecino.com", manager="Nora")
    with db.session_scope() as s:
        nora = s.query(User).filter_by(email="vecina@vecino.com").one()
        de_fuera = nora.id
    assert _olvidar(casa, de_fuera).status_code in (403, 404)
    with db.session_scope() as s:
        assert s.get(User, de_fuera).email == "vecina@vecino.com"


def test_only_whoever_runs_the_house_can_do_it(casa):
    """Un cocinero no borra a otro cocinero."""
    luis, _ = _un_cocinero()
    otro = add_user("albano@marina.com", "eva@marina.com", "Eva", role=Role.BUTCHER)
    assert otro.post(f"/manager/equipo/{luis}/olvidar",
                     data={"csrf": "loquesea"}).status_code in (303, 403)
    with db.session_scope() as s:
        assert s.get(User, luis).email == "luis@marina.com"


def test_the_screen_only_offers_it_once_they_are_out(casa):
    """Borrarle el correo a alguien que sigue trabajando lo deja fuera sin avisar."""
    trabajando = add_user("albano@marina.com", "eva@marina.com", "Eva", role=Role.BUTCHER)
    pantalla = casa.get("/manager/equipo").text
    with db.session_scope() as s:
        eva = s.query(User).filter_by(email="eva@marina.com").one().id
    assert f"/manager/equipo/{eva}/olvidar" not in pantalla

    luis, _ = _un_cocinero()                     # este ya está de baja
    assert f"/manager/equipo/{luis}/olvidar" in casa.get("/manager/equipo").text


# ================================ lo que crecía para siempre sin ser prueba
#
# La ley de la carne obliga a guardar la trazabilidad y eso no se borra nunca.
# Al lado de eso había tres cosas creciendo sin fin que no son trazabilidad de
# nada y llevan datos de personas dentro: las sesiones caducadas —que arrastran
# el último recado que se enseñó, o sea, trabajo de la casa colgando de una
# fila muerta—, los avisos ya leídos y los partes de fallo con el correo de
# quien los mandó. El RGPD 5.1.e dice que eso no se guarda más de lo que haga
# falta, y «para siempre» nunca es lo que hace falta.
def _viejo(cuantos_dias: int):
    from datetime import datetime, timedelta
    return datetime.utcnow() - timedelta(days=cuantos_dias)


def test_the_old_sessions_the_read_notices_and_the_bug_reports_go(casa):
    from thegrill.models import BugReport, Notification
    from thegrill.models import AlertSeverity, NotificationKind

    with db.session_scope() as s:
        quien = s.query(User).filter_by(email="albano@marina.com").one()
        s.add(AuthSession(token_hash="caducada", csrf="x", user_id=quien.id,
                          expires_at=_viejo(30), flash="el cuadre del despiece"))
        s.add(Notification(restaurant_id=quien.restaurant_id, user_id=quien.id,
                           kind=NotificationKind.ALERT, severity=AlertSeverity.WARNING,
                           title="viejo", body="leído hace meses",
                           read_at=_viejo(200)))
        s.add(BugReport(restaurant_id=quien.restaurant_id, user_id=quien.id,
                        email="luis@marina.com", screen="/hoy", message="se rompió",
                        created_at=_viejo(500)))

    with db.session_scope() as s:
        fuera = privacy.limpiar_lo_viejo(s)
    assert fuera == {"sesiones": 1, "avisos": 1, "fallos": 1}, fuera
    with db.session_scope() as s:
        assert s.query(AuthSession).filter_by(token_hash="caducada").count() == 0
        assert s.query(Notification).filter_by(title="viejo").count() == 0
        assert s.query(BugReport).filter_by(email="luis@marina.com").count() == 0


def test_what_still_matters_is_not_swept(casa):
    """La sesión de quien está dentro, el aviso sin leer y el fallo de ayer."""
    from thegrill.models import AlertSeverity, BugReport, Notification, NotificationKind

    with db.session_scope() as s:
        quien = s.query(User).filter_by(email="albano@marina.com").one()
        s.add(Notification(restaurant_id=quien.restaurant_id, user_id=quien.id,
                           kind=NotificationKind.ALERT, severity=AlertSeverity.CRITICAL,
                           title="sin leer", body="la cámara a nueve grados",
                           created_at=_viejo(400)))
        s.add(BugReport(restaurant_id=quien.restaurant_id, user_id=quien.id,
                        email="eva@marina.com", screen="/hoy", message="de ayer",
                        created_at=_viejo(1)))
        abiertas = s.query(AuthSession).count()

    with db.session_scope() as s:
        privacy.limpiar_lo_viejo(s)
    with db.session_scope() as s:
        # Un aviso que nadie ha abierto sigue avisando, tenga la edad que tenga.
        assert s.query(Notification).filter_by(title="sin leer").count() == 1
        assert s.query(BugReport).filter_by(message="de ayer").count() == 1
        assert s.query(AuthSession).count() == abiertas, "echó a quien estaba dentro"
    assert casa.get("/hoy").status_code == 200, "el jefe se ha quedado fuera"


def test_the_meat_is_never_swept(casa):
    """Lo que pide una inspección no lo borra ninguna tarea de limpieza."""
    from thegrill.models import AuditLog, PrimalWeighing

    luis, _ = _un_cocinero()
    with db.session_scope() as s:
        piezas = s.query(Primal).count()
        pesadas = s.query(PrimalWeighing).count()
        firmas = s.query(AuditLog).count()
    with db.session_scope() as s:
        privacy.limpiar_lo_viejo(s)
    with db.session_scope() as s:
        assert s.query(Primal).count() == piezas
        assert s.query(PrimalWeighing).count() == pesadas
        assert s.query(AuditLog).count() == firmas
