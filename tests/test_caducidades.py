"""Herramienta de caducidades (T2.5): lotes a retirar y a rebajar al corte según POL-OPS-014.

Acepta: P13 da 2 retiros y 5 rebajas; P12 da el retiro del 10 de septiembre; la ventana de 3 días
da 7 lotes (cambio en vivo de C9.3). Las cifras de P13, P12 y C9.3 son las de la rúbrica; la base se
construye en un directorio temporal (conftest.base_construida) y las pruebas corren sin red ni modelo.
"""

import datetime as dt
import json

import duckdb
import pytest
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agente.herramientas import caducidades as cad
from agente.herramientas import comun

CORTE = dt.date(2026, 9, 1)


@pytest.fixture(scope="module")
def con(base_construida):
    conexion = duckdb.connect(str(base_construida.base), read_only=True)
    yield conexion
    conexion.close()


def _llamar(con, **argumentos) -> comun.Resultado:
    return cad.caducidades(con, comun.EntradaCaducidades.model_validate(argumentos))


def _clave(fila):
    return (fila["producto"], fila["tienda"])


def _por_accion(resultado, accion):
    return {_clave(f): f for f in resultado.filas if f["accion"] == accion}


# ------------------------------------------------------------------ P13: lácteos a retirar o rebajar

def test_p13_da_2_retiros_y_5_rebajas_con_el_corte_del_1_de_septiembre(con):
    r = _llamar(con, categoria="lacteos")
    assert r.estado == "ok"
    assert r.corte == CORTE
    assert r.cifras["lotes_a_retirar"] == "2"
    assert r.cifras["lotes_a_rebajar"] == "5"
    assert r.n_filas == 7

    retiros = _por_accion(r, "retirar")
    assert set(retiros) == {
        ("Queso manchego rebanado 200 g", "Tienda Anáhuac"),
        ("Yogur natural 1 kg", "Tienda Lomas del Sur"),
    }
    assert retiros[("Queso manchego rebanado 200 g", "Tienda Anáhuac")]["existencia"] == "16"
    assert retiros[("Yogur natural 1 kg", "Tienda Lomas del Sur")]["existencia"] == "7"
    for fila in retiros.values():
        assert fila["caducidad"] == "2026-09-03"
        assert fila["regla"] == "§5.2"
        assert "descuento" not in fila

    rebajas = {clave: (f["dias"], f["descuento"]) for clave, f in _por_accion(r, "rebajar").items()}
    assert rebajas == {
        ("Mantequilla con sal 90 g", "Tienda Anáhuac"): ("3", "35%"),
        ("Leche light 1 L", "Tienda Anáhuac"): ("4", "25%"),
        ("Huevo blanco 12 piezas", "Tienda Cumbres"): ("4", "25%"),
        ("Mantequilla con sal 90 g", "Tienda Cumbres"): ("5", "15%"),
        ("Queso Oaxaca 400 g", "Tienda Lomas del Sur"): ("5", "15%"),
    }
    assert all(f["regla"] == "§6" for f in r.filas if f["accion"] == "rebajar")


def test_p13_lacteos_como_producto_da_los_mismos_lotes(con):
    por_categoria = _llamar(con, categoria="lácteos")
    por_producto = _llamar(con, producto="lácteos")
    assert por_producto.cifras == por_categoria.cifras
    assert {_clave(f) for f in por_producto.filas} == {_clave(f) for f in por_categoria.filas}


def test_p13_avisa_que_2_retiros_y_3_rebajas_estan_en_tiendas_mi_tienda(con):
    r = _llamar(con, categoria="lacteos")
    nota = next(n for n in r.notas if "§1.1" in n)
    assert "Mi Tienda" in nota
    assert "2 retiros y 3 rebajas" in nota
    assert "Tienda Anáhuac (T03)" in nota and "Tienda Lomas del Sur (T04)" in nota
    assert "Tienda Cumbres" not in nota


