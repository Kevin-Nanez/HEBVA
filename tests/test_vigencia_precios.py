"""T8.6: los precios de config/precios.yaml siguen vigentes en la semana de la entrega.

El 2026-11-03 se compararon con la página oficial de cada modelo, sin llamar a ningún modelo. Estas pruebas dejan lo que
se comprueba sin red: que cada modelo de la tabla de precios del plan tenga precio, una fuente oficial y la fecha de una
revisión que no sea anterior a la semana de la entrega (D18), y que el costo del registro coincida con el de
genai-prices, que Pydantic AI usa en ModelResponse.cost(). Ministral 3 3B entra con lo que publica Mistral. El precio de Gemini 3.8 Flash
cambia el 2027-01-01: se comparan los dos lados de esa fecha con una hora fija y no con el reloj de la máquina.

La revisión va en "revisado" de cada modelo y no mueve fecha_consulta, la fecha global que fija tests/test_registro.py.
"""

import datetime as dt
import re
from decimal import Decimal
from urllib.parse import urlparse

import pytest
import yaml
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from agente import agente, registro

ENTREGA = dt.date(2026, 11, 6)
UNA_SEMANA_ANTES = ENTREGA - dt.timedelta(days=7)

# La tabla "Precios usados" del plan sin gpt-6-luna, que solo sirve de referencia de precio y no corre en este proyecto.
MODELOS_DEL_PLAN = (
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "ministral-3b-2512",
)
# ai.google.dev redirige con 301 a aistudio.google.com: las dos son la página de Google.
SITIOS_OFICIALES = {"platform.claude.com", "ai.google.dev", "aistudio.google.com", "docs.mistral.ai"}

ULTIMO_MINUTO_DE_2026 = dt.datetime(2026, 12, 31, 23, 59, tzinfo=dt.timezone.utc)
PRIMER_SEGUNDO_DE_2027 = dt.datetime(2027, 1, 1, 0, 0, 1, tzinfo=dt.timezone.utc)
USO_DE_3_8_FLASH = RequestUsage(input_tokens=7732, output_tokens=1000, cache_read_tokens=4000)


def _archivo() -> dict:
    return yaml.safe_load(registro.ARCHIVO_PRECIOS.read_text(encoding="utf-8"))


def _texto_del_modelo(modelo: str) -> str:
    """Las líneas del archivo que van de un modelo al siguiente, con sus comentarios."""
    texto = registro.ARCHIVO_PRECIOS.read_text(encoding="utf-8")
    bloque = re.search(rf"^  {re.escape(modelo)}:\n(?P<lineas>(?:    .*\n|\n)*)", texto, flags=re.MULTILINE)
    assert bloque, f"{modelo} no tiene bloque en config/precios.yaml"
    return bloque["lineas"]


def _costo_del_registro(modelo: str, uso: RequestUsage) -> Decimal | None:
    llamada = agente.Llamada(modelo, uso.input_tokens, uso.output_tokens, uso.cache_read_tokens, uso.cache_write_tokens)
    return registro.costo_usd(llamada)


def _costo_de_pydantic_ai(modelo: str, proveedor: str, uso: RequestUsage, momento: dt.datetime | None = None) -> Decimal:
    datos = {"timestamp": momento} if momento else {}
    respuesta = ModelResponse(parts=[TextPart("x")], usage=uso, model_name=modelo, provider_name=proveedor, **datos)
    return respuesta.cost().total_price


@pytest.mark.parametrize("modelo", MODELOS_DEL_PLAN)
def test_cada_modelo_de_la_tabla_de_precios_del_plan_tiene_precio(modelo):
    assert registro.precios().de(modelo) is not None, f"config/precios.yaml no trae {modelo}: su costo saldría en null"


@pytest.mark.parametrize("modelo", MODELOS_DEL_PLAN)
def test_cada_modelo_cita_la_pagina_oficial_de_su_proveedor(modelo):
    fuente = registro.precios().modelos[modelo].fuente
    direccion = urlparse(fuente)
    assert direccion.scheme == "https" and direccion.hostname in SITIOS_OFICIALES, fuente


