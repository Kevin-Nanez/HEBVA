"""Interfaz de chat en Streamlit (T4.1, D6-A, K23, K29, C4.4, C1.1, C2.2, C7.2).

Acepta: "una persona que no lo construyó hace P03, P06 y P21 sin ayuda; la consulta del panel es idéntica, carácter
por carácter, a la del registro".

Lo que cubre cada prueba sin red ni llaves, con AppTest de Streamlit y un modelo simulado (FunctionModel) en el lugar
del modelo real:

- P03, P06 y P21 corren por la interfaz: la cifra o la regla abre la respuesta, la fuente y el corte (K29) salen de
  las evidencias del turno y no de lo que dijo el modelo, y un panel plegado trae la consulta, los filtros, las filas,
  la fuente y el fragmento citado.
- La consulta del panel es la del registro, carácter por carácter, también cuando empieza o termina con un salto de
  línea (la de buscar_politicas).
- Cada sesión del navegador tiene su Historial en st.session_state y dos sesiones abiertas a la vez no mezclan
  contexto (P52). Que el modelo real también lo respete queda pendiente de la llave.
- La salida del modelo, el texto de los tickets y los valores de los resultados se muestran como texto (K23): ningún
  elemento que interprete Markdown o HTML recibe un texto que no sea fijo, y una imagen remota en Markdown se ve
  como texto. La petición de red de esa imagen se comprobó en el navegador y no es de esta suite.
- Los estados sin cifra (tope, modelo no disponible, fuente no disponible) y la respuesta vacía del modelo se
  muestran tal cual y sin panel, y la cifra de una herramienta que alcanzó a correr no sale en pantalla.
- T4.2: la pantalla inicial trae tiendas, periodos, cortes, documentos y 3 ejemplos de preguntas que no puede contestar,
  leídos de la base; "qué sabes" y ayuda se responden con lo mismo sin llamar al modelo y dejan en el registro un
  turno con 0 tokens; una pregunta de datos que empieza igual sigue yendo al modelo. El detalle del manifiesto está
  en tests/test_manifiesto.py.
- Que una persona que no construyó la interfaz la use sin ayuda es de una persona (pendiente_humano).

La base se construye una vez en un directorio temporal. Si la extensión fts no está instalada, las pruebas que
la necesitan se saltan: sin red no se descarga (T0.5). Ninguna prueba lee .env: responder() recibe el modelo simulado.
"""

import ast
import csv
import datetime as dt
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import duckdb
import pandas
import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from streamlit.testing.v1 import AppTest

import app
from agente import agente, construir, manifiesto, registro, reintentos
from agente.herramientas.comun import Resultado
from agente.historial import Historial
from tests.conftest import DIR_DATOS, RAIZ
from tests.test_privacidad import (
    TICKET_CON_DATOS,
    DatoPersonal,
    leer_tickets,
    presentes,
    variantes_celular,
    variantes_nombre,
)

HOY = dt.date(2026, 10, 31)
APP = RAIZ / "app.py"

P03 = "¿Cuánto vendimos en total de junio a agosto?"
P06 = "el yogurt del refri caduca el viernes, hasta cuando lo puedo vender?"
P21 = "cuanto vendio la sucursal de guadalupe?"
CIFRA_P03 = "17,228,300.26"
# Ids de sesión fijos y sin dígitos seguidos: así lo que se prueba es qué Historial usó cada sesión y no el UUID al
# azar de cada corrida, que no tiene por qué llegar igual al registro.
ID_SESION_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ID_SESION_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
COBERTURA_DE_LA_LECHE = {"metrica": "cobertura", "producto": "leche deslactosada", "tiendas": ["valle norte"]}
# La consulta que escribiría el modelo para P06 con las palabras de los documentos (T2.7).
CONSULTA_P06 = "retiro de anaquel de lácteos, yogur, días antes de la caducidad"

# El ticket de prueba (K23): su descripción trae una imagen remota, un enlace y HTML. El título es uno de los de
# datos/ para que tenga tema y la construcción no avise.
FOLIO_HOSTIL = "1290"
DESCRIPCION_HOSTIL = (
    "Fuga en el baño. ![x](http://ejemplo.test/imagen?dato=celular) [pulsa aqui](http://ejemplo.test/enlace) "
    '<img src="http://ejemplo.test/p.png"> <script>alert(1)</script>'
)
TITULO_HOSTIL = "Fuga de agua en baños"
DOMINIO_REMOTO = "ejemplo.test"
IMAGEN_Y_ENLACE = "![x](http://ejemplo.test/?q=1) [a](http://ejemplo.test)"

# Lo que la interfaz nunca debe pasar a una función que interprete Markdown o HTML si no es un texto fijo.
INTERPRETAN_MARKDOWN = frozenset(
    {
        "markdown", "caption", "title", "header", "subheader", "write", "info", "warning", "error", "success",
        "exception", "latex", "html", "table", "metric", "toast", "status", "expander", "spinner", "chat_input",
        "chat_message", "set_page_config", "image", "button", "link_button", "page_link", "popover",
    }
)


# ------------------------------------------------------------------ el modelo simulado


def vueltas_del_turno(mensajes):
    """Respuestas del modelo después de la última pregunta del usuario."""
    ultima = max(i for i, m in enumerate(mensajes) if any(isinstance(p, UserPromptPart) for p in m.parts))
    return sum(isinstance(m, ModelResponse) for m in mensajes[ultima:])


def devueltos(mensajes):
    return [p for m in mensajes for p in m.parts if isinstance(p, ToolReturnPart)]


def guion(*vueltas, redactar):
    """Modelo simulado: en la vuelta i pide las llamadas de vueltas[i]; después redacta con lo devuelto."""

    def modelo(mensajes, info: AgentInfo):
        vuelta = vueltas_del_turno(mensajes)
        if vuelta < len(vueltas):
            partes = [
                ToolCallPart(nombre, argumentos, tool_call_id=f"v{vuelta}-{i}")
                for i, (nombre, argumentos) in enumerate(vueltas[vuelta])
            ]
            return ModelResponse(parts=partes)
        return ModelResponse(parts=[TextPart(redactar(devueltos(mensajes)))])

    return FunctionModel(modelo, model_name="guion")


def resultado_de(partes):
    return json.loads(partes[-1].content)


def descripcion_de(partes):
    """La descripción del ticket que devolvió la herramienta de tickets, como la recibe el modelo."""
    return resultado_de(partes)["filas"][0]["descripcion"]


def redactar_venta(partes):
    """Lo que haría el modelo con el resultado de ventas: la cifra en la primera línea, tal como llegó."""
    resultado = resultado_de(partes)
    return (
        f"{resultado['cifras']['venta']} pesos de venta de junio a agosto (corte: {resultado['corte']})\n"
        f"{resultado['definicion']}"
    )


def redactar_regla(partes):
    resultado = resultado_de(partes)
    fila = next(f for f in resultado["filas"] if f["documento"] == "POL-OPS-014" and f["seccion"] == "5")
    return f"Se retira del anaquel antes de caducar ({fila['cita']})."


def redactar_tienda_inexistente(partes):
    resultado = resultado_de(partes)
    tiendas = "; ".join(resultado["detalle"]["existentes"])
    return f"{resultado['resumen']}.\nLas tiendas son: {tiendas}."


def modelo_p03():
    return guion([("ventas", {"periodo": "de junio a agosto"})], redactar=redactar_venta)


def modelo_p06():
    return guion([("buscar_politicas", {"consulta": CONSULTA_P06})], redactar=redactar_regla)


def modelo_p21():
    return guion(
        [("ventas", {"periodo": "de junio a agosto", "tiendas": ["guadalupe"]})], redactar=redactar_tienda_inexistente
    )


def modelo_que_solo_contesta(texto):
    return FunctionModel(lambda mensajes, info: ModelResponse(parts=[TextPart(texto)]), model_name="contesta")


def modelo_espia(llamadas):
    """Modelo simulado que anota cada solicitud que recibe: en la cobertura no debe recibir ninguna."""

    def modelo(mensajes, info: AgentInfo):
        llamadas.append(mensajes)
        return ModelResponse(parts=[TextPart("Entendido.")])

    return FunctionModel(modelo, model_name="espia")


# ------------------------------------------------------------------ la interfaz bajo prueba


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def base(fts, tmp_path_factory):
    """La base de datos/ del módulo, construida una vez con asegurar_base() como en cada turno."""
    ruta = tmp_path_factory.mktemp("interfaz") / "tienda.duckdb"
    construir.asegurar_base(DIR_DATOS, ruta)
    return ruta


@dataclass
class Banco:
    """Lo que la interfaz usa en la prueba en lugar del modelo real, de build/ y de logs/.

    llamadas son los argumentos con que la interfaz llamó a responder(), tal como los mandó; el doble los cambia por
    los de la prueba antes de correr el responder() de verdad. respuestas son las Respuesta que devolvió, en orden.

    T4.2: la pantalla inicial lee el manifiesto de cobertura de la base, y sin este doble lo leería de build/.
    lecturas son los argumentos con que la interfaz pidió esa lectura; sin_modelo, los de cada vez que respondió con
    el manifiesto en lugar de llamar a responder(), y respuestas_sin_modelo lo que devolvió.
    """

    base: Path
    registro: Path
    modelo: Any = None
    opciones: dict[str, Any] = field(default_factory=dict)
    llamadas: list[dict[str, Any]] = field(default_factory=list)
    respuestas: list[agente.Respuesta] = field(default_factory=list)
    lecturas: list[dict[str, Any]] = field(default_factory=list)
    sin_modelo: list[dict[str, Any]] = field(default_factory=list)
    respuestas_sin_modelo: list[agente.Respuesta] = field(default_factory=list)

    def abrir(self) -> AppTest:
        pantalla = AppTest.from_file(str(APP), default_timeout=120)
        pantalla.run()
        assert not pantalla.exception, [e.value for e in pantalla.exception]
        return pantalla

    def lineas_del_registro(self) -> list[dict[str, Any]]:
        return list(registro.leer(self.registro))


