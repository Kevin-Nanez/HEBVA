"""Convierte las marcas de "respuesta incorrecta" del registro en casos pendientes (T4.4, K28, C5.3).

    .venv\\Scripts\\python -m eval.casos_desde_marcas [--registro logs/turnos.jsonl] [--salida eval/casos_pendientes.yaml]

La interfaz deja una línea `marca` en logs/turnos.jsonl cuando la gerente pulsa "Respuesta incorrecta" (agente/registro.py).
Este comando lee esas marcas, busca el turno de cada una por su id, toma su pregunta del registro y la agrega como caso
pendiente a eval/casos_pendientes.yaml:

- La esperada queda vacía (respuesta_esperada nula y cifras_esperadas sin elementos). Se llena con lo que calcula
  eval/respuestas_esperadas.py sobre datos/, o con el PDF citado, y nunca con la respuesta del sistema que se marcó:
  esa respuesta no se copia a ningún campo.
- El archivo es aparte. eval/preguntas.yaml es el banco de C5.1 y este comando no lo abre; el caso entra al banco a mano,
  cuando ya tiene su esperada.
- Volver a correr el comando no duplica casos ni toca los que ya están: cada caso se reconoce por su turno de origen, y
  lo que se haya llenado a mano se conserva. Sin marcas nuevas, el archivo queda como estaba.
- La pregunta pasa otra vez por el enmascarado del registro (K26), por si la línea viene de antes o se editó a mano. Lo
  mismo el comentario, si quien marcó lo escribió: orienta a quien revisa el caso y nunca es la esperada.
- No llama a ningún modelo y no necesita llave.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from agente import registro

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "eval" / "casos_pendientes.yaml"

PREFIJO_DE_ID = "MC"
ESTADO_PENDIENTE = "pendiente"
LLENAR_CON = (
    "eval/respuestas_esperadas.py (python -m eval.respuestas_esperadas datos) o el PDF citado; "
    "nunca la respuesta del sistema"
)
_ID_DE_CASO = re.compile(rf"{PREFIJO_DE_ID}-(\d+)")

CABECERA = f"""\
# Casos pendientes que salen de las marcas de "respuesta incorrecta" de la interfaz (T4.4, C5.3). Los escribe
# python -m eval.casos_desde_marcas; ni eval.correr ni eval/banco.py los leen.
#
# Cada caso trae su id ({PREFIJO_DE_ID}-NN), el turno de origen en logs/turnos.jsonl, la hora de la marca y la pregunta
# tal como quedó en el registro, ya enmascarada. respuesta_esperada y cifras_esperadas empiezan vacías: se llenan con lo que
# calcula eval/respuestas_esperadas.py sobre datos/, o con el PDF citado, y nunca con la respuesta del sistema que se
# marcó. Al llenarlas, estado pasa de {ESTADO_PENDIENTE} a lista y el caso se pasa a eval/preguntas.yaml con el formato
# de su cabecera. comentario, si está, es lo que escribió quien marcó: orienta la revisión y tampoco es la esperada.
# Volver a correr el comando no duplica casos ni cambia los que ya están aquí.

"""


class ArchivoInvalido(ValueError):
    """El archivo de casos existe y no tiene la forma esperada: el comando no lo reescribe."""


def _plural(cantidad: int, singular: str, plural: str) -> str:
    return f"{cantidad} {singular if cantidad == 1 else plural}"


def _sin_datos_de_contacto(pregunta: str) -> str:
    """La pregunta con el mismo enmascarado con que el registro escribe sus líneas (teléfono, correo, llave)."""
    return json.loads(registro.serializar({"pregunta": pregunta}))["pregunta"]


def leer_marcas_y_preguntas(archivo: Path) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Las marcas (turno_id y hora de la primera, en orden), la pregunta de cada turno y el primer comentario de cada marca."""
    marcas: dict[str, Any] = {}
    preguntas: dict[str, str] = {}
    comentarios: dict[str, str] = {}
    for linea in registro.leer(archivo):
        turno_id = linea.get("turno_id")
        if not isinstance(turno_id, str):
            continue
        if linea.get("tipo") == registro.TIPO_MARCA:
            hora = linea.get("hora")
            marcas.setdefault(turno_id, hora if isinstance(hora, str) else None)
            if isinstance(linea.get("comentario"), str):
                comentarios.setdefault(turno_id, linea["comentario"])
        elif linea.get("tipo") == "turno" and isinstance(linea.get("pregunta"), str):
            preguntas.setdefault(turno_id, linea["pregunta"])
    return marcas, preguntas, comentarios


