"""Resolvedor de nombres coloquiales, errores de dedo y fechas relativas (T2.8, C4.3 y C3.1).

Acepta: P02, P04, P06, P08, P12, P14, P38, P39 y P40 se entienden sin pedir que se reformulen, y la
respuesta dice cómo interpretó "cumbre". Corre sin red ni llaves, sobre la base que construye
tests/conftest.py en un directorio temporal. Los textos son los de la batería de la rúbrica, con sus
errores de escritura. Las cifras de venta de la rúbrica las prueba T2.3; aquí se prueba a qué tienda,
producto, canal y periodo llega cada texto, y que lo dudoso quede dicho en las notas (G2: ninguna
respuesta de la rúbrica vive en el código del resolvedor).

El umbral se prueba por sus dos lados: los errores de dedo de una tienda se resuelven y los nombres
parecidos que no existen (P21) dan entidad_inexistente en lugar de otra tienda.
"""

import csv
import datetime as dt
import json
import unicodedata
from pathlib import Path

import duckdb
import pytest

from agente import definiciones, resolver
from agente.herramientas import comun, errores

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "datos"

# La fecha de la rúbrica: hoy es 30 de octubre y los datos terminan el 31 de agosto. El resolvedor
# toma como hoy el 1 de septiembre (D3-A) y avisa que esta fecha real no tiene datos.
HOY_REAL = dt.date(2026, 10, 30)
AVISO = definiciones.aviso_fecha_relativa(HOY_REAL)

TODAS = ["T01", "T02", "T03", "T04"]


