"""Herramienta inventario: existencia al corte, reorden, valuación y días de cobertura (T2.4).

Acepta: P04 da 17 casos; P27 da 2,194,861.95 a costo y 2,906,299.50 a precio; P57 da de 12.70 a 16.66
días y P12 de 17.72 a 24.69, sin la cifra; P62 da 145 piezas con la fecha del corte; P39 da 314 piezas
sin fecha de caducidad. Las cifras de la rúbrica viven aquí y no en el código (G2). Corre sin red ni
llaves sobre la base que construye tests/conftest.py en un directorio temporal; el modelo, cuando hace
falta, es FunctionModel de Pydantic AI.

La cobertura sigue D20-A (centro: venta diaria de agosto entre 31 días) y D23-D (rango de 8 de cada
10 escenarios con las varianzas muestrales de junio, julio y agosto ponderadas 15/25/60 y z = 1.28).
La salida trae los dos extremos y nunca la cifra de D20-A (14.54 en P57 y 20.91 en P12), ni la venta
diaria con la que el modelo la podría calcular.
"""

import calendar
import csv
import datetime as dt
import json
import math
import statistics

import duckdb
import pytest
import yaml
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agente import construir, definiciones, verificacion
from agente.herramientas import comun
from agente.herramientas import inventario as inv
from agente.herramientas.caducidades import caducidades
from agente.herramientas.comun import EntradaCaducidades, EntradaInventario

CORTE = "2026-09-01"


@pytest.fixture(scope="module")
def conexion(base_construida):
    con = duckdb.connect(str(base_construida.base), read_only=True)
    yield con
    con.close()


@pytest.fixture
def con(conexion):
    """Un cursor por prueba, como Consultas.sesion() da uno por llamada de herramienta."""
    cursor = conexion.cursor()
    yield cursor
    cursor.close()


def _llamar(con, s=None, **argumentos) -> comun.Resultado:
    return inv.inventario(con, EntradaInventario(**argumentos), s)


def _modelo(resultado: comun.Resultado) -> dict:
    return json.loads(resultado.texto_modelo())


def _supuestos_con(tmp_path, cambiar) -> definiciones.Supuestos:
    """Una copia de config/supuestos.yaml con un cambio, cargada con la validación de definiciones.py."""
    with open(definiciones.CONFIG / "supuestos.yaml", encoding="utf-8") as archivo:
        contenido = yaml.safe_load(archivo)
    cambiar(contenido)
    ruta = tmp_path / "supuestos.yaml"
    ruta.write_text(yaml.safe_dump(contenido, allow_unicode=True), encoding="utf-8")
    return definiciones.cargar(ruta)


# ------------------------------------------------------------------ P04: bajo el punto de reorden

def test_P04_da_17_casos_producto_tienda_con_el_corte(con):
    r = _llamar(con, metrica="bajo_reorden")
    assert r.estado == "ok"
    assert r.cifras["casos"] == "17"
    assert r.n_filas == 17
    assert r.corte == dt.date(2026, 9, 1)
    assert "1 de septiembre de 2026" in r.definicion


def test_P04_reparte_los_casos_por_tienda(con):
    r = _llamar(con, metrica="bajo_reorden")
    assert r.cifras["T01 Tienda Valle Norte"] == "3"
    assert r.cifras["T02 Tienda Cumbres"] == "3"
    assert r.cifras["T03 Tienda Anáhuac"] == "5"
    assert r.cifras["T04 Tienda Lomas del Sur"] == "6"


def test_P04_lista_los_17_sin_pedir_limite_con_existencia_y_punto(con):
    r = _llamar(con, metrica="bajo_reorden")
    assert len(r.filas) == 17
    cebolla = [f for f in r.filas if f["producto"] == "Cebolla blanca kg" and f["tienda"].startswith("T02")]
    assert cebolla == [{"tienda": "T02 Tienda Cumbres", "sku": cebolla[0]["sku"], "producto": "Cebolla blanca kg",
                        "existencia": "53", "punto_reorden": "138", "unidad": "kg"}]
    for fila in r.filas:
        assert int(fila["existencia"].replace(",", "")) < int(fila["punto_reorden"].replace(",", ""))


def test_P04_no_cuenta_los_que_estan_1_unidad_arriba_del_punto(con):
    r = _llamar(con, metrica="bajo_reorden", tiendas=["cumbres"])
    assert r.cifras["casos"] == "3"
    assert not [f for f in r.filas if f["producto"] == "Huevo blanco 12 piezas"]


