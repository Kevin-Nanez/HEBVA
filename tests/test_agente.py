"""responder(pregunta, historial): prompt de sistema, herramientas registradas y Respuesta armada por código (T3.1).

Criterio de aceptación de la ficha, y lo que cubre cada prueba sin red ni llaves:

- "P03 y P06 dan lo esperado desde la interfaz y desde el comando, con la misma traza (prompt,
  herramientas, modelo y tokens)": con FunctionModel en lugar del modelo, P03 da 17,228,300.26 desde la
  herramienta de ventas y P06 cita POL-OPS-014 §5 desde buscar_politicas; la interfaz y el comando
  llaman al mismo responder(), y dos corridas de la misma pregunta dejan la misma traza. Con el modelo
  real queda pendiente de la llave; desde la interfaz, de T4.1.
- "el prompt no repite definiciones (bandera roja de C1.4)": el prompt no trae ningún texto de
  config/supuestos.yaml ni cifras de la rúbrica, y con una copia de config/ que excluye las
  devoluciones P03 da 17,272,235.76 con el mismo prompt (la mitad pendiente de T1.1).

La base se construye en un directorio temporal con asegurar_base(), como en cada turno. Si la extensión
fts no está instalada, estas pruebas se saltan: sin red no se descarga (T0.5).
"""

import datetime as dt
import hashlib
import json
import re

import duckdb
import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from agente import agente, construir, definiciones
from agente.herramientas import buscar_politicas as bp
from agente.herramientas.comun import estimar_tokens

HOY = dt.date(2026, 10, 31)
HERRAMIENTAS = ("ventas", "inventario", "caducidades", "tickets", "buscar_politicas")
P03 = "¿Cuánto vendimos en total de junio a agosto?"
P06 = "el yogurt del refri caduca el viernes, hasta cuando lo puedo vender?"
# La consulta que escribiría el modelo para P06 con las palabras de los documentos (T2.7).
CONSULTA_P06 = "retiro de anaquel de lácteos, yogur, días antes de la caducidad"


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def base(fts, tmp_path_factory):
    """Ruta de la base del módulo: el primer turno la construye con asegurar_base()."""
    return tmp_path_factory.mktemp("agente") / "tienda.duckdb"


def preguntar(pregunta, modelo, base, **opciones):
    return agente.responder(pregunta, modelo=modelo, base=base, hoy_real=HOY, **opciones)


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


def redactar_venta(partes):
    """Lo que haría el modelo con el resultado de ventas: la cifra en la primera línea, tal como llegó."""
    resultado = json.loads(partes[-1].content)
    return (
        f"{resultado['cifras']['venta']} pesos de venta de junio a agosto (corte: {resultado['corte']})\n"
        f"{resultado['definicion']}"
    )


def modelo_p03():
    return guion([("ventas", {"periodo": "de junio a agosto"})], redactar=redactar_venta)


def redactar_regla(partes):
    resultado = json.loads(partes[-1].content)
    fila = next(f for f in resultado["filas"] if f["documento"] == "POL-OPS-014" and f["seccion"] == "5")
    return f"Se retira del anaquel antes de caducar ({fila['cita']})."


def modelo_p06():
    return guion([("buscar_politicas", {"consulta": CONSULTA_P06})], redactar=redactar_regla)


# ------------------------------------------------------------------ prompt de sistema

def test_el_prompt_pesa_850_tokens_o_menos():
    # El usuario subió el tope de 700 a 850 el 2026-11-01 (T3.1). Se mide con count_tokens de Haiku 5.5, que pide la
    # llave y da 846 con 726 estimados: en prosa estimar_tokens cuenta de menos, y 850 son unos 733 estimados.
    assert estimar_tokens(agente.PROMPT_SISTEMA) <= 733


def test_el_prompt_pide_buscar_la_regla_antes_de_negar_datos_de_un_colaborador():
    # Sin esto el modelo niega y remite a Recursos Humanos sin consultar las políticas, y C7.2 pide citar la regla.
    assert "Datos de un colaborador: busca su regla y remite a Recursos Humanos." in agente.PROMPT_SISTEMA