def _leer_csv(archivo: str) -> list[dict[str, str]]:
    with open(DATOS / archivo, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


CATALOGO = _leer_csv("catalogo_productos.csv")
TIENDAS = {fila["tienda_id"]: fila for fila in _leer_csv("tiendas.csv")}
ETIQUETAS_TIENDAS = [f"{id_} {t['nombre']} ({t['ciudad']})" for id_, t in TIENDAS.items()]
CATEGORIAS = sorted({fila["categoria"] for fila in CATALOGO})


def _sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _skus(**filtro: str) -> list[str]:
    """SKU del catálogo de datos/ con las columnas pedidas, para no escribirlos a mano cuando son muchos."""
    return sorted(f["sku"] for f in CATALOGO if all(f[campo] == valor for campo, valor in filtro.items()))


@pytest.fixture(scope="module")
def catalogos(base_construida):
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        return resolver.cargar_catalogos(con)


def _catalogos_en_memoria(productos=(), alias=(), tiendas=None):
    """Catálogos de una base mínima en memoria: las tres tablas que lee el resolvedor y nada más."""
    con = duckdb.connect()
    con.execute("CREATE TABLE tiendas (tienda_id VARCHAR, nombre VARCHAR, ciudad VARCHAR, formato VARCHAR)")
    con.execute("CREATE TABLE alias (tipo VARCHAR, alias VARCHAR, valor VARCHAR)")
    con.execute(
        "CREATE TABLE catalogo (sku VARCHAR, nombre VARCHAR, categoria VARCHAR, subcategoria VARCHAR, "
        "fecha_baja DATE, sku_sustituto VARCHAR)"
    )
    tiendas = tiendas if tiendas is not None else [(i, t["nombre"], t["ciudad"], t["formato"]) for i, t in TIENDAS.items()]
    # DuckDB 2.0 rechaza executemany con una lista vacía.
    for sql, filas in (
        ("INSERT INTO tiendas VALUES (?, ?, ?, ?)", list(tiendas)),
        ("INSERT INTO alias VALUES (?, ?, ?)", list(alias)),
        ("INSERT INTO catalogo VALUES (?, ?, ?, ?, ?, ?)", list(productos)),
    ):
        if filas:
            con.executemany(sql, filas)
    try:
        return resolver.cargar_catalogos(con)
    finally:
        con.close()


# ------------------------------------------------------------------ criterio de aceptación: las nueve preguntas

def test_P02_los_kilos_de_pollo_son_las_dos_presentaciones_por_kilo_y_no_los_nuggets_ni_el_consome(catalogos):
    esperados = ["SKU-1023", "SKU-1024"]
    for texto in ("pollo", "kilos de pollo", "kilos d pollo"):
        assert resolver.resolver_productos(texto, catalogos).skus == esperados, texto
    notas = " ".join(resolver.resolver_productos("pollo", catalogos).notas)
    assert "Nuggets de pollo" in notas and "Consomé de pollo" in notas, "dice qué productos con pollo deja fuera"


def test_P04_y_P08_no_nombran_tienda_producto_canal_ni_periodo_y_no_piden_aclarar(catalogos):
    assert resolver.resolver_tiendas([], catalogos).ids == TODAS
    assert resolver.resolver_tiendas(None, catalogos).ids == TODAS
    assert resolver.resolver_productos(None, catalogos) is None
    assert resolver.resolver_categoria(None, catalogos) is None
    assert resolver.resolver_canal(None, catalogos) is None
    periodo = resolver.resolver_periodo(None, fuente="ventas")
    assert (periodo.desde, periodo.hasta, periodo.notas) == (dt.date(2026, 6, 1), dt.date(2026, 8, 31), [])


@pytest.mark.parametrize("texto", ["yogurt", "el yogurt del refri", "yogurt del refrigerador", "yogur"])
def test_P06_el_yogurt_del_refri_es_la_subcategoria_yogur(catalogos, texto):
    resuelto = resolver.resolver_productos(texto, catalogos)
    assert resuelto.skus == _skus(subcategoria="Yogur") == ["SKU-1004", "SKU-1005"]
    assert resuelto.por_grupo
    assert "subcategoría Yogur" in " ".join(resuelto.notas)


def test_P12_la_leche_deslactosada_de_valle_norte_es_un_producto_y_una_tienda(catalogos):
    assert resolver.resolver_productos("leche deslactosada", catalogos).skus == ["SKU-1002"]
    tiendas = resolver.resolver_tiendas(["valle norte"], catalogos)
    assert (tiendas.ids, tiendas.notas) == (["T01"], [])


def test_P14_valle_norte_es_la_tienda_T01_sin_pedir_aclarar(catalogos):
    tiendas = resolver.resolver_tiendas(["valle norte"], catalogos)
    assert (tiendas.ids, tiendas.notas) == (["T01"], [])


@pytest.mark.parametrize(
    "texto", ["lechhe entera", "lechhe entera, la de litro", "leche entera la de litro", "leche entera de un litro"]
)
def test_P38_la_leche_entera_de_litro_con_errores_de_dedo_suma_las_dos_presentaciones(catalogos, texto):
    resuelto = resolver.resolver_productos(texto, catalogos)
    assert resuelto.skus == ["SKU-1001", "SKU-1121"]
    assert not resuelto.por_grupo


def test_P38_jullio_es_julio_y_lo_dice(catalogos):
    periodo = resolver.resolver_periodo("jullio", fuente="ventas")
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    assert periodo.notas == ["'jullio' se interpretó como julio de 2026"]


def test_P39_cumbre_es_la_tienda_T02_y_la_respuesta_dice_como_lo_interpreto(catalogos):
    tiendas = resolver.resolver_tiendas(["cumbre"], catalogos)
    assert tiendas.ids == ["T02"]
    assert tiendas.notas == ["'cumbre' se interpretó como Tienda Cumbres (T02)"]
    assert resolver.resolver_productos("pan de caja", catalogos).skus == ["SKU-1018"]


def test_las_notas_de_cumbre_llegan_al_modelo_en_el_resultado_de_la_herramienta(catalogos):
    notas = resolver.resolver_tiendas(["cumbre"], catalogos).notas
    visto = json.loads(comun.Resultado(herramienta="inventario", notas=notas).texto_modelo())
    assert visto["notas"] == ["'cumbre' se interpretó como Tienda Cumbres (T02)"]


def test_P40_anahuac_x_internet_es_la_tienda_T03_en_el_canal_Ecommerce_que_no_tiene(catalogos):
    tiendas = resolver.resolver_tiendas(["anahuac"], catalogos)
    canal = resolver.resolver_canal("x internet", catalogos)
    assert (tiendas.ids, canal) == (["T03"], "Ecommerce")
    with pytest.raises(errores.CanalInexistente) as capturado:
        errores.verificar_canal(canal, catalogos.tiendas, tiendas.ids)
    assert capturado.value.estado == "canal_inexistente"
    assert capturado.value.detalle["tiendas_sin_canal"] == ["Tienda Anáhuac (Mi Tienda)"]


@pytest.mark.parametrize(
    "producto, tienda, skus, id_tienda",
    [
        pytest.param("refresco de cola de 600", "valle norte", ["SKU-1078"], "T01", id="P57"),
        pytest.param("bolillos", "cumbres", ["SKU-1013"], "T02", id="P62"),
        pytest.param("pechuga", "cumbres", ["SKU-1023"], "T02", id="P31_turno_2"),
        pytest.param("refresco cola 600ml", "vn", ["SKU-1078"], "T01", id="unidad_pegada_y_abreviatura"),
    ],
)
def test_otras_preguntas_de_la_bateria_con_producto_y_tienda(catalogos, producto, tienda, skus, id_tienda):
    assert resolver.resolver_productos(producto, catalogos).skus == skus
    assert resolver.resolver_tiendas([tienda], catalogos).ids == [id_tienda]


# ------------------------------------------------------------------ tiendas

@pytest.mark.parametrize(
    "texto, id_",
    [
        ("Tienda Cumbres", "T02"), ("cumbres", "T02"), ("CUMBRES", "T02"), ("Cumbres.", "T02"),
        ("Tienda Valle Norte", "T01"), ("valle norte", "T01"), ("Valle   Norte", "T01"),
        ("Tienda Anáhuac", "T03"), ("anahuac", "T03"), ("Anáhuac", "T03"),
        ("Tienda Lomas del Sur", "T04"), ("lomas del sur", "T04"),
        ("T01", "T01"), ("t02", "T02"), ("T03", "T03"), ("t04", "T04"),
        ("la tienda de cumbres", "T02"), ("sucursal valle norte", "T01"), ("las cumbres", "T02"),
        ("la valle norte", "T01"), ("T01 Tienda Valle Norte (Monterrey)", "T01"),
    ],
)
def test_el_nombre_de_una_tienda_o_su_id_se_resuelve_sin_nota(catalogos, texto, id_):
    tiendas = resolver.resolver_tiendas([texto], catalogos)
    assert (tiendas.ids, tiendas.notas) == ([id_], [])


@pytest.mark.parametrize(
    "texto, id_",
    [
        ("cumbre", "T02"), ("vn", "T01"), ("lomas", "T04"), ("saltillo", "T04"), ("san nicolas", "T03"),
        ("san nicolás de los garza", "T03"), ("lomas dl sur", "T04"), ("lomas sur", "T04"),
    ],
)
def test_un_nombre_coloquial_se_resuelve_y_dice_como_se_interpreto(catalogos, texto, id_):
    tiendas = resolver.resolver_tiendas([texto], catalogos)
    assert tiendas.ids == [id_]
    assert tiendas.notas == [f"'{texto}' se interpretó como {TIENDAS[id_]['nombre']} ({id_})"]


# Cada uno es un error de dedo de un nombre real: debe llegar a su tienda y decir que lo interpretó.
ERRORES_DE_DEDO = [
    ("cunbres", "T02"), ("cumbrez", "T02"), ("kumbres", "T02"), ("cumbress", "T02"), ("cubres", "T02"),
    ("anahuak", "T03"), ("anauac", "T03"), ("anhuac", "T03"), ("anahuacc", "T03"), ("anaguac", "T03"),
    ("valle nrote", "T01"), ("vale norte", "T01"), ("valle nore", "T01"), ("vallenorte", "T01"),
    ("valla norte", "T01"), ("valle norrte", "T01"),
    ("lomaz del sur", "T04"), ("lomas d sur", "T04"),
    ("saltilo", "T04"), ("satillo", "T04"),
    ("san nikolas", "T03"), ("san nicolaz", "T03"),
]

# Cada uno se parece a una tienda real y no lo es: debe dar entidad_inexistente, no esa tienda (P21).
PARECIDAS_QUE_NO_EXISTEN = [
    "guadalupe", "la sucursal de guadalupe", "valle oriente", "valle sur", "valle alto", "valle poniente",
    "lomas del este", "lomas del sol", "lomas del norte", "lomas verdes", "cumbres 2", "cumbres altas",
    "cumbres sur", "anahuac sur", "anahuac 2", "san nicolas de los arroyos", "san pedro", "santa catarina",
    "apodaca", "escobedo", "juarez", "garcia", "tienda centro", "monterrey norte",
]


@pytest.mark.parametrize("texto, id_", ERRORES_DE_DEDO)
def test_un_error_de_dedo_de_una_tienda_llega_a_ella_y_lo_dice(catalogos, texto, id_):
    tiendas = resolver.resolver_tiendas([texto], catalogos)
    assert tiendas.ids == [id_]
    assert tiendas.notas == [f"'{texto}' se interpretó como {TIENDAS[id_]['nombre']} ({id_})"]


@pytest.mark.parametrize("texto", PARECIDAS_QUE_NO_EXISTEN)
def test_una_tienda_que_no_existe_pero_se_parece_a_una_real_no_se_confunde_con_ella(catalogos, texto):
    with pytest.raises(errores.EntidadInexistente) as capturado:
        resolver.resolver_tiendas([texto], catalogos)
    error = capturado.value
    assert error.estado == "entidad_inexistente"
    assert error.detalle["existentes"] == ETIQUETAS_TIENDAS, "lista las 4 que sí existen"


def test_P21_guadalupe_da_el_error_con_tipo_y_lista_las_cuatro_tiendas(catalogos):
    with pytest.raises(errores.EntidadInexistente) as capturado:
        resolver.resolver_tiendas(["guadalupe"], catalogos)
    assert capturado.value.mensaje == "No hay una tienda que coincida con 'guadalupe'"
    assert capturado.value.detalle == {"entidad": "tienda", "texto": "guadalupe", "existentes": ETIQUETAS_TIENDAS}


def test_el_umbral_deja_margen_a_ambos_lados_de_las_palabras_de_las_tiendas(catalogos):
    # Lo más parecido a una palabra de tienda entre lo que no existe queda por debajo del umbral, y lo
    # menos parecido entre los errores de dedo de una palabra queda por encima, con holgura.
    import difflib

    palabras = {palabra for alias in catalogos.alias_lugares for palabra in alias if len(palabra) >= 4}
    peor_de_las_que_no_existen = max(
        difflib.SequenceMatcher(None, "guadalupe", palabra).ratio() for palabra in palabras
    )
    assert peor_de_las_que_no_existen < resolver.UMBRAL_PALABRA - 0.2
    mejores_de_los_dedos = [
        max(difflib.SequenceMatcher(None, dedo, palabra).ratio() for palabra in palabras)
        for dedo, _ in ERRORES_DE_DEDO
        if " " not in dedo and dedo != "vallenorte"
    ]
    assert min(mejores_de_los_dedos) >= resolver.UMBRAL_PALABRA


@pytest.mark.parametrize("texto", ["monterrey", "Monterrey", "la de monterrey", "mty", "las tiendas de monterrey", "monterey"])
def test_P26_monterrey_son_dos_tiendas_y_el_error_las_nombra(catalogos, texto):
    with pytest.raises(errores.EntidadAmbigua) as capturado:
        resolver.resolver_tiendas([texto], catalogos)
    error = capturado.value
    assert error.estado == "entidad_ambigua"
    assert error.detalle["candidatos"] == ETIQUETAS_TIENDAS[:2]


def test_varias_tiendas_salen_ordenadas_sin_repetir_y_con_una_nota_por_texto_dudoso(catalogos):
    tiendas = resolver.resolver_tiendas(["lomas", "cumbres", "cumbre", "valle norte"], catalogos)
    assert tiendas.ids == ["T01", "T02", "T04"]
    assert tiendas.notas == [
        "'lomas' se interpretó como Tienda Lomas del Sur (T04)",
        "'cumbre' se interpretó como Tienda Cumbres (T02)",
    ]


@pytest.mark.parametrize("texto", ["todas", "todas las tiendas", "total", "las 4 tiendas", "toda la cadena"])
def test_todas_las_tiendas_son_las_cuatro(catalogos, texto):
    assert resolver.resolver_tiendas([texto], catalogos).ids == TODAS


def test_una_tienda_inexistente_entre_varias_da_el_error_aunque_las_demas_existan(catalogos):
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_tiendas(["cumbres", "guadalupe"], catalogos)


def test_un_texto_sin_palabras_de_tienda_no_es_una_tienda(catalogos):
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_tiendas(["la tienda de la"], catalogos)


def test_el_texto_del_usuario_se_repite_limpio_en_la_nota_y_en_el_error(catalogos):
    sucio = "cumbres\n\x00" + "x" * 300
    with pytest.raises(errores.EntidadInexistente) as capturado:
        resolver.resolver_tiendas([sucio], catalogos)
    assert "\n" not in capturado.value.mensaje and "\x00" not in capturado.value.mensaje
    assert len(capturado.value.detalle["texto"]) <= errores.MAX_TEXTO


def test_un_texto_largo_se_cita_recortado_en_las_notas_y_en_los_errores(catalogos):
    # Las palabras de relleno no cambian la tienda, pero el texto completo no debe llegar a la nota.
    largo = "cumbre" + " de" * 100
    nota = resolver.resolver_tiendas([largo], catalogos).notas[0]
    assert nota.startswith("'cumbre de de") and "…' se interpretó como Tienda Cumbres (T02)" in nota
    assert len(nota) < 150
    with pytest.raises(comun.ErrorHerramienta) as capturado:
        resolver.resolver_periodo("x" * 500)
    assert capturado.value.estado == "periodo_no_entendido" and len(capturado.value.mensaje) < 150


def test_las_palabras_con_cifras_solo_se_comparan_exactas():
    # "10012" se parece 0.8 a "10011", pero un código con otra cifra es otro código y no un error de dedo.
    catalogos = _catalogos_en_memoria(alias=[("tienda", "ruta 10011", "T01")])
    assert resolver.resolver_tiendas(["ruta 10011"], catalogos).ids == ["T01"]
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_tiendas(["ruta 10012"], catalogos)


def test_el_texto_del_usuario_con_caracteres_de_control_no_pasa_a_la_nota(catalogos):
    tiendas = resolver.resolver_tiendas(["cumbre\n\x07"], catalogos)
    assert tiendas.notas == ["'cumbre' se interpretó como Tienda Cumbres (T02)"]


def test_los_alias_salen_de_la_tabla_alias_de_la_base_y_no_del_yaml():
    catalogos = _catalogos_en_memoria(alias=[("tienda", "la matriz", "T01"), ("ciudad", "regio", "T01"), ("ciudad", "regio", "T02")])
    assert resolver.resolver_tiendas(["la matriz"], catalogos).ids == ["T01"]
    with pytest.raises(errores.EntidadAmbigua):
        resolver.resolver_tiendas(["regio"], catalogos)
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_tiendas(["lomas"], catalogos)  # el YAML lo tiene como alias de T04; esta base no


def test_el_nombre_oficial_de_la_tienda_se_resuelve_aunque_no_este_en_los_alias():
    catalogos = _catalogos_en_memoria(alias=[])
    assert resolver.resolver_tiendas(["Tienda Cumbres"], catalogos).ids == ["T02"]
    assert resolver.resolver_tiendas(["t03"], catalogos).ids == ["T03"]


def test_dos_tiendas_con_el_mismo_alias_son_ambiguas_y_no_se_elige_una_en_silencio():
    catalogos = _catalogos_en_memoria(alias=[("tienda", "centro", "T01"), ("tienda", "el centro", "T03")])
    with pytest.raises(errores.EntidadAmbigua) as capturado:
        resolver.resolver_tiendas(["centro"], catalogos)
    assert capturado.value.detalle["candidatos"] == [ETIQUETAS_TIENDAS[0], ETIQUETAS_TIENDAS[2]]


# ------------------------------------------------------------------ canal

@pytest.mark.parametrize(
    "texto",
    ["Ecommerce", "ecommerce", "e-commerce", "en linea", "en línea", "internet", "por internet", "x internet",
     "online", "app", "apps", "las apps", "web", "pagina", "ecomerce", "inernet"],
)
def test_los_nombres_del_canal_en_linea_son_Ecommerce(catalogos, texto):
    assert resolver.resolver_canal(texto, catalogos) == "Ecommerce"


@pytest.mark.parametrize("texto", ["Piso", "piso", "tienda fisica", "tienda física", "mostrador", "en piso"])
def test_los_nombres_del_canal_de_piso_son_Piso(catalogos, texto):
    assert resolver.resolver_canal(texto, catalogos) == "Piso"


@pytest.mark.parametrize("texto", ["caja", "telefono", "mayoreo", "pagina de internet de la caja"])
def test_un_canal_que_no_existe_da_canal_inexistente_con_los_canales_que_si_hay(catalogos, texto):
    with pytest.raises(errores.CanalInexistente) as capturado:
        resolver.resolver_canal(texto, catalogos)
    assert capturado.value.estado == "canal_inexistente"
    assert capturado.value.detalle["canales_disponibles"] == ["Piso", "Ecommerce"]


# ------------------------------------------------------------------ categoría

@pytest.mark.parametrize(
    "texto, categoria",
    [
        ("lacteos", "Lácteos"), ("Lácteos", "Lácteos"), ("LACTEOS", "Lácteos"), ("lacteo", "Lácteos"),
        ("panaderia", "Panadería"), ("carnes", "Carnes"), ("carne", "Carnes"),
        ("frutas y verduras", "Frutas y verduras"), ("verduras y frutas", "Frutas y verduras"),
        ("abarrotes", "Abarrotes"), ("bebidas", "Bebidas"), ("limpieza", "Limpieza"),
        ("cuidado personal", "Cuidado personal"), ("lactoes", "Lácteos"), ("panadria", "Panadería"),
    ],
)
def test_la_categoria_se_resuelve_sin_acentos_en_singular_o_con_un_error_de_dedo(catalogos, texto, categoria):
    assert resolver.resolver_categoria(texto, catalogos) == categoria


@pytest.mark.parametrize("texto", ["verduras", "pollo", "ropa", "yogur"])
def test_lo_que_no_es_una_categoria_da_entidad_inexistente_con_las_ocho_categorias(catalogos, texto):
    with pytest.raises(errores.EntidadInexistente) as capturado:
        resolver.resolver_categoria(texto, catalogos)
    assert capturado.value.detalle["existentes"] == CATEGORIAS
    assert capturado.value.detalle["entidad"] == "categoria"


# ------------------------------------------------------------------ productos

@pytest.mark.parametrize("fila", CATALOGO, ids=[f["sku"] for f in CATALOGO])
def test_el_nombre_completo_de_cada_producto_lo_resuelve_sin_pedir_aclarar(catalogos, fila):
    for texto in (fila["nombre"], fila["nombre"].lower(), _sin_acentos(fila["nombre"]).upper(), fila["sku"]):
        resuelto = resolver.resolver_productos(texto, catalogos)
        assert fila["sku"] in resuelto.skus, texto


def test_el_nombre_completo_de_un_producto_gana_a_los_que_lo_contienen(catalogos):
    # "Refresco de cola light 2 L" tiene todas las palabras de "Refresco de cola 2 L" y no es ese producto.
    assert resolver.resolver_productos("Refresco de cola 2 L", catalogos).skus == ["SKU-1077"]
    assert resolver.resolver_productos("refresco de cola light 2 l", catalogos).skus == ["SKU-1080"]


@pytest.mark.parametrize(
    "texto, skus",
    [
        ("pechuga", ["SKU-1023"]), ("pechuga de pollo", ["SKU-1023"]), ("bolillos", ["SKU-1013"]),
        ("bolillo", ["SKU-1013"]), ("pan de caja", ["SKU-1018"]), ("pan blanco", ["SKU-1018"]),
        ("leche deslactosada", ["SKU-1002"]), ("leche light", ["SKU-1003"]),
        ("refresco de cola de 600", ["SKU-1078"]), ("cola 600 ml", ["SKU-1078"]),
        ("jitomate", ["SKU-1039"]), ("limones", ["SKU-1036"]), ("plátano", ["SKU-1033"]),
        ("tortillas de maiz", ["SKU-1017"]), ("tortillas de harina", ["SKU-1019"]),
        ("queso panela", ["SKU-1006"]), ("huevo blanco", ["SKU-1011"]),
        ("agua natural 6 litros", ["SKU-1076"]), ("pechuga kilos", ["SKU-1023"]),
    ],
)
def test_un_nombre_parcial_o_coloquial_llega_al_producto(catalogos, texto, skus):
    assert resolver.resolver_productos(texto, catalogos).skus == skus


@pytest.mark.parametrize(
    "texto, skus",
    [
        ("pehcuga", ["SKU-1023"]), ("bolilos", ["SKU-1013"]),
        ("deslactosda", ["SKU-1002"]), ("refresco de colla 600", ["SKU-1078"]), ("lechuca", ["SKU-1043"]),
        ("manzna roja", ["SKU-1034"]), ("aguacte", ["SKU-1035"]),
    ],
)
def test_un_error_de_dedo_de_una_palabra_del_catalogo_llega_al_producto_y_lo_dice(catalogos, texto, skus):
    resuelto = resolver.resolver_productos(texto, catalogos)
    assert resuelto.skus == skus
    assert resuelto.notas and f"'{texto}' se interpretó como" in resuelto.notas[0]


@pytest.mark.parametrize(
    "texto, subcategoria",
    [("pollo", "Pollo"), ("yogurt", "Yogur"), ("queso", "Quesos"), ("quesos", "Quesos"), ("huevos", "Huevo"),
     ("refrescos", "Refrescos"), ("cervezas", "Cerveza"), ("tortillas", "Tortillas"), ("leche", "Leche"),
     ("verduras", "Verduras"), ("agua", "Agua"), ("cafe", "Café")],
)
def test_el_nombre_de_una_subcategoria_son_todos_sus_productos_y_lo_dice(catalogos, texto, subcategoria):
    resuelto = resolver.resolver_productos(texto, catalogos)
    assert resuelto.skus == _skus(subcategoria=subcategoria)
    assert resuelto.por_grupo
    assert f"'{texto}' se tomó como la subcategoría {subcategoria}" in resuelto.notas[0]


@pytest.mark.parametrize("texto, categoria", [("lacteos", "Lácteos"), ("carnes", "Carnes"), ("limpieza", "Limpieza")])
def test_el_nombre_de_una_categoria_son_todos_sus_productos_y_lo_dice(catalogos, texto, categoria):
    resuelto = resolver.resolver_productos(texto, catalogos)
    assert resuelto.skus == _skus(categoria=categoria)
    assert f"la categoría {categoria}" in resuelto.notas[0]


def test_un_grupo_dice_que_otros_productos_con_la_palabra_quedan_fuera(catalogos):
    notas = resolver.resolver_productos("leche", catalogos).notas
    fuera = " ".join(notas[1:])
    assert "Leche de almendra" in fuera and "Leche condensada" in fuera and "Leche saborizada" in fuera


@pytest.mark.parametrize(
    "texto, candidatos",
    [
        ("pan", ["SKU-1018", "SKU-1021"]),
        ("papa", ["SKU-1041", "SKU-1071"]),
        ("pavo", ["SKU-1029", "SKU-1030"]),
        ("coca", ["SKU-1077", "SKU-1078", "SKU-1080"]),
        ("refresco de cola", ["SKU-1077", "SKU-1078", "SKU-1080"]),
        ("refresco 2 litros", ["SKU-1077", "SKU-1079", "SKU-1080"]),
    ],
)
def test_un_nombre_que_coincide_con_varios_productos_distintos_da_entidad_ambigua(catalogos, texto, candidatos):
    with pytest.raises(errores.EntidadAmbigua) as capturado:
        resolver.resolver_productos(texto, catalogos)
    etiquetas = capturado.value.detalle["candidatos"]
    assert [etiqueta.split()[0] for etiqueta in etiquetas] == candidatos
    assert etiquetas[0] == f"{candidatos[0]} {next(f['nombre'] for f in CATALOGO if f['sku'] == candidatos[0])}"


@pytest.mark.parametrize("texto", ["pizza", "laptop", "refri", "leche de coco", "pollo con mole", "xyz"])
def test_un_producto_que_no_esta_en_el_catalogo_da_entidad_inexistente(catalogos, texto):
    with pytest.raises(errores.EntidadInexistente) as capturado:
        resolver.resolver_productos(texto, catalogos)
    assert capturado.value.detalle["entidad"] == "producto"


def test_pizza_no_se_confunde_con_pieza(catalogos):
    # Las dos se parecen (0.8), pero "pieza" no es un producto: es lo que cuentan 14 nombres.
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_productos("pizza", catalogos)


def test_un_sku_que_no_existe_da_entidad_inexistente(catalogos):
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_productos("SKU-9999", catalogos)


def test_un_sku_en_minusculas_se_resuelve(catalogos):
    assert resolver.resolver_productos("sku-1018", catalogos).skus == ["SKU-1018"]


def test_dos_palabras_igual_de_parecidas_no_se_adivinan(catalogos):
    # "polo" se parece igual a "pollo" y a "polvo": no hay una corrección clara.
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_productos("polo", catalogos)


def test_un_texto_que_solo_trae_palabras_de_relleno_o_ruido_no_es_un_producto(catalogos):
    for texto in ("de la", "refri", "la de litro"):
        with pytest.raises(errores.EntidadInexistente):
            resolver.resolver_productos(texto, catalogos)


# ------------------------------------------------------------------ sustitución de SKU (P01, P54)

def test_leche_entera_suma_el_sku_descontinuado_y_el_que_lo_sustituye_y_lo_dice(catalogos):
    resuelto = resolver.resolver_productos("leche entera", catalogos)
    assert resuelto.skus == ["SKU-1001", "SKU-1121"]
    sustitucion = resuelto.notas[-1]
    assert "Leche entera 1 L (SKU-1001)" in sustitucion
    assert "14 de julio de 2026" in sustitucion
    assert "Leche entera pasteurizada 1 L (nueva presentación) (SKU-1121)" in sustitucion
    assert "se suman las dos presentaciones" in sustitucion


@pytest.mark.parametrize("texto", ["Leche entera 1 L", "leche entera pasteurizada", "nueva presentacion de leche entera", "SKU-1001", "SKU-1121"])
def test_cualquiera_de_los_dos_sku_de_la_leche_entera_trae_a_los_dos(catalogos, texto):
    assert resolver.resolver_productos(texto, catalogos).skus == ["SKU-1001", "SKU-1121"]


def test_una_cadena_de_tres_sustituciones_se_sigue_en_los_dos_sentidos():
    productos = [
        ("SKU-1001", "Galleta uno", "Panadería", "Galletas", dt.date(2026, 6, 10), "SKU-1002"),
        ("SKU-1002", "Galleta dos", "Panadería", "Galletas", dt.date(2026, 7, 20), "SKU-1003"),
        ("SKU-1003", "Galleta tres", "Panadería", "Galletas", None, None),
        ("SKU-1004", "Galleta ajena", "Panadería", "Galletas", None, None),
    ]
    catalogos = _catalogos_en_memoria(productos=productos)
    for texto in ("galleta uno", "galleta dos", "galleta tres", "SKU-1002"):
        resuelto = resolver.resolver_productos(texto, catalogos)
        assert resuelto.skus == ["SKU-1001", "SKU-1002", "SKU-1003"], texto
    assert "se suman las 3 presentaciones" in resolver.resolver_productos("galleta dos", catalogos).notas[-1]
    assert resolver.resolver_productos("galleta ajena", catalogos).skus == ["SKU-1004"]


def test_con_sumar_sustitutos_apagado_cada_sku_es_su_producto(copia_config, catalogos):
    ruta = copia_config / "supuestos.yaml"
    ruta.write_text(ruta.read_text(encoding="utf-8").replace("sumar_sustitutos: true", "sumar_sustitutos: false"), encoding="utf-8")
    apagado = definiciones.cargar(ruta)
    assert not apagado.ventas.sumar_sustitutos
    assert resolver.resolver_productos("Leche entera 1 L", catalogos, s=apagado).skus == ["SKU-1001"]
    with pytest.raises(errores.EntidadAmbigua) as capturado:
        resolver.resolver_productos("leche entera", catalogos, s=apagado)
    assert [e.split()[0] for e in capturado.value.detalle["candidatos"]] == ["SKU-1001", "SKU-1121"]


def test_sin_el_vinculo_de_sustitucion_los_dos_sku_de_leche_entera_son_productos_distintos():
    # Es el cambio en vivo de E04: SKU-1001 sin sku_sustituto. Pasan de una sola leche a dos.
    productos = [
        ("SKU-1001", "Leche entera 1 L", "Lácteos", "Leche", dt.date(2026, 7, 14), None),
        ("SKU-1121", "Leche entera pasteurizada 1 L (nueva presentación)", "Lácteos", "Leche", None, None),
    ]
    with pytest.raises(errores.EntidadAmbigua):
        resolver.resolver_productos("leche entera", _catalogos_en_memoria(productos=productos))


# ------------------------------------------------------------------ periodos

@pytest.mark.parametrize(
    "texto, desde, hasta",
    [
        ("julio", (2026, 7, 1), (2026, 7, 31)),
        ("Julio", (2026, 7, 1), (2026, 7, 31)),
        ("en julio", (2026, 7, 1), (2026, 7, 31)),
        ("julio de 2026", (2026, 7, 1), (2026, 7, 31)),
        ("julio 2026", (2026, 7, 1), (2026, 7, 31)),
        ("junio", (2026, 6, 1), (2026, 6, 30)),
        ("agosto", (2026, 8, 1), (2026, 8, 31)),
        ("2026-07", (2026, 7, 1), (2026, 7, 31)),
        ("junio a agosto", (2026, 6, 1), (2026, 8, 31)),
        ("de junio a agosto", (2026, 6, 1), (2026, 8, 31)),
        ("de junio hasta agosto", (2026, 6, 1), (2026, 8, 31)),
        ("desde junio hasta agosto", (2026, 6, 1), (2026, 8, 31)),
        ("desde el 15 de julio hasta el 15 de agosto", (2026, 7, 15), (2026, 8, 15)),
        ("entre junio y agosto", (2026, 6, 1), (2026, 8, 31)),
        ("junio y julio", (2026, 6, 1), (2026, 7, 31)),
        ("junio-julio", (2026, 6, 1), (2026, 7, 31)),
        ("14 de julio", (2026, 7, 14), (2026, 7, 14)),
        ("el 14 de julio de 2026", (2026, 7, 14), (2026, 7, 14)),
        ("2026-07-14", (2026, 7, 14), (2026, 7, 14)),
        ("del 1 al 15 de julio", (2026, 7, 1), (2026, 7, 15)),
        ("1 de julio al 15 de agosto", (2026, 7, 1), (2026, 8, 15)),
        ("2026", (2026, 6, 1), (2026, 8, 31)),
    ],
)
def test_los_periodos_que_nombran_meses_y_dias_se_resuelven_a_fechas(texto, desde, hasta):
    periodo = resolver.resolver_periodo(texto, fuente="ventas")
    assert (periodo.desde, periodo.hasta) == (dt.date(*desde), dt.date(*hasta))


def test_un_mes_claro_no_lleva_nota_y_dice_cuantos_dias_tiene():
    periodo = resolver.resolver_periodo("julio", fuente="ventas")
    assert periodo.notas == [] and periodo.dias == 31 and periodo.etiqueta == "julio de 2026"


@pytest.mark.parametrize(
    "texto, mes",
    [("jullio", "julio de 2026"), ("agsoto", "agosto de 2026"), ("junnio", "junio de 2026"),
     ("ago", "agosto de 2026"), ("jul", "julio de 2026")],
)
def test_un_mes_con_error_de_dedo_o_abreviado_se_resuelve_y_lo_dice(texto, mes):
    assert resolver.resolver_periodo(texto, fuente="ventas").notas == [f"'{texto}' se interpretó como {mes}"]


@pytest.mark.parametrize("texto", ["septiembrre", "sept"])
def test_un_mes_con_error_de_dedo_o_abreviado_que_cae_fuera_del_corte_dice_como_lo_leyo(texto):
    with pytest.raises(errores.FueraDePeriodo) as capturado:
        resolver.resolver_periodo(texto, fuente="ventas")
    assert capturado.value.detalle["interpretacion"] == f"'{texto}' se interpretó como septiembre de 2026"


def test_dos_meses_igual_de_parecidos_no_se_adivinan():
    # "juio" se parece igual a "junio" y a "julio".
    with pytest.raises(comun.ErrorHerramienta) as capturado:
        resolver.resolver_periodo("juio", fuente="ventas")
    assert capturado.value.estado == "periodo_no_entendido"


@pytest.mark.parametrize("texto", ["el trimestre", "trimestre", "todo", "total", "todo el periodo", "los tres meses"])
def test_el_trimestre_es_todo_el_corte_y_lo_dice(texto):
    periodo = resolver.resolver_periodo(texto, fuente="ventas")
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 6, 1), dt.date(2026, 8, 31))
    assert periodo.notas == [f"'{texto}' se tomó como junio a agosto de 2026"]


