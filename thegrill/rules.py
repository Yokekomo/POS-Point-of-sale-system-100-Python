"""[00803] Reglas de negocio (§8). Cada regla es una función pura y testeable.

Se usan desde importadores, motores y orquestador: NUNCA se reimplementan en
otro sitio.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from thegrill import config
from thegrill.models import SourceStatus

FISH_KEYWORDS = ("fish", "seafood", "salmon", "tuna", "shrimp", "prawn", "octopus",
                 "squid", "pescado", "marisco", "gamba", "pulpo", "atun", "salmon")


class RuleViolation(ValueError):
    """[00804] Una regla crítica no se cumple. Nunca se silencia."""


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "FLAG"   # FLAG (registrar y seguir) / ERROR (bloquea)


# Regla 1 ------------------------------------------------- etiqueta física manda
@dataclass
class LabelResolution:
    value: str
    flagged: bool
    note: str | None = None


def resolve_from_label(sheet_value: str | None, label_value: str | None) -> LabelResolution:
    """[00805] El grado/corte/SKU se toma de la ETIQUETA FÍSICA. Si difiere de la hoja,
    gana la etiqueta y se registra flag."""
    if label_value is None or not str(label_value).strip():
        if sheet_value is None:
            raise RuleViolation("Sin etiqueta y sin valor de hoja: no se puede inventar")
        return LabelResolution(sheet_value, flagged=True, note="NO_LABEL_READ: usado valor de hoja, verificar")
    if sheet_value is None or _norm(sheet_value) == _norm(label_value):
        return LabelResolution(label_value, flagged=False)
    return LabelResolution(label_value, flagged=True,
                           note=f"LABEL_MISMATCH: hoja='{sheet_value}' etiqueta='{label_value}'")


def _norm(s: str) -> str:
    """[00806] Deja un texto comparable: minúsculas y un solo espacio entre palabras."""
    return " ".join(str(s).lower().split())


# [00819] Regla 2 ------------------------------ cada TG guarda seriales + piezas/peso por corte
@dataclass
class TGInput:
    tg: str
    origin: str | None
    serials: list[str | None]           # None = pieza local sin etiqueta
    cuts: list[dict]                    # {cut_name, pieces, weight_per_piece_g}


def validate_tg(tg_in: TGInput) -> list[Issue]:
    """[00807] Repasa un despiece antes de darlo por bueno y devuelve lo que falla.

    Dos cosas distintas: lo que es un error —un despiece sin piezas de entrada
    o sin cortes de salida— y lo que solo hay que mirar. Una pieza importada
    sin número de serie es lo segundo: no se puede rechazar, porque la carne ya
    está en la cámara, pero es una pieza fantasma y hay que recuperarla.

    Un corte que sale entero para cortarlo al vender no tiene piezas ni gramos
    por pieza que exigir; los kilos que se lleva, sí.
    """
    issues: list[Issue] = []
    if not tg_in.serials:
        issues.append(Issue("TG_NO_PRIMALS", f"{tg_in.tg}: sin primales de entrada", "ERROR"))
    imported = (tg_in.origin or "").upper() in config.IMPORTED_ORIGINS
    for s in tg_in.serials:
        if s is None:
            if imported:
                issues.append(Issue("PHANTOM_SERIAL", f"{tg_in.tg}: primal importado sin serial => FANTASMA a recuperar", "FLAG"))
            # [00820] local sin etiqueta: permitido, sin issue
    if not tg_in.cuts:
        issues.append(Issue("TG_NO_CUTS", f"{tg_in.tg}: sin cortes de salida", "ERROR"))
    for c in tg_in.cuts:
        if c.get("by_weight"):
            # [00821] Corte que sale entero y se cortará al vender: no hay piezas ni
            # gramos por pieza que exigir, pero los kilos que entran, sí.
            if not c.get("total_kg") or c["total_kg"] <= 0:
                issues.append(Issue("CUT_NO_KG", f"{tg_in.tg}/{c.get('cut_name')}: kilos obligatorios", "ERROR"))
            continue
        if not c.get("pieces") or c["pieces"] <= 0:
            issues.append(Issue("CUT_NO_PIECES", f"{tg_in.tg}/{c.get('cut_name')}: nº piezas obligatorio", "ERROR"))
        if not c.get("weight_per_piece_g") or c["weight_per_piece_g"] <= 0:
            issues.append(Issue("CUT_NO_WEIGHT", f"{tg_in.tg}/{c.get('cut_name')}: peso/pieza obligatorio", "ERROR"))
    return issues


# [00822] Regla 3 ------------------------------------- nunca marcar CUT por inferencia
ALLOWED_CUT_EVIDENCE = {"despiece", "physical_count"}


def can_mark_cut(evidence: str) -> bool:
    """[00808] Si esa prueba vale para dar un corte por hecho."""
    return evidence in ALLOWED_CUT_EVIDENCE


# Regla 4 -------------------------------------------- conteo semanal completo
def weekly_count_is_complete(counted_pieces: int,
                             expected_pieces: int = config.WEEKLY_COUNT_EXPECTED_PIECES,
                             tolerance: float = 0.05) -> bool:
    """[00809] Si el recuento semanal cubre lo bastante para darlo por bueno.

    Con un margen: pedir la cifra exacta dejaría el recuento abierto para
    siempre por dos piezas que estaban en el otro carro.
    """
    return counted_pieces >= expected_pieces * (1 - tolerance)


def weekly_count_is_stale(last_count: date | None, today: date,
                          max_age_days: int = config.WEEKLY_COUNT_MAX_AGE_DAYS) -> bool:
    """[00810] Si hace demasiado del último recuento —o si no ha habido ninguno."""
    return last_count is None or (today - last_count) > timedelta(days=max_age_days)


# [00823] Regla 6 --------------------------------- pescado/marisco excluido de carne
def is_meat_entry(description: str, category: str | None = None) -> bool:
    """[00811] Si una línea de factura es carne. El pescado no entra aquí."""
    text = f"{description} {category or ''}".lower()
    return not any(k in text for k in FISH_KEYWORDS)


# Regla 12 --------------------------------------------------- tres estados
def classify_source(posted: bool, readable: bool) -> SourceStatus:
    """[00812] En qué estado queda un origen de datos.

    Tres cosas distintas: aún no lo han publicado —no es un fallo, se vuelve a
    mirar—, está publicado pero no hay quien lo lea —eso sí—, o está.
    """
    if not posted:
        return SourceStatus.NOT_POSTED_YET
    if not readable:
        return SourceStatus.BLOCKED
    return SourceStatus.DONE


# Flags de precio ---------------------------------------------------------
def price_change_pct(old: float, new: float) -> float:
    """[00813] Cuánto ha cambiado un precio, en tanto por ciento y sin signo.

    Desde cero no hay porcentaje que valga: si antes era cero y ahora no,
    infinito, que es lo que hace saltar cualquier aviso.
    """
    if old == 0:
        return float("inf") if new else 0.0
    return abs(new - old) / old * 100.0


def bill_price_flag(old: float, new: float) -> bool:
    """[00814] Si el precio de una factura ha subido o bajado lo bastante para mirarlo."""
    return price_change_pct(old, new) >= config.PRICE_FLAG_BILLS_PCT


def pos_price_flag(old: float, new: float) -> bool:
    """[00815] Lo mismo con un precio de carta, que tiene su propio margen."""
    return price_change_pct(old, new) >= config.PRICE_FLAG_POS_PCT


# HACCP -------------------------------------------------------------------
def haccp_status(kind: str, reading_c: float | None) -> str:
    """[00816] Cómo queda una temperatura frente al límite legal.

    Que falte la lectura no es que esté bien: es que nadie la ha tomado, y eso
    se dice aparte. Refrigerado y congelado tienen su propio tope.
    """
    if reading_c is None:
        return "MISSING"
    limit = config.HACCP_LIMIT_CHILLED_C if kind.upper() == "CHILLED" else config.HACCP_LIMIT_FROZEN_C
    return "OK" if reading_c <= limit else "OUT_OF_RANGE"


def haccp_problems(checks: list[dict]) -> list[dict]:
    """[00817] Extrae SOLO problemas: fuera de rango, fallo sección B, sin acción correctiva."""
    out = []
    for c in checks:
        status = haccp_status(c["kind"], c.get("reading_c"))
        problem = status != "OK" or c.get("section_b_ok") is False
        if problem and not c.get("corrective_action"):
            out.append({**c, "status": status, "reason": "SIN_ACCION_CORRECTIVA"})
        elif problem:
            out.append({**c, "status": status, "reason": status if status != "OK" else "SECCION_B"})
    return out


# Importe ilegible ---------------------------------------------------------
def bill_status(amount: float | None) -> str:
    """[00818] Una factura sin importe hay que mirarla; con importe, está."""
    return "CHECK" if amount is None else "OK"