def test_P04_con_limite_explicito_muestra_menos_y_lo_dice(con):
    r = _llamar(con, metrica="bajo_reorden", limite=5)
    assert len(r.filas) == 5
    assert r.n_filas == 17
    assert r.cifras["casos"] == "17"
    assert any("5 de 17" in nota for nota in r.notas)


def test_bajo_reorden_agrupado_por_categoria_cuenta_por_categoria(con):
    r = _llamar(con, metrica="bajo_reorden", agrupar_por="categoria")
    por_categoria = {clave: valor for clave, valor in r.cifras.items() if clave != "casos"}
    assert sum(int(valor) for valor in por_categoria.values()) == 17
    assert not any(clave.startswith("T0") for clave in por_categoria)


def test_bajo_reorden_sin_casos_es_cero_y_no_un_error(con):
    r = _llamar(con, metrica="bajo_reorden", producto="pan de caja")
    assert r.estado == "ok"
    assert r.cifras["casos"] == "0"
    assert r.filas == []


@pytest.fixture(scope="module")
def base_bolillo_en_el_punto(tmp_path_factory):
    """Copia de datos/ con el Bolillo de T01 en 260, justo en su punto de reorden (variante de la copia C)."""
    carpeta = tmp_path_factory.mktemp("bolillo")
    datos = carpeta / "datos"
    datos.mkdir()
    for archivo in construir.DIR_DATOS.iterdir():
        if archivo.is_file():
            (datos / archivo.name).write_bytes(archivo.read_bytes())
    ruta = datos / "inventario.csv"
    with open(ruta, encoding="utf-8", newline="") as f:
        filas = list(csv.reader(f))
    cambiadas = 0
    for fila in filas:
        if fila[:2] == ["T01", "SKU-1013"]:
            assert fila[3] == "260"
            fila[2] = "260"
            cambiadas += 1
    assert cambiadas == 1
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(filas)
    resumen = construir.construir(datos, carpeta / "tienda.duckdb", politicas=False, indice_texto=False)
    con = duckdb.connect(str(resumen.base), read_only=True)
    yield con
    con.close()


def test_el_operador_de_reorden_sale_de_supuestos(base_bolillo_en_el_punto, tmp_path):
    estricto = _llamar(base_bolillo_en_el_punto.cursor(), metrica="bajo_reorden")
    con_igual = _supuestos_con(tmp_path, lambda c: c["inventario"].update(operador_reorden="<="))
    inclusivo = _llamar(base_bolillo_en_el_punto.cursor(), con_igual, metrica="bajo_reorden")
    assert estricto.cifras["casos"] == "16"
    assert inclusivo.cifras["casos"] == "17"
    assert "<=" not in estricto.definicion
    assert "o igual" in inclusivo.definicion


# ------------------------------------------------------------------ P27: valuación

def test_P27_valuacion_a_costo_y_a_precio(con):
    r = _llamar(con, metrica="valuacion")
    assert r.estado == "ok"
    assert r.cifras == {"a_costo": "2,194,861.95", "a_precio": "2,906,299.50"}
    assert r.unidad == "MXN"
    assert r.corte == dt.date(2026, 9, 1)
    assert "costo" in r.definicion and "precio de lista" in r.definicion


def test_la_valuacion_por_tienda_suma_el_total(con):
    total = _llamar(con, metrica="valuacion")
    por_tienda = _llamar(con, metrica="valuacion", agrupar_por="tienda")
    assert len(por_tienda.filas) == 4
    suma = sum(float(f["a_costo"].replace(",", "")) for f in por_tienda.filas)
    assert f"{suma:,.2f}" == total.cifras["a_costo"]
    assert por_tienda.cifras == total.cifras


def test_la_valuacion_dice_que_el_costo_no_tiene_fecha(con):
    r = _llamar(con, metrica="valuacion")
    assert "sin fecha" in r.definicion


# ------------------------------------------------------------------ P57 y P12: días de cobertura

CIFRAS_PROHIBIDAS = {
    # La cifra de D20-A y la venta diaria con la que se calcula: el modelo no debe tenerlas.
    "P57": ("14.54", "14.5", "46.06", "46.1", "14.36"),
    "P12": ("20.91", "20.9", "21.52", "21.5", "17.65"),
}


