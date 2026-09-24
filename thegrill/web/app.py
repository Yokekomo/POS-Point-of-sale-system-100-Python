"""Aplicación web. Dos puertas:

- **Empleado**: pantalla de captura. Lista de registros que puede rellenar,
  formulario con los campos que el manager definió y subida de fotos.
- **Manager**: panel con estadísticas, alertas, historial completo, gestión de
  plantillas, equipo y export CSV.

Todo va contra el restaurante del usuario: nadie ve datos de otro.
"""
import os
import zoneinfo
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile   # el que devuelve request.form(), no el de FastAPI

from thegrill import db, version
from thegrill.models import (Alert, Attachment, ConsumptionMode, CountPeriod, CountStatus,
                             FieldType, Ingredient, IngredientItem,
                             Notification, PosMatch, PosProduct, Record, RecordTemplate, Recipe,
                             RecipeKind, RecipeLine, Restaurant, Role, Rotation,
                             Storage, TemplateField, Unit, User)

from thegrill.web import (auth, butchery, caducidad, cifras, costing, exacto, i18n,
                          inventory, jornada, money, pesos, rangos, seguridad,
                          service, sheets, tracing, waste)
from thegrill.web.seed import seed_templates

# Las zonas horarias que existen, para el desplegable de la configuración y
# para comprobar lo que llega.
ZONAS = sorted(zoneinfo.available_timezones())

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
UPLOAD_DIR = os.environ.get("GRILL_UPLOAD_DIR", "uploads")

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["ceil_pct"] = butchery.ceil_pct
# Los decimales, con el separador del idioma de la casa: «9,400 kg» en
# español y «9.400 kg» en inglés, en las doscientas y pico cifras que salen
# por pantalla, sin tocar ninguna plantilla.
cifras.enganchar(templates.env)
app = FastAPI(title="Plataforma de gestión de cocina")
# Las mismas cabeceras que la otra edición. Esta no tenía ninguna, y sus
# plantillas escribían `<script nonce="">`: parecía que había política.
seguridad.enganchar(app)


# --------------------------------------------------------------- utilidades
def get_db():
    with db.session_scope() as session:
        yield session


def current(request: Request, session: Session):
    token = request.cookies.get(auth.COOKIE_NAME)
    return auth.resolve_session(session, token)


def lang_for(request: Request, session: Session | None = None, user: User | None = None) -> str:
    """Idioma de esta petición: el de la persona, el elegido en el acceso,
    el del navegador, el del restaurante, y si no, español."""
    restaurant_lang = None
    if session is not None and user is not None:
        restaurant = session.get(Restaurant, user.restaurant_id)
        restaurant_lang = restaurant.language if restaurant else None
    return i18n.resolve(user_lang=user.language if user else None,
                        cookie=request.cookies.get(i18n.COOKIE_NAME),
                        accept_header=request.headers.get("accept-language"),
                        restaurant_lang=restaurant_lang)


def require_user(request: Request, session: Session = Depends(get_db)):
    found = current(request, session)
    if found is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return found


def require_manager_user(request: Request, session: Session = Depends(get_db)):
    user, auth_session = require_user(request, session)
    if user.role != Role.MANAGER:
        raise HTTPException(status_code=403,
                            detail=i18n.t(lang_for(request, session, user), "error.managers_only"))
    return user, auth_session


MANAGER_ONLY = {"inventory", "fix", "catalogue", "menu", "team"}


def kitchen_can(user: User | None):
    def can(capability: str) -> bool:
        if capability in MANAGER_ONLY:
            return bool(user) and user.role == Role.MANAGER
        return user is not None
    return can


def page(request: Request, name: str, user: User | None = None, auth_session=None,
         session: Session | None = None, **ctx):
    lang = ctx.pop("lang", None) or lang_for(request, session, user)
    base = {"user": user, "csrf": auth_session.csrf if auth_session else "",
            # El día de trabajo de la casa, no el del servidor.
            "today": jornada.hoy(session, user.restaurant_id if user else None).isoformat(),
            # El número de esta respuesta, que es lo que marca nuestros
            # guiones. Sin esto las plantillas escribían `nonce=""` y con la
            # política puesta el navegador no ejecutaría ni uno.
            "nonce": getattr(request.state, "nonce", ""),
            # La plataforma de cocina no tiene niveles de carne: las plantillas
            # que comparte con esa edición preguntan, y aquí se responde con lo
            # que la cocina ya hacía: el manager manda, el dinero lo ve todo el
            # equipo y los datos del día los mete cualquiera.
            "can": kitchen_can(user),
            "unread": service.unread_count(session, user.id) if (user and session) else 0,
            "t": i18n.translator(lang), "lang": lang, "dir": i18n.direction(lang),
            "languages": i18n.LANGUAGES,
            # El símbolo de la moneda de la casa: un número de dinero sin él no
            # dice si son euros o dólares.
            "moneda": money.simbolo(_moneda_de(session, user))}
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def _moneda_de(session, user) -> str | None:
    if not (session and user):
        return None
    restaurant = session.get(Restaurant, user.restaurant_id)
    return restaurant.currency if restaurant else None


def set_session_cookie(response: Response, token: str) -> Response:
    secure = os.environ.get("GRILL_INSECURE_COOKIE") != "1"
    response.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="lax",
                        secure=secure, max_age=auth.SESSION_DAYS * 86400, path="/")
    return response


def _guard(request, session, user, auth_session, csrf: str) -> None:
    try:
        auth.check_csrf(auth_session, csrf, lang_for(request, session, user))
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None


def _own(session: Session, user: User, model, obj_id: int, request: Request):
    """Trae una fila comprobando que es de este restaurante."""
    row = session.get(model, obj_id)
    if row is None or row.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=404,
                            detail=i18n.t(lang_for(request, session, user), "error.other_restaurant"))
    return row


def home_for(user: User) -> str:
    return "/manager" if user.role == Role.MANAGER else "/app"


