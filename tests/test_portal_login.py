"""Login rechazado por el SII: guarda una captura para poder revisarla después.

Con el navegador en modo visible (``SII_HEADLESS=false``), la ventana se
cierra apenas se lanza el error y no da tiempo a ver qué mostró el SII
(clave rechazada, captcha, sala de espera...). Por eso, además del mensaje,
se guarda una captura de pantalla en disco.
"""

import pytest

from app.rut import parse_rut
from app.sii import endpoints as ep
from app.sii.errors import CredencialesInvalidas
from app.sii.portal import RUTA_CAPTURA_LOGIN_FALLIDO, _login_en_pagina


class _EventoNavegacionFalso:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _PaginaLoginRechazadoFalsa:
    def __init__(self):
        self.capturas = []
        self.url = "https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoRutClave.html"

    def goto(self, *_args, **_kwargs):
        pass

    def wait_for_selector(self, *_args, **_kwargs):
        pass

    def fill(self, *_args, **_kwargs):
        pass

    def expect_navigation(self, **_kwargs):
        return _EventoNavegacionFalso()

    def click(self, *_args, **_kwargs):
        pass

    def wait_for_load_state(self, *_args, **_kwargs):
        pass

    def screenshot(self, path):
        self.capturas.append(path)


def test_login_rechazado_guarda_captura_y_avisa_la_ruta():
    pagina = _PaginaLoginRechazadoFalsa()

    with pytest.raises(CredencialesInvalidas) as excinfo:
        _login_en_pagina(pagina, parse_rut("76655600-0"), "clave-mala", ep.PRODUCCION, 1_000)

    assert pagina.capturas == [str(RUTA_CAPTURA_LOGIN_FALLIDO)]
    assert str(RUTA_CAPTURA_LOGIN_FALLIDO) in str(excinfo.value)
