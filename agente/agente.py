"""Punto de entrada del agente: responder(pregunta, historial) (T3.1 a T3.7).

Arma el prompt de sistema y el historial con tope (D10), pone los topes de llamadas y de tiempo,
reintenta ante un 429 y usa la caché del proveedor. La interfaz y el comando de evaluación llaman
a esta misma función.

Un turno (T3.1):

1. asegurar_base() deja build/tienda.duckdb al día con datos/, politicas/ y config/ (T1.7). Si falta una
   fuente o la base no se puede preparar, la Respuesta dice que no pudo consultar, sin cifra y sin llamar
   al modelo (fuente_no_disponible_de, de agente/herramientas/errores.py).
2. abrir_consultas() abre la base en solo lectura para este turno y la cierra al terminar. En Windows no
   se puede reemplazar una base abierta: una conexión que quedara abierta entre turnos haría que el
   siguiente asegurar_base() lanzara BaseEnUso al cambiar una fuente.
3. El modelo recibe el prompt de sistema (agente/prompt_sistema.md) como instructions, que Pydantic AI
   manda en cada solicitud, también con historial; el historial y la pregunta; y las cinco herramientas.
   El historial (agente/historial.py, T3.5, D10-A) son las últimas 6 vueltas de la sesión como texto: la
   pregunta, la respuesta final y los parámetros de cada herramienta, sin razonamientos ni resultados. Lo crea
   quien atiende la sesión del navegador y le agrega cada vuelta; responder() no guarda nada entre llamadas.
   Cada llamada a una herramienta toma un cursor nuevo con Consultas.sesion(), porque Pydantic AI corre
   en paralelo las de una misma respuesta, y devuelve al modelo Resultado.texto_modelo(), sin la
   consulta SQL.
4. La salida del modelo es texto. La Respuesta la arma este código: el texto, las evidencias (cada
   herramienta que corrió, con su entrada validada y su Resultado completo, consulta incluida) y la
   traza (modelo, sha256 del prompt y de las definiciones de herramientas, tokens por llamada). El
   verificador (T3.4), el registro (T4.3) y el panel de traza (T4.1) leen las evidencias, no el texto
   del modelo.
5. Los topes de K19 (Topes, T3.2) valen para el turno entero, desde que entra responder() hasta que sale:
   4 solicitudes al modelo, 12 ejecuciones de herramientas, 60 s, max_tokens en cada llamada y 1,500
   caracteres en la pregunta. Al llegar a uno, la Respuesta sale con estado "tope", un texto fijo sin
   cifras y lo que alcanzó a correr (evidencias y tokens), sin lanzar una excepción. Un reintento del
   mismo turno (un 429 en T3.3, una discrepancia del verificador en T3.4) corre con el mismo
   Presupuesto: no abre otro.
6. El modelo va envuelto en ModeloConReintentos (agente/reintentos.py, K20): ante un 429 o un servicio
   saturado espera lo que pida Retry-After, o el retroceso exponencial, y repite la solicitud hasta
   Topes.reintentos veces mientras la espera quepa en los segundos del turno. Cada reintento queda en
   Traza.reintentos con su espera. Si el modelo no contesta (se acabaron los reintentos, o la llave o la
   configuración no sirven), la Respuesta sale con estado "modelo_no_disponible", un texto fijo sin cifras y
   la causa en Traza.falla (K21).
7. Si la base o la configuración no se pueden usar (ErrorConstruccion), la Respuesta sale con estado
   "fuente_no_disponible" y "no pude consultar <archivo>", sin cifra, armada con el nombre del archivo y nunca
   con str(error), que trae la ruta con el usuario de Windows. Ante FuentesCambiaron o BaseEnUso la
   preparación de la base se repite una vez.
8. Antes de cerrar la base, el verificador (agente/verificacion.py, T3.4) revisa el texto del modelo contra la
   pregunta y las evidencias de este turno, no contra el historial: cada cifra, fecha e identificador tiene que
   venir de ellas (K16), cada cita tiene que existir en el índice y venir de un fragmento recuperado (K17), y
   no puede haber datos personales (K18). Ante una discrepancia (D16-A), el modelo recibe la lista y escribe
   otra versión en el mismo Presupuesto; si sigue, la respuesta se arma con la evidencia, sin modelo. Las
   versiones y sus revisiones quedan en Traza.verificacion, y la traza visible sale sin teléfonos ni correos.
9. Cada solicitud lleva el esfuerzo de razonamiento y la caché del proveedor (agente/ajustes_modelo.py, T3.6, D8-D):
   Haiku 5.5 con razonamiento adaptativo en "medium" y cache_control en el prompt de sistema y en la última herramienta;
   Gemini con thinking_level MEDIUM. El prefijo (herramientas y prompt) no trae fechas ni ids, y por eso la segunda
   llamada de una sesión lee la caché. Traza.ajustes_modelo dice con cuál configuración corrió el turno y
   Llamada.razonamiento_tokens separa el razonamiento de la salida.
10. El texto de los tickets es dato (T3.7, C7.1, K08). La herramienta de tickets no manda títulos ni descripciones
   en un conteo, delimita la descripción en el detalle y marca por folio los tickets cuyo texto le da órdenes al
   asistente. Esos folios quedan en Traza.tickets_sospechosos, y responder() cambia cualquier renglón del modelo que
   hable de la sospecha de esos folios por una línea fija de aviso, con los folios y sin el texto del ticket, porque un
   modelo que obedece es justo el que la apagaría (D3-A del plan de huecos). El aviso no va en un tope ni en una falla,
   que no dan cifras ni folios. Ninguna herramienta escribe y la base se abre en solo lectura (G8).
11. Cada turno lleva un turno_id (UUID v4) y, si quien llama da archivo_registro (logs/turnos.jsonl en la interfaz y
   en el comando de evaluación), deja su línea con agente/registro.py (T4.3): la pregunta y el historial que se
   mandó, el modelo, los hashes del prompt y de las herramientas, el commit, el hash de las fuentes de la base que
   respondió, cada herramienta con su resultado, el verificador, el estado, los reintentos, los tokens, el costo y
   la latencia de cada llamada y del turno. Si el registro no se puede escribir, la Respuesta sale igual (K27).

El prompt no repite ninguna definición ni corte: viven en config/supuestos.yaml y llegan en cada
resultado (definición, filtros, corte y notas). Cambiar una definición en una copia de config/ cambia la
cifra con el mismo prompt (C1.4). Un argumento que no pasa la validación de Pydantic no ejecuta la
herramienta: vuelve al modelo como ToolReturnPart con outcome "retried" y no deja evidencia.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, Tool, capture_run_messages
from pydantic_ai.exceptions import ModelAPIError, RunTimedOut, UnexpectedModelBehavior, UsageLimitExceeded, UserError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from agente import construir, definiciones
from agente import historial as modulo_historial
from agente import registro as modulo_registro
from agente import reintentos as modulo_reintentos
from agente import verificacion as modulo_verificacion
from agente.ajustes_modelo import AJUSTES, AJUSTES_BASE, AjustesDelModelo  # noqa: F401  (AJUSTES_BASE es de T6.1)
from agente.consultas import Consultas, abrir_consultas
from agente.definiciones import Supuestos
from agente.herramientas import buscar_politicas as modulo_politicas
from agente.herramientas import caducidades as modulo_caducidades
from agente.herramientas import comun
from agente.herramientas import inventario as modulo_inventario
from agente.herramientas import tickets as modulo_tickets
from agente.herramientas import ventas as modulo_ventas
from agente.herramientas.comun import (
    EntradaCaducidades,
    EntradaInventario,
    EntradaPoliticas,
    EntradaTickets,
    EntradaVentas,
    Resultado,
)
from agente.herramientas.errores import fuente_no_disponible_de
from agente.reintentos import FallaDelModelo, Reintento

ARCHIVO_PROMPT = Path(__file__).resolve().parent / "prompt_sistema.md"

# D19-D: Haiku 5.5 por omisión; Gemini en el plan gratuito es la alterna (MODELO en .env).
MODELO_POR_OMISION = "anthropic:claude-haiku-5-5"

EstadoRespuesta = Literal["ok", "fuente_no_disponible", "modelo_no_disponible", "tope"]
# Qué tope de K19 cortó el turno. "tokens" es una respuesta del modelo que se detuvo en max_tokens.
MotivoTope = Literal["solicitudes", "herramientas", "segundos", "tokens", "caracteres"]


def _leer_prompt(ruta: Path) -> str:
    """El prompt con fin de línea LF, que es lo que guarda el commit aunque el disco esté en CRLF.

    Con core.autocrlf=true, Git deja el archivo en CRLF en Windows; el texto que se manda al modelo y su
    hash son los del blob del commit en los dos sistemas (K15).
    """
    return ruta.read_bytes().decode("utf-8").replace("\r\n", "\n")


PROMPT_SISTEMA = _leer_prompt(ARCHIVO_PROMPT)
PROMPT_SHA256 = hashlib.sha256(PROMPT_SISTEMA.encode("utf-8")).hexdigest()


def modelo_del_entorno(entorno: Mapping[str, str]) -> str:
    """El id del modelo de MODELO, o el de omisión si no está o está vacío.

    Al copiar .env.example sin editarlo, load_dotenv deja MODELO="", y entorno.get("MODELO", valor)
    devolvería la cadena vacía.
    """
    return (entorno.get("MODELO") or "").strip() or MODELO_POR_OMISION


def _modelo_configurado() -> str:
    """Lee .env (sin pisar el entorno) y devuelve el modelo configurado. Solo corre con el modelo real."""
    from dotenv import load_dotenv

    load_dotenv()
    return modelo_del_entorno(os.environ)


# ------------------------------------------------------------------ topes (K19)


@dataclass(frozen=True)
class Topes:
    """El presupuesto de un turno (K19, C6.3): lo que responder() no deja pasar aunque el modelo siga pidiendo.

    solicitudes son las llamadas al modelo (request_limit de Pydantic AI). Una cifra simple usa 2 (D9-A); tres
    rondas de herramientas y la redacción caben en 4, y la quinta solicitud no sale.

    herramientas son las ejecuciones de herramientas del turno: dos rondas completas de las cinco y dos más. Pydantic AI
    revisa el tope antes de ejecutar el grupo de llamadas que lo pasaría, y no ejecuta ninguna.

    segundos se miden en responder(), desde que entra: cuentan preparar la base, cada llamada al modelo, la
    espera de un reintento y las herramientas. Es el máximo de latencia del plan. El timeout de ModelSettings no
    sirve para esto: en una respuesta transmitida solo mide la espera entre fragmentos (hallazgo 5).

    max_tokens es el de cada llamada y cuenta el razonamiento, que en Haiku 5.5 y en Gemini sale de la misma
    bolsa: con poco lugar el modelo se queda sin texto. Fue 4,096 hasta el 2026-11-05; con Haiku en "medium" (D8-D)
    «¿qué productos corren riesgo de caducar sin venderse?» razonó 2,340 tokens, escribió 1,756 y se cortó, y
    en 285 llamadas en "medium" dos llegaron a 4,096 y otra a 4,059. 8,192 es el doble y queda debajo de los
    21,333 desde los que el SDK de Anthropic exige transmitir. El peor turno son 4 llamadas por 8,192: 32,768
    tokens de salida.

    caracteres es el largo de la pregunta, para que la entrada no pase de un tamaño razonable (LLM10:2025). Las
    de la batería miden menos de 150, y 1,500 caracteres son del orden de 450 tokens. El resto de la entrada se
    acota por construcción (el historial por D10, en agente/historial.py, cada herramienta por sus filas máximas y el texto de un
    ticket por TOPE_TITULO y TOPE_DESCRIPCION), así que no hay un tope de tokens de entrada.

    reintentos son los de cada solicitud al modelo ante un 429 o un servicio saturado (K20): 3 reintentos
    esperan 1, 2 y 4 s si el proveedor no dice cuánto. No gastan solicitudes de las 4, porque Pydantic AI
    cuenta las respuestas que recibe y una solicitud rechazada no deja ninguna; los acota el tiempo, ya que
    cada espera tiene que caber en los segundos que le quedan al turno con un margen (agente/reintentos.py).
    """

    solicitudes: int = 4
    herramientas: int = 12
    segundos: float = 60.0
    max_tokens: int = 8192
    caracteres: int = 1500
    reintentos: int = 3

    def limites(self) -> UsageLimits:
        """Los límites de uso de Pydantic AI: solicitudes al modelo y ejecuciones de herramientas."""
        return UsageLimits(request_limit=self.solicitudes, tool_calls_limit=self.herramientas)

    def ajustes(self) -> ModelSettings:
        """Los ajustes del modelo que fijan los topes: max_tokens en cada llamada."""
        return ModelSettings(max_tokens=self.max_tokens)


TOPES = Topes()


@dataclass
class Presupuesto:
    """Lo que lleva gastado un turno: el reloj y el uso del modelo, compartidos por todo lo que corra en él.

    Se abre al entrar a responder(). Un reintento del mismo turno, ante un 429 (K20) o ante una discrepancia
    del verificador (D16), corre con este mismo Presupuesto y descuenta del mismo tiempo y de las mismas
    solicitudes: no abre otros. reintentos anota cada reintento que se hizo en el turno, con su espera.
    """

    topes: Topes
    inicio: float = field(default_factory=time.monotonic)
    uso: RunUsage = field(default_factory=RunUsage)
    reintentos: list[Reintento] = field(default_factory=list)
    # El esfuerzo de razonamiento y la caché de este turno (T3.6), y lo que _correr mandó de ellos al modelo, que es
    # lo que la traza anota. Un reintento del mismo turno manda lo mismo: cambiarlo invalidaría la caché.
    ajustes: AjustesDelModelo = AJUSTES
    ajustes_enviados: dict[str, Any] = field(default_factory=dict)

    def segundos_restantes(self) -> float:
        return self.topes.segundos - (time.monotonic() - self.inicio)


# Lo que ve la gerente cuando un tope corta el turno: nunca una cifra, ni siquiera la de una herramienta que
# alcanzó a correr (K19), ni jerga (K21). Lo que cambia es qué hacer: bajar la pregunta a algo más concreto,
# reintentar o acortarla.
_DEMASIADOS_PASOS = (
    "No pude terminar esta consulta: necesitaba más pasos de los que puedo dar en una sola respuesta, "
    "así que no te doy ninguna cifra. Hazme la pregunta más concreta, por ejemplo una tienda o un periodo a la vez."
)
MENSAJES_DE_TOPE: dict[MotivoTope, str] = {
    "solicitudes": _DEMASIADOS_PASOS,
    "herramientas": _DEMASIADOS_PASOS,
    "segundos": (
        "No pude terminar esta consulta a tiempo, así que no te doy ninguna cifra. "
        "Vuelve a intentarlo en un momento o hazme la pregunta más concreta."
    ),
    "tokens": (
        "Mi respuesta salió más larga de lo permitido y quedó incompleta, así que no te doy ninguna cifra. "
        "Hazme la pregunta más concreta, por ejemplo una tienda o un periodo a la vez."
    ),
    "caracteres": "Tu mensaje es demasiado largo para una sola pregunta. Resúmelo y vuelve a enviarlo.",
}

# Los límites que configura Topes.limites(), con el nombre con que los cita el mensaje de UsageLimitExceeded.
_MOTIVO_POR_LIMITE: dict[str, MotivoTope] = {
    "request_limit": "solicitudes",
    "tool_calls_limit": "herramientas",
}

# Cuánto espera responder() a que una herramienta que el corte por tiempo dejó atrás termine su consulta.
ESPERA_DE_CIERRE_SEGUNDOS = 2.0
PAUSA_DE_CIERRE_SEGUNDOS = 0.05


def _motivo_del_limite(error: UsageLimitExceeded) -> MotivoTope:
    """Qué tope de Topes.limites() se pasó, según el límite que nombra el mensaje de Pydantic AI (2.61.0)."""
    for limite, motivo in _MOTIVO_POR_LIMITE.items():
        if limite in str(error):
            return motivo
    # Un límite que Topes.limites() no configura no es un tope conocido, y no se disfraza de uno.
    raise error


def _cerrar(consultas: Consultas, espera: float = ESPERA_DE_CIERRE_SEGUNDOS) -> None:
    """Cierra la base, esperando hasta `espera` segundos a que una herramienta abandonada termine su consulta.

    Un corte por tiempo cancela la espera de una herramienta, pero no su hilo: la herramienta sigue en su
    consulta y DuckDB 2.0.0 no cierra una conexión ocupada, lanza ResourceInUseException al instante.
    """
    limite = time.monotonic() + espera
    while True:
        try:
            consultas.cerrar()
            return
        except duckdb.ResourceInUseException:
            if time.monotonic() >= limite:
                raise
            time.sleep(PAUSA_DE_CIERRE_SEGUNDOS)


# ------------------------------------------------------------------ Respuesta


@dataclass(frozen=True)
class Evidencia:
    """Una herramienta que corrió en el turno: su entrada validada y su Resultado completo, con la consulta."""

    id_llamada: str
    herramienta: str
    entrada: dict[str, Any]
    resultado: Resultado


@dataclass(frozen=True)
class Llamada:
    """Una solicitud al modelo con sus tokens, como los da el proveedor (RequestUsage de Pydantic AI).

    entrada_tokens incluye los de caché (lectura y escritura) y salida_tokens incluye el razonamiento
    (hallazgo 6): las partes se guardan aparte para medir las palancas 1 y 4 (T3.6, T6.2). razonamiento_tokens es
    cero si el modelo no razonó o si el proveedor no lo separa. proveedor y fin son el provider_name y el
    finish_reason de la respuesta. ms es la latencia aproximada de la llamada, del timestamp de la solicitud al de la
    respuesta (línea base §8), con la espera de un reintento incluida; no cuenta al comparar dos trazas, porque el
    reloj nunca da lo mismo dos veces.
    """

    modelo: str
    entrada_tokens: int
    salida_tokens: int
    cache_lectura_tokens: int
    cache_escritura_tokens: int
    razonamiento_tokens: int = 0
    proveedor: str | None = None
    fin: str | None = None
    ms: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Traza:
    """Lo que hace falta para comparar dos turnos (C5.2) y reproducir uno (K15): prompt, herramientas, modelo y tokens."""

    modelo: str
    prompt_sha256: str
    herramientas: tuple[str, ...]
    herramientas_sha256: str
    llamadas: tuple[Llamada, ...]
    entrada_tokens: int
    salida_tokens: int
    reintentos_validacion: int
    hash_fuentes: str | None
    base_reconstruida: bool
    # Qué tope cortó el turno (K19); None si no lo cortó ninguno.
    tope: MotivoTope | None = None
    # Cada reintento del turno (K20) con su espera; la preparación de la base también los deja aquí.
    reintentos: tuple[Reintento, ...] = ()
    # Por qué el modelo no contestó (K21); None si contestó. Es la causa exacta, para quien administra el sistema.
    falla: FallaDelModelo | None = None
    # Lo que hizo el verificador (T3.4, D16-A): las versiones del modelo, la revisión de cada texto y el desenlace.
    # None si el turno no llegó a un texto del modelo (un tope, una falla o la base no disponible).
    verificacion: modulo_verificacion.Verificacion | None = None
    # El esfuerzo de razonamiento y la caché que viajaron con las solicitudes del turno (T3.6), con las claves del
    # proveedor; vacío si el turno no llamó al modelo o su modelo no es de Claude ni de Gemini. El registro (T4.3) y
    # la medición de las palancas (T6.2) lo leen para saber con qué configuración se corrió.
    ajustes_modelo: Mapping[str, Any] = field(default_factory=dict)
    # Los folios que el marcador de frases de la herramienta de tickets marcó como sospechosos en este turno (T3.7,
    # K08): su texto trae órdenes para el asistente. Se toman como dato; el registro (T4.3) los guarda.
    tickets_sospechosos: tuple[str, ...] = ()

    @property
    def espera_reintentos_segundos(self) -> float:
        """Lo que se esperó en total por los reintentos del turno."""
        return sum(reintento.espera_segundos for reintento in self.reintentos)


@dataclass(frozen=True)
class Respuesta:
    """La respuesta de un turno, armada por código.

    texto es la salida del modelo que pasó el verificador, la respuesta armada con la evidencia si no la pasó
    (D16-A, traza.verificacion), o el mensaje determinista cuando no se pudo consultar o un tope cortó el
    turno (sin cifra en los tres casos). estado es "ok", "fuente_no_disponible", "modelo_no_disponible" o
    "tope"; en el último, traza.tope dice cuál, y en el penúltimo, traza.falla. evidencias son las
    herramientas que corrieron, en el orden en que el modelo las pidió, también las de un turno cortado. mensajes
    son los de este turno (la pregunta, las llamadas, los resultados y el texto); el Historial los usa para saber
    si guarda la vuelta, y al turno siguiente le manda solo texto. En un turno cortado, o en el que el modelo no
    contestó, son solo la pregunta y el mensaje, porque las llamadas a herramientas que quedaron sin respuesta no
    son un historial válido, y si el tope fue el largo del mensaje no hay ninguno. turno_id es el UUID v4 del turno,
    el de su línea en logs/turnos.jsonl (T4.3), con el que la interfaz marca una respuesta incorrecta (T4.4).
    """

    texto: str
    estado: EstadoRespuesta
    evidencias: tuple[Evidencia, ...]
    traza: Traza
    mensajes: tuple[ModelMessage, ...]
    turno_id: str = field(default="", compare=False)


# ------------------------------------------------------------------ herramientas


@dataclass
class Turno:
    """Lo que comparten las herramientas de un turno: la base abierta, los supuestos y las evidencias."""

    consultas: Consultas
    supuestos: Supuestos
    hoy_real: dt.date
    evidencias: list[Evidencia] = field(default_factory=list)
    _cerrojo: threading.Lock = field(default_factory=threading.Lock)

    def correr(
        self,
        id_llamada: str,
        herramienta: str,
        entrada: BaseModel,
        calcular: Callable[[duckdb.DuckDBPyConnection], Resultado],
    ) -> str:
        """Corre la herramienta con un cursor nuevo, guarda la evidencia y devuelve el texto para el modelo."""
        cursor = self.consultas.sesion()
        try:
            resultado = calcular(cursor)
        finally:
            cursor.close()
        evidencia = Evidencia(id_llamada, herramienta, entrada.model_dump(mode="json", exclude_defaults=True), resultado)
        with self._cerrojo:
            self.evidencias.append(evidencia)
        return resultado.texto_modelo()


def ventas(ctx: RunContext[Turno], entrada: EntradaVentas) -> str:
    """Ventas por tienda, producto, categoría, canal, periodo o día de la semana: venta, unidades, devoluciones, venta por m², margen, ranking y comparación con otro periodo."""
    turno = ctx.deps
    return turno.correr(
        ctx.tool_call_id, "ventas", entrada,
        lambda cursor: modulo_ventas.ventas(cursor, entrada, turno.supuestos, hoy_real=turno.hoy_real),
    )


def inventario(ctx: RunContext[Turno], entrada: EntradaInventario) -> str:
    """Inventario al corte: existencia, productos bajo el punto de reorden, valuación a costo y a precio, y días de cobertura."""
    turno = ctx.deps
    return turno.correr(
        ctx.tool_call_id, "inventario", entrada,
        lambda cursor: modulo_inventario.inventario(cursor, entrada, turno.supuestos),
    )


def caducidades(ctx: RunContext[Turno], entrada: EntradaCaducidades) -> str:
    """Lotes que toca retirar del anaquel o rebajar por su caducidad, al corte del inventario, con la regla de la política."""
    turno = ctx.deps
    return turno.correr(
        ctx.tool_call_id, "caducidades", entrada,
        lambda cursor: modulo_caducidades.caducidades(cursor, entrada, hoy_real=turno.hoy_real),
    )


def tickets(ctx: RunContext[Turno], entrada: EntradaTickets) -> str:
    """Tickets de la mesa de servicio: conteo, antigüedad, cumplimiento del tiempo objetivo, los más frecuentes, búsqueda por texto y detalle de un folio."""
    turno = ctx.deps
    return turno.correr(
        ctx.tool_call_id, "tickets", entrada,
        lambda cursor: modulo_tickets.tickets(cursor, entrada, turno.supuestos, hoy_real=turno.hoy_real),
    )


def buscar_politicas(ctx: RunContext[Turno], entrada: EntradaPoliticas) -> str:
    # La descripción que lee el modelo es modulo_politicas.DESCRIPCION (T2.7).
    turno = ctx.deps
    return turno.correr(
        ctx.tool_call_id, "buscar_politicas", entrada,
        lambda cursor: modulo_politicas.buscar_politicas(cursor, entrada, turno.supuestos),
    )


HERRAMIENTAS = (
    Tool(ventas),
    Tool(inventario),
    Tool(caducidades),
    Tool(tickets),
    Tool(buscar_politicas, description=modulo_politicas.DESCRIPCION),
)
NOMBRES_HERRAMIENTAS = tuple(herramienta.name for herramienta in HERRAMIENTAS)


def _hash_herramientas() -> str:
    """sha256 de lo que el modelo lee de cada herramienta: nombre, descripción y esquema de la entrada."""
    definiciones_herramientas = [
        {
            "nombre": herramienta.tool_def.name,
            "descripcion": herramienta.tool_def.description,
            "esquema": herramienta.tool_def.parameters_json_schema,
        }
        for herramienta in HERRAMIENTAS
    ]
    texto = json.dumps(definiciones_herramientas, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


HERRAMIENTAS_SHA256 = _hash_herramientas()

# El modelo se elige en cada turno: el agente se arma sin él y sin llaves.
AGENTE = Agent(
    None,
    output_type=str,
    instructions=PROMPT_SISTEMA,
    deps_type=Turno,
    tools=HERRAMIENTAS,
)


# ------------------------------------------------------------------ traza


def _nombre_modelo(modelo: Model | str) -> str:
    if isinstance(modelo, str):
        return modelo
    return f"{modelo.system}:{modelo.model_name}"


def _milisegundos(desde: dt.datetime | None, hasta: dt.datetime | None) -> int | None:
    """Los milisegundos entre dos timestamps de Pydantic AI; None si falta uno o el orden no tiene sentido."""
    if desde is None or hasta is None:
        return None
    ms = round((hasta - desde).total_seconds() * 1000)
    return ms if ms >= 0 else None


def _llamadas(mensajes: Sequence[ModelMessage]) -> tuple[Llamada, ...]:
    llamadas = []
    solicitud = None
    for mensaje in mensajes:
        if isinstance(mensaje, ModelRequest):
            solicitud = mensaje.timestamp
            continue
        llamadas.append(
            Llamada(
                modelo=mensaje.model_name or "",
                entrada_tokens=mensaje.usage.input_tokens,
                salida_tokens=mensaje.usage.output_tokens,
                cache_lectura_tokens=mensaje.usage.cache_read_tokens,
                cache_escritura_tokens=mensaje.usage.cache_write_tokens,
                razonamiento_tokens=mensaje.usage.output_reasoning_tokens,
                proveedor=mensaje.provider_name,
                fin=mensaje.finish_reason,
                ms=_milisegundos(solicitud, mensaje.timestamp),
            )
        )
        solicitud = None
    return tuple(llamadas)


def _reintentos_validacion(mensajes: Sequence[ModelMessage]) -> int:
    """Llamadas a herramientas con argumentos que no pasaron la validación y volvieron al modelo."""
    total = 0
    for mensaje in mensajes:
        for parte in mensaje.parts:
            if not isinstance(parte, ToolReturnPart) or parte.outcome != "retried":
                continue
            if parte.retry is not None and parte.retry.cause == "validation_error":
                total += 1
    return total


def _en_orden_de_llamada(evidencias: Sequence[Evidencia], mensajes: Sequence[ModelMessage]) -> tuple[Evidencia, ...]:
    """Las evidencias en el orden en que el modelo pidió las herramientas, no en el que terminaron los hilos."""
    orden = {}
    for mensaje in mensajes:
        for parte in mensaje.parts:
            if isinstance(parte, ToolCallPart):
                orden.setdefault(parte.tool_call_id, len(orden))
    return tuple(sorted(evidencias, key=lambda evidencia: orden.get(evidencia.id_llamada, len(orden))))


def _traza(
    modelo: str,
    mensajes: Sequence[ModelMessage],
    estado_base: construir.EstadoBase | None,
    tope: MotivoTope | None = None,
    reintentos: Sequence[Reintento] = (),
    falla: FallaDelModelo | None = None,
    verificacion: modulo_verificacion.Verificacion | None = None,
    ajustes_modelo: Mapping[str, Any] | None = None,
    evidencias: Sequence[Evidencia] = (),
) -> Traza:
    llamadas = _llamadas(mensajes)
    return Traza(
        modelo=modelo,
        prompt_sha256=PROMPT_SHA256,
        herramientas=NOMBRES_HERRAMIENTAS,
        herramientas_sha256=HERRAMIENTAS_SHA256,
        llamadas=llamadas,
        entrada_tokens=sum(llamada.entrada_tokens for llamada in llamadas),
        salida_tokens=sum(llamada.salida_tokens for llamada in llamadas),
        reintentos_validacion=_reintentos_validacion(mensajes),
        hash_fuentes=estado_base.hash_fuentes.sha256 if estado_base else None,
        base_reconstruida=estado_base.reconstruida if estado_base else False,
        tope=tope,
        reintentos=tuple(reintentos),
        falla=falla,
        verificacion=verificacion,
        ajustes_modelo=dict(ajustes_modelo or {}),
        tickets_sospechosos=tickets_sospechosos(evidencias),
    )


# ------------------------------------------------------------------ texto de los tickets como dato (T3.7, K08)


def tickets_sospechosos(evidencias: Sequence[Evidencia]) -> tuple[str, ...]:
    """Los folios que la herramienta de tickets marcó como sospechosos en las evidencias, sin repetir y en orden."""
    folios: dict[str, None] = {}
    for evidencia in evidencias:
        if evidencia.herramienta == "tickets":
            folios.update(dict.fromkeys(evidencia.resultado.detalle.get("sospechosos", ())))
    return tuple(folios)


def _lista(folios: Sequence[str]) -> str:
    if len(folios) == 1:
        return folios[0]
    return f"{', '.join(folios[:-1])} y {folios[-1]}"


def aviso_de_sospechosos(folios: Sequence[str]) -> str:
    """La línea que marca los tickets sospechosos en la respuesta, con sus folios y sin su texto."""
    if len(folios) == 1:
        return (
            f"Aviso: el ticket {folios[0]} queda marcado como sospechoso porque su texto trae órdenes para el "
            "asistente; lo tomé como dato y no las seguí."
        )
    return (
        f"Aviso: los tickets {_lista(folios)} quedan marcados como sospechosos porque su texto trae órdenes para el "
        "asistente; los tomé como dato y no las seguí."
    )


def _con_aviso(texto: str, folios: Sequence[str]) -> str:
    """El texto con la línea fija de aviso de los folios sospechosos (K08: el registro y la respuesta; D3-A del plan de huecos).

    El modelo recibe la nota del resultado, pero puede no repetirla o negarla («el ticket 1402 no es sospechoso»); la
    respuesta determinista de D16-A la trae como "Nota:". La marca no depende del modelo: un renglón que nombra un folio
    marcado y habla de sospecha se cambia por este aviso, que va siempre al final y una sola vez.
    """
    if not folios:
        return texto
    patrones = [re.compile(rf"(?<!\d){re.escape(folio)}(?!\d)") for folio in folios]
    lineas = [
        linea for linea in texto.splitlines()
        if "sospech" not in linea.lower() or not any(patron.search(linea) for patron in patrones)
    ]
    return "\n".join([*lineas, aviso_de_sospechosos(folios)]).strip()


# El verificador no revisa una afirmación sin cifras, fechas ni citas («ya no hay tickets abiertos»): contra eso solo está
# el modelo. Si el modelo leyó texto de quien levantó un ticket, la gerente lo sabe y puede revisarlo (D4-A del plan de huecos).
NOTA_TEXTO_DE_TICKET = (
    "Nota: esta respuesta leyó texto escrito por quien levantó el ticket; puedes revisarlo en «Ver cómo se obtuvo la respuesta»."
)


def _leyo_texto_de_ticket(evidencias: Sequence[Evidencia]) -> bool:
    return any(
        modulo_verificacion._TEXTO_DE_TICKET & fila.keys()
        for evidencia in evidencias if evidencia.herramienta == "tickets"
        for fila in evidencia.resultado.filas
    )


def _con_nota_de_ticket(texto: str, evidencias: Sequence[Evidencia], desenlace: str) -> str:
    """El texto con la nota fija, si el modelo redactó con texto de un ticket; la respuesta determinista no lo copia."""
    if desenlace == "determinista" or not _leyo_texto_de_ticket(evidencias):
        return texto
    return f"{texto.rstrip()}\n{NOTA_TEXTO_DE_TICKET}"


def _con_texto_final(mensajes: Sequence[ModelMessage], texto: str) -> tuple[ModelMessage, ...]:
    """Los mensajes del turno con el texto mostrado en la última respuesta del modelo, para el historial."""
    if not mensajes or not isinstance(mensajes[-1], ModelResponse):
        return tuple(mensajes)
    ultima = mensajes[-1]
    partes = [parte for parte in ultima.parts if not isinstance(parte, TextPart)]
    return (*mensajes[:-1], replace(ultima, parts=[*partes, TextPart(texto)]))


def _visibles(evidencias: Sequence[Evidencia]) -> tuple[Evidencia, ...]:
    """Las evidencias como las ve el panel de traza: sin teléfonos ni correos en ningún texto (K18)."""
    return tuple(
        replace(evidencia, resultado=modulo_verificacion.sin_datos_de_contacto(evidencia.resultado))
        for evidencia in evidencias
    )


# ------------------------------------------------------------------ un turno bajo los topes


@dataclass(frozen=True)
class _Corrida:
    """Lo que dejó una corrida del agente: sus mensajes y su texto, el tope que la cortó o la falla del modelo."""

    mensajes: tuple[ModelMessage, ...]
    texto: str = ""
    tope: MotivoTope | None = None
    falla: FallaDelModelo | None = None


def _cortada_por_tokens(mensajes: Sequence[ModelMessage]) -> bool:
    """La última respuesta del modelo se detuvo en max_tokens: lo que dijo, o lo que razonó, quedó incompleto."""
    for mensaje in reversed(mensajes):
        if isinstance(mensaje, ModelResponse):
            return mensaje.finish_reason == "length"
    return False


def _de_esta_corrida(capturados: Sequence[ModelMessage], run_id: str) -> tuple[ModelMessage, ...]:
    """Los mensajes de esta corrida entre los que capturó Pydantic AI, que incluyen los del historial."""
    return tuple(mensaje for mensaje in capturados if mensaje.run_id == run_id)


def _correr(
    pregunta: str,
    historial: Sequence[ModelMessage],
    modelo: Model | str,
    turno: Turno,
    presupuesto: Presupuesto,
) -> _Corrida:
    """Corre el agente bajo el presupuesto del turno. Un tope no lanza: devuelve lo que alcanzó a hacer.

    Pydantic AI recibe los límites de uso, max_tokens en cada llamada, el uso compartido del Presupuesto y lo
    que queda de los segundos del turno como timeout de la corrida entera: cancela la llamada al modelo en
    curso y la espera de las herramientas. Los mensajes de un turno cortado salen de capture_run_messages,
    porque UsageLimitExceeded no los trae; se separan del historial por el run_id de la corrida.

    El modelo va envuelto en ModeloConReintentos (K20): un 429 se espera y se repite dentro del mismo
    Presupuesto. Un error del proveedor que no cedió (ModelAPIError: una llave inválida, un 429 que se acabó los
    reintentos) no lanza: sale como la falla clasificada de _Corrida, con lo que alcanzó a correr. Un id de
    modelo que no se puede armar (sin llave en el entorno, modelo inexistente) es la falla "configuracion".
    Una respuesta que el modelo dejó vacía (UnexpectedModelBehavior que no sea max_tokens) sigue su camino.

    A los topes se suman el esfuerzo de razonamiento y la caché del proveedor del modelo (Presupuesto.ajustes, T3.6):
    se mandan en cada solicitud, también en la de un reintento del mismo turno, y se anotan en
    Presupuesto.ajustes_enviados para la traza.
    """
    restante = presupuesto.segundos_restantes()
    if restante <= 0:
        # Pydantic AI 2.61.0 ya trata un timeout vencido como RunTimedOut sin llamar al modelo, pero un valor
        # negativo no es parte de su contrato; aquí no hay nada que arrancar.
        return _Corrida((), tope="segundos")
    try:
        con_reintentos = modulo_reintentos.ModeloConReintentos(
            modelo,
            reintentos=presupuesto.topes.reintentos,
            restante=presupuesto.segundos_restantes,
            registro=presupuesto.reintentos,
        )
    except UserError as error:
        return _Corrida((), falla=modulo_reintentos.falla_de_configuracion(error))
    ajustes = presupuesto.ajustes.para(con_reintentos)
    presupuesto.ajustes_enviados.update(ajustes)
    run_id = str(uuid.uuid4())
    with capture_run_messages() as capturados:
        try:
            resultado = AGENTE.run_sync(
                pregunta,
                message_history=list(historial) or None,
                model=con_reintentos,
                deps=turno,
                usage_limits=presupuesto.topes.limites(),
                usage=presupuesto.uso,
                timeout=restante,
                model_settings={**presupuesto.topes.ajustes(), **ajustes},
                run_id=run_id,
            )
        except UsageLimitExceeded as error:
            return _Corrida(_de_esta_corrida(capturados, run_id), tope=_motivo_del_limite(error))
        except RunTimedOut:
            return _Corrida(_de_esta_corrida(capturados, run_id), tope="segundos")
        except ModelAPIError as error:
            return _Corrida(_de_esta_corrida(capturados, run_id), falla=modulo_reintentos.clasificar(error))
        except UnexpectedModelBehavior:
            # Un razonamiento o una llamada a herramienta que se comió max_tokens no deja nada que mostrar.
            mensajes = _de_esta_corrida(capturados, run_id)
            if not _cortada_por_tokens(mensajes):
                raise
            return _Corrida(mensajes, tope="tokens")
    mensajes = tuple(resultado.new_messages())
    if _cortada_por_tokens(mensajes):
        return _Corrida(mensajes, tope="tokens")
    texto = modulo_historial.sin_consultas(resultado.output)
    if texto and texto != resultado.output:
        # El modelo copió la anotación del historial al final de su texto: no es parte de la respuesta.
        return _Corrida(_con_texto_final(mensajes, texto), texto=texto)
    return _Corrida(mensajes, texto=resultado.output)


def _historial_de_tope(motivo: MotivoTope, pregunta: str, texto: str) -> tuple[ModelMessage, ...]:
    """La pregunta y el mensaje del tope. Si el motivo es el largo, nada: un mensaje así no se reenvía en cada turno."""
    if motivo == "caracteres":
        return ()
    return (ModelRequest(parts=[UserPromptPart(pregunta)]), ModelResponse(parts=[TextPart(texto)]))


def _respuesta_de_tope(
    motivo: MotivoTope,
    nombre: str,
    pregunta: str,
    mensajes: Sequence[ModelMessage] = (),
    evidencias: Sequence[Evidencia] = (),
    estado_base: construir.EstadoBase | None = None,
    reintentos: Sequence[Reintento] = (),
    ajustes_modelo: Mapping[str, Any] | None = None,
) -> Respuesta:
    """La Respuesta de un turno que un tope cortó: un texto fijo sin cifras, con lo que alcanzó a correr.

    mensajes son los de la corrida cortada. Dan los tokens de las llamadas que sí se hicieron (K38) y el orden
    de las evidencias, pero no se devuelven como historial: un turno cortado deja llamadas a herramientas sin
    la respuesta del modelo.
    """
    texto = MENSAJES_DE_TOPE[motivo]
    ordenadas = _en_orden_de_llamada(evidencias, mensajes)
    return Respuesta(
        texto=texto,
        estado="tope",
        evidencias=_visibles(ordenadas),
        traza=_traza(
            nombre, mensajes, estado_base, tope=motivo, reintentos=reintentos, ajustes_modelo=ajustes_modelo,
            evidencias=ordenadas,
        ),
        mensajes=_historial_de_tope(motivo, pregunta, texto),
    )


def _respuesta_de_falla(
    falla: FallaDelModelo,
    nombre: str,
    pregunta: str,
    mensajes: Sequence[ModelMessage] = (),
    evidencias: Sequence[Evidencia] = (),
    estado_base: construir.EstadoBase | None = None,
    reintentos: Sequence[Reintento] = (),
    ajustes_modelo: Mapping[str, Any] | None = None,
) -> Respuesta:
    """La Respuesta de un turno en que el modelo no contestó: un texto fijo sin cifras, con lo que alcanzó a correr (K21).

    Como en un tope, mensajes dan los tokens de las llamadas que sí se hicieron y el orden de las evidencias, y el
    historial es solo la pregunta y el texto: una llamada a herramienta sin la respuesta del modelo no es un
    historial válido. La causa exacta va en la traza; la gerente lee un texto que no la repite.
    """
    texto = modulo_reintentos.MENSAJES_DE_FALLA[falla.motivo]
    ordenadas = _en_orden_de_llamada(evidencias, mensajes)
    return Respuesta(
        texto=texto,
        estado="modelo_no_disponible",
        evidencias=_visibles(ordenadas),
        traza=_traza(
            nombre, mensajes, estado_base, reintentos=reintentos, falla=falla, ajustes_modelo=ajustes_modelo,
            evidencias=ordenadas,
        ),
        mensajes=(ModelRequest(parts=[UserPromptPart(pregunta)]), ModelResponse(parts=[TextPart(texto)])),
    )


def _respuesta_sin_fuente(
    error: construir.ErrorConstruccion, nombre: str, reintentos: Sequence[Reintento] = ()
) -> Respuesta:
    """La Respuesta de un turno sin base que consultar: dice que no pudo consultar, sin cifra y sin llamar al modelo (K21).

    El texto sale del error con tipo de las herramientas (agente/herramientas/errores.py), que nombra solo el
    archivo, y no de str(error), que trae la ruta absoluta con el usuario de Windows.
    """
    resultado = comun.error("base", fuente_no_disponible_de(error))
    return Respuesta(
        resultado.resumen, "fuente_no_disponible", (), _traza(nombre, (), None, reintentos=reintentos), ()
    )


def _preparar_base(dir_datos: Path, base: Path, dir_config: Path) -> tuple[construir.EstadoBase, Supuestos, Consultas]:
    """Deja la base al día, carga los supuestos y la abre para consultar. Lanza ErrorConstruccion si no se puede.

    Los supuestos se cargan después de asegurar_base(): si config/supuestos.yaml falta o no es válido, es
    asegurar_base() quien lo dice, con ConfiguracionInvalida, y la respuesta es "no pude consultar" en lugar de
    un error de lectura de archivo.
    """
    estado_base = construir.asegurar_base(dir_datos, base, dir_config=dir_config)
    supuestos = definiciones.cargar(Path(dir_config) / "supuestos.yaml")
    return estado_base, supuestos, abrir_consultas(base)


# ------------------------------------------------------------------ verificación (T3.4, D16-A)


@dataclass(frozen=True)
class _Verificado:
    """El texto que se muestra, el historial del turno, todos los mensajes (para los tokens) y la Verificacion."""

    texto: str
    mensajes: tuple[ModelMessage, ...]
    todos: tuple[ModelMessage, ...]
    verificacion: modulo_verificacion.Verificacion


def _lector_de_secciones(turno: Turno) -> Callable[[], modulo_verificacion.Secciones]:
    """Lee el índice de secciones de la base del turno una sola vez, y solo si una cita lo pide."""

    @cache
    def leer() -> modulo_verificacion.Secciones:
        cursor = turno.consultas.sesion()
        try:
            return modulo_politicas.leer_indice(cursor).secciones
        finally:
            cursor.close()

    return leer


def _sin_ultima_respuesta(mensajes: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
    """Los mensajes sin la respuesta final del modelo, que se cambia por la que se muestra."""
    if mensajes and isinstance(mensajes[-1], ModelResponse):
        return tuple(mensajes[:-1])
    return tuple(mensajes)


def _verificar(
    pregunta: str,
    historial: Sequence[ModelMessage],
    modelo: Model | str,
    turno: Turno,
    presupuesto: Presupuesto,
    corrida: _Corrida,
) -> _Verificado:
    """Revisa el texto del modelo y aplica D16-A: un reintento con las discrepancias y, si siguen, la respuesta determinista.

    La evidencia es la pregunta y los resultados del turno, incluidos los que pida el reintento; el historial no
    cuenta (K16). El reintento corre con el mismo Presupuesto: si un tope o una falla lo corta, la respuesta es
    la determinista, que no necesita al modelo. El historial que deja el turno lleva el texto que se mostró, no
    el rechazado, para que el turno siguiente no lo copie.
    """
    leer_secciones = _lector_de_secciones(turno)

    def revisar(texto: str) -> modulo_verificacion.Revision:
        fuentes = modulo_verificacion.fuentes_de(
            pregunta, [evidencia.resultado for evidencia in turno.evidencias], [evidencia.entrada for evidencia in turno.evidencias]
        )
        return modulo_verificacion.verificar(texto, fuentes, leer_secciones)

    primera = revisar(corrida.texto)
    if primera.aprobada:
        verificacion = modulo_verificacion.Verificacion("aprobada", (primera,), (corrida.texto,))
        return _Verificado(corrida.texto, corrida.mensajes, corrida.mensajes, verificacion)

    revisiones = [primera]
    versiones = [modulo_verificacion.para_registro(corrida.texto, primera)]
    reintento = _correr(
        modulo_verificacion.pedido_de_correccion(primera), (*historial, *corrida.mensajes), modelo, turno, presupuesto
    )
    todos = (*corrida.mensajes, *reintento.mensajes)
    corte_del_reintento = reintento.tope or (reintento.falla.motivo if reintento.falla else None)
    if corte_del_reintento is None:
        segunda = revisar(reintento.texto)
        revisiones.append(segunda)
        versiones.append(modulo_verificacion.para_registro(reintento.texto, segunda))
        if segunda.aprobada:
            # La pregunta, las llamadas de las dos corridas y la versión que pasó; sin la rechazada ni el pedido.
            mensajes = (*_sin_ultima_respuesta(corrida.mensajes), *reintento.mensajes[1:])
            verificacion = modulo_verificacion.Verificacion("corregida", tuple(revisiones), tuple(versiones))
            return _Verificado(reintento.texto, mensajes, todos, verificacion)

    resultados = [evidencia.resultado for evidencia in _en_orden_de_llamada(turno.evidencias, todos)]
    texto = modulo_verificacion.respuesta_determinista(resultados)
    final = revisar(texto)
    if not final.aprobada:
        # El mensaje de un error puede repetir una cifra que el modelo escribió en un parámetro, y eso no es evidencia.
        texto = modulo_verificacion.respuesta_determinista([resultado for resultado in resultados if resultado.estado == "ok"])
        final = revisar(texto)
    if not final.aprobada:
        # No debería pasar: la respuesta determinista sale de la evidencia. Si pasa, no se muestra ninguna cifra.
        texto = modulo_verificacion.SIN_EVIDENCIA
        final = revisar(texto)
    revisiones.append(final)
    mensajes = (*_sin_ultima_respuesta(corrida.mensajes), ModelResponse(parts=[TextPart(texto)]))
    verificacion = modulo_verificacion.Verificacion(
        "determinista", tuple(revisiones), tuple(versiones), correccion=corte_del_reintento
    )
    return _Verificado(texto, mensajes, todos, verificacion)


# ------------------------------------------------------------------ responder


def responder(
    pregunta: str,
    historial: modulo_historial.Historial | Sequence[ModelMessage] = (),
    *,
    modelo: Model | str | None = None,
    base: Path = construir.BASE,
    dir_datos: Path = construir.DIR_DATOS,
    dir_config: Path = construir.DIR_CONFIG,
    hoy_real: dt.date | None = None,
    topes: Topes = TOPES,
    ajustes: AjustesDelModelo = AJUSTES,
    archivo_registro: Path | None = None,
    origen: modulo_registro.Origen = "interfaz",
    categoria: str | None = None,
) -> Respuesta:
    """Responde una pregunta con las herramientas y devuelve la Respuesta con sus evidencias y su traza.

    historial es el Historial de la sesión del navegador (agente/historial.py, T3.5): las últimas 6 vueltas como
    texto, con la pregunta, la respuesta final y los parámetros de cada herramienta (D10-A). Quien llama lo crea una
    vez por sesión y, al recibir la Respuesta, hace historial.agregar(pregunta, respuesta); responder() no lo
    modifica ni guarda nada entre llamadas, así que dos sesiones nunca comparten contexto (K22, P52). También
    acepta una secuencia de mensajes de Pydantic AI, que manda tal cual al modelo y sin tope: el tope de D10
    es del Historial.

    modelo es un Model de Pydantic AI o su id; sin él se usa MODELO de .env o MODELO_POR_OMISION. base,
    dir_datos y dir_config permiten correr sobre las copias A a D y sobre una copia de config/. hoy_real es la
    fecha del reloj, para el aviso de las fechas relativas; en las pruebas se fija. topes es el presupuesto del
    turno (K19): la interfaz y el comando de evaluación usan TOPES, y una prueba lo baja para no esperar 60 s.
    ajustes es el esfuerzo de razonamiento y la caché del proveedor (T3.6, D8-A): AJUSTES salvo que T6.1 mida la
    configuración base con AJUSTES_BASE o T6.2 pruebe una palanca por separado. Valen para todas las solicitudes del
    turno y la traza dice cuáles viajaron (traza.ajustes_modelo).

    Si un tope corta el turno, la Respuesta sale con estado "tope", un texto sin cifras y lo que alcanzó a
    correr. Un mensaje más largo que topes.caracteres no llama al modelo ni prepara la base.

    Si el modelo no contesta (la llave no sirve, el proveedor sigue rechazando tras los reintentos de K20),
    sale con estado "modelo_no_disponible" y un texto sin cifras; la causa está en traza.falla. Si la base o
    la configuración no se pueden usar, con estado "fuente_no_disponible", sin llamar al modelo. Ninguno de
    los dos casos lanza una excepción ni deja traza técnica en el texto (K21).

    Cada turno lleva un turno_id (UUID v4) en la Respuesta. Con archivo_registro (la interfaz y el comando de
    evaluación pasan modulo_registro.ARCHIVO, logs/turnos.jsonl), el turno deja ahí su línea (T4.3): origen es
    "interfaz" o "evaluacion" y categoria, la de la pregunta en el set. Sin él no se escribe nada, para que las
    pruebas no llenen logs/. Si la línea no se puede escribir, la Respuesta sale igual (K27).
    """
    inicio = time.monotonic()
    hora = modulo_registro.ahora()
    turno_id = str(uuid.uuid4())
    hoy = hoy_real or dt.date.today()
    # Lo que se manda al modelo antes de la pregunta: el registro lo guarda para reproducir el turno.
    previos = tuple(historial.mensajes() if isinstance(historial, modulo_historial.Historial) else historial)
    respuesta = replace(
        _responder_turno(
            pregunta, previos, modelo=modelo, base=base, dir_datos=dir_datos, dir_config=dir_config, hoy_real=hoy,
            topes=topes, ajustes=ajustes,
        ),
        turno_id=turno_id,
    )
    if archivo_registro is not None:
        modulo_registro.registrar_turno(
            archivo_registro,
            respuesta,
            pregunta=pregunta,
            historial=previos,
            id_sesion=historial.id_sesion if isinstance(historial, modulo_historial.Historial) else None,
            origen=origen,
            categoria=categoria,
            hora=hora,
            latencia_ms=round((time.monotonic() - inicio) * 1000),
            hoy_real=hoy,
            topes=topes,
            ajustes=ajustes,
        )
    return respuesta


def _responder_turno(
    pregunta: str,
    previos: Sequence[ModelMessage],
    *,
    modelo: Model | str | None,
    base: Path,
    dir_datos: Path,
    dir_config: Path,
    hoy_real: dt.date,
    topes: Topes,
    ajustes: AjustesDelModelo,
) -> Respuesta:
    """El turno de responder(), sin el registro: previos son los mensajes del historial que van antes de la pregunta."""
    presupuesto = Presupuesto(topes, ajustes=ajustes)
    elegido = modelo if modelo is not None else _modelo_configurado()
    nombre = _nombre_modelo(elegido)
    if len(pregunta) > topes.caracteres:
        return _respuesta_de_tope("caracteres", nombre, pregunta)
    # Un teléfono o un correo que la gerente escriba no sale al proveedor (K39): la misma regla del historial.
    pregunta = modulo_historial._sin_contacto(pregunta)
    try:
        estado_base, s, consultas = modulo_reintentos.con_reintento_de_base(
            lambda: _preparar_base(dir_datos, base, dir_config), presupuesto.reintentos, presupuesto.segundos_restantes
        )
    except construir.ErrorConstruccion as error:
        return _respuesta_sin_fuente(error, nombre, presupuesto.reintentos)

    turno = Turno(consultas, s, hoy_real)
    verificado = None
    try:
        corrida = _correr(pregunta, previos, elegido, turno, presupuesto)
        if not corrida.tope and not corrida.falla:
            verificado = _verificar(pregunta, previos, elegido, turno, presupuesto, corrida)
    finally:
        _cerrar(consultas)
    if corrida.tope:
        return _respuesta_de_tope(
            corrida.tope, nombre, pregunta, corrida.mensajes, turno.evidencias, estado_base, presupuesto.reintentos,
            presupuesto.ajustes_enviados,
        )
    if corrida.falla:
        return _respuesta_de_falla(
            corrida.falla, nombre, pregunta, corrida.mensajes, turno.evidencias, estado_base, presupuesto.reintentos,
            presupuesto.ajustes_enviados,
        )
    ordenadas = _en_orden_de_llamada(turno.evidencias, verificado.todos)
    texto = _con_aviso(verificado.texto, tickets_sospechosos(ordenadas))
    texto = _con_nota_de_ticket(texto, ordenadas, verificado.verificacion.desenlace)
    return Respuesta(
        texto=texto,
        estado="ok",
        evidencias=_visibles(ordenadas),
        traza=_traza(
            nombre, verificado.todos, estado_base, reintentos=presupuesto.reintentos,
            verificacion=verificado.verificacion, ajustes_modelo=presupuesto.ajustes_enviados, evidencias=ordenadas,
        ),
        mensajes=_con_texto_final(verificado.mensajes, texto) if texto != verificado.texto else verificado.mensajes,
    )
