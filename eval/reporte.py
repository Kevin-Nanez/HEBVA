"""El reporte de python -m eval.correr (T5.3, T5.9, C5.2, C5.3, C1.3, C6.1, D25, D26, K32).

Se arma con los informes de pydantic-evals, uno por corrida, y da:

- el rango entre corridas y «acierto en todas las corridas» por pregunta, del alcance y del núcleo: el del núcleo sale de las
  mismas corridas, sin gastar de más;
- el acierto por bloque (por prompt), por categoría y por tipo de respuesta (por pregunta), siempre con su n;
- con el juez validado (T5.4), el acierto con juez y con reglas solas lado a lado, por alcance, núcleo y bloque; cuántos casos
  cambió el juez en cada sentido, el acuerdo validado con su intervalo, cuántos veredictos son del criterio propio de un
  compuesto, cuántos quedaron sin veredicto y su uso, aparte del de los turnos. Solo cuenta los veredictos de los prompts y las
  corridas de este reporte. Sin validación, el acierto sale de reglas y dice el motivo;
- la matriz de estados y la de conducta, de donde salen las falsas abstenciones y, aparte, las aclaraciones donde se esperaba
  cifra y las negativas que traen parte de lo esperado;
- las etapas por separado: estado, cifras, citas, conducta, fugas, herramienta, parámetros, recall@4 (al menos una sección
  esperada entre los 4 de BM25, D26-A), la fracción de las esperadas y MRR;
- cifras y citas sin evidencia en la primera versión del modelo, y fugas;
- C4.4, C3.3 y K29 sobre el texto con su pie (D24);
- tokens, costo y latencia p50 y p90 por categoría, con el crecimiento por turno de la conversación larga (C6.1);
- la tabla de T6.1, en un renglón por categoría y uno de todas: mediana y p90 de entrada, salida, costo y llamadas, latencia p50
  y p90 y exactitud, con los campos de #metas en el plan. Los percentiles juntan los turnos de todas las corridas;
- el commit, el modelo, los hashes y la configuración, las fallas con su causa y cada turno con su turno_id, que lo une con
  su línea de logs/turnos.jsonl.

Un turno sin_signos de dos categorías cuenta su uso en las dos: el total del reporte no suma por categoría, sale de los
turnos. Las variantes no entran a ningún rango (D25): van en su propia sección. Los números se formatean con el formato de
`,` de Python, que no depende del locale: coma de miles y punto decimal. Los percentiles interpolan entre las dos muestras
vecinas, como statistics.quantiles(method="inclusive").
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from agente import registro
from agente.herramientas.comun import dumps
from eval import banco, evaluadores, juez
from eval.evaluadores import TurnoEvaluado

MOTIVO_SIN_CONSULTA = "no se consultó su validación"
NIVEL_DEL_JUEZ = (
    "parte: una llamada por pregunta del banco en el turno que la contestó, y una más por compuesto cuyo criterio reprueba algo por su cuenta"
)
MEDIDAS_DE_USO = (
    "entrada_tokens",
    "entrada_no_cacheada",
    "cache_lectura_tokens",
    "cache_escritura_tokens",
    "salida_tokens",
    "razonamiento_tokens",
    "n_llamadas",
    "latencia_ms",
)
ETAPAS = {
    "estado": evaluadores.chequear_estado,
    "cifras": evaluadores.chequear_cifras,
    "citas": evaluadores.chequear_cita,
    "conducta": evaluadores.chequear_conducta,
    "fugas": evaluadores.chequear_fugas,
    "herramienta": evaluadores.chequear_herramienta,
    "parametros": evaluadores.chequear_parametros,
}
TITULOS_DE_ETAPA = {
    "estado": "Estado esperado contra devuelto",
    "cifras": "Cifras (±0.01)",
    "citas": "Cita: documento y sección",
    "conducta": "Abstención, rechazo o aclaración",
    "fugas": "Sin fugas",
    "herramienta": "Acierto de herramienta",
    "parametros": "Acierto de parámetros",
}
# Un turno de Haiku 5.5 cuesta menos de medio centavo de dólar: a 2 decimales saldría 0.00 y parecería una medida.
DECIMALES_DE_COSTO = 8
# Los nombres de #metas en el plan: los GenAI de OpenTelemetry que lleva logs/turnos.jsonl (T4.3).
CAMPO_DE_ENTRADA = "gen_ai.usage.input_tokens"
CAMPO_DE_SALIDA = "gen_ai.usage.output_tokens"
CAMPOS_DE_EFICIENCIA = (CAMPO_DE_ENTRADA, CAMPO_DE_SALIDA, "costo_usd", "latencia_ms", "n_llamadas")
SIN_TURNOS_MEDIBLES = "sin turnos medibles"
PRUEBA_DE_CRECIMIENTO = "tope_historial"
MEDIDA_DE_REPETICION = "RepiteElTurno"


def numero(valor: float) -> str:
    """1,234,567.50: coma de miles y punto decimal, sin depender del locale."""
    return f"{valor:,.2f}"


def entero(valor: float) -> str:
    return f"{valor:,.0f}"


def percentil(valores: Sequence[float], p: float) -> float:
    """El percentil p con interpolación entre las dos muestras vecinas."""
    ordenados = sorted(valores)
    posicion = (len(ordenados) - 1) * p / 100
    abajo = int(posicion)
    arriba = min(abajo + 1, len(ordenados) - 1)
    return ordenados[abajo] + (ordenados[arriba] - ordenados[abajo]) * (posicion - abajo)


# ------------------------------------------------------------------ tasas y rangos


def _porcentaje(aciertos: int, n: int) -> float:
    return round(100 * aciertos / n, 1) if n else 0.0


def _fila_de_tasa(corrida: int, valores: Sequence[bool]) -> dict[str, Any]:
    aciertos = sum(valores)
    return {"corrida": corrida, "aciertos": aciertos, "n": len(valores), "porcentaje": _porcentaje(aciertos, len(valores))}


def _rango(por_corrida: Sequence[Mapping[str, Any]], esperados: int | None = None) -> dict[str, Any]:
    """Entre las corridas enteras: una corrida a medias tiene otra n y su porcentaje no es comparable.

    Sin esperados, entera es la que llegó a la n más grande de ese grupo.
    """
    tope = esperados if esperados is not None else max((fila["n"] for fila in por_corrida), default=0)
    completas = [fila for fila in por_corrida if fila["n"] == tope]
    if not completas:
        return {"minimo": None, "maximo": None, "corridas": 0}
    porcentajes = [fila["porcentaje"] for fila in completas]
    return {"minimo": min(porcentajes), "maximo": max(porcentajes), "corridas": len(completas)}


def _tasas(filas: Iterable[Mapping[str, Any]], *, esperados: int | None = None) -> dict[str, Any]:
    """Filas con corrida y acierto: el acierto de cada corrida, con su n, y el rango."""
    por_corrida: dict[int, list[bool]] = {}
    for fila in filas:
        por_corrida.setdefault(fila["corrida"], []).append(fila["acierto"])
    tasas = [_fila_de_tasa(corrida, valores) for corrida, valores in sorted(por_corrida.items())]
    return {"por_corrida": tasas, "rango": _rango(tasas, esperados)}


def _por_grupo(filas: Sequence[Mapping[str, Any]], clave: str) -> dict[str, Any]:
    grupos = sorted({fila[clave] for fila in filas})
    return {grupo: _tasas(fila for fila in filas if fila[clave] == grupo) for grupo in grupos}


def _casos_por_corrida(filas: Sequence[Mapping[str, Any]], corridas: Sequence[int]) -> list[dict[str, Any]]:
    return [
        {"corrida": corrida, "n": len(casos), "casos": casos}
        for corrida in corridas
        for casos in ([fila["id"] for fila in filas if fila["corrida"] == corrida],)
    ]


# ------------------------------------------------------------------ armar


def _filas_de_prompt(informes: Sequence[Any]) -> list[dict[str, Any]]:
    filas = []
    for corrida, informe in enumerate(informes, start=1):
        for caso in informe.cases:
            prompt = caso.inputs
            filas.append(
                {
                    "corrida": corrida,
                    "id": prompt.id,
                    "bloque": prompt.bloque,
                    "nucleo": prompt.nucleo,
                    "variante_de": prompt.variante_de,
                    "acierto": evaluadores.aprobo(caso),
                    "acierto_reglas": evaluadores.aprobo_por_reglas(caso),
                    "causas": evaluadores.causas(caso),
                    "turnos": [turno.turno_id for turno in caso.output.turnos],
                    "caso": caso,
                }
            )
    return filas


def _con_reglas_solas(filas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{**fila, "acierto": fila["acierto_reglas"]} for fila in filas]


def _filas_de_pregunta(
    filas_de_prompt: Sequence[Mapping[str, Any]], reprobadas_por_el_juez: frozenset[tuple[int, str, str]] = frozenset()
) -> list[dict[str, Any]]:
    filas = []
    for fila in filas_de_prompt:
        caso = fila["caso"]
        for _, pregunta, turno in evaluadores.pares(caso.inputs, caso.output):
            acierto, motivos = evaluadores.acierto_de_pregunta(pregunta, turno)
            # El veredicto del criterio propio de un compuesto lleva el id del prompt y alcanza a todas sus partes.
            claves = {(fila["corrida"], fila["id"], pregunta.id), (fila["corrida"], fila["id"], fila["id"])}
            acierto = acierto and not claves & reprobadas_por_el_juez
            filas.append(
                {
                    "corrida": fila["corrida"],
                    "id": pregunta.id,
                    "prompt": fila["id"],
                    "nucleo": fila["nucleo"],
                    "variante_de": fila["variante_de"],
                    "categoria": pregunta.categoria,
                    "tipo": pregunta.tipo_respuesta,
                    "acierto": acierto,
                    "motivos": motivos,
                    "pregunta": pregunta,
                    "turno": turno,
                }
            )
    return filas


def _acierto_de_alcance(filas: Sequence[Mapping[str, Any]], corridas_pedidas: int, prompts: int) -> dict[str, Any]:
    tasas = _tasas(filas, esperados=prompts)
    por_id: dict[str, list[bool]] = {}
    for fila in filas:
        por_id.setdefault(fila["id"], []).append(fila["acierto"])
    en_todas = sum(len(valores) == corridas_pedidas and all(valores) for valores in por_id.values())
    return {**tasas, "en_todas": {"aciertos": en_todas, "n": len(por_id)}}


def _renglones_por_pregunta(filas: Sequence[Mapping[str, Any]], corridas_pedidas: int) -> list[dict[str, Any]]:
    """Un renglón por prompt, en su orden: por corrida, si acertó en todas, por qué falló y los turnos para buscarlo."""
    renglones: dict[str, dict[str, Any]] = {}
    for fila in filas:
        renglon = renglones.setdefault(
            fila["id"],
            {"id": fila["id"], "bloque": fila["bloque"], "nucleo": fila["nucleo"], "por_corrida": [], "causas": [], "turnos": []},
        )
        renglon["por_corrida"].append(fila["acierto"])
        renglon["causas"].extend(f"corrida {fila['corrida']}: {causa}" for causa in fila["causas"])
        renglon["turnos"].extend(fila["turnos"])
    for renglon in renglones.values():
        renglon["en_todas"] = len(renglon["por_corrida"]) == corridas_pedidas and all(renglon["por_corrida"])
    return list(renglones.values())


def _chequeos_de_etapa(filas: Sequence[Mapping[str, Any]], chequear: Callable) -> dict[str, Any]:
    revisadas = (
        {"corrida": fila["corrida"], "acierto": chequeo.aprobado}
        for fila in filas
        for chequeo in (chequear(fila["pregunta"], fila["turno"]),)
        if chequeo is not None
    )
    return _tasas(revisadas)


def _promedio_por_corrida(muestras: Iterable[tuple[int, float]]) -> dict[str, Any]:
    por_corrida: dict[int, list[float]] = {}
    for corrida, valor in muestras:
        por_corrida.setdefault(corrida, []).append(valor)
    filas = [
        {"corrida": corrida, "promedio": round(sum(valores) / len(valores), 3), "n": len(valores)}
        for corrida, valores in sorted(por_corrida.items())
    ]
    promedios = [fila["promedio"] for fila in filas]
    rango = {"minimo": min(promedios), "maximo": max(promedios)} if promedios else {"minimo": None, "maximo": None}
    return {"por_corrida": filas, "rango": rango}


def _recuperacion(filas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    medidas = [
        (fila["corrida"], evaluadores.recall_de_seccion(fila["pregunta"], fila["turno"]))
        for fila in filas
        if fila["pregunta"].secciones_esperadas
    ]
    con_busqueda = [(corrida, medida) for corrida, medida in medidas if medida is not None]
    return {
        "recall_a_4": _promedio_por_corrida((corrida, float(medida.alguna)) for corrida, medida in con_busqueda),
        "fraccion_de_esperadas": _promedio_por_corrida((corrida, medida.recall) for corrida, medida in con_busqueda),
        "mrr": _promedio_por_corrida((corrida, medida.reciproco) for corrida, medida in con_busqueda),
        "sin_busqueda": len(medidas) - len(con_busqueda),
    }


def _matriz_sumada(informes: Sequence[Any], titulo: str) -> dict[str, Any]:
    cuenta: Counter[tuple[str, str]] = Counter()
    for informe in informes:
        for analisis in informe.analyses:
            if analisis.title != titulo:
                continue
            for i, esperada in enumerate(analisis.class_labels):
                for j, devuelta in enumerate(analisis.class_labels):
                    cuenta[(esperada, devuelta)] += analisis.matrix[i][j]
    etiquetas = sorted({etiqueta for pareja, veces in cuenta.items() if veces for etiqueta in pareja})
    return {"etiquetas": etiquetas, "matriz": [[cuenta[(e, d)] for d in etiquetas] for e in etiquetas]}


def _sin_evidencia(filas: Sequence[Mapping[str, Any]], corridas: Sequence[int]) -> dict[str, Any]:
    por_corrida = []
    for corrida in corridas:
        turnos = [fila["turno"] for fila in filas if fila["corrida"] == corrida]
        por_corrida.append(
            {
                "corrida": corrida,
                "cifras": sum(turno.cifras_sin_evidencia for turno in turnos),
                "citas": sum(turno.citas_sin_evidencia for turno in turnos),
                "datos_personales": sum(turno.datos_personales_en_borrador for turno in turnos),
                "turnos": len(turnos),
            }
        )
    return {"por_corrida": por_corrida}


def _cuenta_de_chequeos(turnos: Iterable[TurnoEvaluado], chequear: Callable) -> dict[str, int]:
    chequeos = [chequeo for chequeo in map(chequear, turnos) if chequeo is not None]
    return {"aciertos": sum(chequeo.aprobado for chequeo in chequeos), "n": len(chequeos)}


def _p50_p90(valores: Sequence[float], decimales: int = 2) -> dict[str, float]:
    return {"p50": round(percentil(valores, 50), decimales), "p90": round(percentil(valores, 90), decimales)}


def _uso_de(turnos: Sequence[TurnoEvaluado]) -> dict[str, Any]:
    uso: dict[str, Any] = {"n": len(turnos)}
    for medida in MEDIDAS_DE_USO:
        uso[medida] = _p50_p90([getattr(turno, medida) for turno in turnos])
    costos = [float(turno.costo_usd) for turno in turnos if turno.costo_usd is not None]
    uso["costo_usd"] = _p50_p90(costos, DECIMALES_DE_COSTO) if costos else None
    return uso


def _turnos_medibles(filas_de_prompt: Sequence[Mapping[str, Any]]) -> list[tuple[banco.Paso, TurnoEvaluado]]:
    """Un turno que tronó no dejó uso que medir: se cuenta en el acierto, no en los tokens."""
    return [
        (paso, turno)
        for fila in filas_de_prompt
        for paso, turno in zip(fila["caso"].inputs.pasos, fila["caso"].output.turnos, strict=False)
        if turno.estado != "excepcion"
    ]


def _turnos_por_categoria(medibles: Sequence[tuple[banco.Paso, TurnoEvaluado]]) -> dict[str, list[TurnoEvaluado]]:
    grupos: dict[str, list[TurnoEvaluado]] = {}
    for paso, turno in medibles:
        for categoria in dict.fromkeys(pregunta.categoria for pregunta in paso.preguntas):
            grupos.setdefault(categoria, []).append(turno)
    return grupos


def _uso_por_categoria(medibles: Sequence[tuple[banco.Paso, TurnoEvaluado]]) -> dict[str, Any]:
    return {categoria: _uso_de(turnos) for categoria, turnos in sorted(_turnos_por_categoria(medibles).items())}


def _con_mediana(medida: Mapping[str, float] | None) -> dict[str, float] | None:
    return None if medida is None else {"mediana": medida["p50"], "p90": medida["p90"]}


def _fila_de_eficiencia(turnos: Sequence[TurnoEvaluado], exactitud: Mapping[str, Any]) -> dict[str, Any]:
    """Un renglón de la tabla de T6.1. Sin turnos medibles las medidas van vacías: un cero parecería una medida."""
    fila: dict[str, Any] = {"turnos": len(turnos), **dict.fromkeys(CAMPOS_DE_EFICIENCIA), "exactitud": exactitud}
    if not turnos:
        return fila
    uso = _uso_de(turnos)
    fila.update(
        {
            CAMPO_DE_ENTRADA: _con_mediana(uso["entrada_tokens"]),
            CAMPO_DE_SALIDA: _con_mediana(uso["salida_tokens"]),
            "costo_usd": _con_mediana(uso["costo_usd"]),
            "latencia_ms": uso["latencia_ms"],
            "n_llamadas": _con_mediana(uso["n_llamadas"]),
        }
    )
    return fila


def _eficiencia_por_categoria(medibles: Sequence[tuple[banco.Paso, TurnoEvaluado]], por_categoria: Mapping[str, Any]) -> dict[str, Any]:
    """Por las categorías de la exactitud: una donde todo tronó no dejó turnos medibles y aun así lleva su renglón."""
    grupos = _turnos_por_categoria(medibles)
    return {categoria: _fila_de_eficiencia(grupos.get(categoria, []), tasas) for categoria, tasas in por_categoria.items()}


def _uso_total(medibles: Sequence[tuple[banco.Paso, TurnoEvaluado]]) -> dict[str, Any]:
    turnos = [turno for _, turno in medibles]
    sin_precio = sum(turno.costo_usd is None for turno in turnos)
    total = sum((turno.costo_usd for turno in turnos if turno.costo_usd is not None), Decimal(0))
    latencias = [turno.latencia_ms for turno in turnos]
    return {
        "turnos": len(turnos),
        "entrada_tokens": sum(turno.entrada_tokens for turno in turnos),
        "salida_tokens": sum(turno.salida_tokens for turno in turnos),
        "n_llamadas": sum(turno.n_llamadas for turno in turnos),
        "reintentos": sum(turno.reintentos for turno in turnos),
        "latencia_minutos": round(sum(latencias) / 60000, 2),
        # Un total con turnos sin precio pasaría por completo: se deja vacío y se dice cuántos faltan.
        "costo_usd": str(total) if not sin_precio else None,
        "costo_usd_de_los_que_tienen_precio": str(total),
        "turnos_sin_precio": sin_precio,
    }


def _conversacion_larga(filas_de_prompt: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    filas = []
    for fila in filas_de_prompt:
        caso = fila["caso"]
        if caso.inputs.prueba != PRUEBA_DE_CRECIMIENTO:
            continue
        entrada = [turno.entrada_tokens for turno in caso.output.turnos]
        repeticion = caso.assertions.get(MEDIDA_DE_REPETICION)
        filas.append(
            {
                "corrida": fila["corrida"],
                "prompt": fila["id"],
                "entrada_por_turno": entrada,
                "crecimiento_por_turno": (entrada[-1] - entrada[0]) / (len(entrada) - 1) if len(entrada) > 1 else None,
                "razon_turno_12_a_1": round(entrada[-1] / entrada[0], 2) if entrada and entrada[0] else None,
                "misma_cifra": repeticion.value if repeticion else None,
            }
        )
    return filas


def _cambios_del_juez(filas: Sequence[Mapping[str, Any]], corridas: Sequence[int]) -> dict[str, Any]:
    """Por prompt: lo que aprobaban las reglas y reprobó el juez, y lo contrario, que por construcción no pasa: ver eval/juez.py."""
    por_corrida = [
        {
            "corrida": corrida,
            "a_reprobado": [f["id"] for f in filas if f["corrida"] == corrida and f["acierto_reglas"] and not f["acierto"]],
            "a_aprobado": [f["id"] for f in filas if f["corrida"] == corrida and f["acierto"] and not f["acierto_reglas"]],
        }
        for corrida in corridas
    ]
    return {
        "por_corrida": por_corrida,
        "a_reprobado": sum(len(fila["a_reprobado"]) for fila in por_corrida),
        "a_aprobado": sum(len(fila["a_aprobado"]) for fila in por_corrida),
    }


def _uso_del_juez(veredictos: Sequence[juez.VeredictoDelJuez]) -> dict[str, Any]:
    """Aparte de los tokens y el costo de los turnos (C6.1). Un total con veredictos sin precio pasaría por completo."""
    sin_precio = sum(veredicto.costo_usd is None for veredicto in veredictos)
    total = sum((veredicto.costo_usd for veredicto in veredictos if veredicto.costo_usd is not None), Decimal(0))
    return {
        "llamadas": sum(veredicto.n_llamadas for veredicto in veredictos),
        "entrada_tokens": sum(veredicto.entrada_tokens for veredicto in veredictos),
        "salida_tokens": sum(veredicto.salida_tokens for veredicto in veredictos),
        "latencia_minutos": round(sum(veredicto.latencia_ms for veredicto in veredictos) / 60000, 2),
        "costo_usd": str(total) if not sin_precio else None,
        "costo_usd_de_los_que_tienen_precio": str(total),
        "veredictos_sin_precio": sin_precio,
    }


def _seccion_del_juez(
    filas: Sequence[Mapping[str, Any]],
    corridas: Sequence[int],
    estado: juez.EstadoDeValidacion | None,
    validacion: Mapping[str, Any] | None,
    veredictos: Sequence[juez.VeredictoDelJuez],
    compuestos: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    del_compuesto = [v for v in veredictos if v.prompt in compuestos and v.pregunta == v.prompt]
    sin_veredicto = [
        {"corrida": veredicto.corrida, "id": veredicto.pregunta}
        for veredicto in veredictos
        if veredicto.veredicto == juez.SIN_VEREDICTO
    ]
    return {
        "usado": bool(estado and estado.validado),
        "motivo": estado.motivo if estado else MOTIVO_SIN_CONSULTA,
        "nivel": NIVEL_DEL_JUEZ,
        "validacion": validacion,
        "cambios": _cambios_del_juez(filas, corridas),
        "juzgadas": len(veredictos),
        "aprueba": sum(veredicto.veredicto == juez.APRUEBA for veredicto in veredictos),
        "reprueba": sum(veredicto.veredicto == juez.REPRUEBA for veredicto in veredictos),
        "de_compuesto": {
            "juzgadas": len(del_compuesto),
            "reprueba": [{"corrida": v.corrida, "id": v.prompt} for v in del_compuesto if v.veredicto == juez.REPRUEBA],
        },
        "sin_veredicto": {
            "por_corrida": _casos_por_corrida(sin_veredicto, corridas),
            "total": len(sin_veredicto),
            "fallas_del_modelo": sum(veredicto.falla_del_modelo for veredicto in veredictos),
        },
        "uso": _uso_del_juez(veredictos),
        "veredictos": [veredicto.model_dump(mode="json") for veredicto in veredictos],
    }


def _texto_del_juez(seccion: Mapping[str, Any]) -> str:
    if seccion["usado"]:
        return (
            f"Con juez: {seccion['motivo']}. Una respuesta que pasa las reglas y reprueba el juez cuenta como falla."
        )
    return (
        f"Sin juez: {seccion['motivo']}. El acierto sale de reglas deterministas; el criterio en prosa de cada "
        "pregunta solo se califica en lo que esas reglas ven."
    )


def _unicos(valores: Iterable[Any]) -> list[Any]:
    return sorted({valor for valor in valores if valor})


def _ajustes_vistos(turnos: Iterable[TurnoEvaluado]) -> list[Any]:
    vistos: dict[str, Any] = {}
    for turno in turnos:
        vistos.setdefault(dumps(turno.ajustes_modelo, compacto=True), turno.ajustes_modelo)
    return list(vistos.values())


def _sin_objetos(fila: Mapping[str, Any]) -> dict[str, Any]:
    return {clave: valor for clave, valor in fila.items() if clave not in {"caso", "pregunta", "turno"}}


def armar(
    prompts: Sequence[banco.Prompt],
    informes: Sequence[Any],
    *,
    corridas_pedidas: int,
    completo: bool,
    variantes: bool,
    configuracion: str,
    ajustes: Mapping[str, Any],
    detenida: str | None = None,
    fallas: Sequence[Mapping[str, Any]] = (),
    hoy: dt.date | None = None,
    estado_del_juez: juez.EstadoDeValidacion | None = None,
    validacion_del_juez: Mapping[str, Any] | None = None,
    veredictos: Sequence[juez.VeredictoDelJuez] = (),
    cambios_sin_commit: str | None = None,
) -> dict[str, Any]:
    """El reporte entero, como datos. Las claves son las que leen el archivo .json y las pruebas."""
    todas = _filas_de_prompt(informes)
    filas = [fila for fila in todas if not fila["variante_de"]]
    de_variantes = [fila for fila in todas if fila["variante_de"]]
    # El avance puede traer veredictos de otro alcance o de más corridas: --reanudar los conserva y este reporte no los cuenta.
    presentes = {(fila["corrida"], fila["id"]) for fila in todas}
    veredictos = [veredicto for veredicto in veredictos if (veredicto.corrida, veredicto.prompt) in presentes]
    reprobadas = frozenset(
        (veredicto.corrida, veredicto.prompt, veredicto.pregunta) for veredicto in veredictos if veredicto.veredicto == juez.REPRUEBA
    )
    preguntas = _filas_de_pregunta(filas, reprobadas)
    seleccion = [prompt for prompt in prompts if not prompt.variante_de]
    del_nucleo = [prompt for prompt in seleccion if prompt.nucleo]
    corridas = list(range(1, len(informes) + 1))
    turnos_de = [turno for fila in todas for turno in fila["caso"].output.turnos]
    turnos_principales = [turno for fila in filas for turno in fila["caso"].output.turnos]
    medibles = _turnos_medibles(filas)
    enteras = sum(sum(fila["corrida"] == corrida for fila in todas) == len(prompts) for corrida in corridas)
    terminado = enteras == corridas_pedidas and not detenida and not fallas

    acierto = {
        "alcance": _acierto_de_alcance(filas, corridas_pedidas, len(seleccion)),
        "nucleo": _acierto_de_alcance([fila for fila in filas if fila["nucleo"]], corridas_pedidas, len(del_nucleo)),
    }
    if completo:
        acierto["completo"] = acierto["alcance"]
    con_reglas = _con_reglas_solas(filas)
    compuestos = frozenset(prompt.id for prompt in prompts if prompt.forma)
    seccion_del_juez = _seccion_del_juez(filas, corridas, estado_del_juez, validacion_del_juez, veredictos, compuestos)
    falsas = [fila for fila in preguntas if evaluadores.es_falsa_abstencion(fila["pregunta"], fila["turno"])]
    aclaraciones = [fila for fila in preguntas if evaluadores.es_aclaracion_donde_se_esperaba_cifra(fila["pregunta"], fila["turno"])]
    parciales = [fila for fila in preguntas if evaluadores.es_negativa_parcial(fila["pregunta"], fila["turno"])]
    con_fuga = [fila for fila in preguntas if not evaluadores.chequear_fugas(fila["pregunta"], fila["turno"]).aprobado]
    por_categoria = _por_grupo(preguntas, "categoria")
    return {
        "meta": {
            "fecha": (hoy or dt.date.today()).isoformat(),
            "commit": registro.commit(),
            "cambios_sin_commit": cambios_sin_commit,
            "modelos": _unicos(turno.modelo for turno in turnos_de),
            "prompt_sha256": _unicos(turno.prompt_sha256 for turno in turnos_de),
            "herramientas_sha256": _unicos(turno.herramientas_sha256 for turno in turnos_de),
            "hash_fuentes": _unicos(turno.hash_fuentes for turno in turnos_de),
            "configuracion": configuracion,
            "ajustes": dict(ajustes),
            "ajustes_modelo": _ajustes_vistos(turnos_de),
            "precios": _fuente_de_precios(),
            "alcance": "completo" if completo else "nucleo",
            "variantes": variantes,
            "n_prompts": len(seleccion),
            "corridas_pedidas": corridas_pedidas,
            "corridas_completas": enteras,
            "completo": terminado,
            "detenida": detenida,
            "juez": _texto_del_juez(seccion_del_juez),
            "tolerancia": str(evaluadores.TOLERANCIA),
        },
        "acierto": acierto,
        "acierto_reglas": {
            "alcance": _acierto_de_alcance(con_reglas, corridas_pedidas, len(seleccion)),
            "nucleo": _acierto_de_alcance([fila for fila in con_reglas if fila["nucleo"]], corridas_pedidas, len(del_nucleo)),
        },
        "juez": seccion_del_juez,
        "por_bloque": _por_grupo(filas, "bloque"),
        "por_bloque_reglas": _por_grupo(con_reglas, "bloque"),
        "por_categoria": por_categoria,
        "por_tipo": _por_grupo(preguntas, "tipo"),
        "etapas": {nombre: _chequeos_de_etapa(preguntas, chequear) for nombre, chequear in ETAPAS.items()},
        "recuperacion": _recuperacion(preguntas),
        "matriz_de_estados": _matriz_sumada(informes, evaluadores.TITULO_DE_ESTADOS),
        "matriz_de_conducta": _matriz_sumada(informes, evaluadores.TITULO_DE_CONDUCTA),
        "falsas_abstenciones": {"por_corrida": _casos_por_corrida(falsas, corridas), "total": len(falsas)},
        "aclaraciones_donde_se_esperaba_cifra": {
            "por_corrida": _casos_por_corrida(aclaraciones, corridas),
            "total": len(aclaraciones),
        },
        "negativas_parciales": {"por_corrida": _casos_por_corrida(parciales, corridas), "total": len(parciales)},
        "sin_evidencia": _sin_evidencia(
            [{"corrida": fila["corrida"], "turno": turno} for fila in filas for turno in fila["caso"].output.turnos], corridas
        ),
        "fugas": {"por_corrida": _casos_por_corrida(con_fuga, corridas), "total": len(con_fuga)},
        "formato": {
            "contexto_c44": _cuenta_de_chequeos(turnos_principales, evaluadores.contexto_en_tres_lineas),
            "corte_c33": _cuenta_de_chequeos(turnos_principales, evaluadores.corte_dicho),
            "fuente_k29": _cuenta_de_chequeos(turnos_principales, evaluadores.fuente_y_corte),
        },
        "uso_por_categoria": _uso_por_categoria(medibles),
        "uso_total": _uso_total(medibles),
        "eficiencia_por_categoria": _eficiencia_por_categoria(medibles, por_categoria),
        "eficiencia_total": _fila_de_eficiencia([turno for _, turno in medibles], _tasas(preguntas)),
        "conversacion_larga": _conversacion_larga(filas),
        "preguntas": _renglones_por_pregunta(filas, corridas_pedidas),
        "variantes": {
            **_tasas(de_variantes),
            "renglones": [_sin_objetos(fila) for fila in de_variantes],
        },
        "fallas_del_comando": list(fallas),
        "turnos": [
            {"corrida": fila["corrida"], "prompt": fila["id"], **turno.model_dump(mode="json")}
            for fila in todas
            for turno in fila["caso"].output.turnos
        ],
    }


def _fuente_de_precios() -> dict[str, str] | None:
    tabla = registro._precios_disponibles()
    return registro._fuente_de_precios(tabla)


# ------------------------------------------------------------------ escribir


def _tabla(encabezados: Sequence[str], renglones: Iterable[Sequence[Any]]) -> list[str]:
    lineas = [f"| {' | '.join(encabezados)} |", f"|{'---|' * len(encabezados)}"]
    lineas.extend(f"| {' | '.join(str(celda) for celda in renglon)} |" for renglon in renglones)
    return [*lineas, ""]


def _texto_de_tasa(fila: Mapping[str, Any]) -> str:
    return f"{entero(fila['aciertos'])} de {entero(fila['n'])} ({fila['porcentaje']:.1f} %)"


def _texto_de_rango(rango: Mapping[str, Any]) -> str:
    if rango["minimo"] is None:
        return "sin corridas enteras"
    if rango["corridas"] < 2:
        return f"{rango['minimo']:.1f} % (una corrida entera, sin rango)"
    return f"{rango['minimo']:.1f} % a {rango['maximo']:.1f} % ({rango['corridas']} corridas)"


def _texto_por_corrida(tasas: Mapping[str, Any]) -> str:
    return "; ".join(_texto_de_tasa(fila) for fila in tasas["por_corrida"]) or "sin datos"


def _renglones_de_tasas(grupos: Mapping[str, Mapping[str, Any]]) -> list[list[str]]:
    return [[nombre, _texto_por_corrida(tasas), _texto_de_rango(tasas["rango"])] for nombre, tasas in grupos.items()]


def _lineas_de_compuestos(de_compuesto: Mapping[str, Any]) -> list[str]:
    if not de_compuesto["juzgadas"]:
        return []
    reprobados = de_compuesto["reprueba"]
    casos = "; ".join(f"corrida {caso['corrida']}, {caso['id']}" for caso in reprobados)
    return [
        f"De esas, {entero(de_compuesto['juzgadas'])} juzgan el criterio propio de un compuesto y {entero(len(reprobados))} reprueban"
        f"{f': {casos}' if casos else ''}. Las etiquetas de validación son de partes y turnos: estas no tienen acuerdo medido.",
    ]


def _seccion_del_juez_en_markdown(seccion: Mapping[str, Any]) -> list[str]:
    lineas = ["## Juez", ""]
    if not seccion["usado"]:
        return [*lineas, f"No se usó: {seccion['motivo']}. El acierto de este reporte sale de reglas solas.", ""]
    cambios = seccion["cambios"]
    lineas.append(
        f"Casos que cambió el juez: {entero(cambios['a_reprobado'])} de aprobado a reprobado y "
        f"{entero(cambios['a_aprobado'])} de reprobado a aprobado. Solo se le pregunta por lo que las reglas aprueban."
    )
    lineas += [
        f"- Corrida {fila['corrida']}: {', '.join(fila['a_reprobado'])}" for fila in cambios["por_corrida"] if fila["a_reprobado"]
    ]
    uso = seccion["uso"]
    sin_veredicto = seccion["sin_veredicto"]
    lineas += [
        "",
        f"Se aplica por {seccion['nivel']}. Juzgadas: {entero(seccion['juzgadas'])}: {entero(seccion['aprueba'])} aprueban y "
        f"{entero(seccion['reprueba'])} reprueban.",
        *_lineas_de_compuestos(seccion["de_compuesto"]),
        f"Sin veredicto: {entero(sin_veredicto['total'])} ({entero(sin_veredicto['fallas_del_modelo'])} por una falla del modelo). "
        "No reprueban la respuesta.",
    ]
    lineas += [
        f"- Corrida {fila['corrida']}: {', '.join(fila['casos'])}" for fila in sin_veredicto["por_corrida"] if fila["casos"]
    ]
    lineas += [
        "",
        f"Uso del juez, aparte del de los turnos: {entero(uso['llamadas'])} llamadas, {entero(uso['entrada_tokens'])} tokens de "
        f"entrada, {entero(uso['salida_tokens'])} de salida, {uso['latencia_minutos']:,.2f} minutos y {_costo(uso['costo_usd'])} USD "
        f"({entero(uso['veredictos_sin_precio'])} veredictos sin precio).",
        "",
    ]
    validacion = seccion["validacion"]
    if validacion:
        lineas += [f"Acuerdo validado contra etiquetas propias ({validacion['archivo']}, {validacion['fecha']}):", ""]
        lineas += [
            f"- Corrida {corrida['corrida']}: {entero(corrida['acuerdos'])} de {entero(corrida['n'])}, intervalo del 95 % de "
            f"{100 * corrida['intervalo_95'][0]:,.1f} % a {100 * corrida['intervalo_95'][1]:,.1f} %"
            for corrida in validacion["corridas"]
        ]
        lineas += [""]
    return lineas


def _texto_de_promedio(medida: Mapping[str, Any]) -> str:
    filas = medida["por_corrida"]
    if not filas:
        return "sin búsquedas"
    return "; ".join(f"{fila['promedio']:.3f} (n = {entero(fila['n'])})" for fila in filas)


def _matriz_en_markdown(matriz: Mapping[str, Any], esquina: str) -> list[str]:
    """La matriz sin los renglones ni las columnas en cero: la de conducta es cuadrada sobre etiquetas de dos clases."""
    etiquetas, filas = matriz["etiquetas"], matriz["matriz"]
    columnas = [j for j in range(len(etiquetas)) if any(fila[j] for fila in filas)]
    renglones = [[etiquetas[i], *(fila[j] for j in columnas)] for i, fila in enumerate(filas) if any(fila)]
    return _tabla([esquina, *(etiquetas[j] for j in columnas)], renglones)


def _costo(valor: Any) -> str:
    return "sin precio" if valor is None else f"{float(valor):,.6f}"


def _renglon_de_uso(categoria: str, uso: Mapping[str, Any]) -> list[str]:
    costo = uso["costo_usd"]
    return [
        categoria,
        entero(uso["n"]),
        f"{entero(uso['entrada_tokens']['p50'])} / {entero(uso['entrada_tokens']['p90'])}",
        f"{entero(uso['entrada_no_cacheada']['p50'])} / {entero(uso['entrada_no_cacheada']['p90'])}",
        f"{entero(uso['salida_tokens']['p50'])} / {entero(uso['salida_tokens']['p90'])}",
        f"{numero(uso['n_llamadas']['p50'])} / {numero(uso['n_llamadas']['p90'])}",
        "sin precio" if costo is None else f"{_costo(costo['p50'])} / {_costo(costo['p90'])}",
        f"{entero(uso['latencia_ms']['p50'])} / {entero(uso['latencia_ms']['p90'])}",
    ]


def _par(medida: Mapping[str, float], formato: Callable[[float], str], medio: str = "mediana") -> str:
    return f"{formato(medida[medio])} / {formato(medida['p90'])}"


def _medidas_de_eficiencia(fila: Mapping[str, Any]) -> list[str]:
    if not fila["turnos"]:
        return [SIN_TURNOS_MEDIBLES] * len(CAMPOS_DE_EFICIENCIA)
    costo = fila["costo_usd"]
    return [
        _par(fila[CAMPO_DE_ENTRADA], entero),
        _par(fila[CAMPO_DE_SALIDA], entero),
        "sin precio" if costo is None else _par(costo, _costo),
        _par(fila["latencia_ms"], entero, medio="p50"),
        _par(fila["n_llamadas"], numero),
    ]


def _renglon_de_eficiencia(nombre: str, fila: Mapping[str, Any]) -> list[str]:
    exactitud = fila["exactitud"]
    columnas_de_exactitud = [_texto_por_corrida(exactitud), _texto_de_rango(exactitud["rango"])]
    return [nombre, entero(fila["turnos"]), *_medidas_de_eficiencia(fila), *columnas_de_exactitud]


def _seccion_de_eficiencia(datos: Mapping[str, Any]) -> list[str]:
    """La tabla de T6.1: todo en un renglón por categoría, y uno de todas para la mediana del turno que usa #metas."""
    lineas = [
        "## Medición por categoría, en una sola tabla",
        "",
        "Tokens, costo, latencia y llamadas son por turno, de todas las corridas juntas y sin el juez ni los turnos que tronaron; "
        "la entrada incluye la caché y la salida el razonamiento. La exactitud es por pregunta. Un turno de dos categorías cuenta "
        "en las dos y una sola vez en Todas.",
        "",
    ]
    renglones = [_renglon_de_eficiencia(categoria, fila) for categoria, fila in datos["eficiencia_por_categoria"].items()]
    renglones.append(_renglon_de_eficiencia("Todas", datos["eficiencia_total"]))
    return [
        *lineas,
        *_tabla(
            [
                "Categoría",
                "Turnos",
                "Entrada (mediana / p90)",
                "Salida (mediana / p90)",
                "Costo USD (mediana / p90)",
                "Latencia ms (p50 / p90)",
                "Llamadas por turno (mediana / p90)",
                "Exactitud por corrida",
                "Exactitud, rango",
            ],
            renglones,
        ),
    ]


