"""Carga tipada de los 5 CSV, vistas y tablas auxiliares de build/tienda.duckdb (T1.2, K04).

Acepta: conteos de 4, 121, 41,859, 480 y 285 filas; venta neta total de 17,228,300.26; las 409
combinaciones repetidas de fecha, tienda, SKU y canal se conservan. Las cifras se escriben aquí
porque el criterio de aceptación las fija; el código no las conoce.

La base de la fixture base_construida vive en un directorio temporal (tests/conftest.py). Las
pruebas que alteran datos o configuración trabajan sobre copias en tmp_path y construyen sin
políticas para tardar menos.
"""

import datetime as dt
import json
from decimal import Decimal

import duckdb
import pytest
import yaml

from agente import construir

CONTEOS = {"tiendas": 4, "catalogo": 121, "ventas": 41_859, "inventario": 480, "tickets": 285}
COLUMNAS = {"tiendas": 7, "catalogo": 13, "ventas": 7, "inventario": 6, "tickets": 9}
VENTA_NETA_TOTAL = Decimal("17228300.26")
VENTA_SIN_DEVOLUCIONES = Decimal("17272235.76")
COMBINACIONES_REPETIDAS = 409


@pytest.fixture(scope="module")
def con(base_construida):
    with duckdb.connect(str(base_construida.base), read_only=True) as conexion:
        yield conexion


def uno(con, sql, parametros=None):
    return con.execute(sql, parametros or []).fetchone()[0]


def tipos(con, tabla):
    return {nombre: tipo for nombre, tipo, *_ in con.execute(f"DESCRIBE {tabla}").fetchall()}


def construir_datos(dir_datos, tmp_path, **opciones):
    """Construye solo la parte de datos (sin políticas ni índice) en un archivo nuevo de tmp_path."""
    destino = tmp_path / "copia.duckdb"
    return construir.construir(dir_datos, destino, politicas=False, indice_texto=False, **opciones)


# --- Criterio de aceptación -------------------------------------------------------------------


@pytest.mark.parametrize("tabla, filas", CONTEOS.items())
def test_cada_csv_carga_todas_sus_filas(con, base_construida, tabla, filas):
    assert uno(con, f"SELECT count(*) FROM {tabla}") == filas
    assert base_construida.filas[tabla] == filas


@pytest.mark.parametrize("vista, tabla", [("v_ventas_dia", "ventas"), ("v_inventario", "inventario"), ("v_tickets", "tickets")])
def test_las_vistas_no_pierden_ni_duplican_filas(con, vista, tabla):
    assert uno(con, f"SELECT count(*) FROM {vista}") == CONTEOS[tabla]


def test_la_venta_neta_total_es_exacta_y_decimal(con):
    total = uno(con, "SELECT sum(venta_neta_mxn) FROM ventas")
    assert isinstance(total, Decimal)
    assert total == VENTA_NETA_TOTAL
    assert uno(con, "SELECT sum(venta_neta_mxn) FROM v_ventas_dia WHERE en_venta_neta") == VENTA_NETA_TOTAL


@pytest.mark.parametrize("tabla", ["ventas", "v_ventas_dia"])
def test_las_409_combinaciones_repetidas_se_conservan(con, tabla):
    grupos = con.execute(
        f"SELECT filas, count(*) FROM (SELECT count(*) AS filas FROM {tabla} "
        "GROUP BY fecha, tienda_id, sku, canal HAVING count(*) > 1) GROUP BY filas"
    ).fetchall()
    assert grupos == [(2, COMBINACIONES_REPETIDAS)]


def test_las_repetidas_son_una_venta_y_una_devolucion_del_mismo_dia(con):
    # Son legítimas: no se deduplican. Cada par trae una fila positiva y otra negativa.
    pares = con.execute(
        "SELECT count(*) FILTER (WHERE es_devolucion) AS devoluciones, count(*) AS filas FROM v_ventas_dia "
        "GROUP BY fecha, tienda_id, sku, canal HAVING count(*) > 1"
    ).fetchall()
    assert set(pares) == {(1, 2)}


