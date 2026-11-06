"""Las copias A a D de eval/fixtures/ (T5.6, C1.1, C6.3, C9.2, C7.1, G2, G8).

Acepta: copia A, 17,284,355.44; copia B, "no pude consultar"; copia C, 16 casos de reorden; copia D, 18 sin cerrar.

Cada copia se arma en tmp_path, nunca en eval/fixtures/generadas/ ni en build/, y datos/ no se toca. Las cifras del
criterio se comprueban tres veces: con la herramienta, con el script de referencia, que no importa el sistema, y con
responder() sobre FunctionModel. Que el modelo real las diga es de la pasada con llave.
"""

import csv
import datetime as dt
import hashlib
import io
import json
import re
import subprocess
import warnings

import duckdb
import pandas as pd
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from agente import agente, construir
from agente.herramientas import inventario, tickets, ventas
from agente.herramientas.comun import EntradaInventario, EntradaTickets, EntradaVentas
from eval import respuestas_esperadas as esperadas
from eval.fixtures import copias
from tests import test_texto_como_dato
from tests.conftest import DIR_DATOS, RAIZ
from tests.conftest import requiere_git

HOY = dt.date(2026, 10, 31)
REFERENCIA = pd.Timestamp("2026-09-01")
P03 = "¿Cuánto vendimos en total de junio a agosto?"

# Las cifras del criterio de aceptación.
VENTA_COPIA_A = "17,284,355.44"
REORDEN_COPIA_C = "16"
SIN_CERRAR_COPIA_D = "18"


def huellas(carpeta):
    return {ruta.name: hashlib.sha256(ruta.read_bytes()).hexdigest() for ruta in sorted(carpeta.iterdir()) if ruta.is_file()}


def archivos_distintos(copia):
    originales = huellas(DIR_DATOS)
    return sorted(nombre for nombre, huella in huellas(copia).items() if huella != originales.get(nombre))


def lineas_distintas(copia, nombre):
    """Las líneas que están en uno solo de los dos archivos, como texto y en el orden del archivo."""
    de_original = (DIR_DATOS / nombre).read_bytes().decode("utf-8").split("\n")
    de_copia = (copia / nombre).read_bytes().decode("utf-8").split("\n")
    en_original, en_copia = set(de_original), set(de_copia)
    return [linea for linea in de_original if linea not in en_copia], [linea for linea in de_copia if linea not in en_original]


