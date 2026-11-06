"""Correcciones que salieron de auditar las respuestas contra los CSV y las políticas (handoff del 2026-11-05).

Cada prueba fija lo que la herramienta entrega, para que la respuesta no tenga que suponerlo: la base de un porcentaje, la
definición de un filtro sin resultados, los lotes de una acción pedida o una referencia para comparar una venta diaria. No
llaman a ningún modelo. Las consultas son propias: ninguna copia un texto del banco (G11).
"""

import datetime as dt
from decimal import Decimal

import duckdb
import pytest

from agente import consultas, construir, resolver, verificacion
from agente.herramientas.buscar_politicas import buscar_politicas, leer_indice
from agente.herramientas.caducidades import caducidades
from agente.herramientas.comun import EntradaCaducidades, EntradaInventario, EntradaPoliticas, EntradaTickets, EntradaVentas
from agente.herramientas.inventario import inventario
from agente.herramientas import ventas as ventas_modulo
from agente.herramientas.tickets import tickets
from agente.herramientas.ventas import ventas

from tests.conftest import DIR_DATOS

HOY_REAL = dt.date(2026, 10, 31)


@pytest.fixture(scope="module")
def conexion(base_construida):
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        yield con


def venta(conexion, **campos):
    return ventas(conexion.cursor(), EntradaVentas(**campos), hoy_real=HOY_REAL)


def lotes(conexion, **campos):
    return caducidades(conexion.cursor(), EntradaCaducidades(**campos), hoy_real=HOY_REAL)


def de_tickets(conexion, **campos):
    return tickets(conexion.cursor(), EntradaTickets(**campos), hoy_real=HOY_REAL)


# ------------------------------------------------------------------ la base de un porcentaje


def test_el_peso_de_un_canal_en_una_sola_tienda_dice_que_la_base_es_esa_tienda(conexion):
    resultado = venta(conexion, canal="Ecommerce", tiendas=["Cumbres"], periodo="junio")
    porcentaje = resultado.cifras["pct_de_la_venta_de_las_tiendas"]
    nota = next(nota for nota in resultado.notas if "no de la venta de todas las tiendas" in nota)
    assert nota.startswith(f"Ecommerce pesa {porcentaje} de la venta de Tienda Cumbres (T02)")


def test_el_peso_de_un_canal_en_las_cuatro_tiendas_no_necesita_esa_salvedad(conexion):
    resultado = venta(conexion, canal="Ecommerce", periodo="junio")
    assert not any("no de la venta de todas las tiendas" in nota for nota in resultado.notas)


# ------------------------------------------------------------------ un filtro sin resultados


def test_un_detalle_sin_resultados_dice_que_estados_cuentan_como_sin_cerrar(conexion):
    resultado = de_tickets(conexion, metrica="detalle", situacion="sin_cerrar", prioridad="Crítica")
    assert resultado.estado == "sin_resultados"
    assert "Abierto y En proceso" in resultado.definicion
    assert "Resuelto" not in resultado.definicion


def test_un_detalle_cerrado_sin_resultados_dice_que_estados_cuentan_como_cerrado(conexion):
    resultado = de_tickets(conexion, metrica="detalle", situacion="cerrado", texto="dinosaurio")
    assert resultado.estado == "sin_resultados"
    assert "Resuelto y Cerrado" in resultado.definicion


# ------------------------------------------------------------------ una acción pedida son lotes


def test_una_accion_sobre_una_categoria_trae_sus_lotes_aunque_llegue_con_los_dias(conexion):
    sin_dias = lotes(conexion, accion="retirar", categoria="lácteos")
    con_dias = lotes(conexion, accion="retirar", categoria="lácteos", caduca_en_dias=0)
    assert con_dias.estado == "ok"
    assert con_dias.filas == sin_dias.filas
    assert con_dias.cifras == sin_dias.cifras
    assert con_dias.filas and all("existencia" in fila for fila in con_dias.filas)


def test_una_accion_sin_categoria_tambien_trae_los_lotes_aunque_llegue_con_los_dias(conexion):
    con_dias = lotes(conexion, accion="retirar", caduca_en_dias=0)
    assert con_dias.estado == "ok"
    assert con_dias.filas == lotes(conexion, accion="retirar").filas


def test_una_accion_con_los_dias_conserva_la_regla_de_esos_dias_en_una_nota(conexion):
    resultado = lotes(conexion, accion="retirar", categoria="lácteos", caduca_en_dias=3)
    nota = next(nota for nota in resultado.notas if "a 3 días" in nota)
    assert "Lácteos" in nota and "rebajar 35%" in nota


def test_una_accion_sobre_un_producto_con_los_dias_sigue_contando_desde_esos_dias(conexion):
    resultado = lotes(conexion, accion="retirar", producto="yogurt", caduca_en_dias=2)
    assert [(f["accion"], f["regla"]) for f in resultado.filas] == [("retirar", "§5.2")]
    assert "lotes" not in resultado.cifras


def test_sin_accion_los_dias_siguen_pidiendo_el_producto(conexion):
    resultado = lotes(conexion, caduca_en_dias=0)
    assert resultado.estado == "sin_resultados"
    assert "producto" in resultado.resumen


# ------------------------------------------------------------------ lo que cuentan las cifras y no traen las filas


def test_un_lote_de_otra_accion_va_en_una_nota_con_su_producto_y_su_fecha(conexion):
    pedido = lotes(conexion, accion="rebajar", tiendas=["Valle Norte"])
    otros = lotes(conexion, accion="retirar", tiendas=["Valle Norte"])
    assert otros.filas == []
    assert otros.cifras["lotes_a_rebajar"] == str(len(pedido.filas))
    fila = pedido.filas[0]
    nota = next(nota for nota in otros.notas if fila["producto"] in nota)
    assert "de septiembre de 2026" in nota and "rebajar" in nota and fila["descuento"] in nota


def test_sin_filtro_de_accion_no_hay_nota_de_otra_accion(conexion):
    resultado = lotes(conexion, tiendas=["Valle Norte"])
    assert not any("fuera de la acción pedida" in nota for nota in resultado.notas)


def test_con_demasiados_lotes_de_otra_accion_la_nota_solo_dice_como_verlos(conexion):
    resultado = lotes(conexion, accion="retirar", ventana_dias=30)
    nota = next(nota for nota in resultado.notas if "accion todas" in nota)
    assert "fuera de la acción pedida" in nota


# ------------------------------------------------------------------ una venta diaria con qué compararse


def test_la_venta_por_dia_trae_la_mediana_diaria_del_mes_con_su_nombre(conexion):
    resultado = venta(conexion, tiendas=["Valle Norte"], grano="dia", desde="2026-06-10", hasta="2026-06-12")
    assert resultado.cifras["mediana_diaria_de_junio_de_2026"]
    nota = next(nota for nota in resultado.notas if "mediana_diaria_de_junio_de_2026" in nota)
    assert "30 días" in nota


