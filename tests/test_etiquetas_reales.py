"""T5.13, D32-A: las etiquetas de validación son respuestas reales. Sin red y sin llaves.

Las 20 del primer intento quedaron gastadas en eval/etiquetas_juez_gastadas.yaml: se comprueba contra la línea base que cada
respuesta es lo que el juez recibió en la corrida, y contra la semilla que la selección es la que dice su cabecera. Las 20 de hoy se
comprueban contra el reporte que cada una nombra. Los textos se leen del reporte y del banco: ninguno se escribe aquí (G11).
"""

import json

import pytest
import yaml

from agente import privacidad
from eval import banco, elegir_etiquetas, evaluadores, juez
from eval.evaluadores import ResultadoPrompt, TurnoEvaluado
from tests.conftest import requiere_reportes

GASTADAS = banco.RAIZ / "eval" / "etiquetas_juez_gastadas.yaml"
REPORTES = banco.RAIZ / "eval" / "reportes"
REPORTE = REPORTES / "2026-11-02-completo-39d2639.json"
REVISION = banco.RAIZ / "eval" / "reportes" / "2026-11-02-completo-39d2639-revision.md"
CAMBIOS = banco.RAIZ / "eval" / "cambios_criterios.md"
# Los dudosos de la revisión y los que adjudicó con confianza media.
DUDOSOS = {(2, "PT-29"), (2, "PT-47"), (2, "DN-05.N-22"), (1, "PT-16"), (1, "PT-35"), (2, "PT-07")}
# Salen de los 166 aprobados que la revisión leyó fuera de su muestra: esos no tienen fila en sus tablas.
SIN_FILA_EN_LA_REVISION = {(2, "PT-18-2"), (1, "PT-08"), (2, "PT-08")}
LARGO_MEDIO_MINIMO = 500


def _endurecidos():
    """Los que el registro marca como endurecidos: la lista no se repite a mano aquí, para que no se quede atrás."""
    endurecidos = set()
    for renglon in CAMBIOS.read_text(encoding="utf-8").splitlines():
        celdas = [celda.strip() for celda in renglon.strip().strip("|").split("|")]
        if len(celdas) == 5 and celdas[3] == "endurece":
            endurecidos.add(celdas[0])
    return endurecidos


def _adjudicados():
    """(veredicto del juez viejo, adjudicación) de cada caso con fila en las tablas de la revisión, por corrida y pregunta."""
    adjudicados = {}
    columnas = None
    for renglon in REVISION.read_text(encoding="utf-8").splitlines():
        celdas = [celda.strip() for celda in renglon.strip().strip("|").split("|")]
        if {"Pregunta", "Corrida", "Veredicto", "Adjudicación"} <= set(celdas):
            columnas = {nombre: posicion for posicion, nombre in enumerate(celdas)}
        elif not renglon.startswith("|"):
            columnas = None
        elif columnas and celdas[columnas["Corrida"]].isdigit():
            clave = (int(celdas[columnas["Corrida"]]), celdas[columnas["Pregunta"]])
            adjudicados[clave] = (celdas[columnas["Veredicto"]], celdas[columnas["Adjudicación"]])
    return adjudicados


def _casos(*opciones):
    return {(corrida, pregunta) for grupo in opciones for pregunta, corridas in grupo.items() for corrida in corridas}


@pytest.fixture(scope="module")
def reporte():
    return json.loads(REPORTE.read_text(encoding="utf-8"))


def _casos_del_reporte(reporte):
    """Lo que el juez recibió en la corrida, por corrida y pregunta, reconstruido con el código que lo arma."""
    campos = set(TurnoEvaluado.model_fields)
    agrupados = {}
    for fila in reporte["turnos"]:
        turno = TurnoEvaluado(**{clave: valor for clave, valor in fila.items() if clave in campos})
        agrupados.setdefault((fila["corrida"], fila["prompt"]), []).append(turno)
    prompts = {prompt.id: prompt for prompt in banco.leer_banco()}
    casos = {}
    for (corrida, identificador), turnos in agrupados.items():
        for _, pregunta, caso in juez._casos_del_prompt(prompts[identificador], ResultadoPrompt(turnos=tuple(turnos))):
            casos[(corrida, pregunta)] = caso
    return casos


@pytest.fixture(scope="module")
def casos_de_la_corrida(reporte):
    return _casos_del_reporte(reporte)