# Lo que se dice cuando el error no trae texto: «Error 400» y una pantalla en
# blanco no le dicen nada a nadie.
SIN_TEXTO = {400: "error.bad_request", 403: "error.forbidden",
             404: "error.not_found", 409: "error.conflict",
             413: "error.too_big", 429: "error.too_many"}


@app.exception_handler(HTTPException)
async def redirect_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    lang = i18n.resolve(cookie=request.cookies.get(i18n.COOKIE_NAME),
                        accept_header=request.headers.get("accept-language"))
    detalle = exc.detail or ""
    if not str(detalle).strip():
        detalle = i18n.t(lang, SIN_TEXTO.get(exc.status_code, "error.other"))
    volver = request.headers.get("referer") or ""
    if not volver.startswith(str(request.base_url).rstrip("/")):
        volver = "/"
    return templates.TemplateResponse(request, "error.html",
                                      {"user": None, "csrf": "", "unread": 0,
                                       "t": i18n.translator(lang), "lang": lang,
                                       "dir": i18n.direction(lang), "languages": i18n.LANGUAGES,
                                       "code": exc.status_code, "detail": detalle,
                                       "volver": volver},
                                      status_code=exc.status_code)


def set_lang_cookie(response: Response, lang: str) -> Response:
    """La elección de idioma en la pantalla de acceso sobrevive al cierre de sesión."""
    response.set_cookie(i18n.COOKIE_NAME, lang, httponly=False, samesite="lax",
                        max_age=365 * 86400, path="/")
    return response


@app.get("/idioma/{lang}")
def choose_language(lang: str, request: Request):
    """Selector de idioma de la pantalla de acceso."""
    if not i18n.is_supported(lang):
        raise HTTPException(status_code=404, detail="Idioma no disponible")
    destination = request.query_params.get("next", "/login")
    if not destination.startswith("/") or destination.startswith("//"):
        destination = "/login"
    return set_lang_cookie(RedirectResponse(destination, status_code=303), lang)


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
    lang = lang_for(request, session)
    try:
        user = auth.authenticate(session, email, password, lang=lang)
    except auth.AuthError as e:
        return page(request, "login.html", error=str(e), lang=lang)
    token, _ = auth.start_session(session, user)
    response = set_session_cookie(RedirectResponse(home_for(user), status_code=303), token)
    return set_lang_cookie(response, user.language or lang)


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
           email: str = Form(...), password: str = Form(...), language: str = Form(""),
           session: Session = Depends(get_db)):
    lang = language if i18n.is_supported(language) else lang_for(request, session)
    try:
        rest, manager = auth.create_restaurant(session, restaurant, email, name, password,
                                               language=lang)
        seed_templates(session, rest.id, lang)
    except (ValueError, auth.AuthError) as e:
        return page(request, "signup.html", error=str(e), lang=lang)
    token, _ = auth.start_session(session, manager)
    response = set_session_cookie(RedirectResponse("/manager", status_code=303), token)
    return set_lang_cookie(response, lang)


@app.get("/join", response_class=HTMLResponse)
def join_form(request: Request, error: str = "", code: str = ""):
    return page(request, "join.html", error=error, code=code)


@app.post("/join")
def join(request: Request, join_code: str = Form(...), name: str = Form(...),
         email: str = Form(...), password: str = Form(...), language: str = Form(""),
         session: Session = Depends(get_db)):
    lang = language if i18n.is_supported(language) else lang_for(request, session)
    try:
        user = auth.join_restaurant(session, join_code, email, name, password, language=lang)
    except (ValueError, auth.AuthError) as e:
        return page(request, "join.html", error=str(e), code=join_code, lang=lang)
    token, _ = auth.start_session(session, user)
    response = set_session_cookie(RedirectResponse("/app", status_code=303), token)
    return set_lang_cookie(response, lang)


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
    return page(request, "employee_home.html", user, auth_session, session,
                templates_list=tpls, mine=mine, restaurant=restaurant)


@app.get("/app/registro/{code}", response_class=HTMLResponse)
def record_form(code: str, request: Request, ctx=Depends(require_user),
                session: Session = Depends(get_db), error: str = ""):
    user, auth_session = ctx
    tpl = (session.query(RecordTemplate)
           .filter_by(restaurant_id=user.restaurant_id, code=code, active=True).first())
    if tpl is None:
        raise HTTPException(status_code=404, detail=i18n.t(lang_for(request, session, user), "error.template_not_found"))
    return page(request, "record_form.html", user, auth_session, session, tpl=tpl,
                FieldType=FieldType, error=error, errors={}, sent=False)


@app.post("/app/registro/{code}", response_class=HTMLResponse)
async def record_submit(code: str, request: Request, ctx=Depends(require_user),
                        session: Session = Depends(get_db)):
    user, auth_session = ctx
    tpl = (session.query(RecordTemplate)
           .filter_by(restaurant_id=user.restaurant_id, code=code, active=True).first())
    if tpl is None:
        raise HTTPException(status_code=404, detail=i18n.t(lang_for(request, session, user), "error.template_not_found"))

    form = await request.form()
    try:
        auth.check_csrf(auth_session, form.get("csrf"), lang_for(request, session, user))
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None

    lang = lang_for(request, session, user)
    data = {f.key: form.get(f.key) for f in tpl.fields}
    business_date = form.get("business_date") or None
    try:
        parsed_date = date.fromisoformat(business_date) if business_date else None
        result = service.submit_record(session, user, tpl, data, business_date=parsed_date,
                                       shift=form.get("shift") or None,
                                       note=form.get("note") or None, lang=lang)
    except service.ValidationError as e:
        return page(request, "record_form.html", user, auth_session, session, tpl=tpl,
                    FieldType=FieldType, error=i18n.t(lang, "form.check_fields"),
                    errors=e.errors, sent=False, submitted=data)
    except ValueError as e:
        return page(request, "record_form.html", user, auth_session, session, tpl=tpl,
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
                                     payload, UPLOAD_DIR, lang)
        except service.ValidationError as e:
            photo_errors.extend(e.errors.values())

    if tpl.requires_photo and not result.record.attachments:
        photo_errors.append(i18n.t(lang, "form.photo_missing"))

    return page(request, "record_form.html", user, auth_session, session, tpl=tpl, FieldType=FieldType,
                error="", errors={}, sent=True, result=result, photo_errors=photo_errors)


