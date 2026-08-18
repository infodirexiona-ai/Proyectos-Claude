from datetime import date
from decimal import Decimal

import pytest

from app.sii import endpoints as ep
from app.sii.parsers import (
    a_decimal,
    a_fecha,
    documento_desde_json,
    normalizar_clave,
    parsear_csv_rcv,
    parsear_resumen,
)

CSV_COMPRAS = (
    "﻿Nro;Tipo Doc;Tipo Compra;RUT Proveedor;Razon Social;Folio;Fecha Docto.;"
    "Fecha Recepcion;Acuse Recibo;Monto Exento;Monto Neto;Monto IVA Recuperable;"
    "Monto Iva No Recuperable;Monto Total\n"
    "1;33;Del Giro;76192083-9;COMERCIALIZADORA ANDES SPA;123456;05/03/2024;"
    "06/03/2024 10:15:00;;0;1.000.000;190.000;0;1.190.000\n"
    "2;61;Del Giro;76192083-9;COMERCIALIZADORA ANDES SPA;987;12/03/2024;"
    "12/03/2024 09:00:00;;0;100.000;19.000;0;119.000\n"
)

CSV_VENTAS = (
    "Nro;Tipo Doc;RUT Cliente;Razon Social;Folio;Fecha Docto.;Monto Exento;"
    "Monto Neto;Monto IVA;Monto Total\n"
    "1;33;96806980-2;DISTRIBUIDORA CENTRAL S.A.;5001;20/03/2024;0;2.500.000;475.000;2.975.000\n"
)


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("1.190.000", Decimal("1190000")),
        ("1.234.567,89", Decimal("1234567.89")),
        ("1234567.89", Decimal("1234567.89")),
        (190000, Decimal("190000")),
        ("", Decimal(0)),
        (None, Decimal(0)),
        ("-50.000", Decimal("-50000")),
        ("(1.000)", Decimal("-1000")),
    ],
)
def test_a_decimal(entrada, esperado):
    assert a_decimal(entrada) == esperado


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("05/03/2024", date(2024, 3, 5)),
        ("2024-03-05", date(2024, 3, 5)),
        ("05/03/2024 10:15:00", date(2024, 3, 5)),
        ("", None),
        ("no es fecha", None),
    ],
)
def test_a_fecha(entrada, esperado):
    assert a_fecha(entrada) == esperado


def test_normalizar_clave_ignora_tildes_y_puntuacion():
    assert normalizar_clave("Fecha Recepción") == normalizar_clave("Fecha Recepcion")
    assert normalizar_clave("Impto. Sin Derecho a Crédito") == "imptosinderechoacredito"


def test_csv_compras():
    docs = parsear_csv_rcv(CSV_COMPRAS, ep.COMPRA, "202403", ep.REGISTRO)
    assert len(docs) == 2
    factura = docs[0]
    assert factura.tipo_doc == 33
    assert factura.folio == 123456
    assert factura.rut_contraparte == "76192083-9"
    assert factura.razon_social == "COMERCIALIZADORA ANDES SPA"
    assert factura.fecha_emision == date(2024, 3, 5)
    assert factura.monto_neto == Decimal("1000000")
    assert factura.monto_iva == Decimal("190000")
    assert factura.monto_total == Decimal("1190000")
    assert factura.operacion == ep.COMPRA
    assert factura.periodo == "202403"
    assert docs[1].tipo_doc == 61


def test_csv_ventas_usa_columna_rut_cliente():
    docs = parsear_csv_rcv(CSV_VENTAS, ep.VENTA, "202403", ep.REGISTRO)
    assert len(docs) == 1
    assert docs[0].rut_contraparte == "96806980-2"
    assert docs[0].monto_iva == Decimal("475000")


def test_csv_vacio_o_solo_cabecera():
    assert parsear_csv_rcv("", ep.COMPRA, "202403", ep.REGISTRO) == []
    assert parsear_csv_rcv("Tipo Doc;Folio\n", ep.COMPRA, "202403", ep.REGISTRO) == []


def test_json_detalle():
    fila = {
        "detTipoDoc": "33",
        "detNroDoc": 445566,
        "detRutDoc": "77345612",
        "detDvDoc": "7",
        "detRznSoc": "SERVICIOS LIRCAY LTDA",
        "detFchDoc": "15/04/2024",
        "detFecRecepcion": "16/04/2024 08:30:00",
        "detMntExe": 0,
        "detMntNeto": 250000,
        "detMntIVA": 47500,
        "detMntTotal": 297500,
        "detIVAUsoComun": 0,
    }
    doc = documento_desde_json(fila, ep.COMPRA, "202404", ep.REGISTRO)
    assert doc.folio == 445566
    assert doc.rut_contraparte == "77345612-7"
    assert doc.monto_total == Decimal("297500")
    assert doc.fecha_emision == date(2024, 4, 15)
    assert doc.raw["detRznSoc"] == "SERVICIOS LIRCAY LTDA"
    assert doc.clave == (ep.COMPRA, 33, 445566, "77345612-7")


def test_resumen():
    payload = {
        "respEstado": {"codRespuesta": 0},
        "data": [
            {
                "rsmnTipoDocInteger": 33,
                "rsmnTotDoc": 12,
                "rsmnMntNeto": 1000,
                "rsmnMntIVA": 190,
                "rsmnMntTotal": 1190,
            },
            {
                "rsmnTipoDocInteger": 61,
                "rsmnTotDoc": 1,
                "rsmnMntNeto": 100,
                "rsmnMntIVA": 19,
                "rsmnMntTotal": 119,
            },
            {"sinTipo": 1},
        ],
    }
    resumen = parsear_resumen(payload)
    assert [r.tipo_doc for r in resumen] == [33, 61]
    assert resumen[0].total_documentos == 12
    assert resumen[0].monto_total == Decimal("1190")


def test_resumen_payload_vacio():
    assert parsear_resumen({"data": None}) == []
    assert parsear_resumen([]) == []
