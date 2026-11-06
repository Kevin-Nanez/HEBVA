"""Manifiesto de cobertura: qué sabe el sistema, leído de la base y sin llamar al modelo (T4.2, C4.4, C3.1, K41).

Qué es. La tabla cobertura de la base (agente/construir.py, T1.2) dice qué trae cada fuente y qué canal vende cada
tienda. Con las tiendas, el índice de políticas y los cortes de config/supuestos.yaml arma lo que la gerente lee como
respuesta a "qué sabes": tiendas, periodos, cortes, documentos y 3 ejemplos de preguntas
que no puede contestar. Las tiendas, los periodos, los cortes, los documentos y los ejemplos salen de la base y de la
configuración que hay ahora, no se escriben aquí a mano: una copia de datos/ o de config/ cambia el manifiesto igual
que cambia una cifra (C1.4). Solo es fija la línea de introducción de texto(). El periodo es el que tienen los datos y
el corte el que declara config/supuestos.yaml, el mismo que dice cada respuesta (K29): hoy coinciden.

Los 3 ejemplos son los límites de K41, en este orden: el ticket promedio (los datos no traen transacciones), una fecha
después del corte de ventas y un documento que otro cita y no viene. Si falta alguno, entra el siguiente límite de
metricas_no_calculables cuyo motivo se lee sin jerga. Las preguntas son genéricas a propósito: ninguna es una del set
de evaluación (G11), y el set de T5.1 no debe repetir estas tres.

Sin modelo. pide_la_cobertura() reconoce el mensaje que es solo "qué sabes" o ayuda, y responder() lo contesta con el
manifiesto: no prepara ningún modelo, no lee .env, no llama al responder() de agente/agente.py y no guarda la vuelta en
el Historial, así que el modelo no ve esa respuesta larga como contexto de la pregunta siguiente. El turno deja su
línea en logs/turnos.jsonl con la misma función que los demás (agente/registro.py): 0 llamadas, 0 tokens en cada
campo, costo 0 y gen_ai.request.model = MODELO. El detector es estricto a propósito: un mensaje que sigue con una
pregunta de datos ("qué sabes de las ventas de Cumbres") no es de cobertura y va al modelo.

Reproducir (E16). La línea trae el hash de las fuentes de la base que respondió, y la respuesta es una función de esa
base: leer() con los mismos datos da el mismo texto. registro.reproducir() reconoce MODELO en la línea y repite el
turno con responder() de este módulo, sin modelo y sin leer .env.
"""

from __future__ import annotations

import datetime as dt
import re
import time
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agente import agente as modulo_agente
from agente import construir, definiciones
from agente import historial as modulo_historial
from agente import registro as modulo_registro
from agente.consultas import abrir_consultas
from agente.herramientas import buscar_politicas, comun
from agente.herramientas.comun import consultar, entero, fecha_larga
from agente.herramientas.errores import fuente_no_disponible_de

# El nombre con que la línea del registro dice que el turno no usó ningún modelo.
MODELO = "sin_modelo"

CLAVES_DE_FUENTES = ("ventas", "tickets", "inventario")
NOMBRES_DE_FUENTES = {
    "ventas": "Ventas",
    "tickets": "Tickets de la mesa de servicio",
    "inventario": "Inventario",
}
FRASES_DE_CANALES = {"Piso": "en piso", "Ecommerce": "en línea (Ecommerce)"}

# Los límites declarados en config/supuestos.yaml (metricas_no_calculables) que sirven de ejemplo después de los de K41,
# con la pregunta que los ilustra. merma_real e inventario_historico no están: su motivo trae un identificador de duda
# (D1-A) o una fecha ISO, y la gerente no los lee.
PREGUNTAS_DE_LIMITES = {
    "ticket_promedio": "¿Cuál es el ticket promedio de una tienda?",
    "venta_por_hora": "¿A qué hora se vende más en una tienda?",
    "meta_venta": "¿Cuánto falta para llegar a la meta de venta?",
    "primera_respuesta": "¿Cuánto tardó la primera respuesta de un ticket?",
}
LIMITES_DE_RESPALDO = ("venta_por_hora", "meta_venta", "primera_respuesta")
MAXIMO_DE_EJEMPLOS = 3

SQL_TIENDAS = (
    "SELECT tienda_id, nombre, ciudad, estado, formato FROM tiendas ORDER BY tienda_id"
)
SQL_CANALES = "SELECT tienda_id, canal FROM cobertura WHERE fuente = 'ventas' AND tienda_id IS NOT NULL ORDER BY tienda_id, canal"
SQL_FUENTES = (
    "SELECT fuente, filas, desde, hasta FROM cobertura WHERE tienda_id IS NULL"
)

