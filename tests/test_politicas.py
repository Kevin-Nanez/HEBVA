"""Extracción de los 4 PDF de políticas y fragmentos con metadatos (T1.5).

Las tablas esperadas de abajo se transcribieron de las páginas del PDF renderizadas como imagen
(pypdfium2) el 2026-10-31, no de la salida de pdfplumber: así la prueba compara la extracción
contra lo que se ve en el PDF. Cubren las tablas que pide la ficha de T1.5: MAN-OPS-007 §6.2 y
§9.2, PRO-SAC-021 §3, §6 y §9, POL-OPS-014 §4 y §6 y FAQ-OPS-001 P20.

Cada tabla se revisa tres veces: en la extracción fresca desde el PDF, en la copia revisada de
politicas_md/ y en el fragmento guardado en DuckDB, que además trae las tripletas
"fila, columna = valor". Las bases se crean en directorios temporales, nunca en build/.
"""

import os
import re
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from agente import construir, extraer_politicas, fragmentos_politicas

RAIZ = Path(__file__).resolve().parent.parent
DIR_PDF = RAIZ / "politicas"
DIR_MD = RAIZ / "politicas_md"

PDF_POR_CODIGO = {
    "MAN-OPS-007": "manual_apertura_y_cierre_tienda.pdf",
    "PRO-SAC-021": "procedimiento_devoluciones.pdf",
    "POL-OPS-014": "politica_mermas_y_caducidad.pdf",
    "FAQ-OPS-001": "faq_gerentes_de_tienda.pdf",
}

VERSIONES = {"MAN-OPS-007": "2.5", "PRO-SAC-021": "4.0", "POL-OPS-014": "3.2", "FAQ-OPS-001": "1.4"}

# Un fragmento por sección de primer nivel y uno por pregunta de la FAQ; los encabezados de bloque
# de la FAQ (2 a 6) no tienen texto propio. 11 + 11 + 13 + 27 = 62, la cifra de la línea base §5.
FRAGMENTOS_POR_DOCUMENTO = {"MAN-OPS-007": 11, "PRO-SAC-021": 11, "POL-OPS-014": 13, "FAQ-OPS-001": 27}