def leer_casos(salida: Path) -> list[dict[str, Any]]:
    """Los casos que ya hay en el archivo; ninguno si no existe. Si no tiene la forma esperada, ArchivoInvalido."""
    if not salida.exists():
        return []
    try:
        datos = yaml.safe_load(salida.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        raise ArchivoInvalido(f"{salida.name} no se puede leer como YAML ({type(error).__name__})") from error
    casos = [] if datos is None else (datos.get("casos") if isinstance(datos, dict) else None)
    if not isinstance(casos, list) or not all(isinstance(caso, dict) for caso in casos):
        raise ArchivoInvalido(f"{salida.name} no es un mapa con la lista `casos`")
    return casos


def _siguiente_numero(casos: Sequence[dict[str, Any]]) -> int:
    numeros = [int(coincidencia.group(1)) for caso in casos if (coincidencia := _ID_DE_CASO.fullmatch(str(caso.get("id"))))]
    return max(numeros, default=0) + 1


def caso_nuevo(numero: int, turno_id: str, hora: str | None, pregunta: str, comentario: str | None = None) -> dict[str, Any]:
    """Un caso pendiente: la pregunta del registro, su turno de origen, el comentario si lo hay y la esperada vacía."""
    del_comentario = {"comentario": _sin_datos_de_contacto(comentario)} if comentario else {}
    return {
        "id": f"{PREFIJO_DE_ID}-{numero:02d}",
        "turno_origen": turno_id,
        "marcado": hora,
        "pregunta": _sin_datos_de_contacto(pregunta),
        **del_comentario,
        "estado": ESTADO_PENDIENTE,
        "respuesta_esperada": None,
        "cifras_esperadas": [],
        "llenar_con": LLENAR_CON,
    }


def casos_nuevos(
    marcas: dict[str, Any],
    preguntas: dict[str, str],
    existentes: Sequence[dict[str, Any]],
    comentarios: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Los casos de las marcas que aún no tienen el suyo, cuántas ya lo tenían y cuántas no encontraron su turno."""
    ya_tienen = {caso.get("turno_origen") for caso in existentes}
    de_cada_marca = comentarios or {}
    nuevos: list[dict[str, Any]] = []
    ya_estaban = sin_turno = 0
    numero = _siguiente_numero(existentes)
    for turno_id, hora in marcas.items():
        if turno_id in ya_tienen:
            ya_estaban += 1
        elif turno_id not in preguntas:
            sin_turno += 1
        else:
            nuevos.append(caso_nuevo(numero + len(nuevos), turno_id, hora, preguntas[turno_id], de_cada_marca.get(turno_id)))
    return nuevos, ya_estaban, sin_turno


def escribir_casos(salida: Path, casos: Sequence[dict[str, Any]]) -> None:
    """Escribe el archivo entero en UTF-8 con LF, primero a un temporal para no dejar un archivo a medias."""
    cuerpo = yaml.safe_dump({"casos": list(casos)}, allow_unicode=True, sort_keys=False, width=1000)
    temporal = salida.with_name(f"{salida.name}.tmp")
    salida.parent.mkdir(parents=True, exist_ok=True)
    temporal.write_text(CABECERA + cuerpo, encoding="utf-8", newline="\n")
    os.replace(temporal, salida)


def main(argumentos: Sequence[str] | None = None) -> int:
    lector = argparse.ArgumentParser(prog="python -m eval.casos_desde_marcas", description=__doc__.split("\n\n")[0])
    lector.add_argument("--registro", type=Path, default=registro.ARCHIVO, help="el registro de turnos y marcas")
    lector.add_argument("--salida", type=Path, default=SALIDA, help="el archivo de casos pendientes")
    opciones = lector.parse_args(argumentos)
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")
    if not opciones.registro.is_file():
        print(f"No hay un registro en {opciones.registro}: marca una respuesta desde la interfaz primero.", file=sys.stderr)
        return 1
    try:
        existentes = leer_casos(opciones.salida)
        marcas, preguntas, comentarios = leer_marcas_y_preguntas(opciones.registro)
        nuevos, ya_estaban, sin_turno = casos_nuevos(marcas, preguntas, existentes, comentarios)
        if nuevos:
            escribir_casos(opciones.salida, [*existentes, *nuevos])
    except (ArchivoInvalido, OSError) as error:
        print(f"No pude actualizar los casos pendientes: {error}", file=sys.stderr)
        return 1
    print(
        f"{_plural(len(marcas), 'turno marcado', 'turnos marcados')}: {_plural(len(nuevos), 'caso nuevo', 'casos nuevos')}, "
        f"{_plural(ya_estaban, 'ya estaba', 'ya estaban')}."
    )
    if sin_turno:
        print(f"{_plural(sin_turno, 'marca sin turno en el registro', 'marcas sin turno en el registro')}: no se armó su caso.")
    if nuevos:
        print(f"Casos pendientes: {opciones.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
