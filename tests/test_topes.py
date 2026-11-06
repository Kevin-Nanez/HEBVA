"""Topes de un turno (T3.2, K19): solicitudes, herramientas, segundos, tokens y largo del mensaje.

Criterio de aceptación de la ficha, y lo que cubre cada prueba sin red ni llaves:

- "una prueba con FunctionModel que pide 6 llamadas termina en estado 'tope', sin cifra": un modelo
  simulado que pide la herramienta de ventas en 6 respuestas seguidas se corta en la cuarta solicitud, con
  estado "tope" y un texto sin dígitos (test_un_modelo_que_pide_seis_rondas_...).
- "un modelo simulado lento se corta a los 60 s": una prueba no puede esperar 60 s. El modelo lento duerme
  mucho más que el tope, que se baja con Topes(segundos=...); otra prueba comprueba que el valor por
  omisión es 60 y que responder() le pasa a Pydantic AI lo que queda de esos 60 s, contando lo que tardó
  antes de llamar al modelo.

La base se construye una vez en un directorio temporal. Si la extensión fts no está instalada, estas pruebas
se saltan: sin red no se descarga (T0.5).
"""

import asyncio
import datetime as dt
import re
import threading
import time
from types import SimpleNamespace
from typing import get_args

import duckdb
import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage

from agente import agente, construir, definiciones
from agente.consultas import Consultas, abrir_consultas
from agente.herramientas import ventas as modulo_ventas
from agente.herramientas.comun import Resultado

HOY = dt.date(2026, 10, 31)
P03 = "¿Cuánto vendimos en total de junio a agosto?"
VENTAS_P03 = {"periodo": "de junio a agosto"}
CIFRA_P03 = "17,228,300.26"


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def base(fts, tmp_path_factory):
    """La base del módulo, ya construida: las pruebas de tiempo no deben pagar la construcción."""
    ruta = tmp_path_factory.mktemp("topes") / "tienda.duckdb"
    construir.asegurar_base(construir.DIR_DATOS, ruta)
    return ruta


def preguntar(pregunta, modelo, base, **opciones):
    return agente.responder(pregunta, modelo=modelo, base=base, hoy_real=HOY, **opciones)


def vueltas_del_turno(mensajes):
    """Respuestas del modelo después de la última pregunta del usuario."""
    ultima = max(i for i, m in enumerate(mensajes) if any(isinstance(p, UserPromptPart) for p in m.parts))
    return sum(isinstance(m, ModelResponse) for m in mensajes[ultima:])


def modelo_de_rondas(rondas, *, final="Listo."):
    """Modelo simulado que pide la herramienta de ventas en las primeras `rondas` respuestas y después redacta.

    El registro cuenta cuántas veces lo llamaron y con qué ajustes.
    """
    registro = SimpleNamespace(llamadas=0, ajustes=[])

    def funcion(mensajes, info: AgentInfo):
        registro.llamadas += 1
        registro.ajustes.append(info.model_settings)
        vuelta = vueltas_del_turno(mensajes)
        if vuelta < rondas:
            return ModelResponse(parts=[ToolCallPart("ventas", VENTAS_P03, tool_call_id=f"ronda-{vuelta}")])
        return ModelResponse(parts=[TextPart(final)])

    return FunctionModel(funcion, model_name="rondas"), registro


def modelo_lento_despues_de(rondas, segundos):
    """Pide la herramienta de ventas en las primeras `rondas` respuestas y después tarda `segundos` en contestar."""
    registro = SimpleNamespace(llamadas=0)

    async def funcion(mensajes, info: AgentInfo):
        registro.llamadas += 1
        vuelta = vueltas_del_turno(mensajes)
        if vuelta < rondas:
            return ModelResponse(parts=[ToolCallPart("ventas", VENTAS_P03, tool_call_id=f"ronda-{vuelta}")])
        await asyncio.sleep(segundos)
        return ModelResponse(parts=[TextPart(f"{CIFRA_P03} pesos, pero tarde")])

    return FunctionModel(funcion, model_name="lento"), registro


