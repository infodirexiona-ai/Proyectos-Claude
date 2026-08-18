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


def test_agrega_rut_titular_y_libera_el_rut_para_repetirse(db):
    """Simula una base creada antes de existir ``rut_titular`` en credenciales.

    En el esquema viejo, ``rut`` (con quién se inicia sesión) era único —
    impedía que un mismo representante guardara credenciales para más de una
    empresa. La migración tiene que: agregar la columna, rellenarla con el
    valor de ``rut`` para las filas existentes, y mover la restricción única a
    ``rut_titular``.
    """
    Base.metadata.drop_all(engine, tables=[Base.metadata.tables["credenciales"]])
    with engine.begin() as conexion:
        conexion.execute(
            text(
                """
                CREATE TABLE credenciales (
                    id INTEGER PRIMARY KEY,
                    rut VARCHAR(20),
                    alias VARCHAR(120),
                    clave_cifrada TEXT
                )
                """
            )
        )
        conexion.execute(text("CREATE UNIQUE INDEX ix_credenciales_rut ON credenciales (rut)"))
        conexion.execute(
            text("INSERT INTO credenciales (rut, alias, clave_cifrada) VALUES ('76192083-9', '', 'x')")
        )

    _migrar_columnas_nuevas()

    with engine.begin() as conexion:
        fila = conexion.execute(text("SELECT rut, rut_titular FROM credenciales")).one()
        assert fila.rut_titular == "76192083-9"  # se rellenó con el valor de "rut"

        # Ahora tiene que poder repetirse el mismo rut de acceso para otra empresa.
        conexion.execute(
            text(
                "INSERT INTO credenciales (rut, rut_titular, alias, clave_cifrada) "
                "VALUES ('76192083-9', '76655600-0', '', 'y')"
            )
        )
    with engine.begin() as conexion:
        total = conexion.execute(text("SELECT COUNT(*) FROM credenciales")).scalar()
        assert total == 2
