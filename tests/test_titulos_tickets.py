"""Títulos de tickets normalizados al cargar y temas de config/temas_tickets.csv (T1.3, C1.4, C1.2).

Acepta: los 146 títulos quedan en 56 normalizados; P17 devuelve el empate de 4 y 4 (montacargas y
wifi de los handhelds). Las cifras se escriben aquí porque el criterio de aceptación las fija; el
código no las conoce.

La base de la fixture base_construida vive en un directorio temporal (tests/conftest.py). Las
pruebas que alteran datos o configuración construyen sobre copias en tmp_path, sin políticas ni
índice de texto, para correr sin red. Los nombres y teléfonos que inventan estas pruebas no son
de ningún ticket.

La revisión a mano de los temas queda en EVIDENCIA_POR_TEMA: para cada tema, lo que dicen las
descripciones de todos sus tickets. Si una fila de config/temas_tickets.csv manda un título a un
tema equivocado, un ticket deja de cumplir su patrón y la prueba lo nombra.
"""

import csv
import re

import duckdb
import pytest

from agente import construir, texto_tickets
from tests.conftest import DIR_CONFIG, DIR_DATOS

ARCHIVO_TICKETS = DIR_DATOS / "tickets_mesa_servicio.csv"
ARCHIVO_TEMAS = DIR_CONFIG / "temas_tickets.csv"

TICKETS = 285
TITULOS_CRUDOS = 146
TITULOS_NORMALIZADOS = 56
MONTACARGAS = "montacargas no arranca"
WIFI = "se cayo el wifi de los handhelds"
MONTACARGAS_EN_ANAHUAC = [1025, 1184, 1254, 1278]
IMPRESORAS_DE_CAJA = [
    "impresora de caja atascada",
    "impresora de caja sin conexion",
    "impresora imprime caracteres raros",
    "la caja 2 no imprime",
    "la caja 6 no imprime",
    "la caja 8 no imprime",
    "ticket sale en blanco",
]

# Revisión a mano (2026-10-31): lo que dice la descripción de TODOS los tickets de cada tema. Cada
# patrón se busca en la descripción normalizada (minúsculas, sin acentos ni signos). Salió de leer
# las 285 descripciones, no de los títulos.
EVIDENCIA_POR_TEMA = {
    "Autocobro": r"caja de autocobro \d+ marca fuera de servicio",
    "Báscula": r"bascula de la caja \d+ pesa de mas",
    "Cajón de dinero": r"cajon de la caja \d+ no abre",
    "Impresora de caja": r"printmax tx 200",
    "Lector de código": r"lector de la caja \d+ no lee los codigos",
    "Pantalla de caja": r"pantalla de la caja \d+ parpadea",
    "Teclado de caja": r"teclado de la caja \d+ tiene teclas pegadas",
    "Terminal bancaria": r"terminal de la caja \d+ rechaza todas las tarjetas",
    "Actualización del punto de venta": r"las cajas piden una actualizacion",
    "Cámaras de seguridad": r"sistema de camaras marca q(?:ue)? no esta grabando",
    "Correo electrónico": r"correo del gerente no baja mensajes",
    "Impresora de oficina": r"impresora de la oficina la de reportes no la de cajas",
    "Red e internet": r"el internet esta muy lento|handhelds de piso no agarran wifi",
    "Sistema de inventario (SIGMA)": r"no podemos entrar al sigma",
    "Pantallas de precios": r"pantalla digital de precios",
    "Reloj checador": r"checador de la entrada de empleados no lee huellas",
    "Usuarios y accesos": r"se me bloqueo el usuario del sistema de nomina",
    "Aire acondicionado": r"clima del area de abarrotes no enfria",
    "Anaqueles": r"anaquel del pasillo \d+ esta flojo",
    "Refrigeración": r"congelador de la isla|refri de carnes marca 9 grados|se fue la luz en refris",
    "Puertas y cortinas": r"puerta de la entrada principal se atora|puerta de acceso de empleados no cierra|cortina metalica de recepcion",
    "Iluminación": r"focos fundidos en el estacionamiento",
    "Fugas de agua": r"fuga de agua en el bano de clientes|gotera arriba de la caja",
    "Montacargas": r"montacargas de bodega no arranca",
    "Obras y remodelaciones": r"se remodela el pasillo central",
    "Ajustes y conteos de inventario": r"existencia negativa de|conteo del pasillo \d+ salio con diferencia",
    "Recepción de mercancía": r"en la recepcion de prv \d+ faltaron|triple de agua embotellada",
    "Surtido y reposición": r"pedido de ayer llego sin la tarima|se nos esta acabando",
    "Merma y caducidad": r"merma de frutas y verduras|caducado en anaquel",
    "Precios en anaquel": r"precio en anaquel de .* es distinto al de caja",
    "Códigos de producto": r"producto nuevo de prv \d+ y no tiene codigo cargado",
    "Alta de personal": r"entra un nuevo colaborador",
    "Baja y finiquito": r"renuncio favor de procesar su baja y finiquito",
    "Turnos y plantilla": r"solicitud de cambio de turno|apoyo de 2 cajeros para sabado y domingo",
    "Capacitación": r"capacitacion de manejo higienico de alimentos",
    "Vacaciones": r"como se calculan sus dias de vacaciones",
    "Incapacidades": r"incapacidad del imss",
    "Nómina": r"pagaron menos horas de las que checaron",
    "Uniformes": r"pedimos uniformes hace 3 semanas",
}