TABLAS_ESPERADAS = {
    ("MAN-OPS-007", "6"): [
        ["Diferencia por caja", "Clasificación", "Acción"],
        ["Hasta 20.00 pesos", "Tolerable", "Se registra; no requiere acción adicional."],
        ["De 20.01 a 200.00 pesos", "Menor",
         "Se registra y se comenta con el cajero; tres eventos en un mes generan plan de mejora."],
        ["De 200.01 a 1,000.00 pesos", "Mayor",
         "Se registra, se reporta al Gerente de Tienda el mismo día y se levanta acta administrativa."],
        ["Más de 1,000.00 pesos", "Crítica",
         "Se reporta de inmediato al Gerente de Tienda y a Prevención de Pérdidas; se revisa "
         "videograbación de la caja."],
    ],
    ("MAN-OPS-007", "9"): [
        ["Incidencia", "Acción de primer nivel en tienda", "Prioridad del ticket si persiste"],
        ["Terminal de punto de venta sin acceso al sistema", "Reiniciar terminal; verificar cable de red.", "Alta"],
        ["Pérdida total del enlace de datos de la tienda",
         "Activar modo sin conexión en cajas; avisar a Mesa de Servicio por teléfono.", "Crítica"],
        ["Báscula de perecederos no imprime etiquetas", "Reiniciar báscula; verificar rollo de etiquetas.", "Media"],
        ["Handheld de SIGMA Móvil no sincroniza",
         "Cerrar y abrir la aplicación; conectar a la red inalámbrica de tienda.", "Baja"],
        ["Terminal bancaria rechaza todas las tarjetas",
         "Reiniciar terminal bancaria; probar con tarjeta de prueba de la tienda.", "Alta"],
        ["Servidor de tienda apagado o sin respuesta", "No manipular; llamar de inmediato a Mesa de Servicio.",
         "Crítica"],
    ],
    ("PRO-SAC-021", "3"): [
        ["Tipo de producto", "Plazo para devolución o cambio", "Requisitos"],
        ["Abarrotes y no perecederos", "30 días", "Ticket de compra; producto cerrado y en buen estado."],
        ["Perecederos", "48 horas",
         "Ticket de compra; el producto debe presentar defecto de calidad (descomposición, mal olor, "
         "cuerpo extraño, caducidad vencida al momento de la compra)."],
        ["Electrodomésticos y electrónicos menores", "30 días naturales",
         "Ticket de compra, empaque original completo con accesorios y manuales."],
        ["Ropa y textiles", "30 días", "Ticket de compra; prenda sin uso, con etiquetas originales."],
        ["Medicamentos", "Sin devolución",
         "No aplica, por disposición sanitaria. Únicamente procede cambio por defecto de fabricación "
         "visible o error de despacho documentado."],
        ["Artículos de temporada (navideños, escolares, etc.)", "15 días",
         "Ticket de compra; producto cerrado. No procede después de concluida la temporada."],
        ["Vales, tarjetas de regalo y recargas", "Sin devolución", "No aplica una vez activados."],
    ],
    ("PRO-SAC-021", "6"): [
        ["Forma de pago original", "Forma de reembolso", "Tiempo estimado de acreditación"],
        ["Efectivo", "Efectivo en caja de Servicio al Cliente", "Inmediato"],
        ["Tarjeta de débito", "Reverso a la misma tarjeta", "3 a 5 días hábiles, según banco emisor"],
        ["Tarjeta de crédito", "Reverso a la misma tarjeta", "1 a 2 ciclos de facturación, según banco emisor"],
        ["Vale de tienda o tarjeta de regalo", "Nuevo vale de tienda", "Inmediato"],
        ["Monedero electrónico de lealtad", "Abono al mismo monedero", "Inmediato"],
        ["Pago mixto (dos o más formas)",
         "Se reembolsa primero a tarjeta hasta el monto pagado con ella; el resto en la forma correspondiente",
         "Según cada forma"],
        ["Pago en línea (pedido de ecommerce)",
         "Reverso al medio de pago del pedido, gestionado por el área de Ecommerce", "5 a 10 días hábiles"],
    ],
    ("PRO-SAC-021", "9"): [
        ["Rango de monto (pesos)", "Quién autoriza", "Observaciones"],
        ["Hasta 500.00", "Ejecutivo de Servicio al Cliente", "Autorización directa en terminal."],
        ["De 500.01 a 3,000.00", "Supervisor de Servicio al Cliente", "Firma electrónica en terminal."],
        ["De 3,000.01 a 10,000.00", "Gerente de Servicio al Cliente o Subgerente de Tienda",
         "Firma electrónica y captura de motivo."],
        ["De 10,000.01 a 30,000.00", "Gerente de Tienda",
         "Firma electrónica, captura de motivo y copia de identificación del cliente."],
        ["Más de 30,000.00", "Gerente de Tienda con visto bueno del Gerente Regional de Servicio al Cliente",
         "Se documenta por correo electrónico antes de ejecutar la operación."],
    ],
    ("POL-OPS-014", "4"): [
        ["Categoría", "Tolerancia mensual (% de venta)", "Umbral de alerta (%)", "Frecuencia de conteo"],
        ["Lácteos", "1.8", "2.8", "Semanal"],
        ["Panadería", "3.5", "4.5", "Diaria"],
        ["Carnes", "2.5", "3.5", "Diaria"],
        ["Frutas y verduras", "4.5", "5.5", "Diaria"],
        ["Abarrotes", "0.6", "1.6", "Mensual"],
        ["Bebidas", "0.8", "1.8", "Mensual"],
        ["Limpieza", "0.4", "1.4", "Trimestral"],
        ["Cuidado personal", "0.5", "1.5", "Trimestral"],
    ],
    ("POL-OPS-014", "6"): [
        ["Días restantes antes de la fecha de caducidad", "Descuento sobre precio regular"],
        ["5 días", "15 %"],
        ["4 días", "25 %"],
        ["3 días", "35 %"],
        ["2 días", "50 %"],
        ["1 día (solo perecederos no lácteos)", "60 %"],
    ],
    ("FAQ-OPS-001", "P20"): [
        ["Prioridad", "Cuándo aplica", "Tiempo de primera respuesta", "Tiempo objetivo de solución"],
        ["Baja", "Incidencia que no afecta la venta ni la operación (un handheld de varios, una impresora "
         "de oficina).", "8 horas hábiles", "5 días hábiles"],
        ["Media", "Afecta parcialmente la operación pero existe alternativa (una caja sin impresora, una "
         "báscula de varias).", "4 horas hábiles", "2 días hábiles"],
        ["Alta", "Afecta de forma importante la venta o la seguridad (varias cajas fuera, terminal bancaria "
         "general, remodelación mayor).", "1 hora", "8 horas"],
        ["Crítica", "Detiene la operación de la tienda o pone en riesgo el producto o a las personas "
         "(servidor caído, sin enlace de datos, planta de emergencia que no arranca, cámara de "
         "refrigeración fuera).", "15 minutos", "4 horas"],
    ],
}

