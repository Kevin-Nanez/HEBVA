"""Índice de texto en español y conexión de consultas de solo lectura (T1.6, K09).

Criterio de aceptación de la ficha, y la prueba que lo cubre:

- "Una prueba de escritura falla": test_una_escritura_falla_porque_la_base_esta_en_solo_lectura y
  las que siguen. La conexión abre la base en read_only, con fts cargada, el acceso externo apagado
  y la configuración bloqueada, en ese orden (hallazgo 3 del plan).
- "P10 recupera PRO-SAC-021 §3 y FAQ-OPS-001 P15 entre los primeros 4 fragmentos":
  test_P10_recupera_PRO_SAC_021_3_y_FAQ_OPS_001_P15_entre_los_primeros_4, con el texto de la gerente
  tal como lo escribió. Su control usa el índice por omisión de DuckDB (raíz porter y 571 palabras
  vacías en inglés), que no lo cumple.
- "Dos sesiones que consultan a la vez no dan ResourceInUseException": cada sesión toma su propio
  cursor() (hallazgo 4). La prueba deja una sesión dentro de una llamada que no termina y consulta
  desde otra; el control hace lo mismo con una sola conexión y sí da ResourceInUseException.

La base completa, con el índice, se construye una vez por módulo en un directorio temporal. El
índice necesita la extensión fts, que se descarga la primera vez (T0.5); como las pruebas corren sin
red y sin llaves, esas pruebas se saltan si la extensión no está instalada. Cada prueba de conexión
trabaja sobre su propia copia del archivo: DuckDB comparte el motor entre las conexiones del proceso
a un mismo archivo, y una copia asegura que la apertura se prueba desde cero.
"""

import contextlib
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import threading
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from agente import consultas, construir

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATOS = RAIZ / "datos"
PALABRAS_VACIAS = RAIZ / "config" / construir.ARCHIVO_PALABRAS_VACIAS

# La pregunta de la rúbrica (P10), con las faltas de la gerente: "q" y sin acentos ni signos de apertura.
P10 = "un cliente trae una licuadora q compro hace 3 semanas, se la acepto?"

# Palabras funcionales comunes del español: si una falta, el índice las cuenta como contenido.
PALABRAS_FUNCIONALES = (
    "de la que el en y a los se del las un por con no una su para es al lo como mas pero sus le ya o "
    "este si sin sobre entre cuando muy hasta desde son hay tambien ni ser todo esta fue"
).split()
# Con las que escribe la gerente en el chat (P07, P08, P10, P59, P61 usan q y pa).
ABREVIATURAS_DEL_CHAT = ["q", "pa"]
# Palabras que cargan una regla o que las preguntas del set usan para buscar: no pueden ser vacías.
PALABRAS_DE_CONTENIDO = (
    "siempre nunca solo unicamente excepcion ticket devolucion caja dias horas dos gerente cliente "
    "tienda producto venta merma caducidad lacteos pan"
).split()

# Preguntas de políticas del set que el texto crudo ya resuelve con BM25. Cada una lleva las
# secciones que la rúbrica acepta (C2.2); basta una entre los primeros 4. P06, P59, P60 y P61 no
# entran: lo que preguntan (yogurt, refri, cafetera, Anáhuac) no está en la sección que las responde
# o necesitan dos secciones a la vez, así que dependen de que el modelo reescriba la consulta (T2.7).
PREGUNTAS_DE_POLITICAS = {
    "P07": (
        "a una cajera le faltaron 350 pesos en su corte, q procede?",
        {("MAN-OPS-007", "6"), ("FAQ-OPS-001", "P3")},
    ),
    "P08": (
        "a q hora se abre el anden pa los proveedores?",
        {("MAN-OPS-007", "3"), ("MAN-OPS-007", "2"), ("FAQ-OPS-001", "P2")},
    ),
    "P09": (
        "me piden una devolucion de 2,000 pesos, la puede autorizar la de servicio a clientes?",
        {("PRO-SAC-021", "9"), ("FAQ-OPS-001", "P17")},
    ),
    "P47": ("que dice la politica del fondo fijo de caja?", {("MAN-OPS-007", "4"), ("FAQ-OPS-001", "P4")}),
    "P58": (
        "le hice el reverso a la tarjeta de debito a un cliente, cuando le cae?",
        {("PRO-SAC-021", "6")},
    ),
}

ESCRITURAS = {
    "INSERT": "INSERT INTO tiendas VALUES ('T99', 'Nueva', 'Ciudad', 'Estado', 'Mi Tienda', 100, DATE '2026-01-01')",
    "UPDATE": "UPDATE ventas SET unidades = 0",
    "DELETE": "DELETE FROM ventas",
    "TRUNCATE": "TRUNCATE ventas",
    "DROP TABLE": "DROP TABLE ventas",
    "CREATE TABLE": "CREATE TABLE copia_de_ventas AS SELECT * FROM ventas",
    "CREATE VIEW": "CREATE VIEW v_nueva AS SELECT 1",
    "ALTER TABLE": "ALTER TABLE tiendas ADD COLUMN extra INTEGER",
    "recrear el índice": "PRAGMA create_fts_index('fragmentos', 'id', 'texto', overwrite = 1)",
}