def test_la_mediana_cuenta_los_mismos_filtros_que_la_venta(conexion):
    toda = venta(conexion, tiendas=["Valle Norte"], grano="dia", desde="2026-06-10", hasta="2026-06-12")
    canal = venta(conexion, tiendas=["Valle Norte"], canal="Ecommerce", grano="dia", desde="2026-06-10", hasta="2026-06-12")
    assert toda.cifras["mediana_diaria_de_junio_de_2026"] != canal.cifras["mediana_diaria_de_junio_de_2026"]


def test_un_mes_completo_por_dia_trae_su_promedio_diario_con_nombre(conexion):
    resultado = venta(conexion, tiendas=["Valle Norte"], grano="dia", desde="2026-06-01", hasta="2026-06-30", limite=50)
    assert resultado.cifras["mediana_diaria_de_junio_de_2026"]
    assert resultado.cifras["promedio_diario_de_junio_de_2026"]
    assert resultado.cifras["promedio_diario_de_junio_de_2026"] != resultado.cifras["venta"]


def test_sin_grano_por_dia_no_se_agrega_la_mediana(conexion):
    resultado = venta(conexion, tiendas=["Valle Norte"], desde="2026-06-10", hasta="2026-06-12")
    assert not any(clave.startswith("mediana_diaria") for clave in resultado.cifras)


def test_una_venta_por_dia_que_cruza_de_mes_no_elige_un_mes(conexion):
    resultado = venta(conexion, tiendas=["Valle Norte"], grano="dia", desde="2026-06-29", hasta="2026-07-02")
    assert not any(clave.startswith("mediana_diaria") for clave in resultado.cifras)


# ------------------------------------------------------------------ una búsqueda trae títulos


def test_una_busqueda_con_tickets_dice_como_abrir_la_descripcion(conexion):
    resultado = de_tickets(conexion, metrica="buscar", texto="contraseña")
    assert resultado.estado == "ok"
    aviso = resultado.para_modelo()["detalle"]["para_abrir"]
    assert "metrica detalle" in aviso and "ticket_id" in aviso and "descripción" in aviso
    assert "una sola llamada" in aviso


def test_una_busqueda_sin_tickets_no_trae_ese_aviso(conexion):
    resultado = de_tickets(conexion, metrica="buscar", texto="dinosaurio")
    assert "para_abrir" not in resultado.detalle


# ------------------------------------------------------------------ un estado no es una tienda


def test_un_estado_con_varias_tiendas_pregunta_cual(conexion):
    resultado = venta(conexion, tiendas=["Nuevo León"], periodo="junio")
    assert resultado.estado == "entidad_ambigua"
    candidatos = resultado.detalle["candidatos"]
    assert [candidato.split()[0] for candidato in candidatos] == ["T01", "T02", "T03"]


def test_un_estado_con_una_tienda_es_esa_tienda(conexion):
    resultado = venta(conexion, tiendas=["Coahuila"], periodo="junio")
    assert resultado.estado == "ok"
    assert resultado.filtros["tiendas"] == ["T04"]
    assert any("Lomas del Sur (T04)" in nota for nota in resultado.notas)


def test_una_ciudad_con_dos_tiendas_sigue_siendo_ambigua(conexion):
    catalogos = resolver.cargar_catalogos(conexion.cursor())
    with pytest.raises(Exception) as error:
        catalogos.buscar_tiendas("Monterrey")
    assert len(error.value.detalle["candidatos"]) == 2


# ------------------------------------------------------------------ lo que dice el catálogo en un choque


@pytest.fixture(scope="module")
def con_indice(tmp_path_factory):
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")
    destino = tmp_path_factory.mktemp("politicas") / "tienda.duckdb"
    with consultas.abrir_consultas(construir.construir(DIR_DATOS, destino).base) as abiertas:
        yield abiertas


def _choque_de_perecedero(con_indice, consulta):
    with con_indice.sesion() as sesion:
        resultado = buscar_politicas(sesion, EntradaPoliticas(consulta=consulta))
    return next(choque for choque in resultado.detalle["choques"] if choque["paso"] == 3)


def test_el_choque_de_perecedero_dice_que_el_catalogo_lo_marca_como_no_perecedero(con_indice):
    choque = _choque_de_perecedero(con_indice, "me regresan unos nuggets de pollo sin nota, se aceptan")
    assert choque["dato_del_catalogo"].startswith("el catálogo marca como no perecedero")
    assert "SKU-1032" in choque["dato_del_catalogo"]


def test_el_choque_de_perecedero_dice_para_que_se_acude_al_area(con_indice):
    choque = _choque_de_perecedero(con_indice, "me regresan unos nuggets de pollo sin nota, se aceptan")
    assert "aclarar la definición" in choque["para_que_el_area"]
    assert "autorización" in choque["para_que_el_area"]


# ------------------------------------------------------------------ en qué documentos se buscó


def test_la_busqueda_de_politicas_dice_en_que_documentos_busco(con_indice):
    with con_indice.sesion() as sesion:
        resultado = buscar_politicas(sesion, EntradaPoliticas(consulta="reembolso con tarjeta de una compra"))
    aviso = resultado.para_modelo()["detalle"]["busqueda_en"]
    for documento in ("FAQ-OPS-001", "MAN-OPS-007", "POL-OPS-014", "PRO-SAC-021"):
        assert documento in aviso
    assert "ningún fragmento" in aviso


def test_la_busqueda_en_un_documento_dice_que_solo_busco_en_ese(con_indice):
    with con_indice.sesion() as sesion:
        resultado = buscar_politicas(sesion, EntradaPoliticas(consulta="reembolso con tarjeta", documento="PRO-SAC-021"))
    aviso = resultado.detalle["busqueda_en"]
    assert "PRO-SAC-021" in aviso and "POL-OPS-014" not in aviso


# ------------------------------------------------------------------ ventas: el lugar de cada día y qué sigue


def test_cada_dia_trae_su_lugar_de_mayor_a_menor(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], grano="dia", desde="2026-06-10", hasta="2026-06-14")
    por_venta = sorted(resultado.filas, key=lambda fila: float(fila["venta"].replace(",", "")), reverse=True)
    assert [fila["lugar_de_mayor_a_menor"] for fila in por_venta] == ["1", "2", "3", "4", "5"]


def test_una_venta_por_mes_no_trae_lugar(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], grano="mes", desde="2026-06-01", hasta="2026-07-31")
    assert all("lugar_de_mayor_a_menor" not in fila for fila in resultado.filas)


