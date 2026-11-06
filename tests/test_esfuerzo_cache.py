"""Esfuerzo de razonamiento y caché del prefijo (T3.6, D8-A, palancas 1 y 4 de la sección de eficiencia).

Criterio de aceptación de la ficha: "con Claude, la lectura de caché es mayor que 0 desde la segunda llamada de una
sesión; los tokens de salida quedan dentro de la meta". Lo que se prueba aquí sin red ni llaves, y lo que no:

- Esfuerzo (D8-A): los clientes reales de Anthropic y de Google, con un transporte HTTP simulado, reciben el cuerpo que
  pide la decisión: Haiku 5.5 con razonamiento adaptativo y esfuerzo "medium" (D8-D desde el 2026-11-05; antes "low",
  y por eso dos pruebas conservan «low» en el nombre), Gemini con thinking_level MEDIUM. La
  configuración base de T6.1 (valores por omisión del proveedor, sin caché) no manda nada de esto.
- Caché (palanca 1): cache_control en el bloque del prompt de sistema y en la última herramienta, y en ningún otro
  lugar. El prefijo (herramientas y sistema) es idéntico byte por byte entre las llamadas de un turno, entre turnos,
  entre sesiones, entre fechas y entre procesos con otra semilla de hash, y no trae fechas ni ids: cualquiera de esas
  variaciones deja la caché en cero.
- "Lectura de caché mayor que 0 desde la segunda llamada": el servidor simulado aplica la regla de la documentación
  de Anthropic (prompt-caching, "What invalidates the cache"): un prefijo marcado se lee si otra solicitud lo escribió
  antes, idéntico, y pesa al menos 512 tokens en Haiku 5.5. Con eso se comprueba que las solicitudes de responder()
  cumplen la condición y que la traza recoge lo que el proveedor reporta. Que la lectura real sea mayor que 0 y que
  los tokens de salida queden dentro de la meta (mediana de 450 o menos) solo lo dice el modelo real: queda pendiente
  de la llave (T6.1 y T6.2).

El servidor simulado cuenta los tokens con estimar_tokens, que cuenta por arriba; el prefijo real pesa menos, pero
queda muy por encima del mínimo de 512. Ninguna prueba lee .env ni llama a un modelo real: la llave de los clientes es
un texto de prueba y el transporte nunca sale a la red.
"""

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import httpx2
import pytest
from anthropic import AsyncAnthropic
from google.genai.types import ThinkingLevel
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider

from agente import agente, construir
from agente.ajustes_modelo import AjustesDelModelo
from agente.herramientas.comun import estimar_tokens
from agente.historial import Historial

RAIZ = Path(__file__).resolve().parent.parent
HOY = dt.date(2026, 10, 31)
OTRO_DIA = dt.date(2026, 11, 20)
P03 = "¿Cuánto vendimos en total de junio a agosto?"
VENTAS_P03 = {"periodo": "de junio a agosto"}
CIFRA_P03 = "17,228,300.26"
TEXTO_P03 = f"{CIFRA_P03} pesos de venta de junio a agosto"
LLAVE_DE_PRUEBA = "clave-de-prueba-no-real"
# Claude Haiku 5.5: el prompt más corto que se cachea (prompt-caching, "Cache limitations").
MINIMO_CACHEABLE = 512
# La marca como la manda Pydantic AI: la vida de 5 minutos, la de omisión y la que usa la tabla de precios del plan.
MARCA = {"type": "ephemeral", "ttl": "5m"}


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def base(fts, tmp_path_factory):
    """La base del módulo, ya construida: estas pruebas no deben pagar la construcción en cada turno."""
    ruta = tmp_path_factory.mktemp("esfuerzo_cache") / "tienda.duckdb"
    construir.asegurar_base(construir.DIR_DATOS, ruta)
    return ruta


def preguntar(pregunta, modelo, base, historial=(), **opciones):
    opciones.setdefault("hoy_real", HOY)
    return agente.responder(pregunta, historial, modelo=modelo, base=base, **opciones)


# ------------------------------------------------------------------ Anthropic simulado, con la caché de prefijos


@dataclass(frozen=True)
class Paso:
    """Una respuesta del modelo simulado: sus bloques, por qué paró y cuántos tokens de salida y de razonamiento usó."""

    contenido: list[dict[str, Any]]
    parada: str
    razonamiento: int = 0
    salida: int = 40


def pide_ventas(entrada=None, *, razonamiento=30, id_llamada="toolu_01") -> Paso:
    """Lo que responde Haiku 5.5 con razonamiento adaptativo: un bloque de razonamiento (sin texto) y la llamada."""
    return Paso(
        [
            {"type": "thinking", "thinking": "", "signature": "firma-de-prueba"},
            {"type": "tool_use", "id": id_llamada, "name": "ventas", "input": entrada or VENTAS_P03},
        ],
        "tool_use",
        razonamiento,
    )


def dice(texto, *, razonamiento=0) -> Paso:
    return Paso([{"type": "text", "text": texto}], "end_turn", razonamiento)


def _bloques(contenido):
    return [{"type": "text", "text": contenido}] if isinstance(contenido, str) else list(contenido)