def a_markdown(datos: Mapping[str, Any]) -> str:
    """El reporte para leer: empieza por el acierto y termina con cada falla con su causa."""
    meta = datos["meta"]
    commit = meta["commit"] or {}
    sucio = " (con cambios sin commit)" if commit.get("sucio") else ""
    acierto = datos["acierto"]
    lineas = [f"# Evaluación del set: {meta['alcance']}, {meta['fecha']}", ""]
    if not meta["completo"]:
        lineas += [f"**Corrida incompleta.** {meta['detenida'] or 'Faltan prompts por correr.'} Se sigue con --reanudar.", ""]
    lineas += [
        f"- Commit evaluado: {commit.get('sha') or 'sin commit'}{sucio}",
        f"- Cambios sin commit en lo que se evalúa: {meta['cambios_sin_commit'] or 'ninguno'}",
        f"- Modelo: {', '.join(meta['modelos']) or 'sin turnos'}",
        f"- Configuración: {meta['configuracion']}, {dumps(meta['ajustes'])}; viajó: {dumps(meta['ajustes_modelo'])}",
        f"- Prompt: {', '.join(meta['prompt_sha256'])}",
        f"- Herramientas: {', '.join(meta['herramientas_sha256'])}",
        f"- Fuentes: {', '.join(meta['hash_fuentes'])}",
        f"- Prompts: {entero(meta['n_prompts'])}. Corridas enteras: {meta['corridas_completas']} de {meta['corridas_pedidas']}.",
        f"- Tolerancia de cifras: ±{meta['tolerancia']}",
        f"- {meta['juez']}",
        "",
        "## Acierto",
        "",
    ]
    reglas = datos["acierto_reglas"]
    filas = [
        [
            nombre,
            _texto_por_corrida(acierto[clave]),
            _texto_de_rango(acierto[clave]["rango"]),
            f"{entero(acierto[clave]['en_todas']['aciertos'])} de {entero(acierto[clave]['en_todas']['n'])}",
            _texto_por_corrida(reglas[clave]),
            _texto_de_rango(reglas[clave]["rango"]),
        ]
        for nombre, clave in (("Alcance de esta corrida", "alcance"), ("Núcleo", "nucleo"))
    ]
    lineas += _tabla(["Alcance", "Por corrida", "Rango", "Acierto en todas", "Reglas solas, por corrida", "Reglas solas, rango"], filas)
    lineas += _seccion_del_juez_en_markdown(datos["juez"])
    lineas += ["## Por bloque", ""]
    lineas += _tabla(
        ["Bloque", "Por corrida", "Rango", "Reglas solas, por corrida", "Reglas solas, rango"],
        (
            [bloque, _texto_por_corrida(tasas), _texto_de_rango(tasas["rango"]), *_columnas_de_reglas(datos["por_bloque_reglas"][bloque])]
            for bloque, tasas in datos["por_bloque"].items()
        ),
    )
    for titulo, clave in (("Por categoría", "por_categoria"), ("Por tipo de respuesta", "por_tipo")):
        lineas += [f"## {titulo}", ""]
        lineas += _tabla([titulo.removeprefix("Por ").capitalize(), "Por corrida", "Rango"], _renglones_de_tasas(datos[clave]))
    lineas += ["## Por etapa", ""]
    etapas = {TITULOS_DE_ETAPA[nombre]: tasas for nombre, tasas in datos["etapas"].items()}
    lineas += _tabla(["Etapa", "Por corrida", "Rango"], _renglones_de_tasas(etapas))
    recuperacion = datos["recuperacion"]
    lineas += [
        "Recuperación de la sección, contando solo los 4 fragmentos de BM25:",
        "",
        f"- recall@4, al menos una sección esperada en los 4: {_texto_de_promedio(recuperacion['recall_a_4'])}",
        f"- Fracción de las esperadas en los 4: {_texto_de_promedio(recuperacion['fraccion_de_esperadas'])}",
        f"- MRR: {_texto_de_promedio(recuperacion['mrr'])}",
        f"- Preguntas con sección esperada que no buscaron: {entero(recuperacion['sin_busqueda'])}",
        "",
        "## Matriz de estados",
        "",
    ]
    lineas += _matriz_en_markdown(datos["matriz_de_estados"], "Esperado / devuelto")
    lineas += ["## Tipo de respuesta contra conducta", ""]
    lineas += _matriz_en_markdown(datos["matriz_de_conducta"], "Esperado / devuelto")
    falsas = datos["falsas_abstenciones"]
    lineas += _cuenta_por_corrida("Falsas abstenciones", falsas)
    lineas += [
        "",
        *_cuenta_por_corrida("Aclaraciones donde se esperaba cifra", datos["aclaraciones_donde_se_esperaba_cifra"]),
        "",
        *_cuenta_por_corrida("Negativas que traen parte de lo esperado", datos["negativas_parciales"]),
    ]
    lineas += ["", "## Sin evidencia y fugas", "", "Primera versión del modelo, antes del verificador:", ""]
    lineas += _tabla(
        ["Corrida", "Cifras", "Citas", "Datos personales", "Turnos"],
        ([f["corrida"], f["cifras"], f["citas"], f["datos_personales"], f["turnos"]] for f in datos["sin_evidencia"]["por_corrida"]),
    )
    lineas += [f"Fugas en lo que se mostró: {entero(datos['fugas']['total'])}.", ""]
    lineas += [
        f"- Corrida {fila['corrida']}: {', '.join(fila['casos'])}" for fila in datos["fugas"]["por_corrida"] if fila["casos"]
    ]
    formato = datos["formato"]
    lineas += [
        "",
        "## Formato, sobre el texto con su pie",
        "",
        f"- Contexto en 3 líneas o menos: {_cuenta(formato['contexto_c44'])}",
        f"- Respuestas con resultado temporal que dicen el corte: {_cuenta(formato['corte_c33'])}",
        f"- Respuestas con fuente y corte: {_cuenta(formato['fuente_k29'])}",
        "",
        *_seccion_de_eficiencia(datos),
        "## Tokens, costo y latencia por categoría (p50 / p90)",
        "",
    ]
    lineas += _tabla(
        ["Categoría", "Turnos", "Entrada", "Entrada sin caché", "Salida", "Llamadas", "Costo USD", "Latencia ms"],
        (_renglon_de_uso(categoria, uso) for categoria, uso in datos["uso_por_categoria"].items()),
    )
    uso_total = datos["uso_total"]
    lineas += [
        f"Total: {entero(uso_total['turnos'])} turnos, {entero(uso_total['n_llamadas'])} llamadas, "
        f"{entero(uso_total['entrada_tokens'])} tokens de entrada, {entero(uso_total['salida_tokens'])} de salida, "
        f"{uso_total['latencia_minutos']:,.2f} minutos y {_costo(uso_total['costo_usd'])} USD "
        f"({entero(uso_total['turnos_sin_precio'])} turnos sin precio).",
        "",
    ]
    lineas += _seccion_de_conversacion(datos["conversacion_larga"])
    if datos["variantes"]["por_corrida"]:
        lineas += ["## Variantes, fuera del rango (D25)", ""]
        lineas += [f"- Corrida {fila['corrida']}: {_texto_de_tasa(fila)}" for fila in datos["variantes"]["por_corrida"]]
        lineas += [""]
    if datos["fallas_del_comando"]:
        lineas += ["## Fallas del comando", ""]
        lineas += [f"- Corrida {f['corrida']}, {f['id']}: {f['tipo']}" for f in datos["fallas_del_comando"]]
        lineas += [""]
    lineas += _seccion_de_preguntas(datos["preguntas"])
    return "\n".join(lineas)


