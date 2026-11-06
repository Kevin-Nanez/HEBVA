"""Registro de turnos en logs/turnos.jsonl (T4.3, K24 a K27, K07, K12, K15, K38, C5.3, C6.1).

Acepta: con el id de un turno se recuperan modelo, hash del prompt, commit y hash de los datos, y la respuesta se
reproduce (pregunta E16); cada línea pasa json.loads; con logs/ de solo lectura la app sigue respondiendo.

Corre sin red ni llaves: el modelo es un FunctionModel de Pydantic AI, la base se construye en un directorio
temporal y el registro se escribe en tmp_path, nunca en logs/. La reproducción con el modelo real queda pendiente
de la llave; aquí se reproduce con el mismo modelo simulado, que es determinista.

El celular y el nombre del ticket 1043 se leen del CSV dentro de la prueba y viajan en DatoPersonal, cuyo repr no
los muestra; ninguna aserción recibe el texto donde se buscan.
"""

import csv
import datetime as dt
import json
import os
import re
import stat
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from pydantic_ai.exceptions import ModelHTTPError, ModelRateLimitError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from agente import agente, construir, manifiesto, privacidad, registro
from agente import historial as modulo_historial
from agente import reintentos as modulo_reintentos
from agente.herramientas.comun import Resultado
from tests.conftest import requiere_git

RAIZ = Path(__file__).resolve().parent.parent
HOY = dt.date(2026, 10, 31)
P03 = "¿Cuánto vendimos en total de junio a agosto?"
VENTAS_P03 = {"periodo": "de junio a agosto"}
UUID_V4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
HORA_CON_DESFASE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


# ------------------------------------------------------------------ datos personales del CSV (como test_privacidad)


class DatoPersonal(str):
    """Un dato personal leído del CSV: se compara como texto, pero su repr no lo muestra."""

    def __repr__(self) -> str:
        return "<dato personal>"


@pytest.fixture(scope="module")
def datos_del_1043():
    with open(RAIZ / "datos" / "tickets_mesa_servicio.csv", encoding="utf-8", newline="") as archivo:
        descripcion = next(fila for fila in csv.DictReader(archivo) if fila["ticket_id"] == "1043")["descripcion"]
    celulares = re.findall(r"\d[\d .\-]{8,}\d", descripcion)
    assert len(celulares) == 1, "el 1043 debe traer exactamente un número largo"
    grupos = re.findall(r"\d+", celulares[0])
    encontrado = re.match(r"Reporta ([^,]+),", descripcion)
    assert encontrado, "la descripción del 1043 cambió de forma; revisa la prueba"
    nombre = encontrado.group(1)
    variantes = [celulares[0], "".join(grupos), "-".join(grupos), " ".join(grupos), nombre, *nombre.split()[1:]]
    return [DatoPersonal(valor) for valor in variantes]


def presentes(valores, texto):
    """Los índices de los valores que aparecen en el texto: la aserción compara índices, no el texto."""
    return [i for i, valor in enumerate(valores) if valor in texto]


# ------------------------------------------------------------------ modelos simulados


def vueltas_del_turno(mensajes):
    ultima = max(i for i, m in enumerate(mensajes) if any(isinstance(p, UserPromptPart) for p in m.parts))
    return sum(isinstance(m, ModelResponse) for m in mensajes[ultima:])


def ultimo_resultado(mensajes):
    return json.loads([p for m in mensajes for p in m.parts if isinstance(p, ToolReturnPart)][-1].content)


def modelo_p03(*fallas):
    """Pide ventas de junio a agosto y redacta la cifra; la solicitud i lanza fallas[i] si no es None."""
    vistas = SimpleNamespace(llamadas=0)

    def funcion(mensajes, info: AgentInfo):
        i = vistas.llamadas
        vistas.llamadas += 1
        if i < len(fallas) and fallas[i] is not None:
            raise fallas[i]
        if vueltas_del_turno(mensajes) == 0:
            return ModelResponse(parts=[ToolCallPart("ventas", VENTAS_P03, tool_call_id="v0")])
        resultado = ultimo_resultado(mensajes)
        return ModelResponse(
            parts=[TextPart(f"{resultado['cifras']['venta']} pesos de venta de junio a agosto (corte: {resultado['corte']})")]
        )

    return FunctionModel(funcion, model_name="p03")


