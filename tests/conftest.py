"""Configura un entorno aislado antes de importar la aplicación."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sii-test-"))

os.environ.setdefault("SII_MODO", "demo")
os.environ["SII_DB_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["SII_DIR_DESCARGAS"] = str(_TMP / "descargas")
os.environ["SII_CLAVE_CIFRADO"] = "clave-de-pruebas-no-usar-en-produccion"
os.environ["SII_PAUSA_ENTRE_LLAMADAS"] = "0"

import pytest  # noqa: E402

from app.db import SessionLocal, crear_esquema, engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture
def db():
    Base.metadata.drop_all(engine)
    crear_esquema()
    sesion = SessionLocal()
    try:
        yield sesion
    finally:
        sesion.close()


@pytest.fixture
def cliente():
    from fastapi.testclient import TestClient

    from app.web.main import app

    Base.metadata.drop_all(engine)
    crear_esquema()
    with TestClient(app) as c:
        yield c
