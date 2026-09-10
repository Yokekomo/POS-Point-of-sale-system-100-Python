"""Modelo de datos (§5 de la especificación). Cada workbook Excel pasa a ser una tabla.

Principios: fuente única de verdad, trazabilidad (quién/qué/cuándo) y nunca
inventar datos. Los seriales consumidos por un TG viven SOLO en `despiece_primals`.
"""
import enum
from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from thegrill.db import Base


# ------------------------------------------------------------------- Enums
class PrimalStatus(str, enum.Enum):
    IN_STOCK = "IN_STOCK"
    CUT = "CUT"
    WASTE = "WASTE"


class MovementType(str, enum.Enum):
    IN = "IN"
    OUT = "OUT"
    WASTE = "WASTE"
    FROZEN_CUT = "FROZEN_CUT"
    READJUST = "READJUST"


class SourceStatus(str, enum.Enum):
    """Regla 12: tres estados, nunca colapsar (c) en "0/done"."""
    DONE = "DONE"                      # (a) escaneado, resultado (aunque sea 0)
    NOT_POSTED_YET = "NOT_POSTED_YET"  # (b) la fuente no está publicada -> arrastre
    BLOCKED = "BLOCKED"                # (c) existe pero no se pudo leer -> INCOMPLETE


class ClarificationStatus(str, enum.Enum):
    ENCOLADA = "ENCOLADA"
    ENVIADA = "ENVIADA"
    RESPONDIDA = "RESPONDIDA"
    CANCELADA = "CANCELADA"
    HELD = "HELD"


class HaccpKind(str, enum.Enum):
    CHILLED = "CHILLED"
    FROZEN = "FROZEN"


class FefoStage(str, enum.Enum):
    MASTER = "MASTER"    # maestro FEFO: nunca lo edita un script
    TO_ADD = "TO_ADD"    # staging que el script sí puede escribir


# ------------------------------------------------------------ 1. primals
class Primal(Base):
    __tablename__ = "primals"

    serial: Mapped[str] = mapped_column(String(16), primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    grade: Mapped[str | None] = mapped_column(String(32))
    origin: Mapped[str | None] = mapped_column(String(32))
    weight_kg: Mapped[float] = mapped_column(Float)
    lot: Mapped[str | None] = mapped_column(String(16), index=True)      # DXB{AAAAMMDD}
    received_date: Mapped[date | None] = mapped_column(Date)
    landed_usd_per_kg: Mapped[float | None] = mapped_column(Float)
    piece_cost_usd: Mapped[float | None] = mapped_column(Float)
    status: Mapped[PrimalStatus] = mapped_column(Enum(PrimalStatus), default=PrimalStatus.IN_STOCK)
    status_ref: Mapped[str | None] = mapped_column(String(16))           # TG-####
    status_date: Mapped[date | None] = mapped_column(Date)
    label_product: Mapped[str | None] = mapped_column(String(128))       # lo que dice la etiqueta física
    producer_plant: Mapped[str | None] = mapped_column(String(128))
    est_code: Mapped[str | None] = mapped_column(String(32))
    slaughter_date: Mapped[date | None] = mapped_column(Date)
    pack_date: Mapped[date | None] = mapped_column(Date)
    expiry_label: Mapped[date | None] = mapped_column(Date)
    frozen_use_by: Mapped[date | None] = mapped_column(Date)
    halal: Mapped[bool | None] = mapped_column(Boolean)
    photo_ref: Mapped[str | None] = mapped_column(String(256))
    suspect_phantom: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------- 2. despieces
class Despiece(Base):
    __tablename__ = "despieces"

    tg: Mapped[str] = mapped_column(String(16), primary_key=True)         # TG-####
    date: Mapped[date] = mapped_column(Date, index=True)
    staff: Mapped[str | None] = mapped_column(String(64))
    animal: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(32))
    grade: Mapped[str | None] = mapped_column(String(32))
    prime_cut_input: Mapped[str | None] = mapped_column(String(64))
    weight_before_kg: Mapped[float] = mapped_column(Float)
    waste_kg: Mapped[float] = mapped_column(Float, default=0.0)
    trim_kg: Mapped[float] = mapped_column(Float, default=0.0)
    total_cuts_kg: Mapped[float] = mapped_column(Float, default=0.0)
    yield_pct: Mapped[float | None] = mapped_column(Float)
    freezer: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)

    primals: Mapped[list["DespiecePrimal"]] = relationship(back_populates="despiece", cascade="all, delete-orphan")
    cuts: Mapped[list["DespieceCut"]] = relationship(back_populates="despiece", cascade="all, delete-orphan")


