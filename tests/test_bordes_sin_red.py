"""Bordes del ciclo del agente y de las herramientas que el resto de la suite no ejercitaba (T5.5).

Los encontró una medición de cobertura de líneas de la suite completa, hecha con sys.monitoring y sin instalar
nada (2026-11-01: 95.9% de las 7,928 líneas ejecutables de agente/, app.py y eval/). Cada prueba fija un
comportamiento visible de una rama que ninguna otra prueba alcanzaba; no persigue la línea. Las ramas que son
defensas sin camino de entrada (un número que el patrón ya validó y que Decimal rechazaría, una respuesta final
que no es del modelo) se dejaron fuera a propósito.

Todas corren sin red, sin llaves y sin la extensión fts: las de las herramientas usan la base de tests/conftest.py,
que no trae el índice de texto, y las del ciclo no tocan el modelo.
"""

import datetime as dt

import duckdb
import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from agente import agente, construir, definiciones
from agente.herramientas import inventario as inv
from agente.herramientas import tickets as modulo_tickets
from agente.herramientas.comun import EntradaInventario

# ------------------------------------------------------------------ topes: qué límite cortó el turno


@pytest.mark.parametrize(
    "mensaje, motivo",
    [
        ("The next request would exceed the request_limit of 4", "solicitudes"),
        ("The next tool call(s) would exceed the tool_calls_limit of 12 (tool_calls=13)", "herramientas"),
    ],
)
def test_cada_limite_de_pydantic_ai_que_el_agente_configura_tiene_su_motivo_de_tope(mensaje, motivo):
    assert agente._motivo_del_limite(UsageLimitExceeded(mensaje)) == motivo


def test_un_limite_que_el_agente_no_configura_no_se_disfraza_de_tope():
    error = UsageLimitExceeded("The next request would exceed the output_tokens_limit of 5")
    with pytest.raises(UsageLimitExceeded) as lanzado:
        agente._motivo_del_limite(error)
    assert lanzado.value is error


def test_una_respuesta_cortada_por_max_tokens_cuenta_solo_si_es_la_ultima_del_modelo():
    corta = ModelResponse(parts=[TextPart("dice 17,")], finish_reason="length")
    completa = ModelResponse(parts=[TextPart("Listo.")], finish_reason="stop")
    pregunta = ModelRequest(parts=[UserPromptPart("hola")])

    assert agente._cortada_por_tokens([pregunta, corta]) is True
    assert agente._cortada_por_tokens([pregunta, corta, pregunta, completa]) is False
    assert agente._cortada_por_tokens([pregunta]) is False
    assert agente._cortada_por_tokens([]) is False


# ------------------------------------------------------------------ latencia de cada llamada


def test_la_latencia_de_una_llamada_es_none_si_falta_una_hora_o_el_orden_no_tiene_sentido():
    inicio = dt.datetime(2026, 11, 1, 12, 0, 0, tzinfo=dt.UTC)

    assert agente._milisegundos(inicio, inicio + dt.timedelta(milliseconds=1250)) == 1250
    assert agente._milisegundos(None, inicio) is None
    assert agente._milisegundos(inicio, None) is None
    assert agente._milisegundos(inicio + dt.timedelta(seconds=1), inicio) is None


# ------------------------------------------------------------------ qué modelo se usa cuando nadie lo pasa


def test_el_modelo_configurado_sale_de_env_sin_pisar_el_entorno_y_sin_leer_el_env_real(monkeypatch):
    # Con modelo=None, responder() lee .env con load_dotenv. La prueba lo cambia por un doble: leer el .env
    # real cargaría las llaves de quien corre la suite.
    llamadas = []
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: llamadas.append((args, kwargs)))
    monkeypatch.setenv("MODELO", "anthropic:claude-haiku-5-5")

    assert agente._modelo_configurado() == "anthropic:claude-haiku-5-5"
    assert llamadas == [((), {})], "load_dotenv sin override: lo que ya está en el entorno manda sobre .env"

    monkeypatch.setenv("MODELO", "")
    assert agente._modelo_configurado() == agente.MODELO_POR_OMISION
    monkeypatch.delenv("MODELO")
    assert agente._modelo_configurado() == agente.MODELO_POR_OMISION


# ------------------------------------------------------------------ marca de los tickets sospechosos