def test_p13_aplica_la_regla_a_mi_tienda_como_supuesto_y_no_como_referencia(con):
    nota = next(n for n in _llamar(con, categoria="lacteos").notas if "§1.1" in n)
    assert "como supuesto" in nota
    assert "referencia" not in nota


def test_el_resumen_dice_que_es_al_corte_del_1_de_septiembre(con):
    assert "al corte del 1 de septiembre de 2026" in _llamar(con, categoria="lacteos").resumen


def test_un_solo_lote_fuera_de_alcance_se_dice_en_singular(con):
    r = _llamar(con, producto="yogurt")
    assert r.n_filas == 1
    assert any("1 retiro de esta consulta es de Tienda Lomas del Sur (T04)" in n for n in r.notas)


def test_p13_avisa_que_52_no_nombra_huevo_mantequilla_ni_manchego_y_da_la_otra_lectura(con):
    r = _llamar(con, categoria="lacteos")
    nota = next(n for n in r.notas if "no nombra" in n)
    assert "categoría Lácteos del catálogo" in nota
    for nombre in ("Huevo blanco 12 piezas", "Mantequilla con sal 90 g", "Queso manchego rebanado 200 g"):
        assert nombre in nota
    for nombrado in ("Yogur natural 1 kg", "Leche light 1 L", "Queso Oaxaca 400 g"):
        assert nombrado not in nota
    assert "1 retiro y 6 rebajas" in nota


def test_p13_dice_que_la_fila_de_2_dias_no_excluye_lacteos_pero_salen_de_rebaja(con):
    r = _llamar(con, categoria="lacteos")
    assert any("§6.1" in n and "2 días" in n for n in r.notas)


def test_p13_avisa_que_la_existencia_no_es_la_del_lote(con):
    r = _llamar(con, categoria="lacteos")
    assert any("no dice cuántas piezas son del lote" in n for n in r.notas)


def test_solo_retiros_deja_las_cifras_de_las_dos_acciones(con):
    r = _llamar(con, categoria="lacteos", accion="retirar")
    assert r.n_filas == 2
    assert {f["accion"] for f in r.filas} == {"retirar"}
    assert r.cifras["lotes_a_retirar"] == "2"
    assert r.cifras["lotes_a_rebajar"] == "5"


def test_p32_turno_2_ninguno_de_los_retiros_es_de_cumbres_y_cumbres_solo_tiene_rebajas(con):
    r = _llamar(con, categoria="lacteos", accion="retirar", tiendas=["cumbre"])
    assert r.estado == "ok"
    assert r.filas == []
    assert r.cifras["lotes_a_retirar"] == "0"
    assert r.cifras["lotes_a_rebajar"] == "2"
    assert "'cumbre' se interpretó como Tienda Cumbres (T02)" in r.notas
    assert r.filtros["tiendas"] == ["T02"]
    # Cumbres es Supermercado: no hay salvedad de alcance.
    assert not any("§1.1" in n for n in r.notas)


# ------------------------------------------------------------------ P12: el retiro del 10 de septiembre

def test_p12_la_leche_deslactosada_de_valle_norte_se_retira_el_10_de_septiembre(con):
    r = _llamar(con, producto="leche deslactosada", tiendas=["valle norte"])
    assert r.estado == "ok"
    assert r.n_filas == 1
    fila = r.filas[0]
    assert fila["caducidad"] == "2026-09-12"
    assert fila["dias"] == "11"
    assert fila["fecha_retiro"] == "2026-09-10"
    assert fila["ultimo_dia_venta"] == "2026-09-09"
    assert fila["rebaja_desde"] == "2026-09-07"
    assert fila["escala"] == "2026-09-07 15%, 2026-09-08 25%, 2026-09-09 35%"
    assert fila["regla"] == "§5.2"
    assert fila["accion"] == "ninguna"
    assert r.cifras == {"lotes": "1", "lotes_a_retirar": "0", "lotes_a_rebajar": "0"}


