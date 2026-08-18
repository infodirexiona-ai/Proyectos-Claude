import pytest

from app.rut import RutInvalido, calcular_dv, parse_rut


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("76.192.083-9", "76192083-9"),
        ("761920839", "76192083-9"),
        ("76192083-9", "76192083-9"),
        ("76.543.212-k", "76543212-K"),
    ],
)
def test_parse_rut_normaliza(entrada, esperado):
    assert str(parse_rut(entrada)) == esperado


def test_dv_k():
    assert calcular_dv(76543212) == "K"


def test_formateado():
    assert parse_rut("76192083-9").formateado == "76.192.083-9"


def test_dv_invalido():
    with pytest.raises(RutInvalido):
        parse_rut("76192083-1")


def test_sin_validar_dv():
    assert str(parse_rut("76192083-1", validar_dv=False)) == "76192083-1"