def titulos_del_csv() -> list[str]:
    with open(ARCHIVO_TICKETS, encoding="utf-8", newline="") as archivo:
        return [fila["titulo"] for fila in csv.DictReader(archivo)]


def uno(con, sql, parametros=None):
    return con.execute(sql, parametros or []).fetchone()[0]


def construir_copia(dir_datos, tmp_path, **opciones):
    """Construye solo la parte de datos (sin políticas ni índice) en un archivo nuevo de tmp_path."""
    destino = tmp_path / "copia.duckdb"
    return construir.construir(dir_datos, destino, politicas=False, indice_texto=False, **opciones)


@pytest.fixture(scope="module")
def con(base_construida):
    with duckdb.connect(str(base_construida.base), read_only=True) as conexion:
        yield conexion


# --- Normalización del título ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "crudo, esperado",
    [
        ("Montacargas no arranca", "montacargas no arranca"),
        ("montacargas no arranca gracias", "montacargas no arranca"),
        ("MONTACARGAS NO ARRANCA", "montacargas no arranca"),
        ("Cámaras sin grabar gracias", "camaras sin grabar"),
        ("Camaras sin grabar", "camaras sin grabar"),
        ("Fuga de agua en baños", "fuga de agua en banos"),
        ("Fuga de agua en banos", "fuga de agua en banos"),
        ("Diferencia en conteo cíclico", "diferencia en conteo ciclico"),
        ("Producto no llegó del CEDIS gracias", "producto no llego del cedis"),
        ("  Reposición   urgente,  gracias! ", "reposicion urgente"),
        ("Falta personal en fin de semana, muchas gracias", "falta personal en fin de semana"),
        ("Alta de empleado nuevo mil gracias", "alta de empleado nuevo"),
        ("Cambio de horario gracias gracias", "cambio de horario"),
        ("La caja 6 no imprime", "la caja 6 no imprime"),
        ("Remodelación de pasillo central del 14 al 16 de julio", "remodelacion de pasillo central del 14 al 16 de julio"),
        # El "gracias" solo se quita al final; en medio es parte del título.
        ("Gracias por el apoyo con la caja", "gracias por el apoyo con la caja"),
        # Un título que fuera solo una cortesía no se deja vacío.
        ("Gracias", "gracias"),
        ("Muchas gracias", "muchas gracias"),
    ],
)
def test_el_titulo_se_reduce_a_minusculas_sin_acentos_ni_gracias(crudo, esperado):
    assert texto_tickets.normalizar_titulo(crudo) == esperado


