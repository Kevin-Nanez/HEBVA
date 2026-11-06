"""Mide las palancas de eficiencia una por una (T6.2, C6.2, C5.3): apaga una al vuelo y corre eval.correr, sin tocar el agente.

Uso, desde la raíz del repositorio. Los dos primeros gastan la llave, el tercero no:

    python -m eval.medir_palancas correr <variante> --modelo <id> --corridas 2
    python -m eval.medir_palancas cobertura --modelo <id> --pasadas 2 --salida <ruta.json>
    python -m eval.medir_palancas tabla --base <reporte.json> --repetida <reporte.json> --variante "1 caché=<reporte.json>" ...

Cada variante apaga una sola cosa, con el mismo camino de eval.correr, y su reporte lleva el nombre de la variante:

- interfaz: nada; es la base con todas las palancas puestas, sobre el núcleo.
- sin_cache (palanca 1): AjustesDelModelo(cache_claude=False).
- esfuerzo_medium (palanca 4): AjustesDelModelo(esfuerzo_claude="medium"), el valor por omisión de Haiku 5.5.
- salida_larga (palanca 3): el modelo recibe texto_registro(), el JSON con espacios, campos vacíos y la consulta SQL. Las
  claves cortas y las cifras formateadas no se pueden apagar: salen de cada herramienta.
- sin_tope_solicitudes (palanca 6): sin tope de solicitudes. Siguen los 60 s, las 12 herramientas y max_tokens, y por eso
  un turno sigue acotado.
- base_conversaciones y historial_sin_tope (palanca 7): las 5 conversaciones del banco, porque el núcleo casi no llega al
  tope. La primera con el tope de D10-A y la segunda sin el de vueltas ni el de tokens por vuelta. El historial sigue
  siendo de texto: el resultado completo de las herramientas nunca viajó en él.

La palanca 5 no pasa por el banco, que no trae ninguna frase de cobertura: `cobertura` contesta unas frases con el manifiesto
y con el modelo, y la resta es lo que ahorra. La palanca 2 no tiene variante: en git no hay una versión larga que medir.

La regla de #metas, al revés porque las palancas ya están puestas: la base es con la palanca y la variante sin ella. La
palanca cumple si la exactitud de la base no queda debajo del rango de la variante, si con la palanca baja al menos una
mediana de tokens, costo o latencia, y si lo que sube con ella no queda fuera de su meta.
"""

from __future__ import annotations

import argparse
import functools
import io
import json
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic_ai.models import Model

from agente import agente, construir, manifiesto, registro
from agente import historial as modulo_historial
from agente.ajustes_modelo import AJUSTES, AjustesDelModelo
from agente.herramientas.comun import Resultado, dumps
from eval import banco, correr, reporte

BLOQUE_CONVERSACION = "conversacion"
SOLICITUDES_SIN_TOPE = 1000
VUELTAS_SIN_TOPE = 1000
TOKENS_SIN_TOPE = 10**6
FRASES_DE_COBERTURA = ("qué sabes", "ayuda", "qué puedes hacer")
CATEGORIA_COBERTURA = "cobertura"

# Las metas de la mediana en #metas, con la medida que las compara.
METAS = {"entrada": 4800, "precio_completo": 1000, "salida": 450, "costo": 0.0004, "latencia": 5000}
NOMBRES_DE_MEDIDA = {
    "entrada": "tokens de entrada",
    "precio_completo": "entrada a precio completo",
    "salida": "tokens de salida",
    "costo": "costo",
    "latencia": "latencia",
}

Parches = list[tuple[Any, dict[str, Any]]]


def _historial_sin_tope() -> modulo_historial.Historial:
    return modulo_historial.Historial(capacidad=VUELTAS_SIN_TOPE)


def _parches_salida_larga() -> Parches:
    return [(Resultado, {"texto_modelo": Resultado.texto_registro})]


def _parches_sin_tope_solicitudes() -> Parches:
    topes = replace(agente.TOPES, solicitudes=SOLICITUDES_SIN_TOPE)
    return [(agente, {"responder": functools.partial(agente.responder, topes=topes)})]