def sha256_de(ruta: Path) -> str:
    with open(ruta, "rb") as archivo:
        return hashlib.file_digest(archivo, "sha256").hexdigest()


def citas(encontrados) -> list[tuple[str, str]]:
    return [(f.documento, f.seccion) for f in encontrados]


def reemplazar_encima(ruta: Path) -> None:
    """Reemplaza el archivo por una copia suya, como hace construir al publicar la base nueva (T1.7).

    En Windows falla con PermissionError mientras alguna conexión tenga el archivo abierto, aunque sea
    de solo lectura. Renombrarlo no sirve para comprobarlo: con el archivo abierto sí se puede.
    """
    nuevo = ruta.with_name(f"{ruta.stem}-nuevo{ruta.suffix}")
    shutil.copyfile(ruta, nuevo)
    os.replace(nuevo, ruta)


def buscar(abiertas, texto, **opciones):
    with abiertas.sesion() as sesion:
        return consultas.buscar_fragmentos(sesion, texto, **opciones)


@pytest.fixture(scope="module")
def fts():
    """Salta las pruebas que necesitan la extensión fts si no está instalada: sin red no se descarga."""
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")


@pytest.fixture(scope="module")
def base_con_indice(fts, tmp_path_factory):
    """La base completa, con fragmentos de políticas e índice de texto."""
    destino = tmp_path_factory.mktemp("consultas") / "tienda.duckdb"
    return construir.construir(DIR_DATOS, destino)


@pytest.fixture
def base_propia(tmp_path, base_con_indice):
    """Copia de la base para esta prueba: la apertura se prueba desde cero y nada se comparte."""
    copia = tmp_path / "tienda.duckdb"
    shutil.copyfile(base_con_indice.base, copia)
    return copia


@pytest.fixture
def abiertas(base_propia):
    with consultas.abrir_consultas(base_propia) as conexion:
        yield conexion


@contextlib.contextmanager
def ocupar(cursor):
    """Deja el cursor dentro de una llamada que no termina hasta salir del bloque.

    Registra una función que espera un evento y la llama desde otro hilo: mientras esa llamada siga
    abierta, el cursor está ocupado. Así la prueba no depende de tiempos ni de que dos consultas
    coincidan por suerte.
    """
    dentro, soltar = threading.Event(), threading.Event()

    def esperar_a_la_prueba() -> int:
        dentro.set()
        soltar.wait(timeout=30)
        return 1

    cursor.create_function("esperar_a_la_prueba", esperar_a_la_prueba, [], "INTEGER")
    hilo = threading.Thread(target=lambda: cursor.execute("SELECT esperar_a_la_prueba()").fetchall())
    hilo.start()
    try:
        assert dentro.wait(timeout=30), "el cursor no llegó a quedar ocupado"
        yield
    finally:
        soltar.set()
        hilo.join(timeout=30)


# --- Palabras vacías en español (config/palabras_vacias_es.txt) ----------------------------------------


def test_las_palabras_vacias_son_palabras_sueltas_en_minusculas_sin_acentos_y_sin_repetir():
    # El índice quita los acentos y baja a minúsculas el texto y la consulta antes de compararlos con
    # esta lista: una entrada con acento, mayúsculas o signos nunca coincidiría.
    entradas = [p for r in PALABRAS_VACIAS.read_text(encoding="utf-8").splitlines() if not r.lstrip().startswith("#") for p in r.split()]
    assert entradas
    assert [p for p in entradas if not construir.PATRON_PALABRA_VACIA.fullmatch(p)] == []
    assert len(entradas) == len(set(entradas)), "hay palabras repetidas"
    assert sorted(set(entradas)) == sorted(construir.leer_palabras_vacias(PALABRAS_VACIAS))


def test_la_lista_trae_las_palabras_funcionales_comunes_y_las_abreviaturas_del_chat():
    palabras = set(construir.leer_palabras_vacias(PALABRAS_VACIAS))
    assert [p for p in PALABRAS_FUNCIONALES + ABREVIATURAS_DEL_CHAT if p not in palabras] == []


def test_la_lista_no_quita_palabras_que_cargan_una_regla_ni_las_que_las_preguntas_usan_para_buscar():
    palabras = set(construir.leer_palabras_vacias(PALABRAS_VACIAS))
    assert sorted(palabras & set(PALABRAS_DE_CONTENIDO)) == []


@pytest.mark.parametrize(
    ("contenido", "debe_decir"),
    [
        ("# solo comentarios\n\n", "ninguna palabra"),
        ("de la Que\n", "Que"),
        ("de la más\n", "más"),
        ("de la 3\n", "3"),
        ("de la q.\n", "q."),
        ("de la # artículo\n", "#"),
    ],
    ids=["sin palabras", "mayúsculas", "acento", "dígito", "signo", "comentario en el renglón"],
)
def test_una_lista_con_entradas_que_el_indice_nunca_reconoceria_se_rechaza_con_error_con_tipo(tmp_path, contenido, debe_decir):
    ruta = tmp_path / "palabras.txt"
    ruta.write_text(contenido, encoding="utf-8")
    with pytest.raises(construir.PalabrasVaciasInvalidas) as capturado:
        construir.leer_palabras_vacias(ruta)
    assert isinstance(capturado.value, construir.ErrorConstruccion)
    assert debe_decir in str(capturado.value)