def sin_digitos(texto):
    return not re.search(r"[0-9]", texto)


# ------------------------------------------------------------------ los valores de K19

def test_los_topes_por_omision_son_los_del_plan():
    # T3.2: 4 solicitudes por turno, 60 s en responder() y max_tokens por llamada con lugar para el razonamiento.
    assert agente.TOPES.solicitudes == 4
    assert agente.TOPES.segundos == 60
    # Arriba de la meta de salida por turno (p90 de 1,200) y abajo de los 21,333 desde los que el SDK transmite.
    assert 1200 < agente.TOPES.max_tokens < 21_333
    assert agente.TOPES.herramientas >= 5  # las cinco herramientas en una sola respuesta tienen que caber
    assert agente.TOPES.caracteres >= 500


def test_max_tokens_deja_lugar_al_razonamiento_en_medium():
    # Con Haiku en "medium" (D8-D), «¿qué productos corren riesgo de caducar sin venderse?» razonó 2,340 tokens,
    # escribió 1,756 sobre 18 lotes y se cortó en 4,096 (logs/turnos.jsonl, 2026-11-05 22:01). El tope tiene que
    # dejar al menos el doble de ese turno.
    assert agente.TOPES.max_tokens >= 2 * 4096


def test_el_tope_de_la_pregunta_es_de_1500_caracteres():
    # El usuario lo subió de 1,000 a 1,500 el 2026-11-01. El brief no pide un largo; K19 pide que haya tope.
    assert agente.TOPES.caracteres == 1500


def test_el_tope_de_herramientas_es_de_12_ejecuciones_por_turno():
    # El usuario lo subió de 10 a 12 el 2026-11-01.
    assert agente.TOPES.herramientas == 12


def test_cada_llamada_al_modelo_lleva_max_tokens(base):
    # Sin max_tokens, Pydantic AI pide 128,000 tokens a Haiku 5.5 y lo transmite en streaming (hallazgo 5).
    modelo, registro = modelo_de_rondas(1, final=f"{CIFRA_P03} pesos")
    respuesta = preguntar(P03, modelo, base)
    assert respuesta.estado == "ok"
    assert registro.llamadas == 2
    # Los ajustes de otras tareas (reintentos, esfuerzo, caché) pueden sumarse a este; max_tokens no puede faltar.
    assert [ajustes["max_tokens"] for ajustes in registro.ajustes] == [agente.TOPES.max_tokens] * 2


def test_responder_pasa_a_pydantic_ai_los_limites_y_el_tiempo_que_queda(base, monkeypatch):
    recibido = {}
    original = agente.AGENTE.run_sync

    def espia(*argumentos, **opciones):
        recibido.update(opciones)
        return original(*argumentos, **opciones)

    monkeypatch.setattr(agente.AGENTE, "run_sync", espia)
    modelo, _ = modelo_de_rondas(1)
    preguntar(P03, modelo, base)

    assert recibido["usage_limits"].request_limit == 4
    assert recibido["usage_limits"].tool_calls_limit == agente.TOPES.herramientas
    assert isinstance(recibido["usage"], RunUsage)
    # Lo que queda de los 60 s: casi todo en un turno que apenas empieza, y nunca más.
    assert 50 < recibido["timeout"] <= 60
    assert recibido["model_settings"]["max_tokens"] == agente.TOPES.max_tokens


# ------------------------------------------------------------------ solicitudes al modelo

def test_un_modelo_que_pide_seis_rondas_de_herramientas_termina_en_tope_sin_cifra(base):
    modelo, registro = modelo_de_rondas(6)
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "solicitudes"
    # La quinta solicitud no sale: el tope se aplica antes de llamar al modelo, no después.
    assert registro.llamadas == 4
    assert len(respuesta.traza.llamadas) == 4
    # Sin cifra: ni un dígito, y tampoco la que dieron las herramientas.
    assert sin_digitos(respuesta.texto)
    assert CIFRA_P03 not in respuesta.texto
    # Lo que corrió antes del corte queda como evidencia, para el panel de traza y el registro.
    assert respuesta.evidencias
    assert {e.herramienta for e in respuesta.evidencias} == {"ventas"}
    assert respuesta.evidencias[0].resultado.cifras["venta"] == CIFRA_P03


