"""Plantilla .env.example y reglas de .gitignore que protegen la llave (T0.2, K02 del plan).

Las reglas se prueban en un repositorio temporal con core.excludesFile vacío: un ignorado
global (el del autor ya ignora .env) taparía una regla que falte en el repositorio, y esa regla
es la única que le llega a quien clona el proyecto.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from dotenv import dotenv_values

RAIZ = Path(__file__).resolve().parent.parent

VARIABLES = ["MODELO", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"]

# Lo que git debe seguir viendo: la plantilla, las reglas, el código y los tres archivos de
# docs/ que se versionan.
VERSIONADOS = [
    ".env.example",
    ".gitignore",
    "agente/agente.py",
    "docs/Estandares/indice.md",
    "docs/planes/2026-10-31-plan-implementacion.html",
    "docs/referencias/indice.md",
]

# Lo que git no debe ver: secretos, lo que se genera al instalar, construir la base y correr
# una pregunta, y los documentos de docs/ que no se redistribuyen.
OCULTOS = [
    ".env",
    ".env.local",
    "agente/.env",
    ".venv/Scripts/python.exe",
    "build/tienda.duckdb",
    "logs/turnos.jsonl",
    "agente/__pycache__/agente.cpython-313.pyc",
    "docs/BRIEF.html",
    "docs/planes/borrador.html",
    "docs/Estandares/OWASP/norma.pdf",
]


def correr_git(carpeta, *argumentos):
    # Un hook de pre-commit hereda GIT_INDEX_FILE y otras variables GIT_*: se quitan para que
    # estos comandos trabajen sobre el repositorio temporal y no sobre el real.
    entorno = {nombre: valor for nombre, valor in os.environ.items() if not nombre.startswith("GIT_")}
    resultado = subprocess.run(
        ["git", *argumentos],
        cwd=carpeta,
        env=entorno,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return resultado.stdout


@pytest.fixture
def repo_temporal(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("falta git en el PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    correr_git(repo, "init", "--quiet")
    for ruta in VERSIONADOS + OCULTOS:
        archivo = repo / ruta
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_text("relleno\n", encoding="utf-8")
    # Solo las reglas son las reales; git decide con el nombre de cada archivo, no con su contenido.
    shutil.copy(RAIZ / ".gitignore", repo / ".gitignore")
    return repo


def test_env_example_trae_las_tres_variables_sin_valor():
    texto = (RAIZ / ".env.example").read_text(encoding="utf-8")
    asignaciones = [linea for linea in texto.splitlines() if linea.strip() and not linea.startswith("#")]
    # Cada línea es exactamente NOMBRE=: un comentario o un espacio después del signo igual se
    # leería como valor al copiar el archivo como .env.
    assert sorted(asignaciones) == sorted(f"{nombre}=" for nombre in VARIABLES)


def test_python_dotenv_lee_la_plantilla_con_los_tres_valores_vacios():
    assert dotenv_values(RAIZ / ".env.example") == {nombre: "" for nombre in VARIABLES}


def test_git_ve_lo_que_se_versiona_y_no_ve_secretos_ni_generados(repo_temporal, tmp_path):
    sin_ignorados_globales = tmp_path / "sin-ignorados-globales"
    sin_ignorados_globales.write_text("", encoding="utf-8")
    salida = correr_git(
        repo_temporal,
        "-c",
        f"core.excludesFile={sin_ignorados_globales.as_posix()}",
        "status",
        "--porcelain",
        "--untracked-files=all",
    )
    visibles = sorted(linea[3:] for linea in salida.splitlines())
    assert visibles == sorted(VERSIONADOS)