def test_ayer_es_el_dia_antes_del_hoy_de_la_referencia_y_avisa_que_la_fecha_real_no_tiene_datos():
    periodo = resolver.resolver_periodo("ayer", fuente="tickets", hoy_real=HOY_REAL)
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 8, 31), dt.date(2026, 8, 31))
    assert periodo.notas == [f"{AVISO}; ayer es el 31 de agosto de 2026"]
    assert "Tomo como hoy el 1 de septiembre de 2026" in AVISO and "30 de octubre de 2026 no hay datos" in AVISO
    assert "al corte del 1 de septiembre de 2026" in AVISO


@pytest.mark.parametrize(
    "texto, desde, hasta, frase",
    [
        ("el mes pasado", (2026, 8, 1), (2026, 8, 31), "el mes pasado es agosto de 2026"),
        ("mes pasado", (2026, 8, 1), (2026, 8, 31), "el mes pasado es agosto de 2026"),
        ("el ultimo mes", (2026, 8, 1), (2026, 8, 31), "el mes pasado es agosto de 2026"),
        ("la ultima semana", (2026, 8, 25), (2026, 8, 31), "la última semana va del 25 de agosto de 2026 al 31 de agosto de 2026"),
        ("los ultimos 7 dias", (2026, 8, 25), (2026, 8, 31), "los últimos 7 días van del 25 de agosto de 2026 al 31 de agosto de 2026"),
        ("los ultimos 30 dias", (2026, 8, 2), (2026, 8, 31), "los últimos 30 días van del 2 de agosto de 2026 al 31 de agosto de 2026"),
        ("la semana pasada", (2026, 8, 24), (2026, 8, 30), "la semana pasada va del 24 de agosto de 2026 al 30 de agosto de 2026"),
        ("anteayer", (2026, 8, 30), (2026, 8, 30), "anteayer es el 30 de agosto de 2026"),
    ],
)
def test_las_fechas_relativas_se_cuentan_desde_el_hoy_de_la_referencia_y_dicen_como(texto, desde, hasta, frase):
    periodo = resolver.resolver_periodo(texto, fuente="ventas", hoy_real=HOY_REAL)
    assert (periodo.desde, periodo.hasta) == (dt.date(*desde), dt.date(*hasta))
    assert periodo.notas == [f"{AVISO}; {frase}"]


