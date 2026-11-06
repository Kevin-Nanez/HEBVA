"""T6.4, C6.2: la proyección mensual y las solicitudes contra la cuota salen de los reportes guardados y del plan. Sin red y sin llaves.

eval/reportes/proyeccion-mensual.md se escribe a mano con cifras calculadas. Esta prueba las recalcula desde los JSON de
eval/reportes/ que el texto cita, desde config/precios.yaml, desde las cuotas que guarda eval/reportes/cuota-gemini.md y desde las
estimaciones del plan (docs/planes/2026-10-31-plan-implementacion.html), y comprueba que cada una aparece en su tabla y en su sección
con el formato del proyecto (coma de miles y punto decimal). Cada veredicto («alcanza», «no alcanza», «cabe», «Sí», «No») se calcula
contra la cuota y se busca dentro de la frase que lo lleva, no solo la cifra. Si un reporte o el plan cambian, o una cifra o un
veredicto se teclea mal, falla.

La batería del evaluador sale del plan, no de una constante de esta prueba: la sección «Eficiencia del modelo» dice que la batería, sus
turnos extra y dos corridas del set suman unos 215 turnos y ~470 llamadas en un día, y que esas dos corridas eran de un set de unos 140
turnos. Los turnos de la batería propia son la resta, y el set de ahora no se suma a los 215 ni a las 470 (eso contaría dos veces las
corridas del set). Si el plan reescribe esas frases, la prueba falla y pide revisar los escenarios con batería.

Lo que no sale de un archivo se comprueba solo como texto: los 191 turnos y 403 solicitudes de la corrida de Flash-Lite que se detuvo
(sin reporte) y lo que dicen las páginas de los proveedores (límites de Anthropic, página de límites de Google).
"""

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import pytest
import yaml
from tests.conftest import SIN_DOCS, requiere_reportes

RAIZ = Path(__file__).resolve().parent.parent
REPORTES = RAIZ / "eval" / "reportes"
# proyeccion-mensual.md y los reportes que cita no viajan en el zip.
pytestmark = requiere_reportes
TEXTO = REPORTES / "proyeccion-mensual.md"
CUOTAS = REPORTES / "cuota-gemini.md"
PRECIOS = RAIZ / "config" / "precios.yaml"
BITACORA = RAIZ / "eval" / "bitacora-asistente.md"
PLAN = RAIZ / "docs" / "planes" / "2026-10-31-plan-implementacion.html"

PREGUNTAS_POR_DIA = 30
DIAS_AL_MES = 22
TURNOS_AL_MES = PREGUNTAS_POR_DIA * DIAS_AL_MES
FECHA = "2026-11-03"
MODELO_FLASH_LITE = "gemini-3.5-flash-lite"
MODELO_3_8_FLASH = "gemini-3.8-flash"
ESPERA_DEL_429_POR_DIA_S = 43005
CENTRO_DE_MEXICO = "America/Mexico_City"
PACIFICO = "America/Los_Angeles"
SOLICITUDES_DE_LA_CORRIDA_QUE_SE_DETUVO = 403
LIMITE_DE_HAIKU_POR_MINUTO = 4000  # página de límites de Anthropic, nivel Start: no sale de ningún archivo del repositorio
LINEAS_DE_LA_PLANTILLA = 5
CARACTERES_POR_LINEA_DE_LA_PLANTILLA = 150

HAIKU = "2026-11-03-completo-9600d3f.json"
HAIKU_BASE = "2026-11-03-completo-base-67e8659.json"
HAIKU_OTRAS_DOS = ("2026-11-03-completo-680bd88.json", "2026-11-03-completo-50e4eae.json")
HAIKU_NUCLEO = "2026-11-03-nucleo-680bd88-haiku.json"
FLASH_LITE = "2026-11-03-nucleo-680bd88-flash-lite.json"
CITADOS = (HAIKU, HAIKU_BASE, *HAIKU_OTRAS_DOS, HAIKU_NUCLEO, FLASH_LITE)

FUENTES = (
    "https://platform.claude.com/docs/en/api/rate-limits",
    "https://platform.claude.com/docs/en/about-claude/pricing",
    "https://ai.google.dev/gemini-api/docs/rate-limits",
    "https://ai.google.dev/gemini-api/docs/pricing",
)

# La primera celda de cada renglón de las tablas del texto: es la clave con que esta prueba lo encuentra.
COSTO_CALIENTE = "Haiku 5.5, esfuerzo bajo y caché caliente"
COSTO_FRIO = "Haiku 5.5, esfuerzo bajo y caché fría en cada pregunta"
COSTO_BASE = "Haiku 5.5, valores por omisión y sin caché"
COSTO_FLASH_LITE = "Gemini 3.5 Flash-Lite, esfuerzo medio, precio de lista"
COSTO_3_8_FLASH = "Gemini 3.8 Flash, sin medir"
POR_DIA_HAIKU = "Claude Haiku 5.5"
POR_DIA_FLASH_LITE = "Gemini 3.5 Flash-Lite gratis"
POR_DIA_3_8_FLASH = "Gemini 3.8 Flash gratis"
USO_SUPUESTO = "Uso supuesto, un día"
NUCLEO_1 = "Núcleo, 1 corrida"
NUCLEO_2 = "Núcleo, 2 corridas (comando por omisión)"
SET_1 = "Set completo, 1 corrida"
SET_2 = "Set completo, 2 corridas"
BATERIA = "Batería del evaluador sin el set"
BATERIA_Y_NUCLEO_2 = "Batería y 2 corridas del núcleo (comando por omisión)"
BATERIA_Y_SET_1 = "Batería y 1 corrida completa del set"
BATERIA_Y_SET_2 = "Batería y 2 corridas completas del set"
ESCENARIOS = (USO_SUPUESTO, NUCLEO_1, NUCLEO_2, SET_1, SET_2, BATERIA, BATERIA_Y_NUCLEO_2, BATERIA_Y_SET_1, BATERIA_Y_SET_2)
PLANTILLA = "Texto para la plantilla (sección 4, T8.2)"
SECCIONES = (
    "Conclusión",
    PLANTILLA,
    "Supuesto de uso",
    "Costo mensual por modelo",
    "Solicitudes por día contra la cuota",
    "Una corrida del set y la batería del evaluador",
    "Qué publica cada proveedor y qué se observó",
    "Contra las estimaciones del plan",
    "Límites de estas cifras",
    "Fuentes",
    "Cómo se calculó",
)

