"""Datos de ejemplo para probar la aplicación sin credenciales del SII.

Se activa con ``SII_MODO=demo``. Genera documentos deterministas (misma semilla
por periodo) para que los reportes y exportaciones sean reproducibles.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from . import endpoints as ep
from .modelos import DocumentoSII, ResumenTipoDoc

_PROVEEDORES = [
    ("76192083-9", "Comercializadora Andes SpA"),
    ("77345612-7", "Servicios Informáticos Lircay Ltda"),
    ("96806980-2", "Distribuidora Central S.A."),
    ("78901234-2", "Transportes del Maule EIRL"),
    ("77112233-7", "Arriendos Providencia SpA"),
]
_CLIENTES = [
    ("76543212-K", "Constructora Bío Bío S.A."),
    ("79876543-4", "Retail Sur Ltda"),
    ("96543210-8", "Minera Atacama S.A."),
    ("77665544-9", "Clínica Los Robles SpA"),
]
_IVA = Decimal("0.19")


def _semilla(periodo: str, operacion: str) -> random.Random:
    return random.Random(f"{periodo}:{operacion}")


def documentos_demo(periodo: str, operacion: str, estado_contab: str = ep.REGISTRO) -> list[DocumentoSII]:
    rnd = _semilla(periodo, operacion)
    contrapartes = _PROVEEDORES if operacion == ep.COMPRA else _CLIENTES
    anio, mes = int(periodo[:4]), int(periodo[4:])
    cantidad = rnd.randint(8, 22)
    documentos = []
    for i in range(cantidad):
        rut, razon = rnd.choice(contrapartes)
        tipo = rnd.choices([33, 34, 61, 56], weights=[80, 8, 8, 4])[0]
        neto = Decimal(rnd.randrange(30_000, 4_500_000, 1_000))
        exento = Decimal(0) if tipo != 34 else neto
        if tipo == 34:
            neto = Decimal(0)
        iva = (neto * _IVA).quantize(Decimal("1"))
        emision = date(anio, mes, rnd.randint(1, 28))
        documentos.append(
            DocumentoSII(
                operacion=operacion,
                periodo=periodo,
                estado_contab=estado_contab,
                tipo_doc=tipo,
                # Folio único y determinista por periodo, como en el RCV real.
                folio=(anio % 100) * 100_000 + mes * 1_000 + i,
                rut_contraparte=rut,
                razon_social=razon,
                fecha_emision=emision,
                fecha_recepcion=datetime.combine(emision, datetime.min.time())
                + timedelta(days=rnd.randint(0, 3), hours=rnd.randint(8, 20)),
                monto_exento=exento,
                monto_neto=neto,
                monto_iva=iva,
                monto_total=neto + exento + iva,
                tipo_compra="Del Giro" if operacion == ep.COMPRA else "",
                raw={"origen": "demo"},
            )
        )
    return documentos


def resumen_demo(periodo: str, operacion: str, estado_contab: str = ep.REGISTRO) -> list[ResumenTipoDoc]:
    por_tipo: dict[int, ResumenTipoDoc] = {}
    for doc in documentos_demo(periodo, operacion, estado_contab):
        fila = por_tipo.setdefault(doc.tipo_doc, ResumenTipoDoc(tipo_doc=doc.tipo_doc))
        fila.total_documentos += 1
        fila.monto_exento += doc.monto_exento
        fila.monto_neto += doc.monto_neto
        fila.monto_iva += doc.monto_iva
        fila.monto_total += doc.monto_total
    return sorted(por_tipo.values(), key=lambda r: r.tipo_doc)
