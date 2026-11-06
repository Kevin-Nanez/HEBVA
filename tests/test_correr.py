"""El comando de evaluación (T5.3, C5.2, C6.1, D12, D25): con TestModel y FunctionModel, sin red y sin llaves.

La base se arma con asegurar_base() en un directorio temporal y sin el índice de texto, como en test_ciclo_sin_fts, para
que corra en un equipo sin la extensión fts; lo que depende de BM25 está en test_recall_del_set.py.

Las preguntas de los bancos de ensayo son inventadas (G11): lo que viene del banco real se lee de eval/preguntas.yaml.
"""

import dataclasses
import json
import re
import shutil
import statistics
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage
from pydantic_evals import Case, Dataset

import app
from agente import agente, consultas, construir, manifiesto, registro
from agente import preguntas as modulo_preguntas
from agente.ajustes_modelo import AJUSTES, AJUSTES_BASE
from agente.reintentos import FallaDelModelo
from eval import banco, correr, evaluadores, juez, reporte
from eval.evaluadores import FragmentoRecuperado, HerramientaCorrida, ResultadoPrompt, TurnoEvaluado
from eval.fixtures import copias
from tests.conftest import requiere_git, requiere_reportes

SIN_CIFRA = "No tengo ese dato."
CUMBRES_JULIO = "1,810,013.00"
VALLE_AGOSTO = "2,163,062.36"
LOMAS_AGOSTO = "833,131.19"
LOMAS_JULIO = "821,337.88"
TRIMESTRE = "17,228,300.26"


# ------------------------------------------------------------------ la base sin índice de texto


def _palabras_vacias_sin_indice(con, palabras_vacias):
    palabras = sorted(set(construir.leer_palabras_vacias(palabras_vacias)))
    con.execute(f"CREATE TABLE {construir.TABLA_PALABRAS_VACIAS} (sw VARCHAR PRIMARY KEY)")
    con.executemany(f"INSERT INTO {construir.TABLA_PALABRAS_VACIAS} VALUES (?)", [[palabra] for palabra in palabras])


@pytest.fixture(scope="module")
def sin_fts():
    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(construir, "cargar_fts", lambda con, instalar=True: None)
        parche.setattr(consultas, "cargar_fts", lambda con, instalar=False: None)
        parche.setattr(construir, "crear_indice_texto", _palabras_vacias_sin_indice)
        yield


@pytest.fixture(scope="module")
def base(sin_fts, tmp_path_factory):
    ruta = tmp_path_factory.mktemp("correr") / "tienda.duckdb"
    construir.asegurar_base(construir.DIR_DATOS, ruta)
    return ruta


# ------------------------------------------------------------------ bancos de ensayo


def sencilla(identificador, prompt, **campos):
    base_campos = {
        "id": identificador,
        "bloque": "normal_sencilla",
        "prompt": prompt,
        "categoria": "cifra_directa",
        "tipo_respuesta": "cifra",
        "estado_esperado": "ok",
        "respuesta_esperada": "x",
        "cifras_esperadas": [],
        "secciones_esperadas": [],
        "herramientas_esperadas": [],
        "parametros_esperados": {},
        "criterio": "x",
        "negativa": False,
    }
    return {**base_campos, **campos}


def turno_de(identificador, prompt, **campos):
    datos = sencilla(identificador, prompt, categoria="varios_turnos", **campos)
    datos.pop("bloque")
    return datos


@pytest.fixture
def escribir_banco(tmp_path):
    def escribir(*prompts):
        ruta = tmp_path / "banco.yaml"
        ruta.write_text(yaml.safe_dump({"prompts": list(prompts)}, allow_unicode=True), encoding="utf-8")
        return ruta

    return escribir


@pytest.fixture
def lanzar(tmp_path, base):
    """Corre el comando con los dobles de esta prueba: nada toca logs/, eval/reportes/ ni build/."""

    def _lanzar(banco_ruta, *argumentos, modelo=None, **opciones):
        modelo = modelo or TestModel(call_tools=[], custom_output_text=SIN_CIFRA)
        return correr.main(
            ["--banco", str(banco_ruta), *argumentos],
            modelo=modelo,
            archivo_registro=opciones.pop("archivo_registro", tmp_path / "turnos.jsonl"),
            dir_reportes=opciones.pop("dir_reportes", tmp_path / "reportes"),
            base=base,
            raiz_copias=opciones.pop("raiz_copias", tmp_path / "copias"),
            **opciones,
        )

    return _lanzar


@pytest.fixture
def leer_reporte(tmp_path):
    def leer():
        archivos = sorted((tmp_path / "reportes").glob("*.json"))
        assert len(archivos) == 1, archivos
        return json.loads(archivos[0].read_text(encoding="utf-8"))

    return leer


def texto_markdown(tmp_path):
    archivos = sorted((tmp_path / "reportes").glob("*.md"))
    assert len(archivos) == 1, archivos
    return archivos[0].read_text(encoding="utf-8")


def _ultima_pregunta(mensajes):
    for mensaje in reversed(mensajes):
        for parte in mensaje.parts:
            if isinstance(parte, UserPromptPart):
                return parte.content
    return ""


def _ya_corrio_una_herramienta(mensajes):
    ultimo = mensajes[-1]
    return any(isinstance(parte, ToolReturnPart) for parte in ultimo.parts)


class Guion:
    """Un modelo simulado: por pregunta, la herramienta que pide y el texto que redacta. Anota las preguntas que recibe.

    Una entrada es el texto, (herramienta, texto), una excepción o una función que recibe cuántas veces llegó esa pregunta.
    """

    def __init__(self, guion, *, por_omision=SIN_CIFRA):
        self.guion = guion
        self.por_omision = por_omision
        self.preguntas = []
        self.historiales = []
        self._pendiente = ""
        self.modelo = FunctionModel(self._contestar, model_name="guion")

    def _contestar(self, mensajes, info: AgentInfo):
        if _ya_corrio_una_herramienta(mensajes):
            return ModelResponse(parts=[TextPart(self._pendiente)])
        pregunta = _ultima_pregunta(mensajes)
        entrada = self.guion.get(pregunta, self.por_omision)
        if isinstance(entrada, Exception):
            raise entrada
        if callable(entrada):
            entrada = entrada(self.preguntas.count(pregunta))
        self.preguntas.append(pregunta)
        self.historiales.append(len(mensajes) - 1)
        if isinstance(entrada, tuple):
            herramienta, self._pendiente = entrada
            return ModelResponse(parts=[ToolCallPart(herramienta[0], herramienta[1])])
        return ModelResponse(parts=[TextPart(entrada)])


VENTAS_T01_AGOSTO = ("ventas", {"tiendas": ["T01"], "desde": "2026-08-01", "hasta": "2026-08-31"})
VENTAS_T02_JULIO = ("ventas", {"tiendas": ["T02"], "desde": "2026-07-01", "hasta": "2026-07-31"})
VENTAS_T04_AGOSTO = ("ventas", {"tiendas": ["T04"], "desde": "2026-08-01", "hasta": "2026-08-31"})
VENTAS_T04_JULIO = ("ventas", {"tiendas": ["T04"], "desde": "2026-07-01", "hasta": "2026-07-31"})
PARAMETROS_T01 = {"ventas": {"tiendas": ["T01"], "desde": "2026-08-01", "hasta": "2026-08-31"}}

CIFRA_T01 = {
    "cifras_esperadas": [VALLE_AGOSTO],
    "herramientas_esperadas": ["ventas"],
    "parametros_esperados": PARAMETROS_T01,
}


# ------------------------------------------------------------------ el banco real


@pytest.fixture(scope="module")
def reales():
    return banco.leer_banco()


def test_el_banco_real_se_lee_con_los_100_prompts_de_d25(reales):
    assert len(reales) == 100
    bloques = [prompt.bloque for prompt in reales]
    assert bloques.count("trampa_sencilla") == 50
    assert bloques.count("normal_sencilla") == 25
    assert bloques.count("conversacion") == 5
    assert bloques.count("dos_normales") == 10
    assert bloques.count("trampa_y_normal") == 5
    assert bloques.count("dos_trampas") == 5


def test_por_omision_corren_los_30_del_nucleo_y_completo_los_100(reales):
    assert len(banco.seleccionar(reales)) == 30
    assert all(prompt.nucleo for prompt in banco.seleccionar(reales))
    assert len(banco.seleccionar(reales, completo=True)) == 100


def test_una_corrida_completa_son_129_turnos(reales):
    assert sum(len(prompt.pasos) for prompt in reales) == 129


def test_variantes_suma_2_por_cada_sencilla_del_alcance(reales):
    con_variantes = banco.seleccionar(reales, completo=True, variantes=True)
    extra = [prompt for prompt in con_variantes if prompt.variante_de]
    assert len(extra) == 150
    assert len(banco.seleccionar(reales, variantes=True)) == 30 + 2 * 27
    primera = extra[0]
    assert primera.pasos[0].texto != next(p for p in reales if p.id == primera.variante_de).pasos[0].texto
    assert primera.pasos[0].preguntas[0].cifras_esperadas == next(
        p for p in reales if p.id == primera.variante_de
    ).pasos[0].preguntas[0].cifras_esperadas


def test_una_parte_hereda_su_esperada_de_la_sencilla(reales):
    por_id = {prompt.id: prompt for prompt in reales}
    primera, segunda = por_id["DN-01"].pasos
    assert primera.preguntas[0].cifras_esperadas == por_id["N-01"].pasos[0].preguntas[0].cifras_esperadas
    assert segunda.preguntas[0].categoria == por_id["N-20"].pasos[0].preguntas[0].categoria


def test_con_signos_son_dos_turnos_iguales_a_los_de_la_interfaz_y_sin_signos_uno(reales):
    for prompt in reales:
        if prompt.forma == "con_signos":
            assert len(prompt.pasos) == 2, prompt.id
            original = yaml.safe_load(banco.BANCO.read_text(encoding="utf-8"))["prompts"]
            texto = next(p["prompt"] for p in original if p["id"] == prompt.id)
            assert tuple(paso.texto for paso in prompt.pasos) == app._preguntas_del_mensaje(texto), prompt.id
        if prompt.forma == "sin_signos":
            assert len(prompt.pasos) == 1, prompt.id
            assert len(prompt.pasos[0].preguntas) == 2, prompt.id


def test_la_conversacion_de_dos_sesiones_reparte_sus_turnos(reales):
    doble = next(prompt for prompt in reales if prompt.prueba == "dos_sesiones")
    assert doble.sesiones == 2
    assert [paso.sesion for paso in doble.pasos] == [1, 2, 1]


def test_el_turno_que_repite_nombra_al_primero(reales):
    larga = next(prompt for prompt in reales if prompt.prueba == "tope_historial")
    assert len(larga.pasos) == 12
    assert larga.pasos[-1].preguntas[0].repite == larga.pasos[0].preguntas[0].id


def test_la_copia_de_pt25_llega_a_su_pregunta(reales):
    pt25 = next(prompt for prompt in reales if prompt.id == "PT-25")
    assert pt25.copia == "D"
    assert pt25.pasos[0].preguntas[0].copia == "D"


def test_un_compuesto_cuyas_partes_no_cuadran_con_separar_es_un_banco_invalido(escribir_banco):
    malo = {
        "id": "DN-99",
        "bloque": "dos_normales",
        "forma": "con_signos",
        "prompt": "una sola pregunta sin signos de cierre",
        "criterio": "x",
        "partes": [{"id": "A", **sencilla("A", "uno")}, {"id": "B", **sencilla("B", "dos")}],
    }
    with pytest.raises(banco.BancoInvalido, match="DN-99"):
        banco.leer_banco(escribir_banco(malo))


# ------------------------------------------------------------------ opciones del comando


def test_corridas_va_por_omision_en_2_y_nucleo_por_omision(escribir_banco, lanzar, leer_reporte):
    ruta = escribir_banco(
        sencilla("N-1", "pregunta uno de ensayo", nucleo=True),
        sencilla("N-2", "pregunta dos de ensayo"),
    )
    assert lanzar(ruta) == 0
    salida = leer_reporte()
    assert salida["meta"]["corridas_pedidas"] == 2
    assert salida["meta"]["alcance"] == "nucleo"
    assert salida["meta"]["n_prompts"] == 1
    assert len(salida["acierto"]["alcance"]["por_corrida"]) == 2


def test_completo_corre_todos_y_el_rango_del_nucleo_sale_de_las_mismas_corridas(escribir_banco, lanzar, leer_reporte):
    guion = Guion({"pregunta uno de ensayo": SIN_CIFRA})
    ruta = escribir_banco(
        sencilla("N-1", "pregunta uno de ensayo", nucleo=True),
        sencilla("N-2", "pregunta dos de ensayo"),
    )
    assert lanzar(ruta, "--completo", modelo=guion.modelo) == 0
    assert guion.preguntas.count("pregunta uno de ensayo") == 2
    salida = leer_reporte()
    assert salida["meta"]["n_prompts"] == 2
    assert salida["acierto"]["nucleo"]["por_corrida"][0]["n"] == 1
    assert salida["acierto"]["completo"]["por_corrida"][0]["n"] == 2


def test_variantes_se_suman_pero_no_cuentan_en_el_rango(escribir_banco, lanzar, leer_reporte):
    ruta = escribir_banco(
        sencilla("N-1", "pregunta uno de ensayo", nucleo=True, variantes=["parafrasis uno de ensayo", "otra parafrasis uno"]),
    )
    guion = Guion({})
    assert lanzar(ruta, "--variantes", "--corridas", "1", modelo=guion.modelo) == 0
    assert set(guion.preguntas) == {"pregunta uno de ensayo", "parafrasis uno de ensayo", "otra parafrasis uno"}
    salida = leer_reporte()
    assert salida["acierto"]["alcance"]["por_corrida"][0]["n"] == 1
    assert salida["variantes"]["por_corrida"][0]["n"] == 2


@pytest.mark.parametrize("argumentos", [["--corridas", "0"], ["--limite-minutos", "0"], ["--limite-minutos", "-1"]])
def test_un_argumento_fuera_de_rango_no_corre(escribir_banco, lanzar, argumentos):
    with pytest.raises(SystemExit):
        lanzar(escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True)), *argumentos)


# ------------------------------------------------------------------ el mismo camino que la interfaz


def test_llama_a_responder_con_el_registro_el_origen_y_la_categoria(escribir_banco, lanzar, tmp_path):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True, categoria="tickets"))
    lanzar(ruta, "--corridas", "1")
    lineas = list(registro.leer(tmp_path / "turnos.jsonl"))
    assert len(lineas) == 1
    assert lineas[0]["origen"] == "evaluacion"
    assert lineas[0]["categoria"] == "tickets"
    assert lineas[0]["pregunta"] == "pregunta uno de ensayo"


def test_sin_modelo_inyectado_responder_recibe_none_para_leer_el_env(escribir_banco, monkeypatch, tmp_path, base):
    recibidos = []
    real = agente.responder

    def espia(pregunta, historial, **opciones):
        recibidos.append(dict(opciones))
        opciones["modelo"] = TestModel(call_tools=[], custom_output_text=SIN_CIFRA)
        return real(pregunta, historial, **opciones)

    monkeypatch.setattr(agente, "responder", espia)
    monkeypatch.setattr(agente, "_modelo_configurado", lambda: "guion:simulado")
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    correr.main(
        ["--banco", str(ruta), "--corridas", "1"],
        archivo_registro=tmp_path / "turnos.jsonl",
        dir_reportes=tmp_path / "reportes",
        base=base,
        raiz_copias=tmp_path / "copias",
    )
    assert recibidos[0]["modelo"] is None
    assert recibidos[0]["origen"] == "evaluacion"
    assert recibidos[0]["categoria"] == "cifra_directa"
    assert recibidos[0]["archivo_registro"] == tmp_path / "turnos.jsonl"
    assert recibidos[0]["ajustes"] is AJUSTES