# --- Tipos declarados y dialecto (K04) -----------------------------------------------------------


@pytest.mark.parametrize("tabla, columnas", COLUMNAS.items())
def test_cada_tabla_tiene_las_columnas_del_diccionario(con, tabla, columnas):
    assert len(tipos(con, tabla)) == columnas
    assert list(tipos(con, tabla)) == list(construir.FUENTES[tabla].nombres())


def test_montos_en_decimal_y_fechas_en_date(con):
    ventas = tipos(con, "ventas")
    assert ventas["fecha"] == "DATE"
    assert ventas["venta_neta_mxn"] == "DECIMAL(12,2)"
    assert ventas["descuento_mxn"] == "DECIMAL(12,2)"
    assert ventas["unidades"] == "INTEGER"
    catalogo = tipos(con, "catalogo")
    assert catalogo["precio_lista_mxn"] == "DECIMAL(12,2)"
    assert catalogo["costo_mxn"] == "DECIMAL(12,2)"
    assert catalogo["perecedero"] == "BOOLEAN"
    assert catalogo["fecha_baja"] == "DATE"
    assert tipos(con, "inventario")["fecha_caducidad_lote_proximo"] == "DATE"
    tickets = tipos(con, "tickets")
    assert tickets["fecha_creacion"] == "TIMESTAMP"
    assert tickets["fecha_cierre"] == "TIMESTAMP"
    assert tipos(con, "tiendas")["fecha_apertura"] == "DATE"


def test_los_vacios_del_csv_quedan_como_nulos(con):
    assert uno(con, "SELECT count(*) FROM tickets WHERE fecha_cierre IS NULL") == 17
    assert uno(con, "SELECT sku_sustituto FROM catalogo WHERE sku = 'SKU-1002'") is None
    assert uno(con, "SELECT sku_sustituto FROM catalogo WHERE sku = 'SKU-1001'") == "SKU-1121"
    assert uno(con, "SELECT fecha_baja FROM catalogo WHERE sku = 'SKU-1001'") == dt.date(2026, 7, 14)


def test_un_archivo_que_falta_da_un_error_con_tipo_y_no_crea_la_base(copia_datos, tmp_path):
    (copia_datos / "ventas.csv").rename(copia_datos / "ventas_2026.csv")
    with pytest.raises(construir.ArchivoFaltante) as capturado:
        construir_datos(copia_datos, tmp_path)
    assert capturado.value.archivos == ("ventas.csv",)
    assert "ventas.csv" in str(capturado.value)
    assert isinstance(capturado.value, construir.ErrorConstruccion)
    assert not (tmp_path / "copia.duckdb").exists()


def test_un_error_no_borra_la_base_anterior(copia_datos, tmp_path):
    anterior = construir_datos(copia_datos, tmp_path)
    (copia_datos / "inventario.csv").unlink()
    with pytest.raises(construir.ArchivoFaltante):
        construir_datos(copia_datos, tmp_path)
    with duckdb.connect(str(anterior.base), read_only=True) as conexion:
        assert uno(conexion, "SELECT count(*) FROM inventario") == CONTEOS["inventario"]


def test_una_columna_renombrada_da_un_error_con_tipo(copia_datos, tmp_path):
    ruta = copia_datos / "inventario.csv"
    ruta.write_bytes(ruta.read_bytes().replace(b"punto_reorden", b"punto_de_reorden", 1))
    with pytest.raises(construir.ColumnasDistintas) as capturado:
        construir_datos(copia_datos, tmp_path)
    assert capturado.value.archivo == "inventario.csv"
    assert "punto_reorden" in str(capturado.value)
    assert "punto_de_reorden" in str(capturado.value)