def test_el_prompt_sale_de_su_archivo_versionado_y_su_hash_es_el_del_texto_con_fin_de_linea_lf():
    contenido = agente.ARCHIVO_PROMPT.read_bytes().replace(b"\r\n", b"\n")
    assert agente.PROMPT_SISTEMA == contenido.decode("utf-8")
    # Con core.autocrlf=true el archivo en disco puede estar en CRLF; el blob del commit está en LF.
    assert agente.PROMPT_SHA256 == hashlib.sha256(contenido).hexdigest()


def textos_de_configuracion(s):
    """Los textos de config/supuestos.yaml que no deben repetirse en el prompt (C1.4)."""
    textos = [
        s.ventas.definicion, s.unidad_venta.definicion, s.margen.supuesto, s.tickets.sin_cerrar,
        s.tickets.cerrado, s.cobertura.metodo, s.aviso_fecha_relativa.split("{")[0],
    ]
    textos += list(s.metricas_no_calculables.values())
    textos += [alcance.salvedad for alcance in s.alcance_documentos.values()]
    textos += [duda.texto for duda in s.dudas.values()]
    return textos


def test_el_prompt_no_repite_ningun_texto_de_las_definiciones():
    prompt = agente.PROMPT_SISTEMA.casefold()
    for texto in textos_de_configuracion(definiciones.supuestos()):
        assert texto.casefold() not in prompt, texto


@pytest.mark.parametrize("fragmento", [
    "venta neta", "devoluciones descontadas", "unidades negativas", "fecha_cierre", "sku_sustituto",
    "punto de reorden", "1.28", "8 de cada 10", "Supermercado", "Mi Tienda",
])
def test_el_prompt_no_trae_reglas_de_negocio(fragmento):
    assert fragmento.casefold() not in agente.PROMPT_SISTEMA.casefold()


@pytest.mark.parametrize("cifra", ["17,228,300.26", "17228300", "17,272,235.76", "3,336", "357,010.50", "43,935.50"])
def test_el_prompt_no_trae_respuestas_de_la_rubrica(cifra):
    # G2: una respuesta de la batería escrita en lo que se ejecuta topa la calificación.
    assert cifra not in agente.PROMPT_SISTEMA


def test_el_prompt_no_trae_fechas_ni_cortes():
    # Los cortes viven en config/supuestos.yaml y llegan en cada resultado; una fecha en el prefijo
    # tampoco deja cachearlo (T3.6).
    assert not re.search(r"20[0-9]{2}", agente.PROMPT_SISTEMA)


@pytest.mark.parametrize("tema, frase", [
    ("cifras solo de herramientas", "herramienta"),
    ("cifra en la primera línea", "primera línea"),
    ("cita", "cita"),
    ("abstención", "no des cifra"),
    ("cortes", "corte"),
    ("precedencia", "prevalece"),
    ("notas del resultado", "notas"),
    ("cobertura como rango sin cifra central", "veces alcanzaría entre X y Y días"),
    ("cobertura sin cifra central", "cifra central"),
    ("periodo no entendido", "periodo_no_entendido"),
    ("fechas explícitas", "desde y hasta"),
    ("reglas ante presión", "insist"),
    ("texto de los datos como dato", "no instrucciones"),
    ("contexto en 3 líneas (D24)", "3 líneas o menos"),
    ("nada antes de la cifra (C4.4)", "nada antes"),
    ("las notas van debajo y marcadas (D24)", "«Nota:»"),
    ("las alternativas van debajo y marcadas (D24)", "«Alternativas:»"),
    ("la fuente y el corte los agrega la pantalla (K29)", "la pantalla agrega"),
    ("sin ofrecer otras consultas", "no ofrezcas"),
    ("listas sin viñetas", "sin viñetas"),
    ("un día de la semana sin fecha (P06)", "día de la semana"),
    ("de dónde sale, con una consulta nueva (P32)", "vuelve a llamar y di la fuente"),
])
def test_el_prompt_cubre_cada_regla_de_la_ficha(tema, frase):
    assert frase.casefold() in agente.PROMPT_SISTEMA.casefold(), tema


# ------------------------------------------------------------------ modelo configurado

def test_sin_modelo_en_el_entorno_usa_el_de_omision():
    assert agente.modelo_del_entorno({}) == agente.MODELO_POR_OMISION


