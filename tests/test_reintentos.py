"""Reintento propio ante un 429 y falla del modelo sin cifra (T3.3, K20, K21).

Criterio de aceptación de la ficha, y lo que cubre cada prueba sin red ni llaves:

- "un 429 simulado con Retry-After: 2 deja 1 reintento y 2 s de espera en el registro, con los dos proveedores":
  los clientes reales de Anthropic y de Google (SDK y modelo de Pydantic AI), con un transporte HTTP simulado en
  lugar de la red. El transporte cuenta las solicitudes: si el SDK reintentara por su cuenta, el registro no se
  enteraría (hallazgos 1 y 2), así que el turno tiene que dejar 2 solicitudes, 1 reintento y 2.0 s de espera en
  la traza (test_un_429_con_retry_after_2_...). Una prueba espera de verdad esos 2 s.
- "con una llave inválida la respuesta dice que no pudo consultar el modelo y no da cifra": el transporte
  responde como lo haría cada proveedor ante una llave inválida (401 en Anthropic, 400 API_KEY_INVALID en Gemini)
  y la Respuesta sale con estado "modelo_no_disponible", sin dígitos y sin repetir lo que dijo el proveedor
  (test_con_una_llave_invalida_...).

K20 (espera lo que diga Retry-After en segundos o como fecha HTTP; sin la cabecera, retroceso exponencial; un
máximo de intentos dentro del tope de tiempo del turno) y K21 (error inesperado como mensaje en español sin
traza, y todo ErrorConstruccion sin la ruta con el usuario de Windows) se prueban sin proveedor con un modelo
simulado que lanza los errores de Pydantic AI, y con la base preparada.

La base se construye una vez en un directorio temporal. Si la extensión fts no está instalada, las pruebas que
la necesitan se saltan: sin red no se descarga (T0.5). Ninguna prueba lee .env ni llama a un modelo real: la
llave de los clientes es un texto de prueba y el transporte nunca sale a la red.
"""

import asyncio
import datetime as dt
import email.utils
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelHTTPError,
    ModelOverloadedError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelServerError,
    ModelTimeoutError,
    ModelUnavailableError,
    UserError,
)
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import Model, ModelRequestParameters, infer_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider

from agente import agente, construir, reintentos
from agente.consultas import BaseNoDisponible
from agente.reintentos import FallaDelModelo, ModeloConReintentos, Reintento

HOY = dt.date(2026, 10, 31)
P03 = "¿Cuánto vendimos en total de junio a agosto?"
VENTAS_P03 = {"periodo": "de junio a agosto"}
CIFRA_P03 = "17,228,300.26"
LLAVE_DE_PRUEBA = "clave-de-prueba-no-real"
RUTA_CON_USUARIO = r"C:\Users\usuario_ficticio\Documents\Agent"


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def base(fts, tmp_path_factory):
    """La base del módulo, ya construida: las pruebas de reintentos no deben pagar la construcción."""
    ruta = tmp_path_factory.mktemp("reintentos") / "tienda.duckdb"
    construir.asegurar_base(construir.DIR_DATOS, ruta)
    return ruta


@pytest.fixture
def esperas(monkeypatch):
    """Cambia las esperas reales por unas que solo anotan los segundos que se pidieron."""
    anotadas = SimpleNamespace(dormidas=[], pausas=[])

    async def dormir(segundos):
        anotadas.dormidas.append(segundos)

    def pausar(segundos):
        anotadas.pausas.append(segundos)

    monkeypatch.setattr(reintentos, "dormir", dormir)
    monkeypatch.setattr(reintentos, "pausar", pausar)
    return anotadas


def preguntar(pregunta, modelo, base, **opciones):
    return agente.responder(pregunta, modelo=modelo, base=base, hoy_real=HOY, **opciones)


def sin_digitos(texto):
    return not re.search(r"[0-9]", texto)


def vueltas_del_turno(mensajes):
    """Respuestas del modelo después de la última pregunta del usuario."""
    ultima = max(i for i, m in enumerate(mensajes) if any(isinstance(p, UserPromptPart) for p in m.parts))
    return sum(isinstance(m, ModelResponse) for m in mensajes[ultima:])


def ultimo_resultado(mensajes):
    return [p for m in mensajes for p in m.parts if isinstance(p, ToolReturnPart)][-1].content


# ------------------------------------------------------------------ errores de Pydantic AI, como los lanzan los modelos

def error_http(estado, categoria=None, *, cuerpo=None, cabeceras=None):
    return ModelHTTPError.for_category(
        categoria, status_code=estado, model_name="simulado", body=cuerpo, headers=cabeceras
    )


def limite(retry_after=None):
    cabeceras = None if retry_after is None else {"retry-after": retry_after}
    return error_http(429, ModelRateLimitError, cabeceras=cabeceras)


def llave_invalida():
    return error_http(401, cuerpo={"type": "error", "error": {"type": "authentication_error", "message": "SECRETO"}})


def modelo_con_fallas(*fallas, final="Listo."):
    """Modelo simulado: la solicitud i lanza fallas[i] (None: contesta); las demás contestan con `final`.

    El registro cuenta las solicitudes que recibió y con qué ajustes.
    """
    registro = SimpleNamespace(llamadas=0, ajustes=[])

    def funcion(mensajes, info: AgentInfo):
        i = registro.llamadas
        registro.llamadas += 1
        registro.ajustes.append(info.model_settings)
        if i < len(fallas) and fallas[i] is not None:
            raise fallas[i]
        return ModelResponse(parts=[TextPart(final)])

    return FunctionModel(funcion, model_name="falla"), registro


def modelo_p03(*fallas):
    """Como el modelo de P03 (pide ventas y redacta la cifra), pero la solicitud i lanza fallas[i] si no es None."""
    registro = SimpleNamespace(llamadas=0)

    def funcion(mensajes, info: AgentInfo):
        i = registro.llamadas
        registro.llamadas += 1
        if i < len(fallas) and fallas[i] is not None:
            raise fallas[i]
        if vueltas_del_turno(mensajes) == 0:
            return ModelResponse(parts=[ToolCallPart("ventas", VENTAS_P03, tool_call_id="v0")])
        resultado = json.loads(ultimo_resultado(mensajes))
        return ModelResponse(parts=[TextPart(f"{resultado['cifras']['venta']} pesos de venta de junio a agosto")])

    return FunctionModel(funcion, model_name="p03"), registro


def envolver(interno, *, reintentos=3, restante=lambda: 60.0):
    registro = []
    return ModeloConReintentos(interno, reintentos=reintentos, restante=restante, registro=registro), registro


def solicitar(modelo, ajustes=None):
    """Una solicitud directa al modelo, sin agente."""

    async def una():
        return await modelo.request([ModelRequest(parts=[UserPromptPart("hola")])], ajustes, ModelRequestParameters())

    return asyncio.run(una())


# ------------------------------------------------------------------ los dos proveedores, con transporte simulado

