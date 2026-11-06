"""Herramienta de tickets de la mesa de servicio (T2.6, C1.2, C4.1, C7.2, K11).

Acepta: P15 da 285; P16 da 17; P17 da el empate; P18 da 268; P19 da 86 de 268; P29 da el 1258 con
9.65 días; P37 da 0 en Lomas del Sur; P43 da 5 tickets sin datos personales; P14 dice que no hay
ticket de la caja 3 en Valle Norte. Las cifras se escriben aquí porque el criterio de aceptación las
fija; el código de la herramienta no las conoce (G2).

La base es la de la fixture base_construida, en un directorio temporal (tests/conftest.py). Cada
llamada toma su propio cursor, como hará el agente con Consultas.sesion(). El celular y el nombre
del ticket 1043 se leen del CSV al correr la prueba, con los ayudantes de tests/test_privacidad.py:
este archivo no los contiene. Sin red, sin llaves y sin modelo.
"""

import datetime as dt
import json
import re

import duckdb
import pytest
from pydantic import ValidationError

from agente import definiciones, privacidad, texto_tickets
from agente.herramientas import comun
from agente.herramientas.comun import EntradaTickets
from agente.herramientas.tickets import TEXTO_NORMALIZADO, terminos_de_busqueda, tickets
from tests.test_privacidad import TICKET_CON_DATOS, DatoPersonal, leer_tickets, variantes_celular

TOTAL = 285
SIN_CERRAR = 17
CERRADOS = 268
CUMPLIERON = 86
SIN_CERRAR_POR_TIENDA = {"T01": 5, "T02": 8, "T03": 4, "T04": 0}
MONTACARGAS_EN_ANAHUAC = "1025, 1184, 1254, 1278"
TICKETS_P43 = ["1012", "1013", "1029", "1039", "1043"]
# P14: los tickets de impresora de caja de Valle Norte, de las cajas 2, 7, 8 y 11 (rúbrica).
IMPRESORAS_VALLE_NORTE = [1037, 1044, 1085, 1088]
# PT-21: los tickets que hablan de la contraseña, escrita con eñe en cinco y sin ella en uno.
CONTRASENA = ["1068", "1105", "1144", "1221", "1229", "1281"]
HOY_REAL = dt.date(2026, 10, 31)


@pytest.fixture(scope="module")
def conexion(base_construida):
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        yield con


@pytest.fixture
def llamar(conexion):
    """Corre la herramienta con un cursor propio por llamada, como el agente."""

    def correr(s=None, **campos):
        return tickets(conexion.cursor(), EntradaTickets(**campos), s=s, hoy_real=HOY_REAL)

    return correr


def ids(resultado):
    return [fila["ticket_id"] for fila in resultado.filas]


# ------------------------------------------------------------------ conteos (P15, P16, P18, P37, P56)

def test_p15_el_total_de_tickets_es_285(llamar):
    r = llamar()
    assert r.estado == "ok"
    assert r.cifras["tickets"] == "285"
    assert r.unidad == "tickets"
    assert r.corte == dt.date(2026, 8, 31)
    assert r.fuente == "tickets_mesa_servicio.csv"


def test_p16_sin_cerrar_son_17_con_9_abiertos_y_8_en_proceso(llamar):
    r = llamar(situacion="sin_cerrar")
    assert r.cifras["tickets"] == str(SIN_CERRAR)
    assert r.cifras["estado Abierto"] == "9"
    assert r.cifras["estado En proceso"] == "8"
    assert "estado Cerrado" not in r.cifras
    assert r.cifras["de_un_total_de"] == "285"
    assert "fecha_cierre" in r.definicion or "fecha de cierre" in r.definicion
    assert any("hoy no se conoce" in nota for nota in r.notas)
    assert any("al corte del 1 de septiembre de 2026" in nota for nota in r.notas)


def test_p16_por_tienda_trae_las_cuatro_aunque_una_tenga_cero(llamar):
    r = llamar(situacion="sin_cerrar", agrupar_por="tienda")
    por_tienda = {fila["tienda_id"]: fila["tickets"] for fila in r.filas}
    assert por_tienda == {tienda: str(n) for tienda, n in SIN_CERRAR_POR_TIENDA.items()}
    assert r.cifras["tickets"] == "17"


