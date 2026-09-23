"""Cuándo sale el tutorial de una pantalla y cómo se apunta que ya se vio.

La marca vive en el servidor y por persona: en una cocina el móvil y la
tablet se comparten, y si viviera en el aparato el primero que entrara se
llevaría el tutorial de todos.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from thegrill.meat import tours
from thegrill.models import TourVisto, User
from thegrill.web.i18n import t


@dataclass
class Guia:
    """El tutorial de una pantalla, listo para enseñarse."""
    pantalla: str
    version: int
    pasos: list


def visto(session: Session, user: User, pantalla: str, version: int) -> bool:
    """Si esa persona ya vio ese tutorial en esa versión o más nueva."""
    row = (session.query(TourVisto)
           .filter_by(user_id=user.id, pantalla=pantalla).first())
    return row is not None and (row.version or 0) >= version


def para(session: Session, user: User | None, ruta: str, lang: str,
         forzar: bool = False) -> Guia | None:
    """El tutorial que toca aquí y ahora, o nada.

    Nada si esa pantalla no tiene, si esa persona ya lo vio o si, después de
    quitar los pasos que no le tocan por su nivel, no queda ninguno: un
    tutorial de cero pasos no es un tutorial.
    """
    if user is None:
        return None
    tour = tours.de_ruta(ruta)
    if tour is None:
        return None
    if not forzar and visto(session, user, tour.pantalla, tour.version):
        return None
    # El filtro por nivel se hace aquí, y lo que se quita no sale de esta
    # función: ni al HTML ni al JSON. Quien no ve dinero no recibe el paso que
    # habla de dinero, aunque mire el código fuente de la página.
    # Diccionarios y no objetos: de aquí salen tal cual al JSON de la plantilla.
    pasos = [{"selector": p.selector, "titulo": t(lang, p.titulo), "texto": t(lang, p.texto)}
             for p in tours.pasos_para(tour, user.role)]
    if not pasos:
        return None
    return Guia(pantalla=tour.pantalla, version=tour.version, pasos=pasos)


def marcar(session: Session, user: User, pantalla: str, completo: bool = True) -> TourVisto:
    """Apunta que esta persona ya lo vio. La versión la pone el servidor.

    La manda el navegador, así que el número de versión no se cree: se coge
    del registro. Si no se creyera tampoco el nombre de la pantalla, alguien
    podría marcarse por visto un tutorial que no existe y llenar la tabla.
    """
    tour = tours.TOURS.get(pantalla)
    if tour is None:
        raise KeyError(pantalla)
    row = (session.query(TourVisto)
           .filter_by(user_id=user.id, pantalla=pantalla).first())
    if row is None:
        row = TourVisto(restaurant_id=user.restaurant_id, user_id=user.id,
                        pantalla=pantalla)
        session.add(row)
    row.version = tour.version
    row.completo = bool(completo)
    row.fecha = datetime.utcnow()
    session.flush()
    return row
