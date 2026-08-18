from app.diagnostico import ejecutar_diagnostico


def test_modo_demo_no_exige_navegador_ni_red():
    """En demo no hace falta clave de cifrado, navegador ni conexión al SII."""
    chequeos = ejecutar_diagnostico(revisar_red=True)
    assert all(c.ok for c in chequeos)
    nombres = {c.nombre for c in chequeos}
    assert "Navegador (Chromium)" not in nombres
    assert not any("zeusr" in n or "Compras y Ventas" in n for n in nombres)


def test_str_de_un_chequeo_ok_no_repite_dos_puntos_vacios():
    chequeos = ejecutar_diagnostico(revisar_red=False)
    for c in chequeos:
        assert str(c).startswith(c.icono)