def test_con_id_de_modelo_carga_el_env_antes_de_pasarlo(escribir_banco, monkeypatch, tmp_path, base):
    eventos = []
    monkeypatch.setattr(correr, "load_dotenv", lambda: eventos.append("env"))
    recibidos = []
    real = agente.responder

    def espia(pregunta, historial, **opciones):
        recibidos.append(opciones["modelo"])
        eventos.append("responder")
        opciones["modelo"] = TestModel(call_tools=[], custom_output_text=SIN_CIFRA)
        return real(pregunta, historial, **opciones)

    monkeypatch.setattr(agente, "responder", espia)
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    correr.main(
        ["--banco", str(ruta), "--corridas", "1", "--modelo", "anthropic:un-modelo"],
        archivo_registro=tmp_path / "turnos.jsonl",
        dir_reportes=tmp_path / "reportes",
        base=base,
        raiz_copias=tmp_path / "copias",
    )
    assert recibidos == ["anthropic:un-modelo"]
    assert eventos == ["env", "responder"], "el .env se carga antes de la primera llamada"


def test_configuracion_base_usa_ajustes_base_y_el_reporte_lo_dice(escribir_banco, monkeypatch, lanzar, leer_reporte):
    recibidos = []
    real = agente.responder

    def espia(pregunta, historial, **opciones):
        recibidos.append(opciones["ajustes"])
        return real(pregunta, historial, **opciones)

    monkeypatch.setattr(agente, "responder", espia)
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", "--configuracion", "base")
    assert recibidos == [AJUSTES_BASE]
    salida = leer_reporte()
    assert salida["meta"]["configuracion"] == "base"
    assert salida["meta"]["ajustes"]["cache_claude"] is False


def test_la_traza_del_comando_coincide_con_la_de_la_interfaz(escribir_banco, monkeypatch, lanzar, leer_reporte, tmp_path, base):
    """K32 y C5.2: mismo prompt, mismas herramientas, mismo modelo y mismos tokens que app.atender()."""
    modelo = TestModel(call_tools=[], custom_output_text=SIN_CIFRA)
    real = agente.responder

    def con_el_doble(pregunta, historial, **opciones):
        return real(
            pregunta, historial, **{**opciones, "modelo": modelo, "base": base, "archivo_registro": tmp_path / "ui.jsonl"}
        )

    monkeypatch.setattr(agente, "responder", con_el_doble)
    mostrado = app.atender("pregunta uno de ensayo", app.modulo_historial.Historial())
    monkeypatch.setattr(agente, "responder", real)
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=modelo)
    turno = leer_reporte()["turnos"][0]
    traza = mostrado.respuesta.traza
    assert turno["prompt_sha256"] == traza.prompt_sha256
    assert turno["herramientas_sha256"] == traza.herramientas_sha256
    assert turno["modelo"] == traza.modelo
    assert turno["entrada_tokens"] == traza.entrada_tokens
    assert turno["salida_tokens"] == traza.salida_tokens
    assert turno["texto"] == mostrado.texto


def test_un_turno_se_reproduce_desde_su_linea_del_registro(escribir_banco, lanzar, leer_reporte, tmp_path, base):
    """E16: lo que el reporte dice de un turno se encuentra por su turno_id y se repite con registro.reproducir()."""
    modelo = TestModel(call_tools=[], custom_output_text=SIN_CIFRA)
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=modelo)
    turno = leer_reporte()["turnos"][0]
    linea = registro.buscar_turno(tmp_path / "turnos.jsonl", turno["turno_id"])
    assert linea is not None
    repetido = registro.reproducir(linea, modelo=modelo, base=base)
    assert repetido.texto == turno["texto"]
    assert repetido.traza.prompt_sha256 == turno["prompt_sha256"]


def test_la_cobertura_la_contesta_el_manifiesto_sin_llamar_a_responder(escribir_banco, monkeypatch, lanzar, leer_reporte, tmp_path):
    frase = next(
        candidata for candidata in ("que sabes", "qué sabes", "ayuda") if manifiesto.pide_la_cobertura(candidata)
    )
    llamadas = []
    real = agente.responder
    monkeypatch.setattr(agente, "responder", lambda *a, **o: (llamadas.append(a), real(*a, **o))[1])
    ruta = escribir_banco(sencilla("N-1", frase, nucleo=True))
    lanzar(ruta, "--corridas", "1")
    assert not llamadas
    turno = leer_reporte()["turnos"][0]
    assert turno["del_manifiesto"] is True
    assert turno["n_llamadas"] == 0
    linea = registro.buscar_turno(tmp_path / "turnos.jsonl", turno["turno_id"])
    assert linea["origen"] == "evaluacion"
    assert linea["gen_ai.request.model"] == manifiesto.MODELO


def test_la_vuelta_del_manifiesto_no_entra_al_historial(escribir_banco, lanzar):
    frase = next(c for c in ("que sabes", "qué sabes", "ayuda") if manifiesto.pide_la_cobertura(c))
    conversacion = {
        "id": "CV-9",
        "bloque": "conversacion",
        "nucleo": True,
        "prueba": "conserva_filtro",
        "turnos": [turno_de("CV-9-1", frase), turno_de("CV-9-2", "pregunta dos de ensayo")],
    }
    guion = Guion({})
    lanzar(escribir_banco(conversacion), "--corridas", "1", modelo=guion.modelo)
    assert guion.preguntas == ["pregunta dos de ensayo"]
    assert guion.historiales == [0]


# ------------------------------------------------------------------ conversaciones, sesiones y compuestos


def test_una_conversacion_usa_un_historial_y_cada_prompt_uno_nuevo(escribir_banco, lanzar, leer_reporte):
    conversacion = {
        "id": "CV-1",
        "bloque": "conversacion",
        "nucleo": True,
        "prueba": "conserva_filtro",
        "turnos": [turno_de("CV-1-1", "turno uno de ensayo"), turno_de("CV-1-2", "turno dos de ensayo")],
    }
    otra = sencilla("N-1", "pregunta suelta de ensayo", nucleo=True)
    guion = Guion({})
    lanzar(escribir_banco(conversacion, otra), "--corridas", "1", modelo=guion.modelo)
    assert guion.preguntas == ["turno uno de ensayo", "turno dos de ensayo", "pregunta suelta de ensayo"]
    assert guion.historiales == [0, 2, 0]
    turnos = leer_reporte()["turnos"]
    assert turnos[0]["id_sesion"] == turnos[1]["id_sesion"]
    assert turnos[2]["id_sesion"] != turnos[0]["id_sesion"]
    assert [turno["n_mensajes_historial"] for turno in turnos] == [0, 2, 0]


def test_una_nueva_corrida_abre_sesiones_nuevas(escribir_banco, lanzar, leer_reporte):
    ruta = escribir_banco(sencilla("N-1", "pregunta suelta de ensayo", nucleo=True))
    lanzar(ruta, modelo=Guion({}).modelo)
    turnos = leer_reporte()["turnos"]
    assert len(turnos) == 2
    assert turnos[0]["id_sesion"] != turnos[1]["id_sesion"]


def dos_sesiones_de_ensayo():
    return {
        "id": "CV-2",
        "bloque": "conversacion",
        "nucleo": True,
        "sesiones": 2,
        "prueba": "dos_sesiones",
        "turnos": [
            turno_de(
                "CV-2-1", "cuanto vendio lomas en ensayo", sesion=1, cifras_esperadas=[LOMAS_AGOSTO],
                herramientas_esperadas=["ventas"], parametros_esperados={"ventas": {"tiendas": ["T04"]}},
            ),
            turno_de("CV-2-2", "y en otro mes de ensayo", sesion=2, tipo_respuesta="aclaracion"),
            turno_de(
                "CV-2-3", "y en julio de ensayo", sesion=1, cifras_esperadas=[LOMAS_JULIO],
                herramientas_esperadas=["ventas"], parametros_esperados={"ventas": {"tiendas": ["T04"]}},
            ),
        ],
    }


def test_dos_sesiones_abiertas_a_la_vez_no_se_mezclan(escribir_banco, lanzar, leer_reporte):
    guion = Guion(
        {
            "cuanto vendio lomas en ensayo": (VENTAS_T04_AGOSTO, f"{LOMAS_AGOSTO} MXN"),
            "y en otro mes de ensayo": "¿De qué tienda?",
            "y en julio de ensayo": (VENTAS_T04_JULIO, f"{LOMAS_JULIO} MXN"),
        }
    )
    assert lanzar(escribir_banco(dos_sesiones_de_ensayo()), "--corridas", "1", modelo=guion.modelo) == 0
    salida = leer_reporte()
    turnos = salida["turnos"]
    assert [turno["n_mensajes_historial"] for turno in turnos] == [0, 0, 2]
    assert turnos[0]["id_sesion"] == turnos[2]["id_sesion"]
    assert turnos[0]["id_sesion"] != turnos[1]["id_sesion"]
    assert salida["preguntas"][0]["en_todas"] is True


def compuesto(identificador, forma, prompt, *partes, bloque="dos_normales"):
    return {
        "id": identificador,
        "bloque": bloque,
        "forma": forma,
        "nucleo": True,
        "prompt": prompt,
        "criterio": "x",
        "partes": [{"de": parte} for parte in partes],
    }


def test_con_signos_se_separa_en_dos_turnos_con_un_historial_compartido(escribir_banco, lanzar, leer_reporte):
    ruta = escribir_banco(
        sencilla("N-1", "cuanto vendio valle en ensayo", **CIFRA_T01),
        sencilla("N-2", "cuanto vendio cumbres en ensayo", cifras_esperadas=[CUMBRES_JULIO]),
        compuesto("DN-1", "con_signos", "¿cuanto vendio valle en ensayo? ¿cuanto vendio cumbres en ensayo?", "N-1", "N-2"),
    )
    guion = Guion(
        {
            "¿cuanto vendio valle en ensayo?": (VENTAS_T01_AGOSTO, f"{VALLE_AGOSTO} MXN"),
            "¿cuanto vendio cumbres en ensayo?": (VENTAS_T02_JULIO, f"{CUMBRES_JULIO} MXN"),
        }
    )
    assert lanzar(ruta, "--corridas", "1", modelo=guion.modelo) == 0
    assert guion.preguntas == ["¿cuanto vendio valle en ensayo?", "¿cuanto vendio cumbres en ensayo?"]
    assert guion.historiales == [0, 2]
    salida = leer_reporte()
    assert salida["por_bloque"]["dos_normales"]["por_corrida"][0] == {"corrida": 1, "aciertos": 1, "n": 1, "porcentaje": 100.0}


def test_con_signos_reprueba_si_falla_una_parte(escribir_banco, lanzar, leer_reporte):
    ruta = escribir_banco(
        sencilla("N-1", "cuanto vendio valle en ensayo", **CIFRA_T01),
        sencilla("N-2", "cuanto vendio cumbres en ensayo", cifras_esperadas=[CUMBRES_JULIO]),
        compuesto("DN-1", "con_signos", "¿cuanto vendio valle en ensayo? ¿cuanto vendio cumbres en ensayo?", "N-1", "N-2"),
    )
    guion = Guion({"¿cuanto vendio valle en ensayo?": (VENTAS_T01_AGOSTO, f"{VALLE_AGOSTO} MXN")})
    lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    salida = leer_reporte()
    assert salida["por_bloque"]["dos_normales"]["por_corrida"][0]["aciertos"] == 0
    assert salida["por_categoria"]["cifra_directa"]["por_corrida"][0] == {"corrida": 1, "aciertos": 1, "n": 2, "porcentaje": 50.0}


def test_sin_signos_es_un_turno_que_trae_lo_de_las_dos_partes(escribir_banco, lanzar, leer_reporte):
    ruta = escribir_banco(
        sencilla("N-1", "cuanto vendio valle en ensayo", **CIFRA_T01),
        sencilla("N-2", "cuanto vendio cumbres en ensayo", cifras_esperadas=[CUMBRES_JULIO]),
        compuesto("DN-2", "sin_signos", "cuanto vendio valle en ensayo y cuanto cumbres", "N-1", "N-2"),
    )
    dos_llamadas = {
        "cuanto vendio valle en ensayo y cuanto cumbres": (VENTAS_T01_AGOSTO, f"{VALLE_AGOSTO} MXN y nada mas"),
    }
    guion = Guion(dos_llamadas)
    lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    assert guion.preguntas == ["cuanto vendio valle en ensayo y cuanto cumbres"]
    salida = leer_reporte()
    assert len(salida["turnos"]) == 1
    assert salida["por_bloque"]["dos_normales"]["por_corrida"][0]["aciertos"] == 0


def test_un_mensaje_con_mas_preguntas_que_el_maximo_solo_contesta_el_maximo(escribir_banco, lanzar, leer_reporte):
    trozos = [f"¿pregunta numero {n} de ensayo?" for n in range(1, modulo_preguntas.MAXIMO_PREGUNTAS + 2)]
    partes = [sencilla(f"N-{n}", f"pregunta suelta {n} de ensayo") for n in range(1, len(trozos) + 1)]
    ruta = escribir_banco(*partes, compuesto("DN-3", "con_signos", " ".join(trozos), *[p["id"] for p in partes]))
    guion = Guion({})
    lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    assert len(guion.preguntas) == modulo_preguntas.MAXIMO_PREGUNTAS
    salida = leer_reporte()
    assert salida["por_bloque"]["dos_normales"]["por_corrida"][0]["aciertos"] == 0


def test_un_prompt_con_copia_corre_con_su_base_y_sus_datos(escribir_banco, monkeypatch, lanzar, tmp_path):
    recibidos = []
    real = agente.responder

    def espia(pregunta, historial, **opciones):
        recibidos.append((opciones["base"], opciones["dir_datos"]))
        return real(pregunta, historial, **opciones)

    monkeypatch.setattr(agente, "responder", espia)
    ruta = escribir_banco(
        sencilla("PT-9", "pregunta con copia de ensayo", nucleo=True, copia="D"),
        sencilla("N-1", "pregunta sin copia de ensayo", nucleo=True),
    )
    lanzar(ruta, "--corridas", "1")
    armada = copias.ruta_de("D", tmp_path / "copias")
    assert recibidos[0] == (armada.base, armada.dir_datos)
    assert recibidos[1][0] != armada.base
    assert recibidos[1][1] == construir.DIR_DATOS


# ------------------------------------------------------------------ fallas: tope, excepción, modelo no disponible


def test_un_tope_cuenta_como_falla_y_la_corrida_sigue(escribir_banco, lanzar, leer_reporte, monkeypatch):
    real = agente.responder

    def con_tope(pregunta, historial, **opciones):
        if pregunta == "pregunta con tope de ensayo":
            opciones["topes"] = agente.Topes(solicitudes=0)
        return real(pregunta, historial, **opciones)

    monkeypatch.setattr(agente, "responder", con_tope)
    ruta = escribir_banco(
        sencilla("N-1", "pregunta con tope de ensayo", nucleo=True),
        sencilla("N-2", "pregunta normal de ensayo", nucleo=True),
    )
    assert lanzar(ruta, "--corridas", "1") == 0
    salida = leer_reporte()
    assert salida["meta"]["completo"] is True
    assert salida["preguntas"][0]["en_todas"] is False
    assert any("tope" in causa for causa in salida["preguntas"][0]["causas"])
    assert salida["preguntas"][1]["en_todas"] is True


def test_una_excepcion_de_responder_es_una_falla_con_su_tipo_y_la_corrida_sigue(escribir_banco, lanzar, leer_reporte):
    guion = Guion({"pregunta que truena de ensayo": UnexpectedModelBehavior("vacia con un texto que no debe salir")})
    ruta = escribir_banco(
        sencilla("N-1", "pregunta que truena de ensayo", nucleo=True),
        sencilla("N-2", "pregunta normal de ensayo", nucleo=True),
    )
    assert lanzar(ruta, "--corridas", "1", modelo=guion.modelo) == 0
    salida = leer_reporte()
    assert salida["turnos"][0]["estado"] == "excepcion"
    assert salida["turnos"][0]["error"] == "UnexpectedModelBehavior"
    assert "no debe salir" not in json.dumps(salida, ensure_ascii=False)
    assert salida["preguntas"][0]["en_todas"] is False
    assert salida["preguntas"][1]["en_todas"] is True


