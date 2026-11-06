"""Pruebas de config/supuestos.yaml y agente/definiciones.py (T1.1).

Una prueba por definición de la ficha de T1.1, con las cifras de la rúbrica cuando la definición
cambia una cifra (P01, P02, P03, P04, P05, P12, P19, P29, P56, P57 y el "ayer" de D3-A). Los CSV
se leen de datos/ con DuckDB en memoria: no se construye ninguna base ni se toca build/. Para
probar que la configuración manda, se copia supuestos.yaml a un directorio temporal, se cambia una
línea y se carga la copia.
"""

import calendar
import datetime as dt
from decimal import Decimal
from pathlib import Path
from statistics import NormalDist

import duckdb
import pytest
import yaml

from agente import definiciones as d

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "datos"
YAML = RAIZ / "config" / "supuestos.yaml"


@pytest.fixture(scope="module")
def con():
    """Vistas sobre los CSV originales con los montos en DECIMAL(12,2), para comparar al centavo."""
    conexion = duckdb.connect()
    conexion.execute(
        f"CREATE VIEW ventas AS SELECT * FROM read_csv('{(DATOS / 'ventas.csv').as_posix()}', "
        "types={'venta_neta_mxn': 'DECIMAL(12,2)', 'descuento_mxn': 'DECIMAL(12,2)'})"
    )
    for tabla, archivo in [("catalogo", "catalogo_productos.csv"), ("tiendas", "tiendas.csv"),
                           ("inventario", "inventario.csv"), ("tickets", "tickets_mesa_servicio.csv")]:
        conexion.execute(f"CREATE VIEW {tabla} AS SELECT * FROM read_csv('{(DATOS / archivo).as_posix()}')")
    yield conexion
    conexion.close()


@pytest.fixture(scope="module")
def s():
    return d.cargar()


def _copia(tmp_path, cambiar):
    """Carga una copia de supuestos.yaml con un cambio aplicado sobre el diccionario."""
    with open(YAML, encoding="utf-8") as archivo:
        contenido = yaml.safe_load(archivo)
    cambiar(contenido)
    ruta = tmp_path / "supuestos.yaml"
    ruta.write_text(yaml.safe_dump(contenido, allow_unicode=True), encoding="utf-8")
    return d.cargar(ruta)


def _uno(con, sql, *parametros):
    return con.execute(sql, list(parametros)).fetchone()[0]


# --- La configuración carga y es la única fuente -------------------------------------------------

def test_supuestos_carga_el_archivo_del_repositorio():
    assert d.supuestos() == d.cargar(YAML)


def test_una_clave_desconocida_no_pasa_en_silencio(tmp_path):
    with pytest.raises(ValueError):
        _copia(tmp_path, lambda c: c["ventas"].update(incluir_devolucion=False))


# --- Cortes con su fuente (C3.3) ----------------------------------------------------------------

def test_cada_corte_dice_su_archivo_y_de_donde_sale(s):
    for nombre in ("ventas", "tickets", "inventario"):
        corte = getattr(s.cortes, nombre)
        assert (DATOS / corte.fuente).is_file(), nombre
        assert corte.origen.strip(), nombre


def test_el_corte_de_ventas_coincide_con_los_datos(s, con):
    desde, hasta = con.execute("SELECT min(fecha), max(fecha) FROM ventas").fetchone()
    assert (s.cortes.ventas.desde, s.cortes.ventas.hasta) == (dt.date(2026, 6, 1), dt.date(2026, 8, 31))
    assert (desde, hasta) == (s.cortes.ventas.desde, s.cortes.ventas.hasta)


def test_el_corte_de_tickets_cubre_el_ultimo_ticket(s, con):
    ultimo = _uno(con, "SELECT max(greatest(fecha_creacion, coalesce(fecha_cierre, fecha_creacion))) FROM tickets")
    assert s.cortes.tickets.hasta == dt.date(2026, 8, 31)
    assert ultimo.date() == s.cortes.tickets.hasta


