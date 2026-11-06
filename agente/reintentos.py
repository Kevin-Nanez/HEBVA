"""Reintento propio ante un 429 y falla del modelo comunicada sin cifra (T3.3, K20, K21).

Quién reintenta. Pydantic AI no deja en failed_attempts los reintentos que hacen el SDK ni el transporte, y los
dos SDK se portan distinto sin configurar: el de Anthropic reintenta un 429 dos veces por su cuenta y el de
Google hace un solo intento (hallazgos 1 y 2). ModeloConReintentos envuelve el modelo del turno, manda
max_retries=0 en cada solicitud para que ninguno reintente por debajo y es la única capa que lo hace: cada
intento pasa por aquí y queda en el registro del turno como un Reintento.

Qué se reintenta. Un 429 y, como max_retries=0 le quita al SDK de Anthropic los reintentos de los 5xx que traía
por omisión, también un 503, un 529 (servicio saturado) o un 500 que el proveedor da por pasajero, y un tiempo
agotado de la solicitud: Pydantic AI le da 5 s a la conexión, y un corte así cuesta el turno aunque le queden unos
55 s (3 de unos 500 turnos en las corridas del 2026-11-03). Un error que esperar no arregla (una llave inválida, un
modelo que no existe, un saldo agotado, una desconexión) no se reintenta: se comunica. Una desconexión no se
reintenta porque sin red el turno no se puede atender y esperar solo alarga la respuesta.

Cuánto se espera (K20). Lo que pida el proveedor en Retry-After (segundos o fecha HTTP) o en retry-after-ms, que
Pydantic AI ya lee en ModelAPIError.retry_after, o lo que pida Google en el cuerpo de su 429 (RetryInfo), porque
ese 429 no trae la cabecera; sin eso, retroceso exponencial de 1, 2, 4 s con techo en
ESPERA_MAXIMA_SEGUNDOS. No lleva aleatoriedad: una sola persona usa el sistema, no hay manada de clientes que
desincronizar, y las pruebas exigen valores exactos. El máximo de reintentos lo fija Topes.reintentos (K19), y
una espera solo ocurre si cabe en los segundos que le quedan al turno con un margen para la solicitud que
sigue: esperar para que el turno se corte a mitad de la siguiente solicitud no sirve de nada.

Cómo se comunica (K21). clasificar() reduce el error del proveedor a un motivo, el estado HTTP y el nombre de
su clase: nunca su texto, que puede repetir parte de la solicitud. La gerente lee MENSAJES_DE_FALLA, que no
tiene cifras ni jerga y, cuando la causa es el acceso o la configuración, no dice cuál (ASVS V16.5.1); la causa
exacta queda en la traza para quien administra el sistema.

La base. con_reintento_de_base() repite una vez la preparación de la base cuando una fuente cambió mientras se
construía (FuentesCambiaron) o cuando otra conexión tiene abierta la base (BaseEnUso): los dos mensajes de
agente/herramientas/errores.py ya piden volver a intentar en un momento.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    ModelRateLimitError,
    ModelServerError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from agente import construir

# Retroceso exponencial cuando el proveedor no dice cuánto esperar: 1, 2, 4 s, y de ahí no pasa de 8 s.
ESPERA_BASE_SEGUNDOS = 1.0
ESPERA_MAXIMA_SEGUNDOS = 8.0
# Lo que debe sobrar del turno después de una espera para que la solicitud que sigue tenga con qué terminar.
# Una llamada al modelo tarda unos segundos (la latencia p50 de un turno completo es de 5 s).
MARGEN_SEGUNDOS = 5.0
# Cuánto se espera antes de repetir la preparación de la base: lo que tarda en terminar de copiarse un CSV.
PAUSA_DE_BASE_SEGUNDOS = 1.0
# Con este exponente 2**n ya rebasa el techo; evita calcular potencias enormes si alguien sube Topes.reintentos.
_EXPONENTE_MAXIMO = 10

# Las causas pasajeras del proveedor: las que se reintentan y, si se acaban los intentos, las que se comunican.
MotivoPasajero = Literal["limite", "servicio"]
MotivoReintento = Literal["limite", "servicio", "fuentes", "base_en_uso"]
# De dónde salió la espera: la cabecera del proveedor, el retroceso exponencial o una pausa fija.
OrigenEspera = Literal["retry-after", "retroceso", "fija"]
# Por qué el modelo no contestó. "limite" y "servicio" son las causas pasajeras a las que se les acabaron los intentos.
MotivoFalla = Literal["limite", "servicio", "llave", "configuracion", "modelo"]

_T = TypeVar("_T")
# La duración de protobuf con la que Google pide esperar: "21s" o "21.5s".
_RETRASO_DE_GOOGLE = re.compile(r"\d+(?:\.\d+)?s")


@dataclass(frozen=True)
class Reintento:
    """Un reintento que ya se hizo: por qué, cuántos segundos se esperaron antes de él y de dónde salió esa espera.

    estado_http es el del error que lo causó, o None si no vino de un proveedor (la preparación de la base).
    El registro del turno (T4.3) guarda la lista y su suma.
    """

    motivo: MotivoReintento
    espera_segundos: float
    origen: OrigenEspera
    estado_http: int | None = None


@dataclass(frozen=True)
class FallaDelModelo:
    """Por qué el modelo no contestó, para quien administra el sistema (K21).

    tipo es el nombre de la clase del error, nunca su texto. retry_after es lo que pidió el proveedor, en
    segundos, aunque no se haya esperado porque no cabía en el turno.
    """

    motivo: MotivoFalla
    tipo: str
    estado_http: int | None = None
    retry_after: float | None = None


# Lo que lee la gerente cuando el modelo no contesta: sin cifras (ni siquiera las de una herramienta que alcanzó a
# correr), sin jerga y sin decir qué tiene mal el acceso. Lo que cambia es qué hacer.
_SIN_ACCESO = (
    "No pude consultar el modelo: no tengo acceso con la configuración actual. "
    "No te doy ninguna cifra. Avisa a quien administra el sistema."
)
MENSAJES_DE_FALLA: dict[MotivoFalla, str] = {
    "limite": (
        "No pude consultar el modelo: está recibiendo demasiadas solicitudes y no alcanzó a atender esta. "
        "No te doy ninguna cifra. Espera un momento y vuelve a preguntar."
    ),
    "servicio": (
        "No pude consultar el modelo: el servicio no está disponible por ahora. "
        "No te doy ninguna cifra. Vuelve a intentarlo en un momento."
    ),
    "llave": _SIN_ACCESO,
    "configuracion": _SIN_ACCESO,
    "modelo": (
        "No pude consultar el modelo por un error inesperado. "
        "No te doy ninguna cifra. Vuelve a intentarlo; si se repite, avisa a quien administra el sistema."
    ),
}


# ------------------------------------------------------------------ esperas

async def dormir(segundos: float) -> None:
    """La espera de un reintento del modelo. Las pruebas la cambian por una que solo anota los segundos."""
    await asyncio.sleep(segundos)


def pausar(segundos: float) -> None:
    """La pausa antes de repetir la preparación de la base. Las pruebas la cambian como a dormir()."""
    time.sleep(segundos)


def cabe(espera: float, restante: float) -> bool:
    """¿Alcanza lo que le queda al turno para esperar `espera` y dejar el margen de la solicitud que sigue?"""
    return espera + MARGEN_SEGUNDOS <= restante


def motivo_de_reintento(error: ModelAPIError) -> MotivoPasajero | None:
    """Por qué se reintenta este error, o None si esperar no lo arregla.

    Un límite de peticiones (429), un servicio saturado o caído (503, 529, 500, 502, 504) y un tiempo agotado son
    pasajeros; todo lo demás (una llave inválida, una petición mal formada, un saldo agotado, una desconexión) se
    comunica.
    """
    if isinstance(error, ModelRateLimitError):
        return "limite"
    if isinstance(error, (ModelUnavailableError, ModelServerError, ModelTimeoutError)):
        return "servicio"
    return None


def _espera_del_cuerpo(error: ModelAPIError) -> float | None:
    """Los segundos que pide el RetryInfo de un 429 de Google, o None si el cuerpo no los trae."""
    cuerpo = error.body.get("error") if isinstance(error.body, dict) else None
    detalles = cuerpo.get("details") if isinstance(cuerpo, dict) else None
    if not isinstance(detalles, list):
        return None
    for detalle in detalles:
        retraso = detalle.get("retryDelay") if isinstance(detalle, dict) else None
        if isinstance(retraso, str) and _RETRASO_DE_GOOGLE.fullmatch(retraso):
            return float(retraso[:-1])
    return None


def espera_para(error: ModelAPIError, numero: int) -> tuple[float, OrigenEspera]:
    """Cuánto esperar antes del reintento `numero` (el primero es el 1) y de dónde sale ese tiempo (K20).

    Lo que pidió el proveedor manda, aunque sea más corto que el retroceso. Si no pidió nada, o si la cabecera
    no se entiende o es negativa (Pydantic AI la descarta), retroceso exponencial con techo.
    """
    if error.retry_after is not None:
        return error.retry_after, "retry-after"
    pedida = _espera_del_cuerpo(error)
    if pedida is not None:
        return pedida, "retry-after"
    exponente = min(max(numero - 1, 0), _EXPONENTE_MAXIMO)
    return min(ESPERA_MAXIMA_SEGUNDOS, ESPERA_BASE_SEGUNDOS * 2**exponente), "retroceso"


# ------------------------------------------------------------------ el modelo con reintentos

class ModeloConReintentos(WrapperModel):
    """El modelo del turno con el reintento de K20: ante un error pasajero espera y repite la misma solicitud.

    reintentos es el máximo por solicitud (Topes.reintentos). restante da los segundos que le quedan al turno,
    se consulta una vez por error y decide si la espera cabe. registro es la lista del Presupuesto del turno,
    donde cada reintento queda anotado antes de esperar: si el turno se corta durante la espera, el registro
    ya dice que se esperaba.

    Solo envuelve request(), que es lo que usa el turno. request_stream() queda tal cual, con los reintentos
    por omisión de cada SDK, así que un turno que transmitiera la respuesta no tendría este registro.
    """

    def __init__(
        self,
        wrapped: Model | str,
        *,
        reintentos: int,
        restante: Callable[[], float],
        registro: list[Reintento],
    ):
        super().__init__(wrapped)
        self.reintentos = reintentos
        self.restante = restante
        self.registro = registro

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        # Con max_retries=0 el SDK hace un solo intento por solicitud: ningún reintento queda fuera del registro.
        ajustes: ModelSettings = {**(model_settings or {}), "max_retries": 0}
        hechos = 0
        while True:
            try:
                return await self.wrapped.request(messages, ajustes, model_request_parameters)
            except ModelAPIError as error:
                reintento = self._planear(error, hechos + 1)
                if reintento is None:
                    raise
            # Fuera del except: la espera no debe correr con el error anterior como contexto.
            self.registro.append(reintento)
            await dormir(reintento.espera_segundos)
            hechos += 1

    def _planear(self, error: ModelAPIError, numero: int) -> Reintento | None:
        """El reintento `numero` que toca hacer ante este error, o None si hay que dejarlo pasar."""
        motivo = motivo_de_reintento(error)
        if motivo is None:
            return None
        if numero > self.reintentos:
            return None
        espera, origen = espera_para(error, numero)
        if not cabe(espera, self.restante()):
            return None
        return Reintento(motivo, espera, origen, _estado_http(error))


# ------------------------------------------------------------------ clasificar la falla (K21)

# Lo que dice Google cuando rechaza una llave: es un 400 y no un 401, y solo el cuerpo lo distingue de otro 400.
_MARCAS_DE_LLAVE_RECHAZADA = ("api_key_invalid", "api key not valid", "api key expired")


def _estado_http(error: ModelAPIError) -> int | None:
    """El estado HTTP del error, o None si no vino con uno (una desconexión, un error dentro de una transmisión)."""
    return error.status_code if isinstance(error, ModelHTTPError) else None


def _rechaza_la_llave(error: ModelAPIError) -> bool:
    """El proveedor rechazó la llave o sus permisos: un 401 o 403, o el 400 con que Google rechaza una llave."""
    estado = _estado_http(error)
    if estado in (401, 403):
        return True
    if estado != 400:
        return False
    cuerpo = json.dumps(error.body, default=str).lower()
    return any(marca in cuerpo for marca in _MARCAS_DE_LLAVE_RECHAZADA)


def _motivo_de_falla(error: ModelAPIError) -> MotivoFalla:
    pasajero = motivo_de_reintento(error)
    if pasajero:
        return pasajero
    if _rechaza_la_llave(error):
        return "llave"
    return "modelo"


def clasificar(error: ModelAPIError) -> FallaDelModelo:
    """El motivo, el estado HTTP y el tipo de un error del proveedor, sin su texto (que puede repetir la solicitud)."""
    return FallaDelModelo(
        motivo=_motivo_de_falla(error),
        tipo=type(error).__name__,
        estado_http=_estado_http(error),
        retry_after=error.retry_after,
    )


def falla_de_configuracion(error: Exception) -> FallaDelModelo:
    """La falla de un modelo que no se pudo armar: falta la llave en el entorno o el id del modelo no existe."""
    return FallaDelModelo(motivo="configuracion", tipo=type(error).__name__)


# ------------------------------------------------------------------ la base

def con_reintento_de_base(
    preparar: Callable[[], _T], registro: list[Reintento], restante: Callable[[], float]
) -> _T:
    """Corre preparar() y, si una fuente cambió mientras se construía la base o la base está en uso, lo repite una vez.

    Los dos errores piden volver a intentar en un momento (agente/herramientas/errores.py): un CSV que terminó
    de copiarse o una conexión que se cerró. Un segundo fallo, o cualquier otro error, sale tal cual para que
    quien responde lo convierta en "no pude consultar", sin cifra. Si al turno no le queda tiempo para la pausa
    y el margen, tampoco se repite.
    """
    try:
        return preparar()
    except (construir.FuentesCambiaron, construir.BaseEnUso) as error:
        if not cabe(PAUSA_DE_BASE_SEGUNDOS, restante()):
            raise
        motivo: MotivoReintento = "fuentes" if isinstance(error, construir.FuentesCambiaron) else "base_en_uso"
    registro.append(Reintento(motivo, PAUSA_DE_BASE_SEGUNDOS, "fija"))
    pausar(PAUSA_DE_BASE_SEGUNDOS)
    return preparar()
