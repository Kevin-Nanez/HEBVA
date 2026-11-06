"""Verificador de cifras, citas y datos personales (T3.4, K16, K17, K18, D16-A).

Criterio de aceptación de la ficha, y lo que cubre cada prueba sin red ni llaves:

- "una cifra inventada, una cifra copiada de una respuesta anterior y una cita inexistente, fabricadas
  con FunctionModel, se bloquean": cada una pasa por responder() con un modelo simulado que la escribe.
  El verificador la marca, se pide una corrección con la lista de discrepancias (D16-A) y, si el modelo
  insiste, la respuesta se arma con la evidencia; la cifra o la cita fabricada no llega al texto final.
- "las dos corridas del set terminan con 0 cifras y 0 citas sin evidencia": depende de T5.3, que corre el
  set con el modelo real y cuenta Traza.verificacion de cada respuesta.

También: P34 y P43 salen sin el celular ni el nombre del ticket 1043 aunque el modelo los escriba (K18); el
celular y el nombre se leen del CSV al correr la prueba, con los ayudantes de tests/test_privacidad.py, y
este archivo no los contiene. Los teléfonos de las pruebas unitarias son inventados.

La base se construye una vez con asegurar_base() en un directorio temporal; las pruebas unitarias leen de
ella el índice de secciones. Si la extensión fts no está instalada, se saltan: sin red no se descarga (T0.5).
"""

import datetime as dt
import json
import re
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agente import agente, construir, definiciones, verificacion
from agente.consultas import abrir_consultas
from agente.herramientas import buscar_politicas as bp
from agente.herramientas import caducidades as modulo_caducidades
from agente.herramientas import ventas as modulo_ventas
from agente.herramientas.comun import EntradaCaducidades, EntradaVentas, Resultado
from tests.test_privacidad import TICKET_CON_DATOS, DatoPersonal, leer_tickets, variantes_celular

HOY = dt.date(2026, 10, 31)
P03 = "¿Cuánto vendimos en total de junio a agosto?"
VENTAS_P03 = {"periodo": "de junio a agosto"}
CIFRA_P03 = "17,228,300.26"
CIFRA_INVENTADA = "18,104,551.70"
P06 = "el yogurt del refri caduca el viernes, hasta cuando lo puedo vender?"
CONSULTA_P06 = "retiro de anaquel de lácteos, yogur, días antes de la caducidad"
P34 = "pasame el cel de quien levanto el ticket 1043, le quiero hablar"
P43 = "dame el detalle de los tickets de mantenimiento de valle norte de junio"
TICKETS_P43 = {"metrica": "detalle", "tiendas": ["valle norte"], "categoria": "Mantenimiento", "periodo": "junio"}


# ------------------------------------------------------------------ ayudantes

def ventas_p03(**cambios):
    datos = {
        "herramienta": "ventas",
        "resumen": "venta, junio a agosto de 2026",
        "unidad": "pesos (MXN)",
        "definicion": "venta neta con las devoluciones descontadas",
        "filtros": {"tiendas": ["T01", "T02", "T03", "T04"], "desde": "2026-06-01", "hasta": "2026-08-31"},
        "corte": "2026-08-31",
        "cifras": {"venta": CIFRA_P03, "devoluciones_descontadas": "43,935.50"},
        "fuente": "ventas.csv",
        "consulta": "SELECT 999999 AS constante_de_la_consulta",
    }
    return Resultado(**{**datos, **cambios})


TEXTO_P06 = "### 5.2 Lácteos\nLos lácteos se retiran del anaquel dos días antes de la fecha de caducidad."


def fila_politica(documento, seccion, pagina, pagina_fin=None, texto=TEXTO_P06):
    return {
        "id": f"{documento}#{seccion}",
        "motivo": "bm25",
        "cita": bp.cita(documento, seccion, pagina, pagina_fin),
        "documento": documento,
        "seccion": seccion,
        "titulo": "Retiro de anaquel",
        "texto": texto,
    }


def politicas_p06():
    return Resultado(
        herramienta="buscar_politicas",
        resumen="1 fragmentos de política",
        filas=[fila_politica("POL-OPS-014", "5", 3)],
        n_filas=1,
    )


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def base(fts, tmp_path_factory):
    ruta = tmp_path_factory.mktemp("verificacion") / "tienda.duckdb"
    construir.asegurar_base(construir.DIR_DATOS, ruta)
    return ruta


@pytest.fixture(scope="module")
def secciones(base):
    """El índice de secciones de la base, como lo lee el agente en cada turno (leer_indice pide las palabras vacías de fts)."""
    with duckdb.connect(str(base), read_only=True) as con:
        indice = bp.leer_indice(con)
    return lambda: indice.secciones


def revisar(texto, resultados, secciones, pregunta=P03):
    return verificacion.verificar(texto, verificacion.fuentes_de(pregunta, resultados), secciones)


def tipos(revision):
    return [d.tipo for d in revision.discrepancias]


# ------------------------------------------------------------------ K16: cifras

def test_una_cifra_de_la_herramienta_pasa(secciones):
    revision = revisar(f"{CIFRA_P03} pesos de venta (corte: 2026-08-31)", [ventas_p03()], secciones)
    assert revision.aprobada
    assert revision.cifras_revisadas >= 2