def modelo_del_1043():
    """Pide el detalle del ticket 1043 y contesta con su folio, sin datos personales."""

    def funcion(mensajes, info: AgentInfo):
        if vueltas_del_turno(mensajes) == 0:
            return ModelResponse(
                parts=[ToolCallPart("tickets", {"metrica": "detalle", "ticket_id": 1043}, tool_call_id="t0")]
            )
        return ModelResponse(parts=[TextPart("Revisé el ticket 1043; no te puedo dar datos de contacto.")])

    return FunctionModel(funcion, model_name="tickets")


def llave_invalida():
    return ModelHTTPError.for_category(
        None, status_code=401, model_name="simulado",
        body={"type": "error", "error": {"type": "authentication_error", "message": "SECRETO-DEL-PROVEEDOR"}},
    )


def limite_429(retry_after):
    return ModelHTTPError.for_category(
        ModelRateLimitError, status_code=429, model_name="simulado", body=None, headers={"retry-after": retry_after}
    )


# ------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def base(fts, tmp_path_factory):
    return tmp_path_factory.mktemp("registro") / "tienda.duckdb"


@pytest.fixture
def archivo(tmp_path):
    return tmp_path / "logs" / "turnos.jsonl"


@pytest.fixture
def esperas(monkeypatch):
    anotadas = SimpleNamespace(dormidas=[])

    async def dormir(segundos):
        anotadas.dormidas.append(segundos)

    monkeypatch.setattr(modulo_reintentos, "dormir", dormir)
    monkeypatch.setattr(modulo_reintentos, "pausar", lambda segundos: None)
    return anotadas


def preguntar(pregunta, modelo, base, archivo, historial=(), **opciones):
    return agente.responder(
        pregunta, historial, modelo=modelo, base=base, hoy_real=HOY, archivo_registro=archivo, **opciones
    )


def lineas(archivo):
    texto = archivo.read_bytes().decode("utf-8")
    return [json.loads(linea) for linea in texto.split("\n") if linea]


def commit_actual():
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=RAIZ, capture_output=True, text=True, check=True
    ).stdout.strip()


# ------------------------------------------------------------------ formato de la línea (K25, K12)


def test_la_hora_lleva_desfase_numerico_y_nunca_z():
    menos_seis = dt.timezone(dt.timedelta(hours=-6))
    assert registro.formatear_hora(dt.datetime(2026, 10, 31, 10, 20, 23, 512000, tzinfo=menos_seis)) == (
        "2026-10-31T10:20:23-06:00"
    )
    assert registro.formatear_hora(dt.datetime(2026, 10, 31, 16, 20, 23, tzinfo=dt.UTC)) == "2026-10-31T16:20:23+00:00"
    assert HORA_CON_DESFASE.match(registro.formatear_hora(registro.ahora()))
    with pytest.raises(ValueError):
        registro.formatear_hora(dt.datetime(2026, 10, 31, 10, 20, 23))


def test_un_texto_con_saltos_de_linea_y_comillas_deja_una_sola_linea_valida_sin_bom(archivo):
    descripcion = 'La puerta "no cierra".\nSegunda línea\r\ntercera cuarta quinta\x85sexta\x0bséptima'
    escrita = registro.escribir(archivo, {"tipo": "prueba", "descripcion": descripcion})
    assert escrita
    contenido = archivo.read_bytes()
    assert not contenido.startswith(b"\xef\xbb\xbf")
    assert contenido.endswith(b"\n")
    texto = contenido.decode("utf-8")
    # Ni \n ni ningún separador que str.splitlines() reconozca queda sin escapar dentro de la línea.
    assert len(texto.splitlines()) == 1
    assert json.loads(texto)["descripcion"] == descripcion


def test_dos_escrituras_dejan_dos_lineas_y_cada_una_pasa_json_loads(archivo):
    assert registro.escribir(archivo, {"tipo": "prueba", "n": 1})
    assert registro.escribir(archivo, {"tipo": "prueba", "n": 2})
    assert [linea["n"] for linea in lineas(archivo)] == [1, 2]


def test_un_valor_no_finito_se_escribe_como_null_y_no_como_nan(archivo):
    assert registro.escribir(archivo, {"a": float("nan"), "b": [float("inf")], "c": Decimal("Infinity"), "d": 1.5})
    texto = archivo.read_text(encoding="utf-8")
    assert "NaN" not in texto and "Infinity" not in texto
    assert json.loads(texto) == {"a": None, "b": [None], "c": None, "d": 1.5}


