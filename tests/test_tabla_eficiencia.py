"""La tabla de T6.1 (C6.1): una sola tabla por categoría en el .md y en el .json de una corrida, sin modelo y sin base.

Las corridas son de turnos armados a mano, así que el reporte sale de cifras que la prueba conoce. Los campos llevan los
nombres de #metas del plan: gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, costo_usd, latencia_ms y n_llamadas.
"""

import json
import statistics
from decimal import Decimal

import pytest
import yaml
from pydantic_ai.models.test import TestModel

from eval import correr
from eval.evaluadores import ResultadoPrompt
from tests.test_correr import compuesto, sencilla, turno_armado

CAMPOS_DE_LA_FILA = {
    "turnos",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "costo_usd",
    "latencia_ms",
    "n_llamadas",
    "exactitud",
}
ENTRADA = (1000, 2000, 3000, 5000, 9000, 13000)
SALIDA = (100, 200, 300, 500, 900, 1300)
COSTO = ("0.0001", "0.0002", "0.0003", "0.0005", "0.0009", "0.0013")
LATENCIA = (1500, 2500, 3500, 5500, 9500, 13500)
LLAMADAS = (1, 1, 2, 2, 3, 4)
SIN_MEDIDA = "sin turnos medibles"


@pytest.fixture
def correr_set(tmp_path, monkeypatch):
    """El comando con --configuracion base y 2 corridas: cada caso es (prompt, turnos por corrida). Da el .json y el .md."""

    def _correr_set(*casos):
        pendientes = {prompt["id"]: list(corridas) for prompt, corridas in casos}
        monkeypatch.setattr(correr, "correr_prompt", lambda prompt, conf: ResultadoPrompt(turnos=pendientes[prompt.id].pop(0)))
        monkeypatch.setattr(correr, "_hash_de_las_fuentes", lambda conf: "hash-de-ensayo")
        ruta_banco = tmp_path / "banco.yaml"
        ruta_banco.write_text(yaml.safe_dump({"prompts": [prompt for prompt, _ in casos]}, allow_unicode=True), encoding="utf-8")
        salida = correr.main(
            ["--banco", str(ruta_banco), "--completo", "--corridas", "2", "--configuracion", "base"],
            modelo=TestModel(call_tools=[], custom_output_text="sin uso"),
            archivo_registro=tmp_path / "turnos.jsonl",
            dir_reportes=tmp_path / "reportes",
            base=tmp_path / "sin_base.duckdb",
            raiz_copias=tmp_path / "copias",
        )
        assert salida == 0
        [como_json] = (tmp_path / "reportes").glob("*.json")
        [markdown] = (tmp_path / "reportes").glob("*.md")
        return json.loads(como_json.read_text(encoding="utf-8")), markdown.read_text(encoding="utf-8")

    return _correr_set


def _turno_medido(i, **campos):
    medidas = {
        "entrada_tokens": ENTRADA[i],
        "salida_tokens": SALIDA[i],
        "costo_usd": Decimal(COSTO[i]),
        "latencia_ms": LATENCIA[i],
        "n_llamadas": LLAMADAS[i],
    }
    return turno_armado(**{**medidas, **campos})


@pytest.fixture
def muestra(correr_set):
    """3 prompts de tickets y 1 de abstención, 2 corridas: en la segunda, un turno de tickets termina en tope y reprueba."""
    tickets = [sencilla(f"N-{i}", f"pregunta {i} de ensayo", categoria="tickets") for i in range(3)]
    abstencion = sencilla("N-9", "pregunta aparte de ensayo", categoria="abstencion")
    return correr_set(
        (tickets[0], [(_turno_medido(0),), (_turno_medido(3),)]),
        (tickets[1], [(_turno_medido(1),), (_turno_medido(4, estado="tope"),)]),
        (tickets[2], [(_turno_medido(2),), (_turno_medido(5),)]),
        (
            abstencion,
            [
                (turno_armado(entrada_tokens=20000, salida_tokens=2000, costo_usd=Decimal("0.002"), latencia_ms=20000, n_llamadas=4),),
                (turno_armado(entrada_tokens=30000, salida_tokens=3000, costo_usd=Decimal("0.003"), latencia_ms=30000, n_llamadas=4),),
            ],
        ),
    )


