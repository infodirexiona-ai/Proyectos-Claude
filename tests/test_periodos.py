import pytest

from app.services.periodos import (
    PeriodoInvalido,
    normalizar_periodo,
    periodo_legible,
    rango_periodos,
)


@pytest.mark.parametrize("entrada", ["202401", "2024-01", "01/2024"])
def test_normaliza(entrada):
    assert normalizar_periodo(entrada) == "202401"


def test_rango_cruza_anio():
    assert rango_periodos("202311", "202402") == ["202311", "202312", "202401", "202402"]


def test_rango_un_periodo():
    assert rango_periodos("202405", "202405") == ["202405"]


def test_rango_invertido():
    with pytest.raises(PeriodoInvalido):
        rango_periodos("202405", "202401")


def test_mes_invalido():
    with pytest.raises(PeriodoInvalido):
        normalizar_periodo("202413")


def test_legible():
    assert periodo_legible("202403") == "Marzo 2024"
