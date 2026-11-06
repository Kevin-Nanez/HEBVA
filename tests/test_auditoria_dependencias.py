"""Auditoría de dependencias con pip-audit (T0.3, K01).

Criterio de aceptación de la ficha: 0 vulnerabilidades altas abiertas; las demás quedan anotadas
con su id. La salida se guarda en eval/reportes/auditoria-dependencias.txt y la genera
eval/auditar_dependencias.py.

Sin red, las pruebas leen ese reporte y comprueban lo que hace valer su 0: que audita el
requirements.txt de hoy (mismo sha256 y mismo número de paquetes) con la versión de pip-audit del
lock de desarrollo, que los dos servicios (PyPI y OSV) auditaron todos los paquetes sin omitir
ninguno, que las cifras dicen lo mismo que la salida literal de pip-audit y que cada
vulnerabilidad abierta tiene su id y una severidad baja o media. Con reportes armados a mano
prueban además que esas comprobaciones fallan cuando deben: un lock distinto, un paquete omitido,
una vulnerabilidad alta, sin clasificar o sin anotar.

Con PROBAR_CON_RED=1 y pip-audit instalado (requirements-dev.txt) repiten la auditoría contra PyPI
y OSV. No debe haber avisos que el reporte no anote, y pyasn1 0.6.4, que tiene cuatro avisos altos,
tiene que aparecer con los cuatro: ese control positivo evita que un 0 sea una auditoría que no ve
nada.

    Git Bash:   PROBAR_CON_RED=1 .venv/Scripts/python -m pytest -q -p no:cacheprovider tests/test_auditoria_dependencias.py
    PowerShell: $env:PROBAR_CON_RED = "1"; .venv\\Scripts\\python -m pytest -q -p no:cacheprovider tests\\test_auditoria_dependencias.py
"""

import hashlib
import importlib
import importlib.util
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
REPORTE = RAIZ / "eval" / "reportes" / "auditoria-dependencias.txt"

# La cifra del criterio de aceptación: 0 vulnerabilidades altas (o críticas) abiertas.
ALTAS_ABIERTAS_PERMITIDAS = 0
SEVERIDADES_ALTAS = {"alta", "crítica"}
SEVERIDADES_ANOTABLES = {"baja", "media"}
SERVICIOS = ["PyPI", "OSV"]

# Los cuatro avisos altos de pyasn1 0.6.4 que corrige 0.6.5 (excepción de parche de D14-B).
AVISOS_DE_PYASN1_0_6_4 = {"CVE-2026-119194", "CVE-2026-119195", "CVE-2026-119196", "CVE-2026-119197"}

FECHA = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)

HAY_RED = os.environ.get("PROBAR_CON_RED") == "1"
CON_RED = pytest.mark.skipif(not HAY_RED, reason="necesita red: se activa con PROBAR_CON_RED=1")
HAY_PIP_AUDIT = importlib.util.find_spec("pip_audit") is not None
CON_PIP_AUDIT = pytest.mark.skipif(not HAY_PIP_AUDIT, reason="falta pip-audit: está en requirements-dev.txt")

PAQUETE = re.compile(r"^(?P<nombre>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)", re.M)
CAMPO = re.compile(r"^(?P<clave>[^:|]+): (?P<valor>.+)$")
SERVICIO = re.compile(
    r"^pip-audit con (?P<servicio>\w+): (?P<auditados>\d+) paquetes auditados, (?P<omitidos>\d+) omitidos, "
    r"(?P<vulnerabilidades>\d+) vulnerabilidades, código de salida (?P<salida>\d+)$"
)
SALIDA = re.compile(r"^\s+Salida de pip-audit: (?P<texto>.+)$")
HALLAZGO = re.compile(r"^\s+(?P<id>\S+) \| (?P<paquete>\S+) \| severidad: (?P<severidad>[^|]+?)(?: \| .*)?$")
RESUMEN_SIN_AVISOS = "No known vulnerabilities found"
RESUMEN_CON_AVISOS = re.compile(r"^Found (?P<cantidad>\d+) known vulnerabilit(?:y|ies) in \d+ packages?$")