def test_un_modelo_que_pide_herramientas_sin_parar_se_corta_en_el_tope(base):
    # La prueba de K19, tal cual: un bucle de herramientas no termina por sí solo, lo termina el tope.
    modelo, registro = modelo_de_rondas(10**9)
    respuesta = preguntar(P03, modelo, base)
    assert respuesta.estado == "tope"
    assert registro.llamadas == 4
    assert sin_digitos(respuesta.texto)


def test_cuatro_solicitudes_alcanzan_si_la_cuarta_redacta(base):
    # El tope es de 4 solicitudes, no de 3: tres rondas de herramientas y la redacción caben.
    modelo, registro = modelo_de_rondas(3, final=f"{CIFRA_P03} pesos de junio a agosto")
    respuesta = preguntar(P03, modelo, base)
    assert respuesta.estado == "ok"
    assert respuesta.traza.tope is None
    assert registro.llamadas == 4
    assert respuesta.texto.startswith(CIFRA_P03)


def test_el_tope_de_solicitudes_se_puede_bajar_para_una_prueba(base):
    modelo, registro = modelo_de_rondas(6)
    respuesta = preguntar(P03, modelo, base, topes=agente.Topes(solicitudes=2))
    assert respuesta.estado == "tope"
    assert registro.llamadas == 2


def test_un_turno_normal_de_cifra_simple_usa_dos_solicitudes_y_no_toca_el_tope(base):
    # D9-A: 2 llamadas siempre en una cifra simple.
    modelo, registro = modelo_de_rondas(1, final=f"{CIFRA_P03} pesos de junio a agosto")
    respuesta = preguntar(P03, modelo, base)
    assert respuesta.estado == "ok"
    assert registro.llamadas == 2
    assert len(respuesta.traza.llamadas) == 2


# ------------------------------------------------------------------ llamadas a herramientas

def test_las_cinco_herramientas_en_una_respuesta_caben_en_el_tope_por_omision(base):
    argumentos = {nombre: {} for nombre in agente.NOMBRES_HERRAMIENTAS}
    argumentos["buscar_politicas"] = {"consulta": "retiro de anaquel"}

    def todas(mensajes, info: AgentInfo):
        if vueltas_del_turno(mensajes) == 0:
            partes = [ToolCallPart(nombre, args, tool_call_id=f"t-{nombre}") for nombre, args in argumentos.items()]
            return ModelResponse(parts=partes)
        return ModelResponse(parts=[TextPart("Listo.")])

    respuesta = preguntar("de todo", FunctionModel(todas), base)
    assert respuesta.estado == "ok"
    assert len(respuesta.evidencias) == 5


def test_un_exceso_de_llamadas_a_herramientas_corta_antes_de_ejecutarlas(base):
    def tres_a_la_vez(mensajes, info: AgentInfo):
        partes = [ToolCallPart("ventas", VENTAS_P03, tool_call_id=f"p-{i}") for i in range(3)]
        return ModelResponse(parts=partes)

    respuesta = preguntar(P03, FunctionModel(tres_a_la_vez), base, topes=agente.Topes(herramientas=2))
    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "herramientas"
    # Pydantic AI revisa el tope antes de ejecutar el grupo: ninguna de las tres corrió.
    assert respuesta.evidencias == ()
    assert sin_digitos(respuesta.texto)


# ------------------------------------------------------------------ segundos

