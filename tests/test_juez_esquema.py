"""T5.13, D32-A: el juez escribe el motivo antes que el veredicto, y su hash cubre el esquema. Sin red y sin llaves."""

import asyncio
import hashlib
import json

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import create_model
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.usage import RequestUsage

from eval import juez


class SoloCambiaElOrden(juez.Veredicto):
    """El esquema de hoy con las propiedades al revés y lo demás igual, required incluido: lo único distinto es el orden."""

    @classmethod
    def model_json_schema(cls, *_, **__):
        esquema = juez.Veredicto.model_json_schema()
        return {**esquema, "properties": dict(reversed(esquema["properties"].items()))}


def _contestar_lo_que_dice_la_etiqueta(mensajes, info):
    texto = "\n".join(str(parte.content) for mensaje in mensajes for parte in mensaje.parts if parte.part_kind == "user-prompt")
    for etiqueta in juez.leer_etiquetas():
        if f"<respuesta>\n{juez._dato(etiqueta.respuesta)}\n</respuesta>" in texto:
            parte = ToolCallPart("final_result", {"motivo": "de ensayo", "aprueba": etiqueta.etiqueta == "aprueba"})
            return ModelResponse(parts=[parte], usage=RequestUsage(input_tokens=10, output_tokens=5))
    raise AssertionError("una respuesta que no es de las etiquetas")


def test_el_motivo_va_antes_del_veredicto():
    """Las propiedades conservan el orden del esquema: el booleano escrito primero ya no lo cambia el motivo."""
    assert list(juez.Veredicto.model_fields) == ["motivo", "aprueba"]
    esquema = juez.Veredicto.model_json_schema()
    assert list(esquema["properties"]) == ["motivo", "aprueba"]
    assert esquema["required"] == ["motivo", "aprueba"]


def test_el_orden_llega_asi_a_la_solicitud_de_haiku(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    cuerpos = []

    def contestar(solicitud):
        cuerpo = json.loads(solicitud.content)
        cuerpos.append(cuerpo)
        return httpx2.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": cuerpo["model"],
                "content": [{"type": "text", "text": json.dumps({"motivo": "falta el conflicto", "aprueba": False})}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 70, "output_tokens": 6},
            },
        )

    cliente = AsyncAnthropic(api_key="llave-de-prueba", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(contestar)))
    modelo = AnthropicModel("claude-haiku-5-5", provider=AnthropicProvider(anthropic_client=cliente))
    caso = juez.CasoDeJuicio(pregunta="p de ensayo", criterio="c de ensayo", referencia="r de ensayo", respuesta="de ensayo")
    juicio = asyncio.run(juez.juzgar(caso, modelo))
    assert juicio.aprueba is False
    assert list(cuerpos[0]["output_config"]["format"]["schema"]["properties"]) == ["motivo", "aprueba"]


def test_el_hash_cubre_el_prompt_y_el_esquema():
    assert juez.PROMPT_JUEZ_SHA256 == juez.sha256_del_juez(juez.PROMPT_JUEZ, juez.Veredicto)
    solo_el_prompt = hashlib.sha256(juez.PROMPT_JUEZ.encode("utf-8")).hexdigest()
    assert juez.PROMPT_JUEZ_SHA256 != solo_el_prompt


def test_cambiar_solo_el_orden_de_las_propiedades_cambia_el_hash():
    """Con sort_keys en el hash esta es la que truena: el título, las descripciones y required son los mismos."""
    de_hoy = juez.Veredicto.model_json_schema()
    al_reves = SoloCambiaElOrden.model_json_schema()
    assert al_reves == de_hoy
    assert list(al_reves["properties"]) != list(de_hoy["properties"])
    assert juez.sha256_del_juez(juez.PROMPT_JUEZ, SoloCambiaElOrden) != juez.PROMPT_JUEZ_SHA256


def test_el_veredicto_de_antes_de_t5_13_da_otro_hash():
    """El esquema que tenían los reportes viejos: mismo nombre, mismas descripciones y el booleano primero."""
    campos = juez.Veredicto.model_fields
    de_antes = create_model("Veredicto", aprueba=(bool, campos["aprueba"]), motivo=(str, campos["motivo"]))
    assert list(de_antes.model_json_schema()["properties"]) == ["aprueba", "motivo"]
    assert de_antes.model_json_schema()["properties"] == juez.Veredicto.model_json_schema()["properties"]
    assert juez.sha256_del_juez(juez.PROMPT_JUEZ, de_antes) != juez.PROMPT_JUEZ_SHA256


def test_cambiar_el_prompt_cambia_el_hash():
    assert juez.sha256_del_juez(juez.PROMPT_JUEZ + " ", juez.Veredicto) != juez.PROMPT_JUEZ_SHA256


@pytest.fixture
def carpeta_validada(tmp_path):
    """Una validación hecha con el juez de hoy en una carpeta de la prueba."""
    carpeta = tmp_path / "juez"
    modelo = FunctionModel(_contestar_lo_que_dice_la_etiqueta, model_name="guion")
    assert juez.main([], modelo=modelo, dir_reportes=carpeta) == juez.SALIDA_BIEN
    return carpeta


def test_una_validacion_hecha_solo_con_el_hash_del_prompt_ya_no_valida(carpeta_validada):
    """Lo que tienen los reportes de antes de T5.13: su prompt_sha256 no cubría el esquema."""
    ruta = next(carpeta_validada.glob("validacion-*.json"))
    assert juez.estado_de_validacion("function:guion", carpeta_validada).validado
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["prompt_sha256"] = hashlib.sha256(juez.PROMPT_JUEZ.encode("utf-8")).hexdigest()
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    estado = juez.estado_de_validacion("function:guion", carpeta_validada)
    assert not estado.validado
    assert "cambió" in estado.motivo
