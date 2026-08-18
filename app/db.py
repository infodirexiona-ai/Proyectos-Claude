"""Motor y sesiones de base de datos."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_settings = get_settings()

_kwargs = {"future": True}
if _settings.db_url.startswith("sqlite"):
    # La sincronización corre en hilos aparte del servidor web.
    _kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(_settings.db_url, **_kwargs)


@event.listens_for(engine, "connect")
def _configurar_sqlite(dbapi_connection, _record):  # pragma: no cover - hook del driver
    if _settings.db_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def crear_esquema() -> None:
    from .models import Base

    Base.metadata.create_all(engine)


@contextmanager
def sesion_db() -> Iterator[Session]:
    """Sesión transaccional: commit al salir bien, rollback si algo falla."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