def test_la_venta_por_dia_de_un_solo_dia_dice_como_desglosarlo(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], grano="dia", desde="2026-06-10", hasta="2026-06-10")
    aviso = resultado.para_modelo()["detalle"]["para_desglosar"]
    assert "agrupar_por" in aviso and "categoria" in aviso and "canal" in aviso


def test_varios_dias_por_dia_no_piden_desglosar(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], grano="dia", desde="2026-06-10", hasta="2026-06-14")
    assert "para_desglosar" not in resultado.detalle


def test_los_m2_de_toda_la_tienda_dicen_hacia_donde_mueven_la_cifra(conexion):
    resultado = venta(conexion, metrica="venta_por_m2", tiendas=["Cumbres"], categoria="Bebidas", periodo="junio")
    nota = next(nota for nota in resultado.notas if "toda la tienda" in nota)
    assert "por debajo" in nota


def test_un_sku_del_grupo_sin_venta_en_el_periodo_lo_dice(conexion):
    resultado = venta(conexion, metrica="unidades", producto="leche", tiendas=["Valle Norte"], periodo="junio")
    assert not any("SKU-1121" in fila["sku"] for fila in resultado.filas)
    nota = next(nota for nota in resultado.notas if nota.startswith("sin venta en este periodo"))
    assert "SKU-1121" in nota


def test_si_todo_el_grupo_vendio_no_hay_nota_de_sin_venta(conexion):
    resultado = venta(conexion, metrica="unidades", producto="leche", tiendas=["Valle Norte"], periodo="julio")
    assert not any(nota.startswith("sin venta en este periodo") for nota in resultado.notas)


# ------------------------------------------------------------------ caducidades: lo que no se lee


def test_los_dias_dados_avisan_que_no_se_leyeron_lotes(conexion):
    resultado = lotes(conexion, producto="yogurt", caduca_en_dias=2)
    aviso = resultado.para_modelo()["detalle"]["antes_de_contestar"]
    assert "no lee lotes" in aviso


def test_el_dia_dado_tambien_avisa_que_no_se_leyeron_lotes(conexion):
    resultado = lotes(conexion, producto="yogurt", caduca_el="viernes")
    assert "no lee lotes" in resultado.detalle["antes_de_contestar"]


def test_la_nota_de_un_lacteo_a_dos_dias_dice_que_52_y_61_coinciden(conexion):
    resultado = lotes(conexion, producto="yogurt", caduca_en_dias=2)
    nota = next(nota for nota in resultado.notas if "50%" in nota)
    assert "§5.2 y §6.1, que coinciden" in nota


def test_la_nota_de_un_perecedero_a_un_dia_da_la_razon_de_6_1(conexion):
    resultado = lotes(conexion, producto="pechuga de pollo", caduca_en_dias=1)
    nota = next(nota for nota in resultado.notas if "60%" in nota)
    assert "porque §6.1" in nota and "sin excepción" in nota


def test_la_nota_de_la_lista_dice_de_que_documento_es_la_lista(conexion):
    resultado = lotes(conexion, categoria="lácteos")
    nota = next(nota for nota in resultado.notas if "no nombra" in nota)
    assert "la lista de POL-OPS-014 §5.2" in nota


# ------------------------------------------------------------------ tickets: lo que dicen los textos


def test_el_detalle_dice_que_lo_enmascarado_es_este_resultado(conexion):
    resultado = de_tickets(conexion, metrica="detalle", ticket_id=1001)
    assert "enmascarados en este resultado" in resultado.definicion


def test_la_herramienta_de_tickets_pide_la_regla_para_juzgar_una_prioridad():
    assert "buscar_politicas" in EntradaTickets.__doc__
    assert "prioridad" in EntradaTickets.__doc__


# ------------------------------------------------------------------ lo que se le dice al modelo sin filas


def test_un_total_sin_filas_no_le_dice_al_modelo_que_trae_cero_filas(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], periodo="junio")
    assert resultado.filas == []
    assert "n_filas" not in resultado.para_modelo()
    assert resultado.cifras["venta"]


def test_con_filas_el_modelo_si_recibe_cuantas_son(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], grano="dia", desde="2026-06-10", hasta="2026-06-12")
    assert resultado.para_modelo()["n_filas"] == 3


# ------------------------------------------------------------------ el principio cuando la regla que prevalece no da


def _choque_de(con_indice, consulta, identificador):
    with con_indice.sesion() as sesion:
        resultado = buscar_politicas(sesion, EntradaPoliticas(consulta=consulta))
    return next(choque for choque in resultado.detalle["choques"] if choque["id"] == identificador)


def test_el_choque_de_un_producto_vendido_caducado_trae_el_principio_de_no_negar_en_seco(con_indice):
    choque = _choque_de(con_indice, "devolución de un queso caducado con ticket, venta caducada", "CH-02")
    assert choque["principio"].startswith("PRO-SAC-021 §1.2")
    assert "escalando al nivel de autorización" in choque["texto_principio"]
    assert "no se niega en seco" in choque["si_no_procede"]


def test_otro_choque_de_la_faq_no_lleva_principio(con_indice):
    choque = _choque_de(con_indice, "plazo de devolución de un electrodoméstico con ticket", "CH-01")
    assert "principio" not in choque


# ------------------------------------------------------------------ la sugerencia de acudir al área no es del procedimiento


def test_acudir_al_area_no_se_le_atribuye_al_documento(con_indice):
    choque = _choque_de_perecedero(con_indice, "me regresan unos nuggets de pollo sin nota, se aceptan")
    assert "ningún documento lo pide" in choque["para_que_el_area"]


# ------------------------------------------------------------------ ventas: un día se puede desglosar


def test_una_venta_de_un_solo_dia_dice_como_desglosarlo(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], desde="2026-06-10", hasta="2026-06-10")
    assert "agrupar_por" in resultado.para_modelo()["detalle"]["para_desglosar"]


def test_un_dia_comparado_con_otro_tambien_dice_como_desglosarlo(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], desde="2026-06-10", hasta="2026-06-10", comparar_con="2026-06-09")
    assert "agrupar_por" in resultado.detalle["para_desglosar"]


def test_un_mes_sin_grano_no_trae_el_aviso_de_desglose(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], periodo="junio")
    assert "para_desglosar" not in resultado.detalle


def test_un_dia_ya_agrupado_no_trae_el_aviso_de_desglose(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], desde="2026-06-10", hasta="2026-06-10", agrupar_por="categoria")
    assert "para_desglosar" not in resultado.detalle


def test_la_herramienta_de_ventas_pide_desglosar_ante_un_por_que():
    assert "agrupar_por" in EntradaVentas.__doc__
    assert "por qué" in EntradaVentas.__doc__


# ------------------------------------------------------------------ una frase en otro idioma


def _revision_del_texto(texto):
    from agente import verificacion

    return verificacion.verificar(texto, verificacion.fuentes_de("una pregunta", []), lambda: {})


