"""El correo que sale de la web pública.

Una solicitud de acceso se guarda siempre, salga el correo o no: una solicitud
perdida es un cliente que no vuelve. El correo es el aviso, no el registro.

Se configura con variables de entorno y, si no están, no se envía nada y no
pasa nada: la solicitud queda en la bandeja del dueño.

    GRILL_SMTP_HOST     servidor de salida
    GRILL_SMTP_PORT     puerto (587 por defecto, STARTTLS)
    GRILL_SMTP_USER     usuario
    GRILL_SMTP_PASSWORD contraseña
    GRILL_MAIL_FROM     remitente
    GRILL_MAIL_TO       a quién llegan las solicitudes

Los datos de una solicitud son personales y fiscales: van a esa dirección y a
ninguna otra.
"""
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def settings() -> dict[str, str]:
    return {
        "host": os.environ.get("GRILL_SMTP_HOST", ""),
        "port": os.environ.get("GRILL_SMTP_PORT", "587"),
        "user": os.environ.get("GRILL_SMTP_USER", ""),
        "password": os.environ.get("GRILL_SMTP_PASSWORD", ""),
        "sender": os.environ.get("GRILL_MAIL_FROM", ""),
        "to": os.environ.get("GRILL_MAIL_TO", ""),
    }


def configured() -> bool:
    cfg = settings()
    return bool(cfg["host"] and cfg["sender"] and cfg["to"])


def send(subject: str, body: str, reply_to: str | None = None) -> bool:
    """Manda el aviso. Devuelve si salió; nunca revienta la petición web."""
    if not configured():
        log.info("correo sin configurar; la solicitud queda solo en la bandeja")
        return False
    cfg = settings()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg["sender"]
    message["To"] = cfg["to"]
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)
    try:
        with smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=10) as smtp:
            smtp.starttls()
            if cfg["user"]:
                smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(message)
        return True
    except Exception:                      # el correo no puede tumbar una alta
        log.exception("no se pudo enviar el aviso de solicitud")
        return False