TITULO_DE_CORTES = "Cortes"
TITULO_DE_PERIODOS = "Periodos con datos"
TITULO_DE_EJEMPLOS = "Ejemplos de preguntas que no puedo contestar"


# ------------------------------------------------------------------ lo que sabe el sistema


@dataclass(frozen=True)
class Tienda:
    id: str
    nombre: str
    ciudad: str
    estado: str
    formato: str
    # Los canales en que la tienda tiene ventas, en el orden de config/supuestos.yaml (ventas.canales).
    canales: tuple[str, ...]


@dataclass(frozen=True)
class Fuente:
    """Una fuente de datos: el periodo que tienen sus datos (de la base) y su corte (de la configuración)."""

    clave: str
    nombre: str
    desde: dt.date | None
    hasta: dt.date | None
    corte: dt.date


@dataclass(frozen=True)
class Documento:
    codigo: str
    titulo: str
    version: str
    vigencia: dt.date


@dataclass(frozen=True)
class NoIncluido:
    """Un documento que otro cita y no viene en el paquete, con los lugares donde se cita ("POL-OPS-014 §1.2, p. 1")."""

    codigo: str
    citado_en: tuple[str, ...]


@dataclass(frozen=True)
class Ejemplo:
    """Una pregunta que el sistema no puede contestar y por qué, en minúscula y sin punto final."""

    pregunta: str
    motivo: str


@dataclass(frozen=True)
class Seccion:
    titulo: str
    lineas: tuple[str, ...]

    @property
    def texto(self) -> str:
        return "\n".join((self.titulo, *self.lineas))


@dataclass(frozen=True)
class Manifiesto:
    """Lo que el sistema puede consultar, con el hash de las fuentes de la base de donde se leyó."""

    tiendas: tuple[Tienda, ...]
    fuentes: tuple[Fuente, ...]
    productos: int
    documentos: tuple[Documento, ...]
    no_incluidos: tuple[NoIncluido, ...]
    ejemplos: tuple[Ejemplo, ...]
    hash_fuentes: str
    base_reconstruida: bool


# ------------------------------------------------------------------ leer la base


def leer(
    *,
    base: Path = construir.BASE,
    dir_datos: Path = construir.DIR_DATOS,
    dir_config: Path = construir.DIR_CONFIG,
) -> Manifiesto:
    """El manifiesto de la base al día con datos/, politicas/ y config/ (como al empezar un turno de responder()).

    Deja la base vigente con asegurar_base(), la abre en solo lectura con la protección de Consultas, lee y la
    cierra: en Windows una conexión que quedara abierta haría fallar el siguiente asegurar_base() al cambiar una
    fuente. Lanza un ErrorConstruccion si no hay base que consultar; mensaje_sin_fuente() dice cómo se lo cuenta a la
    gerente.
    """
    estado = construir.asegurar_base(dir_datos, base, dir_config=dir_config)
    supuestos = definiciones.cargar(Path(dir_config) / "supuestos.yaml")
    with abrir_consultas(base) as consultas:
        cursor = consultas.sesion()
        try:
            return _armar(cursor, supuestos, estado)
        finally:
            cursor.close()


def _armar(
    cursor, s: definiciones.Supuestos, estado: construir.EstadoBase
) -> Manifiesto:
    fuentes = {fila["fuente"]: fila for fila in consultar(cursor, SQL_FUENTES)}
    indice = buscar_politicas.leer_indice(cursor)
    no_incluidos = _no_incluidos(indice)
    fuentes_de_datos = tuple(
        _fuente(clave, fuentes[clave], s) for clave in CLAVES_DE_FUENTES
    )
    corte_de_ventas = next(
        fuente.corte for fuente in fuentes_de_datos if fuente.clave == "ventas"
    )
    return Manifiesto(
        tiendas=_tiendas(cursor, s),
        fuentes=fuentes_de_datos,
        productos=fuentes["catalogo"]["filas"],
        documentos=_documentos(indice),
        no_incluidos=no_incluidos,
        ejemplos=elegir_ejemplos(
            s.metricas_no_calculables, corte_de_ventas, no_incluidos
        ),
        hash_fuentes=estado.hash_fuentes.sha256,
        base_reconstruida=estado.reconstruida,
    )