def servidor(*respuestas):
    """Un transporte HTTP simulado: responde en orden (la última se repite) y guarda las solicitudes que recibió."""
    vistas = []

    def manejar(solicitud: httpx2.Request) -> httpx2.Response:
        vistas.append(solicitud)
        estado, cuerpo, cabeceras = respuestas[min(len(vistas), len(respuestas)) - 1]
        return httpx2.Response(estado, json=cuerpo, headers=cabeceras)

    return httpx2.MockTransport(manejar), vistas


def cliente_http(transporte):
    return httpx2.AsyncClient(transport=transporte)


def modelo_anthropic(transporte) -> Model:
    cliente = AsyncAnthropic(api_key=LLAVE_DE_PRUEBA, http_client=cliente_http(transporte))
    return AnthropicModel("claude-haiku-5-5", provider=AnthropicProvider(anthropic_client=cliente))


def modelo_google(transporte) -> Model:
    proveedor = GoogleProvider(api_key=LLAVE_DE_PRUEBA, http_client=cliente_http(transporte))
    return GoogleModel("gemini-3.5-flash-lite", provider=proveedor)


@dataclass(frozen=True)
class Proveedor:
    """Cómo habla cada proveedor: su modelo, un 429, una respuesta buena y la respuesta a una llave inválida."""

    nombre: str
    modelo: Callable[[Any], Model]
    limite: dict
    exito: dict
    llave_invalida: tuple[int, dict]
    saturado: tuple[int, dict]
    # Lo que dice el proveedor en sus errores: nunca debe llegar a la gerente ni a la traza.
    dicho_por_el_proveedor: tuple[str, ...]


ANTHROPIC = Proveedor(
    nombre="anthropic",
    modelo=modelo_anthropic,
    limite={"type": "error", "error": {"type": "rate_limit_error", "message": "mensaje-del-proveedor"}},
    exito={
        "id": "msg_01", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": "Hola, no tengo cifras."}],
        "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
    },
    llave_invalida=(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}),
    saturado=(529, {"type": "error", "error": {"type": "overloaded_error", "message": "mensaje-del-proveedor"}}),
    dicho_por_el_proveedor=("invalid x-api-key", "authentication_error", "mensaje-del-proveedor"),
)

GOOGLE = Proveedor(
    nombre="google",
    modelo=modelo_google,
    limite={"error": {"code": 429, "message": "mensaje-del-proveedor", "status": "RESOURCE_EXHAUSTED"}},
    exito={
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": "Hola, no tengo cifras."}]}, "finishReason": "STOP", "index": 0}
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
        "modelVersion": "gemini-3.5-flash-lite", "responseId": "abc",
    },
    llave_invalida=(400, {"error": {
        "code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT",
        "details": [{
            "@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID",
            "domain": "googleapis.com", "metadata": {"service": "generativelanguage.googleapis.com"},
        }],
    }}),
    saturado=(503, {"error": {"code": 503, "message": "mensaje-del-proveedor", "status": "UNAVAILABLE"}}),
    dicho_por_el_proveedor=("API key not valid", "API_KEY_INVALID", "INVALID_ARGUMENT", "mensaje-del-proveedor"),
)

PROVEEDORES = pytest.mark.parametrize("proveedor", [ANTHROPIC, GOOGLE], ids=lambda p: p.nombre)


# ------------------------------------------------------------------ criterio de la ficha: el 429 con Retry-After: 2

@PROVEEDORES
def test_un_429_con_retry_after_2_deja_un_reintento_y_2_s_de_espera_en_el_registro(proveedor, base, esperas):
    transporte, vistas = servidor((429, proveedor.limite, {"Retry-After": "2"}), (200, proveedor.exito, {}))
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)

    assert respuesta.estado == "ok"
    assert respuesta.texto == "Hola, no tengo cifras."
    # Dos solicitudes HTTP: el SDK no reintentó por su cuenta (max_retries=0), así que el reintento es el nuestro.
    assert len(vistas) == 2
    assert respuesta.traza.reintentos == (Reintento("limite", 2.0, "retry-after", 429),)
    assert respuesta.traza.espera_reintentos_segundos == 2.0
    assert esperas.dormidas == [2.0]
    assert respuesta.traza.falla is None
    # Un reintento no es otra llamada al modelo: la traza trae la respuesta que sí llegó.
    assert len(respuesta.traza.llamadas) == 1


@PROVEEDORES
def test_la_espera_del_reintento_es_real_y_dura_lo_que_pidio_el_proveedor(proveedor, base):
    transporte, vistas = servidor((429, proveedor.limite, {"Retry-After": "2"}), (200, proveedor.exito, {}))
    inicio = time.monotonic()
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)
    duracion = time.monotonic() - inicio

    assert respuesta.estado == "ok"
    assert len(vistas) == 2
    # Un margen de 0.1 s por la resolución del reloj de Windows; la preparación de la base suma unas décimas.
    assert 1.9 <= duracion < 10


@PROVEEDORES
def test_un_429_con_retry_after_seguido_de_otro_con_fecha_http_espera_las_dos_veces(proveedor, base, esperas):
    fecha = email.utils.formatdate(time.time() + 10, usegmt=True)
    transporte, vistas = servidor(
        (429, proveedor.limite, {"Retry-After": "2"}),
        (429, proveedor.limite, {"Retry-After": fecha}),
        (200, proveedor.exito, {}),
    )
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)

    assert respuesta.estado == "ok"
    assert len(vistas) == 3
    primero, segundo = respuesta.traza.reintentos
    assert (primero.espera_segundos, primero.origen) == (2.0, "retry-after")
    # Una fecha HTTP trae segundos enteros: de 9 a 10 s desde ahora, menos lo que tardó el turno en llegar aquí.
    assert segundo.origen == "retry-after"
    assert 5.0 <= segundo.espera_segundos <= 10.5
    assert esperas.dormidas == [2.0, segundo.espera_segundos]


@PROVEEDORES
def test_si_el_429_no_cede_se_acaban_los_reintentos_y_la_respuesta_no_da_cifra(proveedor, base, esperas):
    transporte, vistas = servidor((429, proveedor.limite, {}))
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)

    assert respuesta.estado == "modelo_no_disponible"
    assert len(vistas) == 1 + agente.TOPES.reintentos
    # Sin Retry-After, retroceso exponencial: 1, 2 y 4 s.
    assert esperas.dormidas == [1.0, 2.0, 4.0]
    assert [r.origen for r in respuesta.traza.reintentos] == ["retroceso"] * 3
    assert respuesta.traza.falla == FallaDelModelo("limite", "ModelRateLimitError", 429, None)
    assert respuesta.texto == reintentos.MENSAJES_DE_FALLA["limite"]
    assert sin_digitos(respuesta.texto)
    assert not any(dicho in respuesta.texto for dicho in proveedor.dicho_por_el_proveedor)


