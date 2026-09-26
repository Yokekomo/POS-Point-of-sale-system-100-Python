"""[00173] Control de carnes. Una sola puerta: la carne.

Misma casa que la plataforma de cocina y el mismo motor probado, pero con las
pantallas que una cocina de carne necesita y sin nada más: recepción de
primales, despiece, cámara, descongelado, inventario, merma, trazabilidad y la
carta de carnes atada al POS.

Corre por su cuenta, con su propia base de datos:

    python -m thegrill.cli --db sqlite:///carnes.db serve-carne --port 8001
"""
import json
import logging
import random
import os
from urllib.parse import quote, urlsplit
from datetime import date, datetime, timedelta, timezone
import zoneinfo
from dataclasses import fields, is_dataclass
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import UploadFile   # el de request.form()
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from thegrill import config, db, version
from thegrill.meat import (billing, bugs, exportar, gateway, mailer, novedades, perms,
                           privacy, security, tarifa, tours, tutorial)
from thegrill.meat import service as meat
from thegrill.meat import sheets_meat
from thegrill.models import (AccessRequest, Alert, Billing, ConsumptionMode, CountPeriod,
                             CountStatus, Ingredient, IngredientItem, IngredientLot,
                             MeatCount, Plan, PosMatch, PosProduct, Primal, PrimalPar,
                             PrimalStatus, Recipe, RequestStatus, Restaurant, Role,
                             Rotation, Site, SiteKind, Storage, Unit, User)
from thegrill.models import BugStatus
from thegrill.web import (aging, auth, butchery, caducidad, cifras, costing, cuadre,
                          defrost, exacto, i18n, impuestos, inventory, jornada,
                          money, pesos, pos_import, rangos, service, sites,
                          tracing, twofactor, waste)

log = logging.getLogger(__name__)

MEAT_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")
KITCHEN_TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                 "web", "templates")
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# [00341] El aviso de cookies se recuerda en una cookie técnica, no en el navegador: el
# almacenamiento del navegador se borra al cerrar, se bloquea en algunas
# configuraciones y no viaja entre ventanas, así que el aviso volvía a salir una
# y otra vez. Una cookie de un año, sin nada dentro más que un uno.
COOKIE_NOTICE = "grill_cookies"
NOTICE_YEAR = 365 * 24 * 3600
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
PHOTO_DIR = os.path.join(STATIC_DIR, "fotos")
# [00342] Las fotos de las etiquetas no van en /static: ahí las vería cualquiera que
# acertara la dirección. Se guardan fuera y se sirven por una ruta que primero
# mira de qué casa es quien las pide.
UPLOAD_DIR = os.environ.get("GRILL_UPLOAD_DIR", "uploads")

# [00343] Las zonas horarias que existen, para el desplegable de la configuración y
# para comprobar lo que llega. Se calcula una vez al arrancar: son unas
# seiscientas y no cambian mientras el programa está en marcha.
ZONAS = sorted(zoneinfo.available_timezones())
# [00344] Las fotos de la portada. Se llaman así y se dejan caer en esa carpeta; la
# portada usa las que encuentre y se arregla sin las que falten.
PHOTO_SLOTS = ("primal", "cortes", "plato")