def _tiendas(cursor, s: definiciones.Supuestos) -> tuple[Tienda, ...]:
    canales: dict[str, list[str]] = {}
    for fila in consultar(cursor, SQL_CANALES):
        canales.setdefault(fila["tienda_id"], []).append(fila["canal"])
    orden = s.ventas.canales

    def lugar(canal: str) -> int:
        return orden.index(canal) if canal in orden else len(orden)

    return tuple(
        Tienda(
            fila["tienda_id"],
            fila["nombre"],
            fila["ciudad"],
            fila["estado"],
            fila["formato"],
            tuple(sorted(canales.get(fila["tienda_id"], ()), key=lugar)),
        )
        for fila in consultar(cursor, SQL_TIENDAS)
    )


def _corte_de(s: definiciones.Supuestos, clave: str) -> dt.date:
    corte = getattr(s.cortes, clave)
    fecha = corte.fecha or corte.hasta
    if fecha is None:
        raise ValueError(f"el corte de {clave} en config/supuestos.yaml no trae fecha")
    return fecha


def _fuente(clave: str, fila: Mapping, s: definiciones.Supuestos) -> Fuente:
    return Fuente(
        clave,
        NOMBRES_DE_FUENTES[clave],
        fila["desde"],
        fila["hasta"],
        _corte_de(s, clave),
    )


def _documentos(indice: buscar_politicas.Indice) -> tuple[Documento, ...]:
    primeros: dict[str, Mapping] = {}
    for fragmento in indice.fragmentos.values():
        primeros.setdefault(fragmento["documento"], fragmento)
    return tuple(
        Documento(codigo, f["titulo_documento"], f["version"], f["vigencia"])
        for codigo, f in sorted(primeros.items())
    )


def _no_incluidos(indice: buscar_politicas.Indice) -> tuple[NoIncluido, ...]:
    # La definición de "no viene" es la de la herramienta de políticas y la del verificador: un código de documento que
    # aparece en un fragmento y no está entre los del índice. No se repite aquí.
    textos = [fragmento["texto"] for fragmento in indice.fragmentos.values()]
    faltantes = buscar_politicas._no_incluidos(textos, indice)
    return tuple(
        NoIncluido(faltante["documento"], tuple(faltante["citado_en"]))
        for faltante in faltantes
    )


# ------------------------------------------------------------------ los 3 ejemplos


def _unir(elementos: Sequence[str]) -> str:
    """'a', 'a y b', 'a, b y c'."""
    if len(elementos) < 2:
        return "".join(elementos)
    return f"{', '.join(elementos[:-1])} y {elementos[-1]}"


def _ejemplo_fuera_de_corte(corte_de_ventas: dt.date) -> Ejemplo:
    """El día siguiente al corte: ese día y todos los que siguen no tienen datos, caiga donde caiga el corte."""
    siguiente = corte_de_ventas + dt.timedelta(days=1)
    return Ejemplo(
        f"¿Cuánto se vendió el {fecha_larga(siguiente)}?",
        f"las ventas llegan al {fecha_larga(corte_de_ventas)}",
    )


def _ejemplo_de_documento(faltante: NoIncluido) -> Ejemplo:
    if faltante.citado_en:
        motivo = f"{faltante.codigo} se menciona en {_unir(faltante.citado_en)}, pero no viene entre los documentos"
    else:
        motivo = f"{faltante.codigo} no viene entre los documentos"
    return Ejemplo(f"¿Qué dice el documento {faltante.codigo}?", motivo)


def elegir_ejemplos(
    metricas: Mapping[str, str],
    corte_de_ventas: dt.date,
    no_incluidos: Sequence[NoIncluido],
) -> tuple[Ejemplo, ...]:
    """Hasta 3 preguntas que el sistema no puede contestar, en el orden de K41.

    metricas es metricas_no_calculables de config/supuestos.yaml: de ahí sale el motivo del ticket promedio y de los
    límites de respaldo, tal como lo declara la configuración. Se cubre primero el ticket promedio, después una fecha
    fuera del corte y después un documento que no viene; lo que falte lo completan, en orden, los límites de respaldo.
    """
    candidatos: list[Ejemplo] = []
    if "ticket_promedio" in metricas:
        candidatos.append(
            Ejemplo(
                PREGUNTAS_DE_LIMITES["ticket_promedio"], metricas["ticket_promedio"]
            )
        )
    candidatos.append(_ejemplo_fuera_de_corte(corte_de_ventas))
    candidatos.extend(_ejemplo_de_documento(faltante) for faltante in no_incluidos[:1])
    candidatos.extend(
        Ejemplo(PREGUNTAS_DE_LIMITES[clave], metricas[clave])
        for clave in LIMITES_DE_RESPALDO
        if clave in metricas
    )
    return tuple(candidatos[:MAXIMO_DE_EJEMPLOS])