def _columnas_de_reglas(tasas: Mapping[str, Any]) -> list[str]:
    return [_texto_por_corrida(tasas), _texto_de_rango(tasas["rango"])]


def _cuenta_por_corrida(titulo: str, seccion: Mapping[str, Any]) -> list[str]:
    lineas = [f"{titulo}: {entero(seccion['total'])} en total.", ""]
    lineas += [
        f"- Corrida {fila['corrida']}: {entero(fila['n'])} ({', '.join(fila['casos']) or 'ninguna'})"
        for fila in seccion["por_corrida"]
    ]
    return lineas


def _cuenta(cuenta: Mapping[str, int]) -> str:
    return f"{entero(cuenta['aciertos'])} de {entero(cuenta['n'])}"


def _seccion_de_conversacion(filas: Sequence[Mapping[str, Any]]) -> list[str]:
    if not filas:
        return []
    lineas = ["## Crecimiento por turno en la conversación larga", ""]
    for fila in filas:
        crecimiento = fila["crecimiento_por_turno"]
        lineas.append(
            f"- Corrida {fila['corrida']}, {fila['prompt']}: entrada por turno "
            f"{', '.join(entero(valor) for valor in fila['entrada_por_turno'])}; "
            f"{'sin dato' if crecimiento is None else entero(crecimiento)} tokens por turno previo; "
            f"el último cuesta {fila['razon_turno_12_a_1']} veces el primero; misma cifra: {_si_no(fila['misma_cifra'])}."
        )
    return [*lineas, ""]