@pytest.fixture(scope="module")
def veredictos_viejos(reporte):
    return {(v["corrida"], v["pregunta"]): v["veredicto"] for v in reporte["juez"]["veredictos"]}


@pytest.fixture(scope="module")
def entradas():
    """Las del primer intento tal como están en el archivo: su corrida y el veredicto viejo no entran a la huella."""
    return yaml.safe_load(GASTADAS.read_text(encoding="utf-8"))["etiquetas"]


@pytest.fixture(scope="module")
def etiquetas():
    return juez.leer_etiquetas(GASTADAS)


@requiere_reportes
def test_cada_respuesta_es_lo_que_el_juez_recibio_en_la_corrida(entradas, etiquetas, casos_de_la_corrida):
    indice = juez.indice_del_banco()
    for entrada, etiqueta in zip(entradas, etiquetas, strict=True):
        de_la_corrida = casos_de_la_corrida[(entrada["corrida"], entrada["pregunta"])]
        assert juez.caso_de(etiqueta, indice) == de_la_corrida, etiqueta.id


def test_la_seleccion_es_la_que_da_la_semilla(entradas):
    elegidos = elegir_etiquetas.elegir()
    assert [(e["corrida"], e["pregunta"], e["etiqueta"], e["juez_viejo"]) for e in entradas] == [
        (elegido.corrida, elegido.pregunta, elegido.etiqueta, elegido.juez_viejo) for elegido in elegidos
    ]


def test_la_semilla_cambia_la_seleccion():
    assert elegir_etiquetas.elegir(1) != elegir_etiquetas.elegir()


@requiere_reportes
def test_el_veredicto_viejo_de_cada_una_es_el_que_dice_el_archivo(entradas, veredictos_viejos):
    for entrada in entradas:
        coincidio = veredictos_viejos[(entrada["corrida"], entrada["pregunta"])] == entrada["etiqueta"]
        assert entrada["juez_viejo"] == (elegir_etiquetas.ACERTO if coincidio else elegir_etiquetas.FALLO), entrada["id"]


def test_mezcla_casos_donde_el_juez_viejo_acerto_y_donde_fallo(entradas):
    por_etiqueta = {
        (entrada["etiqueta"], entrada["juez_viejo"]) for entrada in entradas
    }
    assert por_etiqueta == {
        (juez.APRUEBA, elegir_etiquetas.ACERTO),
        (juez.APRUEBA, elegir_etiquetas.FALLO),
        (juez.REPRUEBA, elegir_etiquetas.ACERTO),
        (juez.REPRUEBA, elegir_etiquetas.FALLO),
    }


def test_ningun_caso_es_dudoso(entradas):
    for entrada in entradas:
        assert (entrada["corrida"], entrada["pregunta"]) not in DUDOSOS, entrada["id"]


def test_de_los_criterios_endurecidos_solo_queda_la_reprobacion_declarada(entradas):
    """Una aprobación adjudicada con el criterio de antes no se sostiene con uno más duro; una reprobación puede que sí."""
    endurecidos = _endurecidos()
    quedan = {(e["corrida"], e["pregunta"], e["etiqueta"]) for e in entradas if elegir_etiquetas.raiz(e["pregunta"]) in endurecidos}
    assert quedan == elegir_etiquetas.ENDURECIDOS_CON_ETIQUETA


def test_el_modulo_y_la_cabecera_nombran_los_endurecidos_que_quedaron_fuera_y_el_que_queda():
    cabecera = GASTADAS.read_text(encoding="utf-8").split("etiquetas:", maxsplit=1)[0]
    assert len(_endurecidos()) == 5
    for identificador in _endurecidos():
        assert identificador in elegir_etiquetas.__doc__, identificador
    for _, pregunta, _ in elegir_etiquetas.ENDURECIDOS_CON_ETIQUETA:
        assert f"salvo {pregunta}" in cabecera, pregunta


@requiere_reportes
def test_la_reprobacion_que_queda_se_sostiene_con_el_criterio_de_hoy(entradas, etiquetas):
    """J-26: el juez viejo reprobó porque no nombra la diferencia, y eso se sigue pidiendo."""
    criterios = {paso.preguntas[0].id: paso.preguntas[0].criterio for prompt in banco.leer_banco() for paso in prompt.pasos}
    for corrida, pregunta, etiqueta in elegir_etiquetas.ENDURECIDOS_CON_ETIQUETA:
        assert etiqueta == elegir_etiquetas.REPRUEBA
        assert _adjudicados()[(corrida, pregunta)] == ("reprueba", "correcto")
        entrada = next(e for e in entradas if (e["corrida"], e["pregunta"]) == (corrida, pregunta))
        assert "diferencia" in next(e for e in etiquetas if e.id == entrada["id"]).porque
        assert "nombra la diferencia" in criterios[pregunta]


