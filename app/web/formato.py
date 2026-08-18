"""Filtros de presentación para las plantillas."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


def clp(valor) -> str:
    """Formatea un monto en pesos chilenos: ``$1.234.567`` / ``-$958.360``."""
    if valor in (None, ""):
        return "—"
    try:
        monto = Decimal(str(valor)).quantize(Decimal("1"))
    except (ArithmeticError, ValueError):
        return str(valor)
    # El signo va delante del símbolo: "-$958.360", no "$-958.360".
    signo = "-" if monto < 0 else ""
    return f"{signo}${abs(monto):,}".replace(",", ".")


def fecha(valor) -> str:
    if isinstance(valor, datetime):
        return valor.strftime("%d-%m-%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d-%m-%Y")
    return "—" if valor in (None, "") else str(valor)


def numero(valor) -> str:
    try:
        return f"{int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(valor)


FILTROS = {"clp": clp, "fecha": fecha, "numero": numero}
