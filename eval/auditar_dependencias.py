"""Audita requirements.txt con pip-audit y guarda el reporte (T0.3, K01).

Escribe eval/reportes/auditoria-dependencias.txt con dos corridas de pip-audit, una contra el
servicio de PyPI y otra contra el de OSV, y con lo que hace valer un 0: el sha256 del lock que se
auditó, cuántos paquetes se auditaron y cuántos se omitieron.
tests/test_auditoria_dependencias.py vuelve a comprobar todo eso sin red.

Se corre desde la raíz del repositorio, con red y con requirements-dev.txt instalado:

    .venv/Scripts/python -m eval.auditar_dependencias

pip-audit no informa la severidad. Si encuentra algo, el reporte lo anota como "sin clasificar" y
el script termina con 1. Se consulta la severidad del id en OSV o en GitHub Advisory y se vuelve a
correr con:

    .venv/Scripts/python -m eval.auditar_dependencias --severidad GHSA-xxxx-xxxx-xxxx=media

El nivel es baja, media, alta o crítica, y el id puede ser el de pip-audit o cualquiera de sus alias.
Con una alta, el reporte dice que no cumple y el script termina con 1. Si pip-audit falla, no se
escribe nada y termina con 2.

Audita una copia de requirements.txt sin marcadores de entorno. pip-audit omite sin avisar las
líneas cuyo marcador no aplica a la máquina que audita, y el lock vale para Windows, macOS y Linux.
"""

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
LOCK = RAIZ / "requirements.txt"
REPORTE = RAIZ / "eval" / "reportes" / "auditoria-dependencias.txt"

# Nombre en el reporte -> nombre del servicio en la opción -s de pip-audit.
SERVICIOS = {"PyPI": "pypi", "OSV": "osv"}

NIVELES = {"baja": "baja", "media": "media", "alta": "alta", "crítica": "crítica", "critica": "crítica"}
SEVERIDADES_ANOTABLES = ("baja", "media")
SEVERIDADES_ALTAS = ("alta", "crítica")
SIN_CLASIFICAR = "sin clasificar"

ENTRADA_DE_SEVERIDAD = re.compile(r"^(?P<id>[^=\s]+)=(?P<nivel>\S+)$")
PAQUETE = re.compile(r"^(?P<nombre>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)", re.M)
CON_MARCADOR = re.compile(r"^(?P<paquete>[A-Za-z0-9][A-Za-z0-9._-]*==[^\s;\\]+)\s*;[^\\]*(?P<continuacion>\\?)$")
RESUMEN_DE_PIP_AUDIT = re.compile(
    r"^(?:No known vulnerabilities found|Found \d+ known vulnerabilit(?:y|ies) in \d+ packages?)$", re.M
)

COMO_SE_CORRIO = (
    "Cómo se corrió",
    "  Para repetirla, desde la raíz del repositorio: .venv/Scripts/python -m eval.auditar_dependencias",
    "  Por cada servicio (-s pypi y -s osv) equivale a:",
    "    python -m pip_audit -r <requirements.txt sin marcadores de entorno> --disable-pip --strict"
    " --progress-spinner off --desc off --cache-dir <carpeta nueva> -s <servicio> -f json",
    "  Sin marcadores porque pip-audit omite sin avisar las líneas cuyo marcador de entorno no aplica a la",
    "  máquina que audita, y el lock vale para Windows, macOS y Linux.",
    "  --disable-pip lee las versiones fijadas del lock sin instalar nada y --cache-dir nuevo evita respuestas guardadas.",
    "  El sha256 se calcula con saltos de línea LF para que sea el mismo en Windows, macOS y Linux.",
    "  Si cambia requirements.txt hay que volver a correrla: tests/test_auditoria_dependencias.py compara el sha256.",
    "  Con PROBAR_CON_RED=1 esa prueba repite la auditoría y comprueba que la misma corrida marca pyasn1 0.6.4, que tiene",
    "  cuatro avisos altos (CVE-2026-119194 a CVE-2026-119197) que corrige 0.6.5; así el 0 no sale de una auditoría que no ve nada.",
    "  Las excepciones de parche de seguridad están anotadas en requirements.in.",
)


