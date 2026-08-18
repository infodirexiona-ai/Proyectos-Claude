"""Pruebas de extremo a extremo de la interfaz, en modo demo."""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

RUT = "76192083-9"


@pytest.fixture
def cliente_con_datos(cliente):
    respuesta = cliente.post(
        "/sincronizar",
        data={"rut": RUT, "desde": "202403", "hasta": "202404", "compras": "on", "ventas": "on"},
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    return cliente


def test_salud(cliente):
    datos = cliente.get("/api/salud").json()
    assert datos["estado"] == "ok"
    assert datos["modo"] == "demo"


def test_panel_sin_datos(cliente):
    respuesta = cliente.get("/")
    assert respuesta.status_code == 200
    assert "Descargar del SII" in respuesta.text


def test_sincronizacion_demo_carga_documentos(cliente_con_datos):
    trabajo = cliente_con_datos.get("/api/sincronizaciones/1").json()
    assert trabajo["estado"] == "ok"
    assert trabajo["nuevos"] > 0

    datos = cliente_con_datos.get("/api/documentos", params={"titular": RUT}).json()
    assert datos["total"] > 0
    primero = datos["documentos"][0]
    assert primero["periodo"] in {"202403", "202404"}
    assert primero["operacion"] in {"COMPRA", "VENTA"}
    assert primero["tipo_doc_nombre"]


def test_paginas_principales(cliente_con_datos):
    for ruta in ("/", "/documentos", "/reportes", "/sincronizaciones", "/credenciales"):
        respuesta = cliente_con_datos.get(ruta)
        assert respuesta.status_code == 200, ruta
        assert "<table" in respuesta.text or "formulario-linea" in respuesta.text


def test_filtros_de_documentos(cliente_con_datos):
    respuesta = cliente_con_datos.get(
        "/documentos", params={"titular": RUT, "operacion": "VENTA", "desde": "202403", "hasta": "202403"}
    )
    assert respuesta.status_code == 200
    assert "COMPRA" not in respuesta.text.split("<tbody>")[1]


def test_reporte_periodo_api(cliente_con_datos):
    datos = cliente_con_datos.get("/api/reportes/periodo", params={"titular": RUT}).json()
    assert len(datos["por_periodo"]) == 4  # 2 periodos x 2 operaciones
    assert len(datos["iva"]) == 2
    assert all("saldo" in fila for fila in datos["iva"])


def test_exportar_excel(cliente_con_datos):
    respuesta = cliente_con_datos.get("/exportar.xlsx", params={"titular": RUT})
    assert respuesta.status_code == 200
    assert "spreadsheetml" in respuesta.headers["content-type"]
    libro = load_workbook(io.BytesIO(respuesta.content))
    assert libro.sheetnames == [
        "Documentos",
        "Por periodo",
        "Por tipo de documento",
        "Por contraparte",
        "IVA por periodo",
    ]
    hoja = libro["Documentos"]
    assert hoja.max_row > 1
    assert hoja["A1"].value == "Periodo"


def test_exportar_csv(cliente_con_datos):
    respuesta = cliente_con_datos.get("/exportar.csv", params={"titular": RUT, "operacion": "COMPRA"})
    assert respuesta.status_code == 200
    lineas = respuesta.text.strip().splitlines()
    assert lineas[0].startswith("﻿Periodo;Operación;")
    assert len(lineas) > 1
    assert all(";COMPRA;" in linea for linea in lineas[1:])


def test_credenciales_ciclo_completo(cliente):
    cliente.post("/credenciales", data={"rut": RUT, "clave": "secreta123", "alias": "Mi empresa"})
    pagina = cliente.get("/credenciales")
    assert RUT in pagina.text
    assert "secreta123" not in pagina.text

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Credencial

    with SessionLocal() as db:
        credencial = db.scalars(select(Credencial)).one()
        assert credencial.clave_cifrada != "secreta123"
        cred_id = credencial.id

    cliente.post(f"/credenciales/{cred_id}/eliminar")
    with SessionLocal() as db:
        assert db.scalars(select(Credencial)).all() == []


def test_rut_invalido_rechazado(cliente):
    respuesta = cliente.post("/sincronizar", data={"rut": "12345678-9", "desde": "202403", "hasta": "202403"})
    assert respuesta.status_code == 400


def test_rango_demasiado_largo(cliente):
    respuesta = cliente.post("/sincronizar", data={"rut": RUT, "desde": "201901", "hasta": "202412"})
    assert respuesta.status_code == 400
    assert "36 periodos" in respuesta.json()["detail"]


def test_descarga_fuera_del_directorio_rechazada(cliente):
    respuesta = cliente.get("/descargas/../../etc/passwd")
    assert respuesta.status_code in (400, 404)


def test_formato_clp_pone_el_signo_antes_del_simbolo():
    from app.web.formato import clp

    assert clp(1234567) == "$1.234.567"
    assert clp(-958360) == "-$958.360"
    assert clp(0) == "$0"
    assert clp(None) == "—"


def test_diagnostico_pagina(cliente):
    respuesta = cliente.get("/diagnostico?red=false")
    assert respuesta.status_code == 200
    assert "Diagnóstico" in respuesta.text
    assert "Todo listo" in respuesta.text  # modo demo: no exige navegador ni red


def test_diagnostico_api(cliente):
    datos = cliente.get("/api/diagnostico?red=false").json()
    assert datos["listo"] is True
    nombres = {c["nombre"] for c in datos["chequeos"]}
    assert "Modo de operación" in nombres
    assert "Base de datos" in nombres