def test_una_subcategoria_dicha_como_producto_es_una_lista_con_la_ventana_de_la_politica(con):
    r = _llamar(con, producto="yogurt")
    assert r.filtros["ventana_dias"] == 5
    assert [(_clave(f), f["accion"]) for f in r.filas] == [(("Yogur natural 1 kg", "Tienda Lomas del Sur"), "retirar")]
    assert "escala" not in r.filas[0]


def test_p12_con_el_sku_da_el_mismo_lote(con):
    r = _llamar(con, sku="SKU-1002", tiendas=["T01"])
    assert [f["fecha_retiro"] for f in r.filas] == ["2026-09-10"]


# ------------------------------------------------------------------ C9.3: ventana de 3 días

def test_c93_la_ventana_de_3_dias_en_todas_las_categorias_da_7_lotes(con):
    r = _llamar(con, ventana_dias=3)
    assert r.estado == "ok"
    assert r.cifras["lotes"] == "7"
    assert r.n_filas == 7
    assert {f["caducidad"] for f in r.filas} == {"2026-09-03", "2026-09-04"}
    assert len({f["categoria"] for f in r.filas}) > 1
    assert r.filtros["ventana_dias"] == 3


def test_c93_la_ventana_del_1_al_3_de_septiembre_da_4_lotes(con):
    r = _llamar(con, ventana_dias=2)
    assert r.n_filas == 4
    assert {f["caducidad"] for f in r.filas} == {"2026-09-03"}


def test_la_ventana_se_dice_con_fechas(con):
    r = _llamar(con, ventana_dias=3)
    assert "del 1 al 4 de septiembre de 2026" in r.definicion


def test_sin_ventana_ni_producto_la_ventana_es_la_de_la_tabla_de_rebajas(con):
    sin_ventana = _llamar(con)
    con_cinco = _llamar(con, ventana_dias=5)
    assert sin_ventana.cifras == con_cinco.cifras
    assert sin_ventana.filtros["ventana_dias"] == 5


def test_los_perecederos_no_lacteos_se_rebajan_al_50_a_2_dias_por_51(con):
    r = _llamar(con, ventana_dias=2, categoria="panaderia")
    pastel = next(f for f in r.filas if f["producto"] == "Pastel de chocolate rebanada")
    assert pastel["accion"] == "rebajar"
    assert pastel["descuento"] == "50%"
    assert pastel["fecha_retiro"] == "2026-09-02"
    assert pastel["regla"] == "§6"


# ------------------------------------------------------------------ P39: producto sin caducidad

def test_p39_el_pan_de_caja_no_tiene_fecha_de_caducidad_y_no_se_inventa(con):
    r = _llamar(con, producto="pan de caja", tiendas=["cumbre"])
    assert r.filas == []
    assert r.cifras["lotes"] == "0"
    assert any("no tiene fecha de caducidad registrada" in n and "no perecedero" in n for n in r.notas)
    assert "2026-" not in r.texto_modelo().replace("2026-09-01", "")


# ------------------------------------------------------------------ reglas puras

@pytest.mark.parametrize(
    "categoria, dias, accion, descuento, regla",
    [
        ("Lácteos", 2, "retirar", None, "§5.2"),
        ("Lácteos", 3, "rebajar", 35, "§6"),
        ("Lácteos", 5, "rebajar", 15, "§6"),
        ("Lácteos", 6, "ninguna", None, "§5.2"),
        ("Lácteos", 0, "retirar", None, "§5.2"),
        ("Lácteos", -1, "retirar", None, "§5.2"),
        ("Panadería", 2, "rebajar", 50, "§6"),
        ("Panadería", 1, "retirar", None, "§5.1"),
        ("Carnes", 4, "rebajar", 25, "§6"),
        ("Carnes", 6, "ninguna", None, "§5.1"),
    ],
)
def test_clasificar(categoria, dias, accion, descuento, regla):
    clasificacion = cad.clasificar(categoria, dias)
    assert (clasificacion.accion, clasificacion.descuento, clasificacion.regla) == (accion, descuento, regla)