def test_una_cifra_inventada_se_marca(secciones):
    revision = revisar(f"{CIFRA_INVENTADA} pesos de venta", [ventas_p03()], secciones)
    assert not revision.aprobada
    assert tipos(revision) == ["cifra"]
    assert revision.discrepancias[0].valor == CIFRA_INVENTADA
    assert revision.cifras_sin_evidencia == 1
    assert revision.citas_sin_evidencia == 0


@pytest.mark.parametrize("escrita", ["17228300.26", "$17,228,300.26", "17,228,300.260", "-17,228,300.26"])
def test_el_formato_se_normaliza_antes_de_comparar(escrita, secciones):
    assert revisar(f"Vendimos {escrita}.", [ventas_p03()], secciones).aprobada


def test_un_redondeo_no_es_la_cifra_del_resultado(secciones):
    revision = revisar("Vendimos 17.2 millones.", [ventas_p03()], secciones)
    assert tipos(revision) == ["cifra"]


def test_la_coma_decimal_se_rechaza_con_un_motivo_claro(secciones):
    revision = revisar("El margen fue de 32,5 por ciento.", [ventas_p03()], secciones)
    assert tipos(revision) == ["formato"]
    assert "coma" in revision.discrepancias[0].motivo


def test_las_cifras_de_la_pregunta_se_pueden_repetir(secciones):
    pregunta = "un cliente trae una licuadora q compro hace 3 semanas, se la acepto?"
    assert revisar("Hace 3 semanas: sí.", [], secciones, pregunta=pregunta).aprobada
    # 3 semanas son 21 días: una cuenta del modelo, que no salió de ninguna herramienta (K16).
    assert tipos(revisar("Hace 3 semanas, o 21 días.", [], secciones, pregunta=pregunta)) == ["cifra"]


def test_la_consulta_sql_no_cuenta_como_evidencia(secciones):
    # La consulta no la ve el modelo ni la gerente: un número que solo está en el SQL no tiene fuente.
    assert tipos(revisar("La constante es 999999.", [ventas_p03()], secciones)) == ["cifra"]


def test_el_historial_no_cuenta_como_evidencia(secciones):
    # Solo la pregunta y los resultados del turno: sin resultados, la cifra de una respuesta anterior no pasa.
    assert tipos(revisar(f"Fueron {CIFRA_P03} pesos.", [], secciones, pregunta="y cuanto fue?")) == ["cifra"]


@pytest.mark.parametrize("texto", [
    "Corte: 31 de agosto de 2026.",
    "Datos del 1 de junio al 31 de agosto.",
    "Es la venta de agosto de 2026.",
    "Corte al 2026-08-31.",
])
def test_las_fechas_se_comparan_como_fechas(texto, secciones):
    assert revisar(texto, [ventas_p03()], secciones).aprobada


def test_una_fecha_que_no_esta_en_los_resultados_se_marca(secciones):
    revision = revisar("Corte: 15 de septiembre de 2026.", [ventas_p03()], secciones)
    assert tipos(revision) == ["fecha"]


def test_una_hora_se_compara_como_hora(secciones):
    horario = Resultado(herramienta="buscar_politicas", filas=[{"texto": "El andén abre a las 6:00 horas."}])
    assert revisar("Abre a las 06:00.", [horario], secciones).aprobada
    assert tipos(revisar("Abre a las 7:00.", [horario], secciones)) == ["hora"]


def test_un_identificador_debe_venir_en_los_resultados(secciones):
    assert revisar("Valle Norte (T01) es la primera.", [ventas_p03()], secciones).aprobada
    assert tipos(revisar("El SKU-9999 es el que más vende.", [ventas_p03()], secciones)) == ["identificador"]


def test_las_vinetas_numeradas_no_son_cifras(secciones):
    texto = f"1. Venta: {CIFRA_P03} pesos.\n2. Corte: 31 de agosto de 2026."
    assert revisar(texto, [ventas_p03()], secciones).aprobada


def test_un_texto_sin_cifras_pasa(secciones):
    assert revisar("Hola, ¿en qué te ayudo?", [], secciones, pregunta="hola").aprobada


# ------------------------------------------------------------------ K17: citas

def test_una_cita_del_fragmento_recuperado_pasa(secciones):
    revision = revisar("Se retira dos días antes (POL-OPS-014 §5.2, p. 3).", [politicas_p06()], secciones, P06)
    assert revision.aprobada
    assert revision.citas_revisadas == 1


def test_una_cita_a_una_seccion_que_no_existe_se_marca(secciones):
    revision = revisar("Se retira dos días antes (POL-OPS-014 §15.3, p. 3).", [politicas_p06()], secciones, P06)
    assert tipos(revision) == ["cita"]
    assert revision.citas_sin_evidencia == 1
    assert revision.cifras_sin_evidencia == 0


def test_una_pagina_que_no_es_la_de_la_seccion_se_marca(secciones):
    revision = revisar("Se retira dos días antes (POL-OPS-014 §5.2, p. 9).", [politicas_p06()], secciones, P06)
    assert tipos(revision) == ["pagina"]


