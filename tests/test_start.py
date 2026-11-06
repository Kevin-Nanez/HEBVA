"""start.py y start.bat: el arranque de un solo comando, sin red y sin llaves.

El flujo corre con un ejecutor falso que anota cada comando y deja lo que dejaría el comando real, así que estas pruebas
no crean un entorno virtual ni llaman a pip. La instalación desde cero no cabe en la suite: se cronometra aparte en una
copia limpia. start.bat sí corre de verdad en Windows, con un start.py de mentira.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

import start

RAIZ = Path(__file__).resolve().parent.parent
BAT = RAIZ / "start.bat"
LLAVE = "valor-de-prueba-que-no-debe-salir"
LLAVES_DE_ENTORNO = {"MODELO", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"}
SOLO_WINDOWS = pytest.mark.skipif(os.name != "nt", reason="cmd.exe solo existe en Windows")


class Ejecutor:
    """Hace de subprocess.call: anota cada comando y deja lo que dejaría el comando real."""

    def __init__(self, raiz: Path, fallan: dict[str, int] | None = None) -> None:
        self.raiz = raiz
        self.fallan = fallan or {}
        self.comandos: list[list[str]] = []
        self.carpetas: list[Path | None] = []

    def pasos(self) -> list[str]:
        return [_modulo(comando) for comando in self.comandos]

    def __call__(self, comando, cwd=None) -> int:
        self.comandos.append([str(parte) for parte in comando])
        self.carpetas.append(cwd)
        paso = _modulo(comando)
        if paso in self.fallan:
            return self.fallan[paso]
        if paso == "venv":
            python = start._python_del_entorno(self.raiz)
            python.parent.mkdir(parents=True)
            python.touch()
        if paso == "agente.construir":
            base = self.raiz / start.BASE
            base.parent.mkdir(parents=True)
            base.touch()
        return 0


def _modulo(comando) -> str:
    partes = [str(parte) for parte in comando]
    return partes[partes.index("-m") + 1]


@pytest.fixture
def carpeta(tmp_path):
    """Lo que start.py lee de un repositorio recién descomprimido, con la llave ya en .env."""
    (tmp_path / "requirements.txt").write_text("paquete==1.0\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("MODELO=\nANTHROPIC_API_KEY=\nGOOGLE_API_KEY=\n", encoding="utf-8")
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={LLAVE}\n", encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def versiones(monkeypatch):
    """Todo intérprete dice ser 3.13, salvo los que cada prueba anote aquí."""
    anotadas: dict[tuple[str, ...], tuple[int, int] | None] = {}
    monkeypatch.setattr(start, "_version_de", lambda comando: anotadas.get(tuple(comando), (3, 13)))
    return anotadas


# ------------------------------------------------------------------ el flujo completo


def test_la_primera_vez_instala_construye_y_abre_en_ese_orden(carpeta):
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 0
    assert ejecutor.pasos() == ["venv", "pip", "agente.construir", "streamlit"]
    python = str(start._python_del_entorno(carpeta))
    crear, instalar, construir, abrir = ejecutor.comandos
    assert crear == [sys.executable, "-m", "venv", ".venv"]
    assert instalar[0] == python
    assert "--require-hashes" in instalar
    assert instalar[instalar.index("-r") + 1] == "requirements.txt"
    assert construir == [python, "-m", "agente.construir"]
    assert abrir == [python, "-m", "streamlit", "run", "app.py"]
    assert set(ejecutor.carpetas) == {carpeta}


def test_la_segunda_vez_solo_abre_la_interfaz(carpeta):
    start.main([], raiz=carpeta, ejecutar=Ejecutor(carpeta), entorno={})
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 0
    assert ejecutor.pasos() == ["streamlit"]


def test_si_cambia_requirements_vuelve_a_instalar_sin_recrear_el_entorno(carpeta):
    start.main([], raiz=carpeta, ejecutar=Ejecutor(carpeta), entorno={})
    (carpeta / "requirements.txt").write_text("paquete==2.0\n", encoding="utf-8")
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 0
    assert ejecutor.pasos() == ["pip", "streamlit"]


def test_los_argumentos_van_a_streamlit(carpeta):
    ejecutor = Ejecutor(carpeta)
    start.main(["--server.port", "8600"], raiz=carpeta, ejecutar=ejecutor, entorno={})
    assert ejecutor.comandos[-1][-2:] == ["--server.port", "8600"]


def test_devuelve_el_codigo_de_streamlit(carpeta):
    ejecutor = Ejecutor(carpeta, fallan={"streamlit": 7})
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 7


def test_ctrl_c_termina_sin_traza(carpeta):
    def interrumpir(comando, cwd=None):
        raise KeyboardInterrupt

    (start._python_del_entorno(carpeta)).parent.mkdir(parents=True)
    start._python_del_entorno(carpeta).touch()
    assert start.main([], raiz=carpeta, ejecutar=interrumpir, entorno={}) == 0


# ------------------------------------------------------------------ los pasos que fallan


@pytest.mark.parametrize(
    ("paso", "pasos_corridos"),
    [
        ("venv", ["venv"]),
        ("pip", ["venv", "pip"]),
        ("agente.construir", ["venv", "pip", "agente.construir"]),
    ],
)
def test_un_paso_que_falla_detiene_los_siguientes(carpeta, capsys, paso, pasos_corridos):
    ejecutor = Ejecutor(carpeta, fallan={paso: 1})
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 1
    assert ejecutor.pasos() == pasos_corridos
    assert capsys.readouterr().err.strip()


def test_una_instalacion_fallida_se_repite_en_la_siguiente_corrida(carpeta):
    start.main([], raiz=carpeta, ejecutar=Ejecutor(carpeta, fallan={"pip": 1}), entorno={})
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 0
    assert ejecutor.pasos() == ["pip", "agente.construir", "streamlit"]


def test_un_archivo_que_falta_se_dice_sin_traza(carpeta, capsys):
    (carpeta / "requirements.txt").unlink()
    assert start.main([], raiz=carpeta, ejecutar=Ejecutor(carpeta), entorno={}) == 1
    assert "requirements.txt" in capsys.readouterr().err


def test_el_aviso_de_pip_dice_que_revisar_de_la_red(carpeta, capsys):
    start.main([], raiz=carpeta, ejecutar=Ejecutor(carpeta, fallan={"pip": 1}), entorno={})
    error = capsys.readouterr().err
    assert "PyPI" in error
    assert "HTTPS_PROXY" in error


# ------------------------------------------------------------------ Python y .venv


def test_si_el_python_actual_no_sirve_busca_otro(carpeta, versiones):
    versiones[(sys.executable,)] = (3, 9)
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 0
    assert ejecutor.comandos[0][:2] == ["py", "-3.13"]


def test_sin_un_python_que_sirva_lo_dice_antes_de_tocar_nada(carpeta, capsys, monkeypatch):
    monkeypatch.setattr(start, "_version_de", lambda comando: (3, 14))
    (carpeta / ".env").unlink()
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 1
    assert ejecutor.pasos() == []
    assert not (carpeta / ".env").exists()
    assert "3.12 o 3.13" in capsys.readouterr().err


@pytest.mark.parametrize("version", [(3, 9), (3, 14), None])
def test_un_entorno_que_no_sirve_se_avisa_y_no_se_borra(carpeta, capsys, versiones, version):
    python = start._python_del_entorno(carpeta)
    python.parent.mkdir(parents=True)
    python.touch()
    testigo = carpeta / ".venv" / "testigo.txt"
    testigo.touch()
    versiones[(str(python),)] = version
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 1
    assert ejecutor.pasos() == []
    assert testigo.exists()
    assert ".venv" in capsys.readouterr().err


# ------------------------------------------------------------------ .env


def test_sin_env_lo_crea_y_se_detiene_antes_de_instalar(carpeta, capsys):
    (carpeta / ".env").unlink()
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 1
    assert ejecutor.pasos() == []
    assert (carpeta / ".env").read_bytes() == (carpeta / ".env.example").read_bytes()
    error = capsys.readouterr().err
    assert str(carpeta / ".env") in error
    assert "GOOGLE_API_KEY" in error


@pytest.mark.skipif(os.name == "nt", reason="Windows no tiene los permisos de POSIX")
def test_el_env_que_crea_solo_lo_lee_su_dueno(carpeta):
    (carpeta / ".env").unlink()
    start.main([], raiz=carpeta, ejecutar=Ejecutor(carpeta), entorno={})
    assert stat.S_IMODE((carpeta / ".env").stat().st_mode) == 0o600


def test_un_env_que_ya_existe_no_se_toca(carpeta):
    antes = (carpeta / ".env").read_bytes()
    start.main([], raiz=carpeta, ejecutar=Ejecutor(carpeta), entorno={})
    assert (carpeta / ".env").read_bytes() == antes


def test_una_llave_del_entorno_basta_aunque_no_exista_env(carpeta):
    (carpeta / ".env").unlink()
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={"ANTHROPIC_API_KEY": LLAVE}) == 0
    assert ejecutor.pasos()[-1] == "streamlit"


def test_el_entorno_gana_a_env_como_en_la_interfaz(carpeta):
    (carpeta / ".env").write_text(f"MODELO=\nGOOGLE_API_KEY={LLAVE}\n", encoding="utf-8")
    ejecutor = Ejecutor(carpeta)
    entorno = {"MODELO": start.MODELO_ALTERNO}
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno=entorno) == 0


@pytest.mark.parametrize(
    "valores",
    [
        {"ANTHROPIC_API_KEY": "x"},
        {"MODELO": "", "ANTHROPIC_API_KEY": "x"},
        {"MODELO": start.MODELO_ALTERNO, "GOOGLE_API_KEY": "x"},
        {"MODELO": start.MODELO_ALTERNO, "GEMINI_API_KEY": "x"},
        {"MODELO": "google:gemini-3.8-flash", "GOOGLE_API_KEY": "x"},
        {"MODELO": "anthropic:claude-haiku-5-5", "ANTHROPIC_API_KEY": "x"},
        {"MODELO": "otro:modelo"},
    ],
)
def test_una_configuracion_completa_no_tiene_problema(valores):
    assert start.problema_de_llave(valores) is None


@pytest.mark.parametrize(
    ("valores", "datos"),
    [
        ({}, ["GOOGLE_API_KEY", "ANTHROPIC_API_KEY", start.MODELO_ALTERNO]),
        ({"MODELO": "", "GOOGLE_API_KEY": "   "}, ["GOOGLE_API_KEY", "ANTHROPIC_API_KEY"]),
        ({"GOOGLE_API_KEY": "x"}, [f"MODELO={start.MODELO_ALTERNO}"]),
        ({"GEMINI_API_KEY": "x"}, [f"MODELO={start.MODELO_ALTERNO}"]),
        ({"MODELO": " ", "GOOGLE_API_KEY": "x"}, [f"MODELO={start.MODELO_ALTERNO}"]),
        ({"MODELO": "anthropic:claude-haiku-5-5", "GOOGLE_API_KEY": "x"}, ["ANTHROPIC_API_KEY"]),
        ({"MODELO": "google:gemini-3.8-flash", "ANTHROPIC_API_KEY": "x"}, ["GOOGLE_API_KEY"]),
    ],
)
def test_una_configuracion_incompleta_dice_que_linea_escribir(valores, datos):
    problema = start.problema_de_llave(valores)
    for dato in datos:
        assert dato in problema


def test_ningun_mensaje_muestra_una_llave(carpeta, capsys):
    (carpeta / ".env").write_text(f"MODELO=\nGOOGLE_API_KEY={LLAVE}\n", encoding="utf-8")
    assert start.main([], raiz=carpeta, ejecutar=Ejecutor(carpeta), entorno={}) == 1
    primera = capsys.readouterr()
    (carpeta / ".env").write_text(f"ANTHROPIC_API_KEY={LLAVE}\n", encoding="utf-8")
    ejecutor = Ejecutor(carpeta)
    assert start.main([], raiz=carpeta, ejecutar=ejecutor, entorno={}) == 0
    segunda = capsys.readouterr()
    assert LLAVE not in primera.out + primera.err + segunda.out + segunda.err
    assert LLAVE not in str(ejecutor.comandos)


@pytest.mark.parametrize(
    "texto",
    [
        "MODELO=\n",
        "MODELO=anthropic:claude-haiku-5-5\n",
        "# Comentario\nGOOGLE_API_KEY=abc\n",
        "# GOOGLE_API_KEY=abc\n",
        "GOOGLE_API_KEY = abc \n",
        "export GOOGLE_API_KEY=abc\n",
        'GOOGLE_API_KEY="abc"\n',
        "GOOGLE_API_KEY='abc'\n",
        'GOOGLE_API_KEY="abc" # nota\n',
        "GOOGLE_API_KEY=abc # nota\n",
        "GOOGLE_API_KEY=abc#def\n",
        "GOOGLE_API_KEY= # nota\n",
        "GOOGLE_API_KEY=#abc\n",
        "GOOGLE_API_KEY=   \n",
        "MODELO=x\r\nGOOGLE_API_KEY=y\r\n",
    ],
)
def test_lee_env_igual_que_la_interfaz(tmp_path, texto):
    ruta = tmp_path / ".env"
    ruta.write_bytes(texto.encode("utf-8"))
    assert start.leer_env(ruta) == {nombre: valor or "" for nombre, valor in dotenv_values(ruta).items()}


# ------------------------------------------------------------------ lo que el archivo debe respetar


def test_start_py_corre_fuera_del_entorno_virtual():
    # Corre antes de que exista .venv y con el Python que haya, así que solo usa la biblioteca estándar y una sintaxis
    # que un Python viejo entienda: tiene que poder decir qué versión falta.
    arbol = ast.parse((RAIZ / "start.py").read_text(encoding="utf-8"), feature_version=(3, 8))
    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados |= {alias.name.split(".")[0] for alias in nodo.names}
        if isinstance(nodo, ast.ImportFrom):
            importados.add(nodo.module.split(".")[0])
    assert importados <= sys.stdlib_module_names


def test_start_py_no_pasa_comandos_por_un_shell():
    arbol = ast.parse((RAIZ / "start.py").read_text(encoding="utf-8"))
    assert not [nodo for nodo in ast.walk(arbol) if isinstance(nodo, ast.keyword) and nodo.arg == "shell"]


def test_coincide_con_el_modelo_y_la_base_del_agente():
    from agente import agente, construir

    assert start.MODELO_POR_OMISION == agente.MODELO_POR_OMISION
    assert RAIZ / start.BASE == construir.BASE


def test_el_readme_dice_como_ejecutarlo():
    texto = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "## Cómo ejecutarlo" in texto, "falta la sección en el README"
    seccion = texto.split("## Cómo ejecutarlo", 1)[1].split("\n## ", 1)[0]
    datos = (
        "Python 3.12 o 3.13", "python start.py", "start.bat", ".env.example", "--require-hashes", "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY", start.MODELO_ALTERNO, "--server.address",
    )
    for dato in datos:
        assert dato in seccion, f"la sección no menciona {dato!r}"


# ------------------------------------------------------------------ start.bat


def test_start_bat_va_en_ascii_y_en_crlf():
    # cmd.exe lee el archivo con la página de códigos del sistema, y puede fallar al saltar a una etiqueta sin CRLF.
    contenido = BAT.read_bytes()
    assert contenido.isascii()
    assert contenido.count(b"\n") == contenido.count(b"\r\n") > 0


def test_git_guarda_start_bat_en_crlf_en_cualquier_equipo():
    atributos = (RAIZ / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "*.bat text eol=crlf" in atributos


def _correr_bat(bat: Path, *argumentos: str, **opciones) -> subprocess.CompletedProcess:
    # Sin entrada estándar, para que su pause no espere una tecla.
    return subprocess.run(
        [str(bat), *argumentos], stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
        timeout=120, **opciones,
    )


@SOLO_WINDOWS
def test_start_bat_corre_start_py_en_su_carpeta_con_los_argumentos(tmp_path):
    carpeta = tmp_path / "con espacios y ñ"
    carpeta.mkdir()
    shutil.copy(BAT, carpeta / "start.bat")
    (carpeta / "start.py").write_text(
        "import json, os, sys\n"
        "json.dump({'carpeta': os.getcwd(), 'argumentos': sys.argv[1:]}, open('corrida.json', 'w'))\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    corrida = _correr_bat(carpeta / "start.bat", "--server.port", "8600", cwd=tmp_path)
    assert corrida.returncode == 3
    datos = json.loads((carpeta / "corrida.json").read_text(encoding="utf-8"))
    assert Path(datos["carpeta"]) == carpeta
    assert datos["argumentos"] == ["--server.port", "8600"]


@SOLO_WINDOWS
def test_start_bat_fuera_de_su_carpeta_pide_descomprimir(tmp_path):
    # Es lo que pasa al abrirlo desde dentro del zip: el Explorador lo saca solo a una carpeta temporal.
    shutil.copy(BAT, tmp_path / "start.bat")
    corrida = _correr_bat(tmp_path / "start.bat")
    assert corrida.returncode == 1
    assert "Descomprime" in corrida.stdout


@SOLO_WINDOWS
def test_start_bat_sin_python_dice_que_falta(tmp_path):
    shutil.copy(BAT, tmp_path / "start.bat")
    (tmp_path / "start.py").write_text("", encoding="utf-8")
    sistema = os.environ["SystemRoot"]
    entorno = {"PATH": str(Path(sistema) / "System32"), "SystemRoot": sistema, "ComSpec": os.environ["ComSpec"]}
    corrida = _correr_bat(tmp_path / "start.bat", env=entorno)
    assert corrida.returncode == 1
    assert "Falta Python 3.12 o 3.13" in corrida.stdout


# ------------------------------------------------------------------ una corrida real, sin instalar nada


def test_una_corrida_real_sin_env_lo_crea_y_no_instala_nada(tmp_path):
    for nombre in ("start.py", ".env.example"):
        shutil.copy(RAIZ / nombre, tmp_path / nombre)
    otra = tmp_path / "otra"
    otra.mkdir()
    entorno = {nombre: valor for nombre, valor in os.environ.items() if nombre not in LLAVES_DE_ENTORNO}
    corrida = subprocess.run(
        [sys.executable, str(tmp_path / "start.py")], cwd=otra, env=entorno, capture_output=True, text=True,
        errors="replace", timeout=120,
    )
    assert corrida.returncode == 1
    assert (tmp_path / ".env").is_file()
    assert not (tmp_path / ".venv").exists()
    assert "Traceback" not in corrida.stderr
    assert ".env" in corrida.stderr
