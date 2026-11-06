"""Registro de turnos: una línea por turno en logs/turnos.jsonl (T4.3, línea base §8, K24 a K27, C5.3, C6.1).

Cada turno que pasa por responder() con archivo_registro deja una línea JSON con lo que hace falta para medirlo y
para reproducirlo (pregunta E16 de la rúbrica):

- quién y cuándo: turno_id y gen_ai.conversation.id (UUID v4 del turno y de la sesión, el id_sesion del Historial),
  origen ("interfaz" o "evaluacion", para separar a las personas del set, C5.1), categoria (la del set; null en la
  interfaz), hora local con desfase numérico (K25) y hoy_real, la fecha que usó el aviso de fechas relativas;
- qué se preguntó: la pregunta, su largo y el historial que se mandó al modelo, con su sha256, para repetir un
  turno que no fue el primero de su sesión;
- con qué se respondió: gen_ai.request.model, el sha256 del prompt (PROMPT_SHA256, el del archivo con LF, K15) y
  el de las definiciones de herramientas, el commit, el hash de las fuentes que guardó la base que respondió (K07),
  los ajustes del turno (AjustesDelModelo y Topes) y lo que de ellos viajó al proveedor;
- qué pasó: cada herramienta con sus argumentos y su Resultado completo, consulta SQL incluida
  (Resultado.texto_registro()), antes del texto final; el verificador con sus versiones y su desenlace (D16-A); los
  tickets sospechosos (K08); la respuesta, el estado, el tope (K19), la falla y los reintentos con su espera (K20,
  K21);
- cuánto costó: cada llamada al modelo con sus tokens separados (hallazgo 6: Pydantic AI da la entrada con la caché
  y la salida con el razonamiento), su latencia aproximada y su costo; los totales del turno, n_llamadas y
  latencia_ms, medida en responder().

Los nombres de los campos de uso y de herramientas son los de las convenciones GenAI de OpenTelemetry v1.41.1
(gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, gen_ai.usage.cache_read.input_tokens,
gen_ai.usage.cache_creation.input_tokens, gen_ai.conversation.id, gen_ai.request.model, gen_ai.response.model,
gen_ai.provider.name, gen_ai.response.finish_reasons, gen_ai.tool.name, gen_ai.tool.call.id,
gen_ai.tool.call.arguments y gen_ai.tool.call.result), sin instalar OpenTelemetry (línea base §9). Como en la
convención, input_tokens incluye la caché y output_tokens el razonamiento. Lo que la convención no nombra lleva
nombre propio: entrada_no_cacheada (la entrada menos lectura y escritura de caché, la que se cobra a precio
completo), razonamiento, ms, costo_usd, latencia_ms y n_llamadas.

Marca de "respuesta incorrecta" (T4.4, K28): el botón de la interfaz agrega al mismo archivo una línea con tipo "marca",
el turno_id del turno marcado y la hora, y nada más: ni la pregunta, ni la respuesta, ni un texto libre. Quien lee el
archivo para sumar campos de turno filtra por tipo == "turno" (buscar_turno() lo hace) o ignora lo que no trae el
campo; `python -m eval.casos_desde_marcas` convierte cada marca en un caso pendiente.

Formato (K12, K25): JSON en UTF-8 sin BOM, una línea por turno, escrito con comun.dumps(), que rechaza NaN e
Infinity. Un valor no finito (un Retry-After absurdo) se escribe como null para no perder la línea. Los separadores
de línea que JSON deja pasar sin escapar (U+0085, U+2028, U+2029) se escapan, para que ningún lector parta la línea.

Privacidad (K26): ningún campo trae la llave ni el texto de un error del proveedor (la falla es su tipo y su
estado HTTP). Antes de escribir, cada texto de la línea pasa por el mismo enmascarado que el historial: un teléfono
o un correo se cambia por <TELEFONO_n> o <CORREO_n> (y, en ese texto, también un nombre), y lo que parezca una llave
de Anthropic o de Google, por <LLAVE>. Los ids y los hashes (turno_id, gen_ai.conversation.id, los sha256, el
commit y el id de cada llamada a herramienta) se guardan tal cual cuando tienen su formato exacto, porque 10 dígitos
seguidos dentro de un UUID o un sha parecen un celular (_FORMATOS_DE_ID). El texto de los tickets ya llega enmascarado de la base (D15-A) y las
evidencias de la Respuesta ya vienen sin datos de contacto (K18); esto cubre la pregunta y lo que se haya escapado.

Si el registro no se puede escribir (logs/ de solo lectura, el disco lleno, una línea que no se puede armar), la
app sigue (K27): registrar_turno() y escribir() no lanzan, devuelven False y dejan un aviso en el logging de Python
con el tipo del error, sin su texto ni datos del turno.

Reproducir un turno (E16): `python -m agente.registro <turno_id>` muestra el modelo, los hashes, el commit y la
pregunta de ese turno; diferencias() dice qué cambió desde entonces (hacer checkout del commit y restaurar los datos
cuyo hash no coincide), y reproducir() vuelve a correr la pregunta con el mismo historial, fecha, ajustes y topes.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import functools
import hashlib
import io
import json
import logging
import math
import re
import subprocess
import sys
import threading
from collections.abc import Iterator, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from agente import privacidad
from agente.herramientas.comun import dumps

if TYPE_CHECKING:
    from agente.agente import Evidencia, Llamada, Respuesta, Topes
    from agente.ajustes_modelo import AjustesDelModelo

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO = RAIZ / "logs" / "turnos.jsonl"
ARCHIVO_PRECIOS = RAIZ / "config" / "precios.yaml"

# Sube si cambia el significado de un campo, para que eval.reporte (T5) no mezcle líneas de dos formatos.
VERSION = 1

Origen = Literal["interfaz", "evaluacion"]

# El tipo de la línea que deja el botón "Respuesta incorrecta" (T4.4). Las de un turno llevan "turno".
TIPO_MARCA = "marca"
# El tope del comentario de una marca: la caja de la interfaz lo aplica y linea_de_marca lo vuelve a aplicar.
MAXIMO_CARACTERES_COMENTARIO = 500

# Lo que se guarda de la pregunta. Las que pasan el tope de K19 (1,500 caracteres) no llaman al modelo; de una más
# larga basta su principio, su largo y su sha256.
MAXIMO_CARACTERES_PREGUNTA = 2000

_aviso = logging.getLogger(__name__)
_cerrojo = threading.Lock()

_DATOS_DE_CONTACTO = frozenset({"TELEFONO", "CORREO"})
# Prefijos de las llaves de Anthropic (sk-ant-...) y de Google (AIza...). No se comparan con la llave del entorno:
# el registro no la lee.
_LLAVE = re.compile(r"sk-ant-[A-Za-z0-9_\-]+|AIza[0-9A-Za-z_\-]{20,}")
MARCA_LLAVE = "<LLAVE>"
# Los ids y hashes de la línea se guardan sin pasar por el enmascarado si tienen su formato exacto: un UUID o un sha
# puede traer 10 dígitos seguidos entre letras hex (2.6% de los UUID v4, 7% de los sha256), que el detector de
# teléfonos toma por un celular, y enmascarados ya no se encuentra el turno ni se sabe qué commit o qué datos usó. Un
# valor que no tenga ese formato pasa por el enmascarado como cualquier texto. La ruta es la de las claves desde la
# raíz de la línea; "*" es cualquier elemento de una lista.
_UUID_V4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# git usa sha-1 (40) y, en un repositorio con objetos sha-256, 64.
_SHA_DE_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# Los ids que genera Pydantic AI (pyd_ai_ y un uuid4 en hex, los de Gemini) y los de Anthropic (toolu_...).
_ID_DE_LLAMADA = re.compile(r"pyd_ai_[0-9a-f]{32}|toolu_[A-Za-z0-9]{16,64}")
_FORMATOS_DE_ID: Mapping[tuple[str, ...], re.Pattern[str]] = {
    ("turno_id",): _UUID_V4,
    ("gen_ai.conversation.id",): _UUID_V4,
    ("pregunta_sha256",): _SHA256,
    ("historial", "sha256"): _SHA256,
    ("prompt_sha256",): _SHA256,
    ("herramientas_sha256",): _SHA256,
    ("commit", "sha"): _SHA_DE_COMMIT,
    ("hash_fuentes",): _SHA256,
    ("herramientas", "*", "gen_ai.tool.call.id"): _ID_DE_LLAMADA,
}
# Caracteres que json.dumps deja sin escapar y que str.splitlines() toma como fin de línea.
_SEPARADORES = {"\x85": "\\u0085", " ": "\\u2028", " ": "\\u2029"}


# ------------------------------------------------------------------ hora (K25)


def ahora() -> dt.datetime:
    """La hora local con su zona, para la línea del turno."""
    return dt.datetime.now().astimezone()


def formatear_hora(momento: dt.datetime) -> str:
    """La hora con desfase numérico y al segundo: 2026-10-31T10:20:23-06:00, nunca Z (RFC 3339 §5.6, K25)."""
    if momento.tzinfo is None or momento.utcoffset() is None:
        raise ValueError("la hora del registro necesita su zona: una hora sin desfase no se puede comparar")
    return momento.isoformat(timespec="seconds")


# ------------------------------------------------------------------ precios y costo (C6.1)


@dataclasses.dataclass(frozen=True)
class Precio:
    """Dólares por millón de tokens de un modelo, con la página de donde salieron."""

    fuente: str
    entrada: Decimal
    salida: Decimal
    cache_lectura: Decimal
    cache_escritura: Decimal | None = None
    hasta_tokens_de_prompt: int | None = None


@dataclasses.dataclass(frozen=True)
class Precios:
    fecha_consulta: dt.date
    modelos: Mapping[str, Precio]

    def de(self, modelo: str) -> Precio | None:
        """El precio de un modelo por su nombre, también con sufijo de versión (claude-haiku-5-5-20261015)."""
        for nombre, precio in self.modelos.items():
            if modelo == nombre or modelo.startswith(f"{nombre}-"):
                return precio
        return None


def _decimal(valor: Any) -> Decimal | None:
    return None if valor is None else Decimal(str(valor))


def precios(archivo: Path | None = None) -> Precios:
    """config/precios.yaml (o el archivo que se dé), leído una vez por proceso."""
    return _leer_precios(Path(archivo or ARCHIVO_PRECIOS))


@functools.cache
def _leer_precios(archivo: Path) -> Precios:
    with open(archivo, encoding="utf-8") as entrada:
        datos = yaml.safe_load(entrada)
    modelos = {
        nombre: Precio(
            fuente=campos["fuente"],
            entrada=_decimal(campos["entrada"]),
            salida=_decimal(campos["salida"]),
            cache_lectura=_decimal(campos["cache_lectura"]),
            cache_escritura=_decimal(campos.get("cache_escritura")),
            hasta_tokens_de_prompt=campos.get("hasta_tokens_de_prompt"),
        )
        for nombre, campos in datos["modelos"].items()
    }
    return Precios(fecha_consulta=datos["fecha_consulta"], modelos=modelos)


def entrada_no_cacheada(llamada: Llamada) -> int:
    """La entrada que se cobra a precio completo: la total menos la leída y la escrita en caché (hallazgo 6)."""
    return llamada.entrada_tokens - llamada.cache_lectura_tokens - llamada.cache_escritura_tokens


_MILLON = Decimal(1_000_000)


def _precios_disponibles() -> Precios | None:
    """Los precios, o None si config/precios.yaml falta o no se puede leer: sin precios, la línea sale sin costo."""
    try:
        return precios()
    except (OSError, yaml.YAMLError, KeyError, TypeError, ArithmeticError) as error:
        _aviso.warning("No se pudieron leer los precios del registro (%s).", type(error).__name__)
        return None


def costo_usd(llamada: Llamada, tabla: Precios | None = None) -> Decimal | None:
    """El costo de una llamada con config/precios.yaml; None si el modelo no tiene precio o el prompt pasa su tramo."""
    tabla = tabla or _precios_disponibles()
    precio = tabla.de(llamada.modelo) if tabla else None
    if precio is None:
        return None
    if precio.hasta_tokens_de_prompt is not None and llamada.entrada_tokens > precio.hasta_tokens_de_prompt:
        return None
    escritura = precio.cache_escritura if precio.cache_escritura is not None else precio.entrada
    total = (
        entrada_no_cacheada(llamada) * precio.entrada
        + llamada.cache_lectura_tokens * precio.cache_lectura
        + llamada.cache_escritura_tokens * escritura
        + llamada.salida_tokens * precio.salida
    )
    return total / _MILLON


# ------------------------------------------------------------------ commit (C5.3)


@functools.cache
def commit() -> dict[str, Any] | None:
    """El commit del código que corre y si hay cambios sin commit; None si no hay git o no es un repositorio.

    Se lee una vez por proceso: es el código que se cargó al arrancar.
    """
    try:
        sha = _git("rev-parse", "HEAD")
        cambios = _git("status", "--porcelain")
    except (OSError, subprocess.SubprocessError):
        return None
    return {"sha": sha, "sucio": bool(cambios)}


def _git(*argumentos: str) -> str:
    resultado = subprocess.run(
        ["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, timeout=10, check=True
    )
    return resultado.stdout.strip()


# ------------------------------------------------------------------ partes de la línea


def hash_historial(mensajes: Sequence[ModelMessage]) -> str:
    """sha256 del historial que se mandó al modelo, sobre su JSON compacto."""
    texto = dumps(_mensajes_json(mensajes), compacto=True)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _mensajes_json(mensajes: Sequence[ModelMessage]) -> list[Any]:
    return ModelMessagesTypeAdapter.dump_python(list(mensajes), mode="json")


def historial_de(linea: Mapping[str, Any]) -> tuple[ModelMessage, ...]:
    """El historial que se mandó en el turno de la línea, como mensajes de Pydantic AI."""
    return tuple(ModelMessagesTypeAdapter.validate_python(linea["historial"]["mensajes"]))


def herramientas_de(evidencias: Sequence[Evidencia]) -> list[dict[str, Any]]:
    """Cada herramienta que corrió: su id, su nombre, su entrada validada y su Resultado completo, con la consulta."""
    return [
        {
            "gen_ai.tool.call.id": evidencia.id_llamada,
            "gen_ai.tool.name": evidencia.herramienta,
            "gen_ai.tool.call.arguments": evidencia.entrada,
            "gen_ai.tool.call.result": json.loads(evidencia.resultado.texto_registro()),
        }
        for evidencia in evidencias
    ]


def _llamada(numero: int, llamada: Llamada, tabla: Precios | None) -> dict[str, Any]:
    return {
        "n": numero,
        "gen_ai.response.model": llamada.modelo,
        "gen_ai.provider.name": llamada.proveedor,
        "gen_ai.response.finish_reasons": [llamada.fin] if llamada.fin else [],
        "gen_ai.usage.input_tokens": llamada.entrada_tokens,
        "gen_ai.usage.output_tokens": llamada.salida_tokens,
        "gen_ai.usage.cache_read.input_tokens": llamada.cache_lectura_tokens,
        "gen_ai.usage.cache_creation.input_tokens": llamada.cache_escritura_tokens,
        "entrada_no_cacheada": entrada_no_cacheada(llamada),
        "razonamiento": llamada.razonamiento_tokens,
        "ms": llamada.ms,
        "costo_usd": costo_usd(llamada, tabla) if tabla else None,
    }


def _verificacion(verificacion: Any) -> dict[str, Any] | None:
    if verificacion is None:
        return None
    return {
        "desenlace": verificacion.desenlace,
        "versiones": list(verificacion.versiones),
        "revisiones": [
            {
                "aprobada": revision.aprobada,
                "cifras_revisadas": revision.cifras_revisadas,
                "citas_revisadas": revision.citas_revisadas,
                "discrepancias": [dataclasses.asdict(d) for d in revision.discrepancias],
            }
            for revision in verificacion.revisiones
        ],
        "correccion": verificacion.correccion,
    }


def _suma_costos(costos: Sequence[Decimal | None]) -> Decimal | None:
    """El costo del turno; None si alguna llamada no tiene precio (un costo parcial pasaría por completo)."""
    if any(costo is None for costo in costos):
        return None
    return sum(costos, Decimal(0))


def _fuente_de_precios(tabla: Precios | None) -> dict[str, str] | None:
    if tabla is None:
        return None
    return {"archivo": "config/precios.yaml", "fecha_consulta": tabla.fecha_consulta.isoformat()}


def linea_de_turno(
    respuesta: Respuesta,
    *,
    pregunta: str,
    historial: Sequence[ModelMessage],
    id_sesion: str | None,
    origen: Origen,
    categoria: str | None,
    hora: dt.datetime,
    latencia_ms: int,
    hoy_real: dt.date,
    topes: Topes,
    ajustes: AjustesDelModelo,
) -> dict[str, Any]:
    """La línea de un turno, armada con su Respuesta y lo que responder() sabe del turno.

    historial son los mensajes que se mandaron antes de la pregunta (historial.mensajes() antes del turno).
    """
    traza = respuesta.traza
    tabla = _precios_disponibles()
    llamadas = [_llamada(i, llamada, tabla) for i, llamada in enumerate(traza.llamadas, start=1)]
    hash_pregunta = hashlib.sha256(pregunta.encode("utf-8")).hexdigest()
    return {
        "tipo": "turno",
        "version": VERSION,
        "turno_id": respuesta.turno_id,
        "gen_ai.conversation.id": id_sesion,
        "origen": origen,
        "categoria": categoria,
        "hora": formatear_hora(hora),
        "hoy_real": hoy_real.isoformat(),
        "pregunta": pregunta[:MAXIMO_CARACTERES_PREGUNTA],
        "pregunta_caracteres": len(pregunta),
        "pregunta_sha256": hash_pregunta,
        "historial": {
            "n_mensajes": len(historial),
            "sha256": hash_historial(historial),
            "mensajes": _mensajes_json(historial),
        },
        "gen_ai.request.model": traza.modelo,
        "prompt_sha256": traza.prompt_sha256,
        "herramientas_definidas": list(traza.herramientas),
        "herramientas_sha256": traza.herramientas_sha256,
        "commit": commit(),
        "hash_fuentes": traza.hash_fuentes,
        "base_reconstruida": traza.base_reconstruida,
        "ajustes": dataclasses.asdict(ajustes),
        "ajustes_modelo": dict(traza.ajustes_modelo),
        "topes": dataclasses.asdict(topes),
        # Los resultados de las herramientas van antes del texto final del modelo, como en el turno.
        "herramientas": herramientas_de(respuesta.evidencias),
        "n_herramientas": len(respuesta.evidencias),
        "reintentos_validacion": traza.reintentos_validacion,
        "tickets_sospechosos": list(traza.tickets_sospechosos),
        "verificacion": _verificacion(traza.verificacion),
        "respuesta": respuesta.texto,
        "estado": respuesta.estado,
        "tope": traza.tope,
        "falla": dataclasses.asdict(traza.falla) if traza.falla else None,
        "reintentos": [dataclasses.asdict(reintento) for reintento in traza.reintentos],
        "espera_reintentos_segundos": traza.espera_reintentos_segundos,
        "n_llamadas": len(llamadas),
        "llamadas": llamadas,
        "gen_ai.usage.input_tokens": traza.entrada_tokens,
        "gen_ai.usage.output_tokens": traza.salida_tokens,
        "gen_ai.usage.cache_read.input_tokens": sum(ll.cache_lectura_tokens for ll in traza.llamadas),
        "gen_ai.usage.cache_creation.input_tokens": sum(ll.cache_escritura_tokens for ll in traza.llamadas),
        "entrada_no_cacheada": sum(llamada["entrada_no_cacheada"] for llamada in llamadas),
        "razonamiento": sum(ll.razonamiento_tokens for ll in traza.llamadas),
        "costo_usd": _suma_costos([llamada["costo_usd"] for llamada in llamadas]),
        "precios": _fuente_de_precios(tabla),
        "latencia_ms": latencia_ms,
    }


# ------------------------------------------------------------------ escribir (K12, K25, K26, K27)


def _limpio(valor: Any, ruta: tuple[str, ...] = ()) -> Any:
    """El valor con cada texto sin datos de contacto ni llaves, y cada número no finito como None.

    ruta son las claves desde la raíz de la línea: un id o un hash con su formato exacto se deja tal cual.
    """
    if isinstance(valor, str):
        return valor if _es_id_valido(ruta, valor) else _texto_limpio(valor)
    if isinstance(valor, Mapping):
        return {clave: _limpio(dato, (*ruta, str(clave))) for clave, dato in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_limpio(dato, (*ruta, "*")) for dato in valor]
    if isinstance(valor, float) and not math.isfinite(valor):
        return None
    if isinstance(valor, Decimal) and not valor.is_finite():
        return None
    return valor


def _es_id_valido(ruta: tuple[str, ...], texto: str) -> bool:
    """True si la ruta es la de un id o un hash y el texto tiene exactamente su formato."""
    formato = _FORMATOS_DE_ID.get(ruta)
    return formato is not None and formato.fullmatch(texto) is not None


def _texto_limpio(texto: str) -> str:
    texto = _LLAVE.sub(MARCA_LLAVE, texto)
    if _DATOS_DE_CONTACTO.intersection(privacidad.buscar_datos_personales(texto)):
        return privacidad.enmascarar(texto) or ""
    return texto


def serializar(linea: Mapping[str, Any]) -> str:
    """La línea como JSON de una sola línea, sin NaN, sin datos de contacto ni llaves, terminada en \\n."""
    texto = dumps(_limpio(linea), compacto=True)
    for caracter, escape in _SEPARADORES.items():
        texto = texto.replace(caracter, escape)
    return f"{texto}\n"


def escribir(archivo: Path, linea: Mapping[str, Any]) -> bool:
    """Agrega la línea al registro. No lanza: si no puede, avisa con el tipo del error y devuelve False (K27)."""
    try:
        texto = serializar(linea)
        archivo = Path(archivo)
        archivo.parent.mkdir(parents=True, exist_ok=True)
        # Un solo write por línea y un cerrojo entre las sesiones del mismo proceso, para que no se mezclen.
        with _cerrojo, open(archivo, "a", encoding="utf-8", newline="\n") as salida:
            salida.write(texto)
    except Exception as error:  # noqa: BLE001 (K27: ningún error del registro tumba la app)
        _aviso.warning("No se pudo escribir el registro de turnos (%s).", type(error).__name__)
        return False
    return True


def registrar_turno(archivo: Path, respuesta: Respuesta, **turno: Any) -> bool:
    """Arma la línea del turno y la escribe. No lanza: un error al armarla tampoco tumba la app (K27)."""
    try:
        linea = linea_de_turno(respuesta, **turno)
    except Exception as error:  # noqa: BLE001 (K27)
        _aviso.warning("No se pudo armar la línea del registro de turnos (%s).", type(error).__name__)
        return False
    return escribir(archivo, linea)


def linea_de_marca(turno_id: str, hora: dt.datetime, comentario: str = "") -> dict[str, Any]:
    """La línea de una marca de "respuesta incorrecta" (T4.4, K28): qué turno, a qué hora y el comentario, si lo escribieron.

    No lleva la pregunta ni la respuesta: están en la línea del turno, que se encuentra por turno_id. El id conserva su
    formato (_FORMATOS_DE_ID) también cuando trae 10 dígitos seguidos. El comentario es texto de la persona: va recortado al
    tope, sin espacios en los extremos, y serializar() le quita teléfonos, correos y llaves como a la pregunta. Sin texto,
    la línea no trae el campo.
    """
    linea = {"tipo": TIPO_MARCA, "version": VERSION, "turno_id": turno_id, "hora": formatear_hora(hora)}
    recortado = comentario.strip()[:MAXIMO_CARACTERES_COMENTARIO]
    if recortado:
        linea["comentario"] = recortado
    return linea


def registrar_marca(archivo: Path, turno_id: str, comentario: str = "") -> bool:
    """Marca el turno como respuesta incorrecta. No lanza: si no puede escribir, devuelve False (K27)."""
    try:
        linea = linea_de_marca(turno_id, ahora(), comentario)
    except Exception as error:  # noqa: BLE001 (K27)
        _aviso.warning("No se pudo armar la línea de la marca (%s).", type(error).__name__)
        return False
    return escribir(archivo, linea)


# ------------------------------------------------------------------ leer y reproducir (E16, C5.3)


def leer(archivo: Path = ARCHIVO) -> Iterator[dict[str, Any]]:
    """Las líneas del registro, de turnos y de marcas, en orden. Una línea que no es JSON (un corte) se salta."""
    with open(archivo, encoding="utf-8") as entrada:
        for texto in entrada:
            try:
                yield json.loads(texto)
            except json.JSONDecodeError:
                continue


def buscar_turno(archivo: Path, turno_id: str) -> dict[str, Any] | None:
    """La línea del turno con ese id, o None si no está."""
    for linea in leer(archivo):
        if linea.get("tipo") == "turno" and linea.get("turno_id") == turno_id:
            return linea
    return None


def datos_de_reproduccion(linea: Mapping[str, Any]) -> dict[str, Any]:
    """Lo que se necesita para volver a correr un turno: modelo, hashes, commit, fecha, ajustes, topes y pregunta."""
    return {
        "turno_id": linea["turno_id"],
        "hora": linea["hora"],
        "modelo": linea["gen_ai.request.model"],
        "prompt_sha256": linea["prompt_sha256"],
        "herramientas_sha256": linea["herramientas_sha256"],
        "commit": linea["commit"],
        "hash_fuentes": linea["hash_fuentes"],
        "hoy_real": linea["hoy_real"],
        "ajustes": linea["ajustes"],
        "topes": linea["topes"],
        "historial_sha256": linea["historial"]["sha256"],
        "pregunta": linea["pregunta"],
    }


def diferencias(linea: Mapping[str, Any], *, base: Path | None = None) -> dict[str, tuple[Any, Any]]:
    """Lo que cambió desde el turno (valor del turno, valor de ahora): prompt, herramientas, commit y datos.

    Sin diferencias, reproducir() corre con lo mismo que el turno salvo el modelo, que con el modelo real puede
    contestar distinto. El hash de los datos se lee de la base (la de construir.BASE si no se da otra).
    """
    from agente import agente, construir

    ahora_mismo = {
        "prompt_sha256": agente.PROMPT_SHA256,
        "herramientas_sha256": agente.HERRAMIENTAS_SHA256,
        "commit": (commit() or {}).get("sha"),
        "hash_fuentes": _hash_de_la_base(base or construir.BASE),
    }
    del_turno = {**{clave: linea[clave] for clave in ahora_mismo}, "commit": (linea["commit"] or {}).get("sha")}
    return {
        clave: (del_turno[clave], ahora_mismo[clave])
        for clave in ahora_mismo
        if del_turno[clave] != ahora_mismo[clave]
    }


def _hash_de_la_base(base: Path) -> str | None:
    from agente import construir

    guardados = construir.leer_hashes(Path(base))
    return guardados.hash_fuentes.sha256 if guardados else None


def reproducir(linea: Mapping[str, Any], *, modelo: Any = None, archivo_registro: Path | None = None, **opciones: Any):
    """Vuelve a correr el turno de la línea: la misma pregunta, historial, fecha, ajustes y topes.

    modelo es el Model o el id; sin él, el id de la línea, que necesita la llave de su proveedor: se lee .env, como
    hace responder() cuando no recibe modelo (con el id como texto no lo lee). Un turno de la cobertura (T4.2, modelo
    sin_modelo) se repite con el manifiesto, sin modelo y sin leer .env. opciones pasan a responder() (base, dir_datos,
    dir_config). La reproducción no se escribe en el registro salvo que se dé archivo_registro.
    """
    from agente import agente, manifiesto
    from agente.ajustes_modelo import AjustesDelModelo

    hoy_real = dt.date.fromisoformat(linea["hoy_real"])
    if linea["gen_ai.request.model"] == manifiesto.MODELO:
        return manifiesto.responder(
            linea["pregunta"], hoy_real=hoy_real, archivo_registro=archivo_registro, origen=linea["origen"],
            categoria=linea["categoria"], **opciones,
        )
    if modelo is None:
        from dotenv import load_dotenv

        load_dotenv()
    return agente.responder(
        linea["pregunta"],
        historial_de(linea),
        modelo=modelo if modelo is not None else linea["gen_ai.request.model"],
        hoy_real=hoy_real,
        topes=agente.Topes(**linea["topes"]),
        ajustes=AjustesDelModelo(**linea["ajustes"]),
        origen=linea["origen"],
        categoria=linea["categoria"],
        archivo_registro=archivo_registro,
        **opciones,
    )


def main(argumentos: Sequence[str] | None = None) -> int:
    """`python -m agente.registro <turno_id>`: muestra lo que hace falta para reproducir ese turno."""
    lector = argparse.ArgumentParser(prog="python -m agente.registro", description=main.__doc__)
    lector.add_argument("turno_id")
    lector.add_argument("--archivo", type=Path, default=ARCHIVO)
    opciones = lector.parse_args(argumentos)
    try:
        linea = buscar_turno(opciones.archivo, opciones.turno_id)
    except OSError as error:
        print(f"No pude leer el registro ({type(error).__name__}).", file=sys.stderr)
        return 1
    if linea is None:
        print("No hay un turno con ese id en el registro.", file=sys.stderr)
        return 1
    # En Windows, una salida redirigida usa cp1252, que no tiene todos los caracteres de una pregunta (−, …).
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(datos_de_reproduccion(linea), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
