"""Consultas y agregaciones para los reportes.

Convención de signos: el SII entrega los montos de las notas de crédito en
positivo y las resta al calcular los totales del periodo. Aquí se hace lo mismo
aplicando un factor -1 a los tipos de nota de crédito.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from ..models import Documento
from ..sii import endpoints as ep
from .periodos import normalizar_periodo, periodo_legible

# Notas de crédito: restan del total del periodo.
TIPOS_NOTA_CREDITO = (60, 61, 112)


@dataclass
class Filtro:
    """Filtros comunes a la vista de documentos y a los reportes."""

    rut_titular: str
    periodo_desde: str | None = None
    periodo_hasta: str | None = None
    operacion: str | None = None
    tipo_doc: int | None = None
    rut_contraparte: str | None = None
    busqueda: str | None = None

    def normalizado(self) -> Filtro:
        return Filtro(
            rut_titular=self.rut_titular,
            periodo_desde=normalizar_periodo(self.periodo_desde) if self.periodo_desde else None,
            periodo_hasta=normalizar_periodo(self.periodo_hasta) if self.periodo_hasta else None,
            operacion=self.operacion or None,
            tipo_doc=self.tipo_doc,
            rut_contraparte=(self.rut_contraparte or "").strip() or None,
            busqueda=(self.busqueda or "").strip() or None,
        )


def _condiciones(filtro: Filtro) -> list:
    f = filtro.normalizado()
    condiciones = [Documento.rut_titular == f.rut_titular]
    if f.periodo_desde:
        condiciones.append(Documento.periodo >= f.periodo_desde)
    if f.periodo_hasta:
        condiciones.append(Documento.periodo <= f.periodo_hasta)
    if f.operacion:
        condiciones.append(Documento.operacion == f.operacion)
    if f.tipo_doc:
        condiciones.append(Documento.tipo_doc == f.tipo_doc)
    if f.rut_contraparte:
        condiciones.append(Documento.rut_contraparte == f.rut_contraparte)
    if f.busqueda:
        patron = f"%{f.busqueda}%"
        condiciones.append(
            or_(
                Documento.razon_social.ilike(patron),
                Documento.rut_contraparte.ilike(patron),
                func.cast(Documento.folio, type_=Documento.razon_social.type).ilike(patron),
            )
        )
    return condiciones


def consulta(filtro: Filtro) -> Select:
    return select(Documento).where(*_condiciones(filtro))


def _signo():
    """-1 para notas de crédito, 1 para el resto."""
    return case((Documento.tipo_doc.in_(TIPOS_NOTA_CREDITO), -1), else_=1)


def _suma(columna):
    return func.coalesce(func.sum(columna * _signo()), 0)


def _dec(valor) -> Decimal:
    return Decimal(str(valor or 0))


def listar_documentos(
    db: Session, filtro: Filtro, *, limite: int = 100, offset: int = 0, orden: str = "fecha"
) -> tuple[list[Documento], int]:
    base = consulta(filtro)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    ordenes = {
        "fecha": (Documento.fecha_emision.desc(), Documento.folio.desc()),
        "monto": (Documento.monto_total.desc(),),
        "contraparte": (Documento.razon_social.asc(),),
        "folio": (Documento.folio.desc(),),
    }
    filas = list(
        db.scalars(base.order_by(*ordenes.get(orden, ordenes["fecha"])).limit(limite).offset(offset))
    )
    return filas, total


def totales(db, filtro: Filtro) -> dict:
    fila = db.execute(
        select(
            func.count(Documento.id),
            _suma(Documento.monto_exento),
            _suma(Documento.monto_neto),
            _suma(Documento.monto_iva),
            _suma(Documento.monto_total),
        ).where(*_condiciones(filtro))
    ).one()
    return {
        "documentos": fila[0] or 0,
        "exento": _dec(fila[1]),
        "neto": _dec(fila[2]),
        "iva": _dec(fila[3]),
        "total": _dec(fila[4]),
    }


def por_periodo(db, filtro: Filtro) -> list[dict]:
    filas = db.execute(
        select(
            Documento.periodo,
            Documento.operacion,
            func.count(Documento.id),
            _suma(Documento.monto_exento),
            _suma(Documento.monto_neto),
            _suma(Documento.monto_iva),
            _suma(Documento.monto_total),
        )
        .where(*_condiciones(filtro))
        .group_by(Documento.periodo, Documento.operacion)
        .order_by(Documento.periodo.desc(), Documento.operacion)
    ).all()
    return [
        {
            "periodo": f[0],
            "periodo_legible": periodo_legible(f[0]),
            "operacion": f[1],
            "documentos": f[2],
            "exento": _dec(f[3]),
            "neto": _dec(f[4]),
            "iva": _dec(f[5]),
            "total": _dec(f[6]),
        }
        for f in filas
    ]


def por_tipo_documento(db, filtro: Filtro) -> list[dict]:
    filas = db.execute(
        select(
            Documento.tipo_doc,
            Documento.operacion,
            func.count(Documento.id),
            _suma(Documento.monto_neto),
            _suma(Documento.monto_iva),
            _suma(Documento.monto_total),
        )
        .where(*_condiciones(filtro))
        .group_by(Documento.tipo_doc, Documento.operacion)
        .order_by(Documento.operacion, Documento.tipo_doc)
    ).all()
    return [
        {
            "tipo_doc": f[0],
            "tipo_doc_nombre": ep.nombre_tipo_dte(f[0]),
            "operacion": f[1],
            "documentos": f[2],
            "neto": _dec(f[3]),
            "iva": _dec(f[4]),
            "total": _dec(f[5]),
        }
        for f in filas
    ]


def por_contraparte(db, filtro: Filtro, *, limite: int = 25) -> list[dict]:
    filas = db.execute(
        select(
            Documento.rut_contraparte,
            func.max(Documento.razon_social),
            Documento.operacion,
            func.count(Documento.id),
            _suma(Documento.monto_neto),
            _suma(Documento.monto_iva),
            _suma(Documento.monto_total),
        )
        .where(*_condiciones(filtro))
        .group_by(Documento.rut_contraparte, Documento.operacion)
        .order_by(_suma(Documento.monto_total).desc())
        .limit(limite)
    ).all()
    return [
        {
            "rut": f[0],
            "razon_social": f[1] or "",
            "operacion": f[2],
            "documentos": f[3],
            "neto": _dec(f[4]),
            "iva": _dec(f[5]),
            "total": _dec(f[6]),
        }
        for f in filas
    ]


def resumen_iva(db, filtro: Filtro) -> list[dict]:
    """IVA débito (ventas) vs crédito (compras) por periodo.

    Es un apoyo para preparar el F29; no reemplaza la propuesta oficial del SII.
    """
    sin_operacion = Filtro(**{**filtro.__dict__, "operacion": None})
    filas = db.execute(
        select(
            Documento.periodo,
            _suma(case((Documento.operacion == ep.VENTA, Documento.monto_iva), else_=0)),
            _suma(case((Documento.operacion == ep.COMPRA, Documento.monto_iva), else_=0)),
            _suma(case((Documento.operacion == ep.VENTA, Documento.monto_neto), else_=0)),
            _suma(case((Documento.operacion == ep.COMPRA, Documento.monto_neto), else_=0)),
        )
        .where(*_condiciones(sin_operacion))
        .group_by(Documento.periodo)
        .order_by(Documento.periodo.desc())
    ).all()
    resultado = []
    for f in filas:
        debito, credito = _dec(f[1]), _dec(f[2])
        resultado.append(
            {
                "periodo": f[0],
                "periodo_legible": periodo_legible(f[0]),
                "iva_debito": debito,
                "iva_credito": credito,
                "saldo": debito - credito,
                "ventas_netas": _dec(f[3]),
                "compras_netas": _dec(f[4]),
            }
        )
    return resultado


def periodos_disponibles(db, rut_titular: str) -> list[str]:
    return list(
        db.scalars(
            select(Documento.periodo)
            .where(Documento.rut_titular == rut_titular)
            .distinct()
            .order_by(Documento.periodo.desc())
        )
    )


def titulares(db) -> list[str]:
    return list(db.scalars(select(Documento.rut_titular).distinct().order_by(Documento.rut_titular)))
