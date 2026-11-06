"""Herramienta de ventas (T2.3, C1.1, C1.2 y C1.4).

Acepta: P01, P02, P03, P05, P25, P26, P28, P30, P44, P54 y P55 dan las cifras de la rúbrica, y la
venta por m² de Anáhuac da 1,847.53. Además cierra la mitad pendiente de T1.1: con una copia de
config/supuestos.yaml que pone incluir_devoluciones en false, la misma herramienta da 17,272,235.76 en
P03 sin tocar el código.

Las cifras salen de docs/planes/2026-10-30-rubrica-evaluacion.md y viven solo aquí (G2): el código de la
herramienta no trae ninguna. Corre sin red ni llaves sobre la base que construye tests/conftest.py en un
directorio temporal; los textos de tienda, producto y periodo son los de la batería, con sus errores.
"""

import datetime as dt
import inspect
import json
import re
from decimal import Decimal

import duckdb
import pytest
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agente import definiciones
from agente.herramientas import comun, ventas

# La fecha de la rúbrica; el resolvedor toma como hoy el 1 de septiembre (D3-A).
HOY_REAL = dt.date(2026, 10, 30)


@pytest.fixture(scope="module")
def con(base_construida):
    conexion = duckdb.connect(str(base_construida.base), read_only=True)
    yield conexion
    conexion.close()


def correr(con, s=None, **campos):
    """La herramienta con su propio cursor, como la llama el agente (Consultas.sesion())."""
    return ventas.ventas(con.cursor(), comun.EntradaVentas(**campos), s=s, hoy_real=HOY_REAL)


@pytest.fixture
def sin_devoluciones(copia_config):
    """Supuestos de una copia de config/ con incluir_devoluciones en false; el resto del YAML igual."""
    ruta = copia_config / "supuestos.yaml"
    texto = ruta.read_text(encoding="utf-8")
    cambiado = texto.replace("incluir_devoluciones: true", "incluir_devoluciones: false")
    assert cambiado.count("incluir_devoluciones: false") == 1
    ruta.write_text(cambiado, encoding="utf-8")
    return definiciones.cargar(ruta)


def _fila(resultado, **clave):
    encontradas = [f for f in resultado.filas if all(f.get(k) == v for k, v in clave.items())]
    assert len(encontradas) == 1, (clave, resultado.filas)
    return encontradas[0]


def _a_decimal(texto):
    return Decimal(texto.replace(",", "").rstrip("%"))


# ------------------------------------------------------------------ P03 y la definición de venta neta

def test_p03_venta_total_con_devoluciones_descontadas(con):
    r = correr(con)
    assert r.estado == "ok"
    assert r.cifras["venta"] == "17,228,300.26"
    assert r.cifras["devoluciones_descontadas"] == "43,935.50"
    assert r.definicion == definiciones.supuestos().ventas.definicion
    assert r.corte == dt.date(2026, 8, 31)
    assert r.unidad == "pesos (MXN)"
    assert r.filtros["tiendas"] == ["T01", "T02", "T03", "T04"]
    assert r.filtros["desde"] == dt.date(2026, 6, 1)
    assert r.filtros["hasta"] == dt.date(2026, 8, 31)
    assert r.fuente == "ventas.csv"


def test_p03_sin_devoluciones_cambia_con_el_yaml_sin_tocar_el_codigo(con, sin_devoluciones):
    """Mitad pendiente de T1.1: la misma base y el mismo código; solo cambió la línea del YAML."""
    r = correr(con, s=sin_devoluciones)
    assert r.estado == "ok"
    assert r.cifras["venta"] == "17,272,235.76"
    assert r.cifras["devoluciones_sin_restar"] == "43,935.50"
    assert "devoluciones_descontadas" not in r.cifras
    assert "sin restar las devoluciones" in r.definicion
    # La condición de la consulta es la que da definiciones.py para esa copia del YAML.
    assert sin_devoluciones.ventas.condicion("v") in r.consulta


def test_sin_devoluciones_cambia_tambien_p01_p02_p54_y_m2(con, sin_devoluciones):
    p01 = correr(con, s=sin_devoluciones, producto="leche entera", periodo="julio")
    assert (p01.cifras["venta"], p01.cifras["unidades_piezas"]) == ("91,801.60", "3,337")
    p02 = correr(con, s=sin_devoluciones, metrica="unidades", producto="kilos d pollo")
    assert p02.cifras["unidades_kg"] == "6,964"
    p54 = correr(con, s=sin_devoluciones, producto="leche entera", periodo="junio", comparar_con="agosto")
    assert p54.cifras["unidades_piezas (junio de 2026)"] == "3,232"
    assert p54.cifras["unidades_piezas (agosto de 2026)"] == "3,535"
    m2 = correr(con, s=sin_devoluciones, metrica="venta_por_m2", tiendas=["anahuac"])
    assert m2.cifras["venta_por_m2"] == "1,854.67"


