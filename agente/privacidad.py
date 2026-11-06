"""Enmascarado y detección de datos personales en el texto libre de los tickets (D15-A, K05, K06).

agente/construir.py enmascara el título y la descripción de cada ticket al cargarlos, antes de
que el texto llegue a la base y, por lo tanto, a cualquier prompt. Cada dato se sustituye por su
tipo y un consecutivo (<PERSONA_1>, <TELEFONO_1>, <CORREO_1>). No se guarda el dato, ni su hash,
ni una tabla para reidentificarlo: el consecutivo vale dentro de un solo texto y empieza en 1 en
el siguiente.

Las reglas son propias (D15-A, sin dependencias):

- Teléfono: 10 dígitos de México, o 12 con lada de país 52, en grupos separados por punto, guion
  o espacio, o seguidos. El celular del ticket 1043 usa puntos (P43). También con diagonal, coma o
  guion bajo, si el último grupo trae 4 dígitos, y con cada dígito suelto.
- Nombre: de una a cuatro palabras con mayúscula inicial después de una frase que presenta a una
  persona ("reporta", "de parte de", "llamar a", "contacto de", "firma", "Sr."). Un nombre en
  minúsculas o sin esa frase no se detecta; el inventario del README lo anota como límite.
- Correo: usuario@dominio.

La revisión de los 285 tickets de datos/ está en el README (sección "Inventario de datos
personales"): solo el 1043 trae datos personales, un nombre y un celular.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from agente.texto_tickets import sin_formato

# --- Teléfonos -------------------------------------------------------------------------------------

# Una corrida de grupos de dígitos separados por un punto, un guion o un espacio, con lada entre
# paréntesis o "+" opcionales. Dentro de la corrida se buscan los grupos que forman un teléfono.
_CORRIDA_DE_DIGITOS = re.compile(r"(?<!\d)(?:\+\s?)?(?:\(\d{1,4}\)|\d+)(?:[ .\-]?(?:\(\d{1,4}\)|\d+))*(?!\d)")
_GRUPO = re.compile(r"\d+")
_DIGITOS_NACIONALES = 10
_LADA_PAIS = "52"
_MAXIMO_DIGITOS = len(_LADA_PAIS) + _DIGITOS_NACIONALES
# La lada nacional va en el primer grupo con 2 o 3 dígitos (55, 81, 222). Así una fecha con hora,
# "2026-06-02 10", que también suma 10 dígitos, no pasa por teléfono.
_DIGITOS_DE_LADA = (2, 3)
# Los otros separadores van aparte de la corrida: con la coma adentro, 17,284,355.44 pasaría por teléfono. Una coma de
# miles nunca va antes de un grupo de 4 dígitos, y por eso el último grupo los exige.
_OTROS_SEPARADORES = re.compile(
    r"(?<![\d/,_+])(?:\+?52[/,_])?\d{2,3}[/,_]\d{3,4}[/,_]\d{4}(?![\d/,_])"
    r"|(?<!\d)(?:\d ){9}\d(?!\d)"
)


def _clave_telefono(grupos: list[str]) -> str | None:
    """Los 10 dígitos nacionales si los grupos forman un teléfono; None si no.

    La clave hace que el mismo número, con o sin lada de país, lleve el mismo consecutivo.
    """
    digitos = "".join(grupos)
    nacionales = grupos
    if len(digitos) == _MAXIMO_DIGITOS and digitos.startswith(_LADA_PAIS):
        nacionales = grupos[1:] if grupos[0] == _LADA_PAIS else [digitos[len(_LADA_PAIS):]]
    elif len(digitos) != _DIGITOS_NACIONALES:
        return None
    if len(nacionales) > 1 and len(nacionales[0]) not in _DIGITOS_DE_LADA:
        return None
    return digitos[-_DIGITOS_NACIONALES:]


def _tramos_de_telefono(corrida: re.Match) -> list[tuple[int, int, str]]:
    """(inicio, fin, clave) de cada teléfono dentro de una corrida de dígitos.

    Desde cada grupo se toma la ventana más larga de grupos seguidos que forma un teléfono, para
    que "caja 2 55 1234 5678" enmascare el teléfono aunque el 2 vaya antes.
    """
    grupos = list(_GRUPO.finditer(corrida.group(0)))
    tramos = []
    i = 0
    while i < len(grupos):
        encontrado = None
        for j in range(i, len(grupos)):
            ventana = [g.group(0) for g in grupos[i : j + 1]]
            if len("".join(ventana)) > _MAXIMO_DIGITOS:
                break
            clave = _clave_telefono(ventana)
            if clave is not None:
                encontrado = (j, clave)
        if encontrado is None:
            i += 1
            continue
        j, clave = encontrado
        tramos.append((_ampliar_inicio(corrida, grupos[i].start()), _ampliar_fin(corrida, grupos[j].end()), clave))
        i = j + 1
    return tramos


def _ampliar_inicio(corrida: re.Match, inicio: int) -> int:
    """Incluye en el tramo el "(" de la lada y el "+" del prefijo de país."""
    texto = corrida.group(0)
    while inicio > 0 and texto[inicio - 1] in "(+":
        inicio -= 1
    return corrida.start() + inicio


def _ampliar_fin(corrida: re.Match, fin: int) -> int:
    texto = corrida.group(0)
    if fin < len(texto) and texto[fin] == ")":
        fin += 1
    return corrida.start() + fin


def _buscar_telefonos(texto: str) -> list[tuple[int, int, str]]:
    tramos = []
    for corrida in _CORRIDA_DE_DIGITOS.finditer(texto):
        tramos.extend(_tramos_de_telefono(corrida))
    for hallado in _OTROS_SEPARADORES.finditer(texto):
        digitos = re.sub(r"\D", "", hallado.group(0))
        if len(digitos) in (_DIGITOS_NACIONALES, _MAXIMO_DIGITOS):
            tramos.append((hallado.start(), hallado.end(), digitos[-_DIGITOS_NACIONALES:]))
    return tramos


# --- Correos ---------------------------------------------------------------------------------------

_CORREO = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")


def _buscar_correos(texto: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0).casefold()) for m in _CORREO.finditer(texto)]


# --- Nombres ---------------------------------------------------------------------------------------

# Frases que presentan a una persona. Se comparan sin distinguir mayúsculas; el nombre que sigue,
# en cambio, debe empezar con mayúscula ("Reporta que la puerta..." no es un nombre).
_DISPARADORES = (
    r"reporta", r"report[oó]", r"levanta", r"levant[oó]", r"de parte de", r"a nombre de",
    r"atiende", r"atendi[oó]", r"contactar a", r"contacta a", r"hablar con", r"habla con",
    r"habl[oó] con", r"llamar a", r"preguntar por", r"comunicarse con", r"me llamo",
    r"mi nombre es", r"soy", r"sra?\.?", r"srita\.?", r"señor", r"señora", r"lic\.",
    r"ing\.", r"don", r"doña", r"contacto de", r"contacto:", r"firma:?", r"atentamente,?",
    r"cel de", r"celular de",
)
_PALABRA_NOMBRE = r"[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]+(?:[-'][A-ZÁÉÍÓÚÑÜ]?[a-záéíóúñü]+)?"
_ENLACE_NOMBRE = r"(?:de la|de los|de las|del|de|y)"
_NOMBRE = re.compile(
    rf"(?<!\w)(?i:{'|'.join(_DISPARADORES)})\s+"
    rf"(?P<nombre>{_PALABRA_NOMBRE}(?:\s+(?:{_ENLACE_NOMBRE}\s+)?{_PALABRA_NOMBRE}){{0,3}})"
)


def _buscar_nombres(texto: str) -> list[tuple[int, int, str]]:
    return [(m.start("nombre"), m.end("nombre"), m.group("nombre").casefold()) for m in _NOMBRE.finditer(texto)]


# --- Enmascarado -----------------------------------------------------------------------------------

# Orden de búsqueda: primero los teléfonos y correos, que no se confunden con un nombre.
_DETECTORES: tuple[tuple[str, Callable[[str], list[tuple[int, int, str]]]], ...] = (
    ("TELEFONO", _buscar_telefonos),
    ("CORREO", _buscar_correos),
    ("PERSONA", _buscar_nombres),
)


def _sustituir(texto: str, tipo: str, tramos: list[tuple[int, int, str]]) -> str:
    """Cambia cada tramo por <TIPO_n>; el mismo dato dentro del texto lleva el mismo n.

    El diccionario de consecutivos vive solo durante esta llamada: no se devuelve ni se guarda.
    """
    consecutivos: dict[str, int] = {}
    partes = []
    anterior = 0
    for inicio, fin, clave in sorted(tramos):
        numero = consecutivos.setdefault(clave, len(consecutivos) + 1)
        partes.append(texto[anterior:inicio])
        partes.append(f"<{tipo}_{numero}>")
        anterior = fin
    partes.append(texto[anterior:])
    return "".join(partes)


def enmascarar(texto: str | None) -> str | None:
    """El texto sin caracteres de formato y con cada teléfono, correo y nombre sustituido por su tipo y un consecutivo."""
    if not texto:
        return texto
    texto = sin_formato(texto)
    for tipo, buscar in _DETECTORES:
        tramos = buscar(texto)
        if tramos:
            texto = _sustituir(texto, tipo, tramos)
    return texto


def buscar_datos_personales(texto: str | None) -> list[str]:
    """Tipos de dato personal que aparecen en un texto (sin los valores), en el orden de búsqueda.

    Sirve para la revisión de los tickets y para el detector de la respuesta (K18, T3.4).
    """
    if not texto:
        return []
    hallados = []
    for tipo, buscar in _DETECTORES:
        if buscar(texto):
            hallados.append(tipo)
            texto = _sustituir(texto, tipo, buscar(texto))
    return hallados
