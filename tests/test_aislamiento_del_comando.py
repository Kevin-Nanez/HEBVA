"""Aislamiento del subproceso de la prueba del comando de test_politicas.py (T5.12, D28-A).

Esa prueba corre `python -m agente.construir` con un directorio personal propio al que copia el fts ya
instalado, para que el INSTALL del comando no baje nada ni escriba en el ~/.duckdb real. Sus aserciones
miden los fragmentos y el hash, no el aislamiento: sin el HOME propio seguiría pasando. Aquí se mide el
aislamiento con el mismo entorno, el que arma _entorno_con_casa_propia.

El comando llama a cargar_fts con instalar=True, o sea INSTALL y LOAD. La prueba hace lo mismo y comprueba
tres cosas: DuckDB resuelve la extensión dentro de la casa y no en el ~/.duckdb real, la casa queda igual
(tamaño y fecha de cada archivo) y no hace falta red, porque el proxy apunta a un puerto sin servicio y
una descarga haría fallar el INSTALL.
"""

import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from agente import construir
from tests.conftest import RAIZ
from tests.test_politicas import _entorno_con_casa_propia

PROXY_SIN_SERVICIO = "http://127.0.0.1:9"
VARIABLES_DE_PROXY = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

# Lo que hace el comando antes de crear el índice, y la ruta de la extensión que DuckDB terminó usando.
CODIGO_DEL_HIJO = """
import duckdb
from agente import construir

with duckdb.connect() as con:
    construir.cargar_fts(con)
    print(con.execute("SELECT install_path FROM duckdb_extensions() WHERE extension_name = 'fts'").fetchone()[0])
"""


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


def _estado_de(carpeta):
    """Tamaño y fecha de cada archivo de la carpeta: si el INSTALL reescribiera algo, cambiarían."""
    estados = {}
    for ruta in sorted(carpeta.rglob("*")):
        if ruta.is_file():
            datos = ruta.stat()
            estados[ruta.relative_to(carpeta).as_posix()] = (datos.st_size, datos.st_mtime_ns)
    return estados


def test_el_install_del_comando_usa_la_copia_de_la_casa_sin_red_y_sin_escribir(fts, tmp_path):
    casa = tmp_path / "casa"
    entorno = _entorno_con_casa_propia(casa)
    entorno.update(dict.fromkeys(VARIABLES_DE_PROXY, PROXY_SIN_SERVICIO))
    entorno.pop("NO_PROXY", None)
    entorno.pop("no_proxy", None)
    antes = _estado_de(casa)

    hijo = subprocess.run(
        [sys.executable, "-c", CODIGO_DEL_HIJO],
        cwd=RAIZ, env=entorno, capture_output=True, text=True, encoding="utf-8",
    )

    assert antes, "la casa debe traer la copia de fts"
    assert hijo.returncode == 0, hijo.stderr
    assert Path(hijo.stdout.strip()).is_relative_to(casa), "DuckDB tomó la extensión de otro directorio personal"
    assert _estado_de(casa) == antes
