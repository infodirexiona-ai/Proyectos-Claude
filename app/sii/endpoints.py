"""URLs, endpoints y constantes del portal del SII.

Todo lo específico del portal vive aquí para que, cuando el SII cambie algo,
sólo haya que tocar este archivo.

El flujo del RCV (incluidos el paso JSONP ``AutTknData.cgi`` y el token de
recaptcha ``t-o-k-e-n-web``) está portado desde el SDK de código abierto
`emisso-ai/emisso-sii` (MIT), que lo documentó a partir del bundle Angular
del portal.
"""

from __future__ import annotations

# --- Entornos ---------------------------------------------------------------
PRODUCCION = "produccion"
CERTIFICACION = "certificacion"

AUTH_URL = "https://zeusr.sii.cl"


def base_rcv(entorno: str = PRODUCCION) -> str:
    """Host del Registro de Compras y Ventas."""
    return "https://www4.sii.cl" if entorno == PRODUCCION else "https://www4c.sii.cl"


def referencia_post_login(entorno: str = PRODUCCION) -> str:
    """URL a la que el portal redirige tras autenticarse."""
    return (
        "https://misii.sii.cl/cgi_misii/siihome.cgi"
        if entorno == PRODUCCION
        else "https://misiir.sii.cl/cgi_misii/siihome.cgi"
    )


def url_login(entorno: str = PRODUCCION) -> str:
    """Formulario de RUT + clave tributaria.

    El SII espera la ``referencia`` como query string desnuda, sin nombre de
    parámetro; no es un error de construcción de la URL.
    """
    return f"{AUTH_URL}/AUT2000/InicioAutenticacion/IngresoRutClave.html?{referencia_post_login(entorno)}"


URL_LOGOUT = f"{AUTH_URL}/cgi_AUT2000/CAutInwor498.cgi?https://www.sii.cl"

# Selectores del formulario de login.
SEL_RUT = "#rutcntr"
SEL_CLAVE = "#clave"
SEL_BOTON = "#bt_ingresar"

# --- Servicios del RCV ------------------------------------------------------
_FACADE = "/consdcvinternetui/services/data/facadeService"

ENDPOINTS = {
    "datos_inicio": f"{_FACADE}/getDatosInicio",
    "resumen": f"{_FACADE}/getResumen",
    "resumen_export": f"{_FACADE}/getResumenExport",
    "detalle_compra": f"{_FACADE}/getDetalleCompra",
    "detalle_compra_export": f"{_FACADE}/getDetalleCompraExport",
    "detalle_venta": f"{_FACADE}/getDetalleVenta",
    "detalle_venta_export": f"{_FACADE}/getDetalleVentaExport",
    "parametros": "/consdcvinternetui/services/data/settingsService/consultarParametros",
    "sesion_load": "/common-1.0/services/aaSessionService/load",
    "auth_conf": "/common-1.0/services/autConfDataService/obtieneConf",
}

NAMESPACE = "cl.sii.sdi.lob.diii.consdcv.data.api.interfaces.FacadeService"

# El SPA envía este literal en lugar de resolver un recaptcha real.
TOKEN_RECAPTCHA = "t-o-k-e-n-web"

# Secciones del RCV.
REGISTRO = "REGISTRO"
PENDIENTE = "PENDIENTE"
NO_INCLUIR = "NO_INCLUIR"
RECLAMADO = "RECLAMADO"
ESTADOS_CONTAB = (REGISTRO, PENDIENTE, NO_INCLUIR, RECLAMADO)

COMPRA = "COMPRA"
VENTA = "VENTA"
OPERACIONES = (COMPRA, VENTA)

# Texto que el portal devuelve cuando la sesión murió.
SENTINELA_SESION_CAIDA = "NO ESTA AUTENTICADO"

# Observado en producción (agosto 2026): getResumen devuelve codRespuesta=3, sin
# mensaje, cuando el periodo no tiene documentos de ese tipo — en vez de una
# lista vacía. No está documentado por el SII; si en el futuro se ve con otro
# significado, hay que sacarlo de aquí.
RESUMEN_CODIGO_SIN_DOCUMENTOS = 3