def test_si_el_modelo_no_esta_disponible_se_detiene_sin_guardar_y_reanudar_lo_repite(
    escribir_banco, lanzar, leer_reporte, tmp_path
):
    ruta = escribir_banco(
        sencilla("N-1", "pregunta buena de ensayo", nucleo=True),
        sencilla("N-2", "pregunta sin modelo de ensayo", nucleo=True),
        sencilla("N-3", "pregunta tres de ensayo", nucleo=True),
    )
    from pydantic_ai.exceptions import ModelHTTPError

    guion = Guion({"pregunta sin modelo de ensayo": ModelHTTPError(401, "guion", None)})
    codigo = lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    assert codigo == correr.SALIDA_SIN_MODELO
    assert guion.preguntas == ["pregunta buena de ensayo"]
    salida = leer_reporte()
    assert salida["meta"]["completo"] is False
    assert salida["meta"]["detenida"]
    assert salida["acierto"]["alcance"]["por_corrida"][0]["n"] == 1

    guion_bien = Guion({})
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=guion_bien.modelo) == 0
    assert guion_bien.preguntas == ["pregunta sin modelo de ensayo", "pregunta tres de ensayo"]
    assert leer_reporte()["meta"]["completo"] is True


def test_un_estado_sin_modelo_esperado_no_detiene_la_corrida(escribir_banco, monkeypatch, lanzar, leer_reporte):
    ruta = escribir_banco(
        sencilla("N-1", "pregunta sin fuente de ensayo", nucleo=True, copia="B", estado_esperado="fuente_no_disponible"),
        sencilla("N-2", "pregunta normal de ensayo", nucleo=True),
    )
    assert lanzar(ruta, "--corridas", "1") == 0
    salida = leer_reporte()
    assert salida["turnos"][0]["estado"] == "fuente_no_disponible"
    assert salida["preguntas"][0]["en_todas"] is True
    assert salida["meta"]["completo"] is True


# ------------------------------------------------------------------ reanudar y límite de minutos


def test_guarda_cada_prompt_al_terminarlo_y_reanudar_no_repite_llamadas(escribir_banco, lanzar, tmp_path):
    ruta = escribir_banco(
        sencilla("N-1", "pregunta uno de ensayo", nucleo=True),
        sencilla("N-2", "pregunta dos de ensayo", nucleo=True),
    )
    primero = Guion({})
    assert lanzar(ruta, modelo=primero.modelo) == 0
    assert len(primero.preguntas) == 4
    segundo = Guion({})
    assert lanzar(ruta, "--reanudar", modelo=segundo.modelo) == 0
    assert segundo.preguntas == []


def test_reanudar_con_mas_corridas_solo_corre_las_que_faltan(escribir_banco, lanzar, leer_reporte):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    segundo = Guion({})
    assert lanzar(ruta, "--reanudar", modelo=segundo.modelo) == 0
    assert segundo.preguntas == ["pregunta uno de ensayo"]
    assert len(leer_reporte()["acierto"]["alcance"]["por_corrida"]) == 2


def test_reanudar_con_el_completo_aprovecha_lo_que_ya_corrio_el_nucleo(escribir_banco, lanzar):
    ruta = escribir_banco(
        sencilla("N-1", "pregunta uno de ensayo", nucleo=True),
        sencilla("N-2", "pregunta dos de ensayo"),
    )
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    segundo = Guion({})
    assert lanzar(ruta, "--corridas", "1", "--completo", "--reanudar", modelo=segundo.modelo) == 0
    assert segundo.preguntas == ["pregunta dos de ensayo"]


def test_sin_reanudar_no_se_pierde_lo_ya_pagado(escribir_banco, lanzar, tmp_path):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    segundo = Guion({})
    lanzar(ruta, "--corridas", "1", modelo=segundo.modelo)
    assert segundo.preguntas == ["pregunta uno de ensayo"]
    guardados = list((tmp_path / "reportes" / "avance").glob("*.jsonl"))
    assert len(guardados) == 2


def test_reanudar_sin_avance_empieza_de_cero(escribir_banco, lanzar):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    guion = Guion({})
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=guion.modelo) == 0
    assert guion.preguntas == ["pregunta uno de ensayo"]


def test_reanudar_con_otro_banco_o_prompt_no_mezcla_y_lo_dice(escribir_banco, lanzar, capsys):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    capsys.readouterr()
    otro = escribir_banco(sencilla("N-1", "pregunta cambiada de ensayo", nucleo=True))
    guion = Guion({})
    assert lanzar(otro, "--corridas", "1", "--reanudar", modelo=guion.modelo) == correr.SALIDA_AVANCE_INVALIDO
    assert guion.preguntas == []
    assert "banco_sha256" in capsys.readouterr().err


def test_reanudar_con_otra_configuracion_no_mezcla(escribir_banco, lanzar):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    assert (
        lanzar(ruta, "--corridas", "1", "--reanudar", "--configuracion", "base", modelo=Guion({}).modelo)
        == correr.SALIDA_AVANCE_INVALIDO
    )


def test_una_linea_cortada_a_medio_escribir_no_rompe_reanudar(escribir_banco, lanzar, tmp_path):
    ruta = escribir_banco(
        sencilla("N-1", "pregunta uno de ensayo", nucleo=True),
        sencilla("N-2", "pregunta dos de ensayo", nucleo=True),
    )
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    avance = next((tmp_path / "reportes" / "avance").glob("*.jsonl"))
    contenido = avance.read_text(encoding="utf-8").splitlines(keepends=True)
    avance.write_text("".join(contenido[:-1]) + contenido[-1][: len(contenido[-1]) // 2], encoding="utf-8", newline="\n")
    segundo = Guion({})
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=segundo.modelo) == 0
    assert segundo.preguntas == ["pregunta dos de ensayo"]
    tercero = Guion({})
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=tercero.modelo) == 0
    assert tercero.preguntas == []


class Reloj:
    """Un reloj que avanza 60 s cada vez que el modelo contesta: una llamada es un minuto."""

    def __init__(self):
        self.ahora = 0.0

    def __call__(self):
        return self.ahora


def test_limite_de_minutos_se_detiene_despues_del_prompt_en_curso(escribir_banco, lanzar, leer_reporte):
    reloj = Reloj()
    conversacion = {
        "id": "CV-1",
        "bloque": "conversacion",
        "nucleo": True,
        "prueba": "conserva_filtro",
        "turnos": [turno_de("CV-1-1", "turno uno de ensayo"), turno_de("CV-1-2", "turno dos de ensayo")],
    }

    def contesta_y_avanza(_):
        reloj.ahora += 60
        return SIN_CIFRA

    guion = Guion(
        {
            "turno uno de ensayo": contesta_y_avanza,
            "turno dos de ensayo": contesta_y_avanza,
            "pregunta dos de ensayo": contesta_y_avanza,
        }
    )
    ruta = escribir_banco(conversacion, sencilla("N-2", "pregunta dos de ensayo", nucleo=True))
    codigo = lanzar(ruta, "--corridas", "1", "--limite-minutos", "1", modelo=guion.modelo, reloj=reloj)
    assert codigo == correr.SALIDA_LIMITE
    assert guion.preguntas == ["turno uno de ensayo", "turno dos de ensayo"], "termina la conversación en curso"
    salida = leer_reporte()
    assert salida["meta"]["completo"] is False
    assert "minutos" in salida["meta"]["detenida"]
    assert salida["acierto"]["alcance"]["por_corrida"][0]["n"] == 1

    otro = Guion({})
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=otro.modelo) == 0
    assert otro.preguntas == ["pregunta dos de ensayo"]
    assert leer_reporte()["meta"]["completo"] is True


def test_una_corrida_a_medias_no_entra_al_rango(escribir_banco, lanzar, leer_reporte):
    reloj = Reloj()

    def avanza(_):
        reloj.ahora += 60
        return SIN_CIFRA

    ruta = escribir_banco(
        sencilla("N-1", "pregunta uno de ensayo", nucleo=True),
        sencilla("N-2", "pregunta dos de ensayo", nucleo=True),
    )
    guion = Guion({"pregunta uno de ensayo": avanza, "pregunta dos de ensayo": avanza})
    assert lanzar(ruta, "--limite-minutos", "3", modelo=guion.modelo, reloj=reloj) == correr.SALIDA_LIMITE
    salida = leer_reporte()
    assert salida["meta"]["corridas_completas"] == 1
    assert salida["acierto"]["alcance"]["rango"]["corridas"] == 1
    por_id = {fila["id"]: fila for fila in salida["preguntas"]}
    assert por_id["N-1"]["en_todas"] is True
    assert por_id["N-2"]["por_corrida"] == [True]
    assert por_id["N-2"]["en_todas"] is False, "le falta una corrida"


# ------------------------------------------------------------------ el reporte


@pytest.fixture
def corrida_con_rango(escribir_banco, lanzar):
    """Dos corridas: N-1 acierta en las dos, N-2 solo en la primera y N-3 en ninguna."""

    def segun_corrida(veces):
        return (VENTAS_T01_AGOSTO, f"{VALLE_AGOSTO} MXN") if veces == 0 else SIN_CIFRA

    guion = Guion(
        {
            "pregunta buena de ensayo": (VENTAS_T01_AGOSTO, f"{VALLE_AGOSTO} MXN"),
            "pregunta inestable de ensayo": segun_corrida,
            "pregunta mala de ensayo": SIN_CIFRA,
        }
    )
    ruta = escribir_banco(
        sencilla("N-1", "pregunta buena de ensayo", nucleo=True, **CIFRA_T01),
        sencilla("N-2", "pregunta inestable de ensayo", nucleo=True, categoria="tickets", **CIFRA_T01),
        sencilla("PT-3", "pregunta mala de ensayo", bloque="trampa_sencilla", nucleo=True, **CIFRA_T01),
    )
    assert lanzar(ruta, modelo=guion.modelo) == 0
    return guion


def test_el_reporte_da_el_rango_entre_corridas(corrida_con_rango, leer_reporte):
    acierto = leer_reporte()["acierto"]["alcance"]
    assert [fila["aciertos"] for fila in acierto["por_corrida"]] == [2, 1]
    assert all(fila["n"] == 3 for fila in acierto["por_corrida"])
    assert acierto["rango"]["minimo"] == pytest.approx(33.3, abs=0.1)
    assert acierto["rango"]["maximo"] == pytest.approx(66.7, abs=0.1)
    assert acierto["rango"]["corridas"] == 2


def test_el_reporte_da_acierto_en_todas_las_corridas_por_pregunta(corrida_con_rango, leer_reporte):
    salida = leer_reporte()
    por_id = {fila["id"]: fila for fila in salida["preguntas"]}
    assert por_id["N-1"]["por_corrida"] == [True, True]
    assert por_id["N-1"]["en_todas"] is True
    assert por_id["N-2"]["por_corrida"] == [True, False]
    assert por_id["N-2"]["en_todas"] is False
    assert por_id["PT-3"]["en_todas"] is False
    assert salida["acierto"]["alcance"]["en_todas"] == {"aciertos": 1, "n": 3}


def test_el_reporte_da_el_acierto_por_bloque_categoria_y_tipo_con_su_n(corrida_con_rango, leer_reporte):
    salida = leer_reporte()
    assert salida["por_bloque"]["normal_sencilla"]["por_corrida"][0]["n"] == 2
    assert salida["por_bloque"]["trampa_sencilla"]["por_corrida"][0] == {"corrida": 1, "aciertos": 0, "n": 1, "porcentaje": 0.0}
    assert salida["por_categoria"]["tickets"]["por_corrida"][1]["aciertos"] == 0
    assert salida["por_tipo"]["cifra"]["por_corrida"][0]["n"] == 3


def test_el_reporte_da_la_matriz_de_estados_y_las_falsas_abstenciones(corrida_con_rango, leer_reporte):
    salida = leer_reporte()
    estados = salida["matriz_de_estados"]
    assert estados["etiquetas"] == ["ok"]
    assert estados["matriz"] == [[6]]
    conducta = salida["matriz_de_conducta"]
    fila = conducta["matriz"][conducta["etiquetas"].index("cifra")]
    assert fila[conducta["etiquetas"].index("abstiene")] == 3
    assert fila[conducta["etiquetas"].index("contesta")] == 3
    falsas = salida["falsas_abstenciones"]
    assert [fila["casos"] for fila in falsas["por_corrida"]] == [["PT-3"], ["N-2", "PT-3"]]
    assert falsas["total"] == 3


def test_el_markdown_quita_de_las_matrices_lo_que_esta_en_cero():
    matriz = {"etiquetas": ["a", "b", "c"], "matriz": [[0, 2, 0], [0, 0, 0], [0, 1, 0]]}
    assert reporte._matriz_en_markdown(matriz, "x") == ["| x | b |", "|---|---|", "| a | 2 |", "| c | 1 |", ""]


@requiere_git
def test_el_reporte_da_el_commit_y_el_modelo(corrida_con_rango, leer_reporte):
    meta = leer_reporte()["meta"]
    assert meta["commit"]["sha"] == registro.commit()["sha"]
    assert "sucio" in meta["commit"]
    assert meta["modelos"] == ["function:guion"]
    assert meta["prompt_sha256"] == [agente.PROMPT_SHA256]
    assert meta["herramientas_sha256"] == [agente.HERRAMIENTAS_SHA256]
    assert meta["hash_fuentes"]


def test_el_reporte_dice_que_el_juez_no_entro(corrida_con_rango, leer_reporte, tmp_path):
    assert leer_reporte()["meta"]["juez"].startswith("Sin juez: ")
    assert "- Sin juez: " in texto_markdown(tmp_path)


def test_el_reporte_da_tokens_costo_y_latencia_p50_y_p90_por_categoria(corrida_con_rango, leer_reporte):
    uso = leer_reporte()["uso_por_categoria"]
    tickets = uso["tickets"]
    assert tickets["n"] == 2
    for medida in ("entrada_tokens", "entrada_no_cacheada", "salida_tokens", "razonamiento_tokens", "latencia_ms", "n_llamadas"):
        assert set(tickets[medida]) == {"p50", "p90"}, medida
    assert tickets["entrada_tokens"]["p50"] > 0
    assert "costo_usd" in tickets
    assert uso["cifra_directa"]["n"] == 4


def test_percentil_con_interpolacion():
    assert reporte.percentil([10], 50) == 10
    assert reporte.percentil([10, 20], 50) == 15
    assert reporte.percentil([0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100], 90) == 90


def test_el_costo_sale_de_los_precios_y_si_falta_uno_no_se_inventa(leer_reporte, escribir_banco, lanzar):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1")
    salida = leer_reporte()
    assert salida["turnos"][0]["costo_usd"] is None
    assert salida["uso_total"]["turnos_sin_precio"] == 1
    assert salida["uso_total"]["costo_usd"] is None


