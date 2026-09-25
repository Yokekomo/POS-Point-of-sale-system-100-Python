"""[00561] Lo que la edición de carne hace y la plataforma de cocina no tenía pantalla.

El motor no cambia: despiece, reparto de coste, FEFO, descongelado, inventario,
trazabilidad y merma son los mismos de siempre. Aquí está lo que faltaba para
que una cocina de carne pueda trabajar sola:

- dar de alta un lote de primales, cada pieza con su número y su coste;
- montar un despiece desde un formulario y volcarlo a cámara;
- el catálogo de cortes, que es lo que se cuenta y se vende;
- la carta: un plato de carne es un corte y unos gramos, atado a su POS;
- el resumen de lo que está pendiente hoy.
"""
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from thegrill.models import (Alert, AlertSeverity, ConsumptionMode, CountStatus, Despiece,
                             DespieceCut, DespiecePrimal, Ingredient, IngredientItem,
                             IngredientLot, MeatCount, NotificationKind, PosProduct,
                             Primal, PrimalStatus, Recipe, RecipeKind, RecipeLine, Restaurant,
                             Rotation, Storage, Unit, User)
from thegrill.meat import novedades
from thegrill.web import aging as aging_mod
from thegrill.web import waste as waste_mod
from thegrill.web import service as plataforma
from thegrill.web import butchery, costing, defrost, inventory, jornada, locking, rangos, sites
from thegrill.web.i18n import t

MAX_CUTS = 10
# [00621] En cocina se habla en gramos, no en kilos. Cada unidad base tiene su unidad
# pequeña, que es la que se escribe y la que se lee.
SMALL = {Unit.KG: ("g", 1000.0), Unit.L: ("ml", 1000.0), Unit.UNIT: ("", 1.0)}
CATEGORY = "carne"      # lo que se despieza, se cuenta y se descuenta
EXTRA = "extra"         # lo que acompaña en el plato: solo interesa su coste


AUTO_TG = re.compile(r"^TG-\d{4}$")      # el que propone la pantalla
TIPOS_FOTO = {v: k for k, v in plataforma.ALLOWED_IMAGE_TYPES.items()}


class MeatError(ValueError):
    """[00562] Lo que se ha metido no se puede dar de alta tal y como está."""


# ===================================================== recepción de primales
@dataclass
class PrimalRow:
    serial: str
    kg: float
    price_kg: float | None = None
    # [01615] Lo del camión, repartido a cada kilo de cada pieza: el transporte y la
    # aduana. Son de importación y casi nunca están, por eso van aparte del
    # precio de la carne y no se exigen.
    freight_kg: float | None = None
    duty_kg: float | None = None
    sku: str = ""
    grade: str | None = None
    origin: str | None = None
    use_by: date | None = None
    # [00622] ---- la etiqueta del proveedor, la de esta pieza.
    # Lo que es igual para todo el camión se escribe una vez arriba y se copia
    # a cada línea; lo que cambia de una bolsa a otra —el número de canal, la
    # fecha de sacrificio, la calificación— se escribe en su línea.
    supplier_lot: str | None = None
    producer_plant: str | None = None
    est_code: str | None = None
    breed: str | None = None
    slaughter_date: date | None = None
    pack_date: date | None = None
    label_product: str | None = None
    halal: bool | None = None
    # [00623] Cómo bajó del camión: refrigerada o congelada, a cuántos grados, y —si
    # llegó fresca— si va derecha al arcón sin pasar por la cámara.
    arrival: Storage | None = None
    arrival_c: float | None = None
    frozen_on_arrival: bool | None = None
    # [00624] El número lo ha puesto la casa, no el proveedor: si otra recepción se
    # adelanta con ese mismo número, este se puede cambiar sin preguntar.
    auto: bool = False


def next_lot(session: Session, restaurant_id: int, on: date | None = None) -> str:
    """[00563] El número del próximo lote de recepción: la fecha y un orden del día.

    Nadie tiene que inventarse un código en el muelle con el camión esperando.
    Se propone uno —`L-260921-1`— y se confirma al dar de alta: hasta entonces
    no existe, así que abrir la pantalla y cerrarla no quema ningún número.
    """
    on = on or jornada.hoy(session, restaurant_id)
    base = f"L-{on:%y%m%d}"
    usados = {p.lot for p in session.query(Primal.lot)
              .filter(Primal.restaurant_id == restaurant_id,
                      Primal.lot.like(f"{base}%"))}
    for n in range(1, 100):
        propuesto = f"{base}-{n}"
        if propuesto not in usados:
            return propuesto
    return f"{base}-{len(usados) + 1}"


def next_serials(session: Session, restaurant_id: int, count: int = 1) -> list[str]:
    """[00564] Los próximos números de pieza, siguiendo por donde iba la casa.

    Si los que hay son números, se sigue contando; si no lo son —porque el
    proveedor los trae con letras—, se empieza una serie propia. Se proponen,
    y solo se quedan cogidos cuando la recepción se da de alta.
    """
    numeros = []
    for (serial,) in session.query(Primal.serial).filter_by(restaurant_id=restaurant_id):
        limpio = (serial or "").strip()
        if limpio.isdigit():
            numeros.append(int(limpio))
    siguiente = (max(numeros) + 1) if numeros else 8001
    ancho = max(4, len(str(siguiente)))
    return [f"{siguiente + i:0{ancho}d}" for i in range(max(1, count))]


def receive_primals(session: Session, user: User, lot: str, rows: list[PrimalRow],
                    received: date | None = None, lang: str = "es",
                    chamber: str | None = None) -> list[Primal]:
    """[00565] Da de alta un grupo de primales bajo un lote de recepción común.

    Cada pieza lleva su número y su coste: ese coste es el que luego reparte el
    despiece entre los cortes, así que no se pierde por el camino.
    """
    rows = [r for r in rows if r.serial.strip() or r.kg]
    # [00625] La carne entra donde está quien la recibe: el obrador, casi siempre.
    destino = sites.of_user(session, user) or sites.main(session, user.restaurant_id)
    if not rows:
        raise MeatError(t(lang, "m.rec.empty"))
    received = received or jornada.del_usuario(session, user)
    lot = (lot or "").strip() or next_lot(session, user.restaurant_id, received)

    # [00626] Los números que no se hayan escrito se ponen aquí, al dar de alta, y no
    # al abrir la pantalla: dos personas recibiendo a la vez no se pisan, y el
    # que abre y cierra no deja un hueco en la serie.
    faltan = [r for r in rows if not r.serial.strip()]
    for row in faltan:
        row.auto = True           # numerada por la casa: si choca, se renumera
    if faltan:
        libres = next_serials(session, user.restaurant_id, len(faltan) + len(rows))
        escritos = {r.serial.strip() for r in rows if r.serial.strip()}
        for row in faltan:
            while libres and (libres[0] in escritos or
                              session.query(Primal).filter_by(
                                  restaurant_id=user.restaurant_id,
                                  serial=libres[0]).first()):
                libres.pop(0)
            if not libres:
                raise MeatError(t(lang, "m.rec.needs"))
            row.serial = libres.pop(0)
            escritos.add(row.serial)

    seen: set[str] = set()
    for row in rows:
        serial = row.serial.strip()
        if not serial or row.kg <= 0:
            raise MeatError(t(lang, "m.rec.needs"))
        if serial in seen:
            raise MeatError(t(lang, "m.rec.dup", serial=serial))
        seen.add(serial)
        # [00627] Al número que ha escrito una persona se le dice aquí que ya existe,
        # que es lo que quiere oír: se ha equivocado de pieza. Al que ha puesto
        # la casa no se le dice nada —lo elegimos nosotros—: si justo lo acaba
        # de coger otra recepción, se cambia al guardar y nadie se entera.
        if not getattr(row, "auto", False) and (
                session.query(Primal)
                .filter_by(restaurant_id=user.restaurant_id, serial=serial).first()):
            raise MeatError(t(lang, "m.rec.dup", serial=serial))

    for row in rows:
        _check_label(row, received, lang)

    created = _save_primals(session, user, rows, lot, received, destino, chamber, lang)
    # [00628] Que se entere el que está en otra pantalla: en la cámara hay carne que
    # hace un minuto no estaba, y contar sin ella deja el inventario corto.
    novedades.anotar(session, user, novedades.RECEPCION, ref=lot,
                     label=_lo_que_mas_entro(created), pieces=len(created),
                     kg=sum(p.weight_kg or 0.0 for p in created),
                     site_id=destino.id if destino else None)
    _ask_for_prices(session, user, created, lot, lang)
    _haccp_de_llegada(session, user, created, lang)
    return created