class ErrorDeAuditoria(Exception):
    """pip-audit no pudo auditar el lock completo: no se escribe un reporte que parezca limpio."""


@dataclass(frozen=True)
class Vulnerabilidad:
    id: str
    paquete: str  # "nombre==versión"
    alias: tuple[str, ...] = ()
    corrige_en: tuple[str, ...] = ()


@dataclass
class Auditoria:
    servicio: str
    auditados: int
    omitidos: int
    codigo_de_salida: int
    salida: str  # el resumen que pip-audit escribe en stderr
    vulnerabilidades: list[Vulnerabilidad] = field(default_factory=list)


@dataclass
class Hallazgo:
    vulnerabilidad: Vulnerabilidad
    severidad: str
    servicios: list[str]


# --- El lock -------------------------------------------------------------------------------


def normalizar(nombre):
    return re.sub(r"[-_.]+", "-", nombre).lower()


def paquetes_fijados(texto):
    return [(normalizar(coincidencia["nombre"]), coincidencia["version"]) for coincidencia in PAQUETE.finditer(texto)]


def huella_del_lock(texto):
    """sha256 con saltos de línea LF: en Windows, git puede dejar el archivo con CRLF."""
    return hashlib.sha256(texto.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def quitar_marcadores(texto):
    """Copia del lock sin los marcadores de entorno ("; sys_platform == 'win32'") y con todo lo demás igual."""
    lineas = []
    for linea in texto.splitlines():
        con_marcador = CON_MARCADOR.match(linea)
        lineas.append(f"{con_marcador['paquete']} {con_marcador['continuacion']}".rstrip() if con_marcador else linea)
    return "\n".join(lineas) + "\n"


# --- pip-audit -----------------------------------------------------------------------------


def auditar_lock(texto_del_lock, carpeta):
    """Audita con cada servicio todos los paquetes fijados. En `carpeta` quedan la copia y las cachés."""
    carpeta = Path(carpeta)
    copia = carpeta / "requirements-sin-marcadores.txt"
    copia.write_text(quitar_marcadores(texto_del_lock), encoding="utf-8", newline="\n")
    esperados = set(paquetes_fijados(texto_del_lock))
    return [auditar(servicio, copia, carpeta / f"cache-{servicio}", esperados) for servicio in SERVICIOS]


def auditar(servicio, requisitos, cache, esperados):
    comando = [
        sys.executable, "-m", "pip_audit",
        "-r", str(requisitos), "--disable-pip", "--strict", "--progress-spinner", "off", "--desc", "off",
        "--cache-dir", str(cache), "-s", SERVICIOS[servicio], "-f", "json",
    ]
    try:
        corrida = subprocess.run(comando, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    except subprocess.TimeoutExpired:
        raise ErrorDeAuditoria(f"pip-audit con {servicio} tardó más de 900 segundos") from None
    return leer_corrida(servicio, corrida, esperados)


def leer_corrida(servicio, corrida, esperados):
    """pip-audit sale con 1 tanto si encuentra vulnerabilidades como si falla: se distingue por su JSON."""
    try:
        dependencias = json.loads(corrida.stdout)["dependencies"]
    except (ValueError, KeyError, TypeError):
        raise ErrorDeAuditoria(f"pip-audit con {servicio} falló (código {corrida.returncode}): {corrida.stderr.strip()[-500:]}") from None
    resumen = RESUMEN_DE_PIP_AUDIT.search(corrida.stderr)
    if resumen is None:
        raise ErrorDeAuditoria(f"pip-audit con {servicio} no dio su resumen: {corrida.stderr.strip()[-500:]}")

    auditados, omitidos, vulnerabilidades = set(), 0, []
    for dependencia in dependencias:
        if "skip_reason" in dependencia:
            omitidos += 1
            continue
        auditados.add((normalizar(dependencia["name"]), dependencia["version"]))
        paquete = f"{dependencia['name']}=={dependencia['version']}"
        for aviso in dependencia["vulns"]:
            alias, corrige_en = tuple(aviso.get("aliases", ())), tuple(aviso.get("fix_versions", ()))
            vulnerabilidades.append(Vulnerabilidad(aviso["id"], paquete, alias, corrige_en))

    comprobar_que_audito_todo(servicio, auditados, esperados)
    esperado = 1 if vulnerabilidades else 0
    if corrida.returncode != esperado:
        raise ErrorDeAuditoria(f"pip-audit con {servicio} salió con {corrida.returncode} y encontró {len(vulnerabilidades)} vulnerabilidades")
    return Auditoria(servicio, len(auditados), omitidos, corrida.returncode, resumen[0], vulnerabilidades)


def comprobar_que_audito_todo(servicio, auditados, esperados):
    if auditados == esperados:
        return
    faltan, sobran = sorted(esperados - auditados), sorted(auditados - esperados)
    raise ErrorDeAuditoria(f"pip-audit con {servicio} no auditó el lock tal cual: faltan {faltan[:5]}, sobran {sobran[:5]}")


# --- El reporte ----------------------------------------------------------------------------


def severidad_de(vulnerabilidad, severidades):
    for identificador in (vulnerabilidad.id, *vulnerabilidad.alias):
        if identificador in severidades:
            return severidades[identificador]
    return SIN_CLASIFICAR


def unir_vulnerabilidades(auditorias, severidades):
    """Un hallazgo por (paquete, id), con los servicios que lo reportaron."""
    grupos = {}
    for auditoria in auditorias:
        for reportada in auditoria.vulnerabilidades:
            grupos.setdefault((reportada.paquete, reportada.id), []).append((auditoria.servicio, reportada))
    hallazgos = []
    for (paquete, identificador), reportes in sorted(grupos.items()):
        alias = tuple(sorted({a for _, reportada in reportes for a in reportada.alias}))
        corrige_en = tuple(sorted({v for _, reportada in reportes for v in reportada.corrige_en}))
        vulnerabilidad = Vulnerabilidad(identificador, paquete, alias, corrige_en)
        servicios = [servicio for servicio, _ in reportes]
        hallazgos.append(Hallazgo(vulnerabilidad, severidad_de(vulnerabilidad, severidades), servicios))
    return hallazgos


def cumple(hallazgos):
    return all(hallazgo.severidad in SEVERIDADES_ANOTABLES for hallazgo in hallazgos)


def veredicto(hallazgos):
    altas = [hallazgo for hallazgo in hallazgos if hallazgo.severidad in SEVERIDADES_ALTAS]
    sin_clasificar = [hallazgo for hallazgo in hallazgos if hallazgo.severidad == SIN_CLASIFICAR]
    if altas:
        return f"NO CUMPLE. {len(altas)} vulnerabilidades altas o críticas abiertas."
    if sin_clasificar:
        return f"NO CUMPLE. {len(sin_clasificar)} vulnerabilidades sin clasificar: falta su severidad."
    return f"CUMPLE. 0 vulnerabilidades altas o críticas abiertas y {len(hallazgos)} de otra severidad, anotadas con su id."


def lineas_por_servicio(auditorias):
    lineas = []
    for auditoria in auditorias:
        lineas.append(
            f"pip-audit con {auditoria.servicio}: {auditoria.auditados} paquetes auditados, {auditoria.omitidos} omitidos, "
            f"{len(auditoria.vulnerabilidades)} vulnerabilidades, código de salida {auditoria.codigo_de_salida}"
        )
        lineas.append(f"  Salida de pip-audit: {auditoria.salida}")
    return lineas


def lineas_de_hallazgos(hallazgos):
    if not hallazgos:
        return ["  ninguna"]
    return [
        f"  {hallazgo.vulnerabilidad.id} | {hallazgo.vulnerabilidad.paquete} | severidad: {hallazgo.severidad}"
        f" | corrige en: {', '.join(hallazgo.vulnerabilidad.corrige_en) or 'sin corrección'}"
        f" | alias: {', '.join(hallazgo.vulnerabilidad.alias) or 'ninguno'}"
        f" | servicios: {', '.join(hallazgo.servicios)}"
        for hallazgo in hallazgos
    ]


def componer_reporte(texto_del_lock, version_de_pip_audit, fecha, auditorias, severidades):
    hallazgos = unir_vulnerabilidades(auditorias, severidades)
    altas = [hallazgo for hallazgo in hallazgos if hallazgo.severidad in SEVERIDADES_ALTAS]
    lineas = [
        "Auditoría de dependencias con pip-audit",
        "",
        f"Veredicto: {veredicto(hallazgos)}",
        "",
        "Corrida",
        f"Fecha (UTC): {fecha.astimezone(timezone.utc):%Y-%m-%dT%H:%MZ}",
        f"Herramienta: pip-audit {version_de_pip_audit}",
        "Archivo auditado: requirements.txt",
        f"sha256 del archivo: {huella_del_lock(texto_del_lock)}",
        f"Paquetes fijados en el archivo: {len(paquetes_fijados(texto_del_lock))}",
        "",
        "Resultado por servicio de vulnerabilidades",
        *lineas_por_servicio(auditorias),
        "",
        "Criterio de aceptación: 0 vulnerabilidades altas abiertas; las demás, anotadas con su id",
        f"Vulnerabilidades abiertas: {len(hallazgos)}",
        f"Altas o críticas abiertas: {len(altas)}",
        "Vulnerabilidades anotadas (id | paquete | severidad | corrige en | alias | servicios):",
        *lineas_de_hallazgos(hallazgos),
        "",
        *COMO_SE_CORRIO,
    ]
    return "\n".join(lineas) + "\n"


# --- Línea de comandos ---------------------------------------------------------------------


def parsear_severidades(valores):
    severidades = {}
    for valor in valores:
        entrada = ENTRADA_DE_SEVERIDAD.match(valor.strip())
        nivel = NIVELES.get(entrada["nivel"].lower()) if entrada else None
        if nivel is None:
            raise SystemExit(f"--severidad espera ID=NIVEL, con NIVEL baja, media, alta o crítica; recibí {valor!r}")
        severidades[entrada["id"]] = nivel
    return severidades


def version_de_pip_audit():
    return importlib.metadata.version("pip-audit")


def main(argumentos=None):
    analizador = argparse.ArgumentParser(description="Audita requirements.txt con pip-audit y escribe el reporte de T0.3.")
    analizador.add_argument(
        "--severidad",
        action="append",
        default=[],
        metavar="ID=NIVEL",
        help="severidad de una vulnerabilidad que pip-audit encontró: baja, media, alta o crítica (se puede repetir)",
    )
    severidades = parsear_severidades(analizador.parse_args(argumentos).severidad)

    texto_del_lock = LOCK.read_text(encoding="utf-8")
    try:
        with tempfile.TemporaryDirectory(prefix="auditoria-") as carpeta:
            auditorias = auditar_lock(texto_del_lock, carpeta)
    except ErrorDeAuditoria as error:
        print(f"No se escribió el reporte. {error}", file=sys.stderr)
        return 2

    reporte = componer_reporte(texto_del_lock, version_de_pip_audit(), datetime.now(timezone.utc), auditorias, severidades)
    REPORTE.parent.mkdir(parents=True, exist_ok=True)
    REPORTE.write_text(reporte, encoding="utf-8", newline="\n")

    hallazgos = unir_vulnerabilidades(auditorias, severidades)
    print(f"{REPORTE}\n{veredicto(hallazgos)}")
    return 0 if cumple(hallazgos) else 1


if __name__ == "__main__":
    sys.exit(main())