@pytest.mark.parametrize(
    "original, alterada",
    [
        # Coma como separador decimal entre comillas: no se convierte a punto en silencio.
        (b"2026-06-01,T01,SKU-1001,52,1430.00,", b'2026-06-01,T01,SKU-1001,52,"1430,00",'),
        # Tres decimales: DECIMAL(12,2) los redondearía sin avisar.
        (b"2026-06-01,T01,SKU-1001,52,1430.00,", b"2026-06-01,T01,SKU-1001,52,1430.005,"),
        # Fecha con otro formato.
        (b"2026-06-01,T01,SKU-1001,52,1430.00,", b"01/06/2026,T01,SKU-1001,52,1430.00,"),
        # Coma sin comillas: una columna de más.
        (b"2026-06-01,T01,SKU-1001,52,1430.00,", b"2026-06-01,T01,SKU-1001,52,1430,00,"),
    ],
    ids=["coma-decimal", "tres-decimales", "fecha-dd-mm", "columna-de-mas"],
)
def test_un_valor_fuera_del_dialecto_da_un_error_con_tipo_que_nombra_el_archivo(copia_datos, tmp_path, original, alterada):
    ruta = copia_datos / "ventas.csv"
    contenido = ruta.read_bytes()
    assert original in contenido
    ruta.write_bytes(contenido.replace(original, alterada, 1))
    with pytest.raises(construir.CsvInvalido) as capturado:
        construir_datos(copia_datos, tmp_path)
    assert capturado.value.archivo == "ventas.csv"
    assert not isinstance(capturado.value, duckdb.Error)