def test_las_devoluciones_no_dependen_de_incluir_devoluciones(con, sin_devoluciones):
    """Las devoluciones son las filas negativas con las dos definiciones de venta."""
    con_def = correr(con, metrica="devoluciones", tiendas=["cumbres"])
    sin_def = correr(con, s=sin_devoluciones, metrica="devoluciones", tiendas=["cumbres"])
    for clave in ("registros", "pesos", "unidades_kg", "unidades_piezas"):
        assert con_def.cifras[clave] == sin_def.cifras[clave]


def test_el_codigo_no_escribe_la_regla_de_venta_neta():
    """La regla de qué filas entran vive en definiciones.py; ventas.py no compara unidades con cero."""
    fuente = inspect.getsource(ventas)
    assert not re.search(r"unidades\s*[<>]=?\s*0", fuente)
    assert "en_venta_neta" not in fuente
    assert "es_devolucion" not in fuente
    for cifra in ("17,228,300.26", "17228300.26", "1,847.53", "1847.53", "43,935.50"):
        assert cifra not in fuente


# ------------------------------------------------------------------ P01 y P54: SKU sustituto

def test_p01_leche_entera_julio_suma_el_sku_sustituto(con):
    r = correr(con, producto="leche entera", periodo="julio")
    assert r.estado == "ok"
    assert r.cifras["venta"] == "91,773.10"
    assert r.cifras["unidades_piezas"] == "3,336"
    assert "unidades_kg" not in r.cifras
    assert r.filtros["productos"] == ["SKU-1001", "SKU-1121"]
    vieja = _fila(r, sku="SKU-1001")
    nueva = _fila(r, sku="SKU-1121")
    assert (vieja["unidades"], vieja["venta"]) == ("1,513", "41,196.58")
    assert (nueva["unidades"], nueva["venta"]) == ("1,823", "50,576.52")
    assert any("SKU-1001" in nota and "SKU-1121" in nota and "se suman" in nota for nota in r.notas)


def test_p01_la_devolucion_de_julio_se_resta(con):
    r = correr(con, producto="leche entera", periodo="julio")
    assert r.cifras["devoluciones_descontadas"] == "28.50"


def test_p54_leche_entera_junio_contra_agosto(con):
    r = correr(con, producto="leche entera", periodo="junio", comparar_con="agosto")
    assert r.estado == "ok"
    assert r.cifras["unidades_piezas (junio de 2026)"] == "3,231"
    assert r.cifras["unidades_piezas (agosto de 2026)"] == "3,532"
    assert r.cifras["variacion unidades_piezas"] == "+9.32%"
    assert r.cifras["venta (junio de 2026)"] == "86,574.62"
    assert r.cifras["venta (agosto de 2026)"] == "99,903.26"
    # Cada SKU con lo que vendió en cada mes: SKU-1001 solo en junio y SKU-1121 solo en agosto.
    vieja = _fila(r, sku="SKU-1001")
    nueva = _fila(r, sku="SKU-1121")
    assert vieja["unidades_piezas (junio de 2026)"] == "3,231"
    assert vieja["unidades_piezas (agosto de 2026)"] == "0"
    assert nueva["unidades_piezas (junio de 2026)"] == "0"
    assert nueva["unidades_piezas (agosto de 2026)"] == "3,532"


def test_p54_el_orden_de_los_periodos_no_cambia_la_variacion(con):
    """La variación va siempre del periodo anterior al posterior, lo pida el modelo como lo pida."""
    al_reves = correr(con, producto="leche entera", periodo="agosto", comparar_con="junio")
    assert al_reves.cifras["variacion unidades_piezas"] == "+9.32%"
    assert any("de junio de 2026 a agosto de 2026" in nota for nota in al_reves.notas)


# ------------------------------------------------------------------ P02 y P25: kilos y piezas