def test_el_registro_enmascara_telefonos_correos_y_llaves_en_cualquier_texto(archivo):
    # Valores ficticios: un celular con formato de México, un correo y el prefijo de una llave de Anthropic y de Google.
    linea = {
        "pregunta": "márcale al 55 1234 5678 o escribe a alguien@ejemplo.com",
        "anidado": [{"texto": "mi llave es sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"}],
        "otra": "AIzaSyA1234567890abcdefghijklmnopqrstuv",  # gitleaks:allow (llave ficticia de la prueba)
    }
    assert registro.escribir(archivo, linea)
    texto = archivo.read_text(encoding="utf-8")
    for dato in ("1234 5678", "alguien@ejemplo.com", "sk-ant-", "AIza"):
        assert dato not in texto, dato
    assert "<TELEFONO_1>" in texto and "<CORREO_1>" in texto


# ------------------------------------------------------------------ tokens y costo por llamada (hallazgo 6, C6.1)


def test_la_entrada_a_precio_completo_es_la_entrada_menos_la_cache():
    llamada = agente.Llamada("claude-haiku-5-5", 4832, 450, 4372, 0, 220)
    assert registro.entrada_no_cacheada(llamada) == 460
    con_escritura = agente.Llamada("claude-haiku-5-5", 5000, 100, 1000, 3000, 0)
    assert registro.entrada_no_cacheada(con_escritura) == 1000


def test_el_costo_de_la_tabla_del_plan_con_haiku_es_0_00031472():
    # "Con caché caliente: 4,372 tokens leídos a $0.01, 460 a $0.10 y 450 de salida a $0.50" (plan, Metas).
    llamada = agente.Llamada("claude-haiku-5-5", 4832, 450, 4372, 0, 220)
    assert registro.costo_usd(llamada) == Decimal("0.00031472")


@pytest.mark.parametrize(
    ("modelo", "proveedor", "uso"),
    [
        ("claude-haiku-5-5", "anthropic", RequestUsage(input_tokens=4832, output_tokens=450, cache_read_tokens=4372)),
        ("claude-haiku-5-5", "anthropic", RequestUsage(input_tokens=6000, output_tokens=900, cache_write_tokens=4372)),
        ("claude-haiku-5-5", "anthropic", RequestUsage(input_tokens=3000, output_tokens=1200)),
        ("gemini-3.5-flash-lite", "google-gla", RequestUsage(input_tokens=7732, output_tokens=1000)),
        ("gemini-3.5-flash-lite", "google-gla", RequestUsage(input_tokens=7732, output_tokens=1000, cache_read_tokens=4000)),
    ],
)
def test_el_costo_con_config_precios_coincide_con_el_de_pydantic_ai(modelo, proveedor, uso):
    # El plan pide calcularlo con config/precios.yaml (precio, fecha y URL) y cotejarlo con ModelResponse.cost().
    respuesta = ModelResponse(parts=[TextPart("x")], usage=uso, model_name=modelo, provider_name=proveedor)
    llamada = agente.Llamada(modelo, uso.input_tokens, uso.output_tokens, uso.cache_read_tokens, uso.cache_write_tokens)
    assert registro.costo_usd(llamada) == respuesta.cost().total_price


def test_un_modelo_sin_precio_deja_el_costo_en_null_y_no_en_cero():
    assert registro.costo_usd(agente.Llamada("p03", 100, 10, 0, 0)) is None


def test_sin_config_precios_la_linea_sale_igual_sin_costo(base, archivo, tmp_path, monkeypatch):
    monkeypatch.setattr(registro, "ARCHIVO_PRECIOS", tmp_path / "no_existe.yaml")
    respuesta = preguntar(P03, modelo_p03(), base, archivo)
    assert respuesta.estado == "ok"
    (linea,) = lineas(archivo)
    assert linea["costo_usd"] is None and linea["precios"] is None
    assert all(llamada["costo_usd"] is None for llamada in linea["llamadas"])
    assert registro.costo_usd(agente.Llamada("claude-haiku-5-5", 4832, 450, 4372, 0)) is None


def test_los_precios_traen_fecha_y_fuente():
    precios = registro.precios()
    assert precios.fecha_consulta == dt.date(2026, 10, 31)
    for nombre in ("claude-haiku-5-5", "gemini-3.5-flash-lite"):
        assert precios.modelos[nombre].fuente.startswith("https://")