def test_fin_de_linea_crlf_da_un_error_con_tipo_en_lugar_de_cero_filas(copia_datos, tmp_path):
    # Con new_line LF declarado, DuckDB 2.0.0 lee un archivo CRLF como 0 filas sin avisar.
    ruta = copia_datos / "tiendas.csv"
    ruta.write_bytes(ruta.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(construir.CsvInvalido) as capturado:
        construir_datos(copia_datos, tmp_path)
    assert capturado.value.archivo == "tiendas.csv"
    assert "CRLF" in str(capturado.value)


def test_una_venta_de_un_sku_que_no_esta_en_el_catalogo_da_un_error_con_tipo(copia_datos, tmp_path):
    ruta = copia_datos / "ventas.csv"
    ruta.write_bytes(ruta.read_bytes() + b"2026-08-31,T01,SKU-9999,1,10.00,0.00,Piso\n")
    with pytest.raises(construir.CsvInvalido) as capturado:
        construir_datos(copia_datos, tmp_path)
    assert capturado.value.archivo == "ventas.csv"
    assert "SKU-9999" in str(capturado.value)


def test_acepta_otra_carpeta_de_datos(copia_datos, tmp_path):
    # Las copias A a D (T5.6) son carpetas como esta: la construcción lee la que se le pasa.
    ruta = copia_datos / "ventas.csv"
    lineas = ruta.read_bytes().splitlines(keepends=True)
    ruta.write_bytes(b"".join(lineas[:-1]))
    resumen = construir_datos(copia_datos, tmp_path)
    assert resumen.filas["ventas"] == CONTEOS["ventas"] - 1


# --- Vistas ----------------------------------------------------------------------------------------


def test_v_ventas_dia_trae_tienda_formato_producto_y_unidad(con):
    fila = con.execute(
        "SELECT tienda, formato, producto, categoria, unidad_venta FROM v_ventas_dia "
        "WHERE sku = 'SKU-1023' AND tienda_id = 'T03' LIMIT 1"
    ).fetchone()
    assert fila == ("Tienda Anáhuac", "Mi Tienda", "Pechuga de pollo sin hueso kg", "Carnes", "kg")
    assert uno(con, "SELECT DISTINCT unidad_venta FROM v_ventas_dia WHERE sku = 'SKU-1004'") == "pieza"


def test_la_venta_neta_sale_de_la_definicion_de_supuestos(copia_datos, copia_config, tmp_path):
    ruta = copia_config / "supuestos.yaml"
    texto = ruta.read_text(encoding="utf-8")
    assert "incluir_devoluciones: true" in texto
    ruta.write_text(texto.replace("incluir_devoluciones: true", "incluir_devoluciones: false", 1), encoding="utf-8")
    resumen = construir_datos(copia_datos, tmp_path, dir_config=copia_config)
    with duckdb.connect(str(resumen.base), read_only=True) as conexion:
        assert uno(conexion, "SELECT sum(venta_neta_mxn) FROM v_ventas_dia WHERE en_venta_neta") == VENTA_SIN_DEVOLUCIONES
        # Las filas siguen todas: la definición filtra, no borra.
        assert uno(conexion, "SELECT count(*) FROM v_ventas_dia") == CONTEOS["ventas"]
        assert uno(conexion, "SELECT valor FROM supuestos WHERE clave = 'ventas.incluir_devoluciones'") == "false"


def test_v_inventario_marca_los_17_casos_bajo_el_punto_de_reorden(con):
    assert uno(con, "SELECT count(*) FROM v_inventario WHERE bajo_reorden") == 17
    assert uno(con, "SELECT DISTINCT fecha_corte FROM v_inventario") == dt.date(2026, 9, 1)


def test_v_tickets_marca_17_sin_cerrar_y_mide_la_antiguedad_contra_el_fin_del_corte(con):
    assert uno(con, "SELECT count(*) FROM v_tickets WHERE sin_cerrar") == 17
    assert uno(con, "SELECT antiguedad_dias FROM v_tickets WHERE ticket_id = 1258") == pytest.approx(9.65)
    assert uno(con, "SELECT count(*) FROM v_tickets WHERE sin_cerrar AND horas_solucion IS NOT NULL") == 0
    assert uno(con, "SELECT count(*) FROM v_tickets WHERE NOT sin_cerrar AND antiguedad_dias IS NOT NULL") == 0
    assert uno(con, "SELECT count(*) FROM v_tickets WHERE horas_solucion < 0") == 0


# --- Tablas auxiliares --------------------------------------------------------------------------------


def test_supuestos_guarda_cada_definicion_con_su_valor_en_json(con):
    assert uno(con, "SELECT valor FROM supuestos WHERE clave = 'fecha_referencia'") == '"2026-09-01"'
    assert json.loads(uno(con, "SELECT valor FROM supuestos WHERE clave = 'ventas.canales'")) == ["Piso", "Ecommerce"]
    assert uno(con, "SELECT valor FROM supuestos WHERE clave = 'inventario.operador_reorden'") == '"<"'
    assert uno(con, "SELECT count(*) FROM supuestos WHERE clave LIKE 'cortes.ventas.%'") == 5


def test_alias_resuelve_tiendas_ciudades_y_canales(con):
    assert uno(con, "SELECT valor FROM alias WHERE tipo = 'tienda' AND alias = 'cumbres'") == "T02"
    ciudad = con.execute("SELECT valor FROM alias WHERE tipo = 'ciudad' AND alias = 'monterrey' ORDER BY valor").fetchall()
    assert ciudad == [("T01",), ("T02",)]
    assert uno(con, "SELECT valor FROM alias WHERE tipo = 'canal' AND alias = 'en linea'") == "Ecommerce"
    assert uno(con, "SELECT valor FROM alias WHERE tipo = 'producto' AND alias = 'yogurt'") == "yogur"


def test_alias_no_manda_caja_al_canal_piso(con):
    # "caja" choca con "Pan de caja blanco 680 g" y con la categoría Cajas de los tickets.
    assert uno(con, "SELECT count(*) FROM alias WHERE alias = 'caja'") == 0


def test_alias_con_una_tienda_que_no_existe_da_un_error_con_tipo(copia_datos, copia_config, tmp_path):
    ruta = copia_config / "alias.yaml"
    alias = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    alias["tiendas"]["T09"] = ["tienda fantasma"]
    ruta.write_text(yaml.safe_dump(alias, allow_unicode=True), encoding="utf-8")
    with pytest.raises(construir.AliasInvalido) as capturado:
        construir_datos(copia_datos, tmp_path, dir_config=copia_config)
    assert "T09" in str(capturado.value)


def test_alias_repetido_en_dos_tiendas_da_un_error_con_tipo(copia_datos, copia_config, tmp_path):
    ruta = copia_config / "alias.yaml"
    alias = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    alias["tiendas"]["T04"].append("cumbres")
    ruta.write_text(yaml.safe_dump(alias, allow_unicode=True), encoding="utf-8")
    with pytest.raises(construir.AliasInvalido) as capturado:
        construir_datos(copia_datos, tmp_path, dir_config=copia_config)
    assert "cumbres" in str(capturado.value)


def test_cobertura_da_filas_columnas_y_periodo_de_cada_fuente(con):
    filas = con.execute(
        "SELECT fuente, archivo, filas, columnas, desde, hasta FROM cobertura WHERE tienda_id IS NULL ORDER BY fuente"
    ).fetchall()
    assert filas == [
        ("catalogo", "catalogo_productos.csv", 121, 13, None, None),
        ("inventario", "inventario.csv", 480, 6, dt.date(2026, 9, 1), dt.date(2026, 9, 1)),
        ("tickets", "tickets_mesa_servicio.csv", 285, 9, dt.date(2026, 6, 2), dt.date(2026, 8, 31)),
        ("tiendas", "tiendas.csv", 4, 7, None, None),
        ("ventas", "ventas.csv", 41_859, 7, dt.date(2026, 6, 1), dt.date(2026, 8, 31)),
    ]


def test_cobertura_dice_que_solo_los_supermercados_venden_en_linea(con):
    canales = con.execute(
        "SELECT tienda_id, canal FROM cobertura WHERE fuente = 'ventas' AND tienda_id IS NOT NULL ORDER BY tienda_id, canal"
    ).fetchall()
    assert canales == [
        ("T01", "Ecommerce"), ("T01", "Piso"),
        ("T02", "Ecommerce"), ("T02", "Piso"),
        ("T03", "Piso"),
        ("T04", "Piso"),
    ]
    assert uno(con, "SELECT sum(filas) FROM cobertura WHERE fuente = 'ventas' AND tienda_id IS NOT NULL") == CONTEOS["ventas"]


def test_la_base_completa_trae_los_62_fragmentos(con, base_construida):
    assert uno(con, "SELECT count(*) FROM fragmentos") == 62
    assert sum(base_construida.politicas.conteos.values()) == 62


# --- Índice de texto y línea de comandos ----------------------------------------------------------


def test_construir_deja_pasar_extension_no_disponible_sin_crear_la_base(copia_datos, tmp_path, monkeypatch):
    def sin_red(con, instalar=True):
        raise construir.ExtensionNoDisponible("fts", "INSTALL", "IO Error: sin red")

    monkeypatch.setattr(construir, "cargar_fts", sin_red)
    with pytest.raises(construir.ExtensionNoDisponible):
        construir.construir(copia_datos, tmp_path / "copia.duckdb", politicas=False)
    assert not (tmp_path / "copia.duckdb").exists()


def test_la_linea_de_comandos_muestra_extension_no_disponible(copia_datos, tmp_path, monkeypatch, capsys):
    def sin_red(con, instalar=True):
        raise construir.ExtensionNoDisponible("fts", "INSTALL", "IO Error: sin red")

    monkeypatch.setattr(construir, "cargar_fts", sin_red)
    codigo = construir.main(["--datos", str(copia_datos), "--base", str(tmp_path / "cli.duckdb")])
    salida = capsys.readouterr()
    assert codigo == 1
    assert "No se pudo instalar la extensión fts" in salida.err
    assert "Traceback" not in salida.err


def test_la_linea_de_comandos_reporta_filas_por_tabla(copia_datos, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(construir, "crear_indice_texto", lambda *argumentos: None)
    codigo = construir.main(["--datos", str(copia_datos), "--base", str(tmp_path / "cli.duckdb")])
    salida = capsys.readouterr().out
    assert codigo == 0
    assert "ventas: 41,859 filas" in salida
    assert "Total: 62 fragmentos" in salida


def test_la_linea_de_comandos_reporta_un_archivo_que_falta(copia_datos, tmp_path, capsys):
    (copia_datos / "ventas.csv").unlink()
    codigo = construir.main(["--datos", str(copia_datos), "--base", str(tmp_path / "cli.duckdb")])
    assert codigo == 1
    assert "ventas.csv" in capsys.readouterr().err
