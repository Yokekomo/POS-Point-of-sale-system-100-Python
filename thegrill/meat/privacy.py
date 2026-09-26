"""[00529] Los datos personales: cuánto se guarda, cuánto tiempo y quién los toca.

Una solicitud de acceso trae nombre, correo, teléfono, dirección y número
fiscal de una persona identificable. En la Unión Europea eso no es un formulario
cualquiera, así que aquí están las cuatro reglas que lo gobiernan:

- **Se guarda lo que hace falta y nada más.** Ni tarjetas, ni datos que no se
  usen para dar de alta y facturar.
- **No se guarda para siempre.** Una solicitud que no llegó a cuenta se borra
  sola pasado el plazo, y el plazo está escrito y se puede ejecutar a mano.
- **Se puede borrar y se puede entregar.** Derecho de supresión y de
  portabilidad, cada uno con su botón y su registro.
- **Queda escrito quién los mira.** Abrir la bandeja de solicitudes deja huella,
  igual que borrarlas.

Sobre el cifrado en reposo: la base de datos debe ir cifrada a nivel de disco o
de motor, que es donde tiene sentido. Además, si está instalada la biblioteca
`cryptography` y hay clave, los campos de contacto se guardan cifrados con
Fernet. Sin clave se guardan en claro y la consola lo dice en rojo: es mejor
saberlo que creerse protegido.
"""
import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from thegrill.models import AccessRequest, AuditLog, RequestStatus, User

log = logging.getLogger(__name__)

# [00544] Cuánto se guarda una solicitud que no llegó a ser cuenta.
RETENTION_DAYS = 180
MARK = "enc:v1:"          # lo que lleva delante un valor cifrado

# Lo que se le pone de correo a quien ya no está. Ni es de nadie ni sirve para
# entrar, y sigue siendo distinto del de los demás, que la tabla lo exige.
BORRADO = "borrado-"
BORRADO_DOMINIO = "invalid"      # RFC 2606: reservado, no existe ni puede existir


def _key() -> bytes | None:
    """[00530] La clave de cifrado, derivada de `GRILL_DATA_KEY`. Sin ella, no hay cifrado."""
    secret = os.environ.get("GRILL_DATA_KEY", "").strip()
    if not secret:
        return None
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), b"thegrill/privacy", 200_000)
    return base64.urlsafe_b64encode(digest)


def cipher():
    """[00531] El cifrador, si se puede. None si falta la clave o la biblioteca."""
    key = _key()
    if key is None:
        return None
    try:
        from cryptography.fernet import Fernet
    except BaseException:        # una biblioteca nativa rota no lanza Exception
        log.warning("hay clave de cifrado pero la biblioteca cryptography no carga")
        return None
    return Fernet(key)


def encryption_on() -> bool:
    """[00532] Si los datos personales se están guardando cifrados."""
    return cipher() is not None


def protect(value: str | None) -> str | None:
    """[00533] Cifra un dato de contacto, si se puede. Si no, lo deja como está."""
    if not value:
        return value
    box = cipher()
    if box is None:
        return value
    return MARK + box.encrypt(value.encode()).decode()


def reveal(value: str | None) -> str | None:
    """[00534] Descifra lo que esté cifrado. Lo demás se devuelve tal cual."""
    if not value or not value.startswith(MARK):
        return value
    box = cipher()
    if box is None:
        return "—"           # sin clave no se inventa el dato
    try:
        return box.decrypt(value[len(MARK):].encode()).decode()
    except Exception:
        log.exception("no se pudo descifrar un dato de contacto")
        return "—"


PERSONAL = ("contact_name", "email", "phone", "address", "tax_number", "legal_name")


def protect_request(row: AccessRequest) -> AccessRequest:
    """[00535] Cifra los datos personales de una solicitud antes de guardarla.

    Nombre, correo y teléfono de alguien que pide acceso: en la base quedan
    cifrados, y quien se lleve el fichero no se lleva la lista de contactos.
    """
    for field in PERSONAL:
        setattr(row, field, protect(getattr(row, field)))
    return row


@dataclass
class Readable:
    """[00536] Una solicitud lista para leer, con sus datos ya en claro."""
    row: AccessRequest

    def __getattr__(self, name):
        """[00543] Devuelve el campo, descifrado si era de los personales."""
        value = getattr(self.row, name)
        return reveal(value) if name in PERSONAL else value


def readable(rows: list[AccessRequest]) -> list[Readable]:
    """[00537] Envuelve las solicitudes para poder leerlas en claro en la pantalla."""
    return [Readable(row) for row in rows]


# ------------------------------------------------------------------ registro
def audit(session: Session, actor: User, key: str, action: str, detail: str = "") -> None:
    """[00538] Deja escrito quién miró o tocó datos personales, y cuándo.

    Lo pide la ley y lo pide el sentido común: los datos de quien pide acceso
    los ve alguien con nombre y apellidos, no «el sistema».
    """
    session.add(AuditLog(restaurant_id=actor.restaurant_id,
                         actor=f"{actor.name} <{actor.email}>",
                         table="personal_data", key=key, action=action, detail=detail))
    session.flush()


