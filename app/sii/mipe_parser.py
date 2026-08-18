"""Parser del XML de respaldo del Sistema de Facturación Gratuita del SII.

El botón "Archivo Respaldo" de ``mipeAdminDocsEmi.cgi`` entrega un ``SetDTE``
con el XML oficial y firmado de cada documento emitido. A diferencia del RCV,
aquí sí viene el detalle línea por línea (glosa incluida): cada ``Detalle``
trae un nombre corto (``NmbItem``, hasta 80 caracteres) y, si el emisor usó el
cuadro de texto largo del facturador, una descripción aparte (``DscItem``) que
el Excel/CSV del mismo sistema descarta al exportar. Este parser junta ambas
para reconstruir el texto completo tal como se ve en el PDF del documento.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from xml.etree import ElementTree as ET

from .parsers import a_decimal, a_fecha


@dataclass
class LineaDetalle:
    numero: int
    codigo: str
    descripcion: str
    cantidad: Decimal
    precio: Decimal
    monto: Decimal

    def a_dict(self) -> dict:
        return {
            "numero": self.numero,
            "codigo": self.codigo,
            "descripcion": self.descripcion,
            "cantidad": str(self.cantidad),
            "precio": str(self.precio),
            "monto": str(self.monto),
        }


@dataclass
class DocumentoEmitido:
    tipo_doc: int
    folio: int
    fecha_emision: date | None
    rut_receptor: str
    razon_social_receptor: str
    monto_neto: Decimal
    monto_exento: Decimal
    monto_iva: Decimal
    monto_total: Decimal
    lineas: list[LineaDetalle] = field(default_factory=list)

    @property
    def clave(self) -> tuple[int, int]:
        return (self.tipo_doc, self.folio)


def _texto(nodo: ET.Element | None, ruta: str, defecto: str = "") -> str:
    if nodo is None:
        return defecto
    hijo = nodo.find(ruta)
    if hijo is None or hijo.text is None:
        return defecto
    return hijo.text.strip()


def _primero(nodo: ET.Element | None, *rutas: str, defecto: str = "") -> str:
    for ruta in rutas:
        valor = _texto(nodo, ruta)
        if valor:
            return valor
    return defecto


def _glosa_completa(detalle: ET.Element) -> str:
    """Junta ``NmbItem`` (nombre corto) y ``DscItem`` (descripción larga).

    El facturador gratuito del SII limita ``NmbItem`` a un cuadro chico y
    guarda el texto adicional del cuadro largo en ``DscItem``, aparte. Unidos
    con un espacio reproducen exactamente el texto que se ve en el PDF.
    """
    nombre = _texto(detalle, "NmbItem")
    descripcion = _texto(detalle, "DscItem")
    if nombre and descripcion:
        return f"{nombre} {descripcion}".replace("\n", " ").strip()
    return (nombre or descripcion).replace("\n", " ").strip()


def _lineas(documento: ET.Element) -> list[LineaDetalle]:
    lineas = []
    for detalle in documento.findall("Detalle"):
        lineas.append(
            LineaDetalle(
                numero=int(_texto(detalle, "NroLinDet", "0") or 0),
                codigo=_texto(detalle, "CdgItem/VlrCodigo"),
                descripcion=_glosa_completa(detalle),
                cantidad=a_decimal(_texto(detalle, "QtyItem", "0")),
                precio=a_decimal(_texto(detalle, "PrcItem", "0")),
                monto=a_decimal(_texto(detalle, "MontoItem", "0")),
            )
        )
    return lineas


def _documento(documento: ET.Element) -> DocumentoEmitido | None:
    encabezado = documento.find("Encabezado")
    if encabezado is None:
        return None
    id_doc = encabezado.find("IdDoc")
    receptor = encabezado.find("Receptor")
    totales = encabezado.find("Totales")

    folio_txt = _texto(id_doc, "Folio")
    tipo_txt = _texto(id_doc, "TipoDTE")
    if not folio_txt or not tipo_txt:
        return None

    return DocumentoEmitido(
        tipo_doc=int(tipo_txt),
        folio=int(folio_txt),
        fecha_emision=a_fecha(_texto(id_doc, "FchEmis")),
        rut_receptor=_texto(receptor, "RUTRecep"),
        razon_social_receptor=_texto(receptor, "RznSocRecep"),
        monto_neto=a_decimal(_texto(totales, "MntNeto", "0")),
        monto_exento=a_decimal(_texto(totales, "MntExe", "0")),
        # El estándar del DTE llama a este campo "IVA", no "MntIVA" como el resto
        # de los montos — se prueba también "MntIVA" por si acaso, sin verificar
        # aún contra un documento afecto real (las muestras vistas eran exentas).
        monto_iva=a_decimal(_primero(totales, "IVA", "MntIVA", defecto="0")),
        monto_total=a_decimal(_texto(totales, "MntTotal", "0")),
        lineas=_lineas(documento),
    )


def parsear_respaldo_mipyme(xml: str | bytes) -> list[DocumentoEmitido]:
    """Parsea un ``SetDTE`` de "Archivo Respaldo" en una lista de documentos.

    Acepta bytes (recomendado: el XML se declara en ISO-8859-1, y dejar que el
    parser lea la declaración evita problemas de codificación) o texto ya
    decodificado.
    """
    if not xml:
        return []
    datos = xml if isinstance(xml, bytes) else xml.encode("iso-8859-1", errors="replace")
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError:
        return []

    documentos = []
    for nodo in raiz.iter("Documento"):
        doc = _documento(nodo)
        if doc:
            documentos.append(doc)
    return documentos
