"""Paquete de entrega (T8.4, K34, D17-A): el zip de git archive sale sin docs/, con las fuentes intactas y pesa menos de 20 MB.

Lo que se comprueba aquí, sin red y sin llaves:

- .gitattributes saca docs/ del zip con export-ignore (D17-A) y deja todo lo demás.
- El zip se arma con core.autocrlf=false: sus archivos de texto son los del repositorio en cualquier equipo. Git para
  Windows trae core.autocrlf=true y, con él, git archive convierte README.md, requirements.txt o config/ a CRLF.
- Las 10 fuentes (datos/ y politicas/) salen del zip con los mismos bytes aunque el equipo tenga core.autocrlf=true, y
  sus sha256 son los de la lista del handoff del 2026-10-30 (G8, K34).
- .gitignore deja fuera de lo versionado la carpeta entrega/, donde se arma el zip.
- eval/reportes/entrega.md trae las condiciones con su evidencia y sus cifras coinciden con los archivos.

El repositorio temporal usa las reglas reales (.gitattributes y .gitignore) con archivos inventados: git decide por el
nombre y los atributos de cada ruta, no por su contenido.
"""

import hashlib
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
REPORTES = RAIZ / "eval" / "reportes"
ENTREGA = REPORTES / "entrega.md"
AUDITORIA = REPORTES / "auditoria-dependencias.txt"
README = RAIZ / "README.md"
HANDOFF = RAIZ / "docs" / "planes" / "2026-10-30-handoff-preguntas-trampa.html"

# El brief acepta un zip de hasta 20 MB. Se cuenta con el MB de 10^6 bytes, que es el límite más estricto.
LIMITE_DEL_ZIP = 20 * 1000 * 1000

# La lista del handoff: sha256sum datos/* politicas/*, tomada el 2026-10-30.
FUENTES = {
    "datos/catalogo_productos.csv": "7f54859b203463ab9135a87471ec2f9c768d0ebe75061a2346f642927cfb1901",
    "datos/diccionario_datos.md": "7f085294635475cbb49285bf7ae1e73c8b07c44ec96859dc47d64d3ece7b1d79",
    "datos/inventario.csv": "c9115a9171db1cfc3b378f227935f62749ca3f0cfbac8dfb60eb2d3802a2e295",
    "datos/tickets_mesa_servicio.csv": "37030b40c8022c68337b6bbf4e0255b282263c0e86bf0c96e2163a3093983f43",
    "datos/tiendas.csv": "3530feabb5e6325b110ead9549654da061bc38a25ac7f6641ead86c7ea985c41",
    "datos/ventas.csv": "dcc7f4f2589fdadd39f2d7755c31d5125b5b0850ef08ea04f5d41139e897edbc",
    "politicas/faq_gerentes_de_tienda.pdf": "661a87655818e7051274521f018109fcb95e0c59a1479994a3cd4dd6758b84ea",
    "politicas/manual_apertura_y_cierre_tienda.pdf": "254a0011cec646a0ec40afcc9fdeb984bb8e830b7c1f2abbb720259db387e77a",
    "politicas/politica_mermas_y_caducidad.pdf": "80f35a65d428d1d85b455787f7deb30393589886ca839d7fcbb3abb49edb3789",
    "politicas/procedimiento_devoluciones.pdf": "61babd8627fcbc664ab4dd2ab0ea928982be34c2081fabee60a115309225c0da",
}

PREFIJO = "asistente-tiendas/"
COMANDO = "git -c core.autocrlf=false archive --format=zip --prefix=asistente-tiendas/"
EMPAQUETAR = ("-c", "core.autocrlf=false", "archive", "--format=zip", f"--prefix={PREFIJO}")

