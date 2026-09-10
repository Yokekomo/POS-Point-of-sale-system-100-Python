from datetime import date, datetime

from thegrill.messaging.guards import AntiDup, Gate, Outbox, Outgoing, SessionMutex, Blocked, in_send_window

D = date(2026, 9, 10)


def msg(recipient="Ahmed", topic="meat", template="parte", chat=None, early=False):
    return Outgoing(chat or recipient, recipient, topic, template, "body", D, early_order=early)


def test_send_window():
    assert in_send_window(datetime(2026, 9, 10, 9, 0)) and not in_send_window(datetime(2026, 9, 10, 20, 0))


def test_suppressions_drop_not_queue():
    ob, gate = Outbox(), Gate(AntiDup(), now_fn=lambda: datetime(2026, 9, 10, 12, 0))
    assert ob.dispatch(msg("Fadi", "meat"), gate, lambda m: None) == "SUPPRESSED:Fadi:meat"
    assert ob.dispatch(msg("Fadi", "haccp"), gate, lambda m: None).startswith("SUPPRESSED")
    assert ob.dispatch(msg("Olivier", "meat"), gate, lambda m: None).startswith("SUPPRESSED")
    assert ob.dispatch(msg("Olivier", "orders"), gate, lambda m: None) == "SENT"
    assert len(ob.dropped) == 3 and ob.queued == []


def test_outside_window_queues_then_flushes_once():
    sent = []
    clock = {"now": datetime(2026, 9, 10, 0, 30)}
    ob, gate = Outbox(), Gate(AntiDup(), now_fn=lambda: clock["now"])
    assert ob.dispatch(msg(), gate, sent.append) == "OUTSIDE_SEND_WINDOW"
    assert ob.dispatch(msg("Albano", "brief", "brief"), gate, sent.append) == "SENT"     # self-chat sin ventana
    assert ob.dispatch(msg("Ram", "roster", "clockout"), gate, sent.append, step_is_night_clockout=True) == "SENT"
    assert ob.dispatch(msg("BOH", "orders", "OR-0001", early=True), gate, sent.append) == "SENT"
    clock["now"] = datetime(2026, 9, 10, 9, 5)
    assert ob.flush(gate, sent.append) == 1
    assert ob.dispatch(msg(), gate, sent.append) == "DUPLICATE_TODAY"
    assert len(sent) == 4


def test_mutex_single_session(tmp_path):
    lock = str(tmp_path / "wa.lock")
    with SessionMutex(lock):
        try:
            with SessionMutex(lock):
                assert False, "second session must be blocked"
        except Blocked as b:
            assert b.queue and b.reason == "WA_SESSION_BUSY"
    assert SessionMutex(lock).acquire()