def test_la_fila_de_60_por_ciento_nunca_se_aplica_porque_cae_en_el_dia_de_retiro():
    descuentos = {cad.clasificar(c, d).descuento for c in ("Lácteos", "Carnes") for d in range(-2, 10)}
    assert 60 not in descuentos


def test_calendario_de_un_lacteo_y_de_otro_perecedero():
    lacteo = cad.calendario("Lácteos", dt.date(2026, 9, 12))
    assert (lacteo.fecha_retiro, lacteo.ultimo_dia_venta, lacteo.rebaja_desde) == (
        dt.date(2026, 9, 10), dt.date(2026, 9, 9), dt.date(2026, 9, 7))
    assert [d.day for d, _ in lacteo.escala] == [7, 8, 9]
    otro = cad.calendario("Carnes", dt.date(2026, 9, 12))
    assert (otro.fecha_retiro, otro.ultimo_dia_venta) == (dt.date(2026, 9, 11), dt.date(2026, 9, 10))
    assert otro.escala == [(dt.date(2026, 9, 7), 15), (dt.date(2026, 9, 8), 25), (dt.date(2026, 9, 9), 35),
                           (dt.date(2026, 9, 10), 50)]


# ------------------------------------------------------------------ errores con tipo

def test_una_tienda_que_no_existe_da_entidad_inexistente(con):
    r = _llamar(con, tiendas=["guadalupe"])
    assert r.estado == "entidad_inexistente"
    assert r.filas == [] and r.cifras == {}


def test_monterrey_da_entidad_ambigua(con):
    r = _llamar(con, tiendas=["monterrey"], categoria="lacteos")
    assert r.estado == "entidad_ambigua"


def test_una_categoria_que_no_existe_da_entidad_inexistente(con):
    r = _llamar(con, categoria="juguetes")
    assert r.estado == "entidad_inexistente"


def test_un_sku_que_no_esta_en_el_catalogo_da_entidad_inexistente(con):
    r = _llamar(con, sku="SKU-9999")
    assert r.estado == "entidad_inexistente"


def test_un_texto_con_comillas_no_llega_a_la_consulta(con):
    r = _llamar(con, producto="x'; DROP TABLE inventario; --")
    assert r.estado == "entidad_inexistente"
    assert con.execute("SELECT count(*) FROM inventario").fetchall()[0][0] == 480


def test_una_accion_fuera_del_enum_es_error_de_validacion():
    with pytest.raises(ValidationError):
        comun.EntradaCaducidades.model_validate({"accion": "regalar"})


# ------------------------------------------------------------------ salida

def test_la_salida_va_formateada_y_sin_la_consulta_para_el_modelo(con):
    r = _llamar(con, categoria="lacteos")
    assert all(isinstance(v, str) for f in r.filas for v in f.values())
    para_modelo = json.loads(r.texto_modelo())
    assert "consulta" not in para_modelo
    assert para_modelo["corte"] == "2026-09-01"
    assert "POL-OPS-014" in para_modelo["fuente"]
    registro = json.loads(r.texto_registro())
    sql, _, parametros = registro["consulta"].partition("\n-- parámetros: ")
    assert "v_inventario" in sql
    assert "Lácteos" not in sql  # el valor va como parámetro
    assert json.loads(parametros) == ["2026-09-01", ["T01", "T02", "T03", "T04"], "Lácteos", "2026-09-01", 5]


def test_la_definicion_cita_las_secciones_de_la_politica(con):
    r = _llamar(con, categoria="lacteos")
    for seccion in ("§5.1", "§5.2", "§6", "§6.1"):
        assert seccion in r.definicion


