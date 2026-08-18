"""Descarga de documentos desde el SII y persistencia en la base local."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import sesion_db
from ..models import Documento, Sincronizacion
from ..rut import parse_rut
from ..sii import endpoints as ep
from ..sii.demo import detalle_demo, documentos_demo
from ..sii.errors import SiiError
from ..sii.mipe import descargar_detalle_ventas
from ..sii.modelos import DocumentoSII
from ..sii.portal import iniciar_sesion
from ..sii.rcv import ClienteRCV

log = logging.getLogger(__name__)

_CAMPOS = (
    "estado_contab",
    "razon_social",
    "fecha_emision",
    "fecha_recepcion",
    "fecha_acuse",
    "monto_exento",
    "monto_neto",
    "monto_iva",
    "monto_total",
    "iva_no_recuperable",
    "iva_uso_comun",
    "impuesto_sin_credito",
    "otros_impuestos",
    "monto_activo_fijo",
    "iva_activo_fijo",
    "tipo_compra",
    "tipo_transaccion",
)


@dataclass
class ResultadoSync:
    nuevos: int = 0
    actualizados: int = 0
    archivos: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _cargar_existentes(
    db: Session, rut_titular: str, documentos: list[DocumentoSII]
) -> dict[tuple, Documento]:
    """Precarga los documentos ya guardados que podrían chocar con este lote.

    La clave natural no lleva el periodo a propósito: un folio de un tipo de
    documento es único por emisor, aunque el SII lo registre en otro periodo
    (por ejemplo una factura recibida con retraso). Por eso la búsqueda filtra
    por folio y no por periodo; si filtrara por periodo, un documento existente
    en otro mes pasaría desapercibido y el INSERT rompería la restricción única.
    """
    folios = sorted({d.folio for d in documentos})
    tipos = {d.tipo_doc for d in documentos}
    operaciones = {d.operacion for d in documentos}
    encontrados: dict[tuple, Documento] = {}
    # SQLite limita el número de parámetros por consulta; se trocea el IN.
    for inicio in range(0, len(folios), 400):
        lote = folios[inicio : inicio + 400]
        for fila in db.scalars(
            select(Documento).where(
                Documento.rut_titular == rut_titular,
                Documento.operacion.in_(operaciones),
                Documento.tipo_doc.in_(tipos),
                Documento.folio.in_(lote),
            )
        ):
            encontrados[(fila.operacion, fila.tipo_doc, fila.folio, fila.rut_contraparte)] = fila
    return encontrados


def guardar_documentos(
    db: Session, rut_titular: str, documentos: list[DocumentoSII], origen: str
) -> tuple[int, int]:
    """Inserta o actualiza documentos usando su clave natural.

    Clave: (titular, operación, tipo de documento, folio, RUT de la contraparte).
    """
    if not documentos:
        return 0, 0

    existentes = _cargar_existentes(db, rut_titular, documentos)

    nuevos = actualizados = 0
    for doc in documentos:
        fila = existentes.get(doc.clave)
        if fila is None:
            fila = Documento(
                rut_titular=rut_titular,
                operacion=doc.operacion,
                periodo=doc.periodo,
                tipo_doc=doc.tipo_doc,
                folio=doc.folio,
                rut_contraparte=doc.rut_contraparte,
            )
            db.add(fila)
            existentes[doc.clave] = fila
            nuevos += 1
        else:
            actualizados += 1
        fila.periodo = doc.periodo
        fila.origen = origen
        fila.raw = doc.raw
        for campo in _CAMPOS:
            setattr(fila, campo, getattr(doc, campo))
    db.flush()
    return nuevos, actualizados


def _guardar_csv(settings: Settings, rut: str, periodo: str, operacion: str, texto: str) -> str:
    destino = settings.dir_descargas / rut / periodo
    destino.mkdir(parents=True, exist_ok=True)
    archivo = destino / f"rcv_{operacion.lower()}_{periodo}.csv"
    archivo.write_text(texto, encoding="utf-8")
    return str(archivo.relative_to(settings.dir_descargas))


def sincronizar(
    *,
    rut: str,
    clave_tributaria: str,
    periodos: list[str],
    operaciones: tuple[str, ...] = (ep.COMPRA, ep.VENTA),
    estados: tuple[str, ...] = (ep.REGISTRO,),
    sync_id: int | None = None,
    settings: Settings | None = None,
) -> ResultadoSync:
    """Descarga los periodos indicados y los deja guardados en la base.

    En modo ``demo`` no se conecta al SII: usa datos de ejemplo. Es la forma de
    probar la interfaz y los reportes sin credenciales reales.
    """
    settings = settings or get_settings()
    rut_titular = str(parse_rut(rut))
    resultado = ResultadoSync()

    _marcar(sync_id, estado="corriendo")

    if settings.modo == "demo":
        for periodo in periodos:
            for operacion in operaciones:
                docs = documentos_demo(periodo, operacion)
                with sesion_db() as db:
                    n, a = guardar_documentos(db, rut_titular, docs, "demo")
                resultado.nuevos += n
                resultado.actualizados += a
        resultado.avisos.append("Modo demo: los datos son de ejemplo, no vienen del SII.")
        _marcar(sync_id, estado="ok", resultado=resultado)
        return resultado

    sesion = iniciar_sesion(
        rut_titular,
        clave_tributaria,
        headless=settings.headless,
        timeout_ms=settings.timeout_ms,
        user_agent=settings.user_agent,
        ruta_navegador=settings.ruta_navegador,
    )
    try:
        cliente = ClienteRCV(sesion, pausa=settings.pausa_entre_llamadas)
        for periodo in periodos:
            for operacion in operaciones:
                for estado in estados:
                    try:
                        docs, csv_texto = cliente.listar(periodo, operacion, estado)
                    except SiiError as exc:
                        aviso = f"{periodo} {operacion} {estado}: {exc}"
                        log.warning(aviso)
                        resultado.avisos.append(aviso)
                        continue
                    if csv_texto:
                        resultado.archivos.append(
                            _guardar_csv(settings, rut_titular, periodo, operacion, csv_texto)
                        )
                    origen = "csv" if csv_texto else "json"
                    with sesion_db() as db:
                        n, a = guardar_documentos(db, rut_titular, docs, origen)
                    resultado.nuevos += n
                    resultado.actualizados += a
    finally:
        sesion.cerrar()

    _marcar(sync_id, estado="ok", resultado=resultado)
    return resultado


def guardar_detalle_ventas(db: Session, rut_titular: str, documentos) -> int:
    """Guarda el detalle (glosa, cantidad, precio) sobre documentos de venta ya existentes.

    Sólo actualiza filas que el RCV ya trajo — no crea documentos nuevos, porque
    el detalle por sí solo no trae todos los campos que el RCV sí tiene (estado,
    fecha de recepción, etc.). Si no hay coincidencia por (tipo, folio), el
    documento se ignora y queda contado en ``sin_coincidencia``.
    """
    if not documentos:
        return 0

    tipos = {d.tipo_doc for d in documentos}
    folios = sorted({d.folio for d in documentos})
    existentes: dict[tuple[int, int], Documento] = {}
    for inicio in range(0, len(folios), 400):
        lote = folios[inicio : inicio + 400]
        for fila in db.scalars(
            select(Documento).where(
                Documento.rut_titular == rut_titular,
                Documento.operacion == ep.VENTA,
                Documento.tipo_doc.in_(tipos),
                Documento.folio.in_(lote),
            )
        ):
            existentes[(fila.tipo_doc, fila.folio)] = fila

    actualizados = 0
    for doc in documentos:
        fila = existentes.get(doc.clave)
        if fila is None:
            continue
        fila.detalle = [linea.a_dict() for linea in doc.lineas]
        actualizados += 1
    db.flush()
    return actualizados


def sincronizar_detalle_ventas(
    *,
    rut: str,
    clave_tributaria: str,
    fecha_desde: date,
    fecha_hasta: date,
    sync_id: int | None = None,
    settings: Settings | None = None,
) -> ResultadoSync:
    """Trae la glosa de las ventas ya descargadas por el RCV, vía el MIPYME.

    Sólo sirve para documentos emitidos con el facturador gratuito del SII.
    Requiere haber corrido ``sincronizar`` antes para ese rango: este flujo
    actualiza documentos existentes, no crea nuevos.
    """
    settings = settings or get_settings()
    rut_titular = str(parse_rut(rut))
    resultado = ResultadoSync()

    _marcar(sync_id, estado="corriendo")

    if settings.modo == "demo":
        with sesion_db() as db:
            filas = list(
                db.scalars(
                    select(Documento).where(
                        Documento.rut_titular == rut_titular,
                        Documento.operacion == ep.VENTA,
                        Documento.periodo >= f"{fecha_desde:%Y%m}",
                        Documento.periodo <= f"{fecha_hasta:%Y%m}",
                    )
                )
            )
            for fila in filas:
                doc = DocumentoSII(
                    operacion=ep.VENTA,
                    periodo=fila.periodo,
                    estado_contab=fila.estado_contab,
                    tipo_doc=fila.tipo_doc,
                    folio=fila.folio,
                    monto_neto=fila.monto_neto,
                    monto_exento=fila.monto_exento,
                )
                fila.detalle = detalle_demo(doc)
            resultado.actualizados = len(filas)
        resultado.avisos.append("Modo demo: la glosa es de ejemplo, no viene del SII.")
        _marcar(sync_id, estado="ok", resultado=resultado)
        return resultado

    detalle = descargar_detalle_ventas(
        rut_titular,
        clave_tributaria,
        fecha_desde,
        fecha_hasta,
        headless=settings.headless,
        timeout_ms=settings.timeout_ms,
        user_agent=settings.user_agent,
        ruta_navegador=settings.ruta_navegador,
    )
    resultado.avisos.extend(detalle.avisos)
    with sesion_db() as db:
        resultado.actualizados = guardar_detalle_ventas(db, rut_titular, detalle.documentos)

    _marcar(sync_id, estado="ok", resultado=resultado)
    return resultado


def sincronizar_detalle_ventas_en_segundo_plano(**kwargs) -> None:
    """Envoltorio para BackgroundTasks: registra el error en el trabajo."""
    sync_id = kwargs.get("sync_id")
    try:
        sincronizar_detalle_ventas(**kwargs)
    except Exception as exc:  # noqa: BLE001 - el detalle queda en el trabajo
        log.exception("Falló la sincronización de detalle de ventas %s", sync_id)
        _marcar(sync_id, estado="error", mensaje=str(exc))


def sincronizar_en_segundo_plano(**kwargs) -> None:
    """Envoltorio para BackgroundTasks: registra el error en el trabajo."""
    sync_id = kwargs.get("sync_id")
    try:
        sincronizar(**kwargs)
    except Exception as exc:  # noqa: BLE001 - el detalle queda en el trabajo
        log.exception("Falló la sincronización %s", sync_id)
        _marcar(sync_id, estado="error", mensaje=str(exc))


def _marcar(
    sync_id: int | None,
    *,
    estado: str,
    resultado: ResultadoSync | None = None,
    mensaje: str = "",
) -> None:
    if sync_id is None:
        return
    with sesion_db() as db:
        trabajo = db.get(Sincronizacion, sync_id)
        if trabajo is None:
            return
        trabajo.estado = estado
        if resultado is not None:
            trabajo.nuevos = resultado.nuevos
            trabajo.actualizados = resultado.actualizados
            trabajo.archivos = resultado.archivos
            if resultado.avisos:
                trabajo.mensaje = "\n".join(resultado.avisos)
        if mensaje:
            trabajo.mensaje = mensaje
        if estado in {"ok", "error"}:
            trabajo.terminado = datetime.now(UTC)


def ruta_descarga(nombre_relativo: str, settings: Settings | None = None) -> Path:
    """Resuelve una ruta dentro del directorio de descargas, sin salirse de él."""
    settings = settings or get_settings()
    base = settings.dir_descargas.resolve()
    destino = (base / nombre_relativo).resolve()
    if not destino.is_relative_to(base):
        raise ValueError("Ruta fuera del directorio de descargas")
    return destino