def bloques_del_prompt(cuerpo) -> list[dict[str, Any]]:
    """Los bloques del prompt en el orden con que Anthropic arma el prefijo: herramientas, sistema y mensajes."""
    sistema = _bloques(cuerpo.get("system") or [])
    mensajes = [
        {"rol": mensaje["role"], **bloque} for mensaje in cuerpo["messages"] for bloque in _bloques(mensaje["content"])
    ]
    return [*cuerpo.get("tools", []), *sistema, *mensajes]


def sin_marca(bloque) -> dict[str, Any]:
    return {clave: valor for clave, valor in bloque.items() if clave != "cache_control"}


def tokens_de(bloques) -> int:
    return estimar_tokens(json.dumps([sin_marca(bloque) for bloque in bloques], ensure_ascii=False))


class AnthropicSimulado:
    """Un Anthropic simulado: responde con un guion, anota los cuerpos que recibe y modela su caché de prefijos.

    Con ciclico=True el guion se repite (pide la herramienta, redacta, pide la herramienta...), para varios turnos.

    Modelo de la caché, según la documentación: un bloque con cache_control marca el final de un prefijo; el prefijo
    abarca herramientas, sistema y mensajes en ese orden. Se lee si otra solicitud escribió antes el mismo prefijo,
    con el mismo modelo, razonamiento y esfuerzo, y se escribe si pesa al menos MINIMO_CACHEABLE. input_tokens es lo
    que queda después del último punto de corte; la lectura y la escritura se reportan aparte.
    """

    def __init__(self, *pasos: Paso, con_cache: bool = True, ciclico: bool = False):
        self.pasos = list(pasos)
        self.ciclico = ciclico
        self.vistas: list[dict[str, Any]] = []
        self.usos: list[dict[str, Any]] = []
        self.con_cache = con_cache
        self._escritos: set[str] = set()

    def modelo(self, nombre: str = "claude-haiku-5-5") -> Model:
        cliente = AsyncAnthropic(
            api_key=LLAVE_DE_PRUEBA, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self._manejar))
        )
        return AnthropicModel(nombre, provider=AnthropicProvider(anthropic_client=cliente))

    def _clave(self, cuerpo, prefijo) -> str:
        contexto = {clave: cuerpo.get(clave) for clave in ("model", "thinking", "output_config")}
        texto = json.dumps([contexto, [sin_marca(bloque) for bloque in prefijo]], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(texto.encode("utf-8")).hexdigest()

    def uso_de(self, cuerpo, salida=40, razonamiento=0) -> dict[str, Any]:
        """El campo usage que reportaría Anthropic para esta solicitud, y deja escrita su caché."""
        bloques = bloques_del_prompt(cuerpo)
        total = tokens_de(bloques)
        prefijos = [bloques[: i + 1] for i, bloque in enumerate(bloques) if "cache_control" in bloque]
        leidos = escritos = 0
        if self.con_cache and prefijos:
            for prefijo in reversed(prefijos):
                if self._clave(cuerpo, prefijo) in self._escritos:
                    leidos = tokens_de(prefijo)
                    break
            mas_largo = prefijos[-1]
            if tokens_de(mas_largo) >= MINIMO_CACHEABLE:
                escritos = tokens_de(mas_largo) - leidos
                self._escritos.update(self._clave(cuerpo, prefijo) for prefijo in prefijos)
        uso: dict[str, Any] = {
            "input_tokens": total - leidos - escritos,
            "output_tokens": salida,
            "cache_read_input_tokens": leidos,
            "cache_creation_input_tokens": escritos,
        }
        if razonamiento:
            uso["output_tokens_details"] = {"thinking_tokens": razonamiento}
        return uso

    def _manejar(self, solicitud: httpx2.Request) -> httpx2.Response:
        cuerpo = json.loads(solicitud.content)
        numero = len(self.vistas)
        paso = self.pasos[numero % len(self.pasos) if self.ciclico else min(numero, len(self.pasos) - 1)]
        self.vistas.append(cuerpo)
        uso = self.uso_de(cuerpo, paso.salida, paso.razonamiento)
        self.usos.append(uso)
        return httpx2.Response(
            200,
            json={
                "id": f"msg_{len(self.vistas):02d}", "type": "message", "role": "assistant", "model": cuerpo["model"],
                "content": paso.contenido, "stop_reason": paso.parada, "stop_sequence": None, "usage": uso,
            },
        )


def marcas_de_cache(valor) -> int:
    """Cuántos cache_control hay en cualquier parte del cuerpo de una solicitud."""
    if isinstance(valor, dict):
        return sum((clave == "cache_control") + marcas_de_cache(hijo) for clave, hijo in valor.items())
    if isinstance(valor, list):
        return sum(marcas_de_cache(hijo) for hijo in valor)
    return 0


def prefijo(cuerpo) -> str:
    """Lo que se cachea, tal como viaja: las herramientas y el sistema, con sus claves en el orden en que se mandaron."""
    return json.dumps({"tools": cuerpo["tools"], "system": cuerpo["system"]}, ensure_ascii=False)


def hash_del_prefijo(cuerpo) -> str:
    return hashlib.sha256(prefijo(cuerpo).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ Gemini simulado


class GeminiSimulado:
    """Un Gemini simulado: responde con un guion de listas de partes y anota los cuerpos que recibe."""

    def __init__(self, *respuestas: list[dict[str, Any]], razonamiento: int = 7):
        self.respuestas = list(respuestas)
        self.razonamiento = razonamiento
        self.vistas: list[dict[str, Any]] = []

    def modelo(self, nombre: str = "gemini-3.5-flash-lite") -> Model:
        cliente = httpx2.AsyncClient(transport=httpx2.MockTransport(self._manejar))
        return GoogleModel(nombre, provider=GoogleProvider(api_key=LLAVE_DE_PRUEBA, http_client=cliente))

    def _manejar(self, solicitud: httpx2.Request) -> httpx2.Response:
        partes = self.respuestas[min(len(self.vistas), len(self.respuestas) - 1)]
        self.vistas.append(json.loads(solicitud.content))
        return httpx2.Response(
            200,
            json={
                "candidates": [{"content": {"role": "model", "parts": partes}, "finishReason": "STOP", "index": 0}],
                "usageMetadata": {
                    "promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": self.razonamiento,
                    "totalTokenCount": 120 + self.razonamiento,
                },
                "modelVersion": "gemini-3.5-flash-lite", "responseId": "abc",
            },
        )


GEMINI_PIDE_VENTAS = [{"functionCall": {"name": "ventas", "args": VENTAS_P03}}]
GEMINI_DICE = [{"text": TEXTO_P03}]


# ------------------------------------------------------------------ la configuración y su validación


def test_los_ajustes_por_omision_son_los_de_d8_a():
    assert agente.AJUSTES == AjustesDelModelo()
    assert agente.AJUSTES.esfuerzo_claude == "medium"
    assert agente.AJUSTES.esfuerzo_gemini == "medium"
    assert agente.AJUSTES.cache_claude is True


def test_la_configuracion_base_de_t6_1_deja_los_valores_del_proveedor_y_sin_cache():
    assert agente.AJUSTES_BASE.esfuerzo_claude is None
    assert agente.AJUSTES_BASE.esfuerzo_gemini is None
    assert agente.AJUSTES_BASE.cache_claude is False


@pytest.mark.parametrize("campo, valor", [
    ("esfuerzo_claude", "bajo"), ("esfuerzo_claude", "LOW"), ("esfuerzo_claude", "minimal"),
    ("esfuerzo_gemini", "bajo"), ("esfuerzo_gemini", "MEDIUM"), ("esfuerzo_gemini", "max"),
])
def test_un_esfuerzo_que_el_proveedor_no_conoce_se_rechaza_antes_de_mandarlo(campo, valor):
    with pytest.raises(ValueError, match=campo):
        AjustesDelModelo(**{campo: valor})


@pytest.mark.parametrize("esfuerzo", ["low", "medium", "high", "xhigh", "max"])
def test_cada_esfuerzo_de_claude_que_documenta_anthropic_es_valido(esfuerzo):
    assert AjustesDelModelo(esfuerzo_claude=esfuerzo).esfuerzo_claude == esfuerzo


@pytest.mark.parametrize("esfuerzo", ["minimal", "low", "medium", "high"])
def test_cada_nivel_de_gemini_3_que_documenta_google_es_valido(esfuerzo):
    assert AjustesDelModelo(esfuerzo_gemini=esfuerzo).esfuerzo_gemini == esfuerzo


def modelo_claude(nombre):
    return AnthropicModel(nombre, provider=AnthropicProvider(api_key=LLAVE_DE_PRUEBA))


def modelo_gemini(nombre):
    return GoogleModel(nombre, provider=GoogleProvider(api_key=LLAVE_DE_PRUEBA))


def test_haiku_5_5_recibe_razonamiento_adaptativo_esfuerzo_low_y_las_dos_marcas_de_cache():
    assert agente.AJUSTES.para(modelo_claude("claude-haiku-5-5")) == {
        "anthropic_thinking": {"type": "adaptive"},
        "anthropic_effort": "medium",
        "anthropic_cache_instructions": True,
        "anthropic_cache_tool_definitions": True,
    }


def test_gemini_3_5_flash_lite_recibe_el_nivel_medium_y_ninguna_marca_de_cache():
    ajustes = agente.AJUSTES.para(modelo_gemini("gemini-3.5-flash-lite"))
    assert ajustes == {"google_thinking_config": {"thinking_level": ThinkingLevel.MEDIUM}}
    # Sin include_thoughts: no hace falta el resumen del razonamiento, y no viaja a la traza ni al registro.
    assert "include_thoughts" not in ajustes["google_thinking_config"]


def test_un_claude_sin_esfuerzo_ni_razonamiento_adaptativo_solo_recibe_la_cache():
    # Haiku 4.5 no tiene el parámetro effort y rechaza thinking adaptive con un 400.
    assert agente.AJUSTES.para(modelo_claude("claude-haiku-4-5")) == {
        "anthropic_cache_instructions": True,
        "anthropic_cache_tool_definitions": True,
    }


def test_un_gemini_sin_niveles_de_razonamiento_no_recibe_thinking_level():
    # Gemini 2.5 usa thinking_budget y rechaza thinking_level.
    assert agente.AJUSTES.para(modelo_gemini("gemini-2.5-flash")) == {}


@pytest.mark.parametrize("modelo", [
    FunctionModel(lambda mensajes, info: ModelResponse(parts=[TextPart("listo")])),
    TestModel(),
], ids=["FunctionModel", "TestModel"])
def test_un_modelo_que_no_es_de_claude_ni_de_gemini_no_recibe_ajustes_del_proveedor(modelo):
    assert agente.AJUSTES.para(modelo) == {}


@pytest.mark.parametrize("id_del_modelo, variable, esperado", [
    ("anthropic:claude-haiku-5-5", "ANTHROPIC_API_KEY", {
        "anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": "medium",
        "anthropic_cache_instructions": True, "anthropic_cache_tool_definitions": True,
    }),
    ("google:gemini-3.5-flash-lite", "GOOGLE_API_KEY", {"google_thinking_config": {"thinking_level": ThinkingLevel.MEDIUM}}),
])
def test_el_id_que_se_escribe_en_modelo_recibe_los_ajustes_de_su_proveedor(monkeypatch, id_del_modelo, variable, esperado):
    # MODELO de .env es un texto: Pydantic AI arma el modelo de su proveedor al usarlo, con la llave del entorno.
    monkeypatch.setenv(variable, LLAVE_DE_PRUEBA)
    assert agente.AJUSTES.para(infer_model(id_del_modelo)) == esperado


def test_el_modelo_por_omision_de_d19_d_recibe_los_ajustes_de_haiku(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", LLAVE_DE_PRUEBA)
    modelo = infer_model(agente.modelo_del_entorno({}))
    assert agente.AJUSTES.para(modelo) == {
        "anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": "medium",
        "anthropic_cache_instructions": True, "anthropic_cache_tool_definitions": True,
    }


def test_el_modelo_envuelto_en_reintentos_recibe_los_ajustes_de_su_proveedor():
    from agente.reintentos import ModeloConReintentos

    envuelto = ModeloConReintentos(modelo_claude("claude-haiku-5-5"), reintentos=3, restante=lambda: 60.0, registro=[])
    assert agente.AJUSTES.para(envuelto) == agente.AJUSTES.para(modelo_claude("claude-haiku-5-5"))


def test_la_configuracion_base_no_manda_nada_a_ningun_proveedor():
    for modelo in (modelo_claude("claude-haiku-5-5"), modelo_gemini("gemini-3.5-flash-lite"), TestModel()):
        assert agente.AJUSTES_BASE.para(modelo) == {}


def test_se_puede_apagar_la_cache_o_cambiar_el_esfuerzo_sin_tocar_lo_demas():
    haiku = modelo_claude("claude-haiku-5-5")
    sin_cache = AjustesDelModelo(cache_claude=False).para(haiku)
    assert sin_cache == {"anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": "medium"}
    en_medium = AjustesDelModelo(esfuerzo_claude="medium").para(haiku)
    assert en_medium["anthropic_effort"] == "medium"
    assert en_medium["anthropic_cache_instructions"] is True
    solo_cache = AjustesDelModelo(esfuerzo_claude=None).para(haiku)
    assert solo_cache == {"anthropic_cache_instructions": True, "anthropic_cache_tool_definitions": True}
    gemini = AjustesDelModelo(esfuerzo_gemini="low").para(modelo_gemini("gemini-3.5-flash-lite"))
    assert gemini == {"google_thinking_config": {"thinking_level": ThinkingLevel.LOW}}


def test_los_ajustes_de_cada_proveedor_llevan_solo_claves_con_su_prefijo():
    # Pydantic AI exige que cada clave propia de un proveedor lleve su prefijo para poder mezclarlas.
    assert all(c.startswith("anthropic_") for c in agente.AJUSTES.para(modelo_claude("claude-haiku-5-5")))
    assert all(c.startswith("google_") for c in agente.AJUSTES.para(modelo_gemini("gemini-3.5-flash-lite")))


# ------------------------------------------------------------------ esfuerzo (D8-A) en el cuerpo HTTP real


def test_haiku_razona_de_forma_adaptativa_con_esfuerzo_low_en_cada_solicitud_del_turno(base):
    servidor = AnthropicSimulado(pide_ventas(), dice(TEXTO_P03))
    respuesta = preguntar(P03, servidor.modelo(), base)

    assert respuesta.estado == "ok"
    assert len(servidor.vistas) == 2
    for cuerpo in servidor.vistas:
        assert cuerpo["thinking"] == {"type": "adaptive"}
        assert cuerpo["output_config"] == {"effort": "medium"}
        # El tope de K19 sigue en cada llamada y es mayor que lo que razona en medium: el razonamiento sale de max_tokens.
        assert cuerpo["max_tokens"] == agente.TOPES.max_tokens
        assert cuerpo["stream"] is False


def test_gemini_razona_con_thinking_level_medium_en_cada_solicitud_del_turno(base):
    servidor = GeminiSimulado(GEMINI_PIDE_VENTAS, GEMINI_DICE)
    respuesta = preguntar(P03, servidor.modelo(), base)

    assert respuesta.estado == "ok"
    assert len(servidor.vistas) == 2
    for cuerpo in servidor.vistas:
        configuracion = cuerpo["generationConfig"]
        # El SDK de Google deja el objeto thinking_config tal como lo recibe: sus claves viajan en minúsculas con guion
        # bajo, que es el nombre original del campo y el servidor acepta igual que el nombre en camelCase.
        assert configuracion["thinkingConfig"] == {"thinking_level": "MEDIUM"}
        assert configuracion["maxOutputTokens"] == agente.TOPES.max_tokens


def test_la_configuracion_base_no_manda_razonamiento_ni_esfuerzo_ni_cache_a_claude(base):
    servidor = AnthropicSimulado(pide_ventas(razonamiento=0), dice(TEXTO_P03))
    respuesta = preguntar(P03, servidor.modelo(), base, ajustes=agente.AJUSTES_BASE)

    assert respuesta.estado == "ok"
    for cuerpo in servidor.vistas:
        assert "thinking" not in cuerpo
        assert "output_config" not in cuerpo
        assert marcas_de_cache(cuerpo) == 0
        assert isinstance(cuerpo["system"], list) and "cache_control" not in cuerpo["system"][0]
    assert respuesta.traza.ajustes_modelo == {}


def test_la_configuracion_base_no_manda_thinking_config_a_gemini(base):
    servidor = GeminiSimulado(GEMINI_PIDE_VENTAS, GEMINI_DICE)
    respuesta = preguntar(P03, servidor.modelo(), base, ajustes=agente.AJUSTES_BASE)

    assert respuesta.estado == "ok"
    assert all("thinkingConfig" not in cuerpo["generationConfig"] for cuerpo in servidor.vistas)


def test_un_esfuerzo_distinto_viaja_en_la_solicitud_para_medir_la_palanca_4(base):
    servidor = AnthropicSimulado(pide_ventas(), dice(TEXTO_P03))
    # Un nivel que no es el de omisión: con "medium" la prueba pasaría aunque los ajustes pedidos no viajaran.
    preguntar(P03, servidor.modelo(), base, ajustes=AjustesDelModelo(esfuerzo_claude="low"))
    assert [cuerpo["output_config"] for cuerpo in servidor.vistas] == [{"effort": "low"}] * 2

    gemini = GeminiSimulado(GEMINI_PIDE_VENTAS, GEMINI_DICE)
    preguntar(P03, gemini.modelo(), base, ajustes=AjustesDelModelo(esfuerzo_gemini="low"))
    assert [c["generationConfig"]["thinkingConfig"] for c in gemini.vistas] == [{"thinking_level": "LOW"}] * 2


def test_un_modelo_simulado_de_pydantic_ai_no_recibe_claves_de_ningun_proveedor(base):
    vistos = []

    def funcion(mensajes, info: AgentInfo):
        vistos.append(info.model_settings)
        return ModelResponse(parts=[TextPart("hola")])

    preguntar("hola", FunctionModel(funcion), base)
    [ajustes] = vistos
    assert ajustes["max_tokens"] == agente.TOPES.max_tokens
    assert not [clave for clave in ajustes if clave.startswith(("anthropic_", "google_"))]


def test_el_bloque_de_razonamiento_de_la_primera_llamada_vuelve_intacto_en_la_segunda(base):
    # Con razonamiento adaptativo la respuesta que pide una herramienta empieza con un bloque de razonamiento, y la
    # solicitud siguiente tiene que devolverlo tal cual: sin él, Anthropic responde 400.
    servidor = AnthropicSimulado(pide_ventas(), dice(TEXTO_P03))
    respuesta = preguntar(P03, servidor.modelo(), base)

    assert respuesta.estado == "ok"
    [mensaje_del_modelo] = [m for m in servidor.vistas[1]["messages"] if m["role"] == "assistant"]
    assert mensaje_del_modelo["content"][0] == {"type": "thinking", "thinking": "", "signature": "firma-de-prueba"}
    assert mensaje_del_modelo["content"][1]["type"] == "tool_use"


# ------------------------------------------------------------------ caché: dónde van las marcas


def test_cache_control_va_en_el_prompt_de_sistema_y_en_la_ultima_herramienta_y_en_ningun_otro_lugar(base):
    servidor = AnthropicSimulado(pide_ventas(), dice(TEXTO_P03))
    preguntar(P03, servidor.modelo(), base)

    assert len(servidor.vistas) == 2
    for cuerpo in servidor.vistas:
        [bloque_de_sistema] = cuerpo["system"]
        assert bloque_de_sistema["text"] == agente.PROMPT_SISTEMA.strip()
        assert bloque_de_sistema["cache_control"] == MARCA
        marcadas = [herramienta["name"] for herramienta in cuerpo["tools"] if "cache_control" in herramienta]
        assert marcadas == [agente.NOMBRES_HERRAMIENTAS[-1]]
        assert cuerpo["tools"][-1]["cache_control"] == MARCA
        assert [t["name"] for t in cuerpo["tools"]] == list(agente.NOMBRES_HERRAMIENTAS)
        # Dos marcas en total: ninguna en los mensajes (el historial cambia en cada turno) ni en la raíz.
        assert marcas_de_cache(cuerpo) == 2
        assert "cache_control" not in cuerpo


def test_con_historial_el_prompt_y_las_herramientas_siguen_marcados_y_los_mensajes_no(base):
    servidor = AnthropicSimulado(
        pide_ventas(id_llamada="toolu_01"), dice(TEXTO_P03), pide_ventas(id_llamada="toolu_02"), dice(TEXTO_P03)
    )
    historial = Historial()
    for _ in range(2):
        respuesta = preguntar(P03, servidor.modelo(), base, historial)
        historial.agregar(P03, respuesta)

    assert len(servidor.vistas) == 4
    assert all(marcas_de_cache(cuerpo) == 2 for cuerpo in servidor.vistas)
    # Las llamadas del segundo turno mandan el historial como texto, y el prompt de sistema va completo.
    assert len(servidor.vistas[2]["messages"]) > len(servidor.vistas[0]["messages"])
    assert servidor.vistas[2]["system"][0]["text"] == agente.PROMPT_SISTEMA.strip()


# ------------------------------------------------------------------ caché: el prefijo no cambia


def test_el_prefijo_es_identico_entre_las_dos_llamadas_de_un_turno(base):
    servidor = AnthropicSimulado(pide_ventas(), dice(TEXTO_P03))
    preguntar(P03, servidor.modelo(), base)

    primera, segunda = servidor.vistas
    assert prefijo(primera) == prefijo(segunda)
    assert primera["messages"] != segunda["messages"]  # lo que cambia está después del prefijo


def test_el_prefijo_es_identico_entre_sesiones_fechas_y_preguntas(base):
    servidor = AnthropicSimulado(pide_ventas(), dice(TEXTO_P03), ciclico=True)
    preguntar(P03, servidor.modelo(), base, Historial(), hoy_real=HOY)
    preguntar("¿y cuánto vendimos ayer?", servidor.modelo(), base, Historial(), hoy_real=OTRO_DIA)
    preguntar(P03, servidor.modelo(), base, Historial(), hoy_real=None)  # el reloj del sistema

    assert len(servidor.vistas) == 6
    assert len({hash_del_prefijo(cuerpo) for cuerpo in servidor.vistas}) == 1


def test_el_prefijo_no_trae_fechas_ni_horas_ni_ids_que_cambien(base):
    servidor = AnthropicSimulado(pide_ventas(), dice(TEXTO_P03))
    historial = Historial()
    preguntar(P03, servidor.modelo(), base, historial)

    texto = prefijo(servidor.vistas[0])
    variables = {
        "fecha ISO": r"\b\d{4}-\d{2}-\d{2}\b",
        "hora": r"\b\d{2}:\d{2}(:\d{2})?\b",
        "UUID": r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        "id de mensaje o de llamada del proveedor": r"\b(msg|toolu|call)_[0-9A-Za-z]+",
        "marca de tiempo": r"\b1[0-9]{9,12}\b",
    }
    for nombre, patron in variables.items():
        assert not re.search(patron, texto), nombre
    # Ni el id de la sesión, ni el día de hoy dicho de ninguna manera.
    for dato in (historial.id_sesion, HOY.isoformat(), "31 de octubre", "octubre de 2026", "hoy es"):
        assert dato.casefold() not in texto.casefold(), dato


CODIGO_DEL_PREFIJO = """
import hashlib, json, sys
sys.path.insert(0, {raiz!r})
from agente import agente
herramientas = [
    {{"name": t.tool_def.name, "description": t.tool_def.description, "input_schema": t.tool_def.parameters_json_schema}}
    for t in agente.HERRAMIENTAS
]
texto = json.dumps({{"system": agente.PROMPT_SISTEMA, "tools": herramientas}}, ensure_ascii=False)
print(hashlib.sha256(texto.encode("utf-8")).hexdigest())
"""


def hash_del_prefijo_en_otro_proceso(semilla: str) -> str:
    entorno = {**os.environ, "PYTHONHASHSEED": semilla, "PYDANTIC_AI_NO_BANNER": "1", "PYTHONIOENCODING": "utf-8"}
    resultado = subprocess.run(
        [sys.executable, "-c", CODIGO_DEL_PREFIJO.format(raiz=str(RAIZ))],
        cwd=RAIZ, env=entorno, capture_output=True, text=True, timeout=120, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr[-2000:]
    return resultado.stdout.strip().splitlines()[-1]


def test_el_prefijo_no_depende_de_la_semilla_de_hash_del_proceso():
    # Si el esquema de una herramienta saliera de un conjunto, el orden cambiaría entre procesos y la interfaz
    # (que se reinicia) escribiría la caché de nuevo cada vez.
    herramientas = [
        {"name": t.tool_def.name, "description": t.tool_def.description, "input_schema": t.tool_def.parameters_json_schema}
        for t in agente.HERRAMIENTAS
    ]
    aqui = json.dumps({"system": agente.PROMPT_SISTEMA, "tools": herramientas}, ensure_ascii=False)
    esperado = hashlib.sha256(aqui.encode("utf-8")).hexdigest()
    assert hash_del_prefijo_en_otro_proceso("1") == esperado
    assert hash_del_prefijo_en_otro_proceso("2") == esperado


# ------------------------------------------------------------------ caché: lectura desde la segunda llamada


def test_el_simulador_de_cache_solo_lee_un_prefijo_identico_y_de_al_menos_512_tokens():
    # Sin esta comprobación, las pruebas que siguen podrían pasar con un simulador que siempre lee.
    servidor = AnthropicSimulado()
    sistema = {"type": "text", "text": "regla " * 1500, "cache_control": {"type": "ephemeral"}}
    cuerpo = {"model": "m", "system": [sistema], "messages": [{"role": "user", "content": "hola"}]}

    escrito = servidor.uso_de(cuerpo)
    assert (escrito["cache_read_input_tokens"], escrito["cache_creation_input_tokens"]) == (0, tokens_de([sistema]))
    leido = servidor.uso_de(cuerpo)
    assert (leido["cache_read_input_tokens"], leido["cache_creation_input_tokens"]) == (tokens_de([sistema]), 0)

    con_fecha = {**cuerpo, "system": [{**sistema, "text": "Hoy es 2026-11-01. " + sistema["text"]}]}
    assert servidor.uso_de(con_fecha)["cache_read_input_tokens"] == 0
    otro_esfuerzo = {**cuerpo, "output_config": {"effort": "high"}}
    assert servidor.uso_de(otro_esfuerzo)["cache_read_input_tokens"] == 0

    corto = {"model": "m", "system": [{"type": "text", "text": "hola", "cache_control": {"type": "ephemeral"}}],
             "messages": cuerpo["messages"]}
    assert servidor.uso_de(corto)["cache_creation_input_tokens"] == 0
    assert servidor.uso_de(corto)["cache_read_input_tokens"] == 0


def test_la_lectura_de_cache_es_mayor_que_0_desde_la_segunda_llamada_de_la_sesion(base):
    servidor = AnthropicSimulado(
        pide_ventas(id_llamada="toolu_01"), dice(TEXTO_P03), pide_ventas(id_llamada="toolu_02"), dice(TEXTO_P03)
    )
    historial = Historial()
    llamadas = []
    for _ in range(2):
        respuesta = preguntar(P03, servidor.modelo(), base, historial)
        assert respuesta.estado == "ok"
        historial.agregar(P03, respuesta)
        llamadas += respuesta.traza.llamadas

    primera, *siguientes = llamadas
    assert len(siguientes) == 3
    # La primera llamada de la sesión escribe el prefijo; ninguna de las que siguen, en este turno o en el próximo,
    # empieza de cero.
    assert primera.cache_lectura_tokens == 0
    assert primera.cache_escritura_tokens >= MINIMO_CACHEABLE
    assert all(llamada.cache_lectura_tokens > 0 for llamada in siguientes)
    # Se lee lo que se escribió: el prefijo de herramientas y sistema.
    assert siguientes[0].cache_lectura_tokens == primera.cache_escritura_tokens
    # Y a precio completo solo se paga lo que viene después del prefijo: la pregunta, el historial y la herramienta.
    a_precio_completo = [l.entrada_tokens - l.cache_lectura_tokens - l.cache_escritura_tokens for l in siguientes]
    assert all(0 < tokens < siguientes[0].cache_lectura_tokens for tokens in a_precio_completo)


def test_sin_cache_control_no_hay_lectura_de_cache_en_ninguna_llamada(base):
    servidor = AnthropicSimulado(pide_ventas(), dice(TEXTO_P03))
    respuesta = preguntar(P03, servidor.modelo(), base, ajustes=agente.AJUSTES_BASE)

    assert [(l.cache_lectura_tokens, l.cache_escritura_tokens) for l in respuesta.traza.llamadas] == [(0, 0), (0, 0)]


def test_el_reintento_de_la_verificacion_conserva_el_prefijo_y_lee_la_cache(base):
    # D16-A: una cifra inventada provoca un reintento en el mismo turno, con el mismo esfuerzo, las mismas marcas y
    # el mismo prefijo; la solicitud del reintento no vuelve a escribir la caché.
    servidor = AnthropicSimulado(pide_ventas(), dice("999,999.99 pesos de venta"), dice(TEXTO_P03))
    respuesta = preguntar(P03, servidor.modelo(), base)

    assert respuesta.traza.verificacion.desenlace == "corregida"
    assert len(servidor.vistas) == 3
    assert len({hash_del_prefijo(cuerpo) for cuerpo in servidor.vistas}) == 1
    assert all(cuerpo["thinking"] == {"type": "adaptive"} for cuerpo in servidor.vistas)
    assert all(cuerpo["output_config"] == {"effort": "medium"} for cuerpo in servidor.vistas)
    assert all(marcas_de_cache(cuerpo) == 2 for cuerpo in servidor.vistas)
    assert all(llamada.cache_lectura_tokens > 0 for llamada in respuesta.traza.llamadas[1:])


# ------------------------------------------------------------------ traza


def test_la_traza_separa_lectura_escritura_y_razonamiento_de_cada_llamada(base):
    servidor = AnthropicSimulado(pide_ventas(razonamiento=30), dice(TEXTO_P03, razonamiento=12))
    respuesta = preguntar(P03, servidor.modelo(), base)

    primera, segunda = respuesta.traza.llamadas
    assert (primera.razonamiento_tokens, segunda.razonamiento_tokens) == (30, 12)
    # La salida que reporta el proveedor ya incluye el razonamiento (hallazgo 6): no se suma otra vez.
    assert (primera.salida_tokens, segunda.salida_tokens) == (40, 40)
    assert respuesta.traza.salida_tokens == 80
    # La entrada incluye lo leído y lo escrito en la caché.
    for llamada, uso in zip(respuesta.traza.llamadas, servidor.usos, strict=True):
        assert llamada.cache_lectura_tokens == uso["cache_read_input_tokens"]
        assert llamada.cache_escritura_tokens == uso["cache_creation_input_tokens"]
        assert llamada.entrada_tokens == (
            uso["input_tokens"] + uso["cache_read_input_tokens"] + uso["cache_creation_input_tokens"]
        )


def test_el_razonamiento_de_gemini_tambien_queda_en_la_traza(base):
    servidor = GeminiSimulado(GEMINI_PIDE_VENTAS, GEMINI_DICE, razonamiento=7)
    respuesta = preguntar(P03, servidor.modelo(), base)

    assert [llamada.razonamiento_tokens for llamada in respuesta.traza.llamadas] == [7, 7]
    assert [llamada.salida_tokens for llamada in respuesta.traza.llamadas] == [27, 27]


def test_sin_razonamiento_el_campo_vale_0(base):
    respuesta = preguntar("hola", TestModel(call_tools=[], custom_output_text="Hola."), base)
    assert [llamada.razonamiento_tokens for llamada in respuesta.traza.llamadas] == [0]


def test_la_traza_dice_con_que_esfuerzo_y_cache_corrio_el_turno(base):
    claude = preguntar(P03, AnthropicSimulado(pide_ventas(), dice(TEXTO_P03)).modelo(), base)
    assert claude.traza.ajustes_modelo == {
        "anthropic_thinking": {"type": "adaptive"},
        "anthropic_effort": "medium",
        "anthropic_cache_instructions": True,
        "anthropic_cache_tool_definitions": True,
    }
    gemini = preguntar(P03, GeminiSimulado(GEMINI_PIDE_VENTAS, GEMINI_DICE).modelo(), base)
    assert gemini.traza.ajustes_modelo == {"google_thinking_config": {"thinking_level": "MEDIUM"}}
    assert preguntar("hola", TestModel(call_tools=[], custom_output_text="Hola."), base).traza.ajustes_modelo == {}
    # Se guarda en el registro como JSON (T4.3).
    assert json.loads(json.dumps(gemini.traza.ajustes_modelo)) == {"google_thinking_config": {"thinking_level": "MEDIUM"}}


def test_un_turno_sin_modelo_que_consultar_no_deja_ajustes_en_la_traza(fts, copia_datos, tmp_path):
    (copia_datos / "ventas.csv").rename(copia_datos / "ventas_renombrado.csv")

    def modelo(mensajes, info):
        raise AssertionError("no debe llamar al modelo")

    respuesta = preguntar(P03, FunctionModel(modelo), tmp_path / "tienda.duckdb", dir_datos=copia_datos)
    assert respuesta.estado == "fuente_no_disponible"
    assert respuesta.traza.ajustes_modelo == {}


def test_un_tope_o_una_falla_del_modelo_conservan_los_ajustes_con_que_se_intento(base):
    sin_tokens = AnthropicSimulado(
        Paso([{"type": "text", "text": "17,2"}], "max_tokens", razonamiento=4000, salida=4096),
    )
    respuesta = preguntar(P03, sin_tokens.modelo(), base)
    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "tokens"
    assert respuesta.traza.ajustes_modelo["anthropic_effort"] == "medium"
    assert respuesta.traza.llamadas[0].razonamiento_tokens == 4000


def test_importar_el_agente_no_carga_los_sdk_de_los_proveedores():
    # Los SDK de Anthropic y de Google tardan unos 3 s en importarse y cada proceso de la suite los pagaba al
    # cargar agente.agente. Se cargan cuando se arma un modelo de ese proveedor.
    codigo = (
        "import sys\n"
        "import agente.agente\n"
        "print(sorted(m for m in ('anthropic', 'google.genai', 'pydantic_ai.models.anthropic',"
        " 'pydantic_ai.models.google') if m in sys.modules))\n"
    )
    salida = subprocess.run(
        [sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, encoding="utf-8", check=True
    ).stdout

    assert salida.strip() == "[]"
