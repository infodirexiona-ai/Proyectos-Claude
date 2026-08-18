import pytest

from app.crypto import ClaveCifradoAusente, cifrar, descifrar, generar_clave


def test_ida_y_vuelta_con_clave_fernet():
    clave = generar_clave()
    assert descifrar(cifrar("mi-clave-tributaria", clave), clave) == "mi-clave-tributaria"


def test_acepta_passphrase_arbitraria():
    clave = "una passphrase cualquiera"
    assert descifrar(cifrar("secreto", clave), clave) == "secreto"


def test_el_cifrado_no_deja_el_texto_visible():
    token = cifrar("secreto", generar_clave())
    assert "secreto" not in token


def test_sin_clave_falla():
    with pytest.raises(ClaveCifradoAusente):
        cifrar("x", "")


def test_clave_distinta_falla():
    token = cifrar("secreto", generar_clave())
    with pytest.raises(ValueError):
        descifrar(token, generar_clave())
