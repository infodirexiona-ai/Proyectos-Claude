from datetime import date
from decimal import Decimal

import pytest

from app.services import reports
from app.services.sync import guardar_documentos
from app.sii import endpoints as ep
from app.sii.modelos import DocumentoSII

TITULAR = "76192083-9"


def _doc(operacion, periodo, tipo, folio, neto, iva, contraparte="77345612-7", razon="PROVEEDOR SPA"):
    return DocumentoSII(
        operacion=operacion,
        periodo=periodo,
        estado_contab=ep.REGISTRO,
        tipo_doc=tipo,
        folio=folio,
        rut_contraparte=contraparte,
        razon_social=razon,
        fecha_emision=date(int(periodo[:4]), int(periodo[4:]), 10),
        monto_neto=Decimal(neto),
        monto_iva=Decimal(iva),
        monto_total=Decimal(neto) + Decimal(iva),
    )


@pytest.fixture
def poblada(db):
    documentos = [
        _doc(ep.COMPRA, "202403", 33, 1, 1_000_000, 190_000),
        _doc(ep.COMPRA, "202403", 33, 2, 500_000, 95_000, "96806980-2", "DISTRIBUIDORA CENTRAL"),
        _doc(ep.COMPRA, "202403", 61, 3, 100_000, 19_000),
        _doc(ep.VENTA, "202403", 33, 10, 3_000_000, 570_000, "96543210-8", "CLIENTE S.A."),
        _doc(ep.VENTA, "202404", 33, 11, 2_000_000, 380_000, "96543210-8", "CLIENTE S.A."),
    ]
    guardar_documentos(db, TITULAR, documentos, "test")
    db.commit()
    return db


def test_nota_de_credito_resta(poblada):
    total = reports.totales(
        poblada, reports.Filtro(TITULAR, operacion=ep.COMPRA, periodo_desde="202403", periodo_hasta="202403")
    )
    # 1.000.000 + 500.000 - 100.000 de la nota de crédito
    assert total["neto"] == Decimal("1400000")
    assert total["iva"] == Decimal("266000")
    assert total["documentos"] == 3


def test_por_periodo(poblada):
    filas = reports.por_periodo(poblada, reports.Filtro(TITULAR))
    ventas_abril = next(f for f in filas if f["periodo"] == "202404" and f["operacion"] == ep.VENTA)
    assert ventas_abril["total"] == Decimal("2380000")
    assert ventas_abril["periodo_legible"] == "Abril 2024"


def test_resumen_iva(poblada):
    filas = reports.resumen_iva(poblada, reports.Filtro(TITULAR))
    marzo = next(f for f in filas if f["periodo"] == "202403")
    assert marzo["iva_debito"] == Decimal("570000")
    assert marzo["iva_credito"] == Decimal("266000")
    assert marzo["saldo"] == Decimal("304000")


def test_por_contraparte_ordena_por_total(poblada):
    filas = reports.por_contraparte(poblada, reports.Filtro(TITULAR))
    assert filas[0]["rut"] == "96543210-8"
    assert filas[0]["documentos"] == 2


def test_por_tipo_documento(poblada):
    filas = reports.por_tipo_documento(poblada, reports.Filtro(TITULAR, operacion=ep.COMPRA))
    nota = next(f for f in filas if f["tipo_doc"] == 61)
    assert nota["tipo_doc_nombre"] == "Nota de crédito electrónica"
    assert nota["neto"] == Decimal("-100000")


def test_filtro_busqueda(poblada):
    docs, total = reports.listar_documentos(poblada, reports.Filtro(TITULAR, busqueda="DISTRIBUIDORA"))
    assert total == 1
    assert docs[0].razon_social == "DISTRIBUIDORA CENTRAL"


def test_upsert_no_duplica(db):
    doc = _doc(ep.COMPRA, "202403", 33, 1, 1_000_000, 190_000)
    nuevos, actualizados = guardar_documentos(db, TITULAR, [doc], "csv")
    assert (nuevos, actualizados) == (1, 0)
    doc.razon_social = "NOMBRE CORREGIDO SPA"
    nuevos, actualizados = guardar_documentos(db, TITULAR, [doc], "csv")
    assert (nuevos, actualizados) == (0, 1)
    db.commit()
    docs, total = reports.listar_documentos(db, reports.Filtro(TITULAR))
    assert total == 1
    assert docs[0].razon_social == "NOMBRE CORREGIDO SPA"


def test_periodos_disponibles(poblada):
    assert reports.periodos_disponibles(poblada, TITULAR) == ["202404", "202403"]


def test_mismo_folio_en_otro_periodo_no_duplica(db):
    """El SII puede registrar una factura recibida con retraso en otro periodo.

    La clave natural no incluye el periodo, así que el segundo guardado tiene que
    actualizar la fila existente en lugar de intentar insertar una nueva.
    """
    original = _doc(ep.COMPRA, "202403", 33, 777, 1_000_000, 190_000)
    guardar_documentos(db, TITULAR, [original], "csv")
    db.commit()

    tardio = _doc(ep.COMPRA, "202404", 33, 777, 1_000_000, 190_000)
    nuevos, actualizados = guardar_documentos(db, TITULAR, [tardio], "csv")
    db.commit()

    assert (nuevos, actualizados) == (0, 1)
    docs, total = reports.listar_documentos(db, reports.Filtro(TITULAR))
    assert total == 1
    assert docs[0].periodo == "202404"


def test_lote_con_el_mismo_documento_repetido(db):
    doc = _doc(ep.COMPRA, "202403", 33, 888, 100_000, 19_000)
    nuevos, actualizados = guardar_documentos(db, TITULAR, [doc, doc], "csv")
    db.commit()
    assert nuevos == 1
    assert reports.listar_documentos(db, reports.Filtro(TITULAR))[1] == 1