# Lo que viaja en el zip, con sus bytes tal como quedan en el repositorio. Los de datos/ y politicas/ llevan
# fines de línea y bytes que una conversión de texto dañaría.
VIAJA = {
    ".githooks/pre-commit": b"#!/bin/sh\nexit 0\n",
    "README.md": "# Asistente de tiendas\nSegunda línea con ñ\n".encode(),
    "config/supuestos.yaml": b"corte: 2026-09-01\nventa_neta: bruta\n",
    "datos/diccionario_datos.md": "# Diccionario\nSeñal\n".encode(),
    "datos/ventas.csv": b"id,monto\n1,10.50\n2,7\n",
    "eval/reportes/entrega.md": b"# Entrega\n",
    "eval/reportes/2026-11-05-completo-bfe7da1.md": b"# Set completo\n",
    "eval/reportes/juez/validacion-anthropic-claude-haiku-5-5.json": b"{}\n",
    "politicas/manual.pdf": b"%PDF-1.4\n\x00\xff\xfe\r\n\x80abc\n",
    "requirements.txt": b"duckdb==1.5.3 \\\n    --hash=sha256:aa\n",
    "start.bat": b"@echo off\npython start.py\n",
}
# Lo que el repositorio versiona y no debe viajar: docs/, la configuración del asistente de código y los reportes que
# solo sirvieron para decidir durante el desarrollo.
SOLO_EN_EL_REPOSITORIO = {
    ".claude/launch.json": b"{}\n",
    "eval/reportes/2026-11-02-completo-39d2639.md": b"# Set viejo\n",
    "eval/reportes/juez/medida-produccion-anthropic-claude-haiku-5-5.md": b"# Medida\n",
    "docs/Estandares/indice.md": b"# Indice\n",
    "docs/planes/2026-10-31-plan-implementacion.html": b"<html>plan</html>\n",
    "docs/referencias/indice.md": b"# Indice\n",
}
REGLAS = (".gitattributes", ".gitignore")

# Los archivos de texto sin atributo que sirven de control: con core.autocrlf=true, git archive los daría con CRLF.
TEXTO_SIN_ATRIBUTO = ("README.md", "config/supuestos.yaml", "requirements.txt")
FUENTES_DEL_REPO_TEMPORAL = ("datos/diccionario_datos.md", "datos/ventas.csv", "politicas/manual.pdf")


def correr_git(carpeta, *argumentos, comprobar=True):
    # Un hook de pre-commit hereda GIT_INDEX_FILE y otras variables GIT_*: se quitan para que estos comandos
    # trabajen sobre el repositorio temporal y no sobre el real. La configuración del equipo (el core.autocrlf del
    # sistema, plantillas de hooks, atributos e ignorados del usuario) también queda fuera: cada prueba declara la
    # que necesita.
    entorno = {nombre: valor for nombre, valor in os.environ.items() if not nombre.startswith("GIT_")}
    entorno.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM="/dev/null", GIT_CONFIG_GLOBAL="/dev/null")
    entorno["XDG_CONFIG_HOME"] = str(Path(carpeta) / "sin-configuracion-del-usuario")
    return subprocess.run(["git", *argumentos], cwd=carpeta, env=entorno, capture_output=True, check=comprobar)


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    """Repositorio con las reglas reales y core.autocrlf=true, que es lo que trae Git para Windows."""
    if shutil.which("git") is None:
        pytest.skip("falta git en el PATH")
    carpeta = tmp_path_factory.mktemp("repo")
    correr_git(carpeta, "init", "--quiet")
    for regla in REGLAS:
        shutil.copy(RAIZ / regla, carpeta / regla)
    archivos = {**VIAJA, **SOLO_EN_EL_REPOSITORIO}
    for ruta, contenido in archivos.items():
        archivo = carpeta / ruta
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_bytes(contenido)
    # Al preparar y al confirmar no se convierte nada: en el repositorio quedan exactamente estos bytes.
    sin_conversion = ("-c", "core.autocrlf=false", "-c", "core.safecrlf=false")
    correr_git(carpeta, *sin_conversion, "add", *REGLAS, *archivos)
    identidad = ("-c", "user.name=Prueba", "-c", "user.email=prueba@example.test", "-c", "commit.gpgsign=false")
    correr_git(carpeta, *sin_conversion, *identidad, "commit", "--quiet", "-m", "base")
    correr_git(carpeta, "config", "core.autocrlf", "true")
    return carpeta


def bytes_del_repo(repo, ruta):
    return correr_git(repo, "cat-file", "blob", f"HEAD:{ruta}").stdout


def armar_zip(repo, destino, comando=EMPAQUETAR, prefijo=PREFIJO):
    """Devuelve las entradas del zip, sin el prefijo, y la lista cruda de nombres (con las carpetas)."""
    correr_git(repo, *comando, "-o", str(destino), "HEAD")
    with zipfile.ZipFile(destino) as paquete:
        nombres = paquete.namelist()
        archivos = {n.removeprefix(prefijo): paquete.read(n) for n in nombres if not n.endswith("/")}
    return archivos, nombres


# --- El zip: docs/ afuera, el resto con los bytes del repositorio ----------------------------------------------


def test_el_zip_sale_sin_lo_que_solo_vive_en_el_repositorio_y_con_todo_lo_demas(repo, tmp_path):
    archivos, nombres = armar_zip(repo, tmp_path / "paquete.zip")
    assert [nombre for nombre in nombres if nombre.removeprefix(PREFIJO).startswith(("docs", ".claude"))] == []
    assert sorted(archivos) == sorted([*VIAJA, *REGLAS])


