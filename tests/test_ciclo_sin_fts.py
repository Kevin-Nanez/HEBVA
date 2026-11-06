"""El ciclo del agente sin la extensión fts: topes, reintentos, verificador, errores con tipo y conversaciones (T5.5).

Por qué existe. Doce módulos de pruebas (el ciclo del agente, los topes, los reintentos, el verificador, el
registro, el historial) se saltan enteros cuando la extensión fts de DuckDB no está instalada, y un equipo
limpio sin red no la tiene. El 2026-11-01, con un directorio personal vacío y un proxy que no responde, la suite
dio 1,944 pruebas pasadas, 483 saltadas y 1 fallida: 475 de las saltadas son esos módulos (las otras 8 piden red a
propósito, con PROBAR_CON_RED=1). Este módulo corre lo esencial de cada criterio de T5.5 con responder() de punta
a punta y un modelo simulado (FunctionModel), sobre una base sin índice de texto, para que el ciclo se pruebe en
cualquier equipo.

No reemplaza a los módulos con fts: lo que depende de BM25 (buscar_politicas y las citas que recupera) se prueba
solo allí. Aquí no hay ninguna prueba que lo necesite, y la primera lo comprueba: la base no trae el índice.

Cómo. La base se arma con asegurar_base() como en cada turno, con dos dobles en lugar de los pasos de fts: cargar
la extensión no hace nada y crear el índice deja la tabla de palabras vacías, que el verificador lee con el
índice de secciones, pero no el índice. Los dobles valen mientras dure el módulo; donde fts está instalada
tampoco se usa, de modo que estas pruebas dan lo mismo en un equipo con la extensión y en uno sin ella.
"""

import asyncio
import datetime as dt
import json
import re
import time
from types import SimpleNamespace

import duckdb
import pytest
from pydantic_ai.exceptions import ModelHTTPError, ModelRateLimitError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agente import agente, consultas, construir, reintentos, verificacion
from agente import historial as modulo_historial
from agente.herramientas import buscar_politicas as bp
from agente.herramientas.comun import Resultado
from agente.reintentos import Reintento

HOY = dt.date(2026, 10, 31)
P03 = "¿Cuánto vendimos en total de junio a agosto?"
VENTAS_P03 = {"periodo": "de junio a agosto"}
CIFRA_P03 = "17,228,300.26"
CIFRA_P03_SIN_DEVOLUCIONES = "17,272,235.76"
CIFRA_INVENTADA = "18,104,551.70"
PREGUNTA_DE_POLITICA = "hasta cuando puedo vender el yogurt?"


# ------------------------------------------------------------------ la base sin índice de texto


def _palabras_vacias_sin_indice(con, palabras_vacias):
    """Lo que crear_indice_texto deja en la base salvo el índice: la tabla de palabras vacías."""
    palabras = sorted(set(construir.leer_palabras_vacias(palabras_vacias)))
    con.execute(f"CREATE TABLE {construir.TABLA_PALABRAS_VACIAS} (sw VARCHAR PRIMARY KEY)")
    con.executemany(f"INSERT INTO {construir.TABLA_PALABRAS_VACIAS} VALUES (?)", [[palabra] for palabra in palabras])


@pytest.fixture(scope="module")
def sin_fts():
    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(construir, "cargar_fts", lambda con, instalar=True: None)
        parche.setattr(consultas, "cargar_fts", lambda con, instalar=False: None)
        parche.setattr(construir, "crear_indice_texto", _palabras_vacias_sin_indice)
        yield


@pytest.fixture(scope="module")
def base(sin_fts, tmp_path_factory):
    """La base del módulo, ya construida: estas pruebas no deben pagar la construcción en cada turno."""
    ruta = tmp_path_factory.mktemp("ciclo_sin_fts") / "tienda.duckdb"
    construir.asegurar_base(construir.DIR_DATOS, ruta)
    return ruta


@pytest.fixture(scope="module")
def secciones(base):
    """El índice de secciones de las políticas, como lo lee el verificador en cada turno."""
    with duckdb.connect(str(base), read_only=True) as con:
        indice = bp.leer_indice(con)
    return lambda: indice.secciones


