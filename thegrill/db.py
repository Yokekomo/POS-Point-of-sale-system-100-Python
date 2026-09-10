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