def _parches_historial_sin_tope() -> Parches:
    sin_tope = dict.fromkeys(("TOKENS_POR_VUELTA", "TOKENS_PREGUNTA", "TOKENS_CONSULTAS"), TOKENS_SIN_TOPE)
    return [(correr, {"Historial": _historial_sin_tope}), (modulo_historial, sin_tope)]


def _sin_parches() -> Parches:
    return []


@dataclass(frozen=True)
class Variante:
    """Una palanca apagada: los ajustes del modelo, lo que se cambia al vuelo y sobre qué prompts corre."""

    palanca: int | None
    ajustes: AjustesDelModelo = AJUSTES
    parches: Callable[[], Parches] = _sin_parches
    conversaciones: bool = False


VARIANTES = {
    "interfaz": Variante(None),
    "sin_cache": Variante(1, ajustes=AjustesDelModelo(cache_claude=False)),
    "salida_larga": Variante(3, parches=_parches_salida_larga),
    "esfuerzo_medium": Variante(4, ajustes=AjustesDelModelo(esfuerzo_claude="medium")),
    "sin_tope_solicitudes": Variante(6, parches=_parches_sin_tope_solicitudes),
    "base_conversaciones": Variante(None, conversaciones=True),
    "historial_sin_tope": Variante(7, parches=_parches_historial_sin_tope, conversaciones=True),
}


@contextmanager
def _con_atributos(parches: Parches) -> Iterator[None]:
    antes = [(objeto, nombre, getattr(objeto, nombre)) for objeto, valores in parches for nombre in valores]
    for objeto, valores in parches:
        for nombre, valor in valores.items():
            setattr(objeto, nombre, valor)
    try:
        yield
    finally:
        for objeto, nombre, valor in antes:
            setattr(objeto, nombre, valor)


def _solo_conversaciones(prompts: Sequence[banco.Prompt], *, completo: bool = False, variantes: bool = False) -> tuple[banco.Prompt, ...]:
    return tuple(prompt for prompt in prompts if prompt.bloque == BLOQUE_CONVERSACION)


@contextmanager
def apagada(nombre: str) -> Iterator[Variante]:
    """Mientras dure, la palanca de esta variante está apagada; al salir, aun con una excepción, todo queda como estaba."""
    variante = VARIANTES[nombre]
    parches = variante.parches()
    if variante.conversaciones:
        parches.append((banco, {"seleccionar": _solo_conversaciones}))
    parches.append((correr, {"CONFIGURACIONES": {**correr.CONFIGURACIONES, nombre: variante.ajustes}}))
    with _con_atributos(parches):
        yield variante


def correr_variante(nombre: str, argumentos: Sequence[str] = (), **opciones: Any) -> int:
    """`python -m eval.correr` con esta variante. Los demás argumentos y opciones son los de correr.main()."""
    with apagada(nombre):
        return correr.main(["--configuracion", nombre, *argumentos], **opciones)


# ------------------------------------------------------------------ palanca 5: respuestas sin modelo


def turnos_del_manifiesto_en_el_banco(ruta_banco: Path = banco.BANCO) -> int:
    """Cuántos turnos del banco completo contesta el manifiesto: lo que la palanca 5 ahorra en el set."""
    pasos = (paso for prompt in banco.leer_banco(ruta_banco) for paso in prompt.pasos)
    return sum(manifiesto.pide_la_cobertura(paso.texto) for paso in pasos)


def _fila_de_cobertura(frase: str, camino: str, respuesta: agente.Respuesta, inicio: float) -> dict[str, Any]:
    """Sin el texto de la respuesta: solo lo que se mide."""
    return {
        "frase": frase,
        "camino": camino,
        "estado": respuesta.estado,
        "latencia_ms": round((time.monotonic() - inicio) * 1000),
        **correr._uso(respuesta),
    }


