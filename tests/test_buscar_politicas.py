"""buscar_politicas: fragmentos por BM25, documentos que no vienen y choques entre fuentes (T2.7).

Criterio de aceptación de la ficha, y las pruebas que lo cubren:

- "P06 a P10, P47 y P58 a P61 citan un documento y una sección aceptados": las pruebas de
  SECCIONES_ACEPTADAS, con la pregunta tal como la escribió la gerente y con la consulta que el modelo
  escribe con las palabras de los documentos (la descripción de la herramienta se lo pide). Las
  secciones aceptadas son las de C2.2 y las respuestas esperadas de la rúbrica.
- "recall@4 de la sección de 95% o más en el set": se mide aquí sobre esas 10 preguntas; el set de
  T5.1 todavía no existe.
- "P10 resuelve con el paso 1, P06, P59 y P60 con el paso 2 y el criterio dicho como propio, P61 con el
  paso 4, y una devolución de los 7 SKU de D21 con el paso 3": test_P10_..., test_P06_..., etc. La
  devolución de D21 se prueba con cada uno de los 7 SKU nombrado como lo diría la gerente, también
  cuando BM25 no trae ninguna sección de PRO-SAC-021.

Que la respuesta final del modelo nombre el choque y diga cuál prevalece necesita el modelo real (T3.1).
La base completa, con el índice, se construye una vez por módulo en un directorio temporal. Si la
extensión fts no está instalada, estas pruebas se saltan: sin red no se descarga (T0.5).
"""

import re
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest
import yaml
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agente import consultas, construir, definiciones
from agente.fragmentos_politicas import separar_encabezado
from agente.herramientas import buscar_politicas as bp
from agente.herramientas.comun import EntradaPoliticas, estimar_tokens

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATOS = RAIZ / "datos"
DIR_POLITICAS_MD = RAIZ / "politicas_md"
CONTRADICCIONES = RAIZ / "config" / "contradicciones.yaml"

# Preguntas de políticas de la rúbrica: el texto de la gerente, la consulta que escribiría el modelo
# con las palabras de los documentos, y las secciones que la rúbrica acepta para citarla (C2.2 y la
# respuesta esperada). Una sección de primer nivel ("5") acepta sus subsecciones (5.2).
SECCIONES_ACEPTADAS = {
    "P06": (
        "el yogurt del refri caduca el viernes, hasta cuando lo puedo vender?",
        "retiro de anaquel de lácteos, yogur, días antes de la caducidad",
        {("POL-OPS-014", "5")},
    ),
    "P07": (
        "a una cajera le faltaron 350 pesos en su corte, q procede?",
        "diferencia de arqueo en el corte de caja, faltante",
        {("MAN-OPS-007", "6"), ("FAQ-OPS-001", "P3")},
    ),
    "P08": (
        "a q hora se abre el anden pa los proveedores?",
        "horario de recepción de proveedores, apertura de andén",
        {("MAN-OPS-007", "3"), ("MAN-OPS-007", "2"), ("FAQ-OPS-001", "P2")},
    ),
    "P09": (
        "me piden una devolucion de 2,000 pesos, la puede autorizar la de servicio a clientes?",
        "autorización de devolución por monto, Ejecutivo de Servicio al Cliente",
        {("PRO-SAC-021", "9"), ("FAQ-OPS-001", "P17")},
    ),
    "P10": (
        "un cliente trae una licuadora q compro hace 3 semanas, se la acepto?",
        "plazo de devolución de electrodomésticos, licuadora",
        {("PRO-SAC-021", "3"), ("FAQ-OPS-001", "P15")},
    ),
    "P47": (
        "que dice la politica del fondo fijo de caja?",
        "manejo de fondo fijo de caja",
        {("MAN-OPS-007", "4"), ("FAQ-OPS-001", "P4")},
    ),
    "P58": (
        "le hice el reverso a la tarjeta de debito a un cliente, cuando le cae?",
        "reembolso a tarjeta de débito, tiempo",
        {("PRO-SAC-021", "6")},
    ),
    "P59": (
        "puedo rebajar el pan al 60% el dia q se tiene q retirar?",
        "rebaja escalonada de un perecedero el día de retiro de anaquel",
        {("POL-OPS-014", "5")},
    ),
    "P60": (
        "un cliente trae una cafetera con defecto de fabrica, la compro hace 2 meses y trae ticket, "
        "quien me autoriza la devolucion?",
        "devolución fuera de plazo por defecto de fabricación, quién autoriza",
        {("PRO-SAC-021", "4"), ("PRO-SAC-021", "9")},
    ),
    "P61": (
        "en anahuac tambien aplico la politica de mermas pa retirar los lacteos?",
        "retiro de anaquel de lácteos, política de mermas",
        {("POL-OPS-014", "5")},
    ),
}
# Preguntas de la rúbrica que piden dos secciones a la vez (C2.3): las dos deben llegar al modelo.
SECCIONES_DE_LAS_DOS_FUENTES = {
    "P10": {("PRO-SAC-021", "3"), ("FAQ-OPS-001", "P15")},
    "P59": {("POL-OPS-014", "5"), ("POL-OPS-014", "6")},
    "P60": {("PRO-SAC-021", "4"), ("PRO-SAC-021", "9")},
}

# D21: los 7 SKU de Panadería y Carnes que el catálogo marca como no perecederos y que PRO-SAC-021 §2
# define como perecederos por tipo de alimento (mapa de políticas y datos, puente 4).
SKUS_D21 = {"SKU-1018", "SKU-1019", "SKU-1020", "SKU-1021", "SKU-1022", "SKU-1031", "SKU-1032"}

# Lo que la rúbrica espera en la respuesta de P06, P10, P59, P60 y P61: G2 prohíbe escribirlo en la
# configuración que se ejecuta.
RESPUESTAS_DEL_SET = [
    "30 días", "15 días", "48 horas", "60 %", "60%", "50 %", "50%", "martes", "miércoles", "jueves",
    "Gerente de Tienda", "Subgerente", "Gerente de Servicio al Cliente", "90 días", "noventa",
]


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def base(fts, tmp_path_factory):
    destino = tmp_path_factory.mktemp("politicas") / "tienda.duckdb"
    return construir.construir(DIR_DATOS, destino).base