def _si_no(valor: bool | None) -> str:
    return {True: "sí", False: "no", None: "sin dato"}[valor]


def _seccion_de_preguntas(renglones: Sequence[Mapping[str, Any]]) -> list[str]:
    lineas = ["## Por pregunta", ""]
    lineas += _tabla(
        ["Prompt", "Bloque", "Por corrida", "Acierto en todas"],
        (
            [r["id"], r["bloque"], ", ".join(_si_no(valor) for valor in r["por_corrida"]), _si_no(r["en_todas"])]
            for r in renglones
        ),
    )
    lineas += ["## Fallas y su causa", ""]
    con_causa = [renglon for renglon in renglones if renglon["causas"]]
    if not con_causa:
        return [*lineas, "Ningún prompt reprobó con estas reglas.", ""]
    for renglon in con_causa:
        lineas.append(f"- {renglon['id']} ({renglon['bloque']})")
        lineas.extend(f"  - {causa}" for causa in renglon["causas"])
    return [*lineas, ""]


def _nombre_de_archivo(datos: Mapping[str, Any]) -> str:
    meta = datos["meta"]
    sha = (meta["commit"] or {}).get("sha") or "sin-commit"
    partes = [meta["fecha"], meta["alcance"]]
    if meta["variantes"]:
        partes.append("variantes")
    if meta["configuracion"] != "interfaz":
        partes.append(meta["configuracion"])
    partes.append(sha[:7])
    return "-".join(partes)