@pytest.mark.parametrize("modelo", MODELOS_DEL_PLAN)
def test_la_revision_de_cada_modelo_no_es_anterior_a_la_semana_de_la_entrega(modelo):
    """Una revisión posterior a la entrega no rompe la prueba: solo una fecha vieja o sin fecha."""
    revisado = _archivo()["modelos"][modelo].get("revisado")
    assert isinstance(revisado, dt.date), f"{modelo} no trae la fecha de su revisión en 'revisado'"
    assert revisado >= UNA_SEMANA_ANTES, f"{modelo}: revisado el {revisado}, antes del {UNA_SEMANA_ANTES}"


def test_ministral_trae_los_precios_de_la_pagina_de_mistral():
    precio = registro.precios().de("ministral-3b-2512")
    assert precio is not None
    assert (precio.entrada, precio.salida, precio.cache_lectura) == (Decimal("0.10"), Decimal("0.10"), Decimal("0.01"))
    # Mistral no publica precio de escritura de caché: se cobra como entrada, igual que Gemini.
    assert precio.cache_escritura is None and precio.hasta_tokens_de_prompt is None


@pytest.mark.parametrize(
    ("modelo", "proveedor", "uso"),
    [
        ("claude-sonnet-5-5", "anthropic", RequestUsage(input_tokens=4832, output_tokens=450, cache_read_tokens=4372)),
        ("claude-sonnet-5-5", "anthropic", RequestUsage(input_tokens=6000, output_tokens=900, cache_write_tokens=4372)),
        ("claude-sonnet-5-5", "anthropic", RequestUsage(input_tokens=3000, output_tokens=1200)),
        ("ministral-3b-2512", "mistral", RequestUsage(input_tokens=7732, output_tokens=1000)),
        ("ministral-3b-2512", "mistral", RequestUsage(input_tokens=7732, output_tokens=1000, cache_read_tokens=4000)),
        ("ministral-3b-2512", "mistral", RequestUsage(input_tokens=6000, output_tokens=900, cache_write_tokens=4372)),
    ],
)
def test_el_costo_de_sonnet_y_de_ministral_coincide_con_el_de_pydantic_ai(modelo, proveedor, uso):
    """Haiku y Flash-Lite los coteja tests/test_registro.py y 3.8 Flash tests/test_precio_gemini_flash.py."""
    costo = _costo_del_registro(modelo, uso)
    assert costo is not None
    assert costo == _costo_de_pydantic_ai(modelo, proveedor, uso)


def test_hasta_el_2026_12_31_gemini_3_8_flash_cuesta_lo_que_dice_el_archivo():
    esperado = _costo_de_pydantic_ai("gemini-3.8-flash", "google-gla", USO_DE_3_8_FLASH, ULTIMO_MINUTO_DE_2026)
    assert _costo_del_registro("gemini-3.8-flash", USO_DE_3_8_FLASH) == esperado


def test_desde_el_2027_01_01_gemini_3_8_flash_cuesta_el_doble_del_precio_del_archivo():
    """El archivo y tests/test_precio_gemini_flash.py traen los precios de hoy: ese día hay que cambiarlos.

    Cuando el archivo pase a 1.50, 7.50 y 0.15, este caso se borra.
    """
    del_archivo = _costo_del_registro("gemini-3.8-flash", USO_DE_3_8_FLASH)
    assert _costo_de_pydantic_ai("gemini-3.8-flash", "google-gla", USO_DE_3_8_FLASH, PRIMER_SEGUNDO_DE_2027) == 2 * del_archivo


def test_el_archivo_dice_junto_a_gemini_3_8_flash_el_precio_que_rige_desde_el_2027_01_01():
    bloque = _texto_del_modelo("gemini-3.8-flash")
    faltan = [dato for dato in ("2026-12-31", "2027-01-01", "1.50", "7.50", "0.15") if dato not in bloque]
    assert not faltan, f"El bloque de gemini-3.8-flash no menciona {faltan}"
