"""Pruebas del script de respuestas esperadas (T5.2).

Las cifras salen del criterio de aceptación de T5.2 y de la rúbrica (P01, P03, P04, P13, P16,
P19, P29). Las copias alteradas A y C se arman en un directorio temporal a partir de datos/,
sin tocar los originales.
"""

import ast
import contextlib
import csv
import io
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from eval import respuestas_esperadas as esperadas

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "datos"
SCRIPT = RAIZ / "eval" / "respuestas_esperadas.py"
HOY = pd.Timestamp("2026-09-01")


def _leer_csv(ruta):
    with ruta.open(encoding="utf-8", newline="") as archivo:
        lector = csv.DictReader(archivo)
        return lector.fieldnames, list(lector)


def _escribir_csv(ruta, columnas, filas):
    with ruta.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)


def _copiar_datos(destino):
    destino.mkdir()
    for ruta in DATOS.glob("*.csv"):
        shutil.copy2(ruta, destino / ruta.name)
    return destino


@pytest.fixture(scope="module")
def datos():
    return esperadas.cargar(DATOS)


@pytest.fixture
def copia_c(tmp_path):
    """Copia C de la rúbrica: Bolillo en T01 de 72 a 300 y sku_sustituto de SKU-1001 vacío."""
    carpeta = _copiar_datos(tmp_path / "copia_c")

    columnas, catalogo = _leer_csv(carpeta / "catalogo_productos.csv")
    bolillo = [fila["sku"] for fila in catalogo if fila["nombre"].startswith("Bolillo")]
    assert len(bolillo) == 1
    for fila in catalogo:
        if fila["sku"] == "SKU-1001":
            fila["sku_sustituto"] = ""
    _escribir_csv(carpeta / "catalogo_productos.csv", columnas, catalogo)

    columnas, inventario = _leer_csv(carpeta / "inventario.csv")
    cambiadas = 0
    for fila in inventario:
        if fila["tienda_id"] == "T01" and fila["sku"] == bolillo[0]:
            assert fila["existencia"] == "72"
            assert fila["punto_reorden"] == "260"
            fila["existencia"] = "300"
            cambiadas += 1
    assert cambiadas == 1
    _escribir_csv(carpeta / "inventario.csv", columnas, inventario)
    return carpeta


@pytest.fixture
def copia_a(tmp_path):
    """Copia A de la rúbrica: venta_neta_mxn de T02 del 2026-07-01 multiplicada por 2."""
    carpeta = _copiar_datos(tmp_path / "copia_a")
    columnas, ventas = _leer_csv(carpeta / "ventas.csv")
    for fila in ventas:
        if fila["tienda_id"] == "T02" and fila["fecha"] == "2026-07-01":
            fila["venta_neta_mxn"] = f"{Decimal(fila['venta_neta_mxn']) * 2:.2f}"
    _escribir_csv(carpeta / "ventas.csv", columnas, ventas)
    return carpeta


def test_venta_neta_del_trimestre(datos):
    venta = esperadas.venta_neta_trimestre(datos)
    assert venta["neta"] == Decimal("17228300.26")
    assert venta["sin_devoluciones"] == Decimal("17272235.76")
    assert venta["devoluciones"] == Decimal("43935.50")


def test_leche_entera_de_julio_suma_la_presentacion_sustituta(datos):
    leche = esperadas.venta_producto(datos, "SKU-1001", mes=7)
    assert leche["unidades"] == 3336
    assert leche["venta"] == Decimal("91773.10")
    assert leche["unidades_sin_devoluciones"] == 3337
    assert leche["venta_sin_devoluciones"] == Decimal("91801.60")
    assert leche["por_sku"] == {
        "SKU-1001": {"unidades": 1513, "venta": Decimal("41196.58")},
        "SKU-1121": {"unidades": 1823, "venta": Decimal("50576.52")},
    }