def test_el_reporte_cuenta_las_cifras_sin_evidencia_de_la_primera_version(escribir_banco, lanzar, leer_reporte):
    """El texto mostrado da 0 por construcción: la cuenta es la de revisiones[0]."""
    cifra_falsa = "9,999,999.99"
    versiones = []

    def borrador_y_correccion(mensajes, info: AgentInfo):
        ultima = mensajes[-1]
        if any(isinstance(parte, ToolReturnPart) for parte in ultima.parts):
            versiones.append("borrador")
            return ModelResponse(parts=[TextPart(f"{cifra_falsa} MXN")])
        if versiones:
            versiones.append("corregida")
            return ModelResponse(parts=[TextPart(f"{VALLE_AGOSTO} MXN")])
        return ModelResponse(parts=[ToolCallPart(*VENTAS_T01_AGOSTO)])

    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True, **CIFRA_T01))
    lanzar(ruta, "--corridas", "1", modelo=FunctionModel(borrador_y_correccion))
    salida = leer_reporte()
    turno = salida["turnos"][0]
    assert turno["cifras_sin_evidencia"] == 1
    assert turno["desenlace"] == "corregida"
    assert cifra_falsa not in turno["texto"]
    assert salida["sin_evidencia"]["por_corrida"][0]["cifras"] == 1
    assert salida["sin_evidencia"]["por_corrida"][0]["citas"] == 0
    assert salida["preguntas"][0]["en_todas"] is True


def test_el_reporte_guarda_el_texto_con_las_lineas_de_fuente_y_c33_y_k29_se_califican_con_el_pie(
    escribir_banco, lanzar, leer_reporte, tmp_path
):
    guion = Guion({"pregunta uno de ensayo": (VENTAS_T01_AGOSTO, f"{VALLE_AGOSTO} MXN")})
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True, **CIFRA_T01))
    lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    salida = leer_reporte()
    turno = salida["turnos"][0]
    assert "Corte:" not in turno["texto"], "el modelo no escribe el corte (D24)"
    assert "Corte:" in turno["texto_gerente"]
    assert "Fuente:" in turno["texto_gerente"]
    assert salida["formato"]["corte_c33"] == {"aciertos": 1, "n": 1}
    assert salida["formato"]["fuente_k29"] == {"aciertos": 1, "n": 1}


def test_el_reporte_cuenta_cuantas_respuestas_quedan_en_3_lineas_o_menos(escribir_banco, lanzar, leer_reporte):
    largo = "Cifra\nuno\ndos\ntres\ncuatro"
    corto = "Cifra\nuno\nNota: no cuenta.\nAlternativas: tampoco."
    guion = Guion({"pregunta larga de ensayo": largo, "pregunta corta de ensayo": corto})
    ruta = escribir_banco(
        sencilla("N-1", "pregunta larga de ensayo", nucleo=True),
        sencilla("N-2", "pregunta corta de ensayo", nucleo=True),
    )
    lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    assert leer_reporte()["formato"]["contexto_c44"] == {"aciertos": 1, "n": 2}


def test_la_negrita_y_la_linea_de_fuente_del_modelo_no_se_muestran_como_en_la_interfaz(escribir_banco, lanzar, leer_reporte):
    guion = Guion({"pregunta uno de ensayo": "**Sin dato**\nFuente: lo que escribio el modelo\nNota: algo"})
    lanzar(escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True)), "--corridas", "1", modelo=guion.modelo)
    turno = leer_reporte()["turnos"][0]
    assert turno["texto_mostrado"] == "Sin dato\nNota: algo"
    assert "lo que escribio el modelo" in turno["texto"]


def test_las_fallas_se_nombran_con_su_causa_y_su_turno(corrida_con_rango, leer_reporte, tmp_path):
    salida = leer_reporte()
    por_id = {fila["id"]: fila for fila in salida["preguntas"]}
    assert any(VALLE_AGOSTO in causa for causa in por_id["PT-3"]["causas"])
    assert por_id["PT-3"]["turnos"]
    markdown = texto_markdown(tmp_path)
    assert "PT-3" in markdown
    assert VALLE_AGOSTO in markdown


def test_la_conversacion_larga_reporta_el_crecimiento_por_turno(escribir_banco, lanzar, leer_reporte):
    turnos = [turno_de(f"CV-5-{n:02d}", f"turno {n} de la conversacion larga") for n in range(1, 13)]
    turnos[0].update(cifras_esperadas=[VALLE_AGOSTO])
    turnos[-1].update(cifras_esperadas=[VALLE_AGOSTO], repite="CV-5-01")
    conversacion = {"id": "CV-5", "bloque": "conversacion", "nucleo": True, "prueba": "tope_historial", "turnos": turnos}
    guion = Guion(
        {
            "turno 1 de la conversacion larga": (VENTAS_T01_AGOSTO, f"{VALLE_AGOSTO} MXN"),
            "turno 12 de la conversacion larga": (VENTAS_T01_AGOSTO, f"{VALLE_AGOSTO} MXN"),
        }
    )
    lanzar(escribir_banco(conversacion), "--corridas", "1", modelo=guion.modelo)
    salida = leer_reporte()
    larga = salida["conversacion_larga"][0]
    assert len(larga["entrada_por_turno"]) == 12
    assert larga["crecimiento_por_turno"] == pytest.approx(
        (larga["entrada_por_turno"][-1] - larga["entrada_por_turno"][0]) / 11
    )
    assert larga["razon_turno_12_a_1"] > 1
    assert larga["misma_cifra"] is True


def test_el_reporte_no_trae_datos_personales_aunque_el_modelo_los_escriba(escribir_banco, lanzar, leer_reporte, tmp_path):
    """Un teléfono inventado del bloque ficticio: el verificador lo frena y el reporte no lo guarda en ningún lado."""
    telefono = "81.0199.0042"
    guion = Guion({"pregunta uno de ensayo": f"Márcale al {telefono}"})
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    salida = leer_reporte()
    assert telefono not in json.dumps(salida, ensure_ascii=False)
    assert telefono not in texto_markdown(tmp_path)
    assert salida["turnos"][0]["datos_personales_en_borrador"] >= 1
    for archivo in (tmp_path / "reportes" / "avance").glob("*.jsonl"):
        assert telefono not in archivo.read_text(encoding="utf-8")


def test_un_ticket_con_datos_personales_marca_la_fuga_antes_de_enmascarar(escribir_banco, lanzar, leer_reporte, monkeypatch):
    """Si el dato llega a salir, el reporte lo cuenta como fuga aunque el texto guardado salga enmascarado."""
    digitos = evaluadores.datos_personales_del_ticket(1043).digitos[0]
    monkeypatch.setattr(agente.modulo_verificacion, "verificar", lambda texto, fuentes, leer: agente.modulo_verificacion.Revision((), 0, 0))
    guion = Guion({"pregunta uno de ensayo": f"El dato es {digitos}"})
    ruta = escribir_banco(
        sencilla("PT-1", "pregunta uno de ensayo", nucleo=True, tipo_respuesta="rechazo", ticket_con_datos_personales=1043)
    )
    lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    salida = leer_reporte()
    assert digitos not in json.dumps(salida, ensure_ascii=False)
    assert salida["turnos"][0]["fuga_ticket"] is True
    assert salida["fugas"]["por_corrida"][0]["casos"] == ["PT-1"]
    assert salida["preguntas"][0]["en_todas"] is False


def test_una_falla_del_comando_no_se_oculta(escribir_banco, lanzar, monkeypatch, capsys, leer_reporte):
    def truena(*_args, **_opciones):
        raise OSError("la copia no se pudo armar")

    monkeypatch.setattr(copias, "copia", truena)
    ruta = escribir_banco(
        sencilla("PT-9", "pregunta con copia de ensayo", nucleo=True, copia="D"),
        sencilla("N-1", "pregunta sin copia de ensayo", nucleo=True),
    )
    assert lanzar(ruta, "--corridas", "1") == correr.SALIDA_FALLAS_DEL_COMANDO
    salida = leer_reporte()
    assert salida["meta"]["completo"] is False
    assert [falla["id"] for falla in salida["fallas_del_comando"]] == ["PT-9"]
    assert "OSError" in salida["fallas_del_comando"][0]["tipo"]
    assert "PT-9" in capsys.readouterr().err


def test_la_salida_de_consola_resume_el_acierto_y_los_archivos(escribir_banco, lanzar, capsys, tmp_path):
    lanzar(escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True)), "--corridas", "1")
    salida = capsys.readouterr().out
    assert "N-1" in salida
    assert ".md" in salida
    assert ".json" in salida
    assert re.search(r"\d+ de \d+", salida)


def test_el_reporte_formatea_con_coma_de_miles_y_punto_decimal():
    assert reporte.numero(1234567.5) == "1,234,567.50"
    assert reporte.entero(1234567) == "1,234,567"


def test_el_nucleo_real_corre_con_testmodel_de_punta_a_punta(lanzar, leer_reporte, monkeypatch):
    """Los 30 prompts reales, una corrida, con TestModel: ninguno revienta el comando. No mide acierto."""
    assert lanzar(banco.BANCO, "--corridas", "1") == 0
    salida = leer_reporte()
    assert salida["meta"]["n_prompts"] == 30
    assert salida["acierto"]["alcance"]["por_corrida"][0]["n"] == 30
    assert set(salida["por_categoria"]) == {
        "cifra_directa", "cifra_con_definicion", "politica_con_tabla", "contradiccion", "cruce_de_fuentes",
        "tickets", "abstencion", "ambiguedad", "varios_turnos", "adversarial",
    }
    assert not salida["fallas_del_comando"]


def test_eval_correr_no_escribe_en_build_ni_en_logs_por_las_pruebas():
    """Las pruebas de este módulo pasan su registro y su base: esta prueba fija los valores por omisión del comando."""
    import inspect

    firma = inspect.signature(correr.main)
    assert firma.parameters["archivo_registro"].default == registro.ARCHIVO
    assert firma.parameters["base"].default == construir.BASE
    assert Path(firma.parameters["dir_reportes"].default).name == "reportes"


# ------------------------------------------------------------------ los números del reporte, con turnos armados a mano


def turno_armado(texto="", **campos):
    return TurnoEvaluado(**{"pregunta": "x", "estado": "ok", "texto": texto, "texto_mostrado": texto, "modelo": "guion", **campos})


@pytest.fixture
def reporte_de(escribir_banco, lanzar, leer_reporte, monkeypatch):
    """El comando entero con los turnos de cada prompt armados a mano: el reporte sale de cifras que la prueba conoce."""

    def armar(*casos):
        turnos_de = {prompt["id"]: turnos for prompt, turnos in casos}
        monkeypatch.setattr(correr, "correr_prompt", lambda prompt, conf: ResultadoPrompt(turnos=turnos_de[prompt.id]))
        assert lanzar(escribir_banco(*(prompt for prompt, _ in casos)), "--completo", "--corridas", "1") == 0
        return leer_reporte()

    return armar


MEDIDAS_POR_TURNO = (
    "entrada_tokens", "entrada_no_cacheada", "cache_lectura_tokens", "cache_escritura_tokens",
    "salida_tokens", "razonamiento_tokens", "n_llamadas", "latencia_ms",
)
BASE_DE_PERCENTILES = (1000, 2000, 3000, 4000, 10000)


def test_p50_y_p90_por_categoria_coinciden_con_statistics_quantiles(reporte_de, tmp_path):
    """Cada medida con su propia serie: un p90 que dé el p50, o una medida que lea otra, no pasa."""
    series = {medida: [valor * n for valor in BASE_DE_PERCENTILES] for n, medida in enumerate(MEDIDAS_POR_TURNO, start=1)}
    casos = [
        (
            sencilla(f"N-{i}", f"pregunta {i} de ensayo", categoria="tickets"),
            (turno_armado(**{medida: valores[i] for medida, valores in series.items()}),),
        )
        for i in range(len(BASE_DE_PERCENTILES))
    ]
    casos.append((sencilla("N-9", "pregunta aparte de ensayo", categoria="abstencion"), (turno_armado(entrada_tokens=77),)))
    uso = reporte_de(*casos)["uso_por_categoria"]
    assert uso["tickets"]["n"] == 5
    for medida, valores in series.items():
        cuantiles = statistics.quantiles(valores, n=10, method="inclusive")
        assert uso["tickets"][medida] == {"p50": pytest.approx(cuantiles[4]), "p90": pytest.approx(cuantiles[8])}, medida
        assert cuantiles[4] != cuantiles[8]
    assert uso["abstencion"]["n"] == 1
    assert uso["abstencion"]["entrada_tokens"] == {"p50": 77, "p90": 77}
    renglon = "| tickets | 5 | 3,000 / 7,600 | 6,000 / 15,200 | 15,000 / 38,000 | 21,000.00 / 53,200.00 | sin precio | 24,000 / 60,800 |"
    assert renglon in texto_markdown(tmp_path)


def test_el_costo_por_categoria_no_se_redondea_a_cero_con_las_cifras_del_plan(reporte_de, tmp_path):
    """$0.00084 de media y $0.0032 el más caro con Haiku 5.5: a 2 decimales saldrían 0.00 y parecería una medida."""
    salida = reporte_de(
        (sencilla("N-1", "pregunta uno de ensayo", categoria="tickets"), (turno_armado(costo_usd=Decimal("0.00084")),)),
        (sencilla("N-2", "pregunta dos de ensayo", categoria="tickets"), (turno_armado(costo_usd=Decimal("0.0032")),)),
        (sencilla("N-3", "pregunta tres de ensayo", categoria="abstencion"), (turno_armado(costo_usd=Decimal("0.00084")),)),
    )
    uso = salida["uso_por_categoria"]
    cuantiles = statistics.quantiles([0.00084, 0.0032], n=10, method="inclusive")
    assert uso["tickets"]["costo_usd"] == {"p50": pytest.approx(cuantiles[4]), "p90": pytest.approx(cuantiles[8])}
    assert uso["abstencion"]["costo_usd"] == {"p50": 0.00084, "p90": 0.00084}
    markdown = texto_markdown(tmp_path)
    assert "| 0.002020 / 0.002964 |" in markdown
    assert "| 0.000840 / 0.000840 |" in markdown
    assert "0.000000" not in markdown
    assert Decimal(salida["uso_total"]["costo_usd"]) == Decimal("0.00488")


def test_un_costo_real_de_cero_sigue_siendo_cero_y_sin_precio_sigue_sin_precio():
    assert reporte._uso_de([turno_armado(costo_usd=Decimal(0))])["costo_usd"] == {"p50": 0, "p90": 0}
    assert reporte._uso_de([turno_armado()])["costo_usd"] is None


def _modelo_con_uso(*respuestas, nombre="claude-haiku-5-5"):
    """Un FunctionModel con el nombre de un modelo con precio: cada respuesta es (partes, RequestUsage), en orden."""
    pendientes = list(respuestas)

    def contestar(mensajes, info: AgentInfo):
        partes, uso = pendientes.pop(0)
        return ModelResponse(parts=partes, usage=uso)

    return FunctionModel(contestar, model_name=nombre)


def _dos_llamadas_con_cache():
    primera = RequestUsage(input_tokens=1000, cache_read_tokens=700, cache_write_tokens=100, output_tokens=50, output_reasoning_tokens=10)
    segunda = RequestUsage(input_tokens=2000, cache_read_tokens=1500, output_tokens=30, output_reasoning_tokens=5)
    return _modelo_con_uso(
        ([ToolCallPart(*VENTAS_T01_AGOSTO)], primera),
        ([TextPart(f"{VALLE_AGOSTO} MXN")], segunda),
    )


def test_el_costo_del_turno_se_calcula_con_los_precios_y_los_tokens_de_cada_llamada(escribir_banco, lanzar, leer_reporte, tmp_path):
    """El cálculo a mano, con los precios leídos del archivo y no de registro: (200 + 500) sin caché, 2,200 leídos, 100 escritos."""
    precio = yaml.safe_load((banco.RAIZ / "config" / "precios.yaml").read_text(encoding="utf-8"))["modelos"]["claude-haiku-5-5"]
    por_millon = (
        700 * Decimal(precio["entrada"])
        + 2200 * Decimal(precio["cache_lectura"])
        + 100 * Decimal(precio["cache_escritura"])
        + 80 * Decimal(precio["salida"])
    )
    esperado = por_millon / Decimal(1_000_000)
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True, **CIFRA_T01))
    assert lanzar(ruta, "--corridas", "1", modelo=_dos_llamadas_con_cache()) == 0
    salida = leer_reporte()
    assert Decimal(salida["turnos"][0]["costo_usd"]) == esperado
    assert Decimal(salida["uso_total"]["costo_usd"]) == esperado
    assert salida["uso_total"]["turnos_sin_precio"] == 0
    assert salida["uso_por_categoria"]["cifra_directa"]["costo_usd"] == {"p50": float(esperado), "p90": float(esperado)}
    assert f"{esperado:.6f}" in texto_markdown(tmp_path)