def test_p02_kilos_de_pollo(con):
    r = correr(con, metrica="unidades", producto="kilos d pollo")
    assert r.estado == "ok"
    assert r.cifras["unidades_kg"] == "6,954"
    assert "unidades_piezas" not in r.cifras
    assert _fila(r, sku="SKU-1023")["unidades"] == "3,904"
    assert _fila(r, sku="SKU-1024")["unidades"] == "3,050"
    assert "10,610" not in r.texto_modelo()
    assert any("Nuggets" in nota and "Consomé" in nota for nota in r.notas)


def test_unidades_sin_filtro_no_suman_kilos_con_piezas(con):
    r = correr(con, metrica="unidades")
    assert set(r.cifras) == {"unidades_kg", "unidades_piezas"}
    assert "por separado" in r.unidad
    por_tienda = correr(con, metrica="unidades", agrupar_por="tienda")
    for fila in por_tienda.filas:
        assert "unidades" not in fila
        assert fila["unidades_kg"] and fila["unidades_piezas"]


def test_p25_producto_que_mas_se_vende_en_pesos(con):
    r = correr(con, agrupar_por="producto")
    assert r.estado == "ok"
    primero = r.filas[0]
    assert primero["producto"] == "Pechuga de pollo sin hueso kg"
    assert primero["venta"] == "524,073.50"
    assert primero["unidad"] == "kg"
    assert len(r.filas) == 10


def test_p25_ranking_en_unidades_separa_kilos_de_piezas(con):
    r = correr(con, metrica="unidades", agrupar_por="producto")
    piezas = [f for f in r.filas if f["unidad"] == "piezas o paquetes"]
    kilos = [f for f in r.filas if f["unidad"] == "kg"]
    assert piezas[0]["producto"].startswith("Bolillo")
    assert piezas[0]["unidades"] == "12,134"
    assert kilos[0]["producto"].startswith("Plátano")
    assert kilos[0]["unidades"] == "11,289"
    assert len(piezas) == 10 and len(kilos) == 10
    assert any("por separado" in nota for nota in r.notas)
    for grupo in (piezas, kilos):
        valores = [_a_decimal(f["unidades"]) for f in grupo]
        assert valores == sorted(valores, reverse=True)


def test_el_ranking_por_producto_suma_el_sustituto(con):
    r = correr(con, agrupar_por="producto", limite=50, orden="mayor")
    leche = [f for f in r.filas if "SKU-1001" in f["sku"]]
    assert len(leche) == 1
    assert leche[0]["sku"] == "SKU-1001 + SKU-1121"
    assert not [f for f in r.filas if f["sku"] == "SKU-1121"]
    assert any("SKU-1001 + SKU-1121" in nota for nota in r.notas)


# ------------------------------------------------------------------ P05: canal

def test_p05_venta_por_internet_y_su_peso(con):
    r = correr(con, canal="Ecommerce")
    assert r.estado == "ok"
    assert r.cifras["venta"] == "357,010.50"
    assert r.cifras["pct_de_la_venta_de_las_tiendas"] == "2.07%"
    assert r.cifras["pct_de_la_venta_de_las_tiendas_con_canal"] == "2.97%"
    assert r.filtros["tiendas"] == ["T01", "T02"]
    assert _fila(r, tienda_id="T01")["venta"] == "188,179.50"
    assert _fila(r, tienda_id="T02")["venta"] == "168,831.00"
    assert not [f for f in r.filas if f["tienda_id"] in ("T03", "T04")]
    assert any("Anáhuac" in nota and "Lomas del Sur" in nota and "quedan fuera" in nota for nota in r.notas)


def test_ecommerce_en_una_mi_tienda_es_canal_inexistente(con):
    r = correr(con, canal="ecommerce", tiendas=["anahuac"])
    assert r.estado == "canal_inexistente"
    assert not r.cifras
    assert "0.00" not in r.texto_modelo()


def test_agrupar_por_canal_dice_que_las_mi_tienda_no_tienen_ecommerce(con):
    r = correr(con, agrupar_por="canal")
    assert {f["canal"] for f in r.filas} == {"Piso", "Ecommerce"}
    assert any("Ecommerce" in nota and "Anáhuac" in nota for nota in r.notas)
    total = sum(_a_decimal(f["venta"]) for f in r.filas)
    assert total == Decimal("17228300.26")


# ------------------------------------------------------------------ P26 y P44: tiendas

def test_p26_monterrey_es_ambigua(con):
    r = correr(con, tiendas=["la de monterrey"], periodo="agosto")
    assert r.estado == "entidad_ambigua"
    assert not r.cifras
    candidatos = " ".join(r.detalle["candidatos"])
    assert "T01" in candidatos and "T02" in candidatos