def test_el_primero_de_septiembre_de_2026_es_un_martes():
    # La semana pasada del test anterior supone esto: la semana de hoy empieza el lunes 31 de agosto.
    assert dt.date(2026, 9, 1).weekday() == 1


def test_esta_semana_se_recorta_a_los_dias_con_datos_y_lo_dice():
    periodo = resolver.resolver_periodo("esta semana", fuente="ventas", hoy_real=HOY_REAL)
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 8, 31), dt.date(2026, 8, 31))
    assert len(periodo.notas) == 2
    assert periodo.notas[0] == f"{AVISO}; esta semana va del 31 de agosto de 2026 al 6 de septiembre de 2026"
    assert periodo.notas[1] == "esta semana: solo hay datos del 31 de agosto de 2026 al 31 de agosto de 2026"


def test_los_ultimos_n_dias_que_pasan_del_inicio_de_los_datos_se_recortan_y_lo_dicen():
    # 400 días antes del 1 de septiembre de 2026 es el 28 de julio de 2025: los datos empiezan el 1 de junio de 2026.
    periodo = resolver.resolver_periodo("los ultimos 400 dias", fuente="ventas", hoy_real=HOY_REAL)
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 6, 1), dt.date(2026, 8, 31))
    assert periodo.notas == [
        f"{AVISO}; los últimos 400 días van del 28 de julio de 2025 al 31 de agosto de 2026",
        "los ultimos 400 dias: solo hay datos del 1 de junio de 2026 al 31 de agosto de 2026",
    ]


