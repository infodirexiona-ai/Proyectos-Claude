"""Un solo día con más de 20 documentos: partir por tipo de documento.

Partir el rango por fecha tiene un piso — un solo día —, así que si ese día
por sí solo ya reúne más de 20 documentos (caso real: 22 facturas el
31-05-2024), la bisección por fecha no puede ayudar más. Se prueba entonces
por tipo de documento, la otra columna que acepta el mismo buscador del
MIPYME.
"""

from datetime import date

import app.sii.mipe as mipe
from app.sii import endpoints as ep
from app.sii.mipe_parser import DocumentoEmitido


class _PaginaFalsa:
    def goto(self, *_args, **_kwargs):
        pass


class _ContextoFalso:
    def new_page(self):
        return _PaginaFalsa()


class _NavegadorFalso:
    def new_context(self, **_kwargs):
        return _ContextoFalso()

    def close(self):
        pass


class _PwFalso:
    class chromium:
        @staticmethod
        def launch(**_kwargs):
            return _NavegadorFalso()


class _SyncPlaywrightFalso:
    def __enter__(self):
        return _PwFalso()

    def __exit__(self, *_args):
        return False


def _importar_playwright_falso():
    return Exception, Exception, lambda: _SyncPlaywrightFalso()


def _documento(folio: int) -> DocumentoEmitido:
    from decimal import Decimal

    return DocumentoEmitido(
        tipo_doc=33,
        folio=folio,
        fecha_emision=date(2024, 5, 31),
        rut_receptor="1-9",
        razon_social_receptor="Cliente",
        monto_neto=Decimal("1000"),
        monto_exento=Decimal("0"),
        monto_iva=Decimal("190"),
        monto_total=Decimal("1190"),
    )


def test_un_dia_con_mas_de_20_documentos_se_particiona_por_tipo(monkeypatch):
    monkeypatch.setattr(mipe, "_importar_playwright", _importar_playwright_falso)
    monkeypatch.setattr(mipe, "_login_en_pagina", lambda *_a, **_k: None)
    monkeypatch.setattr(mipe, "_seleccionar_empresa", lambda *_a, **_k: None)

    dia = date(2024, 5, 31)
    tipos_probados = []

    def _intentar_descarga_falso(_pagina, desde, hasta, _timeout_ms, tipo_doc=None):
        assert (desde, hasta) == (dia, dia)
        if tipo_doc is None:
            return None, "el sistema retorna demasiados documentos electrónicos"
        tipos_probados.append(tipo_doc)
        if tipo_doc == 33:
            return b"<xml/>", None
        return None, "no hay documentos"

    monkeypatch.setattr(mipe, "_intentar_descarga", _intentar_descarga_falso)
    monkeypatch.setattr(mipe, "parsear_respaldo_mipyme", lambda _contenido: [_documento(folio=7)])

    resultado = mipe.descargar_detalle_ventas("76655600-0", "76655600-0", "clave", dia, dia)

    assert [d.folio for d in resultado.documentos] == [7]
    assert set(tipos_probados) == set(ep.MIPE_TIPOS_DOC_VENTA)
    # Los tipos sin documentos ese día quedan como aviso, no como error fatal.
    assert len(resultado.avisos) == len(ep.MIPE_TIPOS_DOC_VENTA) - 1