# ------------------------------------------------------------------ un turno completo (K24, K38, línea base §8)


@requiere_git
def test_un_turno_deja_una_linea_con_lo_que_pide_la_linea_base(base, archivo):
    historial = modulo_historial.Historial()
    respuesta = preguntar(P03, modelo_p03(), base, archivo, historial, origen="evaluacion", categoria="cifra simple")
    assert respuesta.estado == "ok"
    (linea,) = lineas(archivo)

    assert linea["tipo"] == "turno"
    assert linea["origen"] == "evaluacion" and linea["categoria"] == "cifra simple"
    assert UUID_V4.match(linea["turno_id"]) and linea["turno_id"] == respuesta.turno_id
    assert UUID_V4.match(linea["gen_ai.conversation.id"]) and linea["gen_ai.conversation.id"] == historial.id_sesion
    assert HORA_CON_DESFASE.match(linea["hora"])
    assert linea["hoy_real"] == "2026-10-31"
    assert linea["pregunta"] == P03

    assert linea["gen_ai.request.model"] == "function:p03"
    assert linea["prompt_sha256"] == agente.PROMPT_SHA256
    assert linea["herramientas_sha256"] == agente.HERRAMIENTAS_SHA256
    assert linea["commit"]["sha"] == commit_actual()
    with duckdb.connect(str(base), read_only=True) as con:
        assert linea["hash_fuentes"] == construir.leer_hash_fuentes(con).sha256

    (herramienta,) = linea["herramientas"]
    evidencia = respuesta.evidencias[0]
    assert herramienta["gen_ai.tool.name"] == "ventas"
    assert herramienta["gen_ai.tool.call.arguments"] == evidencia.entrada
    assert herramienta["gen_ai.tool.call.result"] == json.loads(evidencia.resultado.texto_registro())
    # La consulta es idéntica, carácter por carácter, a la de la evidencia que ve el panel (T4.1).
    assert herramienta["gen_ai.tool.call.result"]["consulta"] == evidencia.resultado.consulta
    assert linea["respuesta"] == respuesta.texto
    assert linea["estado"] == "ok" and linea["tope"] is None and linea["falla"] is None
    assert linea["reintentos"] == [] and linea["espera_reintentos_segundos"] == 0
    assert linea["verificacion"]["desenlace"] == "aprobada"

    assert linea["n_llamadas"] == 2 == len(linea["llamadas"])
    for llamada, de_traza in zip(linea["llamadas"], respuesta.traza.llamadas, strict=True):
        assert llamada["gen_ai.usage.input_tokens"] == de_traza.entrada_tokens
        assert llamada["gen_ai.usage.output_tokens"] == de_traza.salida_tokens
        assert llamada["gen_ai.usage.cache_read.input_tokens"] == de_traza.cache_lectura_tokens
        assert llamada["gen_ai.usage.cache_creation.input_tokens"] == de_traza.cache_escritura_tokens
        assert llamada["entrada_no_cacheada"] == de_traza.entrada_tokens
        assert llamada["razonamiento"] == 0
        assert isinstance(llamada["ms"], int) and llamada["ms"] >= 0
        assert llamada["costo_usd"] is None
    assert linea["gen_ai.usage.input_tokens"] == respuesta.traza.entrada_tokens
    assert linea["gen_ai.usage.output_tokens"] == respuesta.traza.salida_tokens
    assert linea["costo_usd"] is None
    assert isinstance(linea["latencia_ms"], int) and linea["latencia_ms"] >= 0


def test_la_linea_guarda_el_resultado_de_la_herramienta_antes_que_el_texto_final(base, archivo):
    preguntar(P03, modelo_p03(), base, archivo)
    texto = archivo.read_text(encoding="utf-8")
    assert texto.index('"herramientas"') < texto.index('"respuesta"')
    assert list(json.loads(texto)).index("herramientas") < list(json.loads(texto)).index("respuesta")