@pytest.mark.parametrize(
    "texto, frase",
    [("hoy", None), ("este mes", "este mes es septiembre de 2026")],
)
def test_hoy_y_este_mes_caen_fuera_de_los_datos_y_dan_fuera_de_periodo_con_la_interpretacion(texto, frase):
    with pytest.raises(errores.FueraDePeriodo) as capturado:
        resolver.resolver_periodo(texto, fuente="ventas", hoy_real=HOY_REAL)
    error = capturado.value
    assert error.estado == "fuera_de_periodo"
    assert error.detalle["periodo_pedido"] == texto
    assert error.detalle["interpretacion"] == (AVISO if frase is None else f"{AVISO}; {frase}")
    assert (error.detalle["corte_desde"], error.detalle["corte_hasta"]) == ("2026-06-01", "2026-08-31")


@pytest.mark.parametrize("texto", ["septiembre", "mayo", "julio 2025", "2025", "2026-09-15", "septiembre de 2026"])
def test_un_periodo_sin_ningun_dia_con_datos_da_fuera_de_periodo_y_no_un_cero(texto):
    with pytest.raises(errores.FueraDePeriodo) as capturado:
        resolver.resolver_periodo(texto, fuente="ventas")
    assert capturado.value.detalle["periodo_pedido"] == texto
    assert capturado.value.detalle["corte_hasta"] == "2026-08-31"
    assert "interpretacion" not in capturado.value.detalle, "no es una fecha relativa: no hay hoy que interpretar"