def test_los_tokens_del_turno_suman_los_de_sus_llamadas_y_separan_la_cache(escribir_banco, lanzar, leer_reporte):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True, **CIFRA_T01))
    assert lanzar(ruta, "--corridas", "1", modelo=_dos_llamadas_con_cache()) == 0
    turno = leer_reporte()["turnos"][0]
    assert turno["entrada_tokens"] == 3000
    assert turno["entrada_no_cacheada"] == 700
    assert turno["cache_lectura_tokens"] == 2200
    assert turno["cache_escritura_tokens"] == 100
    assert turno["salida_tokens"] == 80
    assert turno["razonamiento_tokens"] == 15
    assert turno["n_llamadas"] == 2


def test_los_reintentos_del_turno_llegan_al_reporte():
    traza = SimpleNamespace(
        entrada_tokens=0, salida_tokens=0, llamadas=(), reintentos=("uno", "dos"), espera_reintentos_segundos=1.5
    )
    uso = correr._uso(SimpleNamespace(traza=traza))
    assert uso["reintentos"] == 2
    assert uso["espera_reintentos_segundos"] == 1.5


def test_la_latencia_del_turno_mide_lo_que_tardo(escribir_banco, lanzar, leer_reporte):
    def tarda(_):
        time.sleep(0.2)
        return SIN_CIFRA

    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({"pregunta uno de ensayo": tarda}).modelo)
    salida = leer_reporte()
    latencia = salida["turnos"][0]["latencia_ms"]
    assert 150 <= latencia < 60_000, "en milisegundos, con la llamada adentro"
    assert salida["uso_por_categoria"]["cifra_directa"]["latencia_ms"] == {"p50": latencia, "p90": latencia}


def test_el_total_suma_los_turnos_y_deja_fuera_los_que_tronaron(reporte_de):
    salida = reporte_de(
        (
            sencilla("N-1", "pregunta uno de ensayo"),
            (turno_armado(entrada_tokens=100, salida_tokens=10, n_llamadas=2, reintentos=1, latencia_ms=60_000, costo_usd=Decimal("0.01")),),
        ),
        (
            sencilla("N-2", "pregunta dos de ensayo"),
            (turno_armado(entrada_tokens=200, salida_tokens=20, n_llamadas=3, latencia_ms=30_000, costo_usd=Decimal("0.02")),),
        ),
        (sencilla("N-3", "pregunta tres de ensayo"), (turno_armado(estado="excepcion", error="Falla", entrada_tokens=999),)),
    )
    total = salida["uso_total"]
    assert total["turnos"] == 2
    assert total["entrada_tokens"] == 300
    assert total["salida_tokens"] == 30
    assert total["n_llamadas"] == 5
    assert total["reintentos"] == 1
    assert total["latencia_minutos"] == 1.5
    assert Decimal(total["costo_usd"]) == Decimal("0.03")
    assert salida["uso_por_categoria"]["cifra_directa"]["n"] == 2


def test_si_un_turno_no_tiene_precio_el_total_no_pasa_por_completo(reporte_de):
    total = reporte_de(
        (sencilla("N-1", "pregunta uno de ensayo"), (turno_armado(costo_usd=Decimal("0.01")),)),
        (sencilla("N-2", "pregunta dos de ensayo"), (turno_armado(),)),
    )["uso_total"]
    assert total["costo_usd"] is None
    assert Decimal(total["costo_usd_de_los_que_tienen_precio"]) == Decimal("0.01")
    assert total["turnos_sin_precio"] == 1


def test_cada_etapa_cuenta_lo_suyo(reporte_de):
    """Cada etapa con una cuenta distinta: herramienta y parámetros por separado, y ninguna lee a otra."""
    bien = HerramientaCorrida(nombre="ventas", filtros=PARAMETROS_T01["ventas"])
    otra_tienda = HerramientaCorrida(nombre="ventas", filtros={**PARAMETROS_T01["ventas"], "tiendas": ["T02"]})
    cita = {"tipo_respuesta": "cita", "secciones_esperadas": [{"documento": "PRO-SAC-021", "seccion": "3"}]}
    con_cifra = f"{VALLE_AGOSTO} MXN"
    etapas = reporte_de(
        (sencilla("N-1", "pregunta uno de ensayo", **CIFRA_T01), (turno_armado(con_cifra, herramientas=(bien,)),)),
        (sencilla("N-2", "pregunta dos de ensayo", **CIFRA_T01), (turno_armado(con_cifra, herramientas=(otra_tienda,)),)),
        (sencilla("N-3", "pregunta tres de ensayo", **CIFRA_T01), (turno_armado(con_cifra),)),
        (sencilla("N-4", "pregunta cuatro de ensayo", **cita), (turno_armado("Son 30 días."),)),
        (sencilla("N-5", "pregunta cinco de ensayo", tipo_respuesta="abstencion"), (turno_armado(SIN_CIFRA),)),
        (
            sencilla("N-6", "pregunta seis de ensayo", tipo_respuesta="abstencion"),
            (turno_armado("Son 5.", fuga_contacto=("TELEFONO",)),),
        ),
        (sencilla("N-7", "pregunta siete de ensayo"), (turno_armado("Sin terminar", estado="tope", tope="segundos"),)),
        (sencilla("N-8", "pregunta ocho de ensayo"), (turno_armado("texto", fuga_ticket=True),)),
    )["etapas"]
    cuentas = {nombre: (tasas["por_corrida"][0]["aciertos"], tasas["por_corrida"][0]["n"]) for nombre, tasas in etapas.items()}
    assert cuentas == {
        "estado": (7, 8),
        "cifras": (3, 3),
        "citas": (0, 1),
        "conducta": (1, 2),
        "fugas": (6, 8),
        "herramienta": (2, 3),
        "parametros": (1, 3),
    }


def test_recall_alguna_y_mrr_se_reportan_cada_uno_con_lo_suyo(reporte_de, tmp_path):
    """Promedios distintos: recall@4 0.667, fracción 0.500 y MRR 0.250, con una pregunta que no buscó aparte (D26-A)."""

    def fragmentos(*secciones):
        return (
            HerramientaCorrida(
                nombre="buscar_politicas",
                fragmentos=tuple(FragmentoRecuperado(documento=documento, seccion=numero) for documento, numero in secciones),
            ),
        )

    def con_secciones(*numeros):
        return {"secciones_esperadas": [{"documento": "POL-OPS-014", "seccion": numero} for numero in numeros]}

    ajenas = [("FAQ-OPS-001", f"P{n}") for n in range(1, 4)]
    recuperacion = reporte_de(
        (
            sencilla("N-1", "pregunta uno de ensayo", **con_secciones("4", "1.1")),
            (turno_armado(herramientas=fragmentos(*ajenas, ("POL-OPS-014", "4"))),),
        ),
        (
            sencilla("N-2", "pregunta dos de ensayo", **con_secciones("4")),
            (turno_armado(herramientas=fragmentos(ajenas[0], ("POL-OPS-014", "4"))),),
        ),
        (sencilla("N-3", "pregunta tres de ensayo", **con_secciones("4")), (turno_armado(herramientas=fragmentos(*ajenas)),)),
        (sencilla("N-4", "pregunta cuatro de ensayo", **con_secciones("4")), (turno_armado(),)),
    )["recuperacion"]
    assert recuperacion["recall_a_4"]["por_corrida"] == [{"corrida": 1, "promedio": 0.667, "n": 3}]
    assert recuperacion["fraccion_de_esperadas"]["por_corrida"] == [{"corrida": 1, "promedio": 0.5, "n": 3}]
    assert recuperacion["mrr"]["por_corrida"] == [{"corrida": 1, "promedio": 0.25, "n": 3}]
    assert recuperacion["sin_busqueda"] == 1
    markdown = texto_markdown(tmp_path)
    assert "- recall@4, al menos una sección esperada en los 4: 0.667 (n = 3)" in markdown
    assert "- Fracción de las esperadas en los 4: 0.500 (n = 3)" in markdown
    assert "- MRR: 0.250 (n = 3)" in markdown
    assert "que no buscaron: 1" in markdown


def test_sin_evidencia_cuenta_cifras_citas_y_datos_personales_por_separado(reporte_de, tmp_path):
    primera = turno_armado(cifras_sin_evidencia=3, citas_sin_evidencia=2, datos_personales_en_borrador=1)
    segunda = turno_armado(cifras_sin_evidencia=4)
    salida = reporte_de(
        (sencilla("N-1", "pregunta uno de ensayo"), (primera,)),
        (sencilla("N-2", "pregunta dos de ensayo"), (segunda,)),
    )
    assert salida["sin_evidencia"]["por_corrida"] == [{"corrida": 1, "cifras": 7, "citas": 2, "datos_personales": 1, "turnos": 2}]
    assert "| 1 | 7 | 2 | 1 | 2 |" in texto_markdown(tmp_path)


def test_una_cita_inventada_del_borrador_se_cuenta_aunque_el_texto_mostrado_ya_no_la_traiga(escribir_banco, lanzar, leer_reporte):
    """Como la cifra: la cuenta es la de revisiones[0], y la versión corregida daría 0."""
    respuestas = iter(["Lo dice POL-OPS-014 §99.", SIN_CIFRA])

    def borrador_y_correccion(mensajes, info: AgentInfo):
        return ModelResponse(parts=[TextPart(next(respuestas))])

    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True, tipo_respuesta="abstencion"))
    lanzar(ruta, "--corridas", "1", modelo=FunctionModel(borrador_y_correccion))
    salida = leer_reporte()
    turno = salida["turnos"][0]
    assert turno["citas_sin_evidencia"] == 1
    assert turno["cifras_sin_evidencia"] == 0
    assert "§99" not in turno["texto"]
    assert salida["sin_evidencia"]["por_corrida"][0]["citas"] == 1


def test_una_abstencion_con_una_pregunta_al_lado_se_cuenta_como_falsa_abstencion(escribir_banco, lanzar, leer_reporte):
    """La respuesta reprueba de todas formas: lo que cambiaba era la cuenta y la matriz."""
    guion = Guion({"pregunta uno de ensayo": "No tengo ese dato. ¿Quieres que revise otro mes?"})
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True, cifras_esperadas=[VALLE_AGOSTO]))
    lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    salida = leer_reporte()
    assert salida["falsas_abstenciones"]["por_corrida"][0]["casos"] == ["N-1"]
    conducta = salida["matriz_de_conducta"]
    fila = conducta["matriz"][conducta["etiquetas"].index("cifra")]
    assert fila[conducta["etiquetas"].index("abstiene")] == 1
    assert "aclara" not in conducta["etiquetas"]


@pytest.mark.parametrize(
    "cambio",
    [
        pytest.param(lambda parche: parche.setattr(registro, "commit", lambda: {"sha": "otro", "sucio": False}), id="commit"),
        pytest.param(lambda parche: parche.setattr(agente, "PROMPT_SHA256", "otro"), id="prompt_sha256"),
        pytest.param(lambda parche: parche.setattr(agente, "HERRAMIENTAS_SHA256", "otro"), id="herramientas_sha256"),
    ],
)
def test_reanudar_con_otro_commit_prompt_o_herramientas_no_mezcla_y_lo_dice(escribir_banco, lanzar, monkeypatch, capsys, request, cambio):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    capsys.readouterr()
    cambio(monkeypatch)
    guion = Guion({})
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=guion.modelo) == correr.SALIDA_AVANCE_INVALIDO
    assert guion.preguntas == []
    assert request.node.callspec.id in capsys.readouterr().err


def test_reanudar_con_otro_modelo_no_mezcla(escribir_banco, lanzar):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    otro = FunctionModel(lambda mensajes, info: ModelResponse(parts=[TextPart(SIN_CIFRA)]), model_name="otro")
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=otro) == correr.SALIDA_AVANCE_INVALIDO


# ------------------------------------------------------------------ el nombre del ticket no se guarda


def _palabras_del_nombre_en(texto):
    """Cuántas palabras del nombre del ticket 1043 hay en el texto: una cuenta, para que una falla no las muestre."""
    palabras = set(re.findall(r"\w+", evaluadores.plano(texto)))
    return sum(palabra in palabras for palabra in evaluadores.datos_personales_del_ticket(1043).palabras)


def _nombre_del_ticket():
    return " ".join(palabra.title() for palabra in evaluadores.datos_personales_del_ticket(1043).palabras)


def _salidas_del_comando(tmp_path):
    return [archivo.read_text(encoding="utf-8") for archivo in (tmp_path / "reportes").rglob("*") if archivo.is_file()]


@pytest.mark.parametrize("escribir", [str, str.upper], ids=["como_nombre", "en_mayusculas"])
def test_el_nombre_del_ticket_no_queda_en_las_salidas_aunque_el_verificador_lo_apruebe(
    escribir_banco, lanzar, leer_reporte, tmp_path, escribir
):
    """El verificador solo enmascara contacto: un nombre sin teléfono llega hasta el reporte si este no lo quita."""
    guion = Guion({"pregunta uno de ensayo": f"Se llama {escribir(_nombre_del_ticket())}."})
    ruta = escribir_banco(sencilla("PT-1", "pregunta uno de ensayo", nucleo=True, tipo_respuesta="rechazo", ticket_con_datos_personales=1043))
    lanzar(ruta, "--corridas", "1", modelo=guion.modelo)
    salida = leer_reporte()
    turno = salida["turnos"][0]
    assert turno["desenlace"] == "aprobada", "el caso es el que el verificador deja pasar"
    assert turno["fuga_ticket"] is True
    assert salida["fugas"]["por_corrida"][0]["casos"] == ["PT-1"]
    assert turno["texto"] == "Se llama <PERSONA>."
    assert turno["texto_mostrado"] == "Se llama <PERSONA>."
    salidas = _salidas_del_comando(tmp_path)
    assert len(salidas) >= 3, "el .md, el .json y el avance"
    assert [_palabras_del_nombre_en(contenido) for contenido in salidas] == [0] * len(salidas)


def test_el_nombre_del_ticket_se_mide_y_se_quita_en_cualquier_prompt(escribir_banco, lanzar, leer_reporte, tmp_path):
    """Un prompt que no pregunta por el ticket tampoco lo guarda, y el reporte no esconde que salió."""
    guion = Guion({"pregunta uno de ensayo": f"El contacto es {_nombre_del_ticket()}."})
    lanzar(escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True)), "--corridas", "1", modelo=guion.modelo)
    salida = leer_reporte()
    assert salida["turnos"][0]["fuga_ticket"] is True
    assert salida["preguntas"][0]["en_todas"] is False
    salidas = _salidas_del_comando(tmp_path)
    assert [_palabras_del_nombre_en(contenido) for contenido in salidas] == [0] * len(salidas)


def test_el_nombre_del_ticket_en_los_parametros_es_fuga_y_no_se_guarda(escribir_banco, lanzar, leer_reporte, tmp_path):
    guion = Guion({"pregunta uno de ensayo": (("tickets", {"texto": _nombre_del_ticket()}), SIN_CIFRA)})
    lanzar(escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True)), "--corridas", "1", modelo=guion.modelo)
    turno = leer_reporte()["turnos"][0]
    assert turno["herramientas"][0]["nombre"] == "tickets"
    assert "<PERSONA>" in turno["herramientas"][0]["entrada"]["texto"]
    assert turno["fuga_ticket"] is True
    salidas = _salidas_del_comando(tmp_path)
    assert [_palabras_del_nombre_en(contenido) for contenido in salidas] == [0] * len(salidas)


