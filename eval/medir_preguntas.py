"""Unas preguntas del banco varias veces, con reglas y juez (D34-B): python -m eval.medir_preguntas PT-19 DN-07 --salida CARPETA.

Repite el antes y después de `eval/reportes/antes-despues.md`. Va por el mismo camino de `python -m eval.correr`, con Haiku 5.5, y se
corre en un clon del commit que se mide, con `build/` copiado y la llave de Anthropic en `.env` o en el archivo que diga ARCHIVO_ENV.
El reporte y el registro van a CARPETA: un reporte de pocas preguntas se llama como el del set completo y no debe caer en
`eval/reportes`. No lee ni imprime la llave.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

MODELO = "anthropic:claude-haiku-5-5"
CORRIDAS = 8
SALIDA_ID_DESCONOCIDO = 2


def elegir(prompts: Sequence[Any], ids: Iterable[str]) -> tuple[Any, ...]:
    """Los prompts con esos ids, en el orden del banco; un id que no existe es un error."""
    pedidos = set(ids)
    desconocidos = pedidos - {prompt.id for prompt in prompts}
    if desconocidos:
        raise ValueError(f"No están en el banco: {', '.join(sorted(desconocidos))}")
    return tuple(prompt for prompt in prompts if prompt.id in pedidos)


def _resumen(reporte: dict[str, Any]) -> list[str]:
    """Cuántas corridas acertó cada pregunta, con el juez si corrió."""
    return [
        f"{pregunta['id']}: {sum(bool(marca) for marca in pregunta['por_corrida'])} de {len(pregunta['por_corrida'])}"
        for pregunta in reporte["preguntas"]
    ]


def main(argumentos: list[str] | None = None) -> int:
    lector = argparse.ArgumentParser(prog="python -m eval.medir_preguntas")
    lector.add_argument("ids", nargs="+", metavar="ID", help="ids del banco, como PT-19")
    lector.add_argument("--corridas", type=int, default=CORRIDAS)
    lector.add_argument("--salida", type=Path, required=True, help="carpeta del reporte y del registro")
    opciones = lector.parse_args(argumentos)

    from dotenv import load_dotenv

    load_dotenv(os.environ.get("ARCHIVO_ENV") or None)
    sys.path.insert(0, os.getcwd())
    from eval import banco, correr

    try:
        elegidos = elegir(banco.leer_banco(), opciones.ids)
    except ValueError as error:
        print(error, file=sys.stderr)
        return SALIDA_ID_DESCONOCIDO

    original = banco.seleccionar
    banco.seleccionar = lambda *_, **__: elegidos
    try:
        codigo = correr.main(
            ["--completo", "--corridas", str(opciones.corridas), "--modelo", MODELO],
            dir_reportes=opciones.salida,
            archivo_registro=opciones.salida / "turnos.jsonl",
        )
    finally:
        banco.seleccionar = original
    for ruta in sorted(opciones.salida.glob("*.json")):
        print("\n".join(_resumen(json.loads(ruta.read_text(encoding="utf-8")))))
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
