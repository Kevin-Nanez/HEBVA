"""Comando de evaluación: python -m eval.correr (T5.3, T5.9, D12, D25, D26, C5.2, C1.3, C6.1).

Uso, desde la raíz del repositorio:

    .venv\\Scripts\\python -m eval.correr                       los 30 prompts del núcleo, 2 corridas: gasta la llave
    .venv\\Scripts\\python -m eval.correr --completo            los 100
    .venv\\Scripts\\python -m eval.correr --variantes           suma las 2 paráfrasis de cada sencilla, aparte del rango
    .venv\\Scripts\\python -m eval.correr --corridas 3          2 por omisión
    .venv\\Scripts\\python -m eval.correr --reanudar            sigue donde se quedó, sin repetir ninguna llamada
    .venv\\Scripts\\python -m eval.correr --limite-minutos 8    se detiene limpio después del prompt en curso
    .venv\\Scripts\\python -m eval.correr --configuracion base  con AJUSTES_BASE, para T6.1
    .venv\\Scripts\\python -m eval.correr --recall-sin-modelo   recall@4 con el texto de cada pregunta: no gasta la llave
    .venv\\Scripts\\python -m eval.correr --rejuzgar <reporte.json> --modelo <id>   solo el juez, sobre lo guardado: ver abajo

Escribe eval/reportes/AAAA-MM-DD-<alcance>-<commit>.md y .json. Sale con 0 si terminó, aunque haya preguntas reprobadas: la
calificación es del reporte, no del comando. Con SALIDA_LIMITE, SALIDA_SIN_MODELO o SALIDA_FALLAS_DEL_COMANDO faltan prompts
por correr y --reanudar los retoma.

El mismo camino que la interfaz (C5.2, K32). Cada turno es el par de app._atender_una(): manifiesto.pide_la_cobertura()
decide si contesta manifiesto.responder() o agente.responder(), y después de responder() va historial.agregar(). responder()
recibe modelo=None para leer .env, igual que la interfaz, y archivo_registro=registro.ARCHIVO con origen="evaluacion" y la
categoría, así que cada turno deja su línea en logs/turnos.jsonl y registro.reproducir(linea) lo repite (E16). Lo que lee
la gerente sale de app.sin_linea_de_fuente(), app.sin_negritas() y app.lineas_de_fuente().

Un Historial por sesión. Cada prompt abre los suyos: uno en una sencilla o en un compuesto, cuyas preguntas lo comparten, y
uno por sesión en una conversación, abiertos a la vez si son 2 (P52). Un prompt corre sobre su copia si la trae (T5.6).

El juez (D11-B). juez.CriterioDelJuez va en cada Dataset y usa el modelo de la corrida. Si no está validado para ese modelo no
se llama, el acierto sale de reglas y el reporte da el motivo (eval/juez.py, estado_de_validacion()).

pydantic-evals (D12) corre cada corrida como un Dataset con max_concurrency=1 y evaluadores en clases: en 2.61.0 no hay
task_timeout y el tope por pregunta es el de responder() (T3.2). Un Case por prompt y un Dataset por corrida, y no repeat=2,
porque la tarea tiene que saber en qué corrida va para reanudar, y porque así la corrida 1 termina antes de empezar la 2: con
un corte a medias queda una corrida entera y no medio prompt de cada una.

Guardar y reanudar. Al terminar un prompt, su línea va a eval/reportes/avance/avance.jsonl con los turnos ya enmascarados, y
cada veredicto del juez, una línea más por prompt, corrida, turno y pregunta. --reanudar lee ese archivo y solo corre lo que
falta, y por eso el núcleo seguido de --completo --reanudar no paga dos veces. La huella (banco, prompt, herramientas, commit,
configuración, modelo, prompt del juez, fuentes y cambios sin commit) tiene que coincidir: mezclar corridas de otro commit, o
del mismo con otro cambio sin commit en agente/ o en datos/, falsearía el reporte. Lo que la corrida misma escribe en
eval/reportes/ no cuenta como cambio. Sin --reanudar, el avance anterior se renombra y no se pierde lo ya pagado.

Cuándo se detiene antes de tiempo. Con --limite-minutos, al empezar el primer prompt que quede después del límite: nunca a la
mitad de uno. Si un turno sale con modelo_no_disponible o fuente_no_disponible y no era lo esperado, el prompt no se guarda y
la corrida se detiene: un 429 en cadena (G11) o una base bloqueada no dicen nada del sistema, y --reanudar lo repite.

Juzgar otra vez (T5.13, D32-A). --rejuzgar toma las respuestas guardadas en un reporte, recalcula las reglas con el código y el
banco de hoy y llama solo al juez: el agente no corre y sus turnos no se pagan otra vez. Sin validación no llama a nada y sale
con SALIDA_JUEZ_SIN_VALIDAR. Escribe <reporte>-rejuzgado-<fecha>-<commit>.md y .json en la carpeta de reportes, con el acierto
con juez y con reglas solas, el reporte de origen, el commit del juez y lo que costó. Un reporte que no se puede usar sale con
SALIDA_REPORTE_INVALIDO, antes de llamar al modelo. Si alguna llamada no tuvo modelo sale con SALIDA_SIN_MODELO, y si alguna no dio
un veredicto legible, con SALIDA_SIN_VEREDICTO; en los dos casos no escribe nada: un veredicto que falta no reprueba, y la cifra
saldría inflada. Se vuelve a correr entero, porque esto no guarda avance. El reporte da también el saldo de los criterios corregidos
desde la línea base, contado de eval/cambios_criterios.md (D33-A).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import functools
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic_ai.models import Model
from pydantic_evals import Case, Dataset

import app
from agente import agente, construir, manifiesto, privacidad, registro
from agente import preguntas as modulo_preguntas
from agente.ajustes_modelo import AJUSTES, AJUSTES_BASE, AjustesDelModelo
from agente.consultas import abrir_consultas
from agente.herramientas import buscar_politicas as modulo_politicas
from agente.herramientas.comun import EntradaPoliticas, dumps
from agente.historial import Historial
from eval import banco, evaluadores, juez, reporte
from eval.evaluadores import FragmentoRecuperado, HerramientaCorrida, ResultadoPrompt, TurnoEvaluado
from eval.fixtures import copias

DIR_REPORTES = banco.RAIZ / "eval" / "reportes"
CARPETA_AVANCE = "avance"
ARCHIVO_AVANCE = "avance.jsonl"
# No deciden el resultado de una corrida, o los escribe ella misma: un cambio aquí no es un cambio en lo que se evalúa.
FUERA_DE_LA_HUELLA = ("eval/reportes", "docs", "tests", ".claude")
SEGUNDOS_DE_GIT = 30

SALIDA_BIEN = 0
SALIDA_AVANCE_INVALIDO = 1
# argparse sale con 2 ante un argumento inválido.
SALIDA_LIMITE = 3
SALIDA_SIN_MODELO = 4
SALIDA_FALLAS_DEL_COMANDO = 5
SALIDA_REPORTE_INVALIDO = 6
SALIDA_JUEZ_SIN_VALIDAR = 7
SALIDA_SIN_VEREDICTO = 8

CAMBIOS_DE_CRITERIOS = banco.RAIZ / "eval" / "cambios_criterios.md"
SENTIDOS_DE_CRITERIOS = ("afloja", "endurece", "nota")

CONFIGURACIONES = {"interfaz": AJUSTES, "base": AJUSTES_BASE}
ESTADOS_DEL_ENTORNO = ("modelo_no_disponible", "fuente_no_disponible")
_DATOS_DE_CONTACTO = {"TELEFONO", "CORREO"}


class CorridaDetenida(Exception):
    """La tarea no corrió este prompt porque la corrida ya se detuvo. No es una falla del prompt."""


class EntornoNoDisponible(Exception):
    """Un turno salió sin modelo o sin fuente y no era lo esperado. El mensaje dice el estado y su motivo."""


class AvanceInvalido(Exception):
    """El avance guardado es de otra configuración. El mensaje nombra lo que cambió."""


class ReporteInvalido(Exception):
    """El reporte que se quiere juzgar otra vez no se puede usar. El mensaje dice por qué."""


# ------------------------------------------------------------------ un turno


@dataclass(frozen=True)
class Configuracion:
    """Lo que no cambia entre turnos: el modelo (None lee .env), los ajustes, el registro y las rutas."""

    modelo: Model | str | None
    ajustes: AjustesDelModelo
    archivo_registro: Path
    base: Path
    dir_datos: Path
    raiz_copias: Path
    dir_juez: Path = juez.DIR_REPORTES

    def rutas_de(self, prompt: banco.Prompt) -> tuple[Path, Path]:
        """La base y los datos del prompt: los de su copia, si la trae (T5.6)."""
        if prompt.copia is None:
            return self.base, self.dir_datos
        armada = copias.copia(prompt.copia, self.raiz_copias)
        return armada.base, armada.dir_datos


def _sin_datos_de_tickets(valor: Any) -> Any:
    if isinstance(valor, str):
        return evaluadores.sin_datos_de_tickets(valor)
    if isinstance(valor, dict):
        return {clave: _sin_datos_de_tickets(dato) for clave, dato in valor.items()}
    if isinstance(valor, list):
        return [_sin_datos_de_tickets(dato) for dato in valor]
    return valor


def _para_guardar(valor: Any) -> Any:
    """Lo que va al reporte y al avance: sin datos de contacto, y sin el nombre ni el celular del ticket que los trae."""
    return _sin_datos_de_tickets(registro._limpio(valor))


def _lineas_de_fuente(respuesta: agente.Respuesta) -> tuple[str, ...]:
    """Como en app.mostrar_respuesta(): si el pie no se puede armar, lo dice en lugar de tumbar el turno."""
    try:
        return app.lineas_de_fuente(respuesta)
    except Exception as error:  # noqa: BLE001 (K21)
        print(f"La fuente de una respuesta no se pudo armar ({type(error).__name__}).", file=sys.stderr)
        return ("No pude armar la fuente de esta respuesta.",)


def _subsecciones(fila: dict[str, Any]) -> tuple[str, ...]:
    partes = (fila.get("subsecciones") or "").split("; ")
    return tuple(parte.split(maxsplit=1)[0] for parte in partes if parte)


def _herramienta_corrida(evidencia: agente.Evidencia) -> HerramientaCorrida:
    resultado = evidencia.resultado
    como_json = resultado.model_dump(mode="json", include={"filtros", "corte"})
    fragmentos: tuple[FragmentoRecuperado, ...] = ()
    if evidencia.herramienta == app.HERRAMIENTA_DE_POLITICAS and resultado.estado == "ok":
        fragmentos = tuple(
            FragmentoRecuperado(
                documento=fila["documento"], seccion=fila["seccion"], motivo=fila["motivo"], subsecciones=_subsecciones(fila)
            )
            for fila in resultado.filas
        )
    return HerramientaCorrida(
        nombre=evidencia.herramienta,
        estado=resultado.estado,
        entrada=_para_guardar(evidencia.entrada),
        filtros=_para_guardar(como_json["filtros"]),
        corte=como_json["corte"],
        definicion=resultado.definicion,
        fragmentos=fragmentos,
    )


def _revision_del_borrador(respuesta: agente.Respuesta) -> dict[str, Any]:
    """Lo que el verificador encontró en la primera versión del modelo: la del texto mostrado da 0 por construcción."""
    verificacion = respuesta.traza.verificacion
    if verificacion is None:
        return {}
    primera = verificacion.revisiones[0]
    return {
        "desenlace": verificacion.desenlace,
        "cifras_sin_evidencia": primera.cifras_sin_evidencia,
        "citas_sin_evidencia": primera.citas_sin_evidencia,
        "datos_personales_en_borrador": primera.datos_personales,
        "discrepancias": tuple(_para_guardar(f"{d.tipo}: {d.valor}") for d in primera.discrepancias),
    }


def _uso(respuesta: agente.Respuesta) -> dict[str, Any]:
    traza = respuesta.traza
    return {
        "entrada_tokens": traza.entrada_tokens,
        "entrada_no_cacheada": sum(registro.entrada_no_cacheada(llamada) for llamada in traza.llamadas),
        "cache_lectura_tokens": sum(llamada.cache_lectura_tokens for llamada in traza.llamadas),
        "cache_escritura_tokens": sum(llamada.cache_escritura_tokens for llamada in traza.llamadas),
        "salida_tokens": traza.salida_tokens,
        "razonamiento_tokens": sum(llamada.razonamiento_tokens for llamada in traza.llamadas),
        "n_llamadas": len(traza.llamadas),
        "costo_usd": registro._suma_costos([registro.costo_usd(llamada) for llamada in traza.llamadas]),
        "reintentos": len(traza.reintentos),
        "espera_reintentos_segundos": traza.espera_reintentos_segundos,
    }


def _fugas(respuesta: agente.Respuesta, lineas: Sequence[str]) -> dict[str, Any]:
    """Se mide antes de enmascarar y en todo prompt: lo que se guarda ya no trae el dato. Solo quedan el tipo y si hubo fuga."""
    entradas = [dumps(evidencia.entrada) for evidencia in respuesta.evidencias]
    visible = "\n".join([respuesta.texto, *lineas, *entradas])
    contacto = [tipo for tipo in privacidad.buscar_datos_personales(visible) if tipo in _DATOS_DE_CONTACTO]
    return {
        "fuga_contacto": tuple(contacto),
        "fuga_ticket": any(evaluadores.hay_dato_del_ticket(folio, visible) for folio in evaluadores.TICKETS_CON_DATOS_PERSONALES),
    }


def _capturar(respuesta: agente.Respuesta, paso: banco.Paso, previos: int, id_sesion: str, inicio: float) -> TurnoEvaluado:
    traza = respuesta.traza
    lineas = _lineas_de_fuente(respuesta)
    mostrado = app.sin_linea_de_fuente(app.sin_negritas(respuesta.texto))
    return TurnoEvaluado(
        pregunta=_para_guardar(paso.texto),
        estado=respuesta.estado,
        texto=_para_guardar(respuesta.texto),
        texto_mostrado=_para_guardar(mostrado),
        lineas_fuente=tuple(_para_guardar(linea) for linea in lineas),
        tope=traza.tope,
        falla=traza.falla.motivo if traza.falla else None,
        turno_id=respuesta.turno_id,
        id_sesion=id_sesion,
        n_mensajes_historial=previos,
        modelo=traza.modelo,
        del_manifiesto=manifiesto.es_del_manifiesto(respuesta),
        prompt_sha256=traza.prompt_sha256,
        herramientas_sha256=traza.herramientas_sha256,
        hash_fuentes=traza.hash_fuentes,
        ajustes_modelo=json.loads(dumps(dict(traza.ajustes_modelo))),
        herramientas=tuple(_herramienta_corrida(evidencia) for evidencia in respuesta.evidencias),
        tickets_sospechosos=traza.tickets_sospechosos,
        latencia_ms=round((time.monotonic() - inicio) * 1000),
        **_revision_del_borrador(respuesta),
        **_uso(respuesta),
        **_fugas(respuesta, lineas),
    )


def contestar(paso: banco.Paso, historial: Historial, conf: Configuracion, base: Path, dir_datos: Path) -> TurnoEvaluado:
    """Un turno, como app._atender_una(): la cobertura va al manifiesto, y su vuelta no entra al Historial. No lanza."""
    previos = len(historial.mensajes())
    inicio = time.monotonic()
    comunes = {"base": base, "dir_datos": dir_datos, "archivo_registro": conf.archivo_registro, "origen": "evaluacion"}
    try:
        if manifiesto.pide_la_cobertura(paso.texto):
            respuesta = manifiesto.responder(paso.texto, historial, categoria=paso.categoria, **comunes)
        else:
            respuesta = agente.responder(
                paso.texto, historial, modelo=conf.modelo, ajustes=conf.ajustes, categoria=paso.categoria, **comunes
            )
            historial.agregar(paso.texto, respuesta)
    except Exception as error:  # noqa: BLE001 (como la interfaz: una excepción es una falla del turno, sin su texto)
        return TurnoEvaluado(
            pregunta=_para_guardar(paso.texto),
            estado="excepcion",
            error=type(error).__name__,
            id_sesion=historial.id_sesion,
            n_mensajes_historial=previos,
            latencia_ms=round((time.monotonic() - inicio) * 1000),
        )
    return _capturar(respuesta, paso, previos, historial.id_sesion, inicio)


def _revisar_entorno(paso: banco.Paso, turno: TurnoEvaluado) -> None:
    if turno.estado not in ESTADOS_DEL_ENTORNO:
        return
    if all(pregunta.estado_esperado == turno.estado for pregunta in paso.preguntas):
        return
    detalle = f" ({turno.falla})" if turno.falla else ""
    raise EntornoNoDisponible(f"{turno.estado}{detalle}")


def _no_hay_como_seguir(turno: TurnoEvaluado) -> bool:
    """Como app._no_hay_como_seguir(): en un mensaje con varias preguntas, estas fallas se repetirían en la siguiente."""
    return turno.estado in (*ESTADOS_DEL_ENTORNO, "excepcion")


def correr_prompt(prompt: banco.Prompt, conf: Configuracion) -> ResultadoPrompt:
    """Los turnos del prompt, con un Historial por sesión. Un mensaje con signos contesta hasta MAXIMO_PREGUNTAS (T4.6)."""
    es_un_mensaje = prompt.forma == banco.CON_SIGNOS
    pasos = prompt.pasos[: modulo_preguntas.MAXIMO_PREGUNTAS] if es_un_mensaje else prompt.pasos
    base, dir_datos = conf.rutas_de(prompt)
    historiales: dict[int, Historial] = {}
    turnos: list[TurnoEvaluado] = []
    for paso in pasos:
        historial = historiales.setdefault(paso.sesion, Historial())
        turno = contestar(paso, historial, conf, base, dir_datos)
        _revisar_entorno(paso, turno)
        turnos.append(turno)
        if es_un_mensaje and _no_hay_como_seguir(turno):
            break
    return ResultadoPrompt(turnos=tuple(turnos))


# ------------------------------------------------------------------ guardar y reanudar


TIPO_VEREDICTO = "juez"


class Avance:
    """Lo que ya corrió: la huella en la primera línea, y una línea por prompt terminado y por veredicto del juez."""

    def __init__(
        self,
        ruta: Path,
        huella: dict[str, Any],
        guardados: dict[tuple[int, str], ResultadoPrompt],
        veredictos: Sequence[juez.VeredictoDelJuez] = (),
    ) -> None:
        self.ruta = ruta
        self.huella = huella
        self.guardados = guardados
        self.veredictos = juez.Veredictos(veredictos, persistir=self.guardar_veredicto)

    @classmethod
    def abrir(cls, ruta: Path, huella: dict[str, Any], *, reanudar: bool) -> Avance:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        if reanudar and ruta.exists():
            return cls(ruta, huella, *_leer_avance(ruta, huella))
        if ruta.exists():
            _guardar_aparte(ruta)
        with open(ruta, "w", encoding="utf-8", newline="\n") as salida:
            salida.write(dumps({"tipo": "huella", **huella}, compacto=True) + "\n")
        return cls(ruta, huella, {})

    def buscar(self, corrida: int, identificador: str) -> ResultadoPrompt | None:
        return self.guardados.get((corrida, identificador))

    def guardar(self, corrida: int, identificador: str, resultado: ResultadoPrompt) -> None:
        linea = {"tipo": "prompt", "corrida": corrida, "id": identificador, "turnos": resultado.model_dump(mode="json")["turnos"]}
        self._escribir(linea)
        self.guardados[(corrida, identificador)] = resultado

    def guardar_veredicto(self, veredicto: juez.VeredictoDelJuez) -> None:
        self._escribir({"tipo": TIPO_VEREDICTO, **veredicto.model_dump(mode="json")})

    def _escribir(self, linea: dict[str, Any]) -> None:
        with open(self.ruta, "a", encoding="utf-8", newline="\n") as salida:
            salida.write(dumps(linea, compacto=True) + "\n")
            salida.flush()
            os.fsync(salida.fileno())


def _guardar_aparte(ruta: Path) -> None:
    """Renombra el avance anterior: lo que ya se pagó no se borra aunque se empiece de cero."""
    marca = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    os.replace(ruta, ruta.with_name(f"{ruta.stem}-{marca}{ruta.suffix}"))


def _leer_avance(ruta: Path, huella: dict[str, Any]) -> tuple[dict[tuple[int, str], ResultadoPrompt], list[juez.VeredictoDelJuez]]:
    """Lo guardado. Una última línea cortada a medio escribir se quita, para que la siguiente no se pegue a ella."""
    lineas = ruta.read_text(encoding="utf-8").split("\n")
    buenas, guardados, veredictos = [], {}, []
    for texto in lineas:
        if not texto:
            continue
        try:
            datos = json.loads(texto)
        except json.JSONDecodeError:
            continue
        buenas.append(texto)
        if datos.get("tipo") == "huella":
            _comparar_huella(datos, huella)
        if datos.get("tipo") == "prompt":
            guardados[(datos["corrida"], datos["id"])] = ResultadoPrompt(turnos=datos["turnos"])
        if datos.get("tipo") == TIPO_VEREDICTO:
            veredictos.append(juez.VeredictoDelJuez.model_validate(datos))
    if len(buenas) != len([texto for texto in lineas if texto]):
        temporal = ruta.with_suffix(".tmp")
        temporal.write_text("".join(f"{texto}\n" for texto in buenas), encoding="utf-8", newline="\n")
        os.replace(temporal, ruta)
    return guardados, veredictos


def _comparar_huella(guardada: dict[str, Any], huella: dict[str, Any]) -> None:
    distintas = [clave for clave, valor in huella.items() if guardada.get(clave) != valor]
    if distintas:
        raise AvanceInvalido(f"el avance guardado es de otra corrida: cambió {', '.join(distintas)}")


def _contenido_de(raiz: Path, nombres: bytes) -> bytes:
    """Cada archivo de la lista de git, con su nombre y su contenido. Si uno no se puede leer, queda solo su nombre."""
    partes = []
    for nombre in filter(None, nombres.split(b"\0")):
        try:
            contenido = (raiz / os.fsdecode(nombre)).read_bytes()
        except OSError:
            contenido = b""
        partes += [nombre, contenido]
    return b"\0".join(partes)


def _diferencia_con_el_commit(raiz: Path) -> str | None:
    """sha256 de lo que difiere de HEAD, fuera de FUERA_DE_LA_HUELLA; None si nada difiere o no hay git.

    Es un hash y no un sí o no: dos cambios distintos sobre el mismo commit tampoco se mezclan. Un archivo sin seguimiento
    entra con su contenido, porque git diff HEAD no lo trae.
    """
    rutas = ["--", ".", *(f":(exclude){ruta}" for ruta in FUERA_DE_LA_HUELLA)]
    try:
        # En bytes: un diff puede traer lo que la codificación del sistema no sabe leer.
        estado, diferencia, sin_seguimiento = (
            subprocess.run(
                ["git", *argumentos, *rutas], cwd=raiz, capture_output=True, timeout=SEGUNDOS_DE_GIT, check=True
            ).stdout
            for argumentos in (("status", "--porcelain"), ("diff", "HEAD"), ("ls-files", "--others", "--exclude-standard", "-z"))
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not estado:
        return None
    return hashlib.sha256(b"\0".join((estado, diferencia, _contenido_de(raiz, sin_seguimiento)))).hexdigest()


@functools.cache
def cambios_sin_commit() -> str | None:
    """Como registro.commit(), se lee una vez por proceso: es el código que se cargó al arrancar."""
    return _diferencia_con_el_commit(registro.RAIZ)


def _hash_de_las_fuentes(conf: Configuracion) -> str | None:
    """El mismo de traza.hash_fuentes: datos/ y politicas/. Si la base no se puede armar, el primer turno lo dirá."""
    try:
        return construir.asegurar_base(conf.dir_datos, conf.base).hash_fuentes.sha256
    except (construir.ErrorConstruccion, OSError):
        return None


def _modelo_de(conf: Configuracion) -> Model | str:
    """El modelo como lo leerá responder()."""
    return conf.modelo if conf.modelo is not None else agente._modelo_configurado()


def huella_de(ruta_banco: Path, conf: Configuracion, nombre_configuracion: str) -> dict[str, Any]:
    """Lo que tiene que coincidir para sumar turnos y veredictos al mismo reporte."""
    return {
        "banco_sha256": banco.sha256_del_banco(ruta_banco),
        "prompt_sha256": agente.PROMPT_SHA256,
        "herramientas_sha256": agente.HERRAMIENTAS_SHA256,
        "commit": (registro.commit() or {}).get("sha"),
        "configuracion": nombre_configuracion,
        "modelo": agente._nombre_modelo(_modelo_de(conf)),
        "juez_prompt_sha256": juez.PROMPT_JUEZ_SHA256,
        "hash_fuentes": _hash_de_las_fuentes(conf),
        "cambios_sin_commit": cambios_sin_commit(),
    }


# ------------------------------------------------------------------ las corridas


@dataclass
class Control:
    """Si la corrida sigue, por qué se detuvo y qué se rompió."""

    reloj: Callable[[], float]
    limite_segundos: float | None
    inicio: float = 0.0
    detenida: str | None = None
    salida: int = SALIDA_BIEN
    fallas: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def detener(self, motivo: str, salida: int) -> None:
        self.detenida, self.salida = motivo, salida

    def revisar_el_limite(self) -> None:
        if self.limite_segundos is None or self.detenida:
            return
        if self.reloj() - self.inicio >= self.limite_segundos:
            self.detener(f"se alcanzó el límite de {self.limite_segundos / 60:,.1f} minutos", SALIDA_LIMITE)


def _tarea(corrida: int, conf: Configuracion, avance: Avance, control: Control) -> Callable[[banco.Prompt], ResultadoPrompt]:
    """La tarea de pydantic-evals: lo guardado se reusa, lo demás corre y se guarda al terminar."""

    def tarea(prompt: banco.Prompt) -> ResultadoPrompt:
        guardado = avance.buscar(corrida, prompt.id)
        if guardado is not None:
            return guardado
        control.revisar_el_limite()
        if control.detenida:
            raise CorridaDetenida(control.detenida)
        try:
            resultado = correr_prompt(prompt, conf)
        except EntornoNoDisponible as error:
            control.detener(f"{prompt.id}: {error}", SALIDA_SIN_MODELO)
            raise CorridaDetenida(control.detenida) from error
        except Exception as error:
            control.fallas.append({"corrida": corrida, "id": prompt.id, "tipo": type(error).__name__})
            print(f"Corrida {corrida}, {prompt.id}: falla del comando ({type(error).__name__}).", file=sys.stderr)
            raise
        avance.guardar(corrida, prompt.id, resultado)
        print(f"Corrida {corrida}, {prompt.id}: {', '.join(turno.estado for turno in resultado.turnos)}.", flush=True)
        return resultado

    return tarea


def _dataset(prompts: Sequence[banco.Prompt], criterio: juez.CriterioDelJuez) -> Dataset:
    return Dataset(
        name="set de evaluación",
        cases=[Case(name=prompt.id, inputs=prompt) for prompt in prompts],
        evaluators=[*evaluadores.EVALUADORES, criterio],
        report_evaluators=list(evaluadores.EVALUADORES_DE_REPORTE),
    )


def correr_corridas(
    prompts: Sequence[banco.Prompt],
    corridas: int,
    conf: Configuracion,
    avance: Avance,
    control: Control,
    estado_del_juez: juez.EstadoDeValidacion,
) -> list[Any]:
    """Un informe de pydantic-evals por corrida, en orden. Se detiene al terminar la corrida en que algo la detuvo."""
    control.inicio = control.reloj()
    informes = []
    for corrida in range(1, corridas + 1):
        tarea = _tarea(corrida, conf, avance, control)
        # Un Dataset por corrida porque el veredicto se guarda con la suya; el evaluador no recibe ese dato de pydantic-evals.
        criterio = juez.CriterioDelJuez(conf.modelo, conf.dir_juez, avance.veredictos, corrida, estado_del_juez)
        conjunto = _dataset(prompts, criterio)
        informes.append(conjunto.evaluate_sync(tarea, name=f"corrida {corrida}", max_concurrency=1, progress=False))
        if control.detenida:
            break
    return informes


# ------------------------------------------------------------------ recall@4 sin modelo (D26-A)


def _preguntas_sueltas(prompts: Sequence[banco.Prompt]) -> list[banco.Pregunta]:
    """Las preguntas que se entienden solas: un seguimiento de conversación sin su contexto no dice qué buscar."""
    vistas: dict[str, banco.Pregunta] = {}
    for prompt in prompts:
        pasos = prompt.pasos[:1] if prompt.bloque == "conversacion" else prompt.pasos
        for paso in pasos:
            for pregunta in paso.preguntas:
                if pregunta.secciones_esperadas:
                    vistas.setdefault(pregunta.prompt, pregunta)
    return list(vistas.values())


def medir_recall_sin_modelo(prompts: Sequence[banco.Prompt], *, base: Path, dir_datos: Path) -> dict[str, Any]:
    """recall@4 (al menos una sección esperada), fracción de las esperadas y MRR, con el texto de la gerente como consulta (T2.7, D26-A).

    Es el peor caso de la búsqueda: el modelo suele reescribir la consulta con las palabras de los documentos. La medida con
    el modelo sale del reporte de cada corrida.
    """
    construir.asegurar_base(dir_datos, base)
    consultas = abrir_consultas(base)
    medidas = []
    try:
        for pregunta in _preguntas_sueltas(prompts):
            entrada = EntradaPoliticas(consulta=pregunta.prompt[:300])
            cursor = consultas.sesion()
            try:
                resultado = modulo_politicas.buscar_politicas(cursor, entrada)
            finally:
                cursor.close()
            evidencia = agente.Evidencia("recall", "buscar_politicas", entrada.model_dump(), resultado)
            turno = TurnoEvaluado(pregunta=pregunta.prompt, estado="ok", herramientas=(_herramienta_corrida(evidencia),))
            medidas.append((pregunta.id, evaluadores.recall_de_seccion(pregunta, turno)))
    finally:
        consultas.cerrar()
    n = len(medidas)
    if not n:
        return {"n": 0, "recall_a_4": None, "fraccion_de_esperadas": None, "mrr": None, "sin_ninguna": []}
    return {
        "n": n,
        "recall_a_4": round(100 * sum(medida.alguna for _, medida in medidas) / n, 1),
        "fraccion_de_esperadas": round(100 * sum(medida.recall for _, medida in medidas) / n, 1),
        "mrr": round(sum(medida.reciproco for _, medida in medidas) / n, 3),
        "sin_ninguna": [identificador for identificador, medida in medidas if not medida.alguna],
    }


def _correr_recall(prompts: Sequence[banco.Prompt], base: Path, dir_datos: Path, dir_reportes: Path) -> int:
    medida = medir_recall_sin_modelo(prompts, base=base, dir_datos=dir_datos)
    sha = (registro.commit() or {}).get("sha") or "sin-commit"
    dir_reportes.mkdir(parents=True, exist_ok=True)
    ruta = dir_reportes / f"recall-sin-modelo-{dt.date.today().isoformat()}-{sha[:7]}.json"
    ruta.write_text(dumps({"commit": registro.commit(), **medida}) + "\n", encoding="utf-8", newline="\n")
    print(f"recall@4 de la sección sin modelo: {medida['recall_a_4']} % en {medida['n']} preguntas, con al menos una esperada en los 4.")
    print(f"Fracción de las esperadas: {medida['fraccion_de_esperadas']} %. MRR: {medida['mrr']}.")
    print(f"Sin ninguna sección: {', '.join(medida['sin_ninguna']) or 'ninguna'}.")
    print(f"Archivo: {ruta}")
    return SALIDA_BIEN


# ------------------------------------------------------------------ juzgar otra vez (T5.13, D32-A)


@dataclass(frozen=True)
class Origen:
    """Lo que se juzga otra vez: los prompts del banco de hoy y lo que contestaron en cada corrida del reporte."""

    ruta: Path
    meta: dict[str, Any]
    prompts: tuple[banco.Prompt, ...]
    corridas: tuple[int, ...]
    resultados: dict[tuple[int, str], ResultadoPrompt]


def _turnos_guardados(filas: Sequence[dict[str, Any]]) -> dict[tuple[int, str], ResultadoPrompt]:
    campos = set(TurnoEvaluado.model_fields)
    turnos: dict[tuple[int, str], list[TurnoEvaluado]] = {}
    for fila in filas:
        guardado = TurnoEvaluado(**{clave: valor for clave, valor in fila.items() if clave in campos})
        turnos.setdefault((fila["corrida"], fila["prompt"]), []).append(guardado)
    return {clave: ResultadoPrompt(turnos=tuple(de_la_clave)) for clave, de_la_clave in turnos.items()}


def _prompts_guardados(identificadores: set[str], ruta_banco: Path) -> tuple[banco.Prompt, ...]:
    """Los prompts del reporte, leídos del banco de hoy y en su orden. Las variantes también, porque un reporte las puede traer."""
    todos = banco.seleccionar(banco.leer_banco(ruta_banco), completo=True, variantes=True)
    faltan = sorted(identificadores - {prompt.id for prompt in todos})
    if faltan:
        raise ReporteInvalido(f"trae prompts que el banco de hoy no tiene: {', '.join(faltan)}")
    return tuple(prompt for prompt in todos if prompt.id in identificadores)


def _leer_origen(ruta: Path, ruta_banco: Path) -> Origen:
    """Lanza ReporteInvalido si no sirve: un reporte a medias daría una cifra que no se compara con ninguna otra."""
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        meta, resultados = datos["meta"], _turnos_guardados(datos["turnos"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReporteInvalido(f"no se puede leer ({type(error).__name__})") from error
    if meta.get("completo") is not True:
        raise ReporteInvalido("es de una corrida incompleta")
    if not resultados:
        raise ReporteInvalido("no trae turnos")
    corridas = tuple(sorted({corrida for corrida, _ in resultados}))
    identificadores = {identificador for _, identificador in resultados}
    prompts = _prompts_guardados(identificadores, ruta_banco)
    if len(resultados) != len(corridas) * len(identificadores):
        raise ReporteInvalido("no trae los mismos prompts en todas sus corridas")
    return Origen(ruta, meta, prompts, corridas, resultados)


def _cuantas_llamadas(origen: Origen) -> int:
    return sum(
        len(list(juez._casos_del_prompt(prompt, origen.resultados[(corrida, prompt.id)])))
        for corrida in origen.corridas
        for prompt in origen.prompts
    )


def _informes_con_lo_guardado(
    origen: Origen, modelo: Model | str, conf: Configuracion, estado: juez.EstadoDeValidacion, veredictos: juez.Veredictos
) -> list[Any]:
    """Un informe de pydantic-evals por corrida, con los mismos evaluadores de una corrida: la tarea solo lee lo guardado."""
    informes = []
    for corrida in origen.corridas:
        criterio = juez.CriterioDelJuez(modelo, conf.dir_juez, veredictos, corrida, estado)
        tarea = functools.partial(_lo_guardado, origen, corrida)
        informes.append(_dataset(origen.prompts, criterio).evaluate_sync(tarea, name=f"corrida {corrida}", max_concurrency=1, progress=False))
    return informes


def _lo_guardado(origen: Origen, corrida: int, prompt: banco.Prompt) -> ResultadoPrompt:
    return origen.resultados[(corrida, prompt.id)]


def _saldo_de_criterios() -> dict[str, int]:
    """Cuántas filas de la tabla de cambios tiene cada sentido. Vacío si no hay registro: el reporte lo dice y la corrida ya se pagó."""
    if not CAMBIOS_DE_CRITERIOS.exists():
        return {}
    saldo = dict.fromkeys(SENTIDOS_DE_CRITERIOS, 0)
    for renglon in CAMBIOS_DE_CRITERIOS.read_text(encoding="utf-8").splitlines():
        celdas = [celda.strip() for celda in renglon.strip().strip("|").split("|")]
        if len(celdas) == 5 and celdas[3] in saldo:
            saldo[celdas[3]] += 1
    return saldo


def _texto_del_saldo(saldo: dict[str, int]) -> str:
    if not saldo:
        return f"sin registro en {CAMBIOS_DE_CRITERIOS.name}"
    return f"{saldo['afloja']} se aflojan, {saldo['endurece']} se endurecen, {saldo['nota']} solo mueven una nota ({CAMBIOS_DE_CRITERIOS.name})"


def _seccion_de_rejuzgado(armado: dict[str, Any], origen: Origen, nombre_del_modelo: str, ruta_banco: Path) -> dict[str, Any]:
    return {
        "reporte_de_origen": origen.ruta.name,
        "commit_de_origen": origen.meta.get("commit"),
        "commit_del_juez": armado["meta"]["commit"],
        "cambios_sin_commit": armado["meta"]["cambios_sin_commit"],
        "modelo_del_juez": nombre_del_modelo,
        "juez_prompt_sha256": juez.PROMPT_JUEZ_SHA256,
        "banco_sha256": banco.sha256_del_banco(ruta_banco),
        "saldo_de_criterios": _saldo_de_criterios(),
        "uso_del_juez": armado["juez"]["uso"],
        "acierto_con_juez": armado["acierto"]["alcance"]["por_corrida"],
        "acierto_con_reglas_solas": armado["acierto_reglas"]["alcance"]["por_corrida"],
    }


def _encabezado_de_rejuzgado(seccion: dict[str, Any]) -> str:
    commit = seccion["commit_del_juez"] or {}
    sucio = " con cambios sin commit" if commit.get("sucio") else ""
    uso = seccion["uso_del_juez"]
    costo = uso["costo_usd"] if uso["costo_usd"] is not None else "sin precio"
    con_juez = "; ".join(reporte._texto_de_tasa(fila) for fila in seccion["acierto_con_juez"])
    con_reglas = "; ".join(reporte._texto_de_tasa(fila) for fila in seccion["acierto_con_reglas_solas"])
    return "\n".join(
        [
            f"# Juzgar otra vez: {seccion['reporte_de_origen']}",
            "",
            f"Con juez: {con_juez}. Con reglas solas: {con_reglas}.",
            "",
            f"- Reporte de origen: {seccion['reporte_de_origen']}, commit {(seccion['commit_de_origen'] or {}).get('sha')}",
            f"- Commit del juez, del banco y de las reglas: {commit.get('sha')}{sucio}",
            f"- Modelo del juez: {seccion['modelo_del_juez']}; prompt y esquema, sha256: {seccion['juez_prompt_sha256']}",
            f"- Criterios corregidos desde la línea base: {_texto_del_saldo(seccion['saldo_de_criterios'])}",
            f"- Uso del juez: {reporte.entero(uso['llamadas'])} llamadas, {reporte.entero(uso['entrada_tokens'])} tokens de entrada, "
            f"{reporte.entero(uso['salida_tokens'])} de salida, {costo} USD",
            "",
            "Los turnos no se volvieron a correr: el uso, el costo y la latencia de los turnos de abajo son los de la corrida de origen.",
            "",
            "",
        ]
    )


def _guardar_rejuzgado(armado: dict[str, Any], origen: Origen, carpeta: Path) -> tuple[Path, Path]:
    """Con su propio nombre: reporte.guardar() sobrescribiría el reporte del mismo día y commit."""
    sha = (armado["meta"]["commit"] or {}).get("sha") or "sin-commit"
    nombre = f"{origen.ruta.stem}-rejuzgado-{armado['meta']['fecha']}-{sha[:7]}"
    carpeta.mkdir(parents=True, exist_ok=True)
    markdown, como_json = carpeta / f"{nombre}.md", carpeta / f"{nombre}.json"
    markdown.write_text(_encabezado_de_rejuzgado(armado["rejuzgado"]) + reporte.a_markdown(armado), encoding="utf-8", newline="\n")
    como_json.write_text(dumps(armado) + "\n", encoding="utf-8", newline="\n")
    return markdown, como_json


def _rejuzgar(ruta: Path, ruta_banco: Path, conf: Configuracion, dir_reportes: Path) -> int:
    modelo = _modelo_de(conf)
    nombre_del_modelo = agente._nombre_modelo(modelo)
    estado = juez.estado_de_validacion(nombre_del_modelo, conf.dir_juez)
    if not estado.validado:
        print(
            f"No se llamó al modelo: el juez no está validado ({estado.motivo}). Corre python -m eval.juez --modelo {nombre_del_modelo}.",
            file=sys.stderr,
        )
        return SALIDA_JUEZ_SIN_VALIDAR
    try:
        origen = _leer_origen(ruta, ruta_banco)
    except ReporteInvalido as error:
        print(f"No se llamó al modelo: {ruta.name} {error}.", file=sys.stderr)
        return SALIDA_REPORTE_INVALIDO
    print(f"{_cuantas_llamadas(origen):,} llamadas al juez con {nombre_del_modelo}, o más si alguna se reintenta. El agente no corre.", flush=True)
    veredictos = juez.Veredictos()
    informes = _informes_con_lo_guardado(origen, modelo, conf, estado, veredictos)
    sin_modelo = [veredicto for veredicto in veredictos.todos() if veredicto.falla_del_modelo]
    if sin_modelo:
        print(
            f"No se escribió ningún reporte: {len(sin_modelo):,} llamadas no tuvieron modelo ({sin_modelo[0].motivo}). Vuelve a correr el comando.",
            file=sys.stderr,
        )
        return SALIDA_SIN_MODELO
    ilegibles = [veredicto for veredicto in veredictos.todos() if veredicto.veredicto == juez.SIN_VEREDICTO]
    if ilegibles:
        casos = ", ".join(f"{veredicto.pregunta} de la corrida {veredicto.corrida} ({veredicto.motivo})" for veredicto in ilegibles)
        print(f"No se escribió ningún reporte: {len(ilegibles):,} veredictos ilegibles: {casos}. Vuelve a correr el comando.", file=sys.stderr)
        return SALIDA_SIN_VEREDICTO
    armado = reporte.armar(
        origen.prompts,
        informes,
        corridas_pedidas=len(origen.corridas),
        completo=origen.meta.get("alcance") == "completo",
        variantes=bool(origen.meta.get("variantes")),
        configuracion=origen.meta.get("configuracion", "interfaz"),
        ajustes=origen.meta.get("ajustes", {}),
        estado_del_juez=estado,
        validacion_del_juez=juez.resumen_de_validacion(nombre_del_modelo, conf.dir_juez, estado=estado),
        veredictos=veredictos.todos(),
        cambios_sin_commit=cambios_sin_commit(),
    )
    armado["rejuzgado"] = _seccion_de_rejuzgado(armado, origen, nombre_del_modelo, ruta_banco)
    rutas = _guardar_rejuzgado(armado, origen, dir_reportes)
    print(reporte.resumen_de_consola(armado, rutas))
    return SALIDA_BIEN


# ------------------------------------------------------------------ el comando


def _entero_positivo(texto: str) -> int:
    valor = int(texto)
    if valor < 1:
        raise argparse.ArgumentTypeError("debe ser 1 o más")
    return valor


def _minutos(texto: str) -> float:
    valor = float(texto)
    if valor <= 0:
        raise argparse.ArgumentTypeError("debe ser mayor que 0")
    return valor


def _argumentos(argumentos: Sequence[str] | None) -> argparse.Namespace:
    lector = argparse.ArgumentParser(prog="python -m eval.correr", description="Corre el set y escribe el reporte (T5.3).")
    lector.add_argument("--completo", action="store_true", help="los 100 prompts; sin esto, los 30 del núcleo")
    lector.add_argument("--variantes", action="store_true", help="suma las paráfrasis de las sencillas, aparte del rango")
    lector.add_argument("--corridas", type=_entero_positivo, default=2, help="corridas del set (2 por omisión)")
    lector.add_argument("--reanudar", action="store_true", help="sigue donde se quedó, sin repetir llamadas")
    lector.add_argument("--limite-minutos", type=_minutos, help="se detiene después del prompt en curso")
    lector.add_argument("--configuracion", choices=sorted(CONFIGURACIONES), default="interfaz", help="base es la de T6.1")
    lector.add_argument("--modelo", help="id del modelo; sin esto, MODELO de .env, como la interfaz")
    lector.add_argument("--recall-sin-modelo", action="store_true", help="recall@4 con el texto de cada pregunta, sin llave")
    lector.add_argument("--rejuzgar", type=Path, metavar="REPORTE.JSON", help="solo el juez, sobre las respuestas de ese reporte: no corre el agente")
    lector.add_argument("--banco", type=Path, default=banco.BANCO, help=argparse.SUPPRESS)
    opciones = lector.parse_args(argumentos)
    if opciones.rejuzgar:
        de_una_corrida = {
            "--completo": opciones.completo,
            "--variantes": opciones.variantes,
            "--reanudar": opciones.reanudar,
            "--limite-minutos": opciones.limite_minutos,
            "--recall-sin-modelo": opciones.recall_sin_modelo,
        }
        mezcladas = [nombre for nombre, activa in de_una_corrida.items() if activa]
        if mezcladas:
            lector.error(f"--rejuzgar no se mezcla con {', '.join(mezcladas)}")
    return opciones


def main(
    argumentos: Sequence[str] | None = None,
    *,
    modelo: Model | str | None = None,
    archivo_registro: Path = registro.ARCHIVO,
    dir_reportes: Path = DIR_REPORTES,
    base: Path = construir.BASE,
    dir_datos: Path = construir.DIR_DATOS,
    raiz_copias: Path = copias.RAIZ_COPIAS,
    reloj: Callable[[], float] = time.monotonic,
    dir_juez: Path = juez.DIR_REPORTES,
) -> int:
    """`python -m eval.correr`. Los parámetros de palabra clave son para las pruebas: la línea de comandos no los cambia."""
    # En Windows, una salida redirigida usa cp1252, que no tiene todos los caracteres de las preguntas.
    for salida in (sys.stdout, sys.stderr):
        if isinstance(salida, io.TextIOWrapper):
            salida.reconfigure(encoding="utf-8")
    opciones = _argumentos(argumentos)
    prompts = banco.leer_banco(opciones.banco)
    if opciones.recall_sin_modelo:
        return _correr_recall(prompts, base, dir_datos, Path(dir_reportes))
    if opciones.modelo:
        # responder() solo carga .env cuando no recibe modelo: con el id como texto, la llave no se leería.
        load_dotenv()
        modelo = opciones.modelo
    conf = Configuracion(
        modelo,
        CONFIGURACIONES[opciones.configuracion],
        Path(archivo_registro),
        Path(base),
        Path(dir_datos),
        Path(raiz_copias),
        Path(dir_juez),
    )
    if opciones.rejuzgar:
        return _rejuzgar(opciones.rejuzgar, opciones.banco, conf, Path(dir_reportes))
    elegidos = banco.seleccionar(prompts, completo=opciones.completo, variantes=opciones.variantes)
    huella = huella_de(opciones.banco, conf, opciones.configuracion)
    try:
        ruta_avance = Path(dir_reportes) / CARPETA_AVANCE / ARCHIVO_AVANCE
        avance = Avance.abrir(ruta_avance, huella, reanudar=opciones.reanudar)
    except AvanceInvalido as error:
        print(f"No se puede reanudar: {error}. Corre sin --reanudar para empezar de cero.", file=sys.stderr)
        return SALIDA_AVANCE_INVALIDO
    control = Control(reloj, opciones.limite_minutos * 60 if opciones.limite_minutos else None)
    nombre_del_modelo = agente._nombre_modelo(_modelo_de(conf))
    estado_del_juez = juez.estado_de_validacion(nombre_del_modelo, conf.dir_juez)
    informes = correr_corridas(elegidos, opciones.corridas, conf, avance, control, estado_del_juez)
    armado = reporte.armar(
        elegidos,
        informes,
        corridas_pedidas=opciones.corridas,
        completo=opciones.completo,
        variantes=opciones.variantes,
        configuracion=opciones.configuracion,
        ajustes=dataclasses.asdict(conf.ajustes),
        detenida=control.detenida,
        fallas=control.fallas,
        estado_del_juez=estado_del_juez,
        validacion_del_juez=juez.resumen_de_validacion(nombre_del_modelo, conf.dir_juez, estado=estado_del_juez),
        veredictos=avance.veredictos.todos(),
        cambios_sin_commit=huella["cambios_sin_commit"],
    )
    rutas = reporte.guardar(armado, Path(dir_reportes))
    print(reporte.resumen_de_consola(armado, rutas))
    if control.fallas:
        return SALIDA_FALLAS_DEL_COMANDO
    if control.detenida:
        print(f"Se detuvo: {control.detenida}. Corre con --reanudar para seguir.", file=sys.stderr)
    return control.salida


if __name__ == "__main__":
    raise SystemExit(main())
