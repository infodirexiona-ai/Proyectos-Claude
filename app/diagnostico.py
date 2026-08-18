"""Diagnóstico de arranque: qué falta para que el modo real funcione.

Pensado para correr en la máquina donde realmente se va a usar la app (este
mismo chequeo, ejecutado desde un entorno sin salida a internet, reportará
fallas de red que en tu equipo no deberían aparecer).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from .config import Settings, get_settings
from .crypto import ClaveCifradoAusente, cifrar, descifrar


@dataclass
class Chequeo:
    nombre: str
    ok: bool
    detalle: str = ""

    @property
    def icono(self) -> str:
        return "✓" if self.ok else "✗"

    def __str__(self) -> str:
        linea = f"{self.icono} {self.nombre}"
        return f"{linea}: {self.detalle}" if self.detalle else linea


def _chequear_config(settings: Settings) -> list[Chequeo]:
    chequeos = [Chequeo("Modo de operación", True, settings.modo)]
    if settings.modo == "demo":
        chequeos.append(
            Chequeo(
                "SII_CLAVE_CIFRADO",
                True,
                "no hace falta en modo demo",
            )
        )
        return chequeos

    if not settings.clave_cifrado:
        chequeos.append(
            Chequeo(
                "SII_CLAVE_CIFRADO",
                False,
                "falta. Genérala con: python -m app.cli generar-clave",
            )
        )
    else:
        try:
            token = cifrar("verificación", settings.clave_cifrado)
            assert descifrar(token, settings.clave_cifrado) == "verificación"
            chequeos.append(Chequeo("SII_CLAVE_CIFRADO", True, "cifra y descifra correctamente"))
        except (ClaveCifradoAusente, ValueError, AssertionError) as exc:
            chequeos.append(Chequeo("SII_CLAVE_CIFRADO", False, str(exc)))
    return chequeos


def _chequear_almacenamiento(settings: Settings) -> list[Chequeo]:
    chequeos = []
    try:
        settings.asegurar_directorios()
        chequeos.append(Chequeo("Directorio de descargas", True, str(settings.dir_descargas)))
    except OSError as exc:
        chequeos.append(Chequeo("Directorio de descargas", False, str(exc)))

    try:
        from .db import crear_esquema

        crear_esquema()
        chequeos.append(Chequeo("Base de datos", True, settings.db_url))
    except Exception as exc:  # noqa: BLE001 - se reporta cualquier fallo, no se decide aquí
        chequeos.append(Chequeo("Base de datos", False, f"{type(exc).__name__}: {exc}"))
    return chequeos


def _chequear_navegador(settings: Settings) -> Chequeo:
    if settings.ruta_navegador:
        existe = shutil.which(settings.ruta_navegador) or __import__("os").path.isfile(
            settings.ruta_navegador
        )
        if existe:
            return Chequeo("Navegador (Chromium)", True, f"SII_RUTA_NAVEGADOR = {settings.ruta_navegador}")
        return Chequeo(
            "Navegador (Chromium)",
            False,
            f"SII_RUTA_NAVEGADOR apunta a {settings.ruta_navegador!r}, que no existe",
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return Chequeo(
            "Navegador (Chromium)",
            False,
            "falta el paquete playwright. Instálalo con: pip install playwright",
        )

    try:
        with sync_playwright() as pw:
            navegador = pw.chromium.launch(headless=True)
            version = navegador.version
            navegador.close()
        return Chequeo("Navegador (Chromium)", True, f"instalado (versión {version})")
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de lanzamiento es "no instalado"
        # El propio mensaje de Playwright trae un banner ASCII de varias líneas;
        # sólo interesa la primera para el resumen del diagnóstico.
        motivo = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return Chequeo(
            "Navegador (Chromium)",
            False,
            f"no está instalado. Instálalo con: playwright install chromium [{motivo}]",
        )


def _chequear_red(entorno: str, timeout_s: float = 10.0) -> list[Chequeo]:
    import httpx

    from .sii import endpoints as ep

    objetivos = {
        "Portal de autenticación (zeusr.sii.cl)": ep.AUTH_URL,
        "Registro de Compras y Ventas": ep.base_rcv(entorno),
    }
    chequeos = []
    for nombre, url in objetivos.items():
        try:
            respuesta = httpx.get(url, timeout=timeout_s, follow_redirects=True)
            chequeos.append(Chequeo(nombre, respuesta.status_code < 500, f"HTTP {respuesta.status_code}"))
        except httpx.TimeoutException:
            chequeos.append(
                Chequeo(nombre, False, "sin respuesta (timeout). ¿Hay un firewall o proxy de por medio?")
            )
        except httpx.ConnectError as exc:
            chequeos.append(Chequeo(nombre, False, f"no se pudo conectar: {exc}"))
        except httpx.HTTPError as exc:
            chequeos.append(Chequeo(nombre, False, f"{type(exc).__name__}: {exc}"))
    return chequeos


def ejecutar_diagnostico(*, revisar_red: bool = True) -> list[Chequeo]:
    """Corre todos los chequeos y los devuelve en el orden en que hay que resolverlos."""
    settings = get_settings()
    chequeos = [*_chequear_config(settings), *_chequear_almacenamiento(settings)]
    if settings.modo == "demo":
        return chequeos
    chequeos.append(_chequear_navegador(settings))
    if revisar_red:
        from .sii import endpoints as ep

        chequeos.extend(_chequear_red(ep.PRODUCCION))
    return chequeos