def test_un_modelo_lento_se_corta_cuando_se_acaba_el_tiempo(base):
    modelo, registro = modelo_lento_despues_de(0, segundos=30)
    inicio = time.monotonic()
    respuesta = preguntar(P03, modelo, base, topes=agente.Topes(segundos=0.5))
    duracion = time.monotonic() - inicio

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "segundos"
    assert duracion < 10  # el modelo dormía 30 s
    assert sin_digitos(respuesta.texto)
    assert CIFRA_P03 not in respuesta.texto


def test_un_corte_por_tiempo_conserva_lo_que_alcanzo_a_correr(base):
    modelo, registro = modelo_lento_despues_de(1, segundos=30)
    respuesta = preguntar(P03, modelo, base, topes=agente.Topes(segundos=4))

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "segundos"
    # La primera solicitud y la herramienta terminaron antes del corte: se ven en la evidencia y en los tokens.
    assert [e.herramienta for e in respuesta.evidencias] == ["ventas"]
    assert len(respuesta.traza.llamadas) == 1
    assert respuesta.traza.entrada_tokens == respuesta.traza.llamadas[0].entrada_tokens > 0
    assert CIFRA_P03 not in respuesta.texto


def test_el_tiempo_se_mide_desde_que_entra_responder_y_no_desde_que_empieza_el_modelo(base, monkeypatch):
    # Una base que tarda en prepararse se come el presupuesto: si ya no queda tiempo, el modelo ni se llama.
    asegurar = construir.asegurar_base

    def base_lenta(*argumentos, **opciones):
        time.sleep(0.6)
        return asegurar(*argumentos, **opciones)

    monkeypatch.setattr(construir, "asegurar_base", base_lenta)
    modelo, registro = modelo_de_rondas(1)
    respuesta = preguntar(P03, modelo, base, topes=agente.Topes(segundos=0.3))

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "segundos"
    assert registro.llamadas == 0
    assert respuesta.traza.llamadas == ()


# ------------------------------------------------------------------ tokens

def test_una_respuesta_cortada_por_max_tokens_no_se_muestra_como_si_estuviera_completa(base):
    def cortada(mensajes, info: AgentInfo):
        return ModelResponse(parts=[TextPart(f"{CIFRA_P03} pesos de ven")], finish_reason="length")

    respuesta = preguntar(P03, FunctionModel(cortada), base)
    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "tokens"
    assert CIFRA_P03 not in respuesta.texto
    assert sin_digitos(respuesta.texto)


def test_un_razonamiento_que_se_come_todo_max_tokens_es_un_tope(base):
    # Gemini y Haiku cuentan el razonamiento dentro de max_tokens: puede no quedar nada para el texto.
    def solo_razona(mensajes, info: AgentInfo):
        return ModelResponse(parts=[ThinkingPart("pensando...")], finish_reason="length")

    respuesta = preguntar(P03, FunctionModel(solo_razona), base)
    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "tokens"
    assert sin_digitos(respuesta.texto)


def test_un_fallo_del_modelo_que_no_es_un_tope_sigue_propagandose(base):
    # La comunicación de las fallas del modelo es de T3.3: el tope no las esconde.
    def vacia(mensajes, info: AgentInfo):
        return ModelResponse(parts=[])

    with pytest.raises(UnexpectedModelBehavior):
        preguntar(P03, FunctionModel(vacia), base)
    # Y la base no queda abierta por el error.
    with duckdb.connect(str(base)) as escritura:
        assert escritura.execute("SELECT 1").fetchall() == [(1,)]


# ------------------------------------------------------------------ largo del mensaje