def test_un_periodo_que_pasa_del_corte_se_recorta_y_lo_dice():
    periodo = resolver.resolver_periodo("de junio a septiembre", fuente="ventas")
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 6, 1), dt.date(2026, 8, 31))
    assert periodo.notas == ["de junio a septiembre: solo hay datos del 1 de junio de 2026 al 31 de agosto de 2026"]


def test_los_tickets_tienen_su_propio_corte_sin_fecha_de_inicio():
    sin_texto = resolver.resolver_periodo(None, fuente="tickets")
    assert (sin_texto.desde, sin_texto.hasta, sin_texto.dias) == (None, dt.date(2026, 8, 31), None)
    assert resolver.resolver_periodo("junio", fuente="tickets").hasta == dt.date(2026, 6, 30)
    with pytest.raises(errores.FueraDePeriodo) as capturado:
        resolver.resolver_periodo("septiembre", fuente="tickets")
    assert "corte_desde" not in capturado.value.detalle and capturado.value.detalle["corte_hasta"] == "2026-08-31"


def test_cada_fuente_recorta_el_texto_a_su_propio_corte_y_no_al_de_ventas():
    # Mayo está antes del primer día de ventas, pero los tickets no tienen fecha de inicio.
    mayo = resolver.resolver_periodo("mayo", fuente="tickets")
    assert (mayo.desde, mayo.hasta, mayo.notas) == (dt.date(2026, 5, 1), dt.date(2026, 5, 31), [])
    with pytest.raises(errores.FueraDePeriodo):
        resolver.resolver_periodo("mayo", fuente="ventas")
    # "Todo" es todo el corte de la fuente: los tickets, sin inicio; el inventario, la foto de un solo día.
    todo_tickets = resolver.resolver_periodo("todo", fuente="tickets")
    assert (todo_tickets.desde, todo_tickets.hasta) == (None, dt.date(2026, 8, 31))
    assert todo_tickets.notas == ["'todo' se tomó como hasta el 31 de agosto de 2026"]
    todo_inventario = resolver.resolver_periodo("todo", fuente="inventario")
    assert (todo_inventario.desde, todo_inventario.hasta) == (dt.date(2026, 9, 1), dt.date(2026, 9, 1))


