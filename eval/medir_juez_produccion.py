r"""Mide el juez de producción contra los errores de la auditoría: python -m eval.medir_juez_produccion.

Uso, desde la raíz del repositorio:

    .venv\Scripts\python -m eval.medir_juez_produccion --corridas 2     las pasadas completas: gasta la llave y escribe el reporte
    .venv\Scripts\python -m eval.medir_juez_produccion --solo-conjunto  cuenta el conjunto: no gasta la llave
    .venv\Scripts\python -m eval.medir_juez_produccion --muestra 3      3 de cada etiqueta, para probar la conexión: no escribe nada

Sale con 0 si cumple D2-B en todas las corridas y con 1 si no; con 2 ante un argumento inválido y con 4 si el modelo no estuvo
disponible, sin escribir nada, porque un 429 en cadena no dice nada del juez.

El conjunto sale de docs/planes/2026-11-05-auditoria-respuestas/, que está fuera de git, y de logs/turnos.jsonl. «Debe reprobar»
son los turnos con un caso CONFIRMADO de E01 a E10. «Debe aprobar» son los turnos de E10 sin caso confirmado ni dudoso; un caso
RETIRADO se refutó con su fuente, así que su turno cuenta como correcto. Las etiquetas las puso la auditoría, hecha por agentes y
sin revisión de una persona.

La vara es D2-B, fijada antes de medir: atrapa 9 de cada 10 errores y marca como máximo 1 de cada 50 correctas, en cada corrida
y comparado con enteros. Un turno con dos casos es un veredicto: se cuentan turnos y casos, y se exigen los dos. Un turno que
producción no le pasaría al juez (un texto fijo) y un turno sin veredicto no cuentan como detectados, y un turno correcto sin
veredicto tampoco cuenta como falsa alarma. Detectado quiere decir que el juez reprobó el turno: no que su motivo nombre ese error.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import io
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import ModelAPIError, RunTimedOut, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from agente import agente, registro
from agente import juez as modulo_juez
from agente import reintentos as modulo_reintentos
from agente.herramientas.comun import Resultado, dumps
from eval import banco, evaluadores
from eval import juez as juez_del_set

DIR_AUDITORIA = banco.RAIZ / "docs" / "planes" / "2026-11-05-auditoria-respuestas"
DIR_REPORTES = juez_del_set.DIR_REPORTES
PREFIJO_DEL_REPORTE = "medida-produccion"

EJECUCION_DE_CORRECTOS = "E10"
VIGILADOS = ("E10/PT-19", "E10/PT-30")
CONFIRMADO = "CONFIRMADO"
DUDOSO = "DUDOSO"
DEBE_REPROBAR = "debe_reprobar"
DEBE_APROBAR = "debe_aprobar"
NO_LLEGA = "no_llega_al_juez"

# D2-B: 90 de cada 100 detectados y hasta 2 de cada 100 falsas alarmas, comparados con enteros.
MINIMO_DETECTADOS_POR_CIENTO = 90
MAXIMO_FALSAS_POR_CIENTO = 2
# El límite que t2-2 propone para la app: el reporte cuenta cuántas llamadas lo pasarían.
SEGUNDOS_PROPUESTOS = 10

CORRIDAS_POR_OMISION = 2
PARALELAS_POR_OMISION = 4

SALIDA_BIEN = 0
SALIDA_BAJO_D2 = 1
SALIDA_SIN_MODELO = 4

_NUMERO_DE_TURNO = re.compile(r"(?P<prompt>.+)-(?P<numero>\d+)$")
_PUNTOS_SUSPENSIVOS = re.compile(r"\.\.\.|…")


# ------------------------------------------------------------------ el conjunto


@dataclass(frozen=True)
class CasoConfirmado:
    clase: str
    cita: str


@dataclass(frozen=True)
class Renglon:
    """Un turno del conjunto: id es ejecución y turno de la auditoría, como E10/PT-19."""

    id: str
    etiqueta: str
    turno_id: str
    revision: modulo_juez.TurnoARevisar
    casos: tuple[CasoConfirmado, ...] = ()
    llega_al_juez: bool = True


@dataclass(frozen=True)
class Conjunto:
    debe_reprobar: tuple[Renglon, ...]
    debe_aprobar: tuple[Renglon, ...]
    no_cruzaron: tuple[str, ...] = ()
    citas_sin_hallar: tuple[str, ...] = ()
    dudosos_fuera: int = 0

    @property
    def casos(self) -> int:
        return sum(len(renglon.casos) for renglon in self.debe_reprobar)

    @property
    def renglones(self) -> tuple[Renglon, ...]:
        return (*self.debe_reprobar, *self.debe_aprobar)

    @property
    def no_llegan_al_juez(self) -> tuple[str, ...]:
        return tuple(renglon.id for renglon in self.renglones if not renglon.llega_al_juez)


def _leer_csv(ruta: Path) -> list[dict[str, str]]:
    with open(ruta, encoding="utf-8", newline="") as entrada:
        return list(csv.DictReader(entrada))


def _ejecuciones(carpeta: Path) -> list[str]:
    return sorted(ruta.name.removesuffix("-consolidado.csv") for ruta in (carpeta / "casos").glob("E*-consolidado.csv"))


def _turnos_por_prompt(reporte: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    por_prompt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for turno in reporte["turnos"]:
        por_prompt[turno["prompt"]].append(turno)
    return por_prompt


def ubicar_turno(por_prompt: Mapping[str, Sequence[dict[str, Any]]], turno_de_auditoria: str) -> dict[str, Any] | None:
    """El turno de un caso: PT-19 es el único turno de ese prompt y DN-05-2 es el segundo de DN-05. None si no cruza."""
    if turno_de_auditoria in por_prompt:
        turnos = por_prompt[turno_de_auditoria]
        return turnos[0] if len(turnos) == 1 else None
    partes = _NUMERO_DE_TURNO.fullmatch(turno_de_auditoria)
    if partes is None or partes["prompt"] not in por_prompt:
        return None
    turnos = por_prompt[partes["prompt"]]
    posicion = int(partes["numero"]) - 1
    return turnos[posicion] if 0 <= posicion < len(turnos) else None


def _plano(texto: str) -> str:
    return " ".join(texto.replace("*", "").split()).casefold()


def _cita_en_texto(cita: str, texto: str) -> bool:
    """Si cada tramo de la cita está en el texto: un caso sin su cita pudo cruzar con el turno equivocado."""
    plano = _plano(texto)
    return all(_plano(tramo) in plano for tramo in _PUNTOS_SUSPENSIVOS.split(cita) if tramo.strip())


def _lineas_del_registro(archivo: Path) -> dict[str, dict[str, Any]]:
    return {linea["turno_id"]: linea for linea in registro.leer(archivo) if linea.get("tipo") == "turno"}


def _revision_de(turno: Mapping[str, Any], linea: Mapping[str, Any]) -> modulo_juez.TurnoARevisar:
    resultados = [Resultado.model_validate(herramienta["gen_ai.tool.call.result"]) for herramienta in linea["herramientas"]]
    return modulo_juez.TurnoARevisar(
        pregunta=linea["pregunta"],
        evidencia=modulo_juez.evidencia_de(resultados),
        # Lo que leyó la auditoría: el texto mostrado y debajo su pie, como lo lee la gerente.
        respuesta=turno["texto_gerente"],
        anteriores=modulo_juez.anteriores_de(registro.historial_de(linea)),
    )


def _renglon(identificador: str, etiqueta: str, turno: Mapping[str, Any], linea: Mapping[str, Any], casos: Sequence[CasoConfirmado] = ()) -> Renglon:
    return Renglon(
        id=identificador,
        etiqueta=etiqueta,
        turno_id=turno["turno_id"],
        revision=_revision_de(turno, linea),
        casos=tuple(casos),
        llega_al_juez=modulo_juez.se_revisa(linea["estado"], linea["respuesta"]),
    )


def armar_conjunto(carpeta: Path = DIR_AUDITORIA, archivo_turnos: Path = registro.ARCHIVO) -> Conjunto:
    lineas = _lineas_del_registro(archivo_turnos)
    errores: list[Renglon] = []
    correctos: list[Renglon] = []
    no_cruzaron: list[str] = []
    citas_sin_hallar: list[str] = []
    dudosos_fuera = 0
    for ejecucion in _ejecuciones(carpeta):
        filas = _leer_csv(carpeta / "casos" / f"{ejecucion}-consolidado.csv")
        por_prompt = _turnos_por_prompt(json.loads((carpeta / "reportes" / f"{ejecucion}.json").read_text(encoding="utf-8")))
        ubicados: dict[str, tuple[dict[str, Any], list[CasoConfirmado]]] = {}
        for fila in filas:
            if fila["estado"] != CONFIRMADO:
                continue
            identificador = f"{ejecucion}/{fila['turno_id']}"
            turno = ubicar_turno(por_prompt, fila["turno_id"])
            if turno is None or turno["turno_id"] not in lineas:
                motivo = "no hay un turno con ese id" if turno is None else "su turno no está en el registro"
                no_cruzaron.append(f"{identificador}: {motivo}")
                continue
            if not _cita_en_texto(fila["cita"], turno["texto_gerente"]) and identificador not in citas_sin_hallar:
                citas_sin_hallar.append(identificador)
            ubicados.setdefault(identificador, (turno, []))[1].append(CasoConfirmado(fila["clase"], fila["cita"]))
        errores += [
            _renglon(identificador, DEBE_REPROBAR, turno, lineas[turno["turno_id"]], casos)
            for identificador, (turno, casos) in ubicados.items()
        ]
        if ejecucion != EJECUCION_DE_CORRECTOS:
            continue
        con_caso = {fila["turno_id"] for fila in filas if fila["estado"] in (CONFIRMADO, DUDOSO)}
        marcados = [ubicar_turno(por_prompt, turno_de_auditoria) for turno_de_auditoria in con_caso]
        ids_marcados = {turno["turno_id"] for turno in marcados if turno}
        dudosos_fuera = len({fila["turno_id"] for fila in filas if fila["estado"] == DUDOSO})
        for prompt, turnos in por_prompt.items():
            for numero, turno in enumerate(turnos, start=1):
                if turno["turno_id"] in ids_marcados:
                    continue
                if turno["turno_id"] not in lineas:
                    no_cruzaron.append(f"{ejecucion}/{prompt}: su turno no está en el registro")
                    continue
                identificador = f"{ejecucion}/{prompt}" if len(turnos) == 1 else f"{ejecucion}/{prompt}-{numero}"
                correctos.append(_renglon(identificador, DEBE_APROBAR, turno, lineas[turno["turno_id"]]))
    return Conjunto(tuple(errores), tuple(correctos), tuple(no_cruzaron), tuple(citas_sin_hallar), dudosos_fuera)


# ------------------------------------------------------------------ la cuenta contra D2-B


def cumple_d2(detectados: int, con_error: int, falsas: int, correctos: int) -> bool:
    """D2-B con enteros, para que 89.99 % no pase por redondeo."""
    return detectados * 100 >= MINIMO_DETECTADOS_POR_CIENTO * con_error and falsas * 100 <= MAXIMO_FALSAS_POR_CIENTO * correctos


def percentil(valores: Sequence[int], porcentaje: int) -> int | None:
    """El percentil por rango más cercano: el valor que deja debajo ese porcentaje, sin interpolar."""
    if not valores:
        return None
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, math.floor(len(ordenados) * porcentaje / 100))]


def contar_corrida(conjunto: Conjunto, juicios: Mapping[str, juez_del_set.Juicio], corrida: int) -> dict[str, Any]:
    """La cuenta de una corrida a partir de cada veredicto: lo que un revisor recalcula con los renglones del reporte."""
    detectados = [renglon for renglon in conjunto.debe_reprobar if juicios[renglon.id].veredicto == juez_del_set.REPRUEBA]
    falsas = [renglon.id for renglon in conjunto.debe_aprobar if juicios[renglon.id].veredicto == juez_del_set.REPRUEBA]
    sin_veredicto = [
        renglon.id
        for renglon in conjunto.renglones
        if renglon.llega_al_juez and juicios[renglon.id].veredicto == juez_del_set.SIN_VEREDICTO
    ]
    ids_detectados = {renglon.id for renglon in detectados}
    casos_detectados = sum(len(renglon.casos) for renglon in detectados)
    por_clase: Counter[str] = Counter(caso.clase for renglon in conjunto.debe_reprobar for caso in renglon.casos)
    clases_detectadas: Counter[str] = Counter(caso.clase for renglon in detectados for caso in renglon.casos)
    con_error = len(conjunto.debe_reprobar)
    correctos = len(conjunto.debe_aprobar)
    por_turnos = cumple_d2(len(detectados), con_error, len(falsas), correctos)
    por_casos = cumple_d2(casos_detectados, conjunto.casos, len(falsas), correctos)
    return {
        "corrida": corrida,
        "turnos_detectados": len(detectados),
        "turnos_con_error": con_error,
        "casos_detectados": casos_detectados,
        "casos": conjunto.casos,
        "falsas_alarmas": len(falsas),
        "turnos_correctos": correctos,
        "intervalo_95_detectados": [round(valor, 3) for valor in juez_del_set.intervalo_wilson(len(detectados), con_error)] if con_error else None,
        "intervalo_95_falsas": [round(valor, 3) for valor in juez_del_set.intervalo_wilson(len(falsas), correctos)] if correctos else None,
        "por_clase": {clase: {"detectados": clases_detectadas[clase], "n": por_clase[clase]} for clase in sorted(por_clase)},
        "no_detectados": [renglon.id for renglon in conjunto.debe_reprobar if renglon.id not in ids_detectados],
        "falsas": falsas,
        "sin_veredicto": sin_veredicto,
        "cumple": por_turnos and por_casos,
    }


# ------------------------------------------------------------------ el juicio


async def juzgar(turno: modulo_juez.TurnoARevisar, modelo: Model | str) -> juez_del_set.Juicio:
    """Una llamada, con el presupuesto del juez del set. Un veredicto ilegible da sin veredicto; sin modelo, lanza ModeloNoDisponible."""
    inicio = time.monotonic()

    def restante() -> float:
        return juez_del_set.SEGUNDOS_DEL_JUEZ - (time.monotonic() - inicio)

    con_reintentos, ajustes = juez_del_set._preparar(modelo, restante)
    with capture_run_messages() as capturados:
        try:
            resultado = await modulo_juez.JUEZ.run(
                modulo_juez.armar_mensaje(turno),
                model=con_reintentos,
                model_settings=ajustes,
                usage_limits=UsageLimits(request_limit=juez_del_set.SOLICITUDES_DEL_JUEZ),
                timeout=juez_del_set.SEGUNDOS_DEL_JUEZ,
            )
        except ModelAPIError as error:
            raise juez_del_set.ModeloNoDisponible(modulo_reintentos.clasificar(error).motivo) from error
        except (UnexpectedModelBehavior, UsageLimitExceeded, RunTimedOut) as error:
            return juez_del_set.Juicio(None, type(error).__name__, agente._llamadas(capturados), juez_del_set._ms_desde(inicio))
    motivo = registro._texto_limpio(resultado.output.motivo)[: juez_del_set.LARGO_MAXIMO_DEL_MOTIVO]
    return juez_del_set.Juicio(resultado.output.aprueba, motivo, agente._llamadas(resultado.new_messages()), juez_del_set._ms_desde(inicio))


@dataclass
class _Avance:
    total: int
    hechos: int = 0
    inicio: float = field(default_factory=time.monotonic)

    def uno_mas(self) -> None:
        self.hechos += 1
        if self.hechos % 25 == 0 or self.hechos == self.total:
            print(f"  {self.hechos} de {self.total} en {time.monotonic() - self.inicio:,.0f} s", flush=True)


async def _juzgar_corrida(renglones: Sequence[Renglon], modelo: Model | str, paralelas: int) -> dict[str, juez_del_set.Juicio]:
    semaforo = asyncio.Semaphore(paralelas)
    avance = _Avance(sum(renglon.llega_al_juez for renglon in renglones))

    async def uno(renglon: Renglon) -> tuple[str, juez_del_set.Juicio]:
        if not renglon.llega_al_juez:
            return renglon.id, juez_del_set.Juicio(None, NO_LLEGA)
        async with semaforo:
            juicio = await juzgar(renglon.revision, modelo)
        avance.uno_mas()
        return renglon.id, juicio

    return dict(await asyncio.gather(*(uno(renglon) for renglon in renglones)))


# ------------------------------------------------------------------ el reporte


def _uso(conjunto: Conjunto, por_corrida: Sequence[Mapping[str, juez_del_set.Juicio]]) -> dict[str, Any]:
    juicios = [juicio for corrida in por_corrida for renglon in conjunto.renglones if (juicio := corrida[renglon.id]).llamadas]
    llamadas = [llamada for juicio in juicios for llamada in juicio.llamadas]
    latencias = [juicio.latencia_ms for juicio in juicios]
    entradas = [sum(llamada.entrada_tokens for llamada in juicio.llamadas) for juicio in juicios]
    salidas = [sum(llamada.salida_tokens for llamada in juicio.llamadas) for juicio in juicios]
    costo = registro._suma_costos([registro.costo_usd(llamada) for llamada in llamadas])
    return {
        "juicios": len(juicios),
        "llamadas": len(llamadas),
        "entrada_tokens": sum(entradas),
        "salida_tokens": sum(salidas),
        "entrada_p50": percentil(entradas, 50),
        "entrada_p90": percentil(entradas, 90),
        "salida_p50": percentil(salidas, 50),
        "salida_p90": percentil(salidas, 90),
        "costo_usd": costo,
        "costo_por_1000_respuestas_usd": None if costo is None or not juicios else round(costo * 1000 / len(juicios), 4),
        "latencia_p50_ms": percentil(latencias, 50),
        "latencia_p90_ms": percentil(latencias, 90),
        "latencia_maxima_ms": max(latencias, default=None),
        "sobre_el_limite_propuesto": sum(latencia > SEGUNDOS_PROPUESTOS * 1000 for latencia in latencias),
    }


def _veredictos_de(identificador: str, por_corrida: Sequence[Mapping[str, juez_del_set.Juicio]]) -> list[str]:
    return [corrida[identificador].veredicto for corrida in por_corrida if identificador in corrida]


def armar_reporte(conjunto: Conjunto, por_corrida: Sequence[Mapping[str, juez_del_set.Juicio]], modelo: str) -> dict[str, Any]:
    corridas = [contar_corrida(conjunto, juicios, numero) for numero, juicios in enumerate(por_corrida, start=1)]
    renglones = [
        {
            "id": renglon.id,
            "etiqueta": renglon.etiqueta,
            "turno_id": renglon.turno_id,
            "clases": [caso.clase for caso in renglon.casos],
            "veredictos": _veredictos_de(renglon.id, por_corrida),
            "motivos": [evaluadores.sin_datos_de_tickets(corrida[renglon.id].motivo) for corrida in por_corrida],
        }
        for renglon in conjunto.renglones
    ]
    return {
        "fecha": dt.date.today().isoformat(),
        "commit": registro.commit(),
        "modelo": modelo,
        "prompt_sha256": modulo_juez.PROMPT_JUEZ_PRODUCCION_SHA256,
        "vara": f"D2-B: {MINIMO_DETECTADOS_POR_CIENTO} % detectados o más y {MAXIMO_FALSAS_POR_CIENTO} % de falsas alarmas o menos, en cada corrida",
        "cumple_d2": bool(corridas) and all(corrida["cumple"] for corrida in corridas),
        "conjunto": {
            "turnos_con_error": len(conjunto.debe_reprobar),
            "casos": conjunto.casos,
            "turnos_correctos": len(conjunto.debe_aprobar),
            "dudosos_fuera": conjunto.dudosos_fuera,
            "no_cruzaron": list(conjunto.no_cruzaron),
            "citas_sin_hallar": list(conjunto.citas_sin_hallar),
            "no_llegan_al_juez": list(conjunto.no_llegan_al_juez),
        },
        "corridas": corridas,
        "vigilados": {identificador: _veredictos_de(identificador, por_corrida) for identificador in VIGILADOS},
        "uso": _uso(conjunto, por_corrida),
        "renglones": renglones,
    }


def _por_ciento(parte: int, total: int) -> str:
    return f"{100 * parte / total:,.1f} %" if total else "sin datos"


def _lista(identificadores: Sequence[str]) -> str:
    return ", ".join(identificadores) or "ninguno"


def _markdown(reporte: Mapping[str, Any]) -> str:
    conjunto = reporte["conjunto"]
    uso = reporte["uso"]
    commit = reporte["commit"] or {}
    lineas = [
        f"# Juez de producción contra la auditoría, {reporte['fecha']}",
        "",
        f"{'Cumple' if reporte['cumple_d2'] else 'No cumple'} {reporte['vara']}.",
        "",
        f"- Modelo: {reporte['modelo']}",
        f"- Commit: {commit.get('sha')}{' con cambios sin commit' if commit.get('sucio') else ''}",
        f"- Prompt del juez, sha256: {reporte['prompt_sha256']}",
        f"- Conjunto: {conjunto['turnos_con_error']} turnos con error ({conjunto['casos']} casos confirmados) y {conjunto['turnos_correctos']} turnos correctos de E10; {conjunto['dudosos_fuera']} turnos dudosos de E10 fuera.",
        f"- No cruzaron: {_lista(conjunto['no_cruzaron'])}.",
        f"- Citas sin hallar en su turno: {_lista(conjunto['citas_sin_hallar'])}.",
        f"- No llegan al juez porque son texto fijo: {_lista(conjunto['no_llegan_al_juez'])}.",
        f"- Uso: {uso['llamadas']:,} llamadas, {uso['entrada_tokens']:,} tokens de entrada y {uso['salida_tokens']:,} de salida, {uso['costo_usd']} USD.",
        f"- Por respuesta: entrada p50 {uso['entrada_p50']} y p90 {uso['entrada_p90']} tokens, salida p50 {uso['salida_p50']} y p90 {uso['salida_p90']}; {uso['costo_por_1000_respuestas_usd']} USD por cada 1,000.",
        f"- Latencia: p50 {uso['latencia_p50_ms']} ms, p90 {uso['latencia_p90_ms']} ms, máxima {uso['latencia_maxima_ms']} ms; {uso['sobre_el_limite_propuesto']} juicios pasan de {SEGUNDOS_PROPUESTOS} s.",
        "",
        "## Corridas",
        "",
        "| Corrida | Turnos detectados | Casos detectados | Falsas alarmas | Clase A | Clase B | Clase C | Sin veredicto | Cumple |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for corrida in reporte["corridas"]:
        clases = [corrida["por_clase"].get(clase, {"detectados": 0, "n": 0}) for clase in ("A", "B", "C")]
        lineas.append(
            "| "
            + " | ".join(
                [
                    str(corrida["corrida"]),
                    f"{corrida['turnos_detectados']} de {corrida['turnos_con_error']} ({_por_ciento(corrida['turnos_detectados'], corrida['turnos_con_error'])})",
                    f"{corrida['casos_detectados']} de {corrida['casos']} ({_por_ciento(corrida['casos_detectados'], corrida['casos'])})",
                    f"{corrida['falsas_alarmas']} de {corrida['turnos_correctos']} ({_por_ciento(corrida['falsas_alarmas'], corrida['turnos_correctos'])})",
                    *[f"{clase['detectados']} de {clase['n']}" for clase in clases],
                    str(len(corrida["sin_veredicto"])),
                    "sí" if corrida["cumple"] else "no",
                ]
            )
            + " |"
        )
    lineas += ["", "## Vigilados de E10", ""]
    lineas += [f"- {identificador}: {', '.join(veredictos) or 'no está en el conjunto'}" for identificador, veredictos in reporte["vigilados"].items()]
    lineas += ["", "## Lo que falló en cada corrida", ""]
    for corrida in reporte["corridas"]:
        lineas += [
            f"Corrida {corrida['corrida']}. No detectados: {_lista(corrida['no_detectados'])}.",
            "",
            f"Falsas alarmas: {_lista(corrida['falsas'])}.",
            "",
        ]
    return "\n".join(lineas).rstrip() + "\n"


def _nombre_del_reporte(modelo: str) -> str:
    return f"{PREFIJO_DEL_REPORTE}-{re.sub(r'[^A-Za-z0-9._-]+', '-', modelo)}"


def guardar_reporte(reporte: Mapping[str, Any], carpeta: Path) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    base = _nombre_del_reporte(reporte["modelo"])
    (carpeta / f"{base}.json").write_text(dumps(reporte) + "\n", encoding="utf-8", newline="\n")
    ruta = carpeta / f"{base}.md"
    ruta.write_text(_markdown(reporte), encoding="utf-8", newline="\n")
    return ruta


# ------------------------------------------------------------------ el comando


def _entero_positivo(texto: str) -> int:
    valor = int(texto)
    if valor < 1:
        raise argparse.ArgumentTypeError("debe ser 1 o más")
    return valor


def _lector() -> argparse.ArgumentParser:
    lector = argparse.ArgumentParser(prog="python -m eval.medir_juez_produccion", description="Mide el juez de producción contra D2-B.")
    lector.add_argument("--corridas", type=_entero_positivo, default=CORRIDAS_POR_OMISION, help=f"{CORRIDAS_POR_OMISION} por omisión")
    lector.add_argument("--paralelas", type=_entero_positivo, default=PARALELAS_POR_OMISION, help=f"llamadas a la vez, {PARALELAS_POR_OMISION} por omisión")
    lector.add_argument("--modelo", help="id del modelo; sin esto, MODELO de .env, como la interfaz")
    lector.add_argument("--solo-conjunto", action="store_true", help="cuenta el conjunto sin llamar al modelo")
    lector.add_argument("--muestra", type=_entero_positivo, help="N de cada etiqueta, 1 corrida, sin escribir reporte")
    return lector


def _imprimir_conjunto(conjunto: Conjunto) -> None:
    print(
        f"{len(conjunto.debe_reprobar)} turnos con error ({conjunto.casos} casos), {len(conjunto.debe_aprobar)} turnos correctos, "
        f"{conjunto.dudosos_fuera} dudosos de E10 fuera."
    )
    print(f"No cruzaron: {_lista(conjunto.no_cruzaron)}.")
    print(f"Citas sin hallar: {_lista(conjunto.citas_sin_hallar)}.")
    print(f"No llegan al juez: {_lista(conjunto.no_llegan_al_juez)}.")


def _imprimir_corridas(reporte: Mapping[str, Any]) -> None:
    for corrida in reporte["corridas"]:
        print(
            f"Corrida {corrida['corrida']}: turnos {corrida['turnos_detectados']} de {corrida['turnos_con_error']}, "
            f"casos {corrida['casos_detectados']} de {corrida['casos']}, falsas alarmas {corrida['falsas_alarmas']} de {corrida['turnos_correctos']}, "
            f"sin veredicto {len(corrida['sin_veredicto'])}."
        )
    print(f"{'Cumple' if reporte['cumple_d2'] else 'No cumple'} D2-B.")


def _muestra_de(conjunto: Conjunto, cuantos: int) -> Conjunto:
    return Conjunto(conjunto.debe_reprobar[:cuantos], conjunto.debe_aprobar[:cuantos])


def main(
    argumentos: Sequence[str] | None = None,
    *,
    modelo: Model | str | None = None,
    dir_auditoria: Path = DIR_AUDITORIA,
    archivo_turnos: Path = registro.ARCHIVO,
    dir_reportes: Path = DIR_REPORTES,
) -> int:
    """`python -m eval.medir_juez_produccion`. Los parámetros de palabra clave son para las pruebas."""
    # En Windows, una salida redirigida usa cp1252, que no tiene todos los caracteres de los motivos.
    for salida in (sys.stdout, sys.stderr):
        if isinstance(salida, io.TextIOWrapper):
            salida.reconfigure(encoding="utf-8")
    opciones = _lector().parse_args(argumentos)
    conjunto = armar_conjunto(Path(dir_auditoria), Path(archivo_turnos))
    _imprimir_conjunto(conjunto)
    if opciones.solo_conjunto:
        return SALIDA_BIEN
    if opciones.modelo:
        # Con el id como texto, la llave no se leería sola de .env.
        load_dotenv()
    elegido = opciones.modelo or (modelo if modelo is not None else agente._modelo_configurado())
    nombre = agente._nombre_modelo(elegido)
    medido = _muestra_de(conjunto, opciones.muestra) if opciones.muestra else conjunto
    corridas = 1 if opciones.muestra else opciones.corridas
    por_corrida_juicios = sum(renglon.llega_al_juez for renglon in medido.renglones)
    print(f"{por_corrida_juicios * corridas} juicios con {nombre}, {corridas} corridas, {opciones.paralelas} a la vez.", flush=True)

    async def correr() -> list[dict[str, juez_del_set.Juicio]]:
        return [await _juzgar_corrida(medido.renglones, elegido, opciones.paralelas) for _ in range(corridas)]

    try:
        por_corrida = asyncio.run(correr())
    except juez_del_set.ModeloNoDisponible as error:
        print(f"El modelo no estuvo disponible ({error}). No se escribió nada.", file=sys.stderr)
        return SALIDA_SIN_MODELO
    reporte = armar_reporte(medido, por_corrida, nombre)
    _imprimir_corridas(reporte)
    uso = reporte["uso"]
    print(f"Uso: {uso['llamadas']:,} llamadas, {uso['costo_usd']} USD, latencia p50 {uso['latencia_p50_ms']} ms y p90 {uso['latencia_p90_ms']} ms.")
    if opciones.muestra:
        return SALIDA_BIEN
    print(f"Reporte: {guardar_reporte(reporte, Path(dir_reportes))}")
    return SALIDA_BIEN if reporte["cumple_d2"] else SALIDA_BAJO_D2


if __name__ == "__main__":
    raise SystemExit(main())