def test_una_frase_en_ingles_es_una_discrepancia():
    revision = _revision_del_texto("Sí. Now that the ticket is closed, its value can't be changed and this is final.")
    assert [d.tipo for d in revision.discrepancias] == ["idioma"]
    assert not revision.aprobada


@pytest.mark.parametrize(
    "texto",
    [
        "Once de ellos tienen menor prioridad, y son los que más tardaron.",
        "El internet de la caja está lento y el router no enciende.",
        "No hay ticket: se buscó en los documentos y ninguno trata lo pedido.",
    ],
)
def test_el_espanol_y_una_palabra_prestada_no_son_otro_idioma(texto):
    assert _revision_del_texto(texto).aprobada


# ------------------------------------------------------------------ fechas que dicen lo mismo que el texto


def test_fechas_iguales_al_texto_no_dejan_una_nota_de_contraste(conexion):
    resultado = venta(conexion, periodo="julio", desde="2026-07-01", hasta="2026-07-31")
    assert not any("se usaron las fechas pedidas" in nota for nota in resultado.notas)


def test_fechas_distintas_del_texto_si_dejan_la_nota(conexion):
    resultado = venta(conexion, periodo="julio", desde="2026-06-01", hasta="2026-06-30")
    assert any("se usaron las fechas pedidas" in nota for nota in resultado.notas)


def test_un_texto_que_no_se_entiende_con_fechas_deja_la_nota(conexion):
    resultado = venta(conexion, periodo="la temporada alta", desde="2026-06-01", hasta="2026-06-30")
    assert any("se usaron las fechas pedidas" in nota for nota in resultado.notas)


# ------------------------------------------------------------------ tickets: cuántos de cada tienda


def test_una_busqueda_en_varias_tiendas_cuenta_los_de_cada_una(conexion):
    resultado = de_tickets(conexion, metrica="buscar", texto="contraseña", limite=50)
    por_tienda = {clave: valor for clave, valor in resultado.cifras.items() if clave.startswith("tickets de ")}
    assert len(por_tienda) >= 2
    assert sum(int(valor) for valor in por_tienda.values()) == int(resultado.cifras["tickets"])
    for clave, valor in por_tienda.items():
        tienda = clave.removeprefix("tickets de ")
        assert sum(fila["tienda"] == tienda for fila in resultado.filas) == int(valor)


def test_un_detalle_en_varias_tiendas_tambien_cuenta_los_de_cada_una(conexion):
    resultado = de_tickets(conexion, metrica="detalle", texto="contraseña", limite=50)
    assert any(clave.startswith("tickets de ") for clave in resultado.cifras)


def test_una_busqueda_en_una_sola_tienda_no_repite_la_cuenta(conexion):
    resultado = de_tickets(conexion, metrica="buscar", texto="contraseña", tiendas=["Cumbres"], limite=50)
    assert not any(clave.startswith("tickets de ") for clave in resultado.cifras)


# ------------------------------------------------------------------ a quién se escala


def test_el_choque_de_un_producto_vendido_caducado_dice_donde_esta_el_nivel_de_autorizacion(con_indice):
    with con_indice.sesion() as sesion:
        resultado = buscar_politicas(sesion, EntradaPoliticas(consulta="devolución de un queso caducado con ticket, venta caducada"))
    choque = next(choque for choque in resultado.detalle["choques"] if choque["id"] == "CH-02")
    assert "PRO-SAC-021 §9" in choque["si_no_procede"]
    assert "búscalo" in choque["si_no_procede"]


def test_la_regla_entre_documentos_pide_no_inventar_una_diferencia(con_indice):
    with con_indice.sesion() as sesion:
        resultado = buscar_politicas(sesion, EntradaPoliticas(consulta="devolución de un queso caducado con ticket, venta caducada"))
    assert "si no difieren, no la menciones" in resultado.detalle["regla_entre_documentos"]["cuando"]


# ------------------------------------------------------------------ página y desglose


def test_la_nota_de_un_lacteo_a_dos_dias_da_la_pagina_de_esa_vineta(conexion):
    resultado = lotes(conexion, producto="yogurt", caduca_en_dias=2)
    nota = next(nota for nota in resultado.notas if "50%" in nota)
    assert "(§6.1, p. 5)" in nota


def test_el_aviso_de_desglose_dice_como_comparar_las_categorias_de_dos_dias(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], desde="2026-06-10", hasta="2026-06-10")
    aviso = resultado.detalle["para_desglosar"]
    assert "comparar_con" in aviso and "agrupar_por" in aviso


def test_comparar_dos_dias_por_categoria_trae_las_categorias_en_los_dos(conexion):
    resultado = venta(
        conexion, tiendas=["Cumbres"], desde="2026-06-10", hasta="2026-06-10", comparar_con="2026-06-09", agrupar_por="categoria"
    )
    assert len(resultado.filas) >= 2
    assert all(any("variacion" in clave for clave in fila) for fila in resultado.filas)


# ------------------------------------------------------------------ la fuente de lo que no lee lotes


def test_los_dias_dados_no_dicen_que_salieron_del_inventario(conexion):
    resultado = lotes(conexion, producto="yogurt", caduca_en_dias=2)
    assert "inventario.csv" not in resultado.fuente
    assert "catalogo_productos.csv" in resultado.fuente and "POL-OPS-014" in resultado.fuente


def test_los_lotes_si_dicen_que_salieron_del_inventario(conexion):
    assert "inventario.csv" in lotes(conexion, categoria="lácteos").fuente


# ------------------------------------------------------------------ lo que dice un ticket no prueba que se hizo


def test_el_detalle_dice_que_lo_escrito_en_un_ticket_no_prueba_que_se_hizo(conexion):
    resultado = de_tickets(conexion, metrica="detalle", ticket_id=1001)
    assert "no prueba que se hiciera" in resultado.definicion


# ------------------------------------------------------------------ el texto de una sección que coincide


def test_un_choque_con_una_seccion_que_coincide_trae_su_texto(con_indice):
    with con_indice.sesion() as sesion:
        resultado = buscar_politicas(sesion, EntradaPoliticas(consulta="quien firma una devolucion fuera de plazo por defecto de fabricacion"))
    choque = next(choque for choque in resultado.detalle["choques"] if choque.get("coinciden"))
    assert choque["texto_coinciden"]
    assert all(len(texto) > 20 for texto in choque["texto_coinciden"])


# ------------------------------------------------------------------ un requisito no reemplaza a otro


def test_el_choque_de_un_producto_vendido_caducado_dice_que_los_requisitos_van_juntos(con_indice):
    choque = _choque_de(con_indice, "devolución de un queso caducado con ticket, venta caducada", "CH-02")
    assert "a la vez" in choque["si_no_procede"]