def test_el_acento_compuesto_y_el_descompuesto_dan_el_mismo_titulo():
    compuesto = "Cámaras sin grabar"
    descompuesto = "Cámaras sin grabar"
    assert compuesto != descompuesto
    assert texto_tickets.normalizar_titulo(compuesto) == texto_tickets.normalizar_titulo(descompuesto) == "camaras sin grabar"


def test_normalizar_dos_veces_da_lo_mismo_que_una():
    for titulo in titulos_del_csv():
        una_vez = texto_tickets.normalizar_titulo(titulo)
        assert texto_tickets.normalizar_titulo(una_vez) == una_vez


def test_normalizar_texto_quita_signos_y_espacios_de_mas():
    assert texto_tickets.normalizar_texto("  ¿Cómo   se calculan, mis VACACIONES?  ") == "como se calculan mis vacaciones"
    assert texto_tickets.normalizar_texto("<PERSONA_1> llamó") == "persona 1 llamo"


# --- Criterio de aceptación: 146 títulos en 56 ------------------------------------------------------


def test_el_csv_trae_146_titulos_escritos_y_56_normalizados():
    titulos = titulos_del_csv()
    assert len(titulos) == TICKETS
    assert len(set(titulos)) == TITULOS_CRUDOS
    assert len({texto_tickets.normalizar_titulo(titulo) for titulo in titulos}) == TITULOS_NORMALIZADOS


def test_la_base_guarda_los_146_titulos_tal_como_vienen_y_los_56_normalizados(con):
    assert uno(con, "SELECT count(DISTINCT titulo) FROM v_tickets") == TITULOS_CRUDOS
    assert uno(con, "SELECT count(DISTINCT titulo_normalizado) FROM v_tickets") == TITULOS_NORMALIZADOS
    assert uno(con, "SELECT count(*) FROM v_tickets") == TICKETS


def test_el_titulo_normalizado_de_la_base_es_el_de_la_funcion(con):
    guardados = dict(con.execute("SELECT ticket_id, titulo_normalizado FROM v_tickets").fetchall())
    with open(ARCHIVO_TICKETS, encoding="utf-8", newline="") as archivo:
        esperados = {int(fila["ticket_id"]): texto_tickets.normalizar_titulo(fila["titulo"]) for fila in csv.DictReader(archivo)}
    assert guardados == esperados


def test_la_normalizacion_se_calcula_al_cargar_y_queda_en_una_tabla(con):
    # Una sola vez al construir (C1.4 n4): una tabla con llave, no una expresión que se repita en cada consulta.
    tablas = con.execute("SELECT table_name FROM duckdb_tables() WHERE table_name = 'tickets_normalizados'").fetchall()
    assert tablas == [("tickets_normalizados",)]
    assert uno(con, "SELECT count(*) FROM tickets_normalizados") == TICKETS
    assert uno(con, "SELECT count(DISTINCT ticket_id) FROM tickets_normalizados") == TICKETS


def test_p17_en_anahuac_el_montacargas_y_el_wifi_empatan_con_4(con):
    ranking = con.execute(
        "SELECT titulo_normalizado, count(*) AS n FROM v_tickets WHERE tienda = 'Tienda Anáhuac' "
        "GROUP BY titulo_normalizado ORDER BY n DESC, titulo_normalizado"
    ).fetchall()
    maximo = ranking[0][1]
    assert maximo == 4
    assert [titulo for titulo, n in ranking if n == maximo] == [MONTACARGAS, WIFI]
    assert ranking[2][1] < maximo