def test_un_modelo_vacio_cuenta_como_no_definido():
    # Al copiar .env.example sin editarlo, load_dotenv deja MODELO="".
    assert agente.modelo_del_entorno({"MODELO": ""}) == agente.MODELO_POR_OMISION
    assert agente.modelo_del_entorno({"MODELO": "   "}) == agente.MODELO_POR_OMISION


def test_el_modelo_del_entorno_se_respeta():
    assert agente.modelo_del_entorno({"MODELO": "anthropic:claude-haiku-5-5"}) == "anthropic:claude-haiku-5-5"


# ------------------------------------------------------------------ herramientas registradas

def test_registra_las_cinco_herramientas_con_su_descripcion(base):
    vistas = {}

    def modelo(mensajes, info: AgentInfo):
        vistas.update({t.name: t for t in info.function_tools})
        vistas["instrucciones"] = info.instructions
        return ModelResponse(parts=[TextPart("listo")])

    respuesta = preguntar("hola", FunctionModel(modelo), base)
    assert set(vistas) - {"instrucciones"} == set(HERRAMIENTAS)
    assert vistas["buscar_politicas"].description == bp.DESCRIPCION
    for nombre in HERRAMIENTAS:
        assert vistas[nombre].description, nombre
    assert vistas["instrucciones"] == agente.PROMPT_SISTEMA.strip()  # Pydantic AI quita el salto final
    assert respuesta.traza.herramientas == HERRAMIENTAS


def test_el_esquema_de_cada_herramienta_es_el_de_su_entrada(base):
    vistas = {}

    def modelo(mensajes, info: AgentInfo):
        vistas.update({t.name: t.parameters_json_schema for t in info.function_tools})
        return ModelResponse(parts=[TextPart("listo")])

    preguntar("hola", FunctionModel(modelo), base)
    assert vistas["ventas"]["title"] == "EntradaVentas"
    assert vistas["inventario"]["title"] == "EntradaInventario"
    assert vistas["caducidades"]["title"] == "EntradaCaducidades"
    assert vistas["tickets"]["title"] == "EntradaTickets"
    assert vistas["buscar_politicas"]["title"] == "EntradaPoliticas"
    assert vistas["buscar_politicas"]["required"] == ["consulta"]


def test_las_cinco_herramientas_en_paralelo_toman_cada_una_su_cursor(base):
    respuesta = preguntar("de todo", TestModel(call_tools="all"), base)
    assert sorted(e.herramienta for e in respuesta.evidencias) == sorted(HERRAMIENTAS)
    assert respuesta.estado == "ok"


# ------------------------------------------------------------------ P03

def test_p03_da_la_venta_neta_desde_la_herramienta_con_la_cifra_en_la_primera_linea(base):
    respuesta = preguntar(P03, modelo_p03(), base)
    assert respuesta.estado == "ok"
    assert respuesta.texto.splitlines()[0].startswith("17,228,300.26 pesos")
    [evidencia] = respuesta.evidencias
    assert evidencia.herramienta == "ventas"
    assert evidencia.entrada == {"periodo": "de junio a agosto"}
    assert evidencia.resultado.cifras["venta"] == "17,228,300.26"
    assert evidencia.resultado.cifras["devoluciones_descontadas"] == "43,935.50"
    # La consulta que corrió queda en la evidencia para la traza, pero no viaja al modelo.
    assert "SELECT" in evidencia.resultado.consulta


def test_al_modelo_le_llega_texto_modelo_sin_la_consulta(base):
    respuesta = preguntar(P03, modelo_p03(), base)
    [evidencia] = respuesta.evidencias
    [devuelto] = devueltos(respuesta.mensajes)
    assert devuelto.content == evidencia.resultado.texto_modelo()
    assert "SELECT" not in devuelto.content
    assert devuelto.tool_call_id == evidencia.id_llamada


def test_p03_cambia_con_una_copia_de_supuestos_sin_tocar_el_prompt(fts, copia_config, tmp_path):
    base_copia = tmp_path / "tienda.duckdb"
    antes = preguntar(P03, modelo_p03(), base_copia, dir_config=copia_config)
    assert antes.evidencias[0].resultado.cifras["venta"] == "17,228,300.26"

    ruta = copia_config / "supuestos.yaml"
    texto = ruta.read_text(encoding="utf-8")
    ruta.write_text(texto.replace("incluir_devoluciones: true", "incluir_devoluciones: false"), encoding="utf-8")

    # El turno anterior cerró su conexión: asegurar_base() puede reemplazar la base sin BaseEnUso.
    despues = preguntar(P03, modelo_p03(), base_copia, dir_config=copia_config)
    assert despues.traza.base_reconstruida
    assert despues.evidencias[0].resultado.cifras["venta"] == "17,272,235.76"
    assert despues.texto.splitlines()[0].startswith("17,272,235.76 pesos")
    assert "sin restar las devoluciones" in despues.texto
    assert despues.traza.prompt_sha256 == antes.traza.prompt_sha256 == agente.PROMPT_SHA256


