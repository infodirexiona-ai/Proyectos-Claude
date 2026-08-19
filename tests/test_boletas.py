"""Emisión y anulación de Boletas de Honorarios — sin red, con un navegador falso.

Este flujo todavía no se probó en vivo contra el SII (ver el docstring de
``app.sii.boletas``), así que estas pruebas verifican lo único que se puede
verificar sin acceso real: que los campos correctos reciben los valores
correctos, y que ``confirmar=False`` nunca hace clic en el botón final que
tendría efecto real (emitir o anular).
"""

from datetime import date

import pytest

import app.sii.boletas as boletas
from app.sii import endpoints as ep


class _EventoNavegacionFalso:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _BotonFalso:
    def __init__(self, pagina, nombre):
        self._pagina = pagina
        self._nombre = nombre

    def click(self):
        self._pagina.clics.append(f"role:{self._nombre}")


class _PaginaFalsa:
    def __init__(self):
        self.valores = {}
        self.checks = []
        self.clics = []
        self.urls_visitadas = []

    def goto(self, url, **_kwargs):
        self.urls_visitadas.append(url)

    def expect_navigation(self, **_kwargs):
        return _EventoNavegacionFalso()

    def get_by_role(self, _rol, name):
        return _BotonFalso(self, name)

    def wait_for_selector(self, *_args, **_kwargs):
        pass

    def wait_for_timeout(self, _ms):
        pass

    def fill(self, selector, valor):
        self.valores[selector] = valor

    def select_option(self, selector, valor=None, label=None):
        self.valores[selector] = valor if valor is not None else f"label:{label}"

    def check(self, selector):
        self.checks.append(selector)

    def click(self, selector):
        self.clics.append(selector)

    def inner_text(self, _selector):
        return "texto de la pantalla"

    def screenshot(self, **_kwargs):
        return b"captura"


class _NavegadorFalso:
    def __init__(self, pagina):
        self._pagina = pagina

    def new_context(self, **_kwargs):
        return self

    def new_page(self):
        return self._pagina

    def close(self):
        pass


def _pw_falso(pagina):
    class _Pw:
        class chromium:
            @staticmethod
            def launch(**_kwargs):
                return _NavegadorFalso(pagina)

    class _SyncPlaywright:
        def __enter__(self):
            return _Pw()

        def __exit__(self, *_args):
            return False

    return lambda: _SyncPlaywright()


def _preparar(monkeypatch, pagina):
    monkeypatch.setattr(boletas, "_importar_playwright", lambda: (Exception, Exception, _pw_falso(pagina)))
    monkeypatch.setattr(boletas, "_login_en_pagina", lambda *_a, **_k: None)


def _solicitud(**overrides):
    base = {
        "rut_receptor": "76655600-0",
        "nombre_receptor": "Sociedad de Profesionales Direxiona Limitada",
        "domicilio_receptor": "Perez Valenzuela 1235 Of 303 3P",
        "region": "Región Metropolitana de Santiago",
        "comuna": "Providencia",
        "prestaciones": [boletas.Prestacion("Asesoría contable", 500_000)],
    }
    base.update(overrides)
    return boletas.SolicitudBoleta(**base)


def test_llena_el_rut_del_receptor_separado_en_cuerpo_y_dv(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)

    boletas.emitir_boleta_honorarios("17805525-9", "clave", _solicitud())

    assert pagina.valores[ep.BOLETAS_CAMPO_RUT_DESTINATARIO] == "76655600"
    assert pagina.valores[ep.BOLETAS_CAMPO_DV_DESTINATARIO] == "0"


def test_confirmar_falso_no_hace_clic_en_emitir(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)

    resultado = boletas.emitir_boleta_honorarios("17805525-9", "clave", _solicitud(), confirmar=False)

    assert resultado.confirmada is False
    assert ep.BOLETAS_BOTON_EMITIR not in pagina.clics
    assert ep.BOLETAS_BOTON_CONFIRMAR_EMISION in pagina.clics


def test_confirmar_verdadero_hace_clic_en_emitir(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)

    resultado = boletas.emitir_boleta_honorarios("17805525-9", "clave", _solicitud(), confirmar=True)

    assert resultado.confirmada is True
    assert ep.BOLETAS_BOTON_EMITIR in pagina.clics


def test_mas_de_cuatro_prestaciones_agrega_filas(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)
    seis_prestaciones = [boletas.Prestacion(f"Servicio {i}", 100_000) for i in range(1, 7)]

    boletas.emitir_boleta_honorarios("17805525-9", "clave", _solicitud(prestaciones=seis_prestaciones))

    assert pagina.clics.count(ep.BOLETAS_BOTON_MAS_PRESTACIONES) == 2
    assert pagina.valores["input[name='desc_prestacion_6']"] == "Servicio 6"
    assert pagina.valores["input[name='valor_prestacion_6']"] == "100000"


def test_mas_de_diez_prestaciones_es_rechazado(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)
    once = [boletas.Prestacion(f"Servicio {i}", 1) for i in range(11)]

    with pytest.raises(ValueError, match="10 prestaciones"):
        boletas.emitir_boleta_honorarios("17805525-9", "clave", _solicitud(prestaciones=once))


def test_cierra_la_sesion_al_terminar(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)

    boletas.emitir_boleta_honorarios("17805525-9", "clave", _solicitud())

    assert ep.URL_LOGOUT in pagina.urls_visitadas


def test_fecha_se_traduce_a_dia_mes_anio(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)

    boletas.emitir_boleta_honorarios("17805525-9", "clave", _solicitud(fecha=date(2026, 3, 5)))

    assert pagina.valores[ep.BOLETAS_CAMPO_DIA] == "05"
    assert pagina.valores[ep.BOLETAS_CAMPO_MES] == "03"
    assert pagina.valores[ep.BOLETAS_CAMPO_ANIO] == "2026"


def test_anular_causa_invalida_se_rechaza_antes_de_abrir_el_navegador(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)

    with pytest.raises(ValueError, match="Causa de anulación inválida"):
        boletas.anular_boleta_honorarios("17805525-9", "clave", 479, "9")

    assert pagina.urls_visitadas == []  # ni siquiera llegó a navegar


def test_anular_confirmar_falso_no_hace_clic_en_confirmar_anulacion(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)

    resultado = boletas.anular_boleta_honorarios(
        "17805525-9", "clave", 479, ep.BOLETAS_CAUSA_ERROR_DIGITACION, confirmar=False
    )

    assert resultado.confirmada is False
    assert ep.BOLETAS_BOTON_CONFIRMAR_ANULACION not in pagina.clics
    assert pagina.valores[ep.BOLETAS_CAMPO_FOLIO_ANULAR] == "479"
    assert pagina.checks == [f"{ep.BOLETAS_CAMPO_CAUSA_ANULACION}[value='3']"]


def test_anular_confirmar_verdadero_hace_clic_en_confirmar_anulacion(monkeypatch):
    pagina = _PaginaFalsa()
    _preparar(monkeypatch, pagina)

    resultado = boletas.anular_boleta_honorarios(
        "17805525-9", "clave", 479, ep.BOLETAS_CAUSA_ERROR_DIGITACION, confirmar=True
    )

    assert resultado.confirmada is True
    assert ep.BOLETAS_BOTON_CONFIRMAR_ANULACION in pagina.clics