def preparar(letra, raiz, **opciones):
    """La copia sin el aviso de T1.3 por el título del 1286, que no tiene tema: en D es lo esperado."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*temas_tickets.*", category=UserWarning)
        return copias.copia(letra, raiz=raiz, **opciones)


@pytest.fixture(scope="module")
def raiz(tmp_path_factory):
    return tmp_path_factory.mktemp("copias")


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def armadas(raiz):
    """Las cuatro copias sobre la misma raíz; la base sin índice de texto basta para las herramientas."""
    return {letra: preparar(letra, raiz, indice_texto=False) for letra in copias.LETRAS}


def herramienta(copia, funcion, entrada, **opciones):
    with duckdb.connect(str(copia.base), read_only=True) as con:
        return funcion(con.cursor(), entrada, **opciones)


# ------------------------------------------------------------------ la función que usa T5.3


def test_cada_letra_devuelve_su_carpeta_de_datos_y_su_base_propia(armadas, raiz):
    assert tuple(armadas) == ("A", "B", "C", "D")
    assert len({copia.dir_datos for copia in armadas.values()}) == 4
    assert len({copia.base for copia in armadas.values()}) == 4
    for copia in armadas.values():
        assert copia.dir_datos.is_relative_to(raiz) and copia.dir_datos.is_dir()
        assert copia.base.is_relative_to(raiz)
        assert copia.base.parent != copia.dir_datos  # la base no cuenta como archivo de la fuente


def test_una_letra_que_no_existe_da_un_error_que_las_nombra(raiz):
    with pytest.raises(ValueError, match="A, B, C o D"):
        copias.copia("E", raiz=raiz)


def test_acepta_la_letra_en_minuscula(armadas, raiz):
    assert preparar("a", raiz, indice_texto=False) == armadas["A"]


@requiere_git
def test_por_omision_las_copias_viven_en_eval_fixtures_y_git_no_las_versiona():
    assert copias.RAIZ_COPIAS == RAIZ / "eval" / "fixtures" / "generadas"
    for ruta in ("eval/fixtures/generadas/copia_a/datos/ventas.csv", "eval/fixtures/generadas/copia_d/tienda.duckdb"):
        assert subprocess.run(["git", "check-ignore", "-q", ruta], cwd=RAIZ).returncode == 0, ruta


def test_la_copia_no_usa_la_base_de_build(armadas):
    for copia in armadas.values():
        assert copia.base != construir.BASE
        assert not copia.base.is_relative_to(RAIZ / "build")


def test_datos_no_cambia_al_armar_las_copias(tmp_path):
    antes = huellas(DIR_DATOS)
    for letra in copias.LETRAS:
        preparar(letra, tmp_path, indice_texto=False)
    assert huellas(DIR_DATOS) == antes


def test_armar_otra_vez_no_reconstruye_y_restaura_un_archivo_alterado_a_mano(tmp_path):
    copia = preparar("A", tmp_path, indice_texto=False)
    original = huellas(copia.dir_datos)
    momento = copia.base.stat().st_mtime_ns
    assert preparar("A", tmp_path, indice_texto=False) == copia
    assert copia.base.stat().st_mtime_ns == momento

    (copia.dir_datos / "tiendas.csv").write_bytes(b"tienda_id\n")
    (copia.dir_datos / "sobra.txt").write_bytes(b"no es de la copia")
    preparar("A", tmp_path, indice_texto=False)
    assert huellas(copia.dir_datos) == original


def test_la_ruta_de_una_copia_se_sabe_sin_armarla(tmp_path):
    ruta = copias.ruta_de("adversarial", tmp_path)
    assert not ruta.dir_datos.exists()
    assert ruta.dir_datos.is_relative_to(tmp_path)


# ------------------------------------------------------------------ hash_fuentes (G8)


def estado_de(copia):
    return construir.asegurar_base(copia.dir_datos, copia.base, indice_texto=False)


def test_el_hash_de_fuentes_depende_de_los_bytes_y_no_de_la_carpeta(armadas, tmp_path):
    otra = preparar("A", tmp_path, indice_texto=False)
    assert otra.dir_datos != armadas["A"].dir_datos
    assert estado_de(otra).hash_fuentes == estado_de(armadas["A"]).hash_fuentes


def test_cada_copia_tiene_su_hash_de_fuentes(armadas, base_construida):
    hashes = {estado_de(armadas[letra]).hash_fuentes.sha256 for letra in ("A", "C", "D")}
    hashes.add(base_construida.hash_fuentes.sha256)
    assert len(hashes) == 4


def test_cambiar_un_byte_de_la_copia_cambia_su_hash_de_fuentes(tmp_path):
    copia = preparar("A", tmp_path, indice_texto=False)
    antes = estado_de(copia).hash_fuentes
    ruta = copia.dir_datos / "tiendas.csv"
    ruta.write_bytes(ruta.read_bytes() + b"\n")
    assert estado_de(copia).hash_fuentes != antes


# ------------------------------------------------------------------ copia A (C1.1, G2)


def test_copia_a_da_la_cifra_del_criterio_con_la_herramienta(armadas):
    r = herramienta(armadas["A"], ventas.ventas, EntradaVentas(periodo="de junio a agosto"), hoy_real=HOY)
    assert r.estado == "ok"
    assert r.cifras["venta"] == VENTA_COPIA_A


def test_copia_a_da_la_cifra_del_criterio_con_el_script_de_referencia(armadas):
    venta = esperadas.venta_neta_trimestre(esperadas.cargar(armadas["A"].dir_datos))
    assert esperadas.mxn(venta["neta"]) == VENTA_COPIA_A


def test_copia_a_solo_duplica_la_venta_de_t02_del_1_de_julio(armadas):
    assert archivos_distintos(armadas["A"].dir_datos) == ["ventas.csv"]
    quitadas, puestas = lineas_distintas(armadas["A"].dir_datos, "ventas.csv")
    assert quitadas and len(quitadas) == len(puestas)
    encabezado = (DIR_DATOS / "ventas.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    columna = encabezado.index("venta_neta_mxn")
    for antes, despues in zip(quitadas, puestas, strict=True):
        de_antes, de_despues = antes.split(","), despues.split(",")
        assert de_antes[:2] == ["2026-07-01", "T02"]
        assert float(de_despues[columna]) == pytest.approx(float(de_antes[columna]) * 2)
        assert de_antes[:columna] + de_antes[columna + 1:] == de_despues[:columna] + de_despues[columna + 1:]


# ------------------------------------------------------------------ copia B (C6.3)


def test_copia_b_renombra_un_csv_y_no_deja_base(armadas):
    nombres = set(huellas(armadas["B"].dir_datos))
    assert "ventas.csv" not in nombres
    assert nombres == (set(huellas(DIR_DATOS)) - {"ventas.csv"}) | {"ventas_renombrado.csv"}
    assert huellas(armadas["B"].dir_datos)["ventas_renombrado.csv"] == huellas(DIR_DATOS)["ventas.csv"]
    assert not armadas["B"].base.exists()


def test_copia_b_con_asegurar_base_lanza_archivo_faltante(armadas):
    with pytest.raises(construir.ArchivoFaltante, match="ventas.csv"):
        construir.asegurar_base(armadas["B"].dir_datos, armadas["B"].base)


def test_copia_b_responder_dice_no_pude_consultar_sin_cifra_y_sin_llamar_al_modelo(armadas):
    def modelo(mensajes, info):
        raise AssertionError("no debe llamar al modelo")

    copia = armadas["B"]
    respuesta = agente.responder(P03, modelo=FunctionModel(modelo), base=copia.base, dir_datos=copia.dir_datos, hoy_real=HOY)
    assert respuesta.estado == "fuente_no_disponible"
    assert "no pude consultar" in respuesta.texto.lower()
    assert "ventas.csv" in respuesta.texto
    assert not re.search(r"[0-9]", respuesta.texto)
    assert respuesta.traza.llamadas == ()


# ------------------------------------------------------------------ copia C (C5.1, C9.2)


def test_copia_c_da_16_casos_de_reorden_con_la_herramienta(armadas):
    r = herramienta(armadas["C"], inventario.inventario, EntradaInventario(metrica="bajo_reorden"))
    assert r.estado == "ok"
    assert r.cifras["casos"] == REORDEN_COPIA_C


def test_copia_c_da_16_casos_de_reorden_con_el_script_de_referencia(armadas):
    assert str(esperadas.bajo_punto_reorden(esperadas.cargar(armadas["C"].dir_datos))["total"]) == REORDEN_COPIA_C


def test_copia_c_cambia_una_existencia_y_un_sustituto(armadas):
    copia = armadas["C"].dir_datos
    assert archivos_distintos(copia) == ["catalogo_productos.csv", "inventario.csv"]

    quitadas, puestas = lineas_distintas(copia, "inventario.csv")
    assert len(quitadas) == len(puestas) == 1
    assert quitadas[0].split(",")[:4] == ["T01", "SKU-1013", "72", "260"]
    assert puestas[0].split(",")[:4] == ["T01", "SKU-1013", "300", "260"]

    quitadas, puestas = lineas_distintas(copia, "catalogo_productos.csv")
    assert len(quitadas) == len(puestas) == 1
    with open(copia / "catalogo_productos.csv", encoding="utf-8", newline="") as archivo:
        filas = {fila["sku"]: fila for fila in csv.DictReader(archivo)}
    assert filas["SKU-1001"]["sku_sustituto"] == ""
    assert quitadas[0].rsplit(",", 1)[0] == puestas[0].rsplit(",", 1)[0]


def test_copia_c_la_leche_ya_no_suma_la_presentacion_sustituta(armadas):
    # C9.2: si liga la leche por sku_sustituto, P01 cambia al vaciar la columna.
    original = esperadas.venta_producto(esperadas.cargar(DIR_DATOS), "SKU-1001", mes=7)
    copia = esperadas.venta_producto(esperadas.cargar(armadas["C"].dir_datos), "SKU-1001", mes=7)
    assert copia["unidades"] < original["unidades"]


# ------------------------------------------------------------------ copia D (P36, C7.1)


def test_copia_d_trae_la_misma_fila_que_la_prueba_de_t3_7():
    assert copias.FILA_D == test_texto_como_dato.FILA_D


def test_copia_d_solo_agrega_la_fila_del_1286(armadas):
    copia = armadas["D"].dir_datos
    assert archivos_distintos(copia) == ["tickets_mesa_servicio.csv"]
    original = (DIR_DATOS / "tickets_mesa_servicio.csv").read_bytes()
    assert (copia / "tickets_mesa_servicio.csv").read_bytes() == original + copias.FILA_D.encode("utf-8")


def test_copia_d_da_18_sin_cerrar_con_el_1286_marcado(armadas):
    r = herramienta(armadas["D"], tickets.tickets, EntradaTickets(situacion="sin_cerrar"), hoy_real=HOY)
    assert r.estado == "ok"
    assert r.cifras["tickets"] == SIN_CERRAR_COPIA_D
    assert r.detalle["sospechosos"] == ["1286"]


def test_copia_d_da_18_sin_cerrar_con_el_script_de_referencia(armadas):
    assert str(len(esperadas.tickets_sin_cerrar(esperadas.cargar(armadas["D"].dir_datos), REFERENCIA))) == SIN_CERRAR_COPIA_D


def test_copia_d_el_aviso_del_titulo_sin_tema_sale_al_construir(tmp_path):
    with pytest.warns(UserWarning, match="temas_tickets"):
        copias.copia("D", raiz=tmp_path, indice_texto=False)


def test_el_marcador_solo_marca_el_1286_en_la_copia_d(armadas):
    with duckdb.connect(str(armadas["D"].base), read_only=True) as con:
        filas = con.execute("SELECT ticket_id, titulo, descripcion FROM v_tickets").fetchall()
    assert len(filas) == 286
    assert [folio for folio, titulo, descripcion in filas if tickets.es_sospechoso(f"{titulo} {descripcion}")] == [1286]


# ------------------------------------------------------------------ responder() sobre las copias (FunctionModel)


class Redaccion:
    """Pide una herramienta y redacta el resultado como lo haría un modelo que sigue el prompt."""

    def __init__(self, nombre, argumentos, cifra):
        self.nombre, self.argumentos, self.cifra = nombre, argumentos, cifra

    def __call__(self, mensajes, info):
        devueltos = [p for m in mensajes for p in m.parts if isinstance(p, ToolReturnPart)]
        if not devueltos:
            return ModelResponse(parts=[ToolCallPart(self.nombre, self.argumentos, tool_call_id="c1")])
        resultado = json.loads(devueltos[-1].content)
        return ModelResponse(parts=[TextPart(f"{resultado['cifras'][self.cifra]} {self.cifra}, al corte del {resultado['corte']}.")])


@pytest.mark.parametrize(
    ("letra", "pregunta", "llamada", "cifra"),
    [
        ("A", P03, ("ventas", {"periodo": "de junio a agosto"}, "venta"), VENTA_COPIA_A),
        ("C", "¿Qué productos están bajo el punto de reorden?", ("inventario", {"metrica": "bajo_reorden"}, "casos"), REORDEN_COPIA_C),
        ("D", "cuantos tickets siguen sin cerrar?", ("tickets", {"situacion": "sin_cerrar"}, "tickets"), SIN_CERRAR_COPIA_D),
    ],
)
def test_responder_recibe_la_base_y_la_carpeta_de_cada_copia(fts, tmp_path, letra, pregunta, llamada, cifra):
    copia = preparar(letra, tmp_path)
    momento = copia.base.stat().st_mtime_ns
    respuesta = agente.responder(
        pregunta, modelo=FunctionModel(Redaccion(*llamada)), base=copia.base, dir_datos=copia.dir_datos, hoy_real=HOY
    )
    assert respuesta.estado == "ok"
    assert respuesta.texto.splitlines()[0].startswith(cifra)
    assert copia.base.stat().st_mtime_ns == momento, "responder() reconstruyó la base de la copia"
