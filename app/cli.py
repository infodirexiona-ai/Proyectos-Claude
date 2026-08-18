"""Interfaz de línea de comandos, pensada para automatizar con cron.

Ejemplos:
    python -m app.cli sincronizar --rut 76.192.083-9 --desde 202401 --hasta 202403
    python -m app.cli exportar --rut 76.192.083-9 --salida rcv.xlsx
    python -m app.cli generar-clave
"""

from __future__ import annotations

import argparse
import calendar
import logging
import sys
from datetime import date
from getpass import getpass
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .crypto import descifrar, generar_clave
from .db import SessionLocal, crear_esquema
from .diagnostico import ejecutar_diagnostico
from .models import Credencial
from .rut import parse_rut
from .services import export, reports
from .services.periodos import normalizar_periodo, periodo_actual, rango_periodos
from .services.sync import sincronizar, sincronizar_detalle_ventas
from .sii import endpoints as ep
from .web.formato import clp


def _clave_para(rut: str, pedir: bool) -> str:
    settings = get_settings()
    if settings.modo == "demo":
        return ""
    with SessionLocal() as db:
        credencial = db.scalars(select(Credencial).where(Credencial.rut == rut)).first()
        if credencial:
            return descifrar(credencial.clave_cifrada, settings.clave_cifrado)
    if settings.clave_tributaria and settings.rut and str(parse_rut(settings.rut)) == rut:
        return settings.clave_tributaria
    if pedir and sys.stdin.isatty():
        return getpass(f"Clave tributaria de {rut}: ")
    raise SystemExit(f"No hay clave tributaria para {rut}. Guárdala en la app o define SII_CLAVE_TRIBUTARIA.")


def _cmd_sincronizar(args) -> int:
    rut = str(parse_rut(args.rut))
    periodos = rango_periodos(args.desde, args.hasta or args.desde)
    operaciones = tuple(o for o in (ep.COMPRA, ep.VENTA) if getattr(args, o.lower()))
    resultado = sincronizar(
        rut=rut,
        clave_tributaria=_clave_para(rut, pedir=True),
        periodos=periodos,
        operaciones=operaciones or (ep.COMPRA, ep.VENTA),
    )
    print(f"Periodos: {periodos[0]} → {periodos[-1]}")
    print(f"Nuevos: {resultado.nuevos} · Actualizados: {resultado.actualizados}")
    for archivo in resultado.archivos:
        print(f"  CSV: {archivo}")
    for aviso in resultado.avisos:
        print(f"  ! {aviso}")
    return 0


def _cmd_exportar(args) -> int:
    rut = str(parse_rut(args.rut))
    filtro = reports.Filtro(
        rut_titular=rut,
        periodo_desde=args.desde,
        periodo_hasta=args.hasta,
        operacion=args.operacion,
    )
    salida = Path(args.salida)
    with SessionLocal() as db:
        if salida.suffix.lower() == ".csv":
            salida.write_text(export.csv_documentos(db, filtro).getvalue(), encoding="utf-8-sig")
        else:
            salida.write_bytes(export.excel_documentos(db, filtro).getvalue())
        total = reports.totales(db, filtro)
    print(f"{total['documentos']} documentos exportados a {salida}")
    return 0


def _cmd_resumen(args) -> int:
    rut = str(parse_rut(args.rut))
    with SessionLocal() as db:
        filas = reports.resumen_iva(db, reports.Filtro(rut_titular=rut))
    if not filas:
        print("Sin datos. Ejecuta primero 'sincronizar'.")
        return 1
    print(f"{'Periodo':<16}{'IVA débito':>15}{'IVA crédito':>15}{'Saldo':>15}")
    for fila in filas:
        print(
            f"{fila['periodo_legible']:<16}{clp(fila['iva_debito']):>15}"
            f"{clp(fila['iva_credito']):>15}{clp(fila['saldo']):>15}"
        )
    return 0


def _cmd_detalle_ventas(args) -> int:
    rut = str(parse_rut(args.rut))
    periodo_desde = normalizar_periodo(args.desde)
    periodo_hasta = normalizar_periodo(args.hasta or args.desde)
    fecha_desde = date(int(periodo_desde[:4]), int(periodo_desde[4:]), 1)
    ultimo_dia = calendar.monthrange(int(periodo_hasta[:4]), int(periodo_hasta[4:]))[1]
    fecha_hasta = date(int(periodo_hasta[:4]), int(periodo_hasta[4:]), ultimo_dia)

    resultado = sincronizar_detalle_ventas(
        rut=rut,
        clave_tributaria=_clave_para(rut, pedir=True),
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
    )
    print(f"Periodos: {periodo_desde} → {periodo_hasta}")
    print(f"Documentos con glosa actualizada: {resultado.actualizados}")
    for aviso in resultado.avisos:
        print(f"  ! {aviso}")
    return 0


def _cmd_doctor(args) -> int:
    chequeos = ejecutar_diagnostico(revisar_red=not args.sin_red)
    for chequeo in chequeos:
        print(chequeo)
    if all(c.ok for c in chequeos):
        print("\nTodo listo.")
        return 0
    print("\nHay puntos pendientes arriba (✗). Resuélvelos antes de sincronizar de verdad.")
    return 1


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__.splitlines()[0])
    parser.add_argument("-v", "--verbose", action="store_true", help="Muestra el detalle de las llamadas")
    sub = parser.add_subparsers(dest="comando", required=True)

    sincro = sub.add_parser("sincronizar", help="Descarga periodos del SII")
    sincro.add_argument("--rut", required=True)
    sincro.add_argument("--desde", default=periodo_actual(), help="AAAAMM (por defecto, el periodo actual)")
    sincro.add_argument("--hasta", default=None, help="AAAAMM (por defecto, igual a --desde)")
    sincro.add_argument("--compra", action="store_true", help="Sólo compras")
    sincro.add_argument("--venta", action="store_true", help="Sólo ventas")
    sincro.set_defaults(func=_cmd_sincronizar)

    exportar = sub.add_parser("exportar", help="Genera un Excel o CSV con lo ya descargado")
    exportar.add_argument("--rut", required=True)
    exportar.add_argument("--desde", default=None)
    exportar.add_argument("--hasta", default=None)
    exportar.add_argument("--operacion", choices=list(ep.OPERACIONES), default=None)
    exportar.add_argument("--salida", default="rcv.xlsx")
    exportar.set_defaults(func=_cmd_exportar)

    resumen = sub.add_parser("resumen", help="Muestra el IVA por periodo en la terminal")
    resumen.add_argument("--rut", required=True)
    resumen.set_defaults(func=_cmd_resumen)

    detalle = sub.add_parser(
        "detalle-ventas",
        help="Trae la glosa de ventas emitidas con el facturador gratuito del SII",
    )
    detalle.add_argument("--rut", required=True)
    detalle.add_argument("--desde", required=True, help="AAAAMM")
    detalle.add_argument("--hasta", default=None, help="AAAAMM (por defecto, igual a --desde)")
    detalle.set_defaults(func=_cmd_detalle_ventas)

    clave = sub.add_parser("generar-clave", help="Genera una SII_CLAVE_CIFRADO")
    clave.set_defaults(func=lambda _args: (print(generar_clave()), 0)[1])

    doctor = sub.add_parser("doctor", help="Revisa que la configuración, el navegador y la red estén listos")
    doctor.add_argument("--sin-red", action="store_true", help="Omite las pruebas de conexión al SII")
    doctor.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    crear_esquema()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