# Página donde está cada tabla (la del encabezado de su subsección o pregunta).
PAGINA_DE_LA_TABLA = {
    ("MAN-OPS-007", "6"): ("6.2", 4),
    ("MAN-OPS-007", "9"): ("9.2", 7),
    ("PRO-SAC-021", "3"): ("3", 2),
    ("PRO-SAC-021", "6"): ("6", 4),
    ("PRO-SAC-021", "9"): ("9", 6),
    ("POL-OPS-014", "4"): ("4", 3),
    ("POL-OPS-014", "6"): ("6", 4),
    ("FAQ-OPS-001", "P20"): ("P20", 5),
}


@pytest.fixture(scope="module")
def extraidos():
    """Extracción fresca de los 4 PDF con pdfplumber, sin pasar por politicas_md/."""
    return {codigo: extraer_politicas.extraer_pdf(DIR_PDF / archivo) for codigo, archivo in PDF_POR_CODIGO.items()}


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """Base DuckDB en un directorio temporal con los fragmentos de política."""
    ruta = tmp_path_factory.mktemp("politicas") / "tienda.duckdb"
    con = duckdb.connect(str(ruta))
    resumen = construir.construir_politicas(con, DIR_PDF, DIR_MD)
    yield con, resumen
    con.close()


def _fragmentos(con, documento):
    filas = con.execute(
        "SELECT seccion, version, pagina, pagina_fin, subsecciones, texto FROM fragmentos "
        "WHERE documento = ? ORDER BY orden",
        [documento],
    ).fetchall()
    columnas = ["seccion", "version", "pagina", "pagina_fin", "subsecciones", "texto"]
    return {fila[0]: dict(zip(columnas, fila)) for fila in filas}


def _seccion_del_markdown(markdown, seccion):
    """Texto de un fragmento de la copia revisada, por su sección."""
    for fragmento in fragmentos_politicas.fragmentar(markdown):
        if fragmento.seccion == seccion:
            return fragmento.texto
    raise AssertionError(f"No hay fragmento de la sección {seccion}")


def _unica_tabla(texto):
    tablas = fragmentos_politicas.tablas(texto)
    assert len(tablas) == 1, f"Se esperaba una tabla y hay {len(tablas)}"
    return tablas[0]


def _monto(texto):
    return Decimal(texto.replace(",", ""))


def _rango(celda):
    """Convierte 'Hasta 20.00 pesos', 'De 20.01 a 200.00' o 'Más de 1,000.00' en (mínimo, máximo)."""
    numeros = [_monto(n) for n in re.findall(r"\d[\d,]*\.\d{2}", celda)]
    if celda.startswith("Hasta"):
        return Decimal("0"), numeros[0]
    if celda.startswith("Más de"):
        return numeros[0], None
    return numeros[0], numeros[1]


def _fila_del_monto(tabla, monto):
    for fila in tabla[1:]:
        minimo, maximo = _rango(fila[0])
        if minimo <= monto and (maximo is None or monto <= maximo):
            return fila
    raise AssertionError(f"Ningún rango de la tabla contiene {monto}")


