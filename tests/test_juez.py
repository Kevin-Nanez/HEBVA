"""El juez de texto y su validación (T5.4, D11-B, C5.2, K33): con FunctionModel, sin red y sin llaves.

Lo que aquí se comprueba es el comando y la cuenta del acuerdo, no la calidad del juez: esa cifra sale de la corrida con
llave. Los textos de prueba son inventados y las preguntas se piden por id: ninguna del banco se escribe aquí (G11).
"""

import asyncio
import difflib
import json
import re

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic_ai import models
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.usage import RequestUsage
from pydantic_evals import Case, Dataset

from agente import privacidad
from eval import banco, evaluadores, juez
from eval.evaluadores import ResultadoPrompt, TurnoEvaluado

ACUERDO_DEL_CRITERIO = "0.90"
LLAVE_DE_PRUEBA = "llave-de-prueba"


@pytest.fixture(autouse=True)
def sin_llamadas_reales(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture(scope="module")
def etiquetas():
    return juez.leer_etiquetas()


@pytest.fixture(scope="module")
def indice():
    return juez.indice_del_banco()


@pytest.fixture
def carpeta(tmp_path):
    return tmp_path / "juez"


def _de(etiquetas, conjunto):
    return [etiqueta for etiqueta in etiquetas if etiqueta.conjunto == conjunto]


def _texto_del_usuario(mensajes):
    return "\n".join(str(parte.content) for mensaje in mensajes for parte in mensaje.parts if parte.part_kind == "user-prompt")


def _responder(aprueba, motivo="motivo de ensayo"):
    veredicto = ToolCallPart("final_result", {"aprueba": aprueba, "motivo": motivo})
    return ModelResponse(parts=[veredicto], usage=RequestUsage(input_tokens=100, output_tokens=10))


class Guion:
    """Un juez que contesta lo que dice la etiqueta, salvo en los id de `invertir`; anota lo que recibe."""

    def __init__(self, etiquetas, invertir=()):
        self.etiquetas = etiquetas
        self.invertir = set(invertir)
        self.recibidos = []
        self.instrucciones = []
        self.modelo = FunctionModel(self._contestar, model_name="guion")

    def _contestar(self, mensajes, info: AgentInfo):
        texto = _texto_del_usuario(mensajes)
        self.recibidos.append(texto)
        self.instrucciones.append(info.instructions)
        for etiqueta in self.etiquetas:
            if f"<respuesta>\n{juez._dato(etiqueta.respuesta)}\n</respuesta>" in texto:
                aprueba = etiqueta.etiqueta == "aprueba"
                return _responder(aprueba != (etiqueta.id in self.invertir))
        return _responder(True)


NOMBRE_GUION = "function:guion"


def lanzar(carpeta, *argumentos, modelo=None):
    return juez.main(list(argumentos), modelo=modelo, dir_reportes=carpeta)


def leer_reporte(carpeta):
    return json.loads(next(carpeta.glob("validacion-*.json")).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ las etiquetas


def test_son_30_etiquetas_con_id_unico(etiquetas):
    assert len(etiquetas) == 30
    assert len({etiqueta.id for etiqueta in etiquetas}) == 30


@pytest.mark.parametrize(("conjunto", "cuantas"), [("ajuste", 10), ("validacion", 20)])
def test_cada_conjunto_trae_la_mitad_de_cada_etiqueta(etiquetas, conjunto, cuantas):
    """Un juez constante da 50 %: el acuerdo de 90 % no se alcanza sin distinguir."""
    del_conjunto = _de(etiquetas, conjunto)
    assert len(del_conjunto) == cuantas
    assert sum(etiqueta.etiqueta == "aprueba" for etiqueta in del_conjunto) == cuantas // 2


def test_ninguna_pregunta_esta_en_los_dos_conjuntos(etiquetas):
    def raices(conjunto):
        return {etiqueta.pregunta.rsplit(".", maxsplit=1)[-1] for etiqueta in _de(etiquetas, conjunto)}

    assert not raices("ajuste") & raices("validacion")


def test_cada_etiqueta_apunta_a_una_pregunta_del_banco_y_trae_sus_turnos_anteriores(etiquetas, indice):
    for etiqueta in etiquetas:
        caso = juez.caso_de(etiqueta, indice)
        assert caso.criterio, etiqueta.id
        assert len(caso.anteriores) == len(etiqueta.respuestas_anteriores), etiqueta.id


def test_los_tres_aspectos_de_d11_se_validan_al_menos_dos_veces(etiquetas):
    validacion = [etiqueta.aspecto for etiqueta in _de(etiquetas, "validacion")]
    for aspecto in ("nombra_conflicto", "no_afirma_causa", "deja_supuesto"):
        assert validacion.count(aspecto) >= 2, aspecto


def test_las_conversaciones_tambien_estan_en_los_dos_conjuntos(etiquetas):
    for conjunto in ("ajuste", "validacion"):
        assert any(etiqueta.pregunta.startswith("CV-") for etiqueta in _de(etiquetas, conjunto)), conjunto


def test_ninguna_respuesta_copia_la_esperada_del_banco(etiquetas, indice):
    """Una copia casi literal se aprobaría por parecido y no por cumplir el criterio."""
    for etiqueta in etiquetas:
        esperada = juez.caso_de(etiqueta, indice).referencia
        parecido = difflib.SequenceMatcher(None, etiqueta.respuesta, esperada).ratio()
        assert parecido < 0.9, f"{etiqueta.id}: {parecido:.2f}"


def test_lo_que_debe_aprobar_el_juez_tambien_lo_aprueban_las_reglas(etiquetas, indice):
    """El juez se suma a las reglas: una etiqueta «aprueba» que las reglas reprueban tendría mal sus cifras o su cita."""
    for etiqueta in etiquetas:
        if etiqueta.etiqueta == "aprueba":
            assert juez.aprobada_por_reglas(etiqueta, indice), etiqueta.id


def test_las_etiquetas_de_validacion_prueban_lo_que_las_reglas_no_ven(etiquetas, indice):
    """Al menos 8 de las 10 «reprueba» pasan las reglas: ahí es donde el juez aporta, no en repetir las cifras."""
    reprueba = [e for e in _de(etiquetas, "validacion") if e.etiqueta == "reprueba"]
    assert sum(juez.aprobada_por_reglas(e, indice) for e in reprueba) >= 8


def test_las_etiquetas_no_traen_datos_de_contacto(etiquetas):
    for etiqueta in etiquetas:
        textos = [etiqueta.respuesta, etiqueta.porque, *etiqueta.respuestas_anteriores]
        assert not privacidad.buscar_datos_personales("\n".join(textos)), etiqueta.id


def test_el_prompt_del_juez_no_trae_ejemplos_de_las_etiquetas_ni_del_banco(etiquetas, indice):
    """Las etiquetas de validación no ajustan al juez, y sin ejemplos del set no se fija ninguna respuesta (G2)."""
    prompt = juez.PROMPT_JUEZ
    for etiqueta in etiquetas:
        caso = juez.caso_de(etiqueta, indice)
        assert etiqueta.respuesta not in prompt, etiqueta.id
        assert caso.criterio not in prompt, etiqueta.id
        assert caso.pregunta not in prompt, etiqueta.id


# ------------------------------------------------------------------ lo que recibe el juez


def _caso(**campos):
    base = {"pregunta": "pregunta de ensayo", "criterio": "criterio de ensayo", "referencia": "referencia de ensayo"}
    return juez.CasoDeJuicio(**{**base, "respuesta": "respuesta de ensayo", **campos})


def test_el_juez_recibe_la_pregunta_el_criterio_la_referencia_y_la_respuesta_por_separado():
    texto = juez.armar_mensaje(_caso())
    for etiqueta, contenido in (
        ("pregunta", "pregunta de ensayo"),
        ("criterio", "criterio de ensayo"),
        ("referencia", "referencia de ensayo"),
        ("respuesta", "respuesta de ensayo"),
    ):
        assert f"<{etiqueta}>\n{contenido}\n</{etiqueta}>" in texto
    assert "turnos_anteriores" not in texto


def test_el_juez_recibe_los_turnos_anteriores_de_la_conversacion():
    texto = juez.armar_mensaje(_caso(anteriores=(("primera pregunta", "primera respuesta"),)))
    assert "<turnos_anteriores>" in texto
    assert "primera pregunta" in texto
    assert "primera respuesta" in texto


@pytest.mark.parametrize("cierre", ["</respuesta>", "</RESPUESTA >", "<criterio>", "</turnos_anteriores>"])
def test_una_respuesta_no_puede_salirse_de_su_etiqueta(cierre):
    """Si pudiera cerrar la etiqueta, lo que sigue se leería como una instrucción y no como lo que se califica."""
    texto = juez.armar_mensaje(_caso(respuesta=f"antes {cierre} después"))
    tramo = texto.split("<respuesta>\n", maxsplit=1)[1]
    assert tramo.count("</respuesta>") == 1
    assert cierre not in tramo.replace("\n</respuesta>", "")
    assert texto.count("<criterio>") == 1


def test_el_prompt_del_juez_dice_que_la_respuesta_es_dato():
    assert "dato" in juez.PROMPT_JUEZ
    assert "<respuesta>" in juez.PROMPT_JUEZ


def test_la_salida_no_valida_cuenta_como_sin_veredicto_y_no_como_acuerdo(etiquetas, carpeta, capsys):
    """Un juez que no contesta no coincide con nadie: el caso cuenta en el denominador."""

    def sin_json(mensajes, info):
        return ModelResponse(parts=[TextPart("no es un veredicto")], usage=RequestUsage(input_tokens=5, output_tokens=5))

    salida = lanzar(carpeta, "--corridas", "1", modelo=FunctionModel(sin_json, model_name="roto"))
    assert salida == juez.SALIDA_BAJO_EL_UMBRAL
    corrida = leer_reporte(carpeta)["corridas"][0]
    assert corrida["acuerdos"] == 0
    assert corrida["n"] == 20
    assert len(corrida["sin_veredicto"]) == 20


# ------------------------------------------------------------------ el acuerdo


def test_con_18_de_20_el_acuerdo_es_90_y_el_juez_queda_validado(etiquetas, carpeta, capsys):
    """El criterio de T5.4: 90 % o más, con un comando, en las etiquetas que no se usaron para ajustar."""
    invertir = [e.id for e in _de(etiquetas, "validacion")][:2]
    salida = lanzar(carpeta, modelo=Guion(etiquetas, invertir).modelo)
    assert salida == juez.SALIDA_BIEN
    reporte = leer_reporte(carpeta)
    assert reporte["acuerdo_minimo"] == ACUERDO_DEL_CRITERIO
    assert reporte["alcanza"] is True
    assert [corrida["acuerdos"] for corrida in reporte["corridas"]] == [18, 18]
    assert [corrida["n"] for corrida in reporte["corridas"]] == [20, 20]
    assert juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado
    assert "90.0 %" in capsys.readouterr().out


def test_con_17_de_20_no_alcanza_y_el_juez_no_queda_validado(etiquetas, carpeta):
    invertir = [e.id for e in _de(etiquetas, "validacion")][:3]
    salida = lanzar(carpeta, modelo=Guion(etiquetas, invertir).modelo)
    assert salida == juez.SALIDA_BAJO_EL_UMBRAL
    assert leer_reporte(carpeta)["acuerdo_minimo"] == "0.85"
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado


@pytest.mark.parametrize("aprueba", [True, False])
def test_un_juez_constante_da_50_y_no_se_valida(carpeta, aprueba):
    modelo = FunctionModel(lambda mensajes, info: _responder(aprueba), model_name="constante")
    assert lanzar(carpeta, modelo=modelo) == juez.SALIDA_BAJO_EL_UMBRAL
    assert leer_reporte(carpeta)["acuerdo_minimo"] == "0.50"


def test_cuenta_por_separado_lo_que_aprobo_de_mas_y_lo_que_reprobo_de_mas(etiquetas, carpeta):
    validacion = _de(etiquetas, "validacion")
    reprueba = next(e.id for e in validacion if e.etiqueta == "reprueba")
    aprueba = next(e.id for e in validacion if e.etiqueta == "aprueba")
    lanzar(carpeta, "--corridas", "1", modelo=Guion(etiquetas, [reprueba, aprueba]).modelo)
    corrida = leer_reporte(carpeta)["corridas"][0]
    assert corrida["aprobo_de_mas"] == [reprueba]
    assert corrida["reprobo_de_mas"] == [aprueba]


def test_la_corrida_peor_decide(etiquetas, carpeta):
    """El modelo no da siempre lo mismo: una corrida buena no valida a un juez que en la otra se queda corto."""
    validacion = [e.id for e in _de(etiquetas, "validacion")]
    guion = Guion(etiquetas)
    original = guion._contestar

    def segunda_corrida_peor(mensajes, info):
        guion.invertir = set(validacion[:3]) if len(guion.recibidos) >= len(validacion) else set()
        return original(mensajes, info)

    salida = lanzar(carpeta, modelo=FunctionModel(segunda_corrida_peor, model_name="guion"))
    assert salida == juez.SALIDA_BAJO_EL_UMBRAL
    reporte = leer_reporte(carpeta)
    assert [corrida["acuerdos"] for corrida in reporte["corridas"]] == [20, 17]
    assert reporte["acuerdo_minimo"] == "0.85"
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado


def test_89_66_por_ciento_no_pasa_por_redondeo():
    casos = [{"id": f"J-{numero}", "etiqueta": "aprueba", "veredictos": ["aprueba"]} for numero in range(26)]
    casos += [{"id": f"J-{numero}", "etiqueta": "aprueba", "veredictos": ["reprueba"]} for numero in range(26, 29)]
    cuenta = juez.contar_corrida(casos, 0)
    assert cuenta["acuerdos"] == 26
    assert cuenta["acuerdo"] == "0.89"
    assert not juez._alcanza(cuenta["acuerdos"], cuenta["n"])


def test_el_intervalo_dice_cuanto_pesa_una_n_de_20():
    bajo, alto = juez.intervalo_wilson(18, 20)
    assert round(bajo, 3) == 0.699
    assert round(alto, 3) == 0.972


def test_por_omision_solo_ven_al_juez_las_de_validacion_y_dos_veces(etiquetas, carpeta):
    guion = Guion(etiquetas)
    lanzar(carpeta, modelo=guion.modelo)
    assert len(guion.recibidos) == 40
    for etiqueta in _de(etiquetas, "ajuste"):
        assert not any(juez._dato(etiqueta.respuesta) in recibido for recibido in guion.recibidos), etiqueta.id


def test_el_reporte_guarda_cada_caso_para_recalcular_el_acuerdo_a_mano(etiquetas, carpeta):
    """K33: el evaluador recalcula el acuerdo en 10 casos con el id, la etiqueta y el veredicto."""
    invertir = [e.id for e in _de(etiquetas, "validacion")][:1]
    lanzar(carpeta, modelo=Guion(etiquetas, invertir).modelo)
    reporte = leer_reporte(carpeta)
    casos = reporte["casos"]
    assert len(casos) == 20
    primera = sum(caso["veredictos"][0] == caso["etiqueta"] for caso in casos)
    assert primera == reporte["corridas"][0]["acuerdos"] == 19
    tabla = next(carpeta.glob("validacion-*.md")).read_text(encoding="utf-8")
    for caso in casos:
        assert caso["id"] in tabla


def test_el_reporte_lleva_commit_modelo_hashes_tokens_y_costo(etiquetas, carpeta):
    lanzar(carpeta, modelo=Guion(etiquetas).modelo)
    reporte = leer_reporte(carpeta)
    assert reporte["modelo"] == NOMBRE_GUION
    assert reporte["prompt_sha256"] == juez.PROMPT_JUEZ_SHA256
    assert len(reporte["etiquetas_sha256"]) == 64
    assert "commit" in reporte
    assert reporte["uso"]["llamadas"] == 40
    assert reporte["uso"]["entrada_tokens"] == 4000
    assert reporte["uso"]["salida_tokens"] == 400


def test_el_reporte_compara_contra_las_reglas_solas(etiquetas, carpeta):
    lanzar(carpeta, modelo=Guion(etiquetas).modelo)
    base = leer_reporte(carpeta)["reglas_solas"]
    assert base["n"] == 20
    assert base["acuerdos"] < 18


def test_el_motivo_del_juez_se_guarda_sin_datos_de_contacto(etiquetas, carpeta):
    telefono = "81.0199.0042"
    modelo = FunctionModel(lambda mensajes, info: _responder(True, f"llama al {telefono}"), model_name="fuga")
    lanzar(carpeta, "--corridas", "1", modelo=modelo)
    for archivo in carpeta.iterdir():
        assert telefono not in archivo.read_text(encoding="utf-8"), archivo.name


# ------------------------------------------------------------------ ajuste y muestras no validan


def test_el_conjunto_de_ajuste_no_escribe_validacion(etiquetas, carpeta, capsys):
    guion = Guion(etiquetas)
    assert lanzar(carpeta, "--conjunto", "ajuste", modelo=guion.modelo) == juez.SALIDA_BIEN
    assert len(guion.recibidos) == 10
    assert not list(carpeta.glob("validacion-*")) if carpeta.exists() else True
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado
    assert "100.0 %" in capsys.readouterr().out


def test_el_evaluador_puede_recalcular_en_10_casos_elegidos(etiquetas, carpeta, capsys):
    """La prueba de C5.2: recalcular el acuerdo en 10 casos del archivo. No cuenta como validación."""
    elegidos = [e.id for e in etiquetas][5:15]
    guion = Guion(etiquetas)
    assert lanzar(carpeta, "--casos", ",".join(elegidos), modelo=guion.modelo) == juez.SALIDA_BIEN
    assert len(guion.recibidos) == 10
    salida = capsys.readouterr().out
    assert "10 de 10" in salida
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado


def test_un_caso_que_no_existe_se_rechaza_sin_llamar_al_modelo(etiquetas, carpeta, capsys):
    guion = Guion(etiquetas)
    with pytest.raises(SystemExit) as salida:
        lanzar(carpeta, "--casos", "J-99", modelo=guion.modelo)
    assert salida.value.code == 2
    assert "J-99" in capsys.readouterr().err
    assert not guion.recibidos


# ------------------------------------------------------------------ sin esta validación el juez no se usa


def _validar(carpeta, etiquetas):
    assert lanzar(carpeta, modelo=Guion(etiquetas).modelo) == juez.SALIDA_BIEN


def _reescribir(carpeta, cambio):
    ruta = next(carpeta.glob("validacion-*.json"))
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    cambio(datos)
    ruta.write_text(json.dumps(datos), encoding="utf-8")


def test_sin_reporte_no_esta_validado(carpeta):
    estado = juez.estado_de_validacion(NOMBRE_GUION, carpeta)
    assert not estado.validado
    assert estado.motivo


def test_una_sola_corrida_no_valida_aunque_coincida_en_todo(carpeta, etiquetas):
    """El modelo no siempre da lo mismo: con una corrida no se sabe si el acuerdo se reproduce."""
    salida = lanzar(carpeta, "--corridas", "1", modelo=Guion(etiquetas).modelo)
    assert salida == juez.SALIDA_BAJO_EL_UMBRAL
    assert leer_reporte(carpeta)["acuerdo_minimo"] == "1.00"
    estado = juez.estado_de_validacion(NOMBRE_GUION, carpeta)
    assert not estado.validado
    assert "corridas" in estado.motivo


def test_un_reporte_ilegible_no_valida(carpeta, etiquetas):
    _validar(carpeta, etiquetas)
    next(carpeta.glob("validacion-*.json")).write_text("{no es json", encoding="utf-8")
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado


def test_otro_modelo_no_hereda_la_validacion(carpeta, etiquetas):
    _validar(carpeta, etiquetas)
    assert not juez.estado_de_validacion("function:otro", carpeta).validado


def test_un_reporte_de_otro_modelo_copiado_a_este_nombre_no_valida(carpeta, etiquetas):
    _validar(carpeta, etiquetas)
    _reescribir(carpeta, lambda datos: datos.update(modelo="function:otro"))
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado


def test_otro_prompt_no_hereda_la_validacion(carpeta, etiquetas, monkeypatch):
    _validar(carpeta, etiquetas)
    monkeypatch.setattr(juez, "PROMPT_JUEZ_SHA256", "0" * 64)
    estado = juez.estado_de_validacion(NOMBRE_GUION, carpeta)
    assert not estado.validado
    assert "cambió" in estado.motivo


def test_cambiar_una_etiqueta_de_validacion_invalida_y_una_de_ajuste_no(carpeta, etiquetas, tmp_path):
    _validar(carpeta, etiquetas)
    texto = juez.ETIQUETAS.read_text(encoding="utf-8")

    ajuste = texto.replace("Contesta que sí, cita el procedimiento", "Contesta que sí y cita el procedimiento")
    assert ajuste != texto
    ruta_ajuste = tmp_path / "ajuste.yaml"
    ruta_ajuste.write_text(ajuste, encoding="utf-8")
    assert juez.estado_de_validacion(NOMBRE_GUION, carpeta, ruta_etiquetas=ruta_ajuste).validado

    validacion = texto.replace("Da las 48 horas y que ya pasaron", "Da las 48 horas y dice que ya pasaron")
    assert validacion != texto
    ruta_validacion = tmp_path / "validacion.yaml"
    ruta_validacion.write_text(validacion, encoding="utf-8")
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta, ruta_etiquetas=ruta_validacion).validado


def test_el_acuerdo_se_recalcula_de_los_casos_y_no_se_lee_del_reporte(carpeta, etiquetas):
    """Un reporte editado a mano, o de una versión con otra cuenta, no valida por decir 1.00."""
    _validar(carpeta, etiquetas)

    def cambiar_tres_veredictos(datos):
        datos["acuerdo_minimo"] = "1.00"
        datos["alcanza"] = True
        for caso in datos["casos"][:3]:
            caso["veredictos"] = ["sin_veredicto", "sin_veredicto"]

    _reescribir(carpeta, cambiar_tres_veredictos)
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado


def test_un_reporte_al_que_le_faltan_casos_no_valida(carpeta, etiquetas):
    _validar(carpeta, etiquetas)
    _reescribir(carpeta, lambda datos: datos["casos"].pop())
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado


def test_validar_con_otro_prompt_sobre_las_mismas_etiquetas_las_gasta(etiquetas, carpeta, monkeypatch, capsys):
    """Si la validación falla y se corrige el prompt, esas etiquetas ya ajustaron al juez: el acuerdo no vale."""
    invertir = [e.id for e in _de(etiquetas, "validacion")][:4]
    assert lanzar(carpeta, modelo=Guion(etiquetas, invertir).modelo) == juez.SALIDA_BAJO_EL_UMBRAL
    monkeypatch.setattr(juez, "PROMPT_JUEZ_SHA256", "1" * 64)
    capsys.readouterr()
    assert lanzar(carpeta, modelo=Guion(etiquetas).modelo) == juez.SALIDA_BAJO_EL_UMBRAL
    reporte = leer_reporte(carpeta)
    assert reporte["acuerdo_minimo"] == "1.00"
    assert reporte["etiquetas_gastadas"] is True
    assert reporte["alcanza"] is False
    estado = juez.estado_de_validacion(NOMBRE_GUION, carpeta)
    assert not estado.validado
    assert "gastadas" in estado.motivo
    assert "gastadas" in capsys.readouterr().out


def test_repetir_con_el_mismo_prompt_no_gasta_las_etiquetas_pero_se_cuenta(etiquetas, carpeta):
    _validar(carpeta, etiquetas)
    _validar(carpeta, etiquetas)
    reporte = leer_reporte(carpeta)
    assert reporte["etiquetas_gastadas"] is False
    assert reporte["validaciones_con_este_prompt"] == 2


def test_una_prueba_de_ajuste_no_cuenta_como_intento_de_validacion(etiquetas, carpeta, monkeypatch):
    lanzar(carpeta, "--conjunto", "ajuste", modelo=Guion(etiquetas).modelo)
    monkeypatch.setattr(juez, "PROMPT_JUEZ_SHA256", "2" * 64)
    assert lanzar(carpeta, modelo=Guion(etiquetas).modelo) == juez.SALIDA_BIEN


# ------------------------------------------------------------------ las fallas del modelo


def _sin_servicio(mensajes, info):
    raise ModelHTTPError(status_code=401, model_name="caido", body="llave rechazada")


def test_sin_modelo_se_detiene_sin_reporte_y_sin_gastar_intento(etiquetas, carpeta, capsys):
    """Un 401 o un 429 en cadena no dicen nada del juez: no es un desacuerdo."""
    salida = lanzar(carpeta, modelo=FunctionModel(_sin_servicio, model_name="caido"))
    assert salida == juez.SALIDA_SIN_MODELO
    assert not carpeta.exists() or not list(carpeta.iterdir())
    assert "401" not in capsys.readouterr().out


def test_un_id_de_modelo_sin_llave_es_una_falla_de_configuracion(carpeta, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert lanzar(carpeta, modelo="anthropic:claude-haiku-5-5") == juez.SALIDA_SIN_MODELO
    assert "configuracion" in capsys.readouterr().err


# ------------------------------------------------------------------ lo que corre sin llave


def test_sin_modelo_da_la_linea_base_de_las_reglas(carpeta, capsys):
    assert juez.main(["--sin-modelo"], dir_reportes=carpeta) == juez.SALIDA_BIEN
    salida = capsys.readouterr().out
    assert "de 20" in salida
    assert not carpeta.exists()


def test_el_comando_de_estado_no_llama_al_modelo(etiquetas, carpeta, capsys):
    assert lanzar(carpeta, "--estado", modelo=Guion(etiquetas).modelo) == juez.SALIDA_BAJO_EL_UMBRAL
    assert "no está validado" in capsys.readouterr().out
    _validar(carpeta, etiquetas)
    capsys.readouterr()
    guion = Guion(etiquetas)
    assert lanzar(carpeta, "--estado", modelo=guion.modelo) == juez.SALIDA_BIEN
    assert "validado" in capsys.readouterr().out
    assert not guion.recibidos


def test_el_comando_anuncia_cuantas_llamadas_va_a_hacer_antes_de_hacerlas(etiquetas, carpeta, capsys):
    lanzar(carpeta, modelo=Guion(etiquetas).modelo)
    assert re.search(r"40 llamadas", capsys.readouterr().out)


# ------------------------------------------------------------------ el evaluador para el comando del set


def _pregunta(**campos):
    base = {
        "id": "X-01",
        "prompt": "pregunta de ensayo",
        "categoria": "cruce",
        "tipo_respuesta": "cita",
        "estado_esperado": "ok",
        "respuesta_esperada": "lo que debe decir",
        "cifras_esperadas": (),
        "secciones_esperadas": (),
        "herramientas_esperadas": (),
        "parametros_esperados": {},
        "criterio": "Nombra el conflicto.",
        "negativa": False,
    }
    return banco.Pregunta(**{**base, **campos})


def _turno(texto="una respuesta cualquiera", **campos):
    base = {"pregunta": "pregunta de ensayo", "estado": "ok", "texto": texto, "texto_mostrado": texto, "modelo": "test"}
    return TurnoEvaluado(**{**base, **campos})


def _prompt(*preguntas, sesiones=None):
    pasos = tuple(banco.Paso(pregunta.prompt, 1, (pregunta,)) for pregunta in preguntas)
    return banco.Prompt(id="X", bloque="conversacion", pasos=pasos, sesiones=sesiones)


def _evaluar(prompt, turnos, evaluador):
    def tarea(_):
        return ResultadoPrompt(turnos=turnos)

    conjunto = Dataset(name="ensayo", cases=[Case(name=prompt.id, inputs=prompt)], evaluators=[evaluador])
    return conjunto.evaluate_sync(tarea, max_concurrency=1, progress=False).cases[0]


class Contador:
    def __init__(self, aprueba=True):
        self.recibidos = []
        self.aprueba = aprueba
        self.modelo = FunctionModel(self._contestar, model_name="guion")

    def _contestar(self, mensajes, info):
        self.recibidos.append(_texto_del_usuario(mensajes))
        return _responder(self.aprueba, "motivo del contador")


def test_sin_validar_el_evaluador_no_llama_al_modelo_ni_califica(carpeta):
    """Lo que pide T5.4: sin esta validación el juez no se usa."""
    contador = Contador(aprueba=False)
    caso = _evaluar(_prompt(_pregunta()), (_turno(),), juez.CriterioDelJuez(modelo=contador.modelo, dir_reportes=carpeta))
    assert not contador.recibidos
    assert "CriterioDelJuez" not in caso.assertions
    assert not caso.evaluator_failures


@pytest.fixture
def validado(carpeta, etiquetas):
    _validar(carpeta, etiquetas)
    return carpeta


@pytest.mark.parametrize("aprueba", [True, False])
def test_validado_el_evaluador_califica_con_el_veredicto(validado, aprueba):
    contador = Contador(aprueba)
    evaluador = juez.CriterioDelJuez(modelo=contador.modelo, dir_reportes=validado)
    caso = _evaluar(_prompt(_pregunta()), (_turno(),), evaluador)
    assert len(contador.recibidos) == 1
    assert caso.assertions["CriterioDelJuez"].value is aprueba
    if not aprueba:
        assert "motivo del contador" in caso.assertions["CriterioDelJuez"].reason


def test_no_gasta_una_llamada_si_las_reglas_ya_reprobaron_o_el_criterio_es_solo_la_cifra(validado):
    contador = Contador()
    evaluador = juez.CriterioDelJuez(modelo=contador.modelo, dir_reportes=validado)
    con_cifra_mala = _pregunta(id="X-02", cifras_esperadas=("123.45",))
    solo_cifra = _pregunta(id="X-03", criterio="Cifra exacta al centavo.")
    otro_estado = _pregunta(id="X-04")
    turnos = (_turno(), _turno(), _turno(estado="tope", tope="segundos"))
    caso = _evaluar(_prompt(con_cifra_mala, solo_cifra, otro_estado), turnos, evaluador)
    assert not contador.recibidos
    assert "CriterioDelJuez" not in caso.assertions


def test_en_una_conversacion_recibe_los_turnos_anteriores_de_su_sesion(validado):
    contador = Contador()
    evaluador = juez.CriterioDelJuez(modelo=contador.modelo, dir_reportes=validado)
    primera = _pregunta(id="X-05", prompt="turno uno", criterio="Cifra exacta al centavo.")
    segunda = _pregunta(id="X-06", prompt="turno dos")
    otra = _pregunta(id="X-07", prompt="turno de la otra sesión", criterio="Cifra exacta al centavo.")
    pasos = (
        banco.Paso("turno de la otra sesión", 2, (otra,)),
        banco.Paso("turno uno", 1, (primera,)),
        banco.Paso("turno dos", 1, (segunda,)),
    )
    prompt = banco.Prompt(id="X", bloque="conversacion", pasos=pasos, sesiones=2)
    turnos = (_turno("respuesta de la otra"), _turno("respuesta uno"), _turno("respuesta dos"))
    _evaluar(prompt, turnos, evaluador)
    assert len(contador.recibidos) == 1
    assert "respuesta uno" in contador.recibidos[0]
    assert "respuesta de la otra" not in contador.recibidos[0]


def test_un_turno_sin_veredicto_reprueba_y_lo_dice(validado):
    def sin_json(mensajes, info):
        return ModelResponse(parts=[TextPart("no es un veredicto")])

    evaluador = juez.CriterioDelJuez(modelo=FunctionModel(sin_json, model_name="guion"), dir_reportes=validado)
    caso = _evaluar(_prompt(_pregunta()), (_turno(),), evaluador)
    assert caso.assertions["CriterioDelJuez"].value is False
    assert "sin veredicto" in caso.assertions["CriterioDelJuez"].reason


# ------------------------------------------------------------------ la solicitud que sale a cada proveedor, sin red


class ProveedorSimulado:
    """Contesta por la capa de transporte con lo que el modo de salida pidió, y anota el cuerpo que recibió."""

    def __init__(self):
        self.cuerpos = []

    def anthropic(self):
        transporte = httpx2.MockTransport(self._de_claude)
        cliente = AsyncAnthropic(api_key=LLAVE_DE_PRUEBA, http_client=httpx2.AsyncClient(transport=transporte))
        return AnthropicModel("claude-haiku-5-5", provider=AnthropicProvider(anthropic_client=cliente))

    def gemini(self):
        cliente = httpx2.AsyncClient(transport=httpx2.MockTransport(self._de_gemini))
        return GoogleModel("gemini-3.5-flash-lite", provider=GoogleProvider(api_key=LLAVE_DE_PRUEBA, http_client=cliente))

    def _de_claude(self, solicitud):
        cuerpo = json.loads(solicitud.content)
        self.cuerpos.append(cuerpo)
        contenido = [{"type": "text", "text": json.dumps({"aprueba": False, "motivo": "falta el conflicto"})}]
        return httpx2.Response(
            200,
            json={
                "id": "msg_01",
                "type": "message",
                "role": "assistant",
                "model": cuerpo["model"],
                "content": contenido,
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 700, "output_tokens": 60},
            },
        )

    def _de_gemini(self, solicitud):
        cuerpo = json.loads(solicitud.content)
        self.cuerpos.append(cuerpo)
        argumentos = {"aprueba": False, "motivo": "falta el conflicto"}
        declaradas = [f["name"] for bloque in cuerpo.get("tools", []) for f in bloque.get("functionDeclarations", [])]
        parte = {"functionCall": {"name": declaradas[0], "args": argumentos}} if declaradas else {"text": json.dumps(argumentos)}
        return httpx2.Response(
            200,
            json={
                "candidates": [{"content": {"role": "model", "parts": [parte]}, "finishReason": "STOP", "index": 0}],
                "usageMetadata": {"promptTokenCount": 700, "candidatesTokenCount": 60, "totalTokenCount": 760},
                "modelVersion": "gemini-3.5-flash-lite",
                "responseId": "abc",
            },
        )


def _juzgar_una_vez(modelo):
    return asyncio.run(juez.juzgar(_caso(), modelo))


def test_con_haiku_el_veredicto_sale_por_esquema_sin_forzar_herramienta_y_con_el_prompt_del_juez(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    proveedor = ProveedorSimulado()
    juicio = _juzgar_una_vez(proveedor.anthropic())
    assert juicio.aprueba is False
    assert juicio.motivo == "falta el conflicto"
    cuerpo = proveedor.cuerpos[0]
    assert "tool_choice" not in cuerpo
    assert "tools" not in cuerpo
    assert cuerpo["output_config"]["format"]["type"] == "json_schema"
    assert cuerpo["output_config"]["effort"] == "low"
    assert cuerpo["thinking"]["type"] == "adaptive"
    assert cuerpo["max_tokens"] == juez.MAX_TOKENS_DEL_JUEZ
    assert "temperature" not in cuerpo
    assert "cache_control" not in json.dumps(cuerpo)
    assert juez.PROMPT_JUEZ.strip() in "\n".join(bloque["text"] for bloque in cuerpo["system"])
    assert juicio.llamadas[0].entrada_tokens == 700


def test_con_gemini_el_veredicto_tambien_se_lee(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    proveedor = ProveedorSimulado()
    juicio = _juzgar_una_vez(proveedor.gemini())
    assert juicio.aprueba is False
    assert juicio.motivo == "falta el conflicto"
    assert juicio.llamadas[0].entrada_tokens == 700


# ------------------------------------------------------------------ T5.9: la compuerta


def _ruta_intentos(carpeta):
    return carpeta / juez.ARCHIVO_INTENTOS


def _lineas_de_intentos(carpeta):
    return [json.loads(linea) for linea in _ruta_intentos(carpeta).read_text(encoding="utf-8").splitlines() if linea]


def test_sin_la_linea_en_la_bitacora_un_reporte_no_valida(carpeta, etiquetas):
    """Borrar la bitácora no vuelve a validar nada: el reporte tiene que tener la línea que le corresponde."""
    _validar(carpeta, etiquetas)
    assert juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado
    _ruta_intentos(carpeta).unlink()
    estado = juez.estado_de_validacion(NOMBRE_GUION, carpeta)
    assert not estado.validado
    assert "bitácora" in estado.motivo


def test_una_linea_de_otra_validacion_no_le_corresponde_al_reporte(carpeta, etiquetas):
    _validar(carpeta, etiquetas)
    _reescribir(carpeta, lambda datos: datos.update(acuerdo_minimo="0.95"))
    assert "bitácora" in juez.estado_de_validacion(NOMBRE_GUION, carpeta).motivo


def test_un_reporte_marcado_como_gastado_no_valida_aunque_se_borre_la_bitacora(carpeta, etiquetas, monkeypatch):
    invertir = [e.id for e in _de(etiquetas, "validacion")][:4]
    lanzar(carpeta, modelo=Guion(etiquetas, invertir).modelo)
    monkeypatch.setattr(juez, "PROMPT_JUEZ_SHA256", "1" * 64)
    lanzar(carpeta, modelo=Guion(etiquetas).modelo)
    assert leer_reporte(carpeta)["etiquetas_gastadas"] is True
    _ruta_intentos(carpeta).unlink()
    estado = juez.estado_de_validacion(NOMBRE_GUION, carpeta)
    assert not estado.validado
    assert "gastadas" in estado.motivo


def test_un_reporte_que_no_alcanzo_no_valida_aunque_sus_casos_digan_otra_cosa(carpeta, etiquetas):
    """El reporte marcado como no alcanza se rechaza por sí mismo, aunque alguien le corrija los veredictos."""
    invertir = [e.id for e in _de(etiquetas, "validacion")][:4]
    lanzar(carpeta, modelo=Guion(etiquetas, invertir).modelo)

    def corregir_los_veredictos(datos):
        for caso in datos["casos"]:
            caso["veredictos"] = [caso["etiqueta"], caso["etiqueta"]]

    _reescribir(carpeta, corregir_los_veredictos)
    estado = juez.estado_de_validacion(NOMBRE_GUION, carpeta)
    assert not estado.validado
    assert "no alcanzó" in estado.motivo


def test_casos_con_etiquetas_de_validacion_deja_un_intento_con_su_prompt(etiquetas, carpeta):
    elegidos = [e.id for e in _de(etiquetas, "validacion")][:2]
    lanzar(carpeta, "--casos", ",".join(elegidos), modelo=Guion(etiquetas).modelo)
    linea = _lineas_de_intentos(carpeta)[-1]
    assert linea["prompt_sha256"] == juez.PROMPT_JUEZ_SHA256
    assert linea["etiquetas_sha256"] == juez.huella_de_validacion(etiquetas, juez.indice_del_banco())
    assert linea["casos"] == elegidos
    assert linea["modelo"] == NOMBRE_GUION


def test_ajustar_el_prompt_despues_de_ver_etiquetas_de_validacion_con_casos_las_gasta(etiquetas, carpeta, monkeypatch):
    elegido = _de(etiquetas, "validacion")[0].id
    lanzar(carpeta, "--casos", elegido, modelo=Guion(etiquetas).modelo)
    monkeypatch.setattr(juez, "PROMPT_JUEZ_SHA256", "3" * 64)
    assert lanzar(carpeta, modelo=Guion(etiquetas).modelo) == juez.SALIDA_BAJO_EL_UMBRAL
    assert leer_reporte(carpeta)["etiquetas_gastadas"] is True


def test_casos_con_el_mismo_prompt_no_gasta_las_etiquetas_ni_cuenta_como_validacion(etiquetas, carpeta):
    lanzar(carpeta, "--casos", _de(etiquetas, "validacion")[0].id, modelo=Guion(etiquetas).modelo)
    assert not juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado
    _validar(carpeta, etiquetas)
    reporte = leer_reporte(carpeta)
    assert reporte["etiquetas_gastadas"] is False
    assert reporte["validaciones_con_este_prompt"] == 1
    assert juez.estado_de_validacion(NOMBRE_GUION, carpeta).validado


def test_la_linea_de_una_muestra_no_le_sirve_a_un_reporte_de_validacion(etiquetas, carpeta):
    _validar(carpeta, etiquetas)
    lanzar(carpeta, "--casos", _de(etiquetas, "validacion")[0].id, modelo=Guion(etiquetas).modelo)
    lineas = _lineas_de_intentos(carpeta)
    _ruta_intentos(carpeta).write_text(json.dumps(lineas[-1]) + "\n", encoding="utf-8")
    assert "bitácora" in juez.estado_de_validacion(NOMBRE_GUION, carpeta).motivo


def test_casos_solo_con_etiquetas_de_ajuste_no_deja_intento(etiquetas, carpeta):
    lanzar(carpeta, "--casos", _de(etiquetas, "ajuste")[0].id, modelo=Guion(etiquetas).modelo)
    assert not _ruta_intentos(carpeta).exists()


@pytest.mark.parametrize("argumentos", [[], ["--conjunto", "ajuste"], ["--casos", "J-11"]])
def test_con_la_bitacora_corrupta_no_se_paga_ninguna_llamada(etiquetas, carpeta, capsys, argumentos):
    carpeta.mkdir(parents=True)
    _ruta_intentos(carpeta).write_text("{no es json\n", encoding="utf-8")
    guion = Guion(etiquetas)
    assert lanzar(carpeta, *argumentos, modelo=guion.modelo) == juez.SALIDA_BITACORA_ILEGIBLE
    assert not guion.recibidos
    assert juez.ARCHIVO_INTENTOS in capsys.readouterr().err


def test_con_la_bitacora_sin_sus_campos_tampoco_se_paga_ninguna_llamada(etiquetas, carpeta):
    carpeta.mkdir(parents=True)
    _ruta_intentos(carpeta).write_text('{"fecha": "2026-11-02"}\n', encoding="utf-8")
    guion = Guion(etiquetas)
    assert lanzar(carpeta, modelo=guion.modelo) == juez.SALIDA_BITACORA_ILEGIBLE
    assert not guion.recibidos


@pytest.mark.parametrize("casos", ["", ",", " , "])
def test_casos_vacio_se_rechaza_y_no_corre_la_validacion_completa(etiquetas, carpeta, capsys, casos):
    guion = Guion(etiquetas)
    with pytest.raises(SystemExit) as salida:
        lanzar(carpeta, "--casos", casos, modelo=guion.modelo)
    assert salida.value.code == 2
    assert "--casos" in capsys.readouterr().err
    assert not guion.recibidos


@pytest.mark.parametrize("argumentos", [["--conjunto", "ajuste"], ["--casos", "J-01"]])
def test_una_muestra_sale_con_0_aunque_no_alcance_y_la_cabecera_lo_dice(etiquetas, carpeta, argumentos):
    invertir = [e.id for e in _de(etiquetas, "ajuste")]
    assert lanzar(carpeta, *argumentos, modelo=Guion(etiquetas, invertir).modelo) == juez.SALIDA_BIEN
    assert "--conjunto ajuste y --casos salen siempre con 0" in juez.__doc__
    assert "con 5" in juez.__doc__


def test_invertir_una_etiqueta_de_validacion_invalida_el_reporte(carpeta, etiquetas, tmp_path):
    _validar(carpeta, etiquetas)
    texto = juez.ETIQUETAS.read_text(encoding="utf-8")
    invertido = re.sub(
        r"(- id: J-11\n(?:.*\n)*?    etiqueta: )(aprueba|reprueba)",
        lambda m: m.group(1) + ("reprueba" if m.group(2) == "aprueba" else "aprueba"),
        texto,
        count=1,
    )
    assert invertido != texto
    ruta = tmp_path / "invertida.yaml"
    ruta.write_text(invertido, encoding="utf-8")
    estado = juez.estado_de_validacion(NOMBRE_GUION, carpeta, ruta_etiquetas=ruta)
    assert not estado.validado
    assert "etiquetas" in estado.motivo


def test_el_reporte_guardado_de_haiku_sigue_validado():
    """Lo que pide T5.9: ni el banco ni la compuerta invalidan la validación con el modelo real."""
    estado = juez.estado_de_validacion("anthropic:claude-haiku-5-5")
    assert estado.validado, estado.motivo


# ------------------------------------------------------------------ T5.9: el veredicto en la calificación del set


def _evaluar_con_registro(prompt, turnos, validado, modelo, veredictos, corrida=1):
    evaluador = juez.CriterioDelJuez(modelo=modelo, dir_reportes=validado, veredictos=veredictos, corrida=corrida)
    return _evaluar(prompt, turnos, evaluador)


def test_con_registro_un_veredicto_ilegible_no_reprueba_y_se_cuenta(validado):
    def sin_json(mensajes, info):
        return ModelResponse(parts=[TextPart("no es un veredicto")], usage=RequestUsage(input_tokens=5, output_tokens=5))

    veredictos = juez.Veredictos()
    modelo = FunctionModel(sin_json, model_name="guion")
    caso = _evaluar_con_registro(_prompt(_pregunta()), (_turno(),), validado, modelo, veredictos)
    assert "CriterioDelJuez" not in caso.assertions
    assert not caso.evaluator_failures
    guardado = veredictos.todos()[0]
    assert guardado.veredicto == juez.SIN_VEREDICTO
    assert guardado.entrada_tokens > 0
    assert guardado.falla_del_modelo is False


def test_con_registro_una_falla_del_modelo_no_reprueba_se_cuenta_y_no_se_guarda(validado):
    guardados = []
    veredictos = juez.Veredictos(persistir=guardados.append)
    modelo = FunctionModel(_sin_servicio, model_name="guion")
    caso = _evaluar_con_registro(_prompt(_pregunta()), (_turno(),), validado, modelo, veredictos)
    assert "CriterioDelJuez" not in caso.assertions
    assert not caso.evaluator_failures
    assert veredictos.todos()[0].veredicto == juez.SIN_VEREDICTO
    assert veredictos.todos()[0].falla_del_modelo is True
    assert not guardados
    assert "llave rechazada" not in veredictos.todos()[0].motivo


def test_con_registro_una_excepcion_inesperada_del_modelo_no_reprueba(validado):
    def truena(mensajes, info):
        raise ValueError("defecto")

    veredictos = juez.Veredictos()
    modelo = FunctionModel(truena, model_name="guion")
    caso = _evaluar_con_registro(_prompt(_pregunta()), (_turno(),), validado, modelo, veredictos)
    assert "CriterioDelJuez" not in caso.assertions
    assert not caso.evaluator_failures
    assert veredictos.todos()[0].motivo == "ValueError"


def test_con_registro_un_veredicto_guardado_no_se_vuelve_a_pedir(validado):
    guardados = []
    contador = Contador(aprueba=False)
    veredictos = juez.Veredictos(persistir=guardados.append)
    caso = _evaluar_con_registro(_prompt(_pregunta()), (_turno(),), validado, contador.modelo, veredictos)
    assert caso.assertions["CriterioDelJuez"].value is False
    assert len(contador.recibidos) == 1
    assert len(guardados) == 1

    segundo = Contador(aprueba=True)
    repetido = _evaluar_con_registro(_prompt(_pregunta()), (_turno(),), validado, segundo.modelo, juez.Veredictos(previos=guardados))
    assert not segundo.recibidos
    assert repetido.assertions["CriterioDelJuez"].value is False
    assert "motivo del contador" in repetido.assertions["CriterioDelJuez"].reason


def test_otra_corrida_no_reusa_el_veredicto_de_la_primera(validado):
    contador = Contador()
    veredictos = juez.Veredictos()
    _evaluar_con_registro(_prompt(_pregunta()), (_turno(),), validado, contador.modelo, veredictos, corrida=1)
    _evaluar_con_registro(_prompt(_pregunta()), (_turno(),), validado, contador.modelo, veredictos, corrida=2)
    assert len(contador.recibidos) == 2


def test_el_veredicto_guarda_prompt_corrida_turno_pregunta_y_uso(validado):
    contador = Contador(aprueba=False)
    veredictos = juez.Veredictos()
    primera = _pregunta(id="X-08", prompt="turno uno", criterio="Cifra exacta al centavo.")
    segunda = _pregunta(id="X-09", prompt="turno dos")
    turnos = (_turno("uno"), _turno("dos"))
    _evaluar_con_registro(_prompt(primera, segunda), turnos, validado, contador.modelo, veredictos, corrida=2)
    guardado = veredictos.todos()[0]
    assert (guardado.corrida, guardado.prompt, guardado.turno, guardado.pregunta) == (2, "X", 1, "X-09")
    assert guardado.veredicto == juez.REPRUEBA
    assert guardado.motivo == "motivo del contador"
    assert (guardado.n_llamadas, guardado.entrada_tokens, guardado.salida_tokens) == (1, 100, 10)


def test_un_compuesto_sin_signos_se_juzga_por_parte_con_el_criterio_de_cada_una(validado):
    contador = Contador()
    veredictos = juez.Veredictos()
    uno = _pregunta(id="X-10", criterio="Criterio de la parte uno.")
    dos = _pregunta(id="X-11", criterio="Criterio de la parte dos.")
    prompt = banco.Prompt(id="X", bloque="dos_normales", pasos=(banco.Paso("uno y dos", 1, (uno, dos)),), forma="sin_signos")
    _evaluar_con_registro(prompt, (_turno(),), validado, contador.modelo, veredictos)
    assert len(contador.recibidos) == 2
    assert "Criterio de la parte uno." in contador.recibidos[0]
    assert "Criterio de la parte dos." in contador.recibidos[1]
    assert [guardado.pregunta for guardado in veredictos.todos()] == ["X-10", "X-11"]


def test_el_resumen_de_la_validacion_da_el_acuerdo_y_su_intervalo(validado):
    resumen = juez.resumen_de_validacion(NOMBRE_GUION, validado)
    assert resumen["acuerdo_minimo"] == "1.00"
    assert [corrida["acuerdos"] for corrida in resumen["corridas"]] == [20, 20]
    bajo, alto = juez.intervalo_wilson(20, 20)
    assert resumen["corridas"][0]["intervalo_95"] == [round(bajo, 3), round(alto, 3)]


def test_sin_validacion_no_hay_resumen(carpeta):
    assert juez.resumen_de_validacion(NOMBRE_GUION, carpeta) is None


# ------------------------------------------------------------------ T5.9: lo que el motivo no puede guardar


def test_el_veredicto_guarda_el_motivo_sin_el_nombre_del_ticket(validado):
    """El motivo llega al avance y al reporte: si el juez repite un nombre, no puede pasar. Se lee del CSV al correr."""
    nombre = evaluadores.datos_personales_del_ticket(evaluadores.TICKETS_CON_DATOS_PERSONALES[0]).palabras[0]

    def repite(mensajes, info):
        return _responder(False, f"La respuesta nombra a {nombre}.")

    veredictos = juez.Veredictos()
    _evaluar_con_registro(_prompt(_pregunta()), (_turno(),), validado, FunctionModel(repite, model_name="guion"), veredictos)
    motivo = veredictos.todos()[0].motivo
    assert evaluadores.MARCA_PERSONA in motivo
    assert not evaluadores.hay_dato_del_ticket(evaluadores.TICKETS_CON_DATOS_PERSONALES[0], motivo)


# ------------------------------------------------------------------ T5.9: el criterio propio de un compuesto


CRITERIO_CON_CONDICION = "Una respuesta con las dos. Reprueba si cruza los datos de una con los de la otra."


def _compuesto_sin_signos(criterio=CRITERIO_CON_CONDICION, **de_la_parte_dos):
    uno = _pregunta(id="X-20", criterio="Criterio de la parte uno.", respuesta_esperada="referencia uno")
    dos = _pregunta(id="X-21", criterio="Criterio de la parte dos.", respuesta_esperada="referencia dos", **de_la_parte_dos)
    pasos = (banco.Paso("uno y dos", 1, (uno, dos)),)
    return banco.Prompt(id="X", bloque="dos_normales", pasos=pasos, forma="sin_signos", criterio=criterio)


def _compuesto_con_signos(criterio=CRITERIO_CON_CONDICION):
    uno = _pregunta(id="X-22", criterio="Criterio de la parte uno.", respuesta_esperada="referencia uno")
    dos = _pregunta(id="X-23", criterio="Criterio de la parte dos.", respuesta_esperada="referencia dos")
    pasos = (banco.Paso("¿uno?", 1, (uno,)), banco.Paso("¿dos?", 1, (dos,)))
    return banco.Prompt(id="X", bloque="dos_normales", pasos=pasos, forma="con_signos", criterio=criterio)


def test_un_compuesto_que_reprueba_algo_por_su_cuenta_se_juzga_ademas_de_sus_partes(validado):
    contador = Contador()
    veredictos = juez.Veredictos()
    _evaluar_con_registro(_compuesto_sin_signos(), (_turno("las dos"),), validado, contador.modelo, veredictos)
    assert len(contador.recibidos) == 3
    ultimo = contador.recibidos[2]
    assert CRITERIO_CON_CONDICION in ultimo
    assert "referencia uno" in ultimo
    assert "referencia dos" in ultimo
    assert "<pregunta>\nuno y dos\n</pregunta>" in ultimo
    assert [guardado.clave for guardado in veredictos.todos()][2] == (1, "X", 0, "X")


def test_un_compuesto_cuyo_criterio_solo_resume_sus_partes_no_gasta_otra_llamada(validado):
    contador = Contador()
    prompt = _compuesto_sin_signos(criterio="Una respuesta con las dos.")
    _evaluar_con_registro(prompt, (_turno("las dos"),), validado, contador.modelo, juez.Veredictos())
    assert len(contador.recibidos) == 2


def test_con_signos_el_juez_del_compuesto_ve_cada_turno_por_separado(validado):
    contador = Contador()
    turnos = (_turno("respuesta del primero"), _turno("respuesta del segundo"))
    _evaluar_con_registro(_compuesto_con_signos(), turnos, validado, contador.modelo, juez.Veredictos())
    assert len(contador.recibidos) == 3
    ultimo = contador.recibidos[2]
    assert "¿uno?\n¿dos?" in ultimo
    assert "Turno 1:\nrespuesta del primero" in ultimo
    assert "Turno 2:\nrespuesta del segundo" in ultimo


def test_el_veredicto_del_compuesto_reprueba_el_prompt(validado):
    class SoloElCompuesto(Contador):
        def _contestar(self, mensajes, info):
            texto = _texto_del_usuario(mensajes)
            self.recibidos.append(texto)
            return _responder(CRITERIO_CON_CONDICION not in texto, "cruzó los datos")

    contador = SoloElCompuesto()
    caso = _evaluar_con_registro(_compuesto_sin_signos(), (_turno("las dos"),), validado, contador.modelo, juez.Veredictos())
    assert caso.assertions["CriterioDelJuez"].value is False
    assert caso.assertions["CriterioDelJuez"].reason == "X: cruzó los datos"


def test_si_una_parte_ya_reprobo_las_reglas_el_compuesto_no_gasta_una_llamada(validado):
    contador = Contador()
    prompt = _compuesto_sin_signos(cifras_esperadas=("123.45",))
    _evaluar_con_registro(prompt, (_turno("las dos"),), validado, contador.modelo, juez.Veredictos())
    assert len(contador.recibidos) == 1
    assert CRITERIO_CON_CONDICION not in contador.recibidos[0]


def test_si_falta_el_turno_de_una_parte_el_compuesto_no_gasta_una_llamada(validado):
    contador = Contador()
    _evaluar_con_registro(_compuesto_con_signos(), (_turno("solo el primero"),), validado, contador.modelo, juez.Veredictos())
    assert len(contador.recibidos) == 1
    assert CRITERIO_CON_CONDICION not in contador.recibidos[0]
