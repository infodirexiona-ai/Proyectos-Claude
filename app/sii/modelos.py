"""Estructura común de un documento del RCV, independiente de su origen
(JSON de la API interna o CSV de "Descargar Detalles")."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class DocumentoSII:
    """Una línea del Registro de Compras y Ventas."""

    operacion: str  # COMPRA | VENTA
    periodo: str  # YYYYMM
    estado_contab: str  # REGISTRO | PENDIENTE | NO_INCLUIR | RECLAMADO
    tipo_doc: int
    folio: int
    rut_contraparte: str = ""  # "76123456-7"
    razon_social: str = ""
    fecha_emision: date | None = None
    fecha_recepcion: datetime | None = None
    fecha_acuse: datetime | None = None
    monto_exento: Decimal = Decimal(0)
    monto_neto: Decimal = Decimal(0)
    monto_iva: Decimal = Decimal(0)
    monto_total: Decimal = Decimal(0)
    iva_no_recuperable: Decimal = Decimal(0)
    iva_uso_comun: Decimal = Decimal(0)
    impuesto_sin_credito: Decimal = Decimal(0)
    otros_impuestos: Decimal = Decimal(0)
    monto_activo_fijo: Decimal = Decimal(0)
    iva_activo_fijo: Decimal = Decimal(0)
    tipo_compra: str = ""
    tipo_transaccion: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def clave(self) -> tuple:
        """Clave natural del documento dentro de un titular."""
        return (self.operacion, self.tipo_doc, self.folio, self.rut_contraparte)


@dataclass
class ResumenTipoDoc:
    """Fila del resumen por tipo de documento que entrega el RCV."""

    tipo_doc: int
    total_documentos: int = 0
    monto_exento: Decimal = Decimal(0)
    monto_neto: Decimal = Decimal(0)
    monto_iva: Decimal = Decimal(0)
    monto_total: Decimal = Decimal(0)
