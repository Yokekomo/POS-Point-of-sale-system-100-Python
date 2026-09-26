"""[01377] Lógica de la plataforma: validar y guardar registros, evaluar límites,
generar alertas y calcular las estadísticas del panel del manager.

Reglas que no se negocian:
- Un campo obligatorio vacío rechaza el registro; no se guarda a medias.
- Un número fuera de límites NO se rechaza: se guarda y se marca alerta. El
  dato real de una cámara a 9 °C tiene que quedar registrado.
- La fecha y hora las pone el servidor.
- Los registros no se editan: una corrección es un registro nuevo que apunta
  al anterior.

Idiomas: los errores de validación salen en el idioma de quien rellena el
formulario, porque los lee él en ese momento. Las alertas y los avisos se
guardan en el idioma del restaurante, porque quedan almacenados y los lee todo
el equipo.
"""
import hashlib
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from thegrill.models import (Alert, AlertSeverity, Attachment, FieldType, Notification,
                             NotificationKind, Record, RecordStatus, RecordTemplate,
                             RecordValue, Restaurant, Role, TemplateField, User)
from thegrill.web import exacto, fotos, jornada
from thegrill.web.i18n import DEFAULT_LANG, t

# [01843] HEIC ya no entra, y no es una manía: es que **nadie sabe abrirlo**.
#
# Se aceptaba, y una foto de iPhone subida desde la galería se guardaba tal
# cual. Después: el servidor no puede abrirla —no hay ninguna librería de
# Python libre que lo haga sin arrastrar una licencia de pago y una GPL—, el
# ordenador del inspector tampoco, y la descarga de datos del cliente se lleva
# un fichero que no se ve. Y nadie se entera hasta el día que hace falta la
# etiqueta, dos años después. Un formato que no se puede abrir no es una
# prueba: es sitio ocupado.
#
# Que se caiga al subirla es mucho mejor que descubrirlo en una inspección: el
# aviso dice el tipo que llegó y se repite la foto, que se hace en diez
# segundos con la pieza todavía delante. Y los iPhone de ahora, al subir desde
# la cámara, ya mandan JPEG.
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                       "application/pdf": ".pdf"}
MAX_UPLOAD_BYTES = 12 * 1024 * 1024


