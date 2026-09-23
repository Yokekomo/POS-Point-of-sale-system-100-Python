"""Acceso a base de datos: SQLite para empezar, PostgreSQL cambiando la URL."""
from contextlib import contextmanager

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


def add_missing_columns() -> list[str]:
    """Añade a las tablas que ya existen las columnas y los índices que falten.

    `create_all` crea tablas, pero no toca las que ya están: una base de datos
    en marcha se quedaba sin las columnas añadidas después y reventaba al leer.
    Solo se añaden columnas que admiten vacío, que es lo único que se puede
    añadir sin inventarse un valor para las filas que ya existen.

    Y detrás van sus índices. Si no, la base de datos de una casa que viene de
    antes y la de una casa nueva tienen la misma forma pero no el mismo
    rendimiento: la columna está, pero buscar por ella recorre la tabla entera.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(_engine)
    tables = set(inspector.get_table_names())
    added: list[str] = []
    with _engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                kind = column.type.compile(dialect=_engine.dialect)
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}'))
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
            index.create(bind=_engine, checkfirst=True)
            added.append(index.name)
    return added


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
