"""Manejo de periodos tributarios en formato ``YYYYMM``."""

from __future__ import annotations

import re
from datetime import date

_PERIODO = re.compile(r"^(\d{4})(0[1-9]|1[0-2])$")


class PeriodoInvalido(ValueError):
    pass


def normalizar_periodo(valor: str) -> str:
    """Acepta ``202401``, ``2024-01`` o ``01/2024`` y devuelve ``202401``."""
    if valor is None:
        raise PeriodoInvalido("El periodo no puede ser vacío")
    texto = str(valor).strip()
    if "-" in texto:
        anio, _, mes = texto.partition("-")
    elif "/" in texto:
        mes, _, anio = texto.partition("/")
    else:
        anio, mes = texto[:4], texto[4:]
    candidato = f"{anio.strip():0>4}{mes.strip():0>2}"
    if not _PERIODO.match(candidato):
        raise PeriodoInvalido(f"Periodo inválido: {valor!r} (se espera YYYYMM)")
    return candidato


def rango_periodos(desde: str, hasta: str) -> list[str]:
    """Lista inclusiva de periodos entre ``desde`` y ``hasta``."""
    inicio, fin = normalizar_periodo(desde), normalizar_periodo(hasta)
    if inicio > fin:
        raise PeriodoInvalido(f"El periodo inicial ({inicio}) es posterior al final ({fin})")
    periodos = []
    anio, mes = int(inicio[:4]), int(inicio[4:])
    while f"{anio}{mes:02d}" <= fin:
        periodos.append(f"{anio}{mes:02d}")
        mes += 1
        if mes > 12:
            mes, anio = 1, anio + 1
    return periodos


def periodo_actual(hoy: date | None = None) -> str:
    hoy = hoy or date.today()
    return f"{hoy.year}{hoy.month:02d}"


def periodo_legible(periodo: str) -> str:
    meses = (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    )
    try:
        p = normalizar_periodo(periodo)
    except PeriodoInvalido:
        return periodo
    return f"{meses[int(p[4:]) - 1].capitalize()} {p[:4]}"
