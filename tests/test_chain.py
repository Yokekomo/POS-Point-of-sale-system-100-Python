from datetime import date

from thegrill import db
from thegrill.models import Restaurant, SourceStatus
from thegrill.orchestrator.chain import (Chain, DbCheckpoints, MemoryCheckpoints, StepResult,
                                         TransientError, build_default_chain)

MON, TUE = date(2026, 9, 7), date(2026, 9, 8)


def test_sequential_idempotent_and_weekday_steps():
    calls = []
    ck = MemoryCheckpoints()
    handlers = {
        "01_fichaje": lambda d: (calls.append("01"), StepResult(SourceStatus.DONE))[1],
        "06_ventas": lambda d: (calls.append("06"), StepResult(SourceStatus.NOT_POSTED_YET, "pdf no posteado"))[1],
        "08_inventario_semanal": lambda d: (calls.append("08"), StepResult(SourceStatus.DONE))[1],
        "08b_plan_pan": lambda d: (calls.append("08b"), StepResult(SourceStatus.DONE))[1],
    }
    chain = build_default_chain(ck, handlers)
    r = chain.run(MON)
    assert calls == ["01", "06", "08"] and "08b_plan_pan" in r.skipped
    assert r.not_posted == ["06_ventas"]
    assert "03_despiece" in r.blocked                      # sin handler => BLOCKED, nunca "done"
    calls.clear()
    r2 = chain.run(MON)                                     # re-ejecutar: DONE no se repite, pendientes sí
    assert calls == ["06"]
    assert r2.results["01_fichaje"].detail.startswith("checkpoint")
    calls.clear()
    chain.run(TUE)
    assert "08b" in calls and "08" not in calls


def test_transient_retry_with_backoff_and_content_error_no_retry():
    sleeps, n = [], {"k": 0}
    chain = Chain(MemoryCheckpoints(), sleep_fn=sleeps.append)

    @chain.step("flaky")
    def flaky(d):
        n["k"] += 1
        if n["k"] < 3:
            raise TransientError("529")
        return StepResult(SourceStatus.DONE)

    @chain.step("illegible")
    def illegible(d):
        n["k"] += 100
        raise ValueError("foto ilegible")

    r = chain.run(MON)
    assert r.results["flaky"].state == SourceStatus.DONE and sleeps == [30, 60]
    assert r.results["illegible"].state == SourceStatus.BLOCKED and n["k"] == 103


def test_db_checkpoints_persist(tmp_path):
    db.init_engine(f"sqlite:///{tmp_path/'t.db'}")
    db.create_all()
    with db.session_scope() as s:
        s.add(Restaurant(id=1, name="Demo", slug="demo", join_code="DEMO1234"))
    ck = DbCheckpoints(db.session_scope, restaurant_id=1)
    ck.set(MON, "01_fichaje", SourceStatus.DONE, "ok")
    ck.set(MON, "01_fichaje", SourceStatus.DONE, "ok again")
    assert ck.get(MON, "01_fichaje") == SourceStatus.DONE
    assert ck.get(TUE, "01_fichaje") is None