def test_sin_archivo_de_registro_responder_no_escribe_nada(base, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    respuesta = agente.responder(P03, modelo=modelo_p03(), base=base, hoy_real=HOY)
    assert respuesta.estado == "ok"
    assert UUID_V4.match(respuesta.turno_id)
    assert list(tmp_path.iterdir()) == []


def test_un_tope_por_largo_deja_su_linea_con_cero_llamadas(base, archivo):
    largo = agente.TOPES.caracteres + 1
    respuesta = preguntar("a" * largo, modelo_p03(), base, archivo)
    assert respuesta.estado == "tope"
    (linea,) = lineas(archivo)
    assert linea["estado"] == "tope" and linea["tope"] == "caracteres"
    assert linea["n_llamadas"] == 0 and linea["llamadas"] == []
    assert linea["gen_ai.usage.input_tokens"] == 0 and linea["gen_ai.usage.output_tokens"] == 0
    assert linea["pregunta_caracteres"] == largo


def test_un_429_queda_en_la_linea_con_su_espera(base, archivo, esperas):
    respuesta = preguntar(P03, modelo_p03(limite_429("2")), base, archivo)
    assert respuesta.estado == "ok"
    (linea,) = lineas(archivo)
    assert [(r["motivo"], r["espera_segundos"], r["estado_http"]) for r in linea["reintentos"]] == [
        (respuesta.traza.reintentos[0].motivo, 2.0, 429)
    ]
    assert linea["espera_reintentos_segundos"] == 2.0


def test_una_falla_del_modelo_queda_con_su_tipo_y_sin_el_texto_del_proveedor(base, archivo, esperas):
    respuesta = preguntar(P03, modelo_p03(llave_invalida()), base, archivo)
    assert respuesta.estado == "modelo_no_disponible"
    texto = archivo.read_text(encoding="utf-8")
    assert "SECRETO-DEL-PROVEEDOR" not in texto
    (linea,) = lineas(archivo)
    assert linea["estado"] == "modelo_no_disponible"
    assert linea["falla"]["motivo"] == respuesta.traza.falla.motivo
    assert linea["falla"]["tipo"] == "ModelHTTPError" and linea["falla"]["estado_http"] == 401


def test_el_registro_no_trae_el_celular_ni_el_nombre_del_1043(base, archivo, datos_del_1043):
    # P43: la pregunta trae el celular y la herramienta devuelve el detalle del 1043.
    historial = modulo_historial.Historial()
    pregunta = f"de quién es el celular {datos_del_1043[0]} del ticket 1043?"
    respuesta = preguntar(pregunta, modelo_del_1043(), base, archivo, historial)
    historial.agregar(pregunta, respuesta)
    preguntar("y qué más dice el ticket 1043?", modelo_del_1043(), base, archivo, historial)
    texto = archivo.read_text(encoding="utf-8")
    assert len(lineas(archivo)) == 2
    assert presentes(datos_del_1043, texto) == []


# ------------------------------------------------------------------ K27: si no puede escribir, la app sigue


def test_con_el_registro_de_solo_lectura_la_app_sigue_respondiendo(base, archivo):
    archivo.parent.mkdir(parents=True)
    archivo.write_text("", encoding="utf-8")
    os.chmod(archivo, stat.S_IREAD)
    try:
        respuesta = preguntar(P03, modelo_p03(), base, archivo)
    finally:
        os.chmod(archivo, stat.S_IREAD | stat.S_IWRITE)
    assert respuesta.estado == "ok"
    assert respuesta.texto.splitlines()[0].startswith(respuesta.evidencias[0].resultado.cifras["venta"])
    assert archivo.read_text(encoding="utf-8") == ""


def test_si_logs_no_se_puede_crear_la_app_sigue_respondiendo(base, tmp_path):
    ocupado = tmp_path / "logs"
    ocupado.write_text("no soy una carpeta", encoding="utf-8")
    respuesta = preguntar(P03, modelo_p03(), base, ocupado / "turnos.jsonl")
    assert respuesta.estado == "ok"
    assert ocupado.read_text(encoding="utf-8") == "no soy una carpeta"


def test_si_armar_la_linea_falla_la_app_sigue_respondiendo_y_no_muestra_el_error(base, archivo, monkeypatch):
    def romper(*args, **kwargs):
        raise RuntimeError("detalle interno")

    monkeypatch.setattr(registro, "linea_de_turno", romper)
    respuesta = preguntar(P03, modelo_p03(), base, archivo)
    assert respuesta.estado == "ok"
    assert "detalle interno" not in respuesta.texto
    assert not archivo.exists()


def test_escribir_no_lanza_y_dice_si_escribio(tmp_path):
    ocupado = tmp_path / "logs"
    ocupado.write_text("", encoding="utf-8")
    assert registro.escribir(ocupado / "turnos.jsonl", {"tipo": "prueba"}) is False


# ------------------------------------------------------------------ E16: reproducir un turno desde su línea


@requiere_git
def test_con_el_id_de_un_turno_se_recuperan_modelo_prompt_commit_y_datos_y_se_reproduce(base, archivo, tmp_path):
    historial = modulo_historial.Historial()
    primera = preguntar(P03, modelo_p03(), base, archivo, historial)
    historial.agregar(P03, primera)
    enviado = historial.mensajes()
    segunda = preguntar("y cuánto fue eso?", modelo_p03(), base, archivo, historial)
    historial.agregar("y cuánto fue eso?", segunda)

    linea = registro.buscar_turno(archivo, segunda.turno_id)
    assert linea is not None and linea["turno_id"] == segunda.turno_id
    assert registro.buscar_turno(archivo, str(uuid.uuid4())) is None

    datos = registro.datos_de_reproduccion(linea)
    with duckdb.connect(str(base), read_only=True) as con:
        hash_base = construir.leer_hash_fuentes(con).sha256
    assert datos["modelo"] == "function:p03"
    assert datos["prompt_sha256"] == agente.PROMPT_SHA256
    assert datos["commit"]["sha"] == commit_actual()
    assert datos["hash_fuentes"] == hash_base
    assert registro.diferencias(linea, base=base) == {}

    # El historial que se mandó en el segundo turno queda en la línea, con su hash.
    assert linea["historial"]["n_mensajes"] == len(enviado) == 2
    assert linea["historial"]["sha256"] == registro.hash_historial(enviado)
    assert [type(m) for m in registro.historial_de(linea)] == [type(m) for m in enviado]

    otro = tmp_path / "reproduccion.jsonl"
    reproducida = registro.reproducir(linea, modelo=modelo_p03(), base=base, archivo_registro=otro)
    assert reproducida.texto == segunda.texto
    assert [e.resultado.consulta for e in reproducida.evidencias] == [e.resultado.consulta for e in segunda.evidencias]
    (de_la_reproduccion,) = lineas(otro)
    assert de_la_reproduccion["historial"]["sha256"] == linea["historial"]["sha256"]
    assert de_la_reproduccion["hash_fuentes"] == linea["hash_fuentes"]
    assert de_la_reproduccion["ajustes"] == linea["ajustes"] and de_la_reproduccion["topes"] == linea["topes"]


def test_diferencias_dice_que_cambio_desde_el_turno(base, archivo):
    preguntar(P03, modelo_p03(), base, archivo)
    (linea,) = lineas(archivo)
    alterada = {**linea, "prompt_sha256": "0" * 64, "hash_fuentes": "1" * 64}
    cambios = registro.diferencias(alterada, base=base)
    assert set(cambios) == {"prompt_sha256", "hash_fuentes"}
    assert cambios["prompt_sha256"] == ("0" * 64, agente.PROMPT_SHA256)


def test_el_comando_muestra_lo_necesario_para_reproducir_un_turno(base, archivo, capsys):
    respuesta = preguntar(P03, modelo_p03(), base, archivo)
    assert registro.main([respuesta.turno_id, "--archivo", str(archivo)]) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida["turno_id"] == respuesta.turno_id
    assert salida["prompt_sha256"] == agente.PROMPT_SHA256
    assert salida["pregunta"] == P03
    assert registro.main([str(uuid.uuid4()), "--archivo", str(archivo)]) == 1


def test_sin_modelo_reproducir_lee_env_antes_de_usar_el_modelo_de_la_linea(base, archivo, monkeypatch):
    # Con el id del modelo como texto, responder() no lee .env y la llave no llegaba: en la pasada con llave de F4 la
    # reproducción salió "modelo_no_disponible" sin llamar a la API. La prueba no lee el .env real.
    preguntar(P03, modelo_p03(), base, archivo)
    (linea,) = lineas(archivo)
    pasos = []
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: pasos.append("lee .env"))
    monkeypatch.setattr(agente, "responder", lambda pregunta, historial, **opciones: pasos.append(opciones["modelo"]))

    registro.reproducir(linea, base=base)
    assert pasos == ["lee .env", "function:p03"]

    # Con un modelo dado no hace falta la llave del entorno, y .env no se lee.
    pasos.clear()
    registro.reproducir(linea, modelo="test", base=base)
    assert pasos == ["test"]