def _medidas_de_cobertura(filas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    costos = [float(fila["costo_usd"]) for fila in filas if fila["costo_usd"] is not None]
    return {
        "n": len(filas),
        "entrada_tokens": reporte._p50_p90([fila["entrada_tokens"] for fila in filas]),
        "salida_tokens": reporte._p50_p90([fila["salida_tokens"] for fila in filas]),
        "n_llamadas": reporte._p50_p90([fila["n_llamadas"] for fila in filas]),
        "costo_usd": reporte._p50_p90(costos, reporte.DECIMALES_DE_COSTO) if costos else None,
        "latencia_ms": reporte._p50_p90([fila["latencia_ms"] for fila in filas]),
    }


def medir_cobertura(
    modelo: Model | str | None,
    *,
    pasadas: int = 2,
    base: Path = construir.BASE,
    archivo_registro: Path = registro.ARCHIVO,
) -> dict[str, Any]:
    """Cada frase de cobertura por los dos caminos, en sesiones nuevas: el manifiesto usa 0 tokens y el modelo los que mida."""
    comunes = {"base": base, "archivo_registro": archivo_registro, "origen": "evaluacion", "categoria": CATEGORIA_COBERTURA}
    sin_modelo, con_modelo = [], []
    for _ in range(pasadas):
        for frase in FRASES_DE_COBERTURA:
            inicio = time.monotonic()
            respuesta = manifiesto.responder(frase, modulo_historial.Historial(), **comunes)
            sin_modelo.append(_fila_de_cobertura(frase, "manifiesto", respuesta, inicio))
            inicio = time.monotonic()
            respuesta = agente.responder(frase, modulo_historial.Historial(), modelo=modelo, **comunes)
            con_modelo.append(_fila_de_cobertura(frase, "modelo", respuesta, inicio))
    return {
        "turnos_del_manifiesto_en_el_banco": turnos_del_manifiesto_en_el_banco(),
        "turnos_sin_modelo": {
            "n": len(sin_modelo),
            "entrada_tokens": sum(fila["entrada_tokens"] for fila in sin_modelo),
            "salida_tokens": sum(fila["salida_tokens"] for fila in sin_modelo),
            "n_llamadas": sum(fila["n_llamadas"] for fila in sin_modelo),
            "latencia_ms": reporte._p50_p90([fila["latencia_ms"] for fila in sin_modelo]),
        },
        "turnos_con_modelo": _medidas_de_cobertura(con_modelo),
        "costo_con_modelo_usd": sum(float(fila["costo_usd"] or 0) for fila in con_modelo),
        "detalle": [*sin_modelo, *con_modelo],
    }


# ------------------------------------------------------------------ la tabla y la regla


def _aciertos(datos: Mapping[str, Any], clave: str) -> list[int]:
    return [fila["aciertos"] for fila in datos[clave]["alcance"]["por_corrida"]]


def medidas_de(datos: Mapping[str, Any]) -> dict[str, float]:
    """Las medianas y los p90 del reporte, más la entrada a precio completo y los topes, que salen de sus turnos."""
    eficiencia = datos["eficiencia_total"]
    entrada, salida = eficiencia[reporte.CAMPO_DE_ENTRADA], eficiencia[reporte.CAMPO_DE_SALIDA]
    turnos = [turno for turno in datos["turnos"] if turno["estado"] != "excepcion"]
    return {
        "entrada": entrada["mediana"],
        "entrada_p90": entrada["p90"],
        "precio_completo": reporte.percentil([turno["entrada_no_cacheada"] for turno in turnos], 50),
        "salida": salida["mediana"],
        "salida_p90": salida["p90"],
        "costo": eficiencia["costo_usd"]["mediana"],
        "costo_p90": eficiencia["costo_usd"]["p90"],
        "costo_total": float(datos["uso_total"]["costo_usd"]),
        "latencia": eficiencia["latencia_ms"]["p50"],
        "latencia_p90": eficiencia["latencia_ms"]["p90"],
        "topes": sum(turno["tope"] is not None for turno in turnos),
        "turnos": len(turnos),
    }


def _rangos_se_tocan_o_arriba(con_ella: Sequence[int], sin_ella: Sequence[int]) -> bool:
    return max(con_ella) >= min(sin_ella)


def ruido_de(base: Mapping[str, Any], repetida: Mapping[str, Any]) -> dict[str, float]:
    """Cuánto difiere cada mediana entre dos reportes del mismo código: lo que aplicar_regla() no cuenta como efecto."""
    medidas_base, medidas_repetida = medidas_de(base), medidas_de(repetida)
    return {medida: abs(medidas_base[medida] - medidas_repetida[medida]) for medida in METAS}


def aplicar_regla(con_ella: Mapping[str, Any], sin_ella: Mapping[str, Any], ruido: Mapping[str, float] | None = None) -> dict[str, Any]:
    """La regla de #metas: con_ella es la base y sin_ella la variante. Compara medianas, como las metas.

    Sin ruido, cualquier diferencia cuenta. Con ruido, solo la que lo pasa: es la lectura que sirve con 2 corridas de 30.
    """
    medidas_con, medidas_sin = medidas_de(con_ella), medidas_de(sin_ella)
    margen = ruido or dict.fromkeys(METAS, 0)
    baja = [medida for medida in METAS if medidas_sin[medida] - medidas_con[medida] > margen[medida]]
    sube = [medida for medida in METAS if medidas_con[medida] - medidas_sin[medida] > margen[medida]]
    fuera_de_meta = [medida for medida in sube if medidas_con[medida] > METAS[medida]]
    con_juez = _rangos_se_tocan_o_arriba(_aciertos(con_ella, "acierto"), _aciertos(sin_ella, "acierto"))
    con_reglas = _rangos_se_tocan_o_arriba(_aciertos(con_ella, "acierto_reglas"), _aciertos(sin_ella, "acierto_reglas"))
    cumple = all((con_juez, con_reglas, baja, not fuera_de_meta))
    return {
        "exactitud_con_juez": con_juez,
        "exactitud_con_reglas": con_reglas,
        "baja": baja,
        "sube_fuera_de_meta": fuera_de_meta,
        "cumple": cumple,
    }


def _numero(valor: float, decimales: int = 1) -> str:
    """Con coma de miles y punto decimal. Un decimal se quita si es 0, porque los tokens casi siempre son enteros."""
    texto = f"{valor:,.{decimales}f}"
    return texto.removesuffix(".0")


def _con_delta(valor: float, referencia: float | None, decimales: int = 1, escala: float = 1) -> str:
    texto = _numero(valor / escala, decimales)
    if referencia is None:
        return texto
    delta = (valor - referencia) / escala
    return f"{texto} ({'+' if delta >= 0 else '-'}{_numero(abs(delta), decimales)})"


def _renglon(nombre: str, datos: Mapping[str, Any], referencia: Mapping[str, float] | None) -> str:
    medidas = medidas_de(datos)

    def celda(clave: str, decimales: int = 1, escala: float = 1) -> str:
        return _con_delta(medidas[clave], referencia[clave] if referencia else None, decimales, escala)

    celdas = [
        nombre,
        " y ".join(map(str, _aciertos(datos, "acierto_reglas"))),
        " y ".join(map(str, _aciertos(datos, "acierto"))),
        celda("entrada"),
        celda("entrada_p90"),
        celda("precio_completo"),
        celda("salida"),
        celda("salida_p90"),
        celda("costo", 6),
        celda("costo_p90", 6),
        celda("costo_total", 6),
        celda("latencia", 2, 1000),
        celda("latencia_p90", 2, 1000),
        f"{medidas['topes']:,.0f} de {medidas['turnos']:,.0f}",
    ]
    return f"| {' | '.join(celdas)} |"


ENCABEZADOS = (
    "Variante",
    "Con reglas, por corrida",
    "Con juez, por corrida",
    "Entrada p50",
    "Entrada p90",
    "A precio completo p50",
    "Salida p50",
    "Salida p90",
    "Costo por turno p50, USD",
    "Costo por turno p90, USD",
    "Costo de los turnos, USD",
    "Latencia p50, s",
    "Latencia p90, s",
    "Turnos en tope",
)


def tabla_markdown(base: Mapping[str, Any], variantes: Sequence[tuple[str, Mapping[str, Any]]], nombre_base: str = "Base") -> str:
    """Una fila por reporte. Entre paréntesis, la variante menos la base: un delta positivo es lo que la palanca ahorra."""
    referencia = medidas_de(base)
    lineas = [f"| {' | '.join(ENCABEZADOS)} |", f"|{'---|' * len(ENCABEZADOS)}", _renglon(nombre_base, base, None)]
    lineas += [_renglon(nombre, datos, referencia) for nombre, datos in variantes]
    return "\n".join(lineas)


def _leer(ruta: Path) -> dict[str, Any]:
    return json.loads(Path(ruta).read_text(encoding="utf-8"))


def _tabla_del_comando(opciones: argparse.Namespace) -> int:
    base = _leer(opciones.base)
    variantes = []
    for texto in opciones.variante:
        nombre, _, ruta = texto.partition("=")
        variantes.append((nombre, _leer(Path(ruta))))
    print(tabla_markdown(base, variantes))
    print()
    ruido = ruido_de(base, _leer(opciones.repetida)) if opciones.repetida else None
    for nombre, datos in variantes:
        print(f"{nombre}: {dumps(aplicar_regla(base, datos), compacto=True)}")
        if ruido:
            print(f"{nombre}, sobre el ruido: {dumps(aplicar_regla(base, datos, ruido), compacto=True)}")
    return correr.SALIDA_BIEN


def _cobertura_del_comando(opciones: argparse.Namespace) -> int:
    medida = medir_cobertura(opciones.modelo, pasadas=opciones.pasadas)
    opciones.salida.write_text(dumps(medida) + "\n", encoding="utf-8", newline="\n")
    print(f"Turnos del banco que contesta el manifiesto: {medida['turnos_del_manifiesto_en_el_banco']:,}.")
    print(f"Con modelo: {medida['turnos_con_modelo']['n']:,} turnos, {medida['costo_con_modelo_usd']:.6f} USD. Archivo: {opciones.salida}")
    return correr.SALIDA_BIEN


def _lector() -> argparse.ArgumentParser:
    lector = argparse.ArgumentParser(prog="python -m eval.medir_palancas", description="Mide las palancas una por una (T6.2).")
    comandos = lector.add_subparsers(dest="comando", required=True)
    una = comandos.add_parser("correr", help="corre una variante: los demás argumentos van a eval.correr; gasta la llave")
    una.add_argument("variante", choices=sorted(VARIANTES))
    cobertura = comandos.add_parser("cobertura", help="las frases de cobertura con el manifiesto y con el modelo; gasta la llave")
    cobertura.add_argument("--modelo", required=True)
    cobertura.add_argument("--pasadas", type=correr._entero_positivo, default=2)
    cobertura.add_argument("--salida", type=Path, required=True)
    tabla = comandos.add_parser("tabla", help="la tabla y la regla, con los JSON de los reportes; no gasta la llave")
    tabla.add_argument("--base", type=Path, required=True)
    tabla.add_argument("--repetida", type=Path, help="la base corrida otra vez: da el ruido, y la regla sale también sobre él")
    tabla.add_argument("--variante", action="append", default=[], metavar="NOMBRE=RUTA")
    return lector


def main(argumentos: Sequence[str] | None = None) -> int:
    for salida in (sys.stdout, sys.stderr):
        if isinstance(salida, io.TextIOWrapper):
            salida.reconfigure(encoding="utf-8")
    opciones, resto = _lector().parse_known_args(argumentos)
    if opciones.comando == "correr":
        return correr_variante(opciones.variante, resto)
    if resto:
        _lector().error(f"argumentos desconocidos: {' '.join(resto)}")
    if opciones.comando == "cobertura":
        return _cobertura_del_comando(opciones)
    return _tabla_del_comando(opciones)


if __name__ == "__main__":
    raise SystemExit(main())
