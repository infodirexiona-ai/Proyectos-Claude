"""Errores del cliente SII."""

from __future__ import annotations


class SiiError(Exception):
    """Error genérico al interactuar con el SII."""


class CredencialesInvalidas(SiiError):
    """El SII rechazó el RUT o la clave tributaria."""


class SesionExpirada(SiiError):
    """La sesión del portal caducó y hay que volver a autenticarse."""


class RespuestaInesperada(SiiError):
    """El SII respondió algo que no sabemos interpretar (posible cambio de portal)."""
