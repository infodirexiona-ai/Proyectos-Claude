from datetime import date

import pytest

from app.services.periodos import (
    PeriodoInvalido,
    normalizar_fecha,
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


def test_normaliza_fecha_exacta():
    assert normalizar_fecha("20250118") == date(2025, 1, 18)


def test_normaliza_fecha_exacta_invalida():
    with pytest.raises(PeriodoInvalido):
        normalizar_fecha("20250230")  # 30 de febrero no existe


def test_normaliza_fecha_desde_periodo_usa_primer_dia():
    assert normalizar_fecha("202502") == date(2025, 2, 1)


def test_normaliza_fecha_desde_periodo_con_fin_usa_ultimo_dia():
    assert normalizar_fecha("202502", fin=True) == date(2025, 2, 28)
