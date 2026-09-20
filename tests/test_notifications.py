"""Las alertas van a buscar a quien debe actuar, dentro de la plataforma."""
from datetime import date

import pytest

from thegrill import db
from thegrill.models import AlertSeverity, Notification, NotificationKind, RecordTemplate, Role
from thegrill.web import auth, service
from thegrill.web.seed import seed_templates


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'n.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, ana = auth.create_restaurant(s, "Casa Pepe", "ana@casa.com", "Ana", "clave-larga-1")
        luis = auth.join_restaurant(s, rest.join_code, "luis@casa.com", "Luis", "clave-larga-2")
        seed_templates(s, rest.id)
        yield s, rest, ana, luis


def tpl(session, rest, code):
    return session.query(RecordTemplate).filter_by(restaurant_id=rest.id, code=code).one()


def fridge_at(session, user, rest, celsius):
    return service.submit_record(session, user, tpl(session, rest, "temp_refrigeracion"),
                                 {"unidad": "Vitrina", "temperatura": str(celsius)})


def test_a_critical_alert_reaches_the_manager(ctx):
    s, rest, ana, luis = ctx
    result = fridge_at(s, luis, rest, 11)
    assert len(result.notifications) == 1
    n = result.notifications[0]
    assert n.user_id == ana.id                      # el manager, no quien lo registró
    assert n.severity == AlertSeverity.CRITICAL
    assert n.kind == NotificationKind.ALERT
    assert "Luis" in n.body and "11°C" in n.body
    assert n.alert_id == result.alerts[0].id and n.record_id == result.record.id
    assert service.unread_count(s, ana.id) == 1
    assert service.unread_count(s, luis.id) == 0    # él ya lo vio al guardar


def test_a_record_within_limits_notifies_nobody(ctx):
    s, rest, ana, luis = ctx
    assert fridge_at(s, luis, rest, 3).notifications == []
    assert service.unread_count(s, ana.id) == 0


def test_every_active_manager_is_notified_but_not_the_author(ctx):
    s, rest, ana, luis = ctx
    eva = auth.join_restaurant(s, rest.join_code, "eva@casa.com", "Eva", "clave-larga-3")
    eva.role = Role.MANAGER
    dormido = auth.join_restaurant(s, rest.join_code, "old@casa.com", "Antiguo", "clave-larga-4")
    dormido.role = Role.MANAGER
    dormido.active = False
    s.flush()

    targets = {n.user_id for n in fridge_at(s, luis, rest, 12).notifications}
    assert targets == {ana.id, eva.id}              # el desactivado no recibe nada

    # Si quien registra es manager, no se avisa a sí mismo
    targets = {n.user_id for n in fridge_at(s, ana, rest, 12).notifications}
    assert targets == {eva.id}


def test_a_manager_never_gets_notifications_from_another_restaurant(ctx):
    s, rest, ana, luis = ctx
    other, eva = auth.create_restaurant(s, "El Otro", "eva@otro.com", "Eva", "clave-larga-9")
    seed_templates(s, other.id)
    fridge_at(s, luis, rest, 12)
    assert service.unread_count(s, eva.id) == 0
    assert service.unread_count(s, ana.id) == 1


def test_several_alerts_in_one_record_produce_one_notice_each(ctx):
    s, rest, ana, luis = ctx
    result = service.submit_record(s, luis, tpl(s, rest, "recepcion"),
                                   {"proveedor": "X", "producto": "Pollo", "cantidad": "5",
                                    "caducidad": date.today().isoformat(),
                                    "temperatura_llegada": "14", "conforme": ""})
    assert len(result.alerts) == 3                  # caduca hoy, temperatura alta, no conforme
    assert len(result.notifications) == 3
    assert service.unread_count(s, ana.id) == 3


def test_closing_an_alert_tells_the_person_who_reported_it(ctx):
    s, rest, ana, luis = ctx
    result = fridge_at(s, luis, rest, 11)
    service.acknowledge_alert(s, ana, result.alerts[0].id, "Vitrina reparada, producto retirado")
    avisos = service.recent_notifications(s, luis.id)
    assert len(avisos) == 1
    assert avisos[0].kind == NotificationKind.RESOLUTION
    assert "Vitrina reparada" in avisos[0].body and "Ana" in avisos[0].body


def test_a_manager_closing_his_own_alert_does_not_notify_himself(ctx):
    s, rest, ana, luis = ctx
    result = fridge_at(s, ana, rest, 11)
    service.acknowledge_alert(s, ana, result.alerts[0].id, "Ya estaba en marcha")
    assert [n for n in service.recent_notifications(s, ana.id)
            if n.kind == NotificationKind.RESOLUTION] == []


def test_reading_marks_them_and_records_when(ctx):
    s, rest, ana, luis = ctx
    fridge_at(s, luis, rest, 11)
    fridge_at(s, luis, rest, 12)
    assert service.unread_count(s, ana.id) == 2
    assert service.mark_all_read(s, ana.id) == 2
    assert service.unread_count(s, ana.id) == 0
    assert all(n.read_at is not None for n in service.recent_notifications(s, ana.id))
    assert service.mark_all_read(s, ana.id) == 0    # idempotente


def test_notifications_come_newest_first(ctx):
    s, rest, ana, luis = ctx
    fridge_at(s, luis, rest, 9)
    last = fridge_at(s, luis, rest, 14)
    assert service.recent_notifications(s, ana.id)[0].alert_id == last.alerts[0].id
