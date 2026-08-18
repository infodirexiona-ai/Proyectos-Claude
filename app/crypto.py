"""Cifrado simétrico de las credenciales guardadas en la base de datos.

La clave tributaria nunca se guarda en claro ni se escribe en los logs.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class ClaveCifradoAusente(RuntimeError):
    """No hay ``SII_CLAVE_CIFRADO`` configurada para cifrar/descifrar."""


def _fernet(clave: str) -> Fernet:
    if not clave:
        raise ClaveCifradoAusente("Falta SII_CLAVE_CIFRADO. Genera una con: python -m app.crypto")
    try:
        return Fernet(clave.encode())
    except (ValueError, TypeError):
        # Permite usar una passphrase arbitraria derivándola a una clave Fernet.
        derivada = base64.urlsafe_b64encode(hashlib.sha256(clave.encode()).digest())
        return Fernet(derivada)


def cifrar(texto: str, clave: str) -> str:
    return _fernet(clave).encrypt(texto.encode()).decode()


def descifrar(token: str, clave: str) -> str:
    try:
        return _fernet(clave).decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - depende del entorno
        raise ValueError(
            "No se pudo descifrar la credencial: la SII_CLAVE_CIFRADO no coincide con la usada al guardarla."
        ) from exc


def generar_clave() -> str:
    return Fernet.generate_key().decode()


if __name__ == "__main__":  # pragma: no cover
    print(generar_clave())