@pytest.fixture
def banco(monkeypatch, base, tmp_path):
    banco = Banco(base, tmp_path / "logs" / "turnos.jsonl")
    verdadero = agente.responder
    leer_verdadero = manifiesto.leer
    responder_sin_modelo_verdadero = manifiesto.responder

    def responder_de_prueba(pregunta, historial=(), **opciones):
        banco.llamadas.append({"pregunta": pregunta, "historial": historial, **opciones})
        pedidas = {
            "modelo": banco.modelo, "base": banco.base, "hoy_real": HOY, **banco.opciones,
            "archivo_registro": banco.registro,
        }
        respuesta = verdadero(pregunta, historial, **{**opciones, **pedidas})
        banco.respuestas.append(respuesta)
        return respuesta

    def de_la_base():
        """Lo de banco.opciones que entiende el manifiesto: de dónde sale la base, no el modelo ni la fecha."""
        return {clave: valor for clave, valor in banco.opciones.items() if clave in ("base", "dir_datos", "dir_config")}

    def leer_de_prueba(**opciones):
        banco.lecturas.append(opciones)
        return leer_verdadero(**{**opciones, "base": banco.base, **de_la_base()})

    def responder_sin_modelo_de_prueba(pregunta, historial=None, **opciones):
        banco.sin_modelo.append({"pregunta": pregunta, "historial": historial, **opciones})
        pedidas = {"base": banco.base, "hoy_real": HOY, **de_la_base(), "archivo_registro": banco.registro}
        respuesta = responder_sin_modelo_verdadero(pregunta, historial, **{**opciones, **pedidas})
        banco.respuestas_sin_modelo.append(respuesta)
        return respuesta

    monkeypatch.setattr(agente, "responder", responder_de_prueba)
    monkeypatch.setattr(manifiesto, "leer", leer_de_prueba)
    monkeypatch.setattr(manifiesto, "responder", responder_sin_modelo_de_prueba)
    return banco


def preguntar(pantalla: AppTest, pregunta: str) -> AppTest:
    pantalla.chat_input[0].set_value(pregunta).run()
    assert not pantalla.exception, [e.value for e in pantalla.exception]
    return pantalla


# ------------------------------------------------------------------ leer la pantalla


def elementos(nodo):
    """Cada elemento de la página bajo un nodo del árbol de AppTest, en orden."""
    for hijo in nodo.children.values():
        yield hijo
        if hasattr(hijo, "children"):
            yield from elementos(hijo)


def respuestas(pantalla: AppTest):
    return [mensaje for mensaje in pantalla.chat_message if mensaje.name == "assistant"]


def ultima_respuesta(pantalla: AppTest):
    return respuestas(pantalla)[-1]


def textos_de(nodo) -> list[str]:
    """Todos los textos bajo un nodo, también los que están dentro del panel plegado."""
    return [elemento.value for elemento in elementos(nodo) if elemento.type == "text"]


def tablas_de(nodo) -> list[dict]:
    """Los datos que recibe el componente de cada tabla bajo un nodo."""
    return [
        json.loads(elemento.proto.json)
        for elemento in elementos(nodo)
        if elemento.type == "bidi_component" and elemento.proto.component_name.endswith(app.COMPONENTE_DE_TABLA)
    ]


def textos_visibles(mensaje) -> list[str]:
    """Los textos de un mensaje que se ven sin abrir el panel: sus hijos directos."""
    return [hijo.value for hijo in mensaje.children.values() if hijo.type == "text"]


def consultas_del_panel(nodo) -> list[str]:
    return [elemento.value for elemento in elementos(nodo) if elemento.type == "code" and elemento.language == "sql"]


def fragmentos_del_panel(nodo) -> list[str]:
    return [elemento.value for elemento in elementos(nodo) if elemento.type == "code" and elemento.language != "sql"]


def panel_de(nodo):
    paneles = [elemento for elemento in elementos(nodo) if elemento.type == "expander"]
    assert len(paneles) == 1, f"se esperaba un panel y hay {len(paneles)}"
    return paneles[0]


def fuentes_de_consultas_del_panel(nodo) -> list[str]:
    """La línea de fuente y corte de cada consulta de datos: el detalle de cada una trae además otra «Fuente:» sin filtros."""
    return [texto for texto in textos_de(panel_de(nodo)) if texto.startswith("Fuente:") and "(herramienta" in texto]


def todo_el_texto(pantalla: AppTest) -> str:
    """Todo lo que la página muestra, en un solo texto, para buscar en él una cifra o un secreto."""
    partes = []
    for elemento in elementos(pantalla.main):
        valor = getattr(elemento, "value", None)
        if isinstance(valor, pandas.DataFrame):
            partes.append(valor.to_csv(index=False))
        elif isinstance(valor, str):
            partes.append(valor)
        partes.append(getattr(elemento, "label", None) or "")
    return "\n".join(partes)


def linea_del_registro_con(banco: Banco, pregunta: str) -> dict[str, Any]:
    lineas = [linea for linea in banco.lineas_del_registro() if linea["pregunta"] == pregunta]
    assert lineas, f"el registro no tiene la pregunta {pregunta!r}"
    return lineas[-1]


def resultados_del_registro(banco: Banco, pregunta: str) -> list[dict[str, Any]]:
    """El Resultado completo de cada herramienta del turno, como lo guardó el registro."""
    herramientas = linea_del_registro_con(banco, pregunta)["herramientas"]
    return [herramienta["gen_ai.tool.call.result"] for herramienta in herramientas]


def consultas_del_registro(banco: Banco, pregunta: str) -> list[str]:
    return [resultado["consulta"] for resultado in resultados_del_registro(banco, pregunta)]


# ------------------------------------------------------------------ la pantalla


def test_la_pantalla_abre_sin_excepciones_y_con_una_caja_para_la_pregunta(banco):
    pantalla = banco.abrir()

    assert len(pantalla.chat_input) == 1
    caja = pantalla.chat_input[0].proto
    # El tope de K19: el navegador no deja escribir más de lo que responder() acepta.
    assert caja.max_chars == agente.TOPES.caracteres
    # Mientras corre un turno la caja se apaga: el Historial se usa de a un turno por vez.
    assert caja.submit_mode == caja.SUBMIT_MODE_DISABLE
    assert banco.llamadas == []


def test_la_pantalla_no_llama_al_modelo_hasta_que_se_pregunta(banco):
    banco.modelo = modelo_que_solo_contesta("no debe llamarse")
    banco.abrir()
    assert banco.llamadas == []
    assert not banco.registro.exists()


# ------------------------------------------------------------------ P03: la cifra, su fuente y su corte


def test_p03_abre_con_la_cifra_y_muestra_la_fuente_y_el_corte_de_la_evidencia(banco):
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    mensaje = ultima_respuesta(pantalla)
    textos = textos_visibles(mensaje)
    # La primera línea de la respuesta es la cifra, con coma de miles y punto decimal, y nada va antes.
    assert textos[0].splitlines()[0].startswith(f"{CIFRA_P03} pesos")
    fuentes = fuentes_de_consultas_del_panel(mensaje)
    assert len(fuentes) == 1
    fuente = fuentes[0]
    assert "ventas.csv" in fuente
    assert "herramienta ventas" in fuente
    assert "metrica: venta" in fuente
    assert "tiendas: T01, T02, T03, T04" in fuente
    assert "desde: 2026-06-01" in fuente and "hasta: 2026-08-31" in fuente
    assert fuente.endswith("Corte: 31 de agosto de 2026.")
    # La fuente de una cifra va en el panel: la respuesta queda limpia.
    assert fuente not in textos


def test_la_pregunta_se_muestra_como_mensaje_de_la_persona_y_antes_de_la_respuesta(banco):
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    assert [mensaje.name for mensaje in pantalla.chat_message] == ["user", "assistant"]
    assert textos_de(pantalla.chat_message[0]) == [P03]


def test_los_numeros_no_cambian_de_formato_en_la_pantalla(banco):
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    pagina = todo_el_texto(pantalla)
    assert CIFRA_P03 in pagina
    # Ni la coma decimal ni el separador de miles del sistema: el formato es el de las herramientas.
    assert "17.228.300,26" not in pagina and "17 228 300,26" not in pagina


# ------------------------------------------------------------------ P06: la regla y su cita


def test_p06_cita_documento_seccion_y_pagina_y_el_panel_trae_solo_el_fragmento_citado(banco):
    banco.modelo = modelo_p06()
    pantalla = preguntar(banco.abrir(), P06)

    mensaje = ultima_respuesta(pantalla)
    textos = textos_visibles(mensaje)
    assert textos[0].startswith("Se retira del anaquel antes de caducar (POL-OPS-014 §5, pp. 3-4)")
    # La línea gris va en un contenedor, fuera de los hijos directos; tests/test_app_pie.py comprueba que se lee sin abrir el panel.
    fuente = next(texto for texto in textos_de(mensaje) if texto.startswith("Fuente:"))
    assert "POL-OPS-014 §5, pp. 3-4" in fuente

    # El fragmento citado es el que guardó el registro, con su texto completo y sin cambios.
    registrada = linea_del_registro_con(banco, P06)["herramientas"][0]["gen_ai.tool.call.result"]
    citado = next(fila for fila in registrada["filas"] if fila["documento"] == "POL-OPS-014" and fila["seccion"] == "5")
    assert fragmentos_del_panel(mensaje) == [citado["texto"]]
    # Los otros fragmentos que devolvió la búsqueda no se muestran enteros: solo se nombran.
    otros = [fila for fila in registrada["filas"] if fila is not citado]
    assert otros, "la búsqueda de P06 devuelve más de un fragmento"
    panel = todo_el_texto(pantalla)
    for fila in otros:
        assert fila["texto"] not in panel
        assert fila["cita"] in panel


# ------------------------------------------------------------------ P21: lo que no existe


def test_p21_dice_que_no_existe_la_tienda_y_no_da_cifra_ni_fuente_de_datos(banco):
    banco.modelo = modelo_p21()
    pantalla = preguntar(banco.abrir(), P21)

    mensaje = ultima_respuesta(pantalla)
    textos = textos_visibles(mensaje)
    del_panel = textos_de(panel_de(mensaje))
    assert "No hay una tienda que coincida con 'guadalupe'" in textos[0]
    for tienda in ("Valle Norte", "Cumbres", "Anáhuac", "Lomas del Sur"):
        assert tienda in textos[0]
    # Ningún resultado trajo datos: la pantalla lo dice, con la herramienta y los filtros, sin inventar una fuente.
    assert not any(texto.startswith("Fuente:") for texto in [*textos, *del_panel])
    (sin_resultado,) = [texto for texto in del_panel if texto.startswith("Sin resultado de la consulta")]
    assert sin_resultado == (
        "Sin resultado de la consulta (herramienta ventas; periodo: de junio a agosto; tiendas: guadalupe)."
    )
    assert not any(".csv" in texto for texto in [*textos, *del_panel])


def test_un_periodo_fuera_del_corte_muestra_el_corte_de_la_fuente(banco):
    banco.modelo = guion(
        [("tickets", {"metrica": "conteo", "desde": "2026-10-30", "hasta": "2026-10-30"})],
        redactar=lambda partes: "No hay datos de tickets de ayer; el corte es el 31 de agosto de 2026.",
    )
    pantalla = preguntar(banco.abrir(), "cuantos tickets se levantaron ayer?")

    textos = textos_de(panel_de(ultima_respuesta(pantalla)))
    linea = next(texto for texto in textos if texto.startswith("Sin resultado de la consulta (herramienta tickets;"))
    assert "desde: 2026-10-30" in linea and linea.endswith("Corte: 31 de agosto de 2026.")


