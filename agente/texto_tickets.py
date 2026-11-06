"""Texto de los tickets: títulos normalizados y temas de config/temas_tickets.csv (T1.3, C1.4).

Los tickets traen 146 títulos distintos tal como los escribió cada tienda: el mismo problema
aparece con otras mayúsculas, con y sin acentos y con un "gracias" al final. normalizar_titulo los
reduce a 56 una sola vez, al construir la base (agente/construir.py). Desde ahí, contar lo que más
falla es un GROUP BY sobre titulo_normalizado, sin un LIKE que parta un mismo problema en varios
ni un LIMIT 1 que esconda un empate.

El tema agrupa los títulos que hablan del mismo equipo o del mismo proceso y vive en
config/temas_tickets.csv, revisado a mano. Un tema suma varios títulos: sirve para buscar (todas las
impresoras de caja) y para ligar una falla con su política, no para decir qué falla más, que se
cuenta por título.

Este módulo no usa DuckDB ni lee los tickets: recibe textos y devuelve textos. Así la carga de la
base y la búsqueda de la herramienta de tickets normalizan igual.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

ARCHIVO_TEMAS = "temas_tickets.csv"
ENCABEZADO_TEMAS = ("titulo_normalizado", "tema")
# Tema de un título que config/temas_tickets.csv no conoce, por ejemplo el de un dato nuevo.
TEMA_SIN_CLASIFICAR = "Sin clasificar"

_NO_ES_PALABRA = re.compile(r"[\W_]+")
_ANTES_DEL_GRACIAS = frozenset({"muchas", "mil"})


def sin_formato(texto: str) -> str:
    """Sin los caracteres Unicode de formato (categoría Cf), como el de ancho cero: no se ven y parten una palabra."""
    return "".join(letra for letra in texto if unicodedata.category(letra) != "Cf")


def normalizar_texto(texto: str) -> str:
    """Minúsculas, sin acentos ni signos y con un solo espacio entre palabras.

    La eñe pierde su tilde igual que los acentos (baños queda banos): las tiendas escriben "Fuga de
    agua en baños" y "Fuga de agua en banos", y es el mismo título.
    """
    descompuesto = unicodedata.normalize("NFD", sin_formato(texto).casefold())
    sin_acentos = "".join(letra for letra in descompuesto if unicodedata.category(letra) != "Mn")
    return _NO_ES_PALABRA.sub(" ", sin_acentos).strip()


# El marcador de frases (K08) trabaja sobre el texto normalizado (minúsculas, sin acentos ni signos), en
# Python con normalizar_texto y en DuckDB con tickets.TEXTO_NORMALIZADO. Cada frase pide palabras
# completas. Busca órdenes al asistente y no temas: "el sistema no responde" o "la asistente de gerencia
# reporta" no se marcan. Es una señal para la traza y la respuesta, no una barrera (Prompt Injection CS).
_ANULAR = "ignora|ignore|ignorar|ignoren|olvida|olvide|olvidar|olviden|omite|omitir|desobedece|saltate|anula"
_REGLAS = "instrucciones|instruccion|reglas|indicaciones|restricciones|directrices|ordenes|prompt"
_AL_ASISTENTE = "asistente|chatbot|bot|ia|inteligencia artificial|chatgpt|gpt|claude|gemini|copilot|llm|modelo de lenguaje"
_DESTINATARIO = f"{_AL_ASISTENTE}|modelo|agente"
_ORDEN = (
    "responde que|responda que|responder que|contesta que|conteste que|contestar que|responde solo|contesta solo|"
    "di que|diga que|decir que|dime que|dile que|digale que|ignora|ignore|olvida|olvide|obedece|omite|debes|tienes que"
)
FRASES_SOSPECHOSAS: tuple[str, ...] = (
    # "olvida tus reglas", "ignora tus instrucciones"
    f"(?:{_ANULAR})(?: [a-z0-9]+){{0,2}} tus (?:{_REGLAS})",
    # "ignora las instrucciones anteriores"
    f"(?:{_ANULAR})(?: [a-z0-9]+){{0,3}} (?:{_REGLAS}) (?:anteriores|previas|originales|iniciales|del sistema|de sistema)",
    # "asistente: responde que...", "IA, di que..."
    f"(?:{_AL_ASISTENTE})(?: [a-z0-9]+){{0,6}} (?:{_ORDEN})",
    # "nota para el asistente", "instrucción para el modelo"
    f"(?:nota|mensaje|instruccion|instrucciones|indicacion|indicaciones) (?:para|al|a la|a el)(?: el| la| tu)? (?:{_DESTINATARIO})",
    # "a partir de ahora eres...", "eres un asistente..."
    "(?:a partir de ahora|desde ahora|de ahora en adelante)(?: [a-z0-9]+){0,2} (?:eres|seras|actua|actuaras|debes|tienes que)",
    f"(?:eres|actua como|actuas como|finge ser)(?: un| una| el| la)? (?:{_DESTINATARIO})",
    "ignore(?: [a-z]+){0,3} instructions|system prompt|prompt del sistema|prompt de sistema|modo desarrollador|developer mode|jailbreak",
    # Otras formas de lo mismo: dan 0 coincidencias en los tickets, las políticas y las preguntas del banco. "Quien lea
    # esto" queda fuera a propósito: es la variante de K31 sin frases conocidas, porque el marcador es una señal.
    "disregard|previous instructions|no hagas caso|tu tarea es",
)
PATRON_SOSPECHOSO = "(?:^| )(?:" + "|".join(f"(?:{frase})" for frase in FRASES_SOSPECHOSAS) + ")(?: |$)"
_SOSPECHOSO = re.compile(PATRON_SOSPECHOSO)
# Letras griegas y cirílicas: se ven como las latinas y sirven para esquivar las frases. Ningún texto del paquete las trae.
# Se busca en el texto sin normalizar. Sirve igual en Python y en RE2, el motor de DuckDB.
PATRON_LETRAS_AJENAS = "[\u0370-\u03ff\u0400-\u04ff]"
_LETRAS_AJENAS = re.compile(PATRON_LETRAS_AJENAS)


def es_sospechoso(texto: str | None) -> bool:
    """El texto trae una orden al asistente o letras que imitan a las latinas (K08). En la base, tickets.py corre lo mismo."""
    if not texto:
        return False
    return bool(_LETRAS_AJENAS.search(texto) or _SOSPECHOSO.search(normalizar_texto(texto)))


def normalizar_titulo(titulo: str) -> str:
    """El título normalizado y sin el "gracias" de cortesía del final ("muchas gracias", "mil gracias").

    Solo se quita al final: en medio es parte del título. Si el título fuera solo una cortesía se
    conserva completo, para no dejar una clave vacía. Normalizar dos veces da lo mismo que una.
    """
    palabras = normalizar_texto(titulo).split()
    restantes = list(palabras)
    while restantes and restantes[-1] == "gracias":
        restantes.pop()
        if restantes and restantes[-1] in _ANTES_DEL_GRACIAS:
            restantes.pop()
    return " ".join(restantes or palabras)


def _celdas(renglon: str) -> list[str]:
    return [celda.strip() for celda in next(csv.reader([renglon]), [])]


def leer_temas(ruta: Path) -> dict[str, str]:
    """Título normalizado -> tema, leído de config/temas_tickets.csv y validado.

    Formato: el encabezado titulo_normalizado,tema y una fila por título. Se ignoran las líneas en
    blanco y las que empiezan con #, para dejar anotada la revisión dentro del archivo. Cada fila
    debe traer el título ya normalizado, sin repetirse, y un tema que no sea TEMA_SIN_CLASIFICAR.
    Dos temas que solo difieren en mayúsculas, acentos o espacios son una errata y se rechazan.

    Lanza ValueError, con el renglón y el motivo, si algo no cumple, y OSError si no se puede leer.
    """
    try:
        contenido = Path(ruta).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("el archivo no está en UTF-8") from error
    renglones = [
        (numero, linea)
        for numero, linea in enumerate(contenido.splitlines(), 1)
        if linea.strip() and not linea.lstrip().startswith("#")
    ]
    if not renglones:
        raise ValueError(f"no trae el encabezado {','.join(ENCABEZADO_TEMAS)}")
    numero_encabezado, encabezado = renglones[0]
    if tuple(_celdas(encabezado)) != ENCABEZADO_TEMAS:
        raise ValueError(
            f"renglón {numero_encabezado}: el encabezado debe ser {','.join(ENCABEZADO_TEMAS)} y es «{encabezado.strip()}»"
        )
    temas: dict[str, str] = {}
    renglon_de: dict[str, int] = {}
    for numero, linea in renglones[1:]:
        celdas = _celdas(linea)
        if len(celdas) != len(ENCABEZADO_TEMAS):
            raise ValueError(f"renglón {numero}: debe tener 2 columnas y tiene {len(celdas)}")
        titulo, tema = celdas
        if not titulo or not tema:
            raise ValueError(f"renglón {numero}: el título y el tema no pueden ir vacíos")
        esperado = normalizar_titulo(titulo)
        if titulo != esperado:
            raise ValueError(f"renglón {numero}: «{titulo}» debe ir normalizado, es decir «{esperado}»")
        if titulo in temas:
            raise ValueError(f"renglón {numero}: «{titulo}» ya está en el renglón {renglon_de[titulo]}")
        if normalizar_texto(tema) == normalizar_texto(TEMA_SIN_CLASIFICAR):
            raise ValueError(f"renglón {numero}: «{tema}» está reservado para los títulos que el archivo no conoce")
        temas[titulo] = tema
        renglon_de[titulo] = numero
    if not temas:
        raise ValueError("no trae filas después del encabezado")
    _verificar_etiquetas(temas)
    return temas


def _verificar_etiquetas(temas: dict[str, str]) -> None:
    """Un solo texto por tema: «Báscula» y «Bascula» juntas son una errata, no dos temas."""
    por_clave: dict[str, str] = {}
    for tema in temas.values():
        clave = normalizar_texto(tema)
        if por_clave.setdefault(clave, tema) != tema:
            raise ValueError(
                f"los temas «{por_clave[clave]}» y «{tema}» difieren solo en mayúsculas, acentos o espacios; deja una sola forma"
            )