# ------------------------------------------------------------------ la selección sale de la revisión guardada


@requiere_reportes
def test_la_revision_que_sustenta_la_seleccion_esta_en_el_repositorio():
    assert REVISION.exists()
    cabecera = GASTADAS.read_text(encoding="utf-8").split("etiquetas:", maxsplit=1)[0]
    assert REVISION.name in cabecera
    assert REVISION.name in elegir_etiquetas.__doc__


@requiere_reportes
def test_la_revision_da_las_cifras_que_cita_el_plan():
    reprobados = [adjudicacion for veredicto, adjudicacion in _adjudicados().values() if veredicto == "reprueba"]
    assert len(reprobados) == 53
    assert (reprobados.count("correcto"), reprobados.count("incorrecto"), reprobados.count("sin decidir")) == (16, 34, 3)


@pytest.mark.parametrize(
    ("opciones", "esperado"),
    [
        ((elegir_etiquetas.REPRUEBA_Y_APROBO,), ("aprueba", "incorrecto")),
        ((elegir_etiquetas.REPRUEBA_Y_REPROBO,), ("reprueba", "correcto")),
        (
            (
                elegir_etiquetas.APRUEBA_Y_REPROBO_CONVERSACION,
                elegir_etiquetas.APRUEBA_Y_REPROBO_COMPUESTO,
                elegir_etiquetas.APRUEBA_Y_REPROBO_SENCILLA,
            ),
            ("reprueba", "incorrecto"),
        ),
        ((elegir_etiquetas.APRUEBA_Y_APROBO,), ("aprueba", "correcto")),
    ],
    ids=["reprueba_y_aprobo", "reprueba_y_reprobo", "aprueba_y_reprobo", "aprueba_y_aprobo"],
)
@requiere_reportes
def test_cada_lista_de_la_seleccion_es_lo_que_adjudico_la_revision(opciones, esperado):
    adjudicados = _adjudicados()
    for caso in _casos(*opciones):
        assert adjudicados[caso] == esperado, caso


@requiere_reportes
def test_las_fijas_tienen_su_fila_o_estan_declaradas_sin_ella(veredictos_viejos):
    adjudicados = _adjudicados()
    for caso in _casos(elegir_etiquetas.APRUEBA_FIJAS, elegir_etiquetas.APRUEBA_DEJA_SUPUESTO):
        if caso in SIN_FILA_EN_LA_REVISION:
            assert caso not in adjudicados, caso
            assert veredictos_viejos[caso] == "aprueba", caso
        else:
            assert adjudicados[caso] == ("aprueba", "correcto"), caso


@requiere_reportes
def test_la_revision_no_trae_datos_personales_de_los_tickets():
    texto = REVISION.read_text(encoding="utf-8")
    assert not privacidad.buscar_datos_personales(texto)
    assert evaluadores.MARCA_PERSONA not in texto
    assert evaluadores.MARCA_TELEFONO not in texto
    for folio in evaluadores.TICKETS_CON_DATOS_PERSONALES:
        assert not evaluadores.hay_dato_del_ticket(folio, texto), folio


def test_el_modulo_dice_de_donde_sale_la_semilla():
    assert f"{elegir_etiquetas.SEMILLA} no se deriva" in " ".join(elegir_etiquetas.__doc__.split())


def test_al_menos_4_son_de_compuestos_o_de_turnos_con_historial(etiquetas):
    con_contexto = [etiqueta.id for etiqueta in etiquetas if "." in etiqueta.pregunta or etiqueta.respuestas_anteriores]
    assert len(con_contexto) >= 4, con_contexto


def test_las_respuestas_miden_lo_que_las_reales(etiquetas):
    """Las escritas a mano medían 156 caracteres de media y dieron 20 de 20; las reales miden 735."""
    largos = [len(etiqueta.respuesta) for etiqueta in etiquetas]
    assert sum(largos) / len(largos) >= LARGO_MEDIO_MINIMO, largos