def test_un_dato_de_contacto_que_pase_el_verificador_tampoco_se_guarda(escribir_banco, lanzar, leer_reporte, monkeypatch, tmp_path):
    """Inventados, del bloque ficticio. Sin el verificador en medio: lo que frena el reporte es su propio enmascarado."""
    telefono, correo = "81.0199.0042", "alguien@ejemplo.test"
    monkeypatch.setattr(agente.modulo_verificacion, "verificar", lambda texto, fuentes, leer: agente.modulo_verificacion.Revision((), 0, 0))
    guion = Guion({"pregunta uno de ensayo": f"Márcale al {telefono} o escribe a {correo}."})
    lanzar(escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True)), "--corridas", "1", modelo=guion.modelo)
    turno = leer_reporte()["turnos"][0]
    assert set(turno["fuga_contacto"]) == {"TELEFONO", "CORREO"}
    assert "<TELEFONO_1>" in turno["texto"]
    assert "<CORREO_1>" in turno["texto"]
    for contenido in _salidas_del_comando(tmp_path):
        assert telefono not in contenido
        assert correo not in contenido


@pytest.mark.parametrize("respuesta", [SIN_CIFRA, UnexpectedModelBehavior("vacia")], ids=["normal", "excepcion"])
def test_el_nombre_del_ticket_en_la_pregunta_no_se_guarda(escribir_banco, lanzar, leer_reporte, tmp_path, respuesta):
    pregunta = f"pregunta sobre {_nombre_del_ticket()} de ensayo"
    lanzar(escribir_banco(sencilla("N-1", pregunta, nucleo=True)), "--corridas", "1", modelo=Guion({pregunta: respuesta}).modelo)
    assert leer_reporte()["turnos"][0]["pregunta"] == "pregunta sobre <PERSONA> de ensayo"
    salidas = _salidas_del_comando(tmp_path)
    assert [_palabras_del_nombre_en(contenido) for contenido in salidas] == [0] * len(salidas)


def test_las_discrepancias_del_borrador_no_guardan_el_nombre_del_ticket():
    primera = SimpleNamespace(
        cifras_sin_evidencia=0,
        citas_sin_evidencia=0,
        datos_personales=1,
        discrepancias=(SimpleNamespace(tipo="dato", valor=_nombre_del_ticket()),),
    )
    traza = SimpleNamespace(verificacion=SimpleNamespace(desenlace="corregida", revisiones=(primera,)))
    assert correr._revision_del_borrador(SimpleNamespace(traza=traza))["discrepancias"] == ("dato: <PERSONA>",)


def test_un_dato_que_solo_viaja_en_los_parametros_es_fuga():
    def respuesta(entrada):
        return SimpleNamespace(texto="texto limpio", evidencias=(SimpleNamespace(entrada=entrada),))

    assert correr._fugas(respuesta({"texto": _nombre_del_ticket()}), ())["fuga_ticket"] is True
    assert correr._fugas(respuesta({"texto": "81.0199.0042"}), ())["fuga_contacto"] == ("TELEFONO",)
    limpio = correr._fugas(respuesta({"texto": "puerta de empleados"}), ())
    assert limpio == {"fuga_contacto": (), "fuga_ticket": False}


def test_un_turno_de_dos_categorias_cuenta_su_uso_en_las_dos(reporte_de):
    """Lo que dice el docstring del reporte: un sin_signos de dos categorías, y el total no suma por categoría."""
    partes = [
        sencilla("N-1", "pregunta uno de ensayo", categoria="tickets"),
        sencilla("N-2", "pregunta dos de ensayo", categoria="abstencion"),
    ]
    dos = compuesto("DN-1", "sin_signos", "pregunta uno de ensayo y dos", "N-1", "N-2")
    salida = reporte_de(
        (partes[0], (turno_armado(entrada_tokens=10),)),
        (partes[1], (turno_armado(entrada_tokens=20),)),
        (dos, (turno_armado(entrada_tokens=500),)),
    )
    uso = salida["uso_por_categoria"]
    assert uso["tickets"]["n"] == 2
    assert uso["abstencion"]["n"] == 2
    assert uso["tickets"]["entrada_tokens"]["p50"] == 255
    assert uso["abstencion"]["entrada_tokens"]["p50"] == 260
    assert salida["uso_total"]["entrada_tokens"] == 530


# ------------------------------------------------------------------ T5.9: el juez en la calificación del set


def _seguir_las_etiquetas(mensajes, info: AgentInfo):
    """Un juez que contesta lo que dice cada etiqueta: alcanza para validar en una carpeta de la prueba."""
    texto = "\n".join(str(parte.content) for mensaje in mensajes for parte in mensaje.parts if parte.part_kind == "user-prompt")
    for etiqueta in juez.leer_etiquetas():
        if f"<respuesta>\n{juez._dato(etiqueta.respuesta)}\n</respuesta>" in texto:
            return _veredicto(etiqueta.etiqueta == "aprueba")
    return _veredicto(True)


def _veredicto(aprueba, motivo="motivo del juez de ensayo"):
    parte = ToolCallPart("final_result", {"aprueba": aprueba, "motivo": motivo})
    return ModelResponse(parts=[parte], usage=RequestUsage(input_tokens=100, output_tokens=10))


@pytest.fixture(scope="module")
def juez_validado(tmp_path_factory):
    """Una carpeta donde el juez ya está validado para function:guion, el modelo de estas pruebas."""
    carpeta = tmp_path_factory.mktemp("juez")
    modelo = FunctionModel(_seguir_las_etiquetas, model_name="guion")
    assert juez.main([], modelo=modelo, dir_reportes=carpeta) == juez.SALIDA_BIEN
    return carpeta


class JuezDeEnsayo:
    """Decide por lo que trae la respuesta y anota lo que recibe. El agente no llega aquí: sus turnos van armados."""

    def __init__(self, reprobar=(), ilegible=(), caido=()):
        self.reprobar, self.ilegible, self.caido = reprobar, ilegible, caido
        self.recibidos = []
        self.modelo = FunctionModel(self._contestar, model_name="guion")

    def _contestar(self, mensajes, info: AgentInfo):
        texto = "\n".join(str(parte.content) for mensaje in mensajes for parte in mensaje.parts if parte.part_kind == "user-prompt")
        respuesta = texto.split("<respuesta>\n", maxsplit=1)[1]
        self.recibidos.append(texto)
        if any(marca in respuesta for marca in self.caido):
            raise ModelHTTPError(status_code=429, model_name="guion", body="cuota")
        if any(marca in respuesta for marca in self.ilegible):
            return ModelResponse(parts=[TextPart("no es un veredicto")], usage=RequestUsage(input_tokens=7, output_tokens=3))
        return _veredicto(not any(marca in respuesta for marca in self.reprobar))


@pytest.fixture
def con_juez(escribir_banco, lanzar, leer_reporte, monkeypatch, juez_validado):
    """El comando con los turnos armados a mano y el juez validado: por prompt, el texto que contestó."""

    def armar(juez_de_ensayo, *casos, argumentos=("--completo", "--corridas", "1"), dir_juez=juez_validado):
        turnos_de = {prompt["id"]: turnos for prompt, turnos in casos}
        monkeypatch.setattr(correr, "correr_prompt", lambda prompt, conf: ResultadoPrompt(turnos=turnos_de[prompt.id]))
        ruta = escribir_banco(*(prompt for prompt, _ in casos))
        assert lanzar(ruta, *argumentos, modelo=juez_de_ensayo.modelo, dir_juez=dir_juez) == 0
        return leer_reporte()

    return armar


def _dos_prompts():
    return (
        (sencilla("N-1", "pregunta uno de ensayo", nucleo=True, criterio="Nombra el conflicto."), (turno_armado("respuesta buena"),)),
        (
            sencilla("PT-2", "pregunta dos de ensayo", bloque="trampa_sencilla", criterio="Nombra el conflicto."),
            (turno_armado("respuesta floja"),),
        ),
    )


def test_una_respuesta_que_pasa_las_reglas_y_reprueba_el_juez_cuenta_como_falla(con_juez):
    salida = con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts())
    assert salida["acierto"]["alcance"]["por_corrida"][0]["aciertos"] == 1
    por_id = {fila["id"]: fila for fila in salida["preguntas"]}
    assert por_id["PT-2"]["por_corrida"] == [False]
    assert any("motivo del juez de ensayo" in causa for causa in por_id["PT-2"]["causas"])


def test_el_reporte_da_lado_a_lado_el_acierto_con_juez_y_con_reglas(con_juez, tmp_path):
    salida = con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts())
    assert salida["acierto"]["alcance"]["por_corrida"][0]["aciertos"] == 1
    assert salida["acierto_reglas"]["alcance"]["por_corrida"][0]["aciertos"] == 2
    assert salida["acierto"]["nucleo"]["por_corrida"][0] == {"corrida": 1, "aciertos": 1, "n": 1, "porcentaje": 100.0}
    assert salida["acierto_reglas"]["nucleo"]["por_corrida"][0]["aciertos"] == 1
    assert salida["por_bloque"]["trampa_sencilla"]["por_corrida"][0]["aciertos"] == 0
    assert salida["por_bloque_reglas"]["trampa_sencilla"]["por_corrida"][0]["aciertos"] == 1
    markdown = texto_markdown(tmp_path)
    assert "Reglas solas" in markdown
    assert "1 de 2 (50.0 %) | 50.0 % (una corrida entera, sin rango) | 1 de 2 | 2 de 2 (100.0 %)" in markdown
    assert "| trampa_sencilla | 0 de 1 (0.0 %) | 0.0 % (una corrida entera, sin rango) | 1 de 1 (100.0 %)" in markdown


def test_el_reporte_dice_cuantos_casos_cambio_el_juez_en_cada_sentido(con_juez, tmp_path):
    salida = con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts())
    cambios = salida["juez"]["cambios"]
    assert cambios["por_corrida"] == [{"corrida": 1, "a_reprobado": ["PT-2"], "a_aprobado": []}]
    assert (cambios["a_reprobado"], cambios["a_aprobado"]) == (1, 0)
    assert "1 de aprobado a reprobado y 0 de reprobado a aprobado" in texto_markdown(tmp_path)


def test_el_reporte_muestra_el_acuerdo_validado_con_su_intervalo(con_juez, tmp_path):
    salida = con_juez(JuezDeEnsayo(), *_dos_prompts())
    validacion = salida["juez"]["validacion"]
    assert validacion["acuerdo_minimo"] == "1.00"
    assert [corrida["acuerdos"] for corrida in validacion["corridas"]] == [20, 20]
    bajo, alto = juez.intervalo_wilson(20, 20)
    assert validacion["corridas"][0]["intervalo_95"] == [round(bajo, 3), round(alto, 3)]
    assert salida["juez"]["usado"] is True
    assert salida["meta"]["juez"].startswith("Con juez: ")
    markdown = texto_markdown(tmp_path)
    assert "20 de 20" in markdown
    assert f"{100 * bajo:,.1f} % a {100 * alto:,.1f} %" in markdown


@pytest.mark.parametrize("carpeta", ["sin_validacion"])
def test_sin_validar_no_se_llama_el_acierto_sale_de_reglas_y_el_reporte_da_el_motivo(con_juez, tmp_path, carpeta):
    jueza = JuezDeEnsayo(reprobar=("floja",))
    salida = con_juez(jueza, *_dos_prompts(), dir_juez=tmp_path / carpeta)
    assert not jueza.recibidos
    assert salida["acierto"]["alcance"]["por_corrida"][0]["aciertos"] == 2
    assert salida["juez"]["usado"] is False
    assert salida["juez"]["validacion"] is None
    assert "no hay validación para function:guion" in salida["juez"]["motivo"]
    assert "no hay validación para function:guion" in salida["meta"]["juez"]
    assert "no hay validación para function:guion" in texto_markdown(tmp_path)


def test_el_motivo_sale_del_estado_real_y_no_de_un_texto_fijo(con_juez, tmp_path, juez_validado, monkeypatch):
    monkeypatch.setattr(juez, "PROMPT_JUEZ_SHA256", "9" * 64)
    jueza = JuezDeEnsayo()
    salida = con_juez(jueza, *_dos_prompts())
    assert not jueza.recibidos
    assert "cambió" in salida["juez"]["motivo"]
    assert "cambió" in salida["meta"]["juez"]


def test_un_veredicto_ilegible_no_reprueba_y_el_reporte_da_cuantos(con_juez, tmp_path):
    salida = con_juez(JuezDeEnsayo(ilegible=("floja",)), *_dos_prompts())
    assert salida["acierto"]["alcance"]["por_corrida"][0]["aciertos"] == 2
    sin_veredicto = salida["juez"]["sin_veredicto"]
    assert sin_veredicto["total"] == 1
    assert sin_veredicto["por_corrida"] == [{"corrida": 1, "n": 1, "casos": ["PT-2"]}]
    assert "Sin veredicto: 1" in texto_markdown(tmp_path)


def test_una_falla_del_modelo_del_juez_no_reprueba_ni_detiene_y_reanudar_la_reintenta(con_juez, tmp_path):
    salida = con_juez(JuezDeEnsayo(caido=("floja",)), *_dos_prompts())
    assert salida["acierto"]["alcance"]["por_corrida"][0]["aciertos"] == 2
    assert salida["meta"]["completo"] is True
    assert salida["juez"]["sin_veredicto"]["total"] == 1
    recuperado = JuezDeEnsayo()
    otra = con_juez(recuperado, *_dos_prompts(), argumentos=("--completo", "--corridas", "1", "--reanudar"))
    assert len(recuperado.recibidos) == 1
    assert "floja" in recuperado.recibidos[0]
    assert otra["juez"]["sin_veredicto"]["total"] == 0


def _lineas_de_avance(tmp_path):
    ruta = tmp_path / "reportes" / correr.CARPETA_AVANCE / correr.ARCHIVO_AVANCE
    return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines()]


def test_el_veredicto_se_guarda_en_el_avance_por_prompt_corrida_y_turno(con_juez, tmp_path):
    con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts(), argumentos=("--completo", "--corridas", "2"))
    veredictos = [linea for linea in _lineas_de_avance(tmp_path) if linea["tipo"] == "juez"]
    claves = {(linea["corrida"], linea["prompt"], linea["turno"], linea["pregunta"]) for linea in veredictos}
    assert claves == {(1, "N-1", 0, "N-1"), (1, "PT-2", 0, "PT-2"), (2, "N-1", 0, "N-1"), (2, "PT-2", 0, "PT-2")}
    assert {linea["veredicto"] for linea in veredictos if linea["prompt"] == "PT-2"} == {"reprueba"}


def test_reanudar_no_repite_las_llamadas_al_juez_y_da_el_mismo_veredicto(con_juez):
    primero = JuezDeEnsayo(reprobar=("floja",))
    con_juez(primero, *_dos_prompts())
    assert len(primero.recibidos) == 2
    segundo = JuezDeEnsayo()
    salida = con_juez(segundo, *_dos_prompts(), argumentos=("--completo", "--corridas", "1", "--reanudar"))
    assert not segundo.recibidos
    assert salida["acierto"]["alcance"]["por_corrida"][0]["aciertos"] == 1
    assert salida["juez"]["uso"]["llamadas"] == 2


def test_reanudar_solo_pide_el_veredicto_que_falta(con_juez, tmp_path):
    con_juez(JuezDeEnsayo(), *_dos_prompts())
    ruta = tmp_path / "reportes" / correr.CARPETA_AVANCE / correr.ARCHIVO_AVANCE
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    sin_una = [linea for linea in lineas if not ('"tipo":"juez"' in linea and '"prompt":"PT-2"' in linea)]
    assert len(sin_una) == len(lineas) - 1
    ruta.write_text("\n".join(sin_una) + "\n", encoding="utf-8")
    segundo = JuezDeEnsayo()
    con_juez(segundo, *_dos_prompts(), argumentos=("--completo", "--corridas", "1", "--reanudar"))
    assert len(segundo.recibidos) == 1
    assert "floja" in segundo.recibidos[0]


