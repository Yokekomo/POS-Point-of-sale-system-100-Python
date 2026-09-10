from datetime import date, datetime

from thegrill.engine.roster import ClockEvent, pair_shifts


def test_midnight_offset_no_false_alarm():
    ev = [ClockEvent("Ram", datetime(2026, 9, 9, 16, 0), "IN"),
          ClockEvent("Ram", datetime(2026, 9, 10, 0, 10), "OUT")]
    s = pair_shifts(ev)
    assert len(s) == 1 and s[0].day == date(2026, 9, 9) and s[0].hours == 8.17
    assert s[0].flag(datetime(2026, 9, 10, 0, 7)) is None


def test_missing_out_after_grace():
    s = pair_shifts([ClockEvent("Ram", datetime(2026, 9, 9, 16, 0), "IN")])[0]
    assert s.flag(datetime(2026, 9, 10, 0, 7)) is None          # aún dentro de gracia
    assert s.flag(datetime(2026, 9, 10, 3, 1)) == "MISSING_OUT"


def test_day_off_never_flags():
    s = pair_shifts([], days_off=set())
    assert s == []
    s = pair_shifts([ClockEvent("Ali", datetime(2026, 9, 9, 10, 0), "OUT")], days_off={("Ali", date(2026, 9, 9))})[0]
    assert s.flag(datetime(2026, 9, 10, 9, 0)) is None