def test_si_el_archivo_de_palabras_vacias_falta_el_error_con_tipo_nombra_el_archivo(tmp_path):
    with pytest.raises(construir.PalabrasVaciasInvalidas) as capturado:
        construir.leer_palabras_vacias(tmp_path / construir.ARCHIVO_PALABRAS_VACIAS)
    assert construir.ARCHIVO_PALABRAS_VACIAS in str(capturado.value)


def test_si_el_archivo_de_palabras_vacias_no_esta_en_utf8_el_error_tambien_tiene_tipo(tmp_path):
    ruta = tmp_path / "palabras.txt"
    ruta.write_bytes("de la más".encode("latin-1"))
    with pytest.raises(construir.PalabrasVaciasInvalidas, match="UTF-8"):
        construir.leer_palabras_vacias(ruta)


def test_la_lista_invalida_se_rechaza_antes_de_intentar_descargar_fts(tmp_path, monkeypatch):
    def sin_red(*argumentos, **opciones):
        raise construir.ExtensionNoDisponible("fts", "INSTALL", "IO Error: sin red")

    monkeypatch.setattr(construir, "cargar_fts", sin_red)
    ruta = tmp_path / "palabras.txt"
    ruta.write_text("de más\n", encoding="utf-8")
    with duckdb.connect() as con, pytest.raises(construir.PalabrasVaciasInvalidas):
        construir.crear_indice_texto(con, ruta)


def test_un_archivo_de_palabras_vacias_invalido_detiene_la_construccion_y_deja_intacta_la_base_anterior(
    tmp_path, copia_config, base_con_indice
):
    base = tmp_path / "tienda.duckdb"
    shutil.copyfile(base_con_indice.base, base)
    antes = sha256_de(base)
    (copia_config / construir.ARCHIVO_PALABRAS_VACIAS).write_text("de la más\n", encoding="utf-8")
    with pytest.raises(construir.PalabrasVaciasInvalidas):
        construir.construir(DIR_DATOS, base, politicas=False, dir_config=copia_config)
    assert sha256_de(base) == antes
    assert not base.with_name(f"{base.name}.construyendo").exists()


# --- El índice de texto ---------------------------------------------------------------------------------


def test_el_indice_usa_la_tabla_de_palabras_vacias_en_espanol_y_no_las_571_en_ingles(abiertas):
    esperadas = sorted(set(construir.leer_palabras_vacias(PALABRAS_VACIAS)))
    with abiertas.sesion() as sesion:
        en_el_indice = [f[0] for f in sesion.execute(f"SELECT sw FROM {construir.ESQUEMA_INDICE}.stopwords ORDER BY sw").fetchall()]
        en_la_tabla = [f[0] for f in sesion.execute(f"SELECT sw FROM {construir.TABLA_PALABRAS_VACIAS} ORDER BY sw").fetchall()]
    assert en_el_indice == esperadas
    assert en_la_tabla == esperadas
    assert len(en_el_indice) != 571
    assert not {"the", "of", "and", "which"} & set(en_el_indice)


def test_una_consulta_hecha_solo_de_palabras_vacias_no_recupera_nada(abiertas):
    # Con la lista en inglés o sin lista, "de la que los" encontraría casi todos los fragmentos.
    assert buscar(abiertas, "de la que los el en y se q pa") == []


def test_las_palabras_no_arrastran_signos_de_apertura_ni_comillas_angulares(abiertas):
    # Por omisión DuckDB no separa ¿ ¡ « » ° del texto: "¿Procede?" queda como "¿procede" y
    # "«Abarrotes»" como "«abarrotes»", y ninguna consulta los encuentra. Con el primer índice, 81 de los
    # 1,148 términos arrastraban un signo.
    with abiertas.sesion() as sesion:
        raros = sesion.execute(f"SELECT term FROM {construir.ESQUEMA_INDICE}.dict WHERE regexp_matches(term, '[^a-z]') ORDER BY term").fetchall()
    assert raros == []


def test_una_forma_verbal_que_no_esta_en_los_documentos_encuentra_la_seccion_por_su_raiz(abiertas):
    # "rebajaron" no aparece en ningún PDF; con raíces en español comparte la de "rebajas" y "rebajar".
    assert ("POL-OPS-014", "6") in citas(buscar(abiertas, "rebajaron"))


# --- La búsqueda: P10 -------------------------------------------------------------------------------------


def test_P10_recupera_PRO_SAC_021_3_y_FAQ_OPS_001_P15_entre_los_primeros_4(abiertas):
    encontrados = buscar(abiertas, P10)
    assert len(encontrados) == 4
    paginas = {(f.documento, f.seccion): f.pagina for f in encontrados}
    # Documento, sección y página que cita la rúbrica (C2.2): PRO-SAC-021 §3, p. 2; FAQ P15, p. 4.
    assert paginas.get(("PRO-SAC-021", "3")) == 2
    assert paginas.get(("FAQ-OPS-001", "P15")) == 4


