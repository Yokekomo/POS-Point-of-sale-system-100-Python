"""Control de carnes. Una sola puerta: la carne.

Misma casa que la plataforma de cocina y el mismo motor probado, pero con las
pantallas que una cocina de carne necesita y sin nada más: recepción de
primales, despiece, cámara, descongelado, inventario, merma, trazabilidad y la
carta de carnes atada al POS.

Corre por su cuenta, con su propia base de datos:

    python -m thegrill.cli --db sqlite:///carnes.db serve-carne --port 8001
"""
import logging
import os
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from thegrill import db
from thegrill.meat import billing, mailer, perms, privacy, security
from thegrill.meat import service as meat
from thegrill.meat import sheets_meat
from thegrill.models import (AccessRequest, Alert, Billing, ConsumptionMode, CountPeriod,
                             CountStatus, Ingredient, IngredientItem, MeatCount, Plan,
                             PosMatch, PosProduct, Primal, Recipe, RequestStatus,
                             Restaurant, Role, Rotation, Unit, User)
from thegrill.web import (auth, butchery, costing, defrost, i18n, inventory, service,
                          tracing, waste)

log = logging.getLogger(__name__)

MEAT_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
KITCHEN_TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                 "web", "templates")
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PHOTO_DIR = os.path.join(STATIC_DIR, "fotos")
# Las fotos de la portada. Se llaman así y se dejan caer en esa carpeta; la
# portada usa las que encuentre y se arregla sin las que falten.
PHOTO_SLOTS = ("primal", "cortes", "plato")

