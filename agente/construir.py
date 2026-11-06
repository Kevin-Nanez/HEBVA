"""Construcción de build/tienda.duckdb desde datos/ y politicas_md/ (T1.2 a T1.7).

Carga los CSV con tipos declarados, enmascara los datos personales, normaliza los títulos de los
tickets y les da su tema, genera los fragmentos de políticas con documento, sección y página, crea
el índice de texto y guarda el sha256 de las fuentes. asegurar_base() compara ese sha256 con los
archivos que hay en disco y reconstruye la base si algo cambió o si no existe.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import functools
import hashlib
import json
import os
import re
import sys
import threading
import time
import unicodedata
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import duckdb
import yaml
from pydantic import ValidationError

from agente import definiciones, extraer_politicas, fragmentos_politicas, privacidad, texto_tickets

RAIZ = Path(__file__).resolve().parent.parent
BASE = RAIZ / "build" / "tienda.duckdb"
DIR_DATOS = RAIZ / "datos"
DIR_CONFIG = RAIZ / "config"
DIR_PDF = RAIZ / "politicas"
DIR_POLITICAS_MD = RAIZ / "politicas_md"

EXTENSION_FTS = "fts"

ACCION_DEL_PASO = {"INSTALL": "instalar", "LOAD": "cargar"}


class ErrorConstruccion(RuntimeError):
    """Error con tipo de la construcción: dice qué falló sin soltar el traceback de DuckDB."""


class ExtensionNoDisponible(ErrorConstruccion):
    """Una extensión de DuckDB no se pudo instalar o cargar (T0.5).

    extension es su nombre, paso es INSTALL o LOAD y causa es la primera línea del mensaje de
    DuckDB. El error original queda en __cause__.
    """

    def __init__(self, extension: str, paso: str, causa: str):
        self.extension = extension
        self.paso = paso
        self.causa = causa
        super().__init__(
            f"No se pudo {ACCION_DEL_PASO[paso]} la extensión {extension} de DuckDB. No viene dentro del "
            "paquete duckdb: la primera construcción la descarga de extensions.duckdb.org y necesita red "
            "una vez. Revisa la conexión y que ningún proxy bloquee ese dominio, o copia la carpeta de "
            "extensiones de un equipo con red (ver el README, sección de fts). "
            f"Detalle de DuckDB: {causa}"
        )


def cargar_fts(con: duckdb.DuckDBPyConnection, instalar: bool = True) -> None:
    """Instala, si falta, y carga la extensión fts en la conexión (T0.5).

    fts no viene dentro del wheel de duckdb: el primer INSTALL la descarga de extensions.duckdb.org
    (unos 47 MB en disco) a ~/.duckdb/extensions/ y los siguientes usan esa copia sin tocar la red.
    Sin ella no existen PRAGMA create_fts_index ni la macro stem que usa match_bm25.

    Con instalar=False solo la carga y nunca sale a la red: es lo que debe usar la conexión de
    consulta. Se llama antes de enable_external_access=false, que bloquea el INSTALL y el LOAD.

    Lanza ExtensionNoDisponible, con el nombre de la extensión, si no se puede instalar o cargar.
    """
    if instalar:
        _ejecutar_paso(con.install_extension, "INSTALL")
    _ejecutar_paso(con.load_extension, "LOAD")


def _ejecutar_paso(accion: Callable[[str], None], paso: str) -> None:
    try:
        accion(EXTENSION_FTS)
    except duckdb.Error as error:
        raise ExtensionNoDisponible(EXTENSION_FTS, paso, str(error).partition("\n")[0]) from error


# --- Políticas (T1.5) -----------------------------------------------------------------------------

CAMPOS_VERIFICADOS = ("codigo", "titulo", "version", "vigencia", "area", "archivo")

TABLA_FRAGMENTOS = """
CREATE OR REPLACE TABLE fragmentos (
    id VARCHAR PRIMARY KEY,
    documento VARCHAR NOT NULL,
    titulo_documento VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    vigencia DATE NOT NULL,
    archivo VARCHAR NOT NULL,
    seccion VARCHAR NOT NULL,
    titulo_seccion VARCHAR NOT NULL,
    bloque VARCHAR,
    pagina INTEGER NOT NULL,
    pagina_fin INTEGER NOT NULL,
    orden INTEGER NOT NULL,
    subsecciones STRUCT(seccion VARCHAR, titulo VARCHAR, pagina INTEGER)[] NOT NULL,
    texto VARCHAR NOT NULL
)
"""

COLUMNAS_FRAGMENTOS = (
    "id", "documento", "titulo_documento", "version", "vigencia", "archivo", "seccion", "titulo_seccion",
    "bloque", "pagina", "pagina_fin", "orden", "subsecciones", "texto",
)


class PoliticaSinRevisar(ErrorConstruccion):
    """La copia revisada de politicas_md/ falta o no coincide con lo que se extrae del PDF (T1.5).

    documento es el código (MAN-OPS-007) y detalle dice qué difiere: el campo del encabezado o el
    primer renglón distinto, con el texto de los dos lados.
    """

    def __init__(self, documento: str, detalle: str):
        self.documento = documento
        self.detalle = detalle
        super().__init__(
            f"La copia revisada de {documento} en politicas_md/ no corresponde al PDF: {detalle}. "
            "Si el PDF cambió, vuelve a extraerlo con python -m agente.extraer_politicas, revisa cada "
            f"tabla contra el PDF y reemplaza el cuerpo de politicas_md/{documento}.md."
        )


@dataclass(frozen=True)
class ResumenPoliticas:
    """Fragmentos guardados por documento y huella sha256 de lo que quedó en la tabla."""

    conteos: dict[str, int]
    huella: str


def _primera_diferencia(esperado: str, revisado: str) -> str:
    lineas_pdf = esperado.splitlines()
    lineas_md = revisado.splitlines()
    for numero, (pdf, md) in enumerate(zip(lineas_pdf, lineas_md), 1):
        if pdf != md:
            return f"renglón {numero} del cuerpo; PDF: «{pdf}»; copia revisada: «{md}»"
    return f"el PDF da {len(lineas_pdf)} renglones y la copia revisada {len(lineas_md)}"


class PoliticaSospechosa(ErrorConstruccion):
    """Un fragmento de los documentos trae una orden al asistente (K08, K14): no se carga sin que una persona lo revise.

    Da solo el id de cada fragmento, para que el mensaje no copie la orden.
    """

    def __init__(self, fragmentos: list[str]):
        self.fragmentos = fragmentos
        super().__init__(
            f"Los fragmentos {', '.join(fragmentos)} de politicas_md/ traen una orden para el asistente. Revísalos con la "
            "persona responsable del documento antes de construir la base."
        )


def verificar_copia_revisada(documento: extraer_politicas.DocumentoExtraido, ruta_md: Path) -> str:
    """Devuelve el archivo revisado si su encabezado y su cuerpo coinciden con la extracción."""
    if not ruta_md.is_file():
        raise PoliticaSinRevisar(documento.codigo, f"falta el archivo {ruta_md.name}")
    texto = ruta_md.read_text(encoding="utf-8")
    try:
        encabezado, cuerpo = fragmentos_politicas.separar_encabezado(texto)
    except ValueError as error:
        raise PoliticaSinRevisar(documento.codigo, str(error)) from error
    metadatos = documento.metadatos()
    for campo in CAMPOS_VERIFICADOS:
        if str(encabezado.get(campo)) != metadatos[campo]:
            raise PoliticaSinRevisar(
                documento.codigo,
                f"el campo {campo} dice «{encabezado.get(campo)}» y el PDF «{metadatos[campo]}»",
            )
    if cuerpo != documento.markdown:
        raise PoliticaSinRevisar(documento.codigo, _primera_diferencia(documento.markdown, cuerpo))
    return texto


def construir_politicas(
    con: duckdb.DuckDBPyConnection, dir_pdf: Path = DIR_PDF, dir_md: Path = DIR_POLITICAS_MD
) -> ResumenPoliticas:
    """Extrae cada PDF, lo compara con su copia revisada y guarda los fragmentos en la tabla fragmentos.

    Reemplaza la tabla completa en cada corrida. Lanza PoliticaSinRevisar si una copia revisada
    falta o no coincide, y PoliticaSospechosa si un fragmento trae una orden al asistente, antes de
    tocar la tabla.
    """
    pdfs = sorted(Path(dir_pdf).glob("*.pdf"))
    if not pdfs:
        raise ErrorConstruccion(f"No hay PDF de políticas en {dir_pdf}")
    filas: list[dict] = []
    for pdf in pdfs:
        documento = extraer_politicas.extraer_pdf(pdf)
        texto = verificar_copia_revisada(documento, Path(dir_md) / f"{documento.codigo}.md")
        filas += [fragmentos_politicas.como_fila(f) for f in fragmentos_politicas.fragmentar(texto)]
    sospechosos = [fila["id"] for fila in filas if texto_tickets.es_sospechoso(fila["texto"])]
    if sospechosos:
        raise PoliticaSospechosa(sospechosos)
    con.execute(TABLA_FRAGMENTOS)
    marcadores = ", ".join("?" for _ in COLUMNAS_FRAGMENTOS)
    con.executemany(
        f"INSERT INTO fragmentos ({', '.join(COLUMNAS_FRAGMENTOS)}) VALUES ({marcadores})",
        [[fila[columna] for columna in COLUMNAS_FRAGMENTOS] for fila in filas],
    )
    return resumir_fragmentos(con)


def resumir_fragmentos(con: duckdb.DuckDBPyConnection) -> ResumenPoliticas:
    """Cuenta los fragmentos guardados por documento y calcula la huella de la tabla tal como quedó."""
    cursor = con.execute(f"SELECT {', '.join(COLUMNAS_FRAGMENTOS)} FROM fragmentos ORDER BY documento, orden")
    guardadas = [dict(zip(COLUMNAS_FRAGMENTOS, fila)) for fila in cursor.fetchall()]
    conteos: dict[str, int] = {}
    for fila in guardadas:
        conteos[fila["documento"]] = conteos.get(fila["documento"], 0) + 1
    return ResumenPoliticas(conteos=conteos, huella=fragmentos_politicas.huella(guardadas))


# --- Datos (T1.2, K04) -----------------------------------------------------------------------------

TIPO_MONTO = "DECIMAL(12,2)"

# Monto del diccionario: punto decimal, a lo más dos decimales y sin separador de miles. Se valida
# antes de convertir porque CAST a DECIMAL(12,2) redondea 1430.005 sin avisar y una coma decimal
# entre comillas («1430,00») no debe convertirse a punto en silencio.
PATRON_MONTO = r"-?[0-9]{1,10}([.][0-9]{1,2})?"
# Función de la conexión que enmascara el texto libre al cargarlo (T1.4). Solo existe durante el
# INSERT: no queda en la base.
FUNCION_ENMASCARAR = "enmascarar_datos_personales"

# Dialecto explícito (K04, RFC 4180 §2): coma, comillas dobles, comilla duplicada como escape,
# encabezado y fin de línea LF. Fechas y horas en el formato del diccionario.
DIALECTO = (
    "header = true, auto_detect = false, strict_mode = true, delim = ',', quote = '\"', "
    "escape = '\"', new_line = '\\n', encoding = 'utf-8', "
    "dateformat = '%Y-%m-%d', timestampformat = '%Y-%m-%d %H:%M'"
)


@dataclass(frozen=True)
class Columna:
    """Columna del diccionario. enmascarar=True marca el texto libre que puede traer datos personales.

    Esas columnas pasan por agente/privacidad.py dentro del INSERT (K06): el valor original nunca
    se escribe en la base.
    """

    nombre: str
    tipo: str
    nula: bool = False
    enmascarar: bool = False


@dataclass(frozen=True)
class Fuente:
    """Un CSV de datos/ con sus columnas en el orden y con el tipo del diccionario de datos."""

    archivo: str
    columnas: tuple[Columna, ...]
    llave: tuple[str, ...] = ()

    def nombres(self) -> tuple[str, ...]:
        return tuple(columna.nombre for columna in self.columnas)


# Tabla de la base -> archivo y columnas del diccionario. Una columna con nula=True es la que el
# diccionario describe "o vacío". ventas no lleva llave: las 409 combinaciones repetidas de fecha,
# tienda, SKU y canal son una venta y una devolución del mismo día, y las dos filas se conservan.
FUENTES: dict[str, Fuente] = {
    "tiendas": Fuente(
        "tiendas.csv",
        (
            Columna("tienda_id", "VARCHAR"),
            Columna("nombre", "VARCHAR"),
            Columna("ciudad", "VARCHAR"),
            Columna("estado", "VARCHAR"),
            Columna("formato", "VARCHAR"),
            Columna("m2", "INTEGER"),
            Columna("fecha_apertura", "DATE"),
        ),
        llave=("tienda_id",),
    ),
    "catalogo": Fuente(
        "catalogo_productos.csv",
        (
            Columna("sku", "VARCHAR"),
            Columna("nombre", "VARCHAR"),
            Columna("categoria", "VARCHAR"),
            Columna("subcategoria", "VARCHAR"),
            Columna("perecedero", "BOOLEAN"),
            Columna("vida_util_dias", "INTEGER"),
            Columna("precio_lista_mxn", TIPO_MONTO),
            Columna("costo_mxn", TIPO_MONTO),
            Columna("proveedor_id", "VARCHAR"),
            Columna("estatus", "VARCHAR"),
            Columna("fecha_alta", "DATE"),
            Columna("fecha_baja", "DATE", nula=True),
            Columna("sku_sustituto", "VARCHAR", nula=True),
        ),
        llave=("sku",),
    ),
    "ventas": Fuente(
        "ventas.csv",
        (
            Columna("fecha", "DATE"),
            Columna("tienda_id", "VARCHAR"),
            Columna("sku", "VARCHAR"),
            Columna("unidades", "INTEGER"),
            Columna("venta_neta_mxn", TIPO_MONTO),
            Columna("descuento_mxn", TIPO_MONTO),
            Columna("canal", "VARCHAR"),
        ),
    ),
    "inventario": Fuente(
        "inventario.csv",
        (
            Columna("tienda_id", "VARCHAR"),
            Columna("sku", "VARCHAR"),
            Columna("existencia", "INTEGER"),
            Columna("punto_reorden", "INTEGER"),
            Columna("fecha_ultimo_conteo", "DATE"),
            Columna("fecha_caducidad_lote_proximo", "DATE", nula=True),
        ),
        llave=("tienda_id", "sku"),
    ),
    "tickets": Fuente(
        "tickets_mesa_servicio.csv",
        (
            Columna("ticket_id", "INTEGER"),
            Columna("fecha_creacion", "TIMESTAMP"),
            Columna("tienda_id", "VARCHAR"),
            Columna("categoria", "VARCHAR"),
            Columna("prioridad", "VARCHAR"),
            Columna("estado", "VARCHAR"),
            Columna("titulo", "VARCHAR", enmascarar=True),
            Columna("descripcion", "VARCHAR", enmascarar=True),
            Columna("fecha_cierre", "TIMESTAMP", nula=True),
        ),
        llave=("ticket_id",),
    ),
}


@dataclass(frozen=True)
class Relacion:
    """Llave foránea de la sección Relaciones del diccionario: tabla.columna -> referida.columna."""

    tabla: str
    columna: str
    referida: str
    columna_referida: str


RELACIONES = (
    Relacion("ventas", "tienda_id", "tiendas", "tienda_id"),
    Relacion("ventas", "sku", "catalogo", "sku"),
    Relacion("inventario", "tienda_id", "tiendas", "tienda_id"),
    Relacion("inventario", "sku", "catalogo", "sku"),
    Relacion("tickets", "tienda_id", "tiendas", "tienda_id"),
    Relacion("catalogo", "sku_sustituto", "catalogo", "sku"),
)


class ArchivoFaltante(ErrorConstruccion):
    """Una o más fuentes no están con su nombre: un CSV de datos/ o un PDF de politicas/ (K04, copia B).

    archivos son los nombres que faltan y carpeta la que se leyó. Se lanza antes de tocar la base,
    también cuando ya había una anterior: una base vieja no debe contestar por un archivo que ya
    no está. Quien responde al usuario usa archivos para decir que no pudo consultarlos, sin cifra.
    """

    def __init__(self, archivos: tuple[str, ...], carpeta: Path):
        self.archivos = archivos
        self.carpeta = carpeta
        super().__init__(
            f"No se encontró {', '.join(archivos)} en {carpeta}. La base no se construyó y la anterior, "
            "si había, sigue igual. El archivo debe llamarse exactamente como en la entrega original."
        )


class CsvInvalido(ErrorConstruccion):
    """Un CSV no cumple el dialecto, los tipos o las relaciones del diccionario (K04).

    archivo es el nombre del CSV y detalle dice qué falló, sin copiar la línea original (puede
    traer datos personales de un ticket).
    """

    def __init__(self, archivo: str, detalle: str):
        self.archivo = archivo
        self.detalle = detalle
        super().__init__(
            f"No se pudo cargar {archivo}: {detalle}. La base no se construyó y la anterior, si había, "
            "sigue igual."
        )


class ColumnasDistintas(CsvInvalido):
    """El encabezado del CSV no trae las columnas del diccionario, en su orden."""

    def __init__(self, archivo: str, esperadas: tuple[str, ...], encontradas: tuple[str, ...]):
        self.esperadas = esperadas
        self.encontradas = encontradas
        faltan = [nombre for nombre in esperadas if nombre not in encontradas]
        sobran = [nombre for nombre in encontradas if nombre not in esperadas]
        super().__init__(
            archivo,
            f"el encabezado no coincide con el diccionario de datos (faltan: {', '.join(faltan) or 'ninguna'}; "
            f"sobran: {', '.join(sobran) or 'ninguna'}; se esperaban, en orden: {', '.join(esperadas)})",
        )


class AliasInvalido(ErrorConstruccion):
    """config/alias.yaml nombra una tienda o un canal que no existe, o repite un alias."""

    def __init__(self, detalle: str):
        self.detalle = detalle
        super().__init__(f"config/alias.yaml no es válido: {detalle}.")


class ConfiguracionInvalida(ErrorConstruccion):
    """config/supuestos.yaml no pasa la validación de agente/definiciones.py."""


def verificar_fuentes(dir_datos: Path) -> dict[str, Path]:
    """Ruta de cada CSV del diccionario; ArchivoFaltante con todos los que falten."""
    rutas = {tabla: Path(dir_datos) / fuente.archivo for tabla, fuente in FUENTES.items()}
    faltan = tuple(FUENTES[tabla].archivo for tabla, ruta in rutas.items() if not ruta.is_file())
    if faltan:
        raise ArchivoFaltante(faltan, Path(dir_datos))
    return rutas


def validar_archivo(ruta: Path, fuente: Fuente) -> None:
    """Fin de línea LF y encabezado igual al diccionario, antes de que DuckDB lea el archivo.

    Con new_line LF declarado, DuckDB 2.0.0 lee un archivo CRLF como 0 filas sin dar error; por eso
    el retorno de carro se rechaza aquí.
    """
    contenido = ruta.read_bytes()
    if b"\r" in contenido:
        raise CsvInvalido(fuente.archivo, "tiene fin de línea CRLF o retornos de carro y el dialecto es LF; guárdalo con fin de línea LF")
    try:
        primera_linea = contenido.split(b"\n", 1)[0].decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CsvInvalido(fuente.archivo, "el encabezado no está en UTF-8") from error
    encontradas = tuple(next(csv.reader([primera_linea]), []))
    if encontradas != fuente.nombres():
        raise ColumnasDistintas(fuente.archivo, fuente.nombres(), encontradas)


def _tipo_al_leer(columna: Columna) -> str:
    """Los montos se leen como texto para validar su formato antes de convertirlos."""
    if columna.tipo == TIPO_MONTO:
        return "VARCHAR"
    return columna.tipo


def _expresion(columna: Columna) -> str:
    if columna.enmascarar:
        return f"{FUNCION_ENMASCARAR}({columna.nombre})"
    if columna.tipo != TIPO_MONTO:
        return columna.nombre
    nombre = columna.nombre
    return (
        f"CASE WHEN {nombre} IS NULL OR regexp_full_match({nombre}, '{PATRON_MONTO}') "
        f"THEN CAST({nombre} AS {TIPO_MONTO}) "
        f"ELSE error('la columna {nombre} trae «' || {nombre} || '», que no es un monto con punto decimal "
        "y a lo más dos decimales') END"
    )


def _sql_crear_tabla(tabla: str, fuente: Fuente) -> str:
    columnas = [f"{c.nombre} {c.tipo}" if c.nula else f"{c.nombre} {c.tipo} NOT NULL" for c in fuente.columnas]
    if fuente.llave:
        columnas.append(f"PRIMARY KEY ({', '.join(fuente.llave)})")
    return f"CREATE TABLE {tabla} ({', '.join(columnas)})"


def _detalle_duckdb(error: duckdb.Error) -> str:
    """El mensaje de DuckDB sin la línea original del CSV, que puede traer datos personales."""
    renglones = [r.strip() for r in str(error).splitlines() if r.strip()]
    utiles = [r for r in renglones if not r.startswith("Original Line")]
    return " ".join(utiles[:2])


def cargar_csv(con: duckdb.DuckDBPyConnection, tabla: str, ruta: Path) -> int:
    """Crea la tabla con los tipos del diccionario y carga el CSV con el dialecto explícito."""
    fuente = FUENTES[tabla]
    validar_archivo(ruta, fuente)
    tipos = ", ".join(f"'{c.nombre}': '{_tipo_al_leer(c)}'" for c in fuente.columnas)
    seleccion = ", ".join(_expresion(c) for c in fuente.columnas)
    enmascara = any(c.enmascarar for c in fuente.columnas)
    if enmascara:
        # DuckDB no registra ni quita una función con un resultado abierto en la conexión; por eso
        # los resultados de este paso se leen con fetchall().
        con.create_function(FUNCION_ENMASCARAR, privacidad.enmascarar, ["VARCHAR"], "VARCHAR")
    try:
        con.execute(_sql_crear_tabla(tabla, fuente))
        con.execute(
            f"INSERT INTO {tabla} SELECT {seleccion} FROM read_csv(?, columns = {{{tipos}}}, {DIALECTO})",
            [str(ruta)],
        ).fetchall()
    except duckdb.Error as error:
        raise CsvInvalido(fuente.archivo, _detalle_duckdb(error)) from error
    finally:
        if enmascara:
            con.remove_function(FUNCION_ENMASCARAR)
    filas = con.execute(f"SELECT count(*) FROM {tabla}").fetchall()[0][0]
    if filas == 0:
        raise CsvInvalido(fuente.archivo, "no trae filas después del encabezado")
    return filas


def verificar_relaciones(con: duckdb.DuckDBPyConnection) -> None:
    """Cada llave foránea del diccionario apunta a una fila que existe."""
    for relacion in RELACIONES:
        huerfanos = [
            fila[0]
            for fila in con.execute(
                f"SELECT DISTINCT a.{relacion.columna} FROM {relacion.tabla} a "
                f"ANTI JOIN {relacion.referida} b ON a.{relacion.columna} = b.{relacion.columna_referida} "
                f"WHERE a.{relacion.columna} IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]
        if huerfanos:
            raise CsvInvalido(
                FUENTES[relacion.tabla].archivo,
                f"{len(huerfanos)} valores de {relacion.columna} no están en {FUENTES[relacion.referida].archivo} "
                f"({', '.join(map(str, huerfanos[:5]))})",
            )


def cargar_datos(con: duckdb.DuckDBPyConnection, rutas: dict[str, Path]) -> dict[str, int]:
    """Carga los 5 CSV en sus tablas y devuelve las filas de cada una."""
    filas = {tabla: cargar_csv(con, tabla, rutas[tabla]) for tabla in FUENTES}
    verificar_relaciones(con)
    return filas


# --- Vistas, supuestos, alias y cobertura (T1.2) ---------------------------------------------------


def crear_unidades_venta(con: duckdb.DuckDBPyConnection, s: definiciones.Supuestos) -> None:
    """Unidad de venta de cada SKU (kg o pieza) según la regla de config/supuestos.yaml.

    Se calcula en Python porque el patrón usa lookbehind, que las expresiones de DuckDB (RE2) no
    aceptan.
    """
    productos = con.execute("SELECT sku, nombre FROM catalogo").fetchall()
    con.execute("CREATE TABLE catalogo_unidad (sku VARCHAR PRIMARY KEY, unidad_venta VARCHAR NOT NULL)")
    con.executemany("INSERT INTO catalogo_unidad VALUES (?, ?)", [[sku, s.unidad_venta.de(nombre)] for sku, nombre in productos])


def crear_vistas(con: duckdb.DuckDBPyConnection, s: definiciones.Supuestos) -> None:
    """v_ventas_dia, v_inventario y v_tickets, con las condiciones que da agente/definiciones.py.

    Las vistas no agregan ni deduplican: una fila por fila del CSV. Las fechas de corte entran
    como literales ISO de objetos date, nunca como texto libre del YAML.

    v_tickets trae titulo_normalizado y tema de la tabla tickets_normalizados (T1.3). Lo que más
    falla se cuenta por titulo_normalizado, no por tema: un tema suma varios títulos y cambiaría
    el ranking.
    """
    con.execute(f"""
        CREATE VIEW v_ventas_dia AS
        SELECT v.fecha, v.tienda_id, t.nombre AS tienda, t.formato, v.sku, c.nombre AS producto,
               c.categoria, c.subcategoria, u.unidad_venta, c.sku_sustituto, v.canal, v.unidades,
               v.venta_neta_mxn, v.descuento_mxn,
               {s.ventas.condicion_devolucion('v')} AS es_devolucion,
               {s.ventas.condicion('v')} AS en_venta_neta
        FROM ventas v
        LEFT JOIN tiendas t ON t.tienda_id = v.tienda_id
        LEFT JOIN catalogo c ON c.sku = v.sku
        LEFT JOIN catalogo_unidad u ON u.sku = v.sku
    """)
    con.execute(f"""
        CREATE VIEW v_inventario AS
        SELECT i.tienda_id, t.nombre AS tienda, t.formato, i.sku, c.nombre AS producto, c.categoria,
               c.perecedero, u.unidad_venta, c.estatus, i.existencia, i.punto_reorden,
               {s.inventario.condicion('i')} AS bajo_reorden,
               i.fecha_ultimo_conteo, i.fecha_caducidad_lote_proximo,
               DATE '{s.cortes.inventario.fecha.isoformat()}' AS fecha_corte
        FROM inventario i
        LEFT JOIN tiendas t ON t.tienda_id = i.tienda_id
        LEFT JOIN catalogo c ON c.sku = i.sku
        LEFT JOIN catalogo_unidad u ON u.sku = i.sku
    """)
    fin_corte = s.fin_corte_tickets().isoformat(sep=" ")
    con.execute(f"""
        CREATE VIEW v_tickets AS
        SELECT k.ticket_id, k.fecha_creacion, k.tienda_id, t.nombre AS tienda, t.formato, k.categoria,
               k.prioridad, k.estado, k.titulo, n.titulo_normalizado, n.tema, k.descripcion, k.fecha_cierre,
               {s.tickets.condicion_sin_cerrar('k')} AS sin_cerrar,
               CASE WHEN {s.tickets.condicion_cerrado('k')}
                    THEN date_diff('second', k.fecha_creacion, k.fecha_cierre) / 3600 END AS horas_solucion,
               CASE WHEN {s.tickets.condicion_sin_cerrar('k')}
                    THEN round(date_diff('second', k.fecha_creacion, TIMESTAMP '{fin_corte}') / 86400,
                               {int(s.tickets.antiguedad_decimales)}) END AS antiguedad_dias
        FROM tickets k
        LEFT JOIN tiendas t ON t.tienda_id = k.tienda_id
        LEFT JOIN tickets_normalizados n ON n.ticket_id = k.ticket_id
    """)


def _hojas(valor: object, prefijo: str) -> list[tuple[str, str]]:
    """Aplana el YAML a pares (clave con puntos, valor en JSON); las listas quedan enteras."""
    if not isinstance(valor, dict) or not valor:
        return [(prefijo, json.dumps(valor, ensure_ascii=False))]
    hojas: list[tuple[str, str]] = []
    for clave, hijo in valor.items():
        hojas += _hojas(hijo, f"{prefijo}.{clave}" if prefijo else str(clave))
    return hojas


def crear_supuestos(con: duckdb.DuckDBPyConnection, s: definiciones.Supuestos) -> None:
    """Tabla supuestos: cada definición de config/supuestos.yaml ya validada, con su valor en JSON."""
    con.execute("CREATE TABLE supuestos (clave VARCHAR PRIMARY KEY, valor JSON NOT NULL)")
    con.executemany("INSERT INTO supuestos VALUES (?, ?)", _hojas(s.model_dump(mode="json"), ""))


def normalizar_alias(texto: str) -> str:
    """Minúsculas y sin acentos, como se comparan los alias (config/alias.yaml)."""
    descompuesto = unicodedata.normalize("NFD", texto.strip().lower())
    return "".join(letra for letra in descompuesto if unicodedata.category(letra) != "Mn")


SECCIONES_ALIAS = ("tiendas", "ciudades_ambiguas", "productos", "canales")


def _validar_alias_normalizado(alias: str) -> None:
    if alias != normalizar_alias(alias):
        raise AliasInvalido(f"«{alias}» debe ir en minúsculas y sin acentos, como se compara")


def _filas_lugares(alias: dict, tiendas: set[str]) -> list[tuple[str, str, str]]:
    filas: list[tuple[str, str, str]] = []
    destino: dict[str, str] = {}
    for tienda_id, nombres in alias["tiendas"].items():
        if tienda_id not in tiendas:
            raise AliasInvalido(f"la tienda {tienda_id} no está en tiendas.csv")
        for nombre in nombres:
            _validar_alias_normalizado(nombre)
            if destino.get(nombre, tienda_id) != tienda_id:
                raise AliasInvalido(f"«{nombre}» apunta a {destino[nombre]} y a {tienda_id}")
            destino[nombre] = tienda_id
            filas.append(("tienda", nombre, tienda_id))
    for ciudad, candidatas in alias["ciudades_ambiguas"].items():
        _validar_alias_normalizado(ciudad)
        if ciudad in destino:
            raise AliasInvalido(f"«{ciudad}» es alias de {destino[ciudad]} y también ciudad ambigua")
        desconocidas = sorted(set(candidatas) - tiendas)
        if desconocidas:
            raise AliasInvalido(f"la ciudad «{ciudad}» nombra tiendas que no están en tiendas.csv: {', '.join(desconocidas)}")
        if len(set(candidatas)) < 2:
            raise AliasInvalido(f"la ciudad «{ciudad}» tiene una sola tienda; va como alias de esa tienda")
        filas += [("ciudad", ciudad, tienda_id) for tienda_id in candidatas]
    return filas


def _filas_canales(alias: dict, canales: list[str]) -> list[tuple[str, str, str]]:
    filas: list[tuple[str, str, str]] = []
    destino: dict[str, str] = {}
    for canal, nombres in alias["canales"].items():
        if canal not in canales:
            raise AliasInvalido(f"el canal {canal} no está en ventas.canales de config/supuestos.yaml")
        for nombre in nombres:
            _validar_alias_normalizado(nombre)
            if destino.get(nombre, canal) != canal:
                raise AliasInvalido(f"«{nombre}» apunta a {destino[nombre]} y a {canal}")
            destino[nombre] = canal
            filas.append(("canal", nombre, canal))
    return filas


def filas_alias(alias: dict, tiendas: set[str], canales: list[str]) -> list[tuple[str, str, str]]:
    """Filas (tipo, alias, valor) de config/alias.yaml, validadas contra tiendas.csv y los supuestos."""
    desconocidas = sorted(set(alias) - set(SECCIONES_ALIAS))
    faltantes = [seccion for seccion in SECCIONES_ALIAS if seccion not in alias]
    if desconocidas or faltantes:
        raise AliasInvalido(
            f"las secciones deben ser {', '.join(SECCIONES_ALIAS)} "
            f"(sobran: {', '.join(desconocidas) or 'ninguna'}; faltan: {', '.join(faltantes) or 'ninguna'})"
        )
    productos = []
    for original, cambio in alias["productos"].items():
        _validar_alias_normalizado(original)
        productos.append(("producto", original, cambio or ""))
    return _filas_lugares(alias, tiendas) + productos + _filas_canales(alias, canales)


def crear_alias(con: duckdb.DuckDBPyConnection, alias: dict, s: definiciones.Supuestos) -> None:
    """Tabla alias: nombres coloquiales de tiendas, ciudades, productos y canales (T2.8 los usa)."""
    tiendas = {fila[0] for fila in con.execute("SELECT tienda_id FROM tiendas").fetchall()}
    filas = filas_alias(alias, tiendas, s.ventas.canales)
    con.execute(
        "CREATE TABLE alias (tipo VARCHAR NOT NULL, alias VARCHAR NOT NULL, valor VARCHAR NOT NULL, "
        "PRIMARY KEY (tipo, alias, valor))"
    )
    con.executemany("INSERT INTO alias VALUES (?, ?, ?)", filas)


def _periodo(con: duckdb.DuckDBPyConnection, tabla: str, s: definiciones.Supuestos) -> tuple[dt.date | None, dt.date | None]:
    """Primer y último día con datos de la fuente; el inventario es una foto al día de su corte."""
    if tabla == "ventas":
        return con.execute("SELECT min(fecha), max(fecha) FROM ventas").fetchone()
    if tabla == "tickets":
        return con.execute("SELECT min(fecha_creacion)::DATE, max(fecha_creacion)::DATE FROM tickets").fetchone()
    if tabla == "inventario":
        return s.cortes.inventario.fecha, s.cortes.inventario.fecha
    return None, None


def crear_cobertura(con: duckdb.DuckDBPyConnection, filas: dict[str, int], s: definiciones.Supuestos) -> None:
    """Manifiesto de cobertura: qué hay en cada fuente y qué canal vende cada tienda.

    Una fila por CSV (tienda_id y canal nulos) con filas, columnas y periodo, y una por tienda y
    canal de ventas. Una combinación que no aparece no tiene ventas: es "sin ese canal".
    """
    con.execute(
        "CREATE TABLE cobertura (fuente VARCHAR NOT NULL, archivo VARCHAR NOT NULL, tienda_id VARCHAR, "
        "canal VARCHAR, filas BIGINT NOT NULL, columnas INTEGER, desde DATE, hasta DATE)"
    )
    for tabla, fuente in FUENTES.items():
        desde, hasta = _periodo(con, tabla, s)
        con.execute(
            "INSERT INTO cobertura VALUES (?, ?, NULL, NULL, ?, ?, ?, ?)",
            [tabla, fuente.archivo, filas[tabla], len(fuente.columnas), desde, hasta],
        )
    con.execute(
        "INSERT INTO cobertura SELECT 'ventas', ?, tienda_id, canal, count(*), NULL, min(fecha), max(fecha) "
        "FROM ventas GROUP BY tienda_id, canal",
        [FUENTES["ventas"].archivo],
    )


# --- Títulos normalizados y temas de los tickets (T1.3) --------------------------------------------


class TemasInvalidos(ErrorConstruccion):
    """config/temas_tickets.csv falta o no cumple el formato (T1.3).

    detalle dice qué falló y, si es una fila, en qué renglón. Se lanza antes de que la base nueva
    reemplace a la anterior.
    """

    def __init__(self, detalle: str):
        self.detalle = detalle
        super().__init__(
            f"config/{texto_tickets.ARCHIVO_TEMAS} no es válido: {detalle}. La base no se construyó y la "
            "anterior, si había, sigue igual."
        )


def cargar_temas_tickets(dir_config: Path) -> dict[str, str]:
    """Título normalizado -> tema de config/temas_tickets.csv, validado por agente/texto_tickets.py."""
    ruta = Path(dir_config) / texto_tickets.ARCHIVO_TEMAS
    try:
        return texto_tickets.leer_temas(ruta)
    except FileNotFoundError as error:
        raise TemasInvalidos(f"falta el archivo {ruta.name} en {ruta.parent}") from error
    except (OSError, ValueError) as error:
        raise TemasInvalidos(str(error)) from error


def crear_tickets_normalizados(con: duckdb.DuckDBPyConnection, dir_config: Path) -> None:
    """Tabla tickets_normalizados: título normalizado y tema de cada ticket, calculados una sola vez.

    Parte de tickets.titulo, que ya pasó por el enmascarado de agente/privacidad.py, así que ningún
    dato personal llega a la columna normalizada. Va en una tabla aparte porque tickets conserva las
    9 columnas del diccionario de datos; v_tickets las une. Un título que el archivo de temas no
    conoce (un dato nuevo, como el ticket que agrega la copia D) queda en TEMA_SIN_CLASIFICAR y la
    construcción sigue con un aviso; el aviso da la cuenta y no los títulos, que son texto libre.
    """
    temas = cargar_temas_tickets(dir_config)
    filas = []
    sin_tema = set()
    for ticket_id, titulo in con.execute("SELECT ticket_id, titulo FROM tickets ORDER BY ticket_id").fetchall():
        normalizado = texto_tickets.normalizar_titulo(titulo)
        if normalizado not in temas:
            sin_tema.add(normalizado)
        filas.append((ticket_id, normalizado, temas.get(normalizado, texto_tickets.TEMA_SIN_CLASIFICAR)))
    con.execute(
        "CREATE TABLE tickets_normalizados (ticket_id INTEGER PRIMARY KEY, "
        "titulo_normalizado VARCHAR NOT NULL, tema VARCHAR NOT NULL)"
    )
    con.executemany("INSERT INTO tickets_normalizados VALUES (?, ?, ?)", filas)
    if sin_tema:
        warnings.warn(
            f"{len(sin_tema)} título(s) de tickets no están en config/{texto_tickets.ARCHIVO_TEMAS} y sus "
            f"tickets quedan en «{texto_tickets.TEMA_SIN_CLASIFICAR}». Agrégalos al archivo; para verlos: "
            f"SELECT DISTINCT titulo_normalizado FROM v_tickets WHERE tema = '{texto_tickets.TEMA_SIN_CLASIFICAR}'",
            stacklevel=2,
        )


# --- Índice de texto (T1.6) --------------------------------------------------------------------------

ARCHIVO_PALABRAS_VACIAS = "palabras_vacias_es.txt"
TABLA_PALABRAS_VACIAS = "palabras_vacias"
# PRAGMA create_fts_index crea, junto a la tabla indexada, el esquema fts_main_<tabla> con las tablas
# del índice y la macro match_bm25. Las consultas (agente/consultas.py) lo buscan por este nombre.
TABLA_INDEXADA = "fragmentos"
ESQUEMA_INDICE = f"fts_main_{TABLA_INDEXADA}"
# Lo que separa dos palabras: todo lo que no sea una letra a-z, después de quitar los acentos y bajar
# a minúsculas. Sin esto DuckDB no separa ¿ ¡ « » ni °: "¿Procede?" queda como "¿procede" y la consulta
# "procede" no lo encuentra (con las políticas de la entrega, 81 de los 1,148 términos del primer
# índice arrastraban un signo). Los dígitos ya se ignoraban; las cifras se buscan por la palabra que
# las acompaña.
IGNORAR_EN_INDICE = "[^a-z]+"
# Una palabra vacía válida. El índice compara con ella cada palabra ya sin acentos y en minúsculas, así
# que una entrada con acento, mayúsculas, dígitos o signos nunca coincidiría.
PATRON_PALABRA_VACIA = re.compile(r"[a-z]+")


class PalabrasVaciasInvalidas(ErrorConstruccion):
    """config/palabras_vacias_es.txt falta, no trae palabras o trae entradas que el índice no reconocería (T1.6).

    ruta es el archivo leído y detalle dice qué falló, con las entradas que no sirven. Se lanza antes
    de que la base nueva reemplace a la anterior.
    """

    def __init__(self, ruta: Path, detalle: str):
        self.ruta = ruta
        self.detalle = detalle
        super().__init__(f"{ruta} no es válido: {detalle}. La base no se construyó y la anterior, si había, sigue igual.")


def leer_palabras_vacias(ruta: Path) -> list[str]:
    """Palabras del archivo, separadas por espacios; los renglones que empiezan con # son comentarios.

    Lanza PalabrasVaciasInvalidas si el archivo no se puede leer, no trae ninguna palabra o trae
    entradas que no son letras a-z en minúsculas: con esas el índice quedaría con la lista a medias
    sin avisar.
    """
    ruta = Path(ruta)
    try:
        renglones = ruta.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise PalabrasVaciasInvalidas(ruta, "no se pudo leer el archivo como texto UTF-8") from error
    palabras = [palabra for renglon in renglones if not renglon.lstrip().startswith("#") for palabra in renglon.split()]
    if not palabras:
        raise PalabrasVaciasInvalidas(ruta, "no trae ninguna palabra")
    invalidas = [palabra for palabra in dict.fromkeys(palabras) if not PATRON_PALABRA_VACIA.fullmatch(palabra)]
    if invalidas:
        raise PalabrasVaciasInvalidas(
            ruta,
            f"estas entradas no son palabras en minúsculas y sin acentos (a-z): {', '.join(invalidas)}. "
            "El índice quita los acentos y baja a minúsculas antes de comparar, así que no las reconocería",
        )
    return palabras


def crear_indice_texto(con: duckdb.DuckDBPyConnection, palabras_vacias: Path) -> None:
    """Índice de texto de los fragmentos con raíces y palabras vacías en español (T1.6).

    Por omisión DuckDB indexa con la raíz porter y 571 palabras vacías, las dos en inglés (hallazgo 3
    del plan). Aquí se piden la raíz spanish y la lista de config/palabras_vacias_es.txt, que se guarda
    en la tabla palabras_vacias; IGNORAR_EN_INDICE evita que los signos de apertura y las comillas
    angulares se peguen a las palabras. La consulta usa match_bm25 dentro de una subconsulta (ver
    agente/consultas.py).

    cargar_fts va antes de crear el índice: sin la extensión no existe PRAGMA create_fts_index. Si no
    se puede instalar o cargar, ExtensionNoDisponible sube tal cual hasta la línea de comandos. La
    lista se valida primero: un archivo malo no debe esperar a una descarga para avisar.
    """
    palabras = sorted(set(leer_palabras_vacias(palabras_vacias)))
    cargar_fts(con)
    con.execute(f"CREATE TABLE {TABLA_PALABRAS_VACIAS} (sw VARCHAR PRIMARY KEY)")
    con.executemany(f"INSERT INTO {TABLA_PALABRAS_VACIAS} VALUES (?)", [[palabra] for palabra in palabras])
    con.execute(
        f"PRAGMA create_fts_index('{TABLA_INDEXADA}', 'id', 'titulo_seccion', 'texto', "
        f"stemmer = 'spanish', stopwords = '{TABLA_PALABRAS_VACIAS}', ignore = '{IGNORAR_EN_INDICE}', overwrite = 1)"
    )


# --- Hash de las fuentes y base vigente (T1.7, K04, K07) -----------------------------------------------

# Cada archivo con que se construye la base queda en la tabla hash_archivos con su sha256 sobre los
# bytes crudos. Se agrupan por el rol de su carpeta y no por dónde estén en disco: una copia A a D da
# las mismas rutas lógicas (datos/ventas.csv) que la entrega original.
GRUPO_DATOS = "datos"
GRUPO_POLITICAS = "politicas"
GRUPO_CONFIG = "config"
# Las fuentes originales, "no se tocan": su hash es el que compara G8.
GRUPOS_FUENTES = (GRUPO_DATOS, GRUPO_POLITICAS)

# Los 4 PDF de politicas/, los mismos que declaran los encabezados de politicas_md/. Si falta uno la
# base no se construye, aunque estén los otros 3.
PDF_POLITICAS = (
    "faq_gerentes_de_tienda.pdf",
    "manual_apertura_y_cierre_tienda.pdf",
    "politica_mermas_y_caducidad.pdf",
    "procedimiento_devoluciones.pdf",
)

# Archivos de config/ que lee la construcción. Cambian la base sin ser datos (la definición de venta
# neta, los alias, los temas): sin ellos aquí, editar config/ dejaría una base vieja.
ARCHIVOS_CONFIG = ("supuestos.yaml", "alias.yaml", texto_tickets.ARCHIVO_TEMAS)

TABLA_HASH_ARCHIVOS = """
CREATE TABLE hash_archivos (
    grupo VARCHAR NOT NULL,
    archivo VARCHAR PRIMARY KEY,
    bytes BIGINT NOT NULL,
    sha256 VARCHAR NOT NULL
)
"""

_GRUPOS_FUENTES_SQL = ", ".join(f"'{grupo}'" for grupo in GRUPOS_FUENTES)

# El hash de las fuentes es el sha256 del listado que imprime `sha256sum -t datos/* politicas/*`:
# una línea "<sha256>  <ruta>" por archivo, en orden de ruta. Se recalcula con
# `sha256sum -t datos/* politicas/* | sha256sum`. Sin -t, sha256sum de Git Bash en Windows marca
# cada archivo con " *" (modo binario) y el hash sale distinto; en Linux -t es el modo por omisión.
VISTA_HASH_FUENTES = f"""
CREATE VIEW hash_fuentes AS
SELECT count(*) AS archivos, sum(bytes)::BIGINT AS bytes,
       sha256(string_agg(sha256 || '  ' || archivo || chr(10), '' ORDER BY archivo)) AS sha256
