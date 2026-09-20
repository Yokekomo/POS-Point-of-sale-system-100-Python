"""Modelo de datos de la plataforma.

Dos capas:

1. **Plataforma genérica** (sirve a cualquier restaurante): restaurantes
   (inquilinos), usuarios con rol manager/empleado, plantillas de registro
   configurables, registros con valores y fotos, alertas.
2. **Módulos especializados** (opcionales, del proyecto The Grill): primales,
   despieces, ventas POS, FEFO. Todas llevan `restaurant_id`.

Invariantes: cada fila pertenece a un restaurante; los registros son
append-only (una corrección es un registro nuevo que apunta al anterior);
la hora la pone el servidor, nunca el cliente.
"""
import enum
from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from thegrill.db import Base


# =============================================================== Enums
class Role(str, enum.Enum):
    MANAGER = "MANAGER"      # acceso total: estadísticas, configuración, usuarios
    EMPLOYEE = "EMPLOYEE"    # solo meter datos y fotos, y ver lo que él mismo metió


class FieldType(str, enum.Enum):
    NUMBER = "NUMBER"
    TEXT = "TEXT"
    SELECT = "SELECT"
    DATE = "DATE"
    BOOL = "BOOL"


class RecordStatus(str, enum.Enum):
    OK = "OK"
    ALERT = "ALERT"          # algún valor fuera de límites
    CORRECTED = "CORRECTED"  # sustituido por un registro posterior


class NotificationKind(str, enum.Enum):
    ALERT = "ALERT"            # un registro se salió de límites
    RESOLUTION = "RESOLUTION"  # un manager cerró una alerta que tú reportaste


class AlertSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


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
    DONE = "DONE"
    NOT_POSTED_YET = "NOT_POSTED_YET"
    BLOCKED = "BLOCKED"


class HaccpKind(str, enum.Enum):
    CHILLED = "CHILLED"
    FROZEN = "FROZEN"


class Unit(str, enum.Enum):
    """Unidad base del ingrediente. Las recetas se escriben en esta unidad."""
    KG = "KG"
    L = "L"
    UNIT = "UNIT"


class PosMatch(str, enum.Enum):
    """Por qué campo identifica el POS cada artículo."""
    CODE = "CODE"   # solo por el número de artículo
    NAME = "NAME"   # solo por el nombre
    BOTH = "BOTH"   # por cualquiera de los dos; el código manda


class ConsumptionMode(str, enum.Enum):
    """Cómo se descuenta un ingrediente del almacén."""
    RECIPE = "RECIPE"   # al vender, según el escandallo
    COUNT = "COUNT"     # al cerrar turno, por el conteo físico de descongelado


class DefrostKind(str, enum.Enum):
    INTAKE = "INTAKE"   # pieza que se saca a descongelar
    COUNT = "COUNT"     # recuento de lo que queda al acabar el turno


class Rotation(str, enum.Enum):
    """Cómo salen los lotes de un ingrediente madre."""
    FEFO = "FEFO"   # antes lo que antes caduca (por defecto, lo correcto en fresco)
    FIFO = "FIFO"   # antes lo que antes entró (seco y no perecedero)


class RecipeKind(str, enum.Enum):
    DISH = "DISH"    # plato que se vende: rinde raciones y tiene precio
    PREP = "PREP"    # elaboración intermedia: rinde kg/l y se usa en otras recetas


class MovementKind(str, enum.Enum):
    IN = "IN"            # compra o entrada
    SALE = "SALE"        # consumo por venta en el POS
    WASTE = "WASTE"      # merma
    PRODUCTION = "PRODUCTION"   # consumo por elaborar una producción
    ADJUST = "ADJUST"    # ajuste por conteo físico


class FefoStage(str, enum.Enum):
    MASTER = "MASTER"
    TO_ADD = "TO_ADD"


# ====================================================== Mixin de inquilino
class TenantMixin:
    """Toda tabla operativa pertenece a un restaurante."""

    @declared_attr
    def restaurant_id(cls) -> Mapped[int]:
        return mapped_column(ForeignKey("restaurants.id"), index=True, nullable=False)


