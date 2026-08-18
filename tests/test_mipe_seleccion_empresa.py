"""Selección de empresa en el MIPYME.

El SII siempre autentica a una persona natural, que puede representar más de
una empresa (el representante legal de varias sociedades, por ejemplo). En
ese caso, cualquier página del MIPYME redirige al menú general hasta que se
elige explícitamente con cuál operar — verificado a mano contra el portal
real: ver el formulario real (``mipeSelEmpresa.cgi``) en el docstring de
``app.sii.mipe._seleccionar_empresa``.

El envío se hace armando y mandando el formulario dentro de la propia página
(no por un canal de API aparte, que en vivo no dejaba la sesión sincronizada
con las navegaciones siguientes).
"""

from app.rut import parse_rut
from app.sii import endpoints as ep
from app.sii.mipe import _seleccionar_empresa


class _EventoNavegacionFalso:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _PaginaFalsa:
    def __init__(self):
        self.evaluaciones = []
        self.navegacion_esperada = None

    def expect_navigation(self, wait_until=None, timeout=None):
        self.navegacion_esperada = {"wait_until": wait_until, "timeout": timeout}
        return _EventoNavegacionFalso()

    def evaluate(self, script, args=None):
        self.evaluaciones.append({"script": script, "args": args})


def test_selecciona_empresa_con_los_campos_exactos_del_formulario_real():
    pagina = _PaginaFalsa()
    _seleccionar_empresa(pagina, parse_rut("76655600-0"), timeout_ms=45_000)

    assert pagina.navegacion_esperada == {"wait_until": "networkidle", "timeout": 45_000}
    assert len(pagina.evaluaciones) == 1

    args = pagina.evaluaciones[0]["args"]
    assert args == [
        ep.MIPE_SELECCIONAR_EMPRESA,
        "DESDE_DONDE_URL",
        "OPCION=2&TIPO=4",
        "RUT_EMP",
        "76655600-0",
    ]
    assert "form.submit" not in pagina.evaluaciones[0]["script"]  # se llama forma, no form
    assert "forma.submit()" in pagina.evaluaciones[0]["script"]