class ValidationError(ValueError):
    """[01378] El formulario no se puede guardar. `errors` lleva el detalle por campo."""

    def __init__(self, errors: dict[str, str]):
        """[01392] Guarda los fallos por casilla, para pintarlos donde están.

        Un mensaje suelto obliga a buscar cuál de las doce casillas era.
        """
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def fmt_number(n: float) -> str:
    """[01379] 5.0 -> «5», 9.40 -> «9.4». El mensaje de alerta se lee igual venga de
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
    notifications: list[Notification] = field(default_factory=list)


# ----------------------------------------------------------- notificaciones
def notify(session: Session, restaurant_id: int, user_ids, title: str, body: str,
           severity: AlertSeverity = AlertSeverity.WARNING,
           kind: NotificationKind = NotificationKind.ALERT,
           alert_id: int | None = None, record_id: int | None = None,
           now: datetime | None = None) -> list[Notification]:
    """[01380] Deja un aviso dentro de la plataforma para cada persona indicada."""
    now = now or datetime.utcnow()
    out = []
    for uid in dict.fromkeys(user_ids):          # sin duplicados, manteniendo el orden
        n = Notification(restaurant_id=restaurant_id, user_id=uid, kind=kind, severity=severity,
                         title=title[:160], body=body, alert_id=alert_id, record_id=record_id,
                         created_at=now)
        session.add(n)
        out.append(n)
    session.flush()
    return out


def manager_ids(session: Session, restaurant_id: int) -> list[int]:
    """[01381] Quién lleva la casa: a estos les llegan los avisos."""
    return [u.id for u in session.query(User)
            .filter_by(restaurant_id=restaurant_id, role=Role.MANAGER, active=True)
            .order_by(User.id)]


def unread_count(session: Session, user_id: int) -> int:
    """[01382] Cuántos avisos tiene esa persona sin leer."""
    return (session.query(func.count(Notification.id))
            .filter(Notification.user_id == user_id, Notification.read_at.is_(None))
            .scalar() or 0)


def recent_notifications(session: Session, user_id: int, limit: int = 50) -> list[Notification]:
    """[01383] Los últimos avisos de esa persona, del más nuevo al más viejo."""
    return (session.query(Notification).filter(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit).all())


def mark_all_read(session: Session, user_id: int, now: datetime | None = None) -> int:
    """[01384] Marca como leídas las pendientes. `read_at` deja constancia de cuándo se vio."""
    now = now or datetime.utcnow()
    pending = (session.query(Notification)
               .filter(Notification.user_id == user_id, Notification.read_at.is_(None)).all())
    for n in pending:
        n.read_at = now
    session.flush()
    return len(pending)


# ------------------------------------------------------------- validación
def parse_field(fld: TemplateField, raw: str | None, today: date,
                lang: str = DEFAULT_LANG, alert_lang: str | None = None) -> ParsedValue:
    """[01385] Convierte el valor del formulario y evalúa sus límites.

    `lang` es el idioma de quien rellena (errores); `alert_lang` el del
    restaurante (mensajes de alerta que se guardan)."""
    alert_lang = alert_lang or lang
    raw = (raw or "").strip()
    pv = ParsedValue(fld.key)

    if fld.type == FieldType.BOOL:
        # [01394] Una casilla sin marcar no envía nada: eso es un "no", no un campo vacío.
        pv.flag = raw.lower() in {"1", "true", "on", "si", "sí", "yes", "ja", "oui", "نعم"}
        if fld.required and not pv.flag:
            pv.out_of_range = True
            pv.alert_message = t(alert_lang, "alert.marked_no", label=fld.label)
        return pv

    if not raw:
        if fld.required:
            raise ValidationError({fld.key: t(lang, "valid.required", label=fld.label)})
        return pv

    if fld.type == FieldType.NUMBER:
        try:
            pv.number = exacto.leer(raw)
        except ValueError:
            raise ValidationError({fld.key: t(lang, "valid.not_a_number", label=fld.label)}) from None
        if fld.min_value is not None and pv.number < fld.min_value:
            pv.out_of_range = True
            pv.alert_message = t(alert_lang, "alert.below_min", label=fld.label,
                                 value=fmt_number(pv.number), unit=fld.unit or "",
                                 limit=fmt_number(fld.min_value))
        elif fld.max_value is not None and pv.number > fld.max_value:
            pv.out_of_range = True
            pv.alert_message = t(alert_lang, "alert.above_max", label=fld.label,
                                 value=fmt_number(pv.number), unit=fld.unit or "",
                                 limit=fmt_number(fld.max_value))
    elif fld.type == FieldType.DATE:
        try:
            pv.day = date.fromisoformat(raw)
        except ValueError:
            raise ValidationError({fld.key: t(lang, "valid.not_a_date", label=fld.label)}) from None
        if fld.expiry_alert_days is not None:
            days_left = (pv.day - today).days
            if days_left < 0:
                pv.out_of_range = True
                pv.alert_message = t(alert_lang, "alert.expired", label=fld.label, date=pv.day)
            elif days_left <= fld.expiry_alert_days:
                pv.out_of_range = True
                pv.alert_message = t(alert_lang, "alert.expires_soon", label=fld.label,
                                     date=pv.day, n=days_left)
    elif fld.type == FieldType.SELECT:
        options = [o.strip() for o in (fld.options or "").split("|") if o.strip()]
        if options and raw not in options:
            raise ValidationError({fld.key: t(lang, "valid.bad_option", label=fld.label)})
        pv.text = raw
    else:
        pv.text = raw
    return pv


def restaurant_language(session: Session, restaurant_id: int) -> str:
    """[01386] En qué idioma habla la casa. Si no consta, el de serie."""
    restaurant = session.get(Restaurant, restaurant_id)
    return (restaurant.language if restaurant else None) or DEFAULT_LANG


def submit_record(session: Session, user: User, template: RecordTemplate, data: dict[str, str],
                  business_date: date | None = None, shift: str | None = None,
                  note: str | None = None, corrects_id: int | None = None,
                  now: datetime | None = None, lang: str | None = None) -> SubmitResult:
    """[01387] Guarda un registro validado. Cualquier empleado del restaurante puede hacerlo."""
    if template.restaurant_id != user.restaurant_id:
        raise PermissionError("La plantilla pertenece a otro restaurante")
    if not template.active:
        raise ValidationError({"_": t(lang or user.language or DEFAULT_LANG, "tpl.disabled")})
    now = now or datetime.utcnow()
    business_date = business_date or now.date()
    alert_lang = restaurant_language(session, user.restaurant_id)
    lang = lang or user.language or alert_lang

    errors: dict[str, str] = {}
    parsed: list[ParsedValue] = []
    for fld in template.fields:
        try:
            parsed.append(parse_field(fld, data.get(fld.key), business_date, lang, alert_lang))
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

    # [01395] El aviso va a buscar al manager: una alerta crítica no puede quedarse
    # esperando a que alguien entre a mirar el panel.
    notifications = []
    targets = [uid for uid in manager_ids(session, user.restaurant_id) if uid != user.id]
    for a in alerts:
        notifications.extend(notify(
            session, user.restaurant_id, targets,
            title=t(alert_lang, "notif.alert_title", template=template.name),
            body=t(alert_lang, "notif.alert_body", message=a.message, who=user.name),
            severity=a.severity, kind=NotificationKind.ALERT,
            alert_id=a.id, record_id=record.id, now=now))
    return SubmitResult(record, alerts, notifications)


# --------------------------------------------------------------- adjuntos
def store_attachment(session: Session, record: Record, filename: str, content_type: str,
                     payload: bytes, upload_dir: str, lang: str = DEFAULT_LANG) -> Attachment:
    """[01388] Guarda una foto del registro. Valida tipo y tamaño antes de escribir."""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise ValidationError({"foto": t(lang, "valid.photo_type", type=content_type)})
    if not payload:
        raise ValidationError({"foto": t(lang, "valid.photo_empty")})
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValidationError({"foto": t(lang, "valid.photo_too_big",
                                         n=MAX_UPLOAD_BYTES // (1024 * 1024))})

    # [01844] Se abre antes de guardarla: se le aplica el giro, se le quita el EXIF
    # —con el GPS de quien la hizo dentro— y se deja del tamaño que hace falta
    # para leerla. Lo que dice ser una imagen y no lo es, se cae aquí.
    try:
        payload, content_type = fotos.normaliza(payload, content_type)
    except fotos.FotoIlegible:
        raise ValidationError({"foto": t(lang, "valid.photo_type", type=content_type)}) from None

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
        """[01393] Qué parte de los partes que tocaban se ha entregado.

        Nunca pasa del cien: entregar tres veces el mismo parte no es cumplir el
        trescientos por cien. Y sin partes que tocaran, cien: no se le puede
        reprochar a nadie no haber hecho lo que no había que hacer.
        """
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
    """[01389] Panel del manager: cumplimiento, alertas y actividad del periodo."""
    until = until or jornada.hoy(session, restaurant_id)
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


def acknowledge_alert(session: Session, user: User, alert_id: int, resolution: str,
                      lang: str | None = None) -> Alert:
    """[01390] Solo un manager cierra una alerta, y deja escrito qué hizo."""
    alert = session.get(Alert, alert_id)
    if alert is None or alert.restaurant_id != user.restaurant_id:
        raise PermissionError("Alerta de otro restaurante")
    if user.role != Role.MANAGER:
        raise PermissionError("Solo un manager puede cerrar alertas")
    if not (resolution or "").strip():
        raise ValidationError({"resolution": t(lang or user.language or DEFAULT_LANG,
                                                "alerts.need_resolution")})
    alert.acknowledged_by = user.id
    alert.acknowledged_at = datetime.utcnow()
    alert.resolution = resolution.strip()
    session.flush()

    # [01396] Quien registró el problema se entera de qué se hizo con él.
    if alert.record_id:
        record = session.get(Record, alert.record_id)
        if record is not None and record.created_by != user.id:
            lang = restaurant_language(session, user.restaurant_id)
            notify(session, user.restaurant_id, [record.created_by],
                   title=t(lang, "notif.resolution_title"),
                   body=t(lang, "notif.resolution_body", message=alert.message,
                          who=user.name, resolution=alert.resolution),
                   severity=AlertSeverity.INFO, kind=NotificationKind.RESOLUTION,
                   alert_id=alert.id, record_id=alert.record_id)
    return alert


def export_records_csv(session: Session, restaurant_id: int, since: date, until: date) -> str:
    """[01391] Export plano para auditoría: una línea por valor registrado."""
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