@PROVEEDORES
def test_un_servicio_saturado_se_reintenta_igual_que_un_429(proveedor, base, esperas):
    # Con max_retries=0 el SDK de Anthropic ya no reintenta los 5xx que reintentaba por omisión: lo hace el modelo.
    estado, cuerpo = proveedor.saturado
    transporte, vistas = servidor((estado, cuerpo, {}), (200, proveedor.exito, {}))
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)

    assert respuesta.estado == "ok"
    assert len(vistas) == 2
    assert respuesta.traza.reintentos == (Reintento("servicio", 1.0, "retroceso", estado),)


@PROVEEDORES
def test_si_el_servicio_sigue_saturado_la_respuesta_no_da_cifra_ni_repite_lo_que_dijo_el_proveedor(
    proveedor, base, esperas
):
    estado, cuerpo = proveedor.saturado
    transporte, vistas = servidor((estado, cuerpo, {}))
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)

    assert respuesta.estado == "modelo_no_disponible"
    assert len(vistas) == 1 + agente.TOPES.reintentos
    assert respuesta.traza.falla.motivo == "servicio"
    assert respuesta.traza.falla.estado_http == estado
    assert respuesta.texto == reintentos.MENSAJES_DE_FALLA["servicio"]
    assert not any(dicho in repr(respuesta) for dicho in proveedor.dicho_por_el_proveedor)


# ------------------------------------------------------------------ criterio de la ficha: la llave inválida

@PROVEEDORES
def test_con_una_llave_invalida_dice_que_no_pudo_consultar_el_modelo_y_no_da_cifra(proveedor, base, esperas):
    estado, cuerpo = proveedor.llave_invalida
    transporte, vistas = servidor((estado, cuerpo, {}))
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)

    assert respuesta.estado == "modelo_no_disponible"
    assert "No pude consultar el modelo" in respuesta.texto
    assert sin_digitos(respuesta.texto)
    assert CIFRA_P03 not in respuesta.texto
    # Una llave inválida no se arregla esperando: una sola solicitud, sin reintentos.
    assert len(vistas) == 1
    assert respuesta.traza.reintentos == ()
    assert esperas.dormidas == []
    assert respuesta.traza.falla == FallaDelModelo("llave", respuesta.traza.falla.tipo, estado, None)
    assert respuesta.evidencias == ()


@PROVEEDORES
def test_lo_que_dice_el_proveedor_de_la_llave_no_llega_ni_a_la_gerente_ni_a_la_traza(proveedor, base, esperas):
    estado, cuerpo = proveedor.llave_invalida
    transporte, _ = servidor((estado, cuerpo, {}))
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)

    visible = [respuesta.texto, repr(respuesta.traza), repr(respuesta.mensajes), repr(respuesta.evidencias)]
    for texto in visible:
        assert LLAVE_DE_PRUEBA not in texto
        assert not any(dicho in texto for dicho in proveedor.dicho_por_el_proveedor)


@PROVEEDORES
def test_la_llave_que_viaja_es_la_de_prueba_y_el_turno_no_sale_a_la_red(proveedor, base, esperas):
    # El transporte simulado es la única salida del cliente: lo que se manda es lo que se ve aquí.
    transporte, vistas = servidor((200, proveedor.exito, {}))
    preguntar(P03, proveedor.modelo(transporte), base)
    assert len(vistas) == 1
    # Anthropic manda la llave en Authorization y Google en x-goog-api-key.
    con_llave = [valor for nombre, valor in vistas[0].headers.items() if nombre in ("authorization", "x-goog-api-key")]
    assert len(con_llave) == 1 and LLAVE_DE_PRUEBA in con_llave[0]


# ------------------------------------------------------------------ los supuestos del plan sobre los SDK (hallazgos 1 y 2)

def test_el_sdk_de_anthropic_reintenta_dos_veces_por_omision_y_por_eso_el_modelo_lo_apaga():
    assert AsyncAnthropic(api_key=LLAVE_DE_PRUEBA).max_retries == 2


