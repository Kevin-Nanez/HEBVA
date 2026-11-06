"""Deja el sistema listo y abre la interfaz: `python start.py`, o `start.bat` con doble clic en Windows.

Corre fuera del entorno virtual y con el Python que haya, así que solo usa la biblioteca estándar y una sintaxis que
Python 3.8 entiende: tiene que poder decir qué versión falta. Cada paso se salta si ya está hecho, de modo que la
segunda corrida solo abre la interfaz.

1. Busca un Python 3.12 o 3.13, las versiones que cubre requirements.txt: el que corre este archivo u otro instalado.
2. Revisa .env. Si no existe, lo crea desde .env.example. Si MODELO no corresponde a una llave con valor, se detiene y
   dice qué línea escribir, sin mostrar ningún valor: sin esto la interfaz abre y a toda pregunta contesta que no tiene
   acceso.
3. Crea .venv e instala requirements.txt con --require-hashes. Una huella de requirements.txt dentro de .venv evita
   repetir la instalación. Un .venv que ya existe no se borra nunca: si no sirve, lo dice.
4. Construye la base si falta, para que un problema al bajar la extensión fts salga aquí, con el mensaje de
   agente.construir, y no en la interfaz. Si ya existe, la interfaz comprueba que siga vigente (asegurar_base).
5. Corre `streamlit run app.py`. Los argumentos de este comando van a Streamlit: `python start.py --server.port 8600`.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
BASE = Path("build") / "tienda.duckdb"
VERSIONES = ((3, 12), (3, 13))
MODELO_POR_OMISION = "anthropic:claude-haiku-5-5"
MODELO_ALTERNO = "google:gemini-3.5-flash-lite"
# Las variables de donde Pydantic AI lee la llave de cada proveedor. Otro proveedor no se revisa: la interfaz dirá si le
# falta algo.
LLAVES_POR_PROVEEDOR = {"google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"), "anthropic": ("ANTHROPIC_API_KEY",)}
HUELLA_INSTALADA = ".requirements.sha256"
_OTROS_INTERPRETES = (["py", "-3.13"], ["py", "-3.12"], ["python3.13"], ["python3.12"], ["python3"], ["python"])
_LINEA_ENV = re.compile(r"\s*(?:export\s+)?([\w.-]+)\s*=([^\S\r\n]*)(.*)")
_VALOR_ENTRE_COMILLAS = re.compile(r"""(['"])(.*?)\1""")


class Alto(Exception):
    """Detiene el arranque con un mensaje para quien lo corre, sin traza."""


def _avisar(texto: str) -> None:
    # Con flush, para que salga antes de lo que imprima el comando siguiente.
    print(texto, flush=True)


def _version_de(comando: list[str]) -> tuple[int, int] | None:
    """La versión de un intérprete, o None si no corre."""
    codigo = "import sys; print(sys.version_info[0], sys.version_info[1])"
    try:
        corrida = subprocess.run([*comando, "-c", codigo], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    version = re.fullmatch(r"(\d+) (\d+)\s*", corrida.stdout)
    if corrida.returncode != 0 or version is None:
        return None
    return int(version.group(1)), int(version.group(2))


def buscar_python() -> list[str]:
    """Un intérprete que requirements.txt cubre: el que corre este archivo, u otro instalado."""
    for comando in ([sys.executable], *_OTROS_INTERPRETES):
        if _version_de(comando) in VERSIONES:
            return comando
    raise Alto(
        "Falta Python 3.12 o 3.13: requirements.txt solo cubre esas dos versiones. "
        "Instálalo desde https://www.python.org/downloads/ y vuelve a correr start."
    )


def _python_del_entorno(raiz: Path) -> Path:
    if os.name == "nt":
        return raiz / ".venv" / "Scripts" / "python.exe"
    return raiz / ".venv" / "bin" / "python"


def _limpiar_valor(valor: str) -> str:
    entre_comillas = _VALOR_ENTRE_COMILLAS.match(valor)
    if entre_comillas:
        return entre_comillas.group(2)
    return re.sub(r"\s+#.*", "", valor).rstrip()


def leer_env(ruta: Path) -> dict[str, str]:
    """Las variables de un .env, con las reglas de python-dotenv, que aquí todavía no está instalado."""
    valores = {}
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        variable = _LINEA_ENV.fullmatch(linea)
        if variable:
            nombre, espacio, valor = variable.groups()
            # "LLAVE= # nota" es una llave vacía con un comentario; "LLAVE=#abc" es un valor.
            valores[nombre] = "" if espacio and valor.startswith("#") else _limpiar_valor(valor)
    return valores


def _tiene_valor(valores: Mapping[str, str], nombre: str) -> bool:
    return bool((valores.get(nombre) or "").strip())


def problema_de_llave(valores: Mapping[str, str]) -> str | None:
    """Qué falta para que el modelo configurado tenga llave, o None. Nunca repite un valor."""
    modelo = (valores.get("MODELO") or "").strip()
    proveedor = (modelo or MODELO_POR_OMISION).split(":", 1)[0]
    llaves = LLAVES_POR_PROVEEDOR.get(proveedor)
    if llaves is None or any(_tiene_valor(valores, llave) for llave in llaves):
        return None
    if modelo:
        return f"MODELO usa el proveedor {proveedor} y falta su llave. Escribe {llaves[0]}=<tu llave>."
    if any(_tiene_valor(valores, llave) for llave in LLAVES_POR_PROVEEDOR["google"]):
        return f"Tienes llave de Gemini, pero con MODELO vacío se usa Claude. Escribe MODELO={MODELO_ALTERNO}."
    return (
        "Falta la llave del modelo. Escribe una de estas dos opciones:\n"
        "  Claude: ANTHROPIC_API_KEY=<tu llave>, con MODELO vacío.\n"
        f"  Gemini: GOOGLE_API_KEY=<tu llave> y MODELO={MODELO_ALTERNO}."
    )


def revisar_env(raiz: Path, entorno: Mapping[str, str]) -> None:
    """Se detiene si al modelo configurado le falta la llave. Crea .env si no existe y nunca lo sobreescribe."""
    ruta = raiz / ".env"
    if not ruta.exists():
        shutil.copyfile(raiz / ".env.example", ruta)
        ruta.chmod(0o600)
        _avisar("Se creó .env desde .env.example.")
    # Como load_dotenv en la interfaz: lo que ya está en el entorno gana a lo que diga .env.
    problema = problema_de_llave({**leer_env(ruta), **entorno})
    if problema:
        raise Alto(f"{problema}\nEl archivo es {ruta}. Guárdalo y vuelve a correr start.")


def _revisar_entorno(python: Path) -> None:
    if _version_de([str(python)]) not in VERSIONES:
        raise Alto(".venv no corre con Python 3.12 ni 3.13. Borra la carpeta .venv y vuelve a correr start.")


def _crear_entorno(interprete: list[str], raiz: Path, ejecutar: Callable[..., int]) -> None:
    _avisar("Creando .venv.")
    if ejecutar([*interprete, "-m", "venv", ".venv"], cwd=raiz) != 0:
        raise Alto("No se pudo crear .venv. En Debian y Ubuntu hace falta el paquete python3-venv.")


def _instalar(python: Path, raiz: Path, ejecutar: Callable[..., int]) -> None:
    """Instala requirements.txt si cambió desde la última instalación."""
    huella = hashlib.sha256((raiz / "requirements.txt").read_bytes()).hexdigest()
    registro = raiz / ".venv" / HUELLA_INSTALADA
    if registro.exists() and registro.read_text(encoding="ascii") == huella:
        return
    _avisar("Instalando las dependencias; solo se hace una vez y tarda unos minutos.")
    comando = [
        str(python), "-m", "pip", "install", "--disable-pip-version-check", "--require-hashes", "-r", "requirements.txt",
    ]
    if ejecutar(comando, cwd=raiz) != 0:
        raise Alto(
            "No se pudieron instalar las dependencias. pip necesita llegar a PyPI: revisa la conexión y, detrás de un "
            "proxy, define HTTPS_PROXY. Si el error sigue, borra la carpeta .venv y vuelve a correr start."
        )
    registro.write_text(huella, encoding="ascii")


def _construir_base(python: Path, raiz: Path, ejecutar: Callable[..., int]) -> None:
    if (raiz / BASE).exists():
        return
    _avisar("Construyendo la base; solo se hace una vez. Si falta la extensión fts de DuckDB, la baja (47 MB).")
    if ejecutar([str(python), "-m", "agente.construir"], cwd=raiz) != 0:
        raise Alto("No se pudo construir la base. El mensaje de arriba dice qué hacer; después vuelve a correr start.")


def preparar(raiz: Path, ejecutar: Callable[..., int], entorno: Mapping[str, str]) -> Path:
    """Deja listo lo que la interfaz necesita y devuelve el Python de .venv."""
    python = _python_del_entorno(raiz)
    if python.exists():
        _revisar_entorno(python)
        revisar_env(raiz, entorno)
    else:
        interprete = buscar_python()
        revisar_env(raiz, entorno)
        _crear_entorno(interprete, raiz, ejecutar)
    _instalar(python, raiz, ejecutar)
    _construir_base(python, raiz, ejecutar)
    return python


def main(
    argumentos: Sequence[str] | None = None,
    *,
    raiz: Path = RAIZ,
    ejecutar: Callable[..., int] = subprocess.call,
    entorno: Mapping[str, str] | None = None,
) -> int:
    """`python start.py`. Los parámetros de palabra clave son para las pruebas: la línea de comandos no los cambia."""
    opciones = sys.argv[1:] if argumentos is None else argumentos
    try:
        python = preparar(raiz, ejecutar, os.environ if entorno is None else entorno)
        _avisar("Abriendo la interfaz. Para detenerla, Ctrl+C en esta ventana.")
        return ejecutar([str(python), "-m", "streamlit", "run", "app.py", *opciones], cwd=raiz)
    except (Alto, OSError) as falla:
        print(falla, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
