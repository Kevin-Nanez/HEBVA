"""Hook de pre-commit: gitleaks y, después, la suite de pruebas sin red ni llaves (T5.5, K36 del plan).

El hook se prueba como lo corre git: con un commit real en un repositorio temporal. gitleaks y el
python del entorno virtual son dobles (scripts de sh) que anotan cómo los llamaron. Así estas pruebas
no necesitan gitleaks instalado, no corren la suite completa dentro de la suite y tardan segundos.

Lo que fija cada grupo de pruebas:

- El orden: primero gitleaks sobre lo preparado, con los argumentos de T0.2; después comprueba que la
  extensión fts y pytest-xdist estén instalados, y al final corre pytest sobre tests/ en 8 procesos y,
  después, tests/test_reintentos.py solo. Un secreto bloquea el commit antes de gastar minutos en la suite.
- Qué bloquea: un gitleaks que falla o que falta, un entorno virtual que falta, una extensión fts que
  falta, un pytest-xdist que falta y una suite que falla, también si solo falla test_reintentos.py. La extensión importa porque 18 módulos de pruebas se saltan sin ella
  (el ciclo del agente, el registro, los topes): sin la comprobación, la suite pasaría sin probarlos. Y un
  archivo preparado mientras corren las pruebas: git relee el índice al terminar el hook, así que entraría al
  commit sin que gitleaks lo haya visto.
- El entorno de la suite: sin las variables GIT_* del commit (un git de una prueba actuaría sobre el
  índice real), sin llaves, sin PROBAR_CON_RED y con un proxy que no responde, de modo que cualquier
  salida a la red falle en lugar de salir.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from agente import construir

RAIZ = Path(__file__).resolve().parent.parent
HOOK = RAIZ / ".githooks" / "pre-commit"

ARGUMENTOS_DE_GITLEAKS = "git --pre-commit --staged --redact --no-banner --verbose"
# La suite va en 8 procesos repartida por archivo, sin test_reintentos.py, que corre después y solo (mide reloj).
ARGUMENTOS_DE_PYTEST = "-m pytest -q -p no:cacheprovider -n 8 --dist loadfile --ignore=tests/test_reintentos.py tests"
ARGUMENTOS_DE_REINTENTOS = "-m pytest -q -p no:cacheprovider tests/test_reintentos.py"

# Las llaves que el proyecto lee (.env.example) y las que cualquier SDK de proveedor leería solo.
VARIABLES_DE_LLAVES = [
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "MODELO",
    "PROBAR_CON_RED",
]
# Las que git le pasa a un hook de pre-commit en un commit común.
VARIABLES_DE_GIT = ["GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_PREFIX"]

# Doble de gitleaks: anota la llamada y el entorno que recibió, y sale con el código que pida la prueba.
GITLEAKS_DOBLE = """#!/bin/sh
echo "gitleaks $*" >> "$DOBLES/orden.txt"
env > "$DOBLES/entorno_gitleaks.txt"
pwd > "$DOBLES/directorio_gitleaks.txt"
exit "${DOBLE_GITLEAKS_SALIDA:-0}"
"""

# Doble del python del venv. El hook lo llama de tres formas: "-" para comprobar fts (el código viene por
# la entrada estándar), "-c ..." para comprobar pytest-xdist y "-m pytest ..." para la suite y para
# test_reintentos.py. DOBLE_REINTENTOS_SALIDA cambia solo la salida de la segunda. Cualquier otra forma sale
# con 97 para que se note.
PYTHON_DOBLE = """#!/bin/sh
case "$1" in
  -m) etapa=pytest; salida="${DOBLE_PYTEST_SALIDA:-0}"
      case "$*" in *tests/test_reintentos.py) salida="${DOBLE_REINTENTOS_SALIDA:-$salida}" ;; esac ;;
  -c) etapa=xdist; salida="${DOBLE_XDIST_SALIDA:-0}"; printf '%s\\n' "$2" > "$DOBLES/codigo_xdist.py" ;;
  -)  etapa=fts; salida="${DOBLE_FTS_SALIDA:-0}"; cat > "$DOBLES/codigo_fts.py" ;;
  *)  etapa=otra; salida=97 ;;
