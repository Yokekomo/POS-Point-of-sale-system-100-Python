"""Validación de formularios, alertas automáticas, fotos y estadísticas."""
from datetime import date, datetime, timedelta

import pytest

from thegrill import db
from thegrill.models import Alert, AlertSeverity, RecordStatus, RecordTemplate
from thegrill.web import auth, service
from thegrill.web.seed import seed_templates


@pytest.fixture
def ctx(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'r.db'}")
    db.create_all()
    with db.session_scope() as s:
        rest, manager = auth.create_restaurant(s, "Casa Pepe", "ana@casa.com", "Ana", "clave-larga-1")
        luis = auth.join_restaurant(s, rest.join_code, "luis@casa.com", "Luis", "clave-larga-2")
        seed_templates(s, rest.id)
        yield s, rest, manager, luis, tmp_path


def tpl(session, rest, code):
    return session.query(RecordTemplate).filter_by(restaurant_id=rest.id, code=code).one()


def test_default_templates_cover_any_restaurant(ctx):
    s, rest, *_ = ctx
    codes = {t.code for t in s.query(RecordTemplate).filter_by(restaurant_id=rest.id)}
    assert codes == {"temp_refrigeracion", "temp_congelacion", "recepcion", "merma",
                     "produccion", "limpieza", "inventario"}
    assert seed_templates(s, rest.id) == []          # idempotente


def test_missing_required_field_rejects_whole_record(ctx):
    s, rest, _, luis, _ = ctx
    with pytest.raises(service.ValidationError) as e:
        service.submit_record(s, luis, tpl(s, rest, "merma"), {"cantidad": "3"})
    assert "producto" in e.value.errors and "motivo" in e.value.errors
    assert s.query(service.Record).count() == 0      # nada a medias


def test_optional_field_may_be_empty(ctx):
    s, rest, _, luis, _ = ctx
    r = service.submit_record(s, luis, tpl(s, rest, "limpieza"),
                              {"zona": "Cocina", "realizada": "1"})
    assert r.record.status == RecordStatus.OK


def test_text_in_a_number_field_is_rejected(ctx):
    s, rest, _, luis, _ = ctx
    with pytest.raises(service.ValidationError):
        service.submit_record(s, luis, tpl(s, rest, "merma"),
                              {"producto": "Pollo", "cantidad": "mucho", "motivo": "Caducado", "area": "Cocina caliente"})


def test_invalid_select_option_is_rejected(ctx):
    s, rest, _, luis, _ = ctx
    with pytest.raises(service.ValidationError):
        service.submit_record(s, luis, tpl(s, rest, "merma"),
                              {"producto": "Pollo", "cantidad": "1", "motivo": "Inventado", "area": "Cocina caliente"})


def test_temperature_out_of_range_is_saved_and_raises_critical_alert(ctx):
    s, rest, _, luis, _ = ctx
    result = service.submit_record(s, luis, tpl(s, rest, "temp_refrigeracion"),
                                   {"unidad": "Cámara refrigerada 1", "temperatura": "9.4"})
    assert result.record.status == RecordStatus.ALERT      # el dato real queda registrado
    assert len(result.alerts) == 1
    assert result.alerts[0].severity == AlertSeverity.CRITICAL
    assert "9,4°C" in result.alerts[0].message and "máximo 5°C" in result.alerts[0].message
    assert [v.out_of_range for v in result.record.values if v.field_key == "temperatura"] == [True]


def test_alert_message_reads_the_same_from_memory_and_from_db(ctx):
    """El límite se imprime igual esté el objeto recién creado o releído."""
    s, rest, _, luis, _ = ctx
    fresh = service.submit_record(s, luis, tpl(s, rest, "temp_refrigeracion"),
                                  {"unidad": "Vitrina", "temperatura": "9"})
    s.expire_all()
    reloaded = service.submit_record(s, luis, tpl(s, rest, "temp_refrigeracion"),
                                     {"unidad": "Vitrina", "temperatura": "9"})
    assert fresh.alerts[0].message == reloaded.alerts[0].message
    assert "máximo 5°C" in reloaded.alerts[0].message


def test_temperature_in_range_raises_nothing(ctx):
    s, rest, _, luis, _ = ctx
    result = service.submit_record(s, luis, tpl(s, rest, "temp_congelacion"),
                                   {"unidad": "Congelador 1", "temperatura": "-20"})
    assert result.record.status == RecordStatus.OK and result.alerts == []