def _cuantiles(valores):
    cortes = statistics.quantiles(valores, n=10, method="inclusive")
    return cortes[4], cortes[8]


def test_cada_categoria_da_mediana_y_p90_de_las_dos_corridas_juntas(muestra):
    fila = muestra[0]["eficiencia_por_categoria"]["tickets"]
    assert set(fila) == CAMPOS_DE_LA_FILA
    assert fila["turnos"] == 6
    assert fila["gen_ai.usage.input_tokens"] == {"mediana": 4000, "p90": 11000}
    assert fila["gen_ai.usage.output_tokens"] == {"mediana": 400, "p90": 1100}
    assert fila["costo_usd"] == {"mediana": pytest.approx(0.0004), "p90": pytest.approx(0.0011)}
    assert fila["latencia_ms"] == {"p50": 4500, "p90": 11500}
    assert fila["n_llamadas"] == {"mediana": 2, "p90": 3.5}


@pytest.mark.parametrize(
    ("campo", "serie", "clave"),
    [
        ("gen_ai.usage.input_tokens", ENTRADA, "mediana"),
        ("gen_ai.usage.output_tokens", SALIDA, "mediana"),
        ("latencia_ms", LATENCIA, "p50"),
        ("n_llamadas", LLAMADAS, "mediana"),
    ],
)
def test_cada_medida_coincide_con_statistics_quantiles(muestra, campo, serie, clave):
    medio, alto = _cuantiles(serie)
    assert muestra[0]["eficiencia_por_categoria"]["tickets"][campo] == {clave: pytest.approx(medio), "p90": pytest.approx(alto)}


def test_la_exactitud_va_en_la_misma_fila_con_su_n_por_corrida_y_su_rango(muestra):
    salida = muestra[0]
    exactitud = salida["eficiencia_por_categoria"]["tickets"]["exactitud"]
    assert exactitud["por_corrida"] == [
        {"corrida": 1, "aciertos": 3, "n": 3, "porcentaje": 100.0},
        {"corrida": 2, "aciertos": 2, "n": 3, "porcentaje": 66.7},
    ]
    assert exactitud["rango"] == {"minimo": 66.7, "maximo": 100.0, "corridas": 2}
    assert exactitud == salida["por_categoria"]["tickets"]


def test_el_markdown_da_las_mismas_cifras_en_una_sola_tabla(muestra):
    markdown = muestra[1]
    assert (
        "| Categoría | Turnos | Entrada (mediana / p90) | Salida (mediana / p90) | Costo USD (mediana / p90) "
        "| Latencia ms (p50 / p90) | Llamadas por turno (mediana / p90) | Exactitud por corrida | Exactitud, rango |"
    ) in markdown
    assert (
        "| tickets | 6 | 4,000 / 11,000 | 400 / 1,100 | 0.000400 / 0.001100 | 4,500 / 11,500 | 2.00 / 3.50 "
        "| 3 de 3 (100.0 %); 2 de 3 (66.7 %) | 66.7 % a 100.0 % (2 corridas) |"
    ) in markdown


def test_el_total_es_la_mediana_de_todos_los_turnos_la_que_sustituye_el_supuesto_de_8000_y_1000(muestra):
    salida, markdown = muestra
    total = salida["eficiencia_total"]
    assert set(total) == CAMPOS_DE_LA_FILA
    assert total["turnos"] == 8
    assert total["gen_ai.usage.input_tokens"] == {"mediana": 7000, "p90": 23000}
    assert total["gen_ai.usage.output_tokens"] == {"mediana": 700, "p90": 2300}
    assert [fila["aciertos"] for fila in total["exactitud"]["por_corrida"]] == [
        sum(fila["exactitud"]["por_corrida"][corrida]["aciertos"] for fila in salida["eficiencia_por_categoria"].values())
        for corrida in range(2)
    ]
    assert (
        "| Todas | 8 | 7,000 / 23,000 | 700 / 2,300 | 0.000700 / 0.002300 | 7,500 / 23,000 | 2.50 / 4.00 "
        "| 4 de 4 (100.0 %); 3 de 4 (75.0 %) | 75.0 % a 100.0 % (2 corridas) |"
    ) in markdown