def test_un_turno_de_que_sabes_se_reproduce_con_el_manifiesto_sin_modelo(base, archivo, monkeypatch):
    # El turno de la cobertura (T4.2) queda con el modelo "sin_modelo", que responder() no sabe usar.
    original = manifiesto.responder("qué sabes", base=base, hoy_real=HOY, archivo_registro=archivo)
    (linea,) = lineas(archivo)
    assert linea["gen_ai.request.model"] == manifiesto.MODELO

    def no_se_llama(*args, **kwargs):
        raise AssertionError("un turno sin modelo no lee .env ni llama a responder()")

    monkeypatch.setattr("dotenv.load_dotenv", no_se_llama)
    monkeypatch.setattr(agente, "responder", no_se_llama)
    reproducida = registro.reproducir(linea, base=base)
    assert manifiesto.es_del_manifiesto(reproducida)
    assert reproducida.estado == "ok"
    assert reproducida.texto == original.texto
    assert reproducida.traza.llamadas == ()


# ------------------------------------------------------------------ ids y hashes con 10 dígitos seguidos (K24)
#
# Un UUID o un sha puede traer 10 dígitos seguidos entre letras hex, que el detector de teléfonos toma por un celular
# (2.6% de los UUID v4 y 7% de los sha256). Esos campos se guardan tal cual si tienen su formato exacto.

