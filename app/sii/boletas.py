"""Emisión de Boletas de Honorarios Electrónicas.

Portal distinto del RCV y del MIPYME — páginas CGI en ``loa.sii.cl``. A
diferencia de esos dos, esto **escribe**: emite un documento tributario real
a nombre de otra persona, con efecto legal (se puede anular, pero requiere
que ambas partes — emisor y receptor — estén de acuerdo).

Reconstruido a partir de capturas de pantalla y "Ver código fuente" del
portal real (agosto 2026) — a diferencia del MIPYME, esto **todavía no se
probó en vivo contra el SII**. Antes de usarlo con datos reales, conviene
correrlo primero con ``confirmar=False`` (que llega hasta el borrador y no
hace nada irreversible) y revisar el resultado a mano.

Flujo, con "Por contribuyente" (formulario en blanco, sin depender de
destinatarios usados antes — sirve para emitir a RUT nuevos):

1. Elegir quién retiene el Pago Provisional Mensual (se deja la opción por
   defecto del portal: el receptor retiene).
2. Llenar el formulario con los datos del destinatario y las prestaciones.
3. "Confirmar Emisión" → pantalla de borrador ("ESTE ES UN BORRADOR, NO TIENE
   NINGUNA VALIDEZ").
4. Sólo si ``confirmar=True``: "Emitir Boleta de Honorarios Electrónica" —
   este último clic es el que de verdad genera el documento.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from ..rut import parse_rut
from . import endpoints as ep
from .portal import _importar_playwright, _login_en_pagina

log = logging.getLogger(__name__)


@dataclass
class Prestacion:
    descripcion: str
    monto: int


@dataclass
class SolicitudBoleta:
    """Datos de una boleta a emitir, típicamente una fila de una planilla."""

    rut_receptor: str
    nombre_receptor: str
    domicilio_receptor: str
    region: str
    comuna: str
    prestaciones: list[Prestacion] = field(default_factory=list)
    fecha: date | None = None


CAUSAS_ANULACION = {
    ep.BOLETAS_CAUSA_NO_PAGO: "No se efectuó el pago de los servicios por parte del receptor",
    ep.BOLETAS_CAUSA_NO_PRESTACION: "No se efectuó la prestación de servicios",
    ep.BOLETAS_CAUSA_ERROR_DIGITACION: "Error en la digitación",
}


@dataclass
class ResultadoBoleta:
    """Lo que se alcanzó a ver en el navegador — no un resultado estructurado.

    Como todavía no se verificó en vivo cómo lucen las pantallas de éxito, se
    captura el texto y una imagen de lo que haya en pantalla al terminar,
    para que quien emite o anula pueda revisarlo con sus propios ojos.
    """

    # True si se hizo clic en el botón final (emitir/anular); False si se
    # quedó en el borrador o la confirmación sin llegar a ese paso.
    confirmada: bool
    texto: str
    captura: bytes


def _cerrar_sesion(pagina, timeout_ms: int) -> None:
    """Best-effort: avisa al SII que cierre la sesión antes de cerrar el navegador.

    Ver el mismo problema documentado en ``app.sii.mipe._cerrar_sesion``: el
    SII limita cuántas sesiones autenticadas puede tener abiertas un RUT a la
    vez, y cerrar sólo el navegador no le avisa al servidor.
    """
    if pagina is None:
        return
    try:
        pagina.goto(ep.URL_LOGOUT, timeout=timeout_ms)
    except Exception:  # noqa: BLE001 - el logout es best-effort
        log.warning("No se pudo cerrar la sesión del emisor de boletas en el SII.")


def _completar_formulario(pagina, solicitud: SolicitudBoleta, timeout_ms: int) -> None:
    """Llena el formulario en blanco de emisión con los datos de la solicitud."""
    receptor = parse_rut(solicitud.rut_receptor)

    if solicitud.fecha:
        pagina.select_option(ep.BOLETAS_CAMPO_DIA, f"{solicitud.fecha.day:02d}")
        pagina.select_option(ep.BOLETAS_CAMPO_MES, f"{solicitud.fecha.month:02d}")
        pagina.select_option(ep.BOLETAS_CAMPO_ANIO, str(solicitud.fecha.year))

    pagina.fill(ep.BOLETAS_CAMPO_RUT_DESTINATARIO, str(receptor.cuerpo))
    pagina.fill(ep.BOLETAS_CAMPO_DV_DESTINATARIO, receptor.dv)
    pagina.fill(ep.BOLETAS_CAMPO_NOMBRE_DESTINATARIO, solicitud.nombre_receptor)
    pagina.fill(ep.BOLETAS_CAMPO_DOMICILIO_DESTINATARIO, solicitud.domicilio_receptor)

    pagina.select_option(ep.BOLETAS_CAMPO_REGION, label=solicitud.region)
    # La comuna se repuebla con JS al cambiar la región (sin ir al servidor);
    # esta espera es defensiva, para no seleccionar antes de que el navegador
    # termine de armar las opciones nuevas.
    pagina.wait_for_timeout(500)
    pagina.select_option(ep.BOLETAS_CAMPO_COMUNA, label=solicitud.comuna)

    if len(solicitud.prestaciones) > ep.BOLETAS_MAX_PRESTACIONES:
        raise ValueError(
            f"El SII no acepta más de {ep.BOLETAS_MAX_PRESTACIONES} prestaciones por boleta "
            f"(se pidieron {len(solicitud.prestaciones)})."
        )
    # El formulario trae 4 filas de entrada listas; el resto hay que agregarlas.
    filas_a_agregar = max(0, len(solicitud.prestaciones) - 4)
    for _ in range(filas_a_agregar):
        pagina.click(ep.BOLETAS_BOTON_MAS_PRESTACIONES)

    for numero, prestacion in enumerate(solicitud.prestaciones, start=1):
        pagina.fill(f"input[name='desc_prestacion_{numero}']", prestacion.descripcion)
        pagina.fill(f"input[name='valor_prestacion_{numero}']", str(prestacion.monto))


def emitir_boleta_honorarios(
    rut: str,
    clave_tributaria: str,
    solicitud: SolicitudBoleta,
    *,
    confirmar: bool = False,
    entorno: str = ep.PRODUCCION,
    headless: bool = True,
    timeout_ms: int = 45_000,
    user_agent: str = "Mozilla/5.0",
    ruta_navegador: str = "",
) -> ResultadoBoleta:
    """Llena y envía una Boleta de Honorarios Electrónica.

    Con ``confirmar=False`` (el default) llega hasta el borrador y **no hace
    nada irreversible** — ideal para revisar que los datos de la planilla se
    interpretaron bien antes de emitir de verdad. Con ``confirmar=True``
    hace el mismo recorrido completo, pero además hace clic en "Emitir
    Boleta de Honorarios Electrónica" al final, lo que sí genera el
    documento — no hay vuelta atrás salvo anulación (ver
    ``anular_boleta_honorarios``), y la anulación requiere que el receptor
    también esté de acuerdo.
    """
    _, _, sync_playwright = _importar_playwright()

    rut_obj = parse_rut(rut)

    with sync_playwright() as pw:
        opciones = {"headless": headless}
        if ruta_navegador:
            opciones["executable_path"] = ruta_navegador
        navegador = pw.chromium.launch(**opciones)
        pagina = None
        try:
            contexto = navegador.new_context(user_agent=user_agent)
            pagina = contexto.new_page()
            _login_en_pagina(pagina, rut_obj, clave_tributaria, entorno, timeout_ms)

            with pagina.expect_navigation(wait_until="networkidle", timeout=timeout_ms):
                pagina.goto(
                    f"{ep.BOLETAS_TIPO_RETENCION}"
                    f"?modo={ep.BOLETAS_TIPO_RETENCION_MODO_CONTRIBUYENTE}&dummy=1",
                    timeout=timeout_ms,
                )
            with pagina.expect_navigation(wait_until="networkidle", timeout=timeout_ms):
                pagina.get_by_role("button", name="Continuar").click()

            pagina.wait_for_selector(ep.BOLETAS_CAMPO_RUT_DESTINATARIO, timeout=timeout_ms)
            _completar_formulario(pagina, solicitud, timeout_ms)

            with pagina.expect_navigation(wait_until="networkidle", timeout=timeout_ms):
                pagina.click(ep.BOLETAS_BOTON_CONFIRMAR_EMISION)

            if not confirmar:
                return ResultadoBoleta(
                    confirmada=False, texto=pagina.inner_text("body"), captura=pagina.screenshot()
                )

            with pagina.expect_navigation(wait_until="networkidle", timeout=timeout_ms):
                pagina.click(ep.BOLETAS_BOTON_EMITIR)

            return ResultadoBoleta(
                confirmada=True, texto=pagina.inner_text("body"), captura=pagina.screenshot()
            )
        finally:
            _cerrar_sesion(pagina, timeout_ms)
            navegador.close()


def anular_boleta_honorarios(
    rut: str,
    clave_tributaria: str,
    folio: int,
    causa: str,
    *,
    confirmar: bool = False,
    entorno: str = ep.PRODUCCION,
    headless: bool = True,
    timeout_ms: int = 45_000,
    user_agent: str = "Mozilla/5.0",
    ruta_navegador: str = "",
) -> ResultadoBoleta:
    """Anula una boleta ya emitida. Requiere que el receptor también esté de
    acuerdo — si no, el SII no hace efectiva la anulación aunque el emisor
    la haya confirmado.

    ``causa`` debe ser una de las claves de ``CAUSAS_ANULACION``. Igual que
    ``emitir_boleta_honorarios``, con ``confirmar=False`` llega hasta la
    pantalla de confirmación y no hace nada irreversible.
    """
    if causa not in CAUSAS_ANULACION:
        raise ValueError(f"Causa de anulación inválida: {causa!r} (opciones: {list(CAUSAS_ANULACION)})")

    _, _, sync_playwright = _importar_playwright()

    rut_obj = parse_rut(rut)

    with sync_playwright() as pw:
        opciones = {"headless": headless}
        if ruta_navegador:
            opciones["executable_path"] = ruta_navegador
        navegador = pw.chromium.launch(**opciones)
        pagina = None
        try:
            contexto = navegador.new_context(user_agent=user_agent)
            pagina = contexto.new_page()
            _login_en_pagina(pagina, rut_obj, clave_tributaria, entorno, timeout_ms)

            pagina.goto(f"{ep.BOLETAS_ANULAR_PASO1}?dummy=1", timeout=timeout_ms)
            pagina.wait_for_selector(ep.BOLETAS_CAMPO_FOLIO_ANULAR, timeout=timeout_ms)
            pagina.fill(ep.BOLETAS_CAMPO_FOLIO_ANULAR, str(folio))
            pagina.check(f"{ep.BOLETAS_CAMPO_CAUSA_ANULACION}[value='{causa}']")

            with pagina.expect_navigation(wait_until="networkidle", timeout=timeout_ms):
                pagina.click(ep.BOLETAS_BOTON_CONTINUAR_ANULACION)

            if not confirmar:
                return ResultadoBoleta(
                    confirmada=False, texto=pagina.inner_text("body"), captura=pagina.screenshot()
                )

            with pagina.expect_navigation(wait_until="networkidle", timeout=timeout_ms):
                pagina.click(ep.BOLETAS_BOTON_CONFIRMAR_ANULACION)

            return ResultadoBoleta(
                confirmada=True, texto=pagina.inner_text("body"), captura=pagina.screenshot()
            )
        finally:
            _cerrar_sesion(pagina, timeout_ms)
            navegador.close()
