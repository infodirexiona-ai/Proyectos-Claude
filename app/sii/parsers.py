"""Normalización de las respuestas del RCV (JSON y CSV) a ``DocumentoSII``.

El SII cambia nombres de columnas entre secciones y entornos, así que la
búsqueda de campos se hace sobre encabezados normalizados (sin tildes, sin
puntuación) y con varios alias por campo.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .modelos import DocumentoSII, ResumenTipoDoc

_NO_ALNUM = re.compile(r"[^a-z0-9]+")


def normalizar_clave(texto: str) -> str:
    """``"Monto IVA Recuperable"`` -> ``"montoivarecuperable"``."""
    sin_tildes = unicodedata.normalize("NFKD", str(texto))
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return _NO_ALNUM.sub("", sin_tildes.lower())


def a_decimal(valor) -> Decimal:
    """Convierte números del SII a ``Decimal``.

    Acepta enteros/flotantes de JSON y strings en formato chileno
    (``1.234.567,89``) o en formato inglés (``1234567.89``).
    """
    if valor is None or valor == "":
        return Decimal(0)
    if isinstance(valor, int | Decimal):
        return Decimal(valor)
    if isinstance(valor, float):
        return Decimal(str(valor))
    texto = str(valor).strip().replace("$", "").replace(" ", "")
    if not texto or texto in {"-", "null", "None"}:
        return Decimal(0)
    negativo = texto.startswith("(") and texto.endswith(")")
    if negativo:
        texto = texto[1:-1]
    if "," in texto and "." in texto:
        # El separador decimal es el último símbolo que aparece.
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        entero, _, decimales = texto.rpartition(",")
        # "1,234" con 3 dígitos a la derecha es separador de miles en CLP.
        texto = f"{entero}{decimales}" if len(decimales) == 3 else f"{entero}.{decimales}"
    elif texto.count(".") > 1:
        texto = texto.replace(".", "")
    elif "." in texto:
        entero, _, decimales = texto.rpartition(".")
        if len(decimales) == 3 and entero:
            texto = f"{entero}{decimales}"
    try:
        resultado = Decimal(texto)
    except InvalidOperation:
        return Decimal(0)
    return -resultado if negativo else resultado


def a_entero(valor) -> int:
    try:
        return int(a_decimal(valor))
    except (ValueError, InvalidOperation):
        return 0


_FORMATOS_FECHA = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M",
)


def a_datetime(valor) -> datetime | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)
    texto = str(valor).strip()
    if not texto or texto.lower() in {"null", "none", "-"}:
        return None
    for formato in _FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    # Epoch en milisegundos (el RCV lo usa en algunos campos de fecha).
    if texto.isdigit() and len(texto) >= 12:
        try:
            return datetime.fromtimestamp(int(texto) / 1000)
        except (ValueError, OSError, OverflowError):
            return None
    return None


def a_fecha(valor) -> date | None:
    momento = a_datetime(valor)
    return momento.date() if momento else None


class _Fila:
    """Acceso tolerante a una fila (dict) por alias de campo."""

    def __init__(self, datos: dict):
        self.original = datos
        self._indice = {normalizar_clave(k): v for k, v in datos.items()}

    def get(self, *alias, defecto=None):
        for nombre in alias:
            valor = self._indice.get(normalizar_clave(nombre))
            if valor not in (None, ""):
                return valor
        return defecto


def _rut_contraparte(fila: _Fila) -> str:
    """Devuelve el RUT de la contraparte como ``cuerpo-DV``."""
    completo = fila.get("rutProveedor", "rutCliente", "rutReceptor", "rutEmisor")
    cuerpo = fila.get("detRutDoc")
    dv = fila.get("detDvDoc")
    if cuerpo not in (None, ""):
        return f"{str(cuerpo).strip()}-{str(dv or '').strip().upper()}".rstrip("-")
    if completo:
        texto = str(completo).strip().replace(".", "").upper()
        return texto
    return ""


def documento_desde_json(fila_json: dict, operacion: str, periodo: str, estado_contab: str) -> DocumentoSII:
    """Mapea una fila de ``getDetalleCompra`` / ``getDetalleVenta``."""
    fila = _Fila(fila_json)
    return DocumentoSII(
        operacion=operacion,
        periodo=periodo,
        estado_contab=estado_contab,
        tipo_doc=a_entero(fila.get("detTipoDoc", "detTpoDoc", defecto=0)),
        folio=a_entero(fila.get("detNroDoc", "detFolio", defecto=0)),
        rut_contraparte=_rut_contraparte(fila),
        razon_social=str(fila.get("detRznSoc", "detRazonSocial", defecto="") or "").strip(),
        fecha_emision=a_fecha(fila.get("detFchDoc", "detFechaDocto")),
        fecha_recepcion=a_datetime(fila.get("detFecRecepcion", "detFchRecepcion")),
        fecha_acuse=a_datetime(fila.get("detFecAcuse", "detFchAcuse")),
        monto_exento=a_decimal(fila.get("detMntExe", defecto=0)),
        monto_neto=a_decimal(fila.get("detMntNeto", defecto=0)),
        monto_iva=a_decimal(fila.get("detMntIVA", "detMntIva", defecto=0)),
        monto_total=a_decimal(fila.get("detMntTotal", defecto=0)),
        iva_no_recuperable=a_decimal(fila.get("detMntIVANoRec", "detIVANoRec", defecto=0)),
        iva_uso_comun=a_decimal(fila.get("detIVAUsoComun", "detMntIVAUsoComun", defecto=0)),
        impuesto_sin_credito=a_decimal(fila.get("detImpSinCredito", "detMntSinCredito", defecto=0)),
        otros_impuestos=a_decimal(fila.get("detMntTotalOtrosImp", "detOtrosImp", "detValOtroImp", defecto=0)),
        monto_activo_fijo=a_decimal(fila.get("detMntActivoFijo", defecto=0)),
        iva_activo_fijo=a_decimal(fila.get("detMntIVAActivoFijo", "detIVAActivoFijo", defecto=0)),
        tipo_compra=str(fila.get("detTipoCompra", defecto="") or "").strip(),
        tipo_transaccion=str(fila.get("detTipoTransaccion", defecto="") or "").strip(),
        raw={k: ("" if v is None else v) for k, v in fila_json.items()},
    )


def documento_desde_csv(
    fila_csv: dict, operacion: str, periodo: str, estado_contab: str
) -> DocumentoSII | None:
    """Mapea una fila del CSV de "Descargar Detalles"."""
    fila = _Fila(fila_csv)
    tipo_doc = a_entero(fila.get("Tipo Doc", "Tipo Documento", "Tipo DTE", defecto=0))
    folio = a_entero(fila.get("Folio", "Nro Documento", defecto=0))
    if not tipo_doc or not folio:
        return None
    return DocumentoSII(
        operacion=operacion,
        periodo=periodo,
        estado_contab=estado_contab,
        tipo_doc=tipo_doc,
        folio=folio,
        rut_contraparte=_rut_contraparte(fila),
        razon_social=str(
            fila.get("Razon Social", "Nombre Proveedor", "Nombre Cliente", defecto="") or ""
        ).strip(),
        fecha_emision=a_fecha(fila.get("Fecha Docto.", "Fecha Docto", "Fecha Documento", "Fecha")),
        fecha_recepcion=a_datetime(fila.get("Fecha Recepcion", "Fecha Recepción")),
        fecha_acuse=a_datetime(fila.get("Fecha Acuse Recibo", "Fecha Acuse")),
        monto_exento=a_decimal(fila.get("Monto Exento", "Exento", defecto=0)),
        monto_neto=a_decimal(fila.get("Monto Neto", "Neto", defecto=0)),
        monto_iva=a_decimal(fila.get("Monto IVA Recuperable", "Monto IVA", "IVA", defecto=0)),
        monto_total=a_decimal(fila.get("Monto Total", "Total", defecto=0)),
        iva_no_recuperable=a_decimal(fila.get("Monto Iva No Recuperable", defecto=0)),
        iva_uso_comun=a_decimal(fila.get("IVA uso Comun", "IVA uso Común", defecto=0)),
        impuesto_sin_credito=a_decimal(
            fila.get("Impto. Sin Derecho a Credito", "Impto. Sin Derecho a Crédito", defecto=0)
        ),
        otros_impuestos=a_decimal(fila.get("Total Otros Impuestos", defecto=0)),
        monto_activo_fijo=a_decimal(fila.get("Monto Neto Activo Fijo", defecto=0)),
        iva_activo_fijo=a_decimal(fila.get("IVA Activo Fijo", defecto=0)),
        tipo_compra=str(fila.get("Tipo Compra", defecto="") or "").strip(),
        tipo_transaccion=str(fila.get("Tipo Venta", defecto="") or "").strip(),
        raw={k: (v or "") for k, v in fila_csv.items() if k},
    )


def parsear_csv_rcv(texto: str, operacion: str, periodo: str, estado_contab: str) -> list[DocumentoSII]:
    """Parsea el CSV (delimitado por ``;``) que entrega "Descargar Detalles"."""
    if not texto:
        return []
    if texto.startswith("﻿"):
        texto = texto[1:]
    lineas = [ln for ln in texto.splitlines() if ln.strip()]
    if len(lineas) < 2:
        return []
    delimitador = ";" if lineas[0].count(";") >= lineas[0].count(",") else ","
    lector = csv.DictReader(io.StringIO("\n".join(lineas)), delimiter=delimitador)
    documentos = []
    for fila in lector:
        limpia = {(k or "").strip(): (v or "").strip() for k, v in fila.items() if k}
        doc = documento_desde_csv(limpia, operacion, periodo, estado_contab)
        if doc:
            documentos.append(doc)
    return documentos


def parsear_resumen(payload: dict | list) -> list[ResumenTipoDoc]:
    """Parsea la respuesta de ``getResumen``."""
    filas = payload.get("data", []) if isinstance(payload, dict) else payload
    if not isinstance(filas, list):
        return []
    resumenes = []
    for cruda in filas:
        if not isinstance(cruda, dict):
            continue
        fila = _Fila(cruda)
        tipo = a_entero(fila.get("rsmnTipoDocInteger", "rsmnTipoDoc", defecto=0))
        if not tipo:
            continue
        resumenes.append(
            ResumenTipoDoc(
                tipo_doc=tipo,
                total_documentos=a_entero(fila.get("rsmnTotDoc", defecto=0)),
                monto_exento=a_decimal(fila.get("rsmnMntExe", "rsmnMntExento", defecto=0)),
                monto_neto=a_decimal(fila.get("rsmnMntNeto", defecto=0)),
                monto_iva=a_decimal(fila.get("rsmnMntIVA", "rsmnMntIva", defecto=0)),
                monto_total=a_decimal(fila.get("rsmnMntTotal", defecto=0)),
            )
        )
    return resumenes
