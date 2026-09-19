"""Constantes de negocio. Todo lo que hoy está disperso en scripts vive aquí."""
from dataclasses import dataclass, field

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