# ------------------------------------------------------------------ el texto


def _plural(cantidad: int, singular: str, plural: str) -> str:
    return f"{entero(cantidad)} {singular if cantidad == 1 else plural}"


def _introduccion(cobertura: Manifiesto) -> str:
    return (
        "Consulto las ventas, el inventario, las caducidades y los tickets de la mesa de servicio de "
        f"{_plural(len(cobertura.tiendas), 'tienda', 'tiendas')}, y "
        f"{_plural(len(cobertura.documentos), 'documento', 'documentos')} de políticas. "
        "Todo lo que respondo sale de esos datos; lo que no cubren, te lo respondo."
    )


ENCABEZADOS_DE_TIENDAS = ("Tienda", "Clave", "Ciudad", "Estado", "Formato", "Ventas")
ENCABEZADOS_DE_PERIODOS = ("Fuente", "Datos")
ENCABEZADOS_DE_DOCUMENTOS = ("Código", "Documento", "Versión", "Vigente desde")


def _celda(valor: object) -> str:
    """Un dato como celda: un salto de línea partiría la fila y una barra sin escapar partiría la celda."""
    texto = " ".join(str(valor).split())
    return texto.replace("|", "\\|")


def _fila(celdas: Sequence[object]) -> str:
    return f"| {' | '.join(_celda(celda) for celda in celdas)} |"


def _tabla(encabezados: Sequence[str], filas: Sequence[Sequence[object]]) -> tuple[str, ...]:
    """Una tabla en Markdown: la pantalla la dibuja con su componente de tablas, sin interpretar Markdown (K23)."""
    separador = _fila(["---"] * len(encabezados))
    return (_fila(encabezados), separador, *(_fila(fila) for fila in filas))


def _ventas_de(tienda: Tienda) -> str:
    frases = [FRASES_DE_CANALES.get(canal, f"en {canal}") for canal in tienda.canales]
    if not frases:
        return "Sin ventas registradas"
    if len(frases) == 1:
        return f"Solo {frases[0]}"
    unidas = _unir(frases)
    return f"{unidas[0].upper()}{unidas[1:]}"


def _fila_de_tienda(tienda: Tienda) -> tuple[str, ...]:
    return (tienda.nombre, tienda.id, tienda.ciudad, tienda.estado, tienda.formato, _ventas_de(tienda))


def _fila_de_periodo(fuente: Fuente) -> tuple[str, str]:
    if fuente.desde is None or fuente.hasta is None:
        return fuente.nombre, "Sin registros"
    if fuente.clave == "inventario":
        return fuente.nombre, f"Una sola foto, la del {fecha_larga(fuente.hasta)}; no hay historial"
    return fuente.nombre, f"Del {fecha_larga(fuente.desde)} al {fecha_larga(fuente.hasta)}"


def _fila_de_documento(documento: Documento) -> tuple[str, ...]:
    return (documento.codigo, documento.titulo, documento.version, fecha_larga(documento.vigencia))


def _lineas_de_ejemplo(ejemplo: Ejemplo) -> tuple[str, str]:
    return f"- «{ejemplo.pregunta}»", f"  Porque {ejemplo.motivo}."


def secciones(cobertura: Manifiesto) -> tuple[Seccion, ...]:
    """Las cinco secciones del manifiesto en el orden en que se leen.

    Tiendas, periodos y documentos van en tabla; cortes y ejemplos, en viñetas.
    """
    tiendas = _tabla(ENCABEZADOS_DE_TIENDAS, [_fila_de_tienda(t) for t in cobertura.tiendas])
    periodos = _tabla(
        ENCABEZADOS_DE_PERIODOS,
        [
            *(_fila_de_periodo(fuente) for fuente in cobertura.fuentes),
            ("Catálogo", _plural(cobertura.productos, "producto", "productos")),
        ],
    )
    cortes = (
        *(
            f"- {fuente.clave.capitalize()}: {fecha_larga(fuente.corte)}."
            for fuente in cobertura.fuentes
        ),
        "No tengo datos posteriores a esas fechas.",
    )
    documentos = list(
        _tabla(ENCABEZADOS_DE_DOCUMENTOS, [_fila_de_documento(d) for d in cobertura.documentos])
    )
    if cobertura.no_incluidos:
        codigos = _unir([faltante.codigo for faltante in cobertura.no_incluidos])
        documentos.append(
            f"No vienen, aunque se mencionan en ellos: {codigos}. No tengo su contenido."
        )
    ejemplos = tuple(
        linea for ejemplo in cobertura.ejemplos for linea in _lineas_de_ejemplo(ejemplo)
    )
    return (
        Seccion(f"Tiendas ({entero(len(cobertura.tiendas))})", tiendas),
        Seccion(TITULO_DE_PERIODOS, periodos),
        Seccion(TITULO_DE_CORTES, cortes),
        Seccion(f"Documentos ({entero(len(cobertura.documentos))})", tuple(documentos)),
        Seccion(TITULO_DE_EJEMPLOS, ejemplos),
    )