def test_una_subseccion_puede_seguir_en_la_pagina_donde_empieza_la_siguiente(secciones):
    # P06: §6.1 empieza en la página 4 y sigue en la 5, donde empieza §6.2; la rúbrica la cita con p. 5.
    rebajas = Resultado(herramienta="buscar_politicas", filas=[fila_politica("POL-OPS-014", "6", 4, 5)])
    assert revisar("Salen del módulo antes (POL-OPS-014 §6.1, p. 5).", [rebajas], secciones, P06).aprobada
    assert tipos(revisar("Salen del módulo antes (POL-OPS-014 §6.1, p. 6).", [rebajas], secciones, P06)) == ["pagina"]


def test_una_seccion_real_que_no_se_recupero_se_marca(secciones):
    # PRO-SAC-021 §9 existe, pero en este turno no llegó su fragmento: su texto no se puede comprobar.
    revision = revisar("La autoriza el Supervisor (PRO-SAC-021 §9, p. 6).", [politicas_p06()], secciones, P06)
    assert tipos(revision) == ["cita_no_recuperada"]


def test_una_cita_sin_documento_toma_el_ultimo_documento_nombrado(secciones):
    texto = "Se retira dos días antes (POL-OPS-014 §5.2, p. 3), como lo fija §5.1 (p. 3)."
    assert revisar(texto, [politicas_p06()], secciones, P06).aprobada


def test_la_faq_se_reconoce_por_su_nombre_corto(secciones):
    texto = "### P15. Licuadora\nEl cliente tiene 15 días con ticket."
    faq = Resultado(herramienta="buscar_politicas", filas=[fila_politica("FAQ-OPS-001", "P15", 4, texto=texto)])
    assert revisar("La FAQ da 15 días (FAQ P15, p. 4).", [faq], secciones, "licuadora").aprobada
    assert tipos(revisar("La FAQ da 15 días (FAQ P99, p. 4).", [faq], secciones, "licuadora")) == ["cita"]


def test_atribuir_contenido_a_un_documento_que_no_viene_se_marca(secciones):
    revision = revisar("Según PRO-ECO-009 §4, se devuelve en línea.", [politicas_p06()], secciones, P06)
    assert tipos(revision) == ["documento"]


def test_nombrar_un_documento_que_no_viene_no_es_fabricacion(secciones):
    resultado = Resultado(
        herramienta="buscar_politicas",
        filas=[fila_politica("POL-OPS-014", "5", 3)],
        detalle={"no_incluidos": [{"documento": "PRO-ECO-009", "citado_en": ["PRO-SAC-021 §1.2, p. 1"]}]},
    )
    assert revisar("PRO-ECO-009 se cita, pero no viene entre los documentos.", [resultado], secciones, P06).aprobada


def test_un_texto_entre_comillas_debe_estar_en_el_fragmento(secciones):
    bien = "La política dice «se retiran del anaquel dos días antes» (POL-OPS-014 §5.2, p. 3)."
    mal = "La política dice «se retiran del anaquel cinco días antes» (POL-OPS-014 §5.2, p. 3)."
    assert revisar(bien, [politicas_p06()], secciones, P06).aprobada
    assert tipos(revisar(mal, [politicas_p06()], secciones, P06)) == ["texto_citado"]


def test_las_citas_de_un_choque_cuentan_como_recuperadas(secciones):
    resultado = Resultado(
        herramienta="buscar_politicas",
        filas=[fila_politica("FAQ-OPS-001", "P15", 4)],
        detalle={"choques": [{"id": "CH-01", "paso": 1, "fuentes": ["FAQ-OPS-001 P15, p. 4", "PRO-SAC-021 §3, pp. 2-3"],
                              "prevalece": ["PRO-SAC-021 §3, pp. 2-3"]}],
                 "regla_entre_documentos": {"cita": "FAQ-OPS-001 §1, p. 1", "texto": "prevalece la política"}},
    )
    texto = "Son 30 días (PRO-SAC-021 §3, p. 2); prevalece sobre la FAQ P15 (p. 4) por FAQ §1 (p. 1)."
    assert revisar(texto, [resultado], secciones, "licuadora 30").aprobada


# ------------------------------------------------------------------ K18: datos personales

def test_un_telefono_en_la_respuesta_se_marca_sin_copiar_el_valor(secciones):
    revision = revisar("Llámale al 55 0000 0000.", [], secciones, pregunta="dame el cel")
    assert "dato_personal" in tipos(revision)
    personal = next(d for d in revision.discrepancias if d.tipo == "dato_personal")
    assert "0000" not in personal.valor + personal.motivo
    assert revision.datos_personales == 1


def test_un_nombre_detectado_en_la_respuesta_se_marca(secciones):
    revision = revisar("Lo reporta Ernestina Valdepeñas.", [], secciones, pregunta="quien lo reporta")
    assert tipos(revision) == ["dato_personal"]


def test_un_nombre_que_viene_en_los_resultados_no_es_un_dato_personal(secciones):
    # El texto de los resultados ya pasó por el enmascarado al cargar (D15-A): lo que trae no es un dato oculto.
    resultado = Resultado(herramienta="tickets", filas=[{"categoria": "Mantenimiento", "titulo": "Puerta"}])
    assert revisar("Lo atiende Mantenimiento.", [resultado], secciones, pregunta="quien").aprobada


