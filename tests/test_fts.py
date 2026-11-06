"""Extensión fts de DuckDB: no viene en el wheel y se descarga en el primer INSTALL (T0.5).

Hallazgo de T0.5, comprobado el 2026-10-31 con duckdb 2.0.0 en Windows x64 (wheel
cp312-abi3-win_amd64): el wheel solo trae integradas core_functions, icu, json y parquet, así que
fts se descarga de extensions.duckdb.org en el primer INSTALL fts (unos 47 MB en disco, 2 s con la
red de la máquina de desarrollo) y queda en ~/.duckdb/extensions/. Sin red y sin esa copia, la
construcción tiene que fallar con un error con tipo que nombre la extensión, no con un traceback
de DuckDB.

Las pruebas sin red guardan las extensiones de cada conexión en una carpeta propia
(extension_directories): ninguna lee ni escribe ~/.duckdb, y por eso "carpeta limpia" es real
aunque el equipo de desarrollo ya tenga fts instalada. Para simular la falta de red, el
repositorio apunta a un dominio .invalid, que está reservado (RFC 6761) y nunca resuelve: el
INSTALL falla igual que sin internet, en un segundo y sin salir a la red.

Las pruebas con red corren solo con PROBAR_CON_RED=1: bajan unos 47 MB y tardan unos 25 segundos.
Usan la carpeta de extensiones por omisión de DuckDB, dentro de un directorio personal falso
(USERPROFILE y HOME), para comprobar la ruta ~/.duckdb/extensions/ y la receta del README sin
tocar el directorio personal real. La red se corta con un proxy inexistente: DuckDB respeta
HTTP_PROXY y así se ejercita el repositorio real (core), no uno de mentira.

    Git Bash:   PROBAR_CON_RED=1 .venv/Scripts/python -m pytest -q -p no:cacheprovider tests/test_fts.py
    PowerShell: $env:PROBAR_CON_RED = "1"; .venv\\Scripts\\python -m pytest -q -p no:cacheprovider tests\\test_fts.py
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb
import pytest

from agente import construir

RAIZ = Path(__file__).resolve().parent.parent
README = RAIZ / "README.md"
TITULO_DE_LA_SECCION = "## Construcción de la base y extensión fts"

HAY_RED = os.environ.get("PROBAR_CON_RED") == "1"
CON_RED = pytest.mark.skipif(not HAY_RED, reason="necesita red: se activa con PROBAR_CON_RED=1")

# .invalid está reservado (RFC 6761): nunca resuelve, así que el INSTALL falla igual que sin red.
REPOSITORIO_SIN_RED = "http://repositorio.invalid"
# Un proxy en un puerto cerrado corta la descarga del repositorio real (core, con su espejo de
# respaldo); DuckDB tarda unos 20 s en rendirse.
PROXY_QUE_CORTA_LA_RED = "http://127.0.0.1:9"
SIN_RED = {"HTTP_PROXY": PROXY_QUE_CORTA_LA_RED, "http_proxy": PROXY_QUE_CORTA_LA_RED}

# Proceso aparte para las pruebas con red: en Windows la extensión cargada bloquea su archivo
# hasta que muere el proceso, y un proceso nuevo es además una "carpeta limpia" de verdad.
CODIGO_DEL_PROCESO = '''
import json
import sys

sys.path.insert(0, sys.argv[1])
import duckdb

from agente.construir import ExtensionNoDisponible, cargar_fts

con = duckdb.connect()
try:
    cargar_fts(con, instalar=sys.argv[2] == "instalar")
    # Llamarla otra vez, o con un cursor de la misma base, no falla: T1.6 da un cursor por sesión.
    cargar_fts(con, instalar=sys.argv[2] == "instalar")
    cargar_fts(con.cursor(), instalar=False)
except ExtensionNoDisponible as error:
    resultado = {"error": "ExtensionNoDisponible", "extension": error.extension, "paso": error.paso, "mensaje": str(error)}
    print(json.dumps(resultado))
    raise SystemExit(0)

ruta, origen = con.execute(
    "SELECT install_path, installed_from FROM duckdb_extensions() WHERE extension_name = 'fts'"
).fetchone()
con.execute("CREATE TABLE politicas (id VARCHAR, texto VARCHAR)")
con.execute(
    "INSERT INTO politicas VALUES "
    "('p1', 'El reembolso con tarjeta de débito tarda de 3 a 5 días hábiles'), "
    "('p2', 'Una impresora de caja descompuesta es una falla de prioridad media')"
)
con.execute("PRAGMA create_fts_index('politicas', 'id', 'texto', stemmer = 'spanish', stopwords = 'none')")
encontrados = con.execute(
    "SELECT id FROM (SELECT id, fts_main_politicas.match_bm25(id, 'reembolsos') AS puntaje FROM politicas) "
    "WHERE puntaje IS NOT NULL"
).fetchall()
print(json.dumps({"ruta": ruta, "origen": origen, "encontrados": [fila[0] for fila in encontrados]}))
'''


def conexion(carpeta, **ajustes):
    """Conexión en memoria cuyas extensiones viven en carpeta y no en ~/.duckdb."""
    configuracion = {"extension_directories": [str(carpeta)], **ajustes}
    return contextlib.closing(duckdb.connect(config=configuracion))


@pytest.fixture(scope="module")
def fallo_sin_red(tmp_path_factory):
    """Lo que pasa en una carpeta limpia cuando el repositorio no responde (el caso sin red)."""
    carpeta = tmp_path_factory.mktemp("extensiones-sin-red")
    with conexion(carpeta, custom_extension_repository=REPOSITORIO_SIN_RED) as con:
        with pytest.raises(construir.ExtensionNoDisponible) as capturado:
            construir.cargar_fts(con)
    return capturado.value, carpeta


def test_fts_no_viene_dentro_del_wheel_de_duckdb(tmp_path):
    with conexion(tmp_path) as con:
        estado = con.execute(
            "SELECT installed, install_mode FROM duckdb_extensions() WHERE extension_name = 'fts'"
        ).fetchone()
    assert estado == (False, "NOT_INSTALLED")


def test_sin_red_en_una_carpeta_limpia_falla_con_un_error_con_tipo_que_nombra_fts(fallo_sin_red):
    error, _ = fallo_sin_red
    assert error.extension == "fts"
    assert error.paso == "INSTALL"
    assert "fts" in str(error)


def test_el_error_con_tipo_es_de_la_construccion_y_conserva_la_causa_de_duckdb(fallo_sin_red):
    error, _ = fallo_sin_red
    assert isinstance(error, construir.ErrorConstruccion)
    assert not isinstance(error, duckdb.Error)
    assert isinstance(error.__cause__, duckdb.Error)


def test_el_mensaje_dice_que_la_primera_construccion_necesita_red_y_trae_el_detalle_de_duckdb(fallo_sin_red):
    error, _ = fallo_sin_red
    mensaje = str(error)
    assert "red" in mensaje
    assert "extensions.duckdb.org" in mensaje
    assert error.causa
    assert error.causa in mensaje


def test_un_install_fallido_no_deja_archivos_a_medias_en_la_carpeta(fallo_sin_red):
    _, carpeta = fallo_sin_red
    assert [ruta for ruta in carpeta.rglob("*") if ruta.is_file()] == []


def test_con_instalar_apagado_solo_carga_y_no_toca_la_red(tmp_path):
    with conexion(tmp_path, custom_extension_repository=REPOSITORIO_SIN_RED) as con:
        with pytest.raises(construir.ExtensionNoDisponible) as capturado:
            construir.cargar_fts(con, instalar=False)
    assert capturado.value.extension == "fts"
    assert capturado.value.paso == "LOAD"


def test_con_el_acceso_externo_apagado_no_se_puede_instalar_y_falla_con_el_mismo_error(tmp_path):
    # Por eso cargar_fts va antes de enable_external_access=false (T1.6).
    with conexion(tmp_path, enable_external_access=False) as con:
        with pytest.raises(construir.ExtensionNoDisponible) as capturado:
            construir.cargar_fts(con)
    assert capturado.value.extension == "fts"
    assert isinstance(capturado.value.__cause__, duckdb.PermissionException)


def test_el_readme_documenta_el_paso_de_fts():
    assert README.is_file(), "Falta README.md"
    texto = README.read_text(encoding="utf-8")
    assert TITULO_DE_LA_SECCION in texto, f"El README no tiene la sección {TITULO_DE_LA_SECCION!r}"
    seccion = texto.split(TITULO_DE_LA_SECCION, 1)[1].split("\n## ", 1)[0]
    for dato in ("INSTALL fts", "extensions.duckdb.org", "ExtensionNoDisponible", "~/.duckdb/extensions"):
        assert dato in seccion, f"La sección de fts del README no menciona {dato!r}"


# --- Con red (PROBAR_CON_RED=1) ------------------------------------------------------------


def casa_falsa(directorio):
    """Variables que mudan el directorio personal de DuckDB (~) a directorio."""
    directorio.mkdir(parents=True, exist_ok=True)
    return {"USERPROFILE": str(directorio), "HOME": str(directorio)}


def correr_cargar_fts(entorno_extra, accion="instalar"):
    """Corre cargar_fts en un proceso nuevo, con la carpeta de extensiones por omisión."""
    resultado = subprocess.run(
        [sys.executable, "-I", "-c", CODIGO_DEL_PROCESO, str(RAIZ), accion],
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, **entorno_extra},
        timeout=600,
    )
    assert resultado.returncode == 0, resultado.stderr[-2000:]
    return json.loads(resultado.stdout.strip().splitlines()[-1])


@CON_RED
def test_la_primera_construccion_instala_fts_desde_core_y_las_siguientes_ya_no_necesitan_red():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as base:
        casa = Path(base) / "equipo-con-red"
        primera = correr_cargar_fts(casa_falsa(casa))
        assert primera["origen"] == "core"
        assert primera["encontrados"] == ["p1"]
        ruta = Path(primera["ruta"]).resolve()
        assert ruta.is_file(), "INSTALL no dejó fts en disco"
        assert ruta.is_relative_to(casa.resolve() / ".duckdb" / "extensions" / f"v{duckdb.__version__}")

        # Instalada, ni INSTALL ni LOAD salen a la red: el proxy inexistente no estorba.
        for accion in ("instalar", "solo_cargar"):
            repetida = correr_cargar_fts({**casa_falsa(casa), **SIN_RED}, accion)
            assert repetida["encontrados"] == ["p1"]

        # La receta del README para un equipo sin red: llevar la carpeta de otro equipo con red.
        otra_casa = Path(base) / "equipo-sin-red"
        carpeta_de_la_plataforma = ruta.parent
        destino = otra_casa.resolve() / carpeta_de_la_plataforma.relative_to(casa.resolve())
        shutil.copytree(carpeta_de_la_plataforma, destino)
        copiada = correr_cargar_fts({**casa_falsa(otra_casa), **SIN_RED})
        assert copiada["encontrados"] == ["p1"]
        assert Path(copiada["ruta"]).resolve().is_relative_to(otra_casa.resolve())


@CON_RED
def test_sin_red_y_con_el_repositorio_real_una_carpeta_limpia_falla_con_el_error_con_tipo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as base:
        resultado = correr_cargar_fts({**casa_falsa(Path(base) / "equipo-limpio"), **SIN_RED})
    assert resultado["error"] == "ExtensionNoDisponible"
    assert resultado["extension"] == "fts"
    assert resultado["paso"] == "INSTALL"
    assert "fts" in resultado["mensaje"]