# ------------------------------------------------------------------ ventas: qué subió y qué bajó


def test_comparar_por_categoria_dice_cuantas_subieron_y_cual_mas(conexion):
    resultado = venta(
        conexion, tiendas=["Cumbres"], desde="2026-06-10", hasta="2026-06-10", comparar_con="2026-06-09", agrupar_por="categoria"
    )
    subieron = int(resultado.cifras["grupos_que_subieron"])
    bajaron = int(resultado.cifras["grupos_que_bajaron"])
    assert subieron + bajaron == len(resultado.filas)
    clave = next(c for c in resultado.filas[0] if c.startswith("variacion "))
    mayor = max(resultado.filas, key=lambda fila: float(fila[clave].strip("+%").replace(",", "")))
    assert resultado.cifras["mayor_variacion"] == f"{mayor['categoria']} ({mayor[clave]})"


def test_comparar_sin_grupos_no_trae_ese_resumen(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], desde="2026-06-10", hasta="2026-06-10", comparar_con="2026-06-09")
    assert "grupos_que_subieron" not in resultado.cifras


# ------------------------------------------------------------------ lo que no se sabe de la existencia


def test_la_nota_de_existencia_no_deja_suponer_que_no_son_todas(conexion):
    resultado = lotes(conexion, categoria="lácteos")
    nota = next(nota for nota in resultado.notas if "no dice cuántas piezas son del lote" in nota)
    assert "pueden ser todas o solo una parte" in nota


# ------------------------------------------------------------------ con qué choca la fila de 2 días


def test_la_nota_de_un_lacteo_a_dos_dias_dice_que_el_choque_es_con_la_fila_de_6(conexion):
    resultado = lotes(conexion, producto="yogurt", caduca_en_dias=2)
    nota = next(nota for nota in resultado.notas if "50%" in nota)
    assert nota.startswith("la fila de 2 días (50%) de §6 choca con §5.2 y §6.1")


# ------------------------------------------------------------------ los niveles de autorización


def _politicas(con_indice, consulta):
    with con_indice.sesion() as sesion:
        return buscar_politicas(sesion, EntradaPoliticas(consulta=consulta))


def test_un_producto_en_duda_trae_los_niveles_de_autorizacion(con_indice):
    resultado = _politicas(con_indice, "me regresan unos nuggets de pollo sin nota, se aceptan")
    niveles = resultado.detalle["niveles_de_autorizacion"]
    assert niveles["cita"].startswith("PRO-SAC-021 §9, p. 6")
    assert len(niveles["por_monto"]) == 5
    assert niveles["por_monto"][0] == "Hasta 500.00: Ejecutivo de Servicio al Cliente"
    assert niveles["por_monto"][1] == "De 500.01 a 3,000.00: Supervisor de Servicio al Cliente"
    assert niveles["sin_importar_el_monto"]["cita"].startswith("PRO-SAC-021 §9.1")
    assert any("sección 5.3" in renglon for renglon in niveles["sin_importar_el_monto"]["casos"])
    assert "Gerente de Tienda" in niveles["sin_importar_el_monto"]["quien"]


def test_el_choque_de_perecedero_dice_donde_vienen_los_niveles(con_indice):
    choque = _choque_de_perecedero(con_indice, "me regresan unos nuggets de pollo sin nota, se aceptan")
    assert "niveles_de_autorizacion" in choque["para_que_el_area"]


def test_sin_un_producto_en_duda_no_vienen_los_niveles(con_indice):
    resultado = _politicas(con_indice, "devolución de un queso caducado con ticket, venta caducada")
    assert "niveles_de_autorizacion" not in resultado.detalle


def test_el_verificador_acepta_los_niveles_de_autorizacion_y_su_cita(con_indice):
    resultado = _politicas(con_indice, "me regresan unos nuggets de pollo sin nota, se aceptan")
    fuentes = verificacion.fuentes_de("una pregunta", [resultado])
    texto = "De 500.01 a 3,000.00 pesos autoriza el Supervisor de Servicio al Cliente (PRO-SAC-021 §9, p. 6)."
    with con_indice.sesion() as sesion:
        revision = verificacion.verificar(texto, fuentes, lambda: leer_indice(sesion).secciones)
    assert revision.aprobada, revision.discrepancias


# ------------------------------------------------------------------ abrir los tickets con las mismas palabras


def test_el_aviso_de_abrir_pide_las_mismas_palabras(conexion):
    resultado = de_tickets(conexion, metrica="buscar", texto="contraseña")
    assert "sin cambiar las palabras" in resultado.detalle["para_abrir"]


# ------------------------------------------------------------------ dónde está la cifra que no pasó


def test_el_motivo_de_una_cifra_sin_evidencia_dice_donde_esta_escrita():
    revision = verificacion.verificar(
        "Cerró el área de cajas 1 a 6 ese día.", verificacion.fuentes_de("una pregunta", []), lambda: {}
    )
    motivos = [d.motivo for d in revision.discrepancias if d.valor == "6"]
    assert motivos and "cajas 1 a 6" in motivos[0]


def test_el_motivo_de_una_cifra_no_repite_un_correo_cercano():
    revision = verificacion.verificar(
        "Escribe a ana.lopez@ejemplo.com 77 veces.", verificacion.fuentes_de("una pregunta", []), lambda: {}
    )
    motivos = [d.motivo for d in revision.discrepancias if d.valor == "77"]
    assert motivos and "ana.lopez" not in motivos[0]


# ------------------------------------------------------------------ la salida armada con las consultas


def test_la_respuesta_armada_no_da_una_definicion_de_lo_que_no_muestra(conexion):
    resultado = de_tickets(conexion, metrica="detalle", ticket_id=1001)
    texto = verificacion.respuesta_determinista([resultado])
    assert "texto_del_ticket" not in texto
    assert "Definición" not in texto


def test_la_respuesta_armada_si_da_la_definicion_de_una_venta(conexion):
    resultado = venta(conexion, tiendas=["Cumbres"], periodo="junio")
    assert "Definición: " in verificacion.respuesta_determinista([resultado])


# ------------------------------------------------------------------ cuántos candidatos tiene una entidad ambigua


def test_contar_los_candidatos_de_una_entidad_ambigua_pasa_la_revision(conexion):
    resultado = venta(conexion, tiendas=["Nuevo León"], periodo="julio")
    assert resultado.estado == "entidad_ambigua"
    fuentes = verificacion.fuentes_de("una pregunta", [resultado])
    revision = verificacion.verificar("Nuevo León coincide con 3 tiendas. ¿Cuál quieres?", fuentes, lambda: {})
    assert revision.aprobada, revision.discrepancias


