"""Historial de una conversación: las últimas vueltas como texto, con tope y propio de cada sesión (T3.5, D10-A, K22).

Una vuelta es una pregunta de la gerente y la respuesta que se le mostró. El historial guarda las últimas 6 (D10-A)
y de cada una lleva solo texto:

- la pregunta;
- la respuesta final, la que pasó el verificador (T3.4);
- los parámetros de cada herramienta que corrió, en una línea al final de la respuesta ("Consulté: ventas {...}"),
  para que un seguimiento ("y en julio?", "y de esos, cuáles son de Cumbres?") repita la consulta cambiando solo lo
  que pide la pregunta nueva.

No lleva razonamientos, llamadas a herramientas ni resultados. Un razonamiento reenviado después de editar turnos
anteriores da error 400 en Haiku 5.5 (D10); un resultado pesa 300 tokens o más en cada solicitud; y en los resultados
llega el texto de los tickets, donde puede venir una instrucción escondida (K31). Sin esos bloques el mismo historial
sirve a Claude y a Gemini. Tampoco sirve de evidencia: el verificador solo cuenta las salidas de herramientas del
turno (K16), y el prompt manda volver a llamar a la herramienta si falta una cifra.

El tope es de dos clases. VUELTAS limita cuántas se guardan. TOKENS_POR_VUELTA limita cuánto pesa cada una, para que
el historial crezca 500 tokens o menos por turno previo (meta de C6.1): una cifra simple usa 2 llamadas y cada una
reenvía todo el historial. La pregunta (TOKENS_PREGUNTA) y los parámetros (TOKENS_CONSULTAS) tienen un tope propio, y
la respuesta recibe lo que quede, que son 90 tokens o más. Lo que pasa de su presupuesto se recorta del final y lo
marca MARCA_RECORTE; una respuesta normal (cifra, definición, filtros y corte) cabe entera.

Antes de guardar, un teléfono o un correo se enmascara (K18; hoja de OWASP sobre agentes, sección 3: revisar lo que se
guarda en la memoria). Cada sesión del navegador tiene su Historial: es un objeto que crea y guarda quien atiende la
sesión (en la interfaz, st.session_state; no st.cache_resource ni una variable de módulo, que se comparten entre
sesiones), y este módulo no conserva nada entre llamadas ni entre sesiones, porque dos sesiones abiertas al mismo
tiempo no deben compartir contexto (P52). Un Historial vive en memoria mientras dure la sesión y se usa de a un turno
por vez.

Uso, igual en la interfaz (T4.1) y en el comando de evaluación (T5.3):

    historial = Historial()                      # una vez por sesión
    respuesta = responder(pregunta, historial)   # arma los mensajes con el tope de D10
    historial.agregar(pregunta, respuesta)       # guarda la vuelta para el turno siguiente

El prompt de sistema no dice qué hacer con los filtros de un seguimiento: conservarlos, o soltarlos cuando la pregunta
cambia de alcance, lo decide el modelo con lo que ve aquí (P29 a P32, P51).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from agente import privacidad
from agente.herramientas.comun import dumps, estimar_tokens

if TYPE_CHECKING:
    from agente.agente import Respuesta

# D10-A: las últimas 6 vueltas.
VUELTAS = 6

# Presupuesto de una vuelta, en tokens de estimar_tokens, que cuenta por arriba. 250 por vuelta son 500 por turno con
# 2 llamadas: la meta de C6.1 (criterio propio: la meta del plan dividida entre las llamadas de una cifra simple).
# La pregunta y los parámetros llevan un tope propio; la respuesta recibe lo que quede, que son 90 o más. La pregunta
# más larga de la batería (P60, 123 caracteres) pesa 45, y una respuesta normal con sus parámetros cabe entera.
TOKENS_POR_VUELTA = 250
TOKENS_PREGUNTA = 60
TOKENS_CONSULTAS = 100

ETIQUETA_CONSULTAS = "Consulté:"
MARCA_RECORTE = "[…]"

_DATOS_DE_CONTACTO = frozenset({"TELEFONO", "CORREO"})
_PALABRA = re.compile(r"\S+")


# ------------------------------------------------------------------ texto de una vuelta


def sin_consultas(texto: str) -> str:
    """El texto sin las líneas «Consulté: ...» que el modelo copia de las vueltas anteriores.

    La anotación es del historial, no de la respuesta: la gerente no debe verla, y sus parámetros (un "limite":50)
    no son evidencia de ninguna cifra para el verificador.
    """
    if ETIQUETA_CONSULTAS not in texto:
        return texto
    lineas = [linea for linea in texto.splitlines() if not linea.lstrip(" -*_>").startswith(ETIQUETA_CONSULTAS)]
    return "\n".join(lineas).rstrip()


def _recortar(texto: str, maximo: int) -> str:
    """El texto, o su principio con MARCA_RECORTE al final si pesa más de `maximo` tokens estimados.

    El corte cae entre palabras y conserva los saltos de línea del principio, de modo que la primera línea de una
    respuesta (la cifra) queda entera mientras quepa. estimar_tokens suma lo mismo por separado que junto, así que la
    cuenta palabra por palabra no se desvía de la del texto completo.
    """
    if estimar_tokens(texto) <= maximo:
        return texto
    reserva = estimar_tokens(MARCA_RECORTE)
    gastado, fin = reserva, 0
    for palabra in _PALABRA.finditer(texto):
        gastado += estimar_tokens(palabra.group())
        if gastado > maximo:
            break
        fin = palabra.end()
    if fin == 0:
        # Ni la primera palabra cabe (un bloque sin espacios): se corta por caracteres, y cada uno pesa a lo más 1.
        fin = max(maximo - reserva, 0)
    return f"{texto[:fin].rstrip()} {MARCA_RECORTE}".strip()


def _sin_contacto(texto: str) -> str:
    """El texto con los teléfonos y correos enmascarados (<TELEFONO_1>, <CORREO_1>); sin ellos, el mismo texto."""
    if _DATOS_DE_CONTACTO.intersection(privacidad.buscar_datos_personales(texto)):
        return privacidad.enmascarar(texto) or ""
    return texto


def consulta_en_texto(herramienta: str, entrada: Mapping[str, Any]) -> str:
    """Una herramienta y su entrada validada en una línea: ventas {"periodo":"julio","tiendas":["cumbres"]}."""
    return f"{herramienta} {dumps(entrada, compacto=True)}"


def _anotacion(consultas: Sequence[str], maximo: int) -> str:
    """"Consulté: a; b" dentro de `maximo` tokens: las primeras consultas enteras y cuántas faltan; vacío si no hubo."""
    if not consultas:
        return ""
    for cuantas in range(len(consultas), 0, -1):
        faltan = len(consultas) - cuantas
        texto = f"{ETIQUETA_CONSULTAS} {'; '.join(consultas[:cuantas])}" + (f" (y {faltan} más)" if faltan else "")
        if estimar_tokens(texto) <= maximo:
            return texto
    # Ni una sola cabe entera: mejor su principio que perder los filtros de la vuelta.
    return _recortar(f"{ETIQUETA_CONSULTAS} {consultas[0]}", maximo)


@dataclass(frozen=True)
class Vuelta:
    """Una pregunta, la respuesta que se mostró y la línea con los parámetros de las herramientas que corrieron.

    Ya vienen recortadas al presupuesto (armar_vuelta): lo que guarda el historial es lo que se manda al modelo.
    momento fija la hora de los mensajes para que mensajes() dé siempre lo mismo.
    """

    pregunta: str
    respuesta: str
    consultas: str = ""
    momento: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    def mensajes(self) -> tuple[ModelRequest, ModelResponse]:
        """La pregunta como mensaje del usuario y la respuesta, con sus consultas al final, como mensaje del modelo."""
        texto = f"{self.respuesta}\n\n{self.consultas}" if self.consultas else self.respuesta
        return (
            ModelRequest(parts=[UserPromptPart(self.pregunta, timestamp=self.momento)]),
            ModelResponse(parts=[TextPart(texto)], timestamp=self.momento),
        )


def armar_vuelta(pregunta: str, texto: str, consultas: Iterable[tuple[str, Mapping[str, Any]]] = ()) -> Vuelta:
    """La Vuelta de una pregunta, su respuesta y las herramientas que corrieron (nombre y entrada), en su presupuesto.

    Enmascara teléfonos y correos, anota una sola vez las consultas repetidas, recorta la pregunta y las consultas a
    su tope y deja a la respuesta lo que sobre de TOKENS_POR_VUELTA.
    """
    pregunta = _recortar(_sin_contacto(pregunta.strip()), TOKENS_PREGUNTA)
    llamadas = dict.fromkeys(
        _sin_contacto(consulta_en_texto(herramienta, entrada)) for herramienta, entrada in consultas
    )
    anotacion = _anotacion(list(llamadas), TOKENS_CONSULTAS)
    cabe = TOKENS_POR_VUELTA - estimar_tokens(pregunta) - estimar_tokens(anotacion)
    return Vuelta(pregunta, _recortar(_sin_contacto(texto.strip()), cabe), anotacion)


# ------------------------------------------------------------------ la sesión


TEXTO_CON_TICKET_MARCADO = "No se guardó el texto de esta respuesta: el turno trajo un ticket marcado como sospechoso."
# La línea fija que agente.py agrega siempre a un turno con un ticket marcado (aviso_de_sospechosos).
_AVISO_DE_SOSPECHOSOS = re.compile(r"^Aviso: .*marcados? como sospechosos?\b", re.MULTILINE)


class Historial:
    """Las últimas vueltas de una sesión del navegador, listas para mandarlas al modelo como texto.

    id_sesion es el UUID v4 de la sesión, para el registro (T4.3). capacidad son las vueltas que guarda (D10-A: 6); al
    llegar a ella, cada vuelta nueva saca a la más vieja.
    """

    def __init__(self, capacidad: int = VUELTAS) -> None:
        if capacidad < 1:
            raise ValueError("un historial guarda al menos una vuelta")
        self.capacidad = capacidad
        self.id_sesion = str(uuid.uuid4())
        self._vueltas: deque[Vuelta] = deque(maxlen=capacidad)

    def __len__(self) -> int:
        return len(self._vueltas)

    def __bool__(self) -> bool:
        # Un historial vacío sigue siendo la sesión: con `historial or Historial()` no debe nacer otra, con otro id.
        return True

    def __repr__(self) -> str:
        # Sin el texto de la conversación: un repr acaba en un log o en una pantalla de error.
        return f"Historial(id_sesion={self.id_sesion!r}, vueltas={len(self)}/{self.capacidad})"

    @property
    def vueltas(self) -> tuple[Vuelta, ...]:
        return tuple(self._vueltas)

    def agregar(self, pregunta: str, respuesta: Respuesta) -> None:
        """Guarda la vuelta que acaba de terminar: la pregunta, el texto mostrado y las herramientas que corrieron.

        Un turno que no dejó mensajes no entra: un mensaje más largo que el tope de K19 y una base que no se pudo
        consultar no los dejan, para no reenviar ese texto en cada turno. Un turno que cortó un tope o una falla del
        modelo sí entra, con su mensaje fijo y lo que alcanzó a correr, porque es lo que la gerente ve y el
        seguimiento ("mejor solo junio") lo necesita. Un turno con un ticket marcado como sospechoso entra sin el texto
        de la respuesta, por si el modelo obedeció: el turno siguiente no tiene que partir de eso (D4-A del plan de huecos).
        """
        if not respuesta.mensajes:
            return
        pregunta, texto = pregunta.strip(), respuesta.texto.strip()
        if not all((pregunta, texto)):
            return
        if _AVISO_DE_SOSPECHOSOS.search(texto):
            texto = TEXTO_CON_TICKET_MARCADO
        consultas = [(evidencia.herramienta, evidencia.entrada) for evidencia in respuesta.evidencias]
        self._vueltas.append(armar_vuelta(pregunta, texto, consultas))

    def mensajes(self) -> tuple[ModelMessage, ...]:
        """El historial como mensajes de Pydantic AI: usuario y modelo por cada vuelta, solo texto, objetos nuevos."""
        return tuple(mensaje for vuelta in self._vueltas for mensaje in vuelta.mensajes())