def test_el_corte_de_inventario_es_el_1_de_septiembre(s, con):
    assert s.cortes.inventario.fecha == dt.date(2026, 9, 1)
    assert _uno(con, "SELECT max(fecha_ultimo_conteo) FROM inventario") < s.cortes.inventario.fecha


# --- Fecha de referencia (D3-A, P50) --------------------------------------------------------------

def test_hoy_es_el_dia_siguiente_al_ultimo_dato(s):
    assert s.fecha_referencia == dt.date(2026, 9, 1)
    assert s.fecha_referencia == s.cortes.ventas.hasta + dt.timedelta(days=1)
    assert d.ayer(s) == dt.date(2026, 8, 31)


def test_ayer_en_valle_norte_da_la_cifra_de_d3a(s, con):
    venta = _uno(con, f"SELECT sum(venta_neta_mxn) FROM ventas WHERE fecha = ? AND tienda_id = 'T01' "
                      f"AND {s.ventas.condicion()}", d.ayer(s))
    assert venta == Decimal("74178.52")


def test_el_aviso_de_fecha_relativa_dice_la_interpretacion_y_que_la_fecha_real_no_tiene_datos(s):
    aviso = d.aviso_fecha_relativa(dt.date(2026, 10, 30), s)
    assert "1 de septiembre de 2026" in aviso
    assert "30 de octubre de 2026" in aviso
    assert "no hay datos" in aviso


# --- Venta neta con devoluciones (P03, criterio de aceptación de T1.1) ----------------------------

def test_p03_descuenta_las_devoluciones(s, con):
    assert s.ventas.incluir_devoluciones is True
    total = _uno(con, f"SELECT sum(venta_neta_mxn) FROM ventas WHERE {s.ventas.condicion()}")
    assert total == Decimal("17228300.26")


def test_excluir_devoluciones_en_la_configuracion_cambia_p03(tmp_path, con):
    copia = _copia(tmp_path, lambda c: c["ventas"].update(incluir_devoluciones=False))
    total = _uno(con, f"SELECT sum(venta_neta_mxn) FROM ventas WHERE {copia.ventas.condicion()}")
    assert total == Decimal("17272235.76")


def test_las_devoluciones_son_las_filas_con_unidades_negativas(s, con):
    devuelto = _uno(con, f"SELECT sum(venta_neta_mxn) FROM ventas WHERE {s.ventas.condicion_devolucion()}")
    assert devuelto == Decimal("-43935.50")


def test_la_condicion_acepta_el_alias_de_la_tabla(s, tmp_path, con):
    copia = _copia(tmp_path, lambda c: c["ventas"].update(incluir_devoluciones=False))
    assert copia.ventas.condicion("v") == "v.unidades > 0"
    total = _uno(con, f"SELECT sum(v.venta_neta_mxn) FROM ventas v WHERE {s.ventas.condicion('v')}")
    assert total == Decimal("17228300.26")


# --- Sustitución por sku_sustituto (P01) ----------------------------------------------------------

def _sustitutos(con):
    return dict(con.execute("SELECT sku, sku_sustituto FROM catalogo").fetchall())


def test_un_sku_se_liga_con_su_sustituto_en_los_dos_sentidos(s, con):
    catalogo = _sustitutos(con)
    assert d.skus_equivalentes("SKU-1001", catalogo, s) == {"SKU-1001", "SKU-1121"}
    assert d.skus_equivalentes("SKU-1121", catalogo, s) == {"SKU-1001", "SKU-1121"}
    assert d.skus_equivalentes("SKU-1002", catalogo, s) == {"SKU-1002"}


def test_p01_leche_entera_de_julio_suma_las_dos_presentaciones(s, con):
    skus = sorted(d.skus_equivalentes("SKU-1001", _sustitutos(con), s))
    unidades, venta = con.execute(
        f"SELECT sum(unidades), sum(venta_neta_mxn) FROM ventas WHERE sku IN (SELECT unnest(?)) "
        f"AND month(fecha) = 7 AND {s.ventas.condicion()}", [skus]).fetchone()
    assert (unidades, venta) == (3336, Decimal("91773.10"))


