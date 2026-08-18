"""El RUT titular (de quién son los documentos) puede ser distinto del RUT con
el que se inició sesión — típicamente cuando quien firma es el representante
legal de una empresa. El RCV tiene que consultarse con el RUT titular, no con
el de la sesión.
"""

import httpx

from app.rut import parse_rut
from app.sii import endpoints as ep
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