# --- Lectura del lock y del reporte, sin usar el código que se prueba ------------------------


def leer(ruta):
    return Path(ruta).read_text(encoding="utf-8")


def leer_de_la_raiz(nombre):
    return leer(RAIZ / nombre)


def paquetes_del_lock(texto):
    return [(coincidencia["nombre"], coincidencia["version"]) for coincidencia in PAQUETE.finditer(texto)]


def huella(texto):
    """sha256 con saltos de línea LF: en Windows, git puede dejar el archivo con CRLF."""
    return hashlib.sha256(texto.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def version_fijada_de_pip_audit(texto_del_lock_de_desarrollo):
    fijado = re.search(r"^pip-audit==(\S+)", texto_del_lock_de_desarrollo, re.M)
    assert fijado, "requirements-dev.txt no fija pip-audit"
    return fijado[1]


@dataclass
class Servicio:
    auditados: int
    omitidos: int
    vulnerabilidades: int
    salida: int
    resumen: str = ""


@dataclass
class Hallazgo:
    id: str
    paquete: str
    severidad: str


@dataclass
class Reporte:
    campos: dict = field(default_factory=dict)
    servicios: dict = field(default_factory=dict)
    hallazgos: list = field(default_factory=list)


def leer_reporte(texto):
    reporte = Reporte()
    ultimo_servicio = None
    for linea in texto.splitlines():
        if servicio := SERVICIO.match(linea):
            ultimo_servicio = servicio["servicio"]
            cifras = (int(servicio[nombre]) for nombre in ("auditados", "omitidos", "vulnerabilidades", "salida"))
            reporte.servicios[ultimo_servicio] = Servicio(*cifras)
        elif salida := SALIDA.match(linea):
            if ultimo_servicio:
                reporte.servicios[ultimo_servicio].resumen = salida["texto"].strip()
        elif hallazgo := HALLAZGO.match(linea):
            reporte.hallazgos.append(Hallazgo(hallazgo["id"], hallazgo["paquete"], hallazgo["severidad"].lower()))
        elif campo := CAMPO.match(linea.strip()):
            reporte.campos[campo["clave"].strip()] = campo["valor"].strip()
    return reporte


# --- Reglas: cada una devuelve la lista de problemas que encuentra ---------------------------


def problemas_del_lock(reporte, texto_del_lock, texto_del_lock_de_desarrollo):
    """El reporte tiene que auditar el requirements.txt de hoy, con la pip-audit del lock de desarrollo."""
    campos = reporte.campos
    esperados = {
        "Archivo auditado": "requirements.txt",
        "sha256 del archivo": huella(texto_del_lock),
        "Paquetes fijados en el archivo": str(len(paquetes_del_lock(texto_del_lock))),
        "Herramienta": f"pip-audit {version_fijada_de_pip_audit(texto_del_lock_de_desarrollo)}",
    }
    return [
        f"{clave}: el reporte dice {campos.get(clave)!r} y el lock de hoy da {valor!r}"
        for clave, valor in esperados.items()
        if campos.get(clave) != valor
    ]


def problemas_de_cobertura(reporte, texto_del_lock):
    """Los dos servicios auditaron todos los paquetes fijados y no omitieron ninguno."""
    fijados = len(paquetes_del_lock(texto_del_lock))
    problemas = []
    for nombre in SERVICIOS:
        servicio = reporte.servicios.get(nombre)
        if servicio is None:
            problemas.append(f"El reporte no trae la corrida de pip-audit con {nombre}")
            continue
        if servicio.auditados != fijados:
            problemas.append(f"{nombre} auditó {servicio.auditados} paquetes y el lock fija {fijados}")
        if servicio.omitidos:
            problemas.append(f"{nombre} omitió {servicio.omitidos} paquetes")
    return problemas


def problemas_de_la_salida(reporte):
    """La salida literal de pip-audit y su código de salida dicen lo mismo que las cifras."""
    problemas = []
    for nombre in SERVICIOS:
        servicio = reporte.servicios.get(nombre)
        if servicio is None:
            problemas.append(f"No hay salida de pip-audit con {nombre} para comparar con las cifras")
            continue
        codigo_esperado = 1 if servicio.vulnerabilidades else 0
        if servicio.salida != codigo_esperado:
            problemas.append(f"{nombre}: código de salida {servicio.salida} con {servicio.vulnerabilidades} vulnerabilidades")
        if not resumen_coincide(servicio):
            problemas.append(f"{nombre}: la salida {servicio.resumen!r} no coincide con {servicio.vulnerabilidades} vulnerabilidades")
    return problemas


def resumen_coincide(servicio):
    if servicio.vulnerabilidades == 0:
        return servicio.resumen == RESUMEN_SIN_AVISOS
    con_avisos = RESUMEN_CON_AVISOS.match(servicio.resumen)
    if con_avisos is None:
        return False
    return int(con_avisos["cantidad"]) == servicio.vulnerabilidades


def problemas_de_altas(reporte):
    """Criterio de aceptación: 0 vulnerabilidades altas o críticas abiertas."""
    altas = [hallazgo for hallazgo in reporte.hallazgos if hallazgo.severidad in SEVERIDADES_ALTAS]
    problemas = []
    if len(altas) > ALTAS_ABIERTAS_PERMITIDAS:
        ids = [hallazgo.id for hallazgo in altas]
        problemas.append(f"Hay {len(altas)} vulnerabilidades altas o críticas abiertas y el criterio pide {ALTAS_ABIERTAS_PERMITIDAS}: {ids}")
    if reporte.campos.get("Altas o críticas abiertas") != str(len(altas)):
        problemas.append(f"'Altas o críticas abiertas' dice {reporte.campos.get('Altas o críticas abiertas')!r} y hay {len(altas)}")
    return problemas


def problemas_de_anotacion(reporte):
    """Criterio de aceptación: las demás vulnerabilidades quedan anotadas con su id."""
    hallazgos = reporte.hallazgos
    problemas = []
    permitidas = SEVERIDADES_ALTAS | SEVERIDADES_ANOTABLES
    sin_clasificar = [hallazgo.id for hallazgo in hallazgos if hallazgo.severidad not in permitidas]
    if sin_clasificar:
        problemas.append(f"Vulnerabilidades sin clasificar: {sin_clasificar}")
    ids = [hallazgo.id for hallazgo in hallazgos]
    if len(ids) != len(set(ids)):
        problemas.append(f"Ids repetidos en las vulnerabilidades anotadas: {ids}")
    if reporte.campos.get("Vulnerabilidades abiertas") != str(len(hallazgos)):
        problemas.append(f"'Vulnerabilidades abiertas' dice {reporte.campos.get('Vulnerabilidades abiertas')!r} y hay {len(hallazgos)} anotadas")
    return problemas + problemas_de_vulnerabilidades_sin_anotar(reporte)


def problemas_de_vulnerabilidades_sin_anotar(reporte):
    """Lo que reportó cada servicio tiene que estar anotado; entre dos servicios puede repetirse."""
    reportadas = [servicio.vulnerabilidades for servicio in reporte.servicios.values()]
    if not reportadas:
        return []
    minimo, maximo = max(reportadas), sum(reportadas)
    if minimo <= len(reporte.hallazgos) <= maximo:
        return []
    return [f"Los servicios reportaron {reportadas} vulnerabilidades y el reporte anota {len(reporte.hallazgos)}"]


def todos_los_problemas(texto_del_reporte, texto_del_lock=None, texto_del_lock_de_desarrollo=None):
    texto_del_lock = texto_del_lock if texto_del_lock is not None else leer_de_la_raiz("requirements.txt")
    texto_del_lock_de_desarrollo = texto_del_lock_de_desarrollo or leer_de_la_raiz("requirements-dev.txt")
    reporte = leer_reporte(texto_del_reporte)
    return (
        problemas_del_lock(reporte, texto_del_lock, texto_del_lock_de_desarrollo)
        + problemas_de_cobertura(reporte, texto_del_lock)
        + problemas_de_la_salida(reporte)
        + problemas_de_altas(reporte)
        + problemas_de_anotacion(reporte)
    )


# --- Sobre el reporte guardado en el repositorio ---------------------------------------------


@pytest.fixture(scope="module")
def texto_del_reporte():
    assert REPORTE.is_file(), "Falta eval/reportes/auditoria-dependencias.txt"
    return leer(REPORTE)


def test_el_reporte_no_deja_vulnerabilidades_altas_abiertas(texto_del_reporte):
    assert problemas_de_altas(leer_reporte(texto_del_reporte)) == []


def test_las_demas_vulnerabilidades_quedan_anotadas_con_su_id_y_su_severidad(texto_del_reporte):
    assert problemas_de_anotacion(leer_reporte(texto_del_reporte)) == []


def test_el_reporte_audita_el_requirements_txt_de_hoy_con_la_pip_audit_del_lock_de_desarrollo(texto_del_reporte):
    reporte = leer_reporte(texto_del_reporte)
    problemas = problemas_del_lock(reporte, leer_de_la_raiz("requirements.txt"), leer_de_la_raiz("requirements-dev.txt"))
    assert problemas == []


def test_el_reporte_audito_todos_los_paquetes_con_pypi_y_con_osv_sin_omitir_ninguno(texto_del_reporte):
    problemas = problemas_de_cobertura(leer_reporte(texto_del_reporte), leer_de_la_raiz("requirements.txt"))
    assert problemas == []


def test_la_salida_literal_de_pip_audit_coincide_con_las_cifras_del_reporte(texto_del_reporte):
    assert problemas_de_la_salida(leer_reporte(texto_del_reporte)) == []


# --- El reporte y su generador: las reglas fallan cuando deben --------------------------------


@pytest.fixture(scope="module")
def script():
    return importlib.import_module("eval.auditar_dependencias")


def auditoria(script, servicio, auditados, vulnerabilidades=(), omitidos=0):
    cantidad = len(vulnerabilidades)
    resumen = f"Found {cantidad} known vulnerabilities in 1 package" if cantidad else RESUMEN_SIN_AVISOS
    return script.Auditoria(
        servicio=servicio,
        auditados=auditados,
        omitidos=omitidos,
        codigo_de_salida=1 if cantidad else 0,
        salida=resumen,
        vulnerabilidades=list(vulnerabilidades),
    )


def vulnerabilidad(script, ident="GHSA-aaaa-bbbb-cccc", paquete="ejemplo==1.0.0"):
    return script.Vulnerabilidad(id=ident, paquete=paquete, alias=("CVE-2026-0001",), corrige_en=("1.0.1",))


def pip_audit_del_lock_de_desarrollo():
    return version_fijada_de_pip_audit(leer_de_la_raiz("requirements-dev.txt"))


def reporte_armado(script, vulnerabilidades=(), severidades=None, lock=None, version=None, omitidos=0):
    lock = lock if lock is not None else leer_de_la_raiz("requirements.txt")
    version = version or pip_audit_del_lock_de_desarrollo()
    fijados = len(paquetes_del_lock(lock))
    auditorias = [auditoria(script, nombre, fijados - omitidos, vulnerabilidades, omitidos) for nombre in SERVICIOS]
    return script.componer_reporte(lock, version, FECHA, auditorias, severidades or {})


def test_un_reporte_sin_vulnerabilidades_cumple_todas_las_reglas(script):
    assert todos_los_problemas(reporte_armado(script)) == []


def test_una_vulnerabilidad_media_anotada_con_su_id_cumple_el_criterio(script):
    texto = reporte_armado(script, [vulnerabilidad(script)], {"GHSA-aaaa-bbbb-cccc": "media"})
    assert todos_los_problemas(texto) == []
    assert re.search(r"^\s+GHSA-aaaa-bbbb-cccc \| ejemplo==1\.0\.0 \| severidad: media", texto, re.M)


def test_la_severidad_se_puede_dar_con_el_alias_de_la_vulnerabilidad(script):
    texto = reporte_armado(script, [vulnerabilidad(script)], {"CVE-2026-0001": "baja"})
    assert todos_los_problemas(texto) == []


@pytest.mark.parametrize("severidad", ["alta", "crítica"])
def test_una_vulnerabilidad_alta_o_critica_rompe_el_criterio_y_el_reporte_lo_declara(script, severidad):
    texto = reporte_armado(script, [vulnerabilidad(script)], {"GHSA-aaaa-bbbb-cccc": severidad})
    reporte = leer_reporte(texto)
    problemas = problemas_de_altas(reporte)
    assert any("1 vulnerabilidades altas" in problema for problema in problemas), problemas
    assert reporte.campos["Altas o críticas abiertas"] == "1"
    assert reporte.campos["Veredicto"].startswith("NO CUMPLE")


def test_una_vulnerabilidad_sin_clasificar_no_pasa_como_no_alta(script):
    texto = reporte_armado(script, [vulnerabilidad(script)])
    problemas = problemas_de_anotacion(leer_reporte(texto))
    assert any("sin clasificar" in problema for problema in problemas), problemas


def test_una_vulnerabilidad_que_un_servicio_reporta_y_el_reporte_no_anota_se_detecta(script):
    texto = reporte_armado(script, [vulnerabilidad(script)], {"GHSA-aaaa-bbbb-cccc": "media"})
    sin_anotar = "\n".join(linea for linea in texto.splitlines() if "| severidad:" not in linea)
    problemas = problemas_de_anotacion(leer_reporte(sin_anotar))
    assert any("anota 0" in problema for problema in problemas), problemas


def test_un_lock_que_cambio_despues_de_la_auditoria_se_detecta(script):
    lock = leer_de_la_raiz("requirements.txt")
    texto = reporte_armado(script, lock=lock)
    lock_nuevo = lock + "paquete-nuevo==1.0.0 \\\n    --hash=sha256:" + "0" * 64 + "\n"
    problemas = problemas_del_lock(leer_reporte(texto), lock_nuevo, leer_de_la_raiz("requirements-dev.txt"))
    claves = {problema.split(":")[0] for problema in problemas}
    assert claves == {"sha256 del archivo", "Paquetes fijados en el archivo"}, problemas


def test_el_sha256_no_cambia_entre_saltos_de_linea_lf_y_crlf(script):
    lock = leer_de_la_raiz("requirements.txt")
    assert script.huella_del_lock(lock.replace("\n", "\r\n")) == huella(lock) == script.huella_del_lock(lock)


def test_otra_version_de_pip_audit_se_detecta(script):
    texto = reporte_armado(script, version="0.0.1")
    problemas = problemas_del_lock(leer_reporte(texto), leer_de_la_raiz("requirements.txt"), leer_de_la_raiz("requirements-dev.txt"))
    assert [problema.split(":")[0] for problema in problemas] == ["Herramienta"]


def test_un_paquete_omitido_se_detecta(script):
    texto = reporte_armado(script, omitidos=1)
    problemas = problemas_de_cobertura(leer_reporte(texto), leer_de_la_raiz("requirements.txt"))
    assert len(problemas) == 2 * len(SERVICIOS), problemas


def test_un_reporte_sin_la_corrida_de_osv_se_detecta(script):
    lock = leer_de_la_raiz("requirements.txt")
    solo_pypi = [auditoria(script, "PyPI", len(paquetes_del_lock(lock)))]
    texto = script.componer_reporte(lock, pip_audit_del_lock_de_desarrollo(), FECHA, solo_pypi, {})
    assert problemas_de_cobertura(leer_reporte(texto), lock) == ["El reporte no trae la corrida de pip-audit con OSV"]


def test_una_salida_que_no_coincide_con_las_cifras_se_detecta(script):
    texto = reporte_armado(script).replace(RESUMEN_SIN_AVISOS, "Found 3 known vulnerabilities in 1 package")
    assert len(problemas_de_la_salida(leer_reporte(texto))) == len(SERVICIOS)


def test_un_archivo_que_no_es_un_reporte_falla_en_todas_las_reglas():
    reporte = leer_reporte("basura\n")
    lock, lock_de_desarrollo = leer_de_la_raiz("requirements.txt"), leer_de_la_raiz("requirements-dev.txt")
    assert problemas_del_lock(reporte, lock, lock_de_desarrollo)
    assert problemas_de_cobertura(reporte, lock)
    assert problemas_de_la_salida(reporte)
    assert problemas_de_altas(reporte)
    assert problemas_de_anotacion(reporte)


# --- El generador ------------------------------------------------------------------------------


LOCK_DE_EJEMPLO = """\
# comentario del encabezado
colorama==0.4.6 ; sys_platform == 'win32' \\
    --hash=sha256:aaaa \\
    --hash=sha256:bbbb
    # via click
pycparser==3.12 ; implementation_name != 'PyPy' and platform_python_implementation != 'PyPy' \\
    --hash=sha256:cccc
click==8.5.0 \\
    --hash=sha256:dddd
"""


def test_quitar_marcadores_en_un_ejemplo_deja_todo_lo_demas_igual(script):
    esperado = LOCK_DE_EJEMPLO.replace(" ; sys_platform == 'win32'", "").replace(
        " ; implementation_name != 'PyPy' and platform_python_implementation != 'PyPy'", ""
    )
    assert script.quitar_marcadores(LOCK_DE_EJEMPLO) == esperado


def test_quitar_marcadores_en_el_lock_deja_los_mismos_paquetes_y_hashes_y_ningun_marcador(script):
    lock = leer_de_la_raiz("requirements.txt")
    sin_marcadores = script.quitar_marcadores(lock)
    assert paquetes_del_lock(sin_marcadores) == paquetes_del_lock(lock)
    assert re.findall(r"--hash=\S+", sin_marcadores) == re.findall(r"--hash=\S+", lock)
    assert " ; " not in sin_marcadores


def test_paquetes_fijados_cuenta_los_mismos_paquetes_que_la_lectura_de_la_prueba(script):
    lock = leer_de_la_raiz("requirements.txt")
    nombres_normalizados = [(re.sub(r"[-_.]+", "-", nombre).lower(), version) for nombre, version in paquetes_del_lock(lock)]
    assert script.paquetes_fijados(lock) == nombres_normalizados
    assert nombres_normalizados


def test_parsear_severidades_acepta_los_cuatro_niveles_y_rechaza_los_demas(script):
    esperado = {"A": "baja", "B": "media", "C": "alta", "D": "crítica", "E": "crítica"}
    assert script.parsear_severidades(["A=baja", "B=Media", "C=ALTA", "D=crítica", "E=critica"]) == esperado
    for invalido in ["A", "A=grave", "=media"]:
        with pytest.raises(SystemExit):
            script.parsear_severidades([invalido])


def corrida(stdout, stderr, codigo):
    return subprocess.CompletedProcess(args=["pip-audit"], returncode=codigo, stdout=stdout, stderr=stderr)


def json_de_pip_audit(*dependencias):
    return json.dumps({"dependencies": list(dependencias), "fixes": []})


LIMPIA = {"name": "click", "version": "8.5.0", "vulns": []}
PYASN1_LIMPIA = {"name": "pyasn1", "version": "0.6.4", "vulns": []}
PYASN1_VULNERABLE = {
    "name": "pyasn1",
    "version": "0.6.4",
    "vulns": [{"id": "CVE-2026-119194", "fix_versions": ["0.6.5"], "aliases": ["GHSA-fm9c-5rcf-2q5p"]}],
}
SIN_AVISOS = "No known vulnerabilities found\n"
CON_UN_AVISO = "Found 1 known vulnerability in 1 package\n"
LO_QUE_FIJA_EL_LOCK = {("click", "8.5.0"), ("pyasn1", "0.6.4")}


def test_leer_corrida_sin_vulnerabilidades(script):
    resultado = script.leer_corrida("PyPI", corrida(json_de_pip_audit(LIMPIA, PYASN1_LIMPIA), SIN_AVISOS, 0), LO_QUE_FIJA_EL_LOCK)
    assert (resultado.auditados, resultado.omitidos, resultado.codigo_de_salida) == (2, 0, 0)
    assert resultado.salida == "No known vulnerabilities found"
    assert resultado.vulnerabilidades == []


def test_leer_corrida_con_una_vulnerabilidad_guarda_su_id_sus_alias_y_la_version_que_la_corrige(script):
    resultado = script.leer_corrida("OSV", corrida(json_de_pip_audit(LIMPIA, PYASN1_VULNERABLE), CON_UN_AVISO, 1), LO_QUE_FIJA_EL_LOCK)
    esperada = script.Vulnerabilidad("CVE-2026-119194", "pyasn1==0.6.4", ("GHSA-fm9c-5rcf-2q5p",), ("0.6.5",))
    assert resultado.vulnerabilidades == [esperada]
    assert (resultado.codigo_de_salida, resultado.salida) == (1, "Found 1 known vulnerability in 1 package")


NO_PRUEBAN_QUE_SE_AUDITO_TODO = [
    pytest.param("", "ERROR: sin red\n", 1, id="el_codigo_1_de_un_error_no_es_una_vulnerabilidad"),
    pytest.param("{}", SIN_AVISOS, 0, id="json_sin_dependencias"),
    pytest.param(json_de_pip_audit(LIMPIA, PYASN1_LIMPIA), "", 0, id="sin_el_resumen_de_pip_audit"),
    pytest.param(json_de_pip_audit(LIMPIA, PYASN1_LIMPIA), SIN_AVISOS, 1, id="codigo_1_sin_vulnerabilidades"),
    pytest.param(json_de_pip_audit(LIMPIA, PYASN1_VULNERABLE), CON_UN_AVISO, 0, id="codigo_0_con_vulnerabilidades"),
    pytest.param(json_de_pip_audit(LIMPIA), SIN_AVISOS, 0, id="falta_un_paquete_del_lock"),
    pytest.param(json_de_pip_audit(LIMPIA, {"name": "pyasn1", "skip_reason": "no está en PyPI"}), SIN_AVISOS, 0, id="paquete_omitido"),
    pytest.param(json_de_pip_audit(LIMPIA, PYASN1_LIMPIA, {"name": "extra", "version": "1", "vulns": []}), SIN_AVISOS, 0, id="paquete_que_no_es_del_lock"),
]


@pytest.mark.parametrize(("stdout", "stderr", "codigo"), NO_PRUEBAN_QUE_SE_AUDITO_TODO)
def test_leer_corrida_rechaza_lo_que_no_prueba_que_se_audito_todo_el_lock(script, stdout, stderr, codigo):
    with pytest.raises(script.ErrorDeAuditoria):
        script.leer_corrida("PyPI", corrida(stdout, stderr, codigo), LO_QUE_FIJA_EL_LOCK)


def test_auditar_corre_pip_audit_sin_instalar_nada_y_sin_omitir_en_silencio(script, monkeypatch, tmp_path):
    comandos = []

    def falsa(comando, **opciones):
        comandos.append(comando)
        return corrida(json_de_pip_audit(LIMPIA), SIN_AVISOS, 0)

    monkeypatch.setattr(script.subprocess, "run", falsa)
    cache = tmp_path / "cache"
    script.auditar("OSV", tmp_path / "requirements.txt", cache, {("click", "8.5.0")})

    comando = comandos[0]
    assert {"--disable-pip", "--strict"} <= set(comando)
    assert comando[comando.index("-s") + 1] == "osv"
    assert comando[comando.index("-f") + 1] == "json"
    assert comando[comando.index("--cache-dir") + 1] == str(cache)


@pytest.fixture
def simular_la_auditoria(script, monkeypatch, tmp_path):
    """main() sin red ni pip-audit instalado: la auditoría devuelve lo que el caso necesite y el reporte va a tmp_path."""
    monkeypatch.setattr(script, "REPORTE", tmp_path / "auditoria.txt")
    monkeypatch.setattr(script, "version_de_pip_audit", pip_audit_del_lock_de_desarrollo)
    fijados = len(paquetes_del_lock(leer_de_la_raiz("requirements.txt")))

    def simular(vulnerabilidades):
        auditorias = [auditoria(script, nombre, fijados, vulnerabilidades) for nombre in SERVICIOS]
        monkeypatch.setattr(script, "auditar_lock", lambda texto, carpeta: auditorias)
        return tmp_path / "auditoria.txt"

    return simular


def test_main_escribe_el_reporte_con_saltos_de_linea_lf_y_devuelve_0_si_cumple(script, simular_la_auditoria):
    destino = simular_la_auditoria([])
    assert script.main([]) == 0
    assert b"\r\n" not in destino.read_bytes()
    assert todos_los_problemas(leer(destino)) == []


def test_main_anota_la_vulnerabilidad_con_la_severidad_que_se_le_da_y_devuelve_0_si_no_es_alta(script, simular_la_auditoria):
    destino = simular_la_auditoria([vulnerabilidad(script)])
    assert script.main(["--severidad", "GHSA-aaaa-bbbb-cccc=media"]) == 0
    assert todos_los_problemas(leer(destino)) == []


def test_main_devuelve_1_si_la_vulnerabilidad_es_alta_y_deja_el_reporte_que_lo_dice(script, simular_la_auditoria):
    destino = simular_la_auditoria([vulnerabilidad(script)])
    assert script.main(["--severidad", "GHSA-aaaa-bbbb-cccc=alta"]) == 1
    assert problemas_de_altas(leer_reporte(leer(destino)))


def test_main_devuelve_1_si_queda_una_vulnerabilidad_sin_clasificar(script, simular_la_auditoria):
    destino = simular_la_auditoria([vulnerabilidad(script)])
    assert script.main([]) == 1
    assert problemas_de_anotacion(leer_reporte(leer(destino)))


def test_main_no_escribe_nada_y_devuelve_2_si_pip_audit_falla(script, monkeypatch, tmp_path):
    destino = tmp_path / "auditoria.txt"
    monkeypatch.setattr(script, "REPORTE", destino)

    def fallar(texto, carpeta):
        raise script.ErrorDeAuditoria("sin red")

    monkeypatch.setattr(script, "auditar_lock", fallar)
    assert script.main([]) == 2
    assert not destino.exists()


# --- Con red (PROBAR_CON_RED=1) ------------------------------------------------------------


@CON_RED
@CON_PIP_AUDIT
def test_una_auditoria_de_hoy_no_encuentra_avisos_que_el_reporte_no_anote(script, tmp_path):
    lock = leer_de_la_raiz("requirements.txt")
    anotados = {hallazgo.id for hallazgo in leer_reporte(leer(REPORTE)).hallazgos}
    for auditoria_de_hoy in script.auditar_lock(lock, tmp_path):
        assert auditoria_de_hoy.omitidos == 0
        assert auditoria_de_hoy.auditados == len(paquetes_del_lock(lock))
        nuevos = {reportada.id for reportada in auditoria_de_hoy.vulnerabilidades} - anotados
        assert not nuevos, f"{auditoria_de_hoy.servicio} encuentra avisos que el reporte no anota: {sorted(nuevos)}"


@CON_RED
@CON_PIP_AUDIT
def test_control_positivo_pyasn1_0_6_4_aparece_con_sus_cuatro_avisos_altos(script, tmp_path):
    lock = re.sub(r"^pyasn1==\S+", "pyasn1==0.6.4", leer_de_la_raiz("requirements.txt"), flags=re.M)
    for auditoria_de_control in script.auditar_lock(lock, tmp_path):
        encontrados = {identificador for v in auditoria_de_control.vulnerabilidades for identificador in (v.id, *v.alias)}
        assert AVISOS_DE_PYASN1_0_6_4 <= encontrados, f"{auditoria_de_control.servicio}: {sorted(encontrados)}"
        assert {v.paquete for v in auditoria_de_control.vulnerabilidades} == {"pyasn1==0.6.4"}