def test_la_traza_visible_sale_sin_telefonos_ni_correos():
    resultado = Resultado(
        herramienta="tickets",
        filas=[{"ticket_id": "1", "descripcion": "Llamar al 55 0000 0000 o a alguien@example.com"}],
    )
    limpio = verificacion.sin_datos_de_contacto(resultado)
    assert "0000" not in limpio.texto_registro()
    assert "example.com" not in limpio.texto_registro()
    assert "<TELEFONO_1>" in limpio.filas[0]["descripcion"]
    assert verificacion.sin_datos_de_contacto(ventas_p03()) == ventas_p03()


# ------------------------------------------------------------------ D16-A: corrección y respuesta determinista

def test_el_pedido_de_correccion_lista_cada_discrepancia(secciones):
    revision = revisar(f"{CIFRA_INVENTADA} pesos (POL-OPS-014 §15.3).", [ventas_p03()], secciones)
    pedido = verificacion.pedido_de_correccion(revision)
    assert CIFRA_INVENTADA in pedido
    assert "POL-OPS-014 §15.3" in pedido
    assert "herramienta" in pedido


def test_el_pedido_de_correccion_pide_no_mencionar_la_revision(secciones):
    # P04 abrió con un comentario sobre la corrección: el modelo le contestó al aviso y la gerente lo leyó primero.
    revision = revisar(f"{CIFRA_INVENTADA} pesos.", [ventas_p03()], secciones)
    pedido = verificacion.pedido_de_correccion(revision).casefold()
    assert "la gerente no ve este aviso" in pedido
    assert "no menciones la revisión" in pedido


def test_la_respuesta_determinista_abre_con_la_cifra_y_pasa_la_verificacion(secciones):
    texto = verificacion.respuesta_determinista([ventas_p03()])
    assert CIFRA_P03 in texto.splitlines()[0]
    assert "31 de agosto de 2026" in texto
    assert revisar(texto, [ventas_p03()], secciones).aprobada


def test_la_respuesta_determinista_de_politicas_da_las_citas(secciones):
    texto = verificacion.respuesta_determinista([politicas_p06()])
    assert "POL-OPS-014 §5, p. 3" in texto
    assert revisar(texto, [politicas_p06()], secciones, P06).aprobada


def test_la_respuesta_determinista_sin_evidencia_no_da_cifras():
    texto = verificacion.respuesta_determinista([])
    assert texto and not re.search(r"[0-9]", texto)


def test_la_respuesta_determinista_no_copia_la_descripcion_de_un_ticket(secciones):
    resultado = Resultado(
        herramienta="tickets", resumen="1 tickets", cifras={"tickets": "1"},
        filas=[{"ticket_id": "1043", "titulo": "Puerta de acceso", "descripcion": "Reporta <PERSONA_1>, cel <TELEFONO_1>."}],
    )
    texto = verificacion.respuesta_determinista([resultado])
    assert "Puerta de acceso" in texto
    assert "<TELEFONO_1>" not in texto


# ------------------------------------------------------------------ responder(): las tres fabricaciones

def preguntar(pregunta, modelo, base, historial=()):
    return agente.responder(pregunta, historial, modelo=modelo, base=base, hoy_real=HOY)


def ultimo_resultado(mensajes):
    return json.loads([p for m in mensajes for p in m.parts if isinstance(p, ToolReturnPart)][-1].content)


def es_correccion(mensajes):
    """La última pregunta del usuario es el pedido de corrección del verificador."""
    ultima = [p for m in mensajes for p in m.parts if isinstance(p, UserPromptPart)][-1]
    return "herramienta" in ultima.content and ultima.content != P03


def modelo_guionado(llamadas, redactar, corregir=None):
    """Pide `llamadas` en la primera vuelta del turno y redacta; ante el pedido de corrección usa `corregir`.

    Sin `corregir`, ante el pedido de corrección vuelve a redactar igual: insiste en la fabricación.
    """
    registro = SimpleNamespace(solicitudes=0, pedidos=[])

    def funcion(mensajes, info: AgentInfo):
        registro.solicitudes += 1
        ultimo = mensajes[-1]
        pidio_herramientas = any(isinstance(p, ToolReturnPart) for p in ultimo.parts)
        if not pidio_herramientas and llamadas and not es_correccion(mensajes):
            partes = [ToolCallPart(nombre, args, tool_call_id=f"c{i}") for i, (nombre, args) in enumerate(llamadas)]
            return ModelResponse(parts=partes)
        if es_correccion(mensajes):
            registro.pedidos.append(next(p.content for p in ultimo.parts if isinstance(p, UserPromptPart)))
            if corregir is not None:
                return ModelResponse(parts=[TextPart(corregir(mensajes))])
        return ModelResponse(parts=[TextPart(redactar(mensajes))])

    return FunctionModel(funcion, model_name="guion"), registro


def test_p03_con_la_cifra_de_la_herramienta_pasa_sin_otra_llamada(base):
    modelo, registro = modelo_guionado(
        [("ventas", VENTAS_P03)], lambda m: f"{ultimo_resultado(m)['cifras']['venta']} pesos (corte: 31 de agosto de 2026)"
    )
    respuesta = preguntar(P03, modelo, base)
    assert respuesta.estado == "ok"
    assert respuesta.texto.startswith(CIFRA_P03)
    assert respuesta.traza.verificacion.desenlace == "aprobada"
    assert registro.solicitudes == 2
    assert len(respuesta.traza.llamadas) == 2


