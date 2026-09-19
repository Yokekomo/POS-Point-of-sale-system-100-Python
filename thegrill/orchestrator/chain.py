"""Orquestador de la cadena diaria (§6): pasos secuenciales, checkpoint por paso,
idempotente y reanudable. Cada paso devuelve uno de los tres estados (regla 12).

Los pasos se registran con `Chain.step(...)`; el cuerpo real de cada paso vive en
importadores/motores. Aquí solo hay orden, estado, reintentos y trazabilidad.
"""
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Protocol

from thegrill import config
from thegrill.models import SourceStatus


class TransientError(Exception):
    """500/529/timeout: se reintenta con backoff. Problemas de contenido NO son transitorios."""


@dataclass
class StepResult:
    state: SourceStatus
    detail: str = ""
    carry_over: list[str] = field(default_factory=list)   # arrastres al día siguiente


class CheckpointStore(Protocol):
    def get(self, run_date: date, step: str) -> SourceStatus | None: ...
    def set(self, run_date: date, step: str, state: SourceStatus, detail: str) -> None: ...


class MemoryCheckpoints:
    def __init__(self):
        self.data: dict[tuple[date, str], tuple[SourceStatus, str]] = {}

    def get(self, run_date, step):
        v = self.data.get((run_date, step))
        return v[0] if v else None

    def set(self, run_date, step, state, detail):
        self.data[(run_date, step)] = (state, detail)


class DbCheckpoints:
    """Checkpoints persistidos en `chain_checkpoints`, por restaurante."""

    def __init__(self, session_scope, restaurant_id: int):
        self.session_scope = session_scope
        self.restaurant_id = restaurant_id

    def get(self, run_date, step):
        from thegrill.models import ChainCheckpoint
        with self.session_scope() as s:
            row = (s.query(ChainCheckpoint)
                   .filter_by(restaurant_id=self.restaurant_id, run_date=run_date, step=step)
                   .one_or_none())
            return row.state if row else None

    def set(self, run_date, step, state, detail):
        from thegrill.models import ChainCheckpoint
        with self.session_scope() as s:
            row = (s.query(ChainCheckpoint)
                   .filter_by(restaurant_id=self.restaurant_id, run_date=run_date, step=step)
                   .one_or_none())
            if row is None:
                row = ChainCheckpoint(restaurant_id=self.restaurant_id, run_date=run_date,
                                      step=step, state=state, started_at=datetime.utcnow())
                s.add(row)
            row.state = state
            row.detail = detail
            row.finished_at = datetime.utcnow()


@dataclass
class Step:
    name: str
    fn: Callable[[date], StepResult]
    only_weekday: int | None = None     # 0 = lunes ... 6 = domingo


@dataclass
class RunReport:
    run_date: date
    results: dict[str, StepResult] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def blocked(self) -> list[str]:
        return [n for n, r in self.results.items() if r.state == SourceStatus.BLOCKED]

    @property
    def not_posted(self) -> list[str]:
        return [n for n, r in self.results.items() if r.state == SourceStatus.NOT_POSTED_YET]


class Chain:
    def __init__(self, checkpoints: CheckpointStore, sleep_fn=_time.sleep,
                 backoff=config.RETRY_BACKOFF_SECONDS):
        self.steps: list[Step] = []
        self.checkpoints = checkpoints
        self.sleep = sleep_fn
        self.backoff = backoff

    def step(self, name: str, only_weekday: int | None = None):
        def deco(fn):
            self.steps.append(Step(name, fn, only_weekday))
            return fn
        return deco

    def run(self, run_date: date, stop_on_blocked: bool = False) -> RunReport:
        report = RunReport(run_date)
        for st in self.steps:
            if st.only_weekday is not None and run_date.weekday() != st.only_weekday:
                report.skipped.append(st.name)
                continue
            prev = self.checkpoints.get(run_date, st.name)
            if prev == SourceStatus.DONE:
                report.results[st.name] = StepResult(SourceStatus.DONE, "checkpoint: ya hecho")
                continue
            t0 = _time.monotonic()
            result = self._run_with_retry(st, run_date)
            report.timings[st.name] = round(_time.monotonic() - t0, 3)
            report.results[st.name] = result
            self.checkpoints.set(run_date, st.name, result.state, result.detail)
            if stop_on_blocked and result.state == SourceStatus.BLOCKED:
                break
        return report

    def _run_with_retry(self, st: Step, run_date: date) -> StepResult:
        attempts = len(self.backoff) + 1
        for i in range(attempts):
            try:
                return st.fn(run_date)
            except TransientError as e:
                if i == attempts - 1:
                    return StepResult(SourceStatus.BLOCKED, f"transitorio agotado: {e}")
                self.sleep(self.backoff[i])
            except Exception as e:  # contenido ilegible u otro: no reintentar, flaggear
                return StepResult(SourceStatus.BLOCKED, f"{type(e).__name__}: {e}")
        return StepResult(SourceStatus.BLOCKED, "sin resultado")


# Los nombres conservan la numeración de la especificación de negocio (§6) para
# que cada paso siga siendo rastreable en una auditoría. El paso 1 era el
# fichaje de personal y se retiró: la cadena empieza en el 2.
STEP_ORDER = [
    ("02_produccion_merma", None),
    ("03_despiece", None),
    ("04_facturas", None),
    ("05_entrada_carne", None),
    ("06_ventas", None),
    ("07_cierre_vitrina", None),
    ("07b_descongelado", None),
    ("07c_haccp_walkin", None),
    ("08_inventario_semanal", 0),     # lunes
    ("08b_plan_pan", 1),              # martes
    ("09_pedidos_envios_brief", None),
    ("10_daily_report", None),
]


def build_default_chain(checkpoints: CheckpointStore, handlers: dict[str, Callable[[date], StepResult]],
                        **kw) -> Chain:
    """Monta la cadena en el orden canónico con los handlers disponibles.
    Un paso sin handler queda registrado como BLOCKED ('no implementado'), nunca como done."""
    chain = Chain(checkpoints, **kw)
    for name, weekday in STEP_ORDER:
        fn = handlers.get(name) or _not_implemented(name)
        chain.steps.append(Step(name, fn, weekday))
    return chain


def _not_implemented(name):
    def fn(run_date):
        return StepResult(SourceStatus.BLOCKED, f"{name}: handler no implementado")
    return fn
