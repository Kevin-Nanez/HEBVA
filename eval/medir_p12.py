"""P12 antes y después del retiro en la nota de cobertura (T5.8, segundo cambio): python -m eval.medir_p12 [--pasadas 8].

Corre P12 con Haiku 5.5 en sesiones nuevas, con el código de la carpeta actual, y cuenta en cuántas pasadas la respuesta da el
retiro del 10 de septiembre y el rango de 17.72 a 24.69 días. Se copia a un clon del commit "antes" para medir ese lado. No lee ni
imprime la llave.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

PREGUNTA = "pa cuantos dias me alcanza la leche deslactosada en valle norte"
MODELO = "anthropic:claude-haiku-5-5"
RANGO = ("17.72", "24.69")
RETIRO = "10 de septiembre"


def cuenta(texto: str) -> dict[str, bool]:
    """Qué trae una respuesta: el retiro y los dos extremos del rango."""
    return {"retiro": RETIRO in texto, "rango": all(extremo in texto for extremo in RANGO)}


def main(argumentos: list[str] | None = None) -> int:
    lector = argparse.ArgumentParser(prog="python -m eval.medir_p12")
    lector.add_argument("--pasadas", type=int, default=8)
    opciones = lector.parse_args(argumentos)

    from dotenv import load_dotenv

    load_dotenv(os.environ.get("ARCHIVO_ENV") or None)
    sys.path.insert(0, os.getcwd())
    from agente import agente, registro

    filas = []
    for pasada in range(1, opciones.pasadas + 1):
        respuesta = agente.responder(
            PREGUNTA, modelo=MODELO, base=Path("build/tienda.duckdb"), hoy_real=datetime.date(2026, 11, 3),
            archivo_registro=Path("logs/medir_p12.jsonl"),
        )
        costo = sum((registro.costo_usd(llamada) or Decimal(0) for llamada in respuesta.traza.llamadas), Decimal(0))
        filas.append({"pasada": pasada, "estado": respuesta.estado, **cuenta(respuesta.texto), "costo_usd": str(costo)})
    total = {
        "pasadas": len(filas),
        "ok": sum(fila["estado"] == "ok" for fila in filas),
        "con_retiro": sum(fila["retiro"] for fila in filas),
        "con_rango": sum(fila["rango"] for fila in filas),
        "costo_usd": str(sum((Decimal(fila["costo_usd"]) for fila in filas), Decimal(0))),
    }
    print(json.dumps(total, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
