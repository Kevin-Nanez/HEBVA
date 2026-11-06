"""Parte el texto de una respuesta en párrafos, listas y tablas, para que la pantalla los dibuje sin interpretar Markdown (K23).

El modelo escribe tablas y viñetas en Markdown porque es lo que sabe escribir, pero la interfaz nunca lo interpreta: una
imagen remota en Markdown puede sacar datos en su URL. Aquí solo se reconoce la forma de los renglones; el contenido de cada
celda y de cada elemento queda como texto, con sus imágenes, enlaces y HTML a la vista.

Ningún dato se pierde ni cambia, de modo que lo que se muestra es lo que revisó el verificador. Lo que no cuadra (una tabla
sin separador, una fila con otro número de celdas) se queda como texto, tal como lo escribió el modelo.

Este módulo no conoce Streamlit: lo usan la pantalla (app.py) y el conteo de líneas de contexto (eval/evaluadores.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VINETA = "• "

_VINETA = re.compile(r"[ \t]*[-*•][ \t]+(?P<texto>\S.*)")
_NUMERADA = re.compile(r"[ \t]*(?P<numero>[0-9]{1,2}[.)])[ \t]+(?P<texto>\S.*)")
_CONTINUACION = re.compile(r"[ \t]{2,}\S.*")
_CELDA_SEPARADORA = re.compile(r":?-+:?")
_BARRA_SIN_ESCAPAR = re.compile(r"(?<!\\)\|")
_CIFRA = re.compile(r"[-+]?\$?[0-9][0-9,]*(?:\.[0-9]+)?(?:\s?(?:%|MXN))?")


@dataclass(frozen=True)
class Parrafo:
    """Texto corrido, con sus saltos de línea tal como vienen."""

    texto: str


@dataclass(frozen=True)
class Lista:
    """Los elementos de una lista, cada uno con su marca: la viñeta o el número que traía."""

    elementos: tuple[str, ...]

    @property
    def texto(self) -> str:
        return "\n".join(self.elementos)


@dataclass(frozen=True)
class Tabla:
    """Una tabla: todas las filas traen tantas celdas como encabezados."""

    encabezados: tuple[str, ...]
    filas: tuple[tuple[str, ...], ...]

    @property
    def texto(self) -> str:
        return "\n".join(" | ".join(fila) for fila in (self.encabezados, *self.filas))

    @property
    def columnas_de_cifras(self) -> tuple[bool, ...]:
        """Por columna, si todas sus celdas con algo escrito son una cifra: esas van alineadas a la derecha."""
        return tuple(self._es_de_cifras(columna) for columna in range(len(self.encabezados)))

    def _es_de_cifras(self, columna: int) -> bool:
        llenas = [fila[columna] for fila in self.filas if fila[columna]]
        return bool(llenas) and all(_CIFRA.fullmatch(celda) for celda in llenas)


Bloque = Parrafo | Lista | Tabla


def _celdas(linea: str) -> tuple[str, ...] | None:
    """Las celdas de un renglón de tabla, o None si no trae ninguna barra. Las barras de las orillas son opcionales."""
    renglon = linea.strip()
    if "|" not in renglon:
        return None
    if renglon.startswith("|"):
        renglon = renglon[1:]
    if renglon.endswith("|") and not renglon.endswith("\\|"):
        renglon = renglon[:-1]
    return tuple(celda.strip().replace("\\|", "|") for celda in _BARRA_SIN_ESCAPAR.split(renglon))


def _es_separador(linea: str, columnas: int) -> bool:
    celdas = _celdas(linea)
    if celdas is None or len(celdas) != columnas:
        return False
    return all(_CELDA_SEPARADORA.fullmatch(celda) for celda in celdas)


def _leer_tabla(lineas: list[str], inicio: int) -> tuple[Tabla, int] | None:
    """La tabla que empieza en ese renglón y el renglón donde sigue el texto; None si ahí no hay una tabla completa."""
    encabezados = _celdas(lineas[inicio])
    if encabezados is None or inicio + 2 >= len(lineas):
        return None
    if not _es_separador(lineas[inicio + 1], len(encabezados)):
        return None
    filas = []
    siguiente = inicio + 2
    while siguiente < len(lineas):
        celdas = _celdas(lineas[siguiente])
        if celdas is None or len(celdas) != len(encabezados):
            break
        filas.append(celdas)
        siguiente += 1
    if not filas:
        return None
    return Tabla(encabezados, tuple(filas)), siguiente


def _elemento(linea: str) -> str | None:
    """El elemento de lista de un renglón, con su marca, o None si el renglón no empieza uno."""
    renglon = linea.rstrip()
    vineta = _VINETA.fullmatch(renglon)
    if vineta:
        return f"{VINETA}{vineta['texto']}"
    numerada = _NUMERADA.fullmatch(renglon)
    if numerada:
        return f"{numerada['numero']} {numerada['texto']}"
    return None


def _leer_lista(lineas: list[str], inicio: int) -> tuple[Lista, int] | None:
    """La lista que empieza en ese renglón y el renglón donde sigue el texto. Un renglón con sangría sigue al elemento."""
    primero = _elemento(lineas[inicio])
    if primero is None:
        return None
    elementos = [primero]
    siguiente = inicio + 1
    while siguiente < len(lineas):
        linea = lineas[siguiente].rstrip()
        elemento = _elemento(linea)
        if elemento is not None:
            elementos.append(elemento)
        elif _CONTINUACION.fullmatch(linea):
            elementos[-1] = f"{elementos[-1]}\n{linea}"
        else:
            break
        siguiente += 1
    return Lista(tuple(elementos)), siguiente


def _cerrar_parrafo(renglones: list[str], bloques: list[Bloque]) -> None:
    texto = "\n".join(renglones).strip("\n")
    if texto.strip():
        bloques.append(Parrafo(texto))
    renglones.clear()


def _leer_bloque(lineas: list[str], posicion: int, listas: bool) -> tuple[Bloque, int] | None:
    tabla = _leer_tabla(lineas, posicion)
    if tabla is not None or not listas:
        return tabla
    return _leer_lista(lineas, posicion)


def bloques_de(texto: str, listas: bool = True) -> tuple[Bloque, ...]:
    """Los bloques del texto, en orden. Un texto sin tablas ni listas es un solo párrafo igual al original.

    Con listas=False solo se parten las tablas: las viñetas se quedan en el párrafo tal como vienen, con su guion. Así
    dibuja la pantalla el manifiesto de cobertura (agente/manifiesto.py), que el código arma y no el modelo.
    """
    lineas = texto.split("\n")
    bloques: list[Bloque] = []
    renglones: list[str] = []
    posicion = 0
    while posicion < len(lineas):
        leido = _leer_bloque(lineas, posicion, listas)
        if leido is None:
            renglones.append(lineas[posicion])
            posicion += 1
            continue
        _cerrar_parrafo(renglones, bloques)
        bloque, posicion = leido
        bloques.append(bloque)
    _cerrar_parrafo(renglones, bloques)
    return tuple(bloques)