TURNO_CON_DIGITOS = "a1b2c3d4-e5f6-4a7b-8c9d-ab1234567890"
SESION_CON_DIGITOS = "0f1e2d3c-4b5a-4968-a7b6-cd0987654321"
SHA256_CON_DIGITOS = "ab1234567890" + "c" * 52
COMMIT_CON_DIGITOS = "ab5512345678" + "e" * 28
LLAMADA_CON_DIGITOS = "pyd_ai_ab1234567890" + "f" * 20


def test_los_valores_de_prueba_parecen_telefonos_al_detector():
    # Sin esto, las pruebas de abajo pasarían aunque el registro enmascarara los ids.
    for valor in (TURNO_CON_DIGITOS, SESION_CON_DIGITOS, SHA256_CON_DIGITOS, COMMIT_CON_DIGITOS, LLAMADA_CON_DIGITOS):
        assert "TELEFONO" in privacidad.buscar_datos_personales(valor), valor
    assert UUID_V4.match(TURNO_CON_DIGITOS) and UUID_V4.match(SESION_CON_DIGITOS)


def test_un_turno_con_diez_digitos_seguidos_en_sus_ids_se_recupera_y_se_reproduce(base, archivo, monkeypatch, capsys):
    monkeypatch.setattr(agente, "uuid", SimpleNamespace(uuid4=lambda: uuid.UUID(TURNO_CON_DIGITOS)))
    monkeypatch.setattr(registro, "commit", lambda: {"sha": COMMIT_CON_DIGITOS, "sucio": False})
    historial = modulo_historial.Historial()
    historial.id_sesion = SESION_CON_DIGITOS
    respuesta = preguntar(P03, modelo_p03(), base, archivo, historial)
    assert respuesta.turno_id == TURNO_CON_DIGITOS

    linea = registro.buscar_turno(archivo, TURNO_CON_DIGITOS)
    assert linea is not None
    assert linea["gen_ai.conversation.id"] == SESION_CON_DIGITOS
    assert linea["commit"]["sha"] == COMMIT_CON_DIGITOS
    assert registro.diferencias(linea, base=base) == {}
    assert registro.main([TURNO_CON_DIGITOS, "--archivo", str(archivo)]) == 0
    assert json.loads(capsys.readouterr().out)["turno_id"] == TURNO_CON_DIGITOS


