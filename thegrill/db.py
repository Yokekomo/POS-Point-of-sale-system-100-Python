"""Acceso a base de datos: SQLite para empezar, PostgreSQL cambiando la URL."""
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = None
_SessionFactory = None


def init_engine(database_url: str = "sqlite:///thegrill.db"):
    global _engine, _SessionFactory
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    if database_url.startswith("sqlite"):
        # Treinta segundos de espera: en una casa hay dos o tres personas
        # escribiendo a la vez, no doscientas. Lo que no puede pasar es que a
        # la que llega segunda le salga un error rojo porque la otra estaba
        # guardando en ese momento.
        connect_args["timeout"] = 30.0
    _engine = create_engine(database_url, connect_args=connect_args, future=True)
    if database_url.startswith("sqlite"):
        event.listen(_engine, "connect", _sqlite_ready)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)
    # Ningún importe con milésimas ni ningún peso con miligramos llega al
    # disco. Se engancha aquí, que es por donde pasa todo lo que se guarda.
    from thegrill.web import exacto
    exacto.enganchar(Session)
    return _engine


def _sqlite_ready(connection, _record) -> None:
    """Pone la base de datos en modo de varios a la vez.

    De serie, SQLite deja escribir a uno y a los demás les cierra la puerta
    entera: mientras el del obrador guarda un despiece, el del local que está
    mirando el inventario se lleva un error. Con WAL, el que lee no molesta al
    que escribe, y los que escriben hacen cola en vez de rebotar.
    """
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass          # en memoria no hay WAL, y no pasa nada: ahí no hay dos
    finally:
        cursor.close()


def create_all():
    from thegrill import models  # noqa: F401  (registra las tablas)
    if _engine is None:
        init_engine()
    Base.metadata.create_all(_engine)
    add_missing_columns()


@dataclass(frozen=True)
class Pendiente:
    """Algo que la actualización no ha podido poner, y por qué.

    Una migración que se calla es peor que una que falla: el programa arranca,
    parece que todo está bien, y la columna que falta se descubre tres semanas
    después con un error raro en una pantalla que no tiene nada que ver. Esto
    es lo que hay que poder leer el día de la actualización.
    """
    que: str          # «restaurants.day_cut_hour», «uq_site_restaurant_name»
    porque: str       # en castellano, para quien lo lee
    mano: str = ""    # la orden que lo arregla, si la hay


# Lo que no se pudo en la última actualización. Se vacía en cada pasada y lo
# lee el panel de la plataforma: es el parte de la migración.
PENDIENTES: list[Pendiente] = []


def pendientes() -> list[Pendiente]:
    """Lo que quedó sin poner la última vez que se actualizó la base."""
    return list(PENDIENTES)


def _literal(column, dialect) -> str | None:
    """El valor con el que se rellenan las filas que ya existen.

    Una columna obligatoria no se puede añadir a una tabla con datos sin decir
    qué va en las filas de antes. Si el modelo trae un valor por defecto, ese
    es. Si lo que trae es una función —una fecha de ahora, un código al azar—,
    no hay un valor que valga para todas y se dice.
    """
    por_defecto = column.default
    if por_defecto is None or getattr(por_defecto, "is_callable", False):
        return None
    valor = getattr(por_defecto, "arg", None)
    if callable(valor):
        return None
    if isinstance(valor, bool):
        return "true" if dialect.name == "postgresql" else "1"
    if isinstance(valor, (int, float)):
        return repr(valor)
    valor = getattr(valor, "value", valor)      # las listas cerradas
    if valor is None:
        return None
    escapado = str(valor).replace("'", "''")
    return f"'{escapado}'"