def _haccp_de_llegada(session: Session, user: User, created: list[Primal],
                      lang: str) -> list[Alert]:
    """[00566] Lo que bajó del camión fuera de norma: guardado, y en los avisos de hoy.

    La pieza entra igual. Una carne que llega a doce grados es carne que ha
    llegado a doce grados, y borrarla del sistema no la enfría: lo único que
    hace es dejar sin prueba al que tiene que decidir si se devuelve. Así que
    se guarda el número tal cual y se le pone delante al responsable el mismo
    día, que es cuando todavía se puede hacer algo.
    """
    ahora = datetime.utcnow()
    avisos: list[Alert] = []
    casa = session.get(Restaurant, user.restaurant_id)
    for pieza in created:
        for aviso in rangos.llegada(pieza.arrival_c, pieza.arrival or Storage.CHILLED,
                                    pieza.serial, kg=pieza.weight_kg, lang=lang,
                                    restaurant=casa):
            alerta = Alert(restaurant_id=user.restaurant_id, code=aviso.code,
                           message=aviso.message,
                           severity=(AlertSeverity.CRITICAL
                                     if aviso.code.startswith("haccp.") else
                                     AlertSeverity.WARNING),
                           created_at=ahora)
            session.add(alerta)
            avisos.append(alerta)
    if not avisos:
        return []
    session.flush()
    graves = [a for a in avisos if a.severity == AlertSeverity.CRITICAL]
    destinatarios = [uid for uid in plataforma.manager_ids(session, user.restaurant_id)
                     if uid != user.id]
    if destinatarios and graves:
        plataforma.notify(
            session, user.restaurant_id, destinatarios,
            title=t(lang, "alert.haccp_title", n=len(graves)),
            body=graves[0].message, severity=AlertSeverity.CRITICAL,
            alert_id=graves[0].id, now=ahora, kind=NotificationKind.ALERT)
    return avisos


def _ask_for_prices(session: Session, user: User, created: list[Primal], lot: str,
                    lang: str) -> None:
    """[00567] Si han entrado piezas sin precio, se le dice a dirección.

    Sin esto, la pieza se queda en la cámara esperando un precio que nadie sabe
    que hace falta: no se puede despiezar, y el día que alguien va a cortarla se
    encuentra con que no está en la lista y no entiende por qué. El aviso va al
    contador de la cabecera del manager, que es donde mira, y se queda ahí
    hasta que lo lee.
    """
    faltan = [p for p in created if p.landed_usd_per_kg is None]
    if not faltan:
        return
    destinatarios = [uid for uid in plataforma.manager_ids(session, user.restaurant_id)
                     if uid != user.id]
    if not destinatarios:
        return
    plataforma.notify(
        session, user.restaurant_id, destinatarios,
        title=t(lang, "notif.price_title", n=len(faltan)),
        body=t(lang, "notif.price_body", n=len(faltan), lot=lot or "—", who=user.name),
        severity=AlertSeverity.INFO, kind=NotificationKind.ALERT)


def _lo_que_mas_entro(created: list[Primal]) -> str:
    """[00568] De lo que ha entrado, lo que más: el aviso dice una cosa, no seis.

    Una recepción normal es un lote de lo mismo. Cuando trae dos artículos, el
    aviso nombra el que más piezas trae —que es el que cambia la cámara— y el
    número de piezas ya dice que hay más de una.
    """
    cuenta: dict[str, int] = {}
    for pieza in created:
        cuenta[pieza.sku or ""] = cuenta.get(pieza.sku or "", 0) + 1
    return max(cuenta, key=lambda k: (cuenta[k], k)) if cuenta else ""


def _check_label(row: PrimalRow, received: date, lang: str) -> None:
    """[00569] Las fechas de la etiqueta tienen que poder haber pasado, y en orden.

    Un dato de etiqueta mal tecleado es peor que no tenerlo: se guarda, nadie
    lo vuelve a mirar, y el día que hay que contestar de dónde salió la pieza
    se contesta mal. Una fecha de sacrificio de la semana que viene, o una
    carne envasada antes de sacrificarla, es un dedo en el teclado.
    """
    # [00629] Un peso o una temperatura que no pueden ser son lo mismo que una fecha
    # que no puede ser: el dedo en la tecla de al lado. Se paran aquí, antes de
    # que el registro sanitario quede completo y falso.
    rangos.peso_pieza(row.kg, lang, serial=row.serial.strip() or "—")
    rangos.temperatura(row.arrival_c, lang)
    rangos.precio_kg(row.price_kg, lang)

    sacrificio, envasado = row.slaughter_date, row.pack_date
    if sacrificio and sacrificio > received:
        raise MeatError(t(lang, "m.rec.bad_slaughter", serial=row.serial.strip() or "—"))
    if envasado and envasado > received:
        raise MeatError(t(lang, "m.rec.bad_pack", serial=row.serial.strip() or "—"))
    if sacrificio and envasado and sacrificio > envasado:
        raise MeatError(t(lang, "m.rec.pack_before_slaughter",
                          serial=row.serial.strip() or "—"))


def _save_primals(session: Session, user: User, rows: list[PrimalRow], lot: str,
                  received: date, destino, chamber: str | None, lang: str,
                  intentos: int = 3) -> list[Primal]:
    """[00570] Escribe las piezas. Si dos muelles dan de alta a la vez, se renumera.

    Los números automáticos se piden justo antes de guardar, pero entre pedirlos
    y guardarlos cabe otra recepción: los dos piden el 8016 y el segundo se
    estrellaba con un error rojo con el camión esperando y la hoja entera por
    volver a escribir. Ahora se vuelve a intentar con los siguientes libres y
    el de fuera ni se entera.
    """
    automaticos = [r for r in rows if getattr(r, "auto", False)]

    def otros_numeros():
        """[00611] Qué hacer cuando el número ya está cogido: renumerar, o parar.

        Solo se renumera lo que puso la máquina. Un número que escribió una
        persona no se toca: si lo ha repetido, lo tiene que ver.
        """
        if not automaticos:
            raise MeatError(t(lang, "m.rec.dup", serial=rows[0].serial)) from None
        _renumber(session, user.restaurant_id, rows, automaticos, lang)

    def escribir():
        """[00612] Mete las piezas. Se llama otra vez si hubo que renumerar."""
        return _insert_primals(session, user, rows, lot, received, destino, chamber)

    try:
        return locking.retry(session, escribir, otros_numeros, intentos=intentos)
    except IntegrityError:
        raise MeatError(t(lang, "m.rec.dup", serial=rows[0].serial)) from None


def _renumber(session: Session, restaurant_id: int, rows: list[PrimalRow],
              automaticos: list[PrimalRow], lang: str) -> None:
    """[00571] Vuelve a repartir los números que había puesto la casa."""
    libres = next_serials(session, restaurant_id, len(automaticos) + len(rows) + 4)
    ocupados = {r.serial.strip() for r in rows if r not in automaticos}
    for row in automaticos:
        while libres and (libres[0] in ocupados or
                          session.query(Primal).filter_by(
                              restaurant_id=restaurant_id, serial=libres[0]).first()):
            libres.pop(0)
        if not libres:
            raise MeatError(t(lang, "m.rec.needs"))
        row.serial = libres.pop(0)
        ocupados.add(row.serial)