# --- Tipos de documento tributario electrónico ------------------------------
TIPOS_DTE = {
    30: "Factura",
    32: "Factura de venta bienes y servicios no afectos o exentos de IVA",
    33: "Factura electrónica",
    34: "Factura no afecta o exenta electrónica",
    35: "Boleta",
    38: "Boleta exenta",
    39: "Boleta electrónica",
    41: "Boleta exenta electrónica",
    43: "Liquidación factura electrónica",
    45: "Factura de compra",
    46: "Factura de compra electrónica",
    52: "Guía de despacho electrónica",
    56: "Nota de débito electrónica",
    60: "Nota de crédito",
    61: "Nota de crédito electrónica",
    110: "Factura de exportación electrónica",
    111: "Nota de débito de exportación electrónica",
    112: "Nota de crédito de exportación electrónica",
}


def nombre_tipo_dte(codigo: int | str | None) -> str:
    try:
        return TIPOS_DTE.get(int(codigo), f"Tipo {codigo}")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "Desconocido"


# --- Sistema de Facturación Gratuita del SII (MIPYME) -----------------------
#
# Portal distinto al RCV: no es una SPA Angular sino páginas CGI clásicas
# (``www1.sii.cl/cgi-bin/Portal001``). Sirve para volver a ver, con el detalle
# línea por línea (glosa incluida), los documentos que el propio contribuyente
# emitió con el facturador gratuito del SII. No aplica a documentos recibidos
# ni a documentos emitidos con un facturador privado (Nubox, Bsale, etc.).
#
# Flujo verificado a mano contra el portal real en agosto de 2026 (a diferencia
# del resto de ``app/sii``, que se portó de investigación de terceros sin poder
# probarlo en vivo).
MIPE_BASE = "https://www1.sii.cl"
MIPE_ADMIN_DOCS = f"{MIPE_BASE}/cgi-bin/Portal001/mipeAdminDocsEmi.cgi"
MIPE_DOWNLOAD = f"{MIPE_BASE}/cgi-bin/Portal001/mipeDownLoad.cgi"

# Cuando el RUT autenticado representa a más de una empresa (el representante
# legal de varias sociedades, por ejemplo), el SII exige elegir con cuál
# operar antes de dejar entrar a cualquier página del MIPYME — si no, redirige
# de vuelta a este menú general sin importar qué URL se haya pedido.
MIPE_MENU = f"{MIPE_BASE}/factura_sii/factura_sii.htm"
MIPE_SELECCIONAR_EMPRESA = f"{MIPE_BASE}/cgi-bin/Portal001/mipeSelEmpresa.cgi"
# Campo oculto que trae el formulario real; sin él el SII también redirige al
# menú aunque el RUT_EMP sea válido.
MIPE_SELECCION_EMPRESA_ORIGEN = "OPCION=2&TIPO=4"

# El botón "Archivo Respaldo" dispara un reCAPTCHA invisible antes de armar la
# URL de descarga, así que hay que hacer clic de verdad en un navegador, igual
# que en el login: no basta con pedir la URL directamente.
MIPE_BOTON_RESPALDO = "input[name='Button_xml']"
MIPE_CAMPO_RUT_RECEPTOR = "input[name='RUT_RECP']"
MIPE_CAMPO_FOLIO = "input[name='FOLIO']"
MIPE_CAMPO_FECHA_DESDE = "input[name='FEC_DESDE']"
MIPE_CAMPO_FECHA_HASTA = "input[name='FEC_HASTA']"
MIPE_CAMPO_TIPO_DOC = "select[name='TPO_DOC']"
MIPE_BOTON_BUSCAR = "input[name='BTN_SUBMIT']"

# El servidor rechaza una descarga que reúna más de este número de documentos:
# muestra un diálogo nativo ("<host> dice") pidiendo acotar la búsqueda, en vez
# de truncar el resultado. Hay que partir el rango de fechas y reintentar.
MIPE_MAX_DOCUMENTOS_POR_DESCARGA = 20
MIPE_TEXTO_DEMASIADOS_DOCUMENTOS = "demasiados documentos"
