"""Detalle de ventas emitidas con el Sistema de Facturación Gratuita del SII.

A diferencia del RCV (una SPA Angular sobre ``www4.sii.cl``), el "MIPYME" es un
conjunto de páginas CGI clásicas en ``www1.sii.cl`` — más simples, pero con dos
particularidades que obligan a manejarlas con un navegador real en vez de sólo
``httpx``:

1. El botón "Archivo Respaldo" dispara un reCAPTCHA invisible antes de armar
   la URL de descarga.
2. El servidor rechaza de plano cualquier búsqueda que reúna más de
   ``MIPE_MAX_DOCUMENTOS_POR_DESCARGA`` documentos (lo avisa con un diálogo
   nativo del navegador, no con una respuesta HTTP de error), así que hay que
   partir el rango de fechas pedido en trozos más chicos y reintentar.
3. Si el RUT autenticado representa a más de una empresa (el representante
   legal de varias sociedades, por ejemplo), el SII exige elegir con cuál
   operar antes de dejar ver cualquier documento — si no, cualquier URL del
   MIPYME redirige de vuelta al menú general. Se resuelve mandando el mismo
   ``POST`` que manda el formulario de selección, con el RUT de la empresa
   como valor.

Sólo sirve para documentos que el propio contribuyente emitió con el
facturador gratuito del SII (VENTA). No aplica a documentos recibidos ni a
documentos emitidos con un facturador privado (Nubox, Bsale, etc.): ahí el
detalle vive en la plataforma de ese proveedor, no en el SII.

Flujo verificado a mano contra el portal real en agosto de 2026, a diferencia
del resto de ``app/sii`` (portado de investigación de terceros sin poder
probarlo en vivo desde este entorno).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..rut import parse_rut
from . import endpoints as ep
from .errors import SiiError
from .mipe_parser import DocumentoEmitido, parsear_respaldo_mipyme
from .portal import _importar_playwright, _login_en_pagina

log = logging.getLogger(__name__)

# Límite defensivo: evita que un día con más de 20 documentos (que no se puede
# partir más) o un error inesperado hagan que el trabajo intente para siempre.
_MAX_INTENTOS = 400


@dataclass
class ResultadoDetalleVentas:
    documentos: list[DocumentoEmitido] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _url_busqueda(fecha_desde: date, fecha_hasta: date) -> str:
    return (
        f"{ep.MIPE_ADMIN_DOCS}?RUT_RECP=&FOLIO=&RZN_SOC="
        f"&FEC_DESDE={fecha_desde.isoformat()}&FEC_HASTA={fecha_hasta.isoformat()}"
        f"&TPO_DOC=&ESTADO=&ORDEN=&NUM_PAG=1"
    )


def _intentar_descarga(pagina, fecha_desde: date, fecha_hasta: date, timeout_ms: int):
    """Busca el rango de fechas y descarga el XML de respaldo.

    Devuelve ``(contenido_xml, None)`` si hubo descarga, o
    ``(None, mensaje_de_error)`` si el SII mostró un diálogo en vez de bajar el
    archivo (típicamente "demasiados documentos").
    """
    _, PlaywrightTimeout, _ = _importar_playwright()

    pagina.goto(_url_busqueda(fecha_desde, fecha_hasta), timeout=timeout_ms)

    mensaje_dialogo: dict[str, str | None] = {"texto": None}

    def _manejar_dialogo(dialogo):
        mensaje_dialogo["texto"] = dialogo.message
        dialogo.accept()

    pagina.on("dialog", _manejar_dialogo)
    try:
        try:
            with pagina.expect_download(timeout=timeout_ms) as descarga_info:
                pagina.click(ep.MIPE_BOTON_RESPALDO)
        except PlaywrightTimeout:
            if mensaje_dialogo["texto"]:
                return None, mensaje_dialogo["texto"]
            raise SiiError(
                f"La descarga del detalle de ventas ({fecha_desde} a {fecha_hasta}) no respondió a tiempo."
            ) from None

        descarga = descarga_info.value
        ruta_temporal = descarga.path()
        if ruta_temporal is None:
            raise SiiError("La descarga del detalle de ventas falló en el navegador.")
        return ruta_temporal.read_bytes(), None
    finally:
        pagina.remove_listener("dialog", _manejar_dialogo)


def _es_error_demasiados_documentos(mensaje: str) -> bool:
    return ep.MIPE_TEXTO_DEMASIADOS_DOCUMENTOS in mensaje.lower()


def _seleccionar_empresa(contexto, rut_titular_obj, timeout_ms: int) -> None:
    """Elige con qué empresa operar en el MIPYME.

    Se hace siempre, no sólo cuando el RUT autenticado representa a más de
    una empresa: seleccionar la única que tiene no rompe nada, y evita tener
    que detectar si el SII mostró o no la pantalla de selección. Se manda por
    ``contexto.request`` (comparte las cookies de la sesión) en vez de
    navegar a la página del formulario y llenarlo, porque el ``POST`` es
    exactamente lo que hace ese formulario y así nos ahorramos un viaje.
    """
    contexto.request.post(
        ep.MIPE_SELECCIONAR_EMPRESA,
        form={
            "DESDE_DONDE_URL": ep.MIPE_SELECCION_EMPRESA_ORIGEN,
            "RUT_EMP": str(rut_titular_obj),
        },
        timeout=timeout_ms,
    )


def descargar_detalle_ventas(
    rut: str,
    rut_titular: str,
    clave_tributaria: str,
    fecha_desde: date,
    fecha_hasta: date,
    *,
    entorno: str = ep.PRODUCCION,
    headless: bool = True,
    timeout_ms: int = 45_000,
    user_agent: str = "Mozilla/5.0",
    ruta_navegador: str = "",
) -> ResultadoDetalleVentas:
    """Descarga el detalle (glosa, cantidad, precio) de las ventas del rango.

    ``rut`` es con quién se inicia sesión; ``rut_titular`` es la empresa cuyos
    documentos se piden — el SII siempre autentica a una persona natural, que
    puede representar una o más empresas, y exige elegir con cuál operar
    antes de dejar ver cualquier documento.

    Parte el rango en tramos cada vez más angostos cada vez que el SII
    rechaza la descarga por traer demasiados documentos, hasta que cada tramo
    entra bajo el límite. Un tramo de un solo día que aun así se rechace queda
    registrado en ``avisos`` y se salta, en vez de hacer fallar todo el resto.
    """
    PlaywrightError, _, sync_playwright = _importar_playwright()

    rut_obj = parse_rut(rut)
    rut_titular_obj = parse_rut(rut_titular)
    resultado = ResultadoDetalleVentas()
    documentos_por_clave: dict[tuple[int, int], DocumentoEmitido] = {}

    with sync_playwright() as pw:
        opciones = {"headless": headless}
        if ruta_navegador:
            opciones["executable_path"] = ruta_navegador
        navegador = pw.chromium.launch(**opciones)
        try:
            contexto = navegador.new_context(user_agent=user_agent, accept_downloads=True)
            pagina = contexto.new_page()
            _login_en_pagina(pagina, rut_obj, clave_tributaria, entorno, timeout_ms)
            _seleccionar_empresa(contexto, rut_titular_obj, timeout_ms)

            pendientes: list[tuple[date, date]] = [(fecha_desde, fecha_hasta)]
            intentos = 0
            while pendientes:
                intentos += 1
                if intentos > _MAX_INTENTOS:
                    resultado.avisos.append(
                        "Se alcanzó el límite de reintentos partiendo el rango de fechas; "
                        "quedaron tramos sin descargar."
                    )
                    break

                desde, hasta = pendientes.pop()
                try:
                    contenido, error = _intentar_descarga(pagina, desde, hasta, timeout_ms)
                except PlaywrightError as exc:
                    resultado.avisos.append(f"Error de navegador en {desde}–{hasta}: {exc}")
                    continue

                if contenido is not None:
                    for doc in parsear_respaldo_mipyme(contenido):
                        documentos_por_clave[doc.clave] = doc
                    continue

                if error and _es_error_demasiados_documentos(error) and desde < hasta:
                    dias = (hasta - desde).days
                    medio = desde + timedelta(days=dias // 2)
                    pendientes.append((medio + timedelta(days=1), hasta))
                    pendientes.append((desde, medio))
                    continue

                resultado.avisos.append(
                    f"No se pudo descargar el detalle de ventas del {desde}"
                    f"{'' if desde == hasta else f' al {hasta}'}: {error or 'error desconocido'}"
                )
        finally:
            navegador.close()

    resultado.documentos = sorted(documentos_por_clave.values(), key=lambda d: (d.tipo_doc, d.folio))
    return resultado