@pytest.mark.parametrize("pregunta", PREGUNTAS_DE_POLITICAS)
def test_las_preguntas_de_politicas_del_set_encuentran_una_seccion_aceptada_entre_los_primeros_4(abiertas, pregunta):
    texto, aceptadas = PREGUNTAS_DE_POLITICAS[pregunta]
    assert aceptadas & set(citas(buscar(abiertas, texto)))


@pytest.fixture(scope="module")
def indice_por_omision(base_con_indice):
    """Los mismos fragmentos con el índice que crea DuckDB sin opciones: raíz porter y 571 palabras en inglés."""
    with duckdb.connect() as con:
        construir.cargar_fts(con, instalar=False)
        con.execute("ATTACH ? AS origen (READ_ONLY)", [str(base_con_indice.base)])
        con.execute("CREATE TABLE fragmentos AS SELECT * FROM origen.fragmentos")
        con.execute("DETACH origen")
        con.execute("PRAGMA create_fts_index('fragmentos', 'id', 'titulo_seccion', 'texto')")
        yield con


def test_control_con_el_indice_por_omision_P10_no_recupera_PRO_SAC_021_3(indice_por_omision):
    # Es lo que corrige T1.6: la misma búsqueda sobre el índice de DuckDB sin opciones no lo cumple.
    encontrados = consultas.buscar_fragmentos(indice_por_omision, P10)
    assert ("FAQ-OPS-001", "P15") in citas(encontrados)
    assert ("PRO-SAC-021", "3") not in citas(encontrados)


def test_control_con_el_indice_por_omision_una_forma_verbal_ausente_no_encuentra_nada(indice_por_omision):
    assert consultas.buscar_fragmentos(indice_por_omision, "rebajaron") == []


def test_buscar_fragmentos_devuelve_a_lo_mas_el_limite_en_orden_de_puntaje_con_los_datos_de_la_cita(abiertas):
    encontrados = buscar(abiertas, P10)
    puntajes = [f.puntaje for f in encontrados]
    assert puntajes == sorted(puntajes, reverse=True)
    assert all(p > 0 for p in puntajes)
    primero = encontrados[0]
    assert (primero.id, primero.documento, primero.seccion, primero.pagina, primero.pagina_fin) == ("FAQ-OPS-001#P15", "FAQ-OPS-001", "P15", 4, 4)
    assert primero.titulo_seccion.startswith("Un cliente quiere devolver una licuadora")
    con_limite = buscar(abiertas, P10, limite=2)
    assert [f.id for f in con_limite] == [f.id for f in encontrados[:2]]
    assert [f.puntaje for f in con_limite] == pytest.approx([f.puntaje for f in encontrados[:2]])
    assert len(buscar(abiertas, P10, limite=50)) > 4


def test_el_puntaje_que_devuelve_no_cambia_de_una_ejecucion_a_otra(abiertas):
    # BM25 suma en paralelo y el último dígito del puntaje cambia entre ejecuciones: la misma consulta dio
    # 10.007454813828847 y 10.007454813828849, y 1 de cada 5 corridas difiere de la más frecuente. Sin
    # redondear, que las 60 corridas den lo mismo por azar tiene una probabilidad menor que 2 en un millón.
    with abiertas.sesion() as sesion:
        puntajes = {tuple(f.puntaje for f in consultas.buscar_fragmentos(sesion, P10)) for _ in range(60)}
    assert len(puntajes) == 1


def test_los_puntajes_que_difieren_en_el_ruido_de_la_suma_son_un_empate_y_lo_decide_el_id():
    # Fabrica el ruido: 'a' y 'b' tienen el mismo puntaje salvo el último dígito, y 'b' es el mayor. Por
    # puntaje exacto saldría 'b' antes que 'a'; son un empate, así que sale 'a' (id menor). 'c' queda
    # claramente debajo y 'd', sin coincidencia (NULL), no sale.
    with duckdb.connect() as con:
        con.execute(
            "CREATE TABLE fragmentos (id VARCHAR, documento VARCHAR, seccion VARCHAR, titulo_seccion VARCHAR, "
            "pagina INTEGER, pagina_fin INTEGER)"
        )
        con.execute("INSERT INTO fragmentos VALUES ('a', 'D', '1', 't', 1, 1), ('b', 'D', '2', 't', 1, 1), ('c', 'D', '3', 't', 1, 1), ('d', 'D', '4', 't', 1, 1)")
        con.execute(f"CREATE SCHEMA {construir.ESQUEMA_INDICE}")
        con.execute(
            f"CREATE MACRO {construir.ESQUEMA_INDICE}.match_bm25(documento, consulta) AS "
            "CASE documento WHEN 'a' THEN 2.5::DOUBLE WHEN 'b' THEN 2.5000000000000004::DOUBLE "
            "WHEN 'c' THEN 2.4::DOUBLE ELSE NULL END"
        )
        encontrados = consultas.buscar_fragmentos(con, "lo que sea")
    assert [f.id for f in encontrados] == ["a", "b", "c"]
    assert [f.puntaje for f in encontrados] == [2.5, 2.5, 2.4]


