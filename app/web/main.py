"""Aplicación FastAPI: interfaz web y API JSON."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..crypto import ClaveCifradoAusente, cifrar, descifrar
from ..db import crear_esquema, get_db
from ..models import Credencial, Sincronizacion
from ..rut import RutInvalido, parse_rut
from ..services import export, reports
from ..services.periodos import PeriodoInvalido, periodo_actual, rango_periodos
from ..services.sync import ruta_descarga, sincronizar_en_segundo_plano
from ..sii import endpoints as ep
from .formato import FILTROS

log = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    crear_esquema()
    get_settings().asegurar_directorios()
    yield


app = FastAPI(
    title="Descarga de facturas SII",
    description="Descarga el Registro de Compras y Ventas del SII de Chile y genera reportes.",
    version=__version__,
    lifespan=ciclo_de_vida,
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

plantillas = Jinja2Templates(directory=str(BASE / "templates"))
plantillas.env.filters.update(FILTROS)


# --- Utilidades compartidas -------------------------------------------------
def _contexto(request: Request, db: Session, **extra) -> dict:
    settings = get_settings()
    credenciales = list(db.scalars(select(Credencial).order_by(Credencial.rut)))
    titulares = sorted({*reports.titulares(db), *(c.rut for c in credenciales)})
    contexto = {
        "modo": settings.modo,
        "version": __version__,
        "credenciales": credenciales,
        "titulares": titulares,
        "tipos_dte": ep.TIPOS_DTE,
        "operaciones": ep.OPERACIONES,
        "periodo_actual": periodo_actual(),
    }
    contexto.update(extra)
    return contexto


def _render(request: Request, db: Session, plantilla: str, **extra):
    return plantillas.TemplateResponse(request, plantilla, _contexto(request, db, **extra))


def _titular_activo(db: Session, rut: str | None) -> str | None:
    """RUT sobre el que se está trabajando: el pedido, o el primero disponible."""
    if rut:
        try:
            return str(parse_rut(rut))
        except RutInvalido:
            return None
    candidatos = reports.titulares(db)
    if candidatos:
        return candidatos[0]
    primera = db.scalars(select(Credencial).order_by(Credencial.id)).first()
    return primera.rut if primera else None


def _filtro(
    titular: str,
    desde: str | None = None,
    hasta: str | None = None,
    operacion: str | None = None,
    tipo_doc: int | None = None,
    contraparte: str | None = None,
    q: str | None = None,
) -> reports.Filtro:
    return reports.Filtro(
        rut_titular=titular,
        periodo_desde=desde or None,
        periodo_hasta=hasta or None,
        operacion=operacion or None,
        tipo_doc=tipo_doc,
        rut_contraparte=contraparte or None,
        busqueda=q or None,
    )


# --- Panel ------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def panel(request: Request, db: Session = Depends(get_db), titular: str | None = Query(None)):
    activo = _titular_activo(db, titular)
    datos = {"totales_compra": None, "totales_venta": None, "por_periodo": [], "iva": []}
    if activo:
        filtro = reports.Filtro(rut_titular=activo)
        datos["totales_compra"] = reports.totales(db, _filtro(activo, operacion=ep.COMPRA))
        datos["totales_venta"] = reports.totales(db, _filtro(activo, operacion=ep.VENTA))
        datos["por_periodo"] = reports.por_periodo(db, filtro)[:24]
        datos["iva"] = reports.resumen_iva(db, filtro)[:12]
    trabajos = list(db.scalars(select(Sincronizacion).order_by(Sincronizacion.id.desc()).limit(5)))
    return _render(request, db, "index.html", titular=activo, trabajos=trabajos, **datos)


# --- Documentos -------------------------------------------------------------
@app.get("/documentos", response_class=HTMLResponse)
def vista_documentos(
    request: Request,
    db: Session = Depends(get_db),
    titular: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
    operacion: str | None = Query(None),
    tipo_doc: int | None = Query(None),
    contraparte: str | None = Query(None),
    q: str | None = Query(None),
    orden: str = Query("fecha"),
    pagina: int = Query(1, ge=1),
):
    activo = _titular_activo(db, titular)
    if not activo:
        return _render(
            request,
            db,
            "documentos.html",
            titular=None,
            documentos=[],
            total=0,
            pagina=1,
            paginas=1,
            filtros={},
        )

    por_pagina = 100
    filtro = _filtro(activo, desde, hasta, operacion, tipo_doc, contraparte, q)
    try:
        documentos, total = reports.listar_documentos(
            db, filtro, limite=por_pagina, offset=(pagina - 1) * por_pagina, orden=orden
        )
        resumen = reports.totales(db, filtro)
    except PeriodoInvalido as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _render(
        request,
        db,
        "documentos.html",
        titular=activo,
        documentos=documentos,
        total=total,
        resumen=resumen,
        pagina=pagina,
        paginas=max(1, -(-total // por_pagina)),
        orden=orden,
        filtros={
            "desde": desde or "",
            "hasta": hasta or "",
            "operacion": operacion or "",
            "tipo_doc": tipo_doc or "",
            "contraparte": contraparte or "",
            "q": q or "",
        },
        periodos=reports.periodos_disponibles(db, activo),
    )


# --- Reportes ---------------------------------------------------------------
@app.get("/reportes", response_class=HTMLResponse)
def vista_reportes(
    request: Request,
    db: Session = Depends(get_db),
    titular: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
    operacion: str | None = Query(None),
):
    activo = _titular_activo(db, titular)
    datos = {"por_periodo": [], "por_tipo": [], "por_contraparte": [], "iva": [], "totales": None}
    if activo:
        filtro = _filtro(activo, desde, hasta, operacion)
        try:
            datos = {
                "por_periodo": reports.por_periodo(db, filtro),
                "por_tipo": reports.por_tipo_documento(db, filtro),
                "por_contraparte": reports.por_contraparte(db, filtro, limite=25),
                "iva": reports.resumen_iva(db, filtro),
                "totales": reports.totales(db, filtro),
            }
        except PeriodoInvalido as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _render(
        request,
        db,
        "reportes.html",
        titular=activo,
        filtros={"desde": desde or "", "hasta": hasta or "", "operacion": operacion or ""},
        **datos,
    )


# --- Exportaciones ----------------------------------------------------------
def _filtro_de_exportacion(db: Session, titular, desde, hasta, operacion, tipo_doc, contraparte, q):
    activo = _titular_activo(db, titular)
    if not activo:
        raise HTTPException(status_code=404, detail="No hay documentos que exportar")
    try:
        return _filtro(activo, desde, hasta, operacion, tipo_doc, contraparte, q).normalizado()
    except PeriodoInvalido as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/exportar.xlsx")
def exportar_excel(
    db: Session = Depends(get_db),
    titular: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
    operacion: str | None = Query(None),
    tipo_doc: int | None = Query(None),
    contraparte: str | None = Query(None),
    q: str | None = Query(None),
):
    filtro = _filtro_de_exportacion(db, titular, desde, hasta, operacion, tipo_doc, contraparte, q)
    buffer = export.excel_documentos(db, filtro)
    nombre = export.nombre_archivo(filtro, "xlsx")
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@app.get("/exportar.csv")
def exportar_csv(
    db: Session = Depends(get_db),
    titular: str | None = Query(None),
    desde: str | None = Query(None),
    hasta: str | None = Query(None),
    operacion: str | None = Query(None),
    tipo_doc: int | None = Query(None),
    contraparte: str | None = Query(None),
    q: str | None = Query(None),
):
    filtro = _filtro_de_exportacion(db, titular, desde, hasta, operacion, tipo_doc, contraparte, q)
    contenido = export.csv_documentos(db, filtro).getvalue()
    nombre = export.nombre_archivo(filtro, "csv")
    return StreamingResponse(
        iter([contenido.encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@app.get("/descargas/{ruta:path}")
def descargar_archivo(ruta: str):
    """Entrega el CSV original tal como lo emitió el SII."""
    try:
        archivo = ruta_descarga(ruta)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not archivo.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(archivo, filename=archivo.name, media_type="text/csv")


# --- Sincronización ---------------------------------------------------------
@app.get("/sincronizaciones", response_class=HTMLResponse)
def vista_sincronizaciones(request: Request, db: Session = Depends(get_db)):
    trabajos = list(db.scalars(select(Sincronizacion).order_by(Sincronizacion.id.desc()).limit(50)))
    return _render(request, db, "sincronizaciones.html", titular=None, trabajos=trabajos)


@app.post("/sincronizar")
def lanzar_sincronizacion(
    tareas: BackgroundTasks,
    db: Session = Depends(get_db),
    rut: str = Form(...),
    desde: str = Form(...),
    hasta: str = Form(...),
    compras: str | None = Form(None),
    ventas: str | None = Form(None),
    clave: str | None = Form(None),
):
    settings = get_settings()
    try:
        rut_titular = str(parse_rut(rut))
        periodos = rango_periodos(desde, hasta)
    except (RutInvalido, PeriodoInvalido) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if len(periodos) > 36:
        raise HTTPException(status_code=400, detail="El rango no puede superar 36 periodos")

    operaciones = tuple(
        operacion for operacion, marcado in ((ep.COMPRA, compras), (ep.VENTA, ventas)) if marcado
    ) or (ep.COMPRA, ep.VENTA)

    clave_final = (clave or "").strip()
    if not clave_final and settings.modo != "demo":
        clave_final = _clave_guardada(db, rut_titular, settings)

    trabajo = Sincronizacion(
        rut_titular=rut_titular,
        periodo_desde=periodos[0],
        periodo_hasta=periodos[-1],
        operaciones=",".join(operaciones),
    )
    db.add(trabajo)
    db.commit()
    db.refresh(trabajo)

    tareas.add_task(
        sincronizar_en_segundo_plano,
        rut=rut_titular,
        clave_tributaria=clave_final,
        periodos=periodos,
        operaciones=operaciones,
        sync_id=trabajo.id,
    )
    return RedirectResponse(url="/sincronizaciones", status_code=303)


def _clave_guardada(db: Session, rut_titular: str, settings) -> str:
    """Recupera la clave tributaria cifrada, o la del ``.env`` si corresponde."""
    credencial = db.scalars(select(Credencial).where(Credencial.rut == rut_titular)).first()
    if credencial:
        try:
            return descifrar(credencial.clave_cifrada, settings.clave_cifrado)
        except (ClaveCifradoAusente, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if settings.clave_tributaria and settings.rut:
        try:
            if str(parse_rut(settings.rut)) == rut_titular:
                return settings.clave_tributaria
        except RutInvalido:
            pass
    raise HTTPException(
        status_code=400,
        detail="No hay clave tributaria guardada para ese RUT. Añádela en Credenciales.",
    )


# --- Credenciales -----------------------------------------------------------
@app.get("/credenciales", response_class=HTMLResponse)
def vista_credenciales(request: Request, db: Session = Depends(get_db), error: str | None = None):
    return _render(
        request,
        db,
        "credenciales.html",
        titular=None,
        error=error,
        cifrado_configurado=bool(get_settings().clave_cifrado),
    )


@app.post("/credenciales")
def guardar_credencial(
    db: Session = Depends(get_db),
    rut: str = Form(...),
    clave: str = Form(...),
    alias: str = Form(""),
):
    try:
        rut_titular = str(parse_rut(rut))
        cifrada = cifrar(clave, get_settings().clave_cifrado)
    except (RutInvalido, ClaveCifradoAusente) as exc:
        return RedirectResponse(url=f"/credenciales?error={exc}", status_code=303)

    credencial = db.scalars(select(Credencial).where(Credencial.rut == rut_titular)).first()
    if credencial is None:
        credencial = Credencial(rut=rut_titular)
        db.add(credencial)
    credencial.clave_cifrada = cifrada
    credencial.alias = alias.strip()
    db.commit()
    return RedirectResponse(url="/credenciales", status_code=303)


@app.post("/credenciales/{credencial_id}/eliminar")
def eliminar_credencial(credencial_id: int, db: Session = Depends(get_db)):
    credencial = db.get(Credencial, credencial_id)
    if credencial:
        db.delete(credencial)
        db.commit()
    return RedirectResponse(url="/credenciales", status_code=303)


# --- API JSON ---------------------------------------------------------------
@app.get("/api/salud")
def salud():
    return {"estado": "ok", "version": __version__, "modo": get_settings().modo}


@app.get("/api/documentos")
def api_documentos(
    db: Session = Depends(get_db),
    titular: str = Query(...),
    desde: str | None = None,
    hasta: str | None = None,
    operacion: str | None = None,
    limite: int = Query(200, le=5000),
    offset: int = 0,
):
    filtro = _filtro(str(parse_rut(titular)), desde, hasta, operacion)
    documentos, total = reports.listar_documentos(db, filtro, limite=limite, offset=offset)
    return {
        "total": total,
        "documentos": [
            {
                "periodo": d.periodo,
                "operacion": d.operacion,
                "tipo_doc": d.tipo_doc,
                "tipo_doc_nombre": d.tipo_doc_nombre,
                "folio": d.folio,
                "rut_contraparte": d.rut_contraparte,
                "razon_social": d.razon_social,
                "fecha_emision": d.fecha_emision.isoformat() if d.fecha_emision else None,
                "monto_exento": float(d.monto_exento),
                "monto_neto": float(d.monto_neto),
                "monto_iva": float(d.monto_iva),
                "monto_total": float(d.monto_total),
                "estado_contab": d.estado_contab,
            }
            for d in documentos
        ],
    }


def _a_float(fila: dict, claves: tuple[str, ...]) -> dict:
    return {**fila, **{clave: float(fila[clave]) for clave in claves}}


@app.get("/api/reportes/periodo")
def api_reporte_periodo(
    db: Session = Depends(get_db),
    titular: str = Query(...),
    desde: str | None = None,
    hasta: str | None = None,
):
    filtro = _filtro(str(parse_rut(titular)), desde, hasta)
    return {
        "por_periodo": [
            _a_float(f, ("exento", "neto", "iva", "total")) for f in reports.por_periodo(db, filtro)
        ],
        "iva": [
            _a_float(f, ("iva_debito", "iva_credito", "saldo", "ventas_netas", "compras_netas"))
            for f in reports.resumen_iva(db, filtro)
        ],
    }


@app.get("/api/sincronizaciones/{sync_id}")
def api_sincronizacion(sync_id: int, db: Session = Depends(get_db)):
    trabajo = db.get(Sincronizacion, sync_id)
    if trabajo is None:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    return {
        "id": trabajo.id,
        "estado": trabajo.estado,
        "rut": trabajo.rut_titular,
        "desde": trabajo.periodo_desde,
        "hasta": trabajo.periodo_hasta,
        "nuevos": trabajo.nuevos,
        "actualizados": trabajo.actualizados,
        "mensaje": trabajo.mensaje,
        "archivos": trabajo.archivos,
    }