@pytest.fixture
def esperas(monkeypatch):
    """Cambia las esperas reales por unas que solo anotan los segundos que se pidieron."""
    anotadas = SimpleNamespace(dormidas=[], pausas=[])

    async def dormir(segundos):
        anotadas.dormidas.append(segundos)

    def pausar(segundos):
        anotadas.pausas.append(segundos)

    monkeypatch.setattr(reintentos, "dormir", dormir)
    monkeypatch.setattr(reintentos, "pausar", pausar)
    return anotadas


# ------------------------------------------------------------------ ayudantes


def preguntar(pregunta, modelo, base, historial=(), **opciones):
    return agente.responder(pregunta, historial, modelo=modelo, base=base, hoy_real=HOY, **opciones)


def sin_digitos(texto):
    return not re.search(r"[0-9]", texto)


def vueltas_del_turno(mensajes):
    """Respuestas del modelo después de la última pregunta del usuario."""
    ultima = max(i for i, m in enumerate(mensajes) if any(isinstance(p, UserPromptPart) for p in m.parts))
    return sum(isinstance(m, ModelResponse) for m in mensajes[ultima:])


def ultimo_resultado(mensajes):
    return json.loads([p for m in mensajes for p in m.parts if isinstance(p, ToolReturnPart)][-1].content)


def es_correccion(mensajes):
    """La última pregunta del usuario es el pedido de corrección del verificador."""
    ultima = [p for m in mensajes for p in m.parts if isinstance(p, UserPromptPart)][-1]
    return "herramienta" in ultima.content and ultima.content != P03


def modelo_de_rondas(rondas, *, final="Listo."):
    """Pide la herramienta de ventas en las primeras `rondas` respuestas y después redacta. Cuenta sus llamadas."""
    registro = SimpleNamespace(llamadas=0)

    def funcion(mensajes, info: AgentInfo):
        registro.llamadas += 1
        vuelta = vueltas_del_turno(mensajes)
        if vuelta < rondas:
            return ModelResponse(parts=[ToolCallPart("ventas", VENTAS_P03, tool_call_id=f"ronda-{vuelta}")])
        return ModelResponse(parts=[TextPart(final)])

    return FunctionModel(funcion, model_name="rondas"), registro


def modelo_con_guion(llamadas, redactar, corregir=None):
    """Pide `llamadas` en la primera vuelta y redacta; ante el pedido de corrección usa `corregir`.

    Sin `corregir`, ante el pedido de corrección vuelve a redactar igual: insiste en la fabricación.
    """
    registro = SimpleNamespace(solicitudes=0, pedidos=[])

    def funcion(mensajes, info: AgentInfo):
        registro.solicitudes += 1
        ultimo = mensajes[-1]
        trae_resultados = any(isinstance(p, ToolReturnPart) for p in ultimo.parts)
        if not trae_resultados and llamadas and not es_correccion(mensajes):
            partes = [ToolCallPart(nombre, args, tool_call_id=f"c{i}") for i, (nombre, args) in enumerate(llamadas)]
            return ModelResponse(parts=partes)
        if es_correccion(mensajes):
            registro.pedidos.append(next(p.content for p in ultimo.parts if isinstance(p, UserPromptPart)))
            if corregir is not None:
                return ModelResponse(parts=[TextPart(corregir(mensajes))])
        return ModelResponse(parts=[TextPart(redactar(mensajes))])

    return FunctionModel(funcion, model_name="guion"), registro


def error_http(estado, categoria=None, *, cuerpo=None, cabeceras=None):
    return ModelHTTPError.for_category(
        categoria, status_code=estado, model_name="simulado", body=cuerpo, headers=cabeceras
    )


def limite(retry_after=None):
    cabeceras = None if retry_after is None else {"retry-after": retry_after}
    return error_http(429, ModelRateLimitError, cabeceras=cabeceras)


def llave_invalida():
    return error_http(401, cuerpo={"type": "error", "error": {"type": "authentication_error", "message": "SECRETO"}})


def modelo_p03(*fallas):
    """Como el modelo de P03 (pide ventas y redacta la cifra), pero la solicitud i lanza fallas[i] si no es None."""
    registro = SimpleNamespace(llamadas=0)

    def funcion(mensajes, info: AgentInfo):
        i = registro.llamadas
        registro.llamadas += 1
        if i < len(fallas) and fallas[i] is not None:
            raise fallas[i]
        if vueltas_del_turno(mensajes) == 0:
            return ModelResponse(parts=[ToolCallPart("ventas", VENTAS_P03, tool_call_id="v0")])
        return ModelResponse(parts=[TextPart(f"{ultimo_resultado(mensajes)['cifras']['venta']} pesos de venta")])

    return FunctionModel(funcion, model_name="p03"), registro


