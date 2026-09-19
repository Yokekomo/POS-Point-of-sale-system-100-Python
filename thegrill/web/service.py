"""Lógica de la plataforma: validar y guardar registros, evaluar límites,
generar alertas y calcular las estadísticas del panel del manager.

Reglas que no se negocian:
- Un campo obligatorio vacío rechaza el registro; no se guarda a medias.
- Un número fuera de límites NO se rechaza: se guarda y se marca alerta. El
  dato real de una cámara a 9 °C tiene que quedar registrado.
- La fecha y hora las pone el servidor.
- Los registros no se editan: una corrección es un registro nuevo que apunta
  al anterior.
"""
import hashlib
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from thegrill.models import (Alert, AlertSeverity, Attachment, FieldType, Record,
                             RecordStatus, RecordTemplate, RecordValue, Role,
                             TemplateField, User)

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                       "image/heic": ".heic", "application/pdf": ".pdf"}
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


class ValidationError(ValueError):
    """El formulario no se puede guardar. `errors` lleva el detalle por campo."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def fmt_number(n: float) -> str:
    """5.0 -> «5», 9.40 -> «9.4». El mensaje de alerta se lee igual venga de
    memoria o de la base de datos."""
    return f"{n:.10g}"


@dataclass
class ParsedValue:
    field_key: str
    text: str | None = None
    number: float | None = None
    day: date | None = None
    flag: bool | None = None
    out_of_range: bool = False
    alert_message: str | None = None


@dataclass
class SubmitResult:
    record: Record
    alerts: list[Alert] = field(default_factory=list)


# ------------------------------------------------------------- validación
def parse_field(fld: TemplateField, raw: str | None, today: date) -> ParsedValue:
    """Convierte el valor del formulario y evalúa sus límites."""
    raw = (raw or "").strip()
    pv = ParsedValue(fld.key)

    if fld.type == FieldType.BOOL:
        # Una casilla sin marcar no envía nada: eso es un "no", no un campo vacío.
        pv.flag = raw.lower() in {"1", "true", "on", "si", "sí", "yes"}
        if fld.required and not pv.flag:
            pv.out_of_range = True
            pv.alert_message = f"{fld.label}: marcado como NO"
        return pv

    if not raw:
        if fld.required:
            raise ValidationError({fld.key: f"«{fld.label}» es obligatorio"})
        return pv

    if fld.type == FieldType.NUMBER:
        try:
            pv.number = float(raw.replace(",", "."))
        except ValueError:
            raise ValidationError({fld.key: f"«{fld.label}» debe ser un número"}) from None
        if fld.min_value is not None and pv.number < fld.min_value:
            pv.out_of_range = True
            pv.alert_message = (f"{fld.label}: {fmt_number(pv.number)}{fld.unit or ''} por debajo "
                                f"del mínimo {fmt_number(fld.min_value)}{fld.unit or ''}")
        elif fld.max_value is not None and pv.number > fld.max_value:
            pv.out_of_range = True
            pv.alert_message = (f"{fld.label}: {fmt_number(pv.number)}{fld.unit or ''} por encima "
                                f"del máximo {fmt_number(fld.max_value)}{fld.unit or ''}")
    elif fld.type == FieldType.DATE:
        try:
            pv.day = date.fromisoformat(raw)
        except ValueError:
            raise ValidationError({fld.key: f"«{fld.label}» debe ser una fecha (AAAA-MM-DD)"}) from None
        if fld.expiry_alert_days is not None:
            days_left = (pv.day - today).days
            if days_left < 0:
                pv.out_of_range = True
                pv.alert_message = f"{fld.label}: {pv.day} ya está caducado"
            elif days_left <= fld.expiry_alert_days:
                pv.out_of_range = True
                pv.alert_message = f"{fld.label}: {pv.day}, caduca en {days_left} día(s)"
    elif fld.type == FieldType.SELECT:
        options = [o.strip() for o in (fld.options or "").split("|") if o.strip()]
        if options and raw not in options:
            raise ValidationError({fld.key: f"«{fld.label}»: opción no válida"})
        pv.text = raw
    else:
        pv.text = raw
    return pv


def submit_record(session: Session, user: User, template: RecordTemplate, data: dict[str, str],
                  business_date: date | None = None, shift: str | None = None,
                  note: str | None = None, corrects_id: int | None = None,
                  now: datetime | None = None) -> SubmitResult:
    """Guarda un registro validado. Cualquier empleado del restaurante puede hacerlo."""
    if template.restaurant_id != user.restaurant_id:
        raise PermissionError("La plantilla pertenece a otro restaurante")
    if not template.active:
        raise ValidationError({"_": "Esta plantilla está desactivada"})
    now = now or datetime.utcnow()
    business_date = business_date or now.date()

    errors: dict[str, str] = {}
    parsed: list[ParsedValue] = []
    for fld in template.fields:
        try:
            parsed.append(parse_field(fld, data.get(fld.key), business_date))
        except ValidationError as e:
            errors.update(e.errors)
    if errors:
        raise ValidationError(errors)

    record = Record(restaurant_id=user.restaurant_id, template_id=template.id,
                    business_date=business_date, shift=shift, note=note,
                    corrects_id=corrects_id, created_by=user.id, created_at=now,
                    status=RecordStatus.OK)
    for pv in parsed:
        record.values.append(RecordValue(field_key=pv.field_key, value_text=pv.text,
                                         value_number=pv.number, value_date=pv.day,
                                         value_bool=pv.flag, out_of_range=pv.out_of_range))
    session.add(record)

    if corrects_id:
        previous = session.get(Record, corrects_id)
        if previous is not None and previous.restaurant_id == user.restaurant_id:
            previous.status = RecordStatus.CORRECTED

    alerts = []
    for pv in parsed:
        if pv.out_of_range:
            record.status = RecordStatus.ALERT
            severity = AlertSeverity.CRITICAL if template.category == "haccp" else AlertSeverity.WARNING
            alerts.append(Alert(restaurant_id=user.restaurant_id, code=f"{template.code}.{pv.field_key}",
                                message=f"[{template.name}] {pv.alert_message}",
                                severity=severity, created_at=now))
    session.flush()
    for a in alerts:
        a.record_id = record.id
        session.add(a)
    session.flush()
    return SubmitResult(record, alerts)


# --------------------------------------------------------------- adjuntos
def store_attachment(session: Session, record: Record, filename: str, content_type: str,
                     payload: bytes, upload_dir: str) -> Attachment:
    """Guarda una foto del registro. Valida tipo y tamaño antes de escribir."""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise ValidationError({"foto": f"Tipo de archivo no permitido: {content_type}"})
    if not payload:
        raise ValidationError({"foto": "El archivo está vacío"})
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValidationError({"foto": f"El archivo supera {MAX_UPLOAD_BYTES // (1024*1024)} MB"})

    digest = hashlib.sha256(payload).hexdigest()
    ext = ALLOWED_IMAGE_TYPES[content_type]
    folder = os.path.join(upload_dir, str(record.restaurant_id), record.business_date.isoformat())
    os.makedirs(folder, exist_ok=True)
    stored = os.path.join(folder, f"{uuid.uuid4().hex}{ext}")
    with open(stored, "wb") as fh:
        fh.write(payload)

    att = Attachment(record_id=record.id, filename=os.path.basename(filename)[:256],
                     content_type=content_type, size_bytes=len(payload),
                     sha256=digest, stored_path=stored)
    session.add(att)
    session.flush()
    return att


# ----------------------------------------------------------- estadísticas
@dataclass
class TemplateStat:
    code: str
    name: str
    category: str
    submitted: int
    expected: int
    alerts: int

    @property
    def compliance_pct(self) -> float:
        if self.expected <= 0:
            return 100.0
        return round(min(self.submitted / self.expected, 1.0) * 100, 1)


@dataclass
class Dashboard:
    since: date
    until: date
    days: int
    total_records: int
    open_alerts: int
    critical_alerts: int
    active_staff: int
    compliance_pct: float
    by_template: list[TemplateStat]
    by_day: list[tuple[date, int]]
    top_contributors: list[tuple[str, int]]


def dashboard(session: Session, restaurant_id: int, until: date | None = None,
              days: int = 7) -> Dashboard:
    """Panel del manager: cumplimiento, alertas y actividad del periodo."""
    until = until or date.today()
    since = until - timedelta(days=days - 1)

    records = (session.query(Record)
               .filter(Record.restaurant_id == restaurant_id,
                       Record.business_date >= since, Record.business_date <= until)
               .all())
    templates = (session.query(RecordTemplate)
                 .filter_by(restaurant_id=restaurant_id, active=True).all())

    per_template = defaultdict(int)
    per_day = defaultdict(int)
    per_user = defaultdict(int)
    alerts_per_template = defaultdict(int)
    for r in records:
        per_template[r.template_id] += 1
        per_day[r.business_date] += 1
        per_user[r.created_by] += 1
        if r.status == RecordStatus.ALERT:
            alerts_per_template[r.template_id] += 1

    stats = []
    for t in sorted(templates, key=lambda x: (x.sort_order, x.name)):
        stats.append(TemplateStat(t.code, t.name, t.category, per_template.get(t.id, 0),
                                  t.expected_per_day * days, alerts_per_template.get(t.id, 0)))

    expected_total = sum(s.expected for s in stats)
    submitted_capped = sum(min(s.submitted, s.expected) for s in stats)
    compliance = round(submitted_capped / expected_total * 100, 1) if expected_total else 100.0

    open_alerts = (session.query(func.count(Alert.id))
                   .filter(Alert.restaurant_id == restaurant_id,
                           Alert.acknowledged_at.is_(None)).scalar() or 0)
    critical = (session.query(func.count(Alert.id))
                .filter(Alert.restaurant_id == restaurant_id,
                        Alert.acknowledged_at.is_(None),
                        Alert.severity == AlertSeverity.CRITICAL).scalar() or 0)

    names = {u.id: u.name for u in session.query(User).filter_by(restaurant_id=restaurant_id)}
    contributors = sorted(((names.get(uid, "?"), n) for uid, n in per_user.items()),
                          key=lambda x: -x[1])[:5]
    by_day = [(since + timedelta(days=i), per_day.get(since + timedelta(days=i), 0))
              for i in range(days)]

    return Dashboard(since=since, until=until, days=days, total_records=len(records),
                     open_alerts=open_alerts, critical_alerts=critical,
                     active_staff=len(per_user), compliance_pct=compliance,
                     by_template=stats, by_day=by_day, top_contributors=contributors)


def acknowledge_alert(session: Session, user: User, alert_id: int, resolution: str) -> Alert:
    """Solo un manager cierra una alerta, y deja escrito qué hizo."""
    alert = session.get(Alert, alert_id)
    if alert is None or alert.restaurant_id != user.restaurant_id:
        raise PermissionError("Alerta de otro restaurante")
    if user.role != Role.MANAGER:
        raise PermissionError("Solo un manager puede cerrar alertas")
    if not (resolution or "").strip():
        raise ValidationError({"resolution": "Escribe qué acción correctiva se tomó"})
    alert.acknowledged_by = user.id
    alert.acknowledged_at = datetime.utcnow()
    alert.resolution = resolution.strip()
    session.flush()
    return alert


def export_records_csv(session: Session, restaurant_id: int, since: date, until: date) -> str:
    """Export plano para auditoría: una línea por valor registrado."""
    import csv
    import io

    rows = (session.query(Record)
            .filter(Record.restaurant_id == restaurant_id,
                    Record.business_date >= since, Record.business_date <= until)
            .order_by(Record.business_date, Record.id).all())
    names = {u.id: u.name for u in session.query(User).filter_by(restaurant_id=restaurant_id)}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["fecha", "hora_servidor", "plantilla", "estado", "usuario", "campo", "valor",
                "fuera_de_rango", "turno", "nota", "fotos"])
    for r in rows:
        for v in r.values:
            w.writerow([r.business_date.isoformat(), r.created_at.isoformat(timespec="seconds"),
                        r.template.name, r.status.value, names.get(r.created_by, "?"),
                        v.field_key, v.display, "SI" if v.out_of_range else "",
                        r.shift or "", r.note or "", len(r.attachments)])
    return buf.getvalue()
