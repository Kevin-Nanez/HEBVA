"""Esfuerzo de razonamiento y caché del prefijo, por proveedor (T3.6, D8-A, palancas 1 y 4 de eficiencia).

Qué pide cada proveedor, con la documentación que lo respalda (docs/referencias/):

- Claude (Haiku 5.5): razonamiento adaptativo con esfuerzo "medium" (D8-D, decisión del usuario del 2026-11-05). Es el
  valor por omisión de Haiku 5.5 y esos tokens se cobran como salida. Hasta ese día fue "low" (D8-A), que la guía de
  Anthropic recomienda para chat y tareas cortas con herramientas (effort.md); en "low" el modelo se salta búsquedas:
  en la pregunta del 60 % a un pastel a 1 día de caducar, 3 pasadas de 3 en "medium" leyeron el lote y la política, y
  de 3 en "low" una no leyó el lote y otra dijo que la política no contempla el 60 %. En esas pasadas "medium" costó
  cerca de 3 veces lo que "low" y tardó el doble. Los reportes de eval/reportes y plantilla_decisiones.md se midieron
  en "low". No hay presupuesto numérico: budget_tokens da 400 en Haiku 5.5, y el tope duro es max_tokens (T3.2), que cuenta el
  razonamiento. El razonamiento adaptativo ya es el comportamiento por omisión del modelo; se manda explícito para
  que la decisión quede escrita en la solicitud y en la traza.
- Claude, caché: cache_control en el último bloque del prompt de sistema y en la última herramienta. Anthropic arma el
  prefijo en el orden herramientas, sistema y mensajes, y una marca cachea todo lo anterior a ella, así que con las
  dos marcas un cambio en el prompt deja leer las herramientas, y un cambio en las herramientas invalida todo. No hay
  marca en los mensajes: el historial (D10-A) cambia en cada turno. La vida es de 5 minutos, la de omisión y la que
  usa la tabla de precios del plan (escritura a 1.25 veces, lectura a 0.1 veces); cada lectura la renueva. Con dos
  llamadas por turno, hasta un turno en frío cuesta 1.35 veces el prefijo contra 2 sin caché; solo un turno de una
  llamada pagaría más (1.25 contra 1). La vida de 1 hora escribe a 2 veces y convendría si las preguntas de una
  gerente llegan con más de 5 minutos entre una y otra; el plan no la mide y se decide con el uso real. El mínimo
  cacheable de Haiku 5.5 es de 512 tokens y el prefijo pesa varias veces eso.
- Gemini: thinking_level MEDIUM (D8-A; Google recomienda "medium" o "high" para agentes con herramientas, y 3.5
  Flash-Lite razona en "minimal" por omisión). No se pide el resumen del razonamiento (include_thoughts): las firmas
  de pensamiento que Gemini 3 exige devolver en las llamadas a herramientas vienen dentro de las partes de la
  respuesta, no en el resumen (thinking.md, "Thought signatures"), y el resumen solo agregaría texto a la traza y al
  registro. La caché de Gemini es implícita y, según el plan, solo existe en el plan de pago: no hay nada que
  configurar, y lo único que la favorece es lo mismo que a la de Claude, un prefijo estable.

Qué mantiene válida la caché (prompt-caching.md, "What invalidates the cache"): las herramientas y el prompt de
sistema salen de constantes (agente/agente.py), sin fechas ni ids; y el razonamiento y el esfuerzo no cambian durante
una sesión, porque cambiarlos invalida los mensajes y puede invalidar también el sistema y las herramientas. Por eso
se eligen una vez por proceso (la interfaz usa siempre AJUSTES) y no por turno.

Cómo se mandan: por solicitud, junto con max_tokens (Topes.ajustes) y no en el modelo, para que valgan igual para
un id de modelo, para un Model ya armado y para el modelo envuelto en los reintentos de K20. Se usan las claves de
cada proveedor y no el ajuste unificado `thinking` de Pydantic AI, que en Gemini agrega include_thoughts. Un modelo
que no es de Claude ni de Gemini (los simulados de las pruebas) no recibe ninguna.

T6.1 mide la configuración base con AJUSTES_BASE (valores por omisión del proveedor, sin caché) y T6.2 prende
cada palanca por separado con AjustesDelModelo(...). responder() recibe el que toque y la traza dice con cuál corrió.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, get_args

from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

# Los SDK de Anthropic y de Google tardan unos 3 s en importarse. Este módulo no los importa: si el módulo del
# proveedor no está cargado, nadie pudo armar un modelo de ese proveedor, y para() lo sabe sin importarlo.
if TYPE_CHECKING:
    from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
    from pydantic_ai.models.google import GoogleModel, GoogleModelSettings

MODULO_CLAUDE = "pydantic_ai.models.anthropic"
MODULO_GEMINI = "pydantic_ai.models.google"

# Los niveles que documenta cada proveedor para su parámetro de esfuerzo (effort.md; thinking.md de Gemini).
EsfuerzoClaude = Literal["low", "medium", "high", "xhigh", "max"]
EsfuerzoGemini = Literal["minimal", "low", "medium", "high"]


def _validar(campo: str, valor: str | None, validos: tuple[str, ...]) -> None:
    if valor is not None and valor not in validos:
        raise ValueError(f"{campo} {valor!r} no es uno de {', '.join(validos)}")


@dataclass(frozen=True)
class AjustesDelModelo:
    """El esfuerzo de razonamiento de cada proveedor y la caché del prefijo de Claude.

    esfuerzo_claude es el de Haiku 5.5 y de los Claude que tengan el parámetro: "medium" por D8-D. None deja el del
    proveedor (también "medium" en Haiku 5.5) y no manda ni el razonamiento adaptativo. esfuerzo_gemini es el thinking_level
    de Gemini 3: "medium" por D8-A. None deja el del modelo ("minimal" en 3.5 Flash-Lite). cache_claude pone
    cache_control en el prompt de sistema y en las herramientas.

    Un proveedor ignora lo que su modelo no admite: un Claude sin parámetro effort (Haiku 4.5) o sin razonamiento
    adaptativo recibe solo lo que sí tiene, y un Gemini que razona con presupuesto (2.5) no recibe thinking_level.
    """

    esfuerzo_claude: EsfuerzoClaude | None = "medium"
    esfuerzo_gemini: EsfuerzoGemini | None = "medium"
    cache_claude: bool = True

    def __post_init__(self) -> None:
        _validar("esfuerzo_claude", self.esfuerzo_claude, get_args(EsfuerzoClaude))
        _validar("esfuerzo_gemini", self.esfuerzo_gemini, get_args(EsfuerzoGemini))

    def para(self, modelo: Model) -> ModelSettings:
        """Los ajustes de este modelo, con las claves de su proveedor; vacío si no es de Claude ni de Gemini."""
        interno = _sin_envoltorios(modelo)
        if _es_de(interno, MODULO_CLAUDE, "AnthropicModel"):
            return self._de_claude(interno)
        if _es_de(interno, MODULO_GEMINI, "GoogleModel"):
            return self._de_gemini(interno)
        return {}

    def _de_claude(self, modelo: AnthropicModel) -> AnthropicModelSettings:
        perfil = modelo.profile
        ajustes: AnthropicModelSettings = {}
        if self.esfuerzo_claude is not None:
            if perfil.get("anthropic_supports_adaptive_thinking", False):
                ajustes["anthropic_thinking"] = {"type": "adaptive"}
            if perfil.get("anthropic_supports_effort", False):
                ajustes["anthropic_effort"] = self.esfuerzo_claude
        if self.cache_claude:
            ajustes["anthropic_cache_instructions"] = True
            ajustes["anthropic_cache_tool_definitions"] = True
        return ajustes

    def _de_gemini(self, modelo: GoogleModel) -> GoogleModelSettings:
        if self.esfuerzo_gemini is None or not modelo.profile.get("google_supports_thinking_level", True):
            return {}
        from google.genai.types import ThinkingLevel  # ya cargado: hay un GoogleModel armado

        return {"google_thinking_config": {"thinking_level": ThinkingLevel(self.esfuerzo_gemini.upper())}}


def _es_de(modelo: Model, modulo: str, clase: str) -> bool:
    """Si modelo es de esa clase del módulo de un proveedor, sin importar el módulo cuando nadie lo cargó."""
    cargado = sys.modules.get(modulo)
    if cargado is None:
        return False
    return isinstance(modelo, getattr(cargado, clase))


def _sin_envoltorios(modelo: Model) -> Model:
    """El modelo de verdad: ModeloConReintentos y los demás envoltorios dejan pasar los ajustes de su proveedor."""
    while isinstance(modelo, WrapperModel):
        modelo = modelo.wrapped
    return modelo


# D8-D (2026-11-05): Haiku en "medium" con razonamiento adaptativo, Gemini en "medium" y la caché del prefijo de Claude.
AJUSTES = AjustesDelModelo()

# La configuración base de T6.1: los valores por omisión de cada proveedor y sin caché.
AJUSTES_BASE = AjustesDelModelo(esfuerzo_claude=None, esfuerzo_gemini=None, cache_claude=False)
