"""T6.2: eval/medir_palancas.py apaga una palanca al vuelo, la deja como estaba y arma la tabla contra la base. Sin red."""

from __future__ import annotations

import json

import pytest
from pydantic_ai.models.test import TestModel

from agente import agente, consultas, construir, manifiesto
from agente.ajustes_modelo import AJUSTES
from agente.herramientas.comun import Resultado
from agente.historial import VUELTAS, Historial, armar_vuelta
from eval import banco, correr, medir_p43, medir_palancas

SIN_CIFRA = "No tengo ese dato."
LARGO = "palabra " * 400


def _palabras_vacias_sin_indice(con, palabras_vacias):
    palabras = sorted(set(construir.leer_palabras_vacias(palabras_vacias)))
    con.execute(f"CREATE TABLE {construir.TABLA_PALABRAS_VACIAS} (sw VARCHAR PRIMARY KEY)")
    con.executemany(f"INSERT INTO {construir.TABLA_PALABRAS_VACIAS} VALUES (?)", [[palabra] for palabra in palabras])


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(construir, "cargar_fts", lambda con, instalar=True: None)
        parche.setattr(consultas, "cargar_fts", lambda con, instalar=False: None)
        parche.setattr(construir, "crear_indice_texto", _palabras_vacias_sin_indice)
        ruta = tmp_path_factory.mktemp("palancas") / "tienda.duckdb"
        construir.asegurar_base(construir.DIR_DATOS, ruta)
        yield ruta


# ------------------------------------------------------------------ cada palanca apaga solo lo suyo


def test_cache_y_esfuerzo_se_apagan_por_separado():
    sin_cache = medir_palancas.VARIANTES["sin_cache"].ajustes
    medium = medir_palancas.VARIANTES["esfuerzo_medium"].ajustes
    assert (sin_cache.cache_claude, sin_cache.esfuerzo_claude) == (False, AJUSTES.esfuerzo_claude)
    assert (medium.cache_claude, medium.esfuerzo_claude) == (True, "medium")


def test_la_base_usa_los_ajustes_de_la_interfaz_y_no_toca_nada():
    responder = agente.responder
    with medir_palancas.apagada("interfaz"):
        assert agente.responder is responder
        assert correr.Historial is Historial
    assert medir_palancas.VARIANTES["interfaz"].ajustes == AJUSTES


def test_salida_larga_manda_la_consulta_y_vuelve_como_estaba():
    resultado = Resultado(herramienta="ventas", resumen="x", consulta="SELECT 1")
    assert "SELECT 1" not in resultado.texto_modelo()
    with medir_palancas.apagada("salida_larga"):
        assert "SELECT 1" in resultado.texto_modelo()
    assert "SELECT 1" not in resultado.texto_modelo()


def test_historial_sin_tope_guarda_todas_las_vueltas_enteras_y_vuelve_como_estaba():
    with medir_palancas.apagada("historial_sin_tope"):
        assert correr.Historial().capacidad > VUELTAS * 10
        assert armar_vuelta("pregunta", LARGO).respuesta == LARGO.strip()
    assert correr.Historial is Historial
    assert armar_vuelta("pregunta", LARGO).respuesta != LARGO.strip()


def test_sin_tope_de_solicitudes_conserva_los_segundos_y_las_herramientas():
    responder = agente.responder
    with medir_palancas.apagada("sin_tope_solicitudes"):
        topes = agente.responder.keywords["topes"]
        assert topes.solicitudes >= 100
        assert (topes.segundos, topes.herramientas, topes.max_tokens) == (
            agente.TOPES.segundos,
            agente.TOPES.herramientas,
            agente.TOPES.max_tokens,
        )
    assert agente.responder is responder


def test_el_alcance_de_conversaciones_son_las_cinco_del_banco():
    prompts = banco.leer_banco(banco.BANCO)
    with medir_palancas.apagada("historial_sin_tope"):
        elegidos = banco.seleccionar(prompts, completo=False, variantes=False)
    assert [prompt.id for prompt in elegidos] == ["CV-01", "CV-02", "CV-03", "CV-04", "CV-05"]
    assert len(banco.seleccionar(prompts, completo=False, variantes=False)) == 30


