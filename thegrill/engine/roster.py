"""Fichaje (§7.5): empareja entradas/salidas, calcula horas y evita falsas alarmas
por el desfase de medianoche."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

MIDNIGHT_GRACE = timedelta(hours=3)   # una salida hasta las 03:00 pertenece al día anterior


@dataclass
class ClockEvent:
    person: str
    ts: datetime
    kind: str          # IN / OUT


@dataclass
class Shift:
    person: str
    day: date
    clock_in: datetime | None
    clock_out: datetime | None
    day_off: bool = False

    @property
    def hours(self) -> float | None:
        if self.clock_in and self.clock_out:
            return round((self.clock_out - self.clock_in).total_seconds() / 3600, 2)
        return None

    def flag(self, now: datetime) -> str | None:
        if self.day_off:
            return None
        if self.clock_in is None and self.clock_out is not None:
            return "MISSING_IN"
        if self.clock_in is not None and self.clock_out is None:
            # Desfase de medianoche: no alarmar si aún está dentro del margen de gracia
            end_of_grace = datetime.combine(self.day + timedelta(days=1), datetime.min.time()) + MIDNIGHT_GRACE
            return None if now < end_of_grace else "MISSING_OUT"
        if self.clock_in is None and self.clock_out is None:
            return "NO_CLOCKING"
        return None


def business_day(ts: datetime) -> date:
    """Un fichaje entre 00:00 y la gracia se atribuye al día anterior."""
    if ts.time() < (datetime.min + MIDNIGHT_GRACE).time():
        return (ts - timedelta(days=1)).date()
    return ts.date()


def pair_shifts(events: list[ClockEvent], days_off: set[tuple[str, date]] | None = None) -> list[Shift]:
    days_off = days_off or set()
    by_key: dict[tuple[str, date], Shift] = {}
    for ev in sorted(events, key=lambda e: e.ts):
        day = business_day(ev.ts) if ev.kind == "OUT" else ev.ts.date()
        key = (ev.person, day)
        shift = by_key.setdefault(key, Shift(ev.person, day, None, None, key in days_off))
        if ev.kind == "IN" and shift.clock_in is None:
            shift.clock_in = ev.ts
        elif ev.kind == "OUT":
            shift.clock_out = ev.ts
    return sorted(by_key.values(), key=lambda s: (s.day, s.person))