# Cómo nombra cada escenario la plantilla en su línea de «Alcanza» o de «No alcanza», y el renglón de la tabla del que sale la cifra.
ESCENARIOS_DE_LA_PLANTILLA = (
    ("uso supuesto", USO_SUPUESTO),
    ("una corrida del set", SET_1),
    ("batería y comando por omisión", BATERIA_Y_NUCLEO_2),
    ("batería y una corrida del set", BATERIA_Y_SET_1),
    ("dos corridas del set", SET_2),
    ("batería y dos corridas del set", BATERIA_Y_SET_2),
)


@dataclass(frozen=True)
class EstimacionDelPlan:
    """Lo que el plan estima para el día del evaluador, leído de su HTML.

    «La batería de 62 pruebas con sus turnos extra, P41 tres veces, 5 variantes, las copias A a D y dos corridas del set suman unos 215
    turnos y ~470 llamadas en un día». Esas dos corridas son del set de antes de D25 (unas 140 vueltas en total, D7-A y la comparación
    de T6.3), así que dentro de los 215 turnos la batería propia es la resta y el set de ahora no se suma encima.
    """

    frase_de_la_bateria: str
    pruebas_de_la_bateria: int
    turnos_del_dia: int
    llamadas_del_dia: int
    turnos_de_dos_corridas: int
    llamadas_con_dos_corridas_completas: int

    @property
    def turnos_de_la_bateria(self) -> int:
        return self.turnos_del_dia - self.turnos_de_dos_corridas

    @property
    def llamadas_por_turno(self) -> Decimal:
        """Las ~470 llamadas entre los 215 turnos, a un decimal: las 2.2 llamadas por turno con que el plan saca sus 66 solicitudes al día."""
        return (Decimal(self.llamadas_del_dia) / self.turnos_del_dia).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


class Escenario(NamedTuple):
    """Un renglón de la tabla de escenarios: los turnos y las solicitudes que pide con cada modelo."""

    turnos: int
    con_haiku: Decimal
    con_flash: Decimal


@dataclass(frozen=True)
class Medida:
    """Lo que un reporte de eval.correr aporta a la proyección: turnos, tokens, costo y solicitudes del uso_total."""

    reporte: dict

    @property
    def uso(self) -> dict:
        return self.reporte["uso_total"]

    @property
    def turnos(self) -> int:
        return self.uso["turnos"]

    @property
    def solicitudes(self) -> int:
        return self.uso["n_llamadas"]

    @property
    def costo(self) -> Decimal:
        return Decimal(self.uso["costo_usd"])

    @property
    def costo_por_turno(self) -> Decimal:
        return self.costo / self.turnos

    @property
    def solicitudes_por_turno(self) -> Decimal:
        return Decimal(self.solicitudes) / self.turnos

    @property
    def salida_por_turno(self) -> Decimal:
        return Decimal(self.uso["salida_tokens"]) / self.turnos

    @property
    def turnos_por_corrida(self) -> int:
        return self.turnos // self.reporte["meta"]["corridas_completas"]

    @property
    def mediana_de_costo(self) -> Decimal:
        return Decimal(str(self.reporte["eficiencia_total"]["costo_usd"]["mediana"]))

    @property
    def prefijo_de_cache(self) -> int:
        """Tokens de caché que lee cada solicitud: el prompt de sistema y las definiciones de las herramientas."""
        return max(turno["cache_lectura_tokens"] // turno["n_llamadas"] for turno in self.reporte["turnos"] if turno["n_llamadas"])

    @property
    def escrituras_de_cache(self) -> int:
        return sum(1 for turno in self.reporte["turnos"] if turno["cache_escritura_tokens"])


def _redondear(valor, decimales=0) -> Decimal:
    return Decimal(valor).quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)


def _numero(valor, decimales=0) -> str:
    """Coma de miles y punto decimal, sin depender del locale del sistema."""
    return f"{_redondear(valor, decimales):,.{decimales}f}"


def _porcentaje(parte, total) -> str:
    return f"{_numero(Decimal(100) * Decimal(parte) / Decimal(total))} %"


def _cabe(solicitudes, limite) -> bool:
    """Lo mismo que decide la columna «¿Alcanza?»: las solicitudes redondeadas contra la cuota."""
    return _redondear(solicitudes) <= limite


def _alcanza(solicitudes, limite) -> str:
    return "alcanza" if _cabe(solicitudes, limite) else "no alcanza"


def _alcanzan(solicitudes, limite) -> str:
    return "alcanzan" if _cabe(solicitudes, limite) else "no alcanzan"


def _filas(texto: str) -> list[list[str]]:
    filas = []
    for renglon in texto.splitlines():
        if not renglon.startswith("|"):
            continue
        celdas = [celda.strip() for celda in renglon.strip().strip("|").split("|")]
        if set("".join(celdas)) <= set("-: "):
            continue
        filas.append(celdas)
    return filas


def _fila(filas: list[list[str]], etiqueta: str) -> list[str]:
    encontradas = [fila for fila in filas if fila[0] == etiqueta]
    assert len(encontradas) == 1, f"el texto trae {len(encontradas)} renglones de tabla que abren con «{etiqueta}», y tiene que traer uno"
    return encontradas[0]


def _seccion(texto: str, titulo: str) -> str:
    [seccion] = re.findall(rf"^## {re.escape(titulo)}\n(.*?)(?=^## |\Z)", texto, flags=re.MULTILINE | re.DOTALL)
    return seccion


def _lineas_de_la_plantilla(texto: str) -> list[str]:
    return [linea for linea in _seccion(texto, PLANTILLA).splitlines() if linea.strip()]


def _cuotas_observadas() -> dict[tuple[str, str], int]:
    """(modelo, 'minuto' o 'día') -> valor, como los dejó el 429 de Google en eval/reportes/cuota-gemini.md."""
    cuotas = {}
    for fila in _filas(CUOTAS.read_text(encoding="utf-8")):
        if fila[0].startswith("gemini-"):
            cuotas[(fila[0], "minuto" if "per minute" in fila[1] else "día")] = int(fila[2])
    return cuotas


def _primer_429_por_dia_de_3_8_flash() -> datetime:
    """La hora, en el centro de México, de la primera vez que el servicio dijo «limit: 20» (cuota-gemini.md)."""
    registro = CUOTAS.read_text(encoding="utf-8")
    [(dia, hora)] = re.findall(rf"\| {re.escape(MODELO_3_8_FLASH)} \|[^|]*\|\s*20\s*\|\s*(\d{{4}}-\d\d-\d\d) (\d\d:\d\d:\d\d)", registro)
    return datetime.fromisoformat(f"{dia} {hora}").replace(tzinfo=ZoneInfo(CENTRO_DE_MEXICO))