def test_sin_sumar_sustitutos_queda_solo_el_sku_pedido(tmp_path, con):
    copia = _copia(tmp_path, lambda c: c["ventas"].update(sumar_sustitutos=False))
    assert d.skus_equivalentes("SKU-1001", _sustitutos(con), copia) == {"SKU-1001"}


# --- Unidad de venta (P02, PT-14) ----------------------------------------------------------------

def test_los_16_productos_a_granel_se_venden_por_kilo(s, con):
    nombres = dict(con.execute("SELECT sku, nombre FROM catalogo").fetchall())
    por_kilo = {sku for sku, nombre in nombres.items() if s.unidad_venta.de(nombre) == "kg"}
    assert len(por_kilo) == 16
    assert "SKU-1035" in por_kilo
    assert s.unidad_venta.de("Yogur natural 1 kg") == "pieza"
    assert s.unidad_venta.de("Huevo blanco 12 piezas") == "pieza"
    assert s.unidad_venta.de("Bolillo pieza") == "pieza"


def test_p02_kilos_de_pollo_no_mezclan_paquetes(s, con):
    filas = con.execute("SELECT sku, nombre FROM catalogo WHERE nombre ILIKE '%pollo%'").fetchall()
    assert not s.unidad_venta.compatibles([nombre for _, nombre in filas])
    kilos = [sku for sku, nombre in filas if s.unidad_venta.de(nombre) == "kg"]
    total = _uno(con, f"SELECT sum(unidades) FROM ventas WHERE sku IN (SELECT unnest(?)) "
                      f"AND {s.ventas.condicion()}", kilos)
    assert total == 6954


# --- Ecommerce solo en Supermercado (P05) ----------------------------------------------------------

def test_ecommerce_solo_existe_en_supermercado(s, con):
    assert s.ventas.canal_existe("Supermercado", "Ecommerce")
    assert not s.ventas.canal_existe("Mi Tienda", "Ecommerce")
    assert s.ventas.canal_existe("Mi Tienda", "Piso")
    formatos = {f for (f,) in con.execute(
        "SELECT DISTINCT t.formato FROM ventas v JOIN tiendas t USING (tienda_id) WHERE v.canal = 'Ecommerce'"
    ).fetchall()}
    assert formatos == set(s.ventas.canal_ecommerce_formatos)


def test_p05_venta_en_linea(s, con):
    total = _uno(con, f"SELECT sum(venta_neta_mxn) FROM ventas WHERE canal = 'Ecommerce' AND {s.ventas.condicion()}")
    assert total == Decimal("357010.50")


# --- Reorden con < (P04) --------------------------------------------------------------------------

def test_reorden_es_estrictamente_menor(s):
    assert s.inventario.bajo_reorden(96, 97)
    assert not s.inventario.bajo_reorden(97, 97)
    assert not s.inventario.bajo_reorden(98, 97)


def test_p04_hay_17_productos_bajo_el_punto_de_reorden(s, con):
    assert _uno(con, f"SELECT count(*) FROM inventario WHERE {s.inventario.condicion()}") == 17


def test_el_operador_de_reorden_sale_de_la_configuracion(tmp_path):
    copia = _copia(tmp_path, lambda c: c["inventario"].update(operador_reorden="<="))
    assert copia.inventario.bajo_reorden(97, 97)
    assert copia.inventario.condicion("i") == "i.existencia <= i.punto_reorden"


# --- "Sin cerrar" y cerrado (P29, P56) ----------------------------------------------------------

def test_sin_cerrar_es_sin_fecha_de_cierre(s, con):
    por_estado = dict(con.execute(
        f"SELECT estado, count(*) FROM tickets WHERE {s.tickets.condicion_sin_cerrar()} GROUP BY estado"
    ).fetchall())
    assert por_estado == {"Abierto": 9, "En proceso": 8}