def _fila(tabla, primera_celda):
    for fila in tabla[1:]:
        if fila[0] == primera_celda:
            return fila
    raise AssertionError(f"No hay fila «{primera_celda}»")


# --- Tablas celda por celda contra el PDF ---------------------------------------------------------


@pytest.mark.parametrize("clave", sorted(TABLAS_ESPERADAS))
def test_la_extraccion_del_pdf_da_la_tabla_celda_por_celda(extraidos, clave):
    documento, seccion = clave
    texto = _seccion_del_markdown(extraer_politicas.documento_markdown(extraidos[documento]), seccion)
    assert _unica_tabla(texto) == TABLAS_ESPERADAS[clave]


@pytest.mark.parametrize("clave", sorted(TABLAS_ESPERADAS))
def test_la_copia_revisada_da_la_tabla_celda_por_celda(clave):
    documento, seccion = clave
    markdown = (DIR_MD / f"{documento}.md").read_text(encoding="utf-8")
    assert _unica_tabla(_seccion_del_markdown(markdown, seccion)) == TABLAS_ESPERADAS[clave]


@pytest.mark.parametrize("clave", sorted(TABLAS_ESPERADAS))
def test_el_fragmento_guardado_trae_la_tabla_y_sus_tripletas(base, clave):
    con, _ = base
    documento, seccion = clave
    fragmento = _fragmentos(con, documento)[seccion]
    tabla = TABLAS_ESPERADAS[clave]
    assert _unica_tabla(fragmento["texto"]) == tabla
    encabezado = tabla[0]
    for fila in tabla[1:]:
        for columna, valor in zip(encabezado[1:], fila[1:]):
            tripleta = f"- {encabezado[0]} «{fila[0]}», {columna} = {valor}"
            assert tripleta in fragmento["texto"].splitlines(), f"Falta la tripleta: {tripleta}"


@pytest.mark.parametrize("clave", sorted(PAGINA_DE_LA_TABLA))
def test_la_tabla_queda_citada_con_su_seccion_y_pagina(base, clave):
    con, _ = base
    documento, seccion = clave
    subseccion, pagina = PAGINA_DE_LA_TABLA[clave]
    fragmento = _fragmentos(con, documento)[seccion]
    if subseccion == seccion:
        assert fragmento["pagina"] == pagina
    else:
        paginas = {s["seccion"]: s["pagina"] for s in fragmento["subsecciones"]}
        assert paginas[subseccion] == pagina


# --- Las cuatro lecturas del criterio de aceptación -----------------------------------------------


def test_350_pesos_de_diferencia_en_caja_es_mayor(base):
    con, _ = base
    tabla = _unica_tabla(_fragmentos(con, "MAN-OPS-007")["6"]["texto"])
    fila = _fila_del_monto(tabla, Decimal("350"))
    assert fila[1] == "Mayor"
    assert fila[2] == "Se registra, se reporta al Gerente de Tienda el mismo día y se levanta acta administrativa."


def test_una_devolucion_de_2000_pesos_la_autoriza_el_supervisor(base):
    con, _ = base
    tabla = _unica_tabla(_fragmentos(con, "PRO-SAC-021")["9"]["texto"])
    assert _fila_del_monto(tabla, Decimal("2000"))[1] == "Supervisor de Servicio al Cliente"
    # Bandera roja de C2.1: «Hasta 500.00» atribuido al Supervisor.
    assert _fila(tabla, "Hasta 500.00")[1] == "Ejecutivo de Servicio al Cliente"


def test_el_reverso_a_debito_tarda_de_3_a_5_dias_habiles(base):
    con, _ = base
    tabla = _unica_tabla(_fragmentos(con, "PRO-SAC-021")["6"]["texto"])
    assert _fila(tabla, "Tarjeta de débito")[2] == "3 a 5 días hábiles, según banco emisor"
    assert _fila(tabla, "Efectivo")[2] == "Inmediato"