def test_otra_cuenta_de_candidatos_no_pasa_la_revision(conexion):
    resultado = venta(conexion, tiendas=["Nuevo León"], periodo="julio")
    fuentes = verificacion.fuentes_de("una pregunta", [resultado])
    revision = verificacion.verificar("Nuevo León coincide con 7 tiendas. ¿Cuál quieres?", fuentes, lambda: {})
    assert not revision.aprobada


def test_la_respuesta_armada_de_una_entidad_ambigua_da_los_candidatos(conexion):
    resultado = venta(conexion, tiendas=["Nuevo León"], periodo="julio")
    texto = verificacion.respuesta_determinista([resultado])
    assert "T01 Tienda Valle Norte" in texto and "T03 Tienda Anáhuac" in texto
    fuentes = verificacion.fuentes_de("una pregunta", [resultado])
    assert verificacion.verificar(texto, fuentes, lambda: {}).aprobada


# ------------------------------------------------------------------ una lista de tickets: cuántos en cada estado


def test_una_busqueda_cuenta_los_de_cada_estado(conexion):
    resultado = de_tickets(conexion, metrica="buscar", texto="contraseña", limite=50)
    estados = {clave: valor for clave, valor in resultado.cifras.items() if clave.startswith("estado ")}
    assert sum(int(valor) for valor in estados.values()) == int(resultado.cifras["tickets"])
    for clave, valor in estados.items():
        assert sum(fila["estado"] == clave.removeprefix("estado ") for fila in resultado.filas) == int(valor)


def test_un_detalle_tambien_cuenta_los_de_cada_estado(conexion):
    resultado = de_tickets(conexion, metrica="detalle", texto="contraseña", limite=50)
    assert any(clave.startswith("estado ") for clave in resultado.cifras)


# ------------------------------------------------------------------ las cifras del texto de un ticket


def test_el_detalle_avisa_que_no_repita_sus_cifras_si_el_turno_consulto_mas(conexion):
    resultado = de_tickets(conexion, metrica="detalle", ticket_id=1001)
    assert "sin repetir sus cifras" in resultado.definicion


# ------------------------------------------------------------------ un documento que no viene


def _de_documento(con_indice, consulta, documento):
    with con_indice.sesion() as sesion:
        return buscar_politicas(sesion, EntradaPoliticas(consulta=consulta, documento=documento))


def test_un_documento_que_no_viene_trae_lo_que_otros_dicen_de_el(con_indice):
    resultado = _de_documento(con_indice, "producto con poca vida de anaquel", "PRO-LOG-003")
    assert resultado.estado == "documento_no_incluido"
    dichos = resultado.detalle["dicho_en_esas_citas"]
    assert dichos
    assert all("PRO-LOG-003" in dicho for dicho in dichos)
    assert any(dicho.startswith("POL-OPS-014 §9") for dicho in dichos)


def test_lo_que_dicen_de_un_documento_que_no_viene_se_puede_citar(con_indice):
    resultado = _de_documento(con_indice, "producto con poca vida de anaquel", "PRO-LOG-003")
    fuentes = verificacion.fuentes_de("una pregunta", [resultado])
    with con_indice.sesion() as sesion:
        revision = verificacion.verificar("Lo dice POL-OPS-014 §9, p. 6.", fuentes, lambda: leer_indice(sesion).secciones)
    assert revision.aprobada, revision.discrepancias


# ------------------------------------------------------------------ el horario que dan los documentos


def test_una_pregunta_de_horario_trae_el_horario_de_los_documentos(con_indice):
    resultado = _politicas(con_indice, "a que hora se abre la tienda en la mañana")
    assert any(fila["id"] == "MAN-OPS-007#2" for fila in resultado.filas)
    assert "ningún dato trae el de cada tienda" in resultado.detalle["antes_de_contestar"]


def test_otra_pregunta_no_trae_ese_aviso(con_indice):
    resultado = _politicas(con_indice, "cuanto tiempo tengo para registrar una merma")
    assert "ningún dato trae el de cada tienda" not in resultado.detalle.get("antes_de_contestar", "")


# ------------------------------------------------------------------ las piezas en litros


def test_las_piezas_de_un_producto_de_un_litro_tambien_se_dan_en_litros(conexion):
    resultado = venta(conexion, metrica="unidades", producto="leche entera", periodo="julio")
    piezas = resultado.cifras["unidades_piezas"]
    assert resultado.cifras["litros"] == f"{piezas}.00"
    assert any("1 L" in nota and "litros" in nota for nota in resultado.notas)


def test_las_piezas_de_medio_litro_se_multiplican(conexion):
    resultado = venta(conexion, metrica="unidades", producto="Refresco de cola 600 ml", periodo="julio")
    piezas = int(resultado.cifras["unidades_piezas"].replace(",", ""))
    assert float(resultado.cifras["litros"].replace(",", "")) == pytest.approx(piezas * 0.6)


def test_un_paquete_no_se_da_en_litros(conexion):
    resultado = venta(conexion, metrica="unidades", producto="Cerveza clara 355 ml 6 pack", periodo="julio")
    assert "litros" not in resultado.cifras


def test_una_categoria_no_se_da_en_litros(conexion):
    resultado = venta(conexion, metrica="unidades", categoria="Bebidas", periodo="julio")
    assert "litros" not in resultado.cifras


def test_un_producto_sin_litros_en_el_nombre_no_los_da(conexion):
    resultado = venta(conexion, metrica="unidades", producto="Dona glaseada pieza", periodo="julio")
    assert "litros" not in resultado.cifras


# ------------------------------------------------------------------ dónde está la cifra: sin espacio raro


def test_el_motivo_no_separa_la_cifra_de_su_puntuacion():
    revision = verificacion.verificar("Cerró el área de cajas 1 a 6. Según el aviso.", verificacion.fuentes_de("una pregunta", []), lambda: {})
    motivos = [d.motivo for d in revision.discrepancias if d.valor == "6"]
    assert motivos and "cajas 1 a 6. Según" in motivos[0]


# ------------------------------------------------------------------ una respuesta que se corrige sola


@pytest.mark.parametrize(
    "texto",
    [
        "La autoriza el Ejecutivo.\n\nCorrijo mi respuesta: el Ejecutivo solo llega a otro monto.",
        "Corrección: en mi respuesta anterior sobre los bolillos, la nota no salió de una consulta.",
        "Me equivoqué: es el Supervisor.",
    ],
)
def test_una_respuesta_que_se_corrige_sola_no_pasa_la_revision(texto):
    revision = verificacion.verificar(texto, verificacion.fuentes_de("una pregunta", []), lambda: {})
    assert any(discrepancia.tipo == "autocorreccion" for discrepancia in revision.discrepancias)