def test_el_limite_por_omision_son_los_4_fragmentos_de_la_ficha():
    assert consultas.LIMITE_FRAGMENTOS == 4


@pytest.mark.parametrize("limite", [0, -1])
def test_un_limite_menor_que_uno_es_un_error_de_programacion(abiertas, limite):
    with pytest.raises(ValueError, match="limite"):
        buscar(abiertas, P10, limite=limite)


@pytest.mark.parametrize("texto", ["", "   ", "¿?!!...", "3 2026 ,"], ids=["vacía", "espacios", "signos", "solo dígitos"])
def test_una_consulta_sin_palabras_no_recupera_nada(abiertas, texto):
    assert buscar(abiertas, texto) == []


def test_el_texto_de_la_consulta_viaja_como_parametro_y_no_como_sql(base_propia):
    # En una conexión de escritura y sin protección: si el texto se pegara al SQL, esto borraría la tabla.
    # Sus palabras sí pueden coincidir con algún fragmento: lo que importa es que se buscan, no se ejecutan.
    ataque = "x'); DROP TABLE fragmentos; --"
    with duckdb.connect(str(base_propia)) as con:
        construir.cargar_fts(con, instalar=False)
        encontrados = consultas.buscar_fragmentos(con, ataque)
        assert all(isinstance(f, consultas.FragmentoEncontrado) for f in encontrados)
        assert con.execute("SELECT count(*) FROM fragmentos").fetchone() == (62,)


def test_filtrar_por_el_alias_de_match_bm25_fuera_de_una_subconsulta_da_Binder_Error(abiertas):
    # Hallazgo 3 del plan: por eso buscar_fragmentos filtra el puntaje desde una subconsulta.
    with abiertas.sesion() as sesion:
        with pytest.raises(duckdb.BinderException, match="side effects"):
            sesion.execute(
                f"SELECT id, {construir.ESQUEMA_INDICE}.match_bm25(id, ?) AS puntaje FROM fragmentos WHERE puntaje IS NOT NULL",
                [P10],
            )


# --- La conexión: solo lectura, sin acceso externo, configuración bloqueada -----------------------------


def test_abrir_consultas_abre_en_solo_lectura_carga_fts_apaga_el_acceso_externo_y_bloquea_la_configuracion(base_propia):
    with consultas.abrir_consultas(base_propia) as abiertas, abiertas.sesion() as sesion:
        acceso, bloqueada = sesion.execute("SELECT current_setting('enable_external_access'), current_setting('lock_configuration')").fetchone()
        # duckdb_extensions() lista una carpeta y con el acceso externo apagado no se puede llamar; stem, de
        # fts, solo existe si la extensión ya estaba cargada (no se puede autocargar sin acceso externo).
        raiz_de_la_palabra = sesion.execute("SELECT stem('licuadoras', 'spanish')").fetchone()[0]
        solo_lectura = sesion.execute("SELECT readonly FROM duckdb_databases() WHERE path = ?", [str(base_propia)]).fetchone()[0]
    assert (solo_lectura, raiz_de_la_palabra, acceso, bloqueada) == (True, "licuador", False, True)
    assert abiertas.base == base_propia


@pytest.mark.parametrize("sentencia", ESCRITURAS.values(), ids=ESCRITURAS.keys())
def test_una_escritura_falla_porque_la_base_esta_en_solo_lectura(abiertas, sentencia):
    with abiertas.sesion() as sesion:
        with pytest.raises(duckdb.InvalidInputException, match="read-only mode"):
            sesion.execute(sentencia)


def test_despues_de_los_intentos_de_escritura_la_base_no_cambia(base_propia):
    antes = sha256_de(base_propia)
    with consultas.abrir_consultas(base_propia) as abiertas, abiertas.sesion() as sesion:
        for sentencia in ESCRITURAS.values():
            with pytest.raises(duckdb.InvalidInputException):
                sesion.execute(sentencia)
        conteos = sesion.execute("SELECT (SELECT count(*) FROM ventas), (SELECT count(*) FROM tiendas), (SELECT count(*) FROM fragmentos)").fetchone()
        total = sesion.execute("SELECT sum(venta_neta_mxn) FROM ventas").fetchone()[0]
    assert conteos == (41859, 4, 62)
    assert total == Decimal("17228300.26")
    assert sha256_de(base_propia) == antes