# [00345] Las plantillas propias mandan; lo que no esté aquí se hereda de la cocina.
templates = Jinja2Templates(directory=[MEAT_TEMPLATES, KITCHEN_TEMPLATES])
templates.env.filters["ceil_pct"] = butchery.ceil_pct
# [00346] Los decimales, con el separador del idioma de la casa: «9,400 kg» en
# español y «9.400 kg» en inglés, en las doscientas y pico cifras que salen
# por pantalla, sin tocar ninguna plantilla.
cifras.enganchar(templates.env)
# [00347] Sin documentación automática: /docs y /openapi.json enseñaban el mapa entero
# de la aplicación a cualquiera que pasara por ahí.
app = FastAPI(title="Control de carnes", docs_url=None, redoc_url=None, openapi_url=None)
os.makedirs(PHOTO_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------- utilidades
def get_db():
    """[00174] Una sesión con la base para cada petición, que se cierra al acabar."""
    with db.session_scope() as session:
        yield session


def current(request: Request, session: Session):
    """[00175] Quién está dentro, según la cookie. Nadie, si no hay sesión válida."""
    return auth.resolve_session(session, request.cookies.get(auth.COOKIE_NAME))


def lang_for(request: Request, session: Session | None = None, user: User | None = None) -> str:
    """[00176] En qué idioma se le contesta a esta petición.

    Por orden: lo que eligió esa persona, la cookie, lo que pide el navegador y
    el idioma de la casa.
    """
    restaurant_lang = None
    if session is not None and user is not None:
        restaurant = session.get(Restaurant, user.restaurant_id)
        restaurant_lang = restaurant.language if restaurant else None
    return i18n.resolve(user_lang=user.language if user else None,
                        cookie=request.cookies.get(i18n.COOKIE_NAME),
                        accept_header=request.headers.get("accept-language"),
                        restaurant_lang=restaurant_lang)


# [00348] La cola del teléfono manda esta cabecera en todo lo que reenvía. A ella hay
# que contestarle con un número, no con una redirección: un `fetch` sigue la
# redirección solo, recibe el 200 de la pantalla de entrar y da por guardado lo
# que no se guardó. Así se perdían recuentos enteros sin que nadie se enterara.
CABECERA_COLA = "x-cola"


def _de_la_cola(request: Request) -> bool:
    """[00177] Si esto viene de la cola del teléfono y no de alguien mirando la pantalla.

    Se contesta distinto: a una persona se la manda a entrar; a la cola, un
    error seco, que no hay pantalla que enseñar.
    """
    return request.headers.get(CABECERA_COLA) == "1"


def require_user(request: Request, session: Session = Depends(get_db)):
    """[00178] La puerta: hay que estar dentro, y la casa no puede estar bloqueada.

    Una cuenta bloqueada puede entrar, ver por qué y salir, y nada más. El
    dueño de la plataforma entra siempre: es quien lo tiene que desbloquear.
    """
    found = current(request, session)
    if found is None:
        if _de_la_cola(request):
            raise HTTPException(status_code=401, detail="")
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user, _ = found
    restaurant = session.get(Restaurant, user.restaurant_id)
    # [00349] Una cuenta bloqueada no trabaja. Se puede entrar, ver por qué y salir.
    if restaurant is not None and restaurant.blocked and user.role != Role.OWNER:
        if _de_la_cola(request):
            raise HTTPException(status_code=402, detail="")
        raise HTTPException(status_code=303, headers={"Location": "/cuenta"})
    return found


def require_manager_user(request: Request, session: Session = Depends(get_db)):
    """[00179] Solo para quien lleva la casa."""
    user, auth_session = require_user(request, session)
    if user.role != Role.MANAGER:
        raise HTTPException(status_code=403,
                            detail=i18n.t(lang_for(request, session, user), "error.managers_only"))
    return user, auth_session


def require_manager_even_blocked(request: Request, session: Session = Depends(get_db)):
    """[01684] Quien lleva la casa, aunque la casa esté bloqueada o dada de baja.

    Es la única puerta del programa que se abre con la cuenta cerrada, y tiene
    que serlo. `require_user` manda a `/cuenta` a la casa bloqueada, que es lo
    correcto para todo lo demás —una cuenta sin pagar no trabaja—, pero aplicado
    a los datos significa que el cliente pierde el mismo día sus recepciones,
    sus despieces y sus inventarios: los papeles que le piden en una inspección
    y que la ley le obliga a él a poder enseñar. Retenerlos no es una palanca de
    cobro, es quedarse con algo que no es nuestro.

    Sigue haciendo falta ser el manager de esa casa y haber entrado: no se abre
    la mano en quién, solo en cuándo.
    """
    found = current(request, session)
    if found is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user, auth_session = found
    if user.role != Role.MANAGER:
        raise HTTPException(
            status_code=403,
            detail=i18n.t(lang_for(request, session, user), "error.managers_only"))
    return user, auth_session


def require_owner(request: Request, session: Session = Depends(get_db)):
    """[00180] Solo para el dueño de la plataforma.

    A los demás se les contesta que no existe, no que no pueden: ni se insinúa
    que haya una pantalla ahí detrás.
    """
    found = current(request, session)
    if found is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    user, auth_session = found
    if user.role != Role.OWNER:
        raise HTTPException(status_code=404, detail="")   # ni se insinúa que existe
    return user, auth_session


def needs(capability: str):
    """[00181] Depende de poder hacer eso. La puerta se cierra aquí, no en la plantilla.

    Una barra sin enlace no es una puerta cerrada: si no se comprueba en la
    ruta, basta escribir la dirección a mano para entrar.
    """
    def dependency(request: Request, session: Session = Depends(get_db)):
        """[00338] Comprueba que esa persona puede hacer eso antes de dejarla pasar."""
        user, auth_session = require_user(request, session)
        if not perms.can(user, capability):
            raise HTTPException(
                status_code=403,
                detail=i18n.t(lang_for(request, session, user), "error.not_your_level"))
        return user, auth_session
    return dependency


def page(request: Request, name: str, user: User | None = None, auth_session=None,
         session: Session | None = None, **ctx):
    """[00182] Pinta una pantalla con todo lo que las plantillas dan por hecho.

    El usuario, el testigo del formulario, el idioma, lo que puede hacer y —lo
    que más se olvida— el día de trabajo de la casa, que no es el del
    servidor: es lo que se propone en cada casilla de fecha.
    """
    lang = ctx.pop("lang", None) or lang_for(request, session, user)
    base = {"user": user, "csrf": auth_session.csrf if auth_session else "",
            "cookies_seen": bool(request.cookies.get(COOKIE_NOTICE)),
            # [00350] El día de trabajo de la casa, no el del servidor: es lo que se
            # propone en cada campo de fecha y lo que se pinta en cada pantalla.
            "today": jornada.hoy(session, user.restaurant_id if user else None).isoformat(),
            "can": perms.checker(user),
            "here": request.url.path,
            "nonce": getattr(request.state, "nonce", ""),
            "unread": service.unread_count(session, user.id) if (user and session) else 0,
            "t": i18n.translator(lang), "lang": lang, "dir": i18n.direction(lang),
            "languages": i18n.LANGUAGES,
            # [00351] El símbolo de la moneda de la casa. Va en el nombre de cada
            # columna y de cada recuadro de dinero, igual que los kilos: un
            # número suelto no dice si son euros o dólares.
            "moneda": money.simbolo(_moneda_de(session, user)),
            # [01627] [00351b] Y cómo se parte lo que se gana: lo que hay que apartar
            # para Hacienda y lo que queda limpio. Va aquí, con la moneda, por
            # lo mismo: es cómo habla de dinero esta casa, y así ninguna
            # pantalla que enseñe un margen tiene que acordarse de pedirlo.
            # Sin tipo puesto, el reparto lo dice y no se pinta nada.
            "reparto": _reparto_de(session, user),
            # [01648] Y el IVA de las compras, que es el otro: el que se descuenta.
            "soportado": impuestos.soportado_de,
            # [00352] El tutorial de esta pantalla, si toca. Los pasos vienen ya
            # traducidos y ya filtrados por nivel: lo que no le toca a esta
            # persona no llega al navegador.
            "tour": tutorial.para(session, user, request.url.path, lang,
                                  forzar=request.query_params.get("tour") == "1"),
            "tour_aqui": tours.RUTAS.get(request.url.path.rstrip("/") or "/")}
    base.update(ctx)
    # [00353] El recado de lo último que se guardó, si lo hay. Se enseña una vez y se
    # borra: la pantalla que lo enseña es una de verdad —se llegó a ella con
    # una redirección— y recargarla no vuelve a mandar nada.
    if auth_session is not None and (getattr(auth_session, "flash", None)
                                     or getattr(auth_session, "flash_data", None)):
        try:
            guardado = json.loads(auth_session.flash_data or "{}")
        except ValueError:                                   # pragma: no cover
            guardado = {}
        # [00354] Y solo en la pantalla a la que iba. Si de la recepción se salta al
        # despiece sin pasar por la recepción, el recado de la pieza que se
        # acaba de dar de alta no tiene nada que hacer en la hoja del
        # despiece: se tira. Enseñarlo donde caiga es peor que perderlo.
        donde = guardado.pop(DONDE_IBA, None)
        if donde is None or donde == request.url.path:
            if auth_session.flash and not base.get("done"):
                base["done"] = auth_session.flash
            for nombre, valor in guardado.items():
                if not base.get(nombre):
                    base[nombre] = _objeto(valor)
        auth_session.flash = None
        auth_session.flash_data = None
    return templates.TemplateResponse(request, name, base)


def _reparto_de(session: Session | None, user: User | None):
    """[01626] Cómo parte esta casa un margen: lo de Hacienda y lo que queda.

    Devuelve una función para que cada pantalla la llame con el margen que
    tenga delante —el de una pieza, el de un corte, el del día— sin volver a
    preguntar por la casa. Sin casa o sin tipo puesto, el reparto que sale
    dice que no hay impuesto y las pantallas no pintan nada.
    """
    restaurant = (session.get(Restaurant, user.restaurant_id)
                  if (session and user) else None)
    return lambda margen: impuestos.de_la_casa(restaurant, margen)


def _moneda_de(session: Session | None, user: User | None) -> str | None:
    """[00183] En qué moneda trabaja esta casa. Sin casa, la de por defecto."""
    if not (session and user):
        return None
    restaurant = session.get(Restaurant, user.restaurant_id)
    return restaurant.currency if restaurant else None


def is_https(request: Request) -> bool:
    """[00184] Si la petición llegó por HTTPS, mirando también lo que dice el proxy."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def set_session_cookie(response: Response, token: str) -> Response:
    """[00185] Deja la cookie de sesión, cerrada a cal y canto.

    No la lee el javascript, no viaja desde otra página y solo va por https.
    Esto es un programa de trabajo: nadie llega aquí desde fuera.
    """
    secure = os.environ.get("GRILL_INSECURE_COOKIE") != "1"
    # [00355] `strict`: la cookie no viaja en peticiones que vengan de otro sitio, ni
    # siquiera al pinchar un enlace. Es un programa de trabajo, no una red
    # social: nadie llega aquí desde fuera y necesita estar dentro al llegar.
    response.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="strict",
                        secure=secure, max_age=auth.SESSION_DAYS * 86400, path="/")
    return response


def set_lang_cookie(response: Response, lang: str) -> Response:
    """[00186] Recuerda el idioma elegido durante un año."""
    response.set_cookie(i18n.COOKIE_NAME, lang, httponly=False, samesite="lax",
                        max_age=365 * 86400, path="/")
    return response


def _guard(request, session, user, auth_session, csrf: str) -> None:
    """[00187] Comprueba que el formulario salió de nuestra pantalla. Si no, no pasa."""
    try:
        auth.check_csrf(auth_session, csrf, lang_for(request, session, user))
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None


def _ya_estaba(request, session, user, envio: str, destino: str):
    """[00188] Si este envío ya se aplicó, se contesta que sí y no se toca nada.

    El teléfono que estuvo sin cobertura reintenta, y a veces el intento de
    antes sí había entrado: se perdió la respuesta, no la escritura. Devolver
    aquí la misma pantalla de siempre es lo que hace que reintentar sea gratis.
    """
    if not (envio or "").strip():
        return None
    if security.first_time(session, envio, restaurant_id=user.restaurant_id,
                           user_id=user.id, path=str(request.url.path)):
        return None
    return RedirectResponse(destino, status_code=303)


def _own(session: Session, user: User, model, obj_id: int, request: Request):
    """[00189] Trae una fila comprobando que es de esta casa.

    Sin esto, cambiar un número en la dirección enseña —o borra— lo del
    vecino. Y se contesta «no existe», que es lo que tiene que parecer.
    """
    row = session.get(model, obj_id)
    if row is None or row.restaurant_id != user.restaurant_id:
        raise HTTPException(status_code=404,
                            detail=i18n.t(lang_for(request, session, user), "error.other_restaurant"))
    return row


def _num(raw: str | None, default: float | None = None,
         decimales: int = exacto.GRAMOS_DECIMALES) -> float | None:
    """[00190] Los teclados de cocina escriben comas, y a veces escriben los miles.

    `decimales` dice qué mide el campo: 3 un peso, 2 un precio. Con eso
    `exacto.leer` sabe si «1.250» es kilo y cuarto o mil doscientos cincuenta,
    que es la única duda que tiene un número escrito a mano.
    """
    return exacto.leer(raw, default, decimales)


# [00356] La marca con la que un objeto viaja como texto sin dejar de ser un objeto.
CAMPOS_DE_UN_OBJETO = "__campos__"
DONDE_IBA = "__pantalla__"


def _en_texto(valor):
    """[00191] Un resultado entero, para que viaje de una pantalla a la siguiente.

    Sus campos y **también lo que calcula**. Copiar solo los campos guardados
    deja fuera lo que la clase saca al vuelo —el margen de una venta, los
    kilos perdidos de un recuento—, que es justo lo que la pantalla pide. Aquí
    van los dos, porque lo que se enseña es el conjunto.
    """
    if is_dataclass(valor) and not isinstance(valor, type):
        # [00357] La marca dice «esto era un objeto»: al volver hay que montarlo como
        # tal, porque la pantalla le pide sus campos con un punto. Un
        # diccionario de los de siempre —lo del camión, por ejemplo— viaja
        # como diccionario y se pide con `.get`, y confundir los dos rompe la
        # mitad de las plantillas.
        datos = {CAMPOS_DE_UN_OBJETO: True}
        datos.update({campo.name: _en_texto(getattr(valor, campo.name))
                      for campo in fields(valor)})
        for nombre in dir(type(valor)):
            if nombre.startswith("_") or nombre in datos:
                continue
            if isinstance(getattr(type(valor), nombre, None), property):
                try:
                    datos[nombre] = _en_texto(getattr(valor, nombre))
                except Exception:                            # noqa: BLE001
                    continue          # una cuenta que no sale no para la pantalla
        return datos
    if isinstance(valor, (list, tuple)):
        return [_en_texto(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _en_texto(v) for k, v in valor.items()}
    return valor


def _objeto(valor):
    """[00192] Vuelve a montar lo que viajó como texto, para que la plantilla no note nada.

    Un diccionario se convierte en algo a lo que se le pueden pedir sus campos
    con un punto —`pesada.kg`— porque es así como están escritas las
    plantillas, y no tiene sentido reescribirlas todas para esto.
    """
    if isinstance(valor, dict):
        limpio = {k: _objeto(v) for k, v in valor.items() if k != CAMPOS_DE_UN_OBJETO}
        return SimpleNamespace(**limpio) if valor.get(CAMPOS_DE_UN_OBJETO) else limpio
    if isinstance(valor, list):
        return [_objeto(v) for v in valor]
    return valor


def _hecho(auth_session, destino: str, recado: str = "",
           **estado) -> RedirectResponse:
    """[00193] Guardado: se deja el recado y se manda a la pantalla, que es otra cosa.

    Contestar a un POST con la pantalla entera parece lo más corto y es lo que
    hace que recargar vuelva a mandar el formulario: el navegador pregunta si
    quieres reenviarlo y esa pregunta, con una pieza en la mano y guantes
    puestos, no la sabe contestar nadie. Se contesta con una redirección, la
    pantalla se pide de nuevo —una petición que se puede recargar mil veces— y
    lo que había que decir viaja aparte.
    """
    if auth_session is not None:
        auth_session.flash = recado or None
        # [00358] Lo que no cabe en una frase viaja aparte. `default=str` es para las
        # fechas: van y vuelven como texto, que es como las pinta la pantalla.
        # Y con ello va a qué pantalla iba, para que no se enseñe en otra.
        estado[DONDE_IBA] = urlsplit(destino).path
        auth_session.flash_data = json.dumps(estado, default=str)
    return RedirectResponse(destino, status_code=303)


def _cuando(form, session, user) -> date:
    """[00194] El día de trabajo del momento en que se **escribió** el apunte.

    La cola del teléfono sella cada cosa con la hora a la que se tecleó. Sin
    leer ese sello, un recuento apuntado a las 23:50 dentro de la cámara y
    mandado a las 00:10 —cuando el teléfono vuelve a tener señal— quedaba
    fechado al día siguiente: el turno de noche entero cambiaba de día.

    Se le pasa el formulario entero o el sello suelto, según lo que tenga a
    mano cada ruta.
    """
    cuando = form.get("cuando") if hasattr(form, "get") else form
    return jornada.apuntado(session, user, str(cuando) if cuando else None)


def _eur(raw: str | None, default: float | None = None) -> float | None:
    """[00195] Un precio. Como el dinero lleva dos decimales y no tres, «1.250» aquí
    son mil doscientos cincuenta euros y no un euro con veinticinco."""
    return _num(raw, default, exacto.CENTIMOS_DECIMALES)


def _dicho(e: Exception, lang: str = "es") -> str:
    """[00196] Lo que se le enseña a quien acaba de escribir algo que no se entiende.

    Un «4 C» en la casilla de la temperatura tumbaba la recepción entera con
    un error 500 y una pantalla en blanco en inglés, con el camión en el muelle
    y la hoja por volver a teclear. Un número mal escrito no es un fallo del
    programa: es una tecla de al lado, y se contesta enseñando lo que se
    escribió y en el idioma de la casa.
    """
    if isinstance(e, pesos.ConDecimales):
        # [00359] El aviso trae la cifra que hay que escribir, no solo la queja: quien
        # está delante tiene la pieza en la mano y no va a echar la cuenta.
        return i18n.t(lang, "valid.grams_have_no_decimals", value=e.escrito,
                      kg=f"{e.como_kilos:.10g}", g=e.gramos)
    if isinstance(e, exacto.NoEsUnNumero):
        return i18n.t(lang, "valid.not_a_number_value", value=e.escrito)
    return str(e)


def _g(raw: str | None, default: float | None = None) -> float | None:
    """[00197] Gramos y unidades: no llevan decimales, así que «1.250» son mil
    doscientos cincuenta gramos y no un gramo y cuarto."""
    return _num(raw, default, 0)


@app.middleware("http")
async def respuestas_para_la_cola(request: Request, call_next):
    """[00198] A la cola del teléfono se le contesta con números, no con redirecciones.

    Casi todas las rutas acaban en un 303 a la pantalla siguiente, que es lo
    correcto para un navegador. Para un `fetch` es un problema: si lo sigue,
    recibe el 200 de la pantalla de destino —también cuando ese destino es la
    de entrar— y no hay manera de distinguir «guardado» de «tu sesión ha
    caducado». Y si no lo sigue, el navegador devuelve una respuesta opaca sin
    cabeceras, así que tampoco se puede mirar adónde iba.

    Se resuelve aquí, una vez, y no en cada ruta: al que manda la cabecera de
    la cola se le traduce el 303 a un número que dice lo que pasó. Un 204 es
    «hecho, no hay nada que enseñar»; el 401 y el 402, «vuelve a entrar» y «la
    casa está bloqueada».
    """
    response = await call_next(request)
    if not _de_la_cola(request) or response.status_code not in (302, 303, 307):
        return response
    destino = response.headers.get("location", "")
    if destino.startswith("/login"):
        return Response(status_code=401)
    if destino.startswith("/cuenta"):
        return Response(status_code=402)
    return Response(status_code=204)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """[00199] Las cabeceras van en todas las respuestas, también en las de error."""
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
    """[00200] De qué dirección viene la petición, para frenar a quien prueba contraseñas."""
    return (request.client.host if request.client else "") or "desconocido"


# [00360] Lo que se dice cuando el error no trae texto. «Error 400» y una pantalla en
# blanco no le dicen nada a nadie: quien está delante necesita saber si se ha
# equivocado él, si no le toca, o si eso ya no está.
SIN_TEXTO = {400: "error.bad_request", 403: "error.forbidden",
             404: "error.not_found", 409: "error.conflict",
             413: "error.too_big", 429: "error.too_many"}


@app.exception_handler(HTTPException)
async def redirect_handler(request: Request, exc: HTTPException):
    """[00201] Pinta los errores en pantalla, en el idioma de quien está delante.

    La pantalla de error se pintaba sin mirar quién era, así que una casa
    española que se equivocaba en una casilla recibía el aviso en inglés.
    """
    if exc.status_code == 303 and "Location" in (exc.headers or {}):
        return RedirectResponse(exc.headers["Location"], status_code=303)
    # [00361] En el idioma de quien está delante, no en el del navegador. La pantalla
    # de error se pintaba sin mirar quién era, así que una casa española que
    # se equivocaba en una casilla recibía el aviso en inglés.
    user = None
    try:
        with db.session_scope() as sesion:
            encontrado = current(request, sesion)
            if encontrado is not None:
                lang = lang_for(request, sesion, encontrado[0])
                user = encontrado[0]
            else:
                lang = lang_for(request)
    except Exception:                                    # noqa: BLE001
        lang = lang_for(request)
    detalle = exc.detail or ""
    if not str(detalle).strip():
        detalle = i18n.t(lang, SIN_TEXTO.get(exc.status_code, "error.other"))
    # [00362] Volver a donde se estaba, no a la portada: el que se equivoca en una
    # casilla quiere la misma pantalla, no empezar de cero.
    volver = request.headers.get("referer") or ""
    if not volver.startswith(str(request.base_url).rstrip("/")):
        volver = "/"
    return templates.TemplateResponse(request, "error.html",
                                      {"user": user, "csrf": "", "unread": 0,
                                       "t": i18n.translator(lang), "lang": lang,
                                       "dir": i18n.direction(lang), "languages": i18n.LANGUAGES,
                                       "code": exc.status_code, "detail": detalle,
                                       "volver": volver,
                                       "nonce": getattr(request.state, "nonce", ""),
                                       "can": lambda capability: False,
                                       "here": request.url.path},
                                      status_code=exc.status_code)


def _pantalla_de_error(request: Request, codigo: int, detalle: str):
    """[01718] La pantalla de error, en el idioma de quien está delante.

    Sale de `redirect_handler` para que la puedan usar también los fallos que
    no son `HTTPException`: el texto se pinta igual, lo único que cambia es de
    dónde viene.
    """
    user = None
    try:
        with db.session_scope() as sesion:
            encontrado = current(request, sesion)
            if encontrado is not None:
                lang = lang_for(request, sesion, encontrado[0])
                user = encontrado[0]
            else:
                lang = lang_for(request)
    except Exception:                                    # noqa: BLE001
        lang = lang_for(request)
    # [01719] Volver a donde se estaba, no a la portada: el que se equivoca en una
    # casilla quiere la misma pantalla, no empezar de cero.
    volver = request.headers.get("referer") or ""
    if not volver.startswith(str(request.base_url).rstrip("/")):
        volver = "/"
    return templates.TemplateResponse(request, "error.html",
                                      {"user": user, "csrf": "", "unread": 0,
                                       "t": i18n.translator(lang), "lang": lang,
                                       "dir": i18n.direction(lang), "languages": i18n.LANGUAGES,
                                       "code": codigo, "detail": detalle,
                                       "volver": volver,
                                       "nonce": getattr(request.state, "nonce", ""),
                                       "can": lambda capability: False,
                                       "here": request.url.path},
                                      status_code=codigo)


# [01727] El mismo manejador, también para el `HTTPException` de Starlette.
#
# El enrutador levanta el suyo —`starlette.exceptions.HTTPException`, que es el
# padre del de FastAPI— cuando una dirección no existe, y ese no encajaba con el
# manejador de arriba: `/pieza/ZZZ` o un marcador viejo devolvían
# `{"detail":"Not Found"}` en inglés, sin la marca y sin botón de volver, a
# alguien que está trabajando en árabe o en húngaro. Los errores que levanta el
# propio programa sí salían bien; era solo la puerta de entrada.
async def router_handler(request: Request, exc: StarletteHTTPException):
    """[01729] El error del enrutador, pintado como los nuestros y en su idioma.

    Starlette pone de su cosecha un `detail` en inglés —«Not Found», «Method Not
    Allowed»—. Se vacía a propósito: con el detalle en blanco, el manejador de
    arriba pone el texto traducido que ya existe para cada código, que es lo que
    tiene que leer quien teclea mal una dirección o abre un marcador viejo.
    """
    return await redirect_handler(
        request, HTTPException(status_code=exc.status_code,
                               detail="", headers=getattr(exc, "headers", None)))


app.add_exception_handler(StarletteHTTPException, router_handler)


@app.exception_handler(RequestValidationError)
async def missing_field_handler(request: Request, exc: RequestValidationError):
    """[01720] Falta una casilla: se dice en la pantalla, no con un JSON en inglés.

    Sin esto, FastAPI contesta con su propio cuerpo —`{"detail":[{"type":
    "missing","loc":["body","serial"]...}]}`, 422 y en inglés— a cualquiera de
    los siete idiomas. Quien está en el muelle con guantes ve un trozo de
    código y no sabe que lo único que pasa es que se dejó un número.

    Y hay un motivo de más: la cola del teléfono aparta a «sin mandar» todo lo
    que llega con 4xx, así que ese JSON acababa siendo lo que una persona abría
    para ver por qué no había entrado su recuento.
    """
    return _pantalla_de_error(request, 400, "")


@app.exception_handler(Exception)
async def unexpected_handler(request: Request, exc: Exception):
    """[01721] El fallo que no habíamos previsto, contado como se cuenta lo demás.

    Antes esto salía por el suelo del framework: «Internal Server Error», 21
    bytes de texto plano, en inglés, sin la marca, sin botón de volver y sin
    nada que darle a quien lo atiende. Igual para el húngaro que para el
    español. Era una llamada garantizada, siempre.

    Lo que cambia, además de la pantalla, es que el fallo **se numera**. El
    número sale en pantalla y el mismo número encabeza la traza en el registro
    del servidor: cuando alguien dice «me ha salido el 4417», eso es todo lo
    que hace falta para ir a mirar qué pasó. Sin él, atender un parte de fallo
    empieza por un interrogatorio.

    El número es corto a propósito: se dicta por teléfono y se escribe en un
    papel con un guante puesto. Cuatro cifras se repiten, y no pasa nada: lo
    que las hace únicas es la hora que va al lado en el registro.
    """
    numero = f"{random.randint(1000, 9999)}"
    logging.exception("FALLO %s en %s %s", numero, request.method, request.url.path)
    return _pantalla_de_error(request, 500, f"#{numero}")


# ============================================================== ACCESO
@app.get("/idioma/{lang}")
def choose_language(lang: str, next: str = "/login"):
    """[00202] Cambia el idioma y devuelve a donde estaba.

    Solo a una dirección de aquí: sin esa comprobación, un enlace preparado
    lleva a quien lo pincha a otra web con nuestra cara.
    """
    if not i18n.is_supported(lang):
        raise HTTPException(status_code=404, detail="")
    target = next if next.startswith("/") and not next.startswith("//") else "/login"
    return set_lang_cookie(RedirectResponse(target, status_code=303), lang)


def landing_photos() -> dict[str, str]:
    """[00203] Las fotos que hay puestas, por su sitio en la portada.

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
    """[00204] La portada: quien ya tiene cuenta entra, quien no, se entera de qué es."""
    if current(request, session):
        return RedirectResponse("/hoy", status_code=303)
    return page(request, "public_home.html", lang=lang_for(request, session),
                retention_days=privacy.RETENTION_DAYS, trial_days=billing.TRIAL_DAYS,
                photos=landing_photos())


@app.post("/pasarela/stripe")
async def gateway_webhook(request: Request, session: Session = Depends(get_db)):
    """[00205] Lo que cuenta la pasarela de pago: recibos cobrados, fallados y bajas.

    Es pública porque la llama la pasarela, así que aquí no manda la sesión
    sino la firma: sin firma buena no se toca nada. Y cada evento se aplica una
    sola vez, porque la pasarela reintenta hasta que se le contesta bien.
    """
    if gateway.secret() is None:
        return JSONResponse({"error": "gateway not configured"}, status_code=503)
    payload = await request.body()
    try:
        result = gateway.apply(session, payload, request.headers.get("stripe-signature", ""))
    except gateway.GatewayError as e:
        log.warning("pasarela: evento rechazado (%s)", e)
        return JSONResponse({"error": str(e)}, status_code=400)
    log.info("pasarela: %s %s -> %s", result.kind, result.event_id,
             result.ignored or (result.now.value if result.now else "sin cambio"))
    return JSONResponse({"ok": True, "applied": not result.ignored})


@app.post("/cookies/visto")
def cookie_notice_seen(request: Request, next: str = Form("/")):
    """[00206] «Entendido»: se apunta que ya se ha leído y se vuelve a donde estabas.

    Sin JavaScript a propósito: así funciona igual en un teléfono viejo, con el
    navegador en modo estricto o con los guiones desactivados.
    """
    destino = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(destino, status_code=303)
    response.set_cookie(COOKIE_NOTICE, "1", max_age=NOTICE_YEAR, path="/",
                        samesite="lax", httponly=True,
                        secure=is_https(request))
    return response


@app.get("/cookies", response_class=HTMLResponse)
def cookies_page(request: Request, session: Session = Depends(get_db)):
    """[00207] Qué cookies hay, para qué y cuánto duran. Solo técnicas."""
    return page(request, "public_cookies.html", lang=lang_for(request, session),
                retention_days=privacy.RETENTION_DAYS)


@app.get("/precios", response_class=HTMLResponse)
def pricing(request: Request, session: Session = Depends(get_db)):
    """[00208] La pantalla de precios, para quien todavía no es cliente."""
    return page(request, "public_pricing.html", lang=lang_for(request, session),
                precio=tarifa.publicada(session), moneda_de=money.simbolo,
                trial_days=billing.TRIAL_DAYS, retention_days=privacy.RETENTION_DAYS)


@app.get("/solicitar", response_class=HTMLResponse)
def request_form(request: Request, session: Session = Depends(get_db), sent: int = 0):
    """[00209] El formulario para pedir acceso al programa."""
    return page(request, "public_request.html", lang=lang_for(request, session),
                sent=bool(sent), error="", plans=list(Plan), sub={},
                trial_days=billing.TRIAL_DAYS, retention_days=privacy.RETENTION_DAYS)


@app.post("/solicitar", response_class=HTMLResponse)
async def submit_request(request: Request, session: Session = Depends(get_db)):
    """[00210] La única puerta abierta a internet. Con freno y sin datos de pago."""
    form = await request.form()
    lang = lang_for(request, session)
    data = {k: (form.get(k) or "").strip() for k in
            ("restaurant_name", "legal_name", "tax_number", "country", "address",
             "contact_name", "contact_role", "email", "phone", "message")}

    def again(error: str):
        """[00339] Vuelve a pintar el formulario con lo que ya se había escrito.

        Un fallo en una casilla no puede costar volver a escribir las doce.
        """
        return page(request, "public_request.html", lang=lang, sent=False, error=error,
                    plans=list(Plan), sub=data, trial_days=billing.TRIAL_DAYS,
                    retention_days=privacy.RETENTION_DAYS)

    if not security.form_allowed(client_ip(request), session=session):
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
    """[00211] Lo que ve cada quien cuando la cuenta está parada."""
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
    """[00212] La pantalla de entrar. Quien ya está dentro, adentro."""
    if current(request, session):
        return RedirectResponse("/hoy", status_code=303)
    return page(request, "login.html", error="")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          session: Session = Depends(get_db)):
    """[00213] Entrar. Con freno para quien prueba contraseñas.

    El freno va por correo **y** por dirección: ni se castiga a una casa entera
    por una dirección, ni se dejan probar mil contraseñas desde la misma.
    """
    lang = lang_for(request, session)
    # [00363] El freno va por correo y por dirección: ni se castiga a una casa entera
    # por una dirección, ni se prueban mil contraseñas desde la misma.
    key = f"{(email or '').strip().lower()}|{client_ip(request)}"
    espera = security.locked_for(key, session=session)
    if espera:
        return page(request, "login.html", lang=lang,
                    error=i18n.t(lang, "auth.too_many", minutes=max(1, espera // 60)))
    try:
        user = auth.authenticate(session, email, password, lang=lang)
    except auth.AuthError as e:
        security.note_failure(key, session=session)
        log.warning("acceso fallido para %s desde %s", (email or "").strip().lower(),
                    client_ip(request))
        return page(request, "login.html", error=str(e), lang=lang)
    security.clear(key, session=session)
    # [00364] Con dos pasos, la contraseña solo abre la puerta de los seis dígitos: la
    # sesión queda a medias hasta que se teclean.
    pendiente = auth.needs_second_step(user)
    token, _ = auth.start_session(session, user, pending_2fa=pendiente)
    if pendiente:
        return set_session_cookie(
            RedirectResponse("/acceso/verificacion", status_code=303), token)
    destino = "/admin" if user.role == Role.OWNER else "/hoy"
    return set_session_cookie(RedirectResponse(destino, status_code=303), token)


def _pending(request: Request, session: Session):
    """[00214] La sesión que ha pasado la contraseña y espera los seis dígitos."""
    found = auth.resolve_session(session, request.cookies.get(auth.COOKIE_NAME),
                                 allow_pending=True)
    if found is None or not found[1].pending_2fa:
        return None
    return found


@app.get("/acceso/verificacion", response_class=HTMLResponse)
def second_step_form(request: Request, session: Session = Depends(get_db), error: str = ""):
    """[00215] Los seis dígitos del teléfono, después de la contraseña."""
    found = _pending(request, session)
    if found is None:
        return RedirectResponse("/login", status_code=303)
    user, auth_session = found
    lang = lang_for(request, session, user)
    return page(request, "second_step.html", lang=lang, error=error, csrf=auth_session.csrf,
                left=twofactor.recovery_left(user.recovery_codes))


@app.post("/acceso/verificacion", response_class=HTMLResponse)
def second_step(request: Request, code: str = Form(...), csrf: str = Form(""),
                session: Session = Depends(get_db)):
    """[00216] El segundo paso: el código del teléfono, o uno de repuesto."""
    user_and_session = _pending(request, session)
    if user_and_session is None:
        return RedirectResponse("/login", status_code=303)
    user, auth_session = user_and_session
    lang = lang_for(request, session, user)
    try:
        auth.check_csrf(auth_session, csrf, lang)
    except auth.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from None

    # [00365] El freno también aquí: seis dígitos se prueban muy deprisa.
    key = f"2fa:{user.id}|{client_ip(request)}"
    espera = security.locked_for(key, session=session)
    if espera:
        return second_step_form(request, session,
                                error=i18n.t(lang, "auth.too_many",
                                             minutes=max(1, espera // 60)))

    if twofactor.verify(user.totp_secret or "", code):
        security.clear(key, session=session)
        auth.finish_second_step(session, auth_session)
    else:
        gastado, quedan = twofactor.spend_recovery(user.recovery_codes, code)
        if not gastado:
            security.note_failure(key, session=session)
            log.warning("segundo paso fallido para %s", user.email)
            return second_step_form(request, session, error=i18n.t(lang, "tfa.bad_code"))
        user.recovery_codes = quedan
        security.clear(key, session=session)
        auth.finish_second_step(session, auth_session)
    destino = "/admin" if user.role == Role.OWNER else "/hoy"
    return RedirectResponse(destino, status_code=303)


@app.post("/logout")
def logout(request: Request, session: Session = Depends(get_db)):
    """[00217] Salir: se cierra la sesión y se borra la cookie."""
    auth.end_session(session, request.cookies.get(auth.COOKIE_NAME))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@app.get("/signup")
def signup_form():
    """[00218] Aquí no hay registro abierto: las cuentas las crea la plataforma."""
    return RedirectResponse("/solicitar", status_code=303)


@app.post("/signup")
def signup_closed():
    """[00219] Aquí no se da de alta nadie solo: se pide acceso."""
    return RedirectResponse("/solicitar", status_code=303)


@app.get("/join")
def join_form():
    """[00220] Las cuentas de la casa las crea su manager, una a una y con su nivel."""
    return RedirectResponse("/login", status_code=303)


@app.post("/join")
def join_closed():
    """[00221] Tampoco se entra en una casa por un enlace: la da de alta quien la lleva."""
    return RedirectResponse("/login", status_code=303)


# ================================================================= HOY
@app.get("/hoy", response_class=HTMLResponse)
def home(request: Request, ctx=Depends(require_user), session: Session = Depends(get_db)):
    """[00222] Lo que está pendiente en la carne, en una pantalla."""
    user, auth_session = ctx
    lang = lang_for(request, session, user)
    restaurant = session.get(Restaurant, user.restaurant_id)
    mia = sites.of_user(session, user)
    return page(request, "home.html", user, auth_session, session, lang=lang,
                restaurant=restaurant, trial_left=billing.trial_left(restaurant),
                free_cancel=billing.free_cancellation(restaurant), site=mia,
                info=meat.today(session, user.restaurant_id, lang=lang,
                                site_id=mia.id if mia else None))


@app.post("/cuenta/cancelar")
def cancel_own_account(request: Request, reason: str = Form(""), csrf: str = Form(""),
                       ctx=Depends(needs(perms.TEAM)), session: Session = Depends(get_db)):
    """[00223] La casa cancela su cuenta. En prueba y antes de tiempo, sin pagar nada.

    Es la acción más cara del programa, y la pedía cualquier manager —también
    el de un local— porque `TEAM` lo tienen todos. Un viernes por la noche el
    obrador y los dos locales se quedaban cancelados. Esto lo hace quien lleva
    la casa entera, y nadie más.
    """
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    if not (perms.is_general_manager(user) or user.role == Role.OWNER):
        raise HTTPException(status_code=403, detail=i18n.t(lang, "pass.not_yours"))
    restaurant = session.get(Restaurant, user.restaurant_id)
    if restaurant is None or restaurant.platform:
        raise HTTPException(status_code=404, detail="")
    billing.cancel(session, user, restaurant, reason.strip() or None)
    return RedirectResponse("/cuenta", status_code=303)


# [00366] ========================================================== RECEPCIÓN
# Un lote de recepción puede traer muchas piezas del mismo corte. Ocho líneas
# es lo que se ve de una vez sin marear; las demás se añaden en la propia
# La pantalla da de alta **una pieza cada vez**: se coge la bolsa, se le hace
# la foto a su etiqueta, se escriben su número y sus kilos, y se guarda. Es como
# se descarga de verdad —pieza a pieza, con las manos ocupadas— y así la foto
# es de la pieza que se está mirando y no de una de las ocho de una tabla.
#
# El servidor sigue aceptando varias filas de un envío: en la cola de un
# teléfono que estuvo sin cobertura puede haber recepciones escritas con la
# pantalla de antes, y esas tienen que entrar igual.
FILAS_RECEPCION = 1
MAX_RECEPCION = 60

# [00367] Lo que vale para todo el camión y no se vuelve a teclear pieza a pieza.
# Lo que vale para toda la descarga y por eso se queda escrito de una pieza a
# la siguiente. El precio del kilo ya no está: es de cada pieza, y arrastrarlo
# hacía que la segunda bolsa se quedara con el precio de la primera sin que
# nadie lo hubiera dicho. El transporte y la aduana sí, que son del camión.
DEL_CAMION = ("lot", "sku", "chamber", "grade", "origin", "use_by",
              "freight_kg", "duty_kg", "vat_pct",
              "producer_plant", "est_code", "breed", "pack_date", "slaughter_date",
              "label_product", "halal", "arrival", "arrival_c", "frozen_on_arrival")


@app.get("/recepcion", response_class=HTMLResponse)
def reception_page(request: Request, ctx=Depends(needs(perms.RECEIVE)),
                   session: Session = Depends(get_db), foto: str = ""):
    """[00224] El muelle: lo que llega del camión."""
    user, auth_session = ctx
    return _reception(request, user, auth_session, session, foto=foto)


def _reception(request, user, auth_session, session, *, done=None, error="",
               foto: str = "", previo=None):
    # [00368] Se proponen el lote y el número; se cogen de verdad al dar de alta.
    """[00225] Pinta el muelle, proponiendo el lote y el número de la pieza.

    Se proponen; se cogen de verdad al dar de alta, que es cuando se sabe si
    otro se ha adelantado.
    """
    return page(request, "reception.html", user, auth_session, session, done=done, error=error,
                recent=meat.recent_primals(session, user.restaurant_id),
                lot=meat.next_lot(session, user.restaurant_id),
                foto=(foto or "")[:200], previo=(previo or {}),
                serials=meat.next_serials(session, user.restaurant_id, 1))


@app.post("/recepcion", response_class=HTMLResponse)
async def receive(request: Request, ctx=Depends(needs(perms.RECEIVE)),
                  session: Session = Depends(get_db)):
    """[00226] Un lote de recepción: cada pieza con su número, su peso y su precio."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)
    repetido = _ya_estaba(request, session, user, str(form.get("envio") or ""), "/recepcion")
    if repetido is not None:
        return repetido

    # [00369] Todo lo que se lee del formulario va dentro del `try`. La temperatura se
    # leía una línea por encima, y un «4 C» —la tecla de al lado— reventaba la
    # pantalla entera con un 500 en vez de contestar «ahí va un número».
    try:
        return await _recibir(request, user, auth_session, session, form, lang)
    except (meat.MeatError, ValueError) as e:
        return _reception(request, user, auth_session, session, error=_dicho(e, lang),
                          previo=_lo_escrito(form))


async def _recibir(request, user, auth_session, session, form, lang):
    """[00227] Lee el formulario del muelle y da de alta lo que ha llegado.

    El precio no lo pone el muelle: quien descarga apunta qué es, cuánto pesa,
    de qué calidad y de dónde viene. El dinero es de dirección y además llega
    después, en la factura.
    """
    lot = (form.get("lot") or "").strip()
    sku = (form.get("sku") or "").strip()
    grade = (form.get("grade") or "").strip() or None
    origin = (form.get("origin") or "").strip() or None
    use_by = (form.get("use_by") or "").strip()
    # [00370] El precio no lo pone el muelle. Quien descarga apunta lo que llega —qué
    # es, cuánto pesa, de qué calidad y de dónde viene—; el dinero es de
    # dirección y además llega después, en la factura. Si quien recibe es el
    # manager, lo pone de una vez y se ahorra el segundo paso.
    puede_dinero = perms.can(user, perms.MONEY)
    # [01613] El precio del kilo es de cada pieza y de nadie más: dos bolsas del mismo
    # camión no valen lo mismo si una es MB9 y la otra MB6, y un precio de lote
    # que se copia a todas esconde justo esa diferencia. Del camión entero son
    # el transporte y la aduana, que sí se reparten por igual a cada kilo que
    # traía, y solo los hay cuando la carne es de importación.
    flete = _eur(form.get("freight_kg")) if puede_dinero else None
    aduana = _eur(form.get("duty_kg")) if puede_dinero else None
    # [01649] El IVA de la compra es del camión entero y **no se suma al kilo**: se
    # recupera al declarar, así que no es un coste de la casa. Se guarda para
    # poder sumar lo que se puede descontar.
    iva = _num(form.get("vat_pct")) if puede_dinero else None
    # [00371] La etiqueta del proveedor: lo que es igual para todo el camión se escribe
    # una vez aquí arriba y se copia a cada pieza. Lo que cambia de una bolsa a
    # otra se escribe en su línea y manda sobre esto.
    planta = (form.get("producer_plant") or "").strip() or None
    registro = (form.get("est_code") or "").strip() or None
    raza = (form.get("breed") or "").strip() or None
    etiqueta = (form.get("label_product") or "").strip() or None
    halal = True if form.get("halal") else None
    envasado = (form.get("pack_date") or "").strip()
    sacrificio = (form.get("slaughter_date") or "").strip()
    # [00372] Cómo bajó del camión. Va con la pieza, no con el camión: se abre su caja,
    # se clava el termómetro y ese número es suyo. En la misma descarga una
    # viene a dos grados y otra a seis, y el día que sale mal se pregunta por
    # esa. Se arrastra de la anterior para no teclearlo veinte veces, pero cada
    # pieza guarda el que tenía delante cuando se dio de alta.
    llegada = Storage.FROZEN if form.get("arrival") == "FROZEN" else Storage.CHILLED
    grados = _num(form.get("arrival_c"))
    al_arcon = bool(form.get("frozen_on_arrival")) and llegada == Storage.CHILLED
    rows = []
    for i in range(MAX_RECEPCION):
        serial = (form.get(f"serial:{i}") or "").strip()
        # [00373] El peso se escribe en gramos; `kg:` es el nombre de antes y solo
        # puede venir de la cola de un teléfono que se quedó sin cobertura
        # con la pantalla vieja abierta. Ahí significaba kilos y se lee como
        # kilos: esa pieza queda bien apuntada y no mil veces más ligera.
        escrito = form.get(f"g:{i}") or form.get(f"kg:{i}")
        kg = pesos.del_formulario(form, f"g:{i}", f"kg:{i}", default=0.0)
        if not serial and not (escrito or "").strip():
            continue
        fecha_sac = (form.get(f"slaughter:{i}") or "").strip() or sacrificio
        rows.append(meat.PrimalRow(
            serial=serial, kg=kg or 0.0,
            price_kg=_eur(form.get(f"price:{i}")) if puede_dinero else None,
            freight_kg=flete, duty_kg=aduana, vat_pct=iva,
            sku=(form.get(f"sku:{i}") or "").strip() or sku,
            grade=(form.get(f"grade:{i}") or "").strip() or grade,
            origin=(form.get(f"origin:{i}") or "").strip() or origin,
            use_by=date.fromisoformat(use_by) if use_by else None,
            supplier_lot=(form.get(f"slot:{i}") or "").strip() or None,
            producer_plant=planta, est_code=registro, breed=raza,
            label_product=etiqueta, halal=halal,
            arrival=llegada, arrival_c=grados, frozen_on_arrival=al_arcon,
            slaughter_date=date.fromisoformat(fecha_sac) if fecha_sac else None,
            pack_date=date.fromisoformat(envasado) if envasado else None))
    # [01682] La cámara, que se escribía y se tiraba. `receive_primals` la
    # acepta y la guarda desde siempre, pero aquí no se le pasaba: el camión
    # entraba sin decir dónde se había metido. Y no se notaba, porque «chamber»
    # está en DEL_CAMION y el formulario volvía a pintarse con la cámara
    # puesta: el carnicero la escribía, la veía quedarse, y no se guardaba
    # nada. Un campo que finge que ha guardado es peor que un campo que falta.
    created = meat.receive_primals(session, user, lot, rows, lang=lang,
                                   chamber=(form.get("chamber") or "").strip() or None,
                                   received=_cuando(form, session, user))
    # [00374] La foto de la etiqueta viaja con la pieza, en el mismo envío: se hace
    # al coger la bolsa, antes de teclear nada, que es cuando la etiqueta
    # está delante. Va aparte del texto solo cuando no hay cobertura, y
    # entonces no viaja: lo escrito se guarda igual y la foto se hace luego.
    subida = form.get("foto")
    if created and isinstance(subida, UploadFile) and subida.filename:
        meat.store_label_photo(session, created[0], subida.content_type or "",
                               await _leer_foto(request, subida, lang),
                               UPLOAD_DIR, lang=lang)
    # [00375] Lo del camión se queda escrito: la siguiente pieza es de la misma caja y
    # nadie vuelve a teclear el matadero veinte veces.
    # Una pieza cada vez es lo normal; varias, solo cuando vuelve la cola de un
    # teléfono que estuvo sin cobertura. «1 primales» no lo dice nadie.
    if len(created) == 1:
        # [00376] Y con el número delante, que es el momento de coger el rotulador.
        hecho = (i18n.t(lang, "m.rec.done_one", serial=created[0].serial,
                        kg=f"{created[0].weight_kg:.10g}", lot=lot or "—")
                 + " " + i18n.t(lang, "m.rec.write_now", serial=created[0].serial,
                                    kg=f"{created[0].weight_kg:.10g}"))
    else:
        hecho = i18n.t(lang, "m.rec.done", n=len(created), lot=lot or "—")
    # [00377] Salió bien: se queda lo del camión y **solo** lo del camión. La siguiente
    # bolsa es de la misma caja pero no es la misma pieza: dejarle puestos el
    # número y los kilos de la anterior es la manera de dar de alta dos veces
    # lo mismo. Lo escrito entero se devuelve cuando algo falla, que es cuando
    # hace falta, y no cuando se ha guardado.
    return _hecho(auth_session, "/recepcion", hecho, previo=_del_camion(form))


async def _leer_foto(request: Request, subida, lang: str = "es") -> bytes:
    """[00228] Lee una foto sin meterse en memoria lo que no cabe.

    Antes se leía entera y después se miraba si pasaba del tope: un envío de
    trescientos megas eran trescientos megas dentro del proceso para acabar
    contestando que no cabía, y con dos a la vez en plena descarga de camión el
    servicio se caía. Ahora se mira lo que dice la cabecera y, aunque mienta,
    se corta en cuanto se pasa: se lee a trozos de un mega, que es donde ni se
    pierde tiempo en llamadas ni se hincha la memoria.
    """
    tope = service.MAX_UPLOAD_BYTES
    demasiado = meat.MeatError(i18n.t(lang, "valid.photo_too_big",
                                      n=tope // (1024 * 1024)))
    # [00378] La cabecera puede mentir, pero cuando dice la verdad ahorra la lectura
    # entera. El doble del tope deja sitio al resto del formulario.
    dicho = request.headers.get("content-length")
    if dicho and dicho.isdigit() and int(dicho) > tope * 2:
        raise demasiado
    trozos, leido = [], 0
    while trozo := await subida.read(1024 * 1024):
        leido += len(trozo)
        if leido > tope:
            raise demasiado
        trozos.append(trozo)
    return b"".join(trozos)


def _lo_escrito(form) -> dict:
    """[00229] Todo lo que había escrito, para devolverlo puesto.

    Antes solo se devolvía lo del camión y las líneas de las piezas —el número,
    los kilos, el precio, el lote del proveedor— se borraban: un error en una
    casilla obligaba a teclear la hoja entera otra vez, con el camión esperando.
    La foto no vuelve: un fichero no se puede devolver escrito en un campo, y
    quien la hizo la tiene todavía en el teléfono.
    """
    fuera = {"csrf", "envio", "foto"}
    return {clave: str(valor) for clave, valor in form.multi_items()
            if clave not in fuera and isinstance(valor, str)}


def _del_camion(form) -> dict:
    """[00230] Lo que vale para toda la descarga, para devolverlo escrito."""
    return {clave: str(form.get(clave) or "") for clave in DEL_CAMION}


@app.get("/recepcion/precios", response_class=HTMLResponse)
def prices_page(request: Request, ctx=Depends(needs(perms.MONEY)),
                session: Session = Depends(get_db)):
    """[00231] Las piezas que esperan precio para poder trabajarse."""
    user, auth_session = ctx
    return _prices(request, user, auth_session, session)


def _prices(request, user, auth_session, session, *, done="", error="", previo=None):
    """[00232] La pantalla de poner precio a lo que entró sin él."""
    mia = sites.of_user(session, user)
    return page(request, "prices.html", user, auth_session, session, done=done,
                error=error, site=mia, previo=(previo or {}),
                pending=meat.awaiting_price(session, user.restaurant_id,
                                            site_id=mia.id if mia else None))


@app.post("/recepcion/precios", response_class=HTMLResponse)
async def save_prices(request: Request, ctx=Depends(needs(perms.MONEY)),
                      session: Session = Depends(get_db)):
    """[00233] Activa las piezas: a cada una su precio, o el mismo a todas."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)
    repetido = _ya_estaba(request, session, user, str(form.get("envio") or ""),
                          "/recepcion/precios")
    if repetido is not None:
        return repetido

    puestas = 0
    try:
        # [00379] «El mismo a todas» es lo normal: un albarán trae un precio por
        # artículo. Lo que se escriba en la línea de una pieza manda sobre eso.
        # Se lee aquí dentro: un «32 eur» en esa casilla tumbaba la pantalla.
        todas = _eur(form.get("all_price"))
        for serial in form.getlist("serial"):
            precio = _eur(form.get(f"price:{serial}"), todas)
            if precio is None:
                continue
            meat.set_price(session, user, serial, precio, lang=lang)
            puestas += 1
    except (meat.MeatError, ValueError) as e:
        session.rollback()
        return _prices(request, user, auth_session, session,
                       error=_dicho(e, lang_for(request, session, user)),
                       previo=_lo_escrito(form))
    if not puestas:
        return _prices(request, user, auth_session, session,
                       error=i18n.t(lang, "m.price.nothing"))
    return _prices(request, user, auth_session, session,
                   done=i18n.t(lang, "m.price.done", n=puestas))


# ============================================================ DESPIECE
CORTES_A_LA_VISTA = 3
@app.get("/despiece", response_class=HTMLResponse)
def butchery_page(request: Request, ctx=Depends(needs(perms.BUTCHER)),
                  session: Session = Depends(get_db), cortes: int = CORTES_A_LA_VISTA):
    """[00234] La mesa de despiece."""
    user, auth_session = ctx
    return _butchery(request, user, auth_session, session, cortes=cortes)


def _butchery(request, user, auth_session, session, *, done=None, issues=(), error="",
              cortes: int = CORTES_A_LA_VISTA, previo=None):
    # [00380] Tres bloques a la vista y los demás se añaden. Diez huecos vacíos de
    # golpe son un muro: casi ningún despiece saca diez cortes, y el que los
    # saca los pide.
    """[00235] Pinta el despiece, con tres huecos de corte a la vista.

    Diez huecos vacíos de golpe son un muro: casi ningún despiece saca diez
    cortes, y el que los saca los pide.
    """
    cuantos = max(CORTES_A_LA_VISTA, min(int(cortes or CORTES_A_LA_VISTA), meat.MAX_CUTS))
    mia = sites.of_user(session, user)
    return page(request, "butchery.html", user, auth_session, session, done=done,
                issues=list(issues), error=error, rows=range(cuantos), site=mia,
                previo=(previo or {}), maximo=meat.MAX_CUTS,
                tg=meat.next_tg(session, user.restaurant_id),
                primals=meat.primals_in_stock(session, user.restaurant_id,
                                              site_id=mia.id if mia else None,
                                              priced_only=True),
                sin_precio=len(meat.awaiting_price(session, user.restaurant_id,
                                                   site_id=mia.id if mia else None)),
                articles=meat.articles(session, user.restaurant_id),
                recent=meat.recent_butchery(session, user.restaurant_id))


@app.post("/despiece", response_class=HTMLResponse)
async def post_butchery(request: Request, ctx=Depends(needs(perms.BUTCHER)),
                        session: Session = Depends(get_db)):
    """[00236] Vuelca el despiece a cámara: cada corte, con su serial y su coste."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)
    # [00381] Ahora la hoja se puede apuntar sin señal, así que el teléfono reintenta:
    # sin esto, un despiece volcado dos veces mete los cortes dos veces.
    repetido = _ya_estaba(request, session, user, str(form.get("envio") or ""), "/despiece")
    if repetido is not None:
        return repetido
    try:
        rows = []
        for i in range(meat.MAX_CUTS):
            name = (form.get(f"cut:{i}") or "").strip()
            pieces = form.get(f"pieces:{i}")
            grams = form.get(f"grams:{i}")
            # [00382] `kg:` es el nombre de antes, de cuando el corte que sale a peso
            # se escribía en kilos. Solo puede llegar de la cola de un
            # teléfono con la pantalla vieja abierta, y ahí significaba kilos.
            pesado = form.get(f"g:{i}") or form.get(f"kg:{i}")
            by_weight = bool(form.get(f"weight:{i}"))
            if not name and not (pieces or "").strip() and not (pesado or "").strip():
                continue
            # [00383] En la mesa se pesa la bandeja entera, no filete a filete: se
            # escriben los gramos de las piezas juntas y el peso de cada una
            # sale de ahí. Los gramos por pieza se siguen aceptando —la hoja de
            # papel los pide así, y la cola de un teléfono puede traerlos—,
            # pero el total manda cuando viene.
            cuantas = int(_num(pieces, 0) or 0)
            gramos = _g(grams, 0.0) or 0.0
            total = pesos.leer(form.get(f"total:{i}"))
            if total and cuantas > 0:
                gramos = round(total * exacto.GRAMOS / cuantas, 4)
            rows.append(meat.CutRow(
                name=name, item_id=int(form.get(f"item:{i}") or 0),
                pieces=cuantas, grams=gramos,
                value_index=_num(form.get(f"index:{i}"), 1.0) or 1.0,
                is_trim=bool(form.get(f"trim:{i}")),
                by_weight=by_weight,
                kg=pesos.del_formulario(form, f"g:{i}", f"kg:{i}", default=0.0) or 0.0))
        on = (form.get("date") or "").strip()
        _, result = meat.post_butchery(
            session, user, tg=(form.get("tg") or ""),
            serials=form.getlist("primal"),
            before_kg=pesos.del_formulario(form, "before_g", "before_kg",
                                           default=0.0) or 0.0,
            rows=rows,
            waste_kg=pesos.del_formulario(form, "waste_g", "waste_kg",
                                          default=0.0) or 0.0,
            # [00384] La fecha escrita en la hoja manda; si no la hay, la del momento
            # en que se apuntó, que no es la misma que la del envío cuando la
            # hoja ha esperado en la cola del teléfono.
            on=(date.fromisoformat(on) if on else _cuando(form, session, user)),
            staff=(form.get("staff") or "").strip() or None, lang=lang)
    except (meat.MeatError, ValueError) as e:
        # [00385] Con la hoja entera puesta: un despiece son diez líneas de números y
        # un error en una no puede obligar a escribirlas todas otra vez.
        return _butchery(request, user, auth_session, session,
                         error=_dicho(e, lang_for(request, session, user)),
                         previo=_lo_escrito(form))
    return _hecho(auth_session, "/despiece",
                  i18n.t(lang, "m.tg.posted", tg=result.tg, cuts=len(result.lots),
                         kg=f"{sum(l.qty for l in result.lots):.10g}"),
                  issues=list(result.issues))


# ============================================================== CÁMARA
@app.post("/carne/{serial}/foto")
async def upload_label_photo(serial: str, request: Request,
                             ctx=Depends(needs(perms.RECEIVE)),
                             session: Session = Depends(get_db)):
    """[00237] La foto de la etiqueta de una pieza.

    Va aparte del formulario de recepción y no por la cola de sin cobertura: en
    la cola caben textos, no ficheros, y meter ahí una foto de tres megas por
    pieza llenaría el teléfono y no se mandaría nunca. Así que la recepción se
    guarda igual sin señal y las fotos se suben cuando hay línea. La foto es el
    respaldo de lo que se tecleó: el día que un número no cuadre con la
    etiqueta, la etiqueta está.
    """
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)
    volver = str(form.get("next") or "/recepcion")
    pieza = (session.query(Primal)
             .filter_by(restaurant_id=user.restaurant_id, serial=serial.strip()).first())
    if pieza is None:
        raise HTTPException(status_code=404, detail=i18n.t(lang, "m.rec.no_piece",
                                                           serial=serial))
    subida = form.get("foto")
    if not isinstance(subida, UploadFile) or not subida.filename:
        return RedirectResponse(volver, status_code=303)
    try:
        meat.store_label_photo(session, pieza, subida.content_type or "",
                               await _leer_foto(request, subida, lang),
                               UPLOAD_DIR, lang=lang)
    except meat.MeatError as e:
        return RedirectResponse(f"{volver}?foto={quote(str(e))}", status_code=303)
    return RedirectResponse(volver, status_code=303)


@app.get("/carne/{serial}/etiqueta")
def label_photo(serial: str, request: Request, ctx=Depends(needs(perms.STOCK)),
                session: Session = Depends(get_db)):
    """[00238] La foto guardada, solo para gente de esta casa."""
    user, _ = ctx
    lang = lang_for(request, session, user)
    pieza = (session.query(Primal)
             .filter_by(restaurant_id=user.restaurant_id, serial=serial.strip()).first())
    if pieza is None or not pieza.photo_ref or not os.path.exists(pieza.photo_ref):
        raise HTTPException(status_code=404,
                            detail=i18n.t(lang, "error.photo_not_found"))
    with open(pieza.photo_ref, "rb") as fh:
        return Response(fh.read(), media_type=meat.photo_type(pieza.photo_ref),
                        headers={"Cache-Control": "private, max-age=3600"})


@app.get("/carne", response_class=HTMLResponse)
def chamber(request: Request, ctx=Depends(needs(perms.STOCK)),
            session: Session = Depends(get_db), closed: str = "", error: str = ""):
    """[00239] Lo que queda: cortes en cámara y primales sin despiezar, en su sede."""
    user, auth_session = ctx
    mia = sites.of_user(session, user)
    return page(request, "chamber.html", user, auth_session, session, closed=closed,
                site=mia, error=error,
                chambers=sites.chambers(session, user.restaurant_id,
                                        site_id=mia.id if mia else None),
                pieces=meat.primals_in_stock(session, user.restaurant_id,
                                             site_id=mia.id if mia else None),
                lots=costing.at_site(
                    session.query(IngredientLot)
                    .filter(IngredientLot.restaurant_id == user.restaurant_id,
                            IngredientLot.qty_remaining > 0,
                            IngredientLot.serial.isnot(None)),
                    session, user.restaurant_id, mia.id if mia else None)
                .order_by(IngredientLot.serial).all(),
                status=butchery.status(session, user.restaurant_id,
                                       site_id=mia.id if mia else None))


@app.post("/carne/camara")
def set_chamber(request: Request, serial: str = Form(...), chamber: str = Form(""),
                csrf: str = Form(""), ctx=Depends(needs(perms.STOCK)),
                session: Session = Depends(get_db)):
    """[00240] Dice en qué cámara de la sede está esa pieza o ese lote."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    try:
        numero, nombre = sites.set_chamber(session, user, serial, chamber)
    except sites.SiteError as e:
        return RedirectResponse(f"/carne?error={e}", status_code=303)
    dicho = i18n.t(lang, "m.ch.moved", serial=numero,
                   chamber=nombre or i18n.t(lang, "m.ch.none"))
    return RedirectResponse(f"/carne?closed={dicho}", status_code=303)


@app.post("/carne/cierre")
def close_meat_day(request: Request, csrf: str = Form(""), ctx=Depends(needs(perms.STOCK)),
                   session: Session = Depends(get_db)):
    """[00241] Cierra el día de la carne y deja los avisos de lo que no cuadró."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    result = butchery.close_day(session, user)
    return RedirectResponse(f"/carne?closed={len(result.alerts)}", status_code=303)


# [00386] ======================================================== DESCONGELADO
# El descongelado tiene dos momentos que no se parecen en nada: sacar carne a
# descongelar —a media mañana, con la cámara abierta— y contar lo que ha
# sobrado al cerrar. Juntar los dos formularios en una pantalla los hacía
# idénticos y a un palmo el uno del otro: la salida en la casilla del recuento
# y el turno sale descuadrado. Cada uno en su pantalla.
@app.get("/descongelado", response_class=HTMLResponse)
def defrost_page(request: Request, ctx=Depends(needs(perms.DEFROST)),
                 session: Session = Depends(get_db), shift: str = "", done: str = ""):
    """[00242] La pantalla del descongelado y el conteo de la vitrina."""
    user, auth_session = ctx
    return _defrost(request, user, auth_session, session, shift=shift, done=done)


@app.get("/descongelado/recuento", response_class=HTMLResponse)
def defrost_count_page(request: Request, ctx=Depends(needs(perms.DEFROST)),
                       session: Session = Depends(get_db), shift: str = "", done: str = ""):
    """[00243] El recuento de cierre: lo que ha sobrado y el cuadre del turno."""
    user, auth_session = ctx
    return _defrost(request, user, auth_session, session, shift=shift, done=done,
                    tab="recuento")


def _defrost(request, user, auth_session, session, *, shift="", done="", error="",
             closed=None, tab="salida"):
    """[00244] Pinta el descongelado con lo que hay en el arcón de **esa** sede.

    No se saca a descongelar lo que está en otra sede: ese arcón no se abre
    desde aquí.
    """
    on = jornada.del_usuario(session, user)
    mia = sites.of_user(session, user)
    # [00387] No se saca a descongelar lo que está en otra sede: ese arcón no se abre
    # desde aquí.
    lots = costing.at_site(
        session.query(butchery.IngredientLot)
        .filter(butchery.IngredientLot.restaurant_id == user.restaurant_id,
                butchery.IngredientLot.qty_remaining > 1e-9,
                butchery.IngredientLot.serial.isnot(None)),
        session, user.restaurant_id, mia.id if mia else None)
    return page(request, "defrost.html", user, auth_session, session, shift=shift,
                done=done, error=error, on=on, closed=closed, site=mia, tab=tab,
                month=defrost.month_so_far(session, user.restaurant_id, on,
                                           site_id=mia.id if mia else None),
                states=defrost.shift_states(session, user.restaurant_id, on, shift,
                                            site_id=mia.id if mia else None),
                lots=lots.order_by(butchery.IngredientLot.expiry).all())


@app.post("/descongelado/salida", response_class=HTMLResponse)
def defrost_intake(request: Request, serial: str = Form(...), pieces: int = Form(...),
                   total_g: str = Form(""), total_kg: str = Form(""),
                   shift: str = Form(""), note: str = Form(""),
                   csrf: str = Form(""), envio: str = Form(""), cuando: str = Form(""),
                   ctx=Depends(needs(perms.DEFROST)),
                   session: Session = Depends(get_db)):
    """[00245] Apunta lo que se saca del congelador: piezas y peso."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    repetido = _ya_estaba(request, session, user, envio, "/descongelado")
    if repetido is not None:
        return repetido
    pedido = serial.strip()
    # [00388] Lo congelado está en espera: esta salida es lo que lo pone a la venta,
    # así que conviene decir con qué número sale.
    estaba = (session.query(IngredientLot)
              .filter_by(restaurant_id=user.restaurant_id, serial=pedido).first())
    congelado = bool(estaba and estaba.frozen)
    try:
        # [00389] `total_kg` es el nombre de antes: solo llega de la cola de un
        # teléfono con la pantalla vieja, y ahí significaba kilos.
        entry = defrost.intake(session, user, pedido, pieces,
                               pesos.de_dos(total_g, total_kg, 0.0) or 0.0,
                               on=_cuando(cuando, session, user),
                               shift=shift.strip(), note=note.strip() or None)
    except (defrost.DefrostError, ValueError) as e:
        return _defrost(request, user, auth_session, session, shift=shift, error=_dicho(e, lang_for(request, session, user)))
    done = ""
    if congelado and entry.lot_serial != pedido:
        queda = (session.query(IngredientLot)
                 .filter_by(restaurant_id=user.restaurant_id, serial=pedido).first())
        done = i18n.t(lang, "m.df.thawed_split", serial=entry.lot_serial,
                      kg=f"{entry.total_kg:.10g}", parent=pedido,
                      left=f"{(queda.qty_remaining if queda else 0):.10g}")
    elif congelado:
        done = i18n.t(lang, "m.df.thawed", serial=entry.lot_serial)
    return _hecho(auth_session, f"/descongelado?shift={shift}", done)


@app.post("/descongelado/recuento", response_class=HTMLResponse)
def defrost_count(request: Request, serial: str = Form(...), pieces: int = Form(...),
                  total_g: str = Form(""), total_kg: str = Form(""),
                  shift: str = Form(""), note: str = Form(""),
                  csrf: str = Form(""), envio: str = Form(""), cuando: str = Form(""),
                  ctx=Depends(needs(perms.DEFROST)),
                  session: Session = Depends(get_db)):
    """[00246] Apunta lo que queda en la vitrina al contar."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    repetido = _ya_estaba(request, session, user, envio, "/descongelado/recuento")
    if repetido is not None:
        return repetido
    try:
        defrost.count(session, user, serial.strip(), pieces,
                      pesos.de_dos(total_g, total_kg, 0.0) or 0.0,
                      on=_cuando(cuando, session, user),
                      shift=shift.strip(), note=note.strip() or None)
    except (defrost.DefrostError, ValueError) as e:
        return _defrost(request, user, auth_session, session, shift=shift, error=_dicho(e, lang_for(request, session, user)),
                        tab="recuento")
    return RedirectResponse(f"/descongelado/recuento?shift={shift}", status_code=303)


@app.post("/descongelado/cierre", response_class=HTMLResponse)
def defrost_close(request: Request, shift: str = Form(""), csrf: str = Form(""),
                  ctx=Depends(needs(perms.CLOSE_SHIFT)), session: Session = Depends(get_db)):
    """[00247] El cierre convierte el recuento en consumo real y lo descuenta."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    try:
        result = defrost.close(session, user, shift=shift.strip(), lang=lang)
    except (defrost.DefrostError, ValueError) as e:
        return _defrost(request, user, auth_session, session, shift=shift, error=_dicho(e, lang_for(request, session, user)),
                        tab="recuento")
    return _hecho(auth_session, f"/descongelado/recuento?shift={shift}",
                  i18n.t(lang, "m.df.closed", n=len(result.consumed)),
                  closed=_en_texto(result))


# ========================================================== MADURACIÓN
@app.get("/maduracion", response_class=HTMLResponse)
def aging_page(request: Request, ctx=Depends(needs(perms.STOCK)),
               session: Session = Depends(get_db), done: str = ""):
    """[00248] La nevera de maduración y el congelador, pieza a pieza."""
    user, auth_session = ctx
    return _aging(request, user, auth_session, session, done=done)


def _aging(request, user, auth_session, session, *, done="", error="", weighed=None,
           sold=None, trimmed=None, counted=None):
    # [00390] Se madura donde se sirve: quien tiene sede pesa la suya, que es la cámara
    # que tiene delante.
    """[00249] Pinta la cámara de maduración: la pizarra, el conteo del día y el resumen.

    Se madura donde se sirve, así que quien tiene sede pesa la suya. Lo pesado
    se lee una vez y lo usan las tres tablas: leerlo tres veces era medio
    segundo en una casa con seis meses dentro, solo por preguntar lo mismo.
    """
    mia = sites.of_user(session, user)
    suya = mia.id if mia else None
    principal = sites.main(session, user.restaurant_id).id
    # [00391] Lo pesado se lee una vez y lo usan las tres tablas de la cámara: la
    # pizarra, el conteo del día y el resumen. Leerlo tres veces era medio
    # segundo en una casa con seis meses dentro, y solo por preguntar lo mismo
    # tres veces. Los tramos de rendimiento van aparte: miran las piezas que ya
    # se gastaron y lo suman la propia base de datos.
    historia = aging.history_of(session, user.restaurant_id, in_stock_only=True)
    filas = aging.board(session, user.restaurant_id, site_id=suya, history=historia)
    return page(request, "aging.html", user, auth_session, session, done=done, error=error,
                weighed=weighed, sold=sold, trimmed=trimmed, counted=counted, site=mia,
                to_count=aging.to_count(session, user.restaurant_id, site_id=suya,
                                        rows=filas, history=historia),
                storages=list(Storage),
                articles=meat.articles(session, user.restaurant_id),
                rows=filas,
                summary=aging.summary(session, user.restaurant_id, site_id=suya, rows=filas),
                bands=aging.yield_by_days(session, user.restaurant_id),
                sales=aging.sales(session, user.restaurant_id),
                chilled=[p for p in meat.primals_in_stock(session, user.restaurant_id)
                         if aging.where(p) == Storage.CHILLED
                         and (not suya or (p.site_id or principal) == suya)])


@app.post("/maduracion/mover", response_class=HTMLResponse)
def aging_move(request: Request, serial: str = Form(...), storage: str = Form(...),
               target_days: str = Form(""), use_by: str = Form(""), note: str = Form(""),
               csrf: str = Form(""), ctx=Depends(needs(perms.AGE)),
               session: Session = Depends(get_db)):
    """[00250] Mete una pieza a madurar, la congela o la devuelve a la cámara."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    if storage not in Storage.__members__:
        raise HTTPException(status_code=400, detail="Ese sitio no existe")
    days = _num(target_days)
    try:
        aging.move(session, user, serial.strip(), Storage[storage],
                   target_days=int(days) if days else None,
                   use_by=date.fromisoformat(use_by) if use_by.strip() else None,
                   note=note.strip() or None)
    except (aging.AgingError, ValueError) as e:
        return _aging(request, user, auth_session, session, error=_dicho(e, lang_for(request, session, user)))
    return RedirectResponse("/maduracion", status_code=303)


@app.post("/maduracion/pesar", response_class=HTMLResponse)
def aging_weigh(request: Request, serial: str = Form(...), g: str = Form(""),
                kg: str = Form(""),
                note: str = Form(""), csrf: str = Form(""),
                ctx=Depends(needs(perms.AGE)), session: Session = Depends(get_db)):
    """[00251] Vuelve a pesar la pieza: lo que ha perdido sube el precio de lo que queda."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    try:
        # [00392] `kg` es el nombre de antes: solo llega de la cola de un teléfono
        # con la pantalla vieja, donde se escribía en kilos.
        result = aging.weigh(session, user, serial.strip(),
                             pesos.de_dos(g, kg, 0.0) or 0.0,
                             note=note.strip() or None, lang=lang)
    except (aging.AgingError, ValueError) as e:
        return _aging(request, user, auth_session, session, error=_dicho(e, lang_for(request, session, user)))
    return _hecho(auth_session, "/maduracion",
                  i18n.t(lang, "m.ag.weighed", serial=result.serial,
                         kg=f"{result.kg:.10g}", loss=f"{result.loss_kg:.10g}",
                         pct=f"{result.total_loss_pct:.10g}"),
                  weighed=_en_texto(result))


@app.post("/maduracion/conteo", response_class=HTMLResponse)
async def aging_daily_count(request: Request, ctx=Depends(needs(perms.COUNT)),
                            session: Session = Depends(get_db)):
    """[00252] El conteo diario de la nevera de maduración: se pesan todas de una vez."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)
    repetido = _ya_estaba(request, session, user, str(form.get("envio") or ""), "/maduracion")
    if repetido is not None:
        return repetido
    lecturas = []
    for key, value in form.multi_items():
        # [00393] `g:` es la casilla de ahora y `kg:` la de antes, que solo puede
        # venir de la cola de un teléfono con la pantalla vieja abierta.
        en_gramos = key.startswith("g:")
        if not (en_gramos or key.startswith("kg:")) or not str(value).strip():
            continue
        try:
            kg = pesos.leer(value) if en_gramos else _num(value)
        except ValueError:
            continue
        if kg:
            lecturas.append((key.split(":", 1)[1], kg))
    try:
        result = aging.count_day(session, user, lecturas, lang=lang)
    except (aging.AgingError, ValueError) as e:
        return _aging(request, user, auth_session, session, error=_dicho(e, lang_for(request, session, user)))
    dicho = i18n.t(lang, "m.ag.counted", n=result.counted,
                   kg=f"{result.loss_kg:.10g}")
    if result.rechazadas:
        dicho += " · " + i18n.t(lang, "inv.not_written", n=len(result.rechazadas),
                                cuales=" · ".join(result.rechazadas[:4]))
    return _hecho(auth_session, "/maduracion", dicho, counted=_en_texto(result))


@app.post("/maduracion/limpiar", response_class=HTMLResponse)
async def aging_trim(request: Request, ctx=Depends(needs(perms.AGE)),
                     session: Session = Depends(get_db)):
    """[00253] Limpia la pieza: parte se aprovecha, parte se tira, y el resto sube de precio."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)
    try:
        partes = []
        for i in range(aging.MAX_TRIM_PARTS):
            item = (form.get(f"item:{i}") or "").strip()
            kg = pesos.del_formulario(form, f"part_g:{i}", f"part_kg:{i}")
            if not item or not kg:
                continue
            partes.append(aging.TrimPart(
                item_id=int(item), kg=kg,
                value_index=_num(form.get(f"index:{i}"), aging.TRIM_VALUE_INDEX)
                or aging.TRIM_VALUE_INDEX))
        result = aging.trim(session, user, (form.get("serial") or "").strip(),
                            removed_kg=pesos.del_formulario(form, "removed_g",
                                                            "removed_kg"),
                            new_kg=pesos.del_formulario(form, "new_g", "new_kg"),
                            parts=partes,
                            waste_kg=pesos.del_formulario(form, "waste_g",
                                                          "waste_kg"),
                            note=(form.get("note") or "").strip() or None, lang=lang)
    except (aging.AgingError, ValueError) as e:
        return _aging(request, user, auth_session, session, error=_dicho(e, lang_for(request, session, user)))
    return _hecho(auth_session, "/maduracion",
                  i18n.t(lang, "m.ag.trimmed", serial=result.serial,
                         kg=f"{result.removed_kg:.10g}",
                         kept=f"{result.kept_kg:.10g}",
                         waste=f"{result.waste_kg:.10g}",
                         left=f"{result.kg:.10g}"),
                  trimmed=_en_texto(result))


@app.post("/maduracion/venta", response_class=HTMLResponse)
def aging_sale(request: Request, serial: str = Form(...), grams: str = Form(...),
               price: str = Form("0"), dish: str = Form(""), note: str = Form(""),
               csrf: str = Form(""), ctx=Depends(needs(perms.MENU)),
               session: Session = Depends(get_db)):
    """[00254] Venta a peso: se corta en el momento y se cobra por kilo."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    try:
        result = aging.sell_by_weight(session, user, serial.strip(), _g(grams, 0.0) or 0.0,
                                      price=_eur(price, 0.0) or 0.0,
                                      dish=dish.strip() or None, note=note.strip() or None)
    except (aging.AgingError, ValueError) as e:
        return _aging(request, user, auth_session, session, error=_dicho(e, lang_for(request, session, user)))
    return _hecho(auth_session, "/maduracion",
                  i18n.t(lang, "m.ag.sold", serial=result.serial,
                         grams=f"{result.grams:.10g}",
                         left=f"{result.kg_left:.10g}"),
                  sold=_en_texto(result))


# ============================================================= TRASLADOS
@app.get("/traslados", response_class=HTMLResponse)
def transfers_page(request: Request, ctx=Depends(needs(perms.STOCK)),
                   session: Session = Depends(get_db), done: str = "", error: str = ""):
    """[00255] Dónde está la carne y lo que se manda de una sede a otra."""
    user, auth_session = ctx
    return _transfers(request, user, auth_session, session, done=done, error=error)


def _transfers(request, user, auth_session, session, *, done="", error=""):
    """[00256] Pinta los traslados: dónde está la carne y qué se puede mandar."""
    mine = sites.of_user(session, user)
    return page(request, "transfers.html", user, auth_session, session, done=done,
                error=error, mine=mine,
                sites=sites.all_sites(session, user.restaurant_id),
                stock=sites.stock(session, user.restaurant_id),
                primals=meat.primals_in_stock(session, user.restaurant_id,
                                              site_id=mine.id if mine else None),
                storage_of=aging.where,
                lots=costing.at_site(
                    session.query(IngredientLot)
                    .filter(IngredientLot.restaurant_id == user.restaurant_id,
                            IngredientLot.qty_remaining > 0),
                    session, user.restaurant_id, mine.id if mine else None)
                .order_by(IngredientLot.serial).all(),
                moves=sites.recent(session, user.restaurant_id,
                                   site_id=mine.id if mine else None))


def _donde_se_veia(valor: str) -> tuple[str, int | None]:
    """[00257] Parte «8017|3» en el número de la pieza y la sede donde se la veía.

    El desplegable manda las dos cosas juntas porque un `<select>` solo manda
    un valor, y así viaja sin depender de que haya javascript: lo que sale del
    teléfono es lo que había en la pantalla.

    Un envío sin la sede —uno guardado en la cola de un teléfono con la
    pantalla de antes— se admite igual y se manda como se mandaba: perder un
    traslado que ya se hizo sería peor que la duda que evita la comprobación.
    Y cero es lo mismo que nada: una pieza de las de antes, que se dio de alta
    cuando la casa no tenía sedes, no tiene ninguna apuntada.
    """
    texto = (valor or "").strip()
    numero, barra, sede = texto.rpartition("|")
    if not barra:
        return texto, None
    sede = sede.strip()
    return numero.strip(), ((int(sede) or None) if sede.isdigit() else None)


@app.post("/traslados/pieza", response_class=HTMLResponse)
def send_primal(request: Request, serial: str = Form(...), site: str = Form(...),
                note: str = Form(""), csrf: str = Form(""), envio: str = Form(""),
                ctx=Depends(needs(perms.TRANSFER)), session: Session = Depends(get_db)):
    """[00258] La pieza entera se va a otra sede, con su número y su coste."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    repetido = _ya_estaba(request, session, user, envio, "/traslados")
    if repetido is not None:
        return repetido
    numero, visto_en = _donde_se_veia(serial)
    try:
        sent = sites.send_primal(session, user, numero, int(site or 0),
                                 note=note.strip() or None, desde=visto_en)
    except (sites.SiteError, ValueError) as e:
        return _transfers(request, user, auth_session, session, error=_dicho(e, lang_for(request, session, user)))
    return _hecho(auth_session, "/traslados",
                  i18n.t(lang, "m.tr.sent_primal", serial=sent.serial,
                         site=sent.to_site.name))


@app.post("/traslados/corte", response_class=HTMLResponse)
def send_cut(request: Request, serial: str = Form(...), g: str = Form(""),
             kg: str = Form(""),
             site: str = Form(...), note: str = Form(""), csrf: str = Form(""),
             envio: str = Form(""),
             ctx=Depends(needs(perms.TRANSFER)), session: Session = Depends(get_db)):
    """[00259] Cortes a otra sede: el lote entero, o unos kilos partiendo el lote."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    repetido = _ya_estaba(request, session, user, envio, "/traslados")
    if repetido is not None:
        return repetido
    numero, visto_en = _donde_se_veia(serial)
    try:
        # [00394] `kg` es el nombre de antes: de la cola de un teléfono con la
        # pantalla vieja, donde esa casilla se escribía en kilos.
        sent = sites.send_cut(session, user, numero,
                              pesos.de_dos(g, kg, 0.0) or 0.0,
                              int(site or 0), note=note.strip() or None,
                              desde=visto_en)
    except (sites.SiteError, ValueError) as e:
        return _transfers(request, user, auth_session, session, error=_dicho(e, lang_for(request, session, user)))
    partido = (i18n.t(lang, "m.tr.split", serial=sent.new_serial) if sent.new_serial else "")
    return _hecho(auth_session, "/traslados",
                  i18n.t(lang, "m.tr.sent_cut", kg=f"{sent.kg:.10g}", label=sent.label,
                         site=sent.to_site.name, split=partido))


@app.get("/sedes", response_class=HTMLResponse)
def sites_page(request: Request, ctx=Depends(needs(perms.TEAM)),
               session: Session = Depends(get_db), done: str = "", error: str = ""):
    """[00260] El obrador y los locales de la casa, y quién trabaja en cada uno."""
    user, auth_session = ctx
    sites.main(session, user.restaurant_id)      # una casa siempre tiene una sede
    return page(request, "sites.html", user, auth_session, session, done=done, error=error,
                rows=sites.all_sites(session, user.restaurant_id, active=False),
                kinds=list(SiteKind), people=sites.people(session, user.restaurant_id))


@app.post("/sedes/nueva")
def create_site(request: Request, name: str = Form(...), kind: str = Form("OUTLET"),
                address: str = Form(""), csrf: str = Form(""),
                ctx=Depends(needs(perms.TEAM)), session: Session = Depends(get_db)):
    """[00261] Abre una sede nueva: un local, o el obrador."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    try:
        site = sites.create(session, user, name,
                            kind=SiteKind[kind] if kind in SiteKind.__members__
                            else SiteKind.OUTLET, address=address)
    except (sites.SiteError, ValueError) as e:
        return RedirectResponse(f"/sedes?error={e}", status_code=303)
    return RedirectResponse(
        f"/sedes?done={i18n.t(lang, 'm.si.created', name=site.name)}", status_code=303)


@app.post("/sedes/{site_id}/estado")
def toggle_site(site_id: int, request: Request, csrf: str = Form(""),
                ctx=Depends(needs(perms.TEAM)), session: Session = Depends(get_db)):
    """[00262] Abre o cierra una sede."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    site = _own(session, user, Site, site_id, request)
    try:
        sites.set_active(session, user, site.id, not site.active)
    except sites.SiteError as e:
        return RedirectResponse(f"/sedes?error={e}", status_code=303)
    return RedirectResponse("/sedes", status_code=303)


@app.get("/sedes/{site_id}/minimos", response_class=HTMLResponse)
def site_pars_page(site_id: int, request: Request, ctx=Depends(needs(perms.TEAM)),
                   session: Session = Depends(get_db), done: str = "", error: str = ""):
    """[00263] Los mínimos de esa sede: lo que la casa dice, y lo que allí es distinto."""
    user, auth_session = ctx
    site = _own(session, user, Site, site_id, request)
    suyos = sites.pars_of(session, user.restaurant_id, site.id)
    skus = sorted({p.sku for p in session.query(Primal)
                   .filter_by(restaurant_id=user.restaurant_id) if p.sku}
                  | set(suyos.primals))
    return page(request, "site_pars.html", user, auth_session, session, done=done,
                error=error, site=site, pars=suyos, skus=skus,
                cuts=meat.cuts(session, user.restaurant_id),
                house={p.sku: p.min_pieces for p in session.query(PrimalPar)
                       .filter_by(restaurant_id=user.restaurant_id)})


@app.post("/sedes/{site_id}/minimos", response_class=HTMLResponse)
async def save_site_pars(site_id: int, request: Request, ctx=Depends(needs(perms.TEAM)),
                         session: Session = Depends(get_db)):
    """[00264] Guarda de una vez lo que esa sede quiere tener siempre. Vacío es «lo de la casa»."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)
    site = _own(session, user, Site, site_id, request)
    try:
        for key, value in form.multi_items():
            crudo = str(value).strip()
            numero = _num(crudo) if crudo else None
            if key.startswith("cut:"):
                sites.set_par(session, user, site.id, ingredient_id=int(key.split(":", 1)[1]),
                              minimum=numero)
            elif key.startswith("sku:"):
                sites.set_par(session, user, site.id, sku=key.split(":", 1)[1], minimum=numero)
    except (sites.SiteError, ValueError) as e:
        return RedirectResponse(f"/sedes/{site.id}/minimos?error={e}", status_code=303)
    return RedirectResponse(
        f"/sedes/{site.id}/minimos?done={i18n.t(lang, 'm.si.pars_saved')}", status_code=303)


@app.post("/manager/equipo/{user_id}/sede")
def change_site(user_id: int, request: Request, site: str = Form(""), csrf: str = Form(""),
                ctx=Depends(needs(perms.TEAM)), session: Session = Depends(get_db)):
    """[00265] A qué sede pertenece esa persona. Sin sede, ve la casa entera.

    Y por eso esta ruta manda tanto como la del nivel: «manager general» es un
    manager **sin sede**, así que quitarle la sede a alguien es ascenderlo a
    jefe de toda la casa. El encargado de un local podía quitarse la suya y
    acto seguido meter a la dueña en un local. Tres peticiones y la casa
    cambiaba de manos.
    """
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    target = _own(session, user, User, user_id, request)
    if target.id == user.id or not perms.can_manage(user, target):
        raise HTTPException(status_code=403, detail=i18n.t(lang, "pass.not_yours"))
    try:
        sites.assign(session, user, target, int(site) if site.strip() else None)
    except (sites.SiteError, ValueError) as e:
        return RedirectResponse(f"/sedes?error={e}", status_code=303)
    return RedirectResponse("/sedes", status_code=303)


# =============================================================== CORTES
@app.get("/cortes", response_class=HTMLResponse)
def cuts_page(request: Request, ctx=Depends(needs(perms.STOCK)),
              session: Session = Depends(get_db), error: str = ""):
    """[00266] El catálogo de cortes, con lo que queda de cada uno y a cómo sale."""
    user, auth_session = ctx
    return page(request, "cuts.html", user, auth_session, session, error=error,
                rotations=list(Rotation), modes=list(ConsumptionMode),
                cuts=meat.cuts(session, user.restaurant_id),
                stock=costing.stock_on_hand(session, user.restaurant_id),
                costs=costing.unit_costs(session, user.restaurant_id))


@app.post("/cortes/nuevo")
def new_cut(request: Request, name: str = Form(...), min_stock: str = Form(""),
            rotation: str = Form("FEFO"), consumption: str = Form("RECIPE"),
            sold_by_weight: str = Form(""), csrf: str = Form(""),
            ctx=Depends(needs(perms.CATALOGUE)), session: Session = Depends(get_db)):
    """[00267] Da de alta un corte nuevo en el catálogo."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        meat.create_cut(session, user, name, min_stock=_num(min_stock),
                        sold_by_weight=bool(sold_by_weight),
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
    """[00268] Da de alta un artículo de compra de ese corte."""
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
    """[00269] La carta: los platos y lo que lleva cada uno."""
    user, auth_session = ctx
    return page(request, "menu.html", user, auth_session, session, error=error,
                rows=meat.menu(session, user.restaurant_id),
                cuts=meat.cuts(session, user.restaurant_id))


@app.post("/carta/nuevo")
def new_dish(request: Request, name: str = Form(...), cut_id: int = Form(...),
             grams: str = Form(...), sale_price: str = Form(""), vat_pct: str = Form("0"),
             pos_code: str = Form(""), pos_name: str = Form(""),
             by_weight: str = Form(""), price_per_kg: str = Form(""), csrf: str = Form(""),
             ctx=Depends(needs(perms.MENU)), session: Session = Depends(get_db)):
    """[00270] Da de alta un plato, con su corte, sus gramos y su precio."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    try:
        meat.add_dish(session, user, name, cut_id, _g(grams, 0.0) or 0.0,
                      sale_price=_eur(sale_price), vat_pct=_num(vat_pct, 0.0) or 0.0,
                      pos_code=pos_code, pos_name=pos_name,
                      by_weight=bool(by_weight), price_per_kg=_eur(price_per_kg), lang=lang)
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/carta", status_code=303)


# =============================================== OTROS INGREDIENTES DEL PLATO
@app.get("/ingredientes", response_class=HTMLResponse)
def extras_page(request: Request, ctx=Depends(needs(perms.MENU)),
                session: Session = Depends(get_db), saved: int = 0, error: str = ""):
    """[00271] Lo que acompaña a la carne. Aquí solo se configura su coste."""
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
    """[00272] Da de alta un acompañamiento: guarnición, salsa, pan."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        meat.create_extra(session, user, name,
                          unit=Unit[unit] if unit in Unit.__members__ else Unit.KG,
                          cost=_eur(cost), portion_g=_g(portion))
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/ingredientes?saved=1", status_code=303)


@app.post("/ingredientes/{ingredient_id}/coste")
def update_extra_cost(ingredient_id: int, request: Request, cost: str = Form(...),
                      portion: str = Form(""), csrf: str = Form(""),
                      ctx=Depends(needs(perms.MENU)),
                      session: Session = Depends(get_db)):
    """[00273] Cambia lo que cuesta un acompañamiento y lo que se pone por ración."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        meat.set_extra_cost(session, user, ingredient_id, _eur(cost, 0.0) or 0.0,
                            portion_g=_g(portion, 0.0))
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/ingredientes?saved=1", status_code=303)


# ============================================================= EMPLATADO
def _dish(session: Session, user: User, code: str, request: Request) -> Recipe:
    """[00274] Trae un plato por su código, comprobando que es de esta casa."""
    dish = (session.query(Recipe)
            .filter_by(restaurant_id=user.restaurant_id, code=code).first())
    if dish is None:
        raise HTTPException(status_code=404,
                            detail=i18n.t(lang_for(request, session, user), "error.tpl_not_found"))
    return dish


@app.get("/carta/{code}", response_class=HTMLResponse)
def plate_page(code: str, request: Request, ctx=Depends(needs(perms.MENU)),
               session: Session = Depends(get_db), error: str = ""):
    """[00275] El emplatado: todo lo que va en el plato y lo que cuesta cada cosa."""
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
    """[00276] Añade un ingrediente al plato, con su cantidad y su merma."""
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
    """[00277] Quita un ingrediente del plato."""
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
    """[00278] Cambia los gramos de carne del plato.

    Es el número que más se toca de la carta y el que más manda en el food
    cost: veinte gramos de más en un plato que se vende cien veces al día son
    dos kilos diarios.
    """
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    dish = _dish(session, user, code, request)
    try:
        meat.set_plate_grams(session, user, dish, _g(grams, 0.0) or 0.0,
                             lang=lang_for(request, session, user))
    except (meat.MeatError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse(f"/carta/{code}", status_code=303)


# =============================================================== VENTAS
@app.get("/ventas", response_class=HTMLResponse)
def sales_page(request: Request, ctx=Depends(needs(perms.MENU)),
               session: Session = Depends(get_db), done: str = ""):
    """[00279] La pantalla de subir el parte de ventas de la caja."""
    user, auth_session = ctx
    return _sales(request, user, auth_session, session, done=done)


def _sales(request, user, auth_session, session, *, done="", error="", parsed=None,
           preview=None, business_date="", site=""):
    """[00280] Pinta las ventas, con lo leído del fichero antes de darlo por bueno.

    Sin sede se elige de cuál son estas ventas: el manager sube el fichero de
    cada local desde su despacho.
    """
    mia = sites.of_user(session, user)
    return page(request, "sales.html", user, auth_session, session, done=done, error=error,
                parsed=parsed, preview=preview, business_date=business_date,
                mine=mia, site=site,
                # [00395] Sin sede se elige de cuál son estas ventas: el manager sube el
                # fichero de cada local desde su despacho.
                sites=[] if mia else sites.all_sites(session, user.restaurant_id),
                rows_json=json.dumps([[p["key"], p["units"], p["kg"]] for p in preview
                                      if p["dish"]]) if preview else "",
                mapping=(session.query(PosProduct).filter_by(restaurant_id=user.restaurant_id)
                         .order_by(PosProduct.pos_name).all()))


@app.post("/ventas/fichero", response_class=HTMLResponse)
async def read_sales_file(request: Request, ctx=Depends(needs(perms.MENU)),
                          session: Session = Depends(get_db)):
    """[00281] Lee el parte de ventas del POS y enseña lo entendido. No descuenta nada."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return _sales(request, user, auth_session, session,
                      error=i18n.t(lang_for(request, session, user), "sale.no_file"))
    try:
        parsed = pos_import.parse(await upload.read(), upload.filename)
    except (pos_import.ImportError_, ValueError) as e:
        return _sales(request, user, auth_session, session, error=_dicho(e, lang_for(request, session, user)))

    index = costing.pos_index(session, user.restaurant_id)
    preview = []
    for row in parsed.rows:
        product = index.get(costing._norm(row.code)) or index.get(costing._norm(row.name))
        preview.append({
            "key": (product.pos_name if product else row.key),
            "label": row.name or row.code, "code": row.code,
            "units": row.units, "kg": row.kg, "amount": row.amount,
            "dish": product.recipe.name if product and product.recipe else None,
            "by_weight": bool(product and product.recipe and product.recipe.by_weight)})
    return _sales(request, user, auth_session, session, parsed=parsed, preview=preview,
                  business_date=(form.get("business_date") or "").strip(),
                  site=(form.get("site") or "").strip())


@app.post("/ventas")
async def import_sales(request: Request, ctx=Depends(needs(perms.MENU)),
                       session: Session = Depends(get_db)):
    """[00282] Lo vendido en el POS descuenta de cámara por rotación, plato a plato."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)

    sales: list[tuple[str, float, float | None]] = []
    confirmed = form.get("rows")
    if confirmed:
        # [00396] Viene de un fichero ya leído y enseñado: se vuelve a validar, que lo
        # que llega de un formulario no se cree por venir de nosotros.
        try:
            for line in json.loads(confirmed)[:pos_import.MAX_ROWS]:
                name, units, kg = str(line[0])[:160], float(line[1]), line[2]
                if units > 0:
                    sales.append((name, units, float(kg) if kg else None))
        except (TypeError, ValueError, IndexError, json.JSONDecodeError):
            return _sales(request, user, auth_session, session,
                          error=i18n.t(lang, "sale.bad_rows"))
    for key, value in form.multi_items():
        if not key.startswith("units:") or not str(value).strip():
            continue
        name = key.split(":", 1)[1]
        try:
            units = _g(value, 0.0) or 0.0
            # [00397] Lo que se cobra por kilo llega con su peso: son los gramos que
            # se cortaron, no los de la carta.
            grams = _g(form.get(f"grams:{name}"), None)
        except ValueError:
            continue
        if units > 0:
            sales.append((name, units, round(grams / 1000, 6) if grams else None))
    on = form.get("business_date")
    sede = (form.get("site") or "").strip()
    try:
        result = costing.consume_sales(
            session, user, sales,
            on=(date.fromisoformat(on) if on else _cuando(form, session, user)),
            lang=lang, site_id=int(sede) if sede else None)
    except ValueError as e:
        return _sales(request, user, auth_session, session, error=_dicho(e, lang_for(request, session, user)))
    summary = i18n.t(lang, "sale.done", n=result.lines, cost=f"{result.cost:.2f}")
    if result.site:
        summary += " · " + result.site
    if result.weighed_kg:
        summary += " · " + i18n.t(lang, "sale.weighed", kg=f"{result.weighed_kg:.10g}")
    if result.missing_weight:
        summary += " · " + i18n.t(lang, "sale.no_weight",
                                  products=", ".join(result.missing_weight[:4]))
    return _hecho(auth_session, "/ventas", summary)


# =========================================================== INVENTARIO
@app.get("/inventario", response_class=HTMLResponse)
def inventory_page(request: Request, ctx=Depends(needs(perms.COUNT)),
                   session: Session = Depends(get_db), done: str = ""):
    """[00283] La hoja de inventario que esté abierta en esa sede.

    Cada cámara se cuenta por su cuenta: la hoja abierta es la de tu sede, no
    la del obrador.
    """
    user, auth_session = ctx
    # [00398] Cada cámara se cuenta por su cuenta: la hoja abierta es la de tu sede.
    mia = sites.of_user(session, user)
    suya = mia.id if mia else None
    open_count = inventory.open_now(session, user.restaurant_id, suya)
    return page(request, "inventory.html", user, auth_session, session,
                recuperada=bool(done),
                count=open_count, site=mia,
                counters={u.id: u.name for u in
                          session.query(User).filter_by(restaurant_id=user.restaurant_id)},
                last=inventory.last_closed(session, user.restaurant_id, suya),
                month=inventory.monthly_status(session, user.restaurant_id, site_id=suya),
                sites=[] if mia else sites.all_sites(session, user.restaurant_id),
                site_names={x.id: x.name for x in
                            sites.all_sites(session, user.restaurant_id, active=False)},
                periods=list(CountPeriod),
                items=(session.query(IngredientItem)
                       .filter_by(restaurant_id=user.restaurant_id, active=True)
                       .order_by(IngredientItem.name).all()))


@app.post("/inventario/abrir")
def open_inventory(request: Request, period: str = Form("MONTHLY"), site: str = Form(""),
                   csrf: str = Form(""), ctx=Depends(needs(perms.INVENTORY)),
                   session: Session = Depends(get_db)):
    """[00284] Abre una hoja de inventario."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    mia = sites.of_user(session, user)
    elegida = int(site) if site.strip() else None
    try:
        inventory.open_count(session, user,
                             CountPeriod[period] if period in CountPeriod.__members__
                             else CountPeriod.MONTHLY,
                             site_id=mia.id if mia else elegida)
    except inventory.InventoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/inventario", status_code=303)


def _open_sheet(session: Session, user, form_id: str = "", lang: str = "es") -> MeatCount:
    """[00285] La hoja de inventario **de tu cámara**, no la primera que aparezca.

    En una casa con obrador y locales hay varias hojas abiertas a la vez, una
    por cámara. Buscando «la abierta» sin más, el carnicero del local acababa
    escribiendo el peso de su costillar en la hoja del obrador: la suya salía
    sin contar y la otra, con una pieza que allí no está.

    Y si la pantalla venía de una pestaña vieja, el número de hoja que trae no
    es el de ahora: mejor un aviso que apuntar en la hoja equivocada.
    """
    mia = sites.of_user(session, user)
    hoja = inventory.open_now(session, user.restaurant_id, mia.id if mia else None)
    if hoja is None:
        raise HTTPException(status_code=404, detail="")
    if form_id.strip() and form_id.strip().isdigit() and int(form_id) != hoja.id:
        raise HTTPException(status_code=409, detail=i18n.t(lang, "inv.other_sheet"))
    return hoja


@app.post("/inventario/contar")
async def record_count(request: Request, ctx=Depends(needs(perms.COUNT)),
                       session: Session = Depends(get_db)):
    """[00286] Contar lo puede hacer cualquiera: se hace en la cámara, con la balanza."""
    user, auth_session = ctx
    form = await request.form()
    _guard(request, session, user, auth_session, form.get("csrf"))
    lang = lang_for(request, session, user)
    repetido = _ya_estaba(request, session, user, str(form.get("envio") or ""), "/inventario")
    if repetido is not None:
        return repetido
    count = _open_sheet(session, user, str(form.get("hoja") or ""), lang)
    # [00399] Lo que no se pudo apuntar, para decirlo. Una hoja de inventario manda
    # cincuenta líneas de golpe y una mal escrita no puede tumbar las otras
    # cuarenta y nueve; pero callársela es peor que tumbarlas: la pieza se
    # queda «sin contar» y quien la acaba de pesar se ha ido de la cámara
    # convencido de que la contó. En el cierre aparece como que falta.
    mudas: list[str] = []
    for key, value in form.multi_items():
        # [00400] `g:` es la casilla de ahora; `kg:` la de antes, que solo puede venir
        # de la cola de un teléfono con la pantalla vieja abierta.
        en_gramos = key.startswith("g:")
        if not (en_gramos or key.startswith("kg:")) or not str(value).strip():
            continue
        serial = key.split(":", 1)[1]
        try:
            pesado = (pesos.leer(value, 0.0) if en_gramos
                      else _num(value, 0.0)) or 0.0
            inventory.record(session, user, count, serial, pesado,
                             pieces=int(_num(form.get(f"pieces:{serial}"), 0) or 0) or None,
                             lang=lang)
        except (inventory.InventoryError, ValueError) as e:
            mudas.append(f"{serial}: {_dicho(e, lang)}")
    if mudas:
        return _hecho(auth_session, "/inventario",
                      i18n.t(lang, "inv.not_written", n=len(mudas),
                             cuales=" · ".join(mudas[:4])))
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/cerrar")
def close_inventory(request: Request, csrf: str = Form(""), hoja: str = Form(""),
                    ctx=Depends(needs(perms.INVENTORY)),
                    session: Session = Depends(get_db)):
    """[00287] Cierra el inventario: lo contado pasa a ser lo que hay."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    count = _open_sheet(session, user, hoja, lang_for(request, session, user))
    try:
        inventory.close_count(session, user, count, lang=lang_for(request, session, user))
    except inventory.InventoryError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/cancelar")
def cancel_inventory(request: Request, reason: str = Form(""), csrf: str = Form(""),
                     hoja: str = Form(""),
                     ctx=Depends(needs(perms.INVENTORY)), session: Session = Depends(get_db)):
    """[00288] Cancela un inventario a medias, diciendo por qué."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    count = _open_sheet(session, user, hoja, lang_for(request, session, user))
    try:
        inventory.cancel_count(session, user, count, reason)
    except inventory.InventoryError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return RedirectResponse("/inventario", status_code=303)


@app.post("/inventario/recuperar")
def recover_piece(request: Request, serial: str = Form(...), g: str = Form(""),
                  kg: str = Form(""),
                  note: str = Form(""), csrf: str = Form(""),
                  ctx=Depends(needs(perms.FIX)), session: Session = Depends(get_db)):
    """[00289] La pieza ha aparecido: vuelve al stock, con quién y por qué."""
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
                unit_cost: float = Form(...), expiry: str = Form(...),
                note: str = Form(""), csrf: str = Form(""),
                ctx=Depends(needs(perms.FIX)), session: Session = Depends(get_db)):
    """[00290] Da de alta algo que apareció y no constaba en ninguna parte."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        # [00401] `g` son gramos; `kg` es el nombre de antes, y la unidad del
        # artículo cuando ese artículo no se pesa.
        inventory.adopt(session, user, serial.strip(), item_id,
                        kg=pesos.de_dos(g, kg, 0.0) or 0.0, unit_cost=unit_cost,
                        expiry=date.fromisoformat(expiry), note=note.strip() or None,
                        lang=lang_for(request, session, user))
    except (inventory.InventoryError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/inventario?done=1", status_code=303)


# ================================================================ MERMA
def _waste_page(request, user, auth_session, session, *, result=None, error="",
                serial="", previo=None, aviso=False):
    """[00291] Todo lo que se tira, junto: lo de cámara y lo que se va limpiando piezas."""
    lines = waste.everything(session, user.restaurant_id)
    return page(request, "waste.html", user, auth_session, session,
                result=result, error=error, serial=serial, lines=lines,
                previo=(previo or {}), aviso=aviso,
                totals=waste.totals(lines),
                ingredients=meat.cuts(session, user.restaurant_id))


@app.get("/merma", response_class=HTMLResponse)
def waste_page(request: Request, ctx=Depends(needs(perms.WASTE)),
               session: Session = Depends(get_db), serial: str = "", aviso: int = 0):
    """[00292] La pantalla de apuntar merma."""
    user, auth_session = ctx
    return _waste_page(request, user, auth_session, session, serial=serial,
                       aviso=bool(aviso))


@app.post("/merma", response_class=HTMLResponse)
def record_waste(request: Request, g: str = Form(""), kg: str = Form(""),
                 serial: str = Form(""),
                 ingredient_id: str = Form(""), pieces: str = Form(""), reason: str = Form(""),
                 csrf: str = Form(""), envio: str = Form(""), cuando: str = Form(""),
                 ctx=Depends(needs(perms.WASTE)),
                 session: Session = Depends(get_db)):
    """[00293] Apunta lo que se ha tirado, con su motivo."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    repetido = _ya_estaba(request, session, user, envio, "/merma")
    if repetido is not None:
        return repetido
    try:
        result = waste.record(
            session, user, kg=pesos.de_dos(g, kg, 0.0) or 0.0,
            serial=serial.strip() or None,
            ingredient_id=int(ingredient_id) if ingredient_id.strip() else None,
            pieces=int(pieces) if pieces.strip() else None,
            reason=reason.strip() or None, on=_cuando(cuando, session, user),
            lang=lang_for(request, session, user))
    except (waste.WasteError, ValueError) as e:
        # [00402] Con lo que había escrito puesto otra vez: un error en los kilos no
        # puede obligar a volver a buscar el número de la pieza y el motivo.
        return _waste_page(request, user, auth_session, session,
                           error=_dicho(e, lang_for(request, session, user)),
                           serial=serial.strip(),
                           previo={"g": g, "pieces": pieces, "reason": reason,
                                   "ingredient_id": ingredient_id})
    # [00403] Guardado. Se contesta con una redirección y el recado viaja aparte: así
    # la pantalla que se queda delante es una que se puede recargar mil veces
    # sin volver a tirar los mismos kilos.
    lang = lang_for(request, session, user)
    partes = [i18n.t(lang, "waste.done"),
              f"{result.ingredient} · {result.kg:.3f} kg"]
    if result.pieces:
        partes.append(f"{result.pieces} pz")
    if perms.can(user, perms.MONEY):
        partes.append(f"{result.cost:.2f}")
    dicho = " · ".join(partes) + " — " + (
        i18n.t(lang, "waste.absorbed", pct=butchery.ceil_pct(result.cost_increase_pct))
        if result.absorbed else i18n.t(lang, "waste.all_gone"))
    # [00404] Sin nada que absorba el coste el aviso no es una buena noticia: se dice
    # con el color de aviso y no con el de hecho.
    destino = "/merma" if result.absorbed else "/merma?aviso=1"
    return _hecho(auth_session, destino, dicho)


# ========================================================= TRAZABILIDAD
@app.get("/trazabilidad", response_class=HTMLResponse)
def tracing_page(request: Request, ctx=Depends(needs(perms.STOCK)),
                 session: Session = Depends(get_db), serial: str = ""):
    """[00294] La historia de una pieza, de la recepción al plato."""
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
               session: Session = Depends(get_db), done: str = "", error: str = ""):
    """[00295] La consola del dueño: solicitudes, casas, el recibo del mes y la tarifa."""
    user, auth_session = ctx
    rows = billing.requests(session)
    privacy.note_access(session, user, len(rows))     # mirar datos deja huella
    return page(request, "admin.html", user, auth_session, session, done=done,
                error=error, tarifa=tarifa.fila(session),
                precio=tarifa.publicada(session), monedas=money.MONEDAS,
                requests=privacy.readable(rows),
                accounts=billing.accounts(session),
                groups=billing.grouped(session),
                statuses=list(RequestStatus), plans=list(Plan),
                trial_left=billing.trial_left,
                mail_ready=mailer.configured(),
                encryption_on=privacy.encryption_on(),
                # [01673] La página que explica el recorrido de una pieza, para
                # mandársela a quien pregunte cómo funciona esto.
                recorrido=config.PAGINA_RECORRIDO,
                # [00405] El parte de la última actualización de la base. Una
                # migración que se calla es peor que una que falla: el
                # programa arranca, parece que todo está bien, y lo que falta
                # se descubre semanas después en una pantalla que no tiene
                # nada que ver. Aquí se ve el día que pasa.
                migracion=db.pendientes(),
                retention_days=privacy.RETENTION_DAYS)


@app.post("/admin/grupo/pago")
def group_payment(request: Request, group: str = Form(...), action: str = Form("paid"),
                  paid_until: str = Form(""), note: str = Form(""), csrf: str = Form(""),
                  ctx=Depends(require_owner), session: Session = Depends(get_db)):
    """[00296] Un grupo se cobra de una vez: es una empresa y un recibo, no cinco."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    casas = (session.query(Restaurant)
             .filter(Restaurant.group_name == group.strip(),
                     Restaurant.platform.isnot(True)).all())
    if not casas:
        raise HTTPException(status_code=404, detail="Ese grupo no existe")
    hasta = date.fromisoformat(paid_until) if paid_until.strip() else None
    for casa in casas:
        if action == "paid":
            billing.mark_paid(session, user, casa, until=hasta, note=note.strip() or None)
        elif action == "past_due":
            billing.mark_unpaid(session, user, casa, note=note.strip() or None)
        elif action == "block":
            billing.mark_unpaid(session, user, casa, block=True, note=note.strip() or None)
    return RedirectResponse("/admin?done=1", status_code=303)


@app.post("/admin/tarifa")
def save_pricing(request: Request, currency: str = Form("EUR"),
                 per_outlet: str = Form(""), extra_outlet: str = Form(""),
                 sale_on: str = Form(""), sale_price: str = Form(""),
                 sale_label: str = Form(""), sale_until: str = Form(""),
                 yearly_on: str = Form(""), yearly_months: str = Form("10"),
                 csrf: str = Form(""), ctx=Depends(require_owner),
                 session: Session = Depends(get_db)):
    """[00297] El precio de la web, cambiado sin desplegar nada.

    Es lo que permite salir con una rebaja de fundador y quitarla el día que
    haya clientes suficientes, que es una decisión de negocio y no de código.
    """
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        hasta = date.fromisoformat(sale_until) if sale_until.strip() else None
    except ValueError:
        return RedirectResponse("/admin?error=fecha#tarifa", status_code=303)
    try:
        tarifa.guardar(
            session, user, currency=currency,
            per_outlet=_eur(per_outlet, 0.0) or 0.0,
            extra_outlet=_eur(extra_outlet),
            sale_on=bool(sale_on), sale_price=_eur(sale_price),
            sale_label=sale_label, sale_until=hasta,
            yearly_on=bool(yearly_on), yearly_months=_num(yearly_months, 10.0) or 10.0)
    except tarifa.TarifaError as e:
        return RedirectResponse(f"/admin?error={quote(str(e))}#tarifa", status_code=303)
    return RedirectResponse("/admin?done=1#tarifa", status_code=303)


@app.post("/admin/solicitud/{request_id}/borrar")
def erase_request(request_id: int, request: Request, csrf: str = Form(""),
                  ctx=Depends(require_owner), session: Session = Depends(get_db)):
    """[00298] Derecho de supresión: se borra la solicitud y queda que se borró."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    privacy.erase_request(session, user, request_id)
    return RedirectResponse("/admin?done=1", status_code=303)


@app.get("/admin/solicitud/{request_id}/datos")
def export_request(request_id: int, request: Request, ctx=Depends(require_owner),
                   session: Session = Depends(get_db)):
    """[00299] Derecho de portabilidad: todo lo que guardamos de esa persona."""
    user, auth_session = ctx
    row = session.get(AccessRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="")
    privacy.audit(session, user, f"request:{request_id}", "EXPORTED", row.restaurant_name)
    return JSONResponse(privacy.export_request(row))


@app.post("/admin/solicitudes/purgar")
def purge_requests(request: Request, csrf: str = Form(""), ctx=Depends(require_owner),
                   session: Session = Depends(get_db)):
    """[00300] Lo que no llegó a cuenta no se guarda para siempre."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    privacy.purge(session, user)
    return RedirectResponse("/admin?done=1", status_code=303)


@app.post("/admin/solicitud/{request_id}/estado")
def set_request_status(request_id: int, request: Request, status: str = Form(...),
                       csrf: str = Form(""), ctx=Depends(require_owner),
                       session: Session = Depends(get_db)):
    """[00301] El dueño de la plataforma atiende o rechaza una solicitud de acceso."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    if status in RequestStatus.__members__:
        billing.set_request_status(session, request_id, RequestStatus[status])
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/casa")
async def create_account(request: Request, ctx=Depends(require_owner),
                         session: Session = Depends(get_db)):
    """[00302] Da de alta la casa y la cuenta de su manager. Es el único camino."""
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
            group=form.get("group") or None,
            plan=Plan[plan] if plan in Plan.__members__ else Plan.SINGLE,
            outlets=int(_num(form.get("outlets"), 1) or 1),
            monthly_fee=_eur(form.get("monthly_fee")),
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
    """[00303] La pestaña del recibo: pagado, fallado o bloqueado."""
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
    """[00304] Apunta el método de pago que devolvió la pasarela y arranca la prueba."""
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
    """[00305] El dueño de la plataforma cancela una casa, diciendo por qué."""
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
                session: Session = Depends(get_db), show: str = "open"):
    """[00306] Lo que ha salido mal y todavía no ha arreglado nadie.

    Esta pantalla estuvo saliendo vacía: la ruta mandaba la lista con un
    nombre y la plantilla la leía con otro, así que no fallaba nada —no había
    error, no había aviso— y simplemente no aparecía ni una línea. La carne
    que llegaba caliente, la pieza que se pasaba de fecha y la merma de
    maduración se guardaban bien y no las veía nadie, que para un registro
    sanitario es exactamente igual que no guardarlas.
    """
    user, auth_session = ctx
    q = session.query(Alert).filter(Alert.restaurant_id == user.restaurant_id)
    if show == "open":
        q = q.filter(Alert.acknowledged_at.is_(None))
    rows = q.order_by(Alert.created_at.desc()).limit(200).all()
    names = {u.id: u.name
             for u in session.query(User).filter_by(restaurant_id=user.restaurant_id)}
    return page(request, "alerts.html", user, auth_session, session,
                rows=rows, show=show, names=names)


@app.post("/manager/alertas/{alert_id}/cerrar")
def close_alert(alert_id: int, request: Request, resolution: str = Form(...),
                csrf: str = Form(""), ctx=Depends(require_manager_user),
                session: Session = Depends(get_db)):
    """[00307] Cierra un aviso diciendo cómo se resolvió.

    Un aviso que se cierra sin más vuelve a salir la semana que viene y nadie
    recuerda qué se hizo la vez pasada.
    """
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        service.acknowledge_alert(session, user, alert_id, resolution,
                                  lang=lang_for(request, session, user))
    except PermissionError:
        # [01716] Un aviso de otra casa. `service.acknowledge_alert` levanta
        # `PermissionError`, que no estaba en esta lista, así que salía por el
        # suelo del framework como «Internal Server Error» en inglés. Se
        # contesta lo mismo que las demás puertas: eso no existe.
        raise HTTPException(status_code=404, detail="") from None
    except (ValueError, service.ValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return RedirectResponse("/manager/alertas", status_code=303)


@app.get("/manager/equipo", response_class=HTMLResponse)
def team_page(request: Request, ctx=Depends(needs(perms.TEAM)),
              session: Session = Depends(get_db), error: str = "", done: str = ""):
    """[00308] El equipo de la casa: quién es quién y qué puede hacer cada uno."""
    user, auth_session = ctx
    restaurant = session.get(Restaurant, user.restaurant_id)
    rows = (session.query(User).filter_by(restaurant_id=user.restaurant_id)
            .order_by(User.name).all())
    # [00406] Los niveles que esta persona puede repartir, de la misma regla que
    # comprueba la ruta: el general reparte managers de local, el de local no.
    roles = perms.grantable_roles(user)
    return page(request, "team.html", user, auth_session, session, error=error,
                done=done, restaurant=restaurant, rows=rows, roles=roles)


@app.post("/manager/equipo/nueva")
def create_team_user(request: Request, name: str = Form(...), email: str = Form(...),
                     password: str = Form(...), role: str = Form("BUTCHER"),
                     csrf: str = Form(""), ctx=Depends(needs(perms.TEAM)),
                     session: Session = Depends(get_db)):
    """[00309] El manager da de alta a su gente: una cuenta, un nivel, una contraseña."""
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
    """[00310] Cambia el nivel de una persona.

    La pantalla no enseña los niveles que no tocan, pero una pantalla sin
    desplegable no es una puerta cerrada: el formulario se manda a mano, y por
    eso se vuelve a comprobar aquí. Nadie se cambia el nivel a sí mismo.
    """
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    target = _own(session, user, User, user_id, request)
    if target.id == user.id:
        raise HTTPException(status_code=400, detail=i18n.t(lang, "error.no_self_role"))
    # [00407] La plantilla no enseña los niveles que no tocan, pero una pantalla sin
    # desplegable no es una puerta cerrada: el formulario se manda a mano. Sin
    # esto, un manager se fabricaba un dueño de la plataforma en una línea.
    nuevo = Role[role] if role in Role.__members__ else None
    if nuevo is None or nuevo not in perms.grantable_roles(user) \
            or not perms.can_manage(user, target):
        raise HTTPException(status_code=403, detail=i18n.t(lang, "pass.not_yours"))
    target.role = nuevo
    return RedirectResponse("/manager/equipo", status_code=303)


@app.post("/manager/equipo/{user_id}/activar")
def toggle_user(user_id: int, request: Request, csrf: str = Form(""),
                ctx=Depends(require_manager_user), session: Session = Depends(get_db)):
    """[00311] Da de alta o de baja a una persona. A uno mismo, no."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    target = _own(session, user, User, user_id, request)
    if target.id == user.id:
        raise HTTPException(status_code=400, detail=i18n.t(lang, "error.no_self_disable"))
    if not perms.can_manage(user, target):
        raise HTTPException(status_code=403, detail=i18n.t(lang, "pass.not_yours"))
    target.active = not target.active
    return RedirectResponse("/manager/equipo", status_code=303)


@app.get("/notificaciones", response_class=HTMLResponse)
def notifications_page(request: Request, ctx=Depends(require_user),
                       session: Session = Depends(get_db)):
    """[00312] Los avisos de esa persona. Al abrirlos, quedan leídos."""
    user, auth_session = ctx
    rows = service.recent_notifications(session, user.id)
    response = page(request, "notifications.html", user, auth_session, session,
                    notifications=rows)
    service.mark_all_read(session, user.id)
    return response


@app.get("/api/notificaciones")
def notifications_api(request: Request, ctx=Depends(require_user),
                      session: Session = Depends(get_db)):
    """[00313] Los últimos avisos y cuántos hay sin leer, para la campanita.

    El número va: sin él, la pantalla no sabe cuál es nueva y la alerta crítica
    que debía saltar al teléfono no saltaba nunca.
    """
    user, _ = ctx
    rows = service.recent_notifications(session, user.id, limit=5)
    # [00408] El número va: sin él, la pantalla no sabe cuál es nueva y la alerta
    # crítica que debía saltar al teléfono no saltaba nunca.
    return JSONResponse({"unread": service.unread_count(session, user.id),
                         "items": [{"id": n.id, "title": n.title, "body": n.body,
                                    "severity": n.severity.value, "read": n.read_at is not None}
                                   for n in rows]})


@app.get("/api/novedades")
def news_api(request: Request, desde: int = 0, ctx=Depends(require_user),
             session: Session = Depends(get_db)):
    """[00314] Lo que ha pasado en la casa desde la última vez que este aparato miró.

    Lo suyo no se le cuenta —quien acaba de recibir ya sabe que ha recibido— y
    lo de otra sede tampoco. `ultimo` es el número de la última novedad haya
    salido o no en la lista: así el aparato avanza por encima de las suyas y no
    vuelve a preguntar por ellas cada treinta segundos.
    """
    user, _ = ctx
    mia = sites.of_user(session, user)
    lang = lang_for(request, session, user)
    filas = novedades.recientes(session, user.restaurant_id, desde_id=desde,
                                site_id=mia.id if mia else None, salvo_user=user.id)

    def contar(n):
        """[00340] Una novedad contada en el idioma de la casa, con su hora."""
        titulo, detalle = novedades.frase(lang, n)
        return {"id": n.id, "kind": n.kind, "titulo": titulo, "detalle": detalle,
                "texto": f"{titulo} · {detalle}",
                "cuando": n.created_at.replace(tzinfo=timezone.utc).isoformat()}

    return JSONResponse({
        "ultimo": novedades.ultimo_id(session, user.restaurant_id),
        "items": [contar(n) for n in filas]})


# ========================================================= CONFIGURACIÓN
@app.get("/configuracion", response_class=HTMLResponse)
def settings_page(request: Request, ctx=Depends(require_user),
                  session: Session = Depends(get_db), saved: int = 0, changed: int = 0,
                  off: int = 0, error: str = ""):
    # [00409] Sin `codes`: por la barra de direcciones no entran ni salen los códigos
    # de repuesto. Los pinta el POST que los crea, una vez y ahí se acabó.
    """[00315] La configuración de la casa: idioma, moneda, horario y temperaturas.

    Los códigos de repuesto no salen por la barra de direcciones: los pinta una
    sola vez el formulario que los crea.
    """
    user, auth_session = ctx
    # [00410] Si aún no la tiene puesta, se le propone un secreto para que lo meta en
    # el teléfono. Hasta que teclee un código no queda activada.
    if not user.totp_enabled and not user.totp_secret:
        user.totp_secret = twofactor.new_secret()
        session.flush()
    return _settings(request, user, auth_session, session, saved=bool(saved),
                     changed=bool(changed), off=bool(off), error=error)


def _settings(request: Request, user, auth_session, session, saved: bool = False,
              changed: bool = False, off: bool = False, error: str = "",
              codes: list | None = None):
    """[00316] La pantalla de configuración, que se pinta desde el GET y desde el POST."""
    restaurant = session.get(Restaurant, user.restaurant_id)
    return page(request, "settings.html", user, auth_session, session,
                restaurant=restaurant, saved=saved, changed=changed,
                off=off, error=error, pos_modes=list(PosMatch),
                currencies=money.MONEDAS,
                zonas=ZONAS, horas_cierre=list(range(jornada.MAXIMO + 1)),
                cierre=jornada.corte(restaurant),
                dias_descongelado=caducidad.dias(restaurant),
                impuesto=(impuestos.tipo_de(restaurant) or ""),
                bandas={"chilled": rangos.banda(Storage.CHILLED, restaurant),
                        "frozen": rangos.banda(Storage.FROZEN, restaurant)},
                version=version.actual(),
                codes=codes or [],
                tfa_uri=twofactor.uri(user.totp_secret or "", user.email,
                                      issuer=i18n.t(lang_for(request, session, user),
                                                    "m.app.title")),
                tfa_left=twofactor.recovery_left(user.recovery_codes))


@app.post("/configuracion")
def save_settings(request: Request, language: str = Form(...),
                  restaurant_language: str = Form(""), pos_match: str = Form(""),
                  currency: str = Form(""), timezone_name: str = Form("", alias="timezone"),
                  day_cut_hour: str = Form(""), thaw_days: str = Form(""),
                  chilled_min_c: str = Form(""), chilled_max_c: str = Form(""),
                  frozen_min_c: str = Form(""), frozen_max_c: str = Form(""),
                  tax_pct: str = Form(""), csrf: str = Form(""),
                  ctx=Depends(require_user), session: Session = Depends(get_db)):
    """[00317] Guarda la configuración de la casa y la de la persona."""
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
            if money.es_valida(currency):
                restaurant.currency = currency.upper()
            # [01628] El impuesto sobre lo que se gana. Vacío lo borra —«no lo digo»,
            # y entonces no se enseña ningún reparto—; un número que no es un
            # número deja el que había, que es lo prudente con algo de lo que
            # cuelga cuánto dinero se aparta.
            if tax_pct.strip() == "":
                restaurant.tax_pct = None
            else:
                try:
                    puesto = exacto.leer(tax_pct, decimales=2)
                except ValueError:
                    puesto = None
                if puesto is not None and 0 <= puesto <= impuestos.TOPE:
                    restaurant.tax_pct = float(puesto) or None
            # [00411] La zona se guarda solo si existe de verdad. Una mal escrita no
            # rompe nada —se lee como UTC— pero deja a la casa creyendo que ha
            # puesto la suya, y eso es peor que no haberla puesto.
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
            # [00412] Las bandas de llegada, límite a límite: quien aprieta solo el
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
    """[00318] Uno cambia la suya: hay que saber la de antes."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
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


@app.post("/configuracion/2fa/activar")
def enable_second_step(request: Request, code: str = Form(...), csrf: str = Form(""),
                       ctx=Depends(require_user), session: Session = Depends(get_db)):
    """[00319] Se activa tecleando un código: así se sabe que el teléfono ya lo tiene."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    if not user.totp_secret or not twofactor.verify(user.totp_secret, code):
        return RedirectResponse(f"/configuracion?error={i18n.t(lang, 'tfa.bad_code')}",
                                status_code=303)
    codigos = twofactor.new_recovery_codes()
    user.totp_enabled = True
    user.recovery_codes = twofactor.store_recovery(codigos)
    session.flush()
    # [00413] Los códigos NO viajan en la barra de direcciones. Cada uno vale como
    # segundo factor entero, y por ahí acababan en el historial de la tablet
    # de cocina y en el registro del proxy, en claro y para siempre. Se pinta
    # la pantalla aquí mismo: se ven una vez, se apuntan, y no quedan escritos
    # en ningún sitio por el que pasen.
    return _settings(request, user, auth_session, session, codes=codigos)


@app.post("/configuracion/2fa/quitar")
def disable_second_step(request: Request, password: str = Form(...), csrf: str = Form(""),
                        ctx=Depends(require_user), session: Session = Depends(get_db)):
    """[00320] Quitarla pide la contraseña: si alguien te deja la sesión abierta, no basta."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    if not auth.verify_password(password, user.password_hash or ""):
        return RedirectResponse(f"/configuracion?error={i18n.t(lang, 'auth.wrong_current')}",
                                status_code=303)
    user.totp_enabled = False
    user.totp_secret = None
    user.recovery_codes = None
    session.flush()
    return RedirectResponse("/configuracion?off=1", status_code=303)


@app.post("/manager/equipo/{user_id}/2fa")
def clear_team_second_step(user_id: int, request: Request, csrf: str = Form(""),
                           ctx=Depends(needs(perms.TEAM)), session: Session = Depends(get_db)):
    """[00321] Quien pierde el teléfono no puede quedarse fuera para siempre."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    target = _own(session, user, User, user_id, request)
    # [00414] Uno sobre sí mismo no es el caso fácil, es el peligroso: por aquí se
    # quitaba el segundo factor sin saber la contraseña de antes. Una tablet
    # olvidada encima del pase es la cuenta entera. Para lo de uno mismo está
    # `/configuracion`, que sí la pide.
    if target.id == user.id or not perms.can_manage(user, target):
        raise HTTPException(status_code=403, detail=i18n.t(lang, "pass.not_yours"))
    target.totp_enabled = False
    target.totp_secret = None
    target.recovery_codes = None
    session.flush()
    return RedirectResponse(
        f"/manager/equipo?done={i18n.t(lang, 'tfa.cleared', name=target.name)}",
        status_code=303)


@app.post("/manager/equipo/{user_id}/contrasena")
def reset_team_password(user_id: int, request: Request, password: str = Form(...),
                        csrf: str = Form(""), ctx=Depends(needs(perms.TEAM)),
                        session: Session = Depends(get_db)):
    """[00322] El manager le pone una nueva a su gente, que es quien la ha olvidado."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    target = _own(session, user, User, user_id, request)
    # [00415] Al dueño de la plataforma no lo toca la casa, y a un manager solo le
    # entra el general —y solo si el otro lleva un local—. Y uno sobre sí
    # mismo, tampoco: aquí no se pide la contraseña de antes, así que por aquí
    # se cambiaba la propia sin saberla. Eso va por `/configuracion`.
    if target.id == user.id or not perms.can_manage(user, target):
        raise HTTPException(status_code=403, detail=i18n.t(lang, "pass.not_yours"))
    try:
        auth.set_password(session, target, password, lang=lang)
    except (auth.AuthError, ValueError) as e:
        return RedirectResponse(f"/manager/equipo?error={e}", status_code=303)
    return RedirectResponse(
        f"/manager/equipo?done={i18n.t(lang, 'pass.reset_done', name=target.name)}",
        status_code=303)


# [00416] ============================================================ DESCARGAS
# ============================================================== FALLOS
@app.get("/fallo", response_class=HTMLResponse)
def bug_page(request: Request, ctx=Depends(require_user), session: Session = Depends(get_db),
             desde: str = "", done: str = "", error: str = ""):
    """[00323] Contar un fallo sin salir del programa ni escribir un correo."""
    user, auth_session = ctx
    return page(request, "bug.html", user, auth_session, session, done=done, error=error,
                desde=desde or "/hoy", kinds=bugs.KINDS,
                mailed=bool(bugs.address()),
                mine=bugs.mine(session, user.restaurant_id))


@app.post("/fallo", response_class=HTMLResponse)
def report_bug(request: Request, message: str = Form(...), kind: str = Form("fallo"),
               screen: str = Form(""), email: str = Form(""), csrf: str = Form(""),
               ctx=Depends(require_user), session: Session = Depends(get_db)):
    """[00324] Lo guarda siempre, y lo manda si hay correo puesto."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    lang = lang_for(request, session, user)
    sede = sites.of_user(session, user)
    try:
        hecho = bugs.report(session, user, message, screen=screen, kind=kind, email=email,
                            lang=lang, site=sede.name if sede else "",
                            # [01717] La versión que tenía puesta quien lo cuenta. El
                            # parámetro estaba desde el principio y no se pasaba
                            # nunca, así que ante cada parte había que preguntar
                            # lo primero: «¿tienes lo de hoy o lo de hace tres
                            # semanas?». Ahora viene escrito.
                            version=version.actual())
    except bugs.BugError as e:
        return RedirectResponse(f"/fallo?error={e}", status_code=303)
    dicho = i18n.t(lang, "bug.done" if hecho.mailed else "bug.done_local",
                   n=hecho.report.id)
    return RedirectResponse(f"/fallo?done={dicho}", status_code=303)


@app.get("/admin/fallos", response_class=HTMLResponse)
def bugs_page(request: Request, ctx=Depends(needs(perms.PLATFORM)),
              session: Session = Depends(get_db), estado: str = ""):
    """[00325] Lo que cuentan las casas, para la plataforma."""
    user, auth_session = ctx
    filtro = BugStatus[estado] if estado in BugStatus.__members__ else None
    return page(request, "bugs.html", user, auth_session, session, estado=estado,
                rows=bugs.recent(session, status=filtro), counts=bugs.counts(session),
                states=list(BugStatus), mailed=bool(bugs.address()))


@app.post("/admin/fallos/{report_id}/estado")
def set_bug_status(report_id: int, request: Request, estado: str = Form(...),
                   note: str = Form(""), csrf: str = Form(""),
                   ctx=Depends(needs(perms.PLATFORM)), session: Session = Depends(get_db)):
    """[00326] El dueño de la plataforma marca en qué anda un parte de fallo."""
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        bugs.set_status(session, report_id,
                        BugStatus[estado] if estado in BugStatus.__members__
                        else BugStatus.SEEN, note=note)
    except bugs.BugError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return RedirectResponse("/admin/fallos", status_code=303)


@app.get("/cuadre", response_class=HTMLResponse)
def cuadre_page(request: Request, ctx=Depends(needs(perms.MONEY)),
                session: Session = Depends(get_db), meses: str = "1"):
    """[00327] El cuadre: los kilos que no se sabe dónde han ido, y por qué.

    Es la pantalla que se abre cuando el mes no sale. Solo dirección: enseña
    dinero y, en la última parte, dice quién pesa y quién calcula a ojo, que
    no es algo que deba colgarse en el pase.
    """
    user, auth_session = ctx
    hoy = jornada.del_usuario(session, user)
    try:
        cuantos = max(1, min(12, int(meses)))
    except ValueError:
        cuantos = 1
    desde = (hoy.replace(day=1) - timedelta(days=31 * (cuantos - 1))).replace(day=1)
    numeros = cuadre.cuadre(session, user.restaurant_id, desde, hoy)
    bandas = [b for b in (cuadre.banda_del_corte(session, user.restaurant_id, sku)
                          for sku in _cortes_de_la_casa(session, user.restaurant_id))
              if b is not None]
    return page(request, "cuadre.html", user, auth_session, session,
                numeros=numeros, bandas=sorted(bandas, key=lambda b: -b.n),
                dedos=cuadre.dedos(session, user.restaurant_id, desde, hoy),
                desde=desde, hasta=hoy, meses=cuantos)


def _cortes_de_la_casa(session: Session, restaurant_id: int) -> list[str]:
    """[00328] Los cortes de primal que esta casa recibe, sin repetir."""
    return sorted({p.sku for p in session.query(Primal)
                   .filter_by(restaurant_id=restaurant_id) if p.sku})


@app.get("/parte", response_class=HTMLResponse)
def daily_report_page(request: Request, ctx=Depends(needs(perms.STOCK)),
                      session: Session = Depends(get_db), fecha: str = ""):
    """[00329] El parte de carne del día, hecho para imprimirlo y colgarlo."""
    user, auth_session = ctx
    lang = lang_for(request, session, user)
    mia = sites.of_user(session, user)
    try:
        on = (date.fromisoformat(fecha) if fecha.strip()
              else jornada.del_usuario(session, user))
    except ValueError:
        on = jornada.del_usuario(session, user)
    return page(request, "report.html", user, auth_session, session, lang=lang, on=on,
                restaurant=session.get(Restaurant, user.restaurant_id),
                report=meat.daily_report(session, user.restaurant_id, on=on, lang=lang,
                                         site_id=mia.id if mia else None))


@app.get("/descargas", response_class=HTMLResponse)
def downloads_page(request: Request, ctx=Depends(require_user),
                   session: Session = Depends(get_db)):
    """[00330] Las hojas que se pueden descargar para llevar a mano."""
    user, auth_session = ctx
    return page(request, "downloads_meat.html", user, auth_session, session,
                sheets=sheets_meat.SHEETS, site=sites.of_user(session, user))


@app.get("/descargas/etiquetas", response_class=HTMLResponse)
def label_sheet(request: Request, ctx=Depends(require_user),
                session: Session = Depends(get_db), recortar: int = 0):
    """[00331] Una hoja A4 de etiquetas en blanco para pegar en la pieza.

    No sustituye a escribir el número en el envoltorio con rotulador —eso se
    sigue pidiendo en la recepción y es lo que hay cuando no hay hoja a mano—:
    es para la casa que prefiere una etiqueta que se lea desde el pasillo.
    """
    user, auth_session = ctx
    return page(request, "labels.html", user, auth_session, session,
                recortar=bool(recortar))


@app.get("/descargas/mis-datos.xlsx")
def download_my_data(request: Request, ctx=Depends(require_manager_even_blocked),
                     session: Session = Depends(get_db)):
    """[01685] Todo lo que ha escrito la casa, en un libro de Excel.

    Va deliberadamente por delante de `/descargas/{code}.xlsx`, que atrapa
    cualquier nombre: registrada después, esta pantalla no existiría.

    Y va con `require_manager_even_blocked` y no con la puerta de siempre por lo
    que explica esa función: es lo que el cliente se lleva, y tiene que poder
    llevárselo justamente el día en que ya no es cliente.
    """
    user, _ = ctx
    restaurant = session.get(Restaurant, user.restaurant_id)
    lang = lang_for(request, session, user)
    payload = exportar.libro(session, user.restaurant_id, lang)
    nombre = exportar.nombre_fichero(restaurant.name if restaurant else "", date.today())
    return Response(payload, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{nombre}"'})


@app.get("/descargas/{code}.xlsx")
def download_sheet(code: str, request: Request, ctx=Depends(require_user),
                   session: Session = Depends(get_db)):
    """[00332] Descarga una hoja en Excel, en el idioma de la casa."""
    user, auth_session = ctx
    restaurant = session.get(Restaurant, user.restaurant_id)
    lang = lang_for(request, session, user)
    mia = sites.of_user(session, user)
    try:
        payload = sheets_meat.workbook(code, restaurant, lang, site=mia.name if mia else "")
    except KeyError:
        raise HTTPException(status_code=404, detail="") from None
    return Response(payload, media_type=XLSX_MEDIA, headers={
        "Content-Disposition": f'attachment; filename="{sheets_meat.filename(code, lang)}"'})


@app.get("/sw.js")
def service_worker(request: Request, idioma: str = ""):
    """[00333] El ayudante que hace que la pantalla cargue dentro de la cámara.

    Guarda una copia de cada pantalla que se visita. Si luego no hay señal
    —o el móvil se ha bloqueado y el navegador ha tirado la pestaña—, se sirve
    la copia en vez de la pantalla del dinosaurio, y el recuento que estaba
    guardado en el teléfono sigue ahí para mandarlo al salir.

    Lo que se manda no se toca nunca: un POST sin red falla como siempre y lo
    recoge el guardado del propio formulario.
    """
    # [00417] El idioma lo dice la propia pantalla que lo registra, no la cabecera del
    # navegador: en un Windows en inglés con la casa en español, adivinarlo
    # dejaba la pantalla de «sin conexión» escrita en inglés, que es justo
    # cuando menos ganas hay de traducir nada.
    lang = idioma if i18n.is_supported(idioma) else lang_for(request)
    codigo = """
// Dos almacenes, y no uno, porque no se borran a la vez.
//
// En las **pantallas** hay datos de alguien: números de pieza, kilos, precios.
// Un móvil de cocina lo usan cuatro personas, así que al salir de la sesión
// eso se borra. En el **armazón** no hay datos de nadie: es el guion del
// tutorial, su hoja de estilo y el icono.
//
// Antes había uno solo y al salir se borraba entero. Como el ayudante no
// cambia, su `activate` no vuelve a dispararse nunca, así que lo borrado no
// se volvía a guardar: desde el primer cierre de sesión, cada mañana salía
// «Sin conexión» en la cámara.
const CACHE = 'carnes-datos-v1';
const ARMAZON = 'carnes-armazon-v1';

// Las pantallas de contar se guardan nada más entrar, sin esperar a que
// alguien las visite: en la cámara puede tocar abrir una por primera vez.
// Todas las pantallas de trabajo, guardadas al entrar: en un restaurante la
// señal falla en cualquier sitio, no solo en la cámara, y lo que no esté
// guardado antes no se abre después.
const DE_MANO = ['/hoy', '/inventario', '/maduracion', '/carne', '/descongelado',
                 '/descongelado/recuento', '/recepcion', '/recepcion/precios',
                 '/despiece', '/merma', '/traslados', '/cortes', '/ventas', '/parte',
                 '/trazabilidad'];

// El tutorial también abre en la cámara, y el icono lo pide cada pantalla.
// Nada de esto lleva datos de nadie, así que sobrevive al cierre de sesión.
const DEL_ARMAZON = ['/static/tour/driver.js', '/static/tour/driver.css',
                     '/static/icono.svg'];

async function llenar(nombre, rutas) {
  const cache = await caches.open(nombre);
  await Promise.all(rutas.map(async ruta => {
    try {
      const res = await fetch(ruta, {credentials: 'same-origin'});
      if (res.ok) await cache.put(ruta, res.clone());
    } catch (e) { /* sin red al arrancar: ya se guardará al visitarla */ }
  }));
}

self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    await self.clients.claim();
    await llenar(ARMAZON, DEL_ARMAZON);
    await llenar(CACHE, DE_MANO);
  })());
});

// Y cuando alguien entra, la página lo pide: el `activate` no vuelve a
// dispararse mientras el ayudante no cambie, así que sin esto las pantallas
// borradas al salir no se volvían a guardar nunca.
self.addEventListener('message', event => {
  if (event.data && event.data.tipo === 'llena') {
    event.waitUntil(llenar(CACHE, DE_MANO));
  }
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;                 // lo que escribe, nunca
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;     // los avisos, al día o nada

  // La avería de una cámara no es «sin señal»: es **una raya**. El punto de
  // acceso se ve, el navegador cree que hay red, y la petición se queda
  // colgada sin fallar nunca. Sin plazo, el `catch` no llegaba jamás y la
  // pantalla se quedaba en blanco indefinidamente teniendo la copia guardada
  // al lado. Por eso lo primero es el plazo, y lo segundo la copia.
  const PLAZO = 2500;      // en una cámara, más de esto es que no hay

  const conRed = () => fetch(req).then(res => {
    if (res.ok && res.type === 'basic') {
      const copia = res.clone();
      caches.open(CACHE).then(c => c.put(req, copia));
    }
    return res;
  });

  const conPlazo = () => new Promise((cumple, falla) => {
    const reloj = setTimeout(() => falla(new Error('tarde')), PLAZO);
    conRed().then(
      res => { clearTimeout(reloj); cumple(res); },
      err => { clearTimeout(reloj); falla(err); });
  });

  event.respondWith((async () => {
    try { return await conPlazo(); } catch (e) { /* ni red ni paciencia */ }

    // La copia guardada vale más que seguir esperando: quien está contando
    // necesita la pantalla ahora, no dentro de diez segundos.
    const guardado = (await caches.match(req)) || (await caches.match(url.pathname));
    if (guardado) return guardado;

    // Sin copia sí merece la pena insistir: a veces es el servidor
    // arrancando, o la wifi parpadeando, y enseñar «sin conexión» por medio
    // segundo de nada asusta más que esperar.
    try {
      await new Promise(listo => setTimeout(listo, 900));
      return await conRed();
    } catch (e) { /* ahora sí */ }

    return new Response(OFFLINE,
      {status: 200, headers: {'Content-Type': 'text/html; charset=utf-8'}});
  })());
});

// La pantalla de cuando no hay nada guardado. No es un sitio donde dejar a
// nadie: vuelve a intentarlo ella sola cada dos segundos y se va en cuanto el
// programa contesta, así que si lo que pasaba era que el servidor estaba
// arrancando —o se paró y se volvió a arrancar— la pantalla vuelve sin que
// haya que tocar nada. El botón es para el que no quiere esperar.
const OFFLINE = `<!doctype html><html lang="__LANG__" dir="__DIR__"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITULO__</title>
<link rel="icon" href="/static/icono.svg" type="image/svg+xml">
<style>body{font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;
display:grid;place-items:center;min-height:100vh;background:#12100e;color:#f4f1ec;
padding:24px;text-align:center}
.marca{font-weight:700;letter-spacing:-.01em;margin:0 0 28px;color:#e08c4a}
h1{font-size:26px;margin:0 0 10px}
p{max-width:36ch;color:#a9a29a;margin:0 auto}
.nota{margin-top:22px;font-size:13px;color:#6f6862;max-width:40ch}
button{margin-top:26px;font:inherit;font-weight:600;padding:13px 22px;border:0;
border-radius:10px;background:#e08c4a;color:#1a1614;cursor:pointer;min-height:46px}
button:hover{filter:brightness(1.08)}</style></head><body><div>
<p class="marca">__CASA__</p>
<h1>__TITULO__</h1><p>__CUERPO__</p>
<button type="button" onclick="location.reload()">__REINTENTAR__</button>
<p class="nota">__NOTA__</p>
<script>
// Se mira si el programa ya contesta; en cuanto lo haga, la pantalla se va
// sola. Se pregunta por una dirección que no pasa por el ayudante y sin tocar
// lo guardado, para que la respuesta sea la de verdad y no una copia.
(function () {
  var cuantas = 0;
  function mirar() {
    fetch('/healthz', {cache: 'no-store'})
      .then(function (r) { if (r.ok) location.reload(); })
      .catch(function () {});
    if (++cuantas < 450) setTimeout(mirar, 2000);   // un cuarto de hora
  }
  setTimeout(mirar, 1500);
  window.addEventListener('online', function () { location.reload(); });
})();
</script>
</div></body></html>`;
"""
    codigo = (codigo.replace("__TITULO__", i18n.t(lang, "off.title"))
              .replace("__CUERPO__", i18n.t(lang, "off.body"))
              .replace("__REINTENTAR__", i18n.t(lang, "off.retry"))
              .replace("__NOTA__", i18n.t(lang, "off.note"))
              .replace("__CASA__", i18n.t(lang, "m.app.title"))
              .replace("__LANG__", lang).replace("__DIR__", i18n.direction(lang)))
    return Response(codigo, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@app.post("/tour/visto")
def tour_seen(request: Request, pantalla: str = Form(...), completo: str = Form("1"),
              pasos: str = Form(""), csrf: str = Form(""), ctx=Depends(require_user),
              session: Session = Depends(get_db)):
    """[00334] «Ya he visto el tutorial de esta pantalla».

    Se marca para quien lo pide y para nadie más: el usuario sale de la
    sesión, no de lo que mande el navegador. Y la pantalla tiene que existir
    en el registro, que si no cualquiera llena la tabla con nombres inventados.
    """
    user, auth_session = ctx
    _guard(request, session, user, auth_session, csrf)
    try:
        tutorial.marcar(session, user, pantalla, completo=completo not in ("", "0"),
                        pasos=int(pasos) if pasos.isdigit() else None)
    except KeyError:
        raise HTTPException(status_code=404, detail="") from None
    return JSONResponse({"ok": True})


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest(request: Request, session: Session = Depends(get_db)):
    """[01746] El manifiesto. Lo que convierte un marcador en una aplicación.

    Esto no es un adorno de pantalla de inicio: es lo único que protege el
    trabajo que se apunta sin cobertura.

    Safari borra **todo** el almacenamiento que escribe un guion —incluido el
    `localStorage` donde vive la cola de apuntes— a los siete días de usar
    Safari sin que nadie entre en el sitio. Es política de Apple, no un fallo.
    Y la propia Apple dice cuál es la salida: las aplicaciones añadidas a la
    pantalla de inicio se libran, porque llevan su propio contador.

    Pero sin manifiesto, «añadir a pantalla de inicio» **no crea una
    aplicación**: crea un marcador que abre Safari, y ese no tiene la exención.
    Traducido a una cocina: el móvil de la cámara con iPhone se queda ocho días
    en un cajón —dos semanas de vacaciones, un local que cierra en agosto— y la
    cola de apuntes pendientes puede desaparecer sin avisar a nadie.

    Va servido desde aquí y no como fichero suelto para que el nombre y los
    colores salgan en el idioma de quien lo instala, que es lo que va a ver en
    la pantalla de inicio de su móvil todos los días.
    """
    lang = lang_for(request, session)
    return JSONResponse({
        "name": i18n.t(lang, "m.app.title"),
        "short_name": i18n.t(lang, "m.app.short"),
        "description": i18n.t(lang, "m.app.tagline"),
        "lang": lang,
        "dir": "rtl" if i18n.direction(lang) == "rtl" else "ltr",
        "start_url": "/hoy",
        # `standalone` es lo que hace que se abra sin la barra del navegador, y
        # es también lo que le dice a iOS que esto es una aplicación y no un
        # marcador. Sin esta línea, todo lo de arriba no sirve de nada.
        "display": "standalone",
        "orientation": "any",
        "background_color": "#f6f6f4",
        "theme_color": "#8a3d1f",
        "icons": [
            {"src": "/static/icono-180.png", "sizes": "180x180", "type": "image/png"},
            {"src": "/static/icono.svg", "sizes": "any", "type": "image/svg+xml",
             "purpose": "any maskable"},
        ],
    }, media_type="application/manifest+json")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """[00335] El icono de la casa.

    Las pantallas ya lo dicen con una etiqueta, pero Safari y los buscadores
    lo piden aquí de todas formas. Sin esto son un 404 por visita: no rompe
    nada, pero ensucia la consola y deja la pestaña sin cara.
    """
    return FileResponse(os.path.join(STATIC_DIR, "favicon.ico"),
                        media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/healthz")
def healthz():
    """[00336] Contesta que está viva. Lo pregunta el servidor, no una persona."""
    return {"status": "ok", "edition": "meat"}


def create_app(database_url: str = "sqlite:///carnes.db") -> FastAPI:
    """[00337] Arranca la edición de carne: abre la base y deja las tablas al día."""
    db.init_engine(database_url)
    db.create_all()
    return app