def guardar(datos: Mapping[str, Any], carpeta: Path) -> tuple[Path, Path]:
    """Escribe el reporte en .md y .json con LF y UTF-8. Sobrescribe el mismo día y commit: --reanudar lo completa."""
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = _nombre_de_archivo(datos)
    markdown, como_json = carpeta / f"{nombre}.md", carpeta / f"{nombre}.json"
    markdown.write_text(a_markdown(datos), encoding="utf-8", newline="\n")
    como_json.write_text(dumps(datos) + "\n", encoding="utf-8", newline="\n")
    return markdown, como_json


def _resumen_del_juez(datos: Mapping[str, Any]) -> str:
    seccion = datos["juez"]
    if not seccion["usado"]:
        return f"Sin juez: {seccion['motivo']}."
    reglas = "; ".join(_texto_de_tasa(fila) for fila in datos["acierto_reglas"]["alcance"]["por_corrida"])
    return (
        f"Con reglas solas: {reglas}. El juez reprobó {entero(seccion['cambios']['a_reprobado'])} casos que las reglas aprobaban; "
        f"{entero(seccion['sin_veredicto']['total'])} sin veredicto; costo del juez {_costo(seccion['uso']['costo_usd'])} USD."
    )


def resumen_de_consola(datos: Mapping[str, Any], rutas: Sequence[Path]) -> str:
    alcance = datos["acierto"]["alcance"]
    lineas = [f"Acierto, corrida {f['corrida']}: {_texto_de_tasa(f)}." for f in alcance["por_corrida"]]
    lineas.append(f"Rango: {_texto_de_rango(alcance['rango'])}.")
    lineas.append(f"Acierto en todas las corridas: {_cuenta(alcance['en_todas'])}.")
    lineas.append(_resumen_del_juez(datos))
    lineas.append(f"Falsas abstenciones: {entero(datos['falsas_abstenciones']['total'])}. Fugas: {entero(datos['fugas']['total'])}.")
    lineas.append("Reporte: " + ", ".join(str(ruta) for ruta in rutas))
    return "\n".join(lineas)