def test_p18_cerrados_son_268_con_72_resueltos_y_196_cerrados(llamar):
    r = llamar(situacion="cerrado")
    assert r.cifras["tickets"] == str(CERRADOS)
    assert r.cifras["estado Resuelto"] == "72"
    assert r.cifras["estado Cerrado"] == "196"


def test_p18_solo_el_estado_resuelto_da_72(llamar):
    r = llamar(estados=["resuelto"])
    assert r.cifras["tickets"] == "72"
    assert r.filtros["estados"] == ["Resuelto"]


def test_p37_lomas_del_sur_tiene_0_sin_cerrar_de_51(llamar):
    r = llamar(situacion="sin_cerrar", tiendas=["lomas del sur"])
    assert r.estado == "ok"
    assert r.cifras["tickets"] == "0"
    assert r.cifras["de_un_total_de"] == "51"
    assert r.filtros["tiendas"] == ["T04"]


def test_p56_valle_norte_cerro_67_con_53_cerrados_y_14_resueltos(llamar):
    r = llamar(situacion="cerrado", tiendas=["valle norte"])
    assert (r.cifras["tickets"], r.cifras["estado Cerrado"], r.cifras["estado Resuelto"]) == ("67", "53", "14")


def test_agrupar_por_estado_trae_los_estados_en_cero(llamar):
    r = llamar(situacion="sin_cerrar", agrupar_por="estado")
    assert {fila["grupo"]: fila["tickets"] for fila in r.filas} == {
        "Abierto": "9", "En proceso": "8", "Resuelto": "0", "Cerrado": "0",
    }


@pytest.mark.parametrize("agrupar, valores", [
    ("estado", ["Abierto", "En proceso", "Resuelto", "Cerrado"]),
    ("prioridad", ["Baja", "Media", "Alta", "Crítica"]),
    ("categoria", ["TI", "Mantenimiento", "RH", "Inventario", "Cajas"]),
])
def test_agrupar_trae_cada_valor_y_suma_el_total(llamar, agrupar, valores):
    r = llamar(agrupar_por=agrupar)
    assert sorted(fila["grupo"] for fila in r.filas) == sorted(valores)
    assert sum(int(fila["tickets"]) for fila in r.filas) == TOTAL


def test_el_conteo_por_prioridad_y_categoria_filtra(llamar, conexion):
    r = llamar(prioridad="critica", categoria="ti")
    esperado = conexion.execute(
        "SELECT count(*) FROM tickets WHERE prioridad = 'Crítica' AND categoria = 'TI'"
    ).fetchone()[0]
    assert r.cifras["tickets"] == str(esperado)
    assert r.filtros["prioridad"] == "Crítica"


def test_el_periodo_filtra_por_fecha_de_creacion(llamar, conexion):
    r = llamar(periodo="junio")
    esperado = conexion.execute("SELECT count(*) FROM tickets WHERE month(fecha_creacion) = 6").fetchone()[0]
    assert r.cifras["tickets"] == str(esperado)
    assert "creación" in r.definicion


# ------------------------------------------------------------------ lo que más falla (P17)

def test_p17_anahuac_da_el_empate_de_4_y_4(llamar):
    r = llamar(metrica="mas_frecuentes", tiendas=["anahuac"])
    primeros = [fila for fila in r.filas if fila["tickets"] == "4"]
    assert len(primeros) == 2
    titulos = {fila["titulo"].casefold() for fila in primeros}
    assert titulos == {"montacargas no arranca", "se cayó el wifi de los handhelds"}
    montacargas = next(fila for fila in primeros if "montacargas" in fila["titulo"].casefold())
    assert montacargas["folios"] == MONTACARGAS_EN_ANAHUAC
    assert r.cifras["maximo"] == "4"
    assert r.cifras["titulos_con_el_maximo"] == "2"
    assert any("empate" in nota for nota in r.notas)