# ------------------------------------------------------------------ la base de estas pruebas


def test_la_base_no_trae_el_indice_de_texto_pero_si_los_fragmentos_y_las_palabras_vacias(base):
    # Si esta prueba falla, el resto del módulo ya no prueba el ciclo sin fts.
    with duckdb.connect(str(base), read_only=True) as con:
        esquemas = {fila[0] for fila in con.execute("SELECT schema_name FROM duckdb_schemas()").fetchall()}
        fragmentos = con.execute("SELECT count(*) FROM fragmentos").fetchone()[0]
        palabras = con.execute(f"SELECT count(*) FROM {construir.TABLA_PALABRAS_VACIAS}").fetchone()[0]
    assert construir.ESQUEMA_INDICE not in esquemas
    assert fragmentos == 62
    assert palabras > 0


def test_p03_da_la_cifra_de_la_herramienta_en_dos_solicitudes_y_pasa_la_verificacion(base):
    modelo, registro = modelo_con_guion(
        [("ventas", VENTAS_P03)], lambda m: f"{ultimo_resultado(m)['cifras']['venta']} pesos (corte: 31 de agosto de 2026)"
    )
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "ok"
    assert respuesta.texto.startswith(CIFRA_P03)
    assert respuesta.traza.verificacion.desenlace == "aprobada"
    assert registro.solicitudes == 2
    assert len(respuesta.traza.llamadas) == 2


def test_con_una_copia_de_supuestos_sin_devoluciones_p03_cambia_y_el_prompt_no(base, copia_config, tmp_path):
    # C1.4: la venta neta se define en un solo lugar. Cambiar config/supuestos.yaml cambia la cifra, no el prompt.
    ruta = copia_config / "supuestos.yaml"
    texto = ruta.read_text(encoding="utf-8")
    assert "incluir_devoluciones: true" in texto
    ruta.write_text(texto.replace("incluir_devoluciones: true", "incluir_devoluciones: false"), encoding="utf-8")

    def modelo():
        return modelo_con_guion([("ventas", VENTAS_P03)], lambda m: f"{ultimo_resultado(m)['cifras']['venta']} pesos")[0]

    con_la_original = preguntar(P03, modelo(), base)
    con_la_copia = preguntar(P03, modelo(), tmp_path / "copia.duckdb", dir_config=copia_config)

    assert con_la_original.texto.startswith(CIFRA_P03)
    assert con_la_copia.texto.startswith(CIFRA_P03_SIN_DEVOLUCIONES)
    assert con_la_copia.traza.prompt_sha256 == con_la_original.traza.prompt_sha256


# ------------------------------------------------------------------ topes (K19)


def test_un_modelo_que_pide_seis_rondas_de_herramientas_termina_en_tope_sin_cifra(base):
    modelo, registro = modelo_de_rondas(6)
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "solicitudes"
    # La quinta solicitud no sale: el tope se aplica antes de llamar al modelo, no después.
    assert registro.llamadas == 4
    assert sin_digitos(respuesta.texto)
    assert CIFRA_P03 not in respuesta.texto
    # Lo que corrió antes del corte queda como evidencia, para el panel de traza y el registro.
    assert {e.herramienta for e in respuesta.evidencias} == {"ventas"}


def test_un_modelo_que_pide_herramientas_sin_parar_se_corta_en_el_tope(base):
    # La prueba de K19, tal cual: un bucle de herramientas no termina por sí solo, lo termina el tope.
    modelo, registro = modelo_de_rondas(10**9)
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "solicitudes"
    assert registro.llamadas == 4
    assert sin_digitos(respuesta.texto)


def test_cuatro_solicitudes_alcanzan_si_la_cuarta_redacta(base):
    modelo, registro = modelo_de_rondas(3, final=f"{CIFRA_P03} pesos de junio a agosto")
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "ok"
    assert respuesta.traza.tope is None
    assert registro.llamadas == 4