def test_p26_las_dos_de_monterrey_por_separado(con):
    r = correr(con, tiendas=["valle norte", "cumbres"], periodo="agosto", agrupar_por="tienda")
    assert r.estado == "ok"
    assert _fila(r, tienda_id="T01")["venta"] == "2,163,062.36"
    assert _fila(r, tienda_id="T02")["venta"] == "1,973,086.37"
    assert r.cifras["venta"] == "4,136,148.73"


def test_p44_cumbres_julio_y_ticket_promedio(con):
    r = correr(con, tiendas=["cumbres"], periodo="julio")
    assert r.cifras["venta"] == "1,810,013.00"
    ticket = correr(con, metrica="ticket_promedio", tiendas=["cumbres"], periodo="julio")
    assert ticket.estado == "metrica_no_calculable"
    assert not ticket.cifras
    assert "transacciones" in ticket.resumen


@pytest.mark.parametrize("metrica", ["ticket_promedio", "venta_por_hora", "meta_venta"])
def test_metricas_no_calculables(con, metrica):
    r = correr(con, metrica=metrica)
    assert r.estado == "metrica_no_calculable"
    assert not r.cifras and not r.filas


def test_cumbre_deja_la_nota_de_como_se_interpreto(con):
    r = correr(con, tiendas=["cumbre"], periodo="julio")
    assert r.cifras["venta"] == "1,810,013.00"
    assert "'cumbre' se interpretó como Tienda Cumbres (T02)" in r.notas


def test_tienda_inexistente(con):
    r = correr(con, tiendas=["guadalupe"])
    assert r.estado == "entidad_inexistente"
    assert not r.cifras


def test_texto_con_comillas_va_como_parametro(con):
    r = correr(con, tiendas=["T01' OR '1'='1"])
    assert r.estado == "entidad_inexistente"


# ------------------------------------------------------------------ P28 y P55: devoluciones

def test_p55_devoluciones_de_cumbres(con):
    r = correr(con, metrica="devoluciones", tiendas=["cumbres"])
    assert r.estado == "ok"
    assert r.cifras["registros"] == "105"
    assert r.cifras["pesos"] == "10,262.50"
    # La rúbrica da 208 unidades sumando kilos con piezas; la herramienta las da por separado (C1.4).
    assert "unidades" not in r.cifras
    kilos = _a_decimal(r.cifras["unidades_kg"])
    piezas = _a_decimal(r.cifras["unidades_piezas"])
    assert kilos + piezas == 208
    assert "unidades negativas" in r.definicion


def test_p28_tienda_con_mas_devoluciones(con):
    r = correr(con, metrica="devoluciones", agrupar_por="tienda")
    primero = r.filas[0]
    assert primero["tienda_id"] == "T01"
    assert (primero["pesos"], primero["registros"]) == ("12,766.50", "117")
    assert _a_decimal(primero["unidades_kg"]) + _a_decimal(primero["unidades_piezas"]) == 225
    assert primero["pct_de_la_venta"] == "0.199%"
    lomas = _fila(r, tienda_id="T04")
    assert lomas["pct_de_la_venta"] == "0.418%"
    assert lomas["registros"] == "90"
    assert min(int(f["registros"]) for f in r.filas) == 90
    assert r.cifras["pesos"] == "43,935.50"


# ------------------------------------------------------------------ P30: comparación y por día

def test_p30_julio_contra_junio_en_total_y_por_dia(con):
    r = correr(con, periodo="julio", comparar_con="junio")
    assert r.estado == "ok"
    assert r.cifras["venta (junio de 2026)"] == "5,597,388.24"
    assert r.cifras["venta (julio de 2026)"] == "5,712,163.10"
    assert r.cifras["variacion venta"] == "+2.05%"
    assert r.cifras["venta_por_dia (junio de 2026, 30 días)"] == "186,579.61"
    assert r.cifras["venta_por_dia (julio de 2026, 31 días)"] == "184,263.33"
    assert r.cifras["variacion venta_por_dia"] == "-1.24%"
    assert r.filtros["comparar_desde"] == dt.date(2026, 6, 1)


def test_comparacion_agrupada_por_tienda(con):
    r = correr(con, periodo="julio", comparar_con="junio", agrupar_por="tienda")
    assert len(r.filas) == 4
    suma_julio = sum(_a_decimal(f["venta (julio de 2026)"]) for f in r.filas)
    assert suma_julio == Decimal("5712163.10")
    assert all(f["variacion venta"] for f in r.filas)