def test_el_sdk_de_google_no_trae_retry_options_por_omision(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", LLAVE_DE_PRUEBA)
    modelo = infer_model("google:gemini-3.5-flash-lite")
    assert modelo.client._api_client._http_options.retry_options is None


@PROVEEDORES
def test_el_modelo_manda_max_retries_0_a_cada_proveedor_sin_perder_los_demas_ajustes(proveedor):
    transporte, vistas = servidor((429, proveedor.limite, {"Retry-After": "2"}), (200, proveedor.exito, {}))
    modelo, registro = envolver(proveedor.modelo(transporte), reintentos=0)
    ajustes = {"max_tokens": 4096, "max_retries": 5}
    with pytest.raises(ModelRateLimitError):
        solicitar(modelo, ajustes)
    # Con max_retries=5 el SDK de Anthropic habría reintentado: el modelo lo fuerza a 0.
    assert len(vistas) == 1
    assert registro == []
    assert ajustes == {"max_tokens": 4096, "max_retries": 5}  # el diccionario del que llama no se toca


# ------------------------------------------------------------------ sin llave configurada

@pytest.mark.parametrize("modelo, variables", [
    ("google:gemini-3.5-flash-lite", ("GOOGLE_API_KEY", "GEMINI_API_KEY")),
    ("anthropic:claude-haiku-5-5", ("ANTHROPIC_API_KEY",)),
])
def test_sin_llave_configurada_dice_que_no_pudo_consultar_el_modelo(modelo, variables, base, monkeypatch, esperas):
    for variable in variables:
        monkeypatch.delenv(variable, raising=False)
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "modelo_no_disponible"
    assert respuesta.traza.falla == FallaDelModelo("configuracion", "UserError", None, None)
    assert "No pude consultar el modelo" in respuesta.texto
    assert sin_digitos(respuesta.texto)
    assert "GOOGLE_API_KEY" not in repr(respuesta) and "ANTHROPIC_API_KEY" not in repr(respuesta)
    assert respuesta.evidencias == ()


def test_un_modelo_que_no_existe_tampoco_lanza_una_excepcion(base, esperas):
    respuesta = preguntar(P03, "desconocido:modelo", base)
    assert respuesta.estado == "modelo_no_disponible"
    assert respuesta.traza.falla.motivo == "configuracion"
    assert "desconocido" not in respuesta.texto


# ------------------------------------------------------------------ cuánto esperar (K20)

def test_retry_after_en_segundos_manda_sobre_el_retroceso():
    assert reintentos.espera_para(limite("2"), 1) == (2.0, "retry-after")
    assert reintentos.espera_para(limite("2"), 3) == (2.0, "retry-after")


def test_retry_after_como_fecha_http_son_los_segundos_que_faltan():
    fecha = email.utils.formatdate(time.time() + 10, usegmt=True)
    espera, origen = reintentos.espera_para(limite(fecha), 1)
    assert origen == "retry-after"
    assert 5.0 <= espera <= 10.5


def test_retry_after_con_una_fecha_ya_pasada_es_reintentar_ya():
    fecha = email.utils.formatdate(time.time() - 60, usegmt=True)
    assert reintentos.espera_para(limite(fecha), 1) == (0.0, "retry-after")


def test_retry_after_ms_se_respeta_como_hacen_los_sdk():
    error = error_http(429, ModelRateLimitError, cabeceras={"retry-after-ms": "1500"})
    assert reintentos.espera_para(error, 1) == (1.5, "retry-after")


def test_retry_after_en_cero_es_reintentar_ya():
    assert reintentos.espera_para(limite("0"), 1) == (0.0, "retry-after")


def test_sin_retry_after_el_retroceso_es_exponencial_y_tiene_techo():
    esperas_pedidas = [reintentos.espera_para(limite(), numero) for numero in range(1, 7)]
    assert esperas_pedidas == [
        (1.0, "retroceso"), (2.0, "retroceso"), (4.0, "retroceso"),
        (8.0, "retroceso"), (8.0, "retroceso"), (8.0, "retroceso"),
    ]
    assert reintentos.ESPERA_MAXIMA_SEGUNDOS == 8.0


@pytest.mark.parametrize("cabecera", ["pronto", "-5", "", "2.5s"])
def test_un_retry_after_que_no_se_entiende_cae_al_retroceso(cabecera):
    assert reintentos.espera_para(limite(cabecera), 2) == (2.0, "retroceso")


def test_un_numero_de_reintento_enorme_no_desborda():
    assert reintentos.espera_para(limite(), 5000) == (8.0, "retroceso")


# ------------------------------------------------------------------ qué se reintenta y cómo se clasifica

CATEGORIAS = [
    # (error, motivo por el que se reintenta o None, motivo de la falla si no se reintenta o se acaban los intentos)
    pytest.param(error_http(429, ModelRateLimitError), "limite", "limite", id="429"),
    pytest.param(error_http(503, ModelUnavailableError), "servicio", "servicio", id="503"),
    pytest.param(error_http(529, ModelOverloadedError), "servicio", "servicio", id="529-saturado"),
    pytest.param(error_http(500, ModelServerError), "servicio", "servicio", id="500"),
    pytest.param(error_http(401), None, "llave", id="401"),
    pytest.param(error_http(403), None, "llave", id="403"),
    pytest.param(
        error_http(400, cuerpo={"error": {"details": [{"reason": "API_KEY_INVALID"}]}}), None, "llave",
        id="400-api-key-invalid",
    ),
    pytest.param(
        error_http(400, cuerpo={"error": {"message": "API key expired. Please renew the API key."}}), None, "llave",
        id="400-api-key-expirada",
    ),
    pytest.param(
        error_http(400, cuerpo={"error": {"message": "el contenido no se pudo procesar"}}), None, "modelo",
        id="400-otro",
    ),
    pytest.param(error_http(404), None, "modelo", id="404"),
    pytest.param(error_http(402, ModelQuotaExceededError), None, "modelo", id="402-sin-saldo"),
    pytest.param(ModelConnectionError("simulado", "sin conexión", phase="connect"), None, "modelo", id="conexion"),
    pytest.param(ModelAPIError("simulado", "algo salió mal"), None, "modelo", id="generico"),
]


@pytest.mark.parametrize("error, se_reintenta, motivo", CATEGORIAS)
def test_cada_error_del_proveedor_se_reintenta_o_se_clasifica(error, se_reintenta, motivo):
    assert reintentos.motivo_de_reintento(error) == se_reintenta
    assert reintentos.clasificar(error).motivo == motivo


def test_la_falla_guarda_el_estado_http_y_lo_que_pidio_el_proveedor_pero_no_su_texto():
    error = error_http(
        429, ModelRateLimitError, cuerpo={"error": {"message": "SECRETO del proveedor"}}, cabeceras={"retry-after": "30"}
    )
    falla = reintentos.clasificar(error)
    assert falla == FallaDelModelo("limite", "ModelRateLimitError", 429, 30.0)
    assert "SECRETO" not in repr(falla)


def test_un_error_sin_estado_http_no_inventa_uno():
    falla = reintentos.clasificar(ModelConnectionError("simulado", "sin conexión", phase="connect"))
    assert falla == FallaDelModelo("modelo", "ModelConnectionError", None, None)


# ------------------------------------------------------------------ el modelo con reintentos, sin proveedor

def test_un_429_con_retry_after_2_deja_un_reintento_de_2_s(esperas):
    interno, vistas = modelo_con_fallas(limite("2"))
    modelo, registro = envolver(interno)
    respuesta = solicitar(modelo)

    assert respuesta.parts[0].content == "Listo."
    assert registro == [Reintento("limite", 2.0, "retry-after", 429)]
    assert esperas.dormidas == [2.0]
    assert vistas.llamadas == 2


def test_retry_after_en_segundos_y_despues_como_fecha_http(esperas):
    fecha = email.utils.formatdate(time.time() + 10, usegmt=True)
    interno, _ = modelo_con_fallas(limite("2"), limite(fecha))
    modelo, registro = envolver(interno)
    solicitar(modelo)

    assert [r.origen for r in registro] == ["retry-after", "retry-after"]
    assert registro[0].espera_segundos == 2.0
    assert 5.0 <= registro[1].espera_segundos <= 10.5
    assert esperas.dormidas == [r.espera_segundos for r in registro]


def test_sin_retry_after_espera_1_2_y_4_s_y_despues_deja_pasar_el_error(esperas):
    interno, vistas = modelo_con_fallas(limite(), limite(), limite(), limite(), limite())
    modelo, registro = envolver(interno, reintentos=3)
    with pytest.raises(ModelRateLimitError):
        solicitar(modelo)

    assert esperas.dormidas == [1.0, 2.0, 4.0]
    assert [r.origen for r in registro] == ["retroceso"] * 3
    assert vistas.llamadas == 4  # el intento inicial y 3 reintentos


def test_el_tope_de_reintentos_se_puede_bajar_a_cero(esperas):
    interno, vistas = modelo_con_fallas(limite("2"))
    modelo, registro = envolver(interno, reintentos=0)
    with pytest.raises(ModelRateLimitError):
        solicitar(modelo)
    assert vistas.llamadas == 1
    assert registro == []
    assert esperas.dormidas == []


def test_un_error_que_esperar_no_arregla_no_se_reintenta(esperas):
    interno, vistas = modelo_con_fallas(llave_invalida())
    modelo, registro = envolver(interno)
    with pytest.raises(ModelHTTPError):
        solicitar(modelo)
    assert vistas.llamadas == 1
    assert registro == [] and esperas.dormidas == []


@pytest.mark.parametrize("error", [error_http(503, ModelUnavailableError), error_http(529, ModelOverloadedError),
                                   error_http(500, ModelServerError)], ids=["503", "529", "500"])
def test_un_servicio_saturado_o_caido_se_reintenta_igual_que_un_429(error, esperas):
    # Con max_retries=0 el SDK de Anthropic ya no reintenta los 5xx que reintentaba por omisión: el modelo los toma.
    interno, vistas = modelo_con_fallas(error)
    modelo, registro = envolver(interno)
    solicitar(modelo)
    assert [r.motivo for r in registro] == ["servicio"]
    assert registro[0].estado_http == error.status_code
    assert vistas.llamadas == 2


def test_un_error_que_no_es_del_proveedor_sigue_su_camino_sin_reintentos(esperas):
    interno, vistas = modelo_con_fallas(KeyError("defecto"))
    modelo, registro = envolver(interno)
    with pytest.raises(KeyError):
        solicitar(modelo)
    assert vistas.llamadas == 1 and registro == []


def test_no_espera_lo_que_no_cabe_en_el_tiempo_que_queda_del_turno(esperas):
    interno, vistas = modelo_con_fallas(limite("30"))
    modelo, registro = envolver(interno, restante=lambda: 20.0)
    with pytest.raises(ModelRateLimitError):
        solicitar(modelo)
    assert vistas.llamadas == 1
    assert registro == [] and esperas.dormidas == []


def test_la_espera_tiene_que_dejar_margen_para_la_solicitud_que_sigue(esperas):
    assert reintentos.MARGEN_SEGUNDOS == 5.0
    # 2 s de espera + 5 s de margen son 7: con 7.5 s alcanza y con 6.9 no.
    interno, vistas = modelo_con_fallas(limite("2"))
    modelo, registro = envolver(interno, restante=lambda: 7.5)
    solicitar(modelo)
    assert esperas.dormidas == [2.0]

    interno, vistas = modelo_con_fallas(limite("2"))
    modelo, registro = envolver(interno, restante=lambda: 6.9)
    with pytest.raises(ModelRateLimitError):
        solicitar(modelo)
    assert esperas.dormidas == [2.0]  # la segunda prueba no esperó


def test_el_tiempo_que_queda_se_consulta_en_cada_reintento(esperas):
    # El turno gasta tiempo entre un reintento y otro: la segunda espera ya no cabe.
    restantes = iter([60.0, 6.0])
    interno, vistas = modelo_con_fallas(limite("2"), limite("2"), limite("2"))
    modelo, registro = envolver(interno, restante=lambda: next(restantes))
    with pytest.raises(ModelRateLimitError):
        solicitar(modelo)
    assert esperas.dormidas == [2.0]
    assert vistas.llamadas == 2


def test_el_modelo_fuerza_max_retries_0_y_conserva_los_ajustes_del_turno(esperas):
    interno, vistas = modelo_con_fallas(limite("1"))
    modelo, _ = envolver(interno)
    ajustes = {"max_tokens": 4096, "max_retries": 5}
    solicitar(modelo, ajustes)

    assert [a["max_retries"] for a in vistas.ajustes] == [0, 0]
    assert [a["max_tokens"] for a in vistas.ajustes] == [4096, 4096]
    assert ajustes == {"max_tokens": 4096, "max_retries": 5}


def test_el_modelo_con_reintentos_se_ve_igual_que_el_modelo_que_envuelve():
    interno, _ = modelo_con_fallas()
    modelo, _ = envolver(interno)
    assert modelo.model_name == interno.model_name == "falla"
    assert modelo.system == interno.system


def test_un_id_de_modelo_desconocido_lanza_user_error_al_envolver():
    with pytest.raises(UserError):
        envolver("desconocido:modelo")


# ------------------------------------------------------------------ el turno completo (responder)

def test_un_429_a_mitad_del_turno_no_repite_la_herramienta_ni_gasta_solicitudes(base, esperas):
    # La segunda solicitud (la redacción) recibe un 429; el reintento repite solo esa solicitud.
    modelo, registro = modelo_p03(None, limite("1"))
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "ok"
    assert respuesta.texto.startswith(CIFRA_P03)
    assert registro.llamadas == 3
    assert [e.herramienta for e in respuesta.evidencias] == ["ventas"]
    assert respuesta.traza.reintentos == (Reintento("limite", 1.0, "retry-after", 429),)
    assert len(respuesta.traza.llamadas) == 2


def test_los_reintentos_no_cuentan_como_solicitudes_del_tope(base, esperas):
    modelo, registro = modelo_con_fallas(limite("1"), limite("1"), limite("1"))
    respuesta = preguntar(P03, modelo, base, topes=agente.Topes(solicitudes=1))
    assert respuesta.estado == "ok"
    assert registro.llamadas == 4
    assert respuesta.traza.tope is None


def test_el_maximo_de_reintentos_sale_del_presupuesto_del_turno(base, esperas):
    modelo, registro = modelo_con_fallas(*[limite("1")] * 10)
    respuesta = preguntar(P03, modelo, base, topes=agente.Topes(reintentos=1))
    assert registro.llamadas == 2
    assert respuesta.estado == "modelo_no_disponible"
    assert len(respuesta.traza.reintentos) == 1


def test_el_tope_por_omision_son_3_reintentos_por_solicitud():
    assert agente.TOPES.reintentos == 3


def test_no_espera_si_el_retry_after_no_cabe_en_los_segundos_del_turno(base, esperas):
    modelo, registro = modelo_con_fallas(limite("120"))
    respuesta = preguntar(P03, modelo, base, topes=agente.Topes(segundos=30))

    assert respuesta.estado == "modelo_no_disponible"
    assert registro.llamadas == 1
    assert esperas.dormidas == []
    assert respuesta.traza.reintentos == ()
    assert respuesta.traza.falla == FallaDelModelo("limite", "ModelRateLimitError", 429, 120.0)
    assert respuesta.traza.tope is None  # no fue un corte del presupuesto: el proveedor pidió esperar demasiado


def test_si_tras_el_reintento_el_modelo_se_tarda_el_turno_se_corta_por_tiempo_y_conserva_la_espera(
    base, esperas, monkeypatch
):
    # Los 60 s del turno cuentan reintentos y solicitudes por igual. Con un margen de 0.5 s la espera cabe.
    monkeypatch.setattr(reintentos, "MARGEN_SEGUNDOS", 0.5)
    llamadas = []

    async def lento(mensajes, info: AgentInfo):
        llamadas.append(1)
        if len(llamadas) == 1:
            raise error_http(429, ModelRateLimitError, cabeceras={"retry-after-ms": "200"})
        await asyncio.sleep(30)
        return ModelResponse(parts=[TextPart(f"{CIFRA_P03} pesos, pero tarde")])

    inicio = time.monotonic()
    respuesta = preguntar(P03, FunctionModel(lento), base, topes=agente.Topes(segundos=2.5))
    duracion = time.monotonic() - inicio

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "segundos"
    assert duracion < 10  # el modelo dormía 30 s
    assert respuesta.traza.reintentos == (Reintento("limite", 0.2, "retry-after", 429),)
    assert esperas.dormidas == [0.2]
    assert CIFRA_P03 not in respuesta.texto


def test_el_reintento_descuenta_del_reloj_del_turno(base):
    # Al turno le quedan 4 s: ni 1 s de espera más el margen caben.
    presupuesto = agente.Presupuesto(agente.Topes(segundos=60), inicio=time.monotonic() - 56)
    consultas = agente.abrir_consultas(base)
    try:
        turno = agente.Turno(consultas, agente.definiciones.supuestos(), HOY)
        modelo, registro = modelo_con_fallas(limite("1"))
        corrida = agente._correr(P03, (), modelo, turno, presupuesto)
    finally:
        consultas.cerrar()
    assert corrida.falla.motivo == "limite"
    assert registro.llamadas == 1
    assert presupuesto.reintentos == []


def test_los_reintentos_de_un_turno_se_acumulan_en_su_presupuesto(base, esperas):
    presupuesto = agente.Presupuesto(agente.TOPES)
    consultas = agente.abrir_consultas(base)
    try:
        turno = agente.Turno(consultas, agente.definiciones.supuestos(), HOY)
        agente._correr(P03, (), modelo_con_fallas(limite("1"))[0], turno, presupuesto)
        agente._correr("otra vez", (), modelo_con_fallas(limite("2"))[0], turno, presupuesto)
    finally:
        consultas.cerrar()
    assert [r.espera_segundos for r in presupuesto.reintentos] == [1.0, 2.0]


def test_un_id_de_modelo_como_texto_tambien_pasa_por_el_modelo_con_reintentos(base, esperas):
    # "test" es el id que Pydantic AI resuelve a su TestModel: el camino de MODELO en .env, sin proveedor.
    respuesta = preguntar(P03, "test", base)
    assert respuesta.estado == "ok"
    assert respuesta.traza.modelo == "test"
    assert respuesta.traza.reintentos == () and respuesta.traza.falla is None


def test_un_turno_sin_fallas_no_deja_reintentos_ni_falla(base, esperas):
    respuesta = preguntar(P03, modelo_p03()[0], base)
    assert respuesta.estado == "ok"
    assert respuesta.traza.reintentos == ()
    assert respuesta.traza.falla is None
    assert respuesta.traza.espera_reintentos_segundos == 0.0


# ------------------------------------------------------------------ la falla después de que corrió una herramienta

def test_si_el_modelo_falla_despues_de_una_herramienta_la_cifra_no_sale_en_el_texto(base, esperas):
    modelo, registro = modelo_p03(None, llave_invalida())
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "modelo_no_disponible"
    assert CIFRA_P03 not in respuesta.texto
    assert sin_digitos(respuesta.texto)
    # Lo que alcanzó a correr queda para el panel de traza y el registro, como en un tope.
    assert [e.herramienta for e in respuesta.evidencias] == ["ventas"]
    assert respuesta.evidencias[0].resultado.cifras["venta"] == CIFRA_P03
    assert len(respuesta.traza.llamadas) == 1
    assert "SECRETO" not in repr(respuesta.traza)


def test_el_historial_de_un_turno_con_falla_es_valido_para_el_siguiente(base, esperas):
    modelo, _ = modelo_p03(None, llave_invalida())
    fallido = preguntar(P03, modelo, base)

    # Solo la pregunta y el mensaje de la falla: ninguna llamada a herramienta sin su resultado.
    pregunta, mensaje = fallido.mensajes
    assert isinstance(pregunta, ModelRequest) and pregunta.parts[0].content == P03
    assert isinstance(mensaje, ModelResponse) and mensaje.parts[0].content == fallido.texto

    vistos = {}

    def siguiente(mensajes, info: AgentInfo):
        vistos["mensajes"] = list(mensajes)
        return ModelResponse(parts=[TextPart("Dime la pregunta por partes.")])

    seguimiento = agente.responder(
        "mejor solo junio", fallido.mensajes, modelo=FunctionModel(siguiente), base=base, hoy_real=HOY
    )
    assert seguimiento.estado == "ok"
    assert vistos["mensajes"][: len(fallido.mensajes)] == list(fallido.mensajes)


def test_una_falla_del_modelo_no_deja_la_conexion_abierta(base, esperas):
    modelo, _ = modelo_con_fallas(llave_invalida())
    assert preguntar(P03, modelo, base).estado == "modelo_no_disponible"
    with duckdb.connect(str(base)) as escritura:
        assert escritura.execute("SELECT 1").fetchall() == [(1,)]


def test_un_error_del_proveedor_que_nadie_previó_sale_como_mensaje_y_sin_su_texto(base, esperas):
    modelo, registro = modelo_con_fallas(error_http(418, cuerpo={"error": {"message": "SECRETO del proveedor"}}))
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "modelo_no_disponible"
    assert respuesta.traza.falla == FallaDelModelo("modelo", respuesta.traza.falla.tipo, 418, None)
    assert respuesta.texto == reintentos.MENSAJES_DE_FALLA["modelo"]
    assert registro.llamadas == 1
    assert "SECRETO" not in repr(respuesta)


# ------------------------------------------------------------------ los mensajes de la gerente

MOTIVOS_DE_FALLA = ("limite", "servicio", "llave", "configuracion", "modelo")


def test_hay_un_mensaje_por_cada_motivo_de_falla():
    from typing import get_args

    assert set(get_args(reintentos.MotivoFalla)) == set(MOTIVOS_DE_FALLA)
    assert set(reintentos.MENSAJES_DE_FALLA) == set(MOTIVOS_DE_FALLA)


@pytest.mark.parametrize("motivo", MOTIVOS_DE_FALLA)
def test_cada_falla_tiene_un_mensaje_en_espanol_sin_cifras_ni_jerga(motivo):
    texto = reintentos.MENSAJES_DE_FALLA[motivo]
    assert texto.startswith("No pude consultar el modelo")
    assert "No te doy ninguna cifra." in texto
    assert sin_digitos(texto)
    assert texto.endswith(".")
    for jerga in ("Traceback", "Exception", "HTTP", "API", "token", "timeout", "llave", "clave", ".env"):
        assert jerga.casefold() not in texto.casefold(), jerga


def test_el_mensaje_dice_que_hacer_segun_la_causa():
    mensajes = reintentos.MENSAJES_DE_FALLA
    # Una saturación pasa sola: se vuelve a preguntar. Un acceso mal configurado lo arregla quien administra.
    assert "vuelve a preguntar" in mensajes["limite"]
    assert "Vuelve a intentarlo" in mensajes["servicio"]
    assert "quien administra el sistema" in mensajes["llave"]
    assert mensajes["llave"] == mensajes["configuracion"]


# ------------------------------------------------------------------ la base: FuentesCambiaron y BaseEnUso (K21)

def asegurar_con_fallas(monkeypatch, *errores):
    """Cambia construir.asegurar_base por una que lanza cada error en orden y después hace lo de siempre."""
    real = construir.asegurar_base
    pendientes = list(errores)
    llamadas = []

    def asegurar(*argumentos, **opciones):
        llamadas.append(1)
        if pendientes:
            raise pendientes.pop(0)
        return real(*argumentos, **opciones)

    monkeypatch.setattr(construir, "asegurar_base", asegurar)
    return llamadas


def modelo_que_no_se_llama():
    def no_debe_llamarse(mensajes, info):
        raise AssertionError("no debe llamar al modelo")

    return FunctionModel(no_debe_llamarse)


def test_unas_fuentes_que_cambiaron_a_media_construccion_se_reintentan_una_vez(base, monkeypatch, esperas):
    llamadas = asegurar_con_fallas(monkeypatch, construir.FuentesCambiaron(("datos/ventas.csv",)))
    modelo, registro = modelo_p03()
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "ok"
    assert respuesta.texto.startswith(CIFRA_P03)
    assert len(llamadas) == 2
    assert respuesta.traza.reintentos == (Reintento("fuentes", reintentos.PAUSA_DE_BASE_SEGUNDOS, "fija"),)
    assert esperas.pausas == [reintentos.PAUSA_DE_BASE_SEGUNDOS]


def test_una_base_en_uso_se_reintenta_una_vez(base, monkeypatch, esperas):
    llamadas = asegurar_con_fallas(monkeypatch, construir.BaseEnUso(Path(RUTA_CON_USUARIO) / "build" / "tienda.duckdb"))
    respuesta = preguntar(P03, modelo_p03()[0], base)

    assert respuesta.estado == "ok"
    assert len(llamadas) == 2
    assert [r.motivo for r in respuesta.traza.reintentos] == ["base_en_uso"]


def test_si_las_fuentes_siguen_cambiando_responde_sin_cifra_tras_un_solo_reintento(base, monkeypatch, esperas):
    llamadas = asegurar_con_fallas(
        monkeypatch,
        construir.FuentesCambiaron((f"{RUTA_CON_USUARIO}\\datos\\ventas.csv",)),
        construir.FuentesCambiaron((f"{RUTA_CON_USUARIO}\\datos\\ventas.csv",)),
    )
    respuesta = preguntar(P03, modelo_que_no_se_llama(), base)

    assert respuesta.estado == "fuente_no_disponible"
    assert len(llamadas) == 2  # el intento y un reintento, no más
    assert respuesta.texto == (
        "No pude consultar ventas.csv: cambió mientras se preparaba la base; vuelve a intentarlo en un momento"
    )
    assert "usuario_ficticio" not in repr(respuesta) and "Users" not in repr(respuesta)
    assert sin_digitos(respuesta.texto)
    assert respuesta.evidencias == () and respuesta.traza.llamadas == ()
    assert len(respuesta.traza.reintentos) == 1


def test_si_la_base_sigue_en_uso_responde_sin_cifra_y_sin_la_ruta(base, monkeypatch, esperas):
    en_uso = construir.BaseEnUso(Path(RUTA_CON_USUARIO) / "build" / "tienda.duckdb")
    asegurar_con_fallas(monkeypatch, en_uso, en_uso)
    respuesta = preguntar(P03, modelo_que_no_se_llama(), base)

    assert respuesta.estado == "fuente_no_disponible"
    assert respuesta.texto == "No pude consultar los datos: otro programa tiene abierta la base; vuelve a intentarlo"
    assert "usuario_ficticio" not in repr(respuesta)


def test_un_archivo_que_falta_no_se_reintenta_y_nombra_solo_el_archivo(base, monkeypatch, esperas):
    falta = construir.ArchivoFaltante(("ventas.csv",), Path(RUTA_CON_USUARIO) / "datos")
    assert "usuario_ficticio" in str(falta)  # str(error) trae la ruta: la respuesta no puede salir de ahí
    llamadas = asegurar_con_fallas(monkeypatch, falta)
    respuesta = preguntar(P03, modelo_que_no_se_llama(), base)

    assert respuesta.estado == "fuente_no_disponible"
    assert respuesta.texto == "No pude consultar ventas.csv: el archivo no está con ese nombre"
    assert len(llamadas) == 1 and respuesta.traza.reintentos == () and esperas.pausas == []
    assert "usuario_ficticio" not in repr(respuesta)
    assert sin_digitos(respuesta.texto)


@pytest.mark.parametrize("original", [
    construir.ExtensionNoDisponible("fts", "LOAD", f"no se encontró en {RUTA_CON_USUARIO}"),
    construir.CsvInvalido("ventas.csv", f"la fila 3 trae un monto inválido en {RUTA_CON_USUARIO}"),
    construir.ConfiguracionInvalida(f"config/supuestos.yaml no se pudo validar: {RUTA_CON_USUARIO}"),
    construir.ErrorConstruccion(f"falló en {RUTA_CON_USUARIO}"),
], ids=lambda e: type(e).__name__)
def test_todo_error_de_construccion_sale_como_mensaje_en_espanol_sin_la_ruta(original, base, monkeypatch, esperas):
    asegurar_con_fallas(monkeypatch, original)
    respuesta = preguntar(P03, modelo_que_no_se_llama(), base)

    assert respuesta.estado == "fuente_no_disponible"
    assert respuesta.texto.startswith("No pude consultar")
    for oculto in ("usuario_ficticio", "Users", "Traceback", "LOAD", "fila", "monto"):
        assert oculto not in respuesta.texto
    assert sin_digitos(respuesta.texto)


def test_una_base_que_no_abre_dice_que_no_pudo_consultar_sin_cifra(base, monkeypatch, esperas):
    def no_abre(ruta):
        raise BaseNoDisponible(Path(RUTA_CON_USUARIO) / "build" / "tienda.duckdb", "el archivo no existe")

    monkeypatch.setattr(agente, "abrir_consultas", no_abre)
    respuesta = preguntar(P03, modelo_que_no_se_llama(), base)

    assert respuesta.estado == "fuente_no_disponible"
    assert respuesta.texto == "No pude consultar los datos: la base no existe o no se pudo abrir"
    assert "usuario_ficticio" not in repr(respuesta)


def test_sin_config_supuestos_yaml_dice_que_no_pudo_consultar_los_datos_en_vez_de_lanzar(fts, copia_config, tmp_path):
    (copia_config / "supuestos.yaml").unlink()
    respuesta = preguntar(P03, modelo_que_no_se_llama(), tmp_path / "tienda.duckdb", dir_config=copia_config)

    assert respuesta.estado == "fuente_no_disponible"
    assert respuesta.texto == "No pude consultar los datos: la base no se pudo preparar"
    assert str(tmp_path) not in repr(respuesta)


# ------------------------------------------------------------------ con_reintento_de_base, aislado

def test_con_reintento_de_base_no_hace_nada_si_la_primera_vez_sale_bien(esperas):
    registro = []
    assert reintentos.con_reintento_de_base(lambda: "listo", registro, lambda: 60.0) == "listo"
    assert registro == [] and esperas.pausas == []


def test_con_reintento_de_base_no_reintenta_si_no_queda_tiempo(esperas):
    llamadas = []

    def preparar():
        llamadas.append(1)
        raise construir.FuentesCambiaron(("datos/ventas.csv",))

    registro = []
    with pytest.raises(construir.FuentesCambiaron):
        reintentos.con_reintento_de_base(preparar, registro, lambda: 3.0)
    assert len(llamadas) == 1 and registro == [] and esperas.pausas == []


def test_el_presupuesto_lleva_la_lista_de_reintentos_de_su_turno():
    uno, otro = agente.Presupuesto(agente.TOPES), agente.Presupuesto(agente.TOPES)
    uno.reintentos.append(Reintento("limite", 1.0, "retroceso", 429))
    assert otro.reintentos == []


# ------------------------------------------------------------------ el tiempo agotado de una solicitud

def tiempo_agotado():
    return ModelTimeoutError("simulado", "se agotó el tiempo", phase="connect")


def test_un_tiempo_agotado_se_reintenta_con_el_retroceso(esperas):
    # Pydantic AI le da 5 s a la conexión: un corte así no debe costar el turno cuando le quedan unos 55 s.
    interno, vistas = modelo_con_fallas(tiempo_agotado())
    modelo, registro = envolver(interno)
    respuesta = solicitar(modelo)

    assert respuesta.parts == [TextPart("Listo.")]
    assert vistas.llamadas == 2
    assert registro == [Reintento("servicio", 1.0, "retroceso", None)]
    assert esperas.dormidas == [1.0]


def test_si_el_tiempo_se_sigue_agotando_se_acaban_los_reintentos(esperas):
    interno, vistas = modelo_con_fallas(*[tiempo_agotado() for _ in range(9)])
    modelo, registro = envolver(interno)

    with pytest.raises(ModelTimeoutError):
        solicitar(modelo)

    assert vistas.llamadas == 4
    assert esperas.dormidas == [1.0, 2.0, 4.0]
    assert len(registro) == 3


def test_un_tiempo_agotado_no_se_reintenta_si_la_espera_no_cabe_en_el_turno(esperas):
    interno, vistas = modelo_con_fallas(tiempo_agotado())
    modelo, registro = envolver(interno, restante=lambda: 5.5)

    with pytest.raises(ModelTimeoutError):
        solicitar(modelo)

    assert vistas.llamadas == 1
    assert registro == []
    assert esperas.dormidas == []


def test_una_desconexion_que_no_es_tiempo_agotado_sigue_sin_reintentarse(esperas):
    interno, vistas = modelo_con_fallas(ModelConnectionError("simulado", "sin conexión", phase="connect"))
    modelo, registro = envolver(interno)

    with pytest.raises(ModelConnectionError):
        solicitar(modelo)

    assert vistas.llamadas == 1
    assert registro == []


def test_el_tiempo_agotado_se_clasifica_como_servicio_sin_estado_http():
    assert reintentos.motivo_de_reintento(tiempo_agotado()) == "servicio"
    assert reintentos.clasificar(tiempo_agotado()) == FallaDelModelo("servicio", "ModelTimeoutError", None, None)


def transporte_que_no_conecta(veces, exito=None):
    """Un transporte que agota el tiempo de conexión las primeras `veces` y después responde `exito`."""
    vistas = []

    def manejar(solicitud: httpx2.Request) -> httpx2.Response:
        vistas.append(solicitud)
        if len(vistas) <= veces:
            raise httpx2.ConnectTimeout("simulado", request=solicitud)
        return httpx2.Response(200, json=exito)

    return httpx2.MockTransport(manejar), vistas


@PROVEEDORES
def test_un_corte_al_conectar_se_reintenta_y_el_turno_contesta(proveedor, base, esperas):
    transporte, vistas = transporte_que_no_conecta(1, proveedor.exito)
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)

    assert respuesta.estado == "ok"
    assert len(vistas) == 2
    assert respuesta.traza.reintentos == (Reintento("servicio", 1.0, "retroceso", None),)
    assert respuesta.traza.falla is None