@pytest.mark.parametrize(
    "pregunta, producto, tienda, desde, hasta",
    [
        pytest.param("P57", "refresco de cola de 600", "valle norte", "12.70", "16.66", id="P57"),
        pytest.param("P12", "leche deslactosada", "valle norte", "17.72", "24.69", id="P12"),
    ],
)
def test_la_cobertura_da_los_dos_extremos_y_nunca_la_cifra(con, pregunta, producto, tienda, desde, hasta):
    r = _llamar(con, metrica="cobertura", producto=producto, tiendas=[tienda])
    assert r.estado == "ok"
    assert r.cifras["dias_desde"] == desde
    assert r.cifras["dias_hasta"] == hasta
    assert r.unidad == "días"
    assert r.resumen == f"8 de cada 10 veces alcanzaría entre {desde} y {hasta} días"
    for texto in (r.texto_modelo(), r.texto_registro()):
        for prohibida in CIFRAS_PROHIBIDAS[pregunta]:
            assert prohibida not in texto, f"{prohibida} aparece en {texto}"


def test_P57_trae_la_existencia_del_corte_y_declara_el_metodo(con):
    r = _llamar(con, metrica="cobertura", producto="refresco de cola de 600", tiendas=["valle norte"])
    assert r.cifras["existencia"] == "670"
    assert r.corte == dt.date(2026, 9, 1)
    for parte in ("agosto", "31 días", "1.28", "junio 15%", "julio 25%", "agosto 60%", "varianzas muestrales",
                  "devoluciones descontadas", "8 de cada 10"):
        assert parte in r.definicion, parte


def test_P12_dice_que_el_lote_caduca_antes_de_que_se_acabe_el_rango(con):
    r = _llamar(con, metrica="cobertura", producto="leche deslactosada", tiendas=["valle norte"])
    assert r.cifras["caducidad_lote"] == "2026-09-12"
    assert "el lote más próximo caduca antes del fin del rango" in r.notas
    assert "caducidad" in r.definicion


NOTA_DE_CADUCIDAD = "el lote más próximo caduca antes del fin del rango"


def _notas_de_retiro(r: comun.Resultado) -> list[str]:
    return [nota for nota in r.notas if "retiro" in nota]


def test_P12_la_nota_da_el_retiro_del_10_de_septiembre_al_modelo(con):
    r = _llamar(con, metrica="cobertura", producto="leche deslactosada", tiendas=["valle norte"])
    assert _notas_de_retiro(r) == ["retiro el 10 de septiembre"]
    assert "retiro el 10 de septiembre" in _modelo(r)["notas"]
    assert NOTA_DE_CADUCIDAD in _modelo(r)["notas"]


@pytest.mark.parametrize(
    "sku, tienda, dia",
    [
        pytest.param("SKU-1002", "T01", 10, id="lacteo-dos-dias-antes"),
        pytest.param("SKU-1023", "T01", 6, id="carne-un-dia-antes"),
    ],
)
def test_el_retiro_de_la_nota_es_el_de_la_herramienta_de_caducidades(con, sku, tienda, dia):
    lote = caducidades(con, EntradaCaducidades(sku=sku, tiendas=[tienda])).filas[0]
    assert lote["fecha_retiro"] == dt.date(2026, 9, dia).isoformat()
    r = _llamar(con, metrica="cobertura", sku=sku, tiendas=[tienda])
    assert _notas_de_retiro(r) == [f"retiro el {dia} de septiembre"]


def test_el_retiro_se_avisa_aunque_la_caducidad_quede_despues_del_rango(con):
    # Leche light en Cumbres: el rango llega al 12, el retiro es el 11 y la caducidad el 13.
    r = _llamar(con, metrica="cobertura", sku="SKU-1003", tiendas=["T02"])
    assert r.cifras["caducidad_lote"] == "2026-09-13"
    assert NOTA_DE_CADUCIDAD not in r.notas
    assert _notas_de_retiro(r) == ["retiro el 11 de septiembre"]


def test_un_retiro_despues_del_rango_no_lleva_nota(con):
    # Mantequilla en Valle Norte: se acaba hacia el 11 y se retira el 21.
    r = _llamar(con, metrica="cobertura", sku="SKU-1010", tiendas=["T01"])
    assert r.cifras["caducidad_lote"] == "2026-09-23"
    assert _notas_de_retiro(r) == []


def test_sin_fecha_de_caducidad_no_hay_retiro_que_avisar(con):
    r = _llamar(con, metrica="cobertura", producto="refresco de cola de 600", tiendas=["valle norte"])
    assert "caducidad_lote" not in r.cifras
    assert _notas_de_retiro(r) == []