def _puesto_en_camara(row: PrimalRow) -> float | None:
    """[01614] Lo que cuesta el kilo de esa pieza puesto en la cámara.

    La carne no cuesta lo que dice la factura del proveedor: cuesta lo que
    costó ponerla ahí. El precio del kilo es de cada pieza —dos bolsas del
    mismo camión no valen lo mismo si una es MB9 y la otra MB6—, pero el
    transporte y la aduana son del camión entero y se reparten por igual a
    cada kilo que traía.

    Un lote nacional no lleva ni lo uno ni lo otro y esto devuelve el precio a
    secas. Una pieza sin precio sigue sin tenerlo: sumarle el flete a lo que no
    se sabe lo que cuesta sería inventarse un coste, y la pieza tiene que
    quedarse esperando a que dirección le ponga el suyo.
    """
    if not row.price_kg:
        return None
    return round(row.price_kg + (row.freight_kg or 0.0) + (row.duty_kg or 0.0), 6)


def _insert_primals(session: Session, user: User, rows: list[PrimalRow], lot: str,
                    received: date, destino, chamber: str | None) -> list[Primal]:
    """[00572] Da de alta las piezas de una recepción, una fila por pieza.

    Cada una se queda con todo lo que venía en la etiqueta del proveedor —lote,
    matadero, registro sanitario, raza, sacrificio— porque el día que hay que
    retirar un lote se pregunta por eso y no por nuestro número.
    """
    created = []
    for row in rows:
        primal = Primal(
            restaurant_id=user.restaurant_id, serial=row.serial.strip(),
            sku=(row.sku or "").strip() or row.serial.strip(),
            grade=(row.grade or None), origin=(row.origin or None),
            weight_kg=row.kg, received_kg=row.kg, lot=lot.strip() or None,
            received_date=received, site_id=destino.id,
            chamber=(chamber or "").strip()[:48] or None,
            goods_usd_per_kg=row.price_kg,
            freight_usd_per_kg=row.freight_kg, duty_usd_per_kg=row.duty_kg,
            landed_usd_per_kg=_puesto_en_camara(row),
            piece_cost_usd=(round(row.kg * _puesto_en_camara(row), 2)
                            if _puesto_en_camara(row) else None),
            frozen_use_by=row.use_by, status=PrimalStatus.IN_STOCK,
            supplier_lot=(row.supplier_lot or None),
            producer_plant=(row.producer_plant or None),
            est_code=(row.est_code or None), breed=(row.breed or None),
            slaughter_date=row.slaughter_date, pack_date=row.pack_date,
            label_product=(row.label_product or None), halal=row.halal,
            arrival=row.arrival, arrival_c=row.arrival_c,
            frozen_on_arrival=row.frozen_on_arrival or None,
            # [00630] Y dónde queda: lo que llega congelado, y lo que llega fresco y se
            # mete al arcón, están en el congelador desde el primer día. Si no,
            # el programa las cuenta como frescas y les pone el reloj que no es.
            storage=(Storage.FROZEN
                     if (row.arrival == Storage.FROZEN or row.frozen_on_arrival)
                     else Storage.CHILLED),
            storage_since=received)
        session.add(primal)
        created.append(primal)
    session.flush()
    return created


def primals_in_stock(session: Session, restaurant_id: int,
                     site_id: int | None = None,
                     priced_only: bool = False) -> list[Primal]:
    """[00573] Las piezas enteras que todavía se pueden despiezar.

    Con sede, las que están en esa sede: el local corta lo suyo, no lo que
    está colgado en el obrador.

    `priced_only` deja fuera las que todavía no tienen precio. Están en la
    cámara y se ven en la cámara —existen, pesan y ocupan sitio—, pero no se
    pueden despiezar: el despiece reparte el coste del primal entre los cortes,
    y repartir cero es perder el rastro del dinero sin que salte nada.
    """
    rows = (session.query(Primal)
            .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK)
            .order_by(Primal.sku, Primal.serial).all())
    if priced_only:
        rows = [p for p in rows if p.landed_usd_per_kg is not None]
    if not site_id:
        return rows
    principal = sites.main(session, restaurant_id).id
    return [p for p in rows if (p.site_id or principal) == site_id]


def awaiting_price(session: Session, restaurant_id: int,
                   site_id: int | None = None) -> list[Primal]:
    """[00574] Las piezas que han entrado y todavía no valen nada.

    En el muelle se apunta lo que llega: qué es, cuánto pesa, de qué calidad y
    de dónde viene. El precio no lo sabe quien descarga —ni tiene por qué: el
    dinero es cosa de dirección— y viene en la factura, que llega después. Así
    que la pieza entra sin precio, se ve en la cámara, y espera a que alguien
    la active poniéndole el suyo.
    """
    rows = [p for p in session.query(Primal)
            .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK)
            .order_by(Primal.received_date.desc(), Primal.lot, Primal.serial)
            if p.landed_usd_per_kg is None]
    if not site_id:
        return rows
    principal = sites.main(session, restaurant_id).id
    return [p for p in rows if (p.site_id or principal) == site_id]


def set_price(session: Session, user: User, serial: str, price_kg: float,
              lang: str = "es") -> Primal:
    """[00575] Activa una pieza: le pone el precio del kilo y con él su coste.

    Ese coste es el que el despiece reparte después entre los cortes, así que
    ponerlo mal aquí desordena el dinero de todo lo que salga de la pieza. Por
    eso solo lo toca quien ve dinero, y por eso no se deja en blanco ni en cero.
    """
    pieza = (session.query(Primal)
             .filter_by(restaurant_id=user.restaurant_id, serial=(serial or "").strip())
             .first())
    if pieza is None:
        raise MeatError(t(lang, "m.rec.no_piece", serial=serial))
    if price_kg is None or price_kg <= 0:
        raise MeatError(t(lang, "m.rec.price_needed", serial=pieza.serial))
    # [00631] El coste sale del peso de la factura —los kilos que se pagaron— y al
    # céntimo: si la pieza arrastra milésimas, todo lo que se reparta luego a
    # partir de ella las arrastra también.
    recibido = pieza.received_kg or pieza.weight_kg or 0.0
    # [01616] Lo que se teclea aquí es el precio de la carne, que es lo que dice la
    # factura del proveedor. Lo que costó traerla ya se apuntó en el muelle y
    # sigue siendo suyo: el kilo puesto en la cámara es la suma de los tres.
    pieza.goods_usd_per_kg = price_kg
    price_kg = round(price_kg + (pieza.freight_usd_per_kg or 0.0)
                     + (pieza.duty_usd_per_kg or 0.0), 6)
    pieza.piece_cost_usd = round(recibido * price_kg, 2)

    # [00632] Y el precio del kilo se calcula contra el peso de HOY, no contra el de la
    # factura. Aquí se perdía el dinero: el carnicero recibe sin precio y
    # dirección lo pone después, a veces días después, y para entonces la pieza
    # lleva una semana madurando y pesa menos. Poniendo el del albarán, una
    # pieza de 10 kg a 30 €/kg que ya está en 8,5 salía a 30 en vez de a 35,29,
    # y todo lo que se apoya en ese número —la venta al peso, la pizarra, los
    # recortes, lo esperado en el inventario— cobraba de menos. Cuarenta y
    # cinco euros por pieza, el quince por ciento, en lo contrario de para lo
    # que existe el módulo de maduración.
    #
    # Es lo mismo que hacen pesar y limpiar: el agua que se fue ya está pagada.
    quedan = pieza.weight_kg or recibido
    pieza.landed_usd_per_kg = (round(pieza.piece_cost_usd / quedan, 6)
                               if quedan > 1e-9 else float(price_kg))
    session.flush()
    return pieza