def test_hoy_si_tiene_datos_de_inventario_porque_la_foto_es_del_1_de_septiembre():
    # Con las ventas, "hoy" cae fuera del corte; el inventario es la foto del día que se toma como hoy.
    periodo = resolver.resolver_periodo("hoy", fuente="inventario", hoy_real=HOY_REAL)
    assert (periodo.desde, periodo.hasta, periodo.notas) == (dt.date(2026, 9, 1), dt.date(2026, 9, 1), [AVISO])
    with pytest.raises(errores.FueraDePeriodo):
        resolver.resolver_periodo("ayer", fuente="inventario", hoy_real=HOY_REAL)


@pytest.mark.parametrize(
    "texto",
    [
        "xyz", "la semana que viene", "junio y agosto", "agosto a junio", "31 de junio", "3 de julio al 1 de julio",
        "cada lunes", "julio y", "ultimos 0 dias",
        # Un extremo sin el otro no se adivina: "desde julio" no dice dónde acaba ni "hasta julio" dónde empieza.
        # Leerlo como el mes suelto devolvería solo julio sin avisar.
        "desde julio", "desde el 1 de julio", "hasta julio", "a julio", "julio a", "hasta", "desde",
    ],
)
def test_un_periodo_que_no_se_entiende_da_periodo_no_entendido_con_ejemplos(texto):
    with pytest.raises(comun.ErrorHerramienta) as capturado:
        resolver.resolver_periodo(texto, fuente="ventas")
    error = capturado.value
    assert error.estado == "periodo_no_entendido"
    assert "ayer" in error.detalle["candidatos"]


def test_las_fechas_explicitas_mandan_y_se_recortan_al_corte():
    periodo = resolver.resolver_periodo(None, dt.date(2026, 8, 15), dt.date(2026, 9, 15), "ventas")
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 8, 15), dt.date(2026, 8, 31))
    assert periodo.notas == ["solo hay datos del 15 de agosto de 2026 al 31 de agosto de 2026"]


def test_unas_fechas_explicitas_fuera_del_corte_dan_el_error_de_en_corte():
    with pytest.raises(comun.ErrorHerramienta) as capturado:
        resolver.resolver_periodo(None, dt.date(2026, 9, 1), dt.date(2026, 9, 30), "ventas")
    assert capturado.value.estado == "fuera_de_periodo"
    assert capturado.value.detalle == {"corte_desde": "2026-06-01", "corte_hasta": "2026-08-31"}


def test_si_llegan_el_texto_y_las_fechas_mandan_las_fechas_y_lo_dice():
    periodo = resolver.resolver_periodo("agosto", dt.date(2026, 7, 1), dt.date(2026, 7, 31), "ventas")
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    assert periodo.notas == ["se usaron las fechas pedidas, 2026-07-01 a 2026-07-31, y no 'agosto'"]


def test_sin_fecha_real_el_aviso_usa_la_del_reloj():
    periodo = resolver.resolver_periodo("ayer", fuente="ventas")
    assert definiciones.aviso_fecha_relativa(dt.date.today()) in periodo.notas[0]


def test_el_hoy_de_la_referencia_sale_de_supuestos_y_cambia_con_el():
    # Si D3 cambia, "ayer" cambia sin tocar el resolvedor: el hoy es fecha_referencia de supuestos.yaml.
    s = definiciones.supuestos().model_copy(update={"fecha_referencia": dt.date(2026, 8, 11)})
    periodo = resolver.resolver_periodo("ayer", fuente="ventas", hoy_real=HOY_REAL, s=s)
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 8, 10), dt.date(2026, 8, 10))
    assert "11 de agosto de 2026" in periodo.notas[0]


# ------------------------------------------------------------------ preguntas de la rúbrica de punta a punta

def test_P48_el_mes_pasado_dice_que_cuenta_desde_el_1_de_septiembre_y_no_lo_da_como_el_mes_pasado_real():
    periodo = resolver.resolver_periodo("el mes pasado", fuente="ventas", hoy_real=HOY_REAL)
    assert AVISO in periodo.notas[0] and "agosto de 2026" in periodo.notas[0]


def test_P50_ayer_en_tickets_dice_que_el_30_de_octubre_no_tiene_datos():
    periodo = resolver.resolver_periodo("ayer", fuente="tickets", hoy_real=HOY_REAL)
    assert "30 de octubre de 2026 no hay datos" in periodo.notas[0]


def test_P43_los_tickets_de_valle_norte_de_junio(catalogos):
    assert resolver.resolver_tiendas(["valle norte"], catalogos).ids == ["T01"]
    periodo = resolver.resolver_periodo("de junio", fuente="tickets")
    assert (periodo.desde, periodo.hasta) == (dt.date(2026, 6, 1), dt.date(2026, 6, 30))