def test_tokens_y_costo_del_juez_van_aparte_de_los_del_turno(con_juez, tmp_path):
    casos = (
        (
            sencilla("N-1", "pregunta uno de ensayo", nucleo=True, criterio="Nombra el conflicto."),
            (turno_armado("respuesta", entrada_tokens=1000, salida_tokens=50, n_llamadas=1, costo_usd=Decimal("0.5")),),
        ),
    )
    salida = con_juez(JuezDeEnsayo(), *casos)
    assert salida["uso_total"]["entrada_tokens"] == 1000
    assert salida["uso_total"]["salida_tokens"] == 50
    assert salida["uso_total"]["n_llamadas"] == 1
    assert salida["uso_total"]["costo_usd"] == "0.5"
    uso = salida["juez"]["uso"]
    assert (uso["llamadas"], uso["entrada_tokens"], uso["salida_tokens"]) == (1, 100, 10)
    assert "Uso del juez, aparte del de los turnos: 1 llamadas, 100 tokens de entrada, 10 de salida" in texto_markdown(tmp_path)


def test_el_costo_del_juez_sale_de_los_precios_y_sin_precio_no_se_inventa(con_juez, monkeypatch):
    salida = con_juez(JuezDeEnsayo(), *_dos_prompts())
    assert salida["juez"]["uso"]["costo_usd"] is None
    assert salida["juez"]["uso"]["veredictos_sin_precio"] == 2


def test_por_categoria_y_por_tipo_cuentan_la_reprobacion_del_juez(con_juez):
    salida = con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts())
    assert salida["por_categoria"]["cifra_directa"]["por_corrida"][0]["aciertos"] == 1
    assert salida["por_tipo"]["cifra"]["por_corrida"][0]["aciertos"] == 1


def test_un_compuesto_se_juzga_por_parte(con_juez):
    uno = sencilla("A", "pregunta a de ensayo", criterio="Criterio de la parte a.")
    dos = sencilla("B", "pregunta b de ensayo", criterio="Criterio de la parte b.")
    entrada = {
        "id": "DN-1", "bloque": "dos_normales", "forma": "sin_signos", "prompt": "pregunta a y b de ensayo",
        "criterio": "Criterio del compuesto.", "partes": [uno, dos],
    }
    jueza = JuezDeEnsayo()
    salida = con_juez(jueza, (entrada, (turno_armado("las dos"),)))
    assert len(jueza.recibidos) == 2
    assert not any("Criterio del compuesto." in texto for texto in jueza.recibidos)
    assert [veredicto["pregunta"] for veredicto in salida["juez"]["veredictos"]] == ["A", "B"]


def test_una_conversacion_se_juzga_por_turno(con_juez):
    entrada = {
        "id": "CV-1", "bloque": "conversacion", "prueba": "conserva_filtro",
        "turnos": [
            turno_de("CV-1-1", "turno uno de ensayo", criterio="Criterio uno."),
            turno_de("CV-1-2", "turno dos de ensayo", criterio="Criterio dos."),
        ],
    }
    jueza = JuezDeEnsayo(reprobar=("respuesta dos",))
    salida = con_juez(jueza, (entrada, (turno_armado("respuesta uno"), turno_armado("respuesta dos"))))
    assert len(jueza.recibidos) == 2
    assert "respuesta uno" in jueza.recibidos[1]
    por_turno = {veredicto["turno"]: veredicto["veredicto"] for veredicto in salida["juez"]["veredictos"]}
    assert por_turno == {0: "aprueba", 1: "reprueba"}


def test_un_criterio_que_solo_pide_la_cifra_no_gasta_una_llamada(con_juez):
    casos = (
        (
            sencilla("N-1", "pregunta uno de ensayo", nucleo=True, criterio="Cifra exacta.", cifras_esperadas=["86"]),
            (turno_armado("Son 86."),),
        ),
    )
    jueza = JuezDeEnsayo()
    salida = con_juez(jueza, *casos)
    assert not jueza.recibidos
    assert salida["juez"]["uso"]["llamadas"] == 0


def test_de_punta_a_punta_el_agente_y_el_juez_comparten_el_modelo_de_la_corrida(escribir_banco, lanzar, leer_reporte, juez_validado):
    def contestar(mensajes, info: AgentInfo):
        if juez.PROMPT_JUEZ.strip() in (info.instructions or ""):
            return _veredicto(False)
        return ModelResponse(parts=[TextPart(SIN_CIFRA)])

    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True, tipo_respuesta="abstencion", criterio="Remite a RH."))
    modelo = FunctionModel(contestar, model_name="guion")
    assert lanzar(ruta, "--corridas", "1", modelo=modelo, dir_juez=juez_validado) == 0
    salida = leer_reporte()
    assert salida["acierto"]["alcance"]["por_corrida"][0]["aciertos"] == 0
    assert salida["acierto_reglas"]["alcance"]["por_corrida"][0]["aciertos"] == 1
    assert salida["juez"]["cambios"]["a_reprobado"] == 1


# ------------------------------------------------------------------ T5.9: falsas abstenciones y aclaraciones en el reporte


def test_el_reporte_cuenta_aparte_falsas_abstenciones_aclaraciones_y_negativas_parciales(reporte_de, tmp_path):
    def cifras(identificador, *esperadas):
        return sencilla(identificador, f"pregunta {identificador} de ensayo", cifras_esperadas=list(esperadas))

    salida = reporte_de(
        (cifras("N-1", "86"), (turno_armado("No encontré ese dato."),)),
        (cifras("N-2", "86"), (turno_armado("Son 86, aunque no puedo confirmar el corte."),)),
        (cifras("N-3", "86"), (turno_armado("Hay dos que coinciden. ¿Cuál quieres?"),)),
        (cifras("N-4", "86"), (turno_armado("No tengo ese dato, pero son 5."),)),
        (cifras("N-5", "86", "268"), (turno_armado("Son 86. No tengo el otro dato."),)),
    )
    assert salida["falsas_abstenciones"]["por_corrida"] == [{"corrida": 1, "n": 1, "casos": ["N-1"]}]
    assert salida["aclaraciones_donde_se_esperaba_cifra"]["por_corrida"] == [{"corrida": 1, "n": 1, "casos": ["N-3"]}]
    assert salida["negativas_parciales"]["por_corrida"] == [{"corrida": 1, "n": 1, "casos": ["N-5"]}]
    markdown = texto_markdown(tmp_path)
    assert "Falsas abstenciones: 1 en total." in markdown
    assert "Aclaraciones donde se esperaba cifra: 1 en total." in markdown
    assert "Negativas que traen parte de lo esperado: 1 en total." in markdown


def _turnos_guardados():
    """Las respuestas de la corrida del 2026-11-01, ya guardadas: no se llama a ningún modelo."""
    datos = json.loads((correr.DIR_REPORTES / "2026-11-01-completo-e88cbe7.json").read_text(encoding="utf-8"))
    agrupados = {}
    for turno in datos["turnos"]:
        campos = {clave: valor for clave, valor in turno.items() if clave not in ("corrida", "prompt", "texto_gerente")}
        agrupados.setdefault((turno["corrida"], turno["prompt"]), []).append(TurnoEvaluado.model_validate(campos))
    return agrupados


@requiere_reportes
def test_la_cuenta_nueva_contra_las_respuestas_guardadas_del_2026_11_01():
    """Las 4 aclaraciones son las del recuento a mano. De falsas abstenciones, la regla da 0: ver el resumen de T5.9."""
    por_id = {prompt.id: prompt for prompt in banco.leer_banco()}
    falsas, aclaraciones = [], []
    for (corrida, identificador), turnos in _turnos_guardados().items():
        for _, pregunta, turno in evaluadores.pares(por_id[identificador], ResultadoPrompt(turnos=turnos)):
            if evaluadores.es_falsa_abstencion(pregunta, turno):
                falsas.append((corrida, pregunta.id))
            if evaluadores.es_aclaracion_donde_se_esperaba_cifra(pregunta, turno):
                aclaraciones.append((corrida, pregunta.id))
    assert falsas == []
    assert sorted(aclaraciones) == [(1, "DN-07.N-07"), (1, "N-07"), (2, "DN-07.N-07"), (2, "N-07")]


@requiere_reportes
def test_con_las_reglas_nuevas_las_respuestas_guardadas_dan_88_y_88():
    """El diagnóstico lo simuló a mano: 83 y 85 suben a 88 y 88 solo con corregir el calificador."""
    guardados = _turnos_guardados()
    prompts = banco.leer_banco()
    aciertos = []
    for corrida in (1, 2):
        conjunto = Dataset(
            name="guardadas",
            cases=[Case(name=prompt.id, inputs=prompt) for prompt in prompts],
            evaluators=list(evaluadores.EVALUADORES),
        )
        tarea = lambda prompt, corrida=corrida: ResultadoPrompt(turnos=guardados[(corrida, prompt.id)])  # noqa: B023
        informe = conjunto.evaluate_sync(tarea, max_concurrency=1, progress=False)
        aciertos.append(sum(evaluadores.aprobo(caso) for caso in informe.cases))
    assert aciertos == [88, 88]


# ------------------------------------------------------------------ T5.9: D26-A


def test_recall_a_4_es_al_menos_una_y_la_fraccion_va_al_lado_con_su_nombre(reporte_de, tmp_path):
    """D26-A: las mismas cifras de la prueba de T5.3, con los nombres nuevos."""

    def fragmentos(*secciones):
        return (
            HerramientaCorrida(
                nombre="buscar_politicas",
                fragmentos=tuple(FragmentoRecuperado(documento=documento, seccion=numero) for documento, numero in secciones),
            ),
        )

    def con_secciones(*numeros):
        return {"secciones_esperadas": [{"documento": "POL-OPS-014", "seccion": numero} for numero in numeros]}

    ajenas = [("FAQ-OPS-001", f"P{n}") for n in range(1, 4)]
    recuperacion = reporte_de(
        (
            sencilla("N-1", "pregunta uno de ensayo", **con_secciones("4", "1.1")),
            (turno_armado(herramientas=fragmentos(*ajenas, ("POL-OPS-014", "4"))),),
        ),
        (
            sencilla("N-2", "pregunta dos de ensayo", **con_secciones("4")),
            (turno_armado(herramientas=fragmentos(ajenas[0], ("POL-OPS-014", "4"))),),
        ),
        (sencilla("N-3", "pregunta tres de ensayo", **con_secciones("4")), (turno_armado(herramientas=fragmentos(*ajenas)),)),
        (sencilla("N-4", "pregunta cuatro de ensayo", **con_secciones("4")), (turno_armado(),)),
    )["recuperacion"]
    assert recuperacion["recall_a_4"]["por_corrida"] == [{"corrida": 1, "promedio": 0.667, "n": 3}]
    assert recuperacion["fraccion_de_esperadas"]["por_corrida"] == [{"corrida": 1, "promedio": 0.5, "n": 3}]
    assert recuperacion["mrr"]["por_corrida"] == [{"corrida": 1, "promedio": 0.25, "n": 3}]
    assert "alguna_en_4" not in recuperacion
    markdown = texto_markdown(tmp_path)
    assert "- recall@4, al menos una sección esperada en los 4: 0.667 (n = 3)" in markdown
    assert "- Fracción de las esperadas en los 4: 0.500 (n = 3)" in markdown
    assert "- MRR: 0.250 (n = 3)" in markdown


def test_recall_sin_modelo_usa_los_nombres_de_d26(escribir_banco, lanzar, monkeypatch, tmp_path, capsys):
    busqueda = HerramientaCorrida(
        nombre="buscar_politicas", fragmentos=(FragmentoRecuperado(documento="POL-OPS-014", seccion="4"),)
    )
    monkeypatch.setattr(correr.modulo_politicas, "buscar_politicas", lambda cursor, entrada: None)
    monkeypatch.setattr(correr, "_herramienta_corrida", lambda evidencia: busqueda)
    secciones = [{"documento": "POL-OPS-014", "seccion": "4"}, {"documento": "POL-OPS-014", "seccion": "1.1"}]
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", secciones_esperadas=secciones))
    assert lanzar(ruta, "--recall-sin-modelo") == 0
    salida = capsys.readouterr().out
    assert "recall@4 de la sección sin modelo: 100.0 % en 1 preguntas" in salida
    assert "Fracción de las esperadas: 50.0 %" in salida
    archivo = next((tmp_path / "reportes").glob("recall-sin-modelo-*.json"))
    datos = json.loads(archivo.read_text(encoding="utf-8"))
    assert datos["recall_a_4"] == 100.0
    assert datos["fraccion_de_esperadas"] == 50.0
    assert datos["mrr"] == 1.0
    assert "alguna_en_4" not in datos


# ------------------------------------------------------------------ T5.9: la huella de --reanudar


def test_reanudar_con_otras_fuentes_no_mezcla_y_lo_dice(escribir_banco, lanzar, monkeypatch, capsys):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    capsys.readouterr()
    monkeypatch.setattr(correr, "_hash_de_las_fuentes", lambda conf: "otro")
    guion = Guion({})
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=guion.modelo) == correr.SALIDA_AVANCE_INVALIDO
    assert guion.preguntas == []
    assert "hash_fuentes" in capsys.readouterr().err


def test_reanudar_con_otros_cambios_sin_commit_no_mezcla_y_lo_dice(escribir_banco, lanzar, monkeypatch, capsys):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    capsys.readouterr()
    monkeypatch.setattr(correr, "cambios_sin_commit", lambda: "otro")
    guion = Guion({})
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=guion.modelo) == correr.SALIDA_AVANCE_INVALIDO
    assert guion.preguntas == []
    assert "cambios_sin_commit" in capsys.readouterr().err


def test_reanudar_con_otro_prompt_del_juez_no_mezcla_sus_veredictos(escribir_banco, lanzar, monkeypatch, capsys):
    ruta = escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True))
    lanzar(ruta, "--corridas", "1", modelo=Guion({}).modelo)
    capsys.readouterr()
    monkeypatch.setattr(juez, "PROMPT_JUEZ_SHA256", "otro")
    assert lanzar(ruta, "--corridas", "1", "--reanudar", modelo=Guion({}).modelo) == correr.SALIDA_AVANCE_INVALIDO
    assert "juez_prompt_sha256" in capsys.readouterr().err


def test_el_hash_de_la_huella_cambia_si_cambian_los_datos(tmp_path, base):
    otros = tmp_path / "datos"
    shutil.copytree(construir.DIR_DATOS, otros)
    conf = correr.Configuracion(None, AJUSTES, tmp_path / "t.jsonl", tmp_path / "copia.duckdb", otros, tmp_path / "copias")
    original = correr._hash_de_las_fuentes(conf)
    assert original == construir.asegurar_base(otros, tmp_path / "copia.duckdb").hash_fuentes.sha256
    with open(otros / "diccionario_datos.md", "a", encoding="utf-8", newline="") as archivo:
        archivo.write("\n")
    assert correr._hash_de_las_fuentes(conf) != original


def _git(raiz, *argumentos):
    config = ("-c", "user.name=prueba", "-c", "user.email=prueba@ejemplo.test", "-c", "core.autocrlf=false")
    subprocess.run(["git", *config, *argumentos], cwd=raiz, check=True, capture_output=True)


@pytest.fixture
def repositorio(tmp_path):
    raiz = tmp_path / "repo"
    (raiz / "agente").mkdir(parents=True)
    (raiz / "datos").mkdir()
    (raiz / "eval" / "reportes").mkdir(parents=True)
    (raiz / "agente" / "a.py").write_text("uno\n", encoding="utf-8")
    (raiz / "datos" / "d.csv").write_text("uno\n", encoding="utf-8")
    (raiz / "eval" / "reportes" / "viejo.md").write_text("uno\n", encoding="utf-8")
    _git(raiz, "init", "-q")
    _git(raiz, "add", ".")
    _git(raiz, "commit", "-q", "-m", "inicio")
    return raiz