@requiere_git
def test_los_hashes_y_los_ids_con_diez_digitos_seguidos_se_guardan_tal_cual(archivo, base, monkeypatch):
    monkeypatch.setattr(registro, "commit", lambda: {"sha": COMMIT_CON_DIGITOS, "sucio": False})
    agente.responder("x", modelo=modelo_p03(), base=base, hoy_real=HOY)  # deja la base construida
    with duckdb.connect(str(base), read_only=True) as con:
        hash_base = construir.leer_hash_fuentes(con).sha256
    # Los valores reales tienen el formato que el registro deja sin enmascarar: sha256 y commit en hex minúscula.
    for valor in (hash_base, agente.PROMPT_SHA256, agente.HERRAMIENTAS_SHA256):
        assert re.fullmatch(r"[0-9a-f]{64}", valor), valor
    assert re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit_actual())
    linea = {
        "tipo": "turno",
        "turno_id": TURNO_CON_DIGITOS,
        "gen_ai.conversation.id": SESION_CON_DIGITOS,
        "pregunta_sha256": SHA256_CON_DIGITOS,
        "historial": {"n_mensajes": 0, "sha256": SHA256_CON_DIGITOS, "mensajes": []},
        "prompt_sha256": SHA256_CON_DIGITOS,
        "herramientas_sha256": SHA256_CON_DIGITOS,
        "commit": {"sha": COMMIT_CON_DIGITOS, "sucio": True},
        "hash_fuentes": SHA256_CON_DIGITOS,
        "herramientas": [{"gen_ai.tool.call.id": LLAMADA_CON_DIGITOS, "gen_ai.tool.name": "ventas"}],
    }
    assert registro.escribir(archivo, linea)
    (escrita,) = lineas(archivo)
    assert escrita == linea
    # Leída del registro, la línea solo difiere de lo de ahora en lo que de verdad cambió: el hash de los datos.
    cambios = registro.diferencias(
        {**escrita, "prompt_sha256": agente.PROMPT_SHA256, "herramientas_sha256": agente.HERRAMIENTAS_SHA256},
        base=base,
    )
    assert cambios == {"hash_fuentes": (SHA256_CON_DIGITOS, hash_base)}


def test_un_id_o_un_hash_sin_su_formato_exacto_se_enmascara(archivo):
    # Valores ficticios. Un campo de id que no es un UUID v4 o un sha en hex pasa por el enmascarado como cualquier texto.
    linea = {
        "turno_id": "55 1234 5678",
        "gen_ai.conversation.id": f"{TURNO_CON_DIGITOS} 55 1234 5678",
        "prompt_sha256": SHA256_CON_DIGITOS.upper(),
        "commit": {"sha": f"{COMMIT_CON_DIGITOS}0"},
        "hash_fuentes": SHA256_CON_DIGITOS[:-1],
        "herramientas": [{"gen_ai.tool.call.id": "5512345678"}],
        # Un campo que no es de id no se libra por traer un valor con formato de sha.
        "pregunta": SHA256_CON_DIGITOS,
    }
    assert registro.escribir(archivo, linea)
    (escrita,) = lineas(archivo)
    texto = archivo.read_text(encoding="utf-8")
    assert "1234 5678" not in texto and "5512345678" not in texto and "1234567890" not in texto
    assert escrita["turno_id"] == "<TELEFONO_1>"
    assert escrita["herramientas"][0]["gen_ai.tool.call.id"] == "<TELEFONO_1>"


# ------------------------------------------------------------------ la línea se arma solo con la Respuesta


def test_una_respuesta_sin_base_deja_su_linea_sin_hash_de_datos(archivo, tmp_path, copia_datos):
    (copia_datos / "ventas.csv").unlink()
    respuesta = agente.responder(
        P03, modelo=modelo_p03(), base=tmp_path / "tienda.duckdb", dir_datos=copia_datos, hoy_real=HOY,
        archivo_registro=archivo,
    )
    assert respuesta.estado == "fuente_no_disponible"
    (linea,) = lineas(archivo)
    assert linea["estado"] == "fuente_no_disponible"
    assert linea["hash_fuentes"] is None
    assert linea["respuesta"] == respuesta.texto
    assert str(tmp_path) not in archivo.read_text(encoding="utf-8")


def test_el_resultado_de_una_herramienta_se_guarda_completo_con_la_consulta():
    resultado = Resultado(herramienta="ventas", resumen="r", consulta="SELECT 1", cifras={"venta": "1.00"})
    evidencia = agente.Evidencia("id-1", "ventas", {"periodo": "julio"}, resultado)
    (herramienta,) = registro.herramientas_de((evidencia,))
    assert herramienta == {
        "gen_ai.tool.call.id": "id-1",
        "gen_ai.tool.name": "ventas",
        "gen_ai.tool.call.arguments": {"periodo": "julio"},
        "gen_ai.tool.call.result": json.loads(resultado.texto_registro()),
    }
