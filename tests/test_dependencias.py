"""Dependencias fijadas con hashes (T0.1, D13-A y D14-B).

Sin red, leen requirements.in, requirements.txt, requirements-dev.in y requirements-dev.txt y
comprueban lo que pide la ficha: cada versión de la tabla de librerías, un hash por archivo, el
corte de antigüedad con sus excepciones anotadas y pip-audit y uv aparte, en el lock de
desarrollo.

Las pruebas que consultan PyPI o instalan en una carpeta limpia, con Python 3.13 y con 3.12,
corren solo con PROBAR_CON_RED=1: descargan unos cientos de MB y tardan unos 2.5 minutos.

    Git Bash:   PROBAR_CON_RED=1 .venv/Scripts/python -m pytest -q -p no:cacheprovider tests/test_dependencias.py
    PowerShell: $env:PROBAR_CON_RED = "1"; .venv\\Scripts\\python -m pytest -q -p no:cacheprovider tests/test_dependencias.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from packaging.version import Version

RAIZ = Path(__file__).resolve().parent.parent

# D14-B: el lock solo toma versiones subidas a PyPI antes del corte. Las excepciones llevan la
# fecha del lock como tope.
CORTE = "2026-10-26T00:00:00Z"
FECHA_DEL_LOCK = "2026-10-31T00:00:00Z"
EXCEPCIONES = ["anthropic", "google-genai", "pyasn1"]

# Tabla "Librerías y versiones" del plan con D14-B aplicada. Los nombres van como los deja uv:
# en minúsculas y con guiones. pdfminer-six lo fija pdfplumber; genai-prices y opentelemetry-api
# son las dos transitivas que D14-B baja a una versión con 7 días publicados.
VERSIONES_DE_EJECUCION = {
    "duckdb": "2.0.0",
    "pydantic-ai-slim": "2.61.0",
    "google-genai": "2.34.0",
    "anthropic": "2.3.0",
    "pdfplumber": "0.11.10",
    "pdfminer-six": "20260107",
    "pandas": "3.1.0",
    "streamlit": "1.66.1",
    "pydantic-evals": "2.61.0",
    "pyyaml": "6.0.3",
    "python-dotenv": "1.2.4",
    "pytest": "9.1.1",
    "genai-prices": "0.1.13",
    "opentelemetry-api": "1.45.1",
}
# pytest-xdist reparte la suite del hook de pre-commit en varios procesos (2026-11-05).
VERSIONES_DE_DESARROLLO = {"pip-audit": "2.10.1", "uv": "0.13.5", "pytest-xdist": "3.8.0"}

DIRECTAS_DE_EJECUCION = {
    "duckdb==2.0.0",
    "pydantic-ai-slim[google,anthropic]==2.61.0",
    "pydantic-evals==2.61.0",
    "anthropic==2.3.0",
    "google-genai==2.34.0",
    "pdfplumber==0.11.10",
    "pandas==3.1.0",
    "streamlit==1.66.1",
    "PyYAML==6.0.3",
    "python-dotenv==1.2.4",
    "pytest==9.1.1",
    "pyasn1>=0.6.5",
}
DIRECTAS_DE_DESARROLLO = {"uv==0.13.5", "pip-audit==2.10.1", "pytest-xdist==3.8.0"}

JUEGOS_DE_ARCHIVOS = [
    ("requirements.in", "requirements.txt"),
    ("requirements-dev.in", "requirements-dev.txt"),
]

MODULOS_DEL_PLAN = [
    "duckdb",
    "pydantic_ai.models.anthropic",
    "pydantic_ai.models.google",
    "pydantic_evals",
    "anthropic",
    "google.genai",
    "pdfplumber",
    "pandas",
    "streamlit",
    "yaml",
    "dotenv",
    "pytest",
]

PAQUETE = re.compile(r"^(?P<nombre>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)")
HASH = re.compile(r"^\s+--hash=sha256:(?P<hash>[0-9a-f]{64})(?: \\)?$")

HAY_RED = os.environ.get("PROBAR_CON_RED") == "1"
CON_RED = pytest.mark.skipif(not HAY_RED, reason="necesita red: se activa con PROBAR_CON_RED=1")


@dataclass
class Fijado:
    nombre: str
    version: str
    hashes: list[str] = field(default_factory=list)


def normalizar(nombre):
    return re.sub(r"[-_.]+", "-", nombre).lower()


def leer(nombre_de_archivo):
    return (RAIZ / nombre_de_archivo).read_text(encoding="utf-8")


def leer_lock(nombre_de_archivo):
    """Devuelve los paquetes fijados y las líneas que no son comentario, paquete ni hash."""
    fijados, sobrantes = [], []
    for linea in leer(nombre_de_archivo).splitlines():
        if not linea.strip() or linea.lstrip().startswith("#"):
            continue
        paquete = PAQUETE.match(linea)
        hash_ = HASH.match(linea)
        if paquete:
            fijados.append(Fijado(normalizar(paquete["nombre"]), paquete["version"]))
        elif hash_ and fijados:
            fijados[-1].hashes.append(hash_["hash"])
        else:
            sobrantes.append(linea)
    return fijados, sobrantes


def versiones_de(nombre_de_archivo):
    fijados, _ = leer_lock(nombre_de_archivo)
    return {fijado.nombre: fijado.version for fijado in fijados}


def comando_del_lock(nombre_de_archivo):
    """Comando de uv que el lock deja en su encabezado."""
    for linea in leer(nombre_de_archivo).splitlines():
        if "uv pip compile" in linea:
            return linea.lstrip("# ").strip()
    pytest.fail(f"{nombre_de_archivo} no trae el comando de uv en su encabezado")


def comando_documentado(nombre_de_archivo):
    """Comando que el .in documenta en sus comentarios, sin las continuaciones de línea."""
    lineas = leer(nombre_de_archivo).splitlines()
    inicio = next((i for i, linea in enumerate(lineas) if "uv pip compile" in linea), None)
    if inicio is None:
        pytest.fail(f"{nombre_de_archivo} no documenta el comando de uv que genera su lock")
    partes = []
    for linea in lineas[inicio:]:
        texto = linea.lstrip("# ").strip()
        partes.append(texto.removesuffix("\\").strip())
        if not texto.endswith("\\"):
            break
    return " ".join(partes)


def excepciones_del_comando(comando):
    return dict(re.findall(r"--exclude-newer-package (\S+)=(\S+)", comando))


def lineas_de_requisitos(nombre_de_archivo):
    lineas = leer(nombre_de_archivo).splitlines()
    return {linea.strip() for linea in lineas if linea.strip() and not linea.startswith("#")}


# --- Sin red -----------------------------------------------------------------------------


@pytest.mark.parametrize("archivo", ["requirements.txt", "requirements-dev.txt"])
def test_cada_paquete_va_fijado_con_su_hash(archivo):
    fijados, sobrantes = leer_lock(archivo)
    sin_hash = [fijado.nombre for fijado in fijados if not fijado.hashes]
    assert fijados, f"{archivo} no fija ningún paquete"
    assert not sobrantes, f"{archivo} trae líneas que --require-hashes no acepta: {sobrantes[:3]}"
    assert not sin_hash, f"Paquetes sin hash en {archivo}: {sin_hash}"


@pytest.mark.parametrize("nombre", sorted(VERSIONES_DE_EJECUCION))
def test_requirements_txt_fija_la_version_de_la_tabla(nombre):
    versiones = versiones_de("requirements.txt")
    assert versiones.get(nombre) == VERSIONES_DE_EJECUCION[nombre]


def test_requirements_txt_deja_pip_audit_y_uv_fuera():
    versiones = versiones_de("requirements.txt")
    assert not set(VERSIONES_DE_DESARROLLO) & set(versiones)


@pytest.mark.parametrize("nombre", sorted(VERSIONES_DE_DESARROLLO))
def test_requirements_dev_txt_fija_pip_audit_y_uv(nombre):
    versiones = versiones_de("requirements-dev.txt")
    assert versiones.get(nombre) == VERSIONES_DE_DESARROLLO[nombre]


def test_requirements_in_lista_las_directas_de_la_tabla():
    assert lineas_de_requisitos("requirements.in") == DIRECTAS_DE_EJECUCION


def test_requirements_dev_in_lista_pip_audit_y_uv():
    assert lineas_de_requisitos("requirements-dev.in") == DIRECTAS_DE_DESARROLLO


@pytest.mark.parametrize("archivo", ["requirements.txt", "requirements-dev.txt"])
def test_el_lock_es_universal_con_hashes_y_con_el_corte_de_antiguedad(archivo):
    comando = comando_del_lock(archivo)
    assert "--universal" in comando
    assert "--generate-hashes" in comando
    assert "--python-version 3.12" in comando
    assert f"--exclude-newer {CORTE}" in comando


def test_las_excepciones_de_antiguedad_llevan_la_fecha_del_lock():
    comando = comando_del_lock("requirements.txt")
    esperadas = {nombre: FECHA_DEL_LOCK for nombre in EXCEPCIONES}
    assert excepciones_del_comando(comando) == esperadas


def test_el_lock_de_desarrollo_no_necesita_excepciones():
    comando = comando_del_lock("requirements-dev.txt")
    assert excepciones_del_comando(comando) == {}


@pytest.mark.parametrize("nombre", EXCEPCIONES)
def test_cada_excepcion_anota_su_motivo_en_requirements_in(nombre):
    anotacion = re.search(rf"^#\s+{re.escape(nombre)}:\s+(?P<motivo>.+)$", leer("requirements.in"), re.M)
    assert anotacion, f"requirements.in no anota el motivo de la excepción de {nombre}"
    assert len(anotacion["motivo"].split()) >= 3, f"El motivo de {nombre} es demasiado corto"


@pytest.mark.parametrize(("archivo_in", "archivo_lock"), JUEGOS_DE_ARCHIVOS)
def test_el_comando_documentado_es_el_que_genero_el_lock(archivo_in, archivo_lock):
    assert comando_documentado(archivo_in) == comando_del_lock(archivo_lock)


def test_el_parche_de_pyasn1_esta_en_el_lock():
    # 0.6.4 tiene cuatro avisos altos (CVE-2026-119194 a CVE-2026-119197) que corrige 0.6.5.
    assert Version(versiones_de("requirements.txt")["pyasn1"]) >= Version("0.6.5")


def test_el_lock_de_desarrollo_no_cambia_versiones_del_lock_de_ejecucion():
    ejecucion = versiones_de("requirements.txt")
    desarrollo = versiones_de("requirements-dev.txt")
    compartidos = ejecucion.keys() & desarrollo.keys()
    distintas = {n: (ejecucion[n], desarrollo[n]) for n in compartidos if ejecucion[n] != desarrollo[n]}
    assert not distintas, f"Versiones distintas entre los dos locks: {distintas}"


# --- Con red (PROBAR_CON_RED=1) ------------------------------------------------------------


def fecha(texto):
    return datetime.fromisoformat(texto)


def archivos_en_pypi(nombre, version):
    """Hash y fecha de subida de cada archivo de esa versión, según la API JSON de PyPI."""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{nombre}/{version}/json", timeout=30) as respuesta:
        archivos = json.load(respuesta)["urls"]
    return {archivo["digests"]["sha256"]: fecha(archivo["upload_time_iso_8601"]) for archivo in archivos}


@CON_RED
@pytest.mark.parametrize("archivo", ["requirements.txt", "requirements-dev.txt"])
def test_el_lock_solo_toma_versiones_subidas_antes_del_corte(archivo):
    fijados, _ = leer_lock(archivo)
    with ThreadPoolExecutor(max_workers=8) as pool:
        publicados = list(pool.map(lambda f: archivos_en_pypi(f.nombre, f.version), fijados))
    fuera_de_plazo, hashes_distintos = [], []
    for fijado, en_pypi in zip(fijados, publicados):
        tope = fecha(FECHA_DEL_LOCK if fijado.nombre in EXCEPCIONES else CORTE)
        ultima_subida = max(en_pypi.values())
        if ultima_subida > tope:
            fuera_de_plazo.append(f"{fijado.nombre}=={fijado.version} subido {ultima_subida:%Y-%m-%d %H:%M}")
        if set(fijado.hashes) != set(en_pypi):
            hashes_distintos.append(f"{fijado.nombre}=={fijado.version}")
    assert not fuera_de_plazo, f"Versiones posteriores al corte sin excepción: {fuera_de_plazo}"
    # Un hash por cada archivo publicado (todas las plataformas) es lo que hace universal al lock.
    assert not hashes_distintos, f"Hashes del lock distintos de los archivos de PyPI: {hashes_distintos}"


def entorno_sin_venv_activo():
    entorno = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV" and not k.startswith("PIP_")}
    entorno["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    entorno["PYTHONUTF8"] = "1"
    return entorno


def ejecutar(comando, carpeta, paso):
    resultado = subprocess.run(
        [str(parte) for parte in comando],
        cwd=carpeta,
        env=entorno_sin_venv_activo(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    detalle = f"{paso} salió con código {resultado.returncode}:\n{resultado.stdout[-2000:]}\n{resultado.stderr[-2000:]}"
    assert resultado.returncode == 0, detalle
    return resultado.stdout.strip()


def interprete(version):
    if sys.platform == "win32":
        return ["py", f"-{version}"]
    ruta = shutil.which(f"python{version}")
    assert ruta, f"No hay python{version} en el PATH"
    return [ruta]


def python_del_venv(venv):
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


@pytest.fixture
def carpeta_limpia():
    carpeta = Path(tempfile.mkdtemp(prefix="agente-lock-"))
    yield carpeta
    shutil.rmtree(carpeta, ignore_errors=True)
    assert not carpeta.exists(), f"No se pudo borrar la carpeta temporal {carpeta}"


@CON_RED
@pytest.mark.parametrize("version", ["3.13", "3.12"])
def test_en_carpeta_limpia_requirements_txt_instala_con_hashes_y_pip_check_no_reporta_conflictos(
    version, carpeta_limpia
):
    ejecutar([*interprete(version), "-m", "venv", ".venv"], carpeta_limpia, "python -m venv .venv")
    python = python_del_venv(carpeta_limpia / ".venv")
    assert ejecutar([python, "--version"], carpeta_limpia, "python --version").startswith(f"Python {version}.")

    instalar = [python, "-m", "pip", "install", "--require-hashes", "--no-cache-dir", "-r", RAIZ / "requirements.txt"]
    ejecutar(instalar, carpeta_limpia, "pip install --require-hashes -r requirements.txt")
    revision = ejecutar([python, "-m", "pip", "check"], carpeta_limpia, "pip check")
    assert revision == "No broken requirements found."

    importar = "; ".join(f"import {modulo}" for modulo in MODULOS_DEL_PLAN)
    ejecutar([python, "-c", importar], carpeta_limpia, "importar las librerías del plan")