def test_P54_la_leche_entera_de_junio_contra_agosto(catalogos):
    assert resolver.resolver_productos("la leche entera", catalogos).skus == ["SKU-1001", "SKU-1121"]
    junio = resolver.resolver_periodo("junio", fuente="ventas")
    agosto = resolver.resolver_periodo("agosto", fuente="ventas")
    assert (junio.desde, junio.hasta, agosto.desde, agosto.hasta) == (
        dt.date(2026, 6, 1), dt.date(2026, 6, 30), dt.date(2026, 8, 1), dt.date(2026, 8, 31),
    )


def test_P05_vender_por_internet_es_el_canal_Ecommerce(catalogos):
    assert resolver.resolver_canal("por internet", catalogos) == "Ecommerce"


def test_la_nota_de_sustitucion_cuenta_solo_los_sku_de_la_cadena_y_no_los_demas_del_grupo(catalogos):
    # "leche" son 4 SKU, pero solo SKU-1001 y SKU-1121 son el mismo producto por sustitución.
    notas = resolver.resolver_productos("leche", catalogos).notas
    sustitucion = notas[-1]
    assert "se suman las dos presentaciones" in sustitucion
    assert "SKU-1002" not in sustitucion and "SKU-1003" not in sustitucion


# ------------------------------------------------------------------ robustez (K10)

TEXTOS_RAROS = [
    "", " ", "   \t\n", "a", "ñ", "ÁÉÍÓÚ", "0", "00", "1234", "-", "--", "...", "a-b-c", "1-2-3", "2026-13-45",
    "2026-02-30", "99999999999999999999", "T01'; DROP TABLE ventas; --", "SKU-", "SKU-12345", "sku-0001",
    "%", "_", "\\", "\x00", "퟿", "😀", "tienda " * 60, "x" * 5000, "de la del los las", "a al hasta y e entre",
    "entre y", "del al de", "hasta hasta", "ultimos 0 dias", "ultimos 999 dias", "los ultimos siete dias",
    "2026 2026", "julio julio", "1 2 3 julio", "32 de julio", "0 de julio", "julio 1999", "julio 2200",
    "kilos", "litros", "kg", "l", "1 kg", "pollo pollo pollo", "refri", "la de litro", "yogurt " * 30,
]


def _aleatorios(cuantos: int) -> list[str]:
    import random

    azar = random.Random(20261031)
    alfabeto = "abcdefghijklmnñopqrstuvwxyzáéíóú0123456789 -.,;:/()'\"\n"
    return ["".join(azar.choice(alfabeto) for _ in range(azar.randint(1, 40))) for _ in range(cuantos)]


@pytest.mark.parametrize("texto", TEXTOS_RAROS + _aleatorios(150))
def test_el_resolvedor_solo_devuelve_un_resultado_o_lanza_errores_con_tipo(catalogos, texto):
    llamadas = [
        lambda: resolver.resolver_tiendas([texto], catalogos),
        lambda: resolver.resolver_canal(texto, catalogos),
        lambda: resolver.resolver_categoria(texto, catalogos),
        lambda: resolver.resolver_productos(texto, catalogos),
        lambda: resolver.resolver_periodo(texto, fuente="ventas", hoy_real=HOY_REAL),
        lambda: resolver.resolver_periodo(texto, fuente="tickets", hoy_real=HOY_REAL),
    ]
    for llamar in llamadas:
        try:
            llamar()
        except comun.ErrorHerramienta:
            pass


def test_un_texto_con_sql_no_llega_a_la_base_y_no_cambia_nada(catalogos, base_construida):
    inyeccion = "T01'; DROP TABLE ventas; --"
    with pytest.raises(comun.ErrorHerramienta):
        resolver.resolver_tiendas([inyeccion], catalogos)
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM ventas").fetchone()[0] == 41_859


@pytest.mark.parametrize("texto", ["T01 guadalupe", "T01 cumbres", "t01 y la de guadalupe", "T01'; DROP TABLE ventas; --"])
def test_un_id_de_tienda_con_otras_palabras_que_no_son_su_etiqueta_no_se_resuelve_en_silencio(catalogos, texto):
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_tiendas([texto], catalogos)


@pytest.mark.parametrize("texto, ids", [("T01 y T02", ["T01", "T02"]), ("t03, t04", ["T03", "T04"]), ("T02 T02", ["T02"])])
def test_varios_ids_en_un_solo_texto_se_resuelven_sin_nota(catalogos, texto, ids):
    tiendas = resolver.resolver_tiendas([texto], catalogos)
    assert (tiendas.ids, tiendas.notas) == (ids, [])


@pytest.mark.parametrize("texto", ["col", "agu", "vin"])
def test_una_palabra_de_tres_letras_no_se_corrige_hacia_otra(catalogos, texto):
    # "col" se parece 0.86 a "cola", "agu" a "agua" y "vin" a "vino", pero con tres letras no hay
    # forma de distinguir un error de dedo de otra palabra: no se adivina.
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_productos(texto, catalogos)


# ------------------------------------------------------------------ la etiqueta de un candidato (D34-B)


def _candidatos_de(texto, catalogos):
    with pytest.raises(errores.EntidadAmbigua) as ambigua:
        resolver.resolver_productos(texto, catalogos)
    return ambigua.value.detalle["candidatos"]


def test_cada_candidato_que_ofrece_entidad_ambigua_se_resuelve_a_su_sku(catalogos):
    # El error ofrece «SKU-1037 Naranja kg» y dice «vuelve a llamar con su SKU»: el modelo a veces copia la etiqueta entera,
    # y rechazarla le cuesta una solicitud de las 4 del turno.
    candidatos = _candidatos_de("naranja", catalogos)
    assert len(candidatos) == 3
    for etiqueta in candidatos:
        sku = etiqueta.split()[0]
        assert resolver.resolver_productos(etiqueta, catalogos).skus == resolver.resolver_productos(sku, catalogos).skus


@pytest.mark.parametrize("etiqueta", ["Naranja kg (SKU-1037)", "naranja kg (sku-1037)", "sku-1037 naranja KG", "SKU-1037, Naranja kg"])
def test_la_etiqueta_se_acepta_con_el_sku_antes_o_despues_del_nombre(catalogos, etiqueta):
    assert resolver.resolver_productos(etiqueta, catalogos).skus == ["SKU-1037"]


@pytest.mark.parametrize("texto", ["SKU-1037 Jugo de naranja 1 L", "SKU-9999 Naranja kg", "SKU-1037 algo más"])
def test_un_sku_con_otro_nombre_no_se_resuelve_por_el_sku(catalogos, texto):
    # Como en las tiendas: un id con otras palabras no se resuelve por el id, y un nombre que no cuadra no se ignora.
    with pytest.raises(errores.EntidadInexistente):
        resolver.resolver_productos(texto, catalogos)


def test_el_aviso_de_entidad_ambigua_dice_en_que_campo_va_el_sku(catalogos):
    # "vuelve a llamar con su SKU" a secas llevó al modelo a inventar un campo sku: la validación lo rechaza y cuesta una
    # solicitud de las 4 del turno.
    with pytest.raises(errores.EntidadAmbigua) as ambigua:
        resolver.resolver_productos("naranja", catalogos)
    assert "en el campo producto" in ambigua.value.detalle["antes_de_contestar"]
