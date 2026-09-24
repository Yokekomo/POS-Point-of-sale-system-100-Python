"""[00509] Lo que acaba de pasar en la casa, para el que está en otra pantalla.

En un servicio, dos personas trabajan la misma carne desde sitios distintos: el
del muelle da de alta seis lomos mientras el de la mesa despieza, y el que está
contando no se entera de ninguna de las dos cosas hasta que va a la cámara y se
lo encuentra. Eso, en FEFO, se paga: se saca la pieza vieja porque nadie sabía
que había entrado una nueva, o se cuenta sin contar lo que entró hace diez
minutos y el inventario sale corto.

Así que cada entrada de carne y cada despiece dejan aquí una fila, y la pantalla
de quien esté trabajando —la que sea— la enseña arriba. Con su X: se lee y se
quita. Un aviso que no se puede quitar acaba tapado con el dedo.

Tres decisiones que importan:

- **Se guarda el hecho, no la frase.** Cuántas piezas, de qué, cuántos kilos, con
  qué lote y quién. La frase se arma al leerla, en el idioma de quien lee: el
  del muelle escribe en español y el jefe de cocina lo lee en francés.
- **Lo tuyo no se te avisa.** Quien acaba de recibir no necesita un cartel
  diciéndole que ha recibido. El aviso es para los demás.
- **Y solo lo de tu sede, y solo lo de hoy.** Lo que entra en el obrador no le
  hace falta al local de la playa, y una novedad de ayer no es una novedad: es
  historia, y la historia está en su pantalla.
"""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from thegrill.models import Novedad, User
from thegrill.web import i18n, sites

RECEPCION = "RECEPCION"
DESPIECE = "DESPIECE"

# [00516] Una jornada larga. Pasado eso deja de ser una novedad: quien entra por la
# mañana no quiere encontrarse los avisos del turno de noche encima del título.
VENTANA = timedelta(hours=12)

# [00517] Cuántas se contestan de golpe. Si han pasado veinte cosas, el aviso no es el
# sitio para verlas: están en la cámara y en la pantalla de cada cosa.
CUANTAS = 6

TEXTOS = {RECEPCION: "new.recepcion", DESPIECE: "new.despiece"}


def anotar(session: Session, user: User, kind: str, *, ref: str | None = None,
           label: str | None = None, pieces: int = 0, kg: float = 0.0,
           site_id: int | None = None) -> Novedad:
    """[00510] Deja escrito que ha pasado algo, para que lo vean los de al lado."""
    if site_id is None:
        mia = sites.of_user(session, user)
        site_id = mia.id if mia else None
    fila = Novedad(restaurant_id=user.restaurant_id, kind=kind,
                   ref=(ref or "").strip()[:24] or None,
                   label=(label or "").strip()[:96] or None,
                   pieces=int(pieces or 0), kg=round(float(kg or 0.0), 4),
                   site_id=site_id, by_user_id=user.id, by_name=(user.name or "")[:128])
    session.add(fila)
    session.flush()
    return fila


def recientes(session: Session, restaurant_id: int, *, desde_id: int = 0,
              site_id: int | None = None, salvo_user: int | None = None,
              ahora: datetime | None = None, limit: int = CUANTAS) -> list[Novedad]:
    """[00511] Las novedades que esa persona todavía no ha visto.

    `desde_id` es hasta dónde había leído: se contesta lo que vino después. Sin
    sede, se ven todas —un encargado que va y viene entre el obrador y el local
    quiere las dos—; con sede, solo las de la suya y las que no tienen ninguna.
    """
    ahora = ahora or datetime.utcnow()
    q = (session.query(Novedad)
         .filter(Novedad.restaurant_id == restaurant_id,
                 Novedad.id > int(desde_id or 0),
                 Novedad.created_at >= ahora - VENTANA))
    if salvo_user:
        q = q.filter((Novedad.by_user_id.is_(None)) | (Novedad.by_user_id != salvo_user))
    if site_id:
        q = q.filter((Novedad.site_id.is_(None)) | (Novedad.site_id == site_id))
    # [00518] Las últimas, pero devueltas en el orden en que pasaron: el aviso de
    # arriba es el más viejo, y se van apilando debajo como se van leyendo.
    ultimas = q.order_by(Novedad.id.desc()).limit(max(1, int(limit))).all()
    return list(reversed(ultimas))


def ultimo_id(session: Session, restaurant_id: int) -> int:
    """[00512] El número de la última novedad. El teléfono que entra por primera vez
    arranca de aquí: lo de antes de llegar no se le enseña."""
    fila = (session.query(Novedad.id)
            .filter(Novedad.restaurant_id == restaurant_id)
            .order_by(Novedad.id.desc()).first())
    return int(fila[0]) if fila else 0


def frase(lang: str, fila: Novedad) -> tuple[str, str]:
    """[00513] La frase, en el idioma de quien la lee, partida en dos.

    Arriba, lo que hay que entender de un vistazo con las manos ocupadas: qué
    ha entrado y cuánto de eso. Debajo, en pequeño, lo que hace falta para ir a
    buscarlo —los kilos, el lote y quién lo metió—. Todo en una línea sola no
    se lee en un móvil: se lee la primera palabra y se pasa de largo.
    """
    clave = TEXTOS.get(fila.kind, TEXTOS[RECEPCION])
    datos = dict(n=fila.pieces or 0, label=(fila.label or "—"),
                 kg=f"{(fila.kg or 0.0):.10g}", ref=(fila.ref or "—"),
                 who=(fila.by_name or "—"))
    return i18n.t(lang, clave, **datos), i18n.t(lang, clave + ".det", **datos)


def texto(lang: str, fila: Novedad) -> str:
    """[00514] Las dos partes seguidas, para donde no quepan dos líneas."""
    titulo, detalle = frase(lang, fila)
    return f"{titulo} · {detalle}"


def olvidar_viejas(session: Session, days: int = 7) -> int:
    """[00515] Una novedad de la semana pasada no avisa de nada: ocupa sitio."""
    limite = datetime.utcnow() - timedelta(days=days)
    return (session.query(Novedad).filter(Novedad.created_at < limite)
            .delete(synchronize_session=False))