def test_freezer_above_minus_18_breaks_the_cold_chain(ctx):
    s, rest, _, luis, _ = ctx
    result = service.submit_record(s, luis, tpl(s, rest, "temp_congelacion"),
                                   {"unidad": "Congelador 1", "temperatura": "-12"})
    assert result.record.status == RecordStatus.ALERT
    assert "máximo -18°C" in result.alerts[0].message


def test_a_freezer_reading_is_not_accepted_in_the_fridge_form(ctx):
    """Cada plantilla lista sus propios equipos: no se pueden mezclar."""
    s, rest, _, luis, _ = ctx
    with pytest.raises(service.ValidationError):
        service.submit_record(s, luis, tpl(s, rest, "temp_refrigeracion"),
                              {"unidad": "Congelador 1", "temperatura": "-20"})


def test_expiry_date_triggers_fefo_alert(ctx):
    s, rest, _, luis, _ = ctx
    today = date(2026, 9, 19)
    base = {"proveedor": "X", "producto": "Pollo", "cantidad": "10", "conforme": "1"}
    caducado = service.submit_record(s, luis, tpl(s, rest, "recepcion"),
                                     {**base, "caducidad": (today - timedelta(days=1)).isoformat()},
                                     business_date=today)
    pronto = service.submit_record(s, luis, tpl(s, rest, "recepcion"),
                                   {**base, "caducidad": (today + timedelta(days=2)).isoformat()},
                                   business_date=today)
    lejos = service.submit_record(s, luis, tpl(s, rest, "recepcion"),
                                  {**base, "caducidad": (today + timedelta(days=30)).isoformat()},
                                  business_date=today)
    assert "ya está caducado" in caducado.alerts[0].message
    assert "caduca en 2 día(s)" in pronto.alerts[0].message
    assert lejos.alerts == []


def test_bool_marked_no_raises_alert(ctx):
    s, rest, _, luis, _ = ctx
    result = service.submit_record(s, luis, tpl(s, rest, "limpieza"),
                                   {"zona": "Cocina", "realizada": ""})
    assert result.record.status == RecordStatus.ALERT


def test_records_are_append_only_corrections_chain(ctx):
    s, rest, _, luis, _ = ctx
    first = service.submit_record(s, luis, tpl(s, rest, "merma"),
                                  {"producto": "Pollo", "cantidad": "3", "motivo": "Caducado",
                                   "area": "Cocina caliente"})
    second = service.submit_record(s, luis, tpl(s, rest, "merma"),
                                   {"producto": "Pollo", "cantidad": "1.5", "motivo": "Caducado",
                                    "area": "Cocina caliente"}, corrects_id=first.record.id)
    s.refresh(first.record)
    assert first.record.status == RecordStatus.CORRECTED
    assert second.record.corrects_id == first.record.id    # el original no se borra


def test_server_sets_the_time(ctx):
    s, rest, _, luis, _ = ctx
    before = datetime.utcnow()
    r = service.submit_record(s, luis, tpl(s, rest, "limpieza"), {"zona": "Cocina", "realizada": "1"})
    assert before <= r.record.created_at <= datetime.utcnow()


def test_cannot_submit_to_another_restaurants_template(ctx):
    s, rest, _, luis, _ = ctx
    other, _ = auth.create_restaurant(s, "Otro", "eva@otro.com", "Eva", "clave-larga-9")
    seed_templates(s, other.id)
    foreign = s.query(RecordTemplate).filter_by(restaurant_id=other.id, code="limpieza").one()
    with pytest.raises(PermissionError):
        service.submit_record(s, luis, foreign, {"zona": "Cocina", "realizada": "1"})


def test_inactive_template_rejects_submissions(ctx):
    s, rest, _, luis, _ = ctx
    t = tpl(s, rest, "limpieza")
    t.active = False
    with pytest.raises(service.ValidationError):
        service.submit_record(s, luis, t, {"zona": "Cocina", "realizada": "1"})


# --------------------------------------------------------------- fotos
def test_photo_is_stored_with_checksum(ctx):
    s, rest, _, luis, tmp_path = ctx
    r = service.submit_record(s, luis, tpl(s, rest, "merma"),
                              {"producto": "Pollo", "cantidad": "2", "motivo": "Rotura", "area": "Almacén"})
    att = service.store_attachment(s, r.record, "foto.jpg", "image/jpeg", b"\xff\xd8binario",
                                   str(tmp_path / "uploads"))
    assert att.size_bytes == 9 and len(att.sha256) == 64
    with open(att.stored_path, "rb") as fh:
        assert fh.read() == b"\xff\xd8binario"