def test_el_verificador_acepta_el_retiro_de_la_nota_y_no_otra_fecha(con):
    r = _llamar(con, metrica="cobertura", producto="leche deslactosada", tiendas=["valle norte"])
    fuentes = verificacion.fuentes_de("cobertura de la leche", [r])

    def revisar(texto: str) -> list[str]:
        return [d.tipo for d in verificacion.verificar(texto, fuentes, lambda: pytest.fail("no trae citas")).discrepancias]

    assert revisar("Nota: retiro el 10 de septiembre.") == []
    assert revisar("Nota: retiro el 8 de septiembre.") == ["fecha"]


def test_la_cobertura_da_lo_mismo_en_cada_llamada(conexion):
    textos = set()
    for _ in range(3):
        cursor = conexion.cursor()
        textos.add(_llamar(cursor, metrica="cobertura", producto="refresco de cola de 600",
                           tiendas=["valle norte"]).texto_modelo())
        cursor.close()
    assert len(textos) == 1


def test_el_centro_de_d20_a_queda_dentro_del_rango_en_las_480_filas(con):
    s = definiciones.supuestos()
    coberturas = inv.coberturas(con, s)
    assert len(coberturas) == 480
    anchos = []
    for c in coberturas:
        cifra = c.existencia / c.media
        assert c.desde < cifra < c.hasta, c
        anchos.append(c.hasta - c.desde)
    # D23 reporta un ancho mediano de 5.45 días; con este método da 5.44.
    assert statistics.median(anchos) == pytest.approx(5.45, abs=0.02)


def test_la_cobertura_usa_la_media_de_agosto_de_definiciones(con):
    s = definiciones.supuestos()
    refresco = next(c for c in inv.coberturas(con, s) if (c.tienda_id, c.sku) == ("T01", "SKU-1078"))
    assert refresco.media == pytest.approx(s.media_diaria_base(1428))
    assert refresco.existencia == 670


def test_los_extremos_resuelven_la_formula():
    existencia, media, desviacion, z = 670, 46.06, 18.65, 1.28
    desde, hasta = inv.extremos_cobertura(existencia, media, desviacion, z)
    assert media * desde + z * desviacion * math.sqrt(desde) == pytest.approx(existencia)
    assert media * hasta - z * desviacion * math.sqrt(hasta) == pytest.approx(existencia)
    assert desde < existencia / media < hasta


def test_sin_variacion_los_extremos_coinciden_y_sin_existencia_son_cero():
    assert inv.extremos_cobertura(100, 10, 0, 1.28) == pytest.approx((10, 10))
    assert inv.extremos_cobertura(0, 10, 3, 1.28) == (0, 0)


def test_sin_venta_en_el_mes_base_la_cobertura_no_se_inventa():
    with pytest.raises(comun.ErrorHerramienta) as error:
        inv.extremos_cobertura(100, 0, 3, 1.28)
    assert error.value.estado == "division_entre_cero"


def test_los_pesos_y_la_z_salen_de_supuestos(con, tmp_path):
    base = _llamar(con, metrica="cobertura", producto="refresco de cola de 600", tiendas=["valle norte"])
    solo_agosto = _supuestos_con(tmp_path, lambda c: c["cobertura"].update(pesos_varianza={8: 1.0}))
    otra_z = _supuestos_con(tmp_path, lambda c: c["cobertura"].update(z=1.645))
    r_agosto = _llamar(con, solo_agosto, metrica="cobertura", producto="refresco de cola de 600", tiendas=["valle norte"])
    r_z = _llamar(con, otra_z, metrica="cobertura", producto="refresco de cola de 600", tiendas=["valle norte"])
    assert r_agosto.cifras["dias_desde"] != base.cifras["dias_desde"]
    assert "agosto 100%" in r_agosto.definicion
    assert float(r_z.cifras["dias_desde"]) < float(base.cifras["dias_desde"])
    assert float(r_z.cifras["dias_hasta"]) > float(base.cifras["dias_hasta"])
    assert "1.645" in r_z.definicion


def test_la_leche_entera_suma_la_venta_del_sku_que_sustituye(con, tmp_path):
    con_sustituto = _llamar(con, metrica="cobertura", sku="SKU-1121", tiendas=["valle norte"])
    sin_sumar = _supuestos_con(tmp_path, lambda c: c["ventas"].update(sumar_sustitutos=False))
    solo_sku = _llamar(con, sin_sumar, metrica="cobertura", sku="SKU-1121", tiendas=["valle norte"])
    assert con_sustituto.estado == solo_sku.estado == "ok"
    assert con_sustituto.cifras != solo_sku.cifras
    assert any("SKU-1001" in nota for nota in con_sustituto.notas)