def test_una_respuesta_sin_herramientas_dice_que_no_consulto_los_datos(banco):
    banco.modelo = modelo_que_solo_contesta("No tengo una herramienta para eso.")
    pantalla = preguntar(banco.abrir(), "cuentame un chiste")

    mensaje = ultima_respuesta(pantalla)
    assert textos_visibles(mensaje)[0] == "No tengo una herramienta para eso."
    assert any(texto.startswith("Fuente:") and "no consultó" in texto for texto in textos_de(panel_de(mensaje)))


# ------------------------------------------------------------------ el panel de traza


def test_el_panel_esta_plegado_y_trae_consulta_filtros_filas_y_fuente(banco):
    banco.modelo = guion(
        [("ventas", {"periodo": "de junio a agosto", "agrupar_por": "tienda"})],
        redactar=lambda partes: "Ventas por tienda de junio a agosto (corte: 2026-08-31).",
    )
    pantalla = preguntar(banco.abrir(), "ventas por tienda de junio a agosto")

    panel = panel_de(ultima_respuesta(pantalla))
    assert panel.proto.expanded is False
    assert panel.label == "Ver cómo se obtuvo la respuesta"

    registrada = linea_del_registro_con(banco, "ventas por tienda de junio a agosto")["herramientas"][0]
    resultado = registrada["gen_ai.tool.call.result"]
    assert consultas_del_panel(panel) == [resultado["consulta"]]

    textos = textos_de(panel)
    # Los filtros, como los guardó el registro, uno por línea.
    filtros = next(texto for texto in textos if texto.startswith("metrica:")).splitlines()
    assert filtros == [
        "metrica: venta", "tiendas: T01, T02, T03, T04", "desde: 2026-06-01", "hasta: 2026-08-31",
        "agrupar_por: tienda",
    ]
    assert resultado["filtros"] == {
        "metrica": "venta", "tiendas": ["T01", "T02", "T03", "T04"], "desde": "2026-06-01", "hasta": "2026-08-31",
        "agrupar_por": "tienda",
    }
    # La fuente y el corte.
    assert "Fuente: ventas.csv\nCorte: 31 de agosto de 2026" in textos
    # Las filas: el número y la tabla, con las cifras tal como las dio la herramienta.
    assert any(texto.startswith("Filas del resultado: 4") for texto in textos)
    tablas = [elemento for elemento in elementos(panel) if elemento.type == "dataframe"]
    assert len(tablas) == 1
    assert tablas[0].value.to_dict("records") == resultado["filas"]


def test_la_consulta_del_panel_es_identica_caracter_por_caracter_a_la_del_registro(banco):
    """El criterio de aceptación de la ficha, con una consulta de cada herramienta que arma SQL."""
    escenarios = [
        (P03, modelo_p03()),
        (P06, modelo_p06()),
        (
            "para cuantos dias me alcanza la leche deslactosada en valle norte?",
            guion(
                [("inventario", COBERTURA_DE_LA_LECHE)],
                redactar=lambda partes: "Cobertura al corte.",
            ),
        ),
        (
            "dame el detalle del ticket 1043",
            guion(
                [("tickets", {"metrica": "detalle", "ticket_id": 1043})], redactar=lambda partes: "Detalle del ticket."
            ),
        ),
        (
            "que lotes toca retirar?",
            guion([("caducidades", {"accion": "retirar"})], redactar=lambda partes: "Lotes por retirar."),
        ),
    ]
    pantalla = banco.abrir()
    for pregunta, modelo in escenarios:
        banco.modelo = modelo
        preguntar(pantalla, pregunta)

        del_panel = consultas_del_panel(panel_de(ultima_respuesta(pantalla)))
        assert del_panel, f"{pregunta}: el panel no trae la consulta"
        assert del_panel == consultas_del_registro(banco, pregunta), pregunta


def test_la_consulta_de_politicas_conserva_sus_saltos_de_linea_del_principio_y_del_final(banco):
    # st.code recorta un salto de línea al principio y otro al final; la interfaz los compensa para mostrar lo mismo.
    banco.modelo = modelo_p06()
    pantalla = preguntar(banco.abrir(), P06)

    consulta = linea_del_registro_con(banco, P06)["herramientas"][0]["gen_ai.tool.call.result"]["consulta"]
    assert consulta.startswith("\n") and consulta.endswith("\n")
    assert consultas_del_panel(panel_de(ultima_respuesta(pantalla))) == [consulta]


def test_el_panel_de_una_consulta_sin_sql_lo_dice_en_lugar_de_dejar_un_bloque_vacio(banco):
    banco.modelo = modelo_p21()
    pantalla = preguntar(banco.abrir(), P21)

    panel = panel_de(ultima_respuesta(pantalla))
    assert consultas_del_panel(panel) == []
    assert "No se ejecutó ninguna consulta SQL." in textos_de(panel)
    assert any("tiendas: guadalupe" in texto for texto in textos_de(panel))


def test_los_numeros_del_panel_usan_coma_de_miles(banco):
    banco.modelo = guion(
        [("ventas", {"periodo": "de junio a agosto", "agrupar_por": "producto", "limite": 5})],
        redactar=lambda partes: "Los cinco productos con más venta.",
    )
    pregunta = "los 5 productos que mas venden"
    pantalla = preguntar(banco.abrir(), pregunta)

    (resultado,) = resultados_del_registro(banco, pregunta)
    assert resultado["n_filas"] > 5
    esperado = f"Filas del resultado: {resultado['n_filas']:,}; se muestran {len(resultado['filas'])}."
    assert esperado in textos_de(panel_de(ultima_respuesta(pantalla)))


def test_el_panel_dice_en_que_turno_del_registro_esta_la_respuesta(banco):
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    # Que la línea del registro lleve ese mismo id es contrato de responder() y del registro (tests/test_registro.py);
    # aquí se prueba lo que la interfaz muestra: el id de la Respuesta que recibió, y que el turno dejó su línea.
    (respuesta,) = banco.respuestas
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", respuesta.turno_id)
    assert f"Turno en el registro: {respuesta.turno_id}" in textos_de(panel_de(ultima_respuesta(pantalla)))
    assert [linea["pregunta"] for linea in banco.lineas_del_registro()] == [P03]


def test_el_panel_termina_con_las_llamadas_y_los_tokens_que_guardo_el_registro(banco):
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    linea = linea_del_registro_con(banco, P03)
    assert linea["n_llamadas"] > 0
    assert linea["gen_ai.usage.input_tokens"] > 0
    assert textos_de(panel_de(ultima_respuesta(pantalla)))[-1] == (
        f"Llamadas al modelo: {linea['n_llamadas']:,}. "
        f"Tokens de entrada: {linea['gen_ai.usage.input_tokens']:,}. "
        f"Tokens de salida: {linea['gen_ai.usage.output_tokens']:,}."
    )


@pytest.mark.parametrize(
    ("herramienta", "argumentos", "corte"),
    [
        ("ventas", {"periodo": "de junio a agosto", "agrupar_por": "tienda"}, "31 de agosto de 2026"),
        ("inventario", {"metrica": "bajo_reorden"}, "1 de septiembre de 2026"),
        ("caducidades", {"accion": "retirar"}, "1 de septiembre de 2026"),
        ("tickets", {"metrica": "conteo", "situacion": "sin_cerrar"}, "31 de agosto de 2026"),
    ],
)
def test_cada_herramienta_de_datos_muestra_su_fuente_y_su_corte(banco, herramienta, argumentos, corte):
    banco.modelo = guion([(herramienta, argumentos)], redactar=lambda partes: "Listo, con lo que devolvió.")
    pantalla = preguntar(banco.abrir(), f"consulta de {herramienta}")

    fuentes = fuentes_de_consultas_del_panel(ultima_respuesta(pantalla))
    assert len(fuentes) == 1
    assert f"herramienta {herramienta}" in fuentes[0]
    assert fuentes[0].endswith(f"Corte: {corte}.")


def test_dos_consultas_en_un_turno_muestran_dos_fuentes_y_las_dos_consultas_del_registro(banco):
    def redactar(partes):
        julio, agosto = (json.loads(parte.content)["cifras"]["venta"] for parte in partes)
        return f"Julio: {julio} pesos; agosto: {agosto} pesos."

    banco.modelo = guion([("ventas", {"periodo": "julio"}), ("ventas", {"periodo": "agosto"})], redactar=redactar)
    pregunta = "ventas de julio y de agosto"
    pantalla = preguntar(banco.abrir(), pregunta)

    mensaje = ultima_respuesta(pantalla)
    fuentes = fuentes_de_consultas_del_panel(mensaje)
    assert len(fuentes) == 2 and fuentes[0] != fuentes[1]
    panel = panel_de(mensaje)
    assert {"Consulta 1 de 2", "Consulta 2 de 2"} <= set(textos_de(panel))
    del_registro = consultas_del_registro(banco, pregunta)
    assert len(del_registro) == 2
    assert consultas_del_panel(panel) == del_registro


def test_un_total_dice_que_la_consulta_devuelve_totales_y_muestra_sus_cifras(banco):
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    textos = textos_de(panel_de(ultima_respuesta(pantalla)))
    assert "La consulta devuelve totales, no filas." in textos
    assert f"venta: {CIFRA_P03}\ndevoluciones_descontadas: 43,935.50" in textos
    assert [e for e in elementos(panel_de(ultima_respuesta(pantalla))) if e.type == "dataframe"] == []


def test_un_detalle_mal_formado_no_tumba_la_respuesta_ni_las_siguientes(banco, monkeypatch):
    """El panel y la fuente se dibujan otra vez en cada interacción: si uno fallara, la sesión quedaría rota."""
    banco.modelo = modelo_p06()
    entregada = agente.responder

    def con_un_fragmento_sin_cita(pregunta, historial=(), **opciones):
        respuesta = entregada(pregunta, historial, **opciones)
        malformada = respuesta.evidencias[0].resultado.model_copy(update={"filas": [{"documento": "POL-OPS-014"}]})
        return replace(respuesta, evidencias=(replace(respuesta.evidencias[0], resultado=malformada),))

    monkeypatch.setattr(agente, "responder", con_un_fragmento_sin_cita)
    pantalla = preguntar(banco.abrir(), P06)

    assert textos_visibles(ultima_respuesta(pantalla))[0].startswith("Se retira del anaquel antes de caducar")
    assert "No pude armar la fuente de esta respuesta." in textos_de(panel_de(ultima_respuesta(pantalla)))
    assert "No pude mostrar el detalle completo de esta respuesta." in textos_de(panel_de(ultima_respuesta(pantalla)))
    # Y la sesión sigue: la siguiente pregunta se contesta y el turno malformado se dibuja otra vez sin romper nada.
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    preguntar(pantalla, "gracias")
    assert len(respuestas(pantalla)) == 2


