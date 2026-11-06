"""Comparación de modelos: python -m eval.comparacion (T6.3, D7-A, C6.1, C6.2, C5.2).

Uso, desde la raíz del repositorio, con uno o más reportes de eval.correr (la ruta, o el nombre si está en eval/reportes/):

    .venv\\Scripts\\python -m eval.comparacion <reporte.json> [<reporte.json> ...]

Escribe eval/reportes/comparacion-modelos.md: por modelo, commit, exactitud, tokens de entrada y de salida, costo, latencia p50 y
p90, solicitudes y, por categoría, el acierto y el uso. No llama a ningún modelo. Sale con 0 aunque la elección quede abierta,
y con SALIDA_REPORTES_INVALIDOS si los reportes no se pueden comparar: entonces no toca el archivo.

Qué se puede comparar. Reportes enteros de eval.correr, uno por modelo de D7-A, del mismo commit (con los mismos cambios sin
commit), del mismo alcance, con la misma configuración y las mismas corridas. Un rejuzgado no entra: su commit es el del juez y
sus respuestas salen de otro, así que el chequeo de commits se engañaría.

La regla (#comparacion en el plan): el más barato cuya exactitud cae dentro del rango del mejor y que cumple las metas de
latencia (#metas: p50 de 5 s, p90 de 10 s y máximo de 60 s); con empate, el que corre en un plan gratuito. El mejor es el de la
cifra más alta de su rango, y un modelo cae dentro si su propia cifra más alta llega a la más baja del mejor. El costo es
uso_total.costo_usd del reporte, al precio de pago de config/precios.yaml: con el $0 del plan gratuito, Gemini ganaría siempre.
Empate es el mismo costo exacto. Si falta un modelo de D7-A, o si un candidato no tiene precio, la elección queda abierta: un
modelo que compitió solo no gana, y de los que faltan no se escribe ninguna cifra. --sin-medir <id>=<motivo> declara que un modelo
de D7-A no se pudo medir: la regla decide entre los demás y el texto dice que ese no compitió y por qué, sin ninguna cifra suya.

El juez. La exactitud de la regla es acierto_reglas en los tres modelos: el juez usa el modelo de la corrida y no hereda la
validación de otro (eval/juez.py, estado_de_validacion()), así que un modelo se juzgaría y otro no. El acierto con juez va en su
columna y solo si el reporte dice que se usó: sin juez, `acierto` es igual a las reglas y escribirlo diría que el juez lo vio.
`por_categoria` del reporte sí trae lo que el juez reprobó; aquí se le devuelve con el banco, porque el reporte no guarda esa
tabla con reglas solas. El juez solo califica lo que las reglas aprobaron (juez.se_juzga), así que cada reprobación es un acierto
de reglas, y el veredicto del criterio propio de un compuesto alcanza a todas sus partes, como en reporte._filas_de_pregunta.

Los tokens, el costo y la latencia juntan los turnos de las corridas sin variantes y sin los que tronaron, como el reporte. Los
números se formatean con el formato de `,` de Python: coma de miles y punto decimal.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from eval import banco, juez, reporte
from eval.reporte import entero, numero, percentil

DIR_REPORTES = banco.RAIZ / "eval" / "reportes"
SALIDA = DIR_REPORTES / "comparacion-modelos.md"

SALIDA_BIEN = 0
SALIDA_REPORTES_INVALIDOS = 1

META_P50_MS = 5_000
META_P90_MS = 10_000
META_MAXIMO_MS = 60_000

ABIERTA = "La elección queda abierta"
CLAVES_DE_META = ("commit", "modelos", "configuracion", "alcance", "variantes", "corridas_pedidas", "completo")
CLAVES_DEL_REPORTE = ("acierto_reglas", "juez", "por_categoria", "uso_por_categoria", "uso_total", "preguntas", "turnos")
ESTADO_SIN_MEDIDA = "excepcion"


@dataclass(frozen=True)
class ModeloDeD7:
    id_base: str
    id_corrida: str
    nombre: str
    proveedor: str
    plan_gratuito: bool


# D7-A y la columna «Plan gratuito» de los precios en el plan. Haiku 5.5 no tiene plan gratuito con límites publicados.
MODELOS_D7A = (
    ModeloDeD7("claude-haiku-5-5", "anthropic:claude-haiku-5-5", "Haiku 5.5", "Anthropic", False),
    ModeloDeD7("gemini-3.5-flash-lite", "google:gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "Google", True),
    ModeloDeD7("gemini-3.8-flash", "google:gemini-3.8-flash", "Gemini 3.8 Flash", "Google", True),
)
PROVEEDORES = {
    "Anthropic": "cuenta contra el tope de la llave de Anthropic",
    "Google": "no sale del tope de la llave de Anthropic; a precio de pago, sin importar el plan con que corrió",
}


class ReportesInvalidos(ValueError):
    """Los reportes no se pueden comparar. El mensaje dice cuáles y por qué."""


@dataclass(frozen=True)
class Medida:
    """Lo que un reporte aporta a la comparación."""

    modelo: ModeloDeD7
    archivo: str
    datos: Mapping[str, Any]
    reglas: Mapping[str, Any]
    categorias_con_reglas: Mapping[str, Any]
    costo: Decimal | None
    turnos: Sequence[Mapping[str, Any]]
    latencia_p50: float
    latencia_p90: float
    latencia_maxima: float

    @property
    def nombre(self) -> str:
        return self.modelo.nombre

    @property
    def juez(self) -> Mapping[str, Any]:
        return self.datos["juez"]

    @property
    def uso(self) -> Mapping[str, Any]:
        return self.datos["uso_total"]

    @property
    def exactitud_maxima(self) -> float:
        return self.reglas["rango"]["maximo"]

    @property
    def exactitud_minima(self) -> float:
        return self.reglas["rango"]["minimo"]


@dataclass(frozen=True)
class Eleccion:
    ganador: ModeloDeD7 | None
    conclusion: str
    fuera: tuple[str, ...] = ()


# ------------------------------------------------------------------ leer y revisar


def _leer(ruta: Path) -> Any:
    real = ruta if ruta.exists() else DIR_REPORTES / ruta
    try:
        return json.loads(real.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReportesInvalidos(f"{ruta}: no se pudo leer ({type(error).__name__}).") from error


def _modelo_de_d7(nombre: str, meta: Mapping[str, Any]) -> ModeloDeD7:
    modelos = meta["modelos"]
    if len(modelos) != 1:
        raise ReportesInvalidos(f"{nombre}: se compara un modelo por reporte y este trae {len(modelos)}: {', '.join(modelos)}.")
    # El proveedor se escribe de más de una forma según cómo se creó el modelo: lo que cuenta es el id.
    id_base = modelos[0].rsplit(":", maxsplit=1)[-1]
    for modelo in MODELOS_D7A:
        if modelo.id_base == id_base:
            return modelo
    aceptados = ", ".join(modelo.id_corrida for modelo in MODELOS_D7A)
    raise ReportesInvalidos(f"{nombre}: {modelos[0]} no es de D7-A, que compara {aceptados}.")


def _revisar_reporte(nombre: str, datos: Any) -> ModeloDeD7:
    """Que sea un reporte de eval.correr, entero y de un modelo de D7-A."""
    es_reporte = isinstance(datos, dict) and isinstance(datos.get("meta"), dict)
    faltan_en_meta = [clave for clave in CLAVES_DE_META if clave not in datos["meta"]] if es_reporte else []
    if not es_reporte or faltan_en_meta:
        raise ReportesInvalidos(f"{nombre}: no es un reporte de python -m eval.correr.")
    if "rejuzgado" in datos:
        raise ReportesInvalidos(
            f"{nombre}: es un rejuzgado: su commit es el del juez y sus respuestas salen de otro. Pasa el reporte de la corrida."
        )
    faltan = [clave for clave in CLAVES_DEL_REPORTE if clave not in datos]
    if faltan:
        raise ReportesInvalidos(f"{nombre}: le falta {', '.join(faltan)}: es de una versión anterior de eval.correr. Corre el set otra vez.")
    meta = datos["meta"]
    if not (meta["commit"] or {}).get("sha"):
        raise ReportesInvalidos(f"{nombre}: no dice de qué commit salió.")
    if not meta["completo"]:
        detalle = meta.get("detenida") or "le faltan prompts o tuvo fallas del comando"
        raise ReportesInvalidos(f"{nombre}: quedó a medias ({detalle}). Termínalo con python -m eval.correr --reanudar.")
    return _modelo_de_d7(nombre, meta)


def _texto_de_commit(datos: Mapping[str, Any]) -> str:
    meta = datos["meta"]
    sha = meta["commit"]["sha"][:7]
    cambios = meta.get("cambios_sin_commit")
    return f"{sha} con cambios sin commit {cambios[:8]}" if cambios else sha


def _texto_de_alcance(datos: Mapping[str, Any]) -> str:
    meta = datos["meta"]
    return f"{meta['alcance']}, {'con' if meta['variantes'] else 'sin'} variantes"


# Lo que tiene que coincidir, con el problema si no coincide y qué hacer. El commit va primero: es el que explica a los demás.
CHEQUEOS_DE_LA_BATERIA: tuple[tuple[str, Callable[[Mapping[str, Any]], str], str], ...] = (
    ("commits distintos", _texto_de_commit, "La comparación pide el mismo código: corre los modelos sobre un solo commit."),
    ("alcances distintos", _texto_de_alcance, "La comparación pide la misma batería: corre todos con el mismo alcance."),
    (
        "configuraciones distintas",
        lambda datos: datos["meta"]["configuracion"],
        "La comparación pide la misma configuración: corre todos con el mismo --configuracion.",
    ),
    (
        "corridas distintas",
        lambda datos: f"{datos['meta']['corridas_pedidas']} corridas",
        "La comparación pide las mismas corridas: corre todos con el mismo --corridas.",
    ),
)


def _ids_de_prompts(datos: Mapping[str, Any]) -> set[str]:
    return {renglon["id"] for renglon in datos["preguntas"]}


def _revisar_que_se_comparan(reportes: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
    for problema, valor_de, consejo in CHEQUEOS_DE_LA_BATERIA:
        valores = [(nombre, valor_de(datos)) for nombre, datos in reportes]
        if len({valor for _, valor in valores}) > 1:
            detalle = "; ".join(f"{nombre}: {valor}" for nombre, valor in valores)
            raise ReportesInvalidos(f"Los reportes tienen {problema}. {detalle}. {consejo}")
    nombre_base, datos_base = reportes[0]
    for nombre, datos in reportes[1:]:
        distintos = _ids_de_prompts(datos_base) ^ _ids_de_prompts(datos)
        if distintos:
            muestra = ", ".join(sorted(distintos)[:5])
            raise ReportesInvalidos(
                f"Los reportes tienen alcances distintos: {nombre_base} y {nombre} no traen los mismos prompts ({muestra}). "
                "La comparación pide la misma batería."
            )


def _revisar_que_no_se_repiten(reportes: Sequence[tuple[str, ModeloDeD7]]) -> None:
    vistos: dict[str, str] = {}
    for nombre, modelo in reportes:
        if modelo.id_base in vistos:
            raise ReportesInvalidos(f"{vistos[modelo.id_base]} y {nombre} son del mismo modelo, {modelo.nombre}: pasa uno por modelo.")
        vistos[modelo.id_base] = nombre


# ------------------------------------------------------------------ medir


def _preguntas_de(prompt: banco.Prompt) -> Iterable[banco.Pregunta]:
    return (pregunta for paso in prompt.pasos for pregunta in paso.preguntas)


def _con_lo_devuelto(tasas: Mapping[str, Any], categoria: str, devueltas: Counter[tuple[str, int]]) -> dict[str, Any]:
    por_corrida = []
    for fila in tasas["por_corrida"]:
        aciertos = fila["aciertos"] + devueltas[(categoria, fila["corrida"])]
        por_corrida.append({**fila, "aciertos": aciertos, "porcentaje": reporte._porcentaje(aciertos, fila["n"])})
    return {"por_corrida": por_corrida, "rango": reporte._rango(por_corrida)}


def _categorias_con_reglas(nombre: str, datos: Mapping[str, Any], ruta_banco: Path) -> Mapping[str, Any]:
    """`por_categoria` sin lo que reprobó el juez. Sin reprobaciones es la misma tabla y el banco no hace falta."""
    por_categoria = datos["por_categoria"]
    ids = _ids_de_prompts(datos)
    reprobadas = {
        (veredicto["corrida"], veredicto["prompt"], veredicto["pregunta"])
        for veredicto in datos["juez"]["veredictos"]
        if veredicto["veredicto"] == juez.REPRUEBA and veredicto["prompt"] in ids
    }
    if not reprobadas:
        return por_categoria
    aviso = f"{nombre}: el banco {ruta_banco} no es el de la corrida"
    try:
        prompts = [prompt for prompt in banco.leer_banco(ruta_banco) if prompt.id in ids]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReportesInvalidos(f"{aviso} ({type(error).__name__}). Pásalo con --banco.") from error
    tamanos = Counter(pregunta.categoria for prompt in prompts for pregunta in _preguntas_de(prompt))
    for categoria, tasas in por_categoria.items():
        if any(fila["n"] != tamanos[categoria] for fila in tasas["por_corrida"]):
            raise ReportesInvalidos(f"{aviso}: la categoría {categoria} no cuadra. Pásalo con --banco.")
    devueltas: Counter[tuple[str, int]] = Counter()
    for corrida in {corrida for corrida, _, _ in reprobadas}:
        for prompt in prompts:
            for pregunta in _preguntas_de(prompt):
                if {(corrida, prompt.id, pregunta.id), (corrida, prompt.id, prompt.id)} & reprobadas:
                    devueltas[(pregunta.categoria, corrida)] += 1
    return {categoria: _con_lo_devuelto(tasas, categoria, devueltas) for categoria, tasas in por_categoria.items()}


def _turnos_medibles(nombre: str, datos: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    ids = _ids_de_prompts(datos)
    turnos = [turno for turno in datos["turnos"] if turno["prompt"] in ids and turno["estado"] != ESTADO_SIN_MEDIDA]
    if not turnos:
        raise ReportesInvalidos(f"{nombre}: no tiene turnos medibles.")
    return turnos


def _medida(nombre: str, modelo: ModeloDeD7, datos: Mapping[str, Any], ruta_banco: Path) -> Medida:
    turnos = _turnos_medibles(nombre, datos)
    latencias = [turno["latencia_ms"] for turno in turnos]
    costo = datos["uso_total"]["costo_usd"]
    return Medida(
        modelo=modelo,
        archivo=nombre,
        datos=datos,
        reglas=datos["acierto_reglas"]["alcance"],
        categorias_con_reglas=_categorias_con_reglas(nombre, datos, ruta_banco),
        costo=None if costo is None else Decimal(costo),
        turnos=turnos,
        latencia_p50=percentil(latencias, 50),
        latencia_p90=percentil(latencias, 90),
        latencia_maxima=max(latencias),
    )


# ------------------------------------------------------------------ la regla


def _y(nombres: Sequence[str]) -> str:
    if len(nombres) < 2:
        return "".join(nombres)
    return f"{', '.join(nombres[:-1])} y {nombres[-1]}"


def _segundos(milisegundos: float) -> str:
    return numero(milisegundos / 1000)


def _texto_de_exactitud(medida: Medida) -> str:
    return reporte._texto_de_rango(medida.reglas["rango"])


def _faltantes(medidas: Sequence[Medida], sin_medir: Mapping[str, str] | None = None) -> list[ModeloDeD7]:
    presentes = {medida.modelo.id_base for medida in medidas}
    declarados = sin_medir or {}
    return [modelo for modelo in MODELOS_D7A if modelo.id_base not in presentes and modelo.id_corrida not in declarados]


def _fuera_de_rango(medida: Medida, mejor: Medida) -> str | None:
    if medida.exactitud_maxima >= mejor.exactitud_minima:
        return None
    return f"{medida.nombre}: exactitud de {_texto_de_exactitud(medida)}, fuera del rango del mejor ({mejor.nombre}, {_texto_de_exactitud(mejor)})."


def _fuera_de_latencia(medida: Medida) -> str | None:
    metas = (
        ("p50", medida.latencia_p50, META_P50_MS),
        ("p90", medida.latencia_p90, META_P90_MS),
        ("máximo", medida.latencia_maxima, META_MAXIMO_MS),
    )
    incumplidas = [f"{dato} de {_segundos(valor)} s (meta {_segundos(meta)} s)" for dato, valor, meta in metas if valor > meta]
    return f"{medida.nombre}: latencia {', '.join(incumplidas)}." if incumplidas else None


def _dato_de_las_salidas(por_exactitud: Sequence[str], por_latencia: Sequence[str]) -> str:
    if por_exactitud and por_latencia:
        return "la exactitud y la latencia"
    return "la exactitud" if por_exactitud else "la latencia"


def _el_mas_barato(candidatos: Sequence[Medida], fuera: tuple[str, ...]) -> Eleccion:
    """Entre los que pasaron, todos con precio. Con empate decide el plan gratuito; si no lo resuelve, la regla no alcanza."""
    menor = min(candidato.costo for candidato in candidatos)
    empatados = [candidato for candidato in candidatos if candidato.costo == menor]
    costo = reporte._costo(menor)
    if len(empatados) == 1:
        ganador = empatados[0]
        contra = [f"{reporte._costo(otro.costo)} de {otro.nombre}" for otro in candidatos if otro is not ganador]
        return Eleccion(ganador.modelo, f"Lo decidió el costo: {ganador.nombre} cuesta {costo} USD contra {_y(contra)}.", fuera)
    nombres = _y([candidato.nombre for candidato in empatados])
    gratuitos = [candidato for candidato in empatados if candidato.modelo.plan_gratuito]
    if len(gratuitos) == 1:
        ganador = gratuitos[0]
        return Eleccion(ganador.modelo, f"Lo decidió el plan gratuito: {nombres} empatan en {costo} USD y solo {ganador.nombre} corre en uno.", fuera)
    return Eleccion(None, f"{ABIERTA}: {nombres} empatan en {costo} USD y el plan gratuito no los separa.", fuera)


def elegir(medidas: Sequence[Medida], sin_medir: Mapping[str, str] | None = None) -> Eleccion:
    faltan = _faltantes(medidas, sin_medir)
    if faltan:
        presentes = _y([medida.nombre for medida in medidas])
        return Eleccion(None, f"{ABIERTA}: faltan los reportes de {_y([modelo.nombre for modelo in faltan])}, y {presentes} no gana sin competir con ellos.")
    mejor = max(medidas, key=lambda medida: (medida.exactitud_maxima, medida.exactitud_minima))
    por_exactitud = [texto for medida in medidas if (texto := _fuera_de_rango(medida, mejor))]
    por_latencia = [texto for medida in medidas if (texto := _fuera_de_latencia(medida))]
    fuera = (*por_exactitud, *por_latencia)
    candidatos = [medida for medida in medidas if not _fuera_de_rango(medida, mejor) and not _fuera_de_latencia(medida)]
    if not candidatos:
        return Eleccion(None, f"{ABIERTA}: ningún modelo cumple la regla.", fuera)
    if len(candidatos) == 1:
        dato = _dato_de_las_salidas(por_exactitud, por_latencia)
        return Eleccion(candidatos[0].modelo, f"Lo decidió {dato}: es el único que cumple las dos condiciones de la regla.", fuera)
    sin_precio = [candidato.nombre for candidato in candidatos if candidato.costo is None]
    if sin_precio:
        return Eleccion(None, f"{ABIERTA}: {_y(sin_precio)} no tiene precio en config/precios.yaml, y sin costo la regla no compara.", fuera)
    return _el_mas_barato(candidatos, fuera)


def _titular(eleccion: Eleccion) -> str:
    if eleccion.ganador is None:
        return eleccion.conclusion
    return f"Elegido: {eleccion.ganador.nombre}, para desarrollo y README. {eleccion.conclusion}"


# ------------------------------------------------------------------ escribir


def _par_de(valores: Sequence[float], formato: Callable[[float], str]) -> str:
    return f"{formato(percentil(valores, 50))} / {formato(percentil(valores, 90))}"


def _costo_por_turno(medida: Medida) -> str:
    costos = [turno["costo_usd"] for turno in medida.turnos]
    if None in costos:
        return "sin precio"
    return _par_de([float(costo) for costo in costos], reporte._costo)


def _con_juez(medida: Medida) -> str:
    if not medida.juez["usado"]:
        return f"sin juez: {medida.juez['motivo']}"
    return reporte._texto_por_corrida(medida.datos["acierto"]["alcance"])


def _renglon_de_exactitud(medida: Medida) -> list[str]:
    latencia = f"{_segundos(medida.latencia_p50)} / {_segundos(medida.latencia_p90)} / {_segundos(medida.latencia_maxima)}"
    return [
        medida.nombre,
        _texto_de_commit(medida.datos),
        reporte._texto_por_corrida(medida.reglas),
        _con_juez(medida),
        reporte._costo(medida.costo),
        _costo_por_turno(medida),
        latencia,
        "no cumple" if _fuera_de_latencia(medida) else "cumple",
    ]


def _renglon_de_tokens(medida: Medida) -> list[str]:
    return [
        medida.nombre,
        entero(medida.uso["entrada_tokens"]),
        _par_de([turno["entrada_tokens"] for turno in medida.turnos], entero),
        entero(medida.uso["salida_tokens"]),
        _par_de([turno["salida_tokens"] for turno in medida.turnos], entero),
    ]


def _renglon_de_solicitudes(medida: Medida) -> list[str]:
    corridas = medida.datos["meta"]["corridas_pedidas"]
    return [
        medida.nombre,
        entero(medida.uso["n_llamadas"]),
        numero(medida.uso["n_llamadas"] / corridas),
        entero(medida.uso["reintentos"]),
        entero(medida.juez["uso"]["llamadas"]),
    ]


def _reintentos_en_la_latencia(medidas: Sequence[Medida]) -> list[str]:
    lineas = []
    for medida in medidas:
        espera = sum(turno.get("espera_reintentos_segundos") or 0 for turno in medida.turnos)
        if medida.uso["reintentos"]:
            lineas.append(
                f"{medida.nombre} tuvo {entero(medida.uso['reintentos'])} reintentos con {numero(espera)} s de espera, que cuentan en su latencia."
            )
    return lineas


def _seccion_por_modelo(medidas: Sequence[Medida]) -> list[str]:
    datos = medidas[0].datos
    meta = datos["meta"]
    alcance = (
        f"Alcance: {_texto_de_alcance(datos)}, {entero(len(_ids_de_prompts(datos)))} prompts, {entero(meta['corridas_pedidas'])} "
        f"corridas por modelo, configuración {meta['configuracion']}."
    )
    return [
        "## Por modelo",
        "",
        alcance,
        "",
        *reporte._tabla(
            (
                "Modelo",
                "Commit",
                "Exactitud con reglas solas",
                "Con juez, aparte",
                "Costo total (USD)",
                "Costo por turno, p50 / p90 (USD)",
                "Latencia p50 / p90 / máximo (s)",
                "Metas de latencia",
            ),
            map(_renglon_de_exactitud, medidas),
        ),
        "Los conteos de tokens no se comparan entre proveedores: Haiku 5.5 cuenta cerca de 30 % más para el mismo texto [A4]. Por eso la regla decide por costo.",
        "",
        *reporte._tabla(
            ("Modelo", "Tokens de entrada, total", "Entrada por turno, p50 / p90", "Tokens de salida, total", "Salida por turno, p50 / p90"),
            map(_renglon_de_tokens, medidas),
        ),
        "Solicitudes para T6.4. Un prompt que se repitió con --reanudar no se guardó, así que sus solicitudes no cuentan aquí.",
        "",
        *reporte._tabla(
            ("Modelo", "Solicitudes de los turnos", "Por corrida", "Reintentos", "Solicitudes del juez"),
            map(_renglon_de_solicitudes, medidas),
        ),
        *[linea for texto in _reintentos_en_la_latencia(medidas) for linea in (texto, "")],
    ]


def _celda_de_categoria(tasas: Mapping[str, Any] | None) -> str:
    return "sin datos" if tasas is None else reporte._texto_por_corrida(tasas)


def _seccion_por_categoria(medidas: Sequence[Medida]) -> list[str]:
    juzgadas = [medida for medida in medidas if medida.juez["usado"]]
    categorias = sorted({categoria for medida in medidas for categoria in medida.categorias_con_reglas})
    encabezados = [
        "Categoría",
        *(f"{medida.nombre}, reglas solas" for medida in medidas),
        *(f"{medida.nombre}, con juez" for medida in juzgadas),
    ]
    renglones = [
        [
            categoria,
            *(_celda_de_categoria(medida.categorias_con_reglas.get(categoria)) for medida in medidas),
            *(_celda_de_categoria(medida.datos["por_categoria"].get(categoria)) for medida in juzgadas),
        ]
        for categoria in categorias
    ]
    return ["## Acierto por categoría", "", *reporte._tabla(encabezados, renglones)]


def _renglones_de_uso(categoria: str, medidas: Sequence[Medida]) -> Iterable[list[str]]:
    for medida in medidas:
        uso = medida.datos["uso_por_categoria"].get(categoria)
        if uso is None:
            continue
        costo = uso["costo_usd"]
        yield [
            categoria,
            medida.nombre,
            entero(uso["n"]),
            f"{entero(uso['entrada_tokens']['p50'])} / {entero(uso['entrada_tokens']['p90'])}",
            f"{entero(uso['salida_tokens']['p50'])} / {entero(uso['salida_tokens']['p90'])}",
            "sin precio" if costo is None else f"{reporte._costo(costo['p50'])} / {reporte._costo(costo['p90'])}",
            f"{_segundos(uso['latencia_ms']['p50'])} / {_segundos(uso['latencia_ms']['p90'])}",
        ]


def _seccion_de_uso_por_categoria(medidas: Sequence[Medida]) -> list[str]:
    categorias = sorted({categoria for medida in medidas for categoria in medida.datos["uso_por_categoria"]})
    encabezados = ("Categoría", "Modelo", "Turnos", "Entrada, p50 / p90", "Salida, p50 / p90", "Costo, p50 / p90 (USD)", "Latencia, p50 / p90 (s)")
    renglones = [renglon for categoria in categorias for renglon in _renglones_de_uso(categoria, medidas)]
    return ["## Uso por categoría", "", *reporte._tabla(encabezados, renglones)]


def _suma_de_costos(costos: Sequence[Any]) -> str:
    """Una suma con un costo sin precio pasaría por completa: se deja sin cifra."""
    if None in costos:
        return "sin precio"
    return reporte._costo(sum((Decimal(costo) for costo in costos), Decimal(0)))


def _renglon_de_gasto(proveedor: str, nota: str, medidas: Sequence[Medida]) -> list[str]:
    propias = [medida for medida in medidas if medida.modelo.proveedor == proveedor]
    if not propias:
        return [proveedor, "sin reporte", "sin reporte", "sin reporte", nota]
    return [
        proveedor,
        ", ".join(medida.nombre for medida in propias),
        _suma_de_costos([medida.uso["costo_usd"] for medida in propias]),
        _suma_de_costos([medida.juez["uso"]["costo_usd"] for medida in propias]),
        nota,
    ]


def _seccion_de_gasto(medidas: Sequence[Medida]) -> list[str]:
    return [
        "## Gasto por proveedor",
        "",
        *reporte._tabla(
            ("Proveedor", "Modelos", "Turnos (USD)", "Juez (USD)", "Tope"),
            (_renglon_de_gasto(proveedor, nota, medidas) for proveedor, nota in PROVEEDORES.items()),
        ),
    ]


def _comando_de_corrida(id_del_modelo: str, meta: Mapping[str, Any]) -> str:
    completo = " --completo" if meta["alcance"] == "completo" else ""
    variantes = " --variantes" if meta["variantes"] else ""
    return f"python -m eval.correr{completo}{variantes} --corridas {meta['corridas_pedidas']} --modelo {id_del_modelo}"


def _lineas_sin_medir(sin_medir: Mapping[str, str] | None) -> list[str]:
    nombres = {modelo.id_corrida: modelo.nombre for modelo in MODELOS_D7A}
    return [f"Sin medir: {nombres[id_corrida]} no compitió. {motivo}." for id_corrida, motivo in (sin_medir or {}).items()]


def _seccion_de_faltantes(medidas: Sequence[Medida], sin_medir: Mapping[str, str] | None = None) -> list[str]:
    lineas_sin_medir = _lineas_sin_medir(sin_medir)
    seccion = [*lineas_sin_medir, ""] if lineas_sin_medir else []
    faltan = _faltantes(medidas, sin_medir)
    if not faltan:
        return seccion
    meta = medidas[0].datos["meta"]
    return [
        *seccion,
        f"Faltan, sobre el commit {_texto_de_commit(medidas[0].datos)} y sin cifras hasta que corran:",
        "",
        *(f"- {modelo.nombre}: `{_comando_de_corrida(modelo.id_corrida, meta)}`" for modelo in faltan),
        "",
    ]


def _regla_en_texto(medidas: Sequence[Medida]) -> list[str]:
    lineas = [
        "Regla (#comparacion en el plan): el más barato cuya exactitud con reglas solas cae dentro del rango del mejor y que cumple "
        f"las metas de latencia (p50 de {_segundos(META_P50_MS)} s, p90 de {_segundos(META_P90_MS)} s y máximo de {_segundos(META_MAXIMO_MS)} s); "
        "con empate en costo, el que corre en un plan gratuito. El costo es a precio de pago, también en los modelos con plan gratuito, donde cuestan $0.",
        "",
    ]
    if medidas[0].datos["meta"]["corridas_pedidas"] == 1:
        lineas += ["Con 1 corrida el rango del mejor es un solo punto: entrar en él pide igualar su acierto. La segunda corrida queda en P2.", ""]
    return lineas


def _seccion_del_set_completo(eleccion: Eleccion, medidas: Sequence[Medida]) -> list[str]:
    if medidas[0].datos["meta"]["alcance"] == "completo":
        return ["Estos reportes ya son del set completo.", ""]
    id_del_modelo = eleccion.ganador.id_corrida if eleccion.ganador else "<id>"
    return [
        "El Acepta de T6.3 pide además que el modelo del README corra el set completo, con "
        f"`python -m eval.correr --completo --modelo {id_del_modelo}`. Ese reporte tiene otro alcance y no entra en esta comparación.",
        "",
    ]


def a_markdown(medidas: Sequence[Medida], eleccion: Eleccion, sin_medir: Mapping[str, str] | None = None) -> str:
    return "\n".join(
        [
            "# Comparación de modelos (T6.3, D7-A)",
            "",
            _titular(eleccion),
            "",
            *[f"- {texto}" for texto in eleccion.fuera],
            *([""] if eleccion.fuera else []),
            *_seccion_de_faltantes(medidas, sin_medir),
            *_regla_en_texto(medidas),
            *_seccion_por_modelo(medidas),
            *_seccion_por_categoria(medidas),
            *_seccion_de_uso_por_categoria(medidas),
            *_seccion_de_gasto(medidas),
            *_seccion_del_set_completo(eleccion, medidas),
            "Reportes leídos:",
            "",
            *(f"- {medida.nombre}: `{medida.archivo}`, del {medida.datos['meta'].get('fecha', 'sin fecha')}" for medida in medidas),
            "",
        ]
    )


def _resumen_de_consola(
    medidas: Sequence[Medida], eleccion: Eleccion, destino: Path, sin_medir: Mapping[str, str] | None = None
) -> str:
    solicitudes = [
        f"Solicitudes de {medida.nombre}: {entero(medida.uso['n_llamadas'])} de los turnos y {entero(medida.juez['uso']['llamadas'])} del juez."
        for medida in medidas
    ]
    return "\n".join([_titular(eleccion), *eleccion.fuera, *_lineas_sin_medir(sin_medir), *solicitudes, f"Comparación: {destino}"])


# ------------------------------------------------------------------ el comando


def comparar(rutas: Sequence[Path], ruta_banco: Path = banco.BANCO) -> list[Medida]:
    """Lee y revisa los reportes. Lanza ReportesInvalidos con lo que no cuadra, antes de calcular nada."""
    reportes = [(ruta.name, _leer(ruta)) for ruta in rutas]
    modelos = [(nombre, _revisar_reporte(nombre, datos)) for nombre, datos in reportes]
    _revisar_que_se_comparan(reportes)
    _revisar_que_no_se_repiten(modelos)
    return [
        _medida(nombre, modelo, datos, ruta_banco)
        for (nombre, datos), (_, modelo) in zip(reportes, modelos, strict=True)
    ]


def _leer_sin_medir(declaraciones: Sequence[str], medidas: Sequence[Medida]) -> dict[str, str]:
    """Las declaraciones <id>=<motivo> de --sin-medir. Lanza ReportesInvalidos si una no cuadra."""
    de_d7 = {modelo.id_corrida for modelo in MODELOS_D7A}
    con_reporte = {medida.modelo.id_corrida for medida in medidas}
    sin_medir = {}
    for declaracion in declaraciones:
        id_corrida, _, motivo = declaracion.partition("=")
        if id_corrida not in de_d7:
            raise ReportesInvalidos(f"--sin-medir: {id_corrida} no es un modelo de D7-A.")
        if id_corrida in con_reporte:
            raise ReportesInvalidos(f"--sin-medir: {id_corrida} sí trae reporte.")
        if not motivo.strip():
            raise ReportesInvalidos(f"--sin-medir: {id_corrida} no dice el motivo; se escribe <id>=<motivo>.")
        sin_medir[id_corrida] = motivo.strip()
    return sin_medir


def _argumentos(argumentos: Sequence[str] | None) -> argparse.Namespace:
    lector = argparse.ArgumentParser(prog="python -m eval.comparacion", description="Compara los modelos de D7-A y aplica la regla de elección.")
    lector.add_argument("reportes", nargs="+", type=Path, help="los .json de python -m eval.correr, uno por modelo")
    lector.add_argument("--banco", type=Path, default=banco.BANCO, help="el banco de la corrida, si no fue el de eval/preguntas.yaml")
    lector.add_argument(
        "--sin-medir",
        action="append",
        default=[],
        metavar="ID=MOTIVO",
        help="un modelo de D7-A que no se pudo medir y por qué: la regla decide entre los demás y el texto lo dice",
    )
    return lector.parse_args(argumentos)


def main(argumentos: Sequence[str] | None = None, *, salida: Path = SALIDA) -> int:
    """`python -m eval.comparacion`. El parámetro de palabra clave es para las pruebas: la línea de comandos no lo cambia."""
    # En Windows, una salida redirigida usa cp1252, que no tiene todos los caracteres del español que se escriben aquí.
    for flujo in (sys.stdout, sys.stderr):
        if isinstance(flujo, io.TextIOWrapper):
            flujo.reconfigure(encoding="utf-8")
    opciones = _argumentos(argumentos)
    try:
        medidas = comparar(opciones.reportes, opciones.banco)
        sin_medir = _leer_sin_medir(opciones.sin_medir, medidas)
    except ReportesInvalidos as error:
        print(f"No se escribió la comparación. {error}", file=sys.stderr)
        return SALIDA_REPORTES_INVALIDOS
    eleccion = elegir(medidas, sin_medir)
    destino = Path(salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(a_markdown(medidas, eleccion, sin_medir), encoding="utf-8", newline="\n")
    print(_resumen_de_consola(medidas, eleccion, destino, sin_medir))
    return SALIDA_BIEN


if __name__ == "__main__":
    raise SystemExit(main())