def test_la_misma_pregunta_deja_la_misma_traza(base):
    # La interfaz (T4.1) y el comando de evaluación (T5.3) llaman a este mismo responder().
    primera = preguntar(P03, modelo_p03(), base).traza
    segunda = preguntar(P03, modelo_p03(), base).traza
    assert primera.prompt_sha256 == segunda.prompt_sha256 == agente.PROMPT_SHA256
    assert primera.herramientas == segunda.herramientas
    assert primera.herramientas_sha256 == segunda.herramientas_sha256
    assert primera.modelo == segunda.modelo
    assert primera.llamadas == segunda.llamadas
    assert len(primera.llamadas) == 2
    assert all(llamada.entrada_tokens > 0 for llamada in primera.llamadas)
    assert primera.entrada_tokens == sum(llamada.entrada_tokens for llamada in primera.llamadas)
    assert primera.salida_tokens == sum(llamada.salida_tokens for llamada in primera.llamadas)


def test_la_traza_trae_el_hash_de_las_fuentes_de_la_base(base):
    respuesta = preguntar(P03, modelo_p03(), base)
    assert re.fullmatch(r"[0-9a-f]{64}", respuesta.traza.hash_fuentes)


# ------------------------------------------------------------------ P06

def test_p06_cita_la_seccion_que_trae_buscar_politicas(base):
    respuesta = preguntar(P06, modelo_p06(), base)
    [evidencia] = respuesta.evidencias
    assert evidencia.herramienta == "buscar_politicas"
    assert evidencia.resultado.estado == "ok"
    citas = {fila["cita"] for fila in evidencia.resultado.filas}
    cita = re.search(r"\((POL-OPS-014 §5[^)]*)\)", respuesta.texto).group(1)
    assert cita in citas


# ------------------------------------------------------------------ evidencias y errores

def test_un_argumento_invalido_vuelve_al_modelo_y_no_deja_evidencia(base):
    modelo = guion(
        [("ventas", {"metrica": "inventada"})],
        [("ventas", {"periodo": "de junio a agosto"})],
        redactar=redactar_venta,
    )
    respuesta = preguntar(P03, modelo, base)
    [evidencia] = respuesta.evidencias
    assert evidencia.id_llamada == "v1-0"
    assert respuesta.traza.reintentos_validacion == 1
    assert respuesta.texto.startswith("17,228,300.26")


def test_un_error_con_tipo_llega_como_evidencia_sin_cifras(base):
    modelo = guion([("ventas", {"metrica": "ticket_promedio"})], redactar=lambda partes: "No se puede calcular.")
    respuesta = preguntar("cual es el ticket promedio", modelo, base)
    [evidencia] = respuesta.evidencias
    assert evidencia.resultado.estado == "metrica_no_calculable"
    assert evidencia.resultado.cifras == {}


def test_las_evidencias_siguen_el_orden_de_las_llamadas(base):
    modelo = guion(
        [("inventario", {}), ("ventas", {}), ("tickets", {})],
        redactar=lambda partes: "listo",
    )
    respuesta = preguntar("resumen", modelo, base)
    assert [e.id_llamada for e in respuesta.evidencias] == ["v0-0", "v0-1", "v0-2"]
    assert [e.herramienta for e in respuesta.evidencias] == ["inventario", "ventas", "tickets"]


def test_ventas_recibe_la_fecha_real_para_el_aviso_de_una_fecha_relativa(base):
    modelo = guion([("ventas", {"periodo": "ayer"})], redactar=lambda partes: "listo")
    respuesta = preguntar("cuanto vendimos ayer", modelo, base)
    resultado = respuesta.evidencias[0].resultado
    assert any("31 de octubre de 2026" in nota for nota in resultado.notas)