@pytest.mark.parametrize(
    ("escrito", "mostrado"),
    [
        ("**17,228,300.26 pesos** de venta", "17,228,300.26 pesos de venta"),
        ("Total: **17,228,300.26** (corte **2026-08-31**)", "Total: 17,228,300.26 (corte 2026-08-31)"),
        ("__Nota__: sin devoluciones", "Nota: sin devoluciones"),
        ("**POL-OPS-014 §5**, p. 3", "POL-OPS-014 §5, p. 3"),
        ("3 * 4 = 12 y a ** b", "3 * 4 = 12 y a ** b"),
        ("un_nombre_con_guiones y __init__x", "un_nombre_con_guiones y __init__x"),
        (
            "- **Filtros:** todas las tiendas\n- **Corte:** 31 de agosto",
            "- Filtros: todas las tiendas\n- Corte: 31 de agosto",
        ),
        (IMAGEN_Y_ENLACE, IMAGEN_Y_ENLACE),
    ],
)
def test_solo_se_quitan_los_marcadores_de_negrita_del_texto_del_modelo(escrito, mostrado):
    assert app.sin_negritas(escrito) == mostrado


def test_la_pantalla_muestra_la_cifra_sin_los_asteriscos_de_negrita_que_escribio_el_modelo(banco):
    banco.modelo = guion(
        [("ventas", {"periodo": "de junio a agosto"})],
        redactar=lambda partes: f"**{resultado_de(partes)['cifras']['venta']} pesos** de venta de junio a agosto",
    )
    pantalla = preguntar(banco.abrir(), P03)

    assert textos_visibles(ultima_respuesta(pantalla))[0] == f"{CIFRA_P03} pesos de venta de junio a agosto"
    # El registro guarda lo que dijo el modelo.
    assert linea_del_registro_con(banco, P03)["respuesta"].startswith(f"**{CIFRA_P03} pesos**")


# ------------------------------------------------------------------ historial por sesión (P52)


def test_cada_turno_se_guarda_en_el_historial_de_la_sesion_con_responder_y_agregar(banco):
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    historial = pantalla.session_state["historial"]
    assert isinstance(historial, Historial)
    assert [vuelta.pregunta for vuelta in historial.vueltas] == [P03]
    assert CIFRA_P03 in historial.vueltas[0].respuesta
    # responder() recibió el Historial de la sesión, que es el que guarda la vuelta.
    assert banco.llamadas[0]["historial"] is historial


def test_dos_sesiones_abiertas_a_la_vez_no_comparten_contexto(banco):
    vistos = []

    def modelo(mensajes, info: AgentInfo):
        vistos.append([p.content for m in mensajes for p in m.parts if isinstance(p, UserPromptPart)])
        return ModelResponse(parts=[TextPart("Entendido.")])

    banco.modelo = FunctionModel(modelo, model_name="espia")
    navegador_a, navegador_b = banco.abrir(), banco.abrir()

    preguntar(navegador_a, "cuanto vendio cumbres en junio?")
    preguntar(navegador_b, "y en julio?")
    # B no hereda la pregunta de A: pide contexto en lugar de responder con Cumbres (P52).
    assert vistos[-1] == ["y en julio?"]

    preguntar(navegador_a, "y en agosto?")
    assert vistos[-1] == ["cuanto vendio cumbres en junio?", "y en agosto?"]

    historial_a, historial_b = navegador_a.session_state["historial"], navegador_b.session_state["historial"]
    assert historial_a is not historial_b
    assert historial_a.id_sesion != historial_b.id_sesion
    assert len(historial_a) == 2 and len(historial_b) == 1
    # La pantalla de B no muestra nada de A.
    assert "cumbres" not in todo_el_texto(navegador_b).casefold()


def test_el_historial_de_una_sesion_no_vive_en_un_recurso_compartido_ni_en_una_variable_de_modulo():
    arbol = ast.parse(APP.read_text(encoding="utf-8"), filename="app.py")
    usados = {nodo.attr for nodo in ast.walk(arbol) if isinstance(nodo, ast.Attribute)}
    assert not usados & {"cache_resource", "cache_data"}
    for nodo in arbol.body:
        asignado = isinstance(nodo, (ast.Assign, ast.AnnAssign)) and "Historial" in ast.dump(nodo)
        assert not asignado, "un Historial no puede crearse a nivel de módulo"


def test_las_dos_sesiones_escriben_su_id_de_sesion_en_el_registro(banco):
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    navegador_a, navegador_b = banco.abrir(), banco.abrir()
    navegador_a.session_state["historial"].id_sesion = ID_SESION_A
    navegador_b.session_state["historial"].id_sesion = ID_SESION_B
    preguntar(navegador_a, "pregunta de la sesion a")
    preguntar(navegador_b, "pregunta de la sesion b")

    de_a = linea_del_registro_con(banco, "pregunta de la sesion a")
    de_b = linea_del_registro_con(banco, "pregunta de la sesion b")
    assert de_a["gen_ai.conversation.id"] == ID_SESION_A
    assert de_b["gen_ai.conversation.id"] == ID_SESION_B


# ------------------------------------------------------------------ cómo llama a responder()


def test_responder_se_llama_con_el_modelo_del_entorno_y_con_el_registro_de_la_interfaz(banco):
    banco.modelo = modelo_p03()
    preguntar(banco.abrir(), P03)

    (llamada,) = banco.llamadas
    assert llamada["pregunta"] == P03
    # modelo=None: responder() lee MODELO de .env con load_dotenv(); con un texto no cargaría la llave.
    assert "modelo" in llamada and llamada["modelo"] is None
    assert llamada["archivo_registro"] == registro.ARCHIVO
    assert llamada.get("origen", "interfaz") == "interfaz"
    assert llamada.get("categoria") is None


def test_el_turno_deja_su_linea_en_el_registro_con_origen_interfaz(banco):
    banco.modelo = modelo_p03()
    pantalla = banco.abrir()
    pantalla.session_state["historial"].id_sesion = ID_SESION_A
    preguntar(pantalla, P03)

    (linea,) = banco.lineas_del_registro()
    assert linea["origen"] == "interfaz" and linea["categoria"] is None
    assert linea["gen_ai.conversation.id"] == ID_SESION_A
    assert linea["estado"] == "ok" and CIFRA_P03 in linea["respuesta"]


def test_la_pregunta_se_envia_sin_espacios_a_los_lados_y_una_en_blanco_no_llama_al_modelo(banco):
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    pantalla = banco.abrir()

    preguntar(pantalla, "   ")
    assert banco.llamadas == []

    preguntar(pantalla, "  hola  ")
    assert [llamada["pregunta"] for llamada in banco.llamadas] == ["hola"]


# ------------------------------------------------------------------ estados sin cifra