@pytest.mark.parametrize(
    "texto",
    [
        "La acción correctiva va en el plan de la tienda.",
        "Es un error de la tienda.",
        "Se corrige con una nota de crédito.",
    ],
)
def test_hablar_de_una_correccion_ajena_si_pasa_la_revision(texto):
    revision = verificacion.verificar(texto, verificacion.fuentes_de("una pregunta", []), lambda: {})
    assert not any(discrepancia.tipo == "autocorreccion" for discrepancia in revision.discrepancias)


def test_el_pedido_de_correccion_pide_escribir_solo_lo_correcto():
    revision = verificacion.verificar("Corrijo mi respuesta: es otro.", verificacion.fuentes_de("una pregunta", []), lambda: {})
    assert "solo lo correcto" in verificacion.pedido_de_correccion(revision)


# ------------------------------------------------------------------ quién autoriza un monto


@pytest.mark.parametrize(
    ("consulta", "quien"),
    [
        ("firma de una devolucion de 1,200 pesos, quien la autoriza", "De 500.01 a 3,000.00: Supervisor de Servicio al Cliente"),
        ("firma de una devolucion de $500, quien la autoriza", "Hasta 500.00: Ejecutivo de Servicio al Cliente"),
        ("autorizacion por monto de una devolucion de 30,000 pesos", "De 10,000.01 a 30,000.00: Gerente de Tienda"),
        ("autorizacion por monto de una devolucion de 45,000.50 pesos", "Más de 30,000.00: Gerente de Tienda con visto bueno"),
    ],
)
def test_un_monto_en_la_consulta_trae_su_nivel_de_autorizacion(con_indice, consulta, quien):
    resultado = _politicas(con_indice, consulta)
    assert any(fila["id"] == "PRO-SAC-021#9" for fila in resultado.filas)
    nivel = resultado.detalle["nivel_del_monto"]
    assert nivel["cita"].startswith("PRO-SAC-021 §9, p. 6")
    assert nivel["fila"].startswith(quien)


def test_sin_monto_no_viene_el_nivel(con_indice):
    resultado = _politicas(con_indice, "quien autoriza una devolucion por monto")
    assert "nivel_del_monto" not in resultado.detalle


def test_con_dos_montos_no_elige_uno(con_indice):
    resultado = _politicas(con_indice, "firma de una devolucion de 1,200 pesos y otra de 5,000 pesos")
    assert "nivel_del_monto" not in resultado.detalle


def test_un_monto_sin_la_tabla_de_autorizacion_no_trae_el_nivel(con_indice):
    resultado = _politicas(con_indice, "fondo fijo de caja de 2,000 pesos")
    assert all(fila["id"] != "PRO-SAC-021#9" for fila in resultado.filas)
    assert "nivel_del_monto" not in resultado.detalle


def test_el_verificador_acepta_la_cita_del_nivel_del_monto(con_indice):
    resultado = _politicas(con_indice, "firma de una devolucion de 1,200 pesos, quien la autoriza")
    fuentes = verificacion.fuentes_de("una pregunta", [resultado])
    texto = "De 500.01 a 3,000.00 autoriza el Supervisor de Servicio al Cliente (PRO-SAC-021 §9, p. 6)."
    with con_indice.sesion() as sesion:
        revision = verificacion.verificar(texto, fuentes, lambda: leer_indice(sesion).secciones)
    assert revision.aprobada, revision.discrepancias


# ------------------------------------------------------------------ el total de una existencia agrupada


def de_inventario(conexion, **campos):
    return inventario(conexion.cursor(), EntradaInventario(**campos))


def test_la_existencia_agrupada_por_producto_trae_el_total(conexion):
    resultado = de_inventario(conexion, agrupar_por="producto", tiendas=["Valle Norte"], producto="SKU-1077", limite=50)
    assert resultado.cifras["existencia"] == resultado.filas[0]["existencia"]


def test_la_existencia_agrupada_de_una_sola_unidad_suma_sus_filas(conexion):
    resultado = de_inventario(conexion, agrupar_por="tienda", categoria="Bebidas", limite=50)
    suma = sum(int(fila["existencia"].replace(",", "")) for fila in resultado.filas)
    assert int(resultado.cifras["existencia"].replace(",", "")) == suma


def test_la_existencia_agrupada_con_dos_unidades_no_las_suma(conexion):
    resultado = de_inventario(conexion, agrupar_por="categoria", tiendas=["Valle Norte"], limite=50)
    assert "existencia" not in resultado.cifras
    assert "existencia_kg" in resultado.cifras and "existencia_piezas" in resultado.cifras


def test_el_total_agrupado_no_cuenta_solo_lo_que_se_muestra(conexion):
    completa = de_inventario(conexion, agrupar_por="producto", tiendas=["Valle Norte"], categoria="Bebidas", limite=50)
    recortada = de_inventario(conexion, agrupar_por="producto", tiendas=["Valle Norte"], categoria="Bebidas", limite=3)
    assert len(recortada.filas) == 3
    assert recortada.cifras["existencia"] == completa.cifras["existencia"]


# ------------------------------------------------------------------ de quién es la existencia de un SKU sustituido


def test_la_existencia_de_un_sku_sustituido_dice_de_quien_es(conexion):
    resultado = de_inventario(conexion, tiendas=["Cumbres"], sku="SKU-1001")
    assert resultado.cifras["existencia_es_de"].endswith("(SKU-1121)")


def test_la_existencia_de_un_sku_vigente_no_lo_aclara(conexion):
    resultado = de_inventario(conexion, tiendas=["Cumbres"], sku="SKU-1121")
    assert "existencia_es_de" not in resultado.cifras


# ------------------------------------------------------------------ títulos que solo cambian de formato


def test_una_lista_cuenta_junto_el_titulo_que_solo_cambia_de_mayusculas(conexion):
    resultado = de_tickets(conexion, metrica="buscar", texto="merma alta", limite=50)
    assert {fila["titulo"] for fila in resultado.filas if "titulo" in fila} >= {"merma alta en frutas", "Merma alta en frutas"}
    iguales = resultado.detalle["mismo_titulo_salvo_formato"]
    conteo = sum(fila.get("titulo", "").casefold() == "merma alta en frutas" for fila in resultado.filas)
    assert iguales["merma alta en frutas"] == str(conteo)


def test_una_lista_de_titulos_identicos_no_lo_aclara(conexion):
    resultado = de_tickets(conexion, metrica="buscar", texto="merma alta", tiendas=["Valle Norte"], limite=50)
    titulos = {fila["titulo"] for fila in resultado.filas if "titulo" in fila}
    if len(titulos) == 1:
        assert "mismo_titulo_salvo_formato" not in resultado.detalle