def test_sin_herramientas_no_hay_evidencias(base):
    respuesta = preguntar("hola", TestModel(call_tools=[], custom_output_text="Hola."), base)
    assert respuesta.texto == "Hola."
    assert respuesta.evidencias == ()
    assert len(respuesta.traza.llamadas) == 1


# ------------------------------------------------------------------ historial y conexión

def test_el_historial_llega_al_modelo_y_las_instrucciones_siguen(base):
    primera = preguntar(P03, modelo_p03(), base)
    vistos = {}

    def modelo(mensajes, info: AgentInfo):
        vistos["mensajes"] = list(mensajes)
        vistos["instrucciones"] = info.instructions
        return ModelResponse(parts=[TextPart("listo")])

    segunda = agente.responder("y en julio?", primera.mensajes, modelo=FunctionModel(modelo), base=base, hoy_real=HOY)
    assert vistos["instrucciones"] == agente.PROMPT_SISTEMA.strip()
    assert vistos["mensajes"][: len(primera.mensajes)] == list(primera.mensajes)
    # mensajes trae solo lo del turno: la pregunta nueva y la respuesta.
    assert isinstance(segunda.mensajes[0], ModelRequest)
    assert segunda.mensajes[0].parts[0].content == "y en julio?"
    assert len(segunda.mensajes) == 2


def test_no_deja_la_conexion_abierta_entre_turnos(base):
    preguntar(P03, modelo_p03(), base)
    # Con una conexión de solo lectura abierta en este proceso, DuckDB no deja abrir la misma base para
    # escribir; asegurar_base() necesita reemplazarla cuando cambian las fuentes.
    with duckdb.connect(str(base)) as escritura:
        assert escritura.execute("SELECT 1").fetchall() == [(1,)]


def test_con_un_csv_que_falta_dice_que_no_pudo_consultar_sin_llamar_al_modelo(fts, copia_datos, tmp_path):
    (copia_datos / "ventas.csv").rename(copia_datos / "ventas_renombrado.csv")

    def modelo(mensajes, info):
        raise AssertionError("no debe llamar al modelo")

    respuesta = preguntar(P03, FunctionModel(modelo), tmp_path / "tienda.duckdb", dir_datos=copia_datos)
    assert respuesta.estado == "fuente_no_disponible"
    assert "ventas.csv" in respuesta.texto
    assert not re.search(r"[0-9]", respuesta.texto)
    assert respuesta.evidencias == ()
    assert respuesta.traza.llamadas == ()


# --- Lo que sale al proveedor: un teléfono o un correo en la pregunta (K39). Datos inventados. -----------------------

def modelo_que_guarda(vistos):
    def modelo(mensajes, info: AgentInfo):
        vistos.extend(
            parte.content for mensaje in mensajes if isinstance(mensaje, ModelRequest) for parte in mensaje.parts
            if isinstance(parte, UserPromptPart)
        )
        return ModelResponse(parts=[TextPart("No doy datos de contacto.")])

    return FunctionModel(modelo)


@pytest.mark.parametrize(
    ("pregunta", "dato", "marca"),
    [
        ("¿De quién es el 55 0000 0000?", "55 0000 0000", "<TELEFONO_1>"),
        ("Escríbele a persona@ejemplo.com", "persona@ejemplo.com", "<CORREO_1>"),
    ],
)
def test_un_dato_de_contacto_en_la_pregunta_no_sale_al_proveedor(base, pregunta, dato, marca):
    vistos = []
    respuesta = preguntar(pregunta, modelo_que_guarda(vistos), base)

    assert respuesta.estado == "ok"
    assert len(vistos) == 1
    assert dato not in vistos[0]
    assert marca in vistos[0]
    guardada = respuesta.mensajes[0].parts[0].content
    assert dato not in guardada


@pytest.mark.parametrize(
    "pregunta",
    [
        P03,
        "285 tickets; folios 1025, 1184, 1254 y 1278; 17,228,300.26 pesos",
        "¿Cuántos tickets hubo del 2026-06-01 al 2026-08-31?",
    ],
)
def test_una_pregunta_sin_datos_de_contacto_sale_tal_cual(base, pregunta):
    vistos = []
    preguntar(pregunta, modelo_que_guarda(vistos), base)
    assert vistos == [pregunta]