# ---------------------------------------------- 3. despiece_primals (N:M)
class DespiecePrimal(Base):
    """ÚNICA fuente de verdad de qué seriales consume cada TG."""
    __tablename__ = "despiece_primals"
    __table_args__ = (UniqueConstraint("tg", "serial", name="uq_tg_serial"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg: Mapped[str] = mapped_column(ForeignKey("despieces.tg"), index=True)
    serial: Mapped[str | None] = mapped_column(ForeignKey("primals.serial"), index=True)  # None = local sin etiqueta
    local_no_label: Mapped[bool] = mapped_column(Boolean, default=False)
    label_kg: Mapped[float | None] = mapped_column(Float)

    despiece: Mapped["Despiece"] = relationship(back_populates="primals")


# ------------------------------------------------------- 4. despiece_cuts
class DespieceCut(Base):
    __tablename__ = "despiece_cuts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg: Mapped[str] = mapped_column(ForeignKey("despieces.tg"), index=True)
    cut_name: Mapped[str] = mapped_column(String(64))
    pieces: Mapped[int] = mapped_column(Integer)
    weight_per_piece_g: Mapped[float] = mapped_column(Float)
    total_kg: Mapped[float] = mapped_column(Float)

    despiece: Mapped["Despiece"] = relationship(back_populates="cuts")


# ------------------------------------------------------ 5. stock_movements
class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[MovementType] = mapped_column(Enum(MovementType))
    kg: Mapped[float] = mapped_column(Float, default=0.0)
    pieces: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))          # TG / POS / count / bill / waste
    source_ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="chain")


# --------------------------------------------------------- 6. daily_counts
class DailyCount(Base):
    __tablename__ = "daily_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16))            # display / defrost
    sku: Mapped[str] = mapped_column(String(64), index=True)
    kg: Mapped[float] = mapped_column(Float)
    pieces: Mapped[int | None] = mapped_column(Integer)
    g_per_piece_flag: Mapped[bool] = mapped_column(Boolean, default=False)


# ------------------------------------------------ 7. weekly_physical_count
class WeeklyPhysicalCount(Base):
    __tablename__ = "weekly_physical_count"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    serial: Mapped[str | None] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    weight_kg: Mapped[float | None] = mapped_column(Float)
    counted_by: Mapped[str | None] = mapped_column(String(64))


# ---------------------------------------------------------- 8. sales_daily
class SalesDaily(Base):
    __tablename__ = "sales_daily"

    op_date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[SourceStatus] = mapped_column(Enum(SourceStatus), default=SourceStatus.DONE)
    gross: Mapped[float | None] = mapped_column(Float)
    net_tax: Mapped[float | None] = mapped_column(Float)
    pax: Mapped[int | None] = mapped_column(Integer)
    units: Mapped[int | None] = mapped_column(Integer)
    avg_check: Mapped[float | None] = mapped_column(Float)
    bar: Mapped[float | None] = mapped_column(Float)
    bev: Mapped[float | None] = mapped_column(Float)
    food: Mapped[float | None] = mapped_column(Float)
    other: Mapped[float | None] = mapped_column(Float)
    promos: Mapped[float | None] = mapped_column(Float)
    refunds: Mapped[float | None] = mapped_column(Float)


# ----------------------------------------------------- 9. sales_by_product
class SalesByProduct(Base):
    __tablename__ = "sales_by_product"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    op_date: Mapped[date] = mapped_column(Date, index=True)
    pos_name: Mapped[str] = mapped_column(String(128))
    units: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Float)
    unit_price: Mapped[float | None] = mapped_column(Float)


# -------------------------------------------- 10. portion_map / product_master
class PortionMap(Base):
    __tablename__ = "portion_map"

    dish: Mapped[str] = mapped_column(String(128), primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    kg_per_portion: Mapped[float] = mapped_column(Float)


class ProductMaster(Base):
    __tablename__ = "product_master"

    pos_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)


# ---------------------------------------------------------------- 11. bills
class Bill(Base):
    __tablename__ = "bills"
    __table_args__ = (UniqueConstraint("supplier", "number", name="uq_bill_supplier_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supplier: Mapped[str] = mapped_column(String(128), index=True)
    number: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))          # food / meat / beverage / ...
    amount: Mapped[float | None] = mapped_column(Float)         # None = ilegible => CHECK
    currency: Mapped[str] = mapped_column(String(3))
    date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default="OK")   # OK / CHECK
    price_flag_pct: Mapped[float | None] = mapped_column(Float)