def test_los_titulos_de_la_lista_no_cuentan_como_fuente_de_una_cifra(conexion):
    resultado = de_tickets(conexion, metrica="buscar", texto="merma alta", limite=50)
    fuentes = verificacion.fuentes_de("una pregunta", [resultado])
    revision = verificacion.verificar("Son 987 con ese título.", fuentes, lambda: {})
    assert not revision.aprobada


# ------------------------------------------------------------------ la merma no falta solo en una tienda


def test_el_motivo_de_la_merma_real_dice_que_no_es_de_una_tienda(conexion):
    resultado = de_inventario(conexion, metrica="merma_real", tiendas=["Cumbres"])
    assert "de ninguna tienda" in resultado.resumen


# ------------------------------------------------------------------ las horas de una sección que no coinciden


def test_el_aviso_del_horario_pide_las_dos_horas_si_no_coinciden(con_indice):
    resultado = _politicas(con_indice, "a que hora se abre la tienda en la mañana")
    aviso = resultado.detalle["antes_de_contestar"]
    assert "da las dos y di que no coinciden" in aviso


# ------------------------------------------------------------------ sin el monto: los rangos


def test_el_choque_de_un_producto_vendido_caducado_pide_los_rangos_sin_el_monto(con_indice):
    choque = _choque_de(con_indice, "devolución de un queso caducado con ticket, venta caducada", "CH-02")
    assert "sin el monto, da sus rangos" in choque["si_no_procede"]


# ------------------------------------------------------------------ ventas: qué parte del total es cada fila


PREGUNTA_DE_PARTE = "¿Qué porcentaje de esa venta fue de Valle Norte?"
PARTE = ventas_modulo.PARTE_DEL_TOTAL


def _proporcion(fila, resultado):
    return ventas_modulo.pct(_decimal(fila["venta"]) / _decimal(resultado.cifras["venta"]))


def _decimal(texto):
    return Decimal(texto.replace(",", ""))


def test_cada_tienda_trae_su_parte_de_la_venta_total(conexion):
    resultado = venta(conexion, desde="2026-06-01", hasta="2026-08-31", agrupar_por="tienda")
    de_cada_tienda = {fila["tienda_id"]: fila[PARTE] for fila in resultado.filas}
    assert de_cada_tienda["T01"] == "37.16%"
    assert de_cada_tienda == {fila["tienda_id"]: _proporcion(fila, resultado) for fila in resultado.filas}


def test_la_respuesta_con_el_porcentaje_de_la_fila_pasa_la_revision(conexion):
    resultado = venta(conexion, desde="2026-06-01", hasta="2026-08-31", agrupar_por="tienda")
    fuentes = verificacion.fuentes_de(PREGUNTA_DE_PARTE, [resultado])
    texto = "Valle Norte fue el 37.16% de la venta de las cuatro tiendas: $6,401,700.22 MXN de $17,228,300.26 MXN."
    assert verificacion.verificar(texto, fuentes, lambda: {}).aprobada


def test_el_porcentaje_redondeado_por_el_modelo_sigue_sin_pasar(conexion):
    resultado = venta(conexion, desde="2026-06-01", hasta="2026-08-31", agrupar_por="tienda")
    fuentes = verificacion.fuentes_de(PREGUNTA_DE_PARTE, [resultado])
    revision = verificacion.verificar("Valle Norte fue el 37.2% de la venta de las cuatro tiendas.", fuentes, lambda: {})
    assert [d.valor for d in revision.discrepancias] == ["37.2"]


def test_la_parte_es_del_total_y_no_de_las_filas_que_se_muestran(conexion):
    resultado = venta(conexion, desde="2026-06-01", hasta="2026-08-31", agrupar_por="tienda", limite=1)
    assert [fila[PARTE] for fila in resultado.filas] == ["37.16%"]


def test_con_tiendas_filtradas_la_parte_es_de_esas_tiendas(conexion):
    resultado = venta(conexion, tiendas=["Valle Norte", "Cumbres"], periodo="agosto", agrupar_por="tienda")
    assert [fila[PARTE] for fila in resultado.filas] == [_proporcion(fila, resultado) for fila in resultado.filas]
    assert resultado.filas[0][PARTE] == "52.30%"


@pytest.mark.parametrize("agrupar_por", ["categoria", "canal", "producto", "dia_semana"])
def test_cada_agrupacion_de_venta_trae_la_parte_de_cada_fila(conexion, agrupar_por):
    resultado = venta(conexion, periodo="julio", agrupar_por=agrupar_por)
    assert resultado.filas
    assert [fila[PARTE] for fila in resultado.filas] == [_proporcion(fila, resultado) for fila in resultado.filas]


def test_cada_mes_trae_su_parte_de_la_venta_del_periodo(conexion):
    resultado = venta(conexion, desde="2026-06-01", hasta="2026-08-31", grano="mes")
    assert len(resultado.filas) == 3
    assert [fila[PARTE] for fila in resultado.filas] == [_proporcion(fila, resultado) for fila in resultado.filas]


def test_con_grano_y_agrupacion_no_hay_parte_porque_la_base_seria_ambigua(conexion):
    resultado = venta(conexion, desde="2026-06-01", hasta="2026-08-31", grano="mes", agrupar_por="tienda")
    assert all(PARTE not in fila for fila in resultado.filas)


def test_una_comparacion_no_trae_la_parte_del_total(conexion):
    resultado = venta(conexion, periodo="julio", comparar_con="junio", agrupar_por="tienda")
    assert all(not any(clave.startswith(PARTE) for clave in fila) for fila in resultado.filas)


@pytest.mark.parametrize("metrica", ["unidades", "devoluciones", "venta_por_m2", "margen"])
def test_las_otras_metricas_no_traen_la_parte_del_total(conexion, metrica):
    resultado = venta(conexion, periodo="julio", metrica=metrica, agrupar_por="tienda")
    assert resultado.filas
    assert all(PARTE not in fila for fila in resultado.filas)


def test_un_total_sin_filas_no_trae_la_parte(conexion):
    resultado = venta(conexion, tiendas=["Valle Norte"], periodo="julio")
    assert not resultado.filas and PARTE not in resultado.cifras


@pytest.mark.parametrize("ventas_de_los_grupos", [["-5.00", "15.00"], ["0.00", "0.00"], ["10.00", "-10.00"]])
def test_con_un_grupo_en_negativo_o_un_total_en_cero_no_hay_parte(ventas_de_los_grupos):
    montos = [Decimal(monto) for monto in ventas_de_los_grupos]
    grupos = [ventas_modulo._Grupo(orden=(i,), celdas={}, suma={"venta": monto}) for i, monto in enumerate(montos)]
    assert ventas_modulo._parte_de_cada_grupo(grupos, sum(montos, Decimal(0))) == {}


def test_la_herramienta_de_ventas_dice_donde_viene_el_porcentaje_del_total():
    assert "pct_del_total" in EntradaVentas.__doc__
