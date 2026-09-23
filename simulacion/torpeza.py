"""Las equivocaciones que se cometen de verdad en una cocina, y quién las ve.

Cada una trae tres cosas:

- **cómo se mete**: por el mismo camino que la metería el empleado. Nada de
  escribir a pelo en la base de datos: si el programa la rechaza, eso ya es
  una respuesta —y de las buenas—.
- **quién se equivoca**: por torpeza (el dedo, la prisa, el guante) o por
  dejadez (no lo hace y ya está). Se cuentan aparte porque se arreglan
  distinto: la torpeza se evita con la pantalla, la dejadez con el aviso.
- **dónde tendría que verlo el manager**: un aviso, un número de la portada o
  una línea de lo pendiente. Solo desde la web, que es lo único que va a
  mirar. Si no se ve en ningún sitio, la equivocación queda CIEGA, y eso es un
  defecto del programa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from thegrill.models import (Alert, CountStatus, IngredientLot, Primal,
                             PrimalStatus, Recipe, Storage, User)


# ------------------------------------------------------------------- resultado
@dataclass
class Metida:
    """Lo que el empleado hizo mal, con la verdad que solo sabemos nosotros."""
    clave: str
    serial: str = ""
    dicho: str = ""                  # lo que quedó escrito
    verdad: str = ""                 # lo que pasaba de verdad
    rechazada: bool = False          # el programa no la dejó entrar
    porque: str = ""                 # qué dijo al rechazarla
    extra: dict = field(default_factory=dict)


@dataclass
class Visto:
    """Si el manager lo ve sin salir de la web, y dónde."""
    visto: bool
    donde: str = ""


@dataclass
class Equivocacion:
    clave: str
    quien: str                       # "torpeza" o "dejadez"
    cuenta: str                      # qué hace el empleado, en una frase
    mete: Callable
    ve: Callable
    critica: bool = False            # de las que cuestan dinero o salud


# --------------------------------------------------------------- herramientas
def _avisos(session: Session, restaurant_id: int, *codigos: str) -> list[Alert]:
    """Los avisos que le salen al manager en su pantalla de alertas."""
    q = session.query(Alert).filter_by(restaurant_id=restaurant_id)
    if codigos:
        q = q.filter(Alert.code.in_(codigos))
    return q.all()


def _portada(session: Session, restaurant_id: int, on: date):
    from thegrill.meat import service as meat_service
    return meat_service.today(session, restaurant_id, on=on)


def _en_lo_pendiente(portada, texto: str) -> Visto:
    """Busca una pieza concreta en la lista de lo pendiente de la portada."""
    for linea in portada.pending:
        for cosa in linea.things:
            if texto and texto in (cosa.label or ""):
                return Visto(True, f"portada · {linea.text} → {cosa.label} ({cosa.where})")
    return Visto(False)


def _pieza_en_camara(session: Session, restaurant_id: int) -> Primal | None:
    return (session.query(Primal)
            .filter_by(restaurant_id=restaurant_id, status=PrimalStatus.IN_STOCK)
            .filter(Primal.weight_kg > 1.0).first())


def _lote_con_carne(session: Session, restaurant_id: int) -> IngredientLot | None:
    return (session.query(IngredientLot)
            .filter_by(restaurant_id=restaurant_id)
            .filter(IngredientLot.qty_remaining > 2.0).first())


def _manager(session: Session, casa) -> User:
    return session.get(User, casa.manager)


def _carnicero(session: Session, casa) -> User:
    from thegrill import bench
    return bench.carnicero_de(session, casa.restaurant_id) or _manager(session, casa)


# =========================================================== las equivocaciones
# --- 1. el dedo gordo en la báscula ------------------------------------------
def _mete_peso_de_mas(session, casa, hoy):
    """Teclea 84,0 donde ponía 8,40: se le fue la coma."""
    from thegrill.meat import service as meat
    from thegrill.models import Unit

    pieza = _pieza_en_camara(session, casa.restaurant_id)
    if pieza is None:
        return None
    bueno, malo = 8.4, 84.0
    lote = meat.next_lot(session, casa.restaurant_id, on=hoy)
    serial = meat.next_serials(session, casa.restaurant_id, 1)[0]
    try:
        meat.receive_primals(session, _carnicero(session, casa), lote,
                             [meat.PrimalRow(serial=serial, kg=malo, sku=pieza.sku,
                                             price_kg=30.0, use_by=hoy + timedelta(days=40))],
                             received=hoy)
    except Exception as e:                                   # noqa: BLE001
        return Metida("peso_de_mas", serial, rechazada=True, porque=str(e)[:120])
    return Metida("peso_de_mas", serial, dicho=f"{malo} kg", verdad=f"{bueno} kg")


def _ve_peso_de_mas(session, casa, metida, hoy):
    """Una pieza de 84 kilos no existe: o es un buey entero o es un dedo."""
    pieza = (session.query(Primal)
             .filter_by(restaurant_id=casa.restaurant_id, serial=metida.serial).first())
    if pieza is None:
        return Visto(False)
    hermanas = [p.weight_kg for p in session.query(Primal)
                .filter_by(restaurant_id=casa.restaurant_id, sku=pieza.sku)
                if p.serial != metida.serial and p.weight_kg]
    if not hermanas:
        return Visto(False)
    media = sum(hermanas) / len(hermanas)
    avisos = _avisos(session, casa.restaurant_id)
    for a in avisos:
        if metida.serial in (a.message or ""):
            return Visto(True, f"aviso {a.code}")
    # Nadie avisa. ¿Al menos se nota mirando la cámara?
    return Visto(False, f"pesa {pieza.weight_kg:.1f} kg contra {media:.1f} de media y nadie dice nada")


# --- 2. el número transpuesto -------------------------------------------------
def _mete_serial_repetido(session, casa, hoy):
    """Escribe el número de otra pieza: dos bolsas con el mismo papel."""
    from thegrill.meat import service as meat

    pieza = _pieza_en_camara(session, casa.restaurant_id)
    if pieza is None:
        return None
    lote = meat.next_lot(session, casa.restaurant_id, on=hoy)
    try:
        meat.receive_primals(session, _carnicero(session, casa), lote,
                             [meat.PrimalRow(serial=pieza.serial, kg=9.1, sku=pieza.sku,
                                             price_kg=30.0, use_by=hoy + timedelta(days=40))],
                             received=hoy)
    except Exception as e:                                   # noqa: BLE001
        return Metida("serial_repetido", pieza.serial, rechazada=True, porque=str(e)[:120])
    return Metida("serial_repetido", pieza.serial, dicho="dos piezas con el mismo número")


def _ve_serial_repetido(session, casa, metida, hoy):
    if metida.rechazada:
        return Visto(True, "el programa no la dejó entrar")
    cuantas = (session.query(Primal)
               .filter_by(restaurant_id=casa.restaurant_id, serial=metida.serial).count())
    return Visto(False, f"{cuantas} piezas con el número {metida.serial}")


# --- 3. la factura que nadie mira --------------------------------------------
def _mete_precio_olvidado(session, casa, hoy):
    """Descarga el camión y no pone el precio. Diez días."""
    from thegrill.meat import service as meat

    lote = meat.next_lot(session, casa.restaurant_id, on=hoy - timedelta(days=10))
    serial = meat.next_serials(session, casa.restaurant_id, 1)[0]
    pieza = _pieza_en_camara(session, casa.restaurant_id)
    try:
        meat.receive_primals(session, _carnicero(session, casa), lote,
                             [meat.PrimalRow(serial=serial, kg=9.2,
                                             sku=pieza.sku if pieza else "Striploin AUS",
                                             price_kg=None,
                                             use_by=hoy + timedelta(days=40))],
                             received=hoy - timedelta(days=10))
    except Exception as e:                                   # noqa: BLE001
        return Metida("precio_olvidado", serial, rechazada=True, porque=str(e)[:120])
    return Metida("precio_olvidado", serial, dicho="sin precio desde hace 10 días")


def _ve_precio_olvidado(session, casa, metida, hoy):
    portada = _portada(session, casa.restaurant_id, hoy)
    if portada.no_price:
        visto = _en_lo_pendiente(portada, metida.serial)
        if visto.visto:
            return visto
        return Visto(True, f"portada · {portada.no_price} esperando precio")
    return Visto(False)


# --- 4. el que no pesa lo que madura -----------------------------------------
def _mete_maduracion_sin_pesar(session, casa, hoy):
    """Deja de pesar lo que madura durante una semana.

    No basta con señalar una pieza: se le quitan las pesadas de los últimos
    siete días, que es lo que queda cuando alguien no las hace. Así la pieza
    lleva una semana de verdad sin pasar por la báscula.
    """
    from thegrill.models import PrimalWeighing

    pieza = (session.query(Primal)
             .filter_by(restaurant_id=casa.restaurant_id, storage=Storage.AGING,
                        status=PrimalStatus.IN_STOCK).first())
    if pieza is None:
        return None
    borradas = (session.query(PrimalWeighing)
                .filter_by(restaurant_id=casa.restaurant_id, primal_id=pieza.id)
                .filter(PrimalWeighing.date >= hoy - timedelta(days=7)).all())
    for p in borradas:
        session.delete(p)
    session.flush()
    return Metida("maduracion_sin_pesar", pieza.serial,
                  dicho="siete días sin pesar", verdad=f"{len(borradas)} pesadas que no se hicieron")


def _ve_maduracion_sin_pesar(session, casa, metida, hoy):
    portada = _portada(session, casa.restaurant_id, hoy)
    visto = _en_lo_pendiente(portada, metida.serial)
    if visto.visto:
        return visto
    if portada.uncounted:
        return Visto(True, f"portada · {portada.uncounted} madurando sin pesar")
    avisos = _avisos(session, casa.restaurant_id, "aging.uncounted")
    if avisos:
        return Visto(True, "aviso aging.uncounted")
    return Visto(False)


# --- 5. la carne que se va sin apuntar ---------------------------------------
def _mete_merma_no_apuntada(session, casa, hoy):
    """Se echa a perder una caja y nadie apunta la merma: desaparece y ya."""
    lote = _lote_con_carne(session, casa.restaurant_id)
    if lote is None:
        return None
    se_van = round(lote.qty_remaining * 0.35, 3)
    antes = lote.qty_remaining
    # El empleado no toca el programa: la carne sale de la cámara y punto.
    # Aquí se simula el hueco, que es justo lo que el inventario tiene que ver.
    lote.qty_remaining = round(antes - se_van, 6)
    session.flush()
    return Metida("merma_no_apuntada", lote.serial or f"lote {lote.id}",
                  dicho="nada", verdad=f"{se_van:.3f} kg fuera de la cámara",
                  extra={"lote": lote.id, "kg": se_van})


def _ve_merma_no_apuntada(session, casa, metida, hoy):
    """Solo se ve al contar: por eso se cuenta. ¿Sale el faltante?"""
    from thegrill.web import inventory

    usuario = _manager(session, casa)
    abierto = inventory.open_now(session, casa.restaurant_id)
    try:
        cuenta = abierto or inventory.open_count(session, usuario, on=hoy)
        for linea in cuenta.lines:
            lote = session.query(IngredientLot).filter_by(
                restaurant_id=casa.restaurant_id, serial=linea.serial).first()
            inventory.record(session, usuario, cuenta, linea.serial,
                             round(lote.qty_remaining, 3) if lote else 0.0)
        resultado = inventory.close_count(session, usuario, cuenta)
    except Exception as e:                                   # noqa: BLE001
        return Visto(False, f"no se pudo cerrar el inventario: {str(e)[:80]}")
    for a in resultado.alerts:
        if a.code.startswith("count."):
            return Visto(True, f"al cerrar inventario · aviso {a.code}")
    return Visto(False, "el inventario cerró sin decir nada del faltante")


# --- 6. el inventario a medias ------------------------------------------------
def _mete_inventario_a_medias(session, casa, hoy):
    """Cuenta la mitad de la cámara, se aburre y cierra."""
    from thegrill.web import inventory

    usuario = _manager(session, casa)
    if inventory.open_now(session, casa.restaurant_id):
        return None
    try:
        cuenta = inventory.open_count(session, usuario, on=hoy)
    except Exception as e:                                   # noqa: BLE001
        return Metida("inventario_a_medias", rechazada=True, porque=str(e)[:120])
    lineas = list(cuenta.lines)
    if len(lineas) < 4:
        return None
    for linea in lineas[:len(lineas) // 2]:
        lote = session.query(IngredientLot).filter_by(
            restaurant_id=casa.restaurant_id, serial=linea.serial).first()
        inventory.record(session, usuario, cuenta, linea.serial,
                         round(lote.qty_remaining, 3) if lote else 0.0)
    return Metida("inventario_a_medias", dicho=f"{len(lineas) // 2} de {len(lineas)} contadas",
                  extra={"cuenta": cuenta.id, "sin_contar": len(lineas) - len(lineas) // 2})


def _ve_inventario_a_medias(session, casa, metida, hoy):
    from thegrill.models import MeatCount
    from thegrill.web import inventory

    cuenta = session.get(MeatCount, metida.extra["cuenta"])
    if cuenta is None or cuenta.status == CountStatus.CLOSED:
        return Visto(False, "la hoja ya no está")
    try:
        resultado = inventory.close_count(session, _manager(session, casa), cuenta)
    except Exception as e:                                   # noqa: BLE001
        return Visto(True, f"no deja cerrarlo a medias: {str(e)[:80]}")
    for a in resultado.alerts:
        if "partial" in a.code or "unknown" in a.code:
            return Visto(True, f"al cerrar · aviso {a.code}")
    return Visto(False, "cerró un inventario a medias sin decir nada")


# --- 7. los dos que no se ponen de acuerdo ------------------------------------
def _mete_doble_conteo(session, casa, hoy):
    """Dos personas cuentan la misma pieza y no les da lo mismo."""
    from thegrill.web import inventory

    if inventory.open_now(session, casa.restaurant_id):
        return None
    usuario, otro = _manager(session, casa), _carnicero(session, casa)
    try:
        cuenta = inventory.open_count(session, usuario, on=hoy)
    except Exception:                                        # noqa: BLE001
        return None
    if not cuenta.lines:
        return None
    serial = cuenta.lines[0].serial
    inventory.record(session, usuario, cuenta, serial, 5.0)
    inventory.record(session, otro, cuenta, serial, 3.2)
    return Metida("doble_conteo", serial, dicho="5,0 y 3,2 kg",
                  extra={"cuenta": cuenta.id})


def _ve_doble_conteo(session, casa, metida, hoy):
    from thegrill.models import MeatCount
    from thegrill.web import inventory

    cuenta = session.get(MeatCount, metida.extra["cuenta"])
    linea = next((l for l in cuenta.lines if l.serial == metida.serial), None)
    if linea is not None and getattr(linea, "disputed", False):
        try:
            resultado = inventory.close_count(session, _manager(session, casa), cuenta)
            for a in resultado.alerts:
                if "disputed" in a.code:
                    return Visto(True, f"la línea sale marcada y avisa: {a.code}")
        except Exception:                                    # noqa: BLE001
            pass
        return Visto(True, "la línea queda marcada en discusión con los dos números")
    return Visto(False, "se guardó el último y nadie sabe que hubo dos")


# --- 8. la temperatura que nadie mira ----------------------------------------
def _mete_temperatura_alta(session, casa, hoy):
    """Llega a ocho grados en refrigerado y se apunta tal cual. HACCP."""
    from thegrill.meat import service as meat

    lote = meat.next_lot(session, casa.restaurant_id, on=hoy)
    serial = meat.next_serials(session, casa.restaurant_id, 1)[0]
    pieza = _pieza_en_camara(session, casa.restaurant_id)
    try:
        meat.receive_primals(session, _carnicero(session, casa), lote,
                             [meat.PrimalRow(serial=serial, kg=9.0,
                                             sku=pieza.sku if pieza else "Striploin AUS",
                                             price_kg=30.0, arrival=Storage.CHILLED,
                                             arrival_c=8.4,
                                             use_by=hoy + timedelta(days=40))],
                             received=hoy)
    except Exception as e:                                   # noqa: BLE001
        return Metida("temperatura_alta", serial, rechazada=True, porque=str(e)[:120])
    return Metida("temperatura_alta", serial, dicho="8,4 °C en refrigerado",
                  verdad="por encima de 4 °C no se acepta la descarga")


def _ve_temperatura_alta(session, casa, metida, hoy):
    for a in _avisos(session, casa.restaurant_id):
        if metida.serial in (a.message or "") or "temp" in a.code:
            return Visto(True, f"aviso {a.code}")
    portada = _portada(session, casa.restaurant_id, hoy)
    return _en_lo_pendiente(portada, metida.serial)


# --- 9. el plato que no está atado al POS -------------------------------------
def _mete_plato_suelto(session, casa, hoy):
    """Da de alta un plato y no lo ata a su artículo del POS.

    Atar un plato es que exista un artículo del POS apuntando a él. Aquí se
    quita ese apunte: el plato se sigue vendiendo, pero lo que salga de él no
    descuenta de la cámara y el stock se va separando de la realidad sin que
    nadie vea por qué.
    """
    from thegrill.models import PosProduct

    atado = (session.query(PosProduct)
             .filter_by(restaurant_id=casa.restaurant_id)
             .filter(PosProduct.recipe_id.isnot(None)).first())
    if atado is None:
        return None
    receta = session.get(Recipe, atado.recipe_id)
    if receta is None:
        return None
    # Un plato sin atar no es un apunte vacío: es que el apunte no existe.
    session.delete(atado)
    session.flush()
    return Metida("plato_suelto", receta.name, dicho="el POS ya no apunta a ese plato",
                  extra={"receta": receta.id})


def _ve_plato_suelto(session, casa, metida, hoy):
    from thegrill.meat import service as meat

    for a in _avisos(session, casa.restaurant_id, "pos.unmapped"):
        return Visto(True, f"aviso {a.code}")
    for fila in meat.menu(session, casa.restaurant_id):
        if fila.dish.name == metida.serial:
            return (Visto(False, "la carta lo enseña igual que uno atado")
                    if fila.paired else Visto(True, "la carta dice que no está atado al POS"))
    return Visto(False)


# --- 10. la pieza que se manda y nadie recibe ---------------------------------
def _mete_traslado_perdido(session, casa, hoy):
    """Sale del obrador hacia el local y allí nadie la toca en una semana."""
    from thegrill.web import sites

    if not casa.outlets:
        return None
    pieza = _pieza_en_camara(session, casa.restaurant_id)
    if pieza is None:
        return None
    try:
        sites.send_primal(session, _manager(session, casa), pieza.serial,
                          casa.outlets[0], on=hoy - timedelta(days=7))
    except Exception as e:                                   # noqa: BLE001
        return Metida("traslado_perdido", pieza.serial, rechazada=True, porque=str(e)[:120])
    return Metida("traslado_perdido", pieza.serial, dicho="mandada hace siete días")


def _ve_traslado_perdido(session, casa, metida, hoy):
    pieza = (session.query(Primal)
             .filter_by(restaurant_id=casa.restaurant_id, serial=metida.serial).first())
    if pieza is None:
        return Visto(False, "la pieza se perdió del todo")
    portada = _portada(session, casa.restaurant_id, hoy)
    visto = _en_lo_pendiente(portada, metida.serial)
    if visto.visto:
        return visto
    return Visto(False, "está en el local y nadie dice que lleve una semana parada")


# --- 11. el que cierra el turno sin contar -----------------------------------
def _mete_turno_sin_recuento(session, casa, hoy):
    """Saca carne a descongelar y cierra el turno sin contar lo que sobró."""
    from thegrill.web import defrost

    lote = _lote_con_carne(session, casa.restaurant_id)
    if lote is None or not lote.serial:
        return None
    try:
        defrost.intake(session, _carnicero(session, casa), lote.serial, 4, 1.6, on=hoy)
    except Exception as e:                                   # noqa: BLE001
        return Metida("turno_sin_recuento", lote.serial, rechazada=True, porque=str(e)[:120])
    return Metida("turno_sin_recuento", lote.serial, dicho="salieron 4 piezas y no se contó nada")


def _ve_turno_sin_recuento(session, casa, metida, hoy):
    portada = _portada(session, casa.restaurant_id, hoy)
    if portada.thawing:
        visto = _en_lo_pendiente(portada, metida.serial)
        return visto if visto.visto else Visto(True, f"portada · {portada.thawing} descongelando sin recuento")
    from thegrill.web import defrost
    try:
        resultado = defrost.close(session, _manager(session, casa), on=hoy)
        for a in resultado.alerts:
            if "missing" in a.code or "not_by_count" in a.code:
                return Visto(True, f"al cerrar turno · aviso {a.code}")
    except Exception:                                        # noqa: BLE001
        pass
    return Visto(False)


# --- 12. lo caducado que sigue en la cámara ----------------------------------
def _mete_caducado_olvidado(session, casa, hoy):
    """Un lote se pasó de fecha hace cinco días y ahí sigue."""
    lote = _lote_con_carne(session, casa.restaurant_id)
    if lote is None:
        return None
    antes = lote.expiry
    lote.expiry = hoy - timedelta(days=5)
    session.flush()
    return Metida("caducado_olvidado", lote.serial or f"lote {lote.id}",
                  dicho=f"caducó el {lote.expiry}", extra={"antes": str(antes)})


def _ve_caducado_olvidado(session, casa, metida, hoy):
    """La cámara ordena por días para caducar y lo pasado sale en rojo."""
    from thegrill.web import butchery

    estado = butchery.status(session, casa.restaurant_id, on=hoy)
    for fila in estado.cuts:
        if fila.days_to_expiry is not None and fila.days_to_expiry <= 0:
            suyas = " ".join(fila.labels or []) + " " + (fila.name or "")
            if metida.serial in suyas:
                return Visto(True, f"cámara · {fila.days_to_expiry} días para caducar, en rojo")
    pasados = [c for c in estado.cuts
               if c.days_to_expiry is not None and c.days_to_expiry <= 0]
    if pasados:
        return Visto(True, f"cámara · {len(pasados)} líneas pasadas de fecha, en rojo")
    for a in _avisos(session, casa.restaurant_id):
        if "expir" in a.code:
            return Visto(True, f"aviso {a.code}")
    return Visto(False, "caducado y la cámara no lo destaca")


# ------------------------------------------------------------------ el catálogo
CATALOGO = [
    Equivocacion("peso_de_mas", "torpeza",
                 "Teclea 84,0 kg donde ponía 8,40: se le fue la coma",
                 _mete_peso_de_mas, _ve_peso_de_mas, critica=True),
    Equivocacion("serial_repetido", "torpeza",
                 "Escribe el número de otra pieza: dos bolsas con el mismo papel",
                 _mete_serial_repetido, _ve_serial_repetido, critica=True),
    Equivocacion("precio_olvidado", "dejadez",
                 "Descarga el camión y no pone el precio en diez días",
                 _mete_precio_olvidado, _ve_precio_olvidado, critica=True),
    Equivocacion("maduracion_sin_pesar", "dejadez",
                 "Deja de pesar lo que madura durante una semana",
                 _mete_maduracion_sin_pesar, _ve_maduracion_sin_pesar),
    Equivocacion("merma_no_apuntada", "dejadez",
                 "Se echa a perder una caja y nadie apunta la merma",
                 _mete_merma_no_apuntada, _ve_merma_no_apuntada, critica=True),
    Equivocacion("inventario_a_medias", "dejadez",
                 "Cuenta media cámara, se aburre y cierra el inventario",
                 _mete_inventario_a_medias, _ve_inventario_a_medias, critica=True),
    Equivocacion("doble_conteo", "torpeza",
                 "Dos personas cuentan la misma pieza y no les da lo mismo",
                 _mete_doble_conteo, _ve_doble_conteo),
    Equivocacion("temperatura_alta", "torpeza",
                 "Apunta 8,4 °C en una descarga refrigerada y la acepta igual",
                 _mete_temperatura_alta, _ve_temperatura_alta, critica=True),
    Equivocacion("plato_suelto", "dejadez",
                 "Da de alta un plato y no lo ata a su artículo del POS",
                 _mete_plato_suelto, _ve_plato_suelto, critica=True),
    Equivocacion("traslado_perdido", "dejadez",
                 "Manda una pieza al local y allí nadie la toca en una semana",
                 _mete_traslado_perdido, _ve_traslado_perdido),
    Equivocacion("turno_sin_recuento", "dejadez",
                 "Saca carne a descongelar y cierra el turno sin contar",
                 _mete_turno_sin_recuento, _ve_turno_sin_recuento),
    Equivocacion("caducado_olvidado", "dejadez",
                 "Un lote se pasó de fecha hace cinco días y ahí sigue",
                 _mete_caducado_olvidado, _ve_caducado_olvidado, critica=True),
]