def test_comparar_con_un_periodo_sin_datos(con):
    r = correr(con, periodo="julio", comparar_con="septiembre")
    assert r.estado == "fuera_de_periodo"
    assert not r.cifras


# ------------------------------------------------------------------ venta por m² y margen

def test_venta_por_m2_de_anahuac(con):
    r = correr(con, metrica="venta_por_m2", tiendas=["anahuac"])
    assert r.estado == "ok"
    assert r.cifras["venta_por_m2"] == "1,847.53"
    assert r.cifras["m2"] == "1,500"
    assert r.unidad == "pesos (MXN) por m²"


def test_venta_por_m2_de_las_cuatro(con):
    r = correr(con, metrica="venta_por_m2")
    assert _fila(r, tienda_id="T03")["venta_por_m2"] == "1,847.53"
    assert len(r.filas) == 4
    assert r.cifras["m2"] == "10,850"


def test_margen_con_el_costo_del_catalogo(con):
    r = correr(con, metrica="margen", tiendas=["valle norte"], periodo="julio", categoria="lácteos")
    assert r.estado == "ok"
    esperado = con.execute(
        "SELECT sum(v.venta_neta_mxn), sum(v.unidades * c.costo_mxn) FROM ventas v "
        "JOIN catalogo c ON c.sku = v.sku WHERE v.tienda_id = 'T01' AND c.categoria = 'Lácteos' "
        "AND v.fecha BETWEEN DATE '2026-07-01' AND DATE '2026-07-31'"
    ).fetchone()
    venta, costo = esperado
    assert r.cifras["venta"] == comun.num(venta)
    assert r.cifras["costo"] == comun.num(costo)
    assert r.cifras["margen"] == comun.num(venta - costo)
    assert r.cifras["margen_pct"] == comun.pct((venta - costo) / venta)
    assert definiciones.supuestos().margen.supuesto in r.notas


# ------------------------------------------------------------------ agrupaciones y grano

def test_por_mes_suma_el_total(con):
    r = correr(con, grano="mes")
    assert [f["periodo"] for f in r.filas] == ["2026-06", "2026-07", "2026-08"]
    assert _fila(r, periodo="2026-07")["venta"] == "5,712,163.10"
    assert sum(_a_decimal(f["venta"]) for f in r.filas) == Decimal("17228300.26")


def test_por_dia_de_la_semana(con):
    r = correr(con, agrupar_por="dia_semana")
    assert len(r.filas) == 7
    assert {f["dia_semana"] for f in r.filas} == {
        "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"}
    assert sum(int(f["dias"]) for f in r.filas) == 92
    # Del lunes 1 de junio al lunes 31 de agosto: 14 lunes y 13 de cada otro día.
    assert _fila(r, dia_semana="lunes")["dias"] == "14"
    assert {f["dias"] for f in r.filas if f["dia_semana"] != "lunes"} == {"13"}
    (lunes,) = con.execute("SELECT sum(venta_neta_mxn) FROM ventas WHERE dayname(fecha) = 'Monday'").fetchone()
    assert _fila(r, dia_semana="lunes")["venta"] == comun.num(lunes)
    assert sum(_a_decimal(f["venta"]) for f in r.filas) == Decimal("17228300.26")
    for fila in r.filas:
        promedio = _a_decimal(fila["venta"]) / int(fila["dias"])
        assert abs(promedio - _a_decimal(fila["promedio_por_dia"])) <= Decimal("0.005")
    valores = [_a_decimal(f["venta"]) for f in r.filas]
    assert valores == sorted(valores, reverse=True)


def test_por_categoria_suma_el_total(con):
    r = correr(con, agrupar_por="categoria")
    assert len(r.filas) == 8
    assert sum(_a_decimal(f["venta"]) for f in r.filas) == Decimal("17228300.26")


def test_por_dia_respeta_el_tope_de_filas(con):
    r = correr(con, grano="dia")
    assert r.n_filas == 92
    assert len(r.filas) == comun.MAX_FILAS
    assert r.filas[0]["periodo"] == "2026-06-01"


def test_orden_menor(con):
    r = correr(con, agrupar_por="tienda", orden="menor", limite=1)
    assert r.filas[0]["tienda_id"] == "T04"
    assert r.n_filas == 4