def photo_type(path: str) -> str:
    """[00576] El tipo de la foto, por su extensión: es la que pusimos al guardarla."""
    return TIPOS_FOTO.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def store_label_photo(session: Session, primal: Primal, content_type: str,
                      payload: bytes, upload_dir: str, lang: str = "es") -> Primal:
    """[00577] Guarda la foto de la etiqueta de una pieza y la cuelga de ella.

    Una sola por pieza: la etiqueta es una. Si se vuelve a hacer —porque la
    primera salió movida, que en una cámara pasa— la nueva sustituye a la
    vieja y el fichero de antes se borra, que si no el disco se llena de
    fotos que no mira nadie.
    """
    if content_type not in plataforma.ALLOWED_IMAGE_TYPES:
        raise MeatError(t(lang, "valid.photo_type", type=content_type or "—"))
    if not payload:
        raise MeatError(t(lang, "valid.photo_empty"))
    if len(payload) > plataforma.MAX_UPLOAD_BYTES:
        raise MeatError(t(lang, "valid.photo_too_big",
                          n=plataforma.MAX_UPLOAD_BYTES // (1024 * 1024)))

    carpeta = os.path.join(upload_dir, str(primal.restaurant_id), "etiquetas")
    os.makedirs(carpeta, exist_ok=True)
    destino = os.path.join(carpeta, f"{uuid.uuid4().hex}"
                                    f"{plataforma.ALLOWED_IMAGE_TYPES[content_type]}")
    with open(destino, "wb") as fh:
        fh.write(payload)
    anterior = primal.photo_ref
    primal.photo_ref = destino
    session.flush()
    if anterior and anterior != destino and os.path.exists(anterior):
        try:
            os.remove(anterior)
        except OSError:
            pass              # si no se deja borrar, mejor una foto de más
    return primal


def recent_primals(session: Session, restaurant_id: int, limit: int = 50) -> list[Primal]:
    """[00578] Las últimas piezas que entraron, de la más nueva a la más vieja."""
    return (session.query(Primal).filter_by(restaurant_id=restaurant_id)
            .order_by(Primal.received_date.desc(), Primal.id.desc()).limit(limit).all())


# ============================================================ cortes madre
def create_cut(session: Session, user: User, name: str, min_stock: float | None = None,
               rotation: Rotation = Rotation.FEFO,
               consumption: ConsumptionMode = ConsumptionMode.RECIPE,
               sold_by_weight: bool = False) -> Ingredient:
    """[00579] Un corte es un ingrediente madre de carne: lo que se cuenta y se vende.

    `consumption` dice de dónde sale el consumo: de la venta en el POS, o del
    recuento de descongelado al cerrar el turno. Las dos cosas a la vez
    descontarían el doble.

    `sold_by_weight` es el corte que no se raciona: entra entero y limpio en
    cámara y se corta delante del cliente, así que lo que descuenta cada venta
    son los gramos que manda el POS, no un gramaje de carta.
    """
    name = name.strip()
    if not name:
        raise MeatError("El corte necesita un nombre")
    if (session.query(Ingredient)
            .filter_by(restaurant_id=user.restaurant_id, name=name).first()):
        raise MeatError(f"Ya hay un corte llamado {name}")
    cut = Ingredient(restaurant_id=user.restaurant_id, name=name, unit=Unit.KG,
                     rotation=rotation, consumption=consumption,
                     min_stock=min_stock, category=CATEGORY,
                     sold_by_weight=bool(sold_by_weight))
    session.add(cut)
    session.flush()
    return cut


def add_article(session: Session, user: User, cut: Ingredient, name: str,
                supplier: str | None = None) -> IngredientItem:
    """[00580] Una procedencia concreta del mismo corte. Todas se gastan en una cola."""
    name = name.strip()
    if not name:
        raise MeatError("El artículo necesita un nombre")
    item = IngredientItem(restaurant_id=user.restaurant_id, ingredient_id=cut.id,
                          name=name, supplier=(supplier or None))
    session.add(item)
    session.flush()
    return item


def cuts(session: Session, restaurant_id: int) -> list[Ingredient]:
    """[00581] Los cortes de carne. La guarnición no se cuenta ni se despieza."""
    return (session.query(Ingredient)
            .filter(Ingredient.restaurant_id == restaurant_id, Ingredient.active.is_(True),
                    (Ingredient.category == CATEGORY) | (Ingredient.category.is_(None)))
            .order_by(Ingredient.name).all())


# ================================================== otros ingredientes del plato
def small_unit(unit: Unit, lang: str = "es") -> str:
    """[00582] Cómo se llama la unidad pequeña: gramos, mililitros o unidades."""
    label = SMALL.get(unit, ("", 1.0))[0]
    return label or t(lang, "m.unit.piece")


def to_base(unit: Unit, qty_small: float) -> float:
    """[00583] De gramos a kilos, de mililitros a litros. Las unidades no se tocan."""
    return round(qty_small / SMALL.get(unit, ("", 1.0))[1], 6)


def to_small(unit: Unit, qty_base: float) -> float:
    """[00584] Pasa de la unidad de la base a la de la mano: kilos a gramos, litros a mililitros."""
    return round(qty_base * SMALL.get(unit, ("", 1.0))[1], 4)


def portion_cost(extra: Ingredient) -> float | None:
    """[00585] A cuánto sale la ración: el precio por kilo por lo que lleva el plato."""
    cost = extra_cost(extra)
    if cost is None or not extra.portion_g:
        return None
    return round(cost * to_base(extra.unit, extra.portion_g), 4)


def create_extra(session: Session, user: User, name: str, unit: Unit = Unit.KG,
                 cost: float | None = None, portion_g: float | None = None) -> Ingredient:
    """[00586] Lo que acompaña a la carne: guarnición, salsa, pan.

    De esto no se lleva stock —aquí no se cuentan patatas—, pero su coste sí
    cuenta: sin él, el food cost del emplatado se queda corto. Por eso se marca
    para que la venta no intente descontarlo del almacén.
    """
    name = name.strip()
    if not name:
        raise MeatError("El ingrediente necesita un nombre")
    if (session.query(Ingredient)
            .filter_by(restaurant_id=user.restaurant_id, name=name).first()):
        raise MeatError(f"Ya hay un ingrediente llamado {name}")
    if cost is not None and cost < 0:
        raise MeatError("El coste no puede ser negativo")
    if portion_g is not None and portion_g <= 0:
        raise MeatError("La porción tiene que ser mayor que cero")
    extra = Ingredient(restaurant_id=user.restaurant_id, name=name, unit=unit,
                       rotation=Rotation.FIFO, consumption=ConsumptionMode.COUNT,
                       category=EXTRA, portion_g=portion_g)
    session.add(extra)
    session.flush()
    session.add(IngredientItem(restaurant_id=user.restaurant_id, ingredient_id=extra.id,
                               name=name, last_cost=cost))
    session.flush()
    return extra


def set_extra_cost(session: Session, user: User, ingredient_id: int, cost: float,
                   portion_g: float | None = None) -> Ingredient:
    """[00587] Cambia el coste configurado y su porción. Se aplica a los platos desde ya."""
    extra = session.get(Ingredient, ingredient_id)
    if extra is None or extra.restaurant_id != user.restaurant_id:
        raise MeatError("Ese ingrediente no es de este restaurante")
    if cost < 0:
        raise MeatError("El coste no puede ser negativo")
    if portion_g is not None:
        if portion_g < 0:
            raise MeatError("La porción no puede ser negativa")
        extra.portion_g = portion_g or None
    item = extra.items[0] if extra.items else None
    if item is None:
        item = IngredientItem(restaurant_id=user.restaurant_id, ingredient_id=extra.id,
                              name=extra.name)
        session.add(item)
    item.last_cost = cost
    session.flush()
    return extra


def extras(session: Session, restaurant_id: int) -> list[Ingredient]:
    """[00588] Los acompañamientos que se pueden poner en un plato."""
    return (session.query(Ingredient)
            .filter_by(restaurant_id=restaurant_id, active=True, category=EXTRA)
            .order_by(Ingredient.name).all())


def extra_cost(extra: Ingredient) -> float | None:
    """[00589] Lo que costó la última vez ese acompañamiento, si consta."""
    return extra.items[0].last_cost if extra.items else None


def extras_usage(session: Session, restaurant_id: int) -> dict[int, int]:
    """[00590] En cuántos platos entra cada ingrediente. Cambiar su coste los mueve todos."""
    counts: dict[int, int] = {}
    for line in (session.query(RecipeLine)
                 .join(Recipe, RecipeLine.recipe_id == Recipe.id)
                 .filter(Recipe.restaurant_id == restaurant_id,
                         Recipe.kind == RecipeKind.DISH,
                         Recipe.active.is_(True),
                         RecipeLine.ingredient_id.isnot(None))):
        counts[line.ingredient_id] = counts.get(line.ingredient_id, 0) + 1
    return counts


def articles(session: Session, restaurant_id: int) -> list[IngredientItem]:
    """[00591] Los artículos de compra que están de alta, por nombre."""
    return (session.query(IngredientItem)
            .filter_by(restaurant_id=restaurant_id, active=True)
            .order_by(IngredientItem.name).all())


# ================================================================= despiece
@dataclass
class CutRow:
    name: str
    item_id: int
    pieces: int
    grams: float
    value_index: float = 1.0
    is_trim: bool = False
    by_weight: bool = False     # sale entero: se corta al vender
    kg: float = 0.0             # solo para los que salen a peso


def post_butchery(session: Session, user: User, tg: str, serials: list[str],
                  before_kg: float, rows: list[CutRow], waste_kg: float = 0.0,
                  on: date | None = None, staff: str | None = None,
                  country: str | None = None, grade: str | None = None,
                  lang: str = "es") -> tuple[Despiece, butchery.PostResult]:
    """[00592] Monta el despiece con lo que se ha escrito y lo vuelca a cámara."""
    # [00633] Una fila vale si dice cuántas piezas y de cuántos gramos, o —si sale a
    # peso— cuántos kilos entran enteros en cámara.
    rows = [r for r in rows if r.name.strip()
            and ((r.by_weight and r.kg) or (not r.by_weight and r.pieces and r.grams))]
    serials = [s.strip() for s in serials if s.strip()]
    if not serials:
        raise MeatError(t(lang, "m.tg.need_primals"))
    if not rows:
        raise MeatError(t(lang, "m.tg.need_cuts"))
    if any(not r.item_id for r in rows):
        raise MeatError(t(lang, "m.tg.need_article"))
    # [00634] El artículo tiene que ser de esta casa. Si no se comprueba, un formulario
    # manipulado mete un lote de este restaurante colgando del corte de otro.
    mine = {i.id for i in session.query(IngredientItem.id)
            .filter_by(restaurant_id=user.restaurant_id)}
    if any(r.item_id not in mine for r in rows):
        raise MeatError(t(lang, "m.tg.need_article"))
    # [00635] Todas las piezas de un despiece tienen que estar donde se despieza: un
    # despiece que mezcla la cámara del obrador con la del local no ha pasado
    # por ninguna mesa, y los cortes que salen no sabrían de dónde son.
    piezas = (session.query(Primal)
              .filter(Primal.restaurant_id == user.restaurant_id,
                      Primal.serial.in_(serials)).all())
    # [00636] Una pieza sin precio no se despieza. El despiece reparte el coste del
    # primal entre los cortes por su índice de valor; si la pieza vale cero,
    # todos los cortes salen a cero y el rastro del dinero se pierde ahí, sin
    # que nada avise. Se para aquí y no solo en la lista de la pantalla, porque
    # un formulario escrito a mano se salta la lista.
    sin_precio = [p.serial for p in piezas if p.landed_usd_per_kg is None]
    if sin_precio:
        raise MeatError(t(lang, "m.tg.no_price", serial=", ".join(sorted(sin_precio))))
    principal = sites.main(session, user.restaurant_id).id
    donde = {(p.site_id or principal) for p in piezas}
    mia = sites.of_user(session, user)
    if len(donde) > 1:
        raise MeatError(t(lang, "m.tg.mixed_sites"))
    if mia is not None and donde and mia.id not in donde:
        nombres = {x.id: x.name for x in sites.all_sites(session, user.restaurant_id,
                                                         active=False)}
        raise MeatError(t(lang, "m.tg.other_site",
                          site=nombres.get(next(iter(donde)), "")))
    tg = tg.strip()
    if not tg:
        raise MeatError("El despiece necesita su número")
    if session.query(Despiece).filter_by(restaurant_id=user.restaurant_id, tg=tg).first():
        # [00637] Si el número lo puso la casa —TG-0007, el que propone la pantalla—,
        # dos carniceros que abren la hoja a la vez traen el mismo y el segundo
        # perdía el despiece entero por un número. Se le da el siguiente libre.
        # Si el número lo escribió una persona, no se toca: ahí sí hay que
        # mirar qué despiece es el que ya existe.
        libre = _free_tg(session, user.restaurant_id) if AUTO_TG.match(tg) else None
        if libre is None:
            raise MeatError(f"Ya hay un despiece con el número {tg}")
        tg = libre

    numero = {"tg": tg}

    def otro_numero():
        """[00613] Busca el siguiente número de despiece libre, si el número lo puso la máquina.

        Uno escrito a mano no se cambia: que se vea que está repetido.
        """
        libre = (_free_tg(session, user.restaurant_id)
                 if AUTO_TG.match(numero["tg"]) else None)
        if libre is None:
            raise MeatError(f"Ya hay un despiece con el número {numero['tg']}") from None
        numero["tg"] = libre

    def montar():
        """[00614] Escribe el despiece. Se llama otra vez si hubo que cambiarle el número."""
        return _write_despiece(session, user, numero["tg"], serials, before_kg, rows,
                               waste_kg, on, staff, country, grade)

    # [00638] El nombre de la pieza, cogido ahora: si hay que reintentar, la sesión se
    # deshace y los objetos de antes ya no se pueden preguntar.
    etiqueta = (piezas[0].sku if piezas else "") or _primer_corte(rows)
    # [00639] Y dónde ha pasado: donde estaban las piezas. Un encargado sin sede que
    # despieza en el obrador lo anuncia en el obrador, no en ningún sitio.
    donde_id = mia.id if mia else (next(iter(donde)) if donde else None)
    despiece, result = locking.retry(session, montar, otro_numero)
    # [00640] La carne despiezada es carne nueva en cámara: el que cuenta tiene que
    # saber que ya no hay una pieza entera, sino cinco cortes con su peso.
    novedades.anotar(session, user, novedades.DESPIECE, ref=result.tg, label=etiqueta,
                     pieces=len(result.lots), kg=sum(l.qty for l in result.lots),
                     site_id=donde_id)
    return despiece, result


def _primer_corte(rows: list[CutRow]) -> str:
    """[00593] Si la pieza no tiene nombre, sirve el del primer corte que sale."""
    return next((r.name.strip() for r in rows if r.name.strip()), "")


def _write_despiece(session: Session, user: User, tg: str, serials: list[str],
                    before_kg: float, rows: list[CutRow], waste_kg: float,
                    on: date | None, staff: str | None, country: str | None,
                    grade: str | None) -> tuple[Despiece, butchery.PostResult]:
    """[00594] Escribe el despiece entero. Se monta de cero en cada intento."""
    despiece = Despiece(restaurant_id=user.restaurant_id, tg=tg, date=on or jornada.del_usuario(session, user),
                        staff=(staff or None), weight_before_kg=before_kg,
                        waste_kg=waste_kg, country=(country or None), grade=(grade or None))
    for serial in serials:
        despiece.primals.append(DespiecePrimal(serial=serial))
    for row in rows:
        despiece.cuts.append(DespieceCut(
            cut_name=row.name.strip(), item_id=row.item_id,
            pieces=0 if row.by_weight else row.pieces,
            weight_per_piece_g=0.0 if row.by_weight else row.grams,
            total_kg=round(row.kg, 4) if row.by_weight
            else round(row.pieces * row.grams / 1000, 4),
            value_index=row.value_index or 1.0, is_trim=row.is_trim,
            by_weight=row.by_weight))
    session.add(despiece)
    session.flush()

    try:
        result = butchery.post(session, user, despiece)
    except butchery.ButcheryError as e:
        session.delete(despiece)      # no se deja a medias un despiece que no cuadra
        session.flush()
        raise MeatError(str(e)) from None
    return despiece, result


def recent_butchery(session: Session, restaurant_id: int, limit: int = 30) -> list[Despiece]:
    """[00595] Los últimos despieces, del más nuevo al más viejo."""
    return (session.query(Despiece).filter_by(restaurant_id=restaurant_id)
            .order_by(Despiece.date.desc(), Despiece.id.desc()).limit(limit).all())


def next_tg(session: Session, restaurant_id: int) -> str:
    """[00596] El siguiente número de despiece, para no tener que acordarse."""
    n = session.query(Despiece).filter_by(restaurant_id=restaurant_id).count()
    return f"TG-{n + 1:04d}"


def _free_tg(session: Session, restaurant_id: int) -> str | None:
    """[00597] Un número de despiece que no esté cogido, empezando por el siguiente."""
    usados = {d.tg for d in session.query(Despiece.tg)
              .filter(Despiece.restaurant_id == restaurant_id)}
    n = len(usados) + 1
    for _ in range(200):
        propuesto = f"TG-{n:04d}"
        if propuesto not in usados:
            return propuesto
        n += 1
    return None


# =================================================================== carta
def add_dish(session: Session, user: User, name: str, cut_id: int, grams: float,
             sale_price: float | None = None, vat_pct: float = 0.0,
             pos_code: str | None = None, pos_name: str | None = None,
             by_weight: bool = False, price_per_kg: float | None = None,
             lang: str = "es") -> Recipe:
    """[00598] Un plato de carne: un corte, unos gramos y su producto del POS.

    Por dentro es una receta de una línea, así que el food cost, el descuento
    de cámara y el reparto del ingreso salen del mismo motor que ya está
    probado, sin una segunda manera de calcular lo mismo.

    Un plato **a peso** es el mismo plato con otra manera de cobrar: el precio
    va por kilo y los gramos los manda el POS en cada venta. Los gramos que se
    escriben aquí son la ración de referencia, la que sirve para ver el food
    cost en la carta antes de vender nada.
    """
    name = name.strip()
    if not name:
        raise MeatError("El plato necesita un nombre")
    cut = session.get(Ingredient, cut_id)
    if cut is None or cut.restaurant_id != user.restaurant_id:
        raise MeatError(t(lang, "m.menu.need_cut"))
    if grams <= 0:
        raise MeatError(t(lang, "m.menu.need_cut"))

    code = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")[:64]
    if session.query(Recipe).filter_by(restaurant_id=user.restaurant_id, code=code).first():
        raise MeatError(f"Ya hay un plato llamado {name}")

    if by_weight and not price_per_kg:
        raise MeatError(t(lang, "m.menu.need_price_kg"))
    if by_weight and sale_price is None:
        # [00641] El PVP de la ración de referencia, para que la carta sepa comparar.
        sale_price = round(price_per_kg * grams / 1000, 4)

    dish = Recipe(restaurant_id=user.restaurant_id, code=code, name=name,
                  kind=RecipeKind.DISH, portions=1, sale_price=sale_price, vat_pct=vat_pct,
                  by_weight=bool(by_weight), price_per_kg=price_per_kg)
    session.add(dish)
    session.flush()
    session.add(RecipeLine(recipe_id=dish.id, ingredient_id=cut.id,
                           qty=round(grams / 1000, 6), waste_pct=0.0, sort_order=0,
                           by_weight=bool(by_weight)))
    session.add(PosProduct(restaurant_id=user.restaurant_id, recipe_id=dish.id,
                           pos_code=(pos_code or "").strip() or None,
                           pos_name=(pos_name or "").strip() or name))
    session.flush()
    return dish


@dataclass
class MenuRow:
    dish: Recipe
    cut: str
    grams: float
    extras: int            # cuántas cosas más van en el plato
    cost: float | None
    food_cost_pct: float | None
    pos_code: str | None
    pos_name: str
    # [00642] Si el plato está emparejado de verdad con un producto del POS. Sin esto
    # no hay forma de distinguir un plato atado a su artículo de uno que
    # todavía no lo está: el nombre se enseñaba igual en los dos casos, porque
    # cuando faltaba se caía al nombre del propio plato.
    paired: bool = False


def menu(session: Session, restaurant_id: int) -> list[MenuRow]:
    """[00599] La carta de carnes, del peor food cost al mejor."""
    costs = costing.unit_costs(session, restaurant_id)
    products = {p.recipe_id: p for p in
                session.query(PosProduct).filter_by(restaurant_id=restaurant_id)}
    rows = []
    for dish in (session.query(Recipe)
                 .filter_by(restaurant_id=restaurant_id, kind=RecipeKind.DISH, active=True)
                 .order_by(Recipe.name)):
        line = meat_line(dish, session)
        cut = session.get(Ingredient, line.ingredient_id) if line and line.ingredient_id else None
        cost = costing.cost_of(session, dish, costs)
        product = products.get(dish.id)
        rows.append(MenuRow(
            dish=dish, cut=cut.name if cut else "", grams=round((line.qty if line else 0) * 1000, 1),
            extras=max(len(dish.lines) - (1 if line else 0), 0),
            cost=cost.cost_per_portion, food_cost_pct=cost.food_cost_pct,
            pos_code=product.pos_code if product else None,
            pos_name=product.pos_name if product else dish.name,
            paired=product is not None))
    rows.sort(key=lambda r: (r.food_cost_pct is None, -(r.food_cost_pct or 0)))
    return rows


# =============================================================== emplatado
def meat_line(dish: Recipe, session: Session) -> RecipeLine | None:
    """[00600] La línea de carne del plato: la que manda y la que se descuenta."""
    for line in dish.lines:
        if not line.ingredient_id:
            continue
        ingredient = session.get(Ingredient, line.ingredient_id)
        if ingredient is not None and ingredient.category != EXTRA:
            return line
    return None


def add_plate_line(session: Session, user: User, dish: Recipe, ingredient_id: int,
                   qty_small: float, waste_pct: float = 0.0, lang: str = "es") -> RecipeLine:
    """[00601] Añade al plato algo que no es la carne. La cantidad, en gramos."""
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None or ingredient.restaurant_id != user.restaurant_id:
        raise MeatError("Ese ingrediente no es de este restaurante")
    if qty_small <= 0:
        raise MeatError(t(lang, "m.plate.qty"))
    qty = to_base(ingredient.unit, qty_small)
    if not 0 <= waste_pct < 100:
        raise MeatError("La merma de limpieza va entre 0 y 100")
    # [00643] Detrás de lo que ya hay, para que la carne siga la primera y el orden del
    # plato sea el orden en que se fue montando.
    last = max((l.sort_order for l in dish.lines), default=0)
    line = RecipeLine(recipe_id=dish.id, ingredient_id=ingredient.id, qty=qty,
                      waste_pct=waste_pct, sort_order=last + 10)
    session.add(line)
    session.flush()
    return line


def remove_plate_line(session: Session, user: User, dish: Recipe, line_id: int,
                      lang: str = "es") -> None:
    """[00602] Quita del plato una línea. La de carne no se quita: el plato es de carne."""
    line = session.get(RecipeLine, line_id)
    if line is None or line.recipe_id != dish.id:
        raise MeatError("Esa línea no es de este plato")
    carne = meat_line(dish, session)
    if carne is not None and line.id == carne.id:
        raise MeatError(t(lang, "m.menu.need_cut"))
    session.delete(line)
    session.flush()


def set_plate_grams(session: Session, user: User, dish: Recipe, grams: float,
                    lang: str = "es") -> None:
    """[00603] Cambia el gramaje de carne del plato, que es lo que se descuenta."""
    if grams <= 0:
        raise MeatError(t(lang, "m.menu.need_cut"))
    line = meat_line(dish, session)
    if line is None:
        raise MeatError(t(lang, "m.menu.need_cut"))
    line.qty = round(grams / 1000, 6)
    session.flush()


@dataclass
class PlateLine:
    line_id: int | None
    name: str
    unit: str
    qty: float                 # en la unidad base, que es como se guarda
    qty_small: float           # y en gramos, que es como se lee
    small: str                 # g, ml o unidades
    waste_pct: float
    gross_qty: float
    gross_small: float
    unit_cost: float | None    # por kilo, por litro o por unidad
    cost: float                # lo que sale esa porción
    share_pct: float
    is_meat: bool


@dataclass
class Plate:
    dish: Recipe
    lines: list[PlateLine]
    cost: float
    food_cost_pct: float | None
    margin: float | None
    pos_code: str | None
    pos_name: str
    missing_price: list[str]
    paired: bool = False

    @property
    def meat(self) -> PlateLine | None:
        """[00615] La línea de carne del plato. Es la que manda en el escandallo."""
        return next((l for l in self.lines if l.is_meat), None)

    @property
    def extras(self) -> list[PlateLine]:
        """[00616] Todo lo demás del plato: guarnición, salsa, pan."""
        return [l for l in self.lines if not l.is_meat]


def plate(session: Session, restaurant_id: int, dish: Recipe, lang: str = "es") -> Plate:
    """[00604] El emplatado entero: qué lleva, qué cuesta cada cosa y su food cost."""
    costs = costing.unit_costs(session, restaurant_id)
    detail = costing.cost_of(session, dish, costs)
    carne = meat_line(dish, session)
    product = (session.query(PosProduct)
               .filter_by(restaurant_id=restaurant_id, recipe_id=dish.id).first())

    lines = []
    for line, computed in zip(dish.lines, detail.lines):
        ingredient = session.get(Ingredient, line.ingredient_id) if line.ingredient_id else None
        unit = ingredient.unit if ingredient else Unit.KG
        lines.append(PlateLine(
            line_id=line.id, name=computed.label, unit=computed.unit,
            qty=computed.net_qty, qty_small=to_small(unit, computed.net_qty),
            small=small_unit(unit, lang), waste_pct=computed.waste_pct,
            gross_qty=computed.gross_qty, gross_small=to_small(unit, computed.gross_qty),
            unit_cost=computed.unit_cost, cost=computed.cost, share_pct=computed.share_pct,
            is_meat=carne is not None and line.id == carne.id))
    return Plate(dish=dish, lines=lines, cost=detail.cost_per_portion,
                 food_cost_pct=detail.food_cost_pct, margin=detail.margin_per_portion,
                 pos_code=product.pos_code if product else None,
                 pos_name=product.pos_name if product else dish.name,
                 missing_price=list(detail.missing), paired=product is not None)


# ============================================================ parte del día
@dataclass
class DailyReport:
    """[00605] El parte de carne de un día, para el pase y para la carpeta.

    Es la foto que se cuelga: lo que hay, lo que se ha ido y lo que queda
    pendiente. Se imprime desde el navegador, que es lo que hay en una cocina,
    y sale igual en papel que en pantalla.
    """
    date: date
    site: str = ""
    status: butchery.MeatStatus | None = None
    aging: aging_mod.Summary | None = None
    to_weigh: list = field(default_factory=list)       # las que maduran sin pesar hoy
    counted: list = field(default_factory=list)        # lo pesado hoy, pieza a pieza
    thawing: list = field(default_factory=list)        # los números descongelando
    shifts: list = field(default_factory=list)         # los turnos cerrados del día
    waste: list = field(default_factory=list)          # lo tirado hoy
    sales_units: int = 0
    sales_kg: float = 0.0
    pending: list[str] = field(default_factory=list)
    stock_value: float = 0.0
    # [01632] Lo que se ingresó con la carne del día y lo que costó esa carne. Los dos
    # juntos son lo único que contesta «¿cuánto hemos ganado hoy con los kilos
    # que han salido?», que es la pregunta del cierre. El parte tenía los
    # kilos y las unidades, y con eso se sabe cuánto se ha movido pero no si
    # ha valido la pena.
    sales_revenue: float = 0.0
    sales_cost: float = 0.0

    @property
    def sales_margin(self) -> float:
        """[01630] Lo ganado con la carne que salió hoy: lo cobrado menos lo que costó."""
        return round(self.sales_revenue - self.sales_cost, 2)

    @property
    def sales_food_cost_pct(self) -> float | None:
        """[01631] A qué food cost ha salido el día. Sin ingresos, no hay porcentaje."""
        if self.sales_revenue <= 1e-9:
            return None
        return round(self.sales_cost / self.sales_revenue * 100, 2)

    @property
    def waste_kg(self) -> float:
        """[00617] Los kilos tirados en el día."""
        return round(sum(w.kg for w in self.waste), 3)

    @property
    def waste_cost(self) -> float:
        """[00618] Lo que costó lo que se tiró."""
        return round(sum(w.cost or 0.0 for w in self.waste), 2)

    @property
    def day_loss(self) -> float:
        """[00619] Lo que se ha ido hoy en dinero: el desvío del turno, el agua y la merma."""
        return round(sum((c.loss_cost or 0.0) + (c.drip_cost or 0.0) for c in self.shifts)
                     + self.waste_cost, 2)


def daily_report(session: Session, restaurant_id: int, on: date | None = None,
                 lang: str = "es", site_id: int | None = None) -> DailyReport:
    """[00606] El parte del día: lo que hay, lo que se ha ido y lo que falta por hacer."""
    from thegrill.models import SalesByProduct, ShiftClosure, Site

    on = on or jornada.hoy(session, restaurant_id)
    # [00644] Una lectura de las pesadas y una de la pizarra para todo el parte: antes
    # el parte pedía lo mismo cinco veces —la portada por dentro, y otras dos
    # para saber qué falta por pesar— y tardaba el doble que la pantalla más
    # lenta de la casa.
    history = aging_mod.history_of(session, restaurant_id, in_stock_only=True)
    rows = aging_mod.board(session, restaurant_id, on=on, site_id=site_id, history=history)
    hoy = today(session, restaurant_id, on=on, lang=lang, site_id=site_id,
                history=history, rows=rows)
    sede = session.get(Site, site_id) if site_id else None
    out = DailyReport(date=on, site=sede.name if sede else "", status=hoy.status,
                      aging=hoy.aging, pending=hoy.pending, stock_value=hoy.stock_value)

    lineas = aging_mod.to_count(session, restaurant_id, on, site_id,
                                rows=rows, history=history)
    out.to_weigh = [l for l in lineas if l.kg is None]
    out.counted = [l for l in lineas if l.kg is not None]
    out.thawing = [st for st in defrost.shift_states(session, restaurant_id, on,
                                                     site_id=site_id)
                   if st.intake_pieces or st.opening_pieces]
    query = (session.query(ShiftClosure)
             .filter(ShiftClosure.restaurant_id == restaurant_id, ShiftClosure.date == on))
    if site_id:
        query = query.filter(ShiftClosure.site_id == site_id)
    out.shifts = query.order_by(ShiftClosure.shift).all()
    out.waste = [w for w in waste_mod.everything(session, restaurant_id, days=1, on=on)
                 if w.date == on]
    for row in (session.query(SalesByProduct)
                .filter_by(restaurant_id=restaurant_id, op_date=on)):
        out.sales_units += row.units or 0
        out.sales_kg = round(out.sales_kg + (row.kg or 0.0), 6)
    _lo_ganado_hoy(session, restaurant_id, on, out)
    return out


def _lo_ganado_hoy(session: Session, restaurant_id: int, on: date,
                   out: DailyReport) -> None:
    """[01629] Lo que se ingresó con la carne del día y lo que costó esa carne.

    El coste sale de los apuntes de salida, que es lo que de verdad se
    descontó de la cámara. El ingreso se reparte igual que en la
    trazabilidad: cada venta de un plato reparte su precio sin impuestos entre
    sus ingredientes en proporción a lo que cuesta cada uno dentro de ese
    plato, y lo que cae sobre la carne es lo que se le atribuye. Sin ese
    reparto, un entrecot con su guarnición y su salsa se apuntaría el precio
    entero del plato y el margen del día saldría inflado.

    Y aparte, lo que se cortó y se cobró al peso, que no pasa por el
    escandallo: ahí el precio es el precio, sin repartir nada.
    """
    from thegrill.models import IngredientMovement, MovementKind, WeightSale
    from thegrill.web import tracing as tracing_mod

    ratios = tracing_mod.revenue_ratios(session, restaurant_id)
    ingreso = coste = 0.0
    for mv in (session.query(IngredientMovement)
               .filter_by(restaurant_id=restaurant_id, date=on,
                          kind=MovementKind.SALE)):
        suyo = mv.cost or 0.0
        coste += suyo
        ratio = ratios.get((mv.source_ref, mv.ingredient_id))
        if ratio is None:
            # [01633] La venta que no dice el plato —el conteo del descongelado— se
            # valora por la media de los platos que llevan ese ingrediente:
            # es mejor que apuntarle cero ingreso a carne que se vendió.
            ratio = tracing_mod.day_ratio(ratios, mv.ingredient_id)
        ingreso += suyo * ratio if ratio else 0.0

    for venta in (session.query(WeightSale)
                  .filter_by(restaurant_id=restaurant_id, date=on)):
        ingreso += venta.price or 0.0
        coste += venta.cost or 0.0

    out.sales_revenue = round(ingreso, 2)
    out.sales_cost = round(coste, 2)


# ==================================================================== hoy
@dataclass
class PendingThing:
    """[00607] Una de las cosas concretas de las que habla una línea de lo pendiente.

    «2 cortes por debajo del mínimo» no dice cuáles son ni dónde están, así que
    hay que ir a buscarlos a mano por otra pantalla. Cada cosa viene con su
    nombre, el número que la explica y la pantalla donde se arregla.
    """
    label: str                       # el número de pieza, el nombre del corte
    where: str                       # a qué pantalla lleva
    note: str = ""                   # lo que hace falta saber: días, kilos


@dataclass
class PendingLine:
    """[00608] Una línea de lo pendiente, con lo que hay detrás."""
    text: str
    where: str                       # la pantalla de la línea entera
    where_label: str = ""            # cómo se llama esa pantalla
    things: list[PendingThing] = field(default_factory=list)
    more: int = 0                    # las que no caben en la lista


# [00645] Lo pendiente se enseña con nombres, pero una casa grande puede tener
# cincuenta piezas sin pesar y esa lista tapa la portada entera. Se enseñan las
# primeras y la línea dice cuántas quedan.
A_LA_VISTA = 12


def _cuanto_falta(lang: str, dias: int | None) -> str:
    """[00609] Lo que le queda a un lote. Si ya pasó, se dice así y no «en -7 días»."""
    if dias is None:
        return ""
    if dias < 0:
        return t(lang, "m.home.days_late", n=-dias)
    return t(lang, "m.home.in_days", n=dias)


@dataclass
class Today:
    status: butchery.MeatStatus
    pending: list[PendingLine] = field(default_factory=list)
    stock_value: float = 0.0
    thawing: int = 0
    uncounted: int = 0
    no_price: int = 0
    open_count: MeatCount | None = None
    month_due: bool = False
    aging: aging_mod.Summary | None = None


def today(session: Session, restaurant_id: int, on: date | None = None,
          lang: str = "es", site_id: int | None = None,
          history: aging_mod.History | None = None, rows: list | None = None) -> Today:
    """[00610] Lo que está pendiente en la carne, en una pantalla.

    Con sede, lo pendiente de esa sede: el del local no arregla la cámara del
    obrador, y las piezas que maduran en su local las pesa él.

    Lo que se ha pesado en la casa se lee una sola vez: con medio año dentro
    son cinco mil pesadas, y leerlas dos veces por pantalla —una para la
    pizarra y otra para el resumen— era la mitad del tiempo de la portada.
    Quien ya las tenga leídas las pasa y aquí no se vuelven a pedir.
    """
    on = on or jornada.hoy(session, restaurant_id)
    # [00646] De la cámara de ahora: la portada habla de lo que hay, no de lo que hubo.
    history = history or aging_mod.history_of(session, restaurant_id, in_stock_only=True)
    status = butchery.status(session, restaurant_id, on=on, site_id=site_id)
    value = round(sum(lot.qty_remaining * lot.unit_cost for lot in
                      costing.at_site(session.query(IngredientLot)
                                      .filter(IngredientLot.restaurant_id == restaurant_id,
                                              IngredientLot.qty_remaining > 1e-9),
                                      session, restaurant_id, site_id)), 2)

    states = defrost.shift_states(session, restaurant_id, on)
    thawing = [s for s in states if s.opening_pieces or s.intake_pieces]
    # [00647] Salió a descongelar y nadie ha contado lo que quedaba: sin eso no hay cierre.
    uncounted = [s for s in thawing if s.closing_pieces is None]

    # [00648] Lo que madura: las que ya han cumplido sus días y las que llevan una
    # semana sin pesar, que es cuando la merma deja de estar controlada.
    aging_rows = rows if rows is not None else aging_mod.board(
        session, restaurant_id, on=on, site_id=site_id, history=history)
    ready = [r for r in aging_rows if r.storage == Storage.AGING and r.ready]
    # [00649] Lo que madura está fresco y abierto: se pesa todos los días, como se
    # cuenta lo descongelado. Sin ese peso, la merma del día no existe.
    stale = [r for r in aging_rows if r.storage == Storage.AGING and r.last_weighed != on]

    month = inventory.monthly_status(session, restaurant_id, on=on, site_id=site_id)
    open_count = inventory.open_now(session, restaurant_id, site_id)
    sin_volcar = (session.query(Despiece)
                  .filter_by(restaurant_id=restaurant_id, posted=False)
                  .order_by(Despiece.date.desc()).limit(A_LA_VISTA + 1).all())
    unposted = (session.query(Despiece)
                .filter_by(restaurant_id=restaurant_id, posted=False).count())
    # [00650] Piezas en la cámara que todavía no valen nada: no se pueden despiezar y
    # nadie se entera hasta que alguien va a cortarlas y no están en la lista.
    sin_precio = awaiting_price(session, restaurant_id, site_id=site_id)

    # [00651] Cada línea se lleva las cosas de las que habla y la pantalla donde se
    # arreglan: un número suelto obliga a ir a buscarlas a mano.
    pending: list[PendingLine] = []

    def anotar(clave: str, cuantas: int, donde: str, nombre: str,
               cosas: list[PendingThing]) -> None:
        """[00620] Añade una línea al parte de pendientes, con unos pocos ejemplos.

        Se enseñan los primeros y se dice cuántos más quedan: una lista de
        cuarenta piezas sin pesar no la lee nadie, y el número sí se ve.
        """
        pending.append(PendingLine(
            text=t(lang, clave, n=cuantas), where=donde, where_label=nombre,
            things=cosas[:A_LA_VISTA], more=max(0, len(cosas) - A_LA_VISTA)))

    if sin_precio:
        anotar("m.home.no_price", len(sin_precio), "/recepcion/precios",
               t(lang, "m.price.nav"),
               [PendingThing(p.serial, "/recepcion/precios", p.sku or "")
                for p in sin_precio])
    if uncounted:
        anotar("m.home.defrost_open", len(uncounted), "/descongelado/recuento",
               t(lang, "m.nav.defrost_count"),
               [PendingThing(st.serial, "/descongelado/recuento",
                             f"{t(lang, 'm.df.out')}: {st.out_pieces}")
                for st in uncounted])
    if not month.done:
        pending.append(PendingLine(text=t(lang, "m.home.count_due"),
                                   where="/inventario",
                                   where_label=t(lang, "nav.inventory")))
    below = len(status.cuts_below) + len(status.primals_below)
    if below:
        anotar("m.home.below_par", below, "/carne", t(lang, "m.nav.chamber"),
               [PendingThing(c.name, "/carne",
                             f"{c.kg:.10g} / {c.min_stock:.10g} {c.unit}")
                for c in status.cuts_below]
               + [PendingThing(p.sku, "/carne",
                               f"{p.pieces} / {p.min_pieces}")
                  for p in status.primals_below])
    if status.expiring:
        anotar("m.home.expiring", len(status.expiring), "/carne",
               t(lang, "m.nav.chamber"),
               [PendingThing(c.name, "/carne", _cuanto_falta(lang, c.days_to_expiry))
                for c in status.expiring])
    if unposted:
        anotar("m.home.unposted", unposted, "/despiece", t(lang, "m.nav.butchery"),
               [PendingThing(d.tg, "/despiece", str(d.date)) for d in sin_volcar])
    if ready:
        anotar("m.home.aging_ready", len(ready), "/maduracion", t(lang, "m.nav.aging"),
               [PendingThing(r.serial, "/maduracion",
                             t(lang, "m.ag.n_days", n=r.days)) for r in ready])
    if stale:
        anotar("m.home.aging_unweighed", len(stale), "/maduracion",
               t(lang, "m.nav.aging"),
               [PendingThing(r.serial, "/maduracion",
                             t(lang, "m.ag.n_days", n=r.days)) for r in stale])

    return Today(status=status, pending=pending, stock_value=value,
                 no_price=len(sin_precio),
                 thawing=len(thawing), uncounted=len(uncounted),
                 open_count=open_count, month_due=not month.done,
                 aging=aging_mod.summary(session, restaurant_id, on=on, site_id=site_id,
                                         rows=aging_rows))
