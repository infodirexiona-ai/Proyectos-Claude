"""Selección de empresa en el MIPYME.

El SII siempre autentica a una persona natural, que puede representar más de
una empresa (el representante legal de varias sociedades, por ejemplo). En
ese caso, cualquier página del MIPYME redirige al menú general hasta que se
elige explícitamente con cuál operar — verificado a mano contra el portal
real: ver el formulario real (``mipeSelEmpresa.cgi``) en el docstring de
``app.sii.mipe._seleccionar_empresa``.
"""

from app.rut import parse_rut
from app.sii import endpoints as ep
from app.sii.mipe import _seleccionar_empresa


class _RequestFalso:
    def __init__(self):
        self.llamadas = []

    def post(self, url, form=None, timeout=None):
        self.llamadas.append({"url": url, "form": form, "timeout": timeout})


class _ContextoFalso:
    def __init__(self):
        self.request = _RequestFalso()


def test_selecciona_empresa_con_los_campos_exactos_del_formulario_real():
    contexto = _ContextoFalso()
    _seleccionar_empresa(contexto, parse_rut("76655600-0"), timeout_ms=45_000)

    assert len(contexto.request.llamadas) == 1
    llamada = contexto.request.llamadas[0]
    assert llamada["url"] == ep.MIPE_SELECCIONAR_EMPRESA
    assert llamada["form"] == {
        "DESDE_DONDE_URL": "OPCION=2&TIPO=4",
        "RUT_EMP": "76655600-0",
    }
    assert llamada["timeout"] == 45_000