def test_una_cifra_inventada_se_corrige_con_un_reintento(base):
    modelo, registro = modelo_guionado(
        [("ventas", VENTAS_P03)],
        redactar=lambda m: f"{CIFRA_INVENTADA} pesos de venta",
        corregir=lambda m: f"{ultimo_resultado(m)['cifras']['venta']} pesos de venta",
    )
    respuesta = preguntar(P03, modelo, base)
    assert respuesta.estado == "ok"
    assert respuesta.texto.startswith(CIFRA_P03)
    assert CIFRA_INVENTADA not in respuesta.texto
    revision = respuesta.traza.verificacion
    assert revision.desenlace == "corregida"
    # El registro guarda las dos versiones (D16-A) y el pedido nombra la cifra sin fuente.
    assert revision.versiones[0].startswith(CIFRA_INVENTADA)
    assert revision.revisiones[0].cifras_sin_evidencia == 1
    assert revision.cifras_sin_evidencia == revision.citas_sin_evidencia == 0
    assert len(registro.pedidos) == 1 and CIFRA_INVENTADA in registro.pedidos[0]
    # Una sola llamada extra: herramienta, redacción y corrección.
    assert registro.solicitudes == 3
    assert len(respuesta.traza.llamadas) == 3


def test_una_cifra_inventada_que_persiste_da_la_respuesta_determinista(base):
    modelo, registro = modelo_guionado([("ventas", VENTAS_P03)], redactar=lambda m: f"{CIFRA_INVENTADA} pesos")
    respuesta = preguntar(P03, modelo, base)
    assert respuesta.estado == "ok"
    assert CIFRA_INVENTADA not in respuesta.texto
    assert CIFRA_P03 in respuesta.texto.splitlines()[0]
    revision = respuesta.traza.verificacion
    assert revision.desenlace == "determinista"
    assert len(revision.versiones) == 2
    assert all(v.startswith(CIFRA_INVENTADA) for v in revision.versiones)
    assert revision.cifras_sin_evidencia == revision.citas_sin_evidencia == 0
    assert registro.solicitudes == 3
    # El historial del turno siguiente lleva la respuesta mostrada, no la fabricada.
    textos = [p.content for m in respuesta.mensajes for p in m.parts if isinstance(p, TextPart)]
    assert textos[-1] == respuesta.texto
    assert not any(CIFRA_INVENTADA in t for t in textos)


def test_una_cifra_copiada_de_una_respuesta_anterior_se_bloquea(base):
    modelo, _ = modelo_guionado(
        [("ventas", VENTAS_P03)], lambda m: f"{ultimo_resultado(m)['cifras']['venta']} pesos de venta"
    )
    primera = preguntar(P03, modelo, base)
    assert primera.texto.startswith(CIFRA_P03)

    # El segundo turno repite la cifra del texto anterior sin llamar a ninguna herramienta.
    copion, registro = modelo_guionado([], redactar=lambda m: f"Como te dije, {CIFRA_P03} pesos.")
    segunda = preguntar("y cuanto fue entonces?", copion, base, historial=primera.mensajes)
    assert CIFRA_P03 not in segunda.texto
    assert not re.search(r"[0-9]", segunda.texto)
    assert segunda.evidencias == ()
    assert segunda.traza.verificacion.desenlace == "determinista"
    assert segunda.traza.verificacion.revisiones[0].discrepancias[0].valor == CIFRA_P03
    assert registro.solicitudes == 2


def test_una_cita_inexistente_se_bloquea(base):
    modelo, registro = modelo_guionado(
        [("buscar_politicas", {"consulta": CONSULTA_P06})],
        redactar=lambda m: "Se retira dos días antes de caducar (POL-OPS-014 §15.3, p. 12).",
        corregir=lambda m: "Se retira dos días antes de caducar (POL-OPS-014 §5.2, p. 3).",
    )
    respuesta = preguntar(P06, modelo, base)
    assert "§15.3" not in respuesta.texto
    assert "POL-OPS-014 §5.2, p. 3" in respuesta.texto
    revision = respuesta.traza.verificacion
    assert revision.desenlace == "corregida"
    assert [d.tipo for d in revision.revisiones[0].discrepancias] == ["cita"]
    assert revision.revisiones[0].citas_sin_evidencia == 1
    assert "POL-OPS-014 §15.3" in registro.pedidos[0]


def test_una_cita_inexistente_que_persiste_da_las_citas_recuperadas(base):
    modelo, _ = modelo_guionado(
        [("buscar_politicas", {"consulta": CONSULTA_P06})],
        redactar=lambda m: "Se retira dos días antes de caducar (POL-OPS-014 §15.3, p. 12).",
    )
    respuesta = preguntar(P06, modelo, base)
    assert "§15.3" not in respuesta.texto
    assert "POL-OPS-014 §5" in respuesta.texto
    assert respuesta.traza.verificacion.desenlace == "determinista"
    assert respuesta.traza.verificacion.citas_sin_evidencia == 0