def test_p17_con_limite_1_no_esconde_el_empate(llamar):
    r = llamar(metrica="mas_frecuentes", tiendas=["anahuac"], limite=1)
    assert [fila["tickets"] for fila in r.filas] == ["4", "4"]


@pytest.mark.parametrize("texto, titulo", [
    ("montacargas", "Montacargas no arranca"),
    ("handhelds wifi", "Se cayó el wifi de los handhelds"),
])
def test_cada_grupo_se_muestra_con_su_titulo_original_mas_frecuente(llamar, conexion, texto, titulo):
    r = llamar(metrica="mas_frecuentes", texto=texto)
    assert [fila["titulo"] for fila in r.filas] == [titulo]
    variantes = conexion.execute(
        "SELECT titulo, count(*) FROM v_tickets WHERE titulo_normalizado = ? GROUP BY titulo ORDER BY 2 DESC",
        [texto_tickets.normalizar_titulo(titulo)],
    ).fetchall()
    assert len(variantes) > 1 and variantes[0][0] == titulo


def test_p17_cuenta_por_titulo_normalizado_y_no_por_tema(llamar, conexion):
    r = llamar(metrica="mas_frecuentes", tiendas=["T03"], limite=50)
    grupos = conexion.execute(
        "SELECT count(DISTINCT titulo_normalizado) FROM v_tickets WHERE tienda_id = 'T03'"
    ).fetchone()[0]
    assert r.n_filas == grupos
    assert r.cifras["tickets"] == "90"


# ------------------------------------------------------------------ antigüedad (P29)

def test_p29_el_mas_viejo_de_cumbres_es_el_1258_con_9_65_dias(llamar):
    r = llamar(metrica="antiguedad", tiendas=["cumbres"])
    assert r.cifras["mas_antiguo"] == "1258"
    assert r.cifras["antiguedad_maxima_dias"] == "9.65"
    assert r.cifras["tickets_sin_cerrar"] == "8"
    assert (r.cifras["estado Abierto"], r.cifras["estado En proceso"]) == ("5", "3")
    assert [fila["ticket_id"] for fila in r.filas[:2]] == ["1258", "1261"]
    assert [fila["antiguedad_dias"] for fila in r.filas[:2]] == ["9.65", "9.32"]
    assert r.filas[0]["creado"] == "2026-08-22 08:20"
    assert "24:00" in r.definicion


def test_la_antiguedad_sale_de_definiciones_y_coincide_con_la_vista(llamar, conexion):
    r = llamar(metrica="antiguedad", limite=50)
    s = definiciones.supuestos()
    vista = dict(conexion.execute("SELECT ticket_id, antiguedad_dias FROM v_tickets WHERE sin_cerrar").fetchall())
    creados = dict(conexion.execute("SELECT ticket_id, fecha_creacion FROM v_tickets WHERE sin_cerrar").fetchall())
    assert r.n_filas == SIN_CERRAR
    for fila in r.filas:
        folio = int(fila["ticket_id"])
        assert fila["antiguedad_dias"] == f"{s.antiguedad_dias(creados[folio]):.2f}" == f"{vista[folio]:.2f}"


def test_la_antiguedad_no_se_mide_en_tickets_cerrados(llamar):
    r = llamar(metrica="antiguedad", situacion="cerrado")
    assert r.estado == "sin_resultados"
    assert not r.cifras


def test_la_antiguedad_de_una_tienda_sin_tickets_abiertos_es_cero_tickets(llamar):
    r = llamar(metrica="antiguedad", tiendas=["lomas del sur"])
    assert r.estado == "ok"
    assert r.cifras["tickets_sin_cerrar"] == "0"
    assert "mas_antiguo" not in r.cifras


# ------------------------------------------------------------------ cumplimiento (P19)