def test_con_el_acceso_externo_apagado_no_se_leen_archivos_ni_se_escribe_ni_se_adjunta_ni_se_instala(abiertas, tmp_path):
    csv = DIR_DATOS / "ventas.csv"
    # Control: la misma lectura funciona en una conexión sin proteger, así que lo que la frena es la configuración.
    with duckdb.connect() as libre:
        assert libre.execute("SELECT count(*) FROM read_csv(?)", [str(csv)]).fetchone()[0] > 0
    salida, otra_base, exportacion = tmp_path / "salida.csv", tmp_path / "otra.duckdb", tmp_path / "exportacion"
    bloqueadas = [
        f"SELECT * FROM read_csv('{csv.as_posix()}')",
        f"SELECT * FROM read_text('{(RAIZ / 'README.md').as_posix()}')",
        f"COPY (SELECT 1) TO '{salida.as_posix()}'",
        f"ATTACH '{otra_base.as_posix()}' AS otra",
        f"EXPORT DATABASE '{exportacion.as_posix()}'",
        "INSTALL httpfs",
    ]
    with abiertas.sesion() as sesion:
        for sentencia in bloqueadas:
            with pytest.raises(duckdb.PermissionException, match="disabled (by|through) configuration"):
                sesion.execute(sentencia)
    assert not (salida.exists() or otra_base.exists() or exportacion.exists())


@pytest.mark.parametrize(
    "sentencia",
    ["SET enable_external_access = true", "SET threads = 1", "SET lock_configuration = false", "SET memory_limit = '1GB'"],
)
def test_la_configuracion_bloqueada_no_se_puede_cambiar_desde_ninguna_sesion(abiertas, sentencia):
    with abiertas.sesion() as sesion:
        with pytest.raises(duckdb.InvalidInputException, match="has been locked"):
            sesion.execute(sentencia)


def test_apagar_el_acceso_externo_antes_de_cargar_fts_impide_cargarla(base_propia):
    # Por eso el orden de la ficha empieza por LOAD fts (hallazgo 3 del plan, comprobado en T0.5).
    with duckdb.connect(str(base_propia), read_only=True) as con:
        con.execute("SET enable_external_access = false")
        with pytest.raises(construir.ExtensionNoDisponible) as capturado:
            construir.cargar_fts(con, instalar=False)
    assert capturado.value.paso == "LOAD"
    assert isinstance(capturado.value.__cause__, duckdb.PermissionException)


def test_bloquear_la_configuracion_antes_de_apagar_el_acceso_externo_deja_el_acceso_encendido(base_propia):
    # Por eso lock_configuration va al final: después de bloquearla ya no se puede apagar nada.
    with duckdb.connect(str(base_propia), read_only=True) as con:
        con.execute("SET lock_configuration = true")
        with pytest.raises(duckdb.InvalidInputException, match="has been locked"):
            con.execute("SET enable_external_access = false")
        assert con.execute("SELECT current_setting('enable_external_access')").fetchone() == (True,)


def test_la_busqueda_de_texto_funciona_con_el_acceso_externo_apagado(abiertas):
    # fts quedó cargada antes de apagarlo: match_bm25 usa la función stem de la extensión.
    assert ("FAQ-OPS-001", "P15") in citas(buscar(abiertas, P10))


# --- Sesiones: un cursor por sesión de la interfaz ---------------------------------------------------------


def test_cada_sesion_tiene_su_propio_cursor_sobre_la_misma_base_y_con_las_mismas_protecciones(abiertas):
    with abiertas.sesion() as a, abiertas.sesion() as b:
        assert a is not b
        assert a.execute("SELECT count(*) FROM ventas").fetchone() == b.execute("SELECT count(*) FROM ventas").fetchone() == (41859,)
        assert citas(consultas.buscar_fragmentos(a, P10)) == citas(consultas.buscar_fragmentos(b, P10))
        for sesion in (a, b):
            with pytest.raises(duckdb.InvalidInputException, match="read-only mode"):
                sesion.execute("DELETE FROM ventas")
            with pytest.raises(duckdb.PermissionException):
                sesion.execute("ATTACH 'otra.duckdb' AS otra")


def test_control_dos_hilos_en_la_misma_conexion_dan_ResourceInUseException(abiertas):
    # Hallazgo 4 del plan: una conexión ocupada por otro hilo no espera, falla.
    compartida = abiertas.sesion()
    with ocupar(compartida):
        with pytest.raises(duckdb.ResourceInUseException, match="connection is busy"):
            compartida.execute("SELECT 1").fetchall()


def test_dos_sesiones_que_consultan_a_la_vez_no_dan_ResourceInUseException(abiertas):
    sesion_a, sesion_b = abiertas.sesion(), abiertas.sesion()
    with ocupar(sesion_a):
        # La sesión A está dentro de una llamada sin terminar; la sesión B consulta ventas y políticas.
        ventas = sesion_b.execute("SELECT count(*) FROM ventas").fetchone()[0]
        encontrados = consultas.buscar_fragmentos(sesion_b, P10)
    assert ventas == 41859
    assert ("PRO-SAC-021", "3") in citas(encontrados)


