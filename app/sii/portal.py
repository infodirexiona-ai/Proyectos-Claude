"""Autenticación en el portal del SII con RUT + clave tributaria.

El login se hace con un navegador (Playwright) porque el portal de producción
usa salas de espera Queue-it y desafíos JavaScript que rompen un POST directo.
Una vez autenticados, extraemos las cookies y seguimos con ``httpx``, que es
mucho más rápido para las decenas de llamadas del RCV.

Flujo portado desde `emisso-ai/emisso-sii` (MIT).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..config import RAIZ
from ..rut import Rut, parse_rut
from . import endpoints as ep
from .errors import CredencialesInvalidas, SiiError

RUTA_CAPTURA_LOGIN_FALLIDO = RAIZ / "data" / "diagnostico" / "ultimo_login_fallido.png"

log = logging.getLogger(__name__)


@dataclass
class SesionPortal:
    """Sesión autenticada reutilizable contra los servicios del SII."""

    rut: Rut
    entorno: str
    cliente: httpx.Client
    token: str = ""
    cookies_crudas: list[dict] = field(default_factory=list)

    @property
    def base_rcv(self) -> str:
        return ep.base_rcv(self.entorno)

    def cerrar(self) -> None:
        """Cierra la sesión en el SII (limita sesiones concurrentes por RUT)."""
        try:
            self.cliente.get(ep.URL_LOGOUT, timeout=10.0)
        except httpx.HTTPError:  # el logout es best-effort
            log.debug("Logout del portal falló; se ignora", exc_info=True)
        finally:
            self.cliente.close()

    def __enter__(self) -> SesionPortal:
        return self

    def __exit__(self, *_exc) -> None:
        self.cerrar()


def _cliente_httpx(cookies: list[dict], user_agent: str, timeout_ms: int) -> httpx.Client:
    jar = httpx.Cookies()
    for cookie in cookies:
        jar.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain", "").lstrip("."),
            path=cookie.get("path", "/"),
        )
    return httpx.Client(
        cookies=jar,
        headers={"User-Agent": user_agent},
        timeout=timeout_ms / 1000,
        follow_redirects=True,
    )


def _importar_playwright():
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - depende de la instalación
        raise SiiError(
            "Falta Playwright. Instálalo con: pip install playwright && playwright install chromium"
        ) from exc
    return PlaywrightError, PlaywrightTimeout, sync_playwright


def _login_en_pagina(pagina, rut_obj: Rut, clave_tributaria: str, entorno: str, timeout_ms: int):
    """Completa el formulario de RUT + clave en una página ya abierta.

    Lanza si el SII rechaza las credenciales; no cierra nada por su cuenta,
    para que quien llama decida si sigue usando la página (MIPYME) o solo
    necesita las cookies (RCV).
    """
    _, PlaywrightTimeout, _ = _importar_playwright()

    pagina.goto(ep.url_login(entorno), timeout=timeout_ms)
    try:
        pagina.wait_for_selector(ep.SEL_RUT, timeout=timeout_ms)
    except PlaywrightTimeout as exc:
        raise SiiError(
            "No apareció el formulario de login del SII. El portal puede estar "
            "en mantención o mostrando una sala de espera."
        ) from exc

    pagina.fill(ep.SEL_RUT, str(rut_obj))
    pagina.fill(ep.SEL_CLAVE, clave_tributaria)
    try:
        with pagina.expect_navigation(wait_until="networkidle", timeout=timeout_ms):
            pagina.click(ep.SEL_BOTON)
    except PlaywrightTimeout:
        # Algunas versiones del portal navegan por JS sin disparar el evento.
        pagina.wait_for_load_state("networkidle", timeout=timeout_ms)

    url_final = pagina.url
    if "IngresoRutClave" in url_final or "CAutInicio" in url_final:
        RUTA_CAPTURA_LOGIN_FALLIDO.parent.mkdir(parents=True, exist_ok=True)
        try:
            pagina.screenshot(path=str(RUTA_CAPTURA_LOGIN_FALLIDO))
        except Exception:  # noqa: BLE001 - la captura es un apoyo, no debe tapar el error real
            log.warning("No se pudo guardar la captura de pantalla del login fallido.")
        raise CredencialesInvalidas(
            "El SII rechazó el acceso: revisa el RUT y la clave tributaria (o el portal pidió un captcha). "
            f"Se guardó una captura de pantalla en {RUTA_CAPTURA_LOGIN_FALLIDO}."
        )


def iniciar_sesion(
    rut: str,
    clave_tributaria: str,
    *,
    entorno: str = ep.PRODUCCION,
    headless: bool = True,
    timeout_ms: int = 45_000,
    user_agent: str = "Mozilla/5.0",
    ruta_navegador: str = "",
) -> SesionPortal:
    """Autentica en el portal y devuelve una sesión lista para consultar el RCV.

    Cierra el navegador apenas obtiene las cookies: el RCV se consulta después
    con ``httpx``, mucho más rápido para las decenas de llamadas que hacen
    falta. Para flujos que necesitan seguir interactuando con la página
    después de autenticar (como MIPYME, por el reCAPTCHA en la descarga), usa
    ``app.sii.mipe`` en su lugar.

    La clave tributaria sólo viaja al formulario del SII; nunca se registra en
    los logs ni se guarda en disco desde aquí.
    """
    PlaywrightError, _, sync_playwright = _importar_playwright()

    rut_obj = parse_rut(rut)
    log.info("Iniciando sesión en el SII para %s", rut_obj.formateado)

    with sync_playwright() as pw:
        opciones = {"headless": headless}
        if ruta_navegador:
            opciones["executable_path"] = ruta_navegador
        navegador = pw.chromium.launch(**opciones)
        try:
            contexto = navegador.new_context(user_agent=user_agent)
            pagina = contexto.new_page()
            _login_en_pagina(pagina, rut_obj, clave_tributaria, entorno, timeout_ms)

            cookies = contexto.cookies()
            if not cookies:
                raise SiiError("El login no dejó cookies: no se pudo abrir la sesión.")

            token = next((c["value"] for c in cookies if c["name"] == "TOKEN"), "")
            if not token:
                log.warning("No se encontró la cookie TOKEN; el RCV podría rechazar las consultas")

            cliente = _cliente_httpx(cookies, user_agent, timeout_ms)
            return SesionPortal(
                rut=rut_obj,
                entorno=entorno,
                cliente=cliente,
                token=token,
                cookies_crudas=cookies,
            )
        except PlaywrightError as exc:
            raise SiiError(f"Error del navegador durante el login: {exc}") from exc
        finally:
            navegador.close()