def test_p19_cumplieron_86_de_268_en_horas_naturales(llamar):
    r = llamar(metrica="cumplimiento")
    assert r.cifras["cumplieron"] == str(CUMPLIERON)
    assert r.cifras["cerrados"] == str(CERRADOS)
    assert r.cifras["porcentaje"] == "32.09%"
    assert "naturales" in r.definicion
    assert any("hábil" in nota for nota in r.notas)
    assert any("primera respuesta" in nota for nota in r.notas)
    por_prioridad = {fila["grupo"]: fila for fila in r.filas}
    assert set(por_prioridad) == {"Baja", "Media", "Alta", "Crítica"}
    assert sum(int(fila["cumplieron"]) for fila in r.filas) == CUMPLIERON
    assert por_prioridad["Media"]["objetivo_horas"] == "48"


def test_p19_con_la_ventana_de_la_tienda_la_cifra_no_cambia_y_la_respuesta_la_dice(llamar):
    r = llamar(metrica="cumplimiento")
    assert r.cifras["cumplieron"] == str(CUMPLIERON)
    assert r.cifras["cumplieron_horas_naturales"] == str(CUMPLIERON)
    assert any("de 7:00 a 22:00 todos los días" in nota and "MAN-OPS-007 §2" in nota for nota in r.notas)
    assert any(f"contando todo en horas naturales da lo mismo: {CUMPLIERON} de {CERRADOS}" in nota for nota in r.notas)
    assert not any("no está definido" in nota for nota in r.notas)
    assert "Baja 75 horas hábiles, Media 30 horas hábiles, Alta 8 horas naturales, Crítica 4 horas naturales" in r.definicion
    assert "120" not in r.definicion  # el objetivo natural de Baja junto al hábil enreda al modelo
    por_prioridad = {fila["grupo"]: fila for fila in r.filas}
    assert por_prioridad["Baja"]["objetivo_horas_habiles"] == "75"
    assert por_prioridad["Media"]["objetivo_horas_habiles"] == "30"
    assert por_prioridad["Alta"]["objetivo_horas_habiles"] is None


def test_p19_con_lunes_a_viernes_de_9_a_18_da_la_cifra_de_la_rubrica(llamar):
    """La rúbrica fija 150 por horas hábiles transcurridas con esa ventana en Baja y Media."""
    s = definiciones.supuestos()
    ventana = s.tickets.horario_habil.model_copy(update={
        "desde": dt.time(9), "hasta": dt.time(18), "dias_semana": frozenset(range(5)),
    })
    otra = s.model_copy(update={"tickets": s.tickets.model_copy(update={"horario_habil": ventana})})
    r = llamar(metrica="cumplimiento", s=otra)
    assert r.cifras["cumplieron"] == "150"
    assert r.cifras["cumplieron_horas_naturales"] == str(CUMPLIERON)
    assert any("contando todo en horas naturales son 86 de 268" in nota for nota in r.notas)


def test_p19_sin_ventana_mide_en_horas_naturales_y_lo_dice(llamar):
    s = definiciones.supuestos()
    sin_ventana = s.model_copy(update={"tickets": s.tickets.model_copy(update={"horario_habil": None})})
    r = llamar(metrica="cumplimiento", s=sin_ventana)
    assert r.cifras["cumplieron"] == str(CUMPLIERON)
    assert "cumplieron_horas_naturales" not in r.cifras
    assert any("no está definido" in nota for nota in r.notas)
    assert "objetivo_horas_habiles" not in r.filas[0]


def test_p19_el_objetivo_sale_de_supuestos(llamar):
    s = definiciones.supuestos()
    holgado = s.model_copy(update={"tickets": s.tickets.model_copy(
        update={"objetivo_solucion_horas": {p: 10_000.0 for p in s.tickets.objetivo_solucion_horas}}
    )})
    r = llamar(metrica="cumplimiento", s=holgado)
    assert r.cifras["cumplieron"] == str(CERRADOS)


def test_el_cumplimiento_cuenta_igual_que_cumple_objetivo(llamar, conexion):
    s = definiciones.supuestos()
    filas = conexion.execute(
        "SELECT prioridad, fecha_creacion, fecha_cierre FROM tickets WHERE fecha_cierre IS NOT NULL AND tienda_id = 'T02'"
    ).fetchall()
    esperado = sum(
        s.tickets.cumple_objetivo(p, (cierre - creacion).total_seconds() / 3600) for p, creacion, cierre in filas
    )
    r = llamar(metrica="cumplimiento", tiendas=["cumbres"], agrupar_por="estado")
    assert r.cifras["cumplieron"] == str(esperado)
    assert r.cifras["cerrados"] == str(len(filas))


