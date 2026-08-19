"""Cerrar sólo el navegador no cierra la sesión del lado del SII.

El SII limita cuántas sesiones autenticadas puede tener abiertas un mismo
RUT ("Usted ha superado el máximo de sesiones autenticadas..."). El flujo
RCV ya llamaba a la URL de logout al terminar; el MIPYME sólo cerraba el
navegador, así que cada corrida dejaba una sesión abierta del lado del
servidor hasta que expiraba sola. Verificado en vivo: tras varias corridas
seguidas, el SII terminó rechazando el login por exceso de sesiones.
"""

from datetime import date

import app.sii.mipe as mipe
from app.sii import endpoints as ep


class _PaginaFalsa:
    def __init__(self):
        self.urls_visitadas = []

    def goto(self, url, **_kwargs):
        self.urls_visitadas.append(url)


class _ContextoFalso:
    def __init__(self, pagina):
        self._pagina = pagina

    def new_page(self):
        return self._pagina


class _NavegadorFalso:
    def __init__(self, pagina):
        self._pagina = pagina
        self.cerrado = False

    def new_context(self, **_kwargs):
        return _ContextoFalso(self._pagina)

    def close(self):
        self.cerrado = True


def _pw_falso(pagina):
    class _Pw:
        class chromium:
            @staticmethod
            def launch(**_kwargs):
                return _NavegadorFalso(pagina)

    class _SyncPlaywright:
        def __enter__(self):
            return _Pw()

        def __exit__(self, *_args):
            return False

    return lambda: _SyncPlaywright()


def test_cierra_la_sesion_en_el_sii_antes_de_cerrar_el_navegador(monkeypatch):
    pagina = _PaginaFalsa()
    monkeypatch.setattr(mipe, "_importar_playwright", lambda: (Exception, Exception, _pw_falso(pagina)))
    monkeypatch.setattr(mipe, "_login_en_pagina", lambda *_a, **_k: None)
    monkeypatch.setattr(mipe, "_seleccionar_empresa", lambda *_a, **_k: None)
    monkeypatch.setattr(mipe, "_intentar_descarga", lambda *_a, **_k: (b"<xml/>", None))
    monkeypatch.setattr(mipe, "parsear_respaldo_mipyme", lambda _contenido: [])

    mipe.descargar_detalle_ventas("76655600-0", "76655600-0", "clave", date(2025, 1, 1), date(2025, 1, 1))

    assert ep.URL_LOGOUT in pagina.urls_visitadas


def test_cierra_la_sesion_incluso_si_falla_el_login(monkeypatch):
    """El logout es un esfuerzo best-effort para no dejar sesiones colgadas,
    incluso cuando el resto del trabajo falla."""
    pagina = _PaginaFalsa()
    monkeypatch.setattr(mipe, "_importar_playwright", lambda: (Exception, Exception, _pw_falso(pagina)))

    def _login_que_falla(*_a, **_k):
        raise mipe.SiiError("boom")

    monkeypatch.setattr(mipe, "_login_en_pagina", _login_que_falla)

    try:
        mipe.descargar_detalle_ventas("76655600-0", "76655600-0", "clave", date(2025, 1, 1), date(2025, 1, 1))
    except mipe.SiiError:
        pass

    assert ep.URL_LOGOUT in pagina.urls_visitadas