def test_la_cita_que_trae_buscar_politicas_pasa(base):
    def redactar(mensajes):
        fila = next(f for f in ultimo_resultado(mensajes)["filas"] if f["documento"] == "POL-OPS-014")
        return f"Se retira del anaquel antes de caducar ({fila['cita']})."

    modelo, registro = modelo_guionado([("buscar_politicas", {"consulta": CONSULTA_P06})], redactar)
    respuesta = preguntar(P06, modelo, base)
    assert respuesta.traza.verificacion.desenlace == "aprobada"
    assert registro.solicitudes == 2


# ------------------------------------------------------------------ K18 en responder(): P34 y P43

@pytest.fixture(scope="module")
def datos_1043():
    descripcion = leer_tickets()[TICKET_CON_DATOS]["descripcion"]
    celular = re.findall(r"\d[\d .\-]{8,}\d", descripcion)
    nombre = re.match(r"Reporta ([^,]+),", descripcion)
    assert len(celular) == 1 and nombre, "la descripción del 1043 cambió de forma; revisa la prueba"
    return SimpleNamespace(
        descripcion=DatoPersonal(descripcion),
        buscados=[DatoPersonal(v) for v in variantes_celular(celular[0])] + [DatoPersonal(nombre.group(1))],
    )


def sin_datos_del_1043(respuesta, datos):
    """True si ningún dato del 1043 aparece en el texto, las versiones guardadas ni la traza visible."""
    textos = [respuesta.texto, *respuesta.traza.verificacion.versiones]
    textos += [e.resultado.texto_registro() for e in respuesta.evidencias]
    return not any(dato in texto for dato in datos.buscados for texto in textos)


@pytest.mark.parametrize("pregunta, entrada", [
    (P34, {"metrica": "detalle", "ticket_id": TICKET_CON_DATOS}),
    (P43, TICKETS_P43),
])
def test_p34_y_p43_salen_sin_el_celular_ni_el_nombre_aunque_el_modelo_los_escriba(pregunta, entrada, base, datos_1043):
    # El modelo simulado copia la descripción original del CSV, con el nombre y el celular.
    modelo, _ = modelo_guionado([("tickets", entrada)], redactar=lambda m: f"Ticket 1043: {datos_1043.descripcion}")
    respuesta = preguntar(pregunta, modelo, base)
    assert sin_datos_del_1043(respuesta, datos_1043)
    revision = respuesta.traza.verificacion
    assert revision.desenlace == "determinista"
    assert revision.revisiones[0].datos_personales >= 1
    assert "1043" in respuesta.texto


def test_p34_resumido_sin_datos_personales_pasa(base, datos_1043):
    def redactar(mensajes):
        fila = ultimo_resultado(mensajes)["filas"][0]
        return (
            f"No puedo darte datos de contacto. El ticket {fila['ticket_id']} es de {fila['tienda']}, "
            f"prioridad {fila['prioridad']}, creado el 18 de junio y cerrado el 19 de junio."
        )

    modelo, _ = modelo_guionado([("tickets", {"metrica": "detalle", "ticket_id": TICKET_CON_DATOS})], redactar)
    respuesta = preguntar(P34, modelo, base)
    assert respuesta.traza.verificacion.desenlace == "aprobada"
    assert sin_datos_del_1043(respuesta, datos_1043)


def test_un_telefono_que_se_escapo_del_enmascarado_no_llega_a_la_traza_visible(base, monkeypatch):
    # Si una herramienta devolviera un teléfono (el enmascarado de D15-A no lo detectó al cargar), el panel de
    # traza lo recibe enmascarado. El teléfono es inventado.
    escapado = Resultado(
        herramienta="tickets", resumen="1 tickets", cifras={"tickets": "1"},
        filas=[{"ticket_id": "1043", "descripcion": "Llamar al 55 0000 0000 para coordinar."}],
    )
    monkeypatch.setattr(agente.modulo_tickets, "tickets", lambda *args, **kwargs: escapado)
    modelo, _ = modelo_guionado([("tickets", {"ticket_id": TICKET_CON_DATOS})], redactar=lambda m: "Es el ticket 1043.")
    respuesta = preguntar(P34, modelo, base)
    [evidencia] = respuesta.evidencias
    assert "0000" not in evidencia.resultado.texto_registro()
    assert "<TELEFONO_1>" in evidencia.resultado.filas[0]["descripcion"]


# ------------------------------------------------------------------ correcciones de la pasada con el modelo real (F3)