# ====================================================== Plataforma genérica
class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    join_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    language: Mapped[str] = mapped_column(String(5), default="es")   # idioma por defecto del local
    pos_match: Mapped[PosMatch] = mapped_column(Enum(PosMatch), default=PosMatch.BOTH)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(TenantMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("restaurant_id", "email", name="uq_user_restaurant_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(190), index=True)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.EMPLOYEE)
    language: Mapped[str | None] = mapped_column(String(5))          # None = usa el del restaurante
    password_hash: Mapped[str] = mapped_column(String(256))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class RecordTemplate(TenantMixin, Base):
    """Plantilla de registro: lo que un restaurante concreto quiere capturar."""
    __tablename__ = "record_templates"
    __table_args__ = (UniqueConstraint("restaurant_id", "code", name="uq_template_restaurant_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))     # haccp / waste / production / reception / cleaning / count / other
    description: Mapped[str | None] = mapped_column(Text)
    requires_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    frequency: Mapped[str] = mapped_column(String(16), default="daily")   # daily / shift / weekly / adhoc
    expected_per_day: Mapped[int] = mapped_column(Integer, default=1)     # para % de cumplimiento
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    fields: Mapped[list["TemplateField"]] = relationship(
        back_populates="template", cascade="all, delete-orphan",
        order_by="TemplateField.sort_order")


class TemplateField(Base):
    __tablename__ = "template_fields"
    __table_args__ = (UniqueConstraint("template_id", "key", name="uq_field_template_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("record_templates.id"), index=True)
    key: Mapped[str] = mapped_column(String(48))
    label: Mapped[str] = mapped_column(String(128))
    type: Mapped[FieldType] = mapped_column(Enum(FieldType), default=FieldType.TEXT)
    unit: Mapped[str | None] = mapped_column(String(16))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    min_value: Mapped[float | None] = mapped_column(Float)   # fuera de rango => alerta automática
    max_value: Mapped[float | None] = mapped_column(Float)
    options: Mapped[str | None] = mapped_column(Text)        # SELECT: opciones separadas por |
    expiry_alert_days: Mapped[int | None] = mapped_column(Integer)   # DATE: avisa si caduca en <= N días
    help_text: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    template: Mapped["RecordTemplate"] = relationship(back_populates="fields")


class Record(TenantMixin, Base):
    """Un registro enviado por un empleado. Append-only."""
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("record_templates.id"), index=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[RecordStatus] = mapped_column(Enum(RecordStatus), default=RecordStatus.OK)
    note: Mapped[str | None] = mapped_column(Text)
    corrects_id: Mapped[int | None] = mapped_column(ForeignKey("records.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    values: Mapped[list["RecordValue"]] = relationship(back_populates="record", cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="record", cascade="all, delete-orphan")
    template: Mapped["RecordTemplate"] = relationship()


class RecordValue(Base):
    __tablename__ = "record_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id"), index=True)
    field_key: Mapped[str] = mapped_column(String(48), index=True)
    value_text: Mapped[str | None] = mapped_column(Text)
    value_number: Mapped[float | None] = mapped_column(Float)
    value_date: Mapped[date | None] = mapped_column(Date)
    value_bool: Mapped[bool | None] = mapped_column(Boolean)
    out_of_range: Mapped[bool] = mapped_column(Boolean, default=False)

    record: Mapped["Record"] = relationship(back_populates="values")

    @property
    def display(self) -> str:
        for v in (self.value_text, self.value_number, self.value_date, self.value_bool):
            if v is not None:
                return str(v)
        return ""


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id"), index=True)
    filename: Mapped[str] = mapped_column(String(256))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    stored_path: Mapped[str] = mapped_column(String(512))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    record: Mapped["Record"] = relationship(back_populates="attachments")


class Alert(TenantMixin, Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(48), index=True)
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity), default=AlertSeverity.WARNING)
    record_id: Mapped[int | None] = mapped_column(ForeignKey("records.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution: Mapped[str | None] = mapped_column(Text)


class Notification(TenantMixin, Base):
    """Aviso dirigido a una persona concreta dentro de la plataforma.

    Existe para que una alerta crítica no se quede esperando a que alguien
    entre a mirar: aparece en el contador de la cabecera de quien debe actuar.
    No sale de la plataforma: no hay correo ni mensajería.
    """
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[NotificationKind] = mapped_column(Enum(NotificationKind), default=NotificationKind.ALERT)
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity), default=AlertSeverity.WARNING)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"))
    record_id: Mapped[int | None] = mapped_column(ForeignKey("records.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


# ================================================= Módulos especializados
class Primal(TenantMixin, Base):
    __tablename__ = "primals"
    __table_args__ = (UniqueConstraint("restaurant_id", "serial", name="uq_primal_restaurant_serial"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    serial: Mapped[str] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    grade: Mapped[str | None] = mapped_column(String(32))
    origin: Mapped[str | None] = mapped_column(String(32))
    weight_kg: Mapped[float] = mapped_column(Float)
    lot: Mapped[str | None] = mapped_column(String(16), index=True)
    received_date: Mapped[date | None] = mapped_column(Date)
    landed_usd_per_kg: Mapped[float | None] = mapped_column(Float)
    piece_cost_usd: Mapped[float | None] = mapped_column(Float)
    status: Mapped[PrimalStatus] = mapped_column(Enum(PrimalStatus), default=PrimalStatus.IN_STOCK)
    status_ref: Mapped[str | None] = mapped_column(String(16))
    status_date: Mapped[date | None] = mapped_column(Date)
    label_product: Mapped[str | None] = mapped_column(String(128))
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


class Despiece(TenantMixin, Base):
    __tablename__ = "despieces"
    __table_args__ = (UniqueConstraint("restaurant_id", "tg", name="uq_despiece_restaurant_tg"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg: Mapped[str] = mapped_column(String(16), index=True)
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
    posted: Mapped[bool] = mapped_column(Boolean, default=False)   # ya volcado al almacén
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)

    primals: Mapped[list["DespiecePrimal"]] = relationship(back_populates="despiece", cascade="all, delete-orphan")
    cuts: Mapped[list["DespieceCut"]] = relationship(back_populates="despiece", cascade="all, delete-orphan")


class DespiecePrimal(Base):
    """ÚNICA fuente de verdad de qué seriales consume cada despiece."""
    __tablename__ = "despiece_primals"
    __table_args__ = (UniqueConstraint("despiece_id", "serial", name="uq_despiece_serial"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    despiece_id: Mapped[int] = mapped_column(ForeignKey("despieces.id"), index=True)
    serial: Mapped[str | None] = mapped_column(String(16), index=True)   # None = local sin etiqueta
    local_no_label: Mapped[bool] = mapped_column(Boolean, default=False)
    label_kg: Mapped[float | None] = mapped_column(Float)

    despiece: Mapped["Despiece"] = relationship(back_populates="primals")


class DespieceCut(Base):
    """Un corte de salida del despiece.

    El corte es lo que enlaza la carne con la cocina: apunta a un artículo, el
    artículo cuelga de un ingrediente madre, y la madre se usa en las recetas.
    Un recorte reutilizable es un corte más, marcado como tal.
    """
    __tablename__ = "despiece_cuts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    despiece_id: Mapped[int] = mapped_column(ForeignKey("despieces.id"), index=True)
    cut_name: Mapped[str] = mapped_column(String(64))
    pieces: Mapped[int] = mapped_column(Integer)
    weight_per_piece_g: Mapped[float] = mapped_column(Float)
    total_kg: Mapped[float] = mapped_column(Float)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient_items.id"), index=True)
    is_trim: Mapped[bool] = mapped_column(Boolean, default=False)   # parte para reusar
    value_index: Mapped[float] = mapped_column(Float, default=1.0)  # reparto del coste del primal
    lot_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient_lots.id"))

    despiece: Mapped["Despiece"] = relationship(back_populates="cuts")
    item: Mapped["IngredientItem"] = relationship()


class StockMovement(TenantMixin, Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[MovementType] = mapped_column(Enum(MovementType))
    kg: Mapped[float] = mapped_column(Float, default=0.0)
    pieces: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="chain")


class DailyCount(TenantMixin, Base):
    __tablename__ = "daily_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    sku: Mapped[str] = mapped_column(String(64), index=True)
    kg: Mapped[float] = mapped_column(Float)
    pieces: Mapped[int | None] = mapped_column(Integer)
    g_per_piece_flag: Mapped[bool] = mapped_column(Boolean, default=False)


class WeeklyPhysicalCount(TenantMixin, Base):
    __tablename__ = "weekly_physical_count"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    serial: Mapped[str | None] = mapped_column(String(16), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    weight_kg: Mapped[float | None] = mapped_column(Float)
    counted_by: Mapped[str | None] = mapped_column(String(64))


class SalesDaily(TenantMixin, Base):
    __tablename__ = "sales_daily"
    __table_args__ = (UniqueConstraint("restaurant_id", "op_date", name="uq_sales_restaurant_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    op_date: Mapped[date] = mapped_column(Date, index=True)
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


class SalesByProduct(TenantMixin, Base):
    __tablename__ = "sales_by_product"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    op_date: Mapped[date] = mapped_column(Date, index=True)
    pos_name: Mapped[str] = mapped_column(String(128))
    units: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float | None] = mapped_column(Float)
    unit_price: Mapped[float | None] = mapped_column(Float)


class Bill(TenantMixin, Base):
    __tablename__ = "bills"
    __table_args__ = (UniqueConstraint("restaurant_id", "supplier", "number", name="uq_bill_restaurant_supplier_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supplier: Mapped[str] = mapped_column(String(128), index=True)
    number: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default="OK")
    price_flag_pct: Mapped[float | None] = mapped_column(Float)


class MeatEntry(TenantMixin, Base):
    __tablename__ = "meat_entry_register"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    supplier: Mapped[str] = mapped_column(String(128))
    sku: Mapped[str] = mapped_column(String(64), index=True)
    kg: Mapped[float] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    bill_id: Mapped[int | None] = mapped_column(ForeignKey("bills.id"))


class HaccpCheck(TenantMixin, Base):
    __tablename__ = "haccp_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    unit: Mapped[str] = mapped_column(String(64))
    kind: Mapped[HaccpKind] = mapped_column(Enum(HaccpKind))
    limit_c: Mapped[float] = mapped_column(Float)
    reading_c: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16))
    section_b_ok: Mapped[bool | None] = mapped_column(Boolean)
    corrective_action: Mapped[str | None] = mapped_column(Text)


# ============================================ Ingredientes, lotes y recetas
class Ingredient(TenantMixin, Base):
    """Ingrediente MADRE: «leche». Es lo que se escribe en las recetas.

    Debajo cuelgan los artículos concretos que se compran («leche entera marca
    X»), y debajo de cada artículo sus lotes con caducidad y precio. Así la
    receta no se toca cuando cambia la marca, y el stock sigue siendo el mismo
    ingrediente.
    """
    __tablename__ = "ingredients"
    __table_args__ = (UniqueConstraint("restaurant_id", "name", name="uq_ingredient_restaurant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    unit: Mapped[Unit] = mapped_column(Enum(Unit), default=Unit.KG)
    rotation: Mapped[Rotation] = mapped_column(Enum(Rotation), default=Rotation.FEFO)
    consumption: Mapped[ConsumptionMode] = mapped_column(Enum(ConsumptionMode),
                                                         default=ConsumptionMode.RECIPE)
    min_stock: Mapped[float | None] = mapped_column(Float)   # mínimo para avisar
    # El gramaje habitual en el plato: gramos, mililitros o unidades, según la
    # unidad base. Es lo que se escribe en cocina, y de ahí sale lo que cuesta
    # la ración.
    portion_g: Mapped[float | None] = mapped_column(Float)
    category: Mapped[str | None] = mapped_column(String(48))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["IngredientItem"]] = relationship(
        back_populates="ingredient", cascade="all, delete-orphan", order_by="IngredientItem.name")


class IngredientItem(TenantMixin, Base):
    """Artículo concreto atado a un ingrediente madre: la marca que se compra.

    Cambiar de proveedor es dar de alta otro artículo bajo la misma madre. Las
    recetas ni se enteran.
    """
    __tablename__ = "ingredient_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    brand: Mapped[str | None] = mapped_column(String(96))
    supplier: Mapped[str | None] = mapped_column(String(128))
    reference: Mapped[str | None] = mapped_column(String(64))       # código del proveedor
    pack_qty: Mapped[float] = mapped_column(Float, default=1.0)     # unidades base por envase
    last_cost: Mapped[float | None] = mapped_column(Float)          # último precio por unidad base
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    ingredient: Mapped["Ingredient"] = relationship(back_populates="items")
    lots: Mapped[list["IngredientLot"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="IngredientLot.expiry")


class IngredientLot(TenantMixin, Base):
    """Una entrada concreta de un artículo: su caducidad, su cantidad y su precio.

    El consumo FEFO compite entre todos los lotes de la misma madre, sea cual
    sea la marca: sale antes lo que antes caduca.
    """
    __tablename__ = "ingredient_lots"
    __table_args__ = (UniqueConstraint("restaurant_id", "serial", name="uq_lot_restaurant_serial"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("ingredient_items.id"), index=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    lot_code: Mapped[str | None] = mapped_column(String(48))
    serial: Mapped[str | None] = mapped_column(String(48), index=True)   # trazabilidad del corte
    parent_serial: Mapped[str | None] = mapped_column(String(16), index=True)  # primal de origen
    parent_lot: Mapped[str | None] = mapped_column(String(16))           # lote de recepción
    expiry: Mapped[date] = mapped_column(Date, index=True)
    received: Mapped[date | None] = mapped_column(Date)
    qty: Mapped[float] = mapped_column(Float)                  # cantidad recibida, en unidad base
    qty_remaining: Mapped[float] = mapped_column(Float)        # lo que queda
    unit_cost: Mapped[float] = mapped_column(Float)            # precio por unidad base
    stage: Mapped[FefoStage] = mapped_column(Enum(FefoStage), default=FefoStage.MASTER)
    # La etiqueta de la pieza: lo que hay que leer sin ir a buscar el despiece.
    pieces: Mapped[int | None] = mapped_column(Integer)          # cuántas piezas salieron
    # Peso MEDIO por pieza: los kilos reales entre las piezas. Un corte a mano
    # nunca sale exacto, así que lo que vale es el total pesado y el recuento.
    piece_weight_g: Mapped[float | None] = mapped_column(Float)
    nominal_piece_g: Mapped[float | None] = mapped_column(Float)  # el peso al que se apunta
    grade: Mapped[str | None] = mapped_column(String(32))        # MB9+, Prime, Choice…
    origin: Mapped[str | None] = mapped_column(String(32))       # AUS, USA, JPN…

    item: Mapped["IngredientItem"] = relationship(back_populates="lots")
    ingredient: Mapped["Ingredient"] = relationship()


class IngredientMovement(TenantMixin, Base):
    """Libro de movimientos: de dónde sale y adónde va cada gramo."""
    __tablename__ = "ingredient_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    lot_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient_lots.id"))
    date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[MovementKind] = mapped_column(Enum(MovementKind))
    qty: Mapped[float] = mapped_column(Float)                  # positiva entra, negativa sale
    cost: Mapped[float | None] = mapped_column(Float)          # valor del movimiento
    source: Mapped[str] = mapped_column(String(32))            # pos / manual / count / purchase
    source_ref: Mapped[str | None] = mapped_column(String(96))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Recipe(TenantMixin, Base):
    """Escandallo. Un plato que se vende, o una elaboración que usan otras recetas."""
    __tablename__ = "recipes"
    __table_args__ = (UniqueConstraint("restaurant_id", "code", name="uq_recipe_restaurant_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[RecipeKind] = mapped_column(Enum(RecipeKind), default=RecipeKind.DISH)
    category: Mapped[str | None] = mapped_column(String(48))
    portions: Mapped[int] = mapped_column(Integer, default=1)        # raciones que salen (DISH)
    yield_qty: Mapped[float | None] = mapped_column(Float)           # cuánto produce (PREP)
    yield_unit: Mapped[Unit | None] = mapped_column(Enum(Unit))
    sale_price: Mapped[float | None] = mapped_column(Float)          # PVP con impuestos
    vat_pct: Mapped[float] = mapped_column(Float, default=0.0)       # para el food cost neto
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    # dos claves apuntan a `recipes` (la receta y su elaboración), hay que decir cuál
    lines: Mapped[list["RecipeLine"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan",
        foreign_keys="RecipeLine.recipe_id", order_by="RecipeLine.sort_order")


class RecipeLine(Base):
    """Una línea del escandallo: un ingrediente base o una elaboración."""
    __tablename__ = "recipe_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), index=True)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredients.id"), index=True)
    sub_recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"), index=True)
    qty: Mapped[float] = mapped_column(Float)                  # peso NETO, el que va al plato
    waste_pct: Mapped[float] = mapped_column(Float, default=0.0)   # merma de limpieza sobre el bruto
    note: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    recipe: Mapped["Recipe"] = relationship(back_populates="lines", foreign_keys=[recipe_id])
    ingredient: Mapped["Ingredient"] = relationship()
    sub_recipe: Mapped["Recipe"] = relationship(foreign_keys=[sub_recipe_id])


class CountPeriod(str, enum.Enum):
    MONTHLY = "MONTHLY"   # el obligatorio, uno al mes en el día que se quiera
    SPOT = "SPOT"         # recuento puntual, cuando se quiera
    WEEKLY = "WEEKLY"     # si el local decide contar más a menudo


class CountStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"   # se empezó y se dejó: no ajusta nada


class CountItemKind(str, enum.Enum):
    CUT = "CUT"         # corte en cámara, con su serial
    PRIMAL = "PRIMAL"   # pieza entera sin despiezar


class MeatCount(TenantMixin, Base):
    """Inventario físico de carne: se cuenta pieza a pieza y se cuadra."""
    __tablename__ = "meat_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    period: Mapped[CountPeriod] = mapped_column(Enum(CountPeriod), default=CountPeriod.WEEKLY)
    status: Mapped[CountStatus] = mapped_column(Enum(CountStatus), default=CountStatus.OPEN)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)   # se contó todo
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["MeatCountLine"]] = relationship(
        back_populates="count", cascade="all, delete-orphan", order_by="MeatCountLine.label")


class MeatCountLine(Base):
    """Una pieza del inventario: lo que dice el sistema y lo que se ha contado."""
    __tablename__ = "meat_count_lines"
    __table_args__ = (UniqueConstraint("count_id", "serial", name="uq_count_serial"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("meat_counts.id"), index=True)
    kind: Mapped[CountItemKind] = mapped_column(Enum(CountItemKind))
    serial: Mapped[str] = mapped_column(String(48), index=True)
    label: Mapped[str] = mapped_column(String(160))
    expected_kg: Mapped[float] = mapped_column(Float, default=0.0)
    counted_kg: Mapped[float | None] = mapped_column(Float)       # None = sin contar
    counted_pieces: Mapped[int | None] = mapped_column(Integer)
    unit_cost: Mapped[float | None] = mapped_column(Float)
    outcome: Mapped[str | None] = mapped_column(String(16))       # resultado al cerrar
    note: Mapped[str | None] = mapped_column(Text)

    count: Mapped["MeatCount"] = relationship(back_populates="lines")


class PrimalPar(TenantMixin, Base):
    """Mínimo de primales por SKU. Si al cerrar el día quedan menos, se avisa."""
    __tablename__ = "primal_pars"
    __table_args__ = (UniqueConstraint("restaurant_id", "sku", name="uq_par_restaurant_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    min_pieces: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)


class DefrostEntry(TenantMixin, Base):
    """Descongelado: lo que se saca a descongelar y lo que queda al cerrar.

    Cada apunte va con el serial de la pieza, las piezas y el peso total. La
    diferencia entre lo que había, lo que se sacó y lo que queda al acabar el
    turno es lo que se ha consumido de verdad. Cruzado con el POS da el peso
    real por pieza vendida, que no es el teórico del escandallo.
    """
    __tablename__ = "defrost_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    shift: Mapped[str] = mapped_column(String(16), default="", index=True)
    kind: Mapped[DefrostKind] = mapped_column(Enum(DefrostKind), index=True)
    lot_serial: Mapped[str] = mapped_column(String(48), index=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), index=True)
    lot_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient_lots.id"))
    pieces: Mapped[int] = mapped_column(Integer, default=0)
    total_kg: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ingredient: Mapped["Ingredient"] = relationship()


class PosProduct(TenantMixin, Base):
    """Producto del POS → emplatado. Fuente única del mapeo.

    Según el POS, el artículo viene identificado por un código numérico o por
    su nombre. Se guardan los dos y se empareja por cualquiera de ellos.
    """
    __tablename__ = "pos_products"
    __table_args__ = (UniqueConstraint("restaurant_id", "pos_name", name="uq_pos_restaurant_name"),
                      UniqueConstraint("restaurant_id", "pos_code", name="uq_pos_restaurant_code"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pos_code: Mapped[str | None] = mapped_column(String(64), index=True)
    pos_name: Mapped[str] = mapped_column(String(160), index=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), index=True)

    recipe: Mapped["Recipe"] = relationship()


# ==================================== Orquestación y trazabilidad
class ChainCheckpoint(TenantMixin, Base):
    __tablename__ = "chain_checkpoints"
    __table_args__ = (UniqueConstraint("restaurant_id", "run_date", "step", name="uq_checkpoint_run_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    step: Mapped[str] = mapped_column(String(32))
    state: Mapped[SourceStatus] = mapped_column(Enum(SourceStatus))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    detail: Mapped[str | None] = mapped_column(Text)


class AuditLog(TenantMixin, Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64))
    table: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str | None] = mapped_column(Text)