def test_la_impresora_de_caja_va_con_prioridad_media(base):
    con, _ = base
    tabla = _unica_tabla(_fragmentos(con, "FAQ-OPS-001")["P20"]["texto"])
    filas = [fila for fila in tabla[1:] if "una caja sin impresora" in fila[1]]
    assert len(filas) == 1
    prioridad, _, respuesta, solucion = filas[0]
    assert (prioridad, respuesta, solucion) == ("Media", "4 horas hábiles", "2 días hábiles")
    # MAN-OPS-007 §9.1 dice lo mismo en texto corrido: el ticket se levanta con prioridad Media.
    manual = _fragmentos(con, "MAN-OPS-007")["9"]
    assert "Mesa de Servicio de TI con prioridad Media" in manual["texto"]
    assert {"seccion": "9.1", "titulo": "Impresoras de caja", "pagina": 6} in manual["subsecciones"]


# --- Fragmentos con documento, versión, sección y página ------------------------------------------


def test_cada_documento_da_sus_fragmentos(base):
    _, resumen = base
    assert resumen.conteos == FRAGMENTOS_POR_DOCUMENTO
    assert sum(resumen.conteos.values()) == 62


def test_cada_fragmento_trae_documento_version_seccion_y_pagina(base):
    con, _ = base
    filas = con.execute(
        "SELECT documento, version, seccion, titulo_seccion, pagina, pagina_fin, texto FROM fragmentos"
    ).fetchall()
    assert len(filas) == 62
    for documento, version, seccion, titulo, pagina, pagina_fin, texto in filas:
        assert version == VERSIONES[documento]
        assert seccion and titulo and texto.strip()
        assert 1 <= pagina <= pagina_fin <= 8


@pytest.mark.parametrize(
    "documento, seccion, pagina",
    [
        ("MAN-OPS-007", "2", 1),
        ("MAN-OPS-007", "3", 2),
        ("MAN-OPS-007", "6", 4),
        ("MAN-OPS-007", "9", 6),
        ("PRO-SAC-021", "3", 2),
        ("PRO-SAC-021", "9", 6),
        ("FAQ-OPS-001", "1", 1),
        ("FAQ-OPS-001", "P3", 2),
        ("FAQ-OPS-001", "P15", 4),
        ("FAQ-OPS-001", "P17", 4),
        ("FAQ-OPS-001", "P21", 6),
    ],
)
def test_las_citas_de_la_rubrica_existen_con_su_pagina(base, documento, seccion, pagina):
    con, _ = base
    assert _fragmentos(con, documento)[seccion]["pagina"] == pagina


def test_una_seccion_que_cruza_de_pagina_guarda_la_ultima(base):
    con, _ = base
    manual = _fragmentos(con, "MAN-OPS-007")
    assert (manual["5"]["pagina"], manual["5"]["pagina_fin"]) == (3, 4)
    assert (manual["9"]["pagina"], manual["9"]["pagina_fin"]) == (6, 7)


def test_la_pregunta_de_dos_renglones_queda_completa(base):
    con, _ = base
    fila = con.execute(
        "SELECT titulo_seccion FROM fragmentos WHERE documento = 'FAQ-OPS-001' AND seccion = 'P18'"
    ).fetchone()
    assert fila[0] == "¿Cómo se atienden los pedidos en línea (ecommerce) que se recogen o devuelven en tienda?"


# --- La copia revisada es la extracción del PDF ---------------------------------------------------


@pytest.mark.parametrize("documento", sorted(PDF_POR_CODIGO))
def test_la_copia_revisada_coincide_con_la_extraccion(extraidos, documento):
    extraido = extraidos[documento]
    assert extraido.codigo == documento
    assert extraido.version == VERSIONES[documento]
    revisado = (DIR_MD / f"{documento}.md").read_text(encoding="utf-8")
    assert fragmentos_politicas.separar_encabezado(revisado)[1] == extraido.markdown


