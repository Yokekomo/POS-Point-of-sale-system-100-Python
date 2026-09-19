"""Aplicación web. Dos puertas:

- **Empleado**: pantalla de captura. Lista de registros que puede rellenar,
  formulario con los campos que el manager definió y subida de fotos.
- **Manager**: panel con estadísticas, alertas, historial completo, gestión de
  plantillas, equipo y export CSV.

Todo va contra el restaurante del usuario: nadie ve datos de otro.
"""
import os
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile   # el que devuelve request.form(), no el de FastAPI

from thegrill import db
from thegrill.models import (Alert, Attachment, FieldType, Record, RecordTemplate,
                             Restaurant, Role, TemplateField, User)

from thegrill.web import auth, service
from thegrill.web.seed import seed_templates

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
UPLOAD_DIR = os.environ.get("GRILL_UPLOAD_DIR", "uploads")

templates = Jinja2Templates(directory=TEMPLATES_DIR)
app = FastAPI(title="Plataforma de gestión de cocina")


# --------------------------------------------------------------- utilidades
def get_db():
    with db.session_scope() as session:
        yield session


def current(request: Request, session: Session):
    token = request.cookies.get(auth.COOKIE_NAME)
    return auth.resolve_session(session, token)


def require_user(request: Request, session: Session = Depends(get_db)):
    found = current(request, session)
    if found is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return found


def require_manager_user(request: Request, session: Session = Depends(get_db)):
    user, auth_session = require_user(request, session)
    if user.role != Role.MANAGER:
        raise HTTPException(status_code=403, detail="Esta sección es solo para managers")
    return user, auth_session


def page(request: Request, name: str, user: User | None = None, auth_session=None, **ctx):
    base = {"user": user, "csrf": auth_session.csrf if auth_session else "",
            "today": date.today().isoformat()}
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def set_session_cookie(response: Response, token: str) -> Response:
    secure = os.environ.get("GRILL_INSECURE_COOKIE") != "1"
    response.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="lax",
                        secure=secure, max_age=auth.SESSION_DAYS * 86400, path="/")
    return response


def home_for(user: User) -> str:
    return "/manager" if user.role == Role.MANAGER else "/app"


@app.exception_handler(HTTPException)
async def redirect_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    return templates.TemplateResponse(request, "error.html",
                                      {"user": None, "csrf": "",
                                       "code": exc.status_code, "detail": exc.detail},
                                      status_code=exc.status_code)


# ------------------------------------------------------------------ acceso
@app.get("/", response_class=HTMLResponse)
def root(request: Request, session: Session = Depends(get_db)):
    found = current(request, session)
    return RedirectResponse(home_for(found[0]) if found else "/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, session: Session = Depends(get_db), error: str = ""):
    found = current(request, session)
    if found:
        return RedirectResponse(home_for(found[0]), status_code=303)
    return page(request, "login.html", error=error)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          session: Session = Depends(get_db)):
    try:
        user = auth.authenticate(session, email, password)
    except auth.AuthError as e:
        return page(request, "login.html", error=str(e))
    token, _ = auth.start_session(session, user)
    return set_session_cookie(RedirectResponse(home_for(user), status_code=303), token)


@app.post("/logout")
def logout(request: Request, session: Session = Depends(get_db)):
    auth.end_session(session, request.cookies.get(auth.COOKIE_NAME))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, error: str = ""):
    return page(request, "signup.html", error=error)


@app.post("/signup")
def signup(request: Request, restaurant: str = Form(...), name: str = Form(...),
           email: str = Form(...), password: str = Form(...),
           session: Session = Depends(get_db)):
    try:
        rest, manager = auth.create_restaurant(session, restaurant, email, name, password)
        seed_templates(session, rest.id)
    except (ValueError, auth.AuthError) as e:
        return page(request, "signup.html", error=str(e))
    token, _ = auth.start_session(session, manager)
    return set_session_cookie(RedirectResponse("/manager", status_code=303), token)


@app.get("/join", response_class=HTMLResponse)
def join_form(request: Request, error: str = "", code: str = ""):
    return page(request, "join.html", error=error, code=code)