def test_el_aviso_de_sospechosos_nombra_cada_folio_en_singular_y_en_plural():
    uno = agente.aviso_de_sospechosos(["1286"])
    dos = agente.aviso_de_sospechosos(["1286", "1301"])
    tres = agente.aviso_de_sospechosos(["1286", "1301", "1400"])

    assert uno.startswith("Aviso: el ticket 1286 queda marcado como sospechoso")
    assert dos.startswith("Aviso: los tickets 1286 y 1301 quedan marcados como sospechosos")
    assert tres.startswith("Aviso: los tickets 1286, 1301 y 1400 quedan marcados como sospechosos")
    assert "lo tomé como dato" in uno and "los tomé como dato" in tres


def test_la_nota_de_la_herramienta_de_tickets_tambien_distingue_uno_de_varios():
    uno = modulo_tickets._nota_de_sospechosos(["1286"])
    varios = modulo_tickets._nota_de_sospechosos(["1286", "1301"])

    assert uno.startswith("ticket 1286 marcado como sospechoso")
    assert varios.startswith("tickets 1286, 1301 marcados como sospechosos")
    assert "cuenta igual" in uno and "cuentan igual" in varios


def test_un_ticket_sin_descripcion_ni_es_sospechoso_ni_trae_texto():
    assert modulo_tickets.es_sospechoso(None) is False
    assert modulo_tickets.es_sospechoso("") is False
    assert modulo_tickets.texto_del_ticket(None) is None


# ------------------------------------------------------------------ inventario


@pytest.fixture(scope="module")
def conexion(base_construida):
    con = duckdb.connect(str(base_construida.base), read_only=True)
    yield con
    con.close()


@pytest.fixture
def con(conexion):
    cursor = conexion.cursor()
    yield cursor
    cursor.close()


def test_un_sku_que_no_es_de_la_categoria_pedida_no_da_filas_en_ninguna_metrica(con):
    # SKU-1002 es una leche deslactosada (Lácteos): con Panadería no hay nada que contar, valuar ni cubrir.
    s = definiciones.supuestos()
    casos = [
        {"metrica": "existencia"},
        {"metrica": "existencia", "agrupar_por": "tienda"},
        {"metrica": "valuacion"},
        {"metrica": "cobertura"},
    ]
    for caso in casos:
        resultado = inv.inventario(con, EntradaInventario(sku="SKU-1002", categoria="Panadería", **caso), s)
        assert resultado.estado == "sin_resultados", caso
        assert resultado.resumen == "No hay filas de inventario con esos filtros", caso
        assert not resultado.cifras, f"{caso}: una consulta vacía no debe traer cifras"


def test_si_se_pide_un_sku_y_un_texto_de_producto_manda_el_sku_y_lo_dice(con):
    resultado = inv.inventario(
        con, EntradaInventario(metrica="existencia", sku="SKU-1002", producto="leche entera", tiendas=["valle norte"]),
        definiciones.supuestos(),
    )

    assert resultado.estado == "ok"
    assert "se usó el SKU SKU-1002; el texto del producto no se usó" in resultado.notas
    assert "Leche deslactosada" in str(resultado.filas) + resultado.resumen


def test_un_producto_que_no_vendio_en_el_mes_base_no_recibe_una_cobertura_inventada(copia_datos, tmp_path):
    # Sin venta en agosto no hay media diaria, y dividir la existencia entre cero no es una cifra (K12).
    ventas = copia_datos / "ventas.csv"
    lineas = ventas.read_bytes().splitlines(keepends=True)
    sin_agosto = [linea for linea in lineas if not (linea.startswith(b"2026-08") and b",SKU-1002," in linea)]
    assert len(sin_agosto) < len(lineas), "la copia debía perder las ventas de agosto del SKU"
    ventas.write_bytes(b"".join(sin_agosto))
    destino = tmp_path / "sin_agosto.duckdb"
    construir.construir(copia_datos, destino, politicas=False, indice_texto=False)

    with duckdb.connect(str(destino), read_only=True) as conexion:
        resultado = inv.inventario(
            conexion.cursor(),
            EntradaInventario(metrica="cobertura", sku="SKU-1002", tiendas=["valle norte"]),
            definiciones.supuestos(),
        )

    # El error con tipo vuelve al modelo como resultado, sin cifras: no hay un rango que darle a la gerente.
    assert resultado.estado == "division_entre_cero"
    assert "agosto" in resultado.resumen.lower()
    assert not resultado.cifras and not resultado.filas