@pytest.fixture(scope="module")
def abiertas(base):
    with consultas.abrir_consultas(base) as conexion:
        yield conexion


def buscar(abiertas, consulta, documento=None, **opciones):
    entrada = EntradaPoliticas(consulta=consulta, documento=documento)
    with abiertas.sesion() as sesion:
        return bp.buscar_politicas(sesion, entrada, **opciones)


def secciones(resultado):
    return {(fila["documento"], fila["seccion"]) for fila in resultado.filas}


def acepta(resultado, aceptadas):
    return bool(secciones(resultado) & aceptadas)


def choques(resultado, paso=None):
    return [c for c in resultado.detalle.get("choques", []) if paso is None or c["paso"] == paso]


def choque(resultado, identificador):
    encontrados = [c for c in choques(resultado) if c["id"] == identificador]
    assert len(encontrados) == 1, [c["id"] for c in choques(resultado)]
    return encontrados[0]


# ------------------------------------------------------------------ fragmentos con sus metadatos

def test_devuelve_los_4_mejores_fragmentos_de_bm25_en_el_mismo_orden_que_la_busqueda(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P58"][0])
    with abiertas.sesion() as sesion:
        esperados = consultas.buscar_fragmentos(sesion, SECCIONES_ACEPTADAS["P58"][0])
    bm25 = [fila for fila in resultado.filas if fila["motivo"] == "bm25"]
    assert resultado.herramienta == "buscar_politicas"
    assert resultado.estado == "ok"
    assert [fila["id"] for fila in bm25] == [f.id for f in esperados]
    assert len(bm25) == 4


def test_cada_fragmento_trae_documento_version_seccion_pagina_subsecciones_y_texto(abiertas):
    resultado = buscar(abiertas, "rebajas escalonadas de producto próximo a vencer")
    fila = next(f for f in resultado.filas if f["id"] == "POL-OPS-014#6")
    assert fila["documento"] == "POL-OPS-014"
    # El título, la versión y la vigencia van una vez por documento, no en cada fila.
    assert resultado.detalle["documentos"]["POL-OPS-014"] == (
        "Política de mermas y caducidad, versión 3.2, vigente desde 2025-03-01"
    )
    assert fila["seccion"] == "6"
    assert fila["titulo"] == "Rebajas escalonadas para producto próximo a vencer"
    assert fila["cita"] == "POL-OPS-014 §6, pp. 4-5"
    # §6.2 está en la página 5 aunque el fragmento empieza en la 4: la cita la toma de subsecciones.
    assert fila["subsecciones"] == "6.1 Reglas de aplicación (p. 4); 6.2 Categorías excluidas de la rebaja (p. 5)"
    assert fila["texto"].startswith("## 6. Rebajas escalonadas")
    assert "| 1 día (solo perecederos no lácteos) | 60 % |" in fila["texto"]


def test_una_pregunta_de_la_faq_se_cita_por_su_numero_y_su_pagina(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P10"][0])
    fila = next(f for f in resultado.filas if f["id"] == "FAQ-OPS-001#P15")
    assert fila["cita"] == "FAQ-OPS-001 P15, p. 4"
    assert "subsecciones" not in fila or fila["subsecciones"] is None


def test_la_cita_de_6_2_y_de_9_2_toma_la_pagina_de_la_subseccion_y_no_la_del_fragmento(abiertas):
    resultado = buscar(abiertas, "incidencias de tecnología, otras incidencias frecuentes", documento="MAN-OPS-007")
    fila = next(f for f in resultado.filas if f["id"] == "MAN-OPS-007#9")
    assert fila["cita"] == "MAN-OPS-007 §9, pp. 6-7"
    assert "9.2 Otras incidencias frecuentes (p. 7)" in fila["subsecciones"]


@pytest.mark.parametrize("documento, seccion, pagina_del_fragmento, pagina", [
    ("POL-OPS-014", "6.2", 4, 5),
    ("MAN-OPS-007", "9.2", 6, 7),
    ("POL-OPS-014", "5.3", 3, 4),
])
def test_una_subseccion_en_otra_pagina_que_su_fragmento_se_cita_con_la_suya(abiertas, documento, seccion, pagina_del_fragmento, pagina):
    with abiertas.sesion() as sesion:
        indice = bp.leer_indice(sesion)
    subseccion = indice.secciones[(documento, seccion)]
    assert indice.fragmentos[subseccion.fragmento]["pagina"] == pagina_del_fragmento
    assert subseccion.pagina == pagina
    assert subseccion.texto.startswith(f"### {seccion} ")


def test_con_documento_solo_busca_en_ese_documento(abiertas):
    resultado = buscar(abiertas, "devolución de producto", documento="FAQ-OPS-001")
    bm25 = [fila for fila in resultado.filas if fila["motivo"] == "bm25"]
    assert len(bm25) == 4
    assert {fila["documento"] for fila in bm25} == {"FAQ-OPS-001"}
    assert resultado.filtros["documento"] == "FAQ-OPS-001"
    # Un fragmento de otro documento solo llega si choca con uno de la FAQ: el normativo prevalece (paso 1).
    otros = [fila for fila in resultado.filas if fila["documento"] != "FAQ-OPS-001"]
    assert all(fila["motivo"].startswith("choque ") for fila in otros)


def test_la_misma_busqueda_da_el_mismo_resultado_en_cada_ejecucion(abiertas):
    primeras = [buscar(abiertas, SECCIONES_ACEPTADAS["P10"][0]).texto_registro() for _ in range(5)]
    assert len(set(primeras)) == 1


def test_el_texto_de_la_busqueda_viaja_como_parametro_y_no_cambia_la_base(abiertas, base):
    antes = buscar(abiertas, "fondo fijo").filas
    resultado = buscar(abiertas, "fondo fijo'); DROP TABLE fragmentos; --")
    assert resultado.estado == "ok"
    assert buscar(abiertas, "fondo fijo").filas == antes


def test_el_registro_lleva_la_consulta_sql_y_el_modelo_no(abiertas):
    resultado = buscar(abiertas, "fondo fijo")
    assert "match_bm25" in resultado.consulta
    assert "match_bm25" not in resultado.texto_modelo()
    assert "match_bm25" in resultado.texto_registro()


def test_una_busqueda_sin_palabras_de_contenido_da_sin_resultados_sin_cifras_y_con_los_documentos(abiertas):
    resultado = buscar(abiertas, "de la que el")
    assert resultado.estado == "sin_resultados"
    assert resultado.filas == []
    assert resultado.cifras == {}
    assert set(resultado.detalle["incluidos"]) == {"FAQ-OPS-001", "MAN-OPS-007", "POL-OPS-014", "PRO-SAC-021"}


# ------------------------------------------------------------------ las preguntas de la rúbrica

@pytest.mark.parametrize("item", ["P07", "P08", "P09", "P10", "P47", "P58", "P59", "P60", "P61"])
def test_con_el_texto_de_la_gerente_llega_una_seccion_aceptada(abiertas, item):
    pregunta, _, aceptadas = SECCIONES_ACEPTADAS[item]
    resultado = buscar(abiertas, pregunta)
    assert acepta(resultado, aceptadas), sorted(secciones(resultado))


@pytest.mark.parametrize("item", sorted(SECCIONES_ACEPTADAS))
def test_con_la_consulta_del_modelo_llega_una_seccion_aceptada(abiertas, item):
    _, consulta, aceptadas = SECCIONES_ACEPTADAS[item]
    resultado = buscar(abiertas, consulta)
    assert acepta(resultado, aceptadas), sorted(secciones(resultado))


def test_P06_con_el_texto_de_la_gerente_llega_5_2_porque_yogurt_suma_la_categoria_lacteos(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P06"][0])
    assert acepta(resultado, SECCIONES_ACEPTADAS["P06"][2]), sorted(secciones(resultado))
    assert "Lácteos" in resultado.filtros["busqueda"]
    assert any("yogurt" in nota and "Lácteos" in nota for nota in resultado.notas)


def test_una_busqueda_sin_productos_no_cambia_el_texto_que_va_al_indice(abiertas):
    for item in ("P07", "P08", "P09", "P10", "P47", "P58", "P60"):
        resultado = buscar(abiertas, SECCIONES_ACEPTADAS[item][0])
        assert resultado.filtros["busqueda"] == SECCIONES_ACEPTADAS[item][0], item


def test_caja_de_fondo_fijo_no_se_lee_como_pan_de_caja(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P47"][0])
    assert "Panadería" not in resultado.filtros["busqueda"]


def test_un_producto_que_esta_en_dos_categorias_no_suma_ninguna(abiertas):
    # "leche" es la primera palabra de productos de Lácteos y de Bebidas (leche saborizada).
    resultado = buscar(abiertas, "devolución de leche")
    assert resultado.filtros["busqueda"] == "devolución de leche"
    assert not any("categoría" in nota for nota in resultado.notas)


@pytest.mark.parametrize("item, texto", [(i, 0) for i in SECCIONES_DE_LAS_DOS_FUENTES] + [(i, 1) for i in SECCIONES_DE_LAS_DOS_FUENTES])
def test_cuando_dos_secciones_chocan_llegan_las_dos_aunque_bm25_solo_traiga_una(abiertas, item, texto):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS[item][texto])
    assert SECCIONES_DE_LAS_DOS_FUENTES[item] <= secciones(resultado), sorted(secciones(resultado))


def test_recall_a_4_de_la_seccion_es_de_95_por_ciento_o_mas_en_las_preguntas_de_politicas(abiertas):
    # Cada pregunta cuenta dos veces: con el texto de la gerente y con la consulta del modelo. Solo
    # cuentan los 4 de BM25, no los fragmentos que agrega un choque. Falla P61 con el texto de la
    # gerente ("anahuac", "pa"), que recibe POL-OPS-014 §5 solo por el choque CH-03: 19 de 20.
    aciertos = {}
    for item, (pregunta, consulta, aceptadas) in SECCIONES_ACEPTADAS.items():
        for etiqueta, texto in (("gerente", pregunta), ("modelo", consulta)):
            resultado = buscar(abiertas, texto)
            de_bm25 = {(f["documento"], f["seccion"]) for f in resultado.filas if f["motivo"] == "bm25"}
            aciertos[(item, etiqueta)] = bool(de_bm25 & aceptadas)
    assert sum(aciertos.values()) / len(aciertos) >= 0.95, [clave for clave, bien in aciertos.items() if not bien]
    assert all(bien for (item, etiqueta), bien in aciertos.items() if etiqueta == "modelo")


# ------------------------------------------------------------------ paso 1: regla de los documentos

def test_P10_resuelve_con_el_paso_1_y_prevalece_PRO_SAC_021_3_por_la_regla_de_FAQ_1(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P10"][0])
    entrada = choque(resultado, "CH-01")
    assert entrada["paso"] == 1
    assert entrada["fuentes"] == [
        "FAQ-OPS-001 P15, p. 4",
        "PRO-SAC-021 §3, fila «Electrodomésticos y electrónicos menores», p. 2",
    ]
    assert entrada["prevalece"] == ["PRO-SAC-021 §3, fila «Electrodomésticos y electrónicos menores», p. 2"]
    assert entrada["regla"] == "FAQ-OPS-001 §1, p. 1"
    # La oración de la regla va una sola vez en el detalle, aunque la usen varios choques de paso 1.
    assert resultado.detalle["regla_entre_documentos"]["cita"] == "FAQ-OPS-001 §1, p. 1"
    assert "prevalece siempre la política o el procedimiento" in resultado.detalle["regla_entre_documentos"]["texto"]
    assert "versión" in entrada["por"]


def test_P10_con_la_faq_sola_en_bm25_agrega_PRO_SAC_021_3_y_dice_por_que(abiertas):
    resultado = buscar(abiertas, "licuadora dos semanas procede")
    assert ("FAQ-OPS-001", "P15") in secciones(resultado)
    agregado = next(f for f in resultado.filas if f["id"] == "PRO-SAC-021#3")
    assert agregado["motivo"] == "choque CH-01"
    assert [f["motivo"] for f in resultado.filas].count("bm25") == 4


def test_el_choque_de_producto_vendido_caducado_tambien_es_paso_1(abiertas):
    resultado = buscar(abiertas, "producto que se vendió ya caducado")
    entrada = choque(resultado, "CH-02")
    assert entrada["paso"] == 1
    assert entrada["prevalece"] == ["PRO-SAC-021 §3, fila «Perecederos», p. 2"]


# ------------------------------------------------------------------ paso 2: criterio propio

def test_P06_resuelve_con_el_paso_2_y_el_criterio_dicho_como_propio(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P06"][0])
    entrada = choque(resultado, "CH-03")
    assert entrada["paso"] == 2
    assert entrada["prevalece"] == ["POL-OPS-014 §5.2, p. 3", "POL-OPS-014 §6.1, p. 4"]
    assert "POL-OPS-014 §6, fila «2 días», p. 4" in entrada["fuentes"]
    assert entrada["por"].startswith("criterio propio")
    assert "«específica»" in entrada["por"]


def test_P59_resuelve_con_el_paso_2_y_manda_5_1_con_el_sin_excepcion_de_6_1(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P59"][0])
    entrada = choque(resultado, "CH-04")
    assert entrada["paso"] == 2
    assert entrada["prevalece"] == ["POL-OPS-014 §5.1, p. 3", "POL-OPS-014 §6.1, p. 4"]
    assert "POL-OPS-014 §6, fila «1 día (solo perecederos no lácteos)», p. 4" in entrada["fuentes"]
    assert entrada["por"].startswith("criterio propio")
    assert "«sin excepción»" in entrada["por"]
    assert "sin excepción" in entrada["texto_marca"]


def test_P60_resuelve_con_el_paso_2_manda_9_1_y_la_faq_P17_coincide(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P60"][0])
    entrada = choque(resultado, "CH-05")
    assert entrada["paso"] == 2
    assert entrada["prevalece"] == ["PRO-SAC-021 §9.1, p. 6"]
    assert entrada["fuentes"] == ["PRO-SAC-021 §9.1, p. 6", "PRO-SAC-021 §4.1, p. 3"]
    assert entrada["coinciden"] == ["FAQ-OPS-001 P17, p. 4"]
    assert entrada["por"].startswith("criterio propio")
    assert "«siempre requieren»" in entrada["por"]


# ------------------------------------------------------------------ paso 3: dato contra documento

def test_los_skus_en_duda_siguen_al_catalogo_en_todas_las_categorias_que_nombra_la_definicion(abiertas):
    # Con los datos de hoy solo Panadería y Carnes tienen no perecederos; un lácteo marcado como no
    # perecedero también quedaría en duda, porque la definición de §2 nombra los lácteos.
    with abiertas.sesion() as sesion:
        indice = bp.leer_indice(sesion)
    catalogo = [dict(fila) for fila in indice.catalogo]
    huevo = next(fila for fila in catalogo if fila["nombre"].startswith("Huevo"))
    huevo["perecedero"] = False
    entrada = next(c for c in bp.contradicciones().choques if c.paso == 3)
    alterado = replace(indice, catalogo=catalogo)
    assert set(bp.productos_en_duda(alterado, entrada)) == SKUS_D21 | {huevo["sku"]}


def test_los_skus_del_paso_3_salen_del_catalogo_y_son_los_7_de_D21(abiertas):
    with abiertas.sesion() as sesion:
        indice = bp.leer_indice(sesion)
    entrada = next(c for c in bp.contradicciones().choques if c.paso == 3)
    assert set(bp.productos_en_duda(indice, entrada)) == SKUS_D21


@pytest.mark.parametrize("consulta, skus", [
    ("plazo de devolución del pan de caja", {"SKU-1018"}),
    ("plazo de devolución de unos nuggets de pollo congelados", {"SKU-1032"}),
    ("plazo de devolución de las tortillas de harina", {"SKU-1019"}),
    ("plazo de devolución de un perecedero", SKUS_D21),
])
def test_una_devolucion_de_los_7_skus_de_D21_resuelve_con_el_paso_3(abiertas, consulta, skus):
    resultado = buscar(abiertas, consulta)
    entrada = choque(resultado, "CH-06")
    assert entrada["paso"] == 3
    assert {re.search(r"SKU-\d{4}", p).group() for p in entrada["productos"]} == skus
    assert entrada["fuentes"] == ["PRO-SAC-021 §2, pp. 1-2", "catálogo de productos, columna perecedero"]
    assert "Producto perecedero" in entrada["texto_documento"]
    assert entrada["principio"] == "PRO-SAC-021 §1.2, p. 1"
    assert "escalando al nivel de autorización" in entrada["texto_principio"]
    assert entrada["area"] == "Dirección de Servicio al Cliente, responsable de PRO-SAC-021"
    assert "ningún documento" in entrada["prevalece"]


# D21: en una devolución de esos SKU se dan las dos lecturas con su fuente: perecedero según §2, con el
# plazo de §3, y no perecedero según el catálogo, con §3 y §5.1. Las tres secciones deben llegar al modelo.
SECCIONES_DE_LAS_DOS_LECTURAS = {("PRO-SAC-021", "2"), ("PRO-SAC-021", "3"), ("PRO-SAC-021", "5")}
# Cada SKU de D21 como lo nombraría la gerente.
NOMBRES_D21 = {
    "SKU-1018": "pan de caja",
    "SKU-1019": "tortillas de harina",
    "SKU-1020": "galletas marías",
    "SKU-1021": "pan molido",
    "SKU-1022": "tostadas horneadas",
    "SKU-1031": "filete de pescado",
    "SKU-1032": "nuggets de pollo",
}


@pytest.mark.parametrize("sku", sorted(NOMBRES_D21))
def test_la_devolucion_de_cada_sku_de_D21_con_las_palabras_de_la_gerente_resuelve_con_el_paso_3(abiertas, sku):
    # Con esta redacción BM25 trae la FAQ (P15, P18) y no trae §2, §3 ni §5 de PRO-SAC-021.
    resultado = buscar(abiertas, f"un cliente quiere devolver {NOMBRES_D21[sku]}, que plazo tiene?")
    entrada = choque(resultado, "CH-06")
    assert {re.search(r"SKU-\d{4}", p).group() for p in entrada["productos"]} == {sku}
    assert SECCIONES_DE_LAS_DOS_LECTURAS <= secciones(resultado), sorted(secciones(resultado))


@pytest.mark.parametrize("consulta", [
    "devolución de pan de caja",
    "devolución de pan de caja blanco",
    "me quieren devolver unas pan de caja sin ticket, procede?",
    "le devolvieron un pan de caja ayer, procede?",
    "un cliente regresa unos nuggets de pollo, se los acepto?",
])
def test_una_devolucion_de_D21_resuelve_con_el_paso_3_aunque_bm25_no_traiga_pro_sac_021(abiertas, consulta):
    resultado = buscar(abiertas, consulta)
    entrada = choque(resultado, "CH-06")
    assert entrada["paso"] == 3
    assert SECCIONES_DE_LAS_DOS_LECTURAS <= secciones(resultado), sorted(secciones(resultado))
    agregados = [f for f in resultado.filas if f["motivo"] == "choque CH-06"]
    assert agregados, [f["id"] + ":" + f["motivo"] for f in resultado.filas]
    assert [f["motivo"] for f in resultado.filas].count("bm25") == 4


def test_el_paso_3_cuenta_los_fragmentos_que_agrego_otro_choque(abiertas, tmp_path):
    # Sin ninguna palabra de devolución que reconozca, CH-06 aún aplica: CH-01 agregó PRO-SAC-021 §3.
    entradas = bp.cargar_contradicciones(_yaml_alterado(
        tmp_path, lambda d: next(c for c in d["choques"] if c["paso"] == 3).update(se_activa_con_palabras=["reembolso"])
    ))
    resultado = buscar(abiertas, "un cliente quiere devolver galletas marías, que plazo tiene?", entradas=entradas)
    bm25 = {f["id"] for f in resultado.filas if f["motivo"] == "bm25"}
    assert not bm25 & {"PRO-SAC-021#2", "PRO-SAC-021#3", "PRO-SAC-021#5"}
    assert choque(resultado, "CH-06")["paso"] == 3


def test_el_paso_3_no_aparece_con_un_producto_que_el_catalogo_ya_marca_perecedero(abiertas):
    # El yogurt es Lácteos y el catálogo lo marca perecedero: catálogo y §2 dicen lo mismo.
    resultado = buscar(abiertas, "un cliente quiere devolver un yogurt, que plazo tiene?")
    assert choques(resultado, 3) == []


@pytest.mark.parametrize("consulta", [
    "cuántos días antes de caducar se retira el pan de caja del anaquel?",
    # BM25 trae la definición de PRO-SAC-021 §2 por la categoría (Carnes), y no es una devolución.
    "merma de nuggets en anahuac",
    "cuanto vendimos de filete de pescado ayer?",
])
def test_el_paso_3_no_aparece_si_el_producto_en_duda_no_es_de_una_devolucion(abiertas, consulta):
    resultado = buscar(abiertas, consulta)
    assert choques(resultado, 3) == []
    assert not [f for f in resultado.filas if f["motivo"] == "choque CH-06"]


def test_el_paso_3_no_aparece_si_la_consulta_no_nombra_uno_de_esos_productos(abiertas):
    # P10 trae PRO-SAC-021 §3, pero una licuadora no es uno de los 7 SKU.
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P10"][0])
    assert choques(resultado, 3) == []


def test_el_paso_3_no_aparece_si_no_llega_la_seccion_de_devoluciones(abiertas):
    # P59 nombra el pan, pero pregunta por rebajas: no llega PRO-SAC-021 y el choque es de devoluciones.
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P59"][0])
    assert choques(resultado, 3) == []


# ------------------------------------------------------------------ paso 4: fuera de alcance

def test_P61_resuelve_con_el_paso_4_y_dice_que_POL_OPS_014_no_cubre_las_tiendas_mi_tienda(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P61"][0])
    entrada = choque(resultado, "CH-07")
    assert entrada["paso"] == 4
    assert entrada["fuentes"] == ["POL-OPS-014 §1.1, p. 1", "tiendas.csv, columna formato"]
    assert entrada["fuera"] == ["Tienda Anáhuac (T03, Mi Tienda)", "Tienda Lomas del Sur (T04, Mi Tienda)"]
    assert "formato supermercado" in entrada["texto_alcance"]
    assert entrada["cubiertas"] == ["Tienda Valle Norte (T01, Supermercado)", "Tienda Cumbres (T02, Supermercado)"]
    assert entrada["salvedad"] == definiciones.supuestos().alcance_documentos["POL-OPS-014"].salvedad
    assert "orientación" not in entrada["por"]
    assert "lo asumes" in entrada["por"]
    assert "cubiertas" in entrada["por"]
    assert entrada["area"] == "Dirección de Operaciones de Tienda, responsable de POL-OPS-014"


def test_con_D4_A_vigente_MAN_OPS_007_cubre_mi_tienda_y_no_hay_paso_4_del_manual(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P47"][0])
    assert ("MAN-OPS-007", "4") in secciones(resultado)
    assert choques(resultado, 4) == []


def test_el_paso_4_sigue_a_supuestos_yaml_si_cambia_la_opcion_de_la_duda(abiertas, copia_config):
    ruta = copia_config / "supuestos.yaml"
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    datos["dudas"]["D2"]["opcion"] = "B"
    datos["dudas"]["D4"]["opcion"] = "B"
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    s = definiciones.cargar(ruta)
    del_manual = buscar(abiertas, SECCIONES_ACEPTADAS["P47"][0], s=s)
    de_mermas = buscar(abiertas, SECCIONES_ACEPTADAS["P61"][0], s=s)
    # Con D4-B el manual no cubre Mi Tienda y su regla es orientación; con D2-B la política de mermas sí la cubre.
    assert [c["id"] for c in choques(del_manual, 4)] == ["CH-08"]
    assert choque(del_manual, "CH-08")["fuera"] == ["Tienda Anáhuac (T03, Mi Tienda)", "Tienda Lomas del Sur (T04, Mi Tienda)"]
    assert "orientación" in choque(del_manual, "CH-08")["por"]
    assert "CH-07" not in [c["id"] for c in choques(de_mermas, 4)]


# ------------------------------------------------------------------ documentos citados que no vienen

def test_P46_dice_que_PRO_ECO_009_se_cita_y_no_viene_y_donde_se_cita(abiertas):
    resultado = buscar(abiertas, "q dice el procedimiento de pedidos en linea sobre devoluciones?")
    assert resultado.estado == "ok"
    no_incluidos = {d["documento"]: d for d in resultado.detalle["no_incluidos"]}
    assert set(no_incluidos) == {"PRO-ECO-009"}
    assert no_incluidos["PRO-ECO-009"]["citado_en"] == ["FAQ-OPS-001 P18, p. 5", "PRO-SAC-021 §1.1, p. 1"]
    assert (
        "PRO-ECO-009 se cita en FAQ-OPS-001 P18, p. 5 y PRO-SAC-021 §1.1, p. 1 pero no viene entre los "
        "documentos: no se tiene su contenido"
    ) in resultado.notas


def test_un_documento_que_no_viene_en_la_entrada_da_documento_no_incluido_con_los_que_si_vienen(abiertas):
    resultado = buscar(abiertas, "devoluciones de pedidos en línea", documento="PRO-ECO-009")
    assert resultado.estado == "documento_no_incluido"
    assert resultado.filas == []
    assert resultado.detalle["incluidos"] == ["FAQ-OPS-001", "MAN-OPS-007", "POL-OPS-014", "PRO-SAC-021"]
    assert resultado.detalle["citado_en"] == ["FAQ-OPS-001 P18, p. 5", "PRO-SAC-021 §1.1, p. 1"]


def test_un_documento_que_nadie_cita_tambien_da_documento_no_incluido_sin_donde_se_cita(abiertas):
    resultado = buscar(abiertas, "lo que sea", documento="POL-XYZ-999")
    assert resultado.estado == "documento_no_incluido"
    assert "citado_en" not in resultado.detalle


def test_un_codigo_que_no_viene_escrito_en_la_consulta_tambien_se_reporta(abiertas):
    resultado = buscar(abiertas, "recepción de mercancía en andén PRO-LOG-003")
    no_incluidos = {d["documento"]: d for d in resultado.detalle["no_incluidos"]}
    assert no_incluidos["PRO-LOG-003"]["citado_en"] == ["POL-OPS-014 §1.2, p. 1", "POL-OPS-014 §9, p. 6"]


def test_los_documentos_que_si_vienen_no_se_reportan_como_faltantes(abiertas):
    # POL-OPS-014 §1.2 lista a los otros tres documentos, que sí vienen, y a dos que no.
    resultado = buscar(abiertas, "documentos relacionados de la política de mermas")
    faltantes = {d["documento"] for d in resultado.detalle.get("no_incluidos", [])}
    assert faltantes <= {"PRO-LOG-003", "GUI-CAL-002"}
    assert "GUI-CAL-002" in faltantes


# ------------------------------------------------------------------ config/contradicciones.yaml

def test_cada_entrada_del_yaml_existe_en_los_fragmentos_y_su_marca_esta_solo_del_lado_que_prevalece(abiertas):
    with abiertas.sesion() as sesion:
        indice = bp.leer_indice(sesion)
    bp.validar(bp.contradicciones(), indice, definiciones.supuestos())


def test_los_ids_del_yaml_son_unicos_y_van_en_orden_de_paso():
    entradas = bp.contradicciones().choques
    assert len({c.id for c in entradas}) == len(entradas)
    assert [c.paso for c in entradas] == sorted(c.paso for c in entradas)


def test_el_yaml_no_trae_la_respuesta_de_ninguna_pregunta_del_set():
    texto = CONTRADICCIONES.read_text(encoding="utf-8")
    sin_comentarios = "\n".join(linea.split("#", 1)[0] for linea in texto.splitlines())
    for respuesta in RESPUESTAS_DEL_SET:
        assert respuesta.casefold() not in sin_comentarios.casefold(), respuesta
    # Fuera de los ids y las secciones no hay cifras: ni plazos, ni montos, ni porcentajes.
    datos = yaml.safe_load(texto)
    for entrada in datos["choques"]:
        assert not re.search(r"\d", entrada["tema"]), entrada["tema"]
        assert not re.search(r"\d", entrada.get("marca", "")), entrada["id"]
        assert not re.search(r"\d", " ".join(entrada.get("se_activa_con_palabras", []))), entrada["id"]


def test_el_area_responsable_del_yaml_es_la_del_encabezado_del_documento():
    for entrada in bp.contradicciones().choques:
        if entrada.paso in (3, 4):
            documento = entrada.documento.documento if entrada.paso == 3 else entrada.alcance.documento
            encabezado, _ = separar_encabezado((DIR_POLITICAS_MD / f"{documento}.md").read_text(encoding="utf-8"))
            assert entrada.area_responsable == encabezado["area"], entrada.id


def test_cada_documento_con_alcance_en_supuestos_tiene_su_entrada_de_paso_4():
    con_paso_4 = {c.alcance.documento for c in bp.contradicciones().choques if c.paso == 4}
    assert con_paso_4 == set(definiciones.supuestos().alcance_documentos)


def _yaml_alterado(tmp_path, cambiar):
    datos = yaml.safe_load(CONTRADICCIONES.read_text(encoding="utf-8"))
    cambiar(datos)
    ruta = tmp_path / "contradicciones.yaml"
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    return ruta


def test_una_clave_desconocida_en_el_yaml_es_error(tmp_path):
    ruta = _yaml_alterado(tmp_path, lambda d: d["choques"][0].update(prevalece="PRO-SAC-021 §3"))
    with pytest.raises(ValidationError):
        bp.cargar_contradicciones(ruta)


def test_un_paso_fuera_de_1_a_4_es_error(tmp_path):
    ruta = _yaml_alterado(tmp_path, lambda d: d["choques"][0].update(paso=5))
    with pytest.raises(ValidationError):
        bp.cargar_contradicciones(ruta)


def test_un_choque_de_paso_1_sin_la_faq_de_un_lado_es_error(tmp_path):
    def sin_faq(datos):
        datos["choques"][0]["lados"][0] = [{"documento": "MAN-OPS-007", "seccion": "4"}]
    with pytest.raises(ValidationError, match="FAQ-OPS-001"):
        bp.cargar_contradicciones(_yaml_alterado(tmp_path, sin_faq))


def test_un_choque_de_paso_2_entre_dos_documentos_es_error(tmp_path):
    def otro_documento(datos):
        entrada = next(c for c in datos["choques"] if c["paso"] == 2)
        entrada["lados"][1] = [{"documento": "FAQ-OPS-001", "seccion": "P10"}]
    with pytest.raises(ValidationError, match="mismo documento"):
        bp.cargar_contradicciones(_yaml_alterado(tmp_path, otro_documento))


@pytest.mark.parametrize("cambio, mensaje", [
    (lambda d: d["choques"][0]["lados"][1][0].update(seccion="3.9"), "3.9"),
    (lambda d: d["choques"][0]["lados"][1][0].update(fila="Licuadoras"), "Licuadoras"),
    # Una marca que no está en ninguno de los dos lados no justifica el criterio.
    (lambda d: next(c for c in d["choques"] if c["id"] == "CH-05").update(marca="sin excepción"), "sin excepción"),
    (lambda d: next(c for c in d["choques"] if c["id"] == "CH-04").update(marca="siempre requieren"), "siempre requieren"),
    (lambda d: next(c for c in d["choques"] if c["paso"] == 3).update(categorias=["Bebidas"]), "Bebidas"),
    (lambda d: next(c for c in d["choques"] if c["paso"] == 4)["alcance"].update(documento="PRO-SAC-021"), "PRO-SAC-021"),
])
def test_una_entrada_que_no_cuadra_con_los_documentos_es_error_al_validarla(abiertas, tmp_path, cambio, mensaje):
    entradas = bp.cargar_contradicciones(_yaml_alterado(tmp_path, cambio))
    with abiertas.sesion() as sesion:
        indice = bp.leer_indice(sesion)
    with pytest.raises(bp.ContradiccionInvalida, match=re.escape(mensaje)):
        bp.validar(entradas, indice, definiciones.supuestos())


def test_la_herramienta_valida_el_yaml_en_cada_llamada_y_no_responde_con_una_entrada_rota(abiertas, tmp_path):
    entradas = bp.cargar_contradicciones(
        _yaml_alterado(tmp_path, lambda d: d["choques"][0]["lados"][1][0].update(seccion="3.9"))
    )
    with pytest.raises(bp.ContradiccionInvalida, match="3.9"):
        buscar(abiertas, SECCIONES_ACEPTADAS["P58"][0], entradas=entradas)


def test_una_marca_que_esta_en_los_dos_lados_es_error_porque_no_decide(abiertas, tmp_path):
    # "siempre" sola está en §4.1 ("siempre que el cliente presente el ticket") y en §9.1.
    entradas = bp.cargar_contradicciones(
        _yaml_alterado(tmp_path, lambda d: next(c for c in d["choques"] if c["id"] == "CH-05").update(marca="siempre"))
    )
    with abiertas.sesion() as sesion:
        indice = bp.leer_indice(sesion)
    with pytest.raises(bp.ContradiccionInvalida, match="los dos lados"):
        bp.validar(entradas, indice, definiciones.supuestos())


# ------------------------------------------------------------------ peso y paso por el modelo

# Tope del resultado que llega al modelo, tomado de las metas del plan: 9,000 tokens de entrada por turno
# en el p90, dos llamadas en una pregunta de política con 1,900 tokens de prompt y herramientas cada una,
# y 500 para la pregunta y el historial: 9,000 - 2 x 1,900 - 500 = 4,700. estimar_tokens cuenta por
# arriba; el conteo exacto pide la llave (T6.1). Con el texto completo de los fragmentos, P58 y P61
# estiman más (traen MAN-OPS-007 §3 o PRO-SAC-021 §3 y §6, las secciones más largas).
TOPE_TOKENS_RESULTADO = 9000 - 2 * 1900 - 500


def test_el_resultado_de_P10_pesa_menos_que_el_tope_que_sale_de_las_metas_del_plan(abiertas):
    resultado = buscar(abiertas, SECCIONES_ACEPTADAS["P10"][0])
    assert estimar_tokens(resultado.texto_modelo()) <= TOPE_TOKENS_RESULTADO


def test_el_modelo_recibe_los_fragmentos_y_los_choques_por_function_model(abiertas):
    recibido = []

    def modelo(mensajes, info: AgentInfo) -> ModelResponse:
        if len(mensajes) == 1:
            return ModelResponse(parts=[ToolCallPart("buscar_politicas", {"entrada": {"consulta": SECCIONES_ACEPTADAS["P10"][0]}})])
        recibido.extend(p for m in mensajes for p in m.parts if isinstance(p, ToolReturnPart))
        return ModelResponse(parts=[TextPart("listo")])

    agente = Agent(FunctionModel(modelo))

    @agente.tool_plain
    def buscar_politicas(entrada: EntradaPoliticas) -> str:
        with abiertas.sesion() as sesion:
            return bp.buscar_politicas(sesion, entrada).texto_modelo()

    agente.run_sync("p")
    assert len(recibido) == 1
    contenido = recibido[0].content
    assert "FAQ-OPS-001 P15, p. 4" in contenido
    assert "CH-01" in contenido
    assert "match_bm25" not in contenido


def test_la_descripcion_de_la_herramienta_pide_escribir_la_consulta_con_las_palabras_de_los_documentos():
    assert "palabras de los documentos" in bp.DESCRIPCION
    assert estimar_tokens(bp.DESCRIPCION) <= 200


def test_el_producto_vendido_caducado_trae_el_texto_de_la_faq_que_cede(abiertas):
    """Sin él, la respuesta solo puede decir que la FAQ difiere, sin decir en qué (T5.11, lectura de aca0f6d)."""
    resultado = buscar(abiertas, "cliente devuelve con ticket un producto perecedero caducado plazo")
    assert choque(resultado, "CH-02")["paso"] == 1
    (agregado,) = [f for f in resultado.filas if f["id"] == "FAQ-OPS-001#P13"]
    assert agregado["motivo"] in ("bm25", "choque CH-02")


def test_el_mismo_choque_sin_hablar_de_caducado_no_arrastra_la_faq(abiertas):
    resultado = buscar(abiertas, "plazo de devolución de perecederos")
    assert choque(resultado, "CH-02")["paso"] == 1
    assert ("FAQ-OPS-001", "P13") not in secciones(resultado)


# ------------------------------------------------------------------ la sección que define un término (D34-B)

# Una consulta larga, como las que escribe el modelo: sus otras palabras dejan la tabla de prioridades fuera de los 4.
CONSULTA_LARGA = "prioridad tickets fallas de refrigeración equipo de frío mantenimiento urgencia"


def test_una_busqueda_sobre_prioridades_trae_la_tabla_que_las_define_aunque_bm25_la_deje_fuera(abiertas):
    resultado = buscar(abiertas, CONSULTA_LARGA)
    motivos = [f["motivo"] for f in resultado.filas]
    assert motivos.count("bm25") == 4
    (tabla,) = [f for f in resultado.filas if (f["documento"], f["seccion"]) == ("FAQ-OPS-001", "P20")]
    assert tabla["motivo"] == "define prioridad"
    assert "Crítica" in tabla["texto"]
    assert "FAQ-OPS-001 P20" in tabla["cita"]


@pytest.mark.parametrize("consulta", ["ticket mal priorizado por la tienda", "qué prioridades tiene un ticket de la bascula", "prioridad de una falla de luz"])
def test_la_palabra_se_compara_por_su_raiz(abiertas, consulta):
    assert ("FAQ-OPS-001", "P20") in secciones(buscar(abiertas, consulta))


def test_si_bm25_ya_la_trae_no_se_repite(abiertas):
    resultado = buscar(abiertas, "cómo levanto un ticket a la mesa de servicio y qué prioridades existen")
    tablas = [f for f in resultado.filas if (f["documento"], f["seccion"]) == ("FAQ-OPS-001", "P20")]
    assert [f["motivo"] for f in tablas] == ["bm25"]


def test_sin_esa_palabra_no_llega_la_tabla_de_prioridades(abiertas):
    assert ("FAQ-OPS-001", "P20") not in secciones(buscar(abiertas, "tolerancia de merma de lácteos"))


def test_con_otro_documento_pedido_no_se_agrega_la_seccion_que_define(abiertas):
    resultado = buscar(abiertas, CONSULTA_LARGA, documento="MAN-OPS-007")
    assert ("FAQ-OPS-001", "P20") not in secciones(resultado)


def test_una_seccion_que_define_y_no_existe_es_error_al_validarla(abiertas, tmp_path):
    entradas = bp.cargar_contradicciones(
        _yaml_alterado(tmp_path, lambda d: d["secciones_que_definen"][0].update(seccion="P99"))
    )
    with abiertas.sesion() as sesion:
        indice = bp.leer_indice(sesion)
    with pytest.raises(bp.ContradiccionInvalida, match="P99"):
        bp.validar(entradas, indice, definiciones.supuestos())


def test_una_seccion_que_define_rechaza_una_clave_desconocida(tmp_path):
    ruta = _yaml_alterado(tmp_path, lambda d: d["secciones_que_definen"][0].update(respuesta="Crítica"))
    with pytest.raises(ValidationError):
        bp.cargar_contradicciones(ruta)


def test_la_seccion_que_define_dice_como_aplicarla_a_un_ticket(abiertas):
    # El título de un ticket no dice cuánto duró la falla: sin abrir la descripción, el modelo aplica la tabla a medias.
    resultado = buscar(abiertas, CONSULTA_LARGA)
    aviso = resultado.detalle["antes_de_contestar"]
    assert "metrica detalle" in aviso
    assert "criterio de los documentos" in aviso
    assert resultado.para_modelo()["detalle"]["antes_de_contestar"] == aviso


def test_el_aviso_llega_tambien_si_bm25_ya_trajo_la_seccion(abiertas):
    resultado = buscar(abiertas, "cómo levanto un ticket a la mesa de servicio y qué prioridades existen")
    assert "metrica detalle" in resultado.detalle["antes_de_contestar"]


def test_sin_esa_palabra_no_hay_aviso(abiertas):
    assert "antes_de_contestar" not in buscar(abiertas, "tolerancia de merma de lácteos").detalle


def test_el_aviso_de_una_seccion_que_define_no_trae_cifras():
    # Dice cómo aplicar la sección y no qué contestar (G2).
    for definida in bp.contradicciones().secciones_que_definen:
        assert not re.search(r"\d", definida.aviso)