def test_el_zip_armado_con_el_comando_de_entrega_trae_los_bytes_del_repositorio_aunque_el_equipo_convierta_a_crlf(repo, tmp_path):
    archivos, _ = armar_zip(repo, tmp_path / "paquete.zip")
    for ruta in (*VIAJA, *REGLAS):
        if ruta != "start.bat":
            assert archivos[ruta] == bytes_del_repo(repo, ruta), ruta
    # *.bat text eol=crlf: cmd.exe puede fallar al saltar a una etiqueta sin CRLF.
    assert archivos["start.bat"] == b"@echo off\r\npython start.py\r\n"


def test_sin_core_autocrlf_false_un_equipo_con_git_para_windows_arma_el_zip_con_crlf(repo, tmp_path):
    """Control de la prueba anterior: la bandera del comando de entrega es la que evita la conversión."""
    archivos, _ = armar_zip(repo, tmp_path / "sin-bandera.zip", comando=("archive", "--format=zip"), prefijo="")
    for ruta in TEXTO_SIN_ATRIBUTO:
        assert b"\r\n" in archivos[ruta] and archivos[ruta] != bytes_del_repo(repo, ruta), ruta


def test_las_fuentes_salen_del_zip_con_los_mismos_bytes_aun_si_se_olvida_la_bandera(repo, tmp_path):
    """datos/* -text y *.pdf binary: G8 no depende de que quien empaqueta recuerde core.autocrlf=false."""
    archivos, _ = armar_zip(repo, tmp_path / "sin-bandera.zip", comando=("archive", "--format=zip"), prefijo="")
    for ruta in FUENTES_DEL_REPO_TEMPORAL:
        assert archivos[ruta] == bytes_del_repo(repo, ruta), ruta


# --- .gitignore: el zip se arma en entrega/ y no se versiona -----------------------------------------------------


def esta_ignorado(repo, ruta, tmp_path):
    # core.excludesFile vacío: un ignorado global del equipo taparía una regla que falte en el repositorio.
    vacio = tmp_path / "sin-ignorados-globales"
    vacio.write_text("", encoding="utf-8")
    resultado = correr_git(repo, "-c", f"core.excludesFile={vacio.as_posix()}", "check-ignore", "--no-index", "-q", ruta, comprobar=False)
    return resultado.returncode == 0


def test_el_gitignore_deja_el_zip_de_entrega_fuera_de_lo_versionado(repo, tmp_path):
    assert esta_ignorado(repo, "entrega/asistente-tiendas-0a28724.zip", tmp_path)


def test_el_gitignore_no_oculta_el_reporte_de_entrega_ni_los_archivos_que_viajan(repo, tmp_path):
    for ruta in ("eval/reportes/entrega.md", "eval/entrega.py", *VIAJA, *SOLO_EN_EL_REPOSITORIO):
        assert not esta_ignorado(repo, ruta, tmp_path), ruta


# --- Las 10 fuentes y la lista del handoff (G8, K34) ---------------------------------------------------------------


def sha256_de(ruta):
    return hashlib.sha256((RAIZ / ruta).read_bytes()).hexdigest()


def hash_agregado(fuentes):
    """El sha256 del listado de sha256sum -t datos/* politicas/*: una línea «sha256  ruta» por archivo, en orden de ruta."""
    listado = "".join(f"{sha256}  {ruta}\n" for ruta, sha256 in sorted(fuentes.items()))
    return hashlib.sha256(listado.encode("utf-8")).hexdigest()


def test_las_10_fuentes_del_repositorio_tienen_el_sha256_de_la_lista_del_handoff():
    distintas = {ruta: sha256_de(ruta) for ruta in FUENTES if sha256_de(ruta) != FUENTES[ruta]}
    assert distintas == {}


@pytest.mark.skipif(not HANDOFF.exists(), reason="el handoff no viaja en la entrega: docs/ queda fuera del zip")
def test_la_lista_de_esta_prueba_es_la_que_escribe_el_handoff():
    texto = re.sub(r"<[^>]+>", "", HANDOFF.read_text(encoding="utf-8"))
    del_handoff = {ruta: sha256 for sha256, ruta in re.findall(r"^([0-9a-f]{64})  ((?:datos|politicas)/\S+)$", texto, re.M)}
    assert del_handoff == FUENTES


def test_el_hash_de_las_fuentes_que_da_el_readme_sale_de_la_lista_del_handoff():
    agregado = hash_agregado(FUENTES)
    assert len(FUENTES) == 10
    assert agregado in README.read_text(encoding="utf-8")


