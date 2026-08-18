"""Configuración de la aplicación, leída desde variables de entorno o ``.env``."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="SII_", extra="ignore", env_file_encoding="utf-8"
    )

    # --- Modo de operación -------------------------------------------------
    # "real": se conecta al portal del SII con Playwright.
    # "demo": usa datos de ejemplo, útil para probar la app sin credenciales.
    modo: str = "real"

    # --- Almacenamiento ----------------------------------------------------
    db_url: str = f"sqlite:///{RAIZ / 'data' / 'sii.db'}"
    dir_descargas: Path = RAIZ / "data" / "descargas"

    # --- Credenciales ------------------------------------------------------
    # Clave de cifrado (Fernet) para guardar la clave tributaria en la base.
    # Genérala con:  python -m app.crypto
    clave_cifrado: str = ""
    # Credenciales por defecto (opcionales: también se pueden cargar desde la UI).
    rut: str = ""
    clave_tributaria: str = ""

    # --- Navegador / red ---------------------------------------------------
    headless: bool = True
    # Ruta a un Chromium ya instalado. Útil en contenedores donde el navegador
    # viene con la imagen y no se quiere ejecutar "playwright install".
    ruta_navegador: str = ""
    timeout_ms: int = 45_000
    reintentos: int = 3
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    # --- Sincronización ----------------------------------------------------
    # Pausa entre llamadas al SII, en segundos, para no saturar el servicio.
    pausa_entre_llamadas: float = 0.8

    def asegurar_directorios(self) -> None:
        self.dir_descargas.mkdir(parents=True, exist_ok=True)
        if self.db_url.startswith("sqlite:///"):
            Path(self.db_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.asegurar_directorios()
    return settings
