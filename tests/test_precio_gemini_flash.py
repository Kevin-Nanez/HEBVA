"""Gemini 3.8 Flash entra en la comparación de D7-A: sin precio, su costo sale en null y la regla de T6.3 no lo puede comparar."""

from decimal import Decimal

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from agente import agente, registro


def test_gemini_3_8_flash_trae_el_precio_de_la_pagina_oficial():
    precio = registro.precios().de("gemini-3.8-flash")
    assert (precio.entrada, precio.salida, precio.cache_lectura) == (Decimal("0.75"), Decimal("3.75"), Decimal("0.075"))
    assert precio.fuente.startswith("https://")


@pytest.mark.parametrize(
    "uso",
    [
        RequestUsage(input_tokens=7732, output_tokens=1000),
        RequestUsage(input_tokens=7732, output_tokens=1000, cache_read_tokens=4000),
    ],
)
def test_el_costo_de_gemini_3_8_flash_coincide_con_el_de_pydantic_ai(uso):
    respuesta = ModelResponse(parts=[TextPart("x")], usage=uso, model_name="gemini-3.8-flash", provider_name="google-gla")
    llamada = agente.Llamada("gemini-3.8-flash", uso.input_tokens, uso.output_tokens, uso.cache_read_tokens, uso.cache_write_tokens)
    assert registro.costo_usd(llamada) == respuesta.cost().total_price