def test_un_exceso_de_llamadas_a_herramientas_corta_antes_de_ejecutarlas(base):
    def tres_a_la_vez(mensajes, info: AgentInfo):
        return ModelResponse(parts=[ToolCallPart("ventas", VENTAS_P03, tool_call_id=f"p-{i}") for i in range(3)])

    respuesta = preguntar(P03, FunctionModel(tres_a_la_vez), base, topes=agente.Topes(herramientas=2))

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "herramientas"
    # Pydantic AI revisa el tope antes de ejecutar el grupo: ninguna de las tres corrió.
    assert respuesta.evidencias == ()
    assert sin_digitos(respuesta.texto)


def test_un_modelo_lento_se_corta_cuando_se_acaba_el_tiempo(base):
    async def lento(mensajes, info: AgentInfo):
        await asyncio.sleep(30)
        return ModelResponse(parts=[TextPart(f"{CIFRA_P03} pesos, pero tarde")])

    inicio = time.monotonic()
    respuesta = preguntar(P03, FunctionModel(lento), base, topes=agente.Topes(segundos=0.5))
    duracion = time.monotonic() - inicio

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "segundos"
    assert duracion < 20, "el modelo dormía 30 s: el tope no lo esperó"
    assert sin_digitos(respuesta.texto)


def test_una_respuesta_cortada_por_max_tokens_no_se_muestra_como_si_estuviera_completa(base):
    def cortada(mensajes, info: AgentInfo):
        return ModelResponse(parts=[TextPart(f"{CIFRA_P03} pesos de ven")], finish_reason="length")

    respuesta = preguntar(P03, FunctionModel(cortada), base)

    assert respuesta.estado == "tope"
    assert respuesta.traza.tope == "tokens"
    assert sin_digitos(respuesta.texto)


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
    assert respuesta.mensajes == ()  # y no entra al historial del turno siguiente


# ------------------------------------------------------------------ reintentos y fallas del modelo (K20, K21)


def test_un_429_con_retry_after_2_deja_un_reintento_de_2_s_y_la_cifra_correcta(base, esperas):
    modelo, registro = modelo_p03(limite("2"))
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "ok"
    assert respuesta.texto.startswith(CIFRA_P03)
    assert respuesta.traza.reintentos == (Reintento("limite", 2.0, "retry-after", 429),)
    assert esperas.dormidas == [2.0]
    # El reintento no es otra llamada al modelo: la traza trae las dos respuestas que sí llegaron.
    assert registro.llamadas == 3
    assert len(respuesta.traza.llamadas) == 2


def test_sin_retry_after_espera_1_2_y_4_s_y_si_el_429_no_cede_responde_sin_cifra(base, esperas):
    modelo, registro = modelo_p03(*[limite()] * 4)
    respuesta = preguntar(P03, modelo, base)

    assert esperas.dormidas == [1.0, 2.0, 4.0]
    assert registro.llamadas == 4
    assert respuesta.estado == "modelo_no_disponible"
    assert respuesta.traza.falla.motivo == "limite"
    assert respuesta.traza.falla.estado_http == 429
    assert sin_digitos(respuesta.texto)


def test_con_una_llave_invalida_no_reintenta_y_no_repite_lo_que_dijo_el_proveedor(base, esperas):
    modelo, registro = modelo_p03(llave_invalida())
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "modelo_no_disponible"
    assert respuesta.traza.falla.motivo == "llave"
    assert registro.llamadas == 1
    assert esperas.dormidas == []
    assert sin_digitos(respuesta.texto)
    assert "SECRETO" not in respuesta.texto + repr(respuesta.traza)


def test_un_429_a_mitad_del_turno_no_repite_la_herramienta_y_la_cifra_sale_una_vez(base, esperas):
    # La primera solicitud pide ventas, la segunda recibe el 429 y la tercera redacta.
    modelo, registro = modelo_p03(None, limite("1"))
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "ok"
    assert [e.herramienta for e in respuesta.evidencias] == ["ventas"]
    assert respuesta.texto.startswith(CIFRA_P03)
    assert esperas.dormidas == [1.0]


def test_si_el_modelo_falla_despues_de_una_herramienta_la_cifra_no_sale_en_el_texto(base, esperas):
    modelo, registro = modelo_p03(None, llave_invalida())
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "modelo_no_disponible"
    assert CIFRA_P03 not in respuesta.texto
    # Lo que alcanzó a correr queda para el panel de traza y el registro, como en un tope.
    assert respuesta.evidencias[0].resultado.cifras["venta"] == CIFRA_P03


