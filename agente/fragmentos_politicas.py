"""Fragmentos de política con documento, versión, sección y página (T1.5).

Parte cada archivo revisado de politicas_md/ en un fragmento por sección de primer nivel (##) y uno
por pregunta de la FAQ (### P<n>.), la regla de la línea base §4: 62 fragmentos en los 4
documentos. Las subsecciones (### 6.2) quedan dentro del fragmento de su sección, con su página
en subsecciones, para citar "MAN-OPS-007 §6.2, p. 4" sin partir la tabla de la sección. Los
encabezados de bloque de la FAQ (2. Operación diaria, ...) no tienen texto propio: no dan
fragmento y quedan como bloque de sus preguntas.

El texto del fragmento conserva el Markdown (títulos, viñetas, tabla y tripletas) sin las marcas de
página. La huella es el sha256 de los fragmentos serializados en JSON canónico: dos corridas sobre
los mismos PDF dan la misma.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

import yaml

TITULO = re.compile(r"^(#{2,3}) (.+?) \{p=(\d+)\}$")
NUMERO = re.compile(r"^(P\d+|\d+(?:\.\d+)*)\.?\s+(.+)$")
PAGINA = re.compile(r"^<!-- pagina (\d+) -->$")
PREGUNTA = re.compile(r"^P\d+$")
SEPARADOR_DE_CELDA = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True)
class Subseccion:
    seccion: str
    titulo: str
    pagina: int


@dataclass
class Fragmento:
    id: str
    documento: str
    titulo_documento: str
    version: str
    vigencia: str
    archivo: str
    seccion: str
    titulo_seccion: str
    bloque: str | None
    pagina: int
    pagina_fin: int
    orden: int
    subsecciones: list[Subseccion] = field(default_factory=list)
    lineas: list[str] = field(default_factory=list, repr=False)

    @property
    def texto(self) -> str:
        return "\n".join(self.lineas).strip()


def separar_encabezado(texto: str) -> tuple[dict, str]:
    """Separa el encabezado YAML del cuerpo de un archivo de politicas_md/."""
    if not texto.startswith("---\n"):
        raise ValueError("El archivo no abre con un encabezado YAML (---)")
    fin = texto.index("\n---\n", 4)
    encabezado = yaml.safe_load(texto[4:fin])
    return encabezado, texto[fin + len("\n---\n"):]


def _numero(titulo: str) -> tuple[str, str]:
    coincidencia = NUMERO.match(titulo)
    if not coincidencia:
        raise ValueError(f"Título sin número de sección: {titulo}")
    return coincidencia.group(1), coincidencia.group(2)


def fragmentar(texto: str) -> list[Fragmento]:
    """Fragmentos de un archivo de politicas_md/ (encabezado YAML y cuerpo Markdown)."""
    meta, cuerpo = separar_encabezado(texto)
    documento = str(meta["codigo"])
    fragmentos: list[Fragmento] = []
    actual: Fragmento | None = None
    bloque: str | None = None
    pagina = 1

    def nuevo(seccion: str, titulo: str, pagina_inicio: int, bloque_padre: str | None) -> Fragmento:
        return Fragmento(
            id=f"{documento}#{seccion}",
            documento=documento,
            titulo_documento=str(meta["titulo"]),
            version=str(meta["version"]),
            vigencia=str(meta["vigencia"]),
            archivo=str(meta["archivo"]),
            seccion=seccion,
            titulo_seccion=titulo,
            bloque=bloque_padre,
            pagina=pagina_inicio,
            pagina_fin=pagina_inicio,
            orden=0,
        )

    def cerrar() -> None:
        # Un fragmento sin más texto que su título (bloque de la FAQ) no se guarda.
        if actual is not None and any(linea.strip() for linea in actual.lineas[1:]):
            actual.orden = len(fragmentos) + 1
            fragmentos.append(actual)

    for linea in cuerpo.splitlines():
        marca = PAGINA.match(linea)
        if marca:
            pagina = int(marca.group(1))
            continue
        titulo = TITULO.match(linea)
        if titulo:
            nivel, texto_titulo, pagina_titulo = titulo.group(1), titulo.group(2), int(titulo.group(3))
            if pagina_titulo != pagina:
                raise ValueError(f"{documento}: el título «{texto_titulo}» dice p={pagina_titulo} en la página {pagina}")
            seccion, nombre = _numero(texto_titulo)
            if nivel == "##":
                cerrar()
                actual = nuevo(seccion, nombre, pagina, None)
                bloque = nombre
            elif PREGUNTA.match(seccion):
                cerrar()
                actual = nuevo(seccion, nombre, pagina, bloque)
            else:
                actual.subsecciones.append(Subseccion(seccion, nombre, pagina))
            actual.lineas.append(f"{nivel} {texto_titulo}")
            continue
        if actual is None:
            if linea.strip():
                raise ValueError(f"{documento}: texto antes de la primera sección: {linea[:60]}")
            continue
        actual.lineas.append(linea)
        if linea.strip():
            actual.pagina_fin = pagina
    cerrar()
    return fragmentos


def como_fila(fragmento: Fragmento) -> dict:
    """Fragmento como diccionario plano, en el orden de las columnas de la tabla fragmentos."""
    fila = asdict(fragmento)
    del fila["lineas"]
    fila["texto"] = fragmento.texto
    return fila


def huella(filas: list[dict]) -> str:
    """sha256 de las filas en JSON canónico (claves ordenadas, UTF-8, fechas como AAAA-MM-DD)."""
    canonico = json.dumps(filas, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def tablas(texto: str) -> list[list[list[str]]]:
    """Tablas Markdown de un texto, como listas de filas (sin la línea separadora ---)."""
    resultado: list[list[list[str]]] = []
    actual: list[list[str]] = []
    for linea in texto.splitlines() + [""]:
        if linea.startswith("|"):
            celdas = [c.strip().replace("\\|", "|") for c in SEPARADOR_DE_CELDA.split(linea.strip())[1:-1]]
            if not all(re.fullmatch(r"-+", c) for c in celdas):
                actual.append(celdas)
            continue
        if actual:
            resultado.append(actual)
            actual = []
    return resultado