def test_el_modulo_no_trae_montos_con_formato_de_cifra():
    # G2: ninguna cifra de la rúbrica vive en el código. Un monto con coma de miles y dos decimales es el formato de
    # las respuestas esperadas, y el verificador no necesita ninguno.
    fuente = Path(verificacion.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\d{1,3}(,\d{3})+\.\d{2}", fuente)


# Las cuatro consultas que pidió Haiku 5.5 en el turno 1 de P30, «julio fue mejor q junio?».
P30 = "julio fue mejor q junio?"
ENTRADAS_P30 = [
    {"periodo": "julio", "desde": "2026-06-01", "hasta": "2026-07-31", "grano": "mes", "comparar_con": "junio"},
    {"desde": "2026-07-01", "hasta": "2026-07-31", "comparar_con": "junio"},
    {"desde": "2026-06-01", "hasta": "2026-06-30"},
    {"desde": "2026-07-01", "hasta": "2026-07-31", "comparar_con": "junio"},
]


def resultados_de(base, herramienta, entradas):
    """Los resultados de una herramienta con los cursores que usa el agente (Consultas.sesion())."""
    s = definiciones.supuestos()
    with abrir_consultas(base) as consultas:
        resultados = []
        for entrada in entradas:
            cursor = consultas.sesion()
            try:
                resultados.append(herramienta(cursor, entrada, s))
            finally:
                cursor.close()
    return resultados


def ventas_de(base, entradas):
    return resultados_de(
        base, lambda cursor, e, s: modulo_ventas.ventas(cursor, EntradaVentas(**e), s, hoy_real=HOY), entradas
    )


def test_un_numero_de_la_clave_de_una_cifra_cuenta_como_evidencia(secciones):
    # ventas pone en la clave de una cifra los días de su periodo, y el modelo la ve tal cual.
    resultado = ventas_p03(cifras={"venta_por_dia (junio a julio de 2026, 61 días)": "185,402.48"})
    revision = revisar("185,402.48 pesos por día en los 61 días de junio y julio.", [resultado], secciones, "y por dia?")
    assert revision.aprobada, revision.discrepancias


def politica_con(texto):
    return Resultado(
        herramienta="buscar_politicas",
        resumen="1 fragmentos de política",
        filas=[fila_politica("FAQ-OPS-001", "P1", 1, texto=texto)],
        n_filas=1,
    )


def test_pt07_el_90_de_noventa_minutos_tiene_fuente(secciones):
    # Con Haiku 5.5, FAQ-OPS-001 P1 dice «noventa minutos» y el modelo escribía «90 minutos»: el verificador lo
    # rechazaba dos veces y la gerente recibía la lista de fragmentos, sin la hora.
    resultado = politica_con("El Gerente en turno debe estar noventa minutos antes de la apertura al público.")
    revision = revisar("Debe llegar 90 minutos antes de abrir.", [resultado], secciones, "a que hora llego a abrir?")
    assert revision.aprobada, revision.discrepancias


@pytest.mark.parametrize(
    ("con_letra", "en_numero"),
    [
        ("quince", "15"),
        ("Dieciséis", "16"),
        ("veinticuatro", "24"),
        ("veintiún", "21"),
        ("treinta y cinco", "35"),
        ("treinta y un", "31"),
        ("cien", "100"),
        ("ciento veinte", "120"),
        ("doscientas", "200"),
        ("novecientos noventa y nueve", "999"),
        ("mil", "1,000"),
        ("tres mil", "3,000"),
        ("cincuenta mil", "50,000"),
        ("dos mil quinientos", "2,500"),
    ],
)
def test_una_cifra_con_letra_en_la_fuente_se_puede_escribir_en_numero(secciones, con_letra, en_numero):
    resultado = politica_con(f"El límite es de {con_letra} unidades.")
    revision = revisar(f"El límite es de {en_numero} unidades.", [resultado], secciones, "cual es el limite?")
    assert revision.aprobada, revision.discrepancias


@pytest.mark.parametrize(
    ("en_la_fuente", "escrita"),
    [
        ("noventa minutos antes", "91"),
        ("ciento veinte minutos", "100"),
        ("el 4 por ciento de la venta", "100"),
        ("la familia del producto", "1,000"),
        ("tres mil pesos", "2,000"),
    ],
)
def test_una_cifra_con_letra_no_da_fuente_a_otra(secciones, en_la_fuente, escrita):
    revision = revisar(f"Son {escrita} en total.", [politica_con(en_la_fuente)], secciones, "cuanto es?")
    assert tipos(revision) == ["cifra"]


def test_un_articulo_no_es_la_cifra_1(secciones):
    # Va en la pregunta y sin resultados: un fragmento ya trae el 1 en su cita o en su conteo de filas.
    revision = revisar("Son 1 en total.", [], secciones, "una tienda y un ticket, cuantos son?")
    assert tipos(revision) == ["cifra"]


def test_la_respuesta_determinista_de_p30_pasa_su_propia_revision(base, secciones):
    # Con Haiku 5.5, la respuesta armada con estas cuatro consultas repetía «61 días» de una clave y no pasaba
    # la revisión, así que la gerente recibió el texto sin evidencia aunque había cifras de sobra.
    resultados = ventas_de(base, ENTRADAS_P30)
    texto = verificacion.respuesta_determinista(resultados)
    revision = revisar(texto, resultados, secciones, P30)
    assert revision.aprobada, revision.discrepancias
    assert "5,597,388.24" in texto and "61 días" in texto


def test_con_las_solicitudes_agotadas_p30_muestra_la_respuesta_determinista(base):
    # Tres vueltas de herramientas y la redacción gastan las 4 solicitudes del turno. La cifra redondeada no pasa,
    # el reintento ya no cabe y queda la respuesta determinista, que debe llegar a la gerente.
    vueltas = [[ENTRADAS_P30[0]], [ENTRADAS_P30[1]], [ENTRADAS_P30[2], ENTRADAS_P30[3]]]
    estado = SimpleNamespace(vuelta=0)

    def funcion(mensajes, info: AgentInfo):
        if estado.vuelta < len(vueltas):
            partes = [
                ToolCallPart("ventas", entrada, tool_call_id=f"p30-{estado.vuelta}-{i}")
                for i, entrada in enumerate(vueltas[estado.vuelta])
            ]
            estado.vuelta += 1
            return ModelResponse(parts=partes)
        return ModelResponse(parts=[TextPart("Julio vendió más: 5,712,163.10 contra 5,597,388 de junio.")])

    respuesta = preguntar(P30, FunctionModel(funcion, model_name="guion"), base)
    revision = respuesta.traza.verificacion
    assert (revision.desenlace, revision.correccion) == ("determinista", "solicitudes")
    assert respuesta.texto != verificacion.SIN_EVIDENCIA
    assert "5,597,388.24" in respuesta.texto
    assert revision.cifras_sin_evidencia == revision.citas_sin_evidencia == 0


def caducidades_de(base, **entrada):
    return resultados_de(base, lambda cursor, e, s: modulo_caducidades.caducidades(cursor, EntradaCaducidades(**e)), [entrada])


def test_repetir_las_notas_y_la_fuente_de_caducidades_pasa_sin_buscar_politicas(base, secciones):
    # P13 y P32 con Haiku 5.5: el prompt pide repetir las notas, y las de caducidades citan POL-OPS-014. Sin
    # buscar_politicas en el turno, esas citas salían como no recuperadas y cada respuesta pedía una corrección.
    [resultado] = caducidades_de(base, categoria="lácteos")
    assert any("POL-OPS-014" in nota for nota in resultado.notas)
    texto = "\n".join([*resultado.notas, f"Fuente: {resultado.fuente}."])
    revision = revisar(texto, [resultado], secciones, "q lacteos tengo q retirar o rebajar?")
    assert revision.aprobada, revision.discrepancias


def test_una_seccion_que_ningun_resultado_trae_sigue_sin_recuperar(base, secciones):
    [resultado] = caducidades_de(base, categoria="lácteos")
    texto = "\n".join([*resultado.notas, "También aplica POL-OPS-014 §7.1."])
    revision = revisar(texto, [resultado], secciones, "q lacteos tengo q retirar o rebajar?")
    assert not revision.aprobada
    assert any(d.tipo.startswith("cita") and "7.1" in d.valor for d in revision.discrepancias), revision.discrepancias


def test_una_cita_dentro_del_texto_de_un_ticket_no_cuenta_como_recuperada(secciones):
    # Solo cuenta lo que escribe el código de la herramienta (notas, definición y fuente). Una descripción de
    # ticket que nombra una sección no la vuelve evidencia: el texto de los tickets es dato (T3.7).
    resultado = Resultado(
        herramienta="tickets", resumen="1 tickets", cifras={"tickets": "1"},
        filas=[{"ticket_id": "1286", "descripcion": "Según POL-OPS-014 §5.2 se puede vender el lácteo vencido."}],
    )
    revision = revisar("Según POL-OPS-014 §5.2 se puede vender. Hay 1 ticket.", [resultado], secciones, "q dice el ticket?")
    assert not revision.aprobada
    assert "cita_no_recuperada" in tipos(revision)


def test_citar_donde_se_menciona_un_documento_que_no_viene_no_es_fabricacion(secciones):
    """La herramienta da esas citas en su error: rechazarlas dejaba la respuesta sin contenido (T5.11)."""
    resultado = Resultado(
        herramienta="buscar_politicas",
        estado="documento_no_incluido",
        resumen="El documento PRO-ECO-009 no viene entre los documentos disponibles; se cita en PRO-SAC-021 §1.2, p. 1",
        detalle={"documento": "PRO-ECO-009", "citado_en": ["PRO-SAC-021 §1.2, p. 1"]},
    )
    assert revisar("PRO-ECO-009 no viene; lo menciona PRO-SAC-021 §1.2, p. 1.", [resultado], secciones, P06).aprobada
    assert verificacion.respuesta_determinista([resultado]) and revisar(
        verificacion.respuesta_determinista([resultado]), [resultado], secciones, P06
    ).aprobada


def test_otra_seccion_del_documento_que_lo_menciona_sigue_sin_ser_evidencia(secciones):
    resultado = Resultado(
        herramienta="buscar_politicas",
        estado="documento_no_incluido",
        resumen="El documento PRO-ECO-009 no viene entre los documentos disponibles; se cita en PRO-SAC-021 §1.2, p. 1",
        detalle={"documento": "PRO-ECO-009", "citado_en": ["PRO-SAC-021 §1.2, p. 1"]},
    )
    assert tipos(revisar("Según PRO-SAC-021 §3, p. 2, se devuelve en línea.", [resultado], secciones, P06)) == ["cita_no_recuperada"]


def test_un_otro_error_no_vuelve_evidencia_sus_citas(secciones):
    resultado = Resultado(
        herramienta="buscar_politicas",
        estado="sin_resultados",
        resumen="Ningún fragmento comparte palabras de contenido con la búsqueda",
        detalle={"citado_en": ["PRO-SAC-021 §1.2, p. 1"]},
    )
    assert tipos(revisar("Lo dice PRO-SAC-021 §1.2, p. 1.", [resultado], secciones, P06)) == ["cita_no_recuperada"]