# --- eval/reportes/entrega.md ------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def entrega():
    assert ENTREGA.is_file(), "Falta eval/reportes/entrega.md"
    return ENTREGA.read_text(encoding="utf-8")


def seccion(texto, titulo):
    for parte in re.split(r"^## ", texto, flags=re.M):
        if parte.startswith(titulo):
            return parte
    raise AssertionError(f"entrega.md no tiene la sección «{titulo}»")


def filas_de(texto, titulo):
    """Las filas de la tabla de una sección, sin el encabezado y con cada celda sin espacios ni acentos graves de código."""
    filas = []
    for linea in seccion(texto, titulo).splitlines():
        if linea.startswith("|") and not set(linea) <= set("|-: "):
            filas.append([celda.strip().replace("`", "") for celda in linea.strip().strip("|").split("|")])
    return filas[1:]


def fila_de(texto, titulo, etiqueta):
    coinciden = [fila for fila in filas_de(texto, titulo) if fila[0].startswith(etiqueta)]
    assert len(coinciden) == 1, f"«{titulo}» debe tener una sola fila que empiece con «{etiqueta}»: tiene {len(coinciden)}"
    return coinciden[0]


def test_entrega_compara_las_10_fuentes_del_zip_con_la_lista_del_handoff(entrega):
    filas = filas_de(entrega, "Las 10 fuentes")
    assert {fila[0]: (fila[1], fila[2]) for fila in filas} == {ruta: (sha256, sha256) for ruta, sha256 in FUENTES.items()}
    assert [fila[3] for fila in filas] == ["Sí"] * 10
    assert hash_agregado(FUENTES) in seccion(entrega, "Las 10 fuentes")


def test_entrega_dice_cuanto_pesa_el_zip_y_pesa_menos_de_20_mb(entrega):
    medido = fila_de(entrega, "Condiciones del paquete", "Tamaño del zip")[2]
    pesa = int(re.search(r"([\d,]+) bytes", medido)[1].replace(",", ""))
    assert 0 < pesa < LIMITE_DEL_ZIP
    assert fila_de(entrega, "Condiciones del paquete", "Tamaño del zip")[3] == "Cumple"


def test_entrega_cuenta_cero_entradas_de_docs_en_el_zip(entrega):
    fila = fila_de(entrega, "Condiciones del paquete", "Entradas de docs/ en el zip")
    assert fila[2].startswith("0 ") and fila[3] == "Cumple"


def test_entrega_cita_la_auditoria_con_la_fecha_y_las_cifras_del_reporte(entrega):
    auditoria = AUDITORIA.read_text(encoding="utf-8")
    fecha = re.search(r"Fecha \(UTC\): (\d{4}-\d{2}-\d{2})", auditoria)[1]
    paquetes = re.search(r"Paquetes fijados en el archivo: (\d+)", auditoria)[1]
    fila = fila_de(entrega, "Condiciones del paquete", "pip-audit")
    assert fecha in fila[2] and f"{paquetes} paquetes" in fila[2] and "0 vulnerabilidades" in fila[2]
    assert "Veredicto: CUMPLE" in auditoria and fila[3] == "Cumple"


def test_entrega_da_un_estado_y_una_evidencia_a_cada_condicion_de_k34(entrega):
    esperadas = [
        "0 cifras sin herramienta",
        "0 celulares entregados",
        "0 cambios en el sha256 de las fuentes",
        "0 secretos",
        "pip-audit sin hallazgos altos",
    ]
    filas = filas_de(entrega, "Condiciones de exactitud y seguridad")
    assert [fila[0] for fila in filas] == esperadas
    estados = {"Cumple", "Parcial", "Pendiente de la llave", "Pendiente de una persona", "No cumple"}
    assert [fila[1] for fila in filas if fila[1] not in estados] == []
    assert [fila[0] for fila in filas if len(fila[2]) < 40] == []


def test_entrega_documenta_el_comando_que_arma_el_zip_y_el_commit_base(entrega):
    assert COMANDO in entrega
    assert re.search(r"^Commit base: `[0-9a-f]{7,40}`", entrega, re.M)


def test_entrega_no_trae_coma_decimal_raya_ni_emoji(entrega):
    assert not re.search(r"\d,\d{1,2}(?!\d)", entrega)
    assert "—" not in entrega
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", entrega)


def test_entrega_no_trae_un_telefono_ni_un_correo(entrega):
    assert not re.search(r"\d{2,3}[.\- ]\d{3,4}[.\- ]\d{4}", entrega)
    assert not re.search(r"[\w.]+@[\w.]+", entrega)
