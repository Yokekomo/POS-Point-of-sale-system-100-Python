"""Constantes de negocio. Todo lo que hoy está disperso en scripts vive aquí."""
from dataclasses import dataclass, field
from datetime import time

# ----------------------------------------------------------------- Moneda / FX
IQD_PER_USD = 1550.0          # FX fijo del negocio
IQD_PER_AED = 360.0           # Dubái


def to_usd(amount: float, currency: str) -> float:
    cur = currency.upper()
    if cur == "USD":
        return amount
    if cur == "IQD":
        return amount / IQD_PER_USD
    if cur == "AED":
        return amount * IQD_PER_AED / IQD_PER_USD
    raise ValueError(f"Moneda desconocida: {currency}")


# ---------------------------------------------------------------------- HACCP
HACCP_LIMIT_CHILLED_C = 5.0    # refrigerado: lectura <= +5 °C
HACCP_LIMIT_FROZEN_C = -18.0   # congelado:   lectura <= -18 °C

# ------------------------------------------------------------ Flags de precio
PRICE_FLAG_BILLS_PCT = 15.0    # facturas: variación >= 15 % => flag
PRICE_FLAG_POS_PCT = 8.0       # POS: movimiento de precio >= 8 % => flag

# ---------------------------------------------------------------- Mensajería
SEND_WINDOW_START = time(9, 0)
SEND_WINDOW_END = time(20, 0)
ORDERS_EARLY_CUTOFF = time(8, 0)   # pedidos "antes de 08:00" pueden salir fuera de ventana

# Supresiones vigentes (destinatario -> temas que NO recibe)
SUPPRESSIONS: dict[str, set[str]] = {
    "Fadi": {"meat", "haccp"},        # 2026-09-04
    "Olivier": {"meat"},              # 2026-09-10
}
MEAT_REPORT_RECIPIENTS = ("Ahmed", "Yuan")   # parte de carne
SELF_CHAT = "Albano"                          # sin ventana horaria

# --------------------------------------------------------------- Reintentos
RETRY_BACKOFF_SECONDS = (30, 60, 120)
TRANSIENT_HTTP_CODES = {500, 529}

# ----------------------------------------------------------------- Despiece
MASS_DRIFT_TOLERANCE_PCT = 2.0     # in = out + waste + trim ± drift
IMPORTED_ORIGINS = {"DXB", "DUBAI", "AUS", "USA", "NZ", "JPN"}  # primal importado => serial obligatorio

# ------------------------------------------------------------ Conteo semanal
WEEKLY_COUNT_EXPECTED_PIECES = 130
WEEKLY_COUNT_MAX_AGE_DAYS = 8      # conteo vencido => floors v4 pueden enmascarar merma


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///thegrill.db"
    backup_dir: str = "backups"
    reports_dir: str = "reports_out"
    timezone: str = "Asia/Baghdad"
    extra: dict = field(default_factory=dict)