def test_el_cumplimiento_no_se_mide_en_tickets_sin_cerrar(llamar):
    r = llamar(metrica="cumplimiento", situacion="sin_cerrar")
    assert r.estado == "sin_resultados"
    assert not r.cifras


def test_la_primera_respuesta_es_metrica_no_calculable(llamar):
    r = llamar(metrica="primera_respuesta")
    assert r.estado == "metrica_no_calculable"
    assert not r.cifras


# ------------------------------------------------------------------ búsqueda (P14, PT-21)

@pytest.mark.parametrize("texto", ["impresora caja 3", "la impresora de la caja 3", "IMPRESORA  Caja 3"])
def test_p14_no_hay_ticket_de_la_caja_3_en_valle_norte(llamar, texto):
    r = llamar(metrica="buscar", texto=texto, tiendas=["valle norte"])
    assert r.estado == "sin_resultados"
    assert r.cifras["tickets"] == "0"
    assert r.filas == []
    assert r.n_filas == 0
    parecidos = [int(p.split()[0]) for p in r.detalle["parecidos"]]
    assert sorted(parecidos) == IMPRESORAS_VALLE_NORTE
    assert all("Cerrado" in p or "Resuelto" in p for p in r.detalle["parecidos"])
    assert "ninguno es el de «caja 3»" in r.resumen
    assert r.detalle["buscado"] == ["impresora", "caja 3"]


def test_la_caja_3_si_aparece_en_otras_tiendas(llamar):
    r = llamar(metrica="buscar", texto="caja 3")
    assert r.estado == "ok"
    assert ids(r) == ["1151", "1247"]
    assert all(fila["tienda_id"] != "T01" for fila in r.filas)


def test_la_normalizacion_de_duckdb_es_la_de_texto_tickets_en_los_285(conexion):
    filas = conexion.execute(f"SELECT titulo, descripcion, {TEXTO_NORMALIZADO} FROM v_tickets").fetchall()
    assert len(filas) == TOTAL
    for titulo, descripcion, en_sql in filas:
        palabras = texto_tickets.normalizar_texto(f"{titulo} {descripcion}").split()
        assert " ".join(p for p in palabras if p.isascii() and p.isalnum()) == en_sql


@pytest.mark.parametrize("texto, esperado", [
    ("la impresora de la caja 3", ["impresora", "caja 3"]),
    ("Caja 3 caja 3", ["caja 3"]),
    ("3 cajas", ["3", "cajas"]),
    ("pasillo 4 y 5", ["pasillo 4", "5"]),
    ("¿Contraseña?", ["contrasena"]),
])
def test_los_terminos_pegan_la_cifra_a_su_palabra(texto, esperado):
    assert [str(t) for t in terminos_de_busqueda(texto)] == esperado


def test_la_consulta_trae_sus_parametros_para_repetirla(llamar, conexion):
    r = llamar(metrica="buscar", texto="caja 3", tiendas=["cumbres"])
    sql, _, parametros = r.consulta.partition("\n-- parámetros: ")
    assert parametros.startswith("'T02', ")
    valores = [valor.strip("'") for valor in parametros.split(", ")]
    assert [str(fila[0]) for fila in conexion.execute(sql, valores).fetchall()] == ids(r)


def test_un_numero_se_busca_como_palabra_completa(llamar):
    con_11 = llamar(metrica="buscar", texto="caja 11", tiendas=["valle norte"], limite=50)
    con_1 = llamar(metrica="buscar", texto="caja 1", tiendas=["valle norte"], limite=50)
    assert "1037" in ids(con_11)
    assert "1037" not in ids(con_1)


@pytest.mark.parametrize("texto", ["contraseña", "contrasena", "CONTRASEÑA", "Contraseñas"])
def test_pt21_contrasena_con_y_sin_ene_da_los_mismos_tickets(llamar, texto):
    r = llamar(metrica="buscar", texto=texto, limite=50)
    assert sorted(ids(r)) == CONTRASENA


