"""README.md: ata a su fuente lo que el repositorio puede desmentir.

El README es lo único que sigue un clon nuevo y dice solo lo necesario para correr el sistema, evaluarlo y saber qué
datos salen. Estas pruebas no juzgan la prosa: buscan en ella lo que el código, config/, la base o eval/reportes/
pueden contradecir, y fallan si el texto se queda atrás.

Atado a su fuente:
- Rutas y comandos: cada ruta existe en un clon (lo que git versiona) y el README no dice que un clon traiga lo que git
  ignora. Cada código en línea y cada línea de los bloques de código nombra algo que existe.
- Código: agente.TOPES, el máximo de preguntas por mensaje, lo que sale al proveedor, las banderas de
  python -m eval.correr, las versiones de Python de start y la configuración de Streamlit.
- Alcance: agente/manifiesto.py, config/supuestos.yaml y la base construida.
- Modelos, evaluación y costo: el último reporte del set con Haiku 5.5, el del set con Flash-Lite, el del núcleo, los
  429 de Google y config/precios.yaml.
- Datos y dependencias: el inventario contra el CSV y el detector, los casos adversariales contra sus reportes y la
  auditoría contra su archivo.
- Lo que no viaja: el README no cita commits ni la rúbrica ni los identificadores del plan, porque docs/ no va en el zip.

No atado, porque ningún archivo del clon lo recalcula: lo que tardaron la instalación y la suite en la máquina de
desarrollo, el texto de los términos de los proveedores y la cuota de Anthropic.

Sin red y sin llaves. Las que construyen la base con el índice de texto necesitan la extensión fts y se saltan sin ella.
"""

from __future__ import annotations

import csv
import fnmatch
import functools
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath

import _duckdb
import duckdb
import pytest
import yaml

import start
from agente import agente as modulo_agente
from agente import construir, definiciones, historial, manifiesto, preguntas, privacidad, registro, reintentos
from agente import texto_tickets
from agente.herramientas import comun as herramientas_comun
from agente.herramientas.comun import fecha_larga
from eval import banco, buscar_privacidad, correr
from tests.conftest import DIR_DATOS

RAIZ = Path(__file__).resolve().parent.parent
README = RAIZ / "README.md"
REPORTES = RAIZ / "eval" / "reportes"
PLANTILLA = RAIZ / "plantilla_decisiones.md"
# El set completo con Haiku 5.5 en esfuerzo medium (D8-D, el de la app desde el 2026-11-05): el que el README da como
# «la última corrida», el de la duración de --completo y el del costo por pregunta.
COMPLETO_MEDIUM = REPORTES / "2026-11-05-completo-bfe7da1.json"
# El set completo con Flash-Lite: 2 corridas, sin juez, sobre una versión anterior.
COMPLETO_FLASH_LITE = REPORTES / "2026-11-04-completo-9600d3f-flash-lite.json"
# El núcleo con Haiku 5.5, en 1 corrida: el README da 2, como python -m eval.correr.
NUCLEO_HAIKU = REPORTES / "2026-11-03-nucleo-680bd88-haiku.json"
ADVERSARIALES = REPORTES / "adversarial-huecos-despues.json"
HUECOS = REPORTES / "huecos-despues.json"
CUOTA = REPORTES / "cuota-gemini.md"
AUDITORIA = REPORTES / "auditoria-dependencias.txt"
PRECIOS = RAIZ / "config" / "precios.yaml"

SECCIONES = (
    "Cómo ejecutarlo",
    "Alcance y límites",
    "Modelos y costo",
    "Evaluación del sistema",
    "Pruebas",
    "Construcción de la base y extensión fts",
    "Datos y seguridad",
    "Mapa del repositorio",
)


# ------------------------------------------------------------------ lectura del README


@pytest.fixture(scope="module")
def texto() -> str:
    assert README.is_file(), "falta README.md"
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prompts() -> tuple:
    """El banco de preguntas de la evaluación, leído una vez: tarda casi medio segundo."""
    return banco.leer_banco()


def _encabezados(contenido: str) -> list[tuple[int, str]]:
    """Los títulos del Markdown fuera de los bloques de código, como (nivel, texto)."""
    encontrados = []
    en_bloque = False
    for linea in contenido.splitlines():
        if linea.lstrip().startswith("```"):
            en_bloque = not en_bloque
            continue
        coincidencia = None if en_bloque else re.match(r"(#{1,6}) (.+)", linea)
        if coincidencia:
            encontrados.append((len(coincidencia.group(1)), coincidencia.group(2).strip()))
    return encontrados


def seccion(contenido: str, titulo: str, nivel: int = 2) -> str:
    """El texto de una sección, desde su título hasta el siguiente de su nivel o de uno más alto."""
    recogidas: list[str] = []
    dentro = False
    en_bloque = False
    for linea in contenido.splitlines():
        if linea.lstrip().startswith("```"):
            en_bloque = not en_bloque
        titulo_de_linea = None if en_bloque else re.match(r"(#{1,6}) (.+)", linea)
        if titulo_de_linea and len(titulo_de_linea.group(1)) <= nivel:
            if dentro:
                break
            dentro = titulo_de_linea.group(1) == "#" * nivel and titulo_de_linea.group(2).strip() == titulo
            continue
        if dentro:
            recogidas.append(linea)
    if not dentro and not recogidas:
        pytest.fail(f"el README no tiene la sección {'#' * nivel} {titulo}")
    return "\n".join(recogidas)


def celdas(linea: str) -> list[str]:
    return [celda.strip() for celda in linea.strip().strip("|").split("|")]


def fila(contenido: str, primera_celda: str) -> list[str]:
    """Las celdas del renglón de una tabla cuya primera celda es exactamente `primera_celda`."""
    for linea in contenido.splitlines():
        if linea.startswith("|") and celdas(linea)[0] == primera_celda:
            return celdas(linea)
    pytest.fail(f"falta el renglón {primera_celda!r} en la tabla")


def dolares(valor: object, decimales: int) -> str:
    return f"{Decimal(str(valor)).quantize(Decimal(1).scaleb(-decimales), ROUND_HALF_UP):f}"


