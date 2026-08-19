"""Un tramo de fechas que se cae por timeout no debe tirar todo el trabajo.

Antes de este arreglo, si un tramo del rango pedido no respondía a tiempo
(y el SII no llegó a mostrar el diálogo de "demasiados documentos"), la
función lanzaba ``SiiError`` y esa excepción abortaba todo
``descargar_detalle_ventas`` — perdiendo los documentos que ya se habían
descargado de otros tramos, incluso los que vinieron de partir el rango por
el límite de 20 documentos. Ahora ese tramo se registra como aviso y se
salta, sin perder el resto.
"""

from datetime import date
from decimal import Decimal

import app.sii.mipe as mipe
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
    return DocumentoEmitido(
        tipo_doc=33,
        folio=folio,
        fecha_emision=date(2025, 1, 1),
        rut_receptor="1-9",
        razon_social_receptor="Cliente",
        monto_neto=Decimal("1000"),
        monto_exento=Decimal("0"),
        monto_iva=Decimal("190"),
        monto_total=Decimal("1190"),
    )


def test_un_tramo_que_no_responde_no_pierde_los_documentos_de_los_demas(monkeypatch):
    monkeypatch.setattr(mipe, "_importar_playwright", _importar_playwright_falso)
    monkeypatch.setattr(mipe, "_login_en_pagina", lambda *_a, **_k: None)
    monkeypatch.setattr(mipe, "_seleccionar_empresa", lambda *_a, **_k: None)

    def _intentar_descarga_falso(_pagina, desde, hasta, _timeout_ms):
        if desde == date(2025, 1, 1) and hasta == date(2025, 1, 4):
            return None, "el sistema retorna demasiados documentos electrónicos"
        if desde == date(2025, 1, 1) and hasta == date(2025, 1, 2):
            return b"<xml/>", None
        if desde == date(2025, 1, 3) and hasta == date(2025, 1, 4):
            return None, "no respondió a tiempo"
        raise AssertionError(f"tramo inesperado: {desde}–{hasta}")

    monkeypatch.setattr(mipe, "_intentar_descarga", _intentar_descarga_falso)
    monkeypatch.setattr(mipe, "parsear_respaldo_mipyme", lambda _contenido: [_documento(folio=1)])

    resultado = mipe.descargar_detalle_ventas(
        "76655600-0",
        "76655600-0",
        "clave",
        date(2025, 1, 1),
        date(2025, 1, 4),
    )

    assert [d.folio for d in resultado.documentos] == [1]
    assert len(resultado.avisos) == 1
    assert "no respondió a tiempo" in resultado.avisos[0]