def test_una_falla_del_modelo_no_deja_la_conexion_abierta(base, esperas):
    modelo, _ = modelo_p03(llave_invalida())
    assert preguntar(P03, modelo, base).estado == "modelo_no_disponible"
    with duckdb.connect(str(base)) as escritura:
        assert escritura.execute("SELECT 1").fetchall() == [(1,)]


# ------------------------------------------------------------------ verificador (K16, K17, K18, D16-A)


def test_una_cifra_inventada_se_corrige_con_un_reintento(base):
    modelo, registro = modelo_con_guion(
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
    assert revision.revisiones[0].cifras_sin_evidencia == 1
    assert len(registro.pedidos) == 1 and CIFRA_INVENTADA in registro.pedidos[0]
    # Una sola llamada extra: herramienta, redacción y corrección.
    assert registro.solicitudes == 3


def test_una_cifra_inventada_que_persiste_da_la_respuesta_armada_con_la_evidencia(base):
    modelo, _ = modelo_con_guion([("ventas", VENTAS_P03)], redactar=lambda m: f"{CIFRA_INVENTADA} pesos")
    respuesta = preguntar(P03, modelo, base)

    assert respuesta.estado == "ok"
    assert CIFRA_INVENTADA not in respuesta.texto
    assert CIFRA_P03 in respuesta.texto.splitlines()[0]
    assert respuesta.traza.verificacion.desenlace == "determinista"
    # El historial del turno siguiente lleva la respuesta mostrada, no la fabricada.
    textos = [p.content for m in respuesta.mensajes for p in m.parts if isinstance(p, TextPart)]
    assert textos[-1] == respuesta.texto
    assert not any(CIFRA_INVENTADA in t for t in textos)


def test_una_cifra_copiada_de_una_respuesta_anterior_se_bloquea(base):
    modelo, _ = modelo_con_guion(
        [("ventas", VENTAS_P03)], lambda m: f"{ultimo_resultado(m)['cifras']['venta']} pesos de venta"
    )
    primera = preguntar(P03, modelo, base)
    assert primera.texto.startswith(CIFRA_P03)

    # El segundo turno repite la cifra del texto anterior sin llamar a ninguna herramienta.
    copion, _ = modelo_con_guion([], redactar=lambda m: f"Como te dije, {CIFRA_P03} pesos.")
    segunda = preguntar("y cuanto fue entonces?", copion, base, historial=primera.mensajes)

    assert CIFRA_P03 not in segunda.texto
    assert sin_digitos(segunda.texto)
    assert segunda.evidencias == ()
    assert segunda.traza.verificacion.desenlace == "determinista"
    assert segunda.traza.verificacion.revisiones[0].discrepancias[0].valor == CIFRA_P03


def test_una_cita_a_una_seccion_que_no_existe_se_bloquea(base):
    modelo, _ = modelo_con_guion(
        [], redactar=lambda m: "Se retira dos días antes de caducar (POL-OPS-014 §15.3, p. 12)."
    )
    respuesta = preguntar(PREGUNTA_DE_POLITICA, modelo, base)

    assert respuesta.estado == "ok"
    assert "§15.3" not in respuesta.texto
    revision = respuesta.traza.verificacion
    assert revision.desenlace == "determinista"
    assert revision.revisiones[0].citas_sin_evidencia == 1
    assert respuesta.texto == verificacion.SIN_EVIDENCIA


def test_un_telefono_en_la_respuesta_no_llega_a_la_gerente_ni_se_guarda_en_la_traza(base):
    # Un teléfono inventado, no el de ningún ticket.
    modelo, _ = modelo_con_guion([], redactar=lambda m: "Llámale al 55 0000 0000 y pregunta por el folio.")
    respuesta = preguntar("pasame el cel del ticket 1043", modelo, base)

    assert respuesta.estado == "ok"
    assert "0000" not in respuesta.texto
    revision = respuesta.traza.verificacion
    assert revision.desenlace == "determinista"
    assert revision.revisiones[0].datos_personales == 1
    assert not any("0000" in version for version in revision.versiones)


# El mismo fragmento que devuelve buscar_politicas para P06, armado a mano: estas pruebas no usan el índice.
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


def ventas_p03():
    return Resultado(
        herramienta="ventas",
        resumen="venta, junio a agosto de 2026",
        unidad="pesos (MXN)",
        definicion="venta neta con las devoluciones descontadas",
        filtros={"tiendas": ["T01", "T02", "T03", "T04"], "desde": "2026-06-01", "hasta": "2026-08-31"},
        corte="2026-08-31",
        cifras={"venta": CIFRA_P03, "devoluciones_descontadas": "43,935.50"},
        fuente="ventas.csv",
        consulta="SELECT 999999 AS constante_de_la_consulta",
    )


def revisar(texto, resultados, secciones, pregunta=P03):
    return verificacion.verificar(texto, verificacion.fuentes_de(pregunta, resultados), secciones)


def tipos(revision):
    return [d.tipo for d in revision.discrepancias]


def test_el_verificador_aprueba_la_cifra_de_la_herramienta_y_marca_la_inventada(secciones):
    assert revisar(f"Vendimos {CIFRA_P03} pesos.", [ventas_p03()], secciones).aprobada
    assert tipos(revisar(f"Vendimos {CIFRA_INVENTADA} pesos.", [ventas_p03()], secciones)) == ["cifra"]


def test_el_verificador_compara_el_valor_y_no_el_formato(secciones):
    for escrita in ("17228300.26", "$17,228,300.26", "17,228,300.260", "-17,228,300.26"):
        assert revisar(f"Vendimos {escrita}.", [ventas_p03()], secciones).aprobada, escrita
    assert tipos(revisar("Vendimos 17.2 millones.", [ventas_p03()], secciones)) == ["cifra"]


def test_la_coma_decimal_se_rechaza_con_un_motivo_claro(secciones):
    revision = revisar("El margen fue de 32,5 por ciento.", [ventas_p03()], secciones)
    assert tipos(revision) == ["formato"]
    assert "coma" in revision.discrepancias[0].motivo


def test_una_cita_del_fragmento_recuperado_pasa(secciones):
    revision = revisar("Se retira dos días antes (POL-OPS-014 §5.2, p. 3).", [politicas_p06()], secciones)
    assert revision.aprobada
    assert revision.citas_revisadas == 1


def test_una_cita_a_una_seccion_que_no_existe_se_marca(secciones):
    revision = revisar("Se retira dos días antes (POL-OPS-014 §15.3, p. 3).", [politicas_p06()], secciones)
    assert tipos(revision) == ["cita"]
    assert revision.cifras_sin_evidencia == 0


def test_una_pagina_que_no_es_la_de_la_seccion_se_marca(secciones):
    revision = revisar("Se retira dos días antes (POL-OPS-014 §5.2, p. 9).", [politicas_p06()], secciones)
    assert tipos(revision) == ["pagina"]


def test_una_seccion_real_que_no_se_recupero_se_marca(secciones):
    # PRO-SAC-021 §9 existe, pero en este turno no llegó su fragmento: su texto no se puede comprobar.
    revision = revisar("La autoriza el Supervisor (PRO-SAC-021 §9, p. 6).", [politicas_p06()], secciones)
    assert tipos(revision) == ["cita_no_recuperada"]


def test_atribuir_contenido_a_un_documento_que_no_viene_se_marca(secciones):
    revision = revisar("Según PRO-ECO-009 §4, se devuelve en línea.", [politicas_p06()], secciones)
    assert tipos(revision) == ["documento"]


def test_una_pagina_sin_seccion_vale_si_esta_en_el_documento_y_se_marca_si_no(secciones):
    assert revisar("Ver POL-OPS-014, p. 3.", [politicas_p06()], secciones).aprobada

    revision = revisar("Ver POL-OPS-014, p. 99.", [politicas_p06()], secciones)
    assert tipos(revision) == ["pagina"]
    assert "no está en el documento" in revision.discrepancias[0].motivo


def test_una_seccion_sin_documento_se_marca_salvo_que_un_resultado_la_traiga_igual(secciones):
    sin_documento = revisar("Se aplica §5.2.", [politicas_p06()], secciones)
    assert tipos(sin_documento) == ["cita"]
    assert "no dice de qué documento es" in sin_documento.discrepancias[0].motivo

    # La regla «§5.2» de caducidades llega en las notas de su resultado: ahí la sección sí tiene fuente.
    caducidades = Resultado(herramienta="caducidades", resumen="lotes", notas=["Retiro del anaquel: POL-OPS-014 §5.2, p. 3"])
    assert revisar("Se aplica §5.2.", [caducidades], secciones).aprobada


def test_un_identificador_de_pregunta_de_la_faq_sin_la_faq_no_es_una_cita(secciones):
    revision = revisar("Es la P15 de la lista.", [politicas_p06()], secciones)
    assert tipos(revision) == ["identificador"]
    assert revision.citas_revisadas == 0


def test_la_respuesta_armada_con_la_evidencia_no_repite_un_resultado_igual():
    una_vez = verificacion.respuesta_determinista([ventas_p03()])
    dos_veces = verificacion.respuesta_determinista([ventas_p03(), ventas_p03()])

    assert CIFRA_P03 in una_vez.splitlines()[0]
    assert dos_veces == una_vez


def test_un_telefono_y_un_nombre_en_la_respuesta_se_marcan_sin_copiar_el_valor(secciones):
    telefono = revisar("Llámale al 55 0000 0000.", [], secciones, pregunta="dame el cel")
    personal = next(d for d in telefono.discrepancias if d.tipo == "dato_personal")
    assert "0000" not in personal.valor + personal.motivo

    nombre = revisar("Lo reporta Ernestina Valdepeñas.", [], secciones, pregunta="quien lo reporta")
    assert tipos(nombre) == ["dato_personal"]


# ------------------------------------------------------------------ errores con tipo (T2.2)

CASOS_DE_ERROR = [
    pytest.param({"tiendas": ["narnia"], "periodo": "julio"}, "entidad_inexistente", id="tienda_que_no_existe"),
    pytest.param({"tiendas": ["cumbres"], "periodo": "hoy"}, "fuera_de_periodo", id="periodo_fuera_del_corte"),
    pytest.param({"metrica": "ticket_promedio", "periodo": "julio"}, "metrica_no_calculable", id="ticket_promedio"),
    pytest.param(
        {"tiendas": ["anahuac"], "canal": "Ecommerce", "periodo": "julio"}, "canal_inexistente", id="tienda_sin_el_canal"
    ),
]


@pytest.mark.parametrize("argumentos, estado", CASOS_DE_ERROR)
def test_un_error_con_tipo_vuelve_al_modelo_y_la_respuesta_no_trae_cifra(argumentos, estado, base):
    def pide_y_redacta(mensajes, info: AgentInfo):
        if vueltas_del_turno(mensajes) == 0:
            return ModelResponse(parts=[ToolCallPart("ventas", argumentos, tool_call_id="v0")])
        recibido = ultimo_resultado(mensajes)
        assert recibido["estado"] == estado
        assert "cifras" not in recibido and "filas" not in recibido
        return ModelResponse(parts=[TextPart("No puedo darte esa cifra con los datos que tengo.")])

    respuesta = preguntar("pregunta de prueba", FunctionModel(pide_y_redacta), base)

    assert respuesta.estado == "ok"
    assert [e.resultado.estado for e in respuesta.evidencias] == [estado]
    assert sin_digitos(respuesta.texto)
    assert respuesta.traza.verificacion.desenlace == "aprobada"


def test_copia_b_un_csv_renombrado_responde_no_pude_consultar_sin_llamar_al_modelo(copia_datos, tmp_path):
    (copia_datos / "ventas.csv").rename(copia_datos / "ventas_agosto.csv")

    def no_debe_llamarse(mensajes, info):
        raise AssertionError("sin los datos no hay nada que preguntarle al modelo")

    respuesta = preguntar(
        P03, FunctionModel(no_debe_llamarse), tmp_path / "copia_b.duckdb", dir_datos=copia_datos
    )

    assert respuesta.estado == "fuente_no_disponible"
    assert "ventas.csv" in respuesta.texto
    assert sin_digitos(respuesta.texto)
    assert str(copia_datos) not in respuesta.texto, "la ruta del equipo no es para la gerente"
    assert respuesta.traza.llamadas == ()


# ------------------------------------------------------------------ conversaciones (D10-A, K22)


def modelo_que_anota(texto="Listo.", **herramienta):
    """Anota los mensajes de cada solicitud; si recibe `herramienta`, la pide en la primera vuelta del turno."""
    vistos = []

    def funcion(mensajes, info: AgentInfo):
        vistos.append(list(mensajes))
        if herramienta and vueltas_del_turno(mensajes) == 0:
            nombre, args = next(iter(herramienta.items()))
            return ModelResponse(parts=[ToolCallPart(nombre, args, tool_call_id="c0")])
        if herramienta:
            return ModelResponse(parts=[TextPart(f"{ultimo_resultado(mensajes)['cifras']['venta']} pesos de venta")])
        return ModelResponse(parts=[TextPart(texto)])

    return FunctionModel(funcion, model_name="anota"), vistos


def textos_del_usuario_y_del_modelo(mensajes):
    return [parte.content for m in mensajes for parte in m.parts if isinstance(parte, (UserPromptPart, TextPart))]


def test_un_seguimiento_recibe_la_pregunta_la_respuesta_y_los_parametros_del_turno_anterior(base):
    historial = modulo_historial.Historial()
    primera = preguntar(P03, modelo_que_anota(ventas=VENTAS_P03)[0], base, historial)
    historial.agregar(P03, primera)

    modelo, vistos = modelo_que_anota(ventas={"periodo": "julio"})
    segunda = preguntar("y solo en julio?", modelo, base, historial)

    enviado = textos_del_usuario_y_del_modelo(vistos[0])
    assert P03 in enviado
    assert any(texto.startswith(CIFRA_P03) and "Consulté: ventas" in texto for texto in enviado)
    # La cifra del seguimiento sale de la herramienta de este turno, no del historial.
    assert segunda.estado == "ok"
    assert not segunda.texto.startswith(CIFRA_P03)
    assert segunda.evidencias[0].entrada == {"periodo": "julio"}


def test_el_historial_no_manda_resultados_de_herramientas_solo_texto(base):
    historial = modulo_historial.Historial()
    historial.agregar(P03, preguntar(P03, modelo_que_anota(ventas=VENTAS_P03)[0], base, historial))

    modelo, vistos = modelo_que_anota()
    preguntar("gracias", modelo, base, historial)

    previos = vistos[0][:-1]
    partes = [parte for m in previos for parte in m.parts]
    assert partes and all(isinstance(p, (UserPromptPart, TextPart)) for p in partes)


def test_dos_sesiones_no_comparten_contexto(base):
    una, otra = modulo_historial.Historial(), modulo_historial.Historial()
    pregunta_privada = "cuanto vendio cumbres en julio"
    una.agregar(pregunta_privada, preguntar(pregunta_privada, modelo_que_anota()[0], base, una))

    modelo, vistos = modelo_que_anota()
    preguntar("y en agosto?", modelo, base, otra)

    assert una.id_sesion != otra.id_sesion
    assert pregunta_privada not in textos_del_usuario_y_del_modelo(vistos[0])
    assert len(vistos[0]) == 1, "la sesión nueva solo manda su pregunta"


def test_el_historial_guarda_las_ultimas_seis_vueltas(base):
    historial = modulo_historial.Historial()
    for numero in range(8):
        pregunta = f"pregunta numero {numero}"
        historial.agregar(pregunta, preguntar(pregunta, modelo_que_anota(f"respuesta {numero}")[0], base, historial))

    modelo, vistos = modelo_que_anota()
    preguntar("la que sigue", modelo, base, historial)

    enviado = textos_del_usuario_y_del_modelo(vistos[0])
    assert len(historial) == modulo_historial.VUELTAS == 6
    assert "pregunta numero 1" not in enviado and "pregunta numero 2" in enviado
    assert enviado[-1] == "la que sigue"
    assert len(vistos[0]) == 2 * modulo_historial.VUELTAS + 1


def test_un_turno_cortado_por_un_tope_deja_un_historial_valido_para_el_siguiente(base):
    historial = modulo_historial.Historial()
    cortado = preguntar(P03, modelo_de_rondas(6)[0], base, historial)
    assert cortado.estado == "tope"
    historial.agregar(P03, cortado)

    modelo, vistos = modelo_que_anota("Dime la pregunta por partes.")
    siguiente = preguntar("mejor solo junio", modelo, base, historial)

    assert siguiente.estado == "ok"
    previos = vistos[0][:-1]
    assert isinstance(previos[0], ModelRequest) and isinstance(previos[1], ModelResponse)
    assert sin_digitos(previos[1].parts[0].content.split("Consulté:")[0])