def test_p17_los_cuatro_montacargas_de_anahuac_son_los_de_la_rubrica(con):
    folios = con.execute(
        "SELECT ticket_id FROM v_tickets WHERE tienda = 'Tienda Anáhuac' AND titulo_normalizado = ? ORDER BY ticket_id",
        [MONTACARGAS],
    ).fetchall()
    assert [folio for (folio,) in folios] == MONTACARGAS_EN_ANAHUAC


def test_sin_normalizar_el_montacargas_se_parte_y_el_wifi_gana_solo(con):
    # La trampa de P17: el montacargas viene con tres escrituras y el wifi con una. La prueba fija que
    # es la normalización, no otra cosa, la que devuelve el empate.
    crudo = con.execute(
        "SELECT titulo, count(*) AS n FROM tickets WHERE tienda_id = 'T03' GROUP BY titulo ORDER BY n DESC, titulo"
    ).fetchall()
    assert crudo[0] == ("Se cayó el wifi de los handhelds", 4)
    assert crudo[1][1] < 4


# --- Temas de config/temas_tickets.csv --------------------------------------------------------------


@pytest.fixture(scope="module")
def temas():
    return texto_tickets.leer_temas(ARCHIVO_TEMAS)


def test_el_archivo_de_temas_cubre_exactamente_los_56_titulos_normalizados(temas):
    normalizados = {texto_tickets.normalizar_titulo(titulo) for titulo in titulos_del_csv()}
    assert set(temas) == normalizados
    assert len(temas) == TITULOS_NORMALIZADOS


def test_ningun_ticket_de_los_datos_queda_sin_clasificar(con):
    assert uno(con, "SELECT count(*) FROM v_tickets WHERE tema = ?", [texto_tickets.TEMA_SIN_CLASIFICAR]) == 0
    assert uno(con, "SELECT count(*) FROM v_tickets WHERE tema IS NULL") == 0


def test_la_base_da_a_cada_ticket_el_tema_de_su_titulo(con, temas):
    filas = con.execute("SELECT titulo_normalizado, tema FROM v_tickets").fetchall()
    assert {(titulo, tema) for titulo, tema in filas} == set(temas.items())


def test_los_temas_del_archivo_son_los_que_la_revision_a_mano_respalda(temas):
    assert set(temas.values()) == set(EVIDENCIA_POR_TEMA)


def test_la_descripcion_de_cada_ticket_respalda_el_tema_de_su_titulo(con):
    sin_respaldo = []
    for ticket_id, tema, descripcion in con.execute("SELECT ticket_id, tema, descripcion FROM v_tickets ORDER BY ticket_id").fetchall():
        if not re.search(EVIDENCIA_POR_TEMA[tema], texto_tickets.normalizar_texto(descripcion)):
            sin_respaldo.append((ticket_id, tema))
    assert sin_respaldo == []


@pytest.mark.parametrize("columna", ["titulo_normalizado", "tema"])
def test_un_titulo_y_un_tema_pertenecen_a_una_sola_categoria(con, columna):
    mezclados = con.execute(
        f"SELECT {columna}, count(DISTINCT categoria) FROM v_tickets GROUP BY {columna} HAVING count(DISTINCT categoria) > 1"
    ).fetchall()
    assert mezclados == []


def test_la_impresora_de_caja_junta_7_titulos_y_15_tickets(con):
    titulos = con.execute("SELECT DISTINCT titulo_normalizado FROM v_tickets WHERE tema = 'Impresora de caja' ORDER BY 1").fetchall()
    assert [titulo for (titulo,) in titulos] == sorted(IMPRESORAS_DE_CAJA)
    assert uno(con, "SELECT count(*) FROM v_tickets WHERE tema = 'Impresora de caja'") == 15