@PROVEEDORES
def test_si_no_conecta_nunca_la_respuesta_lo_dice_sin_cifra(proveedor, base, esperas):
    transporte, vistas = transporte_que_no_conecta(99)
    respuesta = preguntar(P03, proveedor.modelo(transporte), base)

    assert respuesta.estado == "modelo_no_disponible"
    assert len(vistas) == 1 + agente.TOPES.reintentos
    assert esperas.dormidas == [1.0, 2.0, 4.0]
    assert respuesta.traza.falla == FallaDelModelo("servicio", "ModelTimeoutError", None, None)
    assert respuesta.texto == reintentos.MENSAJES_DE_FALLA["servicio"]
    assert sin_digitos(respuesta.texto)


# ------------------------------------------------------------------ la espera que Google pide en el cuerpo

def cuota_de_google(retraso):
    """Un 429 del plan gratuito de Gemini: no trae Retry-After, y la espera va en los detalles, como RetryInfo."""
    return {
        "error": {
            "code": 429,
            "message": "mensaje-del-proveedor",
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": []},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retraso},
            ],
        }
    }


def test_la_cuota_por_minuto_de_google_espera_lo_que_pide_su_cuerpo(base, esperas):
    # Son 15 solicitudes por minuto: con 1, 2 y 4 s se perdían 11 de 191 turnos del set, con el turno a medias.
    transporte, vistas = servidor((429, cuota_de_google("21s"), {}), (200, GOOGLE.exito, {}))
    respuesta = preguntar(P03, modelo_google(transporte), base)

    assert respuesta.estado == "ok"
    assert len(vistas) == 2
    assert respuesta.traza.reintentos == (Reintento("limite", 21.0, "retry-after", 429),)
    assert esperas.dormidas == [21.0]