def _reposicion_diaria_en_el_centro_de_mexico() -> datetime:
    """La medianoche del Pacífico que sigue al primer 429 por día de 3.8 Flash, a la hora del centro de México."""
    medianoche = (_primer_429_por_dia_de_3_8_flash().astimezone(ZoneInfo(PACIFICO)) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return medianoche.astimezone(ZoneInfo(CENTRO_DE_MEXICO))


def _una_vez(patron: str, texto: str, que: str):
    """El único hallazgo del patrón en el texto del plan; si el plan reescribió la frase, la prueba lo dice en lugar de adivinar."""
    hallados = re.findall(patron, texto)
    assert len(hallados) == 1, (
        f"el plan ya no dice «{que}» una sola vez ({len(hallados)} hallazgos): revisa los escenarios con batería de "
        "eval/reportes/proyeccion-mensual.md antes de tocar esta prueba"
    )
    return hallados[0]


def _estimacion_del_plan() -> EstimacionDelPlan:
    texto = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", PLAN.read_text(encoding="utf-8")))
    frase, pruebas, turnos, llamadas = _una_vez(
        r"(la batería de (\d+) pruebas con sus turnos extra[^.]{0,120}? dos corridas del set suman unos (\d+) turnos y ~(\d+) llamadas en un día)",
        texto,
        "la batería de N pruebas con sus turnos extra, ... y dos corridas del set suman unos N turnos y ~N llamadas en un día",
    )
    en_d7 = _una_vez(r"2 corridas del set \(~(\d+) turnos\)", texto, "2 corridas del set (~N turnos)")
    en_t6_3 = _una_vez(r"unas (\d+) vueltas por 2 corridas", texto, "unas N vueltas por 2 corridas")
    assert en_d7 == en_t6_3, f"el plan da dos tamaños para las dos corridas del set de antes: {en_d7} en D7 y {en_t6_3} en T6.3"
    desde, con_dos_completas = _una_vez(r"de unas (\d+) a unas (\d+) llamadas", texto, "de unas N a unas N llamadas")
    assert desde == llamadas, f"D25 parte de {desde} llamadas y la sección de eficiencia dice {llamadas}"
    return EstimacionDelPlan(
        frase_de_la_bateria=frase,
        pruebas_de_la_bateria=int(pruebas),
        turnos_del_dia=int(turnos),
        llamadas_del_dia=int(llamadas),
        turnos_de_dos_corridas=int(en_d7),
        llamadas_con_dos_corridas_completas=int(con_dos_completas),
    )


def _escenarios(medidas: dict[str, Medida], plan: EstimacionDelPlan) -> dict[str, Escenario]:
    """etiqueta -> turnos y solicitudes con Haiku 5.5 y con Flash-Lite de cada escenario de la tabla."""
    haiku, flash, nucleo = medidas[HAIKU], medidas[FLASH_LITE], medidas[HAIKU_NUCLEO]
    turnos_set = haiku.turnos_por_corrida
    set_con_haiku = Decimal(haiku.solicitudes) / haiku.reporte["meta"]["corridas_completas"]
    set_con_flash = flash.solicitudes_por_turno * turnos_set
    turnos_bateria = plan.turnos_de_la_bateria
    bateria_con_haiku = turnos_bateria * haiku.solicitudes_por_turno
    bateria_con_flash = turnos_bateria * flash.solicitudes_por_turno
    nucleo_con_haiku, nucleo_con_flash = Decimal(nucleo.solicitudes), Decimal(flash.solicitudes)
    dia = PREGUNTAS_POR_DIA
    return {
        USO_SUPUESTO: Escenario(dia, haiku.solicitudes_por_turno * dia, flash.solicitudes_por_turno * dia),
        NUCLEO_1: Escenario(nucleo.turnos, nucleo_con_haiku, nucleo_con_flash),
        NUCLEO_2: Escenario(2 * nucleo.turnos, 2 * nucleo_con_haiku, 2 * nucleo_con_flash),
        SET_1: Escenario(turnos_set, set_con_haiku, set_con_flash),
        SET_2: Escenario(2 * turnos_set, 2 * set_con_haiku, 2 * set_con_flash),
        BATERIA: Escenario(turnos_bateria, bateria_con_haiku, bateria_con_flash),
        BATERIA_Y_NUCLEO_2: Escenario(
            turnos_bateria + 2 * nucleo.turnos, bateria_con_haiku + 2 * nucleo_con_haiku, bateria_con_flash + 2 * nucleo_con_flash
        ),
        BATERIA_Y_SET_1: Escenario(turnos_bateria + turnos_set, bateria_con_haiku + set_con_haiku, bateria_con_flash + set_con_flash),
        BATERIA_Y_SET_2: Escenario(
            turnos_bateria + 2 * turnos_set, bateria_con_haiku + 2 * set_con_haiku, bateria_con_flash + 2 * set_con_flash
        ),
    }


def _extra_de_la_cache_fria(haiku: Medida, precios: dict) -> Decimal:
    """Por turno: la primera solicitud escribe el prefijo en lugar de leerlo."""
    precio = precios["claude-haiku-5-5"]
    diferencia = Decimal(precio["cache_escritura"]) - Decimal(precio["cache_lectura"])
    return haiku.prefijo_de_cache * diferencia / 1_000_000


def _referencia_de_3_8_flash(flash: Medida, precios: dict) -> Decimal:
    """Al mes: los tokens medidos de Flash-Lite a los precios de 3.8 Flash. No es una medición."""
    precio = precios[MODELO_3_8_FLASH]
    costo = (flash.uso["entrada_tokens"] * Decimal(precio["entrada"]) + flash.uso["salida_tokens"] * Decimal(precio["salida"])) / 1_000_000
    return costo / flash.turnos * TURNOS_AL_MES


def _cifras_por_seccion(medidas, cuotas, precios, plan) -> dict[str, list[str]]:
    """Las frases con cifras y veredictos que cada sección del texto tiene que traer, calculadas desde los reportes y el plan."""
    haiku, base, flash, nucleo = medidas[HAIKU], medidas[HAIKU_BASE], medidas[FLASH_LITE], medidas[HAIKU_NUCLEO]
    n = _numero
    mes = TURNOS_AL_MES
    escenarios = _escenarios(medidas, plan)
    precio_haiku, precio_flash, precio_38 = precios["claude-haiku-5-5"], precios[MODELO_FLASH_LITE], precios[MODELO_3_8_FLASH]

    caliente = n(haiku.costo_por_turno * mes, 2)
    extra = _extra_de_la_cache_fria(haiku, precios)
    frio = n((haiku.costo_por_turno + extra) * mes, 2)
    flash_mes = n(flash.costo_por_turno * mes, 2)
    referencia = _referencia_de_3_8_flash(flash, precios)
    meses = [medidas[nombre].costo_por_turno * mes for nombre in (HAIKU, *HAIKU_OTRAS_DOS)]

    limite_dia, limite_min = cuotas[(MODELO_FLASH_LITE, "día")], cuotas[(MODELO_FLASH_LITE, "minuto")]
    limite_38 = cuotas[(MODELO_3_8_FLASH, "día")]
    razon = flash.solicitudes_por_turno
    uso, nucleo_1 = escenarios[USO_SUPUESTO], escenarios[NUCLEO_1]
    set1, set2 = escenarios[SET_1], escenarios[SET_2]
    bateria, bateria_nucleo_2 = escenarios[BATERIA], escenarios[BATERIA_Y_NUCLEO_2]
    bateria_set1, bateria_set2 = escenarios[BATERIA_Y_SET_1], escenarios[BATERIA_Y_SET_2]
    dia_haiku, dia_flash = n(uso.con_haiku), n(uso.con_flash)
    turnos_set = haiku.turnos_por_corrida

    def veredicto(escenario: Escenario) -> str:
        return _alcanza(escenario.con_flash, limite_dia)

    def porcentaje(escenario: Escenario) -> str:
        return _porcentaje(escenario.con_flash, limite_dia)

    margen_bateria_set1 = limite_dia - int(_redondear(bateria_set1.con_flash))
    max_bateria_con_set1 = math.floor(limite_dia / razon) - turnos_set
    con_los_215_enteros = "ya no alcanzarían" if _redondear((plan.turnos_del_dia + turnos_set) * razon) > limite_dia else "alcanzarían"
    plan_cabia = "cabía" if plan.llamadas_del_dia <= limite_dia else "no cabía"
    plan_recalculado_flash = plan.turnos_del_dia * razon
    plan_recalculado_haiku = plan.turnos_del_dia * haiku.solicitudes_por_turno
    dia_con_set_doble_a_razon_del_plan = bateria_set2.turnos * plan.llamadas_por_turno
    dia_si_se_sumara_el_set = plan.llamadas_del_dia + set2.turnos * plan.llamadas_por_turno
    minutos = flash.uso["latencia_minutos"]
    ritmo = Decimal(flash.solicitudes) / Decimal(str(minutos))
    juez = haiku.reporte["juez"]["uso"]
    corridas = haiku.reporte["meta"]["corridas_completas"]
    solicitudes_de_una_corrida_con_haiku = set1.con_haiku + Decimal(juez["llamadas"]) / corridas
    haiku_limita = "limita" if solicitudes_de_una_corrida_con_haiku > LIMITE_DE_HAIKU_POR_MINUTO else "no limita"
    cubre_el_uso_supuesto = "cubre" if _cabe(uso.con_flash, limite_38) else "no cubre ni"
    estimado = plan.llamadas_con_dos_corridas_completas
    esta_cerca = abs(estimado - dia_con_set_doble_a_razon_del_plan) <= Decimal("0.05") * estimado
    no_queda_corta = bateria_set2.con_flash <= estimado and plan_recalculado_flash <= plan.llamadas_del_dia
    las_estimaciones = "quedaron cerca, no cortas" if esta_cerca and no_queda_corta else "NO COINCIDEN CON LO MEDIDO"
    por_categoria = {nombre: Decimal(str(fila["costo_usd"]["mediana"])) for nombre, fila in haiku.reporte["eficiencia_por_categoria"].items()}
    bloques = {nombre: fila["por_corrida"][0]["n"] for nombre, fila in haiku.reporte["por_bloque"].items()}
    salida_flash = Decimal(flash.uso["salida_tokens"]) * Decimal(precio_flash["salida"]) / 1_000_000

    return {
        "Conclusión": [
            f"Con {PREGUNTAS_POR_DIA} preguntas por día hábil y {DIAS_AL_MES} días al mes ({mes} turnos)",
            f"cuesta de ${caliente} a ${frio} al mes",
            f"cuesta ${flash_mes} a precio de lista",
            f"({limite_dia} solicitudes por día y {limite_min} por minuto) {veredicto(uso)} para el uso supuesto, que pide unas {dia_flash} solicitudes al día",
            f"{veredicto(set1).capitalize()} para una corrida del set, que pide unas {n(set1.con_flash)}, "
            f"y {veredicto(set2)} para dos corridas del set (unas {n(set2.con_flash)})",
            f"La batería del evaluador, sin el set, pide unas {n(bateria.con_flash)} solicitudes",
            f"suma unas {n(bateria_nucleo_2.con_flash)} y {veredicto(bateria_nucleo_2)}",
            f"con una corrida completa del set suma unas {n(bateria_set1.con_flash)} ({porcentaje(bateria_set1)}) y {veredicto(bateria_set1)}",
            f"con dos corridas completas suma unas {n(bateria_set2.con_flash)} y {veredicto(bateria_set2)}",
            f"Gemini 3.8 Flash, con {limite_38} al día, {_alcanza(uso.con_flash, limite_38)} ni para el uso supuesto",
            "Google no publica la cuota diaria",
        ],
        PLANTILLA: [
            f"Supuesto: {PREGUNTAS_POR_DIA} preguntas por día hábil y {DIAS_AL_MES} días ({mes} al mes)",
            f"Haiku 5.5 cuesta ${n(haiku.costo_por_turno, 5)} por pregunta: ${caliente} al mes con caché caliente y ${frio} con fría",
            f"Flash-Lite cuesta ${flash_mes} al mes a precio de lista y $0 en el plan gratuito",
            "3.8 Flash no se pudo medir",
            "Haiku no tiene cuota diaria",
            f"3.8 Flash ({limite_38} por día), que {cubre_el_uso_supuesto} el uso supuesto",
            f"Cuota ({FECHA}): Google no publica la diaria",
            f"{limite_dia} por día y {limite_min} por minuto (Flash-Lite), {limite_38} por día (3.8 Flash)",
        ],
        "Supuesto de uso": [
            f"{PREGUNTAS_POR_DIA} preguntas por día hábil y {DIAS_AL_MES} días hábiles al mes, o sea {mes} turnos al mes",
            f"cada 10 preguntas más por día suman ${n(10 * DIAS_AL_MES * haiku.costo_por_turno, 2)} al mes",
            f"unas {n(10 * razon)} solicitudes al día",
            f"(en Haiku, ${n(haiku.mediana_de_costo, 6)})",
        ],
        "Costo mensual por modelo": [
            f"Al mes, {mes} turnos (USD)",
            f"cuesta ${caliente} al mes con la caché caliente y ${frio} con la fría",
            f"Flash-Lite cuesta ${flash_mes} a precio de lista",
            f"Flash-Lite se midió en el núcleo de {flash.turnos} turnos",
            f"{n(haiku.prefijo_de_cache)} tokens) y las otras {haiku.solicitudes - 1} lo leyeron",
            f"a ${precio_haiku['cache_escritura']} por millón de tokens en lugar de leerlo a ${precio_haiku['cache_lectura']}",
            f"La caché fría sube el mes de ${caliente} a ${frio}",
            f"y eso suma ${n(extra, 5)} por turno",
            f"Con {PREGUNTAS_POR_DIA} preguntas repartidas en una jornada",
            f"y ${frio} es la prudente",
            f"dan de ${n(min(meses), 2)} a ${n(max(meses), 2)} al mes",
            f"(`{HAIKU_BASE}`, valores por omisión y sin caché) el mes cuesta ${n(base.costo_por_turno * mes, 2)}",
            f"costó {n(100 * (nucleo.costo_por_turno / haiku.costo_por_turno - 1), 1)} % más por turno que el set completo "
            f"(${n(nucleo.costo_por_turno, 6)} contra ${n(haiku.costo_por_turno, 6)})",
            f"El {n(100 * salida_flash / flash.costo)} % de su costo es salida",
            f"{n(flash.salida_por_turno)} tokens de salida por turno, contra {n(nucleo.salida_por_turno)} de Haiku",
            f"cada millón cuesta ${precio_flash['salida']} contra ${precio_haiku['salida']}",
            f"(${precio_38['entrada']} y ${precio_38['salida']} por millón hasta el 2026-12-31)",
            f"darían ${n(referencia, 2)} al mes, y ${n(2 * referencia, 2)} al mes desde el 2027-01-01",
        ],
        "Solicitudes por día contra la cuota": [
            f"Con {PREGUNTAS_POR_DIA} preguntas por día se piden unas {dia_flash} solicitudes a Flash-Lite y unas {dia_haiku} a Haiku",
            f"Por día con {PREGUNTAS_POR_DIA} preguntas",
            f"Las {limite_dia} diarias de Flash-Lite gratis alcanzan para {math.floor(limite_dia / razon)} preguntas por día "
            f"y las {limite_38} de 3.8 Flash para {math.floor(limite_38 / razon)}",
            f"las {limite_min} de Flash-Lite {_alcanzan(ritmo, limite_min)} para el ritmo medio de una corrida secuencial, pero no para una racha de turnos rápidos",
            f"{flash.solicitudes} solicitudes en {minutos} minutos de latencia de turnos, {n(ritmo)} por minuto",
            "11 de 191 turnos",
        ],
        "Una corrida del set y la batería del evaluador": [
            f"Una corrida del set ({turnos_set} turnos) pide unas {n(set1.con_flash)} solicitudes a Flash-Lite, "
            f"{porcentaje(set1)} de las {limite_dia} diarias, y {veredicto(set1)}",
            f"dos corridas piden unas {n(set2.con_flash)} y {_alcanzan(set2.con_flash, limite_dia)}",
            f"La batería del evaluador, sin el set, son unos {bateria.turnos} turnos y unas {n(bateria.con_flash)} solicitudes ({porcentaje(bateria)})",
            f"Con una corrida completa del set suma unas {n(bateria_set1.con_flash)} ({porcentaje(bateria_set1)}) y {veredicto(bateria_set1)}, "
            f"con {margen_bateria_set1} de margen",
            f"con dos corridas completas suma unas {n(bateria_set2.con_flash)} y {veredicto(bateria_set2)}",
            f"con las dos corridas del núcleo que corre el comando por omisión suma unas {n(bateria_nucleo_2.con_flash)} "
            f"({porcentaje(bateria_nucleo_2)}) y {veredicto(bateria_nucleo_2)}",
            f"Parte de las {limite_dia} al día",
            f"Con Haiku la cuota {haiku_limita}.",
            f"({n(set1.con_haiku)} es la mitad de las {haiku.solicitudes} de las dos corridas de `{HAIKU}`; "
            f"{nucleo.solicitudes} es el núcleo de Haiku)",
            f"de sus {bateria.turnos} turnos por las {n(haiku.solicitudes_por_turno, 2)} solicitudes por turno de Haiku",
            f"multiplican sus {n(razon, 2)} solicitudes por turno por los turnos del escenario, salvo el núcleo, que es la medida de {flash.solicitudes}",
            f"corre el núcleo ({nucleo.reporte['meta']['n_prompts']} prompts, {nucleo.turnos} turnos) dos veces",
            f"`--completo` corre los {haiku.reporte['meta']['n_prompts']} prompts ({turnos_set} turnos) por corrida",
            f"Las {plan.llamadas_del_dia} solicitudes del plan no son la batería sola",
            f"dice que {plan.frase_de_la_bateria}.",
            f"unos {plan.turnos_de_dos_corridas} turnos en total",
            f"Quedan unos {bateria.turnos} turnos de batería propia ({plan.turnos_del_dia} menos {plan.turnos_de_dos_corridas})",
            f"Sumar el set completo a los {plan.turnos_del_dia} turnos contaría dos veces las corridas del set",
            f"ese día de unas {plan.llamadas_del_dia} solicitudes {plan_cabia} en las {limite_dia} con {limite_dia - plan.llamadas_del_dia} de margen "
            f"({_porcentaje(plan.llamadas_del_dia, limite_dia)})",
            f"serían {n(plan_recalculado_flash)} ({_porcentaje(plan_recalculado_flash, limite_dia)})",
            f"Una corrida pide {n(set1.con_haiku)} solicitudes de turnos y {n(Decimal(juez['llamadas']) / corridas)} del juez "
            f"({n(set1.con_haiku + Decimal(juez['llamadas']) / corridas)} en total)",
            f"${n(haiku.costo / corridas, 3)} de turnos y ${n(Decimal(juez['costo_usd']) / corridas, 3)} de juez, "
            f"${n((haiku.costo + Decimal(juez['costo_usd'])) / corridas, 2)} en total",
            f"${n(flash.costo_por_turno * turnos_set, 2)} a precio de lista",
            "pide esperar de 11 a 12 horas",
            "191 turnos y 403 solicitudes",
            f"porque la llave ya traía unas {limite_dia - SOLICITUDES_DE_LA_CORRIDA_QUE_SE_DETUVO} del día ({limite_dia} menos {SOLICITUDES_DE_LA_CORRIDA_QUE_SE_DETUVO})",
            f"las {_reposicion_diaria_en_el_centro_de_mexico():%H:%M} del centro de México en noviembre",
        ],
        "Qué publica cada proveedor y qué se observó": [
            "Google no publica la cuota diaria",
            "medianoche del Pacífico",
            "consola de AI Studio",
            f"${precio_haiku['entrada']} de entrada, ${precio_haiku['salida']} de salida, ${precio_haiku['cache_escritura']} "
            f"de escritura de caché de 5 minutos y ${precio_haiku['cache_lectura']} de lectura",
            "4,000 solicitudes por minuto, 2,000,000 de tokens de entrada y 400,000 de salida, con tope de $500 al mes",
            f"visto por primera vez a las {_primer_429_por_dia_de_3_8_flash():%H:%M}, pidió reintentar en "
            f"{ESPERA_DEL_429_POR_DIA_S // 3600} horas con {ESPERA_DEL_429_POR_DIA_S % 3600 // 60} minutos ({n(ESPERA_DEL_429_POR_DIA_S)} s)",
            f"la reposición cae cerca de las {_reposicion_diaria_en_el_centro_de_mexico():%H:%M} del día siguiente",
        ],
        "Contra las estimaciones del plan": [
            # Un renglón por frase, con la cifra del plan (2026-10-31, tecleada aquí) y la recalculada con lo medido.
            f"Haiku 5.5 al mes con las metas | $0.26 | ${caliente} con la caché caliente y ${frio} con la fría | {mes} turnos por el costo medio medido",
            f"Haiku 5.5 al mes sin palancas | $0.84 | ${n(base.costo_por_turno * mes, 2)} | {mes} turnos por el costo medio medido",
            f"Flash-Lite al mes a precio de lista | $1.72 | ${flash_mes} | {mes} turnos por el costo medio medido",
            f"Solicitudes por día con {PREGUNTAS_POR_DIA} preguntas | 66 | {dia_flash} con Flash-Lite y {dia_haiku} con Haiku "
            f"| {PREGUNTAS_POR_DIA} turnos por las solicitudes por turno medidas",
            f"Día del evaluador con la batería y 2 corridas del set de antes ({plan.turnos_del_dia} turnos) | unas {plan.llamadas_del_dia} "
            f"| {n(plan_recalculado_flash)} con Flash-Lite y {n(plan_recalculado_haiku)} con Haiku "
            f"| {plan.turnos_del_dia} turnos del plan por las solicitudes por turno medidas",
            f"Día del evaluador con la batería y 2 corridas completas ({bateria_set2.turnos} turnos) "
            f"| unas {plan.llamadas_con_dos_corridas_completas} | {n(bateria_set2.con_flash)} con Flash-Lite y {n(bateria_set2.con_haiku)} con Haiku "
            f"| {bateria.turnos} turnos de batería y {set2.turnos} de las corridas, por las solicitudes por turno medidas",
            f"la mediana medida es ${n(haiku.mediana_de_costo, 6)} y la media, que es la que suma en un mes, ${n(haiku.costo_por_turno, 5)}",
            f"Las estimaciones de solicitudes {las_estimaciones}.",
            f"las {plan.llamadas_por_turno} solicitudes por turno del plan, los {bateria_set2.turnos} turnos del día con dos corridas completas "
            f"({bateria.turnos} de batería y {set2.turnos} de las corridas) dan {n(dia_con_set_doble_a_razon_del_plan)}, "
            f"cerca de las {plan.llamadas_con_dos_corridas_completas} de D25",
            f"Sumar esas dos corridas a las {plan.llamadas_del_dia} daría {n(dia_si_se_sumara_el_set)}",
        ],
        "Límites de estas cifras": [
            f"Con {limite_38} solicitudes por día {_alcanza(flash.solicitudes, limite_38)} el núcleo (unas {flash.solicitudes})",
            f"Flash-Lite se midió en el núcleo de {flash.turnos} turnos",
            f"del set completo con Flash-Lite ({n(set1.con_flash)} y {n(set2.con_flash)})",
            "con 191 turnos y 403 solicitudes, y no dejó reporte",
            f"Las {limite_dia - SOLICITUDES_DE_LA_CORRIDA_QUE_SE_DETUVO} solicitudes que traía la llave ese día son una resta "
            f"({limite_dia} menos {SOLICITUDES_DE_LA_CORRIDA_QUE_SE_DETUVO}), no un dato registrado",
            "13 turnos sin respuesta: 8 por el tope de 60 s, 4 por un 503 y 1 por el 429",
            f"su razón de {n(Decimal(403) / 191, 2)} solicitudes por turno coincide con la del núcleo ({n(razon, 3)})",
            f"Los {bateria.turnos} turnos de la batería propia son una resta de dos cifras aproximadas del plan "
            f"({plan.turnos_del_dia} y {plan.turnos_de_dos_corridas})",
            f"las {limite_dia} alcanzan mientras la batería propia no pase de {max_bateria_con_set1} turnos",
            f"con los {plan.turnos_del_dia} turnos enteros {con_los_215_enteros}",
            f"Las {plan.llamadas_del_dia} solicitudes del plan son una estimación, no una medida",
            f"fueron {flash.uso['reintentos']} en el núcleo de Flash-Lite ({n(100 * Decimal(flash.uso['reintentos']) / flash.solicitudes, 1)} %) "
            f"y {haiku.uso['reintentos']} en las corridas de Haiku",
            f"de ${n(min(por_categoria.values()), 6)} por pregunta en cifra directa a ${n(max(por_categoria.values()), 6)} en contradicción",
            f"tiene {bloques['trampa_sencilla']} trampas sencillas, {bloques['normal_sencilla']} normales sencillas "
            f"y {sum(bloques.values()) - bloques['trampa_sencilla'] - bloques['normal_sencilla']} prompts de otros tipos",
        ],
        "Fuentes": [
            f"las {plan.llamadas_por_turno} solicitudes por turno y los {plan.turnos_del_dia} turnos y ~{plan.llamadas_del_dia} llamadas del día del evaluador",
            f"el set de antes de unos {plan.turnos_de_dos_corridas} turnos en dos corridas (D7-A)",
            f"las unas {plan.llamadas_con_dos_corridas_completas} llamadas de D25",
            "todos del 2026-11-03",
        ],
        "Cómo se calculó": [
            f"el costo al mes es {mes} veces ese número",
            f"por día son {PREGUNTAS_POR_DIA} veces esa razón y por corrida del set, {turnos_set} veces (Haiku: {haiku.solicitudes} entre {corridas})",
            f"La batería propia son los {plan.turnos_del_dia} turnos del plan menos los {plan.turnos_de_dos_corridas} de las dos corridas del set de antes",
        ],
    }


@pytest.fixture(scope="module")
def texto() -> str:
    assert TEXTO.is_file(), "falta eval/reportes/proyeccion-mensual.md"
    return TEXTO.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def filas(texto) -> list[list[str]]:
    return _filas(texto)


@pytest.fixture(scope="module")
def medidas() -> dict[str, Medida]:
    return {nombre: Medida(json.loads((REPORTES / nombre).read_text(encoding="utf-8"))) for nombre in CITADOS}


@pytest.fixture(scope="module")
def cuotas() -> dict[tuple[str, str], int]:
    return _cuotas_observadas()


@pytest.fixture(scope="module")
def precios() -> dict:
    return yaml.safe_load(PRECIOS.read_text(encoding="utf-8"))["modelos"]


@pytest.fixture(scope="module")
def plan() -> EstimacionDelPlan:
    if not PLAN.exists():
        pytest.skip(SIN_DOCS)
    return _estimacion_del_plan()


def test_el_supuesto_de_uso_es_el_del_plan_y_da_660_turnos_al_mes(texto):
    assert "30 preguntas por día hábil" in texto
    assert "22 días" in texto
    assert f"{TURNOS_AL_MES} turnos al mes" in texto


@pytest.mark.parametrize(
    ("etiqueta", "reporte"),
    [(COSTO_CALIENTE, HAIKU), (COSTO_BASE, HAIKU_BASE), (COSTO_FLASH_LITE, FLASH_LITE)],
)
def test_el_costo_mensual_es_660_veces_el_costo_medio_por_turno_del_reporte(filas, medidas, etiqueta, reporte):
    medida = medidas[reporte]
    fila = _fila(filas, etiqueta)
    assert reporte in fila[1]
    assert fila[2] == _numero(medida.turnos)
    assert fila[3] == _numero(medida.costo_por_turno, 6)
    assert fila[4] == _numero(medida.costo_por_turno * TURNOS_AL_MES, 2)


def test_con_la_cache_fria_cada_turno_escribe_el_prefijo_en_lugar_de_leerlo(filas, medidas, precios):
    haiku = medidas[HAIKU]
    frio = haiku.costo_por_turno + _extra_de_la_cache_fria(haiku, precios)
    fila = _fila(filas, COSTO_FRIO)
    assert HAIKU in fila[1]
    assert fila[2] == _numero(haiku.turnos)
    assert fila[3] == _numero(frio, 6)
    assert fila[4] == _numero(frio * TURNOS_AL_MES, 2)


def test_en_las_corridas_del_set_solo_la_primera_solicitud_escribio_la_cache(medidas):
    """Lo que sostiene decir que el costo medido es de caché caliente: 555 de 556 solicitudes leyeron el prefijo."""
    haiku = medidas[HAIKU]
    assert haiku.escrituras_de_cache == 1
    assert sum(turno["cache_lectura_tokens"] for turno in haiku.reporte["turnos"]) == haiku.prefijo_de_cache * (haiku.solicitudes - 1)


def test_3_8_flash_no_tiene_costo_propio_porque_no_se_pudo_medir(filas):
    assert _fila(filas, COSTO_3_8_FLASH)[1:] == ["sin reporte", "0", "sin dato", "sin dato"]


@pytest.mark.parametrize(
    ("etiqueta", "reporte", "cuota", "es_supuesto"),
    [
        (POR_DIA_HAIKU, HAIKU, None, False),
        (POR_DIA_FLASH_LITE, FLASH_LITE, (MODELO_FLASH_LITE, "día"), False),
        (POR_DIA_3_8_FLASH, FLASH_LITE, (MODELO_3_8_FLASH, "día"), True),
    ],
)
def test_las_solicitudes_por_dia_son_30_veces_las_de_un_turno_y_se_comparan_con_la_cuota(
    filas, medidas, cuotas, etiqueta, reporte, cuota, es_supuesto
):
    razon = medidas[reporte].solicitudes_por_turno
    por_dia = razon * PREGUNTAS_POR_DIA
    fila = _fila(filas, etiqueta)
    assert fila[1].startswith(_numero(razon, 2))
    assert ("supuesto" in fila[1]) is es_supuesto
    assert fila[2] == _numero(por_dia)
    if cuota is None:
        assert fila[3].startswith("sin cuota diaria")
        assert fila[6] == "Sí"
        return
    limite = cuotas[cuota]
    assert fila[3] == _numero(limite)
    assert fila[4] == _porcentaje(por_dia, limite)
    assert fila[5] == _numero(math.floor(limite / razon))
    assert fila[6] == ("Sí" if por_dia <= limite else "No")


@pytest.mark.parametrize("etiqueta", ESCENARIOS)
def test_cada_escenario_suma_sus_solicitudes_y_dice_si_caben_en_las_500_del_dia(filas, medidas, cuotas, plan, etiqueta):
    turnos, con_haiku, con_flash = _escenarios(medidas, plan)[etiqueta]
    limite = cuotas[(MODELO_FLASH_LITE, "día")]
    solicitudes = int(_redondear(con_flash))
    fila = _fila(filas, etiqueta)
    assert fila[1] == _numero(turnos)
    assert fila[2] == _numero(con_haiku)
    assert fila[3] == _numero(con_flash)
    assert fila[4] == _porcentaje(con_flash, limite)
    assert fila[5] == (f"Sí, sobran {limite - solicitudes}" if solicitudes <= limite else f"No, faltan {solicitudes - limite}")


def test_el_plan_pone_las_dos_corridas_del_set_de_antes_dentro_de_sus_215_turnos_y_470_llamadas(plan):
    """La lectura de la que depende toda la tabla de escenarios con batería: los 215 turnos no son solo la batería.

    Con 62 pruebas, la batería propia de unos 75 turnos cuadra con sus turnos extra, P41 tres veces, 5 variantes y las copias A a D;
    215 turnos de batería no cuadrarían.
    """
    assert plan.turnos_de_dos_corridas < plan.turnos_del_dia
    assert plan.turnos_de_la_bateria == plan.turnos_del_dia - plan.turnos_de_dos_corridas
    assert plan.pruebas_de_la_bateria <= plan.turnos_de_la_bateria < 2 * plan.pruebas_de_la_bateria


def test_ningun_escenario_suma_el_set_de_ahora_a_los_215_turnos_del_plan_que_ya_traen_dos_corridas(filas, medidas, plan):
    """Sumarlas contaría dos veces las corridas del set: 215 + 129 = 344 turnos y 215 + 258 = 473, en vez de 204 y 333."""
    turnos_del_set = medidas[HAIKU].turnos_por_corrida
    doblados = {_numero(plan.turnos_del_dia + corridas * turnos_del_set) for corridas in (0, 1, 2)}
    turnos_de_la_tabla = {_fila(filas, escenario)[1] for escenario in ESCENARIOS}
    assert not doblados & turnos_de_la_tabla
    assert _fila(filas, BATERIA)[1] == _numero(plan.turnos_del_dia - plan.turnos_de_dos_corridas)
    assert _fila(filas, BATERIA_Y_SET_1)[1] == _numero(plan.turnos_del_dia - plan.turnos_de_dos_corridas + turnos_del_set)
    assert _fila(filas, BATERIA_Y_SET_2)[1] == _numero(plan.turnos_del_dia - plan.turnos_de_dos_corridas + 2 * turnos_del_set)


def test_la_bitacora_trae_la_entrada_que_el_texto_cita_para_los_turnos_perdidos_por_el_tope_por_minuto():
    entrada = re.search(r"### EA-237\..*?(?=\n### |\Z)", BITACORA.read_text(encoding="utf-8"), flags=re.DOTALL)
    assert entrada is not None
    assert "11 de 191 turnos" in entrada.group(0)
    assert "15 solicitudes por minuto" in entrada.group(0)


def test_la_razon_de_la_corrida_de_flash_lite_que_se_detuvo_coincide_con_la_del_nucleo(medidas):
    """191 turnos y 403 solicitudes: dato del orquestador sin reporte. Si la razón no coincidiera, el texto no podría decirlo."""
    assert abs(Decimal(403) / 191 - medidas[FLASH_LITE].solicitudes_por_turno) < Decimal("0.01")


def test_las_cuotas_observadas_son_las_que_dejo_el_429_en_cuota_gemini_md(filas, cuotas):
    assert set(cuotas) == {(MODELO_FLASH_LITE, "minuto"), (MODELO_FLASH_LITE, "día"), (MODELO_3_8_FLASH, "día")}
    for (modelo, periodo), valor in cuotas.items():
        [fila] = [fila for fila in filas if fila[0] == modelo and f"por {periodo}" in fila[1]]
        assert fila[2] == str(valor)
    registro = CUOTAS.read_text(encoding="utf-8")
    assert "43005 s" in registro
    assert "11 a 12 horas" in registro


def test_la_espera_del_429_por_dia_cae_en_la_reposicion_de_la_medianoche_del_pacifico():
    """El texto dice «cerca de las 02:00» y «medianoche del Pacífico, las 02:00 del centro de México en noviembre».

    Supone que la lectura de los 43,005 s fue la del primer 429 por día (cuota-gemini.md anota esa hora, no la de la lectura).
    """
    pedido = _primer_429_por_dia_de_3_8_flash() + timedelta(seconds=ESPERA_DEL_429_POR_DIA_S)
    reposicion = _reposicion_diaria_en_el_centro_de_mexico()
    assert f"{reposicion:%H:%M}" == "02:00"
    assert abs(pedido - reposicion) < timedelta(minutes=5)


@pytest.mark.parametrize("seccion", SECCIONES)
def test_cada_seccion_trae_las_cifras_que_calculan_los_reportes(texto, medidas, cuotas, precios, plan, seccion):
    cuerpo = _seccion(texto, seccion)
    faltan = [frase for frase in _cifras_por_seccion(medidas, cuotas, precios, plan)[seccion] if frase not in cuerpo]
    assert not faltan, f"en «{seccion}» faltan: {faltan}"


@pytest.mark.parametrize("fuente", FUENTES)
def test_cada_fuente_lleva_su_direccion_y_la_fecha_de_consulta(texto, fuente):
    [linea] = [linea for linea in texto.splitlines() if fuente in linea]
    assert FECHA in linea


def test_el_texto_nombra_los_reportes_que_usa_y_la_fuente_de_las_cuotas(texto, medidas):
    nombres = set(re.findall(r"`(2026-\d\d-\d\d-[\w.\-]+\.json)`", texto))
    assert nombres == set(medidas)
    assert "eval/reportes/cuota-gemini.md" in texto
    assert "docs/planes/2026-10-31-plan-implementacion.html" in texto


def test_el_texto_para_la_plantilla_cabe_en_las_5_lineas_de_la_seccion_4_de_la_plantilla(texto):
    """Una línea es una línea del archivo que cabe sin partirse en 150 caracteres: la plantilla pide de 3 a 5 por sección."""
    lineas = _lineas_de_la_plantilla(texto)
    assert 3 <= len(lineas) <= LINEAS_DE_LA_PLANTILLA
    largas = {len(linea): linea[:50] for linea in lineas if len(linea) > CARACTERES_POR_LINEA_DE_LA_PLANTILLA}
    assert not largas, f"líneas de más de {CARACTERES_POR_LINEA_DE_LA_PLANTILLA} caracteres: {largas}"


def test_la_plantilla_pone_cada_escenario_en_la_linea_de_lo_que_alcanza_o_en_la_de_lo_que_no(texto, medidas, cuotas, plan):
    """Si el veredicto de un escenario cambia, o alguien cambia la línea donde está, esta prueba falla."""
    lineas = _lineas_de_la_plantilla(texto)
    con_alcanza = [linea for linea in lineas if linea.startswith("Alcanza")]
    con_no_alcanza = [linea for linea in lineas if linea.startswith("No alcanza")]
    assert (len(con_alcanza), len(con_no_alcanza)) == (1, 1), "la plantilla trae una línea que abre con «Alcanza» y una con «No alcanza»"
    [linea_alcanza], [linea_no_alcanza] = con_alcanza, con_no_alcanza
    limite = cuotas[(MODELO_FLASH_LITE, "día")]
    escenarios = _escenarios(medidas, plan)
    for nombre_en_la_plantilla, escenario in ESCENARIOS_DE_LA_PLANTILLA:
        solicitudes = escenarios[escenario].con_flash
        frase = f"{nombre_en_la_plantilla} {_numero(solicitudes)}"
        donde, en_la_otra = (linea_alcanza, linea_no_alcanza) if _cabe(solicitudes, limite) else (linea_no_alcanza, linea_alcanza)
        assert frase in donde, f"«{frase}» tiene que ir en «{donde[:30]}...»"
        assert frase not in en_la_otra
    limite_3_8 = cuotas[(MODELO_3_8_FLASH, "día")]
    por_dia = escenarios[USO_SUPUESTO].con_flash
    donde, en_la_otra = (linea_alcanza, linea_no_alcanza) if _cabe(por_dia, limite_3_8) else (linea_no_alcanza, linea_alcanza)
    assert f"3.8 Flash ({limite_3_8} por día)" in donde
    assert "3.8 Flash" not in en_la_otra


def test_ninguna_cifra_usa_la_coma_como_separador_decimal(texto):
    assert not re.findall(r"\d,\d{1,2}(?!\d)", texto)