def test_en_el_agente_la_herramienta_recibe_los_argumentos_del_modelo(con):
    recibido = []

    def modelo(mensajes, info: AgentInfo):
        if len(mensajes) == 1:
            return ModelResponse(parts=[ToolCallPart("caducidades", {"categoria": "lacteos", "accion": "retirar"})])
        recibido.extend(p for p in mensajes[-1].parts if isinstance(p, ToolReturnPart))
        return ModelResponse(parts=[TextPart("listo")])

    agente = Agent(FunctionModel(modelo))

    @agente.tool_plain
    def caducidades(entrada: comun.EntradaCaducidades) -> str:
        return cad.caducidades(con, entrada).texto_modelo()

    agente.run_sync("q lacteos tengo q retirar?")
    datos = json.loads(recibido[0].model_response_str())
    assert datos["cifras"]["lotes_a_retirar"] == "2"
    assert len(datos["filas"]) == 2


# ------------------------------------------------------------------ alcance con la otra opción de D2

def test_si_d2_cambia_a_b_mi_tienda_queda_cubierta_y_no_hay_salvedad(con, copia_config, monkeypatch):
    from agente import definiciones

    ruta = copia_config / "supuestos.yaml"
    texto = ruta.read_text(encoding="utf-8")
    assert texto.count("  D2:\n    opcion: C") == 1
    ruta.write_text(texto.replace("  D2:\n    opcion: C", "  D2:\n    opcion: B"), encoding="utf-8")
    monkeypatch.setattr(cad, "supuestos", lambda: definiciones.cargar(ruta))
    r = _llamar(con, categoria="lacteos")
    assert r.cifras["lotes_a_retirar"] == "2"
    assert not any("§1.1" in n for n in r.notas)


def test_si_d2_cambia_a_a_la_regla_vuelve_a_ser_referencia(con, copia_config, monkeypatch):
    from agente import definiciones

    ruta = copia_config / "supuestos.yaml"
    texto = ruta.read_text(encoding="utf-8")
    ruta.write_text(texto.replace("  D2:\n    opcion: C", "  D2:\n    opcion: A"), encoding="utf-8")
    monkeypatch.setattr(cad, "supuestos", lambda: definiciones.cargar(ruta))
    nota = next(n for n in _llamar(con, categoria="lacteos").notas if "§1.1" in n)
    assert "como referencia" in nota
    assert "como supuesto" not in nota


# ------------------------------------------------------------------ notas de casos que el inventario no trae

def _lote(categoria, dias, producto="Producto de prueba", subcategoria="Prueba"):
    return cad.Lote("T01", "Tienda Valle Norte", "Supermercado", "SKU-0001", producto, categoria, subcategoria,
                    True, 10, CORTE + dt.timedelta(days=dias), dias)


def test_un_perecedero_no_lacteo_en_su_dia_de_retiro_nombra_el_choque_de_la_fila_de_60():
    lote = _lote("Panadería", 1)
    notas = cad._notas_de_reglas([(lote, cad.clasificar("Panadería", 1))])
    assert any("60%" in n and "§5.1" in n and "sin excepción" in n for n in notas)


def test_un_lote_ya_caducado_lo_dice():
    lote = _lote("Carnes", -2)
    notas = cad._notas_de_reglas([(lote, cad.clasificar("Carnes", -2))])
    assert "1 lote ya pasó su fecha de caducidad al corte" in notas


def test_sin_casos_especiales_no_hay_notas_de_reglas():
    lote = _lote("Carnes", 4)
    assert cad._notas_de_reglas([(lote, cad.clasificar("Carnes", 4))]) == []


@pytest.mark.parametrize(
    "producto, subcategoria, nombrado",
    [
        ("Queso panela 400 g", "Quesos", True),
        ("Queso Oaxaca 400 g", "Quesos", True),
        ("Queso manchego rebanado 200 g", "Quesos", False),
        ("Crema ácida 450 ml", "Cremas", True),
        ("Huevo blanco 12 piezas", "Huevo", False),
        ("Mantequilla con sal 90 g", "Mantequillas", False),
        ("Leche de almendra 946 ml", "Bebidas vegetales", False),
    ],
)
def test_que_nombra_la_lista_de_52(producto, subcategoria, nombrado):
    assert cad._nombrado_en_52(producto, subcategoria) is nombrado