esac
echo "$etapa $*" >> "$DOBLES/orden.txt"
env > "$DOBLES/entorno_$etapa.txt"
pwd > "$DOBLES/directorio_$etapa.txt"
if [ "$etapa" = pytest ] && [ -n "$DOBLE_PYTEST_PREPARA" ]; then git add "$DOBLE_PYTEST_PREPARA"; fi
exit "$salida"
"""


def _escribir_doble(ruta, contenido):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8", newline="\n")
    ruta.chmod(0o755)


def _sin_variables_de_git():
    return {nombre: valor for nombre, valor in os.environ.items() if not nombre.startswith("GIT_")}


class Escenario:
    """Un repositorio temporal con el hook real, los dobles y un archivo preparado para el commit."""

    def __init__(self, tmp_path):
        self.repo = tmp_path / "repo"
        self.bin = tmp_path / "bin"
        self.dobles = tmp_path / "dobles"
        self.dobles.mkdir()
        self.repo.mkdir()
        self.git("init", "--quiet")
        (self.repo / ".githooks").mkdir()
        shutil.copyfile(HOOK, self.repo / ".githooks" / "pre-commit")
        _escribir_doble(self.bin / "gitleaks", GITLEAKS_DOBLE)
        _escribir_doble(self.repo / ".venv" / "bin" / "python", PYTHON_DOBLE)
        (self.repo / "cambio.txt").write_text("un cambio\n", encoding="utf-8")
        self.git("add", "cambio.txt")

    def entorno(self, extra=None, sin_gitleaks=False):
        entorno = _sin_variables_de_git()
        entorno["DOBLES"] = self.dobles.as_posix()
        entradas = entorno["PATH"].split(os.pathsep)
        if sin_gitleaks:
            entradas = [carpeta for carpeta in entradas if not _tiene_gitleaks(carpeta)]
        else:
            entradas = [str(self.bin), *entradas]
        entorno["PATH"] = os.pathsep.join(entradas)
        entorno.update(extra or {})
        return entorno

    def git(self, *argumentos, extra=None, **opciones):
        return subprocess.run(
            ["git", *argumentos],
            cwd=self.repo,
            env=self.entorno(extra, **opciones),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )

    def commitear(self, extra=None, **opciones):
        return self.git(
            "-c", "core.hooksPath=.githooks",
            "-c", "commit.gpgsign=false",
            "-c", "user.name=Prueba",
            "-c", "user.email=prueba@example.invalid",
            "commit", "--quiet", "-m", "cambio de prueba",
            extra=extra, **opciones,
        )

    def commits(self):
        resultado = self.git("rev-list", "--count", "HEAD")
        return int(resultado.stdout) if resultado.returncode == 0 else 0

    def llamadas(self):
        """Las etapas que el hook corrió, en orden: gitleaks, fts y pytest, con sus argumentos."""
        archivo = self.dobles / "orden.txt"
        return archivo.read_text(encoding="utf-8").splitlines() if archivo.exists() else []

    def entorno_de(self, etapa):
        """Las variables que recibió una etapa, como diccionario."""
        texto = (self.dobles / f"entorno_{etapa}.txt").read_text(encoding="utf-8", errors="replace")
        return dict(linea.partition("=")[::2] for linea in texto.splitlines() if "=" in linea)

    def carpeta_de(self, etapa):
        """La carpeta de trabajo con la que git llamó a una etapa, ya sin los separadores del sistema."""
        texto = (self.dobles / f"directorio_{etapa}.txt").read_text(encoding="utf-8", errors="replace")
        return texto.strip()


def _tiene_gitleaks(carpeta):
    return any((Path(carpeta) / nombre).exists() for nombre in ("gitleaks", "gitleaks.exe"))


@pytest.fixture
def escenario(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("falta git en el PATH")
    return Escenario(tmp_path)


def _etapas(llamadas):
    return [llamada.split(" ", 1)[0] for llamada in llamadas]


# ------------------------------------------------------------------ el archivo del hook


def test_el_hook_arranca_con_el_shebang_de_sh_y_usa_solo_fin_de_linea_lf():
    # Con CRLF, sh falla en Linux y macOS ("sh\r: not found"); .gitattributes lo fija en LF.
    contenido = HOOK.read_bytes()
    assert contenido.startswith(b"#!/bin/sh\n")
    assert b"\r" not in contenido


# ------------------------------------------------------------------ el orden y los argumentos


def test_gitleaks_corre_primero_con_los_argumentos_de_t0_2_y_pytest_corre_al_final(escenario):
    resultado = escenario.commitear()

    assert resultado.returncode == 0, resultado.stderr
    llamadas = escenario.llamadas()
    assert _etapas(llamadas) == ["gitleaks", "fts", "xdist", "pytest", "pytest"]
    assert llamadas[0] == f"gitleaks {ARGUMENTOS_DE_GITLEAKS}"
    assert llamadas[3] == f"pytest {ARGUMENTOS_DE_PYTEST}"
    assert llamadas[4] == f"pytest {ARGUMENTOS_DE_REINTENTOS}"


def test_la_comprobacion_de_xdist_del_hook_concuerda_con_este_equipo(escenario):
    # Como con fts: el doble no la ejecuta y esta prueba sí, con el python de la suite.
    escenario.commitear()
    codigo = (escenario.dobles / "codigo_xdist.py").read_text(encoding="utf-8")
    try:
        import xdist  # noqa: F401

        instalado = True
    except ImportError:
        instalado = False

    resultado = subprocess.run(
        [sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, encoding="utf-8", errors="replace"
    )

    assert (resultado.returncode == 0) == instalado, resultado.stderr


def test_si_todo_pasa_el_commit_entra(escenario):
    resultado = escenario.commitear()

    assert resultado.returncode == 0, resultado.stderr
    assert escenario.commits() == 1


def test_la_comprobacion_de_fts_pide_solo_cargar_la_extension_y_nunca_instalarla(escenario):
    escenario.commitear()

    codigo = (escenario.dobles / "codigo_fts.py").read_text(encoding="utf-8")
    assert "cargar_fts" in codigo
    assert "instalar=False" in codigo, "instalar=True saldría a la red"
    compile(codigo, "<hook pre-commit>", "exec")


def test_la_comprobacion_de_fts_del_hook_concuerda_con_cargar_fts_en_este_equipo(escenario):
    # El doble no ejecuta la comprobación; esta prueba sí, con el python de la suite y desde la raíz del
    # proyecto como la corre el hook. Si cargar_fts cambiara de nombre o de firma, el hook dejaría de
    # funcionar y nadie se enteraría hasta el primer commit.
    escenario.commitear()
    codigo = (escenario.dobles / "codigo_fts.py").read_text(encoding="utf-8")
    with duckdb.connect() as conexion:
        try:
            construir.cargar_fts(conexion, instalar=False)
            instalada = True
        except construir.ExtensionNoDisponible:
            instalada = False

    resultado = subprocess.run(
        [sys.executable, "-"], input=codigo, cwd=RAIZ, capture_output=True, encoding="utf-8", errors="replace"
    )

    assert (resultado.returncode == 0) == instalada, resultado.stderr
    if not instalada:
        assert "fts" in resultado.stderr


# ------------------------------------------------------------------ lo que bloquea


def test_un_secreto_bloquea_el_commit_y_la_suite_no_corre(escenario):
    resultado = escenario.commitear(extra={"DOBLE_GITLEAKS_SALIDA": "1"})

    assert resultado.returncode != 0
    assert escenario.commits() == 0
    assert _etapas(escenario.llamadas()) == ["gitleaks"], "gastó minutos en la suite con un secreto en el commit"
    assert "gitleaks" in resultado.stderr


def test_sin_gitleaks_en_el_path_bloquea_el_commit_y_la_suite_no_corre(escenario):
    resultado = escenario.commitear(sin_gitleaks=True)

    assert resultado.returncode != 0
    assert escenario.commits() == 0
    assert escenario.llamadas() == []
    assert "falta gitleaks" in resultado.stderr
    assert "winget install" in resultado.stderr


def test_una_suite_que_falla_bloquea_el_commit(escenario):
    resultado = escenario.commitear(extra={"DOBLE_PYTEST_SALIDA": "1"})

    assert resultado.returncode != 0
    assert escenario.commits() == 0
    assert _etapas(escenario.llamadas()) == ["gitleaks", "fts", "xdist", "pytest"]
    assert "pruebas" in resultado.stderr


def test_si_solo_falla_test_reintentos_tambien_bloquea_el_commit(escenario):
    resultado = escenario.commitear(extra={"DOBLE_REINTENTOS_SALIDA": "1"})

    assert resultado.returncode != 0
    assert escenario.commits() == 0
    assert _etapas(escenario.llamadas()) == ["gitleaks", "fts", "xdist", "pytest", "pytest"]
    assert "pruebas" in resultado.stderr


def test_sin_pytest_xdist_bloquea_el_commit_y_dice_como_instalarlo(escenario):
    resultado = escenario.commitear(extra={"DOBLE_XDIST_SALIDA": "1"})

    assert resultado.returncode != 0
    assert escenario.commits() == 0
    assert _etapas(escenario.llamadas()) == ["gitleaks", "fts", "xdist"]
    assert "pytest-xdist" in resultado.stderr
    assert "requirements-dev.txt" in resultado.stderr


def test_una_suite_sin_pruebas_tampoco_pasa(escenario):
    # pytest sale con 5 cuando no recoge ninguna prueba: una carpeta tests/ vacía no es una suite verde.
    resultado = escenario.commitear(extra={"DOBLE_PYTEST_SALIDA": "5"})

    assert resultado.returncode != 0
    assert escenario.commits() == 0


def test_sin_la_extension_fts_bloquea_el_commit_y_la_suite_no_corre(escenario):
    resultado = escenario.commitear(extra={"DOBLE_FTS_SALIDA": "1"})

    assert resultado.returncode != 0
    assert escenario.commits() == 0
    assert _etapas(escenario.llamadas()) == ["gitleaks", "fts"], "pytest correría con 18 módulos saltados"
    assert "fts" in resultado.stderr


def test_un_archivo_preparado_mientras_corren_las_pruebas_bloquea_el_commit(escenario):
    # La suite tarda minutos y git relee el índice al terminar el hook: lo que otro proceso prepare en ese
    # lapso entraría al commit sin que gitleaks lo haya revisado.
    (escenario.repo / "otro.txt").write_text("otro" + chr(10), encoding="utf-8")

    resultado = escenario.commitear(extra={"DOBLE_PYTEST_PREPARA": "otro.txt"})

    assert resultado.returncode != 0
    assert escenario.commits() == 0
    assert "cambió" in resultado.stderr


def test_lo_preparado_antes_de_gitleaks_si_entra_al_commit(escenario):
    # El caso contrario: lo preparado no cambia durante las pruebas y el commit lleva todo lo preparado.
    (escenario.repo / "otro.txt").write_text("otro" + chr(10), encoding="utf-8")
    escenario.git("add", "otro.txt")

    resultado = escenario.commitear()

    assert resultado.returncode == 0, resultado.stderr
    archivos = escenario.git("show", "--name-only", "--format=", "HEAD").stdout.split()
    assert sorted(archivos) == ["cambio.txt", "otro.txt"]


def test_sin_entorno_virtual_bloquea_el_commit_y_dice_como_crearlo(escenario):
    shutil.rmtree(escenario.repo / ".venv")

    resultado = escenario.commitear()

    assert resultado.returncode != 0
    assert escenario.commits() == 0
    assert _etapas(escenario.llamadas()) == ["gitleaks"]
    assert ".venv" in resultado.stderr
    assert "requirements.txt" in resultado.stderr


# ------------------------------------------------------------------ el entorno de la suite


def test_la_suite_corre_sin_llaves_sin_probar_con_red_y_sin_el_modelo_configurado(escenario):
    llaves = {nombre: "valor-de-prueba" for nombre in VARIABLES_DE_LLAVES}

    resultado = escenario.commitear(extra=llaves)

    assert resultado.returncode == 0, resultado.stderr
    entorno_de_la_suite = escenario.entorno_de("pytest")
    presentes = sorted(nombre for nombre in VARIABLES_DE_LLAVES if nombre in entorno_de_la_suite)
    assert presentes == []
    # Sin esto la prueba no probaría nada: gitleaks sí las recibió.
    assert all(nombre in escenario.entorno_de("gitleaks") for nombre in VARIABLES_DE_LLAVES)


def test_la_suite_corre_sin_las_variables_git_del_commit(escenario):
    escenario.commitear()

    de_gitleaks = escenario.entorno_de("gitleaks")
    de_la_suite = escenario.entorno_de("pytest")
    # Gitleaks las necesita (GIT_INDEX_FILE apunta al índice del commit) y la suite no las debe heredar:
    # un git que corra una prueba actuaría sobre el índice real, no sobre el de su repositorio temporal.
    assert all(nombre in de_gitleaks for nombre in VARIABLES_DE_GIT), "git ya no le pasa estas variables al hook"
    assert [nombre for nombre in VARIABLES_DE_GIT if nombre in de_la_suite] == []
    assert [nombre for nombre in de_la_suite if nombre in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")] == []


def test_la_comprobacion_de_fts_tambien_corre_sin_llaves_ni_variables_git(escenario):
    escenario.commitear(extra={"ANTHROPIC_API_KEY": "valor-de-prueba"})

    entorno = escenario.entorno_de("fts")
    assert "ANTHROPIC_API_KEY" not in entorno
    assert "GIT_INDEX_FILE" not in entorno


def test_cualquier_salida_a_la_red_choca_con_un_proxy_que_no_responde(escenario):
    escenario.commitear()

    entorno = escenario.entorno_de("pytest")
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert entorno.get(variable) == "http://127.0.0.1:9", variable
    # La máquina local no pasa por el proxy: Streamlit y los dobles de las pruebas hablan por localhost.
    assert "localhost" in entorno.get("NO_PROXY", "")
    assert "127.0.0.1" in entorno.get("NO_PROXY", "")


def test_cada_etapa_corre_desde_la_raiz_del_repositorio(escenario):
    # El hook llama al venv y a tests/ con rutas relativas: valen solo si git lo corre desde la raíz.
    escenario.commitear()

    for etapa in ("gitleaks", "fts", "xdist", "pytest"):
        assert Path(escenario.carpeta_de(etapa)).name == "repo", etapa
