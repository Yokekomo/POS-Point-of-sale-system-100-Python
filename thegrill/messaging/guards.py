"""Guardas de mensajería (§8 reglas 7-10): ventana horaria, supresiones,
anti-duplicado y mutex de sesión WhatsApp.

Ningún envío sale sin pasar por `Gate.check()`. Si no puede salir, se ENCOLA;
nunca se pierde ni se duplica.
"""
import os
from dataclasses import dataclass
from datetime import date, datetime, time

from thegrill import config


class Blocked(Exception):
    """El envío no puede salir ahora; el motivo dice si se encola o se descarta."""

    def __init__(self, reason: str, queue: bool):
        super().__init__(reason)
        self.reason = reason
        self.queue = queue


@dataclass(frozen=True)
class Outgoing:
    chat: str
    recipient: str
    topic: str            # meat / haccp / orders / brief / general
    template: str         # identificador del mensaje (anti-dup por día)
    body: str
    day: date
    early_order: bool = False    # pedidos "antes de 08:00"


def in_send_window(now: datetime, start: time = config.SEND_WINDOW_START,
                   end: time = config.SEND_WINDOW_END) -> bool:
    return start <= now.time() < end


def is_suppressed(recipient: str, topic: str) -> bool:
    return topic in config.SUPPRESSIONS.get(recipient, set())


class AntiDup:
    """Registro de (chat, template, día). En producción se respalda en `sent_messages`
    y ADEMÁS se verifica en pantalla antes de enviar (regla 10)."""

    def __init__(self, already_sent: set[tuple[str, str, date]] | None = None):
        self._sent = set(already_sent or set())

    def seen(self, msg: Outgoing) -> bool:
        return (msg.chat, msg.template, msg.day) in self._sent

    def record(self, msg: Outgoing) -> None:
        self._sent.add((msg.chat, msg.template, msg.day))


class SessionMutex:
    """Una sola sesión WhatsApp a la vez. Lock por archivo con PID; si está tomado, encolar."""

    def __init__(self, lock_path: str = ".wa_session.lock"):
        self.lock_path = lock_path
        self._held = False

    def acquire(self) -> bool:
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        self._held = True
        return True

    def release(self) -> None:
        if self._held and os.path.exists(self.lock_path):
            os.remove(self.lock_path)
        self._held = False

    def __enter__(self):
        if not self.acquire():
            raise Blocked("WA_SESSION_BUSY", queue=True)
        return self

    def __exit__(self, *exc):
        self.release()


class Gate:
    def __init__(self, antidup: AntiDup, now_fn=datetime.now):
        self.antidup = antidup
        self.now_fn = now_fn

    def check(self, msg: Outgoing) -> None:
        """Lanza Blocked si el mensaje no puede salir. Orden: supresión > dup > ventana."""
        if is_suppressed(msg.recipient, msg.topic):
            raise Blocked(f"SUPPRESSED:{msg.recipient}:{msg.topic}", queue=False)
        if self.antidup.seen(msg):
            raise Blocked("DUPLICATE_TODAY", queue=False)
        now = self.now_fn()
        exempt = (msg.recipient == config.SELF_CHAT
                  or (msg.early_order and now.time() < config.ORDERS_EARLY_CUTOFF))
        if not exempt and not in_send_window(now):
            raise Blocked("OUTSIDE_SEND_WINDOW", queue=True)


class Outbox:
    """Cola de mensajes que no pudieron salir (fuera de ventana / mutex ocupado)."""

    def __init__(self):
        self.queued: list[tuple[Outgoing, str]] = []
        self.dropped: list[tuple[Outgoing, str]] = []
        self.sent: list[Outgoing] = []

    def dispatch(self, msg: Outgoing, gate: Gate, send_fn) -> str:
        try:
            gate.check(msg)
        except Blocked as b:
            (self.queued if b.queue else self.dropped).append((msg, b.reason))
            return b.reason
        send_fn(msg)
        gate.antidup.record(msg)
        self.sent.append(msg)
        return "SENT"

    def flush(self, gate: Gate, send_fn) -> int:
        pending, self.queued = self.queued, []
        n = 0
        for msg, _ in pending:
            if self.dispatch(msg, gate, send_fn) == "SENT":
                n += 1
        return n
