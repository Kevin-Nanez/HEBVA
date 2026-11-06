"""Qué respuestas de la línea base fueron las 20 primeras etiquetas de validación del juez: python -m eval.elegir_etiquetas (T5.13, D32-A).

Esas 20 quedaron gastadas el 2026-11-03 (eval/etiquetas_juez_gastadas.yaml). Las de hoy, en eval/etiquetas_juez.yaml, dicen en su
cabecera cómo se eligieron.

No llama a ningún modelo ni lee el reporte: solo reparte con una semilla los casos que adjudicó la revisión de la corrida del
2026-11-02, guardada en eval/reportes/2026-11-02-completo-39d2639-revision.md. La adjudicación es lectura manual de esa revisión,
y aquí queda solo su resultado. Lo que este módulo decide es la selección; la etiqueta y su porqué están en
eval/etiquetas_juez_gastadas.yaml, y tests/test_etiquetas_reales.py comprueba que las tres cosas coinciden.

La semilla 20261113 no se deriva de nada: es un número fijo que se escribió al crear este módulo, para no elegir los casos a mano,
y en esa sesión no se probó otra. El repositorio no puede demostrar eso último, y las listas de abajo se armaron con la revisión a
la vista. Lo que sí se comprueba es que esta semilla da esta selección.

Qué queda fuera, antes de sortear:
- las preguntas del conjunto ajuste, que no pueden estar en los dos;
- los 4 casos dudosos de la revisión (PT-29 y PT-47 de la corrida 2, DN-05.N-22 de la corrida 2 y PT-16 de la corrida 1) y los 2
  de confianza media (PT-35 de la corrida 1 y PT-07 de la corrida 2);
- las aprobaciones de los criterios que D33-A endureció (PT-04, PT-27, PT-30 y PT-47), porque la revisión las adjudicó con el
  criterio de antes y con el de hoy ya no se sostienen. El quinto endurecido, PT-29, se queda en la corrida 1: reprueba con los dos
  criterios, porque no nombra la diferencia con la FAQ, y sin él no llegan a 10 las reprobaciones sin repetir pregunta;
- el criterio propio de un compuesto, que no tiene etiqueta: eval/juez.py lo mide aparte.

Cómo se reparten. La revisión encontró 15 reprobaciones limpias en solo 9 preguntas y 2 aprobaciones de más, ambas de PT-12: las
«reprueba» salen de una respuesta por pregunta, y por eso casi todas son casos en que el juez viejo acertó. Las «aprueba» mezclan
sus reprobaciones falsas, por forma de la pregunta, con las que tienen que estar porque tests/test_juez.py pide cada aspecto de
D11 al menos dos veces. PT-18-2 de la corrida 2 y PT-08 salen de los 166 aprobados que la revisión leyó fuera de su muestra, y no
tienen fila propia en su tabla: la revisión solo lista los 3 de esos 166 que no comparte.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

SEMILLA = 20261113
APRUEBA = "aprueba"
REPRUEBA = "reprueba"
ACERTO = "acerto"
FALLO = "fallo"

Opciones = dict[str, tuple[int, ...]]

# De los criterios endurecidos, lo único que entra a la selección: corrida, pregunta y etiqueta.
ENDURECIDOS_CON_ETIQUETA = {(1, "PT-29", REPRUEBA)}

# El juez viejo aprobó de más: las dos son PT-12, por la letra del criterio.
REPRUEBA_Y_APROBO: Opciones = {"PT-12": (1, 2)}
# El juez viejo reprobó con razón. Las corridas de cada pregunta que la revisión adjudicó con confianza.
REPRUEBA_Y_REPROBO: Opciones = {
    "PT-05": (1, 2),
    "PT-07": (1,),
    "PT-29": (1,),
    "PT-38": (1, 2),
    "PT-44": (1, 2),
    "N-14": (1, 2),
    "N-18": (1,),
    "DN-07.N-16": (1, 2),
    "DT-03.PT-12": (1, 2),
}
# El juez viejo reprobó de más, por forma de la pregunta: conversación, compuesto y sencilla.
APRUEBA_Y_REPROBO_CONVERSACION: Opciones = {"CV-03-2": (2,), "CV-03-3": (1, 2), "CV-05-01": (1,), "CV-05-12": (1, 2)}
APRUEBA_Y_REPROBO_COMPUESTO: Opciones = {
    "DN-01.N-20": (1,),
    "TN-04.N-02": (1, 2),
    "DN-03.N-11": (2,),
    "DN-04.N-21": (2,),
    "DN-10.N-09": (2,),
    "DT-04.PT-24": (2,),
}
APRUEBA_Y_REPROBO_SENCILLA: Opciones = {
    "PT-20": (1, 2),
    "PT-25": (1,),
    "PT-51": (1, 2),
    "N-23": (1,),
    "N-25": (1, 2),
    "PT-21": (2,),
    "PT-35": (2,),
    "PT-40": (2,),
    "PT-43": (2,),
    "N-09": (2,),
    "N-22": (2,),
}
# El juez viejo aprobó con razón: la muestra de 40 de la revisión, sin lo que queda fuera.
APRUEBA_Y_APROBO: Opciones = {
    "DN-08.N-19": (2,),
    "N-15": (1,),
    "N-16": (1, 2),
    "N-17": (1,),
    "N-18": (2,),
    "N-24": (2,),
    "PT-46": (1,),
    "CV-01-2": (1,),
    "CV-03-1": (2,),
    "CV-04-2": (1,),
    "CV-05-10": (1,),
    "DN-03.N-11": (1,),
    "DN-05.N-22": (1,),
    "DN-08.N-23": (1,),
    "DN-09.N-10": (2,),
    "DN-10.N-20": (2,),
    "N-05": (2,),
    "N-13": (1, 2),
    "PT-02": (2,),
    "PT-13": (2,),
    "TN-01.N-10": (2,),
    "TN-02.N-13": (1,),
    "TN-02.PT-17": (2,),
}
# Lo que no se sortea: cubre PT-10, que tests/test_juez.py nombra, y los aspectos no_afirma_causa y deja_supuesto.
APRUEBA_FIJAS: Opciones = {"PT-10": (2,), "PT-18-2": (1, 2)}
APRUEBA_DEJA_SUPUESTO: Opciones = {"PT-08": (1, 2)}


@dataclass(frozen=True)
class Elegido:
    corrida: int
    pregunta: str
    etiqueta: str
    # Si el veredicto del juez de antes de T5.13 coincide con la etiqueta.
    juez_viejo: str


def raiz(pregunta: str) -> str:
    """El id de la sencilla: una parte de un compuesto lleva <compuesto>.<id>."""
    return pregunta.rsplit(".", maxsplit=1)[-1]


def _sortear(azar: random.Random, opciones: Opciones, cuantas: int, usadas: set[str]) -> list[tuple[str, int]]:
    """Cuantas preguntas sin repetir la sencilla, cada una en una de sus corridas."""
    disponibles = [pregunta for pregunta in opciones if raiz(pregunta) not in usadas]
    elegidas = azar.sample(disponibles, cuantas)
    usadas.update(raiz(pregunta) for pregunta in elegidas)
    return [(pregunta, azar.choice(opciones[pregunta])) for pregunta in elegidas]


def _armar(pares: Sequence[tuple[str, int]], etiqueta: str, juez_viejo: str) -> list[Elegido]:
    return [Elegido(corrida, pregunta, etiqueta, juez_viejo) for pregunta, corrida in pares]


def elegir(semilla: int = SEMILLA) -> list[Elegido]:
    """Las 20, en el orden de sus id. Se barajan para que el orden no diga la etiqueta."""
    azar = random.Random(semilla)
    reprueban = _armar(_sortear(azar, REPRUEBA_Y_APROBO, 1, set()), REPRUEBA, FALLO)
    reprueban += _armar(_sortear(azar, REPRUEBA_Y_REPROBO, len(REPRUEBA_Y_REPROBO), set()), REPRUEBA, ACERTO)

    fijas = [(pregunta, corrida) for pregunta, corridas in APRUEBA_FIJAS.items() for corrida in corridas]
    aprueban = _armar(fijas, APRUEBA, ACERTO)
    usadas = {raiz(pregunta) for pregunta in APRUEBA_FIJAS}
    aprueban += _armar(_sortear(azar, APRUEBA_DEJA_SUPUESTO, 1, usadas), APRUEBA, ACERTO)
    aprueban += _armar(_sortear(azar, APRUEBA_Y_REPROBO_CONVERSACION, 1, usadas), APRUEBA, FALLO)
    aprueban += _armar(_sortear(azar, APRUEBA_Y_REPROBO_COMPUESTO, 2, usadas), APRUEBA, FALLO)
    aprueban += _armar(_sortear(azar, APRUEBA_Y_REPROBO_SENCILLA, 2, usadas), APRUEBA, FALLO)
    aprueban += _armar(_sortear(azar, APRUEBA_Y_APROBO, 1, usadas), APRUEBA, ACERTO)

    elegidos = reprueban + aprueban
    azar.shuffle(elegidos)
    return elegidos


if __name__ == "__main__":
    for numero, elegido in enumerate(elegir(), start=11):
        print(f"J-{numero}  corrida {elegido.corrida}  {elegido.pregunta:<12}  {elegido.etiqueta:<8}  juez viejo: {elegido.juez_viejo}")