def test_la_cuota_del_dia_de_google_no_se_espera_porque_no_cabe_en_el_turno(base, esperas):
    transporte, vistas = servidor((429, cuota_de_google("43005s"), {}))
    respuesta = preguntar(P03, modelo_google(transporte), base)

    assert respuesta.estado == "modelo_no_disponible"
    assert len(vistas) == 1
    assert respuesta.traza.reintentos == ()
    assert esperas.dormidas == []
    assert respuesta.traza.falla.motivo == "limite"
    assert sin_digitos(respuesta.texto)


def test_el_retraso_de_google_acepta_decimales():
    error = error_http(429, ModelRateLimitError, cuerpo=cuota_de_google("21.5s"))
    assert reintentos.espera_para(error, 1) == (21.5, "retry-after")


def test_retry_after_manda_sobre_el_cuerpo():
    error = error_http(429, ModelRateLimitError, cuerpo=cuota_de_google("21s"), cabeceras={"retry-after": "2"})
    assert reintentos.espera_para(error, 1) == (2.0, "retry-after")


@pytest.mark.parametrize(
    "cuerpo",
    [
        None,
        "texto",
        {},
        {"error": "texto"},
        {"error": {"details": "texto"}},
        {"error": {"details": ["texto"]}},
        cuota_de_google("pronto"),
        cuota_de_google("-5s"),
        cuota_de_google(""),
        cuota_de_google(7),
        cuota_de_google("1e3s"),
    ],
)
def test_un_cuerpo_sin_una_espera_que_se_entienda_sigue_con_el_retroceso(cuerpo):
    error = error_http(429, ModelRateLimitError, cuerpo=cuerpo)
    assert reintentos.espera_para(error, 1) == (1.0, "retroceso")