FROM hash_archivos
WHERE grupo IN ({_GRUPOS_FUENTES_SQL})
"""

# Windows no reemplaza un archivo que otra conexión tiene abierto: se espera a que lo suelte.
INTENTOS_REEMPLAZO = 8
ESPERA_REEMPLAZO = 0.25  # segundos


@dataclass(frozen=True)
class HashArchivo:
    """sha256 de un archivo con que se construyó la base, sobre sus bytes crudos (K07).

    grupo es datos, politicas o config y archivo la ruta lógica (datos/ventas.csv).
    """

    grupo: str
    archivo: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class HashFuentes:
    """Hash del conjunto de las fuentes originales (datos/ y politicas/), tal como lo da la vista hash_fuentes."""

    archivos: int
    bytes: int
    sha256: str


@dataclass(frozen=True)
class HashesGuardados:
    """Lo que una base guardó de sus archivos: un hash por archivo y el hash de las fuentes."""

    archivos: tuple[HashArchivo, ...]
    hash_fuentes: HashFuentes


@dataclass(frozen=True)
class EstadoBase:
    """Resultado de asegurar_base: la base lista para consultar y por qué se reconstruyó, si fue el caso."""

    base: Path
    reconstruida: bool
    motivo: str
    hash_fuentes: HashFuentes


class FuentesCambiaron(ErrorConstruccion):
    """Un archivo cambió mientras se construía la base, así que la base nueva no se publica (T1.7).

    archivos son las rutas lógicas (datos/ventas.csv) que ya no tienen el sha256 con que empezó la
    construcción. El hash que guarda la base tiene que ser el de lo que cargó; por eso se descarta
    en lugar de publicar un hash que no corresponde. Volver a intentarlo construye de nuevo.
    """

    def __init__(self, archivos: tuple[str, ...]):
        self.archivos = archivos
        super().__init__(
            f"Cambiaron mientras se construía la base: {', '.join(archivos)}. La base nueva no se usó. "
            "Espera a que termine de copiarse y vuelve a intentarlo; la base anterior, si había, sigue igual."
        )


class BaseEnUso(ErrorConstruccion):
    """La base nueva está lista pero otra conexión tiene abierta la anterior y no se pudo reemplazar (T1.7).

    Windows no deja reemplazar un archivo abierto. Quien consulta cierra su conexión a la base
    antes de llamar a asegurar_base(), y la abre de nuevo después.
    """

    def __init__(self, base: Path):
        self.base = base
        super().__init__(
            f"No se pudo reemplazar {base} porque otra conexión la tiene abierta. Cierra las conexiones a la "
            "base (la interfaz, un cuaderno, otra terminal) y vuelve a intentarlo; la base anterior sigue igual."
        )


def verificar_politicas(dir_pdf: Path) -> dict[str, Path]:
    """Ruta de cada PDF de políticas; ArchivoFaltante con todos los que falten."""
    rutas = {nombre: Path(dir_pdf) / nombre for nombre in PDF_POLITICAS}
    faltan = tuple(nombre for nombre, ruta in rutas.items() if not ruta.is_file())
    if faltan:
        raise ArchivoFaltante(faltan, Path(dir_pdf))
    return rutas


def _archivos_de(carpeta: Path, grupo: str) -> list[tuple[str, Path]]:
    """Los archivos de la carpeta como los lista el shell con carpeta/*: sin ocultos ni subcarpetas."""
    rutas = sorted(Path(carpeta).iterdir(), key=lambda ruta: ruta.name)
    return [(grupo, ruta) for ruta in rutas if ruta.is_file() and not ruta.name.startswith(".")]


def _hash_de(grupo: str, ruta: Path) -> HashArchivo:
    try:
        with open(ruta, "rb") as archivo:
            sha256 = hashlib.file_digest(archivo, "sha256").hexdigest()
            tamano = os.fstat(archivo.fileno()).st_size
    except FileNotFoundError as error:
        raise ArchivoFaltante((ruta.name,), ruta.parent) from error
    return HashArchivo(grupo, f"{grupo}/{ruta.name}", tamano, sha256)


def calcular_hashes(
    dir_datos: Path, dir_pdf: Path, dir_config: Path, *, politicas: bool = True, indice_texto: bool = True
) -> tuple[HashArchivo, ...]:
    """sha256 de cada archivo con que se construye la base, por ruta lógica (K07).

    datos/ y politicas/: todos los archivos de la carpeta, que es lo que lista
    `sha256sum datos/* politicas/*` y lo que compara G8; el diccionario de datos cuenta aunque la
    base no lo lea. config/: solo los que lee la construcción, y la lista de palabras vacías solo si
    se crea el índice de texto. Un archivo de config/ que falta se omite: basta para que la base
    cuente como distinta y el cargador de la configuración lance su propio error. politicas=False
    deja fuera los PDF, igual que la construcción.
    """
    archivos = _archivos_de(dir_datos, GRUPO_DATOS)
    if politicas:
        archivos += _archivos_de(dir_pdf, GRUPO_POLITICAS)
    nombres_config = list(ARCHIVOS_CONFIG)
    if indice_texto:
        nombres_config.append(ARCHIVO_PALABRAS_VACIAS)
    config = [(GRUPO_CONFIG, Path(dir_config) / nombre) for nombre in nombres_config]
    archivos += [(grupo, ruta) for grupo, ruta in config if ruta.is_file()]
    return tuple(sorted((_hash_de(grupo, ruta) for grupo, ruta in archivos), key=lambda hash_: hash_.archivo))


def guardar_hashes(con: duckdb.DuckDBPyConnection, hashes: tuple[HashArchivo, ...]) -> None:
    """Crea la tabla hash_archivos y la vista hash_fuentes con los hashes de esta construcción."""
    con.execute(TABLA_HASH_ARCHIVOS)
    con.executemany(
        "INSERT INTO hash_archivos VALUES (?, ?, ?, ?)",
        [[hash_.grupo, hash_.archivo, hash_.bytes, hash_.sha256] for hash_ in hashes],
    )
    con.execute(VISTA_HASH_FUENTES)
    con.execute(
        "COMMENT ON TABLE hash_archivos IS 'sha256 de cada archivo con que se construyó la base, sobre sus "
        "bytes crudos: datos y politicas son las fuentes originales y config lo que define la base (T1.7, K07)'"
    )
    con.execute(
        "COMMENT ON VIEW hash_fuentes IS 'Hash de las fuentes: sha256 del listado que imprime sha256sum "
        "datos/* politicas/*, una línea \"sha256  ruta\" por archivo (T1.7, K07)'"
    )


def leer_hash_fuentes(con: duckdb.DuckDBPyConnection) -> HashFuentes:
    """El hash de las fuentes que guardó la base a la que está conectada la conexión."""
    return HashFuentes(*con.execute("SELECT archivos, bytes, sha256 FROM hash_fuentes").fetchone())


def leer_hashes(base: Path) -> HashesGuardados | None:
    """Los hashes que guardó la base, o None si no existe, no se puede abrir o no trae esa tabla.

    La abre con ATTACH de solo lectura desde una conexión en memoria y no con connect(): DuckDB
    rechaza una segunda connect() al mismo archivo con otra configuración (la de consulta apaga el
    acceso externo) mientras haya una abierta en este proceso. La conexión se cierra al salir, para
    que el archivo quede libre si hay que reemplazarlo.
    """
    ruta = str(base).replace("'", "''")
    try:
        with duckdb.connect() as con:
            con.execute(f"ATTACH '{ruta}' AS guardada (READ_ONLY)")
            con.execute("USE guardada")
            filas = con.execute("SELECT grupo, archivo, bytes, sha256 FROM hash_archivos ORDER BY archivo").fetchall()
            hash_fuentes = leer_hash_fuentes(con)
    except duckdb.Error:
        return None
    return HashesGuardados(tuple(HashArchivo(*fila) for fila in filas), hash_fuentes)


def hashes_distintos(guardados: tuple[HashArchivo, ...], actuales: tuple[HashArchivo, ...]) -> tuple[str, ...]:
    """Rutas lógicas que no son iguales en los dos lados: cambiadas, nuevas o que ya no están."""
    return tuple(sorted({hash_.archivo for hash_ in set(guardados) ^ set(actuales)}))


def _por_que_reconstruir(guardados: HashesGuardados | None, actuales: tuple[HashArchivo, ...]) -> str:
    """El motivo para reconstruir, o una cadena vacía si la base ya corresponde a los archivos."""
    if guardados is None:
        return "la base no existe o no trae el hash de sus archivos"
    distintos = hashes_distintos(guardados.archivos, actuales)
    return f"cambiaron respecto de la base: {', '.join(distintos)}" if distintos else ""


# Una sola sesión a la vez revisa y reconstruye: la segunda espera y encuentra la base ya vigente.
_CERROJO_BASE = threading.Lock()


def asegurar_base(
    dir_datos: Path = DIR_DATOS,
    base: Path = BASE,
    *,
    dir_pdf: Path = DIR_PDF,
    dir_md: Path = DIR_POLITICAS_MD,
    dir_config: Path = DIR_CONFIG,
    politicas: bool = True,
    indice_texto: bool = True,
) -> EstadoBase:
    """Deja en base una construcción que corresponde a los archivos que hay ahora en disco (T1.7).

    Compara el sha256 de datos/, politicas/ y los archivos de config/ que lee la construcción con los
    que la base guardó al construirse. Si coinciden, no toca nada. Si algo cambió, o la base no
    existe o no se puede leer, la reconstruye (de 0.5 a 3 s) y la reemplaza; el motivo queda en el
    resultado. Es lo que cubre las copias A y B de la rúbrica (G2 y C6.3).

    Si falta un archivo que la construcción necesita, lanza ArchivoFaltante, con su nombre, antes de
    mirar la base: una base vieja no contesta por un archivo que ya no está. En Windows no se puede
    reemplazar una base abierta, así que quien consulta no debe tener una conexión abierta a ella al
    llamar; si la tiene, espera unos segundos y lanza BaseEnUso.
    """
    with _CERROJO_BASE:
        verificar_fuentes(dir_datos)
        if politicas:
            verificar_politicas(dir_pdf)
        actuales = calcular_hashes(dir_datos, dir_pdf, dir_config, politicas=politicas, indice_texto=indice_texto)
        guardados = leer_hashes(base)
        motivo = _por_que_reconstruir(guardados, actuales)
        if not motivo:
            return EstadoBase(Path(base), False, "la base corresponde a los archivos actuales", guardados.hash_fuentes)
        resumen = construir(
            dir_datos, base, dir_pdf=dir_pdf, dir_md=dir_md, dir_config=dir_config,
            politicas=politicas, indice_texto=indice_texto,
        )
    return EstadoBase(resumen.base, True, motivo, resumen.hash_fuentes)


# --- Construcción completa ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ResumenConstruccion:
    """Lo que dejó la construcción: archivo, filas por tabla, fragmentos (si se incluyeron) y hash de las fuentes."""

    base: Path
    filas: dict[str, int]
    politicas: ResumenPoliticas | None
    indice_texto: bool
    hash_fuentes: HashFuentes


def cargar_configuracion(dir_config: Path) -> tuple[definiciones.Supuestos, dict]:
    """supuestos.yaml validado por agente/definiciones.py y alias.yaml leído como datos."""
    try:
        s = definiciones.cargar(Path(dir_config) / "supuestos.yaml")
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfiguracionInvalida(f"config/supuestos.yaml no se pudo validar: {error}") from error
    try:
        with open(Path(dir_config) / "alias.yaml", encoding="utf-8") as archivo:
            alias = yaml.safe_load(archivo)
    except (OSError, yaml.YAMLError) as error:
        raise AliasInvalido(f"no se pudo leer: {error}") from error
    if not isinstance(alias, dict):
        raise AliasInvalido("debe ser un mapa con las secciones tiendas, ciudades_ambiguas, productos y canales")
    return s, alias


def _llenar(
    con: duckdb.DuckDBPyConnection,
    rutas: dict[str, Path],
    s: definiciones.Supuestos,
    alias: dict,
    opciones: dict,
) -> tuple[dict[str, int], ResumenPoliticas | None]:
    filas = cargar_datos(con, rutas)
    crear_tickets_normalizados(con, Path(opciones["dir_config"]))
    crear_unidades_venta(con, s)
    crear_vistas(con, s)
    crear_supuestos(con, s)
    crear_alias(con, alias, s)
    crear_cobertura(con, filas, s)
    resumen_politicas = None
    if opciones["politicas"]:
        resumen_politicas = construir_politicas(con, opciones["dir_pdf"], opciones["dir_md"])
    if opciones["indice_texto"]:
        crear_indice_texto(con, Path(opciones["dir_config"]) / ARCHIVO_PALABRAS_VACIAS)
    return filas, resumen_politicas


def construir(
    dir_datos: Path = DIR_DATOS,
    base: Path = BASE,
    *,
    dir_pdf: Path = DIR_PDF,
    dir_md: Path = DIR_POLITICAS_MD,
    dir_config: Path = DIR_CONFIG,
    politicas: bool = True,
    indice_texto: bool = True,
) -> ResumenConstruccion:
    """Construye la base completa desde dir_datos (datos/ o una copia A a D) en el archivo base.

    Escribe en un archivo temporal junto a base y solo al terminar lo pone en su lugar: si algo
    falla, la base anterior sigue igual y no queda un archivo a medias. politicas=False e
    indice_texto=False sirven a las pruebas que solo miran los datos o corren sin red.

    Guarda el sha256 de cada archivo, calculado antes de cargarlos, y lo vuelve a calcular al
    terminar: si un archivo cambió mientras tanto lanza FuentesCambiaron y no publica la base, para
    que el hash guardado sea siempre el de los datos que cargó. ArchivoFaltante si falta un CSV o,
    con politicas=True, un PDF.
    """
    rutas = verificar_fuentes(dir_datos)
    if politicas:
        verificar_politicas(dir_pdf)
    calcular = functools.partial(
        calcular_hashes, dir_datos, dir_pdf, dir_config, politicas=politicas, indice_texto=indice_texto
    )
    hashes = calcular()
    s, alias = cargar_configuracion(dir_config)
    base = Path(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    temporal = base.with_name(f"{base.name}.construyendo")
    _borrar_base(temporal)
    opciones = {"politicas": politicas, "indice_texto": indice_texto, "dir_pdf": Path(dir_pdf),
                "dir_md": Path(dir_md), "dir_config": Path(dir_config)}
    try:
        with duckdb.connect(str(temporal)) as con:
            filas, resumen_politicas = _llenar(con, rutas, s, alias, opciones)
            guardar_hashes(con, hashes)
            hash_fuentes = leer_hash_fuentes(con)
        cambiados = hashes_distintos(hashes, calcular())
        if cambiados:
            raise FuentesCambiaron(cambiados)
        _poner_en_su_lugar(temporal, base)
    except BaseException:
        _borrar_base(temporal)
        raise
    return ResumenConstruccion(
        base=base, filas=filas, politicas=resumen_politicas, indice_texto=indice_texto, hash_fuentes=hash_fuentes
    )


def _poner_en_su_lugar(temporal: Path, base: Path) -> None:
    """Reemplaza la base por la recién construida; BaseEnUso si otra conexión no la suelta a tiempo."""
    for intento in range(1, INTENTOS_REEMPLAZO + 1):
        try:
            os.replace(temporal, base)
            return
        except PermissionError as error:
            if intento == INTENTOS_REEMPLAZO:
                raise BaseEnUso(base) from error
            time.sleep(ESPERA_REEMPLAZO)


def _borrar_base(ruta: Path) -> None:
    """Borra un archivo DuckDB temporal y su registro de escritura (.wal), si existen."""
    for archivo in (ruta, ruta.with_name(f"{ruta.name}.wal")):
        archivo.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """python -m agente.construir: construye la base y reporta filas por tabla, fragmentos por documento y el hash de las fuentes."""
    parser = argparse.ArgumentParser(description="Construye build/tienda.duckdb.")
    parser.add_argument("--datos", default=str(DIR_DATOS), help="carpeta con los 5 CSV (datos/ o una copia A a D)")
    parser.add_argument("--base", default=str(BASE), help="ruta del archivo DuckDB")
    parser.add_argument("--pdf", default=str(DIR_PDF), help="carpeta con los PDF de políticas")
    parser.add_argument("--politicas-md", default=str(DIR_POLITICAS_MD), help="copias revisadas")
    args = parser.parse_args(argv)
    try:
        resumen = construir(
            Path(args.datos), Path(args.base), dir_pdf=Path(args.pdf), dir_md=Path(args.politicas_md)
        )
    except ErrorConstruccion as error:
        # ExtensionNoDisponible es un ErrorConstruccion: su mensaje nombra fts y dice qué hacer.
        print(error, file=sys.stderr)
        return 1
    for tabla, cantidad in resumen.filas.items():
        print(f"{tabla}: {cantidad:,} filas")
    for documento, cantidad in resumen.politicas.conteos.items():
        print(f"{documento}: {cantidad} fragmentos")
    print(f"Total: {sum(resumen.politicas.conteos.values())} fragmentos")
    print(f"Huella de los fragmentos (sha256): {resumen.politicas.huella}")
    print(f"Fuentes: {resumen.hash_fuentes.archivos} archivos, {resumen.hash_fuentes.bytes:,} bytes")
    print(f"Hash de las fuentes (sha256): {resumen.hash_fuentes.sha256}")
    print(f"Base: {resumen.base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
