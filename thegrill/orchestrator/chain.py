"""[00779] Orquestador de la cadena diaria (§6): pasos secuenciales, checkpoint por paso,
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
    """[00780] 500/529/timeout: se reintenta con backoff. Problemas de contenido NO son transitorios."""


@dataclass
class StepResult:
    state: SourceStatus
    detail: str = ""
    carry_over: list[str] = field(default_factory=list)   # arrastres al día siguiente


class CheckpointStore(Protocol):
    """[00781] Dónde se apunta por dónde va la cadena: en memoria o en la base."""

    def get(self, run_date: date, step: str) -> SourceStatus | None:
        """[00785] Cómo acabó ese paso ese día, o nada si no se ha corrido todavía."""
        ...

    def set(self, run_date: date, step: str, state: SourceStatus, detail: str) -> None:
        """[00786] Apunta cómo acabó el paso, para no repetirlo si se relanza el día."""
        ...


class MemoryCheckpoints:
    def __init__(self):
        """[00787] Checkpoints en memoria: para las pruebas y para una pasada suelta."""
        self.data: dict[tuple[date, str], tuple[SourceStatus, str]] = {}

    def get(self, run_date, step):
        """[00788] Cómo acabó ese paso ese día, si consta."""
        v = self.data.get((run_date, step))
        return v[0] if v else None

    def set(self, run_date, step, state, detail):
        """[00789] Guarda el resultado del paso en el diccionario."""
        self.data[(run_date, step)] = (state, detail)


class DbCheckpoints:
    """[00782] Checkpoints persistidos en `chain_checkpoints`, por restaurante."""

    def __init__(self, session_scope, restaurant_id: int):
        """[00790] Los checkpoints de un restaurante, contra la base de datos."""
        self.session_scope = session_scope
        self.restaurant_id = restaurant_id

    def get(self, run_date, step):
        """[00791] Cómo acabó ese paso ese día, según la tabla."""
        from thegrill.models import ChainCheckpoint
        with self.session_scope() as s:
            row = (s.query(ChainCheckpoint)
                   .filter_by(restaurant_id=self.restaurant_id, run_date=run_date, step=step)
                   .one_or_none())
            return row.state if row else None

    def set(self, run_date, step, state, detail):
        """[00792] Guarda o actualiza la fila del paso, con la hora en que terminó."""
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
        """[00793] Los pasos que se atascaron: lo que hay que mirar a mano."""
        return [n for n, r in self.results.items() if r.state == SourceStatus.BLOCKED]

    @property
    def not_posted(self) -> list[str]:
        """[00794] Los pasos cuyo origen aún no había publicado los datos.

        No es un fallo: la caja todavía no ha cerrado, la factura no ha llegado.
        Se vuelve a intentar en la pasada siguiente y por eso no se marcan hechos.
        """
        return [n for n, r in self.results.items() if r.state == SourceStatus.NOT_POSTED_YET]


class Chain:
    def __init__(self, checkpoints: CheckpointStore, sleep_fn=_time.sleep,
                 backoff=config.RETRY_BACKOFF_SECONDS):
        """[00795] La cadena, con dónde apuntar los checkpoints y cuánto esperar entre intentos."""
        self.steps: list[Step] = []
        self.checkpoints = checkpoints
        self.sleep = sleep_fn
        self.backoff = backoff

    def step(self, name: str, only_weekday: int | None = None):
        """[00796] Registra un paso. Se usa como decorador sobre la función que lo hace.

        Con `only_weekday` el paso solo corre ese día de la semana —el inventario
        los lunes, el plan de pan los martes—: el resto de días se salta y consta
        como saltado, no como hecho.
        """
        def deco(fn):
            """[00800] Apunta la función en la lista de pasos y la devuelve intacta."""
            self.steps.append(Step(name, fn, only_weekday))
            return fn
        return deco

    def run(self, run_date: date, stop_on_blocked: bool = False) -> RunReport:
        """[00797] Corre la cadena entera de un día y devuelve el parte.

        Un paso que ya consta hecho no se repite: la cadena se puede relanzar
        tantas veces como haga falta y solo trabaja lo que quedó pendiente, que es
        lo que se necesita la mañana que algo se atascó a las tres de la noche.
        """
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
        """[00798] Corre un paso, reintentando solo lo que tiene sentido reintentar.

        Un corte de red o un 500 se vuelven a intentar esperando cada vez un poco
        más. Un fichero ilegible o una columna que no está, no: por muchas veces
        que se pida, va a venir igual de mal, y lo que hay que hacer es avisar.
        """
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


# [00801] Los nombres conservan la numeración de la especificación de negocio (§6) para
# que cada paso siga siendo rastreable en una auditoría. Se retiraron el paso 1
# (fichaje de personal) y el 9 (pedidos a locales), así que la cadena empieza en
# el 2 y salta del 8b al 10.
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
    ("10_daily_report", None),
]


def build_default_chain(checkpoints: CheckpointStore, handlers: dict[str, Callable[[date], StepResult]],
                        **kw) -> Chain:
    """[00783] Monta la cadena en el orden canónico con los handlers disponibles.
    Un paso sin handler queda registrado como BLOCKED ('no implementado'), nunca como done."""
    chain = Chain(checkpoints, **kw)
    for name, weekday in STEP_ORDER:
        fn = handlers.get(name) or _not_implemented(name)
        chain.steps.append(Step(name, fn, weekday))
    return chain


def _not_implemented(name):
    """[00784] El relleno de un paso que todavía no existe: siempre atascado.

    Nunca «hecho». Un paso sin código que dijera que sí dejaría el día cerrado
    con un trozo de la cadena sin correr y sin que nadie se enterara.
    """
    def fn(run_date):
        """[00799] Devuelve siempre atascado, diciendo qué paso falta por escribir."""
        return StepResult(SourceStatus.BLOCKED, f"{name}: handler no implementado")
    return fn