def test_la_busqueda_encuentra_el_titulo_sin_mayusculas_ni_acentos(llamar):
    r = llamar(metrica="buscar", texto="montacargas", tiendas=["anahuac"])
    assert ids(r) == MONTACARGAS_EN_ANAHUAC.split(", ")


def test_la_busqueda_no_devuelve_descripciones(llamar, conexion):
    r = llamar(metrica="buscar", texto="caja", limite=50)
    descripciones = [d for (d,) in conexion.execute("SELECT descripcion FROM tickets").fetchall()]
    texto = r.texto_registro()
    assert not any(d in texto for d in descripciones)
    assert "descripcion" not in r.consulta.split("FROM")[0]


@pytest.mark.parametrize("palabra, texto, encuentra", [
    ("caja", "no abren las cajas", True),
    ("cajon", "los cajones no cierran", True),
    ("cajas", "la caja no abre", True),
    ("caja", "el cajero no llego", False),
    ("caja 3", "la caja 31 no abre", False),
    ("caja 3", "las cajas 3 y 4", True),
])
def test_el_patron_pide_la_palabra_completa_con_o_sin_plural(conexion, palabra, texto, encuentra):
    termino = terminos_de_busqueda(palabra)[0]
    assert conexion.execute("SELECT regexp_matches(?, ?)", [texto, termino.patron()]).fetchone()[0] is encuentra


def test_un_texto_de_solo_palabras_vacias_no_busca(llamar):
    r = llamar(metrica="buscar", texto="de la que")
    assert r.estado == "sin_resultados"
    assert not r.cifras


def test_buscar_sin_texto_no_busca(llamar):
    r = llamar(metrica="buscar")
    assert r.estado == "sin_resultados"


def test_el_texto_viaja_como_parametro(llamar):
    r = llamar(metrica="buscar", texto="caja'; DROP TABLE tickets; --")
    assert "DROP" not in r.consulta
    assert r.estado in {"ok", "sin_resultados"}


def test_el_conteo_con_texto_cuenta_los_que_lo_mencionan(llamar):
    r = llamar(texto="contraseña")
    assert r.cifras["tickets"] == str(len(CONTRASENA))


# ------------------------------------------------------------------ detalle y privacidad (P34, P43, K11)


def test_la_descripcion_de_metrica_dice_que_detalle_no_necesita_los_folios():
    # P43 pide el detalle de varios tickets. Con la métrica sin describir, el modelo probaba conteo, buscar y más
    # frecuentes para hallar los folios, y se acababan las 4 solicitudes antes de contestar.
    descripcion = EntradaTickets.model_fields["metrica"].description
    assert "detalle" in descripcion
    assert "sin pedir antes los folios" in descripcion

@pytest.fixture(scope="module")
def datos_1043():
    descripcion = leer_tickets()[TICKET_CON_DATOS]["descripcion"]
    celular = re.findall(r"\d[\d .\-]{8,}\d", descripcion)
    nombre = re.match(r"Reporta ([^,]+),", descripcion)
    assert len(celular) == 1 and nombre, "la descripción del 1043 cambió de forma; revisa la prueba"
    partes = [DatoPersonal(p) for p in nombre.group(1).split() if len(p) > 3]
    return [DatoPersonal(v) for v in variantes_celular(celular[0])] + [DatoPersonal(nombre.group(1))] + partes


def _sin_datos_personales(resultado, datos):
    for texto in (resultado.texto_modelo(), resultado.texto_registro()):
        for dato in datos:
            assert dato not in texto, "un dato personal del 1043 aparece en el resultado"


def test_p43_mantenimiento_de_valle_norte_en_junio_da_5_sin_datos_personales(llamar, datos_1043):
    r = llamar(metrica="detalle", tiendas=["valle norte"], categoria="Mantenimiento", periodo="junio")
    assert r.estado == "ok"
    assert ids(r) == TICKETS_P43
    assert r.cifras["tickets"] == "5"
    assert all(fila["descripcion"] for fila in r.filas)
    _sin_datos_personales(r, datos_1043)


