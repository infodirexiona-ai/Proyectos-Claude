"""``--rut-acceso`` en la CLI: permite indicar con quién iniciar sesión sin
tener que guardar antes una credencial (representante legal probando algo
puntual, por ejemplo)."""

from __future__ import annotations

import app.cli as cli


class _SettingsFalsas:
    def __init__(self, modo: str):
        self.modo = modo
        self.clave_tributaria = ""
        self.rut = ""


def test_rut_acceso_explicito_en_modo_demo(monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: _SettingsFalsas("demo"))
    rut_login, clave = cli._clave_para("76655600-0", pedir=True, rut_acceso="11433270-4")
    assert rut_login == "11433270-4"
    assert clave == ""


def test_rut_acceso_explicito_pide_su_propia_clave(monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: _SettingsFalsas("real"))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "getpass", lambda _prompt: "clave-del-representante")

    rut_login, clave = cli._clave_para("76655600-0", pedir=True, rut_acceso="11.433.270-4")

    assert rut_login == "11433270-4"  # normalizado
    assert clave == "clave-del-representante"


def test_sin_rut_acceso_ni_credencial_falla_claro(monkeypatch, db):
    monkeypatch.setattr(cli, "get_settings", lambda: _SettingsFalsas("real"))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    try:
        cli._clave_para("76655600-0", pedir=True)
        raise AssertionError("debería haber lanzado SystemExit")
    except SystemExit as exc:
        assert "76655600-0" in str(exc)