def note_access(session: Session, actor: User, count: int) -> None:
    """[00539] Mirar la bandeja de solicitudes deja huella. Quién y cuántas."""
    if count:
        audit(session, actor, "requests", "READ", f"{count} solicitudes")


# --------------------------------------------- la persona que deja la casa
def olvidar_a(session: Session, actor: User, quien: User) -> None:
    """[01870] Un cocinero se va: se le borra lo suyo y se queda lo de la carne.

    Aquí chocan dos leyes y hay que saber por dónde pasa la raya, porque
    ninguna de las dos se puede incumplir.

    Por un lado, el Reglamento (UE) 931/2011 y el (CE) 852/2004 obligan al
    restaurante a poder decir **quién** hizo cada cosa con cada pieza: quién la
    pesó, quién la despiezó, quién firmó el parte. Eso está escrito en el libro
    de firmas y se queda, porque conservarlo es una obligación legal del
    restaurante —el artículo 17.3.b del RGPD dice exactamente eso: el derecho
    de supresión no alcanza a lo que hay que conservar por ley—.

    Por otro, en cuanto esa persona deja la casa, su correo, su contraseña, su
    segundo factor y sus códigos de repuesto no hacen falta para nada. Y se
    quedaban ahí para siempre: en la base, en las treinta copias, y en el
    fichero que se descarga el cliente. Un cocinero que estuvo tres meses hace
    dos años seguía teniendo su correo y su secreto de dos pasos guardados en
    un restaurante en el que ya no trabaja.

    Así que se borra lo que sobra y se queda lo que la ley manda guardar. El
    nombre se queda —es parte del registro, no un dato de contacto—, el trabajo
    se queda entero, y queda escrito que esto se hizo, quién lo hizo y cuándo,
    que es lo que hay que poder enseñar si lo preguntan.
    """
    from thegrill.models import AuthSession

    if quien.restaurant_id != actor.restaurant_id:
        raise PermissionError("esa persona no es de esta casa")
    if quien.id == actor.id:
        raise ValueError("a uno mismo no: lo haría quien entrara con tu cuenta")

    audit(session, actor, f"user:{quien.id}", "FORGOTTEN", quien.name or "")
    # Un correo que no es de nadie y que nadie puede usar para entrar. Tiene
    # que seguir siendo único dentro de la casa, que la tabla lo exige.
    quien.email = f"{BORRADO}{quien.id}@{BORRADO_DOMINIO}"
    # Una contraseña que no es el resumen de ninguna: no hay nada que teclear
    # que case con esto, así que la cuenta no se abre ni por casualidad.
    quien.password_hash = "-"
    quien.totp_secret = None
    quien.totp_enabled = False
    quien.recovery_codes = None
    quien.last_login = None
    quien.active = False
    session.query(AuthSession).filter(AuthSession.user_id == quien.id).delete(
        synchronize_session=False)
    session.flush()


def olvidada(quien: User) -> bool:
    """[01871] Si de esa persona ya solo queda el nombre y su trabajo."""
    return (quien.email or "").startswith(BORRADO)


# ------------------------------------------------------ supresión y entrega
def erase_request(session: Session, actor: User, request_id: int) -> bool:
    """[00540] Derecho de supresión: la solicitud se borra, y queda que se borró."""
    row = session.get(AccessRequest, request_id)
    if row is None:
        return False
    audit(session, actor, f"request:{request_id}", "ERASED", row.restaurant_name)
    session.delete(row)
    session.flush()
    return True


def export_request(row: AccessRequest) -> dict:
    """[00541] Derecho de portabilidad: todo lo que guardamos de esa persona, en claro."""
    return {
        "recibida": row.created_at.isoformat(),
        "estado": row.status.value,
        "restaurante": row.restaurant_name,
        "nombre_fiscal": reveal(row.legal_name),
        "numero_fiscal": reveal(row.tax_number),
        "pais": row.country,
        "direccion": reveal(row.address),
        "persona": reveal(row.contact_name),
        "cargo": row.contact_role,
        "correo": reveal(row.email),
        "telefono": reveal(row.phone),
        "cocineros": row.cooks,
        "locales": row.outlets,
        "plan": row.plan.value if row.plan else None,
        "mensaje": row.message,
    }


def purge(session: Session, actor: User | None = None, days: int = RETENTION_DAYS,
          on: date | None = None) -> int:
    """[00542] Borra las solicitudes viejas que no llegaron a cuenta.

    Las aceptadas no se tocan aquí: esas ya son una casa dada de alta y sus
    datos viven en la cuenta, con su propio plazo.
    """
    limite = datetime.combine((on or date.today()) - timedelta(days=days),
                              datetime.min.time())
    viejas = (session.query(AccessRequest)
              .filter(AccessRequest.created_at < limite,
                      AccessRequest.status != RequestStatus.ACCEPTED).all())
    for row in viejas:
        session.delete(row)
    session.flush()
    if viejas and actor is not None:
        audit(session, actor, "requests", "PURGED", f"{len(viejas)} de más de {days} días")
    return len(viejas)
