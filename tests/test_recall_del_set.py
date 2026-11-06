"""recall@4 de la sección sobre el set, con BM25 y sin modelo (T5.3, T2.7). Pide la extensión fts: sin ella se salta."""

import duckdb
import pytest

from agente import agente, construir
from eval import banco, correr


@pytest.fixture(scope="module")
def reales():
    return banco.leer_banco()


@pytest.fixture(scope="module")
def base_con_fts(tmp_path_factory):
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")
    ruta = tmp_path_factory.mktemp("recall") / "tienda.duckdb"
    construir.asegurar_base(construir.DIR_DATOS, ruta)
    return ruta


def test_el_recall_sin_modelo_mide_las_preguntas_con_seccion_que_se_entienden_solas(reales, base_con_fts):
    medida = correr.medir_recall_sin_modelo(reales, base=base_con_fts, dir_datos=construir.DIR_DATOS)
    assert medida["n"] >= 25
    assert 0 <= medida["recall_a_4"] <= 100
    assert medida["fraccion_de_esperadas"] <= medida["recall_a_4"] <= 100
    assert 0 < medida["mrr"] <= 1
    assert isinstance(medida["sin_ninguna"], list)


def test_recall_sin_modelo_desde_el_comando_no_llama_a_responder(monkeypatch, base_con_fts, tmp_path, capsys):
    monkeypatch.setattr(agente, "responder", lambda *a, **o: pytest.fail("no debe llamar a responder"))
    codigo = correr.main(
        ["--recall-sin-modelo"],
        archivo_registro=tmp_path / "turnos.jsonl",
        dir_reportes=tmp_path / "reportes",
        base=base_con_fts,
        raiz_copias=tmp_path / "copias",
    )
    assert codigo == 0
    assert "recall@4" in capsys.readouterr().out
    assert list((tmp_path / "reportes").glob("recall-sin-modelo-*.json"))