def texto(cobertura: Manifiesto) -> str:
    """La respuesta a "qué sabes": una línea de lo que cubre y las cinco secciones, en texto plano."""
    return "\n\n".join(
        (_introduccion(cobertura), *(seccion.texto for seccion in secciones(cobertura)))
    )


def mensaje_sin_fuente(error: construir.ErrorConstruccion) -> str:
    """Lo que lee la gerente si no hay base: el mensaje de responder(), con el nombre del archivo y sin la ruta."""
    return comun.error("base", fuente_no_disponible_de(error)).resumen


# ------------------------------------------------------------------ qué mensaje es "qué sabes"

# Más largo que esto (ya normalizado) no es una frase de cobertura. Acota también el trabajo de la expresión regular.
MAXIMO_DE_CARACTERES = 80

_QUE = r"(?:que|q|ke)"
_PREFIJO = r"(?:(?:oye|disculpa|perdon|a ver|dime|cuentame|digame) )?"
_SALUDOS = rf"(?:hola|holi|ola|buenas|buenos dias|buen dia|buenas tardes|buenas noches|hey|saludos|{_QUE} tal)"
_NUCLEOS = (
    rf"{_QUE} sabes(?: hacer| decir| responder| contestar| consultar)?(?: tu)?",
    rf"{_QUE} puedes(?: hacer| decir| decirme| responder| contestar| consultar)?(?: por mi)?(?: tu)?",
    rf"{_QUE} (?:haces|cubres|manejas|conoces)",
    rf"{_QUE} (?:datos|informacion|documentos|tiendas|periodos|temas|cosas|preguntas) "
    r"(?:tienes|manejas|conoces|cubres|sabes|puedes (?:contestar|responder|consultar))(?: disponibles?)?",
    rf"{_QUE}(?: te)? puedo (?:preguntar|preguntarte|consultar|pedirte|hacer)(?: aqui| contigo)?",
    rf"(?:en|con) {_QUE} (?:me )?(?:puedes|podrias|sabes) (?:ayudar|ayudarme|apoyar|apoyarme)",
    r"como (?:me )?(?:puedes|podrias) (?:ayudar|ayudarme)",
    r"(?:ayuda|ayudame|necesito ayuda|me ayudas|auxilio|help|menu|opciones|instrucciones)",
    r"como (?:funcionas|te uso|se usa|funciona esto|uso esto)",
    r"para que sirves",
    rf"(?:quien|{_QUE}) eres(?: tu)?",
)
_NUCLEO = "|".join(_NUCLEOS)
_CORTESIAS = r"(?: (?:por favor|porfa|porfis|gracias))*"
_FRASE_DE_COBERTURA = re.compile(
    rf"{_PREFIJO}(?:{_SALUDOS} )?(?:{_NUCLEO}){_CORTESIAS}"
)