def minutos(valor: object) -> str:
    return f"{Decimal(str(valor)).quantize(Decimal('0.1'), ROUND_HALF_UP):f}"


def json_de(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def cifra(contenido: str, patron: str, esperado: object) -> None:
    """Cada vez que el README dice `patron`, lo que capturan sus grupos es `esperado`; falla si no lo dice nunca.

    Con un grupo, `esperado` es un valor; con varios, una tupla. Así la cifra queda atada a la frase que la rodea y no a
    cualquier lugar del texto donde aparezca el mismo número.
    """
    dichos = re.findall(patron, contenido, re.S)
    assert dichos, f"el README ya no dice {patron!r}"
    esperados = tuple(str(valor) for valor in esperado) if isinstance(esperado, tuple) else str(esperado)
    assert set(dichos) == {esperados}, f"{patron!r}: el README dice {sorted(set(dichos))} y debería decir {esperados}"


# ------------------------------------------------------------------ estructura y lo que no viaja


def test_el_readme_trae_sus_secciones_en_orden(texto):
    titulos = [titulo for nivel, titulo in _encabezados(texto) if nivel == 2]
    assert titulos == list(SECCIONES)


def test_el_readme_abre_con_un_titulo_y_no_repite_secciones(texto):
    encabezados = _encabezados(texto)
    assert encabezados[0] == (1, "HEBVA")
    assert [nivel for nivel, _ in encabezados].count(1) == 1
    assert len(encabezados) == len(set(encabezados)), "hay dos títulos iguales: las ligas internas apuntarían al primero"


def _ancla(titulo: str) -> str:
    """El ancla que GitHub arma con un título: minúsculas, sin signos y con guiones en lugar de espacios."""
    sin_formato = titulo.replace("`", "").lower()
    return re.sub(r"[^\w\- ]", "", sin_formato).replace(" ", "-")


def test_las_ligas_internas_apuntan_a_un_titulo_del_readme(texto):
    anclas = {_ancla(titulo) for _, titulo in _encabezados(texto)}
    ligas = re.findall(r"\]\(#([^)\s]+)\)", texto)
    assert ligas, "el README ya no liga a sus secciones: ¿cambió el formato?"
    assert [liga for liga in ligas if liga not in anclas] == []


def test_el_readme_no_trae_rutas_de_la_maquina_de_desarrollo_ni_llaves(texto):
    assert not re.search(r"[A-Za-z]:\\Users|/Users/|/home/", texto)
    assert not re.search(r"AIza[0-9A-Za-z_-]{30,}|sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{20,}|AQ\.[\w-]{20,}", texto)


def test_el_readme_no_trae_el_celular_ni_el_nombre_del_ticket_1043(texto):
    buscados = buscar_privacidad.buscados_del_ticket()
    cuenta = buscar_privacidad.contar(texto, buscados)
    assert (cuenta.celular, cuenta.nombre_completo) == (0, 0)


def test_el_readme_cumple_la_guia_de_redaccion_sin_relleno(texto):
    # La raya como conector y los arranques de relleno que la guía manda quitar. No es un filtro de estilo completo.
    assert "—" not in texto
    for frase in ("cabe destacar", "cabe mencionar", "es importante destacar", "en resumen", "en definitiva"):
        assert frase not in texto.lower()
    # La coma separa miles y el punto, decimales.
    assert not re.search(r"\d,\d{1,2}(?!\d)", texto)


def test_el_readme_no_cita_commits(texto):
    # Un hash abreviado o completo: de 7 a 40 caracteres hexadecimales con al menos una letra y un dígito. El sha256 de las
    # fuentes tiene 64 y no entra. Cubre también los nombres de reporte que llevan el commit.
    citados = re.findall(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b", texto)
    assert citados == []


def test_el_readme_no_cita_la_rubrica_ni_los_identificadores_del_plan(texto):
    # Quien recibe el zip no tiene la rúbrica ni el plan: docs/ no viaja, así que esos códigos no le dirían nada.
    assert "rúbrica" not in texto.lower()
    codigos = re.findall(r"\b(?:D\d+(?:-[A-D])?|K\d{2}|T\d\.\d+|EA-\d+|[CG]\d+(?:\.\d+)?)\b", texto)
    assert codigos == []


# Lo que el README cita como ruta tiene que estar en un clon, que trae lo que git versiona y nada más. Lo que el
# programa crea al correr se nombra como destino y git lo ignora: la clave es el comienzo de la ruta y el valor, un
# archivo cualquiera de esa carpeta, para preguntarle a git.
_NO_VERSIONADAS = {
    "build": "build/tienda.duckdb",
    "logs": "logs/turnos.jsonl",
    ".venv": ".venv/Scripts/python.exe",
    ".env": ".env",
}


@pytest.fixture(scope="module")
def clon() -> set[str]:
    """Los archivos que trae un clon: los versionados y los nuevos que git no ignora (los que se van a versionar)."""
    if shutil.which("git") is None or not (RAIZ / ".git").exists():
        pytest.skip("sin git ni historial no se sabe qué trae un clon: el zip de entrega sale de git archive")
    listado = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=RAIZ, capture_output=True, timeout=60,
    )
    assert listado.returncode == 0, listado.stderr.decode("utf-8", errors="replace")
    return {ruta for ruta in listado.stdout.decode("utf-8").split("\0") if ruta}


def _no_versionada(ruta: str) -> str | None:
    """El comienzo de _NO_VERSIONADAS al que pertenece la ruta, o None si debería estar en el clon."""
    return next((inicio for inicio in _NO_VERSIONADAS if ruta == inicio or ruta.startswith(f"{inicio}/")), None)


def _rutas_citadas(texto: str, clon: set[str]) -> list[str]:
    """Los códigos en línea que son una ruta del proyecto: con «/» o con extensión, y que empiezan en algo del clon."""
    primeros = {ruta.split("/")[0] for ruta in clon} | {inicio.split("/")[0] for inicio in _NO_VERSIONADAS}
    citadas = set(re.findall(r"`([\w./-]+)`", texto))
    return sorted(ruta for ruta in citadas if ruta.split("/")[0] in primeros and ("/" in ruta or "." in ruta))


def _git_ignora(ruta: str, vacio: Path) -> bool:
    # core.excludesFile vacío: el ignorado global del equipo no cuenta, porque un clon no lo trae (test_repositorio.py).
    resultado = subprocess.run(
        ["git", "-c", f"core.excludesFile={vacio.as_posix()}", "check-ignore", "-q", "--no-index", "--", ruta],
        cwd=RAIZ, capture_output=True, timeout=60,
    )
    return resultado.returncode == 0


def test_las_rutas_que_cita_el_readme_estan_en_un_clon(texto, clon, tmp_path):
    rutas = _rutas_citadas(texto, clon)
    assert len(rutas) > 15, "el README cita pocas rutas: ¿cambió el formato de los códigos?"
    versionables = [ruta for ruta in rutas if _no_versionada(ruta) is None]
    en_el_clon = [
        ruta for ruta in versionables
        if ruta.rstrip("/") in clon or any(archivo.startswith(ruta.rstrip("/") + "/") for archivo in clon)
    ]
    assert sorted(set(versionables) - set(en_el_clon)) == [], "el README cita rutas que un clon no trae"
    # Lo demás es lo que el README dice que se crea: git lo ignora de verdad y el clon no trae nada así.
    vacio = tmp_path / "sin-ignorados-globales"
    vacio.write_text("", encoding="utf-8")
    for inicio in {_no_versionada(ruta) for ruta in rutas} - {None}:
        assert _git_ignora(_NO_VERSIONADAS[inicio], vacio), f"git no ignora {_NO_VERSIONADAS[inicio]}"
        assert [archivo for archivo in clon if _no_versionada(archivo) == inicio] == [], f"el clon trae {inicio}"
    # Lo que el programa crea en build/ y logs/ tiene el nombre que el código le da.
    destinos = {"build/tienda.duckdb": construir.BASE, "logs/turnos.jsonl": registro.ARCHIVO}
    for ruta, objeto in destinos.items():
        assert objeto == RAIZ / ruta, ruta
    creadas = {ruta for ruta in rutas if _no_versionada(ruta) in {"build", "logs"}}
    assert creadas <= {"build/", "logs/", *destinos}


def test_los_archivos_sueltos_que_cita_el_readme_estan_en_el_clon(texto, clon):
    nombres = {Path(archivo).name for archivo in clon}
    sueltos = set(re.findall(r"`([\w-]+\.(?:py|md|yaml|csv|txt|bat|in|toml|html))`", texto))
    assert len(sueltos) > 8, "el README cita pocos archivos sueltos: ¿cambió el formato de los códigos?"
    assert sorted(sueltos - nombres) == [], "el README cita archivos que ninguna carpeta del clon tiene"


# ------------------------------------------------------------------ cómo ejecutarlo y las dos rutas de modelo


def test_como_ejecutarlo_trae_los_pasos_la_version_de_python_y_la_plataforma_probada(texto):
    ejecutar = seccion(texto, "Cómo ejecutarlo")
    pasos = ("python start.py", "start.bat", ".env.example", "--require-hashes", "Python 3.12 o 3.13")
    for dato in (*pasos, "http://127.0.0.1:8501"):
        assert dato in ejecutar, f"«Cómo ejecutarlo» no menciona {dato!r}"
    assert "Windows" in ejecutar and "macOS y Linux no se probaron" in ejecutar


def test_las_dos_rutas_de_modelo_estan_con_su_id_su_llave_y_la_omision(texto):
    ejecutar = seccion(texto, "Cómo ejecutarlo")
    assert start.MODELO_ALTERNO in ejecutar
    assert "GOOGLE_API_KEY" in ejecutar and "ANTHROPIC_API_KEY" in ejecutar
    assert "ruta por omisión" in ejecutar and "ruta alterna" in ejecutar
    assert "latest" not in texto.lower()
    modelos = seccion(texto, "Modelos y costo")
    ids = fila(modelos, "`MODELO` en `.env`")
    assert "vacío" in ids[1] and modulo_agente.MODELO_POR_OMISION in ids[1]
    assert start.MODELO_ALTERNO in ids[2]
    llaves = fila(modelos, "Llave")
    assert "ANTHROPIC_API_KEY" in llaves[1] and "GOOGLE_API_KEY" in llaves[2]
    assert start.MODELO_POR_OMISION == modulo_agente.MODELO_POR_OMISION


def test_el_readme_dice_que_el_modelo_se_elige_en_modelo_del_env(texto):
    ejecutar = seccion(texto, "Cómo ejecutarlo")
    assert "`MODELO`" in ejecutar
    assert re.search(r"vacío.{0,120}Gemini.{0,200}GOOGLE_API_KEY", ejecutar, re.S)


_OPCIONES_DE_STREAMLIT = ("server.address", "server.port", "server.showEmailPrompt", "browser.gatherUsageStats")


def _configuracion_efectiva_de_streamlit() -> dict:
    """Lo que `streamlit run app.py` usa desde la raíz de un clon: .streamlit/config.toml y los valores por omisión."""
    codigo = (
        "import json; from streamlit import config; "
        f"print(json.dumps({{o: config.get_option(o) for o in {_OPCIONES_DE_STREAMLIT!r}}}))"
    )
    corrida = subprocess.run(
        [sys.executable, "-I", "-c", codigo], cwd=RAIZ, capture_output=True, text=True, timeout=120,
    )
    assert corrida.returncode == 0, corrida.stderr[-1500:]
    return json.loads(corrida.stdout.strip().splitlines()[-1])


def test_la_configuracion_de_streamlit_que_cita_el_readme_es_la_que_usa_un_clon(texto):
    efectivas = _configuracion_efectiva_de_streamlit()
    archivo = tomllib.loads((RAIZ / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    del_archivo = {clave: valor for tabla in archivo.values() for clave, valor in tabla.items()}
    citadas = re.findall(r"`(\w+) = ([^`]+)`", texto)
    assert {clave for clave, _ in citadas} == set(del_archivo) == {"address", "showEmailPrompt", "gatherUsageStats"}
    for clave, valor in citadas:
        assert tomllib.loads(f"x = {valor}")["x"] == del_archivo[clave], clave
    # La interfaz se abre donde Streamlit la pone, y las banderas que el README pasa a start.py son opciones suyas.
    puerto = efectivas["server.port"]
    assert f"http://{efectivas['server.address']}:{puerto}" in seccion(texto, "Cómo ejecutarlo")
    banderas = set(re.findall(r"--(server\.\w+)", texto))
    assert banderas == {"server.port", "server.address"} and banderas <= set(efectivas)


def test_las_versiones_de_python_del_readme_son_las_que_admite_start_y_cubre_el_lock(texto):
    versiones = [f"{mayor}.{menor}" for mayor, menor in start.VERSIONES]
    assert f"Python {' o '.join(versiones)}" in seccion(texto, "Cómo ejecutarlo")
    dependencias = seccion(texto, "Dependencias", nivel=3)
    cifra(dependencias, r"para Python (3\.\d+ y 3\.\d+)\.", " y ".join(versiones))
    lock = (RAIZ / "requirements.txt").read_text(encoding="utf-8").splitlines()[1]
    assert "--universal" in lock and "--python-version 3.12" in lock


# ------------------------------------------------------------------ alcance y límites


@pytest.fixture(scope="module")
def base_con_indice(tmp_path_factory) -> Path:
    """La base completa, con el índice de texto, como la deja `python -m agente.construir` (necesita fts)."""
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")
    base = tmp_path_factory.mktemp("readme") / "tienda.duckdb"
    construir.asegurar_base(DIR_DATOS, base)
    return base


@pytest.fixture(scope="module")
def cobertura(base_con_indice):
    return manifiesto.leer(base=base_con_indice)


def test_el_alcance_del_readme_es_el_de_la_base_y_la_configuracion(texto, cobertura):
    alcance = seccion(texto, "Alcance y límites")
    cifra(texto.split("\n## ", 1)[0], r"gerente de (\d+) tiendas", len(cobertura.tiendas))
    cifra(alcance, r"contesta sobre (\d+) tiendas y (\d+) documentos",
          (len(cobertura.tiendas), len(cobertura.documentos)))
    assert len(cobertura.documentos) == len(list((RAIZ / "politicas").glob("*.pdf")))
    fuentes = {fuente.clave: fuente for fuente in cobertura.fuentes}
    assert set(fuentes) == {"ventas", "tickets", "inventario"}
    for clave in ("ventas", "tickets"):
        desde, hasta = fecha_larga(fuentes[clave].desde), fecha_larga(fuentes[clave].hasta)
        assert f"{clave} del {desde} al {hasta}" in alcance, clave
    assert f"inventario en una sola foto, la del {fecha_larga(fuentes['inventario'].hasta)}" in alcance
    no_incluidos = [faltante.codigo for faltante in cobertura.no_incluidos]
    cifra(alcance, r"ni sobre ([A-Z]{3}-[A-Z]{3}-\d{3}), ([A-Z]{3}-[A-Z]{3}-\d{3}) y ([A-Z]{3}-[A-Z]{3}-\d{3}),",
          tuple(sorted(no_incluidos)))
    cifra(alcance, r"como si hoy fuera el ([^,]+),", fecha_larga(definiciones.cargar().fecha_referencia))
    assert manifiesto.pide_la_cobertura("qué sabes")


# Cómo nombra el README cada métrica que los datos no permiten calcular.
_METRICAS_EN_EL_README = {
    "ticket_promedio": "el ticket promedio",
    "venta_por_hora": "la venta por hora",
    "merma_real": "la merma real",
    "meta_venta": "la meta de venta",
    "primera_respuesta": "la primera respuesta de un ticket",
    "inventario_historico": "el inventario de otra fecha",
}


def test_cada_metrica_que_los_datos_no_permiten_calcular_esta_en_el_readme(texto):
    limites = seccion(texto, "Alcance y límites")
    assert set(_METRICAS_EN_EL_README) == set(definiciones.cargar().metricas_no_calculables)
    for clave, nombre in _METRICAS_EN_EL_README.items():
        assert nombre in limites, clave


def test_los_topes_del_readme_son_los_de_agente_topes(texto):
    limites = seccion(texto, "Alcance y límites")
    topes = modulo_agente.TOPES
    cifra(limites, r"Cada turno tiene topes: (\d+) solicitudes al modelo, (\d+) ejecuciones de herramientas, (\d+) s y "
          r"([\d,]+) caracteres de pregunta",
          (topes.solicitudes, topes.herramientas, f"{topes.segundos:g}", f"{topes.caracteres:,}"))
    cifra(limites, r"se contesta por separado, hasta (\d+)\.", preguntas.MAXIMO_PREGUNTAS)


# ------------------------------------------------------------------ modelos y costo


def _aciertos(reporte: dict, clave: str) -> list[int]:
    return [corrida["aciertos"] for corrida in reporte[clave]["alcance"]["por_corrida"]]


def _latencia(reporte: dict) -> str:
    latencia = reporte["eficiencia_total"]["latencia_ms"]
    return f"{latencia['p50'] / 1000:.2f} / {latencia['p90'] / 1000:.2f} s"


def test_las_cifras_del_set_por_modelo_son_las_de_sus_reportes(texto):
    modelos = seccion(texto, "Modelos y costo")
    haiku, flash = json_de(COMPLETO_MEDIUM), json_de(COMPLETO_FLASH_LITE)
    assert haiku["meta"]["ajustes"]["esfuerzo_claude"] == "medium"
    assert haiku["meta"]["modelos"] == [modulo_agente.MODELO_POR_OMISION]
    assert flash["meta"]["modelos"] == [start.MODELO_ALTERNO]
    reglas, juez = _aciertos(haiku, "acierto_reglas"), _aciertos(haiku, "acierto")
    assert haiku["meta"]["corridas_completas"] == len(reglas) == 1
    cifra(modelos, r"(\d+) de 100 prompts con reglas y (\d+) con juez en la última corrida", (reglas[0], juez[0]))
    set_completo = fila(modelos, "Set de 100 prompts")
    assert f"{reglas[0]} con reglas y {juez[0]} con juez, 1 corrida" == set_completo[1]
    reglas_flash = _aciertos(flash, "acierto_reglas")
    assert flash["juez"]["usado"] is False and flash["meta"]["corridas_completas"] == len(reglas_flash) == 2
    assert set_completo[2].startswith(f"{reglas_flash[0]} y {reglas_flash[1]} con reglas, sin juez, 2 corridas")
    latencias = fila(modelos, "Latencia del set (p50 / p90)")
    assert latencias[1:] == [_latencia(haiku), _latencia(flash)]


def _de_cuota(observada: str, modelo: str, medida: str) -> str:
    patron = rf"{re.escape(modelo)} \| Generate content requests per {medida}[^|]*\| (\d+) \|"
    return re.search(patron, observada).group(1)


def test_la_cuota_de_flash_lite_es_la_que_dijeron_sus_429(texto):
    observada = CUOTA.read_text(encoding="utf-8")
    por_minuto = _de_cuota(observada, "gemini-3.5-flash-lite", "minute")
    por_dia = _de_cuota(observada, "gemini-3.5-flash-lite", "day")
    assert fila(seccion(texto, "Modelos y costo"), "Cuota")[2] == (
        f"{por_minuto} solicitudes por minuto y {por_dia} por día, observadas el 2026-11-03"
    )
    assert "2026-11-03" in observada


def test_el_costo_por_pregunta_sale_del_set_en_medium_y_de_los_precios(texto):
    """Caliente: lo medido en el set con el esfuerzo de la app. Fría: además se escribe el prefijo en caché."""
    modelos = seccion(texto, "Modelos y costo")
    reporte = json_de(COMPLETO_MEDIUM)
    assert reporte["meta"]["ajustes"]["esfuerzo_claude"] == "medium"
    caliente = Decimal(reporte["uso_total"]["costo_usd"]) / reporte["uso_total"]["turnos"]
    prefijos = {turno["cache_escritura_tokens"] for turno in reporte["turnos"] if turno.get("cache_escritura_tokens")}
    assert len(prefijos) == 1, "el prefijo en caché no es uno solo: la cuenta de la caché fría ya no aplica"
    haiku = yaml.safe_load(PRECIOS.read_text(encoding="utf-8"))["modelos"]["claude-haiku-5-5"]
    extra = prefijos.pop() * (Decimal(haiku["cache_escritura"]) - Decimal(haiku["cache_lectura"])) / 1_000_000
    por_pregunta = (dolares(caliente, 5), dolares(caliente + extra, 5))
    assert fila(modelos, "Costo por pregunta")[1] == (
        f"${por_pregunta[0]} con caché caliente y ${por_pregunta[1]} con fría, en esfuerzo medium"
    )
    # Las mismas cifras que la sección 4 de la plantilla, sin un supuesto de uso.
    plantilla = PLANTILLA.read_text(encoding="utf-8")
    for valor in por_pregunta:
        assert valor in plantilla, valor
    assert "al mes" not in modelos and "supuesto" not in modelos


# ------------------------------------------------------------------ evaluación


def test_el_readme_documenta_el_comando_de_evaluacion_y_las_banderas_que_nombra(texto, prompts):
    evaluacion = seccion(texto, "Evaluación del sistema")
    nucleo, completo = banco.seleccionar(prompts), banco.seleccionar(prompts, completo=True)
    assert fila(evaluacion, "`python -m eval.correr`")[1] == (
        f"Núcleo: {len(nucleo)} prompts, {sum(len(p.pasos) for p in nucleo)} turnos por corrida"
    )
    assert fila(evaluacion, "`python -m eval.correr --completo`")[1] == (
        f"Set: {len(completo)} prompts, {sum(len(p.pasos) for p in completo)} turnos por corrida"
    )
    cifra(evaluacion, r"Qué corre, en (\d+) corridas", correr._argumentos([]).corridas)
    fuente = (RAIZ / "eval" / "correr.py").read_text(encoding="utf-8")
    nombradas = set(re.findall(r"(?<![\w-])(--[a-z][\w-]*)", evaluacion)) - {"--help"}
    assert nombradas >= {"--completo", "--corridas", "--modelo", "--reanudar"}
    assert [bandera for bandera in nombradas if f'add_argument("{bandera}"' not in fuente] == []
    # «--help lista las demás»: argparse la agrega y sale con 0.
    with pytest.raises(SystemExit) as salida:
        correr._argumentos(["--help"])
    assert salida.value.code == 0


def test_la_duracion_y_el_costo_de_cada_comando_salen_de_los_reportes(texto):
    evaluacion = seccion(texto, "Evaluación del sistema")
    corridas = correr._argumentos([]).corridas
    for comando, ruta in (("`python -m eval.correr`", NUCLEO_HAIKU), ("`python -m eval.correr --completo`", COMPLETO_MEDIUM)):
        reporte = json_de(ruta)
        assert reporte["meta"]["corridas_completas"] == 1, ruta.name
        duracion = corridas * (reporte["uso_total"]["latencia_minutos"] + reporte["juez"]["uso"]["latencia_minutos"])
        costo = corridas * (Decimal(reporte["uso_total"]["costo_usd"]) + Decimal(reporte["juez"]["uso"]["costo_usd"]))
        assert fila(evaluacion, comando)[2] == f"{minutos(duracion)} minutos y ${dolares(costo, 2)}", comando


# ------------------------------------------------------------------ pruebas y extensión fts


def test_el_readme_pide_construir_con_red_antes_de_pytest(texto):
    pruebas = seccion(texto, "Pruebas")
    assert pruebas.index("python -m agente.construir") < pruebas.index("python -m pytest")
    for dato in ("con red", "fts", "47 MB", "~/.duckdb/extensions"):
        assert dato in pruebas, f"«Pruebas» no menciona {dato!r}"
    # Sin fts pytest -q termina sin error y salta cientos de pruebas, sin decir que falta la extensión.
    assert re.search(r"\b\d{3} pruebas", pruebas)
    assert re.search(r"sin avisar|no avisa|sin decir", pruebas)


def test_la_seccion_de_fts_manda_a_construir_antes_de_las_pruebas(texto):
    fts = seccion(texto, "Construcción de la base y extensión fts")
    assert "(#pruebas)" in fts


def test_el_comando_de_sha256sum_del_readme_imprime_el_hash_que_el_readme_da(texto):
    pruebas = seccion(texto, "Pruebas")
    comando = re.search(r"^    (sha256sum -t datos/\* politicas/\* \| sha256sum)$", pruebas, re.M).group(1)
    # Sin -t, sha256sum de Git Bash en Windows marca los archivos como binarios y da otro hash: nunca se da sin -t.
    assert "sha256sum datos/*" not in texto
    if shutil.which("sha256sum") is None:
        pytest.skip("sin sha256sum no se puede repetir la comprobación del README")
    corrida = subprocess.run(comando, shell=True, cwd=RAIZ, capture_output=True, text=True, timeout=120)
    assert corrida.returncode == 0, corrida.stderr
    salida = corrida.stdout.rstrip("\n")
    cifra(pruebas, r"El comando imprime `([0-9a-f]{64})`", salida[:64])
    # Tras el hash, un guion que cambia según el sistema: « -» o « *-».
    assert salida[64:] in (" -", " *-") and "un guion que cambia según el sistema" in pruebas


# ------------------------------------------------------------------ datos y seguridad


def test_el_inventario_cuenta_los_csv_y_el_unico_ticket_con_datos_personales(texto):
    inventario = seccion(texto, "Inventario de datos personales", nivel=3)
    with (DIR_DATOS / "tickets_mesa_servicio.csv").open(encoding="utf-8", newline="") as archivo:
        filas = list(csv.DictReader(archivo))
    con_datos = [fila_csv["ticket_id"] for fila_csv in filas if privacidad.buscar_datos_personales(fila_csv["descripcion"])]
    assert [fila_csv["ticket_id"] for fila_csv in filas if privacidad.buscar_datos_personales(fila_csv["titulo"])] == []
    cifra(inventario, r"De los (\d+) CSV de", len(list(DIR_DATOS.glob("*.csv"))))
    cifra(inventario, r"y de los (\d+) tickets solo el (\d+) los trae", (len(filas), *con_datos))
    enmascarado = privacidad.enmascarar("Reporta Juan Perez, cel 55 1234 5678")
    for marcador in ("<PERSONA_1>", "<TELEFONO_1>"):
        assert f"`{marcador}`" in inventario and marcador in enmascarado


def test_lo_que_sale_al_proveedor_es_lo_que_manda_el_codigo(texto):
    sale = seccion(texto, "Datos que salen al proveedor del modelo", nivel=3)
    cifra(sale, r"las últimas (\d+) vueltas", historial.VUELTAS)
    cifra(sale, r"hasta (\d+) filas", herramientas_comun.MAX_FILAS)
    for ruta in ("API de Anthropic con llave comercial", "Gemini API sin facturación (la cuota gratuita)",
                 "Gemini API con facturación activa"):
        assert fila(sale, ruta)[1].startswith(("Sí", "No")), ruta


def test_los_casos_adversariales_que_cita_el_readme_son_los_de_sus_reportes(texto):
    hostil = seccion(texto, "Qué puede hacer un ticket hostil", nivel=3)
    adversariales, huecos = json_de(ADVERSARIALES), json_de(HUECOS)
    cifra(hostil, r"en (\d+) casos adversariales", len(adversariales["casos"]) + len(huecos["casos"]))
    # «ninguna cifra falsa ni clave del ticket llegó a la respuesta»: los 15 de K31 pasan y ningún hueco filtra.
    assert all(caso["paso"] for caso in adversariales["casos"])
    assert [caso["id"] for caso in huecos["casos"] if caso["fuga_respuesta"]] == []
    assert adversariales["sha256"]["sin_cambios"] and huecos["sha256"]["sin_cambios"]


def test_el_readme_cita_la_auditoria_de_dependencias_y_el_comando_que_la_repite(texto):
    dependencias = seccion(texto, "Dependencias", nivel=3)
    for dato in (
        "eval/reportes/auditoria-dependencias.txt",
        ".venv/Scripts/python -m eval.auditar_dependencias",
        "requirements-dev.txt",
        "requirements.txt",
        "--require-hashes",
        "pip-audit",
        "red",
    ):
        assert dato in dependencias, f"«Dependencias» no menciona {dato!r}"
    auditoria = AUDITORIA.read_text(encoding="utf-8")
    fijados = re.search(r"Paquetes fijados en el archivo: (\d+)", auditoria).group(1)
    fecha = re.search(r"Fecha \(UTC\): (\d{4}-\d{2}-\d{2})", auditoria).group(1)
    assert "Veredicto: CUMPLE" in auditoria
    cifra(dependencias, r"fija (\d+) paquetes", fijados)
    cifra(dependencias, r"La auditoría de pip-audit del (\d{4}-\d{2}-\d{2}) da 0 vulnerabilidades", fecha)
    pinned = re.findall(r"^[A-Za-z0-9][A-Za-z0-9._-]*==", (RAIZ / "requirements.txt").read_text(encoding="utf-8"), re.M)
    assert len(pinned) == int(fijados)


# ------------------------------------------------------------------ mapa del repositorio


def test_el_mapa_del_repositorio_nombra_lo_que_existe(texto):
    mapa = seccion(texto, "Mapa del repositorio")
    rutas = ("app.py", "start.py", "agente/", "config/", "datos/", "politicas/", "eval/", "tests/", "build/", "logs/")
    for ruta in rutas:
        assert f"`{ruta}`" in mapa, f"el mapa no nombra {ruta}"


# ------------------------------------------------------------------ cada código en línea nombra algo que existe
#
# Esta prueba lee cada código en línea y exige que lo que nombra exista en el clon: un módulo que corre, una bandera que
# ese módulo define, un nombre que aparece en el código, en la configuración o en las pruebas. Un nombre mal escrito o
# un comando inventado falla aquí.

_EXTENSIONES_DE_TEXTO = {".py", ".md", ".yaml", ".toml", ".in", ".txt", ".csv", ".bat", ".json", ".example", ""}
_LIMITE_DE_BYTES = 400_000
# Programas de otros que el README cita tal cual y que este repositorio no define.
_PROGRAMAS_AJENOS = {"python", "python3", "pip", "sha256sum", "git", "streamlit"}
_MODULOS_AJENOS = {"pytest", "pip", "streamlit"}
# Las banderas y los subcomandos de esos módulos que el README usa tal cual.
_BANDERAS_AJENAS = {"pytest": {"-q"}, "pip": {"--require-hashes", "-r"}, "streamlit": set()}
_SUBCOMANDOS_AJENOS = {"pip": {"install"}, "streamlit": {"run"}}
_EXTENSIONES_DE_RUTA = (".py", ".txt", ".md", ".yaml", ".csv", ".json", ".toml", ".in", ".jsonl")


@pytest.fixture(scope="module")
def corpus(clon) -> str:
    """El texto del clon menos el README y esta prueba, que citan a los demás: ahí tiene que estar lo que citan."""
    propios = {"README.md", Path(__file__).relative_to(RAIZ).as_posix()}
    partes = []
    for ruta in sorted(clon - propios):
        archivo = RAIZ / ruta
        es_texto = archivo.suffix in _EXTENSIONES_DE_TEXTO and archivo.is_file()
        if not es_texto or archivo.stat().st_size > _LIMITE_DE_BYTES:
            continue
        partes.append(archivo.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(partes)


@functools.cache
def _binario_de_duckdb() -> bytes:
    """El módulo nativo de DuckDB: ahí viven textos que el README cita de él (un servidor, un modo de instalación)."""
    return Path(_duckdb.__file__).read_bytes()


@functools.cache
def _ajustes_de_duckdb() -> frozenset[str]:
    with duckdb.connect() as con:
        return frozenset(nombre for (nombre,) in con.execute("SELECT name FROM duckdb_settings()").fetchall())


@functools.cache
def _en_minusculas(corpus: str) -> str:
    return corpus.casefold()


def _esta_en(codigo: str, corpus: str) -> bool:
    """Un código de hasta 4 caracteres cuenta solo como palabra entera: «cZ» sale por azar entre los hashes del lock."""
    if len(codigo) > 4:
        return codigo in corpus
    return re.search(rf"(?<![\w-]){re.escape(codigo)}(?![\w-])", corpus) is not None


def _argumento_existe(modulo: str, parte: str, fuente: str | None, clon: set[str], corpus: str) -> bool:
    """Una pieza de `python -m modulo ...`: una bandera del módulo, una ruta del clon o un valor que el código usa."""
    if parte.startswith("-"):
        return parte in _BANDERAS_AJENAS[modulo] if fuente is None else f'"{parte}"' in fuente
    if parte in _SUBCOMANDOS_AJENOS.get(modulo, ()) or parte.isdigit():
        return True
    if "/" in parte or parte.endswith(_EXTENSIONES_DE_RUTA):
        return parte in clon or _no_versionada(parte) is not None or _esta_en(parte, corpus)
    return _esta_en(parte, corpus)


def _resuelve(codigo: str, clon: set[str], corpus: str) -> bool:
    if len(codigo) < 2:
        return False
    sin_marcadores = re.sub(r"<[^>]*>", " ", codigo)
    if codigo in _PROGRAMAS_AJENOS or _esta_en(codigo, corpus):
        return True
    partes = sin_marcadores.split()
    if "*" in codigo and "/" in codigo:  # una ruta con comodín: algún archivo del clon la cumple
        return any(fnmatch.fnmatch(ruta, codigo) for ruta in clon)
    if codigo in _ajustes_de_duckdb() or (len(codigo) > 8 and codigo.encode("utf-8") in _binario_de_duckdb()):
        return True
    asignacion = re.fullmatch(r"([A-Z][A-Z0-9_]+)=(.*)", sin_marcadores.strip())
    if asignacion:  # NOMBRE=valor: el nombre y, si lo trae, el valor
        nombre, valor = (grupo.strip() for grupo in asignacion.groups())
        return nombre in corpus and (not valor or valor in corpus)
    if partes and partes[0].startswith("--server."):  # el valor de las opciones de Streamlit lo ata otra prueba
        return partes[0] in corpus
    if partes and partes[0].startswith("--"):  # --bandera VALOR: la bandera y su valor, sin distinguir mayúsculas
        minusculas = _en_minusculas(corpus)
        return all(parte.casefold() in minusculas for parte in partes)
    if "-m" in partes:  # python -m modulo [banderas y rutas]
        posicion = partes.index("-m")
        modulo, resto = partes[posicion + 1], partes[posicion + 2:]
        archivo = RAIZ / (modulo.replace(".", "/") + ".py")
        if modulo not in _MODULOS_AJENOS and not archivo.is_file():
            return False
        fuente = None if modulo in _MODULOS_AJENOS else archivo.read_text(encoding="utf-8")
        return all(_argumento_existe(modulo, parte, fuente, clon, corpus) for parte in resto)
    if len(partes) > 1:  # un comando o una frase: cada palabra existe en algún lado, salvo los programas ajenos
        return all(parte in _PROGRAMAS_AJENOS or _esta_en(parte, corpus) for parte in partes)
    if re.fullmatch(r"[A-Za-z_][\w]*\.[A-Za-z_]\w*(?:\(\))?", codigo):  # agente.TOPES, privacidad.enmascarar
        nombre, atributo = codigo.removesuffix("()").split(".", 1)
        try:
            return hasattr(importlib.import_module(nombre), atributo) or hasattr(
                importlib.import_module(f"agente.{nombre}"), atributo)
        except ImportError:
            return False
    return False


# Lo que el README cita y no está en el clon como texto, con el motivo y dónde se ata.
_SIN_ORIGEN_EN_EL_CLON = {
    "http://127.0.0.1:8501": (
        "la dirección de la interfaz: test_la_configuracion_de_streamlit_que_cita_el_readme_es_la_que_usa_un_clon"
    ),
    "~/.duckdb/extensions/v2.0.0/windows_amd64/": "la carpeta de la copia de fts: tests/test_fts.py",
    "python -m eval.correr --help": (
        "argparse la agrega: test_el_readme_documenta_el_comando_de_evaluacion_y_las_banderas_que_nombra"
    ),
}


def test_cada_codigo_en_linea_del_readme_nombra_algo_que_existe_en_el_clon(texto, clon, corpus):
    codigos = sorted(set(re.findall(r"`([^`\n]+)`", texto)))
    assert len(codigos) > 40, "el README cita pocos códigos: ¿cambió el formato?"
    rutas = set(_rutas_citadas(texto, clon))
    sin_origen = [
        codigo for codigo in codigos
        if codigo not in rutas and codigo not in _SIN_ORIGEN_EN_EL_CLON and not _resuelve(codigo, clon, corpus)
    ]
    assert sin_origen == [], "estos códigos del README no existen en el clon"


# Entre «comillas» el README pone textos del programa (una etiqueta de la interfaz, un alias) y ejemplos suyos. Los de
# esta lista son lo segundo: el ejemplo está en el README y en ningún otro archivo.
_EJEMPLOS_DEL_README = {
    "¿Cuánto vendió Tienda Cumbres en julio?": "una pregunta de ejemplo: las tiendas existen (prueba de abajo)",
    "¿Cuántos tickets siguen sin cerrar en Tienda Valle Norte?": "una pregunta de ejemplo: las tiendas existen",
    "¿Qué dice la política sobre la merma de los lácteos?": "una pregunta de ejemplo: POL-OPS-014 trata la merma",
}


def test_los_textos_entre_comillas_del_readme_son_del_programa_o_ejemplos_declarados(texto, corpus):
    titulos = {titulo for _, titulo in _encabezados(texto)}
    del_programa = corpus.lower() + "\n" + "\n".join(reintentos.MENSAJES_DE_FALLA.values()).lower()
    citados = sorted(set(re.findall(r"«([^»]+)»", texto)))
    assert len(citados) > 6, "el README cita pocos textos: ¿cambió el formato?"
    sin_origen = [
        cita for cita in citados
        if cita not in _EJEMPLOS_DEL_README and cita not in titulos and cita.lower() not in del_programa
    ]
    assert sin_origen == [], "estos textos entre comillas del README no están en el programa ni en los ejemplos"
    assert not [cita for cita in _EJEMPLOS_DEL_README if cita not in citados], "un ejemplo ya no está en el README"
    # Los ejemplos de pregunta nombran tiendas y un tema que existen.
    tiendas = (DIR_DATOS / "tiendas.csv").read_text(encoding="utf-8")
    assert "Tienda Cumbres" in tiendas and "Tienda Valle Norte" in tiendas
    assert "merma" in (RAIZ / "politicas_md" / "POL-OPS-014.md").read_text(encoding="utf-8").lower()
    assert texto_tickets.es_sospechoso("¿Cuánto vendió Tienda Cumbres en julio?") is False


# ------------------------------------------------------------------ bloques de código

_INTERPRETES = {"python", "python3", ".venv/bin/python", ".venv/Scripts/python"}


def _lineas_de_codigo(contenido: str) -> list[str]:
    """Las líneas de los bloques de código, con sangría de 4 espacios o entre cercas, sin espacios a los lados."""
    lineas, en_cerca = [], False
    for linea in contenido.split("\n"):
        if linea.lstrip().startswith("```"):
            en_cerca = not en_cerca
        elif linea.strip() and (en_cerca or linea.startswith("    ")):
            lineas.append(linea.strip())
    return lineas


def _comando_existe(linea: str, clon: set[str], corpus: str) -> bool:
    """`python start.py`, `python -m modulo --bandera`, `sha256sum -t datos/*`: todo existe."""
    if "|" in linea:
        return all(_comando_existe(trozo.strip(), clon, corpus) for trozo in linea.split("|"))
    partes = linea.replace("\\", "/").split()
    programa, resto = partes[0], partes[1:]
    if programa in _INTERPRETES:
        if resto[:1] == ["-m"]:
            return _resuelve(" ".join(["python", *resto]), clon, corpus)
        return bool(resto) and resto[0] in clon  # python start.py
    if programa == "sha256sum":
        if not resto:  # el lado derecho de una tubería: lee lo que le llega
            return True
        banderas = [parte for parte in resto if parte.startswith("-")]
        patrones = [parte for parte in resto if not parte.startswith("-")]
        return banderas == ["-t"] and all(any(fnmatch.fnmatch(ruta, patron) for ruta in clon) for patron in patrones)
    return False


def test_cada_linea_de_los_bloques_de_codigo_es_un_comando_que_existe(texto, clon, corpus):
    lineas = _lineas_de_codigo(texto)
    assert len(lineas) > 5, "el README tiene pocos bloques de código: ¿cambió el formato?"
    assert [linea for linea in lineas if not _comando_existe(linea, clon, corpus)] == []


def _interprete_del_entorno(sistema: str) -> str:
    """La ruta del Python de .venv que arma start.py para «nt» (Windows) o «posix», sin el «.exe»."""
    anterior = os.name
    os.name = sistema
    try:
        return start._python_del_entorno(PurePosixPath(".")).as_posix().removesuffix(".exe")
    finally:
        os.name = anterior


def test_los_interpretes_del_entorno_que_cita_el_readme_son_los_que_arma_start(texto):
    esperadas = {_interprete_del_entorno("nt"), _interprete_del_entorno("posix")}
    assert esperadas == {".venv/Scripts/python", ".venv/bin/python"}
    dichas = {ruta.replace("\\", "/") for ruta in re.findall(r"\.venv[\\/]\w+[\\/]\w+", texto)}
    assert dichas == esperadas