@app.post("/join")
def join(request: Request, join_code: str = Form(...), name: str = Form(...),
         email: str = Form(...), password: str = Form(...),
         session: Session = Depends(get_db)):
    try:
        user = auth.join_restaurant(session, join_code, email, name, password)
    except (ValueError, auth.AuthError) as e:
        return page(request, "join.html", error=str(e), code=join_code)
    token, _ = auth.start_session(session, user)
    return set_session_cookie(RedirectResponse("/app", status_code=303), token)


# =========================================================== EMPLEADO
@app.get("/app", response_class=HTMLResponse)
def employee_home(request: Request, ctx=Depends(require_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    tpls = (session.query(RecordTemplate)
            .filter_by(restaurant_id=user.restaurant_id, active=True)
            .order_by(RecordTemplate.sort_order, RecordTemplate.name).all())
    mine = (session.query(Record)
            .filter_by(restaurant_id=user.restaurant_id, created_by=user.id)
            .order_by(Record.created_at.desc()).limit(10).all())
    restaurant = session.get(Restaurant, user.restaurant_id)
    return page(request, "employee_home.html", user, auth_session,
                templates_list=tpls, mine=mine, restaurant=restaurant)


@app.get("/app/registro/{code}", response_class=HTMLResponse)
def record_form(code: str, request: Request, ctx=Depends(require_user),
                session: Session = Depends(get_db), error: str = ""):
    user, auth_session = ctx
    tpl = (session.query(RecordTemplate)
           .filter_by(restaurant_id=user.restaurant_id, code=code, active=True).first())
    if tpl is None:
        raise HTTPException(status_code=404, detail="Ese registro no existe o está desactivado")
    return page(request, "record_form.html", user, auth_session, tpl=tpl,
                FieldType=FieldType, error=error, errors={}, sent=False)


@app.post("/app/registro/{code}", response_class=HTMLResponse)
async def record_submit(code: str, request: Request, ctx=Depends(require_user),
                        session: Session = Depends(get_db)):
    user, auth_session = ctx
    tpl = (session.query(RecordTemplate)
           .filter_by(restaurant_id=user.restaurant_id, code=code, active=True).first())
    if tpl is None:
        raise HTTPException(status_code=404, detail="Ese registro no existe o está desactivado")

    form = await request.form()
    try:
        auth.check_csrf(auth_session, form.get("csrf"))
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None

    data = {f.key: form.get(f.key) for f in tpl.fields}
    business_date = form.get("business_date") or None
    try:
        parsed_date = date.fromisoformat(business_date) if business_date else None
        result = service.submit_record(session, user, tpl, data, business_date=parsed_date,
                                       shift=form.get("shift") or None,
                                       note=form.get("note") or None)
    except service.ValidationError as e:
        return page(request, "record_form.html", user, auth_session, tpl=tpl,
                    FieldType=FieldType, error="Revisa los campos marcados",
                    errors=e.errors, sent=False, submitted=data)
    except ValueError as e:
        return page(request, "record_form.html", user, auth_session, tpl=tpl,
                    FieldType=FieldType, error=str(e), errors={}, sent=False, submitted=data)

    photos = form.getlist("photos")
    photo_errors = []
    for upload in photos:
        if not isinstance(upload, UploadFile) or not upload.filename:
            continue
        payload = await upload.read()
        try:
            service.store_attachment(session, result.record, upload.filename,
                                     upload.content_type or "application/octet-stream",
                                     payload, UPLOAD_DIR)
        except service.ValidationError as e:
            photo_errors.extend(e.errors.values())

    if tpl.requires_photo and not result.record.attachments:
        photo_errors.append("Esta plantilla pide foto: el registro se guardó sin ella")

    return page(request, "record_form.html", user, auth_session, tpl=tpl, FieldType=FieldType,
                error="", errors={}, sent=True, result=result, photo_errors=photo_errors)


@app.get("/app/mis-registros", response_class=HTMLResponse)
def my_records(request: Request, ctx=Depends(require_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    rows = (session.query(Record)
            .filter_by(restaurant_id=user.restaurant_id, created_by=user.id)
            .order_by(Record.created_at.desc()).limit(100).all())
    return page(request, "records_list.html", user, auth_session, rows=rows,
                title="Mis registros", authors={user.id: user.name}, manager_view=False)


# ============================================================ MANAGER
@app.get("/manager", response_class=HTMLResponse)
def manager_home(request: Request, ctx=Depends(require_manager_user),
                 session: Session = Depends(get_db), days: int = 7):
    user, auth_session = ctx
    days = max(1, min(days, 90))
    data = service.dashboard(session, user.restaurant_id, days=days)
    restaurant = session.get(Restaurant, user.restaurant_id)
    alerts = (session.query(Alert)
              .filter(Alert.restaurant_id == user.restaurant_id, Alert.acknowledged_at.is_(None))
              .order_by(Alert.created_at.desc()).limit(10).all())
    return page(request, "manager_home.html", user, auth_session, d=data,
                restaurant=restaurant, alerts=alerts, days=days)


@app.get("/manager/registros", response_class=HTMLResponse)
def manager_records(request: Request, ctx=Depends(require_manager_user),
                    session: Session = Depends(get_db), code: str = "", days: int = 14):
    user, auth_session = ctx
    since = date.today() - timedelta(days=max(1, min(days, 365)) - 1)
    q = (session.query(Record).filter(Record.restaurant_id == user.restaurant_id,
                                      Record.business_date >= since))
    if code:
        tpl = session.query(RecordTemplate).filter_by(restaurant_id=user.restaurant_id, code=code).first()
        if tpl:
            q = q.filter(Record.template_id == tpl.id)
    rows = q.order_by(Record.created_at.desc()).limit(300).all()
    authors = {u.id: u.name for u in session.query(User).filter_by(restaurant_id=user.restaurant_id)}
    tpls = session.query(RecordTemplate).filter_by(restaurant_id=user.restaurant_id).all()
    return page(request, "records_list.html", user, auth_session, rows=rows,
                title="Todos los registros", authors=authors, manager_view=True,
                templates_list=tpls, code=code, days=days)


@app.get("/manager/alertas", response_class=HTMLResponse)
def manager_alerts(request: Request, ctx=Depends(require_manager_user),
                   session: Session = Depends(get_db), show: str = "open"):
    user, auth_session = ctx
    q = session.query(Alert).filter(Alert.restaurant_id == user.restaurant_id)
    if show == "open":
        q = q.filter(Alert.acknowledged_at.is_(None))
    rows = q.order_by(Alert.created_at.desc()).limit(200).all()
    names = {u.id: u.name for u in session.query(User).filter_by(restaurant_id=user.restaurant_id)}
    return page(request, "alerts.html", user, auth_session, rows=rows, show=show, names=names)


@app.post("/manager/alertas/{alert_id}/cerrar")
def close_alert(alert_id: int, request: Request, resolution: str = Form(...), csrf: str = Form(""),
                ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    try:
        auth.check_csrf(auth_session, csrf)
        service.acknowledge_alert(session, user, alert_id, resolution)
    except (auth.PermissionDenied, PermissionError) as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    except service.ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/manager/alertas", status_code=303)


@app.get("/manager/plantillas", response_class=HTMLResponse)
def manager_templates(request: Request, ctx=Depends(require_manager_user),
                      session: Session = Depends(get_db)):
    user, auth_session = ctx
    rows = (session.query(RecordTemplate).filter_by(restaurant_id=user.restaurant_id)
            .order_by(RecordTemplate.sort_order, RecordTemplate.name).all())
    return page(request, "templates_admin.html", user, auth_session, rows=rows)


@app.post("/manager/plantillas/nueva")
def create_template(request: Request, name: str = Form(...), category: str = Form("other"),
                    expected_per_day: int = Form(1), requires_photo: str = Form(""),
                    fields_spec: str = Form(""), csrf: str = Form(""),
                    ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    """Alta rápida de plantilla. `fields_spec`: una línea por campo,
    `etiqueta | tipo | unidad | min | max`."""
    user, auth_session = ctx
    try:
        auth.check_csrf(auth_session, csrf)
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None

    import re as _re
    code = _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:48] or "registro"
    if session.query(RecordTemplate).filter_by(restaurant_id=user.restaurant_id, code=code).first():
        code = f"{code}_{int(datetime.utcnow().timestamp())}"
    tpl = RecordTemplate(restaurant_id=user.restaurant_id, code=code, name=name.strip(),
                         category=category, expected_per_day=max(0, expected_per_day),
                         requires_photo=bool(requires_photo), sort_order=900)
    for i, line in enumerate(fields_spec.splitlines()):
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        label = parts[0]
        ftype = (parts[1].upper() if len(parts) > 1 and parts[1] else "TEXT")
        ftype = ftype if ftype in FieldType.__members__ else "TEXT"
        key = _re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:48] or f"campo_{i}"
        tpl.fields.append(TemplateField(
            key=key, label=label, type=FieldType[ftype], sort_order=i * 10,
            unit=(parts[2] or None) if len(parts) > 2 else None,
            min_value=float(parts[3]) if len(parts) > 3 and parts[3] else None,
            max_value=float(parts[4]) if len(parts) > 4 and parts[4] else None))
    session.add(tpl)
    return RedirectResponse("/manager/plantillas", status_code=303)


@app.post("/manager/plantillas/{tpl_id}/activar")
def toggle_template(tpl_id: int, request: Request, csrf: str = Form(""),
                    ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    try:
        auth.check_csrf(auth_session, csrf)
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    tpl = session.get(RecordTemplate, tpl_id)
    if tpl is None or tpl.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    tpl.active = not tpl.active
    return RedirectResponse("/manager/plantillas", status_code=303)


@app.get("/manager/equipo", response_class=HTMLResponse)
def manager_team(request: Request, ctx=Depends(require_manager_user),
                 session: Session = Depends(get_db)):
    user, auth_session = ctx
    rows = (session.query(User).filter_by(restaurant_id=user.restaurant_id)
            .order_by(User.role, User.name).all())
    restaurant = session.get(Restaurant, user.restaurant_id)
    return page(request, "team.html", user, auth_session, rows=rows, restaurant=restaurant)


@app.post("/manager/equipo/{user_id}/rol")
def change_role(user_id: int, request: Request, role: str = Form(...), csrf: str = Form(""),
                ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    try:
        auth.check_csrf(auth_session, csrf)
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    target = session.get(User, user_id)
    if target is None or target.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="No puedes cambiar tu propio rol")
    if role in Role.__members__:
        target.role = Role[role]
    return RedirectResponse("/manager/equipo", status_code=303)


@app.post("/manager/equipo/{user_id}/activar")
def toggle_user(user_id: int, request: Request, csrf: str = Form(""),
                ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    try:
        auth.check_csrf(auth_session, csrf)
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    target = session.get(User, user_id)
    if target is None or target.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="No puedes desactivarte a ti mismo")
    target.active = not target.active
    return RedirectResponse("/manager/equipo", status_code=303)


@app.get("/manager/export.csv")
def export_csv(request: Request, ctx=Depends(require_manager_user),
               session: Session = Depends(get_db), days: int = 30):
    user, _ = ctx
    until = date.today()
    since = until - timedelta(days=max(1, min(days, 365)) - 1)
    body = service.export_records_csv(session, user.restaurant_id, since, until)
    return PlainTextResponse(body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="registros_{since}_{until}.csv"'})


@app.get("/foto/{attachment_id}")
def serve_photo(attachment_id: int, request: Request, ctx=Depends(require_user),
                session: Session = Depends(get_db)):
    """Una foto solo se sirve a gente del mismo restaurante."""
    user, _ = ctx
    att = session.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail="Foto no encontrada")
    record = session.get(Record, att.record_id)
    if record is None or record.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=403, detail="Esa foto pertenece a otro restaurante")
    if user.role != Role.MANAGER and record.created_by != user.id:
        raise HTTPException(status_code=403, detail="Solo puedes ver tus propias fotos")
    if not os.path.exists(att.stored_path):
        raise HTTPException(status_code=404, detail="El archivo ya no está en disco")
    with open(att.stored_path, "rb") as fh:
        return Response(fh.read(), media_type=att.content_type)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def create_app(database_url: str = "sqlite:///thegrill.db") -> FastAPI:
    db.init_engine(database_url)
    db.create_all()
    return app
