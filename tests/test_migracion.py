"""La migración liviana que agrega columnas nuevas a bases ya existentes."""

from sqlalchemy import inspect, text

from app.db import _migrar_columnas_nuevas, engine
from app.models import Base


def test_agrega_columna_detalle_a_tabla_vieja(db):
    """Simula una base creada antes de que existiera la columna ``detalle``."""
    Base.metadata.drop_all(engine, tables=[Base.metadata.tables["documentos"]])
    with engine.begin() as conexion:
        conexion.execute(
            text(
                """
                CREATE TABLE documentos (
                    id INTEGER PRIMARY KEY,
                    rut_titular VARCHAR(20),
                    operacion VARCHAR(10),
                    periodo VARCHAR(6),
                    tipo_doc INTEGER,
                    folio INTEGER
                )
                """
            )
        )

    columnas_antes = {c["name"] for c in inspect(engine).get_columns("documentos")}
    assert "detalle" not in columnas_antes

    _migrar_columnas_nuevas()

    columnas_despues = {c["name"] for c in inspect(engine).get_columns("documentos")}
    assert "detalle" in columnas_despues


def test_migracion_es_idempotente(db):
    """Correrla dos veces (como pasa en cada arranque) no debe fallar."""
    _migrar_columnas_nuevas()
    _migrar_columnas_nuevas()
    columnas = {c["name"] for c in inspect(engine).get_columns("documentos")}
    assert "detalle" in columnas