def test_cumplimiento_de_la_mesa_en_horas_naturales(datos):
    cumplimiento = esperadas.cumplimiento_tickets(datos)
    assert cumplimiento["cerrados"] == 268
    assert cumplimiento["cumplidos"] == 86


def test_reorden_al_corte(datos):
    reorden = esperadas.bajo_punto_reorden(datos)
    assert reorden["total"] == 17
    assert reorden["por_tienda"] == {"T01": 3, "T02": 3, "T03": 5, "T04": 6}


def test_con_la_copia_c_la_esperada_de_reorden_pasa_a_16(copia_c):
    datos_c = esperadas.cargar(copia_c)
    assert esperadas.bajo_punto_reorden(datos_c)["total"] == 16


def test_con_la_copia_c_la_leche_ya_no_se_liga_por_sku_sustituto(copia_c):
    datos_c = esperadas.cargar(copia_c)
    leche = esperadas.venta_producto(datos_c, "SKU-1001", mes=7)
    assert leche["unidades"] == 1513
    assert list(leche["por_sku"]) == ["SKU-1001"]


def test_con_la_copia_a_la_venta_del_trimestre_cambia(copia_a):
    datos_a = esperadas.cargar(copia_a)
    assert esperadas.venta_neta_trimestre(datos_a)["neta"] == Decimal("17284355.44")


def test_tickets_sin_cerrar_con_antiguedad_contra_la_fecha_de_referencia(datos):
    abiertos = esperadas.tickets_sin_cerrar(datos, HOY)
    assert len(abiertos) == 17
    assert abiertos["estado"].value_counts().to_dict() == {"Abierto": 9, "En proceso": 8}
    cumbres = abiertos[abiertos["tienda_id"] == "T02"]
    assert len(cumbres) == 8
    assert cumbres.iloc[0]["ticket_id"] == 1258
    assert cumbres.iloc[0]["dias_abierto"] == pytest.approx(9.65, abs=0.005)
    assert cumbres.iloc[1]["ticket_id"] == 1261
    assert cumbres.iloc[1]["dias_abierto"] == pytest.approx(9.32, abs=0.005)


def test_la_fecha_de_referencia_es_un_parametro(datos):
    otro_dia = esperadas.tickets_sin_cerrar(datos, pd.Timestamp("2026-09-02"))
    assert otro_dia.iloc[0]["dias_abierto"] == pytest.approx(10.65, abs=0.005)


def test_lacteos_a_retirar_y_rebajar_al_corte(datos):
    retirar, rebajar = esperadas.retiros_y_rebajas(datos, HOY)
    lacteos_retiro = retirar[retirar["categoria"] == "Lácteos"]
    lacteos_rebaja = rebajar[rebajar["categoria"] == "Lácteos"]
    assert len(lacteos_retiro) == 2
    assert set(lacteos_retiro["tienda_id"]) == {"T03", "T04"}
    assert sorted(lacteos_rebaja["descuento_pct"].tolist()) == [15, 15, 25, 25, 35]


def test_con_el_31_de_agosto_los_retiros_salen_como_rebaja(datos):
    retirar, rebajar = esperadas.retiros_y_rebajas(datos, pd.Timestamp("2026-08-31"))
    assert len(retirar[retirar["categoria"] == "Lácteos"]) == 0
    assert len(rebajar[rebajar["categoria"] == "Lácteos"]) == 5