def test_varias_sesiones_consultan_ventas_y_politicas_a_la_vez_sin_errores(abiertas):
    hilos, vueltas = 6, 25
    esperado = (Decimal("17228300.26"), citas(buscar(abiertas, P10)))
    barrera = threading.Barrier(hilos)
    errores, resultados = [], []

    def sesion_de_la_interfaz():
        try:
            with abiertas.sesion() as sesion:
                barrera.wait(timeout=30)
                for _ in range(vueltas):
                    total = sesion.execute("SELECT sum(venta_neta_mxn) FROM ventas").fetchone()[0]
                    resultados.append((total, citas(consultas.buscar_fragmentos(sesion, P10))))
        except Exception as error:  # noqa: BLE001 - se reporta todo lo que falle, de cualquier tipo
            errores.append(f"{type(error).__name__}: {str(error).splitlines()[0]}")

    trabajadores = [threading.Thread(target=sesion_de_la_interfaz) for _ in range(hilos)]
    for trabajador in trabajadores:
        trabajador.start()
    for trabajador in trabajadores:
        trabajador.join(timeout=120)
    assert errores == []
    assert resultados == [esperado] * (hilos * vueltas)


def test_abrir_a_la_vez_desde_varios_hilos_deja_a_todos_con_una_conexion_protegida(base_propia):
    hilos = 6
    barrera = threading.Barrier(hilos)
    errores, conexiones = [], []

    def abrir():
        barrera.wait(timeout=30)
        try:
            conexiones.append(consultas.abrir_consultas(base_propia))
        except Exception as error:  # noqa: BLE001 - se reporta todo lo que falle, de cualquier tipo
            errores.append(f"{type(error).__name__}: {str(error).splitlines()[0]}")

    trabajadores = [threading.Thread(target=abrir) for _ in range(hilos)]
    for trabajador in trabajadores:
        trabajador.start()
    for trabajador in trabajadores:
        trabajador.join(timeout=120)
    try:
        assert errores == []
        assert len(conexiones) == hilos
        for conexion in conexiones:
            with conexion.sesion() as sesion:
                assert sesion.execute("SELECT current_setting('lock_configuration')").fetchone() == (True,)
                assert citas(consultas.buscar_fragmentos(sesion, P10))
    finally:
        for conexion in conexiones:
            conexion.cerrar()


def test_abrir_dos_veces_la_misma_base_en_el_proceso_comparte_el_motor_ya_protegido(base_propia):
    # DuckDB comparte el motor entre las conexiones de un proceso al mismo archivo. Una segunda apertura
    # (la interfaz que recarga su caché) no puede fallar por volver a bloquear una configuración ya bloqueada.
    with consultas.abrir_consultas(base_propia) as primera, consultas.abrir_consultas(base_propia) as segunda:
        primera.cerrar()
        with segunda.sesion() as sesion:
            assert sesion.execute("SELECT count(*) FROM tiendas").fetchone() == (4,)
            with pytest.raises(duckdb.InvalidInputException, match="read-only mode"):
                sesion.execute("DELETE FROM tiendas")
            with pytest.raises(duckdb.PermissionException):
                sesion.execute("COPY (SELECT 1) TO 'salida.csv'")


# --- Errores con tipo y cierre ---------------------------------------------------------------------------


def test_si_la_base_no_existe_lanza_BaseNoDisponible_con_el_comando_para_construirla_y_no_la_crea(tmp_path):
    ruta = tmp_path / "no_existe.duckdb"
    with pytest.raises(consultas.BaseNoDisponible) as capturado:
        consultas.abrir_consultas(ruta)
    assert isinstance(capturado.value, construir.ErrorConstruccion)
    assert capturado.value.base == ruta
    assert "no existe" in capturado.value.motivo
    assert "python -m agente.construir" in str(capturado.value)
    assert not ruta.exists()


def test_si_el_archivo_no_es_una_base_de_duckdb_lanza_BaseNoDisponible_sin_traceback_de_duckdb(tmp_path):
    ruta = tmp_path / "tienda.duckdb"
    ruta.write_bytes(b"esto no es una base de datos" * 40)
    with pytest.raises(consultas.BaseNoDisponible) as capturado:
        consultas.abrir_consultas(ruta)
    assert capturado.value.base == ruta
    assert capturado.value.motivo
    assert isinstance(capturado.value.__cause__, duckdb.Error)


def test_si_falla_la_proteccion_la_conexion_se_cierra_y_el_archivo_queda_libre(base_propia, monkeypatch):
    def sin_fts(*argumentos, **opciones):
        raise construir.ExtensionNoDisponible("fts", "LOAD", "IO Error: no está instalada")

    monkeypatch.setattr(consultas, "cargar_fts", sin_fts)
    with pytest.raises(construir.ExtensionNoDisponible) as capturado:
        consultas.abrir_consultas(base_propia)
    # capturado sigue vivo y con él el traceback y la conexión abierta, si no se hubiera cerrado:
    # en Windows, reemplazar el archivo falla con PermissionError si la conexión sigue abierta.
    reemplazar_encima(base_propia)
    assert capturado.value.extension == "fts"


def test_la_base_por_omision_es_build_tienda_duckdb():
    assert inspect.signature(consultas.abrir_consultas).parameters["base"].default == construir.BASE
    assert construir.BASE == RAIZ / "build" / "tienda.duckdb"


