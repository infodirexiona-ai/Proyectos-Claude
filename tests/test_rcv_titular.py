"""El RUT titular (de quién son los documentos) puede ser distinto del RUT con
el que se inició sesión — típicamente cuando quien firma es el representante
legal de una empresa. El RCV tiene que consultarse con el RUT titular, no con
el de la sesión.
"""

import httpx
import pytest

from app.rut import parse_rut
from app.sii import endpoints as ep
from app.sii.errors import RespuestaInesperada
from app.sii.portal import SesionPortal
from app.sii.rcv import ClienteRCV


def _sesion(rut: str) -> SesionPortal:
    return SesionPortal(rut=parse_rut(rut), entorno=ep.PRODUCCION, cliente=httpx.Client(), token="tok")


def test_por_defecto_usa_el_rut_de_la_sesion():
    cliente = ClienteRCV(_sesion("76192083-9"))
    assert cliente.rut_titular == parse_rut("76192083-9")
    data = cliente._data("202403", ep.COMPRA, ep.REGISTRO)
    assert data["rutEmisor"] == "76192083"
    assert data["dvEmisor"] == "9"


def test_representante_legal_consulta_la_empresa_no_a_si_mismo():
    """El representante (11433270-4) inicia sesión, pero se consultan los
    documentos de la empresa que representa (76655600-0), no los suyos."""
    sesion = _sesion("11433270-4")
    cliente = ClienteRCV(sesion, rut_titular=parse_rut("76655600-0"))

    assert cliente.rut_titular == parse_rut("76655600-0")
    data = cliente._data("202403", ep.VENTA, ep.REGISTRO)
    assert data["rutEmisor"] == "76655600"
    assert data["dvEmisor"] == "0"
    # La sesión sigue autenticada como el representante; eso no cambia.
    assert sesion.rut == parse_rut("11433270-4")


def test_codigo_3_en_resumen_se_trata_como_sin_documentos():
    """Observado en producción: getResumen devuelve codRespuesta=3 (sin mensaje)
    cuando el periodo no tiene documentos de ese tipo, en vez de una lista
    vacía. Antes esto se trataba como un error real; ahora resumen() lo
    interpreta como "no hay nada" y sigue, sin lanzar RespuestaInesperada."""

    def _responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"respEstado": {"codRespuesta": 3, "msgeRespuesta": None}})

    transporte = httpx.MockTransport(_responder)
    sesion = _sesion("76192083-9")
    sesion.cliente = httpx.Client(transport=transporte)
    cliente = ClienteRCV(sesion, pausa=0)

    resumen = cliente.resumen("202601", ep.COMPRA)
    assert resumen == []


def test_otro_codigo_de_error_sigue_lanzando():
    """Un código de error real y distinto de 3 no debe silenciarse."""

    def _responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"respEstado": {"codRespuesta": 99, "msgeRespuesta": "boom"}})

    transporte = httpx.MockTransport(_responder)
    sesion = _sesion("76192083-9")
    sesion.cliente = httpx.Client(transport=transporte)
    cliente = ClienteRCV(sesion, pausa=0)

    with pytest.raises(RespuestaInesperada):
        cliente.resumen("202601", ep.COMPRA)