def test_un_mensaje_demasiado_largo_no_llama_al_modelo_ni_arma_la_base(tmp_path):
    base_sin_construir = tmp_path / "tienda.duckdb"
    largo = "1234567890" * (agente.TOPES.caracteres // 10 + 1)

    def no_debe_llamarse(mensajes, info):
        raise AssertionError("no debe llamar al modelo")

    respuesta = preguntar(largo, FunctionModel(no_debe_llamarse), base_sin_construir)

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "caracteres"
    assert not base_sin_construir.exists()
    assert sin_digitos(respuesta.texto)  # no repite el mensaje
    assert respuesta.evidencias == ()
    assert respuesta.traza.llamadas == ()
    # El mensaje largo no entra al historial del turno siguiente.
    assert respuesta.mensajes == ()


def test_un_mensaje_del_largo_del_tope_si_pasa(base):
    exacto = "x" * agente.TOPES.caracteres
    modelo, registro = modelo_de_rondas(0, final="No entendí la pregunta.")
    respuesta = preguntar(exacto, modelo, base)
    assert respuesta.estado == "ok"
    assert registro.llamadas == 1

    demasiado = "x" * (agente.TOPES.caracteres + 1)
    modelo, registro = modelo_de_rondas(0)
    respuesta = preguntar(demasiado, modelo, base)
    assert respuesta.estado == "tope"
    assert registro.llamadas == 0


# ------------------------------------------------------------------ un reintento del mismo turno

@pytest.fixture
def turno(base):
    consultas = abrir_consultas(base)
    yield agente.Turno(consultas, definiciones.supuestos(), HOY)
    consultas.cerrar()


def test_un_reintento_del_turno_descuenta_de_las_mismas_solicitudes(turno):
    # Un 429 (T3.3) o una discrepancia del verificador (D16, T3.4) corren con el Presupuesto del turno: no
    # abren otras 4 solicitudes.
    presupuesto = agente.Presupuesto(agente.TOPES)
    primera = agente._correr(P03, (), modelo_de_rondas(1)[0], turno, presupuesto)
    assert primera.tope is None
    assert presupuesto.uso.requests == 2

    modelo, registro = modelo_de_rondas(6)
    segunda = agente._correr("otra vez", (), modelo, turno, presupuesto)
    assert segunda.tope == "solicitudes"
    assert registro.llamadas == 2  # quedaban 2 de las 4


def test_un_reintento_del_turno_descuenta_del_mismo_reloj(turno):
    # El turno ya gastó 59.5 de sus 60 s: el reintento tiene medio segundo, no 60 nuevos.
    presupuesto = agente.Presupuesto(agente.Topes(segundos=60), inicio=time.monotonic() - 59.5)
    modelo, _ = modelo_lento_despues_de(0, segundos=30)
    inicio = time.monotonic()
    corrida = agente._correr(P03, (), modelo, turno, presupuesto)
    assert corrida.tope == "segundos"
    assert time.monotonic() - inicio < 10


# ------------------------------------------------------------------ el mensaje y el historial de un tope

MOTIVOS = ("solicitudes", "herramientas", "segundos", "tokens", "caracteres")


def test_hay_un_mensaje_por_cada_motivo_de_tope():
    assert set(get_args(agente.MotivoTope)) == set(MOTIVOS)
    assert set(agente.MENSAJES_DE_TOPE) == set(MOTIVOS)


@pytest.mark.parametrize("motivo", MOTIVOS)
def test_cada_tope_tiene_un_mensaje_en_espanol_sin_cifras_ni_jerga(motivo):
    texto = agente.MENSAJES_DE_TOPE[motivo]
    assert sin_digitos(texto)
    assert texto.endswith(".")
    for jerga in ("UsageLimit", "Traceback", "request_limit", "tokens", "timeout"):
        assert jerga.casefold() not in texto.casefold(), jerga


def test_el_historial_de_un_turno_cortado_es_valido_para_el_siguiente(base):
    modelo, _ = modelo_de_rondas(6)
    cortado = preguntar(P03, modelo, base)
    assert cortado.estado == "tope"

    # Solo la pregunta y el mensaje del tope: ninguna llamada a herramienta sin su resultado.
    pregunta, mensaje = cortado.mensajes
    assert isinstance(pregunta, ModelRequest) and pregunta.parts[0].content == P03
    assert isinstance(mensaje, ModelResponse) and mensaje.parts[0].content == cortado.texto

    vistos = {}

    def siguiente(mensajes, info: AgentInfo):
        vistos["mensajes"] = list(mensajes)
        return ModelResponse(parts=[TextPart("Dime la pregunta por partes.")])

    seguimiento = agente.responder(
        "mejor solo junio", cortado.mensajes, modelo=FunctionModel(siguiente), base=base, hoy_real=HOY
    )
    assert seguimiento.estado == "ok"
    assert vistos["mensajes"][: len(cortado.mensajes)] == list(cortado.mensajes)


def test_un_tope_no_deja_la_conexion_abierta(base):
    modelo, _ = modelo_de_rondas(6)
    assert preguntar(P03, modelo, base).estado == "tope"
    with duckdb.connect(str(base)) as escritura:
        assert escritura.execute("SELECT 1").fetchall() == [(1,)]


# ------------------------------------------------------------------ cerrar la base después de un corte

class ConsultasOcupada:
    """Una Consultas que sigue ocupada por una herramienta que el tope de tiempo dejó atrás."""

    def __init__(self, veces_ocupada):
        self.veces_ocupada = veces_ocupada
        self.intentos = 0
        self.cerrada = False

    def cerrar(self):
        self.intentos += 1
        if self.intentos <= self.veces_ocupada:
            raise duckdb.ResourceInUseException("connection is busy with another call")
        self.cerrada = True


def test_cerrar_espera_a_que_termine_la_herramienta_que_el_tope_dejo_atras():
    # Una herramienta síncrona corre en un hilo que el corte por tiempo no detiene (comprobado con DuckDB 2.0.0):
    # cerrar la conexión mientras ese hilo consulta lanza ResourceInUseException al instante.
    consultas = ConsultasOcupada(veces_ocupada=3)
    agente._cerrar(consultas, espera=5)
    assert consultas.cerrada
    assert consultas.intentos == 4


def test_cerrar_no_espera_para_siempre():
    consultas = ConsultasOcupada(veces_ocupada=10**9)
    inicio = time.monotonic()
    with pytest.raises(duckdb.ResourceInUseException):
        agente._cerrar(consultas, espera=0.2)
    assert time.monotonic() - inicio < 5
    assert not consultas.cerrada


def test_un_corte_por_tiempo_con_una_herramienta_en_plena_consulta_devuelve_el_tope_y_no_un_error(base, monkeypatch):
    # Caso real de la sección anterior: el corte por tiempo cancela la espera de la herramienta, pero su hilo
    # sigue dentro de la consulta, y DuckDB no cierra una conexión ocupada. La consulta se queda esperando
    # una señal (una función de usuario de DuckDB) hasta que el temporizador la suelta, ya pasado el tope.
    liberar = threading.Event()

    def bloquea() -> int:
        liberar.wait(10)
        return 1

    def ventas_atorada(cursor, entrada, supuestos, **opciones):
        cursor.create_function("bloquea", bloquea, [], "BIGINT")
        cursor.execute("SELECT bloquea()").fetchall()
        return Resultado(herramienta="ventas")

    cierres = []
    cerrar = Consultas.cerrar

    def cerrar_y_anotar(self):
        try:
            cerrar(self)
        except duckdb.ResourceInUseException:
            cierres.append("ocupada")
            raise
        cierres.append("cerrada")

    monkeypatch.setattr(modulo_ventas, "ventas", ventas_atorada)
    monkeypatch.setattr(Consultas, "cerrar", cerrar_y_anotar)
    modelo, _ = modelo_de_rondas(1)
    # El preámbulo de un turno tarda unos 0.1 s; los márgenes dejan lugar a un equipo mucho más lento.
    temporizador = threading.Timer(2.5, liberar.set)
    temporizador.start()
    try:
        respuesta = preguntar(P03, modelo, base, topes=agente.Topes(segundos=1.5))
    finally:
        liberar.set()
        temporizador.cancel()

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "segundos"
    assert cierres[0] == "ocupada"  # el primer intento de cierre encontró la consulta en curso
    assert cierres[-1] == "cerrada"  # y responder() esperó a que terminara
