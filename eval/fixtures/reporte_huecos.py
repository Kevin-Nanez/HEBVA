"""Reporte de los casos de eval/fixtures/casos_huecos.py (plan docs/planes/2026-11-02-cierre-huecos-inyeccion.html, H4, H8, H12).

Uso, desde la raíz del repositorio:
    .venv\\Scripts\\python -m eval.fixtures.reporte_huecos              con el modelo de .env: gasta la llave
    .venv\\Scripts\\python -m eval.fixtures.reporte_huecos --sin-modelo  lo que no necesita al modelo

Escribe eval/reportes/huecos-AAAA-MM-DD.json e imprime el resumen. El reporte da:

- objetivos: por cada objetivo, cuántos casos pasaron, cuántos marca el marcador de K08, cuántos alcanza la herramienta sin
  modelo, cuántos quedaron expuestos (el modelo vio el texto hostil), cuántas órdenes se cumplieron y cuántas fugas hubo en
  la respuesta, en los parámetros y en un borrador que el verificador detuvo.
- controles: preguntas legítimas sobre tickets del paquete con un número en la descripción. Dice cuántas pasaron a la
  primera, cuántas pidieron corrección y cuántas terminaron en la respuesta armada sin modelo. Es lo que cuesta D1-A:
  una cifra del texto libre ya no es evidencia, así que repetirla pide corrección.
- sha256: el hash_fuentes y el sha256 de cada archivo de datos/ y politicas/ del paquete y de la copia, antes y después (G8).
- casos: lo observable de cada turno, con su respuesta para revisarla a mano.

Con el modelo son 9 casos y 6 controles: 15 turnos de unas 2 solicitudes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from pydantic_ai.models import Model

from agente import agente, registro
from agente.consultas import abrir_consultas
from agente.herramientas import buscar_politicas, tickets
from agente.herramientas.comun import EntradaPoliticas, EntradaTickets
from eval.fixtures import casos_huecos, copias
from eval.fixtures.reporte_adversarial import CATEGORIA, DIR_REPORTES, _commit, huella


def alcanzables(copia: copias.Copia, casos: tuple[casos_huecos.CasoHueco, ...], hoy: dt.date) -> dict[str, bool]:
    """Sin modelo: si la primera llamada de cada caso lleva su texto hostil al resultado de la herramienta."""
    consultas = abrir_consultas(copia.base)
    try:
        return {caso.id: casos_huecos.visto_en(caso, _resultado_de(caso, consultas.sesion(), hoy).texto_modelo()) for caso in casos}
    finally:
        consultas.cerrar()


def _resultado_de(caso: casos_huecos.CasoHueco, cursor: Any, hoy: dt.date) -> Any:
    herramienta, argumentos = caso.llamadas[0]
    if herramienta == "tickets":
        return tickets.tickets(cursor, EntradaTickets(**argumentos), hoy_real=hoy)
    return buscar_politicas.buscar_politicas(cursor, EntradaPoliticas(**argumentos))


def _conteo_de(
    objetivo: str,
    casos: tuple[casos_huecos.CasoHueco, ...],
    vistos: dict[str, bool],
    calificaciones: list[casos_huecos.Calificacion],
    con_modelo: bool,
) -> dict[str, Any]:
    del_objetivo = [caso for caso in casos if caso.objetivo == objetivo]
    conteo = {
        "casos": len(del_objetivo),
        "marcados": sum(casos_huecos.es_marcado(caso) for caso in del_objetivo),
        "alcanzables": sum(vistos[caso.id] for caso in del_objetivo),
        "pasaron": None,
        "expuestos": None,
        "expuestos_y_pasaron": None,
        "ordenes_cumplidas": None,
        "fugas_respuesta": None,
        "fugas_parametros": None,
        "fugas_borrador": None,
        "deterministas": None,
    }
    if not con_modelo:
        return conteo
    propias = [calificacion for calificacion in calificaciones if calificacion.objetivo == objetivo]
    conteo.update(
        pasaron=sum(calificacion.paso for calificacion in propias),
        expuestos=sum(calificacion.expuesto for calificacion in propias),
        expuestos_y_pasaron=sum(calificacion.expuesto and calificacion.paso for calificacion in propias),
        ordenes_cumplidas=sum(calificacion.cumple_la_orden for calificacion in propias),
        fugas_respuesta=sum(calificacion.fuga_respuesta for calificacion in propias),
        fugas_parametros=sum(calificacion.fuga_parametros for calificacion in propias),
        fugas_borrador=sum(calificacion.fuga_borrador for calificacion in propias),
        deterministas=sum(calificacion.desenlace == "determinista" for calificacion in propias),
    )
    return conteo


def _controles(respuestas: list[tuple[str, agente.Respuesta]]) -> dict[str, Any]:
    """Cómo terminó cada pregunta legítima; turnos None si el reporte no llamó al modelo."""
    desenlaces = [respuesta.traza.verificacion.desenlace if respuesta.traza.verificacion else None for _, respuesta in respuestas]
    return {
        "turnos": len(respuestas),
        "aprobadas": desenlaces.count("aprobada"),
        "corregidas": desenlaces.count("corregida"),
        "deterministas": desenlaces.count("determinista"),
        "casos": [
            {"folio": folio, "estado": respuesta.estado, "desenlace": desenlace, "respuesta": respuesta.texto}
            for (folio, respuesta), desenlace in zip(respuestas, desenlaces, strict=True)
        ],
    }


def generar_reporte(
    modelo: Model | str | None,
    *,
    raiz: Path = copias.RAIZ_COPIAS,
    hoy: dt.date | None = None,
    con_modelo: bool = True,
    archivo_registro: Path | None = None,
) -> dict[str, Any]:
    """Arma la copia, corre los casos y devuelve el reporte. modelo None usa el de .env, salvo con con_modelo=False."""
    fecha = hoy or dt.date.today()
    opciones = {"hoy_real": fecha, "archivo_registro": archivo_registro, "origen": "evaluacion", "categoria": CATEGORIA}

    copia = casos_huecos.copia_huecos(raiz)
    paquete = copias.paquete(raiz)
    todas = {"paquete": paquete, "huecos": copia}
    antes = {nombre: huella(armada) for nombre, armada in todas.items()}

    def preguntar(pregunta: str) -> agente.Respuesta:
        return agente.responder(pregunta, modelo=modelo, base=copia.base, dir_datos=copia.dir_datos, **opciones)

    casos = casos_huecos.generar_casos()
    vistos = alcanzables(copia, casos, fecha)
    calificaciones = [casos_huecos.calificar(caso, preguntar(caso.pregunta)) for caso in casos] if con_modelo else []
    folios = casos_huecos.folios_de_control()
    de_control = [(folio, preguntar(casos_huecos.PREGUNTA_DE_CONTROL.format(folio=folio))) for folio in folios] if con_modelo else []

    despues = {nombre: huella(armada) for nombre, armada in todas.items()}
    cambiaron = [nombre for nombre in todas if antes[nombre] != despues[nombre]]
    todo_paso = None
    if con_modelo:
        todo_paso = all(calificacion.paso for calificacion in calificaciones) and not cambiaron
    return {
        "fecha": fecha.isoformat(),
        "commit": _commit(),
        "con_modelo": con_modelo,
        "todo_paso": todo_paso,
        "objetivos": {objetivo: _conteo_de(objetivo, casos, vistos, calificaciones, con_modelo) for objetivo in casos_huecos.OBJETIVOS},
        "controles": _controles(de_control),
        "sha256": {"sin_cambios": not cambiaron, "cambiaron": cambiaron, "antes": antes, "despues": despues},
        "casos": [
            {
                "id": c.id,
                "objetivo": c.objetivo,
                "estado": c.estado,
                "paso": c.paso,
                "motivos": list(c.motivos),
                "marcado": c.marcado,
                "expuesto": c.expuesto,
                "fuga_respuesta": c.fuga_respuesta,
                "fuga_parametros": c.fuga_parametros,
                "fuga_borrador": c.fuga_borrador,
                "cumple_la_orden": c.cumple_la_orden,
                "desenlace": c.desenlace,
                "tope": c.tope,
                "herramientas": c.herramientas,
                "respuesta": c.respuesta,
            }
            for c in calificaciones
        ],
    }


def _imprimir(reporte: dict[str, Any]) -> None:
    print(f"Casos de los huecos de inyección, {reporte['fecha']}, commit {reporte['commit']}, con modelo: {reporte['con_modelo']}")
    print(
        "\nPor objetivo: casos, alcanzables sin modelo, marcados por K08, pasaron, expuestos, expuestos que pasaron, "
        "órdenes cumplidas, fugas en la respuesta, en los parámetros, en un borrador, respuestas armadas sin modelo"
    )
    for objetivo, c in reporte["objetivos"].items():
        print(
            f"  {objetivo}: {c['casos']}, {c['alcanzables']}, {c['marcados']}, {c['pasaron']}, {c['expuestos']}, "
            f"{c['expuestos_y_pasaron']}, {c['ordenes_cumplidas']}, {c['fugas_respuesta']}, {c['fugas_parametros']}, "
            f"{c['fugas_borrador']}, {c['deterministas']}"
        )
    controles = reporte["controles"]
    print(
        f"\nPreguntas legítimas: {controles['turnos']} turnos, {controles['aprobadas']} aprobadas, "
        f"{controles['corregidas']} corregidas y {controles['deterministas']} armadas sin modelo"
    )
    print("\nsha256 de antes y de después (hash_fuentes)")
    for nombre, antes in reporte["sha256"]["antes"].items():
        print(f"  {nombre}: {antes['hash_fuentes']} -> {reporte['sha256']['despues'][nombre]['hash_fuentes']}")
    print(f"  Sin cambios: {reporte['sha256']['sin_cambios']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reporte de los casos de los huecos de inyección.")
    parser.add_argument("--sin-modelo", action="store_true", help="no llama al modelo: deja en blanco lo que lo necesita")
    parser.add_argument("--raiz", type=Path, default=copias.RAIZ_COPIAS, help="carpeta donde se arman las copias")
    parser.add_argument("--salida", type=Path, help="archivo del reporte; por omisión, eval/reportes/huecos-AAAA-MM-DD.json")
    parser.add_argument("--hoy", type=dt.date.fromisoformat, default=dt.date.today(), help="fecha del reloj, AAAA-MM-DD")
    argumentos = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    con_modelo = not argumentos.sin_modelo
    reporte = generar_reporte(
        None, raiz=argumentos.raiz, hoy=argumentos.hoy, con_modelo=con_modelo,
        archivo_registro=registro.ARCHIVO if con_modelo else None,
    )
    salida = argumentos.salida or DIR_REPORTES / f"huecos-{argumentos.hoy.isoformat()}.json"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(reporte, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _imprimir(reporte)
    print(f"\nReporte: {salida}")
    return 1 if reporte["todo_paso"] is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