def test_traen_sus_notas_y_sus_pies(etiquetas):
    con_nota = [etiqueta.id for etiqueta in etiquetas if "Nota" in etiqueta.respuesta]
    assert len(con_nota) >= 15, con_nota
    sin_pie = [etiqueta.id for etiqueta in etiquetas if "Fuente:" not in etiqueta.respuesta and "Sin resultado" not in etiqueta.respuesta]
    assert not sin_pie


def test_no_traen_datos_personales_de_los_tickets(etiquetas):
    for etiqueta in etiquetas:
        textos = "\n".join([etiqueta.respuesta, etiqueta.porque, *etiqueta.respuestas_anteriores])
        assert not privacidad.buscar_datos_personales(textos), etiqueta.id
        assert evaluadores.MARCA_PERSONA not in textos, etiqueta.id
        assert evaluadores.MARCA_TELEFONO not in textos, etiqueta.id
        for folio in evaluadores.TICKETS_CON_DATOS_PERSONALES:
            assert not evaluadores.hay_dato_del_ticket(folio, textos), etiqueta.id


def test_la_cabecera_de_las_gastadas_dice_su_resultado_y_de_donde_salen():
    cabecera = GASTADAS.read_text(encoding="utf-8").split("etiquetas:", maxsplit=1)[0]
    assert REPORTE.name in cabecera
    assert str(elegir_etiquetas.SEMILLA) in cabecera
    assert "17 de 20" in cabecera


def test_las_de_ajuste_siguen_siendo_las_mismas_10():
    ajuste = [etiqueta.id for etiqueta in juez.leer_etiquetas() if etiqueta.conjunto == juez.AJUSTE]
    assert ajuste == [f"J-{numero:02d}" for numero in range(1, 11)]


# ------------------------------------------------------------------ las 20 de hoy


@pytest.fixture(scope="module")
def nuevas():
    datos = yaml.safe_load(juez.ETIQUETAS.read_text(encoding="utf-8"))["etiquetas"]
    return [entrada for entrada in datos if entrada["conjunto"] == juez.VALIDACION]


@requiere_reportes
def test_cada_nueva_es_lo_que_el_juez_recibio_en_su_reporte(nuevas):
    indice = juez.indice_del_banco()
    etiquetas = {etiqueta.id: etiqueta for etiqueta in juez.leer_etiquetas()}
    casos = {}
    for entrada in nuevas:
        nombre = entrada["reporte"]
        if nombre not in casos:
            casos[nombre] = _casos_del_reporte(json.loads((REPORTES / nombre).read_text(encoding="utf-8")))
        de_la_corrida = casos[nombre][(entrada["corrida"], entrada["pregunta"])]
        assert juez.caso_de(etiquetas[entrada["id"]], indice) == de_la_corrida, entrada["id"]


def test_ninguna_nueva_es_una_gastada(nuevas, entradas):
    assert not {entrada["respuesta"] for entrada in nuevas} & {entrada["respuesta"] for entrada in entradas}


def test_las_nuevas_no_traen_las_preguntas_que_movieron_el_ajuste(nuevas):
    errores_del_ajuste = {"N-14", "PT-12", "PT-51", "PT-07"}
    assert not {entrada["pregunta"].rsplit(".", maxsplit=1)[-1] for entrada in nuevas} & errores_del_ajuste


def test_las_nuevas_miden_lo_que_las_reales_y_no_traen_datos_de_los_tickets(nuevas):
    assert sum(len(entrada["respuesta"]) for entrada in nuevas) / len(nuevas) >= LARGO_MEDIO_MINIMO
    for entrada in nuevas:
        textos = "\n".join([entrada["respuesta"], entrada["porque"], *entrada.get("respuestas_anteriores", ())])
        for folio in evaluadores.TICKETS_CON_DATOS_PERSONALES:
            assert not evaluadores.hay_dato_del_ticket(folio, textos), entrada["id"]


def test_la_cabecera_dice_de_donde_salen_las_nuevas_y_que_no_las_reviso_una_persona():
    cabecera = juez.ETIQUETAS.read_text(encoding="utf-8").split("etiquetas:", maxsplit=1)[0]
    for nombre in ("2026-11-03-completo-b1b49c1.json", REPORTE.name, GASTADAS.name, "20261103"):
        assert nombre in cabecera, nombre
    assert "No las revisó una persona" in cabecera
    assert "no es independiente" in cabecera
