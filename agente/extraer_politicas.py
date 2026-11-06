"""Extrae los PDF de políticas a Markdown con pdfplumber (T1.5).

Uso: python -m agente.extraer_politicas [--salida build/politicas_md_borrador]

Escribe un borrador por documento. El borrador se revisa contra el PDF, tabla por tabla, y la
copia revisada vive en politicas_md/<código>.md: es la que fragmenta agente/construir.py, que
antes vuelve a extraer cada PDF y se detiene si el cuerpo de la copia revisada no es idéntico a la
extracción. Así la ingesta corre desde los PDF y una corrección hecha a mano en politicas_md/ no
pasa sin quedar declarada aquí (ver CORRECCIONES_DE_ENCABEZADO).

Cómo se lee cada PDF. Los 4 comparten maqueta, y la extracción se apoya en la fuente de cada
renglón y no en expresiones sobre el texto:

- Helvetica de 8 pt: encabezado y pie de página; se descartan.
- Negrita de 20 pt: título del documento. Lo que va antes de la primera sección (código, tabla de
  metadatos y la leyenda de material ficticio) no entra al cuerpo; la tabla de metadatos da la
  versión, la vigencia y el área.
- Negrita de 13 pt: sección (## en Markdown). Negrita de 11 pt: subsección o pregunta de la FAQ
  (###); un renglón de 11 pt que no empieza con número es la segunda línea del título anterior.
- Primer carácter de 9.5 pt en el margen: inicia una viñeta o un paso numerado.
- Tablas: pdfplumber.find_tables() con su configuración por omisión (líneas dibujadas). Cada tabla
  se escribe como tabla Markdown y además como tripletas "fila, columna = valor" (patrón de
  docling), para que la búsqueda recupere una celda junto con su encabezado y el de su fila.

Cada página abre con <!-- pagina N --> y cada título lleva {p=N}: de ahí salen las páginas de
los fragmentos.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

RAIZ = Path(__file__).resolve().parent.parent

TAMANO_TITULO = 20.0
TAMANO_SECCION = 13.0
TAMANO_SUBSECCION = 11.0
TAMANO_INICIO_DE_LISTA = 9.5
TAMANO_MAXIMO_DE_ENCABEZADO_Y_PIE = 8.0
# Separación vertical, en puntos, a partir de la cual dos renglones de texto son párrafos distintos.
# Dentro de un párrafo la separación es de 4 pt; entre párrafos, de 14 pt o más.
SEPARACION_DE_PARRAFO = 7.0
# Margen izquierdo del texto (68.4 pt en los 4 PDF); las líneas de continuación de una viñeta van
# en 84.4 pt.
MARGEN_IZQUIERDO = 70.0

VINETA = "(cid:127)"
NUMERO_DE_SECCION = re.compile(r"^(?:\d+(?:\.\d+)*\.?|P\d+\.)\s")
PASO = re.compile(r"^(\d+)\s+(.+)$")

# Encabezados de columna que el PDF parte a media palabra por lo angosto de la columna (se ve igual
# en la página impresa: "Clasificaci" arriba de "ón"). pdfplumber une los renglones de una celda
# con un espacio, así que estas son las únicas correcciones a mano de la extracción; se revisaron
# contra la página renderizada el 2026-10-31.
CORRECCIONES_DE_ENCABEZADO = {
    "Clasificaci ón": "Clasificación",  # MAN-OPS-007 §6.2, p. 4
    "Versi ón": "Versión",  # control de cambios de MAN-OPS-007, PRO-SAC-021, POL-OPS-014 y FAQ-OPS-001
    "Priori dad": "Prioridad",  # FAQ-OPS-001 P20, p. 5
}

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

CAMPOS_DE_METADATOS = {
    "Código de documento": "codigo",
    "Versión": "version",
    "Fecha de vigencia": "vigencia",
    "Área responsable": "area",
}


@dataclass(frozen=True)
class DocumentoExtraido:
    """Un PDF extraído: metadatos de su portada y cuerpo en Markdown."""

    codigo: str
    titulo: str
    version: str
    vigencia: str  # AAAA-MM-DD
    area: str
    archivo: str
    markdown: str

    def metadatos(self) -> dict[str, str]:
        return {
            "codigo": self.codigo,
            "titulo": self.titulo,
            "version": self.version,
            "vigencia": self.vigencia,
            "area": self.area,
            "archivo": self.archivo,
        }


def _celda(texto: str | None) -> str:
    return re.sub(r"\s+", " ", (texto or "").replace("\n", " ")).strip()


def _escapar(texto: str) -> str:
    return texto.replace("|", "\\|")


def tabla_markdown(filas: list[list[str | None]]) -> str:
    """Tabla Markdown seguida de una tripleta por celda: «columna 1 «fila», columna = valor»."""
    limpias = [[_celda(c) for c in fila] for fila in filas]
    encabezado = [CORRECCIONES_DE_ENCABEZADO.get(c, c) for c in limpias[0]]
    cuerpo = limpias[1:]
    lineas = ["| " + " | ".join(_escapar(c) for c in encabezado) + " |", "|" + "---|" * len(encabezado)]
    lineas += ["| " + " | ".join(_escapar(c) for c in fila) + " |" for fila in cuerpo]
    lineas.append("")
    for fila in cuerpo:
        for columna, valor in zip(encabezado[1:], fila[1:]):
            lineas.append(f"- {encabezado[0]} «{fila[0]}», {columna} = {valor}")
    return "\n".join(lineas)


def _fecha_iso(texto: str) -> str:
    dia, mes, anio = re.fullmatch(r"(\d{1,2}) de (\w+) de (\d{4})", texto).groups()
    return f"{int(anio):04d}-{MESES[mes.lower()]:02d}-{int(dia):02d}"


def _metadatos_de_portada(filas: list[list[str | None]]) -> dict[str, str]:
    metadatos = {}
    for clave, valor in ((_celda(a), _celda(b)) for a, b in filas):
        campo = CAMPOS_DE_METADATOS.get(clave)
        if campo:
            metadatos[campo] = valor
    metadatos["vigencia"] = _fecha_iso(metadatos["vigencia"])
    return metadatos


def _dentro(linea: dict, caja: tuple[float, float, float, float]) -> bool:
    _, arriba, _, abajo = caja
    return linea["top"] >= arriba - 1 and linea["bottom"] <= abajo + 1


class _Escritor:
    """Arma el Markdown renglón por renglón a partir de los elementos de cada página."""

    def __init__(self) -> None:
        self.bloques: list[str] = []
        self.abierto = False  # hay un párrafo o una viñeta al que se pueden unir renglones
        self.en_lista = False
        self.ultimo_abajo: float | None = None
        self.titulo_pendiente = False

    def cerrar(self) -> None:
        self.abierto = False
        self.en_lista = False
        self.titulo_pendiente = False
        self.ultimo_abajo = None

    def pagina(self, numero: int) -> None:
        self.cerrar()
        self.bloques.append(f"<!-- pagina {numero} -->")

    def tabla(self, filas: list[list[str | None]]) -> None:
        self.cerrar()
        self.bloques.append("\n" + tabla_markdown(filas) + "\n")

    def titulo(self, nivel: str, texto: str, pagina: int, continua: bool) -> None:
        if continua and self.titulo_pendiente:
            anterior = self.bloques.pop()
            texto = anterior.split(" ", 1)[1].rsplit(" {p=", 1)[0] + " " + texto
        else:
            self.cerrar()
        self.bloques.append(f"{nivel} {texto} {{p={pagina}}}")
        self.titulo_pendiente = True

    def texto(self, linea: dict) -> None:
        texto = linea["text"].strip()
        inicio_de_lista = linea["chars"][0]["size"] <= TAMANO_INICIO_DE_LISTA and linea["x0"] < MARGEN_IZQUIERDO
        separado = self.ultimo_abajo is not None and linea["top"] - self.ultimo_abajo > SEPARACION_DE_PARRAFO
        vuelve_al_margen = self.en_lista and linea["x0"] < MARGEN_IZQUIERDO
        self.titulo_pendiente = False
        if inicio_de_lista:
            self.bloques.append(self._elemento_de_lista(texto))
            self.abierto, self.en_lista = True, True
        elif self.abierto and not separado and not vuelve_al_margen:
            self.bloques[-1] += " " + texto
        else:
            self.bloques.append(texto)
            self.abierto, self.en_lista = True, False
        self.ultimo_abajo = linea["bottom"]

    @staticmethod
    def _elemento_de_lista(texto: str) -> str:
        if texto.startswith(VINETA):
            return "- " + texto[len(VINETA):].strip()
        paso = PASO.match(texto)
        if paso:
            return f"{paso.group(1)}. {paso.group(2)}"
        return texto


def extraer_pdf(ruta: Path) -> DocumentoExtraido:
    """Extrae un PDF de política: metadatos de la portada y cuerpo en Markdown."""
    ruta = Path(ruta)
    escritor = _Escritor()
    metadatos: dict[str, str] = {}
    titulo = ""
    en_cuerpo = False
    with pdfplumber.open(ruta) as pdf:
        for numero, pagina in enumerate(pdf.pages, 1):
            tablas = pagina.find_tables()
            elementos: list[tuple[float, int, object]] = []
            for tabla in tablas:
                elementos.append((tabla.bbox[1], 0, tabla))
            for linea in pagina.extract_text_lines():
                if not any(_dentro(linea, tabla.bbox) for tabla in tablas):
                    elementos.append((linea["top"], 1, linea))
            elementos.sort(key=lambda elemento: (elemento[0], elemento[1]))
            if en_cuerpo:
                escritor.pagina(numero)
            for _, tipo, elemento in elementos:
                if tipo == 0:
                    filas = elemento.extract()
                    if not en_cuerpo:
                        metadatos = _metadatos_de_portada(filas)
                    else:
                        escritor.tabla(filas)
                    continue
                primero = elemento["chars"][0]
                tamano = round(primero["size"], 1)
                negrita = "Bold" in primero["fontname"]
                texto = elemento["text"].strip()
                if tamano <= TAMANO_MAXIMO_DE_ENCABEZADO_Y_PIE:
                    continue
                if negrita and tamano == TAMANO_TITULO:
                    titulo = texto
                    continue
                if negrita and tamano == TAMANO_SECCION:
                    if not en_cuerpo:
                        en_cuerpo = True
                        escritor.pagina(numero)
                    escritor.titulo("##", texto, numero, continua=False)
                    continue
                if not en_cuerpo:
                    continue
                if negrita and tamano == TAMANO_SUBSECCION:
                    continua = NUMERO_DE_SECCION.match(texto) is None
                    escritor.titulo("###", texto, numero, continua=continua)
                    continue
                escritor.texto(elemento)
    cuerpo = "\n".join(escritor.bloques).strip() + "\n"
    return DocumentoExtraido(
        codigo=metadatos["codigo"],
        titulo=titulo,
        version=metadatos["version"],
        vigencia=metadatos["vigencia"],
        area=metadatos["area"],
        archivo=ruta.name,
        markdown=cuerpo,
    )


def encabezado_yaml(campos: dict[str, str]) -> str:
    """Encabezado YAML del archivo de politicas_md/, con cada valor entre comillas dobles."""
    lineas = ["---"]
    for clave, valor in campos.items():
        escapado = valor.replace("\\", "\\\\").replace('"', '\\"')
        lineas.append(f'{clave}: "{escapado}"')
    lineas.append("---")
    return "\n".join(lineas) + "\n"


def documento_markdown(documento: DocumentoExtraido, adicionales: dict[str, str] | None = None) -> str:
    """Archivo completo: encabezado con los metadatos (y campos adicionales) y el cuerpo."""
    campos = documento.metadatos() | (adicionales or {})
    return encabezado_yaml(campos) + documento.markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extrae los PDF de políticas a Markdown (borrador).")
    parser.add_argument("--pdf", default=str(RAIZ / "politicas"), help="carpeta con los PDF")
    parser.add_argument("--salida", default=str(RAIZ / "build" / "politicas_md_borrador"))
    args = parser.parse_args(argv)
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    for pdf in sorted(Path(args.pdf).glob("*.pdf")):
        documento = extraer_pdf(pdf)
        destino = salida / f"{documento.codigo}.md"
        destino.write_text(documento_markdown(documento), encoding="utf-8", newline="\n")
        print(destino)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
