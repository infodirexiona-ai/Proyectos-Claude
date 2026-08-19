"""Aplicación FastAPI: interfaz web y API JSON."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..crypto import ClaveCifradoAusente, cifrar, descifrar
from ..db import crear_esquema, get_db
from ..diagnostico import ejecutar_diagnostico
from ..models import Credencial, Documento, Sincronizacion
from ..rut import RutInvalido, parse_rut
from ..services import export, reports
from ..services.periodos import PeriodoInvalido, normalizar_fecha, periodo_actual, rango_periodos
from ..services.sync import (
    ruta_descarga,
    sincronizar_detalle_ventas_en_segundo_plano,
    sincronizar_en_segundo_plano,
)
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
    title="EaSII",
    description="Asistente de trámites del SII: descarga el Registro de Compras y Ventas y genera reportes.",
    version=__version__,
    lifespan=ciclo_de_vida,
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

plantillas = Jinja2Templates(directory=str(BASE / "templates"))
plantillas.env.filters.update(FILTROS)


# --- Utilidades compartidas -------------------------------------------------
def _contexto(request: Request, db: Session, **extra) -> dict:
    settings = get_settings()
    credenciales = list(db.scalars(select(Credencial).order_by(Credencial.rut_titular)))
    titulares = sorted({*reports.titulares(db), *(c.rut_titular for c in credenciales)})
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


_COOKIE_TITULAR = "titular_activo"


def _render(request: Request, db: Session, plantilla: str, **extra):
    respuesta = plantillas.TemplateResponse(request, plantilla, _contexto(request, db, **extra))
    titular = extra.get("titular")
    if titular:
        # Recuerda la última empresa consultada para que no se pierda al
        # cambiar de pestaña (Panel, Documentos, Reportes...), ya que el menú
        # no manda el RUT en cada link.
        respuesta.set_cookie(_COOKIE_TITULAR, titular, max_age=60 * 60 * 24 * 365, samesite="lax")
    return respuesta


def _titular_activo(db: Session, rut: str | None, cookie: str | None = None) -> str | None:
    """RUT sobre el que se está trabajando: el pedido, el de la última visita
    (cookie), o el primero disponible."""
    if rut:
        try:
            return str(parse_rut(rut))
        except RutInvalido:
            return None
    candidatos = reports.titulares(db)
    if cookie:
        try:
            candidato = str(parse_rut(cookie))
        except RutInvalido:
            candidato = None
        if candidato and candidato in candidatos:
            return candidato
    if candidatos:
        return candidatos[0]
    primera = db.scalars(select(Credencial).order_by(Credencial.id)).first()
    return primera.rut_titular if primera else None


_COOKIE_FILTROS_DOCUMENTOS = "filtros_documentos"
_CAMPOS_FILTRO_DOCUMENTOS = ("desde", "hasta", "operacion", "tipo_doc", "contraparte", "q", "orden")


def _filtros_guardados(request: Request) -> dict[str, str]:
    """Últimos filtros usados en Documentos, guardados en una cookie.

    Sólo se aplican cuando se entra a la página sin ningún parámetro en la
    URL (por ejemplo, al hacer clic en "Documentos" del menú) — así "Limpiar"
    y los links con filtros propios (paginación, "ver contraparte X" desde
    Reportes) siguen mandando exactamente lo que dicen, sin que la cookie se
    entrometa.
    """
    crudo = request.cookies.get(_COOKIE_FILTROS_DOCUMENTOS)
    if not crudo:
        return {}
    try:
        datos = json.loads(crudo)
    except ValueError:
        return {}
    return datos if isinstance(datos, dict) else {}


def _entero_opcional(valor: str | None) -> int | None:
    """Convierte a int, tratando "" (lo que manda un <select> en "Todos") como ausente.

    FastAPI/Pydantic no acepta "" para un parámetro declarado ``int | None``:
    lo intenta parsear como entero y falla. Los filtros de la web mandan sus
    campos vacíos como "" en vez de omitirlos, así que las rutas reciben el
    valor crudo como texto y lo convierten con esta función.
    """
    return int(valor) if valor else None


def _paginas_visibles(pagina: int, paginas: int) -> list[int | None]:
    """Números de página a mostrar en el paginador, con ``None`` como "…".

    Siempre muestra la primera, la última, y un par de páginas alrededor de
    la actual — para no listar cientos de números cuando hay muchas páginas.
    """
    if paginas <= 7:
        return list(range(1, paginas + 1))

    visibles = {1, paginas, pagina - 1, pagina, pagina + 1}
    visibles = sorted(p for p in visibles if 1 <= p <= paginas)

    resultado: list[int | None] = []
    anterior = None
    for p in visibles:
        if anterior is not None and p - anterior > 1:
            resultado.append(None)
        resultado.append(p)
        anterior = p
    return resultado


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
    activo = _titular_activo(db, titular, request.cookies.get(_COOKIE_TITULAR))
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
    tipo_doc: str | None = Query(None),
    contraparte: str | None = Query(None),
    q: str | None = Query(None),
    orden: str | None = Query(None),
    pagina: int = Query(1, ge=1),
):
    activo = _titular_activo(db, titular, request.cookies.get(_COOKIE_TITULAR))

    if not request.url.query:
        # Se entró sin ningún parámetro (p. ej. clic en "Documentos" del
        # menú): restaura los últimos filtros usados en vez de mostrar
        # siempre "Todos". Cualquier parámetro explícito (incluido
        # "Limpiar", que sólo manda ?titular=) desactiva esto.
        guardados = _filtros_guardados(request)
        desde = guardados.get("desde") or None
        hasta = guardados.get("hasta") or None
        operacion = guardados.get("operacion") or None
        tipo_doc = guardados.get("tipo_doc") or None
        contraparte = guardados.get("contraparte") or None
        q = guardados.get("q") or None
        orden = guardados.get("orden") or None

    orden = orden or "fecha"

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
    filtro = _filtro(activo, desde, hasta, operacion, _entero_opcional(tipo_doc), contraparte, q)
    try:
        documentos, total = reports.listar_documentos(
            db, filtro, limite=por_pagina, offset=(pagina - 1) * por_pagina, orden=orden
        )
        resumen = reports.totales(db, filtro)
    except PeriodoInvalido as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    paginas = max(1, -(-total // por_pagina))
    filtros_efectivos = {
        "desde": desde or "",
        "hasta": hasta or "",
        "operacion": operacion or "",
        "tipo_doc": tipo_doc or "",
        "contraparte": contraparte or "",
        "q": q or "",
        "orden": orden,
    }
    respuesta = _render(
        request,
        db,
        "documentos.html",
        titular=activo,
        documentos=documentos,
        total=total,
        resumen=resumen,
        pagina=pagina,
        paginas=paginas,
        paginas_visibles=_paginas_visibles(pagina, paginas),
        orden=orden,
        filtros=filtros_efectivos,
        periodos=reports.periodos_disponibles(db, activo),
    )
    respuesta.set_cookie(
        _COOKIE_FILTROS_DOCUMENTOS,
        json.dumps(filtros_efectivos),
        max_age=60 * 60 * 24 * 365,
        samesite="lax",
    )
    return respuesta


@app.post("/documentos/eliminar-todo")
def eliminar_documentos_titular(
    db: Session = Depends(get_db),
    titular: str = Form(...),
    operacion: str = Form(""),
    volver: str = Form("/documentos"),
):
    """Borra los documentos guardados de un titular (no la credencial).

    ``operacion`` acota el borrado a compras o a ventas; vacío borra ambas.
    Pensado para limpiar datos de prueba o de un RUT equivocado. No toca el
    historial de Descargas ni la credencial guardada — esos se borran aparte.
    """
    try:
        rut_titular = str(parse_rut(titular))
    except RutInvalido as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    condiciones = [Documento.rut_titular == rut_titular]
    if operacion in (ep.COMPRA, ep.VENTA):
        condiciones.append(Documento.operacion == operacion)
    db.execute(delete(Documento).where(*condiciones))
    db.commit()
    destino = volver if volver in ("/documentos", "/reportes") else "/documentos"
    return RedirectResponse(url=f"{destino}?titular={rut_titular}", status_code=303)


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
    activo = _titular_activo(db, titular, request.cookies.get(_COOKIE_TITULAR))
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
    tipo_doc: str | None = Query(None),
    contraparte: str | None = Query(None),
    q: str | None = Query(None),
):
    filtro = _filtro_de_exportacion(
        db, titular, desde, hasta, operacion, _entero_opcional(tipo_doc), contraparte, q
    )
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
    tipo_doc: str | None = Query(None),
    contraparte: str | None = Query(None),
    q: str | None = Query(None),
):
    filtro = _filtro_de_exportacion(
        db, titular, desde, hasta, operacion, _entero_opcional(tipo_doc), contraparte, q
    )
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


@app.post("/sincronizaciones/{sync_id}/eliminar")
def eliminar_sincronizacion(sync_id: int, db: Session = Depends(get_db)):
    """Borra un registro del historial de descargas. No toca los documentos ya guardados."""
    trabajo = db.get(Sincronizacion, sync_id)
    if trabajo:
        db.delete(trabajo)
        db.commit()
    return RedirectResponse(url="/sincronizaciones", status_code=303)


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
    rut_acceso: str | None = Form(None),
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
    rut_login = (rut_acceso or "").strip() or None
    if not clave_final and settings.modo != "demo":
        rut_login, clave_final = _clave_guardada(db, rut_titular, settings)

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
        rut_login=rut_login,
        sync_id=trabajo.id,
    )
    return RedirectResponse(url="/sincronizaciones", status_code=303)


@app.post("/sincronizar-detalle-ventas")
def lanzar_sincronizacion_detalle_ventas(
    tareas: BackgroundTasks,
    db: Session = Depends(get_db),
    rut: str = Form(...),
    desde: str = Form(...),
    hasta: str = Form(...),
    clave: str | None = Form(None),
    rut_acceso: str | None = Form(None),
):
    """Trae la glosa de ventas ya descargadas por el RCV, vía el MIPYME.

    Sólo sirve para documentos que el propio contribuyente emitió con el
    facturador gratuito del SII, y sólo actualiza documentos que ya existan
    (hay que haber corrido una descarga normal para ese rango antes).

    ``desde``/``hasta`` aceptan un periodo ``YYYYMM`` o, para acotar un tramo
    puntual (por ejemplo, reintentar sólo los días que fallaron la vez
    anterior), una fecha exacta ``YYYYMMDD``.
    """
    settings = get_settings()
    try:
        rut_titular = str(parse_rut(rut))
        fecha_desde = normalizar_fecha(desde)
        fecha_hasta = normalizar_fecha(hasta, fin=True)
    except (RutInvalido, PeriodoInvalido) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if fecha_desde > fecha_hasta:
        raise HTTPException(status_code=400, detail="La fecha inicial es posterior a la final")

    periodo_desde = f"{fecha_desde:%Y%m}"
    periodo_hasta = f"{fecha_hasta:%Y%m}"

    clave_final = (clave or "").strip()
    rut_login = (rut_acceso or "").strip() or None
    if not clave_final and settings.modo != "demo":
        rut_login, clave_final = _clave_guardada(db, rut_titular, settings)

    trabajo = Sincronizacion(
        rut_titular=rut_titular,
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
        operaciones="VENTA_DETALLE",
    )
    db.add(trabajo)
    db.commit()
    db.refresh(trabajo)

    tareas.add_task(
        sincronizar_detalle_ventas_en_segundo_plano,
        rut=rut_titular,
        clave_tributaria=clave_final,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        rut_login=rut_login,
        sync_id=trabajo.id,
    )
    return RedirectResponse(url="/sincronizaciones", status_code=303)


def _clave_guardada(db: Session, rut_titular: str, settings) -> tuple[str, str]:
    """Recupera con quién iniciar sesión y la clave tributaria para un titular.

    Devuelve ``(rut_login, clave)``. ``rut_login`` puede ser distinto del
    titular cuando la credencial guardada es la de un representante legal.
    """
    credencial = db.scalars(select(Credencial).where(Credencial.rut_titular == rut_titular)).first()
    if credencial:
        try:
            return credencial.rut, descifrar(credencial.clave_cifrada, settings.clave_cifrado)
        except (ClaveCifradoAusente, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if settings.clave_tributaria and settings.rut:
        try:
            if str(parse_rut(settings.rut)) == rut_titular:
                return rut_titular, settings.clave_tributaria
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
    rut_acceso: str = Form(""),
):
    """Guarda una credencial. ``rut`` es el titular (de quién son los
    documentos); ``rut_acceso`` es con quién se inicia sesión, si es distinto
    (por ejemplo, el representante legal de una empresa). Si se deja en
    blanco, se asume que son el mismo RUT.
    """
    try:
        rut_titular = str(parse_rut(rut))
        rut_login = str(parse_rut(rut_acceso)) if rut_acceso.strip() else rut_titular
        cifrada = cifrar(clave, get_settings().clave_cifrado)
    except (RutInvalido, ClaveCifradoAusente) as exc:
        return RedirectResponse(url=f"/credenciales?error={exc}", status_code=303)

    credencial = db.scalars(select(Credencial).where(Credencial.rut_titular == rut_titular)).first()
    if credencial is None:
        credencial = Credencial(rut_titular=rut_titular)
        db.add(credencial)
    credencial.rut = rut_login
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


# --- Diagnóstico -------------------------------------------------------------
@app.get("/diagnostico", response_class=HTMLResponse)
def vista_diagnostico(request: Request, db: Session = Depends(get_db), red: bool = Query(True)):
    chequeos = ejecutar_diagnostico(revisar_red=red)
    return _render(
        request,
        db,
        "diagnostico.html",
        titular=None,
        chequeos=chequeos,
        todo_listo=all(c.ok for c in chequeos),
        reviso_red=red,
    )


# --- API JSON ---------------------------------------------------------------
@app.get("/api/salud")
def salud():
    return {"estado": "ok", "version": __version__, "modo": get_settings().modo}


@app.get("/api/diagnostico")
def api_diagnostico(red: bool = Query(True)):
    chequeos = ejecutar_diagnostico(revisar_red=red)
    return {
        "listo": all(c.ok for c in chequeos),
        "chequeos": [{"nombre": c.nombre, "ok": c.ok, "detalle": c.detalle} for c in chequeos],
    }


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