# Las plantillas propias mandan; lo que no esté aquí se hereda de la cocina.
templates = Jinja2Templates(directory=[MEAT_TEMPLATES, KITCHEN_TEMPLATES])
templates.env.filters["ceil_pct"] = butchery.ceil_pct
# Sin documentación automática: /docs y /openapi.json enseñaban el mapa entero
# de la aplicación a cualquiera que pasara por ahí.
app = FastAPI(title="Control de carnes", docs_url=None, redoc_url=None, openapi_url=None)
os.makedirs(PHOTO_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------- utilidades
def get_db():
    with db.session_scope() as session:
        yield session


def current(request: Request, session: Session):
    return auth.resolve_session(session, request.cookies.get(auth.COOKIE_NAME))


def lang_for(request: Request, session: Session | None = None, user: User | None = None) -> str:
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
    user, _ = found
    restaurant = session.get(Restaurant, user.restaurant_id)
    # Una cuenta bloqueada no trabaja. Se puede entrar, ver por qué y salir.
    if restaurant is not None and restaurant.blocked and user.role != Role.OWNER:
        raise HTTPException(status_code=303, headers={"Location": "/cuenta"})
    return found


def require_manager_user(request: Request, session: Session = Depends(get_db)):
    user, auth_session = require_user(request, session)
    if user.role != Role.MANAGER:
        raise HTTPException(status_code=403,
                            detail=i18n.t(lang_for(request, session, user), "error.managers_only"))
    return user, auth_session


def require_owner(request: Request, session: Session = Depends(get_db)):
    found = current(request, session)
    if found is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user, auth_session = found
    if user.role != Role.OWNER:
        raise HTTPException(status_code=404, detail="")   # ni se insinúa que existe
    return user, auth_session


def needs(capability: str):
    """Depende de poder hacer eso. La puerta se cierra aquí, no en la plantilla.

    Una barra sin enlace no es una puerta cerrada: si no se comprueba en la
    ruta, basta escribir la dirección a mano para entrar.
    """
    def dependency(request: Request, session: Session = Depends(get_db)):
        user, auth_session = require_user(request, session)
        if not perms.can(user, capability):
            raise HTTPException(
                status_code=403,
                detail=i18n.t(lang_for(request, session, user), "error.not_your_level"))
        return user, auth_session
    return dependency


def page(request: Request, name: str, user: User | None = None, auth_session=None,
         session: Session | None = None, **ctx):
    lang = ctx.pop("lang", None) or lang_for(request, session, user)
    base = {"user": user, "csrf": auth_session.csrf if auth_session else "",
            "today": date.today().isoformat(), "can": perms.checker(user),
            "here": request.url.path,
            "nonce": getattr(request.state, "nonce", ""),
            "unread": service.unread_count(session, user.id) if (user and session) else 0,
            "t": i18n.translator(lang), "lang": lang, "dir": i18n.direction(lang),
            "languages": i18n.LANGUAGES}
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def is_https(request: Request) -> bool:
    """Si la petición llegó por HTTPS, mirando también lo que dice el proxy."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def set_session_cookie(response: Response, token: str) -> Response:
    secure = os.environ.get("GRILL_INSECURE_COOKIE") != "1"
    # `strict`: la cookie no viaja en peticiones que vengan de otro sitio, ni
    # siquiera al pinchar un enlace. Es un programa de trabajo, no una red
    # social: nadie llega aquí desde fuera y necesita estar dentro al llegar.
    response.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="strict",
                        secure=secure, max_age=auth.SESSION_DAYS * 86400, path="/")
    return response


def set_lang_cookie(response: Response, lang: str) -> Response:
    response.set_cookie(i18n.COOKIE_NAME, lang, httponly=False, samesite="lax",
                        max_age=365 * 86400, path="/")
    return response


def _guard(request, session, user, auth_session, csrf: str) -> None:
    try:
        auth.check_csrf(auth_session, csrf, lang_for(request, session, user))
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None


def _own(session: Session, user: User, model, obj_id: int, request: Request):
    row = session.get(model, obj_id)
    if row is None or row.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=404,
                            detail=i18n.t(lang_for(request, session, user), "error.other_restaurant"))
    return row


def _num(raw: str | None, default: float | None = None) -> float | None:
    """Los teclados de cocina escriben comas."""
    if raw is None or not str(raw).strip():
        return default
    return float(str(raw).replace(",", "."))


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Las cabeceras van en todas las respuestas, también en las de error."""
    request.state.nonce = security.new_nonce()
    response = await call_next(request)
    for header, value in security.SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    response.headers.setdefault("Content-Security-Policy",
                                security.content_policy(request.state.nonce))
    if is_https(request):
        response.headers.setdefault("Strict-Transport-Security",
                                    "max-age=31536000; includeSubDomains")
    return response


def client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or "desconocido"


@app.exception_handler(HTTPException)
async def redirect_handler(request: Request, exc: HTTPException):
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    lang = i18n.resolve(cookie=request.cookies.get(i18n.COOKIE_NAME),
                        accept_header=request.headers.get("accept-language"))
    return templates.TemplateResponse(request, "error.html",
                                      {"user": None, "csrf": "", "unread": 0,
                                       "t": i18n.translator(lang), "lang": lang,
                                       "dir": i18n.direction(lang), "languages": i18n.LANGUAGES,
                                       "code": exc.status_code, "detail": exc.detail,
                                       "nonce": getattr(request.state, "nonce", ""),
                                       "can": lambda capability: False,
                                       "here": request.url.path},
                                      status_code=exc.status_code)


# ============================================================== ACCESO
@app.get("/idioma/{lang}")
def choose_language(lang: str, next: str = "/login"):
    if not i18n.is_supported(lang):
        raise HTTPException(status_code=404, detail="")
    target = next if next.startswith("/") and not next.startswith("//") else "/login"
    return set_lang_cookie(RedirectResponse(target, status_code=303), lang)


def landing_photos() -> dict[str, str]:
    """Las fotos que hay puestas, por su sitio en la portada.

    No hay foto que buscar en internet ni foto de relleno: si el archivo está,
    se usa; si no está, la portada se ve igual de terminada sin él.
    """
    found = {}
    for slot in PHOTO_SLOTS:
        for ext in ("webp", "jpg", "jpeg", "png"):
            name = f"{slot}.{ext}"
            if os.path.exists(os.path.join(PHOTO_DIR, name)):
                found[slot] = f"/static/fotos/{name}"
                break
    return found


@app.get("/", response_class=HTMLResponse)
def root(request: Request, session: Session = Depends(get_db)):
    """La portada: quien ya tiene cuenta entra, quien no, se entera de qué es."""
    if current(request, session):
        return RedirectResponse("/hoy", status_code=303)
    return page(request, "public_home.html", lang=lang_for(request, session),
                retention_days=privacy.RETENTION_DAYS, trial_days=billing.TRIAL_DAYS,
                photos=landing_photos())


@app.get("/cookies", response_class=HTMLResponse)
def cookies_page(request: Request, session: Session = Depends(get_db)):
    """Qué cookies hay, para qué y cuánto duran. Solo técnicas."""
    return page(request, "public_cookies.html", lang=lang_for(request, session),
                retention_days=privacy.RETENTION_DAYS)


@app.get("/precios", response_class=HTMLResponse)
def pricing(request: Request, session: Session = Depends(get_db)):
    return page(request, "public_pricing.html", lang=lang_for(request, session),
                trial_days=billing.TRIAL_DAYS, retention_days=privacy.RETENTION_DAYS)


@app.get("/solicitar", response_class=HTMLResponse)
def request_form(request: Request, session: Session = Depends(get_db), sent: int = 0):
    return page(request, "public_request.html", lang=lang_for(request, session),
                sent=bool(sent), error="", plans=list(Plan), sub={},
                trial_days=billing.TRIAL_DAYS, retention_days=privacy.RETENTION_DAYS)


@app.post("/solicitar", response_class=HTMLResponse)
async def submit_request(request: Request, session: Session = Depends(get_db)):
    """La única puerta abierta a internet. Con freno y sin datos de pago."""
    form = await request.form()
    lang = lang_for(request, session)
    data = {k: (form.get(k) or "").strip() for k in
            ("restaurant_name", "legal_name", "tax_number", "country", "address",
             "contact_name", "contact_role", "email", "phone", "message")}

    def again(error: str):
        return page(request, "public_request.html", lang=lang, sent=False, error=error,
                    plans=list(Plan), sub=data, trial_days=billing.TRIAL_DAYS,
                    retention_days=privacy.RETENTION_DAYS)

    if not security.form_allowed(client_ip(request)):
        return again(i18n.t(lang, "pub.too_many"))
    plan = form.get("plan")
    try:
        billing.request_access(
            session, plan=Plan[plan] if plan in Plan.__members__ else Plan.SINGLE,
            outlets=int(_num(form.get("outlets"), 1) or 1),
            cooks=int(_num(form.get("cooks"), 0) or 0) or None,
            **data)
    except (billing.BillingError, ValueError) as e:
        return again(str(e))
    return RedirectResponse("/solicitar?sent=1", status_code=303)


@app.get("/cuenta", response_class=HTMLResponse)
def account_notice(request: Request, session: Session = Depends(get_db)):
    """Lo que ve cada quien cuando la cuenta está parada."""
    found = current(request, session)
    if found is None:
        return RedirectResponse("/login", status_code=303)
    user, auth_session = found
    restaurant = session.get(Restaurant, user.restaurant_id)
    if restaurant is None or not restaurant.blocked:
        return RedirectResponse("/hoy", status_code=303)
    return page(request, "account_blocked.html", user, auth_session, session,
                restaurant=restaurant)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, session: Session = Depends(get_db)):
    if current(request, session):
        return RedirectResponse("/hoy", status_code=303)
    return page(request, "login.html", error="")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          session: Session = Depends(get_db)):
    lang = lang_for(request, session)
    # El freno va por correo y por dirección: ni se castiga a una casa entera
    # por una dirección, ni se prueban mil contraseñas desde la misma.
    key = f"{(email or '').strip().lower()}|{client_ip(request)}"
    espera = security.locked_for(key)
    if espera:
        return page(request, "login.html", lang=lang,
                    error=i18n.t(lang, "auth.too_many", minutes=max(1, espera // 60)))
    try:
        user = auth.authenticate(session, email, password, lang=lang)
    except auth.AuthError as e:
        security.note_failure(key)
        log.warning("acceso fallido para %s desde %s", (email or "").strip().lower(),
                    client_ip(request))
        return page(request, "login.html", error=str(e), lang=lang)
    security.clear(key)
    token, _ = auth.start_session(session, user)
    destino = "/admin" if user.role == Role.OWNER else "/hoy"
    return set_session_cookie(RedirectResponse(destino, status_code=303), token)


@app.post("/logout")
def logout(request: Request, session: Session = Depends(get_db)):
    auth.end_session(session, request.cookies.get(auth.COOKIE_NAME))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@app.get("/signup")
def signup_form():
    """Aquí no hay registro abierto: las cuentas las crea la plataforma."""
    return RedirectResponse("/solicitar", status_code=303)


@app.post("/signup")
def signup_closed():
    return RedirectResponse("/solicitar", status_code=303)


@app.get("/join")
def join_form():
    """Las cuentas de la casa las crea su manager, una a una y con su nivel."""
    return RedirectResponse("/login", status_code=303)


@app.post("/join")
def join_closed():
    return RedirectResponse("/login", status_code=303)


# ================================================================= HOY
@app.get("/hoy", response_class=HTMLResponse)
def home(request: Request, ctx=Depends(require_user), session: Session = Depends(get_db)):
    """Lo que está pendiente en la carne, en una pantalla."""
    user, auth_session = ctx
    lang = lang_for(request, session, user)
    restaurant = session.get(Restaurant, user.restaurant_id)
    return page(request, "home.html", user, auth_session, session, lang=lang,
                restaurant=restaurant, trial_left=billing.trial_left(restaurant),
                free_cancel=billing.free_cancellation(restaurant),
                info=meat.today(session, user.restaurant_id, lang=lang))


@app.post("/cuenta/cancelar")
def cancel_own_account(request: Request, reason: str = Form(""), csrf: str = Form(""),
                       ctx=Depends(needs(perms.TEAM)), session: Session = Depends(get_db)):
    """La casa cancela su cuenta. En prueba y antes de tiempo, sin pagar nada."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    restaurant = session.get(Restaurant, user.restaurant_id)
    if restaurant is None or restaurant.platform:
        raise HTTPException(status_code=404, detail="")
    billing.cancel(session, user, restaurant, reason.strip() or None)
    return RedirectResponse("/cuenta", status_code=303)


# ========================================================== RECEPCIÓN
@app.get("/recepcion", response_class=HTMLResponse)
def reception_page(request: Request, ctx=Depends(needs(perms.RECEIVE)),
                   session: Session = Depends(get_db)):
    user, auth_session = ctx
    return _reception(request, user, auth_session, session)


def _reception(request, user, auth_session, session, *, done=None, error=""):
    return page(request, "reception.html", user, auth_session, session, done=done, error=error,
                rows=range(8), recent=meat.recent_primals(session, user.restaurant_id))


@app.post("/recepcion", response_class=HTMLResponse)
async def receive(request: Request, ctx=Depends(needs(perms.RECEIVE)),
                  session: Session = Depends(get_db)):
    """Un lote de recepción: cada pieza con su número, su peso y su precio."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)

    lot = (form.get("lot") or "").strip()
    sku = (form.get("sku") or "").strip()
    grade = (form.get("grade") or "").strip() or None
    origin = (form.get("origin") or "").strip() or None
    use_by = (form.get("use_by") or "").strip()
    price = form.get("price_kg")
    try:
        rows = []
        for i in range(24):
            serial = (form.get(f"serial:{i}") or "").strip()
            kg = form.get(f"kg:{i}")
            if not serial and not (kg or "").strip():
                continue
            rows.append(meat.PrimalRow(
                serial=serial, kg=_num(kg, 0.0) or 0.0,
                price_kg=_num(form.get(f"price:{i}"), _num(price)),
                sku=(form.get(f"sku:{i}") or "").strip() or sku,
                grade=grade, origin=origin,
                use_by=date.fromisoformat(use_by) if use_by else None))
        created = meat.receive_primals(session, user, lot, rows, lang=lang)
    except (meat.MeatError, ValueError) as e:
        return _reception(request, user, auth_session, session, error=str(e))
    return _reception(request, user, auth_session, session,
                      done=i18n.t(lang, "m.rec.done", n=len(created), lot=lot or "—"))


# ============================================================ DESPIECE
@app.get("/despiece", response_class=HTMLResponse)
def butchery_page(request: Request, ctx=Depends(needs(perms.BUTCHER)),
                  session: Session = Depends(get_db)):
    user, auth_session = ctx
    return _butchery(request, user, auth_session, session)


def _butchery(request, user, auth_session, session, *, done=None, issues=(), error=""):
    return page(request, "butchery.html", user, auth_session, session, done=done,
                issues=list(issues), error=error, rows=range(meat.MAX_CUTS),
                tg=meat.next_tg(session, user.restaurant_id),
                primals=meat.primals_in_stock(session, user.restaurant_id),
                articles=meat.articles(session, user.restaurant_id),
                recent=meat.recent_butchery(session, user.restaurant_id))


@app.post("/despiece", response_class=HTMLResponse)
async def post_butchery(request: Request, ctx=Depends(needs(perms.BUTCHER)),
                        session: Session = Depends(get_db)):
    """Vuelca el despiece a cámara: cada corte, con su serial y su coste."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)
    try:
        rows = []
        for i in range(meat.MAX_CUTS):
            name = (form.get(f"cut:{i}") or "").strip()
            pieces = form.get(f"pieces:{i}")
            grams = form.get(f"grams:{i}")
            if not name and not (pieces or "").strip():
                continue
            rows.append(meat.CutRow(
                name=name, item_id=int(form.get(f"item:{i}") or 0),
                pieces=int(_num(pieces, 0) or 0), grams=_num(grams, 0.0) or 0.0,
                value_index=_num(form.get(f"index:{i}"), 1.0) or 1.0,
                is_trim=bool(form.get(f"trim:{i}"))))
        on = (form.get("date") or "").strip()
        _, result = meat.post_butchery(
            session, user, tg=(form.get("tg") or ""),
            serials=form.getlist("primal"),
            before_kg=_num(form.get("before_kg"), 0.0) or 0.0,
            rows=rows, waste_kg=_num(form.get("waste_kg"), 0.0) or 0.0,
            on=date.fromisoformat(on) if on else None,
            staff=(form.get("staff") or "").strip() or None, lang=lang)
    except (meat.MeatError, ValueError) as e:
        return _butchery(request, user, auth_session, session, error=str(e))
    return _butchery(request, user, auth_session, session, issues=result.issues,
                     done=i18n.t(lang, "m.tg.posted", tg=result.tg, cuts=len(result.lots),
                                 kg=f"{sum(l.qty for l in result.lots):.10g}"))


# ============================================================== CÁMARA
@app.get("/carne", response_class=HTMLResponse)
def chamber(request: Request, ctx=Depends(needs(perms.STOCK)),
            session: Session = Depends(get_db), closed: str = ""):
    """Lo que queda: cortes en cámara y primales sin despiezar."""
    user, auth_session = ctx
    return page(request, "chamber.html", user, auth_session, session, closed=closed,
                status=butchery.status(session, user.restaurant_id))


@app.post("/carne/cierre")
def close_meat_day(request: Request, csrf: str = Form(""), ctx=Depends(needs(perms.STOCK)),
                   session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    result = butchery.close_day(session, user)
    return RedirectResponse(f"/carne?closed={len(result.alerts)}", status_code=303)


# ======================================================== DESCONGELADO
@app.get("/descongelado", response_class=HTMLResponse)
def defrost_page(request: Request, ctx=Depends(needs(perms.DEFROST)),
                 session: Session = Depends(get_db), shift: str = "", done: str = ""):
    user, auth_session = ctx
    return _defrost(request, user, auth_session, session, shift=shift, done=done)


def _defrost(request, user, auth_session, session, *, shift="", done="", error="", closed=None):
    on = date.today()
    return page(request, "defrost.html", user, auth_session, session, shift=shift,
                done=done, error=error, on=on, closed=closed,
                states=defrost.shift_states(session, user.restaurant_id, on, shift),
                lots=(session.query(butchery.IngredientLot)
                      .filter(butchery.IngredientLot.restaurant_id == user.restaurant_id,
                              butchery.IngredientLot.qty_remaining > 1e-9,
                              butchery.IngredientLot.serial.isnot(None))
                      .order_by(butchery.IngredientLot.expiry).all()))


@app.post("/descongelado/salida", response_class=HTMLResponse)
def defrost_intake(request: Request, serial: str = Form(...), pieces: int = Form(...),
                   total_kg: str = Form(...), shift: str = Form(""), note: str = Form(""),
                   csrf: str = Form(""), ctx=Depends(needs(perms.DEFROST)),
                   session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        defrost.intake(session, user, serial.strip(), pieces, _num(total_kg, 0.0) or 0.0,
                       shift=shift.strip(), note=note.strip() or None)
    except (defrost.DefrostError, ValueError) as e:
        return _defrost(request, user, auth_session, session, shift=shift, error=str(e))
    return RedirectResponse(f"/descongelado?shift={shift}", status_code=303)


@app.post("/descongelado/recuento", response_class=HTMLResponse)
def defrost_count(request: Request, serial: str = Form(...), pieces: int = Form(...),
                  total_kg: str = Form(...), shift: str = Form(""), note: str = Form(""),
                  csrf: str = Form(""), ctx=Depends(needs(perms.DEFROST)),
                  session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        defrost.count(session, user, serial.strip(), pieces, _num(total_kg, 0.0) or 0.0,
                      shift=shift.strip(), note=note.strip() or None)
    except (defrost.DefrostError, ValueError) as e:
        return _defrost(request, user, auth_session, session, shift=shift, error=str(e))
    return RedirectResponse(f"/descongelado?shift={shift}", status_code=303)


@app.post("/descongelado/cierre", response_class=HTMLResponse)
def defrost_close(request: Request, shift: str = Form(""), csrf: str = Form(""),
                  ctx=Depends(needs(perms.CLOSE_SHIFT)), session: Session = Depends(get_db)):
    """El cierre convierte el recuento en consumo real y lo descuenta."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    try:
        result = defrost.close(session, user, shift=shift.strip(), lang=lang)
    except (defrost.DefrostError, ValueError) as e:
        return _defrost(request, user, auth_session, session, shift=shift, error=str(e))
    return _defrost(request, user, auth_session, session, shift=shift, closed=result,
                    done=i18n.t(lang, "m.df.closed", n=len(result.consumed)))


# =============================================================== CORTES
@app.get("/cortes", response_class=HTMLResponse)
def cuts_page(request: Request, ctx=Depends(needs(perms.STOCK)),
              session: Session = Depends(get_db), error: str = ""):
    user, auth_session = ctx
    return page(request, "cuts.html", user, auth_session, session, error=error,
                rotations=list(Rotation), modes=list(ConsumptionMode),
                cuts=meat.cuts(session, user.restaurant_id),
                stock=costing.stock_on_hand(session, user.restaurant_id),
                costs=costing.unit_costs(session, user.restaurant_id))


@app.post("/cortes/nuevo")
def new_cut(request: Request, name: str = Form(...), min_stock: str = Form(""),
            rotation: str = Form("FEFO"), consumption: str = Form("RECIPE"),
            csrf: str = Form(""), ctx=Depends(needs(perms.CATALOGUE)),
            session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        meat.create_cut(session, user, name, min_stock=_num(min_stock),
                        rotation=Rotation[rotation] if rotation in Rotation.__members__
                        else Rotation.FEFO,
                        consumption=ConsumptionMode[consumption]
                        if consumption in ConsumptionMode.__members__
                        else ConsumptionMode.RECIPE)
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/cortes", status_code=303)


@app.post("/cortes/{cut_id}/articulo")
def new_article(cut_id: int, request: Request, name: str = Form(...),
                supplier: str = Form(""), csrf: str = Form(""),
                ctx=Depends(needs(perms.CATALOGUE)), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    cut = _own(session, user, Ingredient, cut_id, request)
    try:
        meat.add_article(session, user, cut, name, supplier.strip() or None)
    except meat.MeatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/cortes", status_code=303)


# ================================================================ CARTA
@app.get("/carta", response_class=HTMLResponse)
def menu_page(request: Request, ctx=Depends(needs(perms.MENU)),
              session: Session = Depends(get_db), error: str = ""):
    user, auth_session = ctx
    return page(request, "menu.html", user, auth_session, session, error=error,
                rows=meat.menu(session, user.restaurant_id),
                cuts=meat.cuts(session, user.restaurant_id))


@app.post("/carta/nuevo")
def new_dish(request: Request, name: str = Form(...), cut_id: int = Form(...),
             grams: str = Form(...), sale_price: str = Form(""), vat_pct: str = Form("0"),
             pos_code: str = Form(""), pos_name: str = Form(""), csrf: str = Form(""),
             ctx=Depends(needs(perms.MENU)), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    try:
        meat.add_dish(session, user, name, cut_id, _num(grams, 0.0) or 0.0,
                      sale_price=_num(sale_price), vat_pct=_num(vat_pct, 0.0) or 0.0,
                      pos_code=pos_code, pos_name=pos_name, lang=lang)
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/carta", status_code=303)


# =============================================== OTROS INGREDIENTES DEL PLATO
@app.get("/ingredientes", response_class=HTMLResponse)
def extras_page(request: Request, ctx=Depends(needs(perms.MENU)),
                session: Session = Depends(get_db), saved: int = 0, error: str = ""):
    """Lo que acompaña a la carne. Aquí solo se configura su coste."""
    user, auth_session = ctx
    rows = meat.extras(session, user.restaurant_id)
    used = meat.extras_usage(session, user.restaurant_id)
    lang = lang_for(request, session, user)
    return page(request, "extras.html", user, auth_session, session, extras=rows,
                used=used, units=list(Unit), saved=bool(saved), error=error, lang=lang,
                cost_of=meat.extra_cost, portion_cost=meat.portion_cost,
                small=lambda unit: meat.small_unit(unit, lang))


@app.post("/ingredientes/nuevo")
def new_extra(request: Request, name: str = Form(...), unit: str = Form("KG"),
              cost: str = Form(""), portion: str = Form(""), csrf: str = Form(""),
              ctx=Depends(needs(perms.MENU)), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        meat.create_extra(session, user, name,
                          unit=Unit[unit] if unit in Unit.__members__ else Unit.KG,
                          cost=_num(cost), portion_g=_num(portion))
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/ingredientes?saved=1", status_code=303)


@app.post("/ingredientes/{ingredient_id}/coste")
def update_extra_cost(ingredient_id: int, request: Request, cost: str = Form(...),
                      portion: str = Form(""), csrf: str = Form(""),
                      ctx=Depends(needs(perms.MENU)),
                      session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        meat.set_extra_cost(session, user, ingredient_id, _num(cost, 0.0) or 0.0,
                            portion_g=_num(portion, 0.0))
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/ingredientes?saved=1", status_code=303)


# ============================================================= EMPLATADO
def _dish(session: Session, user: User, code: str, request: Request) -> Recipe:
    dish = (session.query(Recipe)
            .filter_by(restaurant_id=user.restaurant_id, code=code).first())
    if dish is None:
        raise HTTPException(status_code=404,
                            detail=i18n.t(lang_for(request, session, user), "error.tpl_not_found"))
    return dish


@app.get("/carta/{code}", response_class=HTMLResponse)
def plate_page(code: str, request: Request, ctx=Depends(needs(perms.MENU)),
               session: Session = Depends(get_db), error: str = ""):
    """El emplatado: todo lo que va en el plato y lo que cuesta cada cosa."""
    user, auth_session = ctx
    dish = _dish(session, user, code, request)
    lang = lang_for(request, session, user)
    return page(request, "plate.html", user, auth_session, session, error=error, lang=lang,
                plate=meat.plate(session, user.restaurant_id, dish, lang),
                extras=meat.extras(session, user.restaurant_id),
                cost_of=meat.extra_cost,
                small=lambda unit: meat.small_unit(unit, lang))


@app.post("/carta/{code}/linea")
def add_plate_line(code: str, request: Request, ingredient_id: int = Form(...),
                   qty: str = Form(...), waste_pct: str = Form("0"), csrf: str = Form(""),
                   ctx=Depends(needs(perms.MENU)), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    dish = _dish(session, user, code, request)
    try:
        meat.add_plate_line(session, user, dish, ingredient_id, _num(qty, 0.0) or 0.0,
                            waste_pct=_num(waste_pct, 0.0) or 0.0,
                            lang=lang_for(request, session, user))
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse(f"/carta/{code}", status_code=303)


@app.post("/carta/{code}/linea/{line_id}/quitar")
def remove_plate_line(code: str, line_id: int, request: Request, csrf: str = Form(""),
                      ctx=Depends(needs(perms.MENU)), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    dish = _dish(session, user, code, request)
    try:
        meat.remove_plate_line(session, user, dish, line_id,
                               lang=lang_for(request, session, user))
    except meat.MeatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse(f"/carta/{code}", status_code=303)


@app.post("/carta/{code}/gramos")
def set_plate_grams(code: str, request: Request, grams: str = Form(...), csrf: str = Form(""),
                    ctx=Depends(needs(perms.MENU)), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    dish = _dish(session, user, code, request)
    try:
        meat.set_plate_grams(session, user, dish, _num(grams, 0.0) or 0.0,
                             lang=lang_for(request, session, user))
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse(f"/carta/{code}", status_code=303)


# =============================================================== VENTAS
@app.get("/ventas", response_class=HTMLResponse)
def sales_page(request: Request, ctx=Depends(needs(perms.MENU)),
               session: Session = Depends(get_db), done: str = ""):
    user, auth_session = ctx
    return page(request, "sales.html", user, auth_session, session, done=done,
                mapping=(session.query(PosProduct).filter_by(restaurant_id=user.restaurant_id)
                         .order_by(PosProduct.pos_name).all()))


@app.post("/ventas")
async def import_sales(request: Request, ctx=Depends(needs(perms.MENU)),
                       session: Session = Depends(get_db)):
    """Lo vendido en el POS descuenta de cámara por rotación, plato a plato."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)

    sales: list[tuple[str, float]] = []
    for key, value in form.multi_items():
        if not key.startswith("units:") or not str(value).strip():
            continue
        try:
            units = _num(value, 0.0) or 0.0
        except ValueError:
            continue
        if units > 0:
            sales.append((key.split(":", 1)[1], units))
    on = form.get("business_date")
    result = costing.consume_sales(session, user, sales,
                                   on=date.fromisoformat(on) if on else None, lang=lang)
    summary = i18n.t(lang, "sale.done", n=result.lines, cost=f"{result.cost:.2f}")
    return RedirectResponse(f"/ventas?done={summary}", status_code=303)


# =========================================================== INVENTARIO
@app.get("/inventario", response_class=HTMLResponse)
def inventory_page(request: Request, ctx=Depends(needs(perms.COUNT)),
                   session: Session = Depends(get_db), done: str = ""):
    user, auth_session = ctx
    open_count = (session.query(MeatCount)
                  .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN).first())
    return page(request, "inventory.html", user, auth_session, session, done=bool(done),
                count=open_count, last=inventory.last_closed(session, user.restaurant_id),
                month=inventory.monthly_status(session, user.restaurant_id),
                periods=list(CountPeriod),
                items=(session.query(IngredientItem)
                       .filter_by(restaurant_id=user.restaurant_id, active=True)
                       .order_by(IngredientItem.name).all()))


@app.post("/inventario/abrir")
def open_inventory(request: Request, period: str = Form("MONTHLY"), csrf: str = Form(""),
                   ctx=Depends(needs(perms.INVENTORY)), session: Session = Depends(get_db)):
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
async def record_count(request: Request, ctx=Depends(needs(perms.COUNT)),
                       session: Session = Depends(get_db)):
    """Contar lo puede hacer cualquiera: se hace en la cámara, con la balanza."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    count = (session.query(MeatCount)
             .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN).first())
    if count is None:
        raise HTTPException(status_code=404, detail="")
    for key, value in form.multi_items():
        if not key.startswith("kg:") or not str(value).strip():
            continue
        serial = key.split(":", 1)[1]
        try:
            inventory.record(session, user, count, serial, _num(value, 0.0) or 0.0,
                             pieces=int(_num(form.get(f"pieces:{serial}"), 0) or 0) or None)
        except (inventory.InventoryError, ValueError):
            continue
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/cerrar")
def close_inventory(request: Request, csrf: str = Form(""), ctx=Depends(needs(perms.INVENTORY)),
                    session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    count = (session.query(MeatCount)
             .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN).first())
    if count is None:
        raise HTTPException(status_code=404, detail="")
    inventory.close_count(session, user, count, lang=lang_for(request, session, user))
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/cancelar")
def cancel_inventory(request: Request, reason: str = Form(""), csrf: str = Form(""),
                     ctx=Depends(needs(perms.INVENTORY)), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    count = (session.query(MeatCount)
             .filter_by(restaurant_id=user.restaurant_id, status=CountStatus.OPEN).first())
    if count is None:
        raise HTTPException(status_code=404, detail="")
    inventory.cancel_count(session, user, count, reason)
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/recuperar")
def recover_piece(request: Request, serial: str = Form(...), kg: str = Form(""),
                  note: str = Form(""), csrf: str = Form(""),
                  ctx=Depends(needs(perms.FIX)), session: Session = Depends(get_db)):
    """La pieza ha aparecido: vuelve al stock, con quién y por qué."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        inventory.recover(session, user, serial.strip(), kg=_num(kg),
                          note=note.strip() or None,
                          lang=lang_for(request, session, user))
    except (inventory.InventoryError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/inventario?done=1", status_code=303)


@app.post("/inventario/alta")
def adopt_piece(request: Request, serial: str = Form(...), item_id: int = Form(...),
                kg: float = Form(...), unit_cost: float = Form(...), expiry: str = Form(...),
                note: str = Form(""), csrf: str = Form(""),
                ctx=Depends(needs(perms.FIX)), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        inventory.adopt(session, user, serial.strip(), item_id, kg=kg, unit_cost=unit_cost,
                        expiry=date.fromisoformat(expiry), note=note.strip() or None,
                        lang=lang_for(request, session, user))
    except (inventory.InventoryError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/inventario?done=1", status_code=303)


# ================================================================ MERMA
def _waste_page(request, user, auth_session, session, *, result=None, error="", serial=""):
    names = {i.id: i.name for i in session.query(Ingredient)
             .filter_by(restaurant_id=user.restaurant_id).all()}
    return page(request, "waste.html", user, auth_session, session,
                result=result, error=error, serial=serial, names=names,
                recent=waste.recent(session, user.restaurant_id),
                ingredients=meat.cuts(session, user.restaurant_id))


@app.get("/merma", response_class=HTMLResponse)
def waste_page(request: Request, ctx=Depends(needs(perms.WASTE)),
               session: Session = Depends(get_db), serial: str = ""):
    user, auth_session = ctx
    return _waste_page(request, user, auth_session, session, serial=serial)


@app.post("/merma", response_class=HTMLResponse)
def record_waste(request: Request, kg: str = Form(...), serial: str = Form(""),
                 ingredient_id: str = Form(""), pieces: str = Form(""), reason: str = Form(""),
                 csrf: str = Form(""), ctx=Depends(needs(perms.WASTE)),
                 session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        result = waste.record(
            session, user, kg=_num(kg, 0.0) or 0.0, serial=serial.strip() or None,
            ingredient_id=int(ingredient_id) if ingredient_id.strip() else None,
            pieces=int(pieces) if pieces.strip() else None,
            reason=reason.strip() or None, lang=lang_for(request, session, user))
    except (waste.WasteError, ValueError) as e:
        return _waste_page(request, user, auth_session, session, error=str(e),
                           serial=serial.strip())
    return _waste_page(request, user, auth_session, session, result=result)


# ========================================================= TRAZABILIDAD
@app.get("/trazabilidad", response_class=HTMLResponse)
def tracing_page(request: Request, ctx=Depends(needs(perms.STOCK)),
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


# ====================================================== LA PLATAFORMA
@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, ctx=Depends(require_owner),
               session: Session = Depends(get_db), done: str = ""):
    """La consola del dueño: solicitudes, casas y el recibo del mes."""
    user, auth_session = ctx
    rows = billing.requests(session)
    privacy.note_access(session, user, len(rows))     # mirar datos deja huella
    return page(request, "admin.html", user, auth_session, session, done=done,
                requests=privacy.readable(rows),
                accounts=billing.accounts(session),
                statuses=list(RequestStatus), plans=list(Plan),
                trial_left=billing.trial_left,
                mail_ready=mailer.configured(),
                encryption_on=privacy.encryption_on(),
                retention_days=privacy.RETENTION_DAYS)


@app.post("/admin/solicitud/{request_id}/borrar")
def erase_request(request_id: int, request: Request, csrf: str = Form(""),
                  ctx=Depends(require_owner), session: Session = Depends(get_db)):
    """Derecho de supresión: se borra la solicitud y queda que se borró."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    privacy.erase_request(session, user, request_id)
    return RedirectResponse("/admin?done=1", status_code=303)


@app.get("/admin/solicitud/{request_id}/datos")
def export_request(request_id: int, request: Request, ctx=Depends(require_owner),
                   session: Session = Depends(get_db)):
    """Derecho de portabilidad: todo lo que guardamos de esa persona."""
    user, auth_session = ctx
    row = session.get(AccessRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="")
    privacy.audit(session, user, f"request:{request_id}", "EXPORTED", row.restaurant_name)
    return JSONResponse(privacy.export_request(row))


@app.post("/admin/solicitudes/purgar")
def purge_requests(request: Request, csrf: str = Form(""), ctx=Depends(require_owner),
                   session: Session = Depends(get_db)):
    """Lo que no llegó a cuenta no se guarda para siempre."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    privacy.purge(session, user)
    return RedirectResponse("/admin?done=1", status_code=303)


@app.post("/admin/solicitud/{request_id}/estado")
def set_request_status(request_id: int, request: Request, status: str = Form(...),
                       csrf: str = Form(""), ctx=Depends(require_owner),
                       session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    if status in RequestStatus.__members__:
        billing.set_request_status(session, request_id, RequestStatus[status])
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/casa")
async def create_account(request: Request, ctx=Depends(require_owner),
                         session: Session = Depends(get_db)):
    """Da de alta la casa y la cuenta de su manager. Es el único camino."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    plan = form.get("plan")
    try:
        restaurant, _ = billing.create_account(
            session,
            name=(form.get("name") or "").strip(),
            manager_name=(form.get("manager_name") or "").strip(),
            manager_email=(form.get("manager_email") or "").strip(),
            password=form.get("password") or "",
            plan=Plan[plan] if plan in Plan.__members__ else Plan.SINGLE,
            outlets=int(_num(form.get("outlets"), 1) or 1),
            monthly_fee=_num(form.get("monthly_fee")),
            language=(form.get("language") or "es"),
            request_id=int(form.get("request_id")) if (form.get("request_id") or "").strip() else None,
            legal_name=(form.get("legal_name") or "").strip(),
            tax_number=(form.get("tax_number") or "").strip(),
            address=(form.get("address") or "").strip(),
            country=(form.get("country") or "").strip(),
            contact_name=(form.get("contact_name") or "").strip(),
            contact_phone=(form.get("contact_phone") or "").strip(),
            billing_email=(form.get("billing_email") or "").strip())
    except (billing.BillingError, auth.AuthError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    billing.audit(session, user, restaurant.id, restaurant.slug, "CREATED", restaurant.name)
    return RedirectResponse("/admin?done=1", status_code=303)


@app.post("/admin/casa/{restaurant_id}/pago")
def set_billing(restaurant_id: int, request: Request, action: str = Form(...),
                note: str = Form(""), paid_until: str = Form(""), csrf: str = Form(""),
                ctx=Depends(require_owner), session: Session = Depends(get_db)):
    """La pestaña del recibo: pagado, fallado o bloqueado."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None or restaurant.platform:
        raise HTTPException(status_code=404, detail="")
    try:
        if action == "paid":
            billing.mark_paid(session, user, restaurant,
                              until=date.fromisoformat(paid_until) if paid_until.strip() else None,
                              note=note.strip() or None)
        elif action == "past_due":
            billing.mark_unpaid(session, user, restaurant, block=False, note=note.strip() or None)
        elif action == "block":
            billing.mark_unpaid(session, user, restaurant, block=True, note=note.strip() or None)
        else:
            raise HTTPException(status_code=400, detail="")
    except (billing.BillingError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/admin?done=1", status_code=303)


@app.post("/admin/casa/{restaurant_id}/pasarela")
def attach_payment(restaurant_id: int, request: Request, provider: str = Form("stripe"),
                   reference: str = Form(...), brand: str = Form(""), last4: str = Form(""),
                   expiry: str = Form(""), csrf: str = Form(""),
                   ctx=Depends(require_owner), session: Session = Depends(get_db)):
    """Apunta el método de pago que devolvió la pasarela y arranca la prueba."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None or restaurant.platform:
        raise HTTPException(status_code=404, detail="")
    try:
        billing.attach_payment_method(session, user, restaurant, provider=provider,
                                      reference=reference, brand=brand, last4=last4,
                                      expiry=expiry)
    except billing.BillingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/admin?done=1", status_code=303)


@app.post("/admin/casa/{restaurant_id}/cancelar")
def cancel_account(restaurant_id: int, request: Request, reason: str = Form(""),
                   csrf: str = Form(""), ctx=Depends(require_owner),
                   session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None or restaurant.platform:
        raise HTTPException(status_code=404, detail="")
    billing.cancel(session, user, restaurant, reason.strip() or None)
    return RedirectResponse("/admin?done=1", status_code=303)


# ======================================================== AVISOS Y EQUIPO
@app.get("/manager/alertas", response_class=HTMLResponse)
def alerts_page(request: Request, ctx=Depends(require_manager_user),
                session: Session = Depends(get_db), days: int = 14):
    user, auth_session = ctx
    since = date.today() - timedelta(days=days)
    rows = (session.query(Alert)
            .filter(Alert.restaurant_id == user.restaurant_id,
                    Alert.created_at >= datetime.combine(since, datetime.min.time()))
            .order_by(Alert.acknowledged_at.is_(None).desc(), Alert.created_at.desc())
            .limit(200).all())
    return page(request, "alerts.html", user, auth_session, session, alerts=rows, days=days)


@app.post("/manager/alertas/{alert_id}/cerrar")
def close_alert(alert_id: int, request: Request, resolution: str = Form(...),
                csrf: str = Form(""), ctx=Depends(require_manager_user),
                session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        service.acknowledge_alert(session, user, alert_id, resolution,
                                  lang=lang_for(request, session, user))
    except (ValueError, service.ValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/manager/alertas", status_code=303)


@app.get("/manager/equipo", response_class=HTMLResponse)
def team_page(request: Request, ctx=Depends(needs(perms.TEAM)),
              session: Session = Depends(get_db), error: str = ""):
    user, auth_session = ctx
    restaurant = session.get(Restaurant, user.restaurant_id)
    rows = (session.query(User).filter_by(restaurant_id=user.restaurant_id)
            .order_by(User.name).all())
    # Un manager no reparte su propio nivel ni el de la plataforma.
    roles = [r for r in Role if r not in (Role.OWNER, Role.MANAGER)] \
        if user.role != Role.OWNER else list(Role)
    return page(request, "team.html", user, auth_session, session, error=error,
                restaurant=restaurant, rows=rows, roles=roles)


@app.post("/manager/equipo/nueva")
def create_team_user(request: Request, name: str = Form(...), email: str = Form(...),
                     password: str = Form(...), role: str = Form("BUTCHER"),
                     csrf: str = Form(""), ctx=Depends(needs(perms.TEAM)),
                     session: Session = Depends(get_db)):
    """El manager da de alta a su gente: una cuenta, un nivel, una contraseña."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        billing.create_user(session, user, name=name, email=email, password=password,
                            role=Role[role] if role in Role.__members__ else Role.BUTCHER,
                            language=lang_for(request, session, user))
    except (billing.BillingError, auth.AuthError, ValueError) as e:
        return RedirectResponse(f"/manager/equipo?error={e}", status_code=303)
    return RedirectResponse("/manager/equipo", status_code=303)


@app.post("/manager/equipo/{user_id}/rol")
def change_role(user_id: int, request: Request, role: str = Form(...), csrf: str = Form(""),
                ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    target = _own(session, user, User, user_id, request)
    if target.id == user.id:
        raise HTTPException(status_code=400, detail=i18n.t(lang, "error.no_self_role"))
    if role in Role.__members__:
        target.role = Role[role]
    return RedirectResponse("/manager/equipo", status_code=303)


@app.post("/manager/equipo/{user_id}/activar")
def toggle_user(user_id: int, request: Request, csrf: str = Form(""),
                ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    target = _own(session, user, User, user_id, request)
    if target.id == user.id:
        raise HTTPException(status_code=400, detail=i18n.t(lang, "error.no_self_disable"))
    target.active = not target.active
    return RedirectResponse("/manager/equipo", status_code=303)


@app.get("/notificaciones", response_class=HTMLResponse)
def notifications_page(request: Request, ctx=Depends(require_user),
                       session: Session = Depends(get_db)):
    user, auth_session = ctx
    rows = service.recent_notifications(session, user.id)
    response = page(request, "notifications.html", user, auth_session, session,
                    notifications=rows)
    service.mark_all_read(session, user.id)
    return response


@app.get("/api/notificaciones")
def notifications_api(request: Request, ctx=Depends(require_user),
                      session: Session = Depends(get_db)):
    user, _ = ctx
    rows = service.recent_notifications(session, user.id, limit=5)
    return JSONResponse({"unread": service.unread_count(session, user.id),
                         "items": [{"title": n.title, "body": n.body,
                                    "severity": n.severity.value, "read": n.read_at is not None}
                                   for n in rows]})


# ========================================================= CONFIGURACIÓN
@app.get("/configuracion", response_class=HTMLResponse)
def settings_page(request: Request, ctx=Depends(require_user),
                  session: Session = Depends(get_db), saved: int = 0):
    user, auth_session = ctx
    restaurant = session.get(Restaurant, user.restaurant_id)
    return page(request, "settings.html", user, auth_session, session,
                restaurant=restaurant, saved=bool(saved), pos_modes=list(PosMatch))


@app.post("/configuracion")
def save_settings(request: Request, language: str = Form(...),
                  restaurant_language: str = Form(""), pos_match: str = Form(""),
                  csrf: str = Form(""), ctx=Depends(require_user),
                  session: Session = Depends(get_db)):
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    if i18n.is_supported(language):
        user.language = language
    if user.role == Role.MANAGER:
        restaurant = session.get(Restaurant, user.restaurant_id)
        if restaurant is not None:
            if restaurant_language and i18n.is_supported(restaurant_language):
                restaurant.language = restaurant_language
            if pos_match in PosMatch.__members__:
                restaurant.pos_match = PosMatch[pos_match]
    response = RedirectResponse("/configuracion?saved=1", status_code=303)
    return set_lang_cookie(response, user.language or i18n.DEFAULT_LANG)


# ============================================================ DESCARGAS
@app.get("/descargas", response_class=HTMLResponse)
def downloads_page(request: Request, ctx=Depends(require_user),
                   session: Session = Depends(get_db)):
    user, auth_session = ctx
    return page(request, "downloads_meat.html", user, auth_session, session,
                sheets=sheets_meat.SHEETS)


@app.get("/descargas/{code}.xlsx")
def download_sheet(code: str, request: Request, ctx=Depends(require_user),
                   session: Session = Depends(get_db)):
    user, auth_session = ctx
    restaurant = session.get(Restaurant, user.restaurant_id)
    lang = lang_for(request, session, user)
    try:
        payload = sheets_meat.workbook(code, restaurant, lang)
    except KeyError:
        raise HTTPException(status_code=404, detail="") from None
    return Response(payload, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{sheets_meat.filename(code, lang)}"'})


@app.get("/healthz")
def healthz():
    return {"status": "ok", "edition": "meat"}


def create_app(database_url: str = "sqlite:///carnes.db") -> FastAPI:
    db.init_engine(database_url)
    db.create_all()
    return app