def test_p56_cerrados_en_valle_norte_cuentan_resuelto_y_cerrado(s, con):
    por_estado = dict(con.execute(
        f"SELECT estado, count(*) FROM tickets WHERE tienda_id = 'T01' AND {s.tickets.condicion_cerrado()} "
        "GROUP BY estado").fetchall())
    assert por_estado == {"Cerrado": 53, "Resuelto": 14}


# --- Antigüedad contra el fin del corte (P29) ----------------------------------------------------

def test_la_antiguedad_se_mide_contra_el_fin_del_31_de_agosto(s):
    assert s.fin_corte_tickets() == dt.datetime(2026, 9, 1, 0, 0)


def test_p29_antiguedad_de_los_tickets_mas_viejos_de_cumbres(s, con):
    creados = dict(con.execute("SELECT ticket_id, fecha_creacion FROM tickets WHERE ticket_id IN (1258, 1261)").fetchall())
    assert s.antiguedad_dias(creados[1258]) == 9.65
    assert s.antiguedad_dias(creados[1261]) == 9.32


# --- Cobertura según D20-A y rango de D23-D (P57, P12) -------------------------------------------

def test_la_base_de_cobertura_es_agosto_entre_31_dias(s):
    assert s.cobertura.mes_base == 8
    assert s.dias_mes_base() == 31 == calendar.monthrange(2026, 8)[1]


def _unidades_agosto(s, con, tienda, sku):
    return _uno(con, f"SELECT sum(unidades) FROM ventas WHERE tienda_id = ? AND sku = ? "
                     f"AND month(fecha) = ? AND {s.ventas.condicion()}", tienda, sku, s.cobertura.mes_base)


def test_p57_centro_de_cobertura_del_refresco_de_cola_600(s, con):
    unidades = _unidades_agosto(s, con, "T01", "SKU-1078")
    assert unidades == 1428
    media = s.media_diaria_base(unidades)
    assert round(media, 2) == 46.06
    assert round(670 / media, 2) == 14.54


def test_p12_centro_de_cobertura_de_la_leche_deslactosada(s, con):
    unidades = _unidades_agosto(s, con, "T01", "SKU-1002")
    assert unidades == 667
    assert round(450 / s.media_diaria_base(unidades), 2) == 20.91


def test_pesos_y_z_del_rango_de_d23(s):
    assert s.cobertura.pesos_varianza == {6: 0.15, 7: 0.25, 8: 0.60}
    assert sum(s.cobertura.pesos_varianza.values()) == pytest.approx(1.0)
    assert s.cobertura.z == pytest.approx(NormalDist().inv_cdf(0.9), abs=0.005)


def test_los_pesos_que_no_suman_uno_se_rechazan(tmp_path):
    with pytest.raises(ValueError):
        _copia(tmp_path, lambda c: c["cobertura"].update(pesos_varianza={6: 0.2, 7: 0.25, 8: 0.6}))


# --- Horario hábil según D5 y tiempos objetivo (P19) --------------------------------------------

def test_d5_a_el_horario_habil_es_el_de_la_tienda(s):
    horario = s.tickets.horario_habil
    assert (horario.desde, horario.hasta) == (dt.time(7), dt.time(22))
    assert horario.dias_semana == frozenset(range(7))
    assert horario.prioridades == ("Baja", "Media")
    assert horario.horas_por_dia == 15
    assert s.dudas["D5"].opcion == "A"
    assert s.dudas["D5"].estado == "cerrada"
    assert horario.definicion.startswith("se toma como horario hábil de 7:00 a 22:00 todos los días")
    assert "MAN-OPS-007 §2" in horario.definicion