def test_sin_fts_instalada_abrir_consultas_falla_al_cargarla_y_no_intenta_descargarla(base_propia, tmp_path):
    # Un directorio personal vacío y un proxy que corta la red: si abrir_consultas intentara el INSTALL,
    # fallaría con paso INSTALL después de ~20 s. La conexión de consulta solo carga (T0.5).
    casa = tmp_path / "casa-sin-extensiones"
    casa.mkdir()
    codigo = """
import json, sys
sys.path.insert(0, sys.argv[1])
from agente.construir import ExtensionNoDisponible
from agente.consultas import abrir_consultas
try:
    abrir_consultas(sys.argv[2]).cerrar()
    print(json.dumps({"error": None}))
except ExtensionNoDisponible as error:
    print(json.dumps({"error": "ExtensionNoDisponible", "extension": error.extension, "paso": error.paso}))
"""
    entorno = {**os.environ, "USERPROFILE": str(casa), "HOME": str(casa), "HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9"}
    resultado = subprocess.run(
        [sys.executable, "-I", "-c", codigo, str(RAIZ), str(base_propia)],
        capture_output=True,
        encoding="utf-8",
        env=entorno,
        timeout=120,
    )
    assert resultado.returncode == 0, resultado.stderr[-2000:]
    assert json.loads(resultado.stdout.strip().splitlines()[-1]) == {"error": "ExtensionNoDisponible", "extension": "fts", "paso": "LOAD"}


@pytest.mark.skipif(sys.platform != "win32", reason="solo Windows impide reemplazar un archivo abierto (T1.7)")
def test_mientras_la_conexion_esta_abierta_no_se_puede_reemplazar_el_archivo(base_propia):
    # Control de la prueba siguiente: reemplazar encima sí detecta una conexión abierta.
    with consultas.abrir_consultas(base_propia):
        with pytest.raises(PermissionError):
            reemplazar_encima(base_propia)


def test_cerrar_libera_el_archivo_cierra_las_sesiones_y_se_puede_repetir(base_propia):
    abiertas = consultas.abrir_consultas(base_propia)
    sesion = abiertas.sesion()
    abiertas.cerrar()
    abiertas.cerrar()
    with pytest.raises(duckdb.Error):
        sesion.execute("SELECT 1")
    reemplazar_encima(base_propia)


def test_cerrar_con_una_sesion_dentro_de_una_llamada_falla_con_ResourceInUseException_y_se_repite_al_terminar(base_propia):
    # DuckDB no espera a que termine la llamada ni la cierra a la fuerza: lanza el error y deja abiertos
    # los cursores que no alcanzó a cerrar (en 2.0.0, los creados después del ocupado). Quien cierre las
    # consultas para reconstruir la base (T1.7) recibe el error, no un cierre a medias en silencio, y
    # repite cerrar() cuando termina la llamada: entonces cierra todo y el archivo queda libre.
    abiertas = consultas.abrir_consultas(base_propia)
    ocupada, despues = abiertas.sesion(), abiertas.sesion()
    try:
        with ocupar(ocupada):
            with pytest.raises(duckdb.ResourceInUseException, match="connection is busy"):
                abiertas.cerrar()
    finally:
        abiertas.cerrar()
    with pytest.raises(duckdb.Error):
        despues.execute("SELECT 1")
    reemplazar_encima(base_propia)


# --- Con T1.7: la base vigente y la conexión de consultas -------------------------------------------------


def test_asegurar_base_reconoce_la_base_vigente_aunque_haya_una_conexion_de_consulta_abierta(base_propia):
    with consultas.abrir_consultas(base_propia):
        estado = construir.asegurar_base(DIR_DATOS, base_propia)
    assert estado.reconstruida is False


def alterar_como_copia_a(dir_datos):
    """Copia A de la rúbrica: venta_neta_mxn de T02 del 2026-07-01 multiplicada por 2 (igual que en T1.7)."""
    ruta = Path(dir_datos) / "ventas.csv"
    lineas = ruta.read_bytes().decode("utf-8").split("\n")
    for numero, linea in enumerate(lineas):
        campos = linea.split(",")
        if campos[:2] == ["2026-07-01", "T02"]:
            campos[4] = f"{Decimal(campos[4]) * 2:.2f}"
            lineas[numero] = ",".join(campos)
    ruta.write_bytes("\n".join(lineas).encode("utf-8"))


def test_tras_cerrar_las_consultas_asegurar_base_reconstruye_y_la_conexion_nueva_ve_los_datos_nuevos(fts, tmp_path, copia_datos):
    # El flujo de la interfaz: cerrar las consultas, asegurar la base y abrirlas de nuevo. Con la copia A
    # de la rúbrica la venta neta pasa de 17,228,300.26 a 17,284,355.44 sin tocar el código.
    base = tmp_path / "tienda.duckdb"
    opciones = {"politicas": False, "indice_texto": False}

    def venta_neta():
        with consultas.abrir_consultas(base) as abiertas, abiertas.sesion() as sesion:
            return sesion.execute("SELECT sum(venta_neta_mxn) FROM ventas").fetchone()[0]

    assert construir.asegurar_base(copia_datos, base, **opciones).reconstruida is True
    assert venta_neta() == Decimal("17228300.26")
    alterar_como_copia_a(copia_datos)
    assert construir.asegurar_base(copia_datos, base, **opciones).reconstruida is True
    assert venta_neta() == Decimal("17284355.44")