def test_una_excepcion_tambien_deja_todo_como_estaba():
    with pytest.raises(ValueError), medir_palancas.apagada("salida_larga"):
        raise ValueError
    assert "SELECT 1" not in Resultado(herramienta="ventas", resumen="x", consulta="SELECT 1").texto_modelo()


# ------------------------------------------------------------------ el comando


def test_el_comando_corre_la_variante_y_el_reporte_lleva_su_nombre(tmp_path, base):
    ruta_banco = tmp_path / "banco.yaml"
    prompt = {
        "id": "PT-01",
        "bloque": "normal_sencilla",
        "nucleo": True,
        "prompt": "cuanto vendio cumbres",
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
    ruta_banco.write_text(json.dumps({"prompts": [prompt]}), encoding="utf-8")
    salida = medir_palancas.correr_variante(
        "sin_cache",
        ["--banco", str(ruta_banco), "--corridas", "1"],
        modelo=TestModel(call_tools=[], custom_output_text=SIN_CIFRA),
        archivo_registro=tmp_path / "turnos.jsonl",
        dir_reportes=tmp_path / "reportes",
        base=base,
        raiz_copias=tmp_path / "copias",
    )
    assert salida == correr.SALIDA_BIEN
    reporte = json.loads(next((tmp_path / "reportes").glob("*-sin_cache-*.json")).read_text(encoding="utf-8"))
    assert reporte["meta"]["configuracion"] == "sin_cache"
    assert reporte["meta"]["ajustes"]["cache_claude"] is False
    assert "sin_cache" not in correr.CONFIGURACIONES


def test_las_frases_de_cobertura_van_al_manifiesto_y_el_banco_no_trae_ninguna():
    assert all(manifiesto.pide_la_cobertura(frase) for frase in medir_palancas.FRASES_DE_COBERTURA)
    assert medir_palancas.turnos_del_manifiesto_en_el_banco(banco.BANCO) == 0


def test_cobertura_con_modelo_resta_lo_que_cuesta_contra_cero(tmp_path, base):
    medida = medir_palancas.medir_cobertura(
        TestModel(call_tools=[], custom_output_text=SIN_CIFRA),
        pasadas=1,
        base=base,
        archivo_registro=tmp_path / "turnos.jsonl",
    )
    assert medida["turnos_sin_modelo"]["entrada_tokens"] == 0
    assert medida["turnos_sin_modelo"]["n_llamadas"] == 0
    assert medida["turnos_con_modelo"]["n"] == len(medir_palancas.FRASES_DE_COBERTURA)
    assert medida["turnos_con_modelo"]["entrada_tokens"]["p50"] > 0
    assert all("texto" not in fila for fila in medida["detalle"])


# ------------------------------------------------------------------ la tabla y la regla


def _reporte(nombre, juez, reglas, entrada=4000, sin_cache=500, salida=400, costo=0.0004, p50=3000, p90=7000, topes=0):
    turno = {"estado": "ok", "tope": None, "entrada_no_cacheada": sin_cache, "razonamiento_tokens": 50, "costo_usd": str(costo), "n_llamadas": 2, "reintentos": 0}
    turnos = [dict(turno, tope="solicitudes") if indice < topes else dict(turno) for indice in range(10)]
    por_corrida = lambda aciertos: [{"corrida": n, "aciertos": a, "n": 30, "porcentaje": a / 30 * 100} for n, a in enumerate(aciertos, 1)]
    return {
        "meta": {"configuracion": nombre},
        "acierto": {"alcance": {"por_corrida": por_corrida(juez)}},
        "acierto_reglas": {"alcance": {"por_corrida": por_corrida(reglas)}},
        "juez": {"uso": {"costo_usd": "0.01"}},
        "uso_total": {"costo_usd": str(costo * 10), "turnos": 10, "entrada_tokens": entrada * 10, "salida_tokens": salida * 10},
        "eficiencia_total": {
            "gen_ai.usage.input_tokens": {"mediana": entrada, "p90": entrada * 2},
            "gen_ai.usage.output_tokens": {"mediana": salida, "p90": salida * 2},
            "costo_usd": {"mediana": costo, "p90": costo * 2},
            "latencia_ms": {"p50": p50, "p90": p90},
            "n_llamadas": {"mediana": 2, "p90": 3},
        },
        "turnos": turnos,
    }


def test_la_palanca_cumple_si_con_ella_no_se_pierde_exactitud_y_baja_algo():
    con_ella = _reporte("interfaz", (27, 28), (29, 29), costo=0.0004)
    sin_ella = _reporte("sin_cache", (27, 27), (29, 28), costo=0.0012)
    regla = medir_palancas.aplicar_regla(con_ella, sin_ella)
    assert regla["cumple"] is True
    assert "costo" in regla["baja"]


def test_la_palanca_no_cumple_si_sin_ella_la_exactitud_queda_por_encima():
    con_ella = _reporte("interfaz", (24, 25), (29, 29), costo=0.0004)
    sin_ella = _reporte("sin_cache", (28, 28), (29, 29), costo=0.0012)
    regla = medir_palancas.aplicar_regla(con_ella, sin_ella)
    assert regla["cumple"] is False
    assert regla["exactitud_con_juez"] is False


def test_la_palanca_no_cumple_si_no_baja_nada():
    igual = _reporte("interfaz", (27, 28), (29, 29))
    regla = medir_palancas.aplicar_regla(igual, _reporte("salida_larga", (27, 28), (29, 29)))
    assert regla["baja"] == []
    assert regla["cumple"] is False


def test_la_palanca_no_cumple_si_lo_que_sube_queda_fuera_de_su_meta():
    con_ella = _reporte("interfaz", (27, 28), (29, 29), costo=0.0003, salida=500)
    sin_ella = _reporte("esfuerzo_medium", (27, 28), (29, 29), costo=0.0009, salida=450)
    regla = medir_palancas.aplicar_regla(con_ella, sin_ella)
    assert regla["sube_fuera_de_meta"] == ["salida"]
    assert regla["cumple"] is False


def test_con_la_base_repetida_lo_que_cae_en_el_ruido_no_cuenta():
    con_ella = _reporte("interfaz", (27, 28), (29, 29), costo=0.0003, salida=500)
    sin_ella = _reporte("sin_cache", (27, 28), (29, 29), costo=0.0012, salida=480)
    repetida = _reporte("interfaz", (27, 28), (29, 29), costo=0.0003, salida=530)
    assert medir_palancas.aplicar_regla(con_ella, sin_ella)["cumple"] is False
    regla = medir_palancas.aplicar_regla(con_ella, sin_ella, medir_palancas.ruido_de(con_ella, repetida))
    assert regla["sube_fuera_de_meta"] == []
    assert regla["baja"] == ["costo"]
    assert regla["cumple"] is True


def test_la_tabla_da_las_dos_exactitudes_y_el_delta_con_coma_de_miles():
    con_ella = _reporte("interfaz", (27, 28), (29, 29), entrada=4000, costo=0.0004)
    sin_ella = _reporte("sin_cache", (27, 26), (28, 29), entrada=5234, costo=0.0012)
    tabla = medir_palancas.tabla_markdown(con_ella, [("1 caché", sin_ella)])
    assert "27 y 28" in tabla
    assert "27 y 26" in tabla
    assert "5,234" in tabla
    assert "+1,234" in tabla


# ------------------------------------------------------------------ T5.8: P43 varias veces


def _texto_con_folios(*folios):
    return "Tickets: " + ", ".join(map(str, folios))


def test_p43_aprueba_con_los_5_folios_y_reprueba_si_falta_uno(base):
    con_todos = medir_p43.medir(TestModel(call_tools=[], custom_output_text=SIN_CIFRA), corridas=1, pasadas=1, base=base)
    assert con_todos["total"] == {"pasadas": 1, "topes": 0, "aciertos": 0, "fugas": 0}
    assert con_todos["filas"][0]["folios_faltantes"] == medir_p43.FOLIOS_P43
    assert medir_p43.folios_faltantes(_texto_con_folios(*medir_p43.FOLIOS_P43)) == ()
    assert medir_p43.folios_faltantes(_texto_con_folios(10121, 1013)) == (1012, 1029, 1039, 1043)


def test_p43_cuenta_el_tope_de_solicitudes(base):
    corta = agente.Topes(solicitudes=1)
    medida = medir_p43.medir(TestModel(call_tools=["tickets"]), corridas=2, pasadas=1, base=base, topes=corta)
    assert medida["total"]["pasadas"] == 2
    assert medida["total"]["topes"] == 2
    assert medida["total"]["aciertos"] == 0
    assert "texto" not in medida["filas"][0]
