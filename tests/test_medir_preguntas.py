"""eval/medir_preguntas.py: solo las preguntas pedidas, por el camino de la evaluación, sin tocar eval/reportes (D34-B). Sin red."""

from __future__ import annotations

import json

import pytest

from eval import banco, correr, medir_preguntas


@pytest.fixture
def sin_llave(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHIVO_ENV", str(tmp_path / "no-existe.env"))


def _correr_de_mentira(monkeypatch, reporte: dict | None = None, codigo: int = 0) -> dict:
    llamada: dict = {}

    def correr_main(argumentos, *, dir_reportes, archivo_registro):
        llamada["argumentos"] = argumentos
        llamada["dir_reportes"] = dir_reportes
        llamada["archivo_registro"] = archivo_registro
        llamada["elegidos"] = banco.seleccionar(banco.leer_banco(), completo=True, variantes=False)
        if reporte is not None:
            dir_reportes.mkdir(parents=True, exist_ok=True)
            (dir_reportes / "reporte.json").write_text(json.dumps(reporte), encoding="utf-8")
        return codigo

    monkeypatch.setattr(correr, "main", correr_main)
    return llamada


def test_elige_las_preguntas_pedidas_en_el_orden_del_banco():
    elegidas = medir_preguntas.elegir(banco.leer_banco(), ["DN-07", "PT-19"])
    assert [prompt.id for prompt in elegidas] == ["PT-19", "DN-07"]


def test_una_pregunta_que_no_esta_en_el_banco_se_nombra():
    with pytest.raises(ValueError, match="PT-999"):
        medir_preguntas.elegir(banco.leer_banco(), ["PT-19", "PT-999"])


def test_corre_solo_esas_preguntas_con_haiku_y_con_su_salida_aparte(monkeypatch, tmp_path, sin_llave):
    llamada = _correr_de_mentira(monkeypatch)
    salida = tmp_path / "medida"
    assert medir_preguntas.main(["PT-19", "N-07", "--corridas", "3", "--salida", str(salida)]) == 0
    assert [prompt.id for prompt in llamada["elegidos"]] == ["PT-19", "N-07"]
    assert llamada["argumentos"] == ["--completo", "--corridas", "3", "--modelo", "anthropic:claude-haiku-5-5"]
    assert llamada["dir_reportes"] == salida
    assert llamada["archivo_registro"] == salida / "turnos.jsonl"


def test_deja_el_banco_como_estaba_aunque_la_corrida_falle(monkeypatch, tmp_path, sin_llave):
    original = banco.seleccionar

    def falla(*_, **__):
        raise RuntimeError("se cayó")

    monkeypatch.setattr(correr, "main", falla)
    with pytest.raises(RuntimeError):
        medir_preguntas.main(["PT-19", "--salida", str(tmp_path)])
    assert banco.seleccionar is original


def test_sin_carpeta_de_salida_no_corre(monkeypatch, sin_llave):
    # El reporte parcial se llamaría como uno completo: en eval/reportes se confundiría con el del set.
    llamada = _correr_de_mentira(monkeypatch)
    with pytest.raises(SystemExit):
        medir_preguntas.main(["PT-19"])
    assert llamada == {}


def test_una_pregunta_desconocida_no_gasta_la_llave(monkeypatch, tmp_path, capsys, sin_llave):
    llamada = _correr_de_mentira(monkeypatch)
    assert medir_preguntas.main(["PT-999", "--salida", str(tmp_path)]) == medir_preguntas.SALIDA_ID_DESCONOCIDO
    assert llamada == {}
    assert "PT-999" in capsys.readouterr().err


def test_devuelve_el_codigo_de_la_corrida(monkeypatch, tmp_path, sin_llave):
    _correr_de_mentira(monkeypatch, codigo=3)
    assert medir_preguntas.main(["PT-19", "--salida", str(tmp_path)]) == 3


def test_imprime_cuantas_corridas_acerto_cada_pregunta(monkeypatch, tmp_path, capsys, sin_llave):
    reporte = {"preguntas": [{"id": "PT-19", "por_corrida": [True, False, True]}, {"id": "N-07", "por_corrida": [True, True, True]}]}
    _correr_de_mentira(monkeypatch, reporte)
    assert medir_preguntas.main(["PT-19", "N-07", "--corridas", "3", "--salida", str(tmp_path)]) == 0
    salida = capsys.readouterr().out
    assert "PT-19: 2 de 3" in salida
    assert "N-07: 3 de 3" in salida