def test_executable_upload_is_rejected(ctx):
    s, rest, _, luis, tmp_path = ctx
    r = service.submit_record(s, luis, tpl(s, rest, "limpieza"), {"zona": "Cocina", "realizada": "1"})
    with pytest.raises(service.ValidationError):
        service.store_attachment(s, r.record, "malo.sh", "application/x-sh", b"rm -rf /",
                                 str(tmp_path / "uploads"))


def test_oversized_upload_is_rejected(ctx):
    s, rest, _, luis, tmp_path = ctx
    r = service.submit_record(s, luis, tpl(s, rest, "limpieza"), {"zona": "Cocina", "realizada": "1"})
    with pytest.raises(service.ValidationError):
        service.store_attachment(s, r.record, "grande.jpg", "image/jpeg",
                                 b"x" * (service.MAX_UPLOAD_BYTES + 1), str(tmp_path / "uploads"))


# --------------------------------------------------------- estadísticas
def test_dashboard_counts_compliance_and_alerts(ctx):
    s, rest, manager, luis, _ = ctx
    today = date.today()
    for t in ("temp_refrigeracion",):
        service.submit_record(s, luis, tpl(s, rest, t),
                              {"unidad": "Cámara refrigerada 1", "temperatura": "3"},
                              business_date=today)
    service.submit_record(s, luis, tpl(s, rest, "temp_refrigeracion"),
                          {"unidad": "Vitrina", "temperatura": "8"},
                          business_date=today)
    service.submit_record(s, manager, tpl(s, rest, "limpieza"),
                          {"zona": "Cocina", "realizada": "1"}, business_date=today)

    d = service.dashboard(s, rest.id, until=today, days=1)
    assert d.total_records == 3 and d.active_staff == 2
    temps = next(x for x in d.by_template if x.code == "temp_refrigeracion")
    assert temps.submitted == 2 and temps.expected == 2 and temps.compliance_pct == 100.0
    assert temps.alerts == 1
    inventario = next(x for x in d.by_template if x.code == "inventario")
    assert inventario.submitted == 0 and inventario.compliance_pct == 0.0
    assert d.open_alerts == 1 and d.critical_alerts == 1
    assert d.top_contributors[0] == ("Luis", 2)


def test_dashboard_only_sees_its_own_restaurant(ctx):
    s, rest, _, luis, _ = ctx
    other, eva = auth.create_restaurant(s, "Otro", "eva@otro.com", "Eva", "clave-larga-9")
    seed_templates(s, other.id)
    service.submit_record(s, luis, tpl(s, rest, "limpieza"), {"zona": "Cocina", "realizada": "1"})
    for _ in range(3):
        service.submit_record(s, eva, s.query(RecordTemplate).filter_by(
            restaurant_id=other.id, code="limpieza").one(), {"zona": "Sala", "realizada": "1"})
    assert service.dashboard(s, rest.id, days=1).total_records == 1
    assert service.dashboard(s, other.id, days=1).total_records == 3


def test_only_manager_closes_alerts_and_must_explain(ctx):
    s, rest, manager, luis, _ = ctx
    r = service.submit_record(s, luis, tpl(s, rest, "temp_refrigeracion"),
                              {"unidad": "Cámara refrigerada 1", "temperatura": "12"})
    alert_id = r.alerts[0].id
    with pytest.raises(PermissionError):
        service.acknowledge_alert(s, luis, alert_id, "lo he mirado")
    with pytest.raises(service.ValidationError):
        service.acknowledge_alert(s, manager, alert_id, "   ")
    closed = service.acknowledge_alert(s, manager, alert_id, "Producto trasladado y equipo reparado")
    assert closed.acknowledged_by == manager.id and closed.acknowledged_at is not None
    assert service.dashboard(s, rest.id, days=1).open_alerts == 0


def test_csv_export_is_auditable(ctx):
    s, rest, _, luis, _ = ctx
    today = date.today()
    service.submit_record(s, luis, tpl(s, rest, "temp_refrigeracion"),
                          {"unidad": "Vitrina", "temperatura": "8"},
                          business_date=today)
    csv_text = service.export_records_csv(s, rest.id, today, today)
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("fecha,hora_servidor,plantilla,estado,usuario")
    assert "Luis" in csv_text and "Temperatura de refrigeración" in csv_text
    assert any(",SI," in line for line in lines[1:])     # marca el valor fuera de rango