def test_la_impresora_de_caja_son_justo_los_tickets_que_nombran_la_printmax(con):
    # Reverso de la revisión: la impresora de oficina dice "no la de cajas" y no entra en el tema.
    con_printmax = {
        ticket_id
        for ticket_id, descripcion in con.execute("SELECT ticket_id, descripcion FROM v_tickets").fetchall()
        if "printmax" in texto_tickets.normalizar_texto(descripcion)
    }
    del_tema = {ticket_id for (ticket_id,) in con.execute("SELECT ticket_id FROM v_tickets WHERE tema = 'Impresora de caja'").fetchall()}
    assert con_printmax == del_tema
    assert uno(con, "SELECT count(*) FROM v_tickets WHERE titulo_normalizado = 'impresora de oficina sin toner' AND tema = 'Impresora de oficina'") == 5


def test_las_impresoras_de_caja_de_valle_norte_son_las_de_las_cajas_2_7_8_y_11_y_estan_cerradas(con):
    # P14: no hay ticket de la caja 3 de Valle Norte; los de impresora son de otras cajas y ya cerraron.
    filas = con.execute(
        "SELECT ticket_id, descripcion, sin_cerrar FROM v_tickets "
        "WHERE tema = 'Impresora de caja' AND tienda = 'Tienda Valle Norte' ORDER BY ticket_id"
    ).fetchall()
    assert [ticket_id for ticket_id, _, _ in filas] == [1037, 1044, 1085, 1088]
    assert sorted(int(re.search(r"caja (\d+)", descripcion).group(1)) for _, descripcion, _ in filas) == [2, 7, 8, 11]
    assert not any(sin_cerrar for _, _, sin_cerrar in filas)


def test_los_tres_titulos_con_anaquel_van_a_temas_distintos(con):
    # Una palabra clave los habría juntado: son un anaquel flojo, un precio y un producto caducado.
    filas = con.execute("SELECT DISTINCT titulo_normalizado, tema FROM v_tickets WHERE titulo_normalizado LIKE '%anaquel%' ORDER BY 1").fetchall()
    assert filas == [
        ("anaquel flojo", "Anaqueles"),
        ("precio en anaquel no coincide", "Precios en anaquel"),
        ("producto caducado en anaquel", "Merma y caducidad"),
    ]


# --- Lectura y validación del archivo de temas ------------------------------------------------------

ENCABEZADO = "titulo_normalizado,tema\n"


def escribir(tmp_path, contenido):
    ruta = tmp_path / "temas.csv"
    ruta.write_bytes(contenido.encode("utf-8"))
    return ruta


def test_leer_temas_ignora_comentarios_lineas_en_blanco_bom_y_crlf(tmp_path):
    contenido = (
        '﻿# Comentario con "comillas sin cerrar y, comas\r\n'
        "\r\n"
        "titulo_normalizado,tema\r\n"
        "# --- Cajas ---\r\n"
        "bascula descalibrada,Báscula\r\n"
        '  la caja 2 no imprime  ,"Impresora, de caja"\r\n'
    )
    assert texto_tickets.leer_temas(escribir(tmp_path, contenido)) == {
        "bascula descalibrada": "Báscula",
        "la caja 2 no imprime": "Impresora, de caja",
    }


@pytest.mark.parametrize(
    "contenido, fragmento",
    [
        ("titulo,tema\nx,Y\n", "encabezado"),
        ("# solo un comentario\n", "encabezado"),
        (ENCABEZADO, "no trae filas"),
        (ENCABEZADO + "bascula descalibrada,Báscula,sobra\n", "2 columnas"),
        (ENCABEZADO + "bascula descalibrada\n", "2 columnas"),
        (ENCABEZADO + "bascula descalibrada,\n", "vacíos"),
        (ENCABEZADO + ",Báscula\n", "vacíos"),
        (ENCABEZADO + "Báscula descalibrada,Báscula\n", "«bascula descalibrada»"),
        (ENCABEZADO + "bascula descalibrada gracias,Báscula\n", "«bascula descalibrada»"),
        (ENCABEZADO + "bascula descalibrada,Báscula\nbascula descalibrada,Otro\n", "ya está en el renglón 2"),
        (ENCABEZADO + "bascula descalibrada,Sin clasificar\n", "reservado"),
        (ENCABEZADO + "bascula descalibrada,sin  clasificar\n", "reservado"),
        (ENCABEZADO + "bascula descalibrada,Báscula\nautocobro fuera de servicio,Bascula\n", "difieren"),
    ],
    ids=[
        "otro-encabezado", "solo-comentarios", "sin-filas", "columna-de-mas", "columna-de-menos", "tema-vacio",
        "titulo-vacio", "titulo-con-mayuscula", "titulo-con-gracias", "titulo-repetido", "tema-reservado",
        "tema-reservado-con-espacios", "temas-casi-iguales",
    ],
)
def test_leer_temas_rechaza_un_archivo_mal_formado_y_dice_donde(tmp_path, contenido, fragmento):
    with pytest.raises(ValueError, match=re.escape(fragmento)):
        texto_tickets.leer_temas(escribir(tmp_path, contenido))