def test_una_celda_cambiada_en_la_copia_revisada_detiene_la_construccion(tmp_path):
    copia = tmp_path / "politicas_md"
    shutil.copytree(DIR_MD, copia)
    ruta = copia / "MAN-OPS-007.md"
    texto = ruta.read_text(encoding="utf-8")
    alterado = texto.replace("| De 200.01 a 1,000.00 pesos | Mayor |", "| De 200.01 a 1,000.00 pesos | Menor |")
    assert alterado != texto
    ruta.write_text(alterado, encoding="utf-8")
    con = duckdb.connect(str(tmp_path / "tienda.duckdb"))
    try:
        with pytest.raises(construir.PoliticaSinRevisar) as error:
            construir.construir_politicas(con, DIR_PDF, copia)
    finally:
        con.close()
    assert error.value.documento == "MAN-OPS-007"
    assert "Mayor" in str(error.value)


def test_falta_la_copia_revisada_de_un_documento(tmp_path):
    copia = tmp_path / "politicas_md"
    shutil.copytree(DIR_MD, copia)
    (copia / "POL-OPS-014.md").unlink()
    con = duckdb.connect(str(tmp_path / "tienda.duckdb"))
    try:
        with pytest.raises(construir.PoliticaSinRevisar) as error:
            construir.construir_politicas(con, DIR_PDF, copia)
    finally:
        con.close()
    assert error.value.documento == "POL-OPS-014"


# --- El comando reporta fragmentos por documento y repite el hash ---------------------------------


def _reporte(salida):
    conteos = dict(re.findall(r"^([A-Z]{3}-[A-Z]{3}-\d{3}): (\d+) fragmentos$", salida, flags=re.M))
    huella = re.search(r"^Huella de los fragmentos \(sha256\): ([0-9a-f]{64})$", salida, flags=re.M)
    assert huella, salida
    return {codigo: int(n) for codigo, n in conteos.items()}, huella.group(1)


# El comando hace INSTALL fts antes de crear el índice. Sin la extensión la prueba se salta, igual que en los
# demás módulos: no falla ni descarga 47 MB. El salto no pasa inadvertido porque el hook de pre-commit bloquea
# el commit si fts no carga.
@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


def _entorno_con_casa_propia(casa):
    """Entorno de un subproceso cuyo directorio personal (~) es casa, con el fts ya instalado copiado dentro.

    El comando sigue ejecutando INSTALL, pero encuentra la extensión en el ~/.duckdb de casa: no baja nada y
    nunca escribe en el ~/.duckdb real. Las aserciones de la prueba no lo ven; lo mide
    tests/test_aislamiento_del_comando.py. PYTHONIOENCODING hace que el hijo escriba en UTF-8 y no en
    cp1252: si el comando falla, su mensaje trae acentos y el lector de subprocess, que decodifica en UTF-8,
    muere con UnicodeDecodeError.
    """
    with duckdb.connect() as con:
        instalada = Path(
            con.execute("SELECT install_path FROM duckdb_extensions() WHERE extension_name = 'fts'").fetchone()[0]
        )
    carpeta = casa / ".duckdb" / "extensions" / instalada.parent.parent.name / instalada.parent.name
    carpeta.mkdir(parents=True)
    shutil.copy2(instalada, carpeta)
    return {**os.environ, "HOME": str(casa), "USERPROFILE": str(casa), "PYTHONIOENCODING": "utf-8"}


def test_el_comando_reporta_fragmentos_y_una_segunda_corrida_da_el_mismo_hash(fts, tmp_path, capsys):
    primera = subprocess.run(
        [sys.executable, "-m", "agente.construir", "--base", str(tmp_path / "a.duckdb")],
        cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", check=True,
        env=_entorno_con_casa_propia(tmp_path / "casa"),
    )
    conteos_1, huella_1 = _reporte(primera.stdout)

    assert construir.main(["--base", str(tmp_path / "b.duckdb")]) == 0
    conteos_2, huella_2 = _reporte(capsys.readouterr().out)

    assert conteos_1 == conteos_2 == FRAGMENTOS_POR_DOCUMENTO
    assert huella_1 == huella_2
    for nombre in ("a.duckdb", "b.duckdb"):
        with duckdb.connect(str(tmp_path / nombre), read_only=True) as con:
            assert con.execute("SELECT count(*) FROM fragmentos").fetchone()[0] == 62
