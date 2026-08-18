"""Modelo de datos: credenciales, documentos del RCV y trabajos de sincronización."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .sii.endpoints import nombre_tipo_dte


def _ahora() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Credencial(Base):
    """Credenciales del contribuyente. La clave se guarda cifrada (Fernet)."""

    __tablename__ = "credenciales"

    id: Mapped[int] = mapped_column(primary_key=True)
    rut: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    alias: Mapped[str] = mapped_column(String(120), default="")
    clave_cifrada: Mapped[str] = mapped_column(Text)
    creado: Mapped[datetime] = mapped_column(DateTime, default=_ahora)
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=_ahora, onupdate=_ahora)


class Documento(Base):
    """Una línea del Registro de Compras y Ventas ya normalizada."""

    __tablename__ = "documentos"
    __table_args__ = (
        UniqueConstraint(
            "rut_titular",
            "operacion",
            "tipo_doc",
            "folio",
            "rut_contraparte",
            name="uq_documento",
        ),
        Index("ix_documento_periodo", "rut_titular", "periodo", "operacion"),
        Index("ix_documento_contraparte", "rut_titular", "rut_contraparte"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rut_titular: Mapped[str] = mapped_column(String(20), index=True)
    operacion: Mapped[str] = mapped_column(String(10))
    periodo: Mapped[str] = mapped_column(String(6), index=True)
    estado_contab: Mapped[str] = mapped_column(String(20), default="REGISTRO")
    tipo_doc: Mapped[int] = mapped_column(Integer)
    folio: Mapped[int] = mapped_column(Integer)
    rut_contraparte: Mapped[str] = mapped_column(String(20), default="")
    razon_social: Mapped[str] = mapped_column(String(255), default="")
    fecha_emision: Mapped[date | None] = mapped_column(default=None)
    fecha_recepcion: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    fecha_acuse: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    monto_exento: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    monto_neto: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    monto_iva: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    monto_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    iva_no_recuperable: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    iva_uso_comun: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    impuesto_sin_credito: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    otros_impuestos: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    monto_activo_fijo: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    iva_activo_fijo: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)

    tipo_compra: Mapped[str] = mapped_column(String(60), default="")
    tipo_transaccion: Mapped[str] = mapped_column(String(60), default="")
    origen: Mapped[str] = mapped_column(String(10), default="csv")  # csv | json | demo
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    # Detalle línea por línea (glosa, cantidad, precio) de documentos que el
    # propio contribuyente emitió con el facturador gratuito del SII. El RCV no
    # lo trae; se completa aparte, vía app.sii.mipe, sólo para VENTA. Lista de
    # {codigo, descripcion, cantidad, precio, monto}.
    detalle: Mapped[list] = mapped_column(JSON, default=list)

    creado: Mapped[datetime] = mapped_column(DateTime, default=_ahora)
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=_ahora, onupdate=_ahora)

    @property
    def tipo_doc_nombre(self) -> str:
        return nombre_tipo_dte(self.tipo_doc)

    @property
    def periodo_legible(self) -> str:
        return f"{self.periodo[4:6]}/{self.periodo[0:4]}" if len(self.periodo) == 6 else self.periodo

    @property
    def tiene_detalle(self) -> bool:
        return bool(self.detalle)


class Sincronizacion(Base):
    """Un trabajo de descarga: rango de periodos y su resultado."""

    __tablename__ = "sincronizaciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    rut_titular: Mapped[str] = mapped_column(String(20), index=True)
    periodo_desde: Mapped[str] = mapped_column(String(6))
    periodo_hasta: Mapped[str] = mapped_column(String(6))
    operaciones: Mapped[str] = mapped_column(String(30), default="COMPRA,VENTA")
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # pendiente|corriendo|ok|error
    mensaje: Mapped[str] = mapped_column(Text, default="")
    nuevos: Mapped[int] = mapped_column(Integer, default=0)
    actualizados: Mapped[int] = mapped_column(Integer, default=0)
    archivos: Mapped[list] = mapped_column(JSON, default=list)
    creado: Mapped[datetime] = mapped_column(DateTime, default=_ahora)
    terminado: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    @property
    def duracion_segundos(self) -> float | None:
        if not self.terminado:
            return None
        return (self.terminado - self.creado).total_seconds()