# -------------------------------------------------- 12. meat_entry_register
class MeatEntry(Base):
    __tablename__ = "meat_entry_register"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    supplier: Mapped[str] = mapped_column(String(128))
    sku: Mapped[str] = mapped_column(String(64), index=True)
    kg: Mapped[float] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    bill_id: Mapped[int | None] = mapped_column(ForeignKey("bills.id"))


# --------------------------------------------------------------- 13. orders
class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(16), primary_key=True)   # OR-####
    date: Mapped[date] = mapped_column(Date, index=True)
    outlet: Mapped[str] = mapped_column(String(128), index=True)
    route: Mapped[str] = mapped_column(String(16))                        # on-route / off-route
    status: Mapped[str] = mapped_column(String(16), default="COMPILED")
    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderLine(Base):
    __tablename__ = "order_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.order_id"), index=True)
    product: Mapped[str] = mapped_column(String(128))
    qty: Mapped[float] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(16))
    order: Mapped["Order"] = relationship(back_populates="lines")


# --------------------------------------------------------------- 14. roster
class RosterEntry(Base):
    __tablename__ = "roster"
    __table_args__ = (UniqueConstraint("person", "date", name="uq_roster_person_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    clock_in: Mapped[datetime | None] = mapped_column(DateTime)
    clock_out: Mapped[datetime | None] = mapped_column(DateTime)
    hours: Mapped[float | None] = mapped_column(Float)
    day_off: Mapped[bool] = mapped_column(Boolean, default=False)
    flag: Mapped[str | None] = mapped_column(String(32))     # MISSING_OUT / MISSING_IN / None


# --------------------------------------------------------- 15. haccp_checks
class HaccpCheck(Base):
    __tablename__ = "haccp_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    unit: Mapped[str] = mapped_column(String(64))
    kind: Mapped[HaccpKind] = mapped_column(Enum(HaccpKind))
    limit_c: Mapped[float] = mapped_column(Float)
    reading_c: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16))          # OK / OUT_OF_RANGE / MISSING
    section_b_ok: Mapped[bool | None] = mapped_column(Boolean)
    corrective_action: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------- 16. clarifications_queue
class Clarification(Base):
    __tablename__ = "clarifications_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    question: Mapped[str] = mapped_column(Text)
    recipient: Mapped[str] = mapped_column(String(64), index=True)
    topic: Mapped[str] = mapped_column(String(32), default="general")
    status: Mapped[ClarificationStatus] = mapped_column(Enum(ClarificationStatus), default=ClarificationStatus.ENCOLADA)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    history: Mapped[str | None] = mapped_column(Text)        # JSON con intentos/respuestas


# ----------------------------------------------------------- 17. fefo_stock
class FefoLot(Base):
    __tablename__ = "fefo_stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingredient: Mapped[str] = mapped_column(String(64), index=True)
    lot: Mapped[str | None] = mapped_column(String(32))
    expiry: Mapped[date] = mapped_column(Date, index=True)
    received: Mapped[date | None] = mapped_column(Date)
    kg: Mapped[float] = mapped_column(Float)
    unit_cost_usd: Mapped[float] = mapped_column(Float)
    stage: Mapped[FefoStage] = mapped_column(Enum(FefoStage), default=FefoStage.TO_ADD)


# ----------------------------------------- Orquestación y trazabilidad
class ChainCheckpoint(Base):
    __tablename__ = "chain_checkpoints"
    __table_args__ = (UniqueConstraint("run_date", "step", name="uq_checkpoint_run_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    step: Mapped[str] = mapped_column(String(32))
    state: Mapped[SourceStatus] = mapped_column(Enum(SourceStatus))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    detail: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    table: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))          # INSERT / UPDATE / FLAG
    detail: Mapped[str | None] = mapped_column(Text)


class SentMessage(Base):
    """Registro anti-duplicado: qué se envió, a qué chat, qué día."""
    __tablename__ = "sent_messages"
    __table_args__ = (UniqueConstraint("chat", "template", "day", name="uq_sent_chat_template_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat: Mapped[str] = mapped_column(String(128))
    template: Mapped[str] = mapped_column(String(64))
    day: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