@pytest.mark.parametrize(
    ("inicio", "fin", "horas"),
    [
        ("2026-07-06 10:00", "2026-07-06 12:30", 2.5),  # dentro de la ventana
        ("2026-07-06 21:00", "2026-07-07 08:00", 2),  # la noche no cuenta
        ("2026-07-06 23:00", "2026-07-07 06:00", 0),  # toda la noche
        ("2026-07-06 05:00", "2026-07-06 08:00", 1),  # empieza antes de abrir
        ("2026-07-06 10:00", "2026-07-11 10:00", 75),  # 5 días exactos
        ("2026-07-04 21:00", "2026-07-05 08:00", 2),  # sábado a domingo: todos los días cuentan
    ],
)
def test_las_horas_habiles_solo_cuentan_dentro_de_la_ventana(s, inicio, fin, horas):
    desde, hasta = dt.datetime.fromisoformat(inicio), dt.datetime.fromisoformat(fin)
    assert s.tickets.horario_habil.horas_entre(desde, hasta) == horas


def test_los_dias_habiles_de_p20_valen_las_horas_de_la_ventana(s):
    assert s.tickets.objetivo_habil("Baja") == 75
    assert s.tickets.objetivo_habil("Media") == 30
    assert s.tickets.objetivo_habil("Alta") is None
    assert s.tickets.objetivo_habil("Crítica") is None


def test_con_la_ventana_alta_y_critica_siguen_en_horas_naturales(s):
    creacion = dt.datetime(2026, 7, 6, 21, 0)
    # 10 horas naturales y solo 2 hábiles: la tabla de P20 no pone «hábiles» en Alta.
    assert not s.tickets.cumple_en_horario_habil("Alta", creacion, dt.datetime(2026, 7, 7, 7, 0))
    assert s.tickets.cumple_en_horario_habil("Alta", creacion, dt.datetime(2026, 7, 7, 5, 0))


def test_baja_cumple_justo_en_la_meta_y_no_un_minuto_despues(s):
    creacion = dt.datetime(2026, 7, 6, 10, 0)
    assert s.tickets.cumple_en_horario_habil("Baja", creacion, dt.datetime(2026, 7, 11, 10, 0))
    assert not s.tickets.cumple_en_horario_habil("Baja", creacion, dt.datetime(2026, 7, 11, 10, 1))


def test_sin_ventana_todo_se_mide_en_horas_naturales(tmp_path):
    sin_ventana = _copia(tmp_path, lambda c: c["tickets"].update(horario_habil=None))
    assert sin_ventana.tickets.objetivo_habil("Baja") is None
    creacion = dt.datetime(2026, 7, 6, 10, 0)
    assert sin_ventana.tickets.cumple_en_horario_habil("Baja", creacion, dt.datetime(2026, 7, 11, 10, 0))
    assert not sin_ventana.tickets.cumple_en_horario_habil("Baja", creacion, dt.datetime(2026, 7, 11, 10, 1))


def test_la_ventana_sale_de_supuestos_y_los_dias_que_no_cuentan_detienen_el_reloj(tmp_path):
    otra = _copia(tmp_path, lambda c: c["tickets"]["horario_habil"].update(
        desde="09:00", hasta="18:00", dias_semana=[0, 1, 2, 3, 4]
    ))
    assert otra.tickets.objetivo_habil("Baja") == 45
    assert otra.tickets.objetivo_habil("Media") == 18
    viernes, lunes = dt.datetime(2026, 7, 10, 17, 0), dt.datetime(2026, 7, 13, 10, 0)
    assert otra.tickets.horario_habil.horas_entre(viernes, lunes) == 2
    assert "de 9:00 a 18:00 lunes, martes, miércoles, jueves, viernes" in otra.tickets.horario_habil.definicion


@pytest.mark.parametrize(
    "cambio",
    [
        {"desde": "22:00", "hasta": "07:00"},
        {"dias_semana": [7]},
        {"dias_semana": []},
        {"prioridades": ["Urgente"]},
        {"otra_clave": 1},
    ],
)
def test_un_horario_habil_mal_escrito_es_un_error(tmp_path, cambio):
    with pytest.raises(ValueError):
        _copia(tmp_path, lambda c: c["tickets"]["horario_habil"].update(cambio))


