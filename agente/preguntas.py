"""Separa las preguntas de un mensaje de la gerente (T4.6).

Un mensaje con varias preguntas se contesta pregunta por pregunta: cada una es un turno de responder(), con sus
topes (K19), sus evidencias y su línea en el registro, así que la fuente de una no se mezcla con la de otra. Este
módulo solo decide dónde termina cada pregunta; quien las contesta y las muestra es app.atender().

Las preguntas se cuentan por su signo de cierre, sin llamar al modelo:

- Un mensaje sin dos signos de cierre sale tal cual. «cuanto pan de caja tengo en cumbre y cuando se me caduca» trae
  dos cosas, pero sin signos no se sabe dónde termina la primera ni si la segunda se entiende sola, y una separación
  mal hecha le quita a una pregunta lo que necesita. Ese mensaje lo contesta el modelo en una sola respuesta.
- Un «?» cierra una pregunta si le sigue un espacio, puntuación o el final del mensaje. El de unas comillas
  («¿Procede?») o el de una dirección no cierra nada.
- Lo que viene después del último signo se queda con la última pregunta: «¿...? ¿...? gracias».
- Una pregunta de una sola palabra («¿no?», «¿verdad?») no es otra pregunta: se queda con la de junto.

separar() no recorta: decir que se contestaron las primeras MAXIMO_PREGUNTAS y que las demás no le toca a la interfaz.
"""

from __future__ import annotations

import re

# Cuántas preguntas de un mensaje se contestan. Cada una puede tardar los segundos de su turno y gastar sus tokens.
MAXIMO_PREGUNTAS = 3

_CIERRE = re.compile(r"\?+!*(?=[\s,;:.¿]|$)")
_ORILLAS = " \t\r\n,;:.-"
_PALABRA = re.compile(r"\w+")
_MINIMO_DE_PALABRAS = 2


def _es_corta(trozo: str) -> bool:
    return len(_PALABRA.findall(trozo)) < _MINIMO_DE_PALABRAS


def _trozos(mensaje: str) -> list[str]:
    """El mensaje cortado después de cada signo de cierre; lo que queda al final se queda con el último trozo."""
    trozos = []
    inicio = 0
    for cierre in _CIERRE.finditer(mensaje):
        trozos.append(mensaje[inicio : cierre.end()])
        inicio = cierre.end()
    if trozos:
        trozos[-1] += mensaje[inicio:]
    return trozos


def _sin_cortas(trozos: list[str]) -> list[str]:
    """Une cada trozo de una sola palabra con el de junto: con el anterior o, si es el primero, con el siguiente."""
    unidos: list[str] = []
    for trozo in trozos:
        if unidos and (_es_corta(trozo) or _es_corta(unidos[-1])):
            unidos[-1] = f"{unidos[-1]} {trozo}"
        else:
            unidos.append(trozo)
    return unidos


def separar(mensaje: str) -> tuple[str, ...]:
    """Las preguntas del mensaje, en su orden y sin la puntuación de sus orillas; si es una, el mensaje tal cual."""
    preguntas = _sin_cortas([trozo.strip(_ORILLAS) for trozo in _trozos(mensaje)])
    return tuple(preguntas) if len(preguntas) > 1 else (mensaje,)