def test_lo_que_ya_reportaba_sigue_igual(muestra):
    salida, markdown = muestra
    uso = salida["uso_por_categoria"]["tickets"]
    assert uso["n"] == 6
    assert uso["entrada_tokens"] == {"p50": 4000, "p90": 11000}
    assert salida["uso_total"]["turnos"] == 8
    assert "## Tokens, costo y latencia por categoría (p50 / p90)" in markdown
    assert "## Por categoría" in markdown


def test_la_corrida_dice_que_es_la_configuracion_base_sin_cache(muestra):
    meta = muestra[0]["meta"]
    assert meta["configuracion"] == "base"
    assert meta["ajustes"]["cache_claude"] is False
    assert meta["ajustes"]["esfuerzo_claude"] is None


def test_un_turno_de_dos_categorias_cuenta_en_las_dos_y_una_sola_vez_en_el_total(correr_set):
    partes = [
        sencilla("N-1", "pregunta uno de ensayo", categoria="tickets"),
        sencilla("N-2", "pregunta dos de ensayo", categoria="abstencion"),
    ]
    dos = compuesto("DN-1", "sin_signos", "pregunta uno de ensayo y dos", "N-1", "N-2")
    salida, _ = correr_set(
        (partes[0], [(turno_armado(entrada_tokens=10),), (turno_armado(entrada_tokens=10),)]),
        (partes[1], [(turno_armado(entrada_tokens=20),), (turno_armado(entrada_tokens=20),)]),
        (dos, [(turno_armado(entrada_tokens=500),), (turno_armado(entrada_tokens=500),)]),
    )
    por_categoria = salida["eficiencia_por_categoria"]
    assert por_categoria["tickets"]["turnos"] == 4
    assert por_categoria["abstencion"]["turnos"] == 4
    assert salida["eficiencia_total"]["turnos"] == 6
    assert salida["eficiencia_total"]["gen_ai.usage.input_tokens"]["mediana"] == 20


def test_una_categoria_donde_todo_trono_sale_sin_medidas_y_con_su_exactitud(correr_set):
    """No se cae ni inventa ceros: el turno que tronó no dejó uso que medir, pero sí cuenta en la exactitud."""
    tronada = sencilla("N-1", "pregunta uno de ensayo", categoria="tickets")
    sana = sencilla("N-2", "pregunta dos de ensayo", categoria="abstencion")
    salida, markdown = correr_set(
        (tronada, [(turno_armado(estado="excepcion"),), (turno_armado(estado="excepcion"),)]),
        (sana, [(turno_armado(entrada_tokens=100),), (turno_armado(entrada_tokens=100),)]),
    )
    fila = salida["eficiencia_por_categoria"]["tickets"]
    assert set(fila) == CAMPOS_DE_LA_FILA
    assert fila["turnos"] == 0
    assert fila["gen_ai.usage.input_tokens"] is None
    assert fila["costo_usd"] is None
    assert fila["latencia_ms"] is None
    assert [corrida["n"] for corrida in fila["exactitud"]["por_corrida"]] == [1, 1]
    assert [corrida["aciertos"] for corrida in fila["exactitud"]["por_corrida"]] == [0, 0]
    assert salida["eficiencia_total"]["turnos"] == 2
    renglon = next(linea for linea in markdown.splitlines() if linea.startswith("| tickets | 0 |"))
    assert renglon.count(SIN_MEDIDA) == 5


def test_si_tronaron_todos_los_turnos_el_reporte_igual_sale(correr_set):
    tronada = sencilla("N-1", "pregunta uno de ensayo", categoria="tickets")
    salida, markdown = correr_set((tronada, [(turno_armado(estado="excepcion"),), (turno_armado(estado="excepcion"),)]))
    assert salida["eficiencia_total"]["turnos"] == 0
    assert salida["eficiencia_total"]["gen_ai.usage.input_tokens"] is None
    assert "| Todas | 0 |" in markdown


def test_sin_precio_no_se_inventa_un_costo(correr_set):
    prompt = sencilla("N-1", "pregunta uno de ensayo", categoria="tickets")
    salida, markdown = correr_set((prompt, [(turno_armado(entrada_tokens=100),), (turno_armado(entrada_tokens=100),)]))
    assert salida["eficiencia_por_categoria"]["tickets"]["costo_usd"] is None
    renglon = next(linea for linea in markdown.splitlines() if linea.startswith("| tickets | 2 | 100 / 100 |"))
    assert "sin precio" in renglon