def test_un_mensaje_demasiado_largo_muestra_el_texto_del_tope_y_ningun_panel(banco):
    banco.modelo = modelo_que_solo_contesta("no debe llamarse")
    largo = "1234567890" * (agente.TOPES.caracteres // 10 + 1)
    pantalla = preguntar(banco.abrir(), largo)

    mensaje = ultima_respuesta(pantalla)
    assert textos_de(mensaje) == [agente.MENSAJES_DE_TOPE["caracteres"]]
    assert [e for e in elementos(mensaje) if e.type == "expander"] == []
    assert linea_del_registro_con(banco, largo[:2000])["estado"] == "tope"


def test_con_el_modelo_sin_acceso_se_muestra_su_texto_y_no_la_cifra_de_la_herramienta_que_alcanzo_a_correr(banco):
    llamadas = []

    def modelo(mensajes, info: AgentInfo):
        llamadas.append(1)
        if len(llamadas) == 1:
            return ModelResponse(parts=[ToolCallPart("ventas", {"periodo": "de junio a agosto"}, tool_call_id="v0")])
        raise ModelHTTPError(401, "modelo-de-prueba", {"error": {"message": "SECRETO del proveedor"}})

    banco.modelo = FunctionModel(modelo, model_name="sin-acceso")
    pantalla = preguntar(banco.abrir(), P03)

    mensaje = ultima_respuesta(pantalla)
    assert textos_de(mensaje) == [reintentos.MENSAJES_DE_FALLA["llave"]]
    assert [e for e in elementos(mensaje) if e.type == "expander"] == []
    pagina = todo_el_texto(pantalla)
    # K19: ni siquiera la cifra de la herramienta que alcanzó a correr, ni el texto del proveedor.
    assert CIFRA_P03 not in pagina and "SECRETO" not in pagina
    # Pero el registro sí conserva lo que alcanzó a correr, para quien administra el sistema.
    registrada = linea_del_registro_con(banco, P03)
    assert registrada["estado"] == "modelo_no_disponible" and registrada["n_herramientas"] == 1


def test_sin_un_archivo_de_datos_la_pantalla_dice_que_no_pudo_consultar_y_no_da_cifra(banco, copia_datos, tmp_path):
    (copia_datos / "tickets_mesa_servicio.csv").unlink()
    banco.opciones = {"dir_datos": copia_datos, "base": tmp_path / "otra.duckdb"}
    banco.modelo = modelo_que_solo_contesta("no debe llamarse")
    pantalla = preguntar(banco.abrir(), P03)

    mensaje = ultima_respuesta(pantalla)
    (texto,) = textos_de(mensaje)
    assert texto.startswith("No pude consultar") and "tickets_mesa_servicio.csv" in texto
    assert [e for e in elementos(mensaje) if e.type == "expander"] == []
    assert str(tmp_path) not in todo_el_texto(pantalla)


def test_una_respuesta_vacia_del_modelo_muestra_el_mensaje_de_error_inesperado_y_no_entra_al_historial(banco):
    banco.modelo = FunctionModel(lambda mensajes, info: ModelResponse(parts=[]), model_name="vacio")
    pantalla = preguntar(banco.abrir(), P03)

    assert textos_de(ultima_respuesta(pantalla)) == [reintentos.MENSAJES_DE_FALLA["modelo"]]
    assert len(pantalla.session_state["historial"]) == 0


def test_un_error_que_nadie_previo_no_muestra_traza_tecnica_ni_su_texto(banco):
    def con_error(mensajes, info: AgentInfo):
        raise RuntimeError("SECRETO interno en C:\\Users\\alguien\\llave.txt")

    banco.modelo = FunctionModel(con_error, model_name="con-error")
    pantalla = preguntar(banco.abrir(), P03)

    assert textos_de(ultima_respuesta(pantalla)) == [app.MENSAJE_INESPERADO]
    pagina = todo_el_texto(pantalla)
    assert "SECRETO" not in pagina and "RuntimeError" not in pagina and "C:\\Users" not in pagina
    assert len(pantalla.session_state["historial"]) == 0
    # Y la pantalla sigue sirviendo: la siguiente pregunta se contesta.
    banco.modelo = modelo_p03()
    preguntar(pantalla, P03)
    assert CIFRA_P03 in todo_el_texto(pantalla)


def test_los_mensajes_de_cada_estado_no_traen_cifras():
    for texto in (app.MENSAJE_INESPERADO, *reintentos.MENSAJES_DE_FALLA.values(), *agente.MENSAJES_DE_TOPE.values()):
        assert not re.search(r"\d", texto), texto


# ------------------------------------------------------------------ G6: ningún dato personal en pantalla


@pytest.fixture(scope="module")
def datos_del_1043():
    """El celular y el nombre del 1043 como vienen en el CSV, en DatoPersonal: un fallo no los imprime."""
    descripcion = leer_tickets()[TICKET_CON_DATOS]["descripcion"]
    celular = re.findall(r"\d[\d .\-]{8,}\d", descripcion)[0]
    nombre = re.match(r"Reporta ([^,]+),", descripcion).group(1)
    return [DatoPersonal(valor) for valor in variantes_celular(celular) + variantes_nombre(nombre)]


def test_el_celular_y_el_nombre_del_1043_no_salen_en_pantalla_ni_en_el_panel_ni_en_el_registro(banco, datos_del_1043):
    banco.modelo = guion(
        [("tickets", {"metrica": "detalle", "ticket_id": TICKET_CON_DATOS})],
        redactar=lambda partes: f"Detalle del ticket {TICKET_CON_DATOS}: {descripcion_de(partes)}",
    )
    pantalla = preguntar(banco.abrir(), f"dame el detalle del ticket {TICKET_CON_DATOS}")

    # Se comparan posiciones y no textos: pytest imprime los dos lados de una comparación que falla.
    pagina = todo_el_texto(pantalla)
    registrado = banco.registro.read_text(encoding="utf-8")
    # La búsqueda sí ve el dato cuando está: en el CSV crudo.
    assert presentes(datos_del_1043, leer_tickets()[TICKET_CON_DATOS]["descripcion"]) != []
    assert presentes(datos_del_1043, pagina) == []
    assert presentes(datos_del_1043, registrado) == []
    # Lo que sí se ve es el texto enmascarado.
    enmascarado = "<TELEFONO_1>" in pagina and "<PERSONA_1>" in pagina
    assert enmascarado


# ------------------------------------------------------------------ K23: todo se muestra como texto


@pytest.fixture(scope="module")
def base_hostil(fts, tmp_path_factory):
    """Una copia de datos/ con un ticket cuya descripción trae Markdown remoto y HTML, y su base."""
    directorio = tmp_path_factory.mktemp("copia_hostil")
    copia = directorio / "datos"
    copia.mkdir()
    for ruta in DIR_DATOS.iterdir():
        if ruta.is_file():
            (copia / ruta.name).write_bytes(ruta.read_bytes())
    fila = [
        FOLIO_HOSTIL, "2026-08-31 22:00", "T02", "Mantenimiento", "Alta", "Abierto",
        TITULO_HOSTIL, DESCRIPCION_HOSTIL, "",
    ]
    with open(copia / "tickets_mesa_servicio.csv", "a", encoding="utf-8", newline="") as salida:
        csv.writer(salida, lineterminator="\n").writerow(fila)
    ruta = directorio / "hostil.duckdb"
    construir.asegurar_base(copia, ruta)
    return copia, ruta


def correr_ticket_hostil(banco, base_hostil):
    """El detalle del ticket hostil, con un modelo que repite su descripción en la respuesta."""
    copia, ruta = base_hostil
    banco.opciones = {"dir_datos": copia, "base": ruta}
    banco.modelo = guion(
        [("tickets", {"metrica": "detalle", "ticket_id": int(FOLIO_HOSTIL)})],
        redactar=lambda partes: f"Detalle del ticket {FOLIO_HOSTIL}:\n{descripcion_de(partes)}",
    )
    return preguntar(banco.abrir(), f"dame el detalle del ticket {FOLIO_HOSTIL}")


def test_el_markdown_y_el_html_del_modelo_y_de_los_tickets_se_ven_como_texto(banco, base_hostil):
    pantalla = correr_ticket_hostil(banco, base_hostil)

    mensaje = ultima_respuesta(pantalla)
    textos = textos_de(mensaje)
    # La respuesta del modelo trae la imagen, el enlace y el HTML tal cual, como texto.
    assert "![x](http://ejemplo.test/imagen?dato=celular)" in textos[0]
    assert "[pulsa aqui](http://ejemplo.test/enlace)" in textos[0]
    assert "<script>alert(1)</script>" in textos[0]
    # La tabla del panel los trae en una celda de texto.
    tabla = next(e for e in elementos(mensaje) if e.type == "dataframe")
    descripcion = tabla.value["descripcion"].iloc[0]
    assert descripcion.startswith("<texto_del_ticket>")
    assert "![x](http://ejemplo.test/imagen?dato=celular)" in descripcion


def test_ningun_elemento_que_interprete_markdown_recibe_un_texto_que_no_sea_fijo(banco, base_hostil):
    pantalla = correr_ticket_hostil(banco, base_hostil)

    for elemento in elementos(pantalla.main):
        if elemento.type in INTERPRETAN_MARKDOWN:
            texto = f"{getattr(elemento, 'value', '')} {getattr(elemento, 'label', '')}"
            assert DOMINIO_REMOTO not in texto and "script" not in texto, (elemento.type, texto)
        # Y la página no abre medios: ni imágenes, ni HTML, ni marcos.
        assert elemento.type not in {"imgs", "html", "iframe", "video", "audio"}, elemento.type


def test_un_texto_de_busqueda_con_markdown_remoto_sale_como_texto_en_la_fuente_y_el_panel(banco):
    busqueda = "![x](http://ejemplo.test/?q=1)"
    banco.modelo = guion(
        [("tickets", {"metrica": "buscar", "texto": busqueda})], redactar=lambda partes: "No encontré tickets."
    )
    pantalla = preguntar(banco.abrir(), f"busca tickets con {busqueda}")

    assert busqueda in todo_el_texto(pantalla)
    for elemento in elementos(pantalla.main):
        if elemento.type in INTERPRETAN_MARKDOWN:
            assert DOMINIO_REMOTO not in f"{getattr(elemento, 'value', '')} {getattr(elemento, 'label', '')}"


def llamadas_a_streamlit(arbol):
    """Cada llamada st.<funcion>(...) del código, con el nombre de la función."""
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute):
            if isinstance(nodo.func.value, ast.Name) and nodo.func.value.id == "st":
                yield nodo.func.attr, nodo


def es_fijo(nodo) -> bool:
    """Un valor escrito en el código: una constante, o un diccionario, lista o tupla de constantes."""
    if isinstance(nodo, ast.Constant):
        return True
    if isinstance(nodo, ast.Dict):
        return all(es_fijo(clave) and es_fijo(valor) for clave, valor in zip(nodo.keys, nodo.values, strict=True))
    if isinstance(nodo, (ast.List, ast.Tuple)):
        return all(es_fijo(elemento) for elemento in nodo.elts)
    return False


def test_solo_el_texto_fijo_va_a_las_funciones_de_streamlit_que_interpretan_markdown_o_html():
    arbol = ast.parse(APP.read_text(encoding="utf-8"), filename="app.py")
    llamadas = list(llamadas_a_streamlit(arbol))
    assert {nombre for nombre, _ in llamadas} >= {"text", "code", "chat_input", "chat_message", "expander"}

    for nombre, llamada in llamadas:
        assert nombre != "html" and nombre != "image", f"st.{nombre} abre contenido que no es texto (K23)"
        asignados = {palabra.arg: palabra.value for palabra in llamada.keywords}
        assert "unsafe_allow_html" not in asignados, f"st.{nombre} con unsafe_allow_html (línea {llamada.lineno})"
        assert "avatar" not in asignados, f"st.{nombre} con un avatar propio (línea {llamada.lineno})"
        if nombre not in INTERPRETAN_MARKDOWN:
            continue
        # max_chars no es un texto: es el tope de K19.
        valores = [*llamada.args, *(valor for clave, valor in asignados.items() if clave != "max_chars")]
        todos_fijos = all(es_fijo(valor) for valor in valores)
        assert todos_fijos, f"st.{nombre} recibe un texto que no es fijo (línea {llamada.lineno})"


def test_la_barra_superior_no_ofrece_deploy_y_el_chat_se_acomoda_sin_iconos():
    from streamlit import config

    pantalla = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not pantalla.exception
    assert config.get_option("client.toolbarMode") == "viewer"
    fuente = APP.read_text(encoding="utf-8")
    assert 'stChatMessageAvatarUser"],' in fuente and "display: none" in fuente
    assert "margin-left: auto" in fuente