def test_leer_temas_rechaza_un_archivo_que_no_esta_en_utf8(tmp_path):
    ruta = tmp_path / "temas.csv"
    ruta.write_bytes(ENCABEZADO.encode("utf-8") + "bascula descalibrada,Báscula\n".encode("latin-1"))
    with pytest.raises(ValueError, match="UTF-8"):
        texto_tickets.leer_temas(ruta)


# --- La construcción lee el archivo de temas ---------------------------------------------------------


def test_sin_el_archivo_de_temas_la_construccion_da_un_error_con_tipo_y_no_crea_la_base(copia_datos, copia_config, tmp_path):
    (copia_config / "temas_tickets.csv").unlink()
    with pytest.raises(construir.TemasInvalidos) as capturado:
        construir_copia(copia_datos, tmp_path, dir_config=copia_config)
    assert "temas_tickets.csv" in str(capturado.value)
    assert isinstance(capturado.value, construir.ErrorConstruccion)
    assert not (tmp_path / "copia.duckdb").exists()


def test_un_archivo_de_temas_mal_formado_da_un_error_con_tipo_que_dice_el_renglon(copia_datos, copia_config, tmp_path):
    ruta = copia_config / "temas_tickets.csv"
    ruta.write_text(ruta.read_text(encoding="utf-8") + "Titulo Sin Normalizar,Tema\n", encoding="utf-8")
    with pytest.raises(construir.TemasInvalidos) as capturado:
        construir_copia(copia_datos, tmp_path, dir_config=copia_config)
    assert "debe ir normalizado" in capturado.value.detalle
    assert "titulo sin normalizar" in capturado.value.detalle
    assert not (tmp_path / "copia.duckdb").exists()


def test_un_error_de_temas_no_borra_la_base_anterior(copia_datos, copia_config, tmp_path):
    anterior = construir_copia(copia_datos, tmp_path, dir_config=copia_config)
    (copia_config / "temas_tickets.csv").unlink()
    with pytest.raises(construir.TemasInvalidos):
        construir_copia(copia_datos, tmp_path, dir_config=copia_config)
    with duckdb.connect(str(anterior.base), read_only=True) as conexion:
        assert uno(conexion, "SELECT count(*) FROM v_tickets") == TICKETS