def test_d5_cerrada_con_la_opcion_recomendada(s):
    assert s.tickets.horario_habil is not None
    assert s.dudas["D5"].opcion == "A"
    assert s.dudas["D5"].recomendada == "A"
    assert not s.tickets.primera_respuesta_medible
    assert "primera_respuesta" in s.metricas_no_calculables


def test_p19_86_de_268_cumplen_en_horas_naturales(s, con):
    filas = con.execute(
        f"SELECT prioridad, epoch(fecha_cierre - fecha_creacion) / 3600 FROM tickets "
        f"WHERE {s.tickets.condicion_cerrado()}").fetchall()
    assert len(filas) == 268
    assert sum(s.tickets.cumple_objetivo(prioridad, horas) for prioridad, horas in filas) == 86


def test_el_objetivo_que_llega_justo_a_la_meta_cumple(s):
    assert s.tickets.cumple_objetivo("Alta", 8)
    assert not s.tickets.cumple_objetivo("Alta", 8.01)


# --- D1, D2 y D4 del handoff --------------------------------------------------------------------

def test_d1_a_la_merma_real_no_se_calcula(s):
    assert s.dudas["D1"].opcion == "A"
    assert "merma_real" in s.metricas_no_calculables


def test_d1_a_dice_que_los_datos_no_traen_registros_sin_nombrar_el_sistema(s):
    # El usuario pidió el 2026-11-04 que la gerente no lea dónde viven los registros: antes esta prueba exigía «SIGMA».
    razon = s.metricas_no_calculables["merma_real"]
    assert "los datos a los que tengo acceso" in razon
    assert "SIGMA" not in razon
    assert "ejercicio" not in razon


def test_d2_c_la_politica_de_mermas_aplica_a_mi_tienda_como_supuesto(s):
    assert s.dudas["D2"].opcion == "C"
    assert s.alcance("POL-OPS-014", "Supermercado").cubre
    alcance = s.alcance("POL-OPS-014", "Mi Tienda")
    assert not alcance.cubre
    assert alcance.por_supuesto
    assert "Mi Tienda" in alcance.salvedad


def test_cambiar_d2_a_la_opcion_a_quita_el_supuesto(tmp_path):
    copia = _copia(tmp_path, lambda c: c["dudas"]["D2"].update(opcion="A"))
    alcance = copia.alcance("POL-OPS-014", "Mi Tienda")
    assert not alcance.cubre
    assert not alcance.por_supuesto


def test_d4_a_el_manual_cubre_mi_tienda_sin_salvedad(s):
    assert s.dudas["D4"].opcion == "A"
    assert s.alcance("MAN-OPS-007", "Supermercado").cubre
    alcance = s.alcance("MAN-OPS-007", "Mi Tienda")
    assert alcance.cubre
    assert alcance.salvedad is None


def test_cambiar_la_respuesta_de_d4_cambia_el_alcance(tmp_path):
    copia = _copia(tmp_path, lambda c: c["dudas"]["D4"].update(opcion="B"))
    alcance = copia.alcance("MAN-OPS-007", "Mi Tienda")
    assert not alcance.cubre
    assert not alcance.por_supuesto
    assert "Mi Tienda" in alcance.salvedad


def test_d1_d2_d4_y_d5_estan_cerradas(s):
    assert {duda: s.dudas[duda].estado for duda in ("D1", "D2", "D4", "D5")} == {
        "D1": "cerrada",
        "D2": "cerrada",
        "D4": "cerrada",
        "D5": "cerrada",
    }


def test_un_documento_sin_alcance_declarado_es_un_error(s):
    with pytest.raises(KeyError):
        s.alcance("PRO-LOG-003", "Supermercado")


# --- Supuestos que no cambian cifras --------------------------------------------------------------

def test_margen_y_metricas_no_calculables_traen_su_motivo(s):
    assert s.margen.supuesto.strip()
    for metrica, motivo in s.metricas_no_calculables.items():
        assert motivo.strip(), metrica
