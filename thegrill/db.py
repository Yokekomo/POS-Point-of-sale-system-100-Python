"""Acceso a base de datos: SQLite para empezar, PostgreSQL cambiando la URL."""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine = None
_SessionFactory = None


def init_engine(database_url: str = "sqlite:///thegrill.db"):
    global _engine, _SessionFactory
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    _engine = create_engine(database_url, connect_args=connect_args, future=True)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)
    return _engine


def create_all():
    from thegrill import models  # noqa: F401  (registra las tablas)
    if _engine is None:
        init_engine()
    Base.metadata.create_all(_engine)
    add_missing_columns()


def add_missing_columns() -> list[str]:
    """Añade a las tablas que ya existen las columnas nuevas que les falten.

    `create_all` crea tablas, pero no toca las que ya están: una base de datos
    en marcha se quedaba sin las columnas añadidas después y reventaba al leer.
    Solo se añaden columnas que admiten vacío, que es lo único que se puede
    añadir sin inventarse un valor para las filas que ya existen.
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