def _normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y con un espacio por cada grupo de signos: '¿Qué sabes?' pasa a 'que sabes'."""
    descompuesto = unicodedata.normalize("NFD", texto.casefold())
    sin_acentos = "".join(
        letra for letra in descompuesto if unicodedata.category(letra) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", sin_acentos).strip()


def pide_la_cobertura(pregunta: str) -> bool:
    """El mensaje es solo "qué sabes" o ayuda, con o sin un saludo antes y palabras de cortesía alrededor.

    Es estricto: el mensaje entero tiene que ser una de esas frases. "Qué sabes de las ventas de Cumbres" o "Hola,
    cuánto vendió Cumbres" tienen una pregunta de datos adentro y van al modelo, que las contesta con las herramientas;
    un falso positivo le daría a la gerente la lista de lo que cubre en lugar de su cifra. Un falso negativo cuesta una
    llamada al modelo, que contesta bien, así que la duda se resuelve a favor del modelo.

    Un saludo solo ("hola", "buenos días") no entra, aunque la palanca 5 del plan lo nombre junto a "qué sabes" y la
    ayuda: dos pruebas de la interfaz de T4.1 usan "hola" como un mensaje que llega al modelo. Atenderlo sin modelo
    (T6.2) es quitar la restricción de _FRASE_DE_COBERTURA y cambiar ese mensaje en esas dos pruebas.
    """
    if len(pregunta) > MAXIMO_DE_CARACTERES * 4:
        return False
    normalizada = _normalizar(pregunta)
    if not normalizada or len(normalizada) > MAXIMO_DE_CARACTERES:
        return False
    return _FRASE_DE_COBERTURA.fullmatch(normalizada) is not None


# ------------------------------------------------------------------ el turno sin modelo


def es_del_manifiesto(respuesta: modulo_agente.Respuesta) -> bool:
    """La respuesta salió de este módulo y no de responder(): su traza no tiene modelo."""
    return respuesta.traza.modelo == MODELO


def _traza(hash_fuentes: str | None, base_reconstruida: bool) -> modulo_agente.Traza:
    """La traza de un turno sin llamadas: 0 tokens, con el prompt y las herramientas del código que corrió."""
    return modulo_agente.Traza(
        modelo=MODELO,
        prompt_sha256=modulo_agente.PROMPT_SHA256,
        herramientas=modulo_agente.NOMBRES_HERRAMIENTAS,
        herramientas_sha256=modulo_agente.HERRAMIENTAS_SHA256,
        llamadas=(),
        entrada_tokens=0,
        salida_tokens=0,
        reintentos_validacion=0,
        hash_fuentes=hash_fuentes,
        base_reconstruida=base_reconstruida,
    )


def _respuesta(
    turno_id: str, leido: Manifiesto | construir.ErrorConstruccion
) -> modulo_agente.Respuesta:
    if isinstance(leido, construir.ErrorConstruccion):
        return modulo_agente.Respuesta(
            mensaje_sin_fuente(leido),
            "fuente_no_disponible",
            (),
            _traza(None, False),
            (),
            turno_id,
        )
    return modulo_agente.Respuesta(
        texto(leido),
        "ok",
        (),
        _traza(leido.hash_fuentes, leido.base_reconstruida),
        (),
        turno_id,
    )


def responder(
    pregunta: str,
    historial: modulo_historial.Historial | None = None,
    *,
    base: Path = construir.BASE,
    dir_datos: Path = construir.DIR_DATOS,
    dir_config: Path = construir.DIR_CONFIG,
    hoy_real: dt.date | None = None,
    archivo_registro: Path | None = None,
    origen: modulo_registro.Origen = "interfaz",
    categoria: str | None = None,
) -> modulo_agente.Respuesta:
    """Responde con el manifiesto, sin modelo, y deja la línea del turno en el registro si se da archivo_registro.

    Los parámetros son los de agente.responder() que aplican, para que la interfaz y el comando de evaluación llamen a
    una o a otra con las mismas opciones. El historial solo da el id de la sesión: la vuelta no se guarda en él.

    Si no hay base que consultar (falta un archivo, no abre), la Respuesta sale con estado "fuente_no_disponible" y
    el mensaje de siempre, sin cifra, y también deja su línea. Un error que no sea de la base es un defecto y se deja
    pasar: quien llama (la interfaz) lo convierte en un mensaje fijo. Si el registro no se puede escribir, la Respuesta
    sale igual (K27).
    """
    inicio = time.monotonic()
    hora = modulo_registro.ahora()
    hoy = hoy_real or dt.date.today()
    turno_id = str(uuid.uuid4())
    try:
        leido: Manifiesto | construir.ErrorConstruccion = leer(
            base=base, dir_datos=dir_datos, dir_config=dir_config
        )
    except construir.ErrorConstruccion as error:
        leido = error
    respuesta = _respuesta(turno_id, leido)
    if archivo_registro is not None:
        modulo_registro.registrar_turno(
            archivo_registro,
            respuesta,
            pregunta=pregunta,
            historial=(),
            id_sesion=(
                historial.id_sesion
                if isinstance(historial, modulo_historial.Historial)
                else None
            ),
            origen=origen,
            categoria=categoria,
            hora=hora,
            latencia_ms=round((time.monotonic() - inicio) * 1000),
            hoy_real=hoy,
            topes=modulo_agente.TOPES,
            ajustes=modulo_agente.AJUSTES,
        )
    return respuesta