def test_la_telemetria_de_la_interfaz_sigue_apagada():
    # T0.4: lo que lee Streamlit al arrancar; esta tarea no lo cambia.
    import tomllib

    configuracion = tomllib.loads((RAIZ / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert configuracion["browser"]["gatherUsageStats"] is False


# ------------------------------------------------------------------ las partes puras


FILA_5 = {
    "documento": "POL-OPS-014", "seccion": "5", "cita": "POL-OPS-014 §5, pp. 3-4",
    "titulo": "Reglas de retiro de anaquel",
    "subsecciones": "5.1 Regla general para perecederos (p. 3); 5.2 Regla específica para lácteos (p. 3)",
}
FILA_6 = {
    "documento": "POL-OPS-014", "seccion": "6", "cita": "POL-OPS-014 §6, pp. 4-5", "titulo": "Reglas de rebaja",
    "subsecciones": "6.1 Lácteos (p. 4)",
}
FILA_MANUAL = {
    "documento": "MAN-OPS-007", "seccion": "5", "cita": "MAN-OPS-007 §5, p. 3", "titulo": "Recepción",
    "subsecciones": "",
}
FILA_FAQ = {
    "documento": "FAQ-OPS-001", "seccion": "P15", "cita": "FAQ-OPS-001 P15, p. 4", "titulo": "Devoluciones",
    "subsecciones": "",
}
FILAS = [FILA_5, FILA_6, FILA_MANUAL, FILA_FAQ]


@pytest.mark.parametrize(
    ("texto", "citados"),
    [
        ("Se retira antes de caducar (POL-OPS-014 §5, pp. 3-4).", [FILA_5]),
        ("Los lácteos salen a los 2 días (POL-OPS-014 §5.2, p. 3).", [FILA_5]),
        ("Según POL-OPS-014 §6.1 y §5.1.", [FILA_5, FILA_6]),
        ("Está en MAN-OPS-007 §5.", [FILA_MANUAL]),
        ("Ver FAQ P15.", [FILA_FAQ]),
        ("Ver FAQ-OPS-001 P15, p. 4.", [FILA_FAQ]),
        ("Dice POL-OPS-014 sin sección.", []),
        ("Sin citas.", []),
        ("POL-OPS-014 §50 no existe.", []),
    ],
)
def test_el_fragmento_citado_es_el_que_nombra_el_texto_por_su_documento_y_su_seccion(texto, citados):
    assert app.fragmentos_citados(texto, FILAS) == citados


def resultado_con_filas(total: int, mostradas: int) -> Resultado:
    return Resultado(herramienta="ventas", n_filas=total, filas=[{"tienda": "T01"}] * mostradas)


def test_las_filas_llevan_coma_de_miles_sin_depender_del_locale():
    assert app.linea_de_filas(resultado_con_filas(1234, 50)) == "Filas del resultado: 1,234; se muestran 50."
    assert app.linea_de_filas(resultado_con_filas(50, 50)) == "Filas del resultado: 50."
    assert app.linea_de_filas(resultado_con_filas(0, 0)) == "Filas del resultado: 0."


# ------------------------------------------------------------------ T4.2: la pantalla inicial y "qué sabes"

TITULOS_DE_COBERTURA = [
    "Tiendas (4)",
    "Periodos con datos",
    "Cortes",
    "Documentos (4)",
    "Ejemplos de preguntas que no puedo contestar",
]
CAMPOS_DE_USO = (
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_creation.input_tokens",
    "entrada_no_cacheada",
    "razonamiento",
)
NOMBRE_HOSTIL = "![x](http://ejemplo.test/imagen?dato=celular)"


def bloques_de_la_pantalla_inicial(pantalla: AppTest) -> list[str]:
    """Los bloques de texto que la página dibuja fuera de los mensajes del chat, en orden."""
    return [hijo.value for hijo in pantalla.main.children.values() if hijo.type == "text"]


def ejemplos_de(texto: str) -> list[str]:
    return [linea for linea in texto.splitlines() if linea.startswith("- «¿")]


def test_la_pantalla_inicial_trae_solo_la_frase_de_los_temas_y_la_caja_de_pregunta(banco):
    """Sin lista de cobertura y sin llamar al modelo; el detalle lo da "qué sabes"."""
    llamadas_al_modelo = []
    banco.modelo = modelo_espia(llamadas_al_modelo)
    pantalla = banco.abrir()

    assert bloques_de_la_pantalla_inicial(pantalla) == []
    assert len(pantalla.caption) == 1
    frase = pantalla.caption[0].value
    for tema in ("ventas", "inventario", "tickets", "políticas", "Escribe abajo y presiona Enter"):
        assert tema in frase
    assert len(pantalla.chat_input) == 1
    # Nada de esto llamó al modelo, a responder() ni al registro: no hubo ninguna pregunta.
    assert llamadas_al_modelo == [] and banco.llamadas == [] and banco.sin_modelo == []
    assert not banco.registro.exists()
    # La base se sigue revisando al abrir, aunque no se dibuje.
    assert len(banco.lecturas) == 1


def test_que_sabes_trae_tiendas_periodos_cortes_documentos_y_tres_ejemplos(banco):
    """El criterio de aceptación de T4.2, leído de la base y sin llamar al modelo."""
    llamadas_al_modelo = []
    banco.modelo = modelo_espia(llamadas_al_modelo)
    pantalla = preguntar(banco.abrir(), "qué sabes")

    mensaje = ultima_respuesta(pantalla)
    pagina = "\n".join(textos_visibles(mensaje))
    lineas = pagina.splitlines()
    for titulo in TITULOS_DE_COBERTURA:
        assert titulo in lineas
    # Tiendas, periodos y documentos van en tablas, con las celdas como datos; ningún texto trae las barras.
    tiendas, periodos, documentos = tablas_de(mensaje)
    assert [fila[:2] for fila in tiendas["filas"]] == [
        ["Tienda Valle Norte", "T01"],
        ["Tienda Cumbres", "T02"],
        ["Tienda Anáhuac", "T03"],
        ["Tienda Lomas del Sur", "T04"],
    ]
    assert not any("|" in texto for texto in textos_visibles(mensaje))
    # Periodos y cortes, con fecha larga.
    assert periodos["filas"][:3] == [
        ["Ventas", "Del 1 de junio de 2026 al 31 de agosto de 2026"],
        ["Tickets de la mesa de servicio", "Del 2 de junio de 2026 al 31 de agosto de 2026"],
        ["Inventario", "Una sola foto, la del 1 de septiembre de 2026; no hay historial"],
    ]
    cortes = "- Ventas: 31 de agosto de 2026.\n- Tickets: 31 de agosto de 2026.\n- Inventario: 1 de septiembre de 2026."
    assert cortes in pagina
    assert [fila[0] for fila in documentos["filas"]] == ["FAQ-OPS-001", "MAN-OPS-007", "POL-OPS-014", "PRO-SAC-021"]
    assert len(ejemplos_de(pagina)) == 3
    assert llamadas_al_modelo == [] and banco.llamadas == []


def test_que_sabes_sigue_a_los_datos_y_no_a_un_texto_fijo(banco, base_con_tienda_hostil):
    copia, ruta = base_con_tienda_hostil
    banco.opciones = {"dir_datos": copia, "base": ruta}
    pantalla = preguntar(banco.abrir(), "qué sabes")

    mensaje = ultima_respuesta(pantalla)
    # El nombre hostil llega como dato de una celda, y el componente lo escribe con textContent.
    tiendas = tablas_de(mensaje)[0]
    assert tiendas["filas"][1][:3] == [NOMBRE_HOSTIL, "T02", "Monterrey"]
    assert "Tienda Cumbres" not in json.dumps(tiendas, ensure_ascii=False)
    assert not any("Tienda Cumbres" in texto for texto in textos_visibles(mensaje))


def test_la_pantalla_inicial_se_va_cuando_empieza_la_conversacion(banco):
    banco.modelo = modelo_que_solo_contesta("Claro.")
    pantalla = preguntar(banco.abrir(), "cuentame un chiste")

    assert bloques_de_la_pantalla_inicial(pantalla) == []
    # Solo se leyó el manifiesto al arrancar: una pregunta de datos no vuelve a leerlo.
    assert len(banco.lecturas) == 1


def test_que_sabes_se_responde_desde_el_manifiesto_sin_llamar_al_modelo_y_el_registro_marca_cero_tokens(banco):
    """El criterio de aceptación de T4.2: el turno de "qué sabes" deja 0 tokens en el registro."""
    llamadas_al_modelo = []
    banco.modelo = modelo_espia(llamadas_al_modelo)
    pantalla = banco.abrir()
    pantalla.session_state["historial"].id_sesion = ID_SESION_A
    inicial = bloques_de_la_pantalla_inicial(pantalla)

    preguntar(pantalla, "qué sabes")

    # No se llamó al modelo ni a responder(): la respuesta salió del manifiesto, con el registro de la interfaz.
    assert llamadas_al_modelo == [] and banco.llamadas == []
    (llamada,) = banco.sin_modelo
    assert llamada["pregunta"] == "qué sabes"
    assert llamada["archivo_registro"] == registro.ARCHIVO
    assert llamada.get("origen", "interfaz") == "interfaz" and llamada.get("categoria") is None
    assert llamada["historial"] is pantalla.session_state["historial"]
    # La respuesta trae lo mismo que la pantalla inicial: cada bloque, tal cual.
    (respuesta,) = banco.respuestas_sin_modelo
    texto = respuesta.texto
    visible = textos_visibles(ultima_respuesta(pantalla))
    assert visible[0].splitlines()[0].startswith("Consulto las ventas, el inventario, las caducidades y los tickets")
    for bloque in inicial:
        assert bloque in texto
    assert len(ejemplos_de("\n".join(visible))) == 3
    # El registro marca 0 llamadas y 0 tokens en ese turno.
    linea = linea_del_registro_con(banco, "qué sabes")
    assert linea["origen"] == "interfaz" and linea["categoria"] is None
    assert linea["gen_ai.conversation.id"] == ID_SESION_A
    assert linea["estado"] == "ok" and linea["respuesta"] == texto
    assert linea["n_llamadas"] == 0 and linea["llamadas"] == []
    for campo in CAMPOS_DE_USO:
        assert linea[campo] == 0, campo
    assert linea["costo_usd"] == 0 and linea["n_herramientas"] == 0
    assert linea["gen_ai.request.model"] == manifiesto.MODELO
    assert len(banco.lineas_del_registro()) == 1


@pytest.mark.parametrize(
    "pregunta",
    [
        "¿Qué sabes hacer?",
        "q sabes",
        "qué puedes hacer",
        "ayuda",
        "hola, qué sabes",
        "buenos días, ¿qué puedes hacer?",
        "en qué me puedes ayudar",
    ],
)
def test_el_mensaje_de_cobertura_se_responde_con_el_manifiesto_sin_llamar_al_modelo(banco, pregunta):
    llamadas_al_modelo = []
    banco.modelo = modelo_espia(llamadas_al_modelo)

    pantalla = preguntar(banco.abrir(), pregunta)

    assert llamadas_al_modelo == [] and banco.llamadas == []
    assert len(banco.sin_modelo) == 1
    texto = "\n".join(textos_visibles(ultima_respuesta(pantalla)))
    assert [titulo for titulo in TITULOS_DE_COBERTURA if titulo in texto] == TITULOS_DE_COBERTURA
    assert linea_del_registro_con(banco, pregunta)["n_llamadas"] == 0


def test_la_respuesta_de_cobertura_dice_su_fuente_y_su_panel_dice_que_no_uso_el_modelo(banco):
    banco.modelo = modelo_espia([])
    pantalla = preguntar(banco.abrir(), "qué sabes")

    mensaje = ultima_respuesta(pantalla)
    assert app.FUENTE_DEL_MANIFIESTO in textos_de(panel_de(mensaje))
    assert app.FUENTE_DEL_MANIFIESTO == (
        "Fuente: manifiesto de cobertura de los datos y los documentos cargados. Esta respuesta no usó el modelo."
    )
    # Es una respuesta que sí consultó algo: no dice que no consultó los datos.
    assert not any("no consultó" in texto for texto in textos_de(mensaje))
    panel = panel_de(mensaje)
    assert panel.proto.expanded is False and panel.label == "Ver cómo se obtuvo la respuesta"
    (respuesta,) = banco.respuestas_sin_modelo
    detalle = textos_de(panel)
    assert f"Turno en el registro: {respuesta.turno_id}" in detalle
    assert "Respuesta armada con el manifiesto de cobertura, sin llamar al modelo." in detalle
    assert "Llamadas al modelo: 0. Tokens de entrada: 0. Tokens de salida: 0." in detalle
    # No corrió ninguna herramienta: el panel no inventa una consulta SQL ni filas.
    assert consultas_del_panel(panel) == []
    assert not [e for e in elementos(panel) if e.type == "dataframe"]


def test_una_pregunta_de_datos_que_empieza_como_que_sabes_va_al_modelo(banco):
    banco.modelo = modelo_p03()
    pregunta = "qué sabes de lo que vendimos de junio a agosto"

    pantalla = preguntar(banco.abrir(), pregunta)

    assert [llamada["pregunta"] for llamada in banco.llamadas] == [pregunta]
    assert banco.sin_modelo == []
    assert CIFRA_P03 in textos_visibles(ultima_respuesta(pantalla))[0]


def test_la_vuelta_de_que_sabes_no_entra_al_historial_ni_viaja_al_modelo_en_la_pregunta_siguiente(banco):
    vistos = []

    def modelo(mensajes, info: AgentInfo):
        partes = [parte for mensaje in mensajes for parte in mensaje.parts]
        vistos.append([parte.content for parte in partes if isinstance(parte, UserPromptPart)])
        return ModelResponse(parts=[TextPart("Entendido.")])

    banco.modelo = FunctionModel(modelo, model_name="espia")
    pantalla = banco.abrir()

    preguntar(pantalla, "qué sabes")
    preguntar(pantalla, "cuanto vendio cumbres en junio?")

    # El modelo solo vio la pregunta de datos, sin la respuesta larga del manifiesto como contexto.
    assert vistos == [["cuanto vendio cumbres en junio?"]]
    historial = pantalla.session_state["historial"]
    assert [vuelta.pregunta for vuelta in historial.vueltas] == ["cuanto vendio cumbres en junio?"]
    # Pero la pantalla sí conserva las dos vueltas.
    assert len(respuestas(pantalla)) == 2


def test_dos_sesiones_piden_que_sabes_cada_una_con_su_id_de_sesion(banco):
    banco.modelo = modelo_espia([])
    navegador_a, navegador_b = banco.abrir(), banco.abrir()
    navegador_a.session_state["historial"].id_sesion = ID_SESION_A
    navegador_b.session_state["historial"].id_sesion = ID_SESION_B

    preguntar(navegador_a, "qué sabes")
    preguntar(navegador_b, "ayuda")

    assert banco.llamadas == [] and len(banco.sin_modelo) == 2
    de_a, de_b = linea_del_registro_con(banco, "qué sabes"), linea_del_registro_con(banco, "ayuda")
    assert de_a["gen_ai.conversation.id"] == ID_SESION_A and de_b["gen_ai.conversation.id"] == ID_SESION_B
    assert de_a["n_llamadas"] == 0 and de_b["n_llamadas"] == 0


def test_el_celular_y_el_nombre_del_1043_no_salen_en_la_cobertura_ni_en_su_linea(banco, datos_del_1043):
    pantalla = banco.abrir()
    inicial = "\n".join(bloques_de_la_pantalla_inicial(pantalla))

    preguntar(pantalla, "qué sabes")

    assert banco.llamadas == [] and len(banco.sin_modelo) == 1
    assert presentes(datos_del_1043, inicial) == []
    assert presentes(datos_del_1043, todo_el_texto(pantalla)) == []
    assert presentes(datos_del_1043, banco.registro.read_text(encoding="utf-8")) == []


# ------------------------------------------------------------------ T4.2: cuando la cobertura no se puede leer


def test_sin_un_archivo_de_datos_la_pantalla_inicial_lo_dice_y_la_caja_de_pregunta_sigue(banco, copia_datos, tmp_path):
    (copia_datos / "tickets_mesa_servicio.csv").unlink()
    banco.opciones = {"dir_datos": copia_datos, "base": tmp_path / "otra.duckdb"}

    pantalla = banco.abrir()

    (bloque,) = bloques_de_la_pantalla_inicial(pantalla)
    assert bloque.startswith("No pude consultar tickets_mesa_servicio.csv")
    assert str(tmp_path) not in todo_el_texto(pantalla)
    assert len(pantalla.chat_input) == 1


def test_sin_un_archivo_de_datos_que_sabes_dice_que_no_pudo_consultar_sin_cifra_ni_panel(banco, copia_datos, tmp_path):
    (copia_datos / "tickets_mesa_servicio.csv").unlink()
    banco.opciones = {"dir_datos": copia_datos, "base": tmp_path / "otra.duckdb"}
    banco.modelo = modelo_espia([])

    pantalla = preguntar(banco.abrir(), "qué sabes")

    assert banco.llamadas == [] and len(banco.sin_modelo) == 1
    mensaje = ultima_respuesta(pantalla)
    (texto,) = textos_de(mensaje)
    assert texto.startswith("No pude consultar tickets_mesa_servicio.csv")
    assert [e for e in elementos(mensaje) if e.type == "expander"] == []
    assert str(tmp_path) not in todo_el_texto(pantalla)
    linea = linea_del_registro_con(banco, "qué sabes")
    assert linea["estado"] == "fuente_no_disponible" and linea["n_llamadas"] == 0
    assert all(linea[campo] == 0 for campo in CAMPOS_DE_USO)


def test_un_error_inesperado_al_leer_la_cobertura_no_tumba_la_pantalla_ni_muestra_su_texto(banco, monkeypatch):
    def con_error(**opciones):
        raise RuntimeError("SECRETO interno en C:\\Users\\alguien\\llave.txt")

    monkeypatch.setattr(manifiesto, "leer", con_error)

    pantalla = banco.abrir()

    assert bloques_de_la_pantalla_inicial(pantalla) == [app.SIN_COBERTURA]
    pagina = todo_el_texto(pantalla)
    assert "SECRETO" not in pagina and "RuntimeError" not in pagina and "C:\\Users" not in pagina
    assert len(pantalla.chat_input) == 1


def test_un_error_inesperado_al_responder_que_sabes_muestra_el_mensaje_fijo_y_la_sesion_sigue(banco, monkeypatch):
    def con_error(*argumentos, **opciones):
        raise RuntimeError("SECRETO interno en C:\\Users\\alguien\\llave.txt")

    pantalla = banco.abrir()
    monkeypatch.setattr(manifiesto, "responder", con_error)
    preguntar(pantalla, "qué sabes")

    assert textos_de(ultima_respuesta(pantalla)) == [app.MENSAJE_INESPERADO]
    pagina = todo_el_texto(pantalla)
    assert "SECRETO" not in pagina and "RuntimeError" not in pagina and "C:\\Users" not in pagina
    assert len(pantalla.session_state["historial"]) == 0
    # Y la pantalla sigue sirviendo: la siguiente pregunta se contesta.
    banco.modelo = modelo_p03()
    preguntar(pantalla, P03)
    assert CIFRA_P03 in todo_el_texto(pantalla)


# ------------------------------------------------------------------ T4.2: K23, todo se muestra como texto


@pytest.fixture(scope="module")
def base_con_tienda_hostil(fts, tmp_path_factory):
    """Una copia de datos/ cuya tienda T02 se llama como una imagen remota en Markdown, y su base."""
    directorio = tmp_path_factory.mktemp("copia_tienda_hostil")
    copia = directorio / "datos"
    copia.mkdir()
    for ruta in DIR_DATOS.iterdir():
        if ruta.is_file():
            (copia / ruta.name).write_bytes(ruta.read_bytes())
    tiendas = copia / "tiendas.csv"
    tiendas.write_bytes(tiendas.read_bytes().replace(b"Tienda Cumbres", NOMBRE_HOSTIL.encode("utf-8")))
    ruta = directorio / "tienda_hostil.duckdb"
    construir.asegurar_base(copia, ruta)
    return copia, ruta


def test_el_nombre_de_una_tienda_con_markdown_remoto_se_ve_como_texto_en_la_respuesta(
    banco, base_con_tienda_hostil
):
    copia, ruta = base_con_tienda_hostil
    banco.opciones = {"dir_datos": copia, "base": ruta}
    banco.modelo = modelo_espia([])
    pantalla = banco.abrir()

    def sin_medios_ni_dominio_remoto(pagina):
        for elemento in elementos(pagina.main):
            if elemento.type in INTERPRETAN_MARKDOWN:
                texto = f"{getattr(elemento, 'value', '')} {getattr(elemento, 'label', '')}"
                assert DOMINIO_REMOTO not in texto, (elemento.type, texto)
            assert elemento.type not in {"imgs", "html", "iframe", "video", "audio"}, elemento.type

    sin_medios_ni_dominio_remoto(pantalla)
    preguntar(pantalla, "qué sabes")
    # Va en la tabla de tiendas, que el componente escribe con textContent (tests/test_app_formato.py).
    assert tablas_de(ultima_respuesta(pantalla))[0]["filas"][1][0] == NOMBRE_HOSTIL
    sin_medios_ni_dominio_remoto(pantalla)


# ------------------------------------------------------------------ varias preguntas en un mensaje (T4.6)

SEGUNDA_PREGUNTA = "y que pasa si ¿el yogurt del refri caduca el viernes, hasta cuando lo puedo vender?"
MENSAJE_CON_DOS = f"{P03}, {SEGUNDA_PREGUNTA}"
JUNIO, JULIO, AGOSTO = "¿Cuánto vendimos en junio?", "¿Y en julio?", "¿Y en agosto?"


def preguntas_vistas(mensajes):
    """Las preguntas que recibió el modelo en una solicitud: las del historial y, al final, la del turno."""
    return [str(p.content) for m in mensajes for p in m.parts if isinstance(p, UserPromptPart)]


def modelo_segun_la_pregunta(*casos):
    """Modelo simulado que sigue un guion u otro según lo que diga la pregunta del turno: (texto, guion)."""

    def modelo(mensajes, info: AgentInfo):
        pregunta = preguntas_vistas(mensajes)[-1]
        guion_del_turno = next(guionado for texto, guionado in casos if texto in pregunta)
        return guion_del_turno.function(mensajes, info)

    return FunctionModel(modelo, model_name="segun_la_pregunta")


def modelo_de_dos_preguntas():
    return modelo_segun_la_pregunta(("vendimos", modelo_p03()), ("yogurt", modelo_p06()))


def bloques_de_preguntas(mensaje):
    """Los hijos directos de una respuesta, agrupados por las líneas que separan una pregunta de otra."""
    bloques = [[]]
    for hijo in mensaje.children.values():
        if hijo.type == "divider":
            bloques.append([])
        else:
            bloques[-1].append(hijo)
    return bloques


def textos_del_bloque(bloque):
    return [hijo.value for hijo in bloque if hijo.type == "text"]


def textos_del_panel_del_bloque(bloque):
    (panel,) = [hijo for hijo in bloque if hijo.type == "expander"]
    return textos_de(panel)


def fuentes_grises_del_bloque(bloque):
    """Las líneas «Fuente:» que se leen sin abrir el panel: van en un contenedor, no como hijos directos."""
    contenedores = [hijo for hijo in bloque if hijo.type not in ("text", "expander") and hasattr(hijo, "children")]
    return [texto for contenedor in contenedores for texto in textos_de(contenedor) if texto.startswith("Fuente:")]


def test_un_mensaje_con_dos_preguntas_sale_separado_y_cada_una_con_su_fuente(banco):
    banco.modelo = modelo_de_dos_preguntas()
    pantalla = preguntar(banco.abrir(), MENSAJE_CON_DOS)

    # La gerente ve su mensaje como lo escribió, y debajo cada pregunta con lo suyo.
    assert textos_de(pantalla.chat_message[0]) == [MENSAJE_CON_DOS]
    primera, segunda = bloques_de_preguntas(ultima_respuesta(pantalla))

    pregunta, texto = textos_del_bloque(primera)
    assert pregunta == P03
    assert CIFRA_P03 in texto
    assert fuentes_grises_del_bloque(primera) == []
    fuente = next(linea for linea in textos_del_panel_del_bloque(primera) if linea.startswith("Fuente:"))
    assert "ventas.csv" in fuente and "POL-OPS-014" not in fuente

    pregunta, texto = textos_del_bloque(segunda)
    (fuente,) = fuentes_grises_del_bloque(segunda)
    assert pregunta == SEGUNDA_PREGUNTA
    assert "POL-OPS-014" in texto and CIFRA_P03 not in texto
    assert "POL-OPS-014" in fuente and "ventas.csv" not in fuente


def test_cada_pregunta_del_mensaje_trae_su_panel_con_la_consulta_del_registro(banco):
    banco.modelo = modelo_de_dos_preguntas()
    pantalla = preguntar(banco.abrir(), MENSAJE_CON_DOS)
    primera, segunda = bloques_de_preguntas(ultima_respuesta(pantalla))

    (panel_de_venta,) = [hijo for hijo in primera if hijo.type == "expander"]
    (panel_de_politica,) = [hijo for hijo in segunda if hijo.type == "expander"]
    assert consultas_del_panel(panel_de_venta) == consultas_del_registro(banco, P03)
    assert consultas_del_panel(panel_de_politica) == consultas_del_registro(banco, SEGUNDA_PREGUNTA)
    assert "ventas" in consultas_del_panel(panel_de_venta)[0]
    assert fragmentos_del_panel(panel_de_venta) == [] and fragmentos_del_panel(panel_de_politica)


def test_cada_pregunta_del_mensaje_es_un_turno_con_su_linea_en_el_registro(banco):
    banco.modelo = modelo_de_dos_preguntas()
    pantalla = preguntar(banco.abrir(), MENSAJE_CON_DOS)

    lineas = banco.lineas_del_registro()
    assert [linea["pregunta"] for linea in lineas] == [P03, SEGUNDA_PREGUNTA]
    assert len({linea["turno_id"] for linea in lineas}) == 2
    assert [herramienta["gen_ai.tool.name"] for herramienta in lineas[0]["herramientas"]] == ["ventas"]
    assert [herramienta["gen_ai.tool.name"] for herramienta in lineas[1]["herramientas"]] == ["buscar_politicas"]
    assert len(pantalla.session_state["historial"]) == 2


def test_la_siguiente_pregunta_del_mensaje_ve_la_anterior_como_contexto(banco):
    vistas = []

    def modelo(mensajes, info: AgentInfo):
        vistas.append(preguntas_vistas(mensajes))
        return ModelResponse(parts=[TextPart("Entendido.")])

    banco.modelo = FunctionModel(modelo, model_name="espia")
    preguntar(banco.abrir(), f"{JUNIO} {JULIO}")

    assert vistas[0] == [JUNIO]
    assert vistas[1] == [JUNIO, JULIO]


def test_una_sola_pregunta_no_repite_la_pregunta_ni_lleva_linea_de_separacion(banco):
    banco.modelo = modelo_p03()
    mensaje = ultima_respuesta(preguntar(banco.abrir(), P03))
    assert P03 not in textos_visibles(mensaje)
    assert len(bloques_de_preguntas(mensaje)) == 1


def test_al_volver_a_dibujar_la_pantalla_el_mensaje_anterior_sigue_separado(banco):
    banco.modelo = modelo_de_dos_preguntas()
    pantalla = preguntar(banco.abrir(), MENSAJE_CON_DOS)
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    preguntar(pantalla, "gracias")

    primera, segunda = bloques_de_preguntas(respuestas(pantalla)[0])
    assert textos_del_bloque(primera)[0] == P03
    assert textos_del_bloque(segunda)[0] == SEGUNDA_PREGUNTA


def test_de_un_mensaje_con_mas_de_3_preguntas_contesta_3_y_dice_que_las_demas_no(banco):
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    pantalla = preguntar(banco.abrir(), f"{JUNIO} {JULIO} {AGOSTO} ¿Cuántos tickets hay?")

    assert [llamada["pregunta"] for llamada in banco.llamadas] == [JUNIO, JULIO, AGOSTO]
    bloques = bloques_de_preguntas(ultima_respuesta(pantalla))
    assert len(bloques) == 4
    assert textos_del_bloque(bloques[-1]) == [app.AVISO_PREGUNTAS_DE_MAS]


def test_si_no_hay_acceso_al_modelo_no_sigue_con_las_demas_preguntas(banco):
    def modelo(mensajes, info: AgentInfo):
        raise ModelHTTPError(401, "modelo-de-prueba", {"error": {"message": "SECRETO del proveedor"}})

    banco.modelo = FunctionModel(modelo, model_name="sin-acceso")
    pantalla = preguntar(banco.abrir(), f"{JUNIO} {JULIO}")

    assert [llamada["pregunta"] for llamada in banco.llamadas] == [JUNIO]
    primera, aviso = bloques_de_preguntas(ultima_respuesta(pantalla))
    assert textos_del_bloque(primera) == [JUNIO, reintentos.MENSAJES_DE_FALLA["llave"]]
    assert textos_del_bloque(aviso) == [app.AVISO_SIN_SEGUIR]


def test_un_tope_en_una_pregunta_no_detiene_a_la_siguiente(banco):
    banco.modelo = modelo_p03()
    banco.opciones = {"topes": agente.Topes(solicitudes=1)}
    pantalla = preguntar(banco.abrir(), f"{JUNIO} {JULIO}")

    assert [llamada["pregunta"] for llamada in banco.llamadas] == [JUNIO, JULIO]
    assert [respuesta.estado for respuesta in banco.respuestas] == ["tope", "tope"]
    assert len(bloques_de_preguntas(ultima_respuesta(pantalla))) == 2


def test_una_excepcion_en_una_pregunta_no_tumba_la_pantalla_ni_sigue_con_las_demas(monkeypatch):
    preguntadas = []

    def falla(pregunta, historial=(), **opciones):
        preguntadas.append(pregunta)
        raise RuntimeError("un defecto")

    monkeypatch.setattr(agente, "responder", falla)
    turno = app.atender(f"{JUNIO} {JULIO}", Historial())

    assert preguntadas == [JUNIO]
    assert [parte.texto for parte in turno.partes] == [app.MENSAJE_INESPERADO]
    assert turno.texto == app.AVISO_SIN_SEGUIR


def test_un_mensaje_que_pasa_del_tope_de_largo_no_se_separa(banco):
    banco.modelo = modelo_que_solo_contesta("no debe llamarse")
    largo = f"{JUNIO} {JULIO} " + "x" * agente.TOPES.caracteres
    turno = app.atender(largo, Historial())

    assert turno.partes == ()
    assert turno.respuesta.estado == "tope" and turno.respuesta.traza.tope == "caracteres"
    assert len(banco.llamadas) == 1


def test_una_pregunta_de_cobertura_dentro_del_mensaje_la_contesta_el_manifiesto_sin_modelo(banco):
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    pantalla = preguntar(banco.abrir(), f"¿qué sabes? {JUNIO}")

    assert [llamada["pregunta"] for llamada in banco.llamadas] == [JUNIO]
    assert [llamada["pregunta"] for llamada in banco.sin_modelo] == ["¿qué sabes?"]
    primera, segunda = bloques_de_preguntas(ultima_respuesta(pantalla))
    assert app.FUENTE_DEL_MANIFIESTO in textos_del_panel_del_bloque(primera)
    assert textos_del_bloque(segunda)[0] == JUNIO


# ------------------------------------------------------------------ la línea de fuente del modelo (D24)


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("3,336 piezas.\nFuente: ventas.csv, corte al 31 de agosto.", "3,336 piezas."),
        ("3,336 piezas.\n\nNota: suma dos SKU.\n\nFuente: ventas.csv.", "3,336 piezas.\n\nNota: suma dos SKU."),
        ("3,336 piezas.\nFuentes: ventas.csv y catalogo.csv.\nNota: suma dos SKU.", "3,336 piezas.\nNota: suma dos SKU."),
        ("3,336 piezas.\n- Fuente: ventas.csv.", "3,336 piezas."),
        ("3,336 piezas.\n\nFuente: ventas.csv.\n\nNota: suma dos SKU.", "3,336 piezas.\n\nNota: suma dos SKU."),
        # La primera línea se queda: es la respuesta a «de dónde sale».
        ("Fuente: ventas.csv, con corte al 31 de agosto.", "Fuente: ventas.csv, con corte al 31 de agosto."),
        # Una línea que solo habla de una fuente tampoco es la línea de fuente.
        ("3,336 piezas.\nLa fuente de esta cifra no cambia.", "3,336 piezas.\nLa fuente de esta cifra no cambia."),
        ("Se revisó.\nFuente de agua: en reparación.", "Se revisó.\nFuente de agua: en reparación."),
        ("3,336 piezas.", "3,336 piezas."),
    ],
)
def test_sin_linea_de_fuente(texto, esperado):
    assert app.sin_linea_de_fuente(texto) == esperado


