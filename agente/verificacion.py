"""Verificación de cifras y citas contra las evidencias del turno (T3.4, K16, K17, K18, D16-A).

Compara las cifras y las citas de la respuesta con la pregunta y con las salidas de las
herramientas, comprueba que cada cita exista en el índice y busca datos personales.

Qué cuenta como evidencia (K16). La pregunta del usuario y lo que escribió el código en los resultados de las
herramientas de este turno (Resultado.para_modelo(), sin la consulta SQL), guardado en código como
Respuesta.evidencias. El historial no cuenta: una cifra de una respuesta anterior, o de un resultado de otro turno, no
tiene fuente en este, y el prompt pide volver a llamar a la herramienta.

Lo que vio el modelo y no escribió el código no es fuente de una cifra, una fecha ni una cita (D1-A del plan de huecos de
inyección): la descripción de un ticket, que puede dictar una cifra falsa, y lo que el propio modelo escogió como parámetro
y la herramienta le repite, que le dejaría inventar una cifra y darle fuente. Sí sirve para reconocer un nombre o un texto
entre comillas, porque eso solo dice que el texto existe. Una respuesta que repite una cifra de la descripción pide
corrección, y si insiste, sale la respuesta determinista, que no copia la descripción.

La excepción es D7-B: si el turno solo abrió el detalle de tickets, la respuesta es sobre esos tickets y «la caja 3» es lo que
dicen, así que valen sus números de hasta 3 dígitos y sus códigos. No valen un monto ni un porcentaje, ni nada de un ticket
marcado como sospechoso. Límite: en un turno así, un ticket que el marcador no reconoce puede dictar «hay 300 tickets abiertos».

Qué se revisa en el texto, en este orden; cada tramo reconocido se tapa antes del paso siguiente:

1. Datos personales (K18): teléfonos y correos siempre; un nombre detectado por agente/privacidad.py, salvo
   que venga en la pregunta o en un resultado (el texto de la base ya pasó por el enmascarado de D15-A, así que
   un nombre que trae es un puesto, una tienda o una categoría). El valor nunca se copia a la discrepancia.
2. Citas (K17): un código de documento (POL-OPS-014, o «FAQ» para la FAQ), una sección (§5.2, «sección 5.2»,
   «sección 5.2 de POL-OPS-014», P15 después de la FAQ) y una página (p. 3, pp. 2-3). Una sección sin documento toma
   el último documento nombrado; si ahí no existe y no estaba pegada a él («FAQ-OPS-001 P17 coincide con §9.1»), vale
   la de otro documento del turno. Cada cita tiene que existir en el índice de fragmentos, venir de un fragmento recuperado en el
   turno (o de una cita que trajo un choque) y, si da página, caer en las páginas de la sección. Un documento
   que no está en el índice solo se puede nombrar si un resultado lo nombra (no_incluidos): decir que existe y
   no viene no es fabricación, atribuirle una sección sí (G1). Un texto entre comillas de 3 palabras o más
   tiene que estar en un resultado o en la pregunta.
3. Fechas (AAAA-MM-DD, «31 de agosto de 2026», «1 de junio», «agosto de 2026») y horas (6:00) se comparan como
   fechas y horas, no como números sueltos.
4. Identificadores (T01, SKU-1013, CH-01) tienen que estar escritos igual en un resultado o en la pregunta.
5. Cifras: se normaliza el formato (coma de miles, ceros a la derecha, signo, $ y %) y se compara el valor. Un
   redondeo no es la cifra del resultado. Una coma decimal (32,5) se rechaza con su motivo: el formato del
   sistema es coma de miles y punto decimal. Las viñetas numeradas al inicio de un renglón no son cifras.
   Los documentos escriben muchas cifras con letra: «noventa minutos» en un resultado es la fuente de un «90».

Límites conocidos: las cifras escritas con letra en la respuesta («dos días») no se revisan; un nombre que no sigue a una
frase que presenta a una persona no se detecta (el mismo límite del enmascarado, anotado en el README).

D16-A, que aplica agente/agente.py: ante una discrepancia, un reintento con pedido_de_correccion() y, si
sigue, respuesta_determinista(), armada solo con la evidencia. La Verificacion queda en la traza con las dos
versiones del modelo (enmascaradas si traían un dato personal) y la revisión de cada texto.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from agente import privacidad
from agente.definiciones import MESES_NOMBRE
from agente.herramientas.buscar_politicas import Seccion
from agente.herramientas.comun import Resultado, dumps, fecha_larga
from agente.texto_tickets import sin_formato

TipoDiscrepancia = Literal[
    "cifra", "fecha", "hora", "identificador", "formato",
    "cita", "pagina", "cita_no_recuperada", "texto_citado", "documento",
    "dato_personal", "idioma", "autocorreccion",
]
_TIPOS_CIFRA = frozenset({"cifra", "fecha", "hora", "identificador", "formato"})
_TIPOS_CITA = frozenset({"cita", "pagina", "cita_no_recuperada", "texto_citado", "documento"})

Desenlace = Literal["aprobada", "corregida", "determinista"]
Secciones = Mapping[tuple[str, str], Seccion]

# Comillas de 3 palabras o más: una más corta («Mayor», «2 días») suele ser una etiqueta, no una cita textual.
PALABRAS_MINIMAS_CITA_TEXTUAL = 3
# Cuántos caracteres puede haber entre una sección y su página para que la página sea de esa sección.
DISTANCIA_PAGINA = 60
# Filas por resultado en la respuesta determinista; las demás quedan en la traza.
FILAS_DETERMINISTA = 10
# Campos de texto libre que la respuesta determinista no copia: la descripción de un ticket (G6 prohíbe
# copiarla tal cual), un título que nadie revisó y el texto completo de un fragmento, que va en la traza.
_TEXTO_LIBRE = frozenset({"descripcion", "texto", "titulo_sin_revisar"})
# Lo que un resultado trae y no escribió el código, para no contarlo como fuente de una cifra (D1-A). Los parámetros que
# el modelo escribe a mano y los campos del resultado que los repiten: un error los trae completos, y un resultado de
# tickets repite en su resumen las palabras buscadas.
_TEXTO_DE_TICKET = frozenset({"descripcion", "titulo_sin_revisar"})
_PARAMETROS_DE_TEXTO = ("tiendas", "producto", "categoria", "periodo", "comparar_con", "texto", "consulta")
_FILTROS_DEL_MODELO = frozenset({"texto", "consulta", "busqueda", "periodo", "comparar_con", "ticket_id"})
_FECHAS_DEL_MODELO = frozenset({"desde", "hasta"})
_DETALLE_QUE_REPITE = frozenset({"buscado", "texto"})
# D7-B: lo que vale de la descripción de un ticket si el turno solo abrió su detalle. Ninguna descripción del paquete trae un
# número de 4 dígitos o más, y las cifras que este módulo protege son montos y porcentajes.
_TOPE_DE_NUMERO_DE_TICKET = Decimal(1000)
_MONTO_O_PORCENTAJE = re.compile(r"\$\s*[0-9][0-9,.]*|[0-9][0-9,.]*\s*(?:%|por ciento\b|mxn\b|pesos?\b)", re.IGNORECASE)


# ------------------------------------------------------------------ tipos


@dataclass(frozen=True)
class Discrepancia:
    """Algo del texto que no sale de la evidencia. valor es lo que se encontró, salvo en un dato personal: su tipo."""

    tipo: TipoDiscrepancia
    valor: str
    motivo: str


@dataclass(frozen=True)
class Revision:
    """La revisión de un texto: sus discrepancias, cuántas cifras y citas se revisaron y la pregunta del turno."""

    discrepancias: tuple[Discrepancia, ...]
    cifras_revisadas: int
    citas_revisadas: int
    pregunta: str = field(default="", repr=False, compare=False)

    @property
    def aprobada(self) -> bool:
        return not self.discrepancias

    @property
    def cifras_sin_evidencia(self) -> int:
        return sum(d.tipo in _TIPOS_CIFRA for d in self.discrepancias)

    @property
    def citas_sin_evidencia(self) -> int:
        return sum(d.tipo in _TIPOS_CITA for d in self.discrepancias)

    @property
    def datos_personales(self) -> int:
        return sum(d.tipo == "dato_personal" for d in self.discrepancias)


@dataclass(frozen=True)
class Verificacion:
    """Lo que hizo el verificador en un turno (D16-A), para la traza y el registro.

    desenlace: "aprobada" (la primera versión pasó), "corregida" (pasó la del reintento) o "determinista" (la
    respuesta se armó con la evidencia). revisiones: la de cada texto revisado, en orden; la última es la del
    texto que se mostró. versiones: los textos del modelo que se revisaron, con los datos personales
    enmascarados. correccion: por qué el reintento no dio una versión que revisar (el tope o la falla), si pasó.
    """

    desenlace: Desenlace
    revisiones: tuple[Revision, ...]
    versiones: tuple[str, ...]
    correccion: str | None = None

    @property
    def cifras_sin_evidencia(self) -> int:
        """Las del texto que se mostró: 0 en todo turno, porque la respuesta determinista sale de la evidencia."""
        return self.revisiones[-1].cifras_sin_evidencia

    @property
    def citas_sin_evidencia(self) -> int:
        return self.revisiones[-1].citas_sin_evidencia


# ------------------------------------------------------------------ normalización

_MESES = "|".join(MESES_NOMBRE)
_NUMERO_MES = {nombre: i + 1 for i, nombre in enumerate(MESES_NOMBRE)}
_LETRA = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"

_FECHA_ISO = re.compile(r"(?<![0-9])([0-9]{4})-([0-9]{2})-([0-9]{2})(?![0-9])")
_FECHA_LARGA = re.compile(
    rf"(?<![0-9])([0-9]{{1,2}})\s+de\s+({_MESES})(?:\s+(?:de|del)\s+([0-9]{{4}})(?![0-9]))?", re.IGNORECASE
)
_MES_ANIO = re.compile(rf"(?<![{_LETRA}])({_MESES})\s+(?:de\s+|del\s+)?([0-9]{{4}})(?![0-9])", re.IGNORECASE)
_HORA = re.compile(r"(?<![0-9:])([0-9]{1,2}):([0-9]{2})(?![0-9:])")
_IDENTIFICADOR = re.compile(rf"(?<![{_LETRA}0-9-])[A-Z]{{1,4}}-?[0-9]+(?![0-9{_LETRA}])")
# Un código de documento no es una cifra: sin taparlo, el 014 de POL-OPS-014 respalda un «14 días» que ningún texto dice.
_CODIGO_DE_DOCUMENTO = re.compile(r"(?<![A-Z0-9-])[A-Z]{3}-[A-Z]{3}-[0-9]{3}(?![0-9])")
_VINETA = re.compile(r"(?m)^[ \t>*-]*[0-9]{1,2}[.)](?=\s)")
_COMA_DECIMAL = re.compile(r"(?<![0-9,])[0-9]+,(?:[0-9]{1,2}|[0-9]{4,})(?![0-9])")
_NUMERO = re.compile(rf"(?<![{_LETRA}0-9_.,])(?:[0-9]{{1,3}}(?:,[0-9]{{3}})+|[0-9]+)(?:\.[0-9]+)?(?![0-9])")
_COMILLAS = re.compile(r"«([^»\n]{1,300})»|“([^”\n]{1,300})”|\"([^\"\n]{1,300})\"")
_SIN_MARCAS = re.compile(r"[*_`|#>]+")

# Cifras con letra, ya sin acentos ni mayúsculas (_plano). El 1 solo cuenta dentro de otra cifra («treinta y un»):
# suelto es un artículo, y «un ticket» no es la fuente de un 1.
_UNIDADES = {"dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9}
_HASTA_29 = _UNIDADES | {
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21, "veintiuna": 21, "veintidos": 22,
    "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29,
}
_DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
_CENTENAS = {
    "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300, "trescientas": 300,
    "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500, "quinientas": 500, "seiscientos": 600,
    "seiscientas": 600, "setecientos": 700, "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}
_UNO = {"un": 1, "uno": 1, "una": 1}
_VALOR_PALABRA = _HASTA_29 | _DECENAS | _CENTENAS | _UNO | {"cien": 100}

_PALABRAS_UNO = "|".join(_UNO)
_MENOR_DE_CIEN = (
    rf"(?:{'|'.join(_DECENAS)})(?: y (?:{'|'.join(_UNIDADES)}|{_PALABRAS_UNO}))?|{'|'.join(_HASTA_29)}"
)
_MENOR_DE_MIL = rf"(?:{'|'.join(_CENTENAS)})(?: (?:{_MENOR_DE_CIEN}|{_PALABRAS_UNO}))?|cien|{_MENOR_DE_CIEN}"
# El «ciento» de «por ciento» no es una cifra.
_CON_LETRA = re.compile(rf"\b(?:(?:({_MENOR_DE_MIL}) )?mil(?: ({_MENOR_DE_MIL}))?|(?<!por )({_MENOR_DE_MIL}))\b")
_MILES_CON_DIGITOS = re.compile(r"(?<![0-9.,])([0-9]{1,3}(?:\.[0-9]+)?) mil\b")

# Una fecha como (año, mes, día); el año o el día faltan en «1 de junio» o en «agosto de 2026».
Fecha = tuple[int | None, int, int | None]


def _valor(texto: str) -> Decimal:
    """El número sin la coma de miles; los ceros finales no cambian el valor (Decimal('2.50') == Decimal('2.5'))."""
    return Decimal(texto.replace(",", ""))


def _suma_de_palabras(cifra: str) -> int:
    """El valor de una cifra menor de mil con letra: «ciento treinta y cinco» es 100 + 30 + 5."""
    return sum(_VALOR_PALABRA[palabra] for palabra in cifra.split() if palabra != "y")


def _cifras_con_letra(texto: str) -> Iterator[Decimal]:
    """Las cifras escritas con letra: «noventa» da 90, «mil» da 1000, «dos mil quinientos» da 2500 y «12 mil» da 12000."""
    plano = _plano(texto)
    yield from (Decimal(m.group(1)) * 1000 for m in _MILES_CON_DIGITOS.finditer(plano))
    for antes_de_mil, despues_de_mil, sin_mil in (m.groups() for m in _CON_LETRA.finditer(plano)):
        if sin_mil:
            yield Decimal(_suma_de_palabras(sin_mil))
        else:
            miles = _suma_de_palabras(antes_de_mil) if antes_de_mil else 1
            resto = _suma_de_palabras(despues_de_mil) if despues_de_mil else 0
            yield Decimal(miles * 1000 + resto)


def _tapar(texto: str, inicio: int, fin: int) -> str:
    """Cambia un tramo por espacios del mismo largo, para que las posiciones del resto no se muevan."""
    return texto[:inicio] + " " * (fin - inicio) + texto[fin:]


def _plano(texto: str) -> str:
    """Sin acentos, mayúsculas, marcas de Markdown ni espacios repetidos, para buscar un texto citado."""
    descompuesto = unicodedata.normalize("NFKD", _SIN_MARCAS.sub(" ", texto).casefold())
    sin_acentos = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return " ".join(sin_acentos.split())


@dataclass
class _Piezas:
    """Lo que se reconoció en un texto: fechas, horas, identificadores, cifras y comas decimales."""

    fechas: list[tuple[str, Fecha]]
    horas: list[tuple[str, tuple[int, int]]]
    identificadores: list[str]
    cifras: list[tuple[str, Decimal]]
    comas: list[str]


def _piezas(texto: str) -> _Piezas:
    """Reconoce cada pieza y la tapa antes de buscar la siguiente: el 31 de una fecha no es una cifra suelta."""
    piezas = _Piezas([], [], [], [], [])
    for m in list(_CODIGO_DE_DOCUMENTO.finditer(texto)):
        texto = _tapar(texto, *m.span())
    for m in list(_FECHA_ISO.finditer(texto)):
        piezas.fechas.append((m.group(0), (int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        texto = _tapar(texto, *m.span())
    for m in list(_FECHA_LARGA.finditer(texto)):
        anio = int(m.group(3)) if m.group(3) else None
        piezas.fechas.append((m.group(0), (anio, _NUMERO_MES[m.group(2).casefold()], int(m.group(1)))))
        texto = _tapar(texto, *m.span())
    for m in list(_MES_ANIO.finditer(texto)):
        piezas.fechas.append((m.group(0), (int(m.group(2)), _NUMERO_MES[m.group(1).casefold()], None)))
        texto = _tapar(texto, *m.span())
    for m in list(_HORA.finditer(texto)):
        piezas.horas.append((m.group(0), (int(m.group(1)), int(m.group(2)))))
        texto = _tapar(texto, *m.span())
    for m in list(_IDENTIFICADOR.finditer(texto)):
        piezas.identificadores.append(m.group(0))
        texto = _tapar(texto, *m.span())
    for m in list(_VINETA.finditer(texto)):
        texto = _tapar(texto, *m.span())
    for m in list(_COMA_DECIMAL.finditer(texto)):
        piezas.comas.append(m.group(0))
        texto = _tapar(texto, *m.span())
    for m in _NUMERO.finditer(texto):
        try:
            piezas.cifras.append((m.group(0), _valor(m.group(0))))
        except InvalidOperation:
            continue
    return piezas


# ------------------------------------------------------------------ citas

_CITA = re.compile(
    r"(?P<doc>(?<![A-Z0-9-])[A-Z]{3}-[A-Z]{3}-[0-9]{3}(?![0-9]))"
    r"|(?P<faq>\bFAQ\b)"
    r"|(?:§{1,2}\s?|\bsecci[oó]n(?:es)?\s+)(?P<sec>[0-9]+(?:\.[0-9]+)*)"
    r"(?:\s+del?\s+(?:la\s+)?(?P<doc_de_sec>[A-Z]{3}-[A-Z]{3}-[0-9]{3}(?![0-9])|FAQ\b))?"
    r"|(?P<preg>\bP[0-9]{1,2}\b)"
    r"|(?P<pag>(?<![A-Za-z])(?:pp?\.|p[aá]gs?\.|p[aá]ginas?)\s?(?P<p1>[0-9]+)(?:\s?[-–]\s?(?P<p2>[0-9]+))?)"
)
_FAQ = "FAQ"
# Lo que puede haber entre un documento y su sección para que la sección sea de ese documento: «POL-OPS-014, §5.2».
_PEGADA = re.compile(r"[\s,:;(]*")


@dataclass
class _Cita:
    """Una cita del texto: documento (o «FAQ»), sección y páginas; tramo es lo que ocupa en el texto.

    heredada dice que el documento es solo el último nombrado: la sección no estaba pegada a él ni dijo «de DOC».
    """

    documento: str | None
    seccion: str | None
    paginas: tuple[int, int] | None
    inicio: int
    fin: int
    heredada: bool = False

    def texto(self) -> str:
        partes = [self.documento or ""]
        if self.seccion:
            partes.append(self.seccion if self.seccion.startswith("P") else f"§{self.seccion}")
        if self.paginas:
            a, b = self.paginas
            partes.append(f"p. {a}" if a == b else f"pp. {a}-{b}")
        return " ".join(p for p in partes if p).strip()


def _citas(texto: str) -> tuple[list[_Cita], list[tuple[int, int]]]:
    """Las citas del texto y los tramos que ocupan sus piezas (para no contarlas como cifras).

    Una sección se liga al documento que dice («sección 5.1 de PRO-SAC-021») o al último nombrado; una página, a la
    sección anterior si está cerca y en el mismo renglón, o al documento anterior si la página lo sigue sin sección de
    por medio.
    """
    citas: list[_Cita] = []
    tramos: list[tuple[int, int]] = []
    documento: str | None = None
    fin_del_documento = 0
    ultima: _Cita | None = None
    for m in _CITA.finditer(texto):
        if m.group("doc") or m.group("faq"):
            documento = m.group("doc") or _FAQ
            fin_del_documento = m.end()
            ultima = _Cita(documento, None, None, m.start(), m.end())
            citas.append(ultima)
            tramos.append(m.span())
        elif m.group("sec"):
            propio = m.group("doc_de_sec")
            heredada = not propio and not _PEGADA.fullmatch(texto, fin_del_documento, m.start())
            if propio:
                documento = propio
                fin_del_documento = m.end()
            ultima = _Cita(documento, m.group("sec"), None, m.start(), m.end(), heredada)
            citas.append(ultima)
            tramos.append(m.span())
        elif m.group("preg"):
            if documento != _FAQ and not (documento or "").startswith("FAQ-"):
                continue
            ultima = _Cita(documento, m.group("preg"), None, m.start(), m.end())
            citas.append(ultima)
            tramos.append(m.span())
        else:
            tramos.append(m.span())
            primera = int(m.group("p1"))
            paginas = (primera, int(m.group("p2")) if m.group("p2") else primera)
            if ultima is not None and ultima.paginas is None and _cerca(texto, ultima.fin, m.start()):
                ultima.paginas = paginas
                ultima.fin = m.end()
                continue
            ultima = _Cita(documento, None, paginas, m.start(), m.end())
            citas.append(ultima)
    # Una mención suelta del documento seguida de su sección no es una cita aparte.
    con_seccion = [c for c in citas if c.seccion or c.paginas]
    menciones = [c for c in citas if not c.seccion and not c.paginas]
    sueltas = [c for c in menciones if not _tiene_seguimiento(c, con_seccion, texto)]
    return sorted(con_seccion + sueltas, key=lambda c: c.inicio), tramos


def _cerca(texto: str, fin: int, inicio: int) -> bool:
    entre = texto[fin:inicio]
    return "\n" not in entre and len(entre) <= DISTANCIA_PAGINA


def _tiene_seguimiento(mencion: _Cita, con_seccion: Sequence[_Cita], texto: str) -> bool:
    return any(
        c.documento == mencion.documento and c.inicio >= mencion.fin and _cerca(texto, mencion.fin, c.inicio)
        for c in con_seccion
    )


def _documento_de(cita: _Cita, documentos: Iterable[str]) -> str | None:
    """El código del documento; «FAQ» es la FAQ del índice si hay una sola."""
    if cita.documento != _FAQ:
        return cita.documento
    faqs = [d for d in documentos if d.startswith("FAQ-")]
    return faqs[0] if len(faqs) == 1 else None


# ------------------------------------------------------------------ evidencia


def _textos(valor: Any) -> Iterator[str]:
    """Cada texto y cada número de un resultado (sin las claves), como texto."""
    if isinstance(valor, str):
        yield valor
    elif isinstance(valor, bool) or valor is None:
        return
    elif isinstance(valor, (int, float)):
        yield str(valor)
    elif isinstance(valor, Mapping):
        for v in valor.values():
            yield from _textos(v)
    elif isinstance(valor, (list, tuple)):
        for v in valor:
            yield from _textos(v)


@dataclass(frozen=True)
class Fuentes:
    """Lo que la respuesta puede repetir o citar: la pregunta y los resultados de las herramientas del turno."""

    textos: tuple[str, ...]
    plano: str
    cifras: frozenset[Decimal]
    fechas: frozenset[Fecha]
    horas: frozenset[tuple[int, int]]
    identificadores: frozenset[str]
    fragmentos: frozenset[str]
    recuperadas: frozenset[tuple[str, str]]
    pregunta: str = ""


def _citas_de_politicas(resultado: Resultado) -> Iterator[str]:
    """Las citas que trae un resultado de políticas: filas, choques, regla de precedencia y documentos que no vienen."""
    for fila in resultado.filas:
        if fila.get("cita"):
            yield fila["cita"]
    for choque in resultado.detalle.get("choques", []):
        for clave in ("fuentes", "prevalece", "regla", "principio", "coinciden"):
            yield from _textos(choque.get(clave))
    regla = resultado.detalle.get("regla_entre_documentos") or {}
    if regla.get("cita"):
        yield regla["cita"]
    for clave in ("nivel_del_monto", "niveles_de_autorizacion"):
        niveles = resultado.detalle.get(clave) or {}
        if niveles:
            yield niveles["cita"]
        if niveles.get("sin_importar_el_monto"):
            yield niveles["sin_importar_el_monto"]["cita"]
    # Dónde se cita un documento que no viene: la nota de la herramienta lo dice con su cita.
    for faltante in resultado.detalle.get("no_incluidos", []):
        yield from _textos(faltante.get("citado_en"))
    # A qué tiendas aplica un documento: la herramienta lo dice con la cita de su alcance.
    for alcance in resultado.detalle.get("alcance", {}).values():
        yield alcance["cita"]


def _citas_de_datos(resultado: Resultado) -> Iterator[str]:
    """Las citas que escribe el código de una herramienta de datos: en sus notas, su definición y su fuente.

    caducidades aplica POL-OPS-014 y lo cita ahí. El texto libre de las filas, como la descripción de un ticket,
    no cuenta: es dato y no lo escribió la herramienta (T3.7).
    """
    yield from resultado.notas
    for texto in (resultado.definicion, resultado.fuente):
        if texto:
            yield texto


def _escrito_por_el_codigo(resultado: Resultado) -> dict[str, Any]:
    """Lo que vio el modelo de un resultado, sin lo que no escribió el código (D1-A).

    Las notas se quedan: las escribe el código, pero pueden citar un parámetro, y fuentes_de les resta sus cifras. Sin
    datos, los filtros corren esa misma suerte: un error los repite como los pidió el modelo y un resultado vacío los trae
    resueltos (T02, SKU-1023), así que valen sus ids y no sus cifras ni sus fechas, que pueden estar fuera del corte.
    """
    datos = resultado.para_modelo()
    datos["detalle"] = {clave: valor for clave, valor in datos.get("detalle", {}).items() if clave not in _DETALLE_QUE_REPITE}
    if "mismo_titulo_salvo_formato" in datos["detalle"]:
        # La cuenta la escribe el código; el título, quien levantó el ticket.
        datos["detalle"]["mismo_titulo_salvo_formato"] = list(datos["detalle"]["mismo_titulo_salvo_formato"].values())
    filtros = {clave: valor for clave, valor in datos.pop("filtros", {}).items() if clave not in _FILTROS_DEL_MODELO}
    if resultado.estado == "ok":
        datos["filtros"] = filtros
    else:
        datos.pop("resumen", None)
        sin_fechas = {clave: valor for clave, valor in filtros.items() if clave not in _FECHAS_DEL_MODELO}
        datos["notas"] = [*datos.get("notas", ()), *_textos(sin_fechas)]
        candidatos = datos["detalle"].get("candidatos")
        if candidatos:
            datos["cuantos_candidatos"] = datos["detalle"].get("total", len(candidatos))
    if resultado.herramienta == "tickets":
        datos.pop("resumen", None)
        datos["filas"] = [{clave: valor for clave, valor in fila.items() if clave not in _TEXTO_DE_TICKET} for fila in datos.get("filas", [])]
    return datos


def _cifras_de(texto: str) -> Iterator[Decimal]:
    """Las cifras de un texto, con dígitos o con letra, sin las partes de una fecha o una hora."""
    yield from (valor for _, valor in _piezas(texto).cifras)
    yield from _cifras_con_letra(texto)


def _cifras_de_los_parametros(parametros: Iterable[Mapping[str, Any]]) -> set[Decimal]:
    """Las cifras que el modelo escribió a mano en los parámetros de texto de sus llamadas."""
    return {
        cifra
        for entrada in parametros
        for clave in _PARAMETROS_DE_TEXTO
        for texto in _textos(entrada.get(clave))
        for cifra in _cifras_de(texto)
    }


def _descripciones_de_un_turno_de_detalle(resultados: Sequence[Resultado]) -> Iterator[str]:
    """Las descripciones sin marcar, sin montos ni porcentajes, si el turno solo abrió el detalle de tickets (D7-B)."""
    if not all(resultado.herramienta == "tickets" and resultado.filtros.get("metrica") == "detalle" for resultado in resultados):
        return
    for resultado in resultados:
        marcados = resultado.detalle.get("sospechosos", ())
        for fila in resultado.filas:
            if fila.get("descripcion") and fila.get("ticket_id") not in marcados:
                yield _MONTO_O_PORCENTAJE.sub(" ", fila["descripcion"])


def fuentes_de(pregunta: str, resultados: Iterable[Resultado], parametros: Iterable[Mapping[str, Any]] = ()) -> Fuentes:
    """Reúne la evidencia del turno: la pregunta y lo que escribió el código en cada resultado (sin la consulta).

    Las claves de las cifras también cuentan: el modelo las ve, y ventas pone en ellas los días de cada periodo
    («venta_por_dia (julio de 2026, 31 días)»). parametros son las entradas de las herramientas del turno: una cifra que
    el modelo escribió en un parámetro de texto no sale de una nota que la repite («'cumbres 9999999' se interpretó como...»).
    textos y plano no son iguales: plano trae todo lo que vio el modelo, para reconocer un nombre o un texto citado.
    """
    resultados = list(resultados)
    vistos = [pregunta]
    textos = [pregunta]
    notas: list[str] = []
    fragmentos: set[str] = set()
    recuperadas: set[tuple[str, str]] = set()
    for resultado in resultados:
        vistos.extend(_textos(resultado.para_modelo()))
        vistos.extend(resultado.cifras)
        datos = _escrito_por_el_codigo(resultado)
        notas.extend(datos.pop("notas", ()))
        textos.extend(_textos(datos))
        textos.extend(resultado.cifras)
        if resultado.estado == "documento_no_incluido":
            # El código saca del índice dónde se menciona el documento que no viene: decirlo es cierto.
            con_citas = resultado.detalle.get("citado_en", [])
        elif resultado.estado != "ok":
            continue
        elif resultado.herramienta == "buscar_politicas":
            fragmentos.update(fila["id"] for fila in resultado.filas if fila.get("id"))
            con_citas = _citas_de_politicas(resultado)
        else:
            con_citas = _citas_de_datos(resultado)
        for texto_cita in con_citas:
            for cita in _citas(texto_cita)[0]:
                if cita.documento and cita.seccion:
                    recuperadas.add((cita.documento, cita.seccion))
    cifras = {cifra for texto in textos for cifra in _cifras_de(texto)}
    cifras |= {cifra for nota in notas for cifra in _cifras_de(nota)} - _cifras_de_los_parametros(parametros)
    fechas: set[Fecha] = set()
    horas: set[tuple[int, int]] = set()
    identificadores: set[str] = set()
    for texto in (*textos, *notas):
        piezas = _piezas(texto)
        fechas.update(fecha for _, fecha in piezas.fechas)
        horas.update(hora for _, hora in piezas.horas)
        identificadores.update(i.casefold() for i in piezas.identificadores)
        # Las partes de una fecha o una hora también se pueden decir sueltas: «el 31», «las 6 de la mañana».
        cifras.update(Decimal(parte) for fecha in (f for _, f in piezas.fechas) for parte in fecha if parte is not None)
        cifras.update(Decimal(parte) for _, hora in piezas.horas for parte in hora)
    for descripcion in _descripciones_de_un_turno_de_detalle(resultados):
        cifras.update(cifra for cifra in _cifras_de(descripcion) if cifra < _TOPE_DE_NUMERO_DE_TICKET)
        identificadores.update(i.casefold() for i in _piezas(descripcion).identificadores)
    return Fuentes(
        textos=(*textos, *notas),
        plano=_plano("\n".join(vistos)),
        cifras=frozenset(cifras),
        fechas=frozenset(fechas),
        horas=frozenset(horas),
        identificadores=frozenset(identificadores),
        fragmentos=frozenset(fragmentos),
        recuperadas=frozenset(recuperadas),
        pregunta=pregunta,
    )


def _fecha_en(fecha: Fecha, fechas: Iterable[Fecha]) -> bool:
    """Una fecha coincide con otra si cada parte que tienen las dos es igual: «1 de junio» con 2026-06-01."""
    return any(
        all(a is None or b is None or a == b for a, b in zip(fecha, otra, strict=True)) for otra in fechas
    )


# ------------------------------------------------------------------ verificar


def _datos_personales(texto: str, fuentes: Fuentes) -> tuple[list[Discrepancia], str]:
    """Los teléfonos, correos y nombres del texto, y el texto con sus tramos tapados (K18).

    Usa los detectores de agente/privacidad.py, los mismos que enmascaran los tickets al cargarlos.
    """
    discrepancias: list[Discrepancia] = []
    contacto = [("TELEFONO", t) for t in privacidad._buscar_telefonos(texto)]
    contacto += [("CORREO", t) for t in privacidad._buscar_correos(texto)]
    for tipo, (inicio, fin, _) in contacto:
        discrepancias.append(Discrepancia("dato_personal", tipo, f"un {_NOMBRE_DATO[tipo]}: no se dan datos personales"))
        texto = _tapar(texto, inicio, fin)
    for inicio, fin, _ in privacidad._buscar_nombres(texto):
        if _en_el_plano(texto[inicio:fin], fuentes):
            continue
        discrepancias.append(Discrepancia("dato_personal", "PERSONA", "un nombre de persona: no se dan datos personales"))
        texto = _tapar(texto, inicio, fin)
    return discrepancias, texto


_NOMBRE_DATO = {"TELEFONO": "teléfono", "CORREO": "correo"}


def _en_el_plano(texto: str, fuentes: Fuentes) -> bool:
    """El texto está, como palabras completas, en la pregunta o en un resultado: «Ana» no está en «Anáhuac»."""
    return re.search(rf"(?<!\w){re.escape(_plano(texto))}(?!\w)", fuentes.plano) is not None


def _revisar_cita(cita: _Cita, fuentes: Fuentes, leer_secciones: Callable[[], Secciones]) -> Discrepancia | None:
    secciones = leer_secciones()
    documentos = {documento for documento, _ in secciones}
    documento = _documento_de(cita, documentos)
    escrita = cita.texto()
    if cita.documento is None:
        return _revisar_sin_documento(cita, fuentes, escrita)
    if documento not in documentos:
        mencionado = documento is not None and documento.casefold() in fuentes.plano
        if mencionado and cita.seccion is None:
            return None
        return Discrepancia(
            "documento", escrita, f"la cita {escrita} es de un documento que no viene entre los documentos"
        )
    if cita.seccion is None:
        return _revisar_pagina_del_documento(cita, documento, secciones, escrita)
    seccion = secciones.get((documento, cita.seccion))
    if seccion is None:
        if cita.heredada and _en_otro_documento(cita, fuentes, secciones):
            return None
        return Discrepancia("cita", escrita, f"la cita {escrita} no existe en el índice de los documentos")
    if not _recuperada(seccion, cita.seccion, fuentes):
        return Discrepancia(
            "cita_no_recuperada", escrita,
            f"la cita {escrita} no viene en los fragmentos que devolvió buscar_politicas en este turno",
        )
    if cita.paginas and not _paginas_dentro(cita.paginas, seccion, secciones):
        return Discrepancia("pagina", escrita, f"la página de la cita {escrita} no es la de esa sección")
    return None


def _recuperada(seccion: Seccion, numero: str, fuentes: Fuentes) -> bool:
    return seccion.fragmento in fuentes.fragmentos or (seccion.documento, numero) in fuentes.recuperadas


def _en_otro_documento(cita: _Cita, fuentes: Fuentes, secciones: Secciones) -> bool:
    """La sección está en un documento recuperado en el turno y, si la cita da página, en esas páginas."""
    candidatas = [s for (_, numero), s in secciones.items() if numero == cita.seccion and _recuperada(s, numero, fuentes)]
    if cita.paginas is None:
        return bool(candidatas)
    return any(_paginas_dentro(cita.paginas, s, secciones) for s in candidatas)


def _revisar_sin_documento(cita: _Cita, fuentes: Fuentes, escrita: str) -> Discrepancia | None:
    """Una sección o página sin documento: vale si un resultado la trae igual (la regla «§5.2» de caducidades)."""
    if cita.seccion and f"§{cita.seccion}" in "\n".join(fuentes.textos):
        return None
    if cita.seccion is None and cita.paginas and Decimal(cita.paginas[0]) in fuentes.cifras:
        return None
    return Discrepancia("cita", escrita, f"la cita {escrita} no dice de qué documento es")


def _revisar_pagina_del_documento(cita: _Cita, documento: str, secciones: Secciones, escrita: str) -> Discrepancia | None:
    if cita.paginas is None:
        return None
    del_documento = [s for (d, _), s in secciones.items() if d == documento]
    if any(_paginas_dentro(cita.paginas, s, secciones) for s in del_documento):
        return None
    return Discrepancia("pagina", escrita, f"la página de la cita {escrita} no está en el documento")


def _ultima_pagina(seccion: Seccion, secciones: Secciones) -> int:
    """La última página en que puede seguir una sección.

    El índice guarda de cada subsección solo la página donde empieza, y una subsección puede seguir en la
    página siguiente: POL-OPS-014 §6.1 empieza en la 4 y sigue en la 5, donde empieza §6.2. Llega hasta la
    página donde empieza la subsección siguiente del mismo fragmento, o hasta la última de la sección.
    """
    if seccion.orden[1] == 0:
        return seccion.pagina_fin or seccion.pagina
    del_fragmento = [s for s in secciones.values() if s.fragmento == seccion.fragmento]
    siguientes = [s for s in del_fragmento if s.orden > seccion.orden]
    if siguientes:
        return max(seccion.pagina, min(siguientes, key=lambda s: s.orden).pagina)
    padre = [s for s in del_fragmento if s.orden[1] == 0]
    return max([seccion.pagina, *(s.pagina_fin or s.pagina for s in padre)])


def _paginas_dentro(paginas: tuple[int, int], seccion: Seccion, secciones: Secciones) -> bool:
    return seccion.pagina <= paginas[0] <= paginas[1] <= _ultima_pagina(seccion, secciones)


def _revisar_comillas(texto: str, fuentes: Fuentes) -> list[Discrepancia]:
    discrepancias = []
    for m in _COMILLAS.finditer(texto):
        citado = next(g for g in m.groups() if g is not None)
        if len(citado.split()) < PALABRAS_MINIMAS_CITA_TEXTUAL:
            continue
        if _plano(citado) not in fuentes.plano:
            discrepancias.append(Discrepancia(
                "texto_citado", citado, f"el texto entre comillas «{citado}» no está en los fragmentos ni en los resultados"
            ))
    return discrepancias


# Palabras gramaticales del inglés que no existen en español. Una sola puede venir en un nombre o en el texto de un ticket.
_PALABRAS_DEL_INGLES = frozenset({
    "the", "is", "are", "was", "were", "of", "to", "and", "or", "but", "so", "this", "these", "that", "those", "it", "its",
    "can't", "cannot", "with", "for", "from", "which", "not", "be", "been", "have", "will", "would", "should", "you", "we", "they",
})
_MINIMO_PARA_OTRO_IDIOMA = 4
_FIN_DE_FRASE = re.compile(r"[.!?\n]+")
_PALABRA_CON_APOSTROFE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
_AUTOCORRECCION = re.compile(r"\b(?:corrijo\b|me equivoqu[eé]\b|correcci[oó]n:|mi respuesta anterior\b)", re.IGNORECASE)


def _revisar_idioma(texto: str) -> list[Discrepancia]:
    """Una frase en otro idioma: la respuesta va en español, y lo que dice ahí suele no salir de ningún resultado."""
    discrepancias = []
    for frase in _FIN_DE_FRASE.split(texto):
        palabras = {palabra.casefold() for palabra in _PALABRA_CON_APOSTROFE.findall(frase)}
        if len(palabras & _PALABRAS_DEL_INGLES) >= _MINIMO_PARA_OTRO_IDIOMA:
            frase_corta = frase.strip()[:80]
            discrepancias.append(Discrepancia("idioma", frase_corta, f"«{frase_corta}» no está en español"))
    return discrepancias


def _revisar_autocorreccion(texto: str) -> list[Discrepancia]:
    """Una respuesta que se corrige sola deja escrita la frase falsa: se reescribe solo con lo correcto."""
    return [
        Discrepancia("autocorreccion", hallada.group(0), f"«{hallada.group(0)}»: la respuesta se corrige a sí misma y deja lo falso escrito")
        for hallada in _AUTOCORRECCION.finditer(texto)
    ]


def _donde_esta(escrita: str, texto: str) -> str:
    """Las palabras alrededor de una cifra, para que el reintento la quite de donde está y no solo de una de sus frases."""
    hallada = re.search(rf"(?<![\w.,]){re.escape(escrita)}(?!\w)", texto)
    if not hallada:
        return ""
    antes = texto[: hallada.start()].split()[-3:]
    despues = texto[hallada.end() :].split(" ", 2)[:2]
    return f" («{privacidad.enmascarar(' '.join([*antes, escrita + ' '.join(despues)]))}»)"


def verificar(texto: str, fuentes: Fuentes, leer_secciones: Callable[[], Secciones]) -> Revision:
    """Revisa un texto contra la evidencia del turno (K16, K17, K18) y devuelve sus discrepancias.

    leer_secciones da el índice de secciones de la base (leer_indice(...).secciones); solo se llama si el
    texto trae una cita.
    """
    discrepancias, resto = _datos_personales(sin_formato(texto), fuentes)
    citas, tramos = _citas(resto)
    for cita in citas:
        problema = _revisar_cita(cita, fuentes, leer_secciones)
        if problema:
            discrepancias.append(problema)
    discrepancias.extend(_revisar_comillas(resto, fuentes))
    discrepancias.extend(_revisar_idioma(resto))
    discrepancias.extend(_revisar_autocorreccion(resto))
    for inicio, fin in tramos:
        resto = _tapar(resto, inicio, fin)
    piezas = _piezas(resto)
    for escrita, fecha in piezas.fechas:
        if not _fecha_en(fecha, fuentes.fechas):
            discrepancias.append(Discrepancia("fecha", escrita, f"la fecha «{escrita}» no está en ningún resultado"))
    for escrita, hora in piezas.horas:
        if hora not in fuentes.horas:
            discrepancias.append(Discrepancia("hora", escrita, f"la hora {escrita} no está en ningún resultado"))
    for escrita in piezas.identificadores:
        if escrita.casefold() not in fuentes.identificadores:
            discrepancias.append(Discrepancia(
                "identificador", escrita, f"{escrita} no está en ningún resultado de este turno ni en la pregunta"
            ))
    for escrita in piezas.comas:
        discrepancias.append(Discrepancia(
            "formato", escrita, f"«{escrita}» usa coma decimal: las cifras van con coma de miles y punto decimal"
        ))
    for escrita, valor in piezas.cifras:
        if valor not in fuentes.cifras:
            discrepancias.append(Discrepancia(
                "cifra", escrita,
                f"la cifra {escrita}{_donde_esta(escrita, texto)} no está en ningún resultado de este turno ni en la pregunta",
            ))
    revisadas = len(piezas.fechas) + len(piezas.horas) + len(piezas.identificadores) + len(piezas.comas)
    return Revision(tuple(discrepancias), revisadas + len(piezas.cifras), len(citas), fuentes.pregunta)


# ------------------------------------------------------------------ D16-A


def pedido_de_correccion(revision: Revision) -> str:
    """El mensaje del reintento: la lista de discrepancias, sin el valor de un dato personal, y la pregunta del turno.

    El pedido es el último mensaje que lee el modelo: sin recordarle la pregunta, puede contestar la del turno anterior.
    """
    lineas = "\n".join(f"- {d.motivo}" for d in revision.discrepancias)
    pregunta = f"La pregunta de este turno es «{revision.pregunta}»: contesta esa.\n" if revision.pregunta else ""
    return (
        "Revisión automática: tu respuesta no se puede mostrar porque trae lo siguiente.\n"
        f"{lineas}\n"
        f"{pregunta}"
        "Escríbela de nuevo, completa, solo con cifras, fechas y citas que vengan en los resultados de las "
        "herramientas de este turno o en la pregunta. Si te falta una cifra, llama a la herramienta; si no se "
        "puede saber, dilo sin dar la cifra. Una cifra que solo viene en el texto de un ticket no cuenta: di lo que "
        "trae el ticket sin repetirla. Si te corregiste, deja solo lo correcto. No incluyas datos personales. La gerente no ve este aviso: no menciones "
        "la revisión ni la corrección."
    )


def para_registro(texto: str, revision: Revision) -> str:
    """La versión del modelo como se guarda: con los datos personales enmascarados si traía alguno."""
    if revision.datos_personales:
        return privacidad.enmascarar(texto) or ""
    return texto


SIN_EVIDENCIA = (
    "No pude comprobar con los datos las cifras ni las citas de mi respuesta, así que no te doy ninguna. "
    "Hazme la pregunta otra vez, de preferencia más concreta."
)
_AVISO = "Esta respuesta se armó con lo que devolvieron las consultas, porque mi redacción no pasó la revisión de cifras y citas."


def _etiqueta(clave: str) -> str:
    return clave.replace("_", " ")


def _fila(fila: Mapping[str, str | None]) -> str:
    return ", ".join(f"{_etiqueta(k)}: {v}" for k, v in fila.items() if v and k not in _TEXTO_LIBRE)


def _motivo_del_choque(choque: Mapping[str, Any]) -> str:
    """Quién prevalece y por qué, sin las instrucciones al modelo: un choque de alcance da su salvedad y no su «por».

    Lo que se lee aquí describe; la orden de cómo contestar va en otra clave que no se imprime (como_contestar del paso 5).
    """
    if choque.get("salvedad"):
        return f"Alcance: {choque['salvedad']}."
    prevalece = choque.get("prevalece") or ""
    if isinstance(prevalece, list):
        prevalece = ", ".join(prevalece)
    partes = [f"Prevalece: {prevalece}."]
    if choque.get("por"):
        partes.append(f"Motivo: {choque['por']}.")
    if choque.get("area"):
        partes.append(f"Área responsable: {choque['area']}.")
    return " ".join(partes)


def _de_politicas(resultado: Resultado) -> list[str]:
    citas = "; ".join(f"{fila['cita']} ({fila['titulo']})" for fila in resultado.filas if fila.get("cita"))
    lineas = [f"Fragmentos de los documentos: {citas}."]
    for choque in resultado.detalle.get("choques", []):
        fuentes = "; ".join(choque.get("fuentes", []))
        lineas.append(f"Choque entre fuentes ({choque.get('tema', '')}): {fuentes}. {_motivo_del_choque(choque)}")
    return lineas


def _de_datos(resultado: Resultado) -> list[str]:
    cifras = ", ".join(f"{_etiqueta(k)} {v}" for k, v in resultado.cifras.items())
    primera = resultado.resumen
    if cifras:
        primera = f"{primera}: {cifras}" if primera else cifras
    if resultado.unidad:
        primera += f" ({resultado.unidad})"
    lineas = [f"{primera}."]
    oculta_texto = any(_TEXTO_LIBRE.intersection(fila) for fila in resultado.filas)
    if resultado.definicion and not oculta_texto:
        lineas.append(f"Definición: {resultado.definicion}.")
    if resultado.corte:
        lineas.append(f"Corte: {fecha_larga(resultado.corte)}.")
    lineas.extend(f"- {_fila(fila)}" for fila in resultado.filas[:FILAS_DETERMINISTA] if _fila(fila))
    return lineas


def respuesta_determinista(resultados: Sequence[Resultado]) -> str:
    """La respuesta armada solo con la evidencia, sin modelo (D16-A): abre con la cifra del primer resultado.

    Un resultado con error da su mensaje, sin cifra. No copia la descripción de un ticket ni el texto completo
    de un fragmento, que quedan en la traza. Sin resultados, un texto fijo sin cifras.
    """
    lineas: list[str] = []
    vistos: set[str] = set()
    for resultado in resultados:
        clave = dumps(resultado.para_modelo())
        if clave in vistos:
            continue
        vistos.add(clave)
        if resultado.estado != "ok":
            lineas.append(f"No se pudo: {resultado.resumen}.")
            lineas.extend(f"- {candidato}" for candidato in resultado.detalle.get("candidatos", ()))
        elif resultado.herramienta == "buscar_politicas":
            lineas.extend(_de_politicas(resultado))
        else:
            lineas.extend(_de_datos(resultado))
        lineas.extend(f"Nota: {nota}." for nota in resultado.notas)
    if not lineas:
        return SIN_EVIDENCIA
    return "\n".join([*lineas, _AVISO])


# ------------------------------------------------------------------ traza visible (K18)


def _sin_contacto(valor: Any) -> Any:
    if isinstance(valor, str):
        hallados = privacidad.buscar_datos_personales(valor)
        if "TELEFONO" in hallados or "CORREO" in hallados:
            return privacidad.enmascarar(valor)
        return valor
    if isinstance(valor, Mapping):
        return {k: _sin_contacto(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_sin_contacto(v) for v in valor]
    return valor


def sin_datos_de_contacto(resultado: Resultado) -> Resultado:
    """El resultado con cada texto que trae un teléfono o un correo enmascarado, para la traza visible (K18).

    El texto de los tickets ya llega enmascarado de la base (D15-A); esto cubre lo que se le hubiera escapado.
    Si no hay nada que enmascarar, devuelve el mismo resultado.
    """
    datos = resultado.model_dump(mode="json")
    limpio = _sin_contacto(datos)
    if limpio == datos:
        return resultado
    return Resultado.model_validate(limpio)