def test_un_titulo_nuevo_queda_sin_clasificar_avisa_y_no_detiene_la_construccion(copia_datos, tmp_path):
    # La copia D del evaluador agrega el ticket 1286 con un título que el archivo no conoce.
    ruta = copia_datos / "tickets_mesa_servicio.csv"
    nuevo = "1286,2026-08-31 21:00,T02,TI,Media,Abierto,Revisión de tiempos,Descripción de prueba,\n"
    ruta.write_bytes(ruta.read_bytes() + nuevo.encode("utf-8"))
    with pytest.warns(UserWarning, match=r"1 título\(s\) de tickets no están en config/temas_tickets.csv") as avisos:
        resumen = construir_copia(copia_datos, tmp_path)
    # El aviso da la cuenta y la consulta para verlos; el texto libre del título no sale en un aviso.
    assert all("revision de tiempos" not in str(aviso.message).lower() for aviso in avisos)
    with duckdb.connect(str(resumen.base), read_only=True) as conexion:
        assert uno(conexion, "SELECT count(*) FROM v_tickets") == TICKETS + 1
        fila = conexion.execute("SELECT titulo_normalizado, tema FROM v_tickets WHERE ticket_id = 1286").fetchone()
        assert fila == ("revision de tiempos", texto_tickets.TEMA_SIN_CLASIFICAR)
        assert uno(conexion, "SELECT count(*) FROM v_tickets WHERE tema = ?", [texto_tickets.TEMA_SIN_CLASIFICAR]) == 1


def test_una_construccion_sin_titulos_nuevos_no_avisa(copia_datos, tmp_path, recwarn):
    construir_copia(copia_datos, tmp_path)
    assert [aviso for aviso in recwarn if "temas_tickets" in str(aviso.message)] == []


def test_una_fila_de_temas_de_un_titulo_que_no_esta_en_los_datos_se_tolera(copia_datos, copia_config, tmp_path):
    ruta = copia_config / "temas_tickets.csv"
    ruta.write_text(ruta.read_text(encoding="utf-8") + "titulo que ya no existe,Tema viejo\n", encoding="utf-8")
    resumen = construir_copia(copia_datos, tmp_path, dir_config=copia_config)
    with duckdb.connect(str(resumen.base), read_only=True) as conexion:
        assert uno(conexion, "SELECT count(*) FROM v_tickets WHERE tema = 'Tema viejo'") == 0
        assert uno(conexion, "SELECT count(DISTINCT titulo_normalizado) FROM v_tickets") == TITULOS_NORMALIZADOS


def test_cambiar_el_tema_de_un_titulo_en_el_archivo_cambia_la_base_sin_tocar_el_codigo(copia_datos, copia_config, tmp_path):
    ruta = copia_config / "temas_tickets.csv"
    texto = ruta.read_text(encoding="utf-8")
    anterior = "montacargas no arranca,Montacargas"
    assert anterior in texto
    ruta.write_text(texto.replace(anterior, "montacargas no arranca,Equipo de bodega", 1), encoding="utf-8")
    resumen = construir_copia(copia_datos, tmp_path, dir_config=copia_config)
    with duckdb.connect(str(resumen.base), read_only=True) as conexion:
        assert uno(conexion, "SELECT count(*) FROM v_tickets WHERE tema = 'Equipo de bodega'") == 6
        assert uno(conexion, "SELECT count(*) FROM v_tickets WHERE tema = 'Montacargas'") == 0


# --- Orden con el enmascarado (D15-A) -----------------------------------------------------------------


def test_el_titulo_normalizado_sale_del_titulo_ya_enmascarado(copia_datos, tmp_path):
    ruta = copia_datos / "tickets_mesa_servicio.csv"
    with open(ruta, encoding="utf-8", newline="") as archivo:
        lector = csv.DictReader(archivo)
        columnas = lector.fieldnames
        filas = list(lector)
    assert filas[0]["ticket_id"] == "1001"
    filas[0]["titulo"] = "Impresora atascada, llamar a Fulano Ejemplo al 55 0000 0000"
    with open(ruta, "w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)
    with pytest.warns(UserWarning):
        resumen = construir_copia(copia_datos, tmp_path)
    with duckdb.connect(str(resumen.base), read_only=True) as conexion:
        guardado, normalizado = conexion.execute("SELECT titulo, titulo_normalizado FROM v_tickets WHERE ticket_id = 1001").fetchone()
    for texto in (guardado, normalizado):
        assert "fulano" not in texto.lower()
        assert "0000" not in texto
    assert normalizado.startswith("impresora atascada llamar a persona")
    assert "telefono" in normalizado