def test_el_script_no_importa_el_sistema():
    arbol = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    modulos = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            modulos.extend(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            modulos.append(nodo.module or "")
    assert modulos
    assert not [m for m in modulos if m.split(".")[0] == "agente"]


def test_importar_el_script_no_carga_modulos_del_sistema():
    codigo = (
        "import sys, eval.respuestas_esperadas; "
        "cargados = [m for m in sys.modules if m.split('.')[0] == 'agente']; "
        "assert not cargados, cargados"
    )
    salida = subprocess.run(
        [sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, timeout=120
    )
    assert salida.returncode == 0, salida.stderr


def test_el_comando_recibe_la_carpeta_y_la_fecha(copia_c):
    salida = subprocess.run(
        [sys.executable, "-m", "eval.respuestas_esperadas", str(copia_c), "--hoy", "2026-09-01"],
        cwd=RAIZ,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert salida.returncode == 0, salida.stderr
    assert "17,228,300.26" in salida.stdout
    assert "total: 16" in salida.stdout


def test_el_comando_rechaza_una_fecha_invalida():
    salida = subprocess.run(
        [sys.executable, "-m", "eval.respuestas_esperadas", str(DATOS), "--hoy", "01/09/2026"],
        cwd=RAIZ,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert salida.returncode == 2
    assert "AAAA-MM-DD" in salida.stderr


def test_el_comando_avisa_si_falta_un_archivo(tmp_path):
    salida = subprocess.run(
        [sys.executable, "-m", "eval.respuestas_esperadas", str(tmp_path)],
        cwd=RAIZ,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert salida.returncode == 2
    assert "ventas.csv" in salida.stderr


# Secciones que agregó T5.1 para el banco de preguntas. Los valores se calcularon también con el
# módulo csv de la biblioteca estándar y con Decimal, sin pandas y sin el sistema.


@pytest.fixture(scope="module")
def reporte():
    salida = subprocess.run(
        [sys.executable, "-m", "eval.respuestas_esperadas", str(DATOS), "--hoy", "2026-09-01"],
        cwd=RAIZ,
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert salida.returncode == 0, salida.stderr
    return salida.stdout


def _seccion_de(reporte, identificador):
    for bloque in reporte.split("\n== ")[1:]:
        if bloque.startswith(f"{identificador} "):
            return bloque
    raise AssertionError(f"el reporte no trae la sección {identificador}")


@pytest.mark.parametrize(
    ("identificador", "textos"),
    [
        ("PT-24", ["380"]),
        ("N-01", ["786,418.46"]),
        ("N-02", ["62,043.50"]),
        ("N-03", ["181"]),
        ("N-04", ["352,887.81"]),
        ("N-05", ["total: 0"]),
        ("N-06", ["519.23", "1,973,086.37", "3,800"]),
        ("N-07", ["248", "249"]),
        ("N-08", ["348,348.43"]),
        ("N-09", ["786,418.46", "821,337.88", "4.44"]),
        ("N-17", ["21", "61"]),
        ("N-18", ["SKU-1032", "perecedero: False", "Carnes"]),
        ("N-19", ["T01: 0", "T02: 0", "T03: 1", "T04: 1"]),
        ("N-20", ["total: 0"]),
        ("N-21", ["total: 0"]),
        ("N-22", ["total: 0"]),
        ("N-23", ["1274", "4.38"]),
        ("N-24", ["2,163,062.36", "632.98"]),
        ("N-25", ["SKU-1001", "465", "SKU-1002", "279", "SKU-1003", "222"]),
    ],
)
def test_las_secciones_de_t51_dan_lo_calculado_aparte(reporte, identificador, textos):
    seccion = _seccion_de(reporte, identificador)
    for texto in textos:
        assert texto in seccion, f"{identificador}: falta {texto}"


def test_las_unidades_devueltas_de_pt12_separan_kg_y_piezas(reporte):
    """El handoff suma 61 unidades: 12 kg y 49 piezas, que la herramienta no mezcla."""
    assert "12 kg y 49 piezas" in reporte


def test_las_secciones_de_t51_responden_a_los_datos(copia_a):
    """Con la copia A cambia la venta de julio de Cumbres: la N-04 no es un valor fijo."""
    datos_a = esperadas.cargar(copia_a)
    original = esperadas.cargar(DATOS)
    assert esperadas.venta_de(datos_a, "T02", mes=7, categoria="Abarrotes") > esperadas.venta_de(
        original, "T02", mes=7, categoria="Abarrotes"
    )


# Secciones que agregó T5.1b. Los valores se calcularon también con el módulo csv y con Decimal, sin
# pandas y sin el sistema.


@pytest.mark.parametrize(
    ("identificador", "textos"),
    [
        ("PT-26", ["ninguna"]),
        ("PT-27", ["2026-08-24 a 2026-08-30: 426,693.49", "2026-08-25 a 2026-08-31: 439,962.33"]),
        ("PT-28", ["última fecha: 2026-08-31", "filas de septiembre: 0"]),
        ("PT-36", ["644 litros", "17,560.73", "SKU-1001: 276", "SKU-1121: 368"]),
        ("PT-37", ["SKU-1013", "1,330 piezas"]),
        ("PT-38", ["filas de SKU-1001 en inventario: 0", "baja: 2026-07-14", "SKU-1121 en T02: 593"]),
        ("PT-40", ["T01", "Mantenimiento", "Alta", "Cerrado"]),
        ("PT-41", ["T01: 85,895.13", "T02: 90,232.38", "T03: 44,010.28", "T04: 37,210.39", "24,531.14", "1186", "1188"]),
        ("PT-42", ["ticket 1118", "40% del piso", "Alta"]),
        ("PT-43", ["1022", "1060", "1097", "1151", "1175", "1277"]),
        ("PT-44", ["239,509.31", "3,800", "63.03"]),
        ("PT-45", ["literal: 2", "sin acentos: 5"]),
        ("PT-46", ["T01: 2,153,571.23", "T02: 1,810,013.00", "T03: 927,240.99"]),
        ("PT-47", ["SKU-1077", "621", "SKU-1078", "670", "SKU-1079", "338", "SKU-1080", "151"]),
        ("PT-48", ["T02", "Abierto"]),
        ("PT-49", ["T01: 25 de 67", "T02: 20 de 64", "T03: 26 de 86", "T04: 15 de 51"]),
        ("CV-01", ["junio: 894,581.96", "agosto: 949,469.00", "Carnes en agosto: 156,789.31"]),
        ("CV-04", ["T04 en agosto: 833,131.19", "total de junio: 5,597,388.24"]),
        ("CV-05", ["SKU-1014 en T04: 212", "tickets de T03: 90", "T02 a rebajar: 8", "T04 por m2 en agosto: 617.13", "T01 Crítica con cierre: 1"]),
    ],
)
def test_las_secciones_de_t51b_dan_lo_calculado_aparte(reporte, identificador, textos):
    seccion = _seccion_de(reporte, identificador)
    for texto in textos:
        assert texto in seccion, f"{identificador}: falta {texto}"


def test_la_seccion_del_ticket_1043_no_trae_su_descripcion(reporte, datos):
    descripcion = datos.tickets.loc[datos.tickets["ticket_id"] == 1043, "descripcion"].iloc[0]
    assert descripcion not in reporte
    assert descripcion[:20] not in _seccion_de(reporte, "PT-40")


def test_las_secciones_de_t51b_responden_a_los_datos(copia_a):
    """Con la copia A sube Cumbres el 1 de julio: la PT-46 cambia y las otras tiendas no."""
    salida_a = io.StringIO()
    with contextlib.redirect_stdout(salida_a):
        esperadas.imprimir_reporte(esperadas.cargar(copia_a), HOY)
    seccion = _seccion_de(salida_a.getvalue(), "PT-46")
    assert "T01: 2,153,571.23" in seccion
    assert "T02: 1,810,013.00" not in seccion


def test_con_la_copia_c_la_pt36_ya_no_suma_la_sustituta(copia_c):
    """Sin sku_sustituto, la leche entera de Anáhuac en julio se queda en los 276 litros de SKU-1001."""
    salida_c = io.StringIO()
    with contextlib.redirect_stdout(salida_c):
        esperadas.imprimir_reporte(esperadas.cargar(copia_c), HOY)
    assert "276 litros" in _seccion_de(salida_c.getvalue(), "PT-36")
    assert "644 litros" not in _seccion_de(salida_c.getvalue(), "PT-36")
