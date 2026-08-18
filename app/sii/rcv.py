"""Cliente del Registro de Compras y Ventas (RCV).

El RCV es una SPA Angular sobre un backend Java (``FacadeService``). Todas las
llamadas envuelven el payload en un sobre ``{metaData, data}`` y requieren que
antes se haya inicializado la sesión de servidor.

Flujo y endpoints portados desde `emisso-ai/emisso-sii` (MIT).
"""

from __future__ import annotations

import logging
import time
import uuid

import httpx

from ..rut import Rut
from . import endpoints as ep
from .errors import RespuestaInesperada, SesionExpirada
from .modelos import DocumentoSII, ResumenTipoDoc
from .parsers import documento_desde_json, parsear_csv_rcv, parsear_resumen
from .portal import SesionPortal

log = logging.getLogger(__name__)


class ClienteRCV:
    """Consultas al Registro de Compras y Ventas sobre una sesión autenticada.

    ``rut_titular`` es de quién se piden los documentos, y puede ser distinto
    del RUT con el que se inició sesión: el SII siempre autentica a una
    persona natural, que puede representar a una o más empresas. Si no se
    indica, se asume que la sesión ya representa al RUT que se quiere
    consultar (el caso más común: alguien consultando sus propios documentos).
    """

    def __init__(self, sesion: SesionPortal, *, pausa: float = 0.8, rut_titular: Rut | None = None):
        self.sesion = sesion
        self.pausa = pausa
        self.rut_titular = rut_titular or sesion.rut
        self._inicializada = False

    # --- Infraestructura ---------------------------------------------------
    @property
    def _base(self) -> str:
        return self.sesion.base_rcv

    def _headers(self) -> dict:
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Referer": f"{self._base}/consdcvinternetui/",
        }

    def _sobre(self, metodo: str, data: dict) -> dict:
        """Envuelve el payload en el sobre ``metaData`` que exige el backend."""
        return {
            "metaData": {
                "namespace": f"{ep.NAMESPACE}/{metodo}",
                "conversationId": self.sesion.token,
                "transactionId": str(uuid.uuid4()),
            },
            "data": data,
        }

    def _esperar(self) -> None:
        if self.pausa > 0:
            time.sleep(self.pausa)

    def inicializar(self) -> None:
        """Levanta la sesión de servidor del RCV.

        El paso clave es la llamada JSONP a ``AutTknData.cgi``: valida la cookie
        ``TOKEN`` contra la infraestructura de autenticación y es lo que hace
        que JBoss acepte las llamadas posteriores al ``FacadeService``.
        """
        if self._inicializada:
            return
        cliente = self.sesion.cliente
        base = self._base

        cliente.get(f"{base}/consdcvinternetui/", headers={"Accept": "text/html"})
        cliente.post(f"{base}{ep.ENDPOINTS['auth_conf']}", json={}, headers=self._headers())
        cliente.get(f"{base}{ep.ENDPOINTS['sesion_load']}", headers=self._headers())

        marca = int(time.time() * 1000)
        cliente.get(
            f"{ep.AUTH_URL}/cgi_AUT2000/AutTknData.cgi",
            params={"rnd": marca, "callback": f"jQuery_{marca}", "_": marca + 1},
            headers={"Accept": "*/*", "Referer": f"{base}/consdcvinternetui/"},
        )

        cliente.post(
            f"{base}{ep.ENDPOINTS['parametros']}",
            json=self._sobre("consultarParametros", {}),
            headers=self._headers(),
        )
        cliente.post(
            f"{base}{ep.ENDPOINTS['datos_inicio']}",
            json=self._sobre("getDatosInicio", {}),
            headers=self._headers(),
        )
        self._inicializada = True
        log.debug("Sesión del RCV inicializada")

    def _data(
        self,
        periodo: str,
        operacion: str,
        estado_contab: str,
        tipo_doc: int | None = None,
        *,
        busqueda_inicial: bool = False,
        recaptcha: bool = False,
    ) -> dict:
        data: dict = {
            "rutEmisor": str(self.rut_titular.cuerpo),
            "dvEmisor": self.rut_titular.dv,
            "ptributario": periodo,
            "operacion": operacion,
            "estadoContab": estado_contab,
        }
        if tipo_doc:
            data["codTipoDoc"] = str(tipo_doc)
        if busqueda_inicial:
            data["busquedaInicial"] = True
        if recaptcha:
            # El SPA envía este literal en vez de resolver un recaptcha real.
            data["tokenRecaptcha"] = ep.TOKEN_RECAPTCHA
            data["accionRecaptcha"] = "RCV_DETC" if operacion == ep.COMPRA else "RCV_DETV"
        return data

    def _post_json(
        self, ruta: str, metodo: str, data: dict, *, codigos_vacios: frozenset = frozenset()
    ) -> dict:
        self.inicializar()
        self._esperar()
        respuesta = self.sesion.cliente.post(
            f"{self._base}{ruta}", json=self._sobre(metodo, data), headers=self._headers()
        )
        self._verificar_sesion(respuesta)
        try:
            payload = respuesta.json()
        except ValueError as exc:
            raise RespuestaInesperada(
                f"{metodo} devolvió algo que no es JSON (HTTP {respuesta.status_code}). "
                "Probablemente el SII cambió el portal o la sesión caducó."
            ) from exc
        estado = payload.get("respEstado") or {}
        codigo = estado.get("codRespuesta")
        if codigo in codigos_vacios:
            # Observado en producción: el SII usa este código para decir "no hay
            # documentos de este tipo en el periodo", en vez de una lista vacía.
            return payload
        if codigo not in (None, 0, "0"):
            raise RespuestaInesperada(
                f"El SII rechazó {metodo}: [{codigo}] {estado.get('msgeRespuesta', 'sin detalle')}"
            )
        return payload

    @staticmethod
    def _verificar_sesion(respuesta: httpx.Response) -> None:
        texto = respuesta.text[:2000] if respuesta.text else ""
        if ep.SENTINELA_SESION_CAIDA in texto.upper():
            raise SesionExpirada("La sesión del portal del SII caducó; vuelve a autenticarte.")

    # --- Consultas ---------------------------------------------------------
    def resumen(self, periodo: str, operacion: str, estado_contab: str = ep.REGISTRO) -> list[ResumenTipoDoc]:
        """Totales por tipo de documento del periodo (``YYYYMM``)."""
        payload = self._post_json(
            ep.ENDPOINTS["resumen"],
            "getResumen",
            self._data(periodo, operacion, estado_contab, busqueda_inicial=True),
            codigos_vacios=frozenset({ep.RESUMEN_CODIGO_SIN_DOCUMENTOS}),
        )
        return parsear_resumen(payload)

    def detalle(
        self,
        periodo: str,
        operacion: str,
        estado_contab: str = ep.REGISTRO,
        tipo_doc: int | None = None,
    ) -> list[DocumentoSII]:
        """Documentos del periodo vía la API JSON del RCV."""
        ruta = ep.ENDPOINTS["detalle_compra"] if operacion == ep.COMPRA else ep.ENDPOINTS["detalle_venta"]
        metodo = "getDetalleCompra" if operacion == ep.COMPRA else "getDetalleVenta"
        payload = self._post_json(
            ruta,
            metodo,
            self._data(periodo, operacion, estado_contab, tipo_doc, recaptcha=True),
        )
        filas = payload.get("data") or []
        if not isinstance(filas, list):
            return []
        return [
            documento_desde_json(fila, operacion, periodo, estado_contab)
            for fila in filas
            if isinstance(fila, dict)
        ]

    def exportar_csv(
        self,
        periodo: str,
        operacion: str,
        estado_contab: str = ep.REGISTRO,
        tipo_doc: int | None = None,
    ) -> str:
        """Descarga el CSV oficial de "Descargar Detalles" (texto crudo)."""
        self.inicializar()
        self._esperar()
        ruta = (
            ep.ENDPOINTS["detalle_compra_export"]
            if operacion == ep.COMPRA
            else ep.ENDPOINTS["detalle_venta_export"]
        )
        metodo = "getDetalleCompraExport" if operacion == ep.COMPRA else "getDetalleVentaExport"
        respuesta = self.sesion.cliente.post(
            f"{self._base}{ruta}",
            json=self._sobre(metodo, self._data(periodo, operacion, estado_contab, tipo_doc, recaptcha=True)),
            headers={**self._headers(), "Accept": "text/csv, application/octet-stream, */*"},
        )
        self._verificar_sesion(respuesta)
        if respuesta.status_code >= 400:
            raise RespuestaInesperada(f"{metodo} respondió HTTP {respuesta.status_code}")
        return respuesta.text or ""

    def listar(
        self, periodo: str, operacion: str, estado_contab: str = ep.REGISTRO
    ) -> tuple[list[DocumentoSII], str | None]:
        """Devuelve todos los documentos del periodo y el CSV original si lo hubo.

        Estrategia: el CSV oficial trae más columnas y es la vía más estable, así
        que se intenta primero. Si falla o viene vacío, se cae al JSON, pidiendo
        el detalle tipo por tipo según lo que declare el resumen.
        """
        resumenes = self.resumen(periodo, operacion, estado_contab)
        tipos = [r.tipo_doc for r in resumenes if r.total_documentos or r.monto_total]
        if not tipos:
            log.info("Periodo %s (%s): sin documentos", periodo, operacion)
            return [], None

        try:
            csv_texto = self.exportar_csv(periodo, operacion, estado_contab)
            documentos = parsear_csv_rcv(csv_texto, operacion, periodo, estado_contab)
            if documentos:
                log.info("Periodo %s (%s): %d documentos vía CSV", periodo, operacion, len(documentos))
                return documentos, csv_texto
            log.info("El CSV vino vacío; se consulta el detalle JSON")
        except (RespuestaInesperada, httpx.HTTPError) as exc:
            log.warning("Falló la exportación CSV (%s); se usa el detalle JSON", exc)

        documentos = []
        for tipo in tipos:
            documentos.extend(self.detalle(periodo, operacion, estado_contab, tipo))
        log.info("Periodo %s (%s): %d documentos vía JSON", periodo, operacion, len(documentos))
        return documentos, None
