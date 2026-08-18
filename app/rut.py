"""Utilidades para trabajar con RUT chilenos."""

from __future__ import annotations

import re
from dataclasses import dataclass

_LIMPIAR = re.compile(r"[^0-9kK]")


class RutInvalido(ValueError):
    """El RUT entregado no tiene un formato o dígito verificador válido."""


@dataclass(frozen=True)
class Rut:
    """RUT normalizado: cuerpo numérico y dígito verificador en mayúscula."""

    cuerpo: int
    dv: str

    def __str__(self) -> str:
        return f"{self.cuerpo}-{self.dv}"

    @property
    def formateado(self) -> str:
        """RUT con separadores de miles, p. ej. ``76.123.456-7``."""
        return f"{self.cuerpo:,}".replace(",", ".") + f"-{self.dv}"


def calcular_dv(cuerpo: int) -> str:
    """Devuelve el dígito verificador (módulo 11) del cuerpo de un RUT."""
    suma = 0
    multiplicador = 2
    for digito in reversed(str(cuerpo)):
        suma += int(digito) * multiplicador
        multiplicador = 2 if multiplicador == 7 else multiplicador + 1
    resto = 11 - (suma % 11)
    if resto == 11:
        return "0"
    if resto == 10:
        return "K"
    return str(resto)


def parse_rut(valor: str, *, validar_dv: bool = True) -> Rut:
    """Normaliza un RUT en cualquier formato habitual (``76.123.456-7``, ``761234567``)."""
    if valor is None:
        raise RutInvalido("El RUT no puede ser vacío")
    limpio = _LIMPIAR.sub("", str(valor)).upper()
    if len(limpio) < 2:
        raise RutInvalido(f"RUT demasiado corto: {valor!r}")
    cuerpo_txt, dv = limpio[:-1], limpio[-1]
    if not cuerpo_txt.isdigit():
        raise RutInvalido(f"El cuerpo del RUT no es numérico: {valor!r}")
    cuerpo = int(cuerpo_txt)
    if validar_dv and calcular_dv(cuerpo) != dv:
        raise RutInvalido(f"Dígito verificador inválido para {valor!r} (esperado {calcular_dv(cuerpo)})")
    return Rut(cuerpo=cuerpo, dv=dv)