def test_p34_el_detalle_del_1043_sale_enmascarado(llamar, datos_1043):
    r = llamar(metrica="detalle", ticket_id=TICKET_CON_DATOS)
    assert ids(r) == ["1043"]
    fila = r.filas[0]
    assert "<TELEFONO_1>" in fila["descripcion"]
    assert fila["tienda"] == "Tienda Valle Norte"
    assert fila["prioridad"] == "Alta"
    assert fila["cerrado"].startswith("2026-06-19")
    assert privacidad.buscar_datos_personales(fila["descripcion"]) == []
    _sin_datos_personales(r, datos_1043)


def test_el_detalle_ampliado_entrega_el_1043_enmascarado(llamar, datos_1043):
    # El 1043 se encuentra por su tema: la búsqueda ampliada no abre otro camino hacia sus datos (K11, D37-A).
    r = llamar(metrica="detalle", texto="puertas cortinas", limite=50)
    assert r.filtros["ampliada"] == "tema"
    assert "1043" in ids(r)
    assert "<TELEFONO_1>" in next(fila for fila in r.filas if fila["ticket_id"] == "1043")["descripcion"]
    _sin_datos_personales(r, datos_1043)


def test_el_detalle_enmascara_aunque_la_base_traiga_el_dato():
    """Defensa en profundidad: una base vieja, sin enmascarar, tampoco entrega el teléfono (datos inventados)."""
    con = duckdb.connect()
    con.execute("CREATE TABLE tiendas AS SELECT 'T01' AS tienda_id, 'Tienda Uno' AS nombre, 'Ciudad' AS ciudad, 'Supermercado' AS formato")
    con.execute("CREATE TABLE alias (tipo VARCHAR, alias VARCHAR, valor VARCHAR)")
    con.execute(
        "CREATE TABLE catalogo (sku VARCHAR, nombre VARCHAR, categoria VARCHAR, subcategoria VARCHAR, "
        "fecha_baja DATE, sku_sustituto VARCHAR)"
    )
    con.execute("""
        CREATE TABLE v_tickets AS SELECT 1 AS ticket_id, TIMESTAMP '2026-06-01 10:00' AS fecha_creacion,
            'T01' AS tienda_id, 'Tienda Uno' AS tienda, 'Supermercado' AS formato, 'RH' AS categoria,
            'Baja' AS prioridad, 'Abierto' AS estado, 'Puerta' AS titulo, 'puerta' AS titulo_normalizado,
            'Puertas' AS tema, 'Reporta Ana Ruiz, cel 55.0000.1111, la puerta no cierra' AS descripcion,
            NULL::TIMESTAMP AS fecha_cierre
    """)
    r = tickets(con.cursor(), EntradaTickets(metrica="detalle", ticket_id=1))
    descripcion = r.filas[0]["descripcion"]
    assert "55.0000.1111" not in descripcion and "Ana Ruiz" not in descripcion
    assert "<TELEFONO_1>" in descripcion and "<PERSONA_1>" in descripcion


def test_un_folio_que_no_existe_no_inventa_ticket(llamar):
    r = llamar(metrica="detalle", ticket_id=9999)
    assert r.estado == "sin_resultados"
    assert r.filas == []
    assert r.detalle["folio_minimo"] == 1001
    assert r.detalle["folio_maximo"] == 1285


def test_k11_el_registro_de_un_conteo_no_trae_descripciones(llamar, conexion):
    descripciones = [d for (d,) in conexion.execute("SELECT descripcion FROM tickets").fetchall()]
    for campos in ({}, {"agrupar_por": "tienda"}, {"texto": "caja"}, {"ticket_id": TICKET_CON_DATOS}):
        r = llamar(**campos)
        texto = r.texto_registro()
        assert not any(d in texto for d in descripciones)
        assert all("descripcion" not in fila for fila in r.filas)
        assert "descripcion" not in r.consulta.split("FROM")[0]