def add_missing_columns() -> list[str]:
    """Añade a las tablas que ya existen las columnas y las reglas que falten.

    `create_all` crea tablas, pero no toca las que ya están: una base de datos
    en marcha se quedaba sin las columnas añadidas después y reventaba al leer.

    Las que admiten vacío se añaden y ya está. Las **obligatorias** también,
    siempre que el modelo diga con qué se rellenan las filas de antes: se
    añaden con ese valor puesto, que es lo que hace una migración de verdad.
    Cuando el valor por defecto es una función —una fecha de ahora, un código
    al azar— no hay uno que valga para todas las filas y la columna se queda
    fuera, pero **se dice** en el parte en vez de callarlo.

    Detrás van los índices y las reglas de «no puede haber dos iguales». Si no,
    la base de una casa que viene de antes y la de una casa nueva tienen la
    misma forma pero no las mismas garantías: el mismo número de pieza se puede
    repetir en una y no en la otra, y eso no se ve hasta que pasa.
    """
    from sqlalchemy import UniqueConstraint, inspect, text
    PENDIENTES.clear()
    inspector = inspect(_engine)
    tables = set(inspector.get_table_names())
    added: list[str] = []
    dialect = _engine.dialect
    with _engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            vacia = _esta_vacia(connection, table.name)
            for column in table.columns:
                if column.name in existing:
                    continue
                kind = column.type.compile(dialect=dialect)
                orden = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}'
                if not column.nullable:
                    relleno = _literal(column, dialect)
                    if relleno is None and not vacia:
                        PENDIENTES.append(Pendiente(
                            f"{table.name}.{column.name}",
                            "es obligatoria y no dice con qué rellenar las filas "
                            "que ya existen",
                            f"{orden} NOT NULL DEFAULT <valor>"))
                        continue
                    orden += " NOT NULL" + (f" DEFAULT {relleno}" if relleno else "")
                try:
                    connection.execute(text(orden))
                except Exception as e:                       # noqa: BLE001
                    PENDIENTES.append(Pendiente(
                        f"{table.name}.{column.name}", str(e)[:200], orden))
                    continue
                added.append(f"{table.name}.{column.name}")

    # Los índices, después de las columnas: uno nuevo suele venir con la suya.
    inspector = inspect(_engine)
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        existing = {i["name"] for i in inspector.get_indexes(table.name)}
        columns = {c["name"] for c in inspector.get_columns(table.name)}
        for index in table.indexes:
            if index.name in existing:
                continue
            if not {c.name for c in index.columns} <= columns:
                continue
            try:
                index.create(bind=_engine, checkfirst=True)
            except Exception as e:                           # noqa: BLE001
                PENDIENTES.append(Pendiente(index.name, str(e)[:200]))
                continue
            added.append(index.name)

        # Y las reglas de «no puede haber dos iguales». No se pueden añadir a
        # una tabla que ya existe —ni SQLite ni casi nadie deja—, pero un
        # índice único hace exactamente lo mismo. Si los datos de la casa ya
        # tienen un repetido, la orden falla: eso no se arregla solo y hay que
        # decirlo, porque significa que algo se duplicó de verdad.
        # Por las columnas que cubren y no por el nombre: SQLite guarda las
        # reglas que vienen en el `CREATE TABLE` como un índice suyo con otro
        # nombre, y buscando por nombre se añadiría un índice repetido en cada
        # arranque para no garantizar nada nuevo.
        ya_cubierto = [frozenset(i["column_names"] or ())
                       for i in inspector.get_indexes(table.name) if i["unique"]]
        for regla in table.constraints:
            if not isinstance(regla, UniqueConstraint) or not regla.name:
                continue
            campos = [c.name for c in regla.columns]
            if frozenset(campos) in ya_cubierto or regla.name in existing:
                continue
            if not set(campos) <= columns:
                continue
            comillas = ", ".join(f'"{c}"' for c in campos)
            orden = (f'CREATE UNIQUE INDEX IF NOT EXISTS "{regla.name}" '
                     f'ON "{table.name}" ({comillas})')
            try:
                with _engine.begin() as connection:
                    connection.execute(text(orden))
            except Exception as e:                           # noqa: BLE001
                PENDIENTES.append(Pendiente(
                    regla.name,
                    "hay filas repetidas y por eso no se puede poner la regla: "
                    + str(e)[:160], orden))
                continue
            added.append(regla.name)

    _valores_nuevos_de_las_listas(added)
    return added


def _esta_vacia(connection, tabla: str) -> bool:
    """Una tabla sin filas admite cualquier columna obligatoria."""
    from sqlalchemy import text
    try:
        return connection.execute(
            text(f'SELECT 1 FROM "{tabla}" LIMIT 1')).first() is None
    except Exception:                                        # noqa: BLE001
        return False


def _valores_nuevos_de_las_listas(added: list[str]) -> None:
    """En PostgreSQL, los valores nuevos de una lista cerrada hay que añadirlos.

    En SQLite una lista cerrada es texto y no hay nada que hacer. En PostgreSQL
    es un tipo de verdad, y un valor nuevo —un estado de pieza, una forma de
    rotación— no existe hasta que alguien lo añade: sin esto, la casa se
    actualiza, parece que va, y revienta el día que alguien usa ese estado.
    """
    from sqlalchemy import Enum as SAEnum
    from sqlalchemy import text
    if _engine.dialect.name != "postgresql":
        return
    vistos: set[str] = set()
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            tipo = column.type
            if not isinstance(tipo, SAEnum) or not tipo.name or tipo.name in vistos:
                continue
            vistos.add(tipo.name)
            for valor in tipo.enums:
                orden = (f"ALTER TYPE \"{tipo.name}\" ADD VALUE IF NOT EXISTS "
                         f"'{valor}'")
                try:
                    # Fuera de transacción: PostgreSQL no deja añadir valores a
                    # un tipo dentro de una.
                    with _engine.connect().execution_options(
                            isolation_level="AUTOCOMMIT") as conexion:
                        conexion.execute(text(orden))
                except Exception as e:                       # noqa: BLE001
                    PENDIENTES.append(Pendiente(
                        f"{tipo.name}.{valor}", str(e)[:200], orden))
                    continue
                added.append(f"{tipo.name}.{valor}")


@contextmanager
def session_scope():
    if _SessionFactory is None:
        init_engine()
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
