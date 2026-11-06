"""Lee eval/preguntas.yaml y lo deja listo para correr (T5.3, D25).

Todo prompt del banco se reduce a la misma forma, una lista de pasos. Un paso es un turno de responder() y trae las
preguntas que su respuesta tiene que contestar:

    sencilla o variante   1 paso con 1 pregunta
    con_signos            1 paso por cada trozo de preguntas.separar(), con 1 pregunta y la misma sesión
    sin_signos            1 paso con las 2 partes: un solo turno cuya respuesta trae lo de las dos
    conversación          1 paso por turno, con la sesión que diga el turno (1 si solo abre una)

Una parte con «de: <id>» hereda la esperada de esa sencilla. El formato completo está en la cabecera del banco y
tests/test_banco_preguntas.py lo comprueba; aquí solo se rechaza lo que impediría correrlo.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from agente import preguntas as modulo_preguntas

RAIZ = Path(__file__).resolve().parent.parent
BANCO = RAIZ / "eval" / "preguntas.yaml"
# El cargador en C de PyYAML lee el banco en 0.03 s y el de Python en 0.2 s; las pruebas lo leen cientos de veces.
# Si PyYAML no trae libyaml queda el de Python, que da el mismo resultado.
CARGADOR = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

CON_SIGNOS = "con_signos"
SIN_SIGNOS = "sin_signos"


class BancoInvalido(ValueError):
    """El banco no se puede correr tal como está: el mensaje nombra el prompt."""


class SeccionEsperada(NamedTuple):
    documento: str
    seccion: str


class LecturaAceptada(NamedTuple):
    """Lo que debe decir una respuesta para aceptar una sola lectura declarada de una pregunta ambigua (T5.9).

    Una de las cifras y, de cada grupo de frases, una: «qué días y qué fecha tomó como hoy», «qué tienda y con qué medida».
    """

    cifras: tuple[str, ...]
    frases: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Pregunta:
    """Lo que una respuesta tiene que traer, con los campos del banco que usan los evaluadores."""

    id: str
    prompt: str
    categoria: str
    tipo_respuesta: str
    estado_esperado: str
    respuesta_esperada: str
    cifras_esperadas: tuple[str, ...]
    secciones_esperadas: tuple[SeccionEsperada, ...]
    herramientas_esperadas: tuple[str, ...]
    parametros_esperados: Mapping[str, Mapping[str, Any]]
    criterio: str
    negativa: bool
    ticket_con_datos_personales: int | None = None
    repite: str | None = None
    copia: str | None = None
    lectura_aceptada: LecturaAceptada | None = None
    # En un compuesto sin_signos, lo que piden las otras partes del mismo turno: su respuesta trae las cifras de todas.
    otras_partes: tuple[str, ...] = ()
    # Lo que explica el criterio y no es requisito (T5.13, D33-A). El juez no lo recibe.
    notas: str | None = None


@dataclass(frozen=True)
class Paso:
    """Un turno de responder(): el texto que se manda, la sesión en que corre y lo que su respuesta debe traer."""

    texto: str
    sesion: int
    preguntas: tuple[Pregunta, ...]

    @property
    def categoria(self) -> str:
        """La categoría para el registro. Un turno con dos partes de distinta categoría lleva las dos."""
        return "+".join(dict.fromkeys(pregunta.categoria for pregunta in self.preguntas))


@dataclass(frozen=True)
class Prompt:
    """Una entrada del banco, o una variante de una sencilla (variante_de dice de cuál)."""

    id: str
    bloque: str
    pasos: tuple[Paso, ...]
    nucleo: bool = False
    forma: str | None = None
    prueba: str | None = None
    sesiones: int | None = None
    copia: str | None = None
    variante_de: str | None = None
    variantes: tuple[str, ...] = field(default=(), repr=False)
    # El criterio propio de un compuesto. Las demás entradas lo llevan en su pregunta.
    criterio: str | None = None
    notas: str | None = None


def _lectura_aceptada(datos: Mapping[str, Any]) -> LecturaAceptada | None:
    lectura = datos.get("lectura_aceptada")
    if lectura is None:
        return None
    return LecturaAceptada(tuple(lectura["cifras"]), tuple(tuple(grupo) for grupo in lectura["frases"]))


def _pregunta(datos: Mapping[str, Any], copia: str | None = None, **cambios: Any) -> Pregunta:
    return Pregunta(
        **{
            "id": datos["id"],
            "prompt": datos["prompt"],
            "categoria": datos["categoria"],
            "tipo_respuesta": datos["tipo_respuesta"],
            "estado_esperado": datos["estado_esperado"],
            "respuesta_esperada": datos["respuesta_esperada"],
            "cifras_esperadas": tuple(datos["cifras_esperadas"]),
            "secciones_esperadas": tuple(
                SeccionEsperada(cita["documento"], cita["seccion"]) for cita in datos["secciones_esperadas"]
            ),
            "herramientas_esperadas": tuple(datos["herramientas_esperadas"]),
            "parametros_esperados": dict(datos["parametros_esperados"]),
            "criterio": datos["criterio"],
            "negativa": datos["negativa"],
            "ticket_con_datos_personales": datos.get("ticket_con_datos_personales"),
            "repite": datos.get("repite"),
            "copia": copia or datos.get("copia"),
            "lectura_aceptada": _lectura_aceptada(datos),
            "notas": datos.get("notas"),
            **cambios,
        }
    )


def _partes(datos: Mapping[str, Any], por_id: Mapping[str, Mapping[str, Any]]) -> tuple[Pregunta, ...]:
    partes = []
    for parte in datos["partes"]:
        if "de" not in parte:
            partes.append(_pregunta(parte))
            continue
        if parte["de"] not in por_id:
            raise BancoInvalido(f"{datos['id']}: la parte {parte['de']} no es una sencilla del banco")
        partes.append(_pregunta(por_id[parte["de"]], id=f"{datos['id']}.{parte['de']}"))
    return tuple(partes)


def _pasos_de_compuesto(datos: Mapping[str, Any], por_id: Mapping[str, Mapping[str, Any]]) -> tuple[Paso, ...]:
    partes = _partes(datos, por_id)
    if datos["forma"] == SIN_SIGNOS:
        con_las_otras = tuple(replace(parte, otras_partes=tuple(o.prompt for o in partes if o is not parte)) for parte in partes)
        return (Paso(datos["prompt"], 1, con_las_otras),)
    trozos = modulo_preguntas.separar(datos["prompt"])
    if len(trozos) != len(partes):
        raise BancoInvalido(f"{datos['id']}: separar() da {len(trozos)} trozos y trae {len(partes)} partes")
    return tuple(Paso(trozo, 1, (parte,)) for trozo, parte in zip(trozos, partes, strict=True))


def _prompt(datos: Mapping[str, Any], por_id: Mapping[str, Mapping[str, Any]]) -> Prompt:
    comunes = {"id": datos["id"], "bloque": datos["bloque"], "nucleo": datos.get("nucleo", False) is True}
    if "turnos" in datos:
        pasos = tuple(Paso(turno["prompt"], turno.get("sesion", 1), (_pregunta(turno),)) for turno in datos["turnos"])
        return Prompt(pasos=pasos, prueba=datos.get("prueba"), sesiones=datos.get("sesiones"), **comunes)
    if "partes" in datos:
        return Prompt(
            pasos=_pasos_de_compuesto(datos, por_id), forma=datos["forma"], criterio=datos["criterio"], notas=datos.get("notas"), **comunes
        )
    pregunta = _pregunta(datos)
    return Prompt(
        pasos=(Paso(datos["prompt"], 1, (pregunta,)),),
        copia=pregunta.copia,
        variantes=tuple(datos.get("variantes", ())),
        **comunes,
    )


def leer_banco(ruta: Path = BANCO) -> tuple[Prompt, ...]:
    """Los prompts del banco, en su orden. Lanza BancoInvalido si uno no se puede correr."""
    entradas = yaml.load(Path(ruta).read_text(encoding="utf-8"), Loader=CARGADOR)["prompts"]
    por_id = {entrada["id"]: entrada for entrada in entradas if "turnos" not in entrada and "partes" not in entrada}
    return tuple(_prompt(entrada, por_id) for entrada in entradas)


def sha256_del_banco(ruta: Path = BANCO) -> str:
    """El sha256 del banco con fin de línea LF: el del commit, aunque el disco esté en CRLF (K15)."""
    return hashlib.sha256(Path(ruta).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _variantes_de(prompt: Prompt) -> tuple[Prompt, ...]:
    """Cada paráfrasis como un prompt aparte, con la misma esperada. Se reporta aparte: no cuenta en el rango (D25)."""
    paso = prompt.pasos[0]
    pregunta = paso.preguntas[0]
    return tuple(
        replace(
            prompt,
            id=f"{prompt.id}~{numero}",
            nucleo=False,
            variante_de=prompt.id,
            variantes=(),
            pasos=(replace(paso, texto=texto, preguntas=(replace(pregunta, id=f"{pregunta.id}~{numero}", prompt=texto),)),),
        )
        for numero, texto in enumerate(prompt.variantes, start=1)
    )


def seleccionar(prompts: Sequence[Prompt], *, completo: bool = False, variantes: bool = False) -> tuple[Prompt, ...]:
    """El núcleo por omisión o todos con completo; con variantes, además las paráfrasis de las sencillas elegidas."""
    elegidos = tuple(prompt for prompt in prompts if completo or prompt.nucleo)
    if not variantes:
        return elegidos
    return elegidos + tuple(variante for prompt in elegidos for variante in _variantes_de(prompt))