def test_el_detalle_respeta_el_limite_y_lo_dice(llamar):
    r = llamar(metrica="detalle", tiendas=["valle norte"], limite=3)
    assert len(r.filas) == 3
    assert r.n_filas == 72
    assert any("3 de 72" in nota for nota in r.notas)


# ------------------------------------------------------------------ errores con tipo y formato

def test_una_tienda_que_no_existe_da_entidad_inexistente(llamar):
    r = llamar(tiendas=["guadalupe"])
    assert r.estado == "entidad_inexistente"
    assert not r.cifras


def test_monterrey_da_entidad_ambigua(llamar):
    r = llamar(tiendas=["monterrey"])
    assert r.estado == "entidad_ambigua"


def test_un_periodo_fuera_del_corte_da_fuera_de_periodo(llamar):
    r = llamar(periodo="octubre")
    assert r.estado == "fuera_de_periodo"
    assert not r.cifras


def test_la_interpretacion_de_la_tienda_llega_a_las_notas(llamar):
    r = llamar(tiendas=["cumbre"], situacion="sin_cerrar")
    assert r.cifras["tickets"] == "8"
    assert any("Tienda Cumbres (T02)" in nota for nota in r.notas)


def test_un_campo_desconocido_es_error_de_validacion():
    with pytest.raises(ValidationError):
        EntradaTickets(metrica="conteo", sucursal="T01")


@pytest.mark.parametrize("campos", [
    {}, {"situacion": "sin_cerrar", "agrupar_por": "tienda"}, {"metrica": "antiguedad"},
    {"metrica": "cumplimiento", "agrupar_por": "tienda"}, {"metrica": "mas_frecuentes"},
    {"metrica": "buscar", "texto": "caja"}, {"metrica": "detalle", "ticket_id": 1043},
])
def test_las_cifras_y_celdas_son_texto_y_el_json_es_valido(llamar, campos):
    r = llamar(**campos)
    assert all(isinstance(v, str) for v in r.cifras.values())
    assert all(v is None or isinstance(v, str) for fila in r.filas for v in fila.values())
    json.loads(r.texto_modelo())
    assert "consulta" not in json.loads(r.texto_modelo())
    assert r.consulta


def test_la_misma_entrada_da_el_mismo_resultado(llamar):
    primero = llamar(metrica="mas_frecuentes", limite=5).texto_registro()
    assert all(llamar(metrica="mas_frecuentes", limite=5).texto_registro() == primero for _ in range(3))


@pytest.mark.parametrize("campos", [
    {}, {"situacion": "sin_cerrar"}, {"situacion": "cerrado"},
    {"situacion": "sin_cerrar", "tiendas": ["lomas del sur"]}, {"situacion": "cerrado", "tiendas": ["valle norte"]},
], ids=["P15", "P16", "P18", "P37", "P56"])
def test_un_conteo_simple_pesa_300_tokens_o_menos(llamar, campos):
    """El criterio de T2.1 para una cifra simple, con la estimación por arriba de comun.estimar_tokens."""
    texto = llamar(**campos).texto_modelo()
    assert comun.estimar_tokens(texto) <= 300, texto


# ------------------------------------------------------------------ dentro del agente (sin modelo real)

def test_en_el_agente_la_herramienta_recibe_los_argumentos_del_modelo(conexion):
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    recibido = []

    def modelo(mensajes, info: AgentInfo):
        if len(mensajes) == 1:
            argumentos = {"metrica": "mas_frecuentes", "tiendas": ["anahuac"], "limite": 1}
            return ModelResponse(parts=[ToolCallPart("tickets_mesa", argumentos)])
        recibido.extend(p for p in mensajes[-1].parts if isinstance(p, ToolReturnPart))
        return ModelResponse(parts=[TextPart("listo")])

    agente = Agent(FunctionModel(modelo))

    @agente.tool_plain
    def tickets_mesa(entrada: EntradaTickets) -> str:
        return tickets(conexion.cursor(), entrada).texto_modelo()

    agente.run_sync("que es lo q mas falla en anahuac")
    datos = json.loads(recibido[0].model_response_str())
    assert [fila["tickets"] for fila in datos["filas"]] == ["4", "4"]
    assert "consulta" not in datos
