"""La medición del juez de producción (eval/medir_juez_produccion.py): con una auditoría inventada, sin red y sin llaves.

Se comprueba cómo se arma el conjunto y cómo se cuenta contra D2-B. Qué tan bien detecta sale de la corrida con llave.
"""

import csv
import json

import pytest
from pydantic_ai import models
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from eval import medir_juez_produccion as medir

COLUMNAS = ("ejecucion", "turno_id", "clase", "cita", "estado")
MARCA_DE_ERROR = "ERROR"


@pytest.fixture(autouse=True)
def sin_llamadas_reales(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


def _uuid(numero):
    return f"00000000-0000-4000-8000-{numero:012d}"


class Auditoria:
    """Una carpeta de auditoría y un registro de turnos, escritos en una carpeta temporal."""

    def __init__(self, raiz):
        self.carpeta = raiz / "auditoria"
        (self.carpeta / "casos").mkdir(parents=True)
        (self.carpeta / "reportes").mkdir()
        self.registro = raiz / "turnos.jsonl"
        self.reportes = raiz / "reportes"
        self._siguiente = 0
        self._lineas = []

    def turno(self, prompt, texto="Vendió 10.00.", estado="ok", historial=(), herramientas=()):
        self._siguiente += 1
        turno_id = _uuid(self._siguiente)
        mensajes = []
        for pregunta, respuesta in historial:
            mensajes += [
                {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": pregunta}]},
                {"kind": "response", "parts": [{"part_kind": "text", "content": respuesta}]},
            ]
        self._lineas.append(
            {
                "tipo": "turno",
                "turno_id": turno_id,
                "pregunta": f"pregunta de {prompt}",
                "estado": estado,
                "respuesta": texto,
                "historial": {"mensajes": mensajes},
                "herramientas": [{"gen_ai.tool.call.result": resultado} for resultado in herramientas],
            }
        )
        return {
            "corrida": 1,
            "prompt": prompt,
            "pregunta": f"pregunta de {prompt}",
            "turno_id": turno_id,
            "texto_gerente": f"{texto}\nFuente: ventas.csv",
        }

    def ejecucion(self, nombre, turnos, casos=()):
        (self.carpeta / "reportes" / f"{nombre}.json").write_text(json.dumps({"turnos": turnos}), encoding="utf-8")
        with open(self.carpeta / "casos" / f"{nombre}-consolidado.csv", "w", encoding="utf-8", newline="") as salida:
            escritor = csv.DictWriter(salida, COLUMNAS, extrasaction="ignore")
            escritor.writeheader()
            for turno_id, clase, cita, estado in casos:
                escritor.writerow({"ejecucion": nombre, "turno_id": turno_id, "clase": clase, "cita": cita, "estado": estado})
        self.registro.write_text("".join(json.dumps(linea) + "\n" for linea in self._lineas), encoding="utf-8")

    def conjunto(self):
        return medir.armar_conjunto(self.carpeta, self.registro)


@pytest.fixture
def auditoria(tmp_path):
    return Auditoria(tmp_path)


def _responder(aprueba, motivo="motivo de ensayo"):
    veredicto = ToolCallPart("final_result", {"motivo": motivo, "aprueba": aprueba})
    return ModelResponse(parts=[veredicto], usage=RequestUsage(input_tokens=1000, output_tokens=100))


def _juez_por_marca(mensajes, info: AgentInfo):
    """Reprueba lo que trae ERROR en la respuesta: un juez que acierta en este conjunto."""
    texto = "\n".join(str(parte.content) for mensaje in mensajes for parte in mensaje.parts if parte.part_kind == "user-prompt")
    respuesta = texto[texto.index("<respuesta>") :]
    return _responder(MARCA_DE_ERROR not in respuesta)


def _ids(renglones):
    return {renglon.id for renglon in renglones}


# ------------------------------------------------------------------ el conjunto


def test_un_caso_cruza_con_su_turno_por_prompt_o_por_numero_de_turno(auditoria):
    sencillo = auditoria.turno("PT-19", "ERROR uno")
    primero = auditoria.turno("DN-05", "bien")
    segundo = auditoria.turno("DN-05", "ERROR dos")
    casos = [("PT-19", "A", "ERROR uno", "CONFIRMADO"), ("DN-05-2", "B", "ERROR dos", "CONFIRMADO")]
    auditoria.ejecucion("E01", [sencillo, primero, segundo], casos)
    conjunto = auditoria.conjunto()
    por_id = {renglon.id: renglon for renglon in conjunto.debe_reprobar}
    assert set(por_id) == {"E01/PT-19", "E01/DN-05-2"}
    assert por_id["E01/PT-19"].turno_id == sencillo["turno_id"]
    assert por_id["E01/DN-05-2"].turno_id == segundo["turno_id"]
    assert conjunto.no_cruzaron == ()


def test_dos_casos_de_un_turno_son_un_renglon_con_dos_casos(auditoria):
    turno = auditoria.turno("PT-30", "ERROR uno y ERROR dos")
    casos = [("PT-30", "B", "ERROR uno", "CONFIRMADO"), ("PT-30", "C", "ERROR dos", "CONFIRMADO")]
    auditoria.ejecucion("E01", [turno], casos)
    renglones = auditoria.conjunto().debe_reprobar
    assert len(renglones) == 1
    assert [caso.clase for caso in renglones[0].casos] == ["B", "C"]


def test_un_caso_que_no_cruza_se_anota_y_no_entra(auditoria):
    turno = auditoria.turno("PT-01", "ERROR")
    auditoria.ejecucion("E01", [turno], [("PT-99", "A", "ERROR", "CONFIRMADO"), ("PT-01-7", "A", "ERROR", "CONFIRMADO")])
    conjunto = auditoria.conjunto()
    assert conjunto.debe_reprobar == ()
    assert [nota.split(":")[0] for nota in conjunto.no_cruzaron] == ["E01/PT-99", "E01/PT-01-7"]


def test_una_cita_que_no_esta_en_el_turno_se_anota_y_el_caso_entra(auditoria):
    turno = auditoria.turno("PT-01", "ERROR real")
    auditoria.ejecucion("E01", [turno], [("PT-01", "A", "otra frase", "CONFIRMADO")])
    conjunto = auditoria.conjunto()
    assert _ids(conjunto.debe_reprobar) == {"E01/PT-01"}
    assert conjunto.citas_sin_hallar == ("E01/PT-01",)


def test_una_cita_con_puntos_suspensivos_se_busca_por_partes_y_sin_negritas(auditoria):
    turno = auditoria.turno("PT-01", "Primero **dice esto**, y luego\nvarias cosas, y al final esto otro.")
    auditoria.ejecucion("E01", [turno], [("PT-01", "A", "dice esto ... al final esto otro", "CONFIRMADO")])
    assert auditoria.conjunto().citas_sin_hallar == ()


def test_los_dudosos_y_los_retirados_no_son_errores_y_de_e10_solo_entran_los_turnos_limpios(auditoria):
    primero = auditoria.turno("PT-01", "dudoso de E01")
    limpio = auditoria.turno("PT-02", "bien")
    dudoso = auditoria.turno("PT-03", "dudoso de E10")
    retirado = auditoria.turno("PT-04", "se refutó")
    confirmado = auditoria.turno("PT-05", "ERROR")
    auditoria.ejecucion("E01", [primero], [("PT-01", "A", "dudoso de E01", "DUDOSO")])
    casos = [
        ("PT-03", "A", "dudoso de E10", "DUDOSO"),
        ("PT-04", "A", "se refutó", "RETIRADO"),
        ("PT-05", "A", "ERROR", "CONFIRMADO"),
    ]
    auditoria.ejecucion("E10", [limpio, dudoso, retirado, confirmado], casos)
    conjunto = auditoria.conjunto()
    assert _ids(conjunto.debe_reprobar) == {"E10/PT-05"}
    assert _ids(conjunto.debe_aprobar) == {"E10/PT-02", "E10/PT-04"}
    assert conjunto.dudosos_fuera == 1


def test_los_turnos_de_otras_ejecuciones_no_entran_como_correctos(auditoria):
    otro = auditoria.turno("PT-02", "bien")
    auditoria.ejecucion("E01", [otro])
    assert auditoria.conjunto().debe_aprobar == ()


def test_la_evidencia_y_la_conversacion_salen_del_registro(auditoria):
    resultado = {"herramienta": "ventas", "resumen": "Total 10.00", "consulta": "SELECT secreto"}
    turno = auditoria.turno("PT-02", historial=[("cuanto vendio", "Vendió 5.00.")], herramientas=[resultado])
    auditoria.ejecucion("E10", [turno])
    renglon = auditoria.conjunto().debe_aprobar[0]
    assert "Total 10.00" in renglon.revision.evidencia
    assert "SELECT" not in renglon.revision.evidencia
    assert renglon.revision.anteriores == (("cuanto vendio", "Vendió 5.00."),)
    assert renglon.revision.respuesta == turno["texto_gerente"]


# ------------------------------------------------------------------ la cuenta contra D2-B


@pytest.mark.parametrize(
    ("detectados", "falsas", "cumple"),
    [(90, 2, True), (100, 0, True), (89, 0, False), (90, 3, False), (89, 3, False)],
)
def test_d2_pide_9_de_10_y_1_de_50_con_enteros(detectados, falsas, cumple):
    assert medir.cumple_d2(detectados, 100, falsas, 100) is cumple


def test_8999_por_ciento_no_pasa_por_redondeo():
    assert medir.cumple_d2(8999, 10000, 0, 100) is False
    assert medir.cumple_d2(90, 100, 201, 10000) is False


def test_p50_y_p90():
    assert medir.percentil(list(range(1, 11)), 50) == 6
    assert medir.percentil(list(range(1, 11)), 90) == 10
    assert medir.percentil([], 50) is None


# ------------------------------------------------------------------ el comando


def _conjunto_de_ensayo(auditoria):
    errores = [auditoria.turno("PT-19", "ERROR uno"), auditoria.turno("PT-30", "ERROR dos y ERROR tres")]
    limpios = [auditoria.turno(f"N-{n:02d}", "Vendió 10.00.") for n in range(1, 4)]
    casos = [
        ("PT-19", "A", "ERROR uno", "CONFIRMADO"),
        ("PT-30", "B", "ERROR dos", "CONFIRMADO"),
        ("PT-30", "C", "ERROR tres", "CONFIRMADO"),
    ]
    auditoria.ejecucion("E10", [*errores, *limpios], casos)


def _lanzar(auditoria, *argumentos, modelo=None):
    return medir.main(
        list(argumentos),
        modelo=modelo or FunctionModel(_juez_por_marca, model_name="guion"),
        dir_auditoria=auditoria.carpeta,
        archivo_turnos=auditoria.registro,
        dir_reportes=auditoria.reportes,
    )


def _reporte(auditoria):
    return json.loads(next(auditoria.reportes.glob("medida-produccion-*.json")).read_text(encoding="utf-8"))


def test_un_juez_que_acierta_cumple_d2_y_sale_con_0(auditoria):
    _conjunto_de_ensayo(auditoria)
    assert _lanzar(auditoria, "--corridas", "2") == medir.SALIDA_BIEN
    reporte = _reporte(auditoria)
    assert reporte["cumple_d2"] is True
    assert len(reporte["corridas"]) == 2
    corrida = reporte["corridas"][0]
    assert (corrida["turnos_detectados"], corrida["turnos_con_error"]) == (2, 2)
    assert (corrida["casos_detectados"], corrida["casos"]) == (3, 3)
    assert (corrida["falsas_alarmas"], corrida["turnos_correctos"]) == (0, 3)


def test_el_reporte_dice_que_paso_con_pt_19_y_pt_30_de_e10(auditoria):
    _conjunto_de_ensayo(auditoria)
    _lanzar(auditoria, "--corridas", "2")
    reporte = _reporte(auditoria)
    assert reporte["vigilados"] == {"E10/PT-19": ["reprueba", "reprueba"], "E10/PT-30": ["reprueba", "reprueba"]}
    tabla = next(auditoria.reportes.glob("medida-produccion-*.md")).read_text(encoding="utf-8")
    assert "E10/PT-19" in tabla
    assert "E10/PT-30" in tabla


def test_un_juez_que_todo_lo_aprueba_no_detecta_nada_y_sale_con_1(auditoria):
    _conjunto_de_ensayo(auditoria)
    modelo = FunctionModel(lambda mensajes, info: _responder(True), model_name="constante")
    assert _lanzar(auditoria, "--corridas", "1", modelo=modelo) == medir.SALIDA_BAJO_D2
    corrida = _reporte(auditoria)["corridas"][0]
    assert corrida["turnos_detectados"] == 0
    assert corrida["no_detectados"] == ["E10/PT-19", "E10/PT-30"]


def test_un_juez_que_todo_lo_reprueba_da_falsas_alarmas(auditoria):
    _conjunto_de_ensayo(auditoria)
    modelo = FunctionModel(lambda mensajes, info: _responder(False), model_name="constante")
    assert _lanzar(auditoria, "--corridas", "1", modelo=modelo) == medir.SALIDA_BAJO_D2
    assert _reporte(auditoria)["corridas"][0]["falsas_alarmas"] == 3


def test_basta_una_corrida_bajo_el_umbral_para_no_cumplir(auditoria):
    _conjunto_de_ensayo(auditoria)
    llamadas = []

    def segunda_peor(mensajes, info):
        llamadas.append(1)
        return _responder(True) if len(llamadas) > 5 else _juez_por_marca(mensajes, info)

    assert _lanzar(auditoria, "--corridas", "2", "--paralelas", "1", modelo=FunctionModel(segunda_peor, model_name="g")) == medir.SALIDA_BAJO_D2
    reporte = _reporte(auditoria)
    assert reporte["corridas"][0]["cumple"] is True
    assert reporte["corridas"][1]["cumple"] is False
    assert reporte["cumple_d2"] is False


def test_un_turno_con_texto_fijo_no_llama_al_modelo_y_cuenta_como_no_detectado(auditoria):
    fijo = auditoria.turno("PT-19", "No pude terminar ERROR", estado="tope")
    limpio = auditoria.turno("N-01", "Vendió 10.00.")
    auditoria.ejecucion("E10", [fijo, limpio], [("PT-19", "C", "No pude terminar", "CONFIRMADO")])
    recibidos = []

    def anota(mensajes, info):
        recibidos.append(1)
        return _responder(True)

    _lanzar(auditoria, "--corridas", "1", modelo=FunctionModel(anota, model_name="guion"))
    assert len(recibidos) == 1
    reporte = _reporte(auditoria)
    assert reporte["conjunto"]["no_llegan_al_juez"] == ["E10/PT-19"]
    assert reporte["corridas"][0]["no_detectados"] == ["E10/PT-19"]


def test_sin_veredicto_no_es_detectado_ni_falsa_alarma(auditoria):
    _conjunto_de_ensayo(auditoria)

    def roto(mensajes, info):
        return ModelResponse(parts=[ToolCallPart("final_result", {"sin": "esquema"})], usage=RequestUsage(input_tokens=1, output_tokens=1))

    assert _lanzar(auditoria, "--corridas", "1", modelo=FunctionModel(roto, model_name="roto")) == medir.SALIDA_BAJO_D2
    corrida = _reporte(auditoria)["corridas"][0]
    assert corrida["turnos_detectados"] == 0
    assert corrida["falsas_alarmas"] == 0
    assert len(corrida["sin_veredicto"]) == 5


def test_si_el_modelo_no_esta_disponible_no_escribe_nada_y_sale_con_4(auditoria):
    _conjunto_de_ensayo(auditoria)

    def caido(mensajes, info):
        raise ModelHTTPError(401, "guion", {"error": "llave"})

    assert _lanzar(auditoria, "--corridas", "1", modelo=FunctionModel(caido, model_name="caido")) == medir.SALIDA_SIN_MODELO
    assert not auditoria.reportes.exists()


def test_el_conjunto_solo_se_cuenta_sin_llamar_al_modelo(auditoria, capsys):
    _conjunto_de_ensayo(auditoria)

    def no_debe(mensajes, info):
        raise AssertionError("no debe llamar al modelo")

    assert _lanzar(auditoria, "--solo-conjunto", modelo=FunctionModel(no_debe, model_name="x")) == medir.SALIDA_BIEN
    salida = capsys.readouterr().out
    assert "2 turnos con error" in salida
    assert "3 casos" in salida
    assert "3 turnos correctos" in salida
    assert not auditoria.reportes.exists()


def test_la_muestra_no_escribe_reporte(auditoria):
    _conjunto_de_ensayo(auditoria)
    assert _lanzar(auditoria, "--muestra", "1") == medir.SALIDA_BIEN
    assert not auditoria.reportes.exists()


def test_el_reporte_no_escribe_en_el_registro_de_turnos(auditoria):
    _conjunto_de_ensayo(auditoria)
    antes = auditoria.registro.read_bytes()
    _lanzar(auditoria, "--corridas", "1")
    assert auditoria.registro.read_bytes() == antes


def test_el_reporte_trae_el_uso_y_el_hash_del_prompt(auditoria):
    _conjunto_de_ensayo(auditoria)
    _lanzar(auditoria, "--corridas", "1")
    reporte = _reporte(auditoria)
    assert reporte["prompt_sha256"] == medir.modulo_juez.PROMPT_JUEZ_PRODUCCION_SHA256
    assert reporte["uso"]["llamadas"] == 5
    assert reporte["uso"]["entrada_tokens"] == 5000
    assert reporte["uso"]["latencia_p50_ms"] is not None
    assert reporte["uso"]["latencia_p90_ms"] is not None
