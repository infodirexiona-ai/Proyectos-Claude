"""Pruebas del parser del XML de respaldo del facturador gratuito (MIPYME).

El fragmento usado en ``XML_EJEMPLO`` reproduce la estructura real que entrega
"Archivo Respaldo", verificada a mano contra el portal (ver app/sii/mipe.py).
"""

from datetime import date
from decimal import Decimal

from app.sii.mipe_parser import parsear_respaldo_mipyme

XML_EJEMPLO = """<?xml version="1.0" encoding="ISO-8859-1"?>
<SetDTE>
<DTE version="1.0" >
<Documento ID="MiPE1-1">
    <Encabezado>
      <IdDoc>
        <TipoDTE>34</TipoDTE>
        <Folio>271</Folio>
        <FchEmis>2026-08-06</FchEmis>
      </IdDoc>
      <Emisor>
        <RUTEmisor>76760089-5</RUTEmisor>
        <RznSoc>CONSULTORA DE PRUEBA</RznSoc>
      </Emisor>
      <Receptor>
        <RUTRecep>78262927-1</RUTRecep>
        <RznSocRecep>CLIENTE DE PRUEBA SPA</RznSocRecep>
      </Receptor>
      <Totales>
        <MntExe>300000</MntExe>
        <MntTotal>300000</MntTotal>
      </Totales>
    </Encabezado>
    <Detalle>
      <NroLinDet>1</NroLinDet>
      <NmbItem>Asesoría empresarial</NmbItem>
      <QtyItem>1.00</QtyItem>
      <PrcItem>300000.00</PrcItem>
      <MontoItem>300000</MontoItem>
    </Detalle>
    <TmstFirma>2026-08-06T11:19:48</TmstFirma>
  </Documento>
</DTE>
<DTE version="1.0" >
<Documento ID="MiPE1-2">
    <Encabezado>
      <IdDoc>
        <TipoDTE>33</TipoDTE>
        <Folio>50</Folio>
        <FchEmis>2026-05-10</FchEmis>
      </IdDoc>
      <Emisor>
        <RUTEmisor>76760089-5</RUTEmisor>
        <RznSoc>CONSULTORA DE PRUEBA</RznSoc>
      </Emisor>
      <Receptor>
        <RUTRecep>76655600-0</RUTRecep>
        <RznSocRecep>OTRO CLIENTE LTDA</RznSocRecep>
      </Receptor>
      <Totales>
        <MntNeto>1000000</MntNeto>
        <IVA>190000</IVA>
        <MntTotal>1190000</MntTotal>
      </Totales>
    </Encabezado>
    <Detalle>
      <NroLinDet>1</NroLinDet>
      <NmbItem>Desarrollo Plan difusión</NmbItem>
      <DscItem>en medios y gestión de redes sociales.
Proyecto Chacabuco, Territorio Encadenado Productivamente Codigo 24VIRM2-265619</DscItem>
      <QtyItem>1.00</QtyItem>
      <PrcItem>700000.00</PrcItem>
      <MontoItem>700000</MontoItem>
    </Detalle>
    <Detalle>
      <NroLinDet>2</NroLinDet>
      <NmbItem>Consultoría adicional</NmbItem>
      <QtyItem>3.00</QtyItem>
      <PrcItem>100000.00</PrcItem>
      <MontoItem>300000</MontoItem>
    </Detalle>
    <TmstFirma>2026-05-10T11:19:48</TmstFirma>
  </Documento>
</DTE>
</SetDTE>
"""


def test_parsea_varios_documentos():
    docs = parsear_respaldo_mipyme(XML_EJEMPLO)
    assert len(docs) == 2
    assert [d.clave for d in docs] == [(34, 271), (33, 50)]


def test_documento_exento_una_linea():
    doc = parsear_respaldo_mipyme(XML_EJEMPLO)[0]
    assert doc.tipo_doc == 34
    assert doc.folio == 271
    assert doc.fecha_emision == date(2026, 8, 6)
    assert doc.rut_receptor == "78262927-1"
    assert doc.monto_exento == Decimal("300000")
    assert doc.monto_total == Decimal("300000")
    assert len(doc.lineas) == 1
    assert doc.lineas[0].descripcion == "Asesoría empresarial"


def test_junta_nombre_corto_y_descripcion_larga():
    """NmbItem (nombre corto) + DscItem (cuadro de texto largo) = la glosa completa."""
    doc = parsear_respaldo_mipyme(XML_EJEMPLO)[1]
    linea = doc.lineas[0]
    assert linea.descripcion == (
        "Desarrollo Plan difusión en medios y gestión de redes sociales. "
        "Proyecto Chacabuco, Territorio Encadenado Productivamente Codigo 24VIRM2-265619"
    )


def test_documento_afecto_usa_tag_iva_no_mntiva():
    doc = parsear_respaldo_mipyme(XML_EJEMPLO)[1]
    assert doc.monto_neto == Decimal("1000000")
    assert doc.monto_iva == Decimal("190000")
    assert doc.monto_total == Decimal("1190000")


def test_documento_con_varias_lineas_de_detalle():
    doc = parsear_respaldo_mipyme(XML_EJEMPLO)[1]
    assert len(doc.lineas) == 2
    assert doc.lineas[1].descripcion == "Consultoría adicional"
    assert doc.lineas[1].cantidad == Decimal("3.00")
    assert doc.lineas[1].monto == Decimal("300000")


def test_linea_a_dict_es_serializable():
    doc = parsear_respaldo_mipyme(XML_EJEMPLO)[0]
    bruto = doc.lineas[0].a_dict()
    assert bruto["nombre_corto"] == "Asesoría empresarial"
    assert bruto["descripcion_larga"] == ""
    assert bruto["monto"] == "300000"


def test_nombre_corto_y_descripcion_larga_quedan_separados():
    """NmbItem y DscItem se guardan aparte, no sólo unidos en un texto."""
    doc = parsear_respaldo_mipyme(XML_EJEMPLO)[1]
    linea = doc.lineas[0]
    assert linea.nombre_corto == "Desarrollo Plan difusión"
    assert linea.descripcion_larga == (
        "en medios y gestión de redes sociales. "
        "Proyecto Chacabuco, Territorio Encadenado Productivamente Codigo 24VIRM2-265619"
    )
    # .descripcion sigue disponible como conveniencia: ambas juntas.
    assert linea.descripcion == f"{linea.nombre_corto} {linea.descripcion_larga}"


def test_xml_vacio_o_invalido():
    assert parsear_respaldo_mipyme("") == []
    assert parsear_respaldo_mipyme("<no-es-un-set-dte/>") == []
    assert parsear_respaldo_mipyme("esto no es xml") == []


def test_acepta_bytes_en_iso_8859_1():
    datos = XML_EJEMPLO.encode("iso-8859-1")
    docs = parsear_respaldo_mipyme(datos)
    assert len(docs) == 2
    assert "difusión" in docs[1].lineas[0].descripcion
