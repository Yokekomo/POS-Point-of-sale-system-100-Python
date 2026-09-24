"""Los fallos que cuenta quien está delante de la pantalla.

Ninguna prueba ve lo que ve un carnicero a las siete de la mañana: el número
que no cuadra el martes, la pantalla que se queda en blanco con su teclado, el
aviso que no se entiende. Eso solo lo cuenta quien lo sufre, y solo lo cuenta si
contarlo cuesta treinta segundos y no un correo.

Por eso el parte está en todas las pantallas, lleva puesto de dónde sale —la
pantalla, el papel de quien escribe, su idioma, su sede— y se guarda siempre. El
correo es el aviso, no el registro: sin servidor de correo el parte sigue aquí y
se dice que no salió, como con las solicitudes de alta.

Quien lo lee es la plataforma, en `/admin/fallos`: los marca como vistos,
arreglados o cerrados, y así una casa que cuenta un fallo sabe que ha servido
para algo.
"""
import os
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from thegrill.meat import mailer
from thegrill.models import BugReport, BugStatus, Restaurant, User

KINDS = ("fallo", "numero", "idea")      # se rompe, no cuadra, o se podría hacer mejor
MAX_MESSAGE = 4000


class BugError(ValueError):
    """El parte no se puede guardar tal y como está."""


@dataclass
class Reported:
    report: BugReport
    mailed: bool


def address() -> str:
    """A dónde van los partes. Sin `GRILL_BUGS_EMAIL`, a donde van las solicitudes."""
    return (os.environ.get("GRILL_BUGS_EMAIL", "").strip()
            or os.environ.get("GRILL_MAIL_TO", "").strip())


def report(session: Session, user: User | None, message: str, screen: str = "",
           kind: str = "fallo", email: str = "", lang: str = "es",
           site: str = "", version: str = "") -> Reported:
    """Guarda el parte y lo manda por correo. Lo primero siempre; lo segundo si se puede."""
    texto = (message or "").strip()
    if len(texto) < 10:
        raise BugError("Cuenta un poco más: con dos palabras no se puede reproducir.")
    restaurant = (session.get(Restaurant, user.restaurant_id)
                  if user is not None and user.restaurant_id else None)
    quien = f"{user.name} · {user.role.value}" if user is not None else "sin cuenta"
    detalle = " · ".join(x for x in (
        f"idioma {lang}", f"sede {site}" if site else "",
        f"casa {restaurant.name}" if restaurant is not None else "",
        f"versión {version}" if version else "") if x)

    row = BugReport(
        restaurant_id=restaurant.id if restaurant is not None else None,
        user_id=user.id if user is not None else None, reporter=quien[:128],
        email=(email or (user.email if user is not None else "")).strip()[:160] or None,
        screen=(screen or "")[:160] or None,
        kind=kind if kind in KINDS else "fallo",
        message=texto[:MAX_MESSAGE], detail=detalle or None,
        status=BugStatus.NEW, created_at=datetime.utcnow())
    session.add(row)
    session.flush()

    salio = False
    if address():
        cuerpo = (f"{row.kind.upper()} · parte #{row.id}\n"
                  f"{quien}\n{detalle}\n"
                  f"Pantalla: {row.screen or '—'}\n"
                  f"Contestar a: {row.email or '—'}\n\n{row.message}\n")
        salio = mailer.send(f"[Control de carnes] fallo #{row.id}: {texto[:60]}",
                            cuerpo, reply_to=row.email)
    row.mailed = bool(salio)
    session.flush()
    return Reported(report=row, mailed=bool(salio))


def recent(session: Session, limit: int = 100,
           status: BugStatus | None = None) -> list[BugReport]:
    """Los últimos partes de fallo, del más nuevo al más viejo."""
    query = session.query(BugReport)
    if status is not None:
        query = query.filter(BugReport.status == status)
    return query.order_by(BugReport.created_at.desc(), BugReport.id.desc()).limit(limit).all()


def mine(session: Session, restaurant_id: int, limit: int = 20) -> list[BugReport]:
    """Los que ha contado esa casa, para que vea en qué han quedado."""
    return (session.query(BugReport).filter_by(restaurant_id=restaurant_id)
            .order_by(BugReport.created_at.desc()).limit(limit).all())


def set_status(session: Session, report_id: int, status: BugStatus,
               note: str | None = None) -> BugReport:
    """Cambia el estado de un parte de fallo y le deja una nota."""
    row = session.get(BugReport, report_id)
    if row is None:
        raise BugError("Ese parte no existe")
    row.status = status
    if note is not None:
        row.note = note.strip() or None
    session.flush()
    return row


def counts(session: Session) -> dict[str, int]:
    """Cuántos hay de cada estado, para la pantalla de la plataforma."""
    out = {s.value: 0 for s in BugStatus}
    for row in session.query(BugReport):
        out[row.status.value] = out.get(row.status.value, 0) + 1
    return out