def test_varias_filas_de_cobertura_van_de_la_que_se_acaba_antes(con):
    r = _llamar(con, metrica="cobertura", tiendas=["valle norte"], orden="menor", limite=5)
    assert r.n_filas == 120
    assert len(r.filas) == 5
    desde = [float(f["dias_desde"]) for f in r.filas]
    assert desde == sorted(desde)
    for fila in r.filas:
        assert set(fila) >= {"tienda", "sku", "producto", "existencia", "dias_desde", "dias_hasta"}


def test_en_el_agente_la_herramienta_devuelve_los_extremos_y_no_la_cifra(con):
    recibido = []

    def modelo(mensajes, info: AgentInfo) -> ModelResponse:
        if len(mensajes) == 1:
            argumentos = {"entrada": {"metrica": "cobertura", "producto": "refresco de cola de 600",
                                      "tiendas": ["valle norte"]}}
            return ModelResponse(parts=[ToolCallPart("inventario", argumentos)])
        recibido.extend(p for p in mensajes[-1].parts if isinstance(p, ToolReturnPart))
        return ModelResponse(parts=[TextPart("listo")])

    agente = Agent(FunctionModel(modelo))

    @agente.tool_plain
    def inventario(entrada: EntradaInventario) -> str:
        return inv.inventario(con, entrada).texto_modelo()

    agente.run_sync("pa cuantos dias me alcanza el refresco de cola de 600 en valle norte?")
    assert len(recibido) == 1
    texto = recibido[0].model_response_str()
    assert "12.70" in texto and "16.66" in texto
    assert "14.54" not in texto and "46.06" not in texto


# ------------------------------------------------------------------ P62 y P39: existencia al corte

def test_P62_bolillos_de_cumbres_con_la_fecha_del_corte(con):
    r = _llamar(con, metrica="existencia", producto="bolillos", tiendas=["cumbres"])
    assert r.estado == "ok"
    assert r.cifras["existencia"] == "145"
    assert r.unidad == "piezas"
    assert r.corte == dt.date(2026, 9, 1)
    assert _modelo(r)["corte"] == CORTE
    assert "1 de septiembre de 2026" in r.definicion
    assert "hoy no se conoce" in r.definicion


def test_P62_dice_que_esta_debajo_de_su_punto_de_reorden(con):
    r = _llamar(con, metrica="existencia", producto="bolillos", tiendas=["cumbres"])
    assert r.cifras["punto_reorden"] == "253"
    assert any("debajo de su punto de reorden" in nota for nota in r.notas)


def test_P39_pan_de_caja_en_cumbre_sin_fecha_de_caducidad(con):
    r = _llamar(con, metrica="existencia", producto="pan de caja", tiendas=["cumbre"])
    assert r.estado == "ok"
    assert r.cifras["existencia"] == "314"
    assert r.unidad == "piezas"
    assert "caducidad_lote" not in r.cifras
    assert any("no tiene fecha de caducidad registrada" in nota and "no perecedero" in nota for nota in r.notas)
    assert "'cumbre' se interpretó como Tienda Cumbres (T02)" in r.notas
    texto = r.texto_modelo()
    for fecha in ("2026-09-1", "2026-09-2", "10 días", "vida"):
        assert fecha not in texto


def test_un_perecedero_trae_la_caducidad_del_lote(con):
    r = _llamar(con, metrica="existencia", producto="bolillos", tiendas=["cumbres"])
    assert r.cifras["caducidad_lote"] == "2026-09-08"


def test_la_existencia_no_suma_kilos_con_piezas(con):
    r = _llamar(con, metrica="existencia", categoria="frutas y verduras", tiendas=["valle norte"])
    assert r.estado == "ok"
    assert set(r.cifras) == {"existencia_kg", "existencia_piezas"}
    assert "existencia" not in r.cifras
    assert "no se suman" in r.unidad


def test_la_existencia_agrupada_por_categoria_separa_las_unidades(con):
    r = _llamar(con, metrica="existencia", agrupar_por="categoria", tiendas=["valle norte"], limite=50)
    frutas = [f for f in r.filas if f["categoria"] == "Frutas y verduras"]
    assert {f["unidad"] for f in frutas} == {"kg", "piezas"}


def test_la_existencia_por_sku_explicito(con):
    r = _llamar(con, metrica="existencia", sku="SKU-1013", tiendas=["T02"])
    assert r.cifras["existencia"] == "145"