@app.get("/app/mis-registros", response_class=HTMLResponse)
def my_records(request: Request, ctx=Depends(require_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    rows = (session.query(Record)
            .filter_by(restaurant_id=user.restaurant_id, created_by=user.id)
            .order_by(Record.created_at.desc()).limit(100).all())
    return page(request, "records_list.html", user, auth_session, session, rows=rows,
                title=i18n.t(lang_for(request, session, user), "records.mine"),
                authors={user.id: user.name}, manager_view=False)


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
    return page(request, "manager_home.html", user, auth_session, session, d=data,
                restaurant=restaurant, alerts=alerts, days=days)


@app.get("/manager/registros", response_class=HTMLResponse)
def manager_records(request: Request, ctx=Depends(require_manager_user),
                    session: Session = Depends(get_db), code: str = "", days: int = 14):
    user, auth_session = ctx
    since = (jornada.del_usuario(session, user)
             - timedelta(days=max(1, min(days, 365)) - 1))
    q = (session.query(Record).filter(Record.restaurant_id == user.restaurant_id,
                                      Record.business_date >= since))
    if code:
        tpl = session.query(RecordTemplate).filter_by(restaurant_id=user.restaurant_id, code=code).first()
        if tpl:
            q = q.filter(Record.template_id == tpl.id)
    rows = q.order_by(Record.created_at.desc()).limit(300).all()
    authors = {u.id: u.name for u in session.query(User).filter_by(restaurant_id=user.restaurant_id)}
    tpls = session.query(RecordTemplate).filter_by(restaurant_id=user.restaurant_id).all()
    return page(request, "records_list.html", user, auth_session, session, rows=rows,
                title=i18n.t(lang_for(request, session, user), "records.all"),
                authors=authors, manager_view=True,
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
    return page(request, "alerts.html", user, auth_session, session, rows=rows, show=show, names=names)


@app.post("/manager/alertas/{alert_id}/cerrar")
def close_alert(alert_id: int, request: Request, resolution: str = Form(...), csrf: str = Form(""),
                ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    try:
        auth.check_csrf(auth_session, csrf)
        service.acknowledge_alert(session, user, alert_id, resolution,
                                  lang_for(request, session, user))
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
    return page(request, "templates_admin.html", user, auth_session, session, rows=rows)


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
        raise HTTPException(status_code=404, detail=i18n.t(lang_for(request, session, user), "error.tpl_not_found"))
    tpl.active = not tpl.active
    return RedirectResponse("/manager/plantillas", status_code=303)


@app.get("/manager/equipo", response_class=HTMLResponse)
def manager_team(request: Request, ctx=Depends(require_manager_user),
                 session: Session = Depends(get_db), error: str = "", done: str = ""):
    user, auth_session = ctx
    rows = (session.query(User).filter_by(restaurant_id=user.restaurant_id)
            .order_by(User.role, User.name).all())
    restaurant = session.get(Restaurant, user.restaurant_id)
    return page(request, "team.html", user, auth_session, session, rows=rows,
                restaurant=restaurant, error=error, done=done)


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
        raise HTTPException(status_code=404, detail=i18n.t(lang_for(request, session, user), "error.user_not_found"))
    if target.id == user.id:
        raise HTTPException(status_code=400, detail=i18n.t(lang_for(request, session, user), "error.no_self_role"))
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
        raise HTTPException(status_code=404, detail=i18n.t(lang_for(request, session, user), "error.user_not_found"))
    if target.id == user.id:
        raise HTTPException(status_code=400, detail=i18n.t(lang_for(request, session, user), "error.no_self_disable"))
    target.active = not target.active
    return RedirectResponse("/manager/equipo", status_code=303)


@app.get("/manager/export.csv")
def export_csv(request: Request, ctx=Depends(require_manager_user),
               session: Session = Depends(get_db), days: int = 30):
    user, _ = ctx
    until = jornada.del_usuario(session, user)
    since = until - timedelta(days=max(1, min(days, 365)) - 1)
    body = service.export_records_csv(session, user.restaurant_id, since, until)
    return PlainTextResponse(body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="registros_{since}_{until}.csv"'})


# ====================================================== INGREDIENTES
@app.get("/ingredientes", response_class=HTMLResponse)
def ingredients_page(request: Request, ctx=Depends(require_user),
                     session: Session = Depends(get_db)):
    """Ingredientes madre con sus marcas, su stock y su precio real."""
    user, auth_session = ctx
    rows = (session.query(Ingredient).filter_by(restaurant_id=user.restaurant_id)
            .order_by(Ingredient.name).all())
    return page(request, "ingredients.html", user, auth_session, session, rows=rows,
                stock=costing.stock_on_hand(session, user.restaurant_id),
                costs=costing.unit_costs(session, user.restaurant_id),
                units=list(Unit), rotations=list(Rotation), modes=list(ConsumptionMode))


@app.post("/ingredientes/nuevo")
def create_ingredient(request: Request, name: str = Form(...), unit: str = Form("KG"),
                      rotation: str = Form("FEFO"), consumption: str = Form("RECIPE"),
                      category: str = Form(""), csrf: str = Form(""),
                      ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    session.add(Ingredient(restaurant_id=user.restaurant_id, name=name.strip(),
                           unit=Unit[unit] if unit in Unit.__members__ else Unit.KG,
                           rotation=Rotation[rotation] if rotation in Rotation.__members__
                           else Rotation.FEFO,
                           consumption=ConsumptionMode[consumption]
                           if consumption in ConsumptionMode.__members__ else ConsumptionMode.RECIPE,
                           category=category.strip() or None))
    return RedirectResponse("/ingredientes", status_code=303)


@app.get("/ingredientes/{ingredient_id}", response_class=HTMLResponse)
def ingredient_detail(ingredient_id: int, request: Request, ctx=Depends(require_user),
                      session: Session = Depends(get_db)):
    user, auth_session = ctx
    ing = _own(session, user, Ingredient, ingredient_id, request)
    return page(request, "ingredient_detail.html", user, auth_session, session, ing=ing,
                se_pesa=pesos.se_pesa(ing),
                stock=costing.stock_on_hand(session, user.restaurant_id),
                costs=costing.unit_costs(session, user.restaurant_id),
                order=costing.rotation_order(session, user.restaurant_id, ing))


@app.post("/ingredientes/{ingredient_id}/gramos")
def set_grams_per_unit(ingredient_id: int, request: Request,
                       grams_per_unit: str = Form(""), csrf: str = Form(""),
                       ctx=Depends(require_manager_user),
                       session: Session = Depends(get_db)):
    """Lo que pesa una unidad de este ingrediente, que lo sabe la casa.

    Es el número que permite escribirlo todo en gramos sin mentir: 55 por
    huevo, 916 por litro de aceite de oliva. Vaciarlo lo deja como estaba y
    cada cosa vuelve a escribirse en su unidad; no se borra nada de lo que ya
    hay guardado, porque el stock siempre vivió en la unidad del ingrediente
    y esto solo cambia cómo se teclea.
    """
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    ing = _own(session, user, Ingredient, ingredient_id, request)
    escrito = (grams_per_unit or "").strip()
    try:
        cuanto = exacto.leer(escrito, decimales=0) if escrito else None
    except ValueError:
        cuanto = None
    # En kilos no se pregunta y tampoco se acepta: un kilo pesa mil gramos.
    # La pantalla ya no lo enseña, pero una pantalla no es la única puerta.
    if ing.unit == Unit.KG:
        cuanto = None
    ing.grams_per_unit = cuanto if cuanto and cuanto > 0 else None
    return RedirectResponse(f"/ingredientes/{ingredient_id}", status_code=303)


@app.post("/ingredientes/{ingredient_id}/articulo")
def add_item(ingredient_id: int, request: Request, name: str = Form(...), brand: str = Form(""),
             supplier: str = Form(""), csrf: str = Form(""), ctx=Depends(require_manager_user),
             session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    ing = _own(session, user, Ingredient, ingredient_id, request)
    session.add(IngredientItem(restaurant_id=user.restaurant_id, ingredient_id=ing.id,
                               name=name.strip(), brand=brand.strip() or None,
                               supplier=supplier.strip() or None))
    return RedirectResponse(f"/ingredientes/{ingredient_id}", status_code=303)


@app.post("/ingredientes/{ingredient_id}/entrada")
def add_lot(ingredient_id: int, request: Request, item_id: int = Form(...),
            qty_g: str = Form(""), qty: str = Form(""),
            unit_cost: str = Form(...), expiry: str = Form(...),
            lot_code: str = Form(""), csrf: str = Form(""), ctx=Depends(require_user),
            session: Session = Depends(get_db)):
    """Registrar una entrada lo puede hacer cualquiera: se hace en el muelle."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    item = _own(session, user, IngredientItem, item_id, request)
    try:
        # `qty_g` son gramos y solo lo mandan los ingredientes que se pesan;
        # `qty` es la unidad del ingrediente —litros, unidades— y también el
        # nombre de antes, de cuando los kilos se escribían en kilos. Los
        # gramos se pasan a la unidad en la que vive el stock: 110 g de huevo
        # son dos huevos si uno pesa 55.
        costing.receive(session, user, item,
                        qty=(pesos.en_su_unidad(pesos.leer(qty_g), item.ingredient)
                             if (qty_g or "").strip()
                             else exacto.leer(qty) or 0.0) or 0.0,
                        unit_cost=exacto.leer(unit_cost, decimales=exacto.CENTIMOS_DECIMALES) or 0.0,
                        expiry=date.fromisoformat(expiry), lot_code=lot_code.strip() or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse(f"/ingredientes/{ingredient_id}", status_code=303)


# ============================================================ CARNE
@app.get("/carne", response_class=HTMLResponse)
def meat_page(request: Request, ctx=Depends(require_user),
              session: Session = Depends(get_db), closed: str = ""):
    """Cuánta carne queda: cortes en cámara y primales sin despiezar."""
    user, auth_session = ctx
    return page(request, "meat.html", user, auth_session, session, closed=closed,
                status=butchery.status(session, user.restaurant_id))


@app.post("/carne/cierre")
def close_meat_day(request: Request, csrf: str = Form(""), ctx=Depends(require_user),
                   session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    result = butchery.close_day(session, user)
    return RedirectResponse(f"/carne?closed={len(result.alerts)}", status_code=303)



# ============================================================== MERMA
def _waste_page(request, user, auth_session, session, *, result=None, error="", serial=""):
    """La pantalla de merma: todo lo tirado, sea de cámara o de una limpieza."""
    lines = waste.everything(session, user.restaurant_id)
    return page(request, "waste.html", user, auth_session, session,
                result=result, error=error, serial=serial, lines=lines,
                totals=waste.totals(lines),
                ingredients=(session.query(Ingredient)
                             .filter_by(restaurant_id=user.restaurant_id, active=True)
                             .order_by(Ingredient.name).all()))


@app.get("/merma", response_class=HTMLResponse)
def waste_page(request: Request, ctx=Depends(require_user),
               session: Session = Depends(get_db), serial: str = ""):
    user, auth_session = ctx
    return _waste_page(request, user, auth_session, session, serial=serial)


@app.post("/merma", response_class=HTMLResponse)
def record_waste(request: Request, g: str = Form(""), kg: str = Form(""),
                 serial: str = Form(""),
                 ingredient_id: str = Form(""), pieces: str = Form(""), reason: str = Form(""),
                 csrf: str = Form(""), ctx=Depends(require_user),
                 session: Session = Depends(get_db)):
    """Lo tirado se apunta con su lote, sus kilos y sus piezas, y sube el coste del resto."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        result = waste.record(
            session, user, kg=pesos.de_dos(g, kg, 0.0) or 0.0,
            serial=serial.strip() or None,
            ingredient_id=int(ingredient_id) if ingredient_id.strip() else None,
            pieces=int(pieces) if pieces.strip() else None,
            reason=reason.strip() or None,
            lang=lang_for(request, session, user))
    except (waste.WasteError, ValueError) as e:
        return _waste_page(request, user, auth_session, session, error=str(e),
                           serial=serial.strip())
    return _waste_page(request, user, auth_session, session, result=result)

# ==================================================== TRAZABILIDAD
@app.get("/trazabilidad", response_class=HTMLResponse)
def tracing_page(request: Request, ctx=Depends(require_user),
                 session: Session = Depends(get_db), serial: str = ""):
    """La historia de una pieza, de la recepción al plato."""
    user, auth_session = ctx
    history = error = None
    matches = []
    if serial.strip():
        try:
            history = tracing.history(session, user.restaurant_id, serial)
        except tracing.NotFound:
            matches = tracing.search(session, user.restaurant_id, serial)
            if not matches:
                error = i18n.t(lang_for(request, session, user), "trace.not_found")
    return page(request, "tracing.html", user, auth_session, session,
                serial=serial, history=history, matches=matches, error=error)


# ====================================================== INVENTARIO
@app.get("/inventario", response_class=HTMLResponse)
def inventory_page(request: Request, ctx=Depends(require_user),
                   session: Session = Depends(get_db), done: str = ""):
    """Contar la carne pieza a pieza y cuadrar."""
    user, auth_session = ctx
    from thegrill.models import MeatCount
    open_count = (session.query(MeatCount)
                  .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN).first())
    return page(request, "inventory.html", user, auth_session, session, recuperada=bool(done),
                count=open_count, last=inventory.last_closed(session, user.restaurant_id),
                counters={u.id: u.name for u in
                          session.query(User).filter_by(restaurant_id=user.restaurant_id)},
                month=inventory.monthly_status(session, user.restaurant_id),
                periods=list(CountPeriod),
                items=(session.query(IngredientItem)
                       .filter_by(restaurant_id=user.restaurant_id, active=True)
                       .order_by(IngredientItem.name).all()))


@app.post("/inventario/recuperar")
def recover_piece(request: Request, serial: str = Form(...), g: str = Form(""),
                  kg: str = Form(""),
                  note: str = Form(""), csrf: str = Form(""),
                  ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    """La pieza ha aparecido: vuelve al stock, con quién y por qué."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        inventory.recover(session, user, serial.strip(),
                          kg=pesos.de_dos(g, kg),
                          note=note.strip() or None,
                          lang=lang_for(request, session, user))
    except (inventory.InventoryError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/inventario?done=1", status_code=303)


@app.post("/inventario/alta")
def adopt_piece(request: Request, serial: str = Form(...), item_id: int = Form(...),
                g: str = Form(""), kg: str = Form(""),
                unit_cost: str = Form(...), expiry: str = Form(...),
                note: str = Form(""), csrf: str = Form(""),
                ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    """Estaba en cámara y el sistema no la tenía."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        # `g` son gramos y lo manda lo que se pesa; `kg` es la unidad del
        # artículo cuando no se pesa, y el nombre de antes.
        inventory.adopt(session, user, serial.strip(), item_id,
                        kg=pesos.de_dos(g, kg, 0.0) or 0.0,
                        unit_cost=exacto.leer(unit_cost, decimales=exacto.CENTIMOS_DECIMALES) or 0.0,
                        expiry=date.fromisoformat(expiry), note=note.strip() or None,
                        lang=lang_for(request, session, user))
    except (inventory.InventoryError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/inventario?done=1", status_code=303)


@app.post("/inventario/cancelar")
def cancel_inventory(request: Request, reason: str = Form(""), csrf: str = Form(""),
                     ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    from thegrill.models import MeatCount
    count = (session.query(MeatCount)
             .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN).first())
    if count is None:
        raise HTTPException(status_code=404, detail="")
    try:
        inventory.cancel_count(session, user, count, reason)
    except inventory.InventoryError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/abrir")
def open_inventory(request: Request, period: str = Form("MONTHLY"), csrf: str = Form(""),
                   ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        inventory.open_count(session, user,
                             CountPeriod[period] if period in CountPeriod.__members__
                             else CountPeriod.MONTHLY)
    except inventory.InventoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/contar")
async def record_count(request: Request, ctx=Depends(require_user),
                       session: Session = Depends(get_db)):
    """Apunta lo pesado. Contar lo puede hacer cualquiera: se hace en la cámara."""
    user, auth_session = ctx
    from thegrill.models import MeatCount
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    count = (session.query(MeatCount)
             .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN).first())
    if count is None:
        raise HTTPException(status_code=404, detail="")
    for key, value in form.multi_items():
        # `g:` es la casilla de ahora; `kg:` la de antes, que solo puede venir
        # de la cola de un teléfono con la pantalla vieja abierta.
        en_gramos = key.startswith("g:")
        if not (en_gramos or key.startswith("kg:")) or not str(value).strip():
            continue
        try:
            kg = pesos.leer(value) if en_gramos else exacto.leer(value)
        except ValueError:
            continue
        try:
            inventory.record(session, user, count, key.split(":", 1)[1], kg)
        except inventory.InventoryError:
            continue
    extra = (form.get("extra_serial") or "").strip()
    if extra and (str(form.get("extra_g") or "").strip()
                  or str(form.get("extra_kg") or "").strip()):
        try:
            inventory.record(session, user, count, extra,
                             pesos.del_formulario(form, "extra_g", "extra_kg",
                                                  default=0.0) or 0.0)
        except (ValueError, inventory.InventoryError):
            pass
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/cerrar")
def close_inventory(request: Request, csrf: str = Form(""),
                    ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    from thegrill.models import MeatCount
    count = (session.query(MeatCount)
             .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN).first())
    if count is None:
        raise HTTPException(status_code=404, detail="")
    try:
        inventory.close_count(session, user, count)
    except inventory.InventoryError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return RedirectResponse("/inventario", status_code=303)


# ========================================================== RECETAS
@app.get("/recetas", response_class=HTMLResponse)
def recipes_page(request: Request, ctx=Depends(require_user),
                 session: Session = Depends(get_db)):
    user, auth_session = ctx
    rows = (session.query(Recipe).filter_by(restaurant_id=user.restaurant_id)
            .order_by(Recipe.kind, Recipe.name).all())
    costs = costing.unit_costs(session, user.restaurant_id)
    from thegrill.engine.recipes import cost_recipe
    costed = {r.id: cost_recipe(r, costs) for r in rows}
    return page(request, "recipes.html", user, auth_session, session, rows=rows,
                costed=costed, menu=costing.menu(session, user.restaurant_id),
                kinds=list(RecipeKind), units=list(Unit))


@app.post("/recetas/nueva")
def create_recipe(request: Request, name: str = Form(...), kind: str = Form("DISH"),
                  portions: int = Form(1), yield_qty: str = Form(""), yield_unit: str = Form("KG"),
                  sale_price: str = Form(""), vat_pct: str = Form("0"), csrf: str = Form(""),
                  ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    import re as _re
    code = _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:64] or "receta"
    if session.query(Recipe).filter_by(restaurant_id=user.restaurant_id, code=code).first():
        code = f"{code}_{int(datetime.utcnow().timestamp())}"
    recipe = Recipe(restaurant_id=user.restaurant_id, code=code, name=name.strip(),
                    kind=RecipeKind[kind] if kind in RecipeKind.__members__ else RecipeKind.DISH,
                    portions=max(1, portions),
                    vat_pct=exacto.leer(vat_pct, 0.0) or 0.0,
                    yield_qty=exacto.leer(yield_qty),
                    yield_unit=Unit[yield_unit] if yield_unit in Unit.__members__ else None,
                    sale_price=exacto.leer(sale_price,
                                           decimales=exacto.CENTIMOS_DECIMALES))
    session.add(recipe)
    session.flush()
    return RedirectResponse(f"/recetas/{recipe.code}", status_code=303)


@app.get("/recetas/{code}", response_class=HTMLResponse)
def recipe_detail(code: str, request: Request, ctx=Depends(require_user),
                  session: Session = Depends(get_db)):
    """El escandallo: el árbol entero, el coste de cada nivel y el food cost."""
    user, auth_session = ctx
    recipe = (session.query(Recipe)
              .filter_by(restaurant_id=user.restaurant_id, code=code).first())
    lang = lang_for(request, session, user)
    if recipe is None:
        raise HTTPException(status_code=404, detail=i18n.t(lang, "error.tpl_not_found"))
    from thegrill.engine.recipes import cost_recipe, cost_tree, ingredient_rollup, prep_costs
    costs = costing.unit_costs(session, user.restaurant_id)
    tree = cost_tree(recipe, costs)
    return page(request, "recipe_detail.html", user, auth_session, session, recipe=recipe,
                cost=cost_recipe(recipe, costs), tree=list(tree.walk()),
                rollup=ingredient_rollup(tree), preps=prep_costs(tree),
                ingredients=(session.query(Ingredient)
                             .filter_by(restaurant_id=user.restaurant_id, active=True)
                             .order_by(Ingredient.name).all()),
                subrecipes=(session.query(Recipe)
                            .filter(Recipe.restaurant_id == user.restaurant_id,
                                    Recipe.kind == RecipeKind.PREP,
                                    Recipe.id != recipe.id)
                            .order_by(Recipe.name).all()))


@app.post("/recetas/{code}/linea")
def add_recipe_line(code: str, request: Request, component: str = Form(...),
                    qty_g: str = Form(""), qty: str = Form(""),
                    waste_pct: float = Form(0.0),
                    csrf: str = Form(""), ctx=Depends(require_manager_user),
                    session: Session = Depends(get_db)):
    """`component` llega como «ing:3» o «rec:7»."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    recipe = (session.query(Recipe)
              .filter_by(restaurant_id=user.restaurant_id, code=code).first())
    if recipe is None:
        raise HTTPException(status_code=404, detail="")
    kind, _, raw = component.partition(":")
    # `qty_g` son gramos y lo manda lo que se pesa; `qty` es la unidad del
    # componente —litros, unidades— y el nombre de antes.
    line = RecipeLine(recipe_id=recipe.id, qty=exacto.leer(qty, 0.0) or 0.0,
                      waste_pct=waste_pct,
                      sort_order=len(recipe.lines) * 10)
    if kind == "ing":
        ing = _own(session, user, Ingredient, int(raw), request)
        line.ingredient_id = ing.id
        # Y si vino en gramos, se pasa a la unidad en la que vive el stock:
        # 110 g de huevo son dos huevos cuando uno pesa 55.
        if (qty_g or "").strip():
            line.qty = pesos.en_su_unidad(pesos.leer(qty_g), ing) or 0.0
    elif kind == "rec":
        line.sub_recipe_id = _own(session, user, Recipe, int(raw), request).id
    else:
        raise HTTPException(status_code=400, detail="")
    session.add(line)
    return RedirectResponse(f"/recetas/{code}", status_code=303)


@app.post("/recetas/{code}/linea/{line_id}/borrar")
def delete_recipe_line(code: str, line_id: int, request: Request, csrf: str = Form(""),
                       ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    line = session.get(RecipeLine, line_id)
    if line is not None:
        recipe = session.get(Recipe, line.recipe_id)
        if recipe is not None and recipe.restaurant_id == user.restaurant_id:
            session.delete(line)
    return RedirectResponse(f"/recetas/{code}", status_code=303)


# ============================================================ VENTAS
@app.get("/ventas", response_class=HTMLResponse)
def sales_page(request: Request, ctx=Depends(require_user),
               session: Session = Depends(get_db), done: str = ""):
    user, auth_session = ctx
    return page(request, "sales.html", user, auth_session, session, done=done,
                mapping=(session.query(PosProduct).filter_by(restaurant_id=user.restaurant_id)
                         .order_by(PosProduct.pos_name).all()),
                dishes=(session.query(Recipe)
                        .filter_by(restaurant_id=user.restaurant_id, kind=RecipeKind.DISH,
                                   active=True).order_by(Recipe.name).all()))


@app.post("/ventas/mapeo")
def map_pos_product(request: Request, pos_name: str = Form(...), recipe_id: int = Form(...),
                    pos_code: str = Form(""), csrf: str = Form(""),
                    ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    recipe = _own(session, user, Recipe, recipe_id, request)
    existing = (session.query(PosProduct)
                .filter_by(restaurant_id=user.restaurant_id, pos_name=pos_name.strip()).first())
    if existing:
        existing.recipe_id = recipe.id
        existing.pos_code = pos_code.strip() or None
    else:
        session.add(PosProduct(restaurant_id=user.restaurant_id, pos_name=pos_name.strip(),
                               pos_code=pos_code.strip() or None, recipe_id=recipe.id))
    return RedirectResponse("/ventas", status_code=303)


@app.post("/ventas")
async def register_sales(request: Request, ctx=Depends(require_user),
                         session: Session = Depends(get_db)):
    """Descuenta del almacén lo vendido. Cualquiera del equipo puede cargarlo."""
    user, auth_session = ctx
    form = await request.form()
    lang = lang_for(request, session, user)
    try:
        auth.check_csrf(auth_session, form.get("csrf"), lang)
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None

    sales: list[tuple[str, float]] = []
    for key, value in form.multi_items():
        if key.startswith("units:") and str(value).strip():
            try:
                units = exacto.leer(value, decimales=0) or 0.0
            except ValueError:
                continue
            if units > 0:
                sales.append((key.split(":", 1)[1], units))
    on = form.get("business_date")
    result = costing.consume_sales(session, user, sales,
                                   on=date.fromisoformat(on) if on else None, lang=lang)
    summary = i18n.t(lang, "sale.done", n=result.lines, cost=f"{result.cost:.2f}")
    return RedirectResponse(f"/ventas?done={summary}", status_code=303)


# ========================================================= DESCARGAS
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def active_templates(session: Session, restaurant_id: int) -> list[RecordTemplate]:
    return (session.query(RecordTemplate)
            .filter_by(restaurant_id=restaurant_id, active=True)
            .order_by(RecordTemplate.sort_order, RecordTemplate.name).all())


@app.get("/descargas", response_class=HTMLResponse)
def downloads_page(request: Request, ctx=Depends(require_user),
                   session: Session = Depends(get_db)):
    """Hojas en Excel para imprimir y rellenar a mano. Las ve todo el equipo."""
    user, auth_session = ctx
    rows = active_templates(session, user.restaurant_id)
    return page(request, "downloads.html", user, auth_session, session,
                rows=rows, blank_rows=sheets.BLANK_ROWS)


@app.get("/descargas/todo.xlsx")
def download_all(request: Request, ctx=Depends(require_user),
                 session: Session = Depends(get_db)):
    user, _ = ctx
    lang = lang_for(request, session, user)
    restaurant = session.get(Restaurant, user.restaurant_id)
    payload = sheets.workbook_for(active_templates(session, user.restaurant_id), restaurant, lang)
    name = sheets.filename_for(f"{restaurant.slug}-hojas")
    return Response(payload, media_type=XLSX_MEDIA,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/descargas/{code}.xlsx")
def download_template(code: str, request: Request, ctx=Depends(require_user),
                      session: Session = Depends(get_db)):
    user, _ = ctx
    lang = lang_for(request, session, user)
    tpl = (session.query(RecordTemplate)
           .filter_by(restaurant_id=user.restaurant_id, code=code, active=True).first())
    if tpl is None:
        raise HTTPException(status_code=404,
                            detail=i18n.t(lang, "error.template_not_found"))
    restaurant = session.get(Restaurant, user.restaurant_id)
    payload = sheets.workbook_for([tpl], restaurant, lang)
    return Response(payload, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{sheets.filename_for(tpl.code)}"'})


# ====================================================== CONFIGURACIÓN
@app.get("/configuracion", response_class=HTMLResponse)
def settings_page(request: Request, ctx=Depends(require_user),
                  session: Session = Depends(get_db), saved: int = 0,
                  changed: int = 0, error: str = ""):
    user, auth_session = ctx
    restaurant = session.get(Restaurant, user.restaurant_id)
    return page(request, "settings.html", user, auth_session, session,
                restaurant=restaurant, saved=bool(saved), changed=bool(changed),
                error=error, pos_modes=list(PosMatch), currencies=money.MONEDAS,
                zonas=ZONAS, horas_cierre=list(range(jornada.MAXIMO + 1)),
                cierre=jornada.corte(restaurant),
                dias_descongelado=caducidad.dias(restaurant),
                bandas={"chilled": rangos.banda(Storage.CHILLED, restaurant),
                        "frozen": rangos.banda(Storage.FROZEN, restaurant)},
                version=version.actual())


@app.post("/configuracion")
def save_settings(request: Request, language: str = Form(...),
                  restaurant_language: str = Form(""), pos_match: str = Form(""),
                  currency: str = Form(""), timezone_name: str = Form("", alias="timezone"),
                  day_cut_hour: str = Form(""), thaw_days: str = Form(""),
                  chilled_min_c: str = Form(""), chilled_max_c: str = Form(""),
                  frozen_min_c: str = Form(""), frozen_max_c: str = Form(""),
                  csrf: str = Form(""),
                  ctx=Depends(require_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    try:
        auth.check_csrf(auth_session, csrf, lang_for(request, session, user))
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    if i18n.is_supported(language):
        user.language = language
    if user.role == Role.MANAGER:
        restaurant = session.get(Restaurant, user.restaurant_id)
        if restaurant is not None:
            if restaurant_language and i18n.is_supported(restaurant_language):
                restaurant.language = restaurant_language
            if pos_match in PosMatch.__members__:
                restaurant.pos_match = PosMatch[pos_match]
            if money.es_valida(currency):
                restaurant.currency = currency.upper()
            if timezone_name.strip() and timezone_name.strip() in ZONAS:
                restaurant.timezone = timezone_name.strip()
            if day_cut_hour.strip():
                try:
                    restaurant.day_cut_hour = max(0, min(jornada.MAXIMO,
                                                         int(day_cut_hour)))
                except ValueError:
                    pass
            if thaw_days.strip():
                try:
                    restaurant.thaw_days = max(1, min(caducidad.MAXIMO,
                                                      int(thaw_days)))
                except ValueError:
                    pass
            # Las bandas de llegada, límite a límite: quien aprieta solo el
            # máximo no tiene que volver a escribir el mínimo. Un número que no
            # es un número deja el que había, que es lo prudente con algo que
            # decide si una carne se devuelve o se acepta.
            for campo in ("chilled_min_c", "chilled_max_c",
                          "frozen_min_c", "frozen_max_c"):
                escrito = locals()[campo]
                if not escrito.strip():
                    continue
                try:
                    grados = exacto.leer(escrito, decimales=1)
                except ValueError:
                    continue
                if grados is None or not (rangos.TEMPERATURA[0] <= grados
                                          <= rangos.TEMPERATURA[1]):
                    continue
                setattr(restaurant, campo, float(grados))
    response = RedirectResponse("/configuracion?saved=1", status_code=303)
    return set_lang_cookie(response, user.language or i18n.DEFAULT_LANG)


@app.post("/configuracion/contrasena")
def change_my_password(request: Request, current: str = Form(...), new: str = Form(...),
                       repeat: str = Form(""), csrf: str = Form(""),
                       ctx=Depends(require_user), session: Session = Depends(get_db)):
    """Uno cambia la suya: hay que saber la de antes."""
    user, auth_session = ctx
    try:
        auth.check_csrf(auth_session, csrf, lang_for(request, session, user))
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    lang = lang_for(request, session, user)
    if new != repeat:
        return RedirectResponse(f"/configuracion?error={i18n.t(lang, 'pass.mismatch')}",
                                status_code=303)
    try:
        auth.set_password(session, user, new, current=current,
                          close_others=request.cookies.get(auth.COOKIE_NAME), lang=lang)
    except (auth.AuthError, ValueError) as e:
        return RedirectResponse(f"/configuracion?error={e}", status_code=303)
    return RedirectResponse("/configuracion?changed=1", status_code=303)


@app.post("/manager/equipo/{user_id}/contrasena")
def reset_team_password(user_id: int, request: Request, password: str = Form(...),
                        csrf: str = Form(""), ctx=Depends(require_manager_user),
                        session: Session = Depends(get_db)):
    """El manager le pone una nueva a su gente, que es quien la ha olvidado."""
    user, auth_session = ctx
    try:
        auth.check_csrf(auth_session, csrf, lang_for(request, session, user))
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    lang = lang_for(request, session, user)
    target = session.get(User, user_id)
    if target is None or target.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=404, detail=i18n.t(lang, "error.user_not_found"))
    try:
        auth.set_password(session, target, password, lang=lang)
    except (auth.AuthError, ValueError) as e:
        return RedirectResponse(f"/manager/equipo?error={e}", status_code=303)
    return RedirectResponse(
        f"/manager/equipo?done={i18n.t(lang, 'pass.reset_done', name=target.name)}",
        status_code=303)


# ====================================================== NOTIFICACIONES
@app.get("/notificaciones", response_class=HTMLResponse)
def notifications_page(request: Request, ctx=Depends(require_user),
                       session: Session = Depends(get_db)):
    """Avisos de esta persona. Verlos los marca como leídos, con hora."""
    user, auth_session = ctx
    rows = service.recent_notifications(session, user.id)
    pending = [n.id for n in rows if n.read_at is None]
    response = page(request, "notifications.html", user, auth_session, session,
                    rows=rows, just_read=set(pending))
    service.mark_all_read(session, user.id)
    return response


@app.get("/api/notificaciones")
def notifications_feed(request: Request, ctx=Depends(require_user),
                       session: Session = Depends(get_db)):
    """Lo consulta la cabecera cada medio minuto para refrescar el contador."""
    user, _ = ctx
    rows = (session.query(Notification)
            .filter(Notification.user_id == user.id, Notification.read_at.is_(None))
            .order_by(Notification.id.desc()).limit(10).all())
    return JSONResponse({
        "unread": service.unread_count(session, user.id),
        "items": [{"id": n.id, "title": n.title, "body": n.body,
                   "severity": n.severity.value} for n in rows],
    })


@app.get("/foto/{attachment_id}")
def serve_photo(attachment_id: int, request: Request, ctx=Depends(require_user),
                session: Session = Depends(get_db)):
    """Una foto solo se sirve a gente del mismo restaurante."""
    user, _ = ctx
    att = session.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail=i18n.t(lang_for(request, session, user), "error.photo_not_found"))
    record = session.get(Record, att.record_id)
    if record is None or record.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=403, detail=i18n.t(lang_for(request, session, user), "error.photo_other_restaurant"))
    if user.role != Role.MANAGER and record.created_by != user.id:
        raise HTTPException(status_code=403, detail=i18n.t(lang_for(request, session, user), "error.photo_only_yours"))
    if not os.path.exists(att.stored_path):
        raise HTTPException(status_code=404, detail=i18n.t(lang_for(request, session, user), "error.photo_missing_file"))
    with open(att.stored_path, "rb") as fh:
        return Response(fh.read(), media_type=att.content_type)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


def create_app(database_url: str = "sqlite:///thegrill.db") -> FastAPI:
    db.init_engine(database_url)
    db.create_all()
    return app