def test_categoria_inexistente(con):
    r = correr(con, categoria="juguetes")
    assert r.estado == "entidad_inexistente"


def test_periodo_fuera_del_corte(con):
    r = correr(con, periodo="septiembre")
    assert r.estado == "fuera_de_periodo"
    assert not r.cifras


def test_sin_filas_no_es_venta_cero(con):
    r = correr(con, producto="tequila reposado", tiendas=["anahuac"], desde="2026-06-03", hasta="2026-06-03")
    assert r.estado == "sin_resultados"
    assert not r.cifras


# ------------------------------------------------------------------ contrato de salida

def test_p03_pesa_300_tokens_o_menos_y_no_lleva_la_consulta(con):
    r = correr(con)
    texto = r.texto_modelo()
    assert comun.estimar_tokens(texto) <= 300, (comun.estimar_tokens(texto), texto)
    assert "SELECT" not in texto
    assert "SELECT" in r.texto_registro()


def test_entrada_fuera_del_enum_es_error_de_validacion():
    with pytest.raises(ValidationError):
        comun.EntradaVentas(metrica="ventas_por_hora_pico")
    with pytest.raises(ValidationError):
        comun.EntradaVentas(agrupar_por="ciudad")


def test_el_resultado_de_cada_cifra_es_texto(con):
    for campos in ({}, {"metrica": "margen"}, {"metrica": "devoluciones", "agrupar_por": "tienda"}):
        r = correr(con, **campos)
        assert all(isinstance(v, str) for v in r.cifras.values())
        assert all(isinstance(v, (str, type(None))) for f in r.filas for v in f.values())


def test_el_ranking_en_unidades_empieza_por_la_unidad_del_primer_lugar(con):
    r = correr(con, metrica="unidades", agrupar_por="producto", limite=3)
    assert r.filas[0]["producto"].startswith("Bolillo")
    assert [f["unidad"] for f in r.filas] == ["piezas o paquetes"] * 3 + ["kg"] * 3


def test_venta_por_m2_comparada_y_por_mes(con):
    comparada = correr(con, metrica="venta_por_m2", tiendas=["anahuac"], periodo="julio", comparar_con="junio")
    assert comparada.estado == "ok"
    assert comparada.cifras["m2 (julio de 2026)"] == "1,500"
    por_mes = correr(con, metrica="venta_por_m2", grano="mes")
    assert por_mes.cifras["m2"] == "10,850"
    assert len(por_mes.filas) == 12


def test_comparar_con_el_mismo_periodo_no_inventa_una_variacion(con):
    r = correr(con, periodo="julio", comparar_con="julio")
    assert r.estado == "periodo_no_entendido"
    assert not r.cifras


def test_una_fila_sin_base_no_lleva_variacion_vacia(con):
    r = correr(con, producto="leche entera", periodo="junio", comparar_con="agosto")
    nueva = _fila(r, sku="SKU-1121")
    assert "variacion venta" not in nueva
    assert None not in nueva.values()


def test_en_el_agente_p44_da_la_venta_y_el_error_con_tipo(con):
    """Con FunctionModel (sin red ni llaves): las dos llamadas de P44 llegan validadas y vuelven al modelo."""
    recibido = []

    def modelo(mensajes, info: AgentInfo):
        if len(mensajes) == 1:
            return ModelResponse(parts=[
                ToolCallPart("ventas", {"tiendas": ["cumbres"], "periodo": "julio"}, tool_call_id="venta"),
                ToolCallPart("ventas", {"metrica": "ticket_promedio", "tiendas": ["cumbres"], "periodo": "julio"},
                             tool_call_id="ticket"),
            ])
        recibido.extend(p for p in mensajes[-1].parts if isinstance(p, ToolReturnPart))
        return ModelResponse(parts=[TextPart("listo")])

    agente = Agent(FunctionModel(modelo))

    @agente.tool_plain(name="ventas")
    def herramienta_ventas(entrada: comun.EntradaVentas) -> str:
        return ventas.ventas(con.cursor(), entrada, hoy_real=HOY_REAL).texto_modelo()

    agente.run_sync("cuanto vendio cumbres en julio y cual fue su ticket promedio?")
    por_id = {p.tool_call_id: json.loads(p.model_response_str()) for p in recibido}
    assert por_id["venta"]["cifras"]["venta"] == "1,810,013.00"
    assert por_id["ticket"]["estado"] == "metrica_no_calculable"
    assert "cifras" not in por_id["ticket"]