def test_solo_rebajas_deja_las_cifras_de_las_dos_acciones(con):
    r = _llamar(con, categoria="lacteos", accion="rebajar")
    assert r.n_filas == 5
    assert {f["accion"] for f in r.filas} == {"rebajar"}
    assert (r.cifras["lotes_a_retirar"], r.cifras["lotes_a_rebajar"]) == ("2", "5")


def test_sin_lotes_en_la_ventana_da_sin_resultados_con_las_cifras_en_cero(con):
    r = _llamar(con, categoria="lacteos", tiendas=["valle norte"], ventana_dias=0)
    assert r.estado == "sin_resultados"
    assert r.filas == []
    assert r.cifras == {"lotes": "0", "lotes_a_retirar": "0", "lotes_a_rebajar": "0"}


# ------------------------------------------------------------------ la fila de 60 % y el día de retiro en la definición

def test_la_definicion_nombra_la_fila_de_60_y_no_le_atribuye_a_6_1_que_el_dia_de_retiro_no_se_rebaja(con):
    """«Mañana le queda 1 día: ¿lo bajo al 60 %?» sobre un lote a 2 días (2026-11-05).

    §6.1 registra como Caducidad, «sin excepción», lo que llega a su fecha de retiro; no dice que ese día no se rebaje.
    Que el retiro mande sobre la fila de 1 día de §6 es criterio propio, y la definición lo dice así aunque ningún lote
    esté en su día de retiro, que es cuando sale la nota del choque.
    """
    r = _llamar(con, tiendas=["Cumbres"], producto="pastel de chocolate")
    (fila,) = r.filas
    assert (fila["sku"], fila["dias"], fila["accion"], fila["descuento"]) == ("SKU-1016", "2", "rebajar", "50%")
    assert not any("criterio propio" in nota for nota in r.notas)

    assert "no se rebaja (§6.1)" not in r.definicion
    assert "1 día 60%" in r.definicion
    assert "solo perecederos no lácteos" in r.definicion
    assert "criterio propio" in r.definicion
    assert "no dice cuál prevalece" in r.definicion
    assert "«sin excepción»" in r.definicion


def test_la_definicion_deja_a_6_1_lo_que_si_dice_de_los_lacteos(con):
    r = _llamar(con, categoria="lacteos")
    assert "los lácteos salen del módulo de rebaja a 2 días (§6.1)" in r.definicion


# ------------------------------------------------------------------ cómo se presentan retiros y rebajas

def _indicacion(resultado) -> str:
    return resultado.detalle.get("antes_de_contestar", "")


def test_con_retiros_y_rebajas_pide_dos_tablas_y_en_la_de_rebajas_la_fecha_de_retiro(con):
    r = _llamar(con)
    assert {f["accion"] for f in r.filas} == {"retirar", "rebajar"}
    indicacion = _indicacion(r)
    assert "tablas separadas" in indicacion
    assert "fecha_retiro" in indicacion
    assert "si no se vende" in indicacion
    assert "«Acción hoy»" in indicacion and "«Rebajar 50%»" in indicacion
    assert "«Retiro si no se vende»" in indicacion
    pastel = next(f for f in r.filas if f["producto"] == "Pastel de chocolate rebanada")
    assert (pastel["accion"], pastel["descuento"], pastel["fecha_retiro"]) == ("rebajar", "50%", "2026-09-02")
    assert "antes_de_contestar" in json.loads(r.texto_modelo())["detalle"]


def test_solo_rebajas_pide_la_fecha_de_retiro_sin_hablar_de_dos_tablas(con):
    r = _llamar(con, categoria="lacteos", accion="rebajar")
    indicacion = _indicacion(r)
    assert "fecha_retiro" in indicacion
    assert "tablas separadas" not in indicacion


def test_solo_retiros_no_trae_la_indicacion_de_las_rebajas(con):
    r = _llamar(con, categoria="lacteos", accion="retirar")
    assert "antes_de_contestar" not in r.detalle