# ------------------------------------------------------------------ errores con tipo

@pytest.mark.parametrize("metrica", ["merma_real", "inventario_historico"])
def test_las_metricas_no_calculables_dan_el_error_con_tipo(con, metrica):
    r = _llamar(con, metrica=metrica)
    assert r.estado == "metrica_no_calculable"
    assert r.cifras == {} and r.filas == []


def test_una_tienda_que_no_existe_no_se_cambia_por_otra(con):
    r = _llamar(con, metrica="existencia", producto="bolillos", tiendas=["guadalupe"])
    assert r.estado == "entidad_inexistente"
    assert r.cifras == {}


def test_una_ciudad_con_dos_tiendas_es_ambigua(con):
    r = _llamar(con, metrica="cobertura", producto="leche deslactosada", tiendas=["monterrey"])
    assert r.estado == "entidad_ambigua"
    assert len(r.detalle["candidatos"]) == 2


def test_un_sku_que_no_esta_en_el_catalogo_es_inexistente(con):
    r = _llamar(con, metrica="existencia", sku="SKU-9999")
    assert r.estado == "entidad_inexistente"


def test_un_producto_descontinuado_lleva_a_su_sustituto(con):
    r = _llamar(con, metrica="existencia", sku="SKU-1001", tiendas=["T01"])
    assert r.estado == "ok"
    assert any("SKU-1121" in nota for nota in r.notas)


# ------------------------------------------------------------------ salida: peso, consulta y parámetros

@pytest.mark.parametrize(
    "argumentos",
    [
        pytest.param({"metrica": "existencia", "producto": "bolillos", "tiendas": ["cumbres"]}, id="P62"),
        pytest.param({"metrica": "existencia", "producto": "pan de caja", "tiendas": ["cumbre"]}, id="P39"),
        pytest.param({"metrica": "valuacion"}, id="P27"),
    ],
)
def test_el_resultado_de_una_cifra_pesa_300_tokens_o_menos(con, argumentos):
    texto = _llamar(con, **argumentos).texto_modelo()
    tokens = comun.estimar_tokens(texto)
    assert tokens <= 300, f"{tokens} tokens: {texto}"


@pytest.mark.parametrize(
    "argumentos",
    [
        pytest.param({"metrica": "cobertura", "producto": "refresco de cola de 600", "tiendas": ["valle norte"]}, id="P57"),
        pytest.param({"metrica": "cobertura", "producto": "leche deslactosada", "tiendas": ["valle norte"]}, id="P12"),
    ],
)
def test_el_rango_de_cobertura_pesa_400_tokens_o_menos(con, argumentos):
    # No es una cifra simple: trae dos extremos, la existencia, la caducidad del lote y el método que
    # C1.4 n4 pide declarar sin que se le pida (unos 120 tokens estimados de los 400).
    texto = _llamar(con, **argumentos).texto_modelo()
    tokens = comun.estimar_tokens(texto)
    assert tokens <= 400, f"{tokens} tokens: {texto}"


def test_el_texto_del_usuario_no_entra_a_la_consulta_y_la_consulta_va_solo_al_registro(con):
    r = _llamar(con, metrica="cobertura", producto="refresco de cola de 600", tiendas=["valle norte"])
    assert r.consulta
    assert "valle" not in r.consulta.lower() and "refresco" not in r.consulta.lower()
    assert "T01" in r.consulta and "SKU-1078" in r.consulta
    assert "consulta" not in _modelo(r)
    assert json.loads(r.texto_registro())["consulta"] == r.consulta


def test_la_consulta_del_registro_reproduce_la_cifra(con):
    r = _llamar(con, metrica="valuacion")
    sql, _, parametros = r.consulta.partition("\n-- parámetros: ")
    filas = con.execute(sql, json.loads(parametros)).fetchall()
    assert f"{filas[0][0]:,.2f}" == r.cifras["a_costo"]
    assert f"{filas[0][1]:,.2f}" == r.cifras["a_precio"]


def test_los_filtros_dicen_lo_que_se_consulto(con):
    r = _llamar(con, metrica="existencia", producto="bolillos", tiendas=["cumbres"])
    assert r.filtros["tiendas"] == ["T02"]
    assert r.filtros["skus"] == ["SKU-1013"]
    assert r.filtros["metrica"] == "existencia"
    assert r.fuente.startswith("inventario.csv")


def test_el_mes_base_tiene_31_dias():
    assert calendar.monthrange(2026, definiciones.supuestos().cobertura.mes_base)[1] == 31