def test_la_pantalla_quita_la_linea_de_fuente_del_modelo_y_deja_el_pie_y_el_registro_completo(banco):
    def redactar(partes):
        return f"{redactar_venta(partes)}\n**Fuente:** ventas.csv, escrita por el modelo."

    banco.modelo = guion([("ventas", {"periodo": "de junio a agosto"})], redactar=redactar)
    pantalla = preguntar(banco.abrir(), P03)

    mensaje = ultima_respuesta(pantalla)
    (texto,) = textos_visibles(mensaje)
    (fuente,) = fuentes_de_consultas_del_panel(mensaje)
    assert "escrita por el modelo" not in texto
    assert fuente.startswith("Fuente: ventas.csv (herramienta ventas")
    assert "escrita por el modelo" in linea_del_registro_con(banco, P03)["respuesta"]


# ------------------------------------------------------------------ un archivo cambia con la sesión abierta


def test_si_cambia_un_archivo_con_la_sesion_abierta_la_conversacion_empieza_de_nuevo_sin_error(banco):
    # Streamlit recarga los módulos al cambiar un archivo (el cambio en vivo de C9.3). El Historial de la sesión queda de
    # la clase anterior, responder() no lo reconoce y la gerente leería "error inesperado" en cada pregunta.
    class HistorialDeAntes:
        pass

    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)
    pantalla.session_state["historial"] = HistorialDeAntes()
    preguntar(pantalla, P03)

    assert isinstance(pantalla.session_state["historial"], Historial)
    assert len(respuestas(pantalla)) == 1
    assert CIFRA_P03 in textos_visibles(ultima_respuesta(pantalla))[0]
