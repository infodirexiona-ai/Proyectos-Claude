"""Motor y sesiones de base de datos."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, inspect, text
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
    _migrar_columnas_nuevas()


def _migrar_columnas_nuevas() -> None:
    """Agrega columnas nuevas a tablas que ya existían de una versión anterior.

    ``create_all`` sólo crea tablas que faltan; no altera las que ya están.
    Como esta app no trae un sistema de migraciones aparte, las columnas que se
    van sumando entre versiones se agregan aquí a mano, sin tocar los datos.
    """
    inspector = inspect(engine)
    tablas = inspector.get_table_names()

    if "documentos" in tablas:
        columnas = {c["name"] for c in inspector.get_columns("documentos")}
        if "detalle" not in columnas:
            with engine.begin() as conexion:
                conexion.execute(text("ALTER TABLE documentos ADD COLUMN detalle JSON"))

    if "credenciales" in tablas:
        columnas = {c["name"] for c in inspector.get_columns("credenciales")}
        if "rut_titular" not in columnas:
            with engine.begin() as conexion:
                # Antes, una credencial sólo servía para el RUT con el que se
                # inicia sesión. Al agregar rut_titular (la empresa cuyos
                # documentos se piden, que puede ser otro RUT si quien firma es
                # un representante legal), las credenciales existentes quedan
                # apuntando a sí mismas, tal como funcionaban hasta ahora.
                conexion.execute(text("ALTER TABLE credenciales ADD COLUMN rut_titular VARCHAR(20)"))
                conexion.execute(text("UPDATE credenciales SET rut_titular = rut WHERE rut_titular IS NULL"))
                # El esquema anterior exigía "rut" único: eso impedía que una misma
                # persona (rut de acceso) guardara credenciales para más de una
                # empresa. Se cambia esa restricción a rut_titular, que es la que
                # tiene que ser única de verdad.
                conexion.execute(text("DROP INDEX IF EXISTS ix_credenciales_rut"))
                conexion.execute(text("CREATE INDEX IF NOT EXISTS ix_credenciales_rut ON credenciales (rut)"))
                conexion.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_credenciales_rut_titular "
                        "ON credenciales (rut_titular)"
                    )
                )


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
