"""Estructura del repositorio y telemetría de la interfaz apagada (T0.4).

La lista de componentes sale de la sección "Componentes" (#dg-componentes) del plan
docs/planes/2026-10-31-plan-implementacion.html. La prueba no importa los módulos: lee su código
con ast, así no ejecuta nada de lo que hagan al importarse y sigue valiendo cuando las demás
tareas los llenen.
"""

import ast
import tomllib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent

CARPETAS = ["agente", "agente/herramientas", "config", "politicas_md", "eval", "tests"]

COMPONENTES = [
    "app.py",
    "agente/agente.py",
    "agente/verificacion.py",
    "agente/resolver.py",
    "agente/definiciones.py",
    "agente/construir.py",
    "agente/registro.py",
    "agente/herramientas/ventas.py",
    "agente/herramientas/inventario.py",
    "agente/herramientas/caducidades.py",
    "agente/herramientas/tickets.py",
    "agente/herramientas/buscar_politicas.py",
    "eval/correr.py",
    "eval/respuestas_esperadas.py",
]

CONFIG_INTERFAZ = RAIZ / ".streamlit" / "config.toml"


@pytest.mark.parametrize("carpeta", CARPETAS)
def test_la_carpeta_del_plan_existe(carpeta):
    assert (RAIZ / carpeta).is_dir(), f"Falta la carpeta {carpeta}/"


@pytest.mark.parametrize("componente", COMPONENTES)
def test_cada_componente_del_diagrama_tiene_un_archivo(componente):
    assert (RAIZ / componente).is_file(), f"Falta el archivo {componente}"


@pytest.mark.parametrize("componente", COMPONENTES)
def test_cada_componente_abre_con_un_docstring_que_dice_su_papel(componente):
    ruta = RAIZ / componente
    assert ruta.is_file(), f"Falta el archivo {componente}"
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=componente)
    assert ast.get_docstring(arbol), f"{componente} no abre con un docstring"


def test_la_interfaz_no_envia_estadisticas_de_uso():
    # Streamlit lee este archivo con tomllib y, si no lo puede parsear, solo registra el error
    # y deja la telemetría encendida: por eso la prueba lo parsea igual que él.
    assert CONFIG_INTERFAZ.is_file(), "Falta .streamlit/config.toml"
    configuracion = tomllib.loads(CONFIG_INTERFAZ.read_text(encoding="utf-8"))
    assert configuracion["browser"]["gatherUsageStats"] is False


def test_la_interfaz_no_pide_un_correo_al_arrancar():
    configuracion = tomllib.loads(CONFIG_INTERFAZ.read_text(encoding="utf-8"))
    assert configuracion["server"]["showEmailPrompt"] is False


def test_la_interfaz_solo_escucha_en_este_equipo():
    # Sin server.address, Streamlit 1.66.1 escucha en todas las interfaces de red y otro equipo de
    # la red podría hacer preguntas con la llave del modelo.
    configuracion = tomllib.loads(CONFIG_INTERFAZ.read_text(encoding="utf-8"))
    assert configuracion["server"]["address"] == "127.0.0.1"
