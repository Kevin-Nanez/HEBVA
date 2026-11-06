"""Hash de las fuentes, base vigente y fuente faltante (T1.7, K04, K07).

Acepta: con la copia A, P03 da 17,284,355.44 y muestra la consulta; con la copia B, la respuesta dice
que no pudo consultar ventas.csv y no da cifra. De eso, esta prueba cubre lo que le toca a la base:
guarda el sha256 de cada fuente, se reconstruye sola si algo cambió y lanza un error con tipo que
nombra el archivo que falta. La consulta que se muestra y la frase de la respuesta son de T2.3, T3.1
y T3.3; las copias A y B formales llegan en T5.6, así que aquí se arman copias mínimas en tmp_path
sin tocar datos/.

Las pruebas construyen sin índice de texto (necesita red la primera vez, T0.5) y casi todas sin
políticas, para tardar menos.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from agente import construir, fragmentos_politicas

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATOS = RAIZ / "datos"
DIR_PDF = RAIZ / "politicas"
DIR_MD = RAIZ / "politicas_md"

# Cifras del criterio de aceptación y de la rúbrica (copia A: T02 del 2026-07-01 multiplicada por 2).
P03_COPIA_A = Decimal("17284355.44")
SUMA_T02_1_DE_JULIO = Decimal("56055.18")
P03_SIN_DEVOLUCIONES = Decimal("17272235.76")
ARCHIVOS_DE_LAS_FUENTES = 10
BYTES_DE_LAS_FUENTES = 2_005_181


@pytest.fixture
def copia_pdf(tmp_path):
    """Copia de politicas/ en tmp_path para alterar un PDF sin tocar el original."""
    destino = tmp_path / "politicas"
    shutil.copytree(DIR_PDF, destino)
    return destino


def sha256_de(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def hash_esperado(dir_datos=DIR_DATOS, dir_pdf=DIR_PDF):
    """sha256 del listado que imprime `sha256sum datos/* politicas/*`, armado aquí con hashlib."""
    lineas = []
    for rol, carpeta in (("datos", dir_datos), ("politicas", dir_pdf)):
        for ruta in sorted(Path(carpeta).iterdir(), key=lambda r: r.name):
            lineas.append(f"{sha256_de(ruta)}  {rol}/{ruta.name}\n")
    return hashlib.sha256("".join(lineas).encode("utf-8")).hexdigest()


def asegurar(dir_datos, tmp_path, **opciones):
    """asegurar_base solo con datos (sin políticas ni índice) sobre tmp_path/tienda.duckdb."""
    opciones = {"politicas": False, "indice_texto": False} | opciones
    return construir.asegurar_base(dir_datos, tmp_path / "tienda.duckdb", **opciones)


def venta_neta(base):
    """La consulta de P03: venta neta del trimestre según la definición de la base."""
    with duckdb.connect(str(base), read_only=True) as con:
        return con.execute("SELECT sum(venta_neta_mxn) FROM v_ventas_dia WHERE en_venta_neta").fetchone()[0]


def alterar_como_copia_a(dir_datos):
    """Copia A de la rúbrica: venta_neta_mxn de T02 del 2026-07-01 multiplicada por 2."""
    ruta = Path(dir_datos) / "ventas.csv"
    lineas = ruta.read_bytes().decode("utf-8").split("\n")
    for numero, linea in enumerate(lineas):
        campos = linea.split(",")
        if campos[:2] == ["2026-07-01", "T02"]:
            campos[4] = f"{Decimal(campos[4]) * 2:.2f}"
            lineas[numero] = ",".join(campos)
    ruta.write_bytes("\n".join(lineas).encode("utf-8"))


def archivos_de_la_base(tmp_path):
    """Nombres que quedan junto a la base: el archivo y nada de temporales ni .wal."""
    return sorted(p.name for p in tmp_path.glob("tienda.duckdb*"))


# --- La base guarda el sha256 de cada fuente (K07) --------------------------------------------------


def test_la_base_guarda_el_sha256_de_cada_fuente(base_construida):
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        filas = con.execute(
            "SELECT grupo, archivo, bytes, sha256 FROM hash_archivos WHERE grupo IN ('datos', 'politicas') ORDER BY archivo"
        ).fetchall()
    carpetas = {"datos": DIR_DATOS, "politicas": DIR_PDF}
    assert len(filas) == ARCHIVOS_DE_LAS_FUENTES
    for grupo, archivo, tamano, sha256 in filas:
        rol, nombre = archivo.split("/")
        ruta = carpetas[rol] / nombre
        assert grupo == rol
        assert tamano == ruta.stat().st_size
        assert sha256 == sha256_de(ruta)


def test_la_base_nombra_los_5_csv_y_el_diccionario_y_los_4_pdf(base_construida):
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        archivos = [fila[0] for fila in con.execute("SELECT archivo FROM hash_archivos WHERE grupo IN ('datos', 'politicas') ORDER BY 1").fetchall()]
    assert archivos == [
        "datos/catalogo_productos.csv",
        "datos/diccionario_datos.md",
        "datos/inventario.csv",
        "datos/tickets_mesa_servicio.csv",
        "datos/tiendas.csv",
        "datos/ventas.csv",
        "politicas/faq_gerentes_de_tienda.pdf",
        "politicas/manual_apertura_y_cierre_tienda.pdf",
        "politicas/politica_mermas_y_caducidad.pdf",
        "politicas/procedimiento_devoluciones.pdf",
    ]


def test_el_hash_de_las_fuentes_es_el_de_sha256sum_datos_politicas(base_construida):
    esperado = hash_esperado()
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        fila = con.execute("SELECT archivos, bytes, sha256 FROM hash_fuentes").fetchone()
    assert fila == (ARCHIVOS_DE_LAS_FUENTES, BYTES_DE_LAS_FUENTES, esperado)
    assert base_construida.hash_fuentes == construir.HashFuentes(*fila)


@pytest.mark.skipif(shutil.which("sha256sum") is None, reason="sha256sum no está en el PATH")
def test_el_hash_de_las_fuentes_coincide_con_la_herramienta_sha256sum(base_construida):
    rutas = sorted(f"{rol}/{r.name}" for rol, carpeta in (("datos", DIR_DATOS), ("politicas", DIR_PDF)) for r in carpeta.iterdir())
    salida = subprocess.run(["sha256sum", *rutas], cwd=RAIZ, capture_output=True, text=True, check=True).stdout
    # En Windows imprime " *" (modo binario) donde Linux imprime dos espacios.
    listado = salida.replace(" *", "  ")
    assert hashlib.sha256(listado.encode("utf-8")).hexdigest() == base_construida.hash_fuentes.sha256


def test_una_segunda_construccion_da_el_mismo_hash(copia_datos, tmp_path):
    primera = construir.construir(copia_datos, tmp_path / "a.duckdb", politicas=False, indice_texto=False)
    segunda = construir.construir(copia_datos, tmp_path / "b.duckdb", politicas=False, indice_texto=False)
    assert primera.hash_fuentes == segunda.hash_fuentes
    assert construir.leer_hashes(primera.base) == construir.leer_hashes(segunda.base)


def test_el_hash_ignora_el_nombre_de_la_carpeta_porque_usa_el_rol(copia_datos, tmp_path):
    # Las copias A a D viven en otra carpeta: el hash depende de los bytes, no de dónde estén.
    otra = tmp_path / "eval" / "fixtures" / "copia_x"
    shutil.copytree(copia_datos, otra)
    a = construir.calcular_hashes(copia_datos, DIR_PDF, construir.DIR_CONFIG, politicas=False, indice_texto=False)
    b = construir.calcular_hashes(otra, DIR_PDF, construir.DIR_CONFIG, politicas=False, indice_texto=False)
    assert a == b


def test_el_hash_trae_lo_mismo_que_lista_el_shell_con_carpeta_asterisco(copia_datos, tmp_path):
    # datos/* no incluye ocultos ni subcarpetas; un archivo visible cualquiera sí cuenta.
    (copia_datos / ".DS_Store").write_bytes(b"basura")
    (copia_datos / "subcarpeta").mkdir()
    (copia_datos / "subcarpeta" / "otro.csv").write_bytes(b"a,b\n")
    hashes = construir.calcular_hashes(copia_datos, DIR_PDF, construir.DIR_CONFIG, politicas=False, indice_texto=False)
    assert [h.archivo for h in hashes if h.grupo == "datos"] == [
        f"datos/{nombre}" for nombre in sorted(p.name for p in DIR_DATOS.iterdir())
    ]
    (copia_datos / "notas.txt").write_bytes(b"un archivo visible")
    con_notas = construir.calcular_hashes(copia_datos, DIR_PDF, construir.DIR_CONFIG, politicas=False, indice_texto=False)
    assert "datos/notas.txt" in [h.archivo for h in con_notas]


def test_el_conjunto_guardado_depende_de_las_opciones_de_la_construccion():
    completo = construir.calcular_hashes(DIR_DATOS, DIR_PDF, construir.DIR_CONFIG, politicas=True, indice_texto=True)
    sin_politicas = construir.calcular_hashes(DIR_DATOS, DIR_PDF, construir.DIR_CONFIG, politicas=False, indice_texto=True)
    sin_indice = construir.calcular_hashes(DIR_DATOS, DIR_PDF, construir.DIR_CONFIG, politicas=True, indice_texto=False)

    def nombres(hashes):
        return {h.archivo for h in hashes}

    assert not [a for a in nombres(sin_politicas) if a.startswith("politicas/")]
    assert len([a for a in nombres(completo) if a.startswith("politicas/")]) == 4
    # La lista de palabras vacías solo importa si se crea el índice de texto.
    assert "config/palabras_vacias_es.txt" in nombres(completo)
    assert "config/palabras_vacias_es.txt" not in nombres(sin_indice)
    assert {"config/supuestos.yaml", "config/alias.yaml", "config/temas_tickets.csv"} <= nombres(sin_indice)


# --- Se reconstruye si cambia ---------------------------------------------------------------------------


def test_sin_base_asegurar_base_la_construye(copia_datos, tmp_path):
    estado = asegurar(copia_datos, tmp_path)
    assert estado.reconstruida
    assert estado.base == tmp_path / "tienda.duckdb"
    assert estado.base.is_file()
    assert estado.hash_fuentes.archivos == 6


@pytest.mark.parametrize("carpeta", ["build", "o'brien", "año nuevo"], ids=["simple", "comilla", "acento"])
def test_la_base_puede_estar_en_una_carpeta_nueva_con_comilla_o_acento(copia_datos, tmp_path, carpeta):
    # leer_hashes arma un ATTACH con la ruta dentro de comillas: una comilla en la ruta no debe romperlo.
    base = tmp_path / carpeta / "tienda.duckdb"
    primera = construir.asegurar_base(copia_datos, base, politicas=False, indice_texto=False)
    segunda = construir.asegurar_base(copia_datos, base, politicas=False, indice_texto=False)
    assert (primera.reconstruida, segunda.reconstruida) == (True, False)
    assert segunda.hash_fuentes == primera.hash_fuentes


def test_con_los_mismos_archivos_la_base_no_se_reconstruye(copia_datos, tmp_path, monkeypatch):
    primera = asegurar(copia_datos, tmp_path)
    llamadas = []
    original = construir.construir
    monkeypatch.setattr(construir, "construir", lambda *a, **k: llamadas.append(1) or original(*a, **k))
    segunda = asegurar(copia_datos, tmp_path)
    assert not segunda.reconstruida
    assert llamadas == []
    assert segunda.hash_fuentes == primera.hash_fuentes


def test_copia_a_la_base_se_reconstruye_y_p03_cambia(copia_datos, tmp_path):
    antes = asegurar(copia_datos, tmp_path)
    total_antes = venta_neta(antes.base)
    alterar_como_copia_a(copia_datos)

    despues = asegurar(copia_datos, tmp_path)

    assert despues.reconstruida
    assert "datos/ventas.csv" in despues.motivo
    assert despues.hash_fuentes.sha256 != antes.hash_fuentes.sha256
    total_despues = venta_neta(despues.base)
    assert total_despues == P03_COPIA_A
    assert total_despues - total_antes == SUMA_T02_1_DE_JULIO
    # La base guarda el hash del archivo con que se construyó, no el de la entrega original.
    guardados = {h.archivo: h.sha256 for h in construir.leer_hashes(despues.base).archivos}
    assert guardados["datos/ventas.csv"] == sha256_de(copia_datos / "ventas.csv")
    assert guardados["datos/tiendas.csv"] == sha256_de(DIR_DATOS / "tiendas.csv")


def test_la_base_sigue_a_la_carpeta_que_se_le_pasa(copia_datos, tmp_path):
    # Así se usan las copias A a D: otra carpeta de datos contra la misma base.
    copia_a = tmp_path / "copia_a"
    shutil.copytree(copia_datos, copia_a)
    alterar_como_copia_a(copia_a)
    base = tmp_path / "tienda.duckdb"

    original = construir.asegurar_base(copia_datos, base, politicas=False, indice_texto=False)
    en_a = construir.asegurar_base(copia_a, base, politicas=False, indice_texto=False)
    en_a_otra_vez = construir.asegurar_base(copia_a, base, politicas=False, indice_texto=False)
    de_vuelta = construir.asegurar_base(copia_datos, base, politicas=False, indice_texto=False)

    assert [e.reconstruida for e in (original, en_a, en_a_otra_vez, de_vuelta)] == [True, True, False, True]
    assert en_a.hash_fuentes.sha256 != original.hash_fuentes.sha256
    assert de_vuelta.hash_fuentes == original.hash_fuentes
    assert venta_neta(base) == venta_neta(original.base)


def test_asegurar_base_no_modifica_las_fuentes(copia_datos, tmp_path):
    antes = {p.name: sha256_de(p) for p in copia_datos.iterdir()}
    asegurar(copia_datos, tmp_path)
    alterar_como_copia_a(copia_datos)
    cambiada = sha256_de(copia_datos / "ventas.csv")
    asegurar(copia_datos, tmp_path)
    despues = {p.name: sha256_de(p) for p in copia_datos.iterdir()}
    assert despues == antes | {"ventas.csv": cambiada}


def test_un_cambio_en_la_configuracion_reconstruye_la_base(copia_datos, copia_config, tmp_path):
    # El cambio en vivo de C9.3: la definición está en config/, no en el prompt.
    antes = asegurar(copia_datos, tmp_path, dir_config=copia_config)
    ruta = copia_config / "supuestos.yaml"
    texto = ruta.read_text(encoding="utf-8")
    assert "incluir_devoluciones: true" in texto
    ruta.write_text(texto.replace("incluir_devoluciones: true", "incluir_devoluciones: false", 1), encoding="utf-8")

    despues = asegurar(copia_datos, tmp_path, dir_config=copia_config)

    assert despues.reconstruida
    assert "config/supuestos.yaml" in despues.motivo
    assert venta_neta(despues.base) == P03_SIN_DEVOLUCIONES
    # Los datos no cambiaron: el hash de las fuentes es el mismo aunque la base se reconstruyó.
    assert despues.hash_fuentes == antes.hash_fuentes


def test_un_cambio_en_un_pdf_reconstruye_la_base_de_politicas(copia_datos, copia_pdf, tmp_path):
    opciones = {"dir_pdf": copia_pdf, "politicas": True}
    antes = asegurar(copia_datos, tmp_path, **opciones)
    # Una línea al final no cambia lo que se extrae, pero sí el sha256.
    pdf = copia_pdf / "procedimiento_devoluciones.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"\n%copia modificada\n")

    despues = asegurar(copia_datos, tmp_path, **opciones)

    assert despues.reconstruida
    assert "politicas/procedimiento_devoluciones.pdf" in despues.motivo
    assert despues.hash_fuentes.sha256 != antes.hash_fuentes.sha256
    guardados = {h.archivo: h.sha256 for h in construir.leer_hashes(despues.base).archivos}
    assert guardados["politicas/procedimiento_devoluciones.pdf"] == sha256_de(pdf)
    with duckdb.connect(str(despues.base), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM fragmentos").fetchone()[0] == 62


def test_quitar_el_diccionario_no_es_un_error_pero_reconstruye(copia_datos, tmp_path):
    # El diccionario cuenta en el hash (G8 hashea datos/ completo) pero la base no lo necesita.
    antes = asegurar(copia_datos, tmp_path)
    (copia_datos / "diccionario_datos.md").unlink()
    despues = asegurar(copia_datos, tmp_path)
    assert despues.reconstruida
    assert "datos/diccionario_datos.md" in despues.motivo
    assert despues.hash_fuentes.archivos == antes.hash_fuentes.archivos - 1


def test_los_archivos_ocultos_y_las_subcarpetas_no_reconstruyen(copia_datos, tmp_path):
    asegurar(copia_datos, tmp_path)
    (copia_datos / ".DS_Store").write_bytes(b"basura")
    (copia_datos / "subcarpeta").mkdir()
    (copia_datos / "subcarpeta" / "otro.csv").write_bytes(b"a,b\n")
    assert not asegurar(copia_datos, tmp_path).reconstruida


@pytest.mark.parametrize("contenido", [None, b"esto no es una base de DuckDB" * 50], ids=["sin-base", "archivo-danado"])
def test_una_base_que_no_se_puede_leer_se_reconstruye(copia_datos, tmp_path, contenido):
    base = tmp_path / "tienda.duckdb"
    if contenido is not None:
        base.write_bytes(contenido)
    estado = asegurar(copia_datos, tmp_path)
    assert estado.reconstruida
    assert construir.leer_hashes(base).hash_fuentes == estado.hash_fuentes


def test_una_base_de_otra_version_sin_la_tabla_de_hashes_se_reconstruye(copia_datos, tmp_path):
    base = tmp_path / "tienda.duckdb"
    with duckdb.connect(str(base)) as con:
        con.execute("CREATE TABLE ventas (fecha DATE)")
    assert construir.leer_hashes(base) is None
    estado = asegurar(copia_datos, tmp_path)
    assert estado.reconstruida
    assert "hash" in estado.motivo


def test_leer_los_hashes_no_choca_con_otra_conexion_de_configuracion_distinta(copia_datos, tmp_path):
    # Si la herramienta tiene abierta la base con el acceso externo apagado, DuckDB rechaza una
    # segunda connect() con otra configuración en el mismo proceso. Leer los hashes no debe depender de eso.
    estado = asegurar(copia_datos, tmp_path)
    with duckdb.connect(str(estado.base), read_only=True, config={"enable_external_access": False}):
        segunda = asegurar(copia_datos, tmp_path)
    assert not segunda.reconstruida


def test_leer_hashes_de_una_ruta_que_no_existe_da_none(tmp_path):
    assert construir.leer_hashes(tmp_path / "no_existe.duckdb") is None


def test_dos_sesiones_a_la_vez_reconstruyen_una_sola_vez(copia_datos, tmp_path, monkeypatch):
    asegurar(copia_datos, tmp_path)
    alterar_como_copia_a(copia_datos)
    construcciones = []
    original = construir.construir

    def contar(*argumentos, **opciones):
        construcciones.append(1)
        return original(*argumentos, **opciones)

    monkeypatch.setattr(construir, "construir", contar)
    resultados, errores = [], []
    salida = threading.Barrier(2)

    def sesion():
        try:
            salida.wait()
            resultados.append(asegurar(copia_datos, tmp_path))
        except Exception as error:  # noqa: BLE001 - se reporta en el hilo principal
            errores.append(error)

    hilos = [threading.Thread(target=sesion) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=60)

    assert errores == []
    assert len(construcciones) == 1
    assert sorted(estado.reconstruida for estado in resultados) == [False, True]
    assert resultados[0].hash_fuentes == resultados[1].hash_fuentes


# --- Error con tipo si falta un archivo (K04, K21) -------------------------------------------------------


def test_copia_b_un_csv_renombrado_da_un_error_con_tipo_aunque_haya_una_base_vieja(copia_datos, tmp_path):
    asegurar(copia_datos, tmp_path)
    base = tmp_path / "tienda.duckdb"
    antes = sha256_de(base)
    (copia_datos / "ventas.csv").rename(copia_datos / "ventas_2026.csv")

    with pytest.raises(construir.ArchivoFaltante) as capturado:
        asegurar(copia_datos, tmp_path)

    # El error nombra el archivo y es un ErrorConstruccion: la capa que responde lo convierte en
    # "no pude consultar ventas.csv", sin cifra. Una base vieja no contesta por un archivo que ya no está.
    assert capturado.value.archivos == ("ventas.csv",)
    assert "ventas.csv" in str(capturado.value)
    assert isinstance(capturado.value, construir.ErrorConstruccion)
    assert sha256_de(base) == antes
    assert archivos_de_la_base(tmp_path) == ["tienda.duckdb"]


@pytest.mark.parametrize("archivo", [fuente.archivo for fuente in construir.FUENTES.values()])
def test_cada_csv_que_falta_se_nombra_en_el_error(copia_datos, tmp_path, archivo):
    asegurar(copia_datos, tmp_path)
    (copia_datos / archivo).unlink()
    with pytest.raises(construir.ArchivoFaltante) as capturado:
        asegurar(copia_datos, tmp_path)
    assert capturado.value.archivos == (archivo,)


def test_si_faltan_dos_archivos_el_error_los_nombra_a_los_dos(copia_datos, tmp_path):
    (copia_datos / "ventas.csv").unlink()
    (copia_datos / "inventario.csv").unlink()
    with pytest.raises(construir.ArchivoFaltante) as capturado:
        asegurar(copia_datos, tmp_path)
    assert set(capturado.value.archivos) == {"ventas.csv", "inventario.csv"}
    assert not (tmp_path / "tienda.duckdb").exists()


def test_una_carpeta_de_datos_que_no_existe_nombra_los_5_csv(tmp_path):
    with pytest.raises(construir.ArchivoFaltante) as capturado:
        asegurar(tmp_path / "no_existe", tmp_path)
    assert len(capturado.value.archivos) == 5


@pytest.mark.parametrize("llamar", ["asegurar_base", "construir"])
def test_un_pdf_que_falta_da_un_error_con_tipo(llamar, copia_datos, copia_pdf, tmp_path):
    (copia_pdf / "procedimiento_devoluciones.pdf").unlink()
    with pytest.raises(construir.ArchivoFaltante) as capturado:
        getattr(construir, llamar)(copia_datos, tmp_path / "tienda.duckdb", dir_pdf=copia_pdf, indice_texto=False)
    assert capturado.value.archivos == ("procedimiento_devoluciones.pdf",)
    assert "procedimiento_devoluciones.pdf" in str(capturado.value)
    assert not (tmp_path / "tienda.duckdb").exists()


def test_un_pdf_que_falta_tampoco_se_cubre_con_una_base_vieja(copia_datos, copia_pdf, tmp_path):
    asegurar(copia_datos, tmp_path)
    base = tmp_path / "tienda.duckdb"
    antes = sha256_de(base)
    (copia_pdf / "politica_mermas_y_caducidad.pdf").unlink()
    with pytest.raises(construir.ArchivoFaltante) as capturado:
        asegurar(copia_datos, tmp_path, dir_pdf=copia_pdf, politicas=True)
    assert capturado.value.archivos == ("politica_mermas_y_caducidad.pdf",)
    assert sha256_de(base) == antes


def test_sin_politicas_los_pdf_no_hacen_falta(copia_datos, tmp_path):
    estado = asegurar(copia_datos, tmp_path, dir_pdf=tmp_path / "no_hay_pdf")
    assert estado.reconstruida


def test_los_pdf_requeridos_son_los_de_politicas_md_y_los_de_politicas():
    declarados = {fragmentos_politicas.separar_encabezado(md.read_text(encoding="utf-8"))[0]["archivo"] for md in DIR_MD.glob("*.md")}
    assert set(construir.PDF_POLITICAS) == declarados == {p.name for p in DIR_PDF.glob("*.pdf")}


# --- La línea de comandos --------------------------------------------------------------------------------


def test_la_linea_de_comandos_reporta_el_hash_de_las_fuentes(copia_datos, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(construir, "crear_indice_texto", lambda *argumentos: None)
    codigo = construir.main(["--datos", str(copia_datos), "--base", str(tmp_path / "cli.duckdb")])
    salida = capsys.readouterr().out
    assert codigo == 0
    assert f"Fuentes: {ARCHIVOS_DE_LAS_FUENTES} archivos, {BYTES_DE_LAS_FUENTES:,} bytes" in salida
    assert f"Hash de las fuentes (sha256): {hash_esperado(copia_datos)}" in salida


def test_la_linea_de_comandos_reporta_un_pdf_que_falta(copia_datos, copia_pdf, tmp_path, capsys):
    (copia_pdf / "faq_gerentes_de_tienda.pdf").unlink()
    codigo = construir.main(["--datos", str(copia_datos), "--base", str(tmp_path / "cli.duckdb"), "--pdf", str(copia_pdf)])
    error = capsys.readouterr().err
    assert codigo == 1
    assert "faq_gerentes_de_tienda.pdf" in error
    assert "Traceback" not in error


# --- Garantías al publicar la base ------------------------------------------------------------------------


def test_si_un_archivo_cambia_durante_la_construccion_la_base_nueva_no_se_publica(copia_datos, tmp_path, monkeypatch):
    anterior = asegurar(copia_datos, tmp_path)
    antes = sha256_de(anterior.base)
    alterar_como_copia_a(copia_datos)
    cargar = construir.cargar_datos

    def cargar_y_luego_cambiar(con, rutas):
        filas = cargar(con, rutas)
        tiendas = copia_datos / "tiendas.csv"
        tiendas.write_bytes(tiendas.read_bytes() + b"\n")
        return filas

    monkeypatch.setattr(construir, "cargar_datos", cargar_y_luego_cambiar)
    with pytest.raises(construir.FuentesCambiaron) as capturado:
        asegurar(copia_datos, tmp_path)

    assert capturado.value.archivos == ("datos/tiendas.csv",)
    assert isinstance(capturado.value, construir.ErrorConstruccion)
    assert sha256_de(anterior.base) == antes
    assert archivos_de_la_base(tmp_path) == ["tienda.duckdb"]


def test_si_la_base_esta_ocupada_se_reintenta_el_reemplazo(copia_datos, tmp_path, monkeypatch):
    # Windows no reemplaza un archivo que otra conexión tiene abierto; en cuanto se libera, sigue.
    monkeypatch.setattr(construir, "ESPERA_REEMPLAZO", 0)
    original = os.replace
    intentos = []

    def ocupada_dos_veces(origen, destino):
        intentos.append(1)
        if len(intentos) <= 2:
            raise PermissionError(13, "Acceso denegado")
        return original(origen, destino)

    monkeypatch.setattr(os, "replace", ocupada_dos_veces)
    estado = asegurar(copia_datos, tmp_path)
    assert estado.reconstruida
    assert len(intentos) == 3
    assert archivos_de_la_base(tmp_path) == ["tienda.duckdb"]


def test_si_la_base_sigue_ocupada_se_avisa_con_un_error_con_tipo_y_la_anterior_queda(copia_datos, tmp_path, monkeypatch):
    anterior = asegurar(copia_datos, tmp_path)
    antes = sha256_de(anterior.base)
    alterar_como_copia_a(copia_datos)
    monkeypatch.setattr(construir, "ESPERA_REEMPLAZO", 0)

    def siempre_ocupada(origen, destino):
        raise PermissionError(13, "Acceso denegado")

    monkeypatch.setattr(os, "replace", siempre_ocupada)

    with pytest.raises(construir.BaseEnUso) as capturado:
        asegurar(copia_datos, tmp_path)

    assert isinstance(capturado.value, construir.ErrorConstruccion)
    assert "tienda.duckdb" in str(capturado.value)
    assert sha256_de(anterior.base) == antes
    assert archivos_de_la_base(tmp_path) == ["tienda.duckdb"]


@pytest.mark.skipif(sys.platform != "win32", reason="solo Windows rechaza reemplazar un archivo abierto")
def test_en_windows_una_conexion_abierta_a_la_base_da_base_en_uso(copia_datos, tmp_path, monkeypatch):
    monkeypatch.setattr(construir, "ESPERA_REEMPLAZO", 0)
    monkeypatch.setattr(construir, "INTENTOS_REEMPLAZO", 2)
    anterior = asegurar(copia_datos, tmp_path)
    alterar_como_copia_a(copia_datos)

    lector = duckdb.connect(str(anterior.base), read_only=True)
    try:
        with pytest.raises(construir.BaseEnUso):
            asegurar(copia_datos, tmp_path)
    finally:
        lector.close()

    despues = asegurar(copia_datos, tmp_path)
    assert despues.reconstruida
    assert venta_neta(despues.base) == P03_COPIA_A