def test_sin_cambios_no_hay_nada_sin_commit(repositorio):
    assert correr._diferencia_con_el_commit(repositorio) is None


@pytest.mark.parametrize("archivo", ["agente/a.py", "datos/d.csv"])
def test_un_cambio_sin_commit_en_agente_o_datos_cambia_la_huella(repositorio, archivo):
    (repositorio / archivo).write_text("dos\n", encoding="utf-8")
    primera = correr._diferencia_con_el_commit(repositorio)
    assert primera
    (repositorio / archivo).write_text("tres\n", encoding="utf-8")
    assert correr._diferencia_con_el_commit(repositorio) not in (None, primera)


@pytest.mark.parametrize("archivo", ["eval/reportes/nuevo.md", "eval/reportes/viejo.md"])
def test_lo_que_la_corrida_misma_escribe_no_cuenta_como_cambio_sin_commit(repositorio, archivo):
    """--limite-minutos deja un reporte a medias: si contara, --reanudar nunca podría seguir desde un commit limpio."""
    (repositorio / archivo).write_text("dos\n", encoding="utf-8")
    assert correr._diferencia_con_el_commit(repositorio) is None


# ------------------------------------------------------------------ T5.9: lo que dejó vivo el verificador de T5.3


def test_el_turno_guarda_tope_falla_hash_de_fuentes_y_tickets_sospechosos(escribir_banco, lanzar, leer_reporte, monkeypatch):
    real = agente.responder
    vistas = []

    def con_traza(pregunta, historial, **opciones):
        respuesta = real(pregunta, historial, **opciones)
        vistas.append(respuesta.traza.hash_fuentes)
        traza = dataclasses.replace(
            respuesta.traza,
            tope="segundos",
            falla=FallaDelModelo(motivo="llave", tipo="ModelHTTPError"),
            tickets_sospechosos=("1286",),
        )
        return dataclasses.replace(respuesta, traza=traza)

    monkeypatch.setattr(agente, "responder", con_traza)
    lanzar(escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True)), "--corridas", "1")
    turno = leer_reporte()["turnos"][0]
    assert turno["tope"] == "segundos"
    assert turno["falla"] == "llave"
    assert turno["tickets_sospechosos"] == ["1286"]
    assert vistas[0]
    assert turno["hash_fuentes"] == vistas[0]


def test_c33_y_k29_no_se_intercambian(reporte_de):
    """El corte está en el texto y no hay pie: C3.3 aprueba y K29, que pide la línea de fuente, reprueba."""
    con_corte = HerramientaCorrida(nombre="ventas", corte="2026-08-31")
    formato = reporte_de(
        (sencilla("N-1", "pregunta uno de ensayo"), (turno_armado("Al 2026-08-31 van 5.", herramientas=(con_corte,)),)),
    )["formato"]
    assert formato["corte_c33"] == {"aciertos": 1, "n": 1}
    assert formato["fuente_k29"] == {"aciertos": 0, "n": 1}


def test_el_formato_no_cuenta_las_variantes(escribir_banco, lanzar, leer_reporte):
    """D25: las variantes van en su propia sección. Una variante mal formateada no mueve C4.4."""
    largo = "Cifra\nuno\ndos\ntres\ncuatro"
    guion = Guion({"parafrasis uno de ensayo": largo, "otra parafrasis uno": largo})
    ruta = escribir_banco(
        sencilla("N-1", "pregunta uno de ensayo", nucleo=True, variantes=["parafrasis uno de ensayo", "otra parafrasis uno"]),
    )
    assert lanzar(ruta, "--variantes", "--corridas", "1", modelo=guion.modelo) == 0
    formato = leer_reporte()["formato"]
    assert formato["contexto_c44"] == {"aciertos": 1, "n": 1}
    assert formato["fuente_k29"]["n"] == 1


@pytest.mark.parametrize(
    ("variantes", "configuracion", "nombre"),
    [
        (False, "interfaz", "2026-11-02-nucleo-abc1234"),
        (True, "interfaz", "2026-11-02-nucleo-variantes-abc1234"),
        (False, "base", "2026-11-02-nucleo-base-abc1234"),
        (True, "base", "2026-11-02-nucleo-variantes-base-abc1234"),
    ],
)
def test_el_nombre_del_archivo_dice_las_variantes_y_la_configuracion(variantes, configuracion, nombre):
    meta = {
        "fecha": "2026-11-02",
        "alcance": "nucleo",
        "variantes": variantes,
        "configuracion": configuracion,
        "commit": {"sha": "abc1234def"},
    }
    assert reporte._nombre_de_archivo({"meta": meta}) == nombre


@pytest.mark.parametrize(("cambios", "texto"), [(None, "ninguno"), ("abc123", "abc123")])
def test_el_reporte_dice_los_cambios_sin_commit_en_lo_que_se_evalua(
    escribir_banco, lanzar, leer_reporte, monkeypatch, tmp_path, cambios, texto
):
    """Un reporte a medias de --limite-minutos deja sucio el árbol: esta línea dice si cambió algo que decide el resultado."""
    monkeypatch.setattr(correr, "cambios_sin_commit", lambda: cambios)
    lanzar(escribir_banco(sencilla("N-1", "pregunta uno de ensayo", nucleo=True)), "--corridas", "1")
    assert leer_reporte()["meta"]["cambios_sin_commit"] == cambios
    assert f"- Cambios sin commit en lo que se evalúa: {texto}" in texto_markdown(tmp_path)


# ------------------------------------------------------------------ T5.9: --reanudar con un alcance menor


def _sin_reportes(tmp_path):
    """Otro alcance escribe otro archivo: leer_reporte() espera uno. El avance queda donde estaba."""
    for archivo in (tmp_path / "reportes").glob("*.*"):
        archivo.unlink()


def test_reanudar_con_un_alcance_menor_solo_reporta_los_veredictos_de_ese_alcance(con_juez, tmp_path):
    con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts())
    _sin_reportes(tmp_path)
    salida = con_juez(JuezDeEnsayo(), *_dos_prompts(), argumentos=("--corridas", "1", "--reanudar"))
    seccion = salida["juez"]
    assert [fila["id"] for fila in salida["preguntas"]] == ["N-1"]
    assert (seccion["juzgadas"], seccion["aprueba"], seccion["reprueba"]) == (1, 1, 0)
    assert seccion["uso"]["llamadas"] == 1
    assert [veredicto["prompt"] for veredicto in seccion["veredictos"]] == ["N-1"]


def test_reanudar_con_menos_corridas_solo_reporta_los_veredictos_de_esas_corridas(con_juez):
    con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts(), argumentos=("--completo", "--corridas", "2"))
    salida = con_juez(JuezDeEnsayo(), *_dos_prompts(), argumentos=("--completo", "--corridas", "1", "--reanudar"))
    assert salida["juez"]["juzgadas"] == 2
    assert {veredicto["corrida"] for veredicto in salida["juez"]["veredictos"]} == {1}


def test_el_alcance_menor_no_borra_del_avance_lo_que_ya_se_pago(con_juez, tmp_path):
    con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts())
    _sin_reportes(tmp_path)
    con_juez(JuezDeEnsayo(), *_dos_prompts(), argumentos=("--corridas", "1", "--reanudar"))
    _sin_reportes(tmp_path)
    tercero = JuezDeEnsayo()
    salida = con_juez(tercero, *_dos_prompts(), argumentos=("--completo", "--corridas", "1", "--reanudar"))
    assert not tercero.recibidos
    assert salida["juez"]["reprueba"] == 1


# ------------------------------------------------------------------ T5.9: lo que el juez cuenta en el reporte


def test_el_reporte_cuenta_lo_que_aprueba_y_lo_que_reprueba_el_juez(con_juez, tmp_path):
    seccion = con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts())["juez"]
    assert (seccion["juzgadas"], seccion["aprueba"], seccion["reprueba"]) == (2, 1, 1)
    assert "Juzgadas: 2: 1 aprueban y 1 reprueban." in texto_markdown(tmp_path)


@pytest.mark.parametrize(("opciones", "fallas"), [({"caido": ("floja",)}, 1), ({"ilegible": ("floja",)}, 0)])
def test_el_reporte_cuenta_aparte_las_fallas_del_modelo_del_juez(con_juez, tmp_path, opciones, fallas):
    sin_veredicto = con_juez(JuezDeEnsayo(**opciones), *_dos_prompts())["juez"]["sin_veredicto"]
    assert (sin_veredicto["total"], sin_veredicto["fallas_del_modelo"]) == (1, fallas)
    assert f"Sin veredicto: 1 ({fallas} por una falla del modelo)." in texto_markdown(tmp_path)


def test_el_costo_del_juez_suma_el_de_cada_veredicto(con_juez, tmp_path, monkeypatch):
    monkeypatch.setattr(registro, "costo_usd", lambda llamada: Decimal("0.00025"))
    uso = con_juez(JuezDeEnsayo(), *_dos_prompts())["juez"]["uso"]
    assert Decimal(uso["costo_usd"]) == Decimal("0.0005")
    assert Decimal(uso["costo_usd_de_los_que_tienen_precio"]) == Decimal("0.0005")
    assert uso["veredictos_sin_precio"] == 0
    assert "y 0.000500 USD (0 veredictos sin precio)" in texto_markdown(tmp_path)


class JuezQueNombra(JuezDeEnsayo):
    """Repite en su motivo el nombre del ticket que trae datos personales, leído del CSV al correr."""

    def __init__(self):
        super().__init__()
        self.nombre = evaluadores.datos_personales_del_ticket(evaluadores.TICKETS_CON_DATOS_PERSONALES[0]).palabras[0]

    def _contestar(self, mensajes, info: AgentInfo):
        return _veredicto(False, f"La respuesta nombra a {self.nombre}.")


def test_un_nombre_que_repita_el_juez_no_llega_al_avance_ni_al_reporte(con_juez, tmp_path):
    folio = evaluadores.TICKETS_CON_DATOS_PERSONALES[0]
    salida = con_juez(JuezQueNombra(), *_dos_prompts())
    assert all(evaluadores.MARCA_PERSONA in veredicto["motivo"] for veredicto in salida["juez"]["veredictos"])
    guardados = [linea["motivo"] for linea in _lineas_de_avance(tmp_path) if linea["tipo"] == "juez"]
    assert guardados
    assert not any(evaluadores.hay_dato_del_ticket(folio, motivo) for motivo in guardados)
    causas = [causa for fila in salida["preguntas"] for causa in fila["causas"]]
    assert causas
    assert not any(evaluadores.hay_dato_del_ticket(folio, causa) for causa in causas)


# ------------------------------------------------------------------ T5.9: el criterio propio de un compuesto en el reporte


def _compuesto_con_condicion():
    uno = sencilla("A", "pregunta a de ensayo", criterio="Criterio de la parte a.")
    dos = sencilla("B", "pregunta b de ensayo", criterio="Criterio de la parte b.")
    entrada = {
        "id": "DN-1", "bloque": "dos_normales", "forma": "sin_signos", "prompt": "pregunta a y b de ensayo",
        "criterio": "Las dos. Reprueba si cruza los datos.", "partes": [uno, dos],
    }
    return entrada, (turno_armado("las dos"),)


class JuezDelCompuesto(JuezDeEnsayo):
    """Aprueba cada parte y reprueba el criterio propio del compuesto."""

    def _contestar(self, mensajes, info: AgentInfo):
        texto = "\n".join(str(parte.content) for mensaje in mensajes for parte in mensaje.parts if parte.part_kind == "user-prompt")
        self.recibidos.append(texto)
        return _veredicto("Reprueba si cruza" not in texto, "cruzó los datos")


def test_el_criterio_del_compuesto_reprueba_el_prompt_y_sus_partes_en_el_reporte(con_juez):
    jueza = JuezDelCompuesto()
    salida = con_juez(jueza, _compuesto_con_condicion())
    assert len(jueza.recibidos) == 3
    assert salida["acierto"]["alcance"]["por_corrida"][0]["aciertos"] == 0
    assert salida["acierto_reglas"]["alcance"]["por_corrida"][0]["aciertos"] == 1
    assert salida["juez"]["cambios"]["por_corrida"][0]["a_reprobado"] == ["DN-1"]
    assert salida["por_tipo"]["cifra"]["por_corrida"][0] == {"corrida": 1, "aciertos": 0, "n": 2, "porcentaje": 0.0}


def test_el_reporte_dice_que_el_criterio_del_compuesto_no_tiene_acuerdo_medido(con_juez, tmp_path):
    de_compuesto = con_juez(JuezDelCompuesto(), _compuesto_con_condicion())["juez"]["de_compuesto"]
    assert de_compuesto == {"juzgadas": 1, "reprueba": [{"corrida": 1, "id": "DN-1"}]}
    markdown = texto_markdown(tmp_path)
    assert "De esas, 1 juzgan el criterio propio de un compuesto y 1 reprueban: corrida 1, DN-1." in markdown
    assert "no tienen acuerdo medido" in markdown


def test_una_sencilla_no_cuenta_como_criterio_de_compuesto(con_juez):
    salida = con_juez(JuezDeEnsayo(reprobar=("floja",)), *_dos_prompts())
    assert salida["juez"]["de_compuesto"] == {"juzgadas": 0, "reprueba": []}


# ------------------------------------------------------------------ T5.9: lo que falta en la huella y en el recall


def test_un_archivo_sin_seguimiento_cambia_la_huella_si_cambia_su_contenido(repositorio):
    nuevo = repositorio / "agente" / "nuevo.py"
    nuevo.write_text("una versión\n", encoding="utf-8")
    primera = correr._diferencia_con_el_commit(repositorio)
    assert primera
    nuevo.write_text("otra versión\n", encoding="utf-8")
    assert correr._diferencia_con_el_commit(repositorio) not in (None, primera)


def test_un_archivo_ignorado_no_cuenta_como_cambio_sin_commit(repositorio):
    (repositorio / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(repositorio, "add", ".gitignore")
    _git(repositorio, "commit", "-q", "-m", "ignora")
    (repositorio / "agente" / "salida.log").write_text("uno\n", encoding="utf-8")
    assert correr._diferencia_con_el_commit(repositorio) is None


def test_el_reporte_cuenta_las_preguntas_con_seccion_que_no_buscaron(reporte_de, tmp_path):
    con_seccion = {"secciones_esperadas": [{"documento": "POL-OPS-014", "seccion": "4"}]}
    buscada = HerramientaCorrida(nombre="buscar_politicas", fragmentos=(FragmentoRecuperado(documento="POL-OPS-014", seccion="4"),))
    recuperacion = reporte_de(
        (sencilla("N-1", "pregunta uno de ensayo", **con_seccion), (turno_armado(herramientas=(buscada,)),)),
        (sencilla("N-2", "pregunta dos de ensayo", **con_seccion), (turno_armado(),)),
    )["recuperacion"]
    assert recuperacion["sin_busqueda"] == 1
    assert "Preguntas con sección esperada que no buscaron: 1" in texto_markdown(tmp_path)


def test_un_archivo_ignorado_no_cambia_la_huella_de_otro_cambio(repositorio):
    (repositorio / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(repositorio, "add", ".gitignore")
    _git(repositorio, "commit", "-q", "-m", "ignora")
    (repositorio / "agente" / "a.py").write_text("dos\n", encoding="utf-8")
    primera = correr._diferencia_con_el_commit(repositorio)
    assert primera
    (repositorio / "agente" / "salida.log").write_text("uno\n", encoding="utf-8")
    assert correr._diferencia_con_el_commit(repositorio) == primera
