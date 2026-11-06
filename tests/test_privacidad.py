"""Inventario y enmascarado de datos personales de los tickets (T1.4, D15-A, K05, K06).

Acepta: el celular y el nombre del ticket 1043, copiados del CSV dentro de la prueba y nunca
escritos en el código, no aparecen en build/tienda.duckdb, en logs/ ni en las respuestas de P34
y P43; la revisión de los 285 tickets queda anotada.

El celular y el nombre se leen de datos/tickets_mesa_servicio.csv al correr la prueba: este archivo
no los contiene, ni completos ni en partes. La base se construye en un directorio temporal
(tests/conftest.py), nunca en build/. Los números de teléfono de las pruebas unitarias son
inventados.
"""

import csv
import hashlib
import re
import shutil

import duckdb
import pytest

from agente import construir, privacidad
from tests.conftest import DIR_DATOS

ARCHIVO_TICKETS = DIR_DATOS / "tickets_mesa_servicio.csv"
TICKET_CON_DATOS = 1043
TICKETS_REVISADOS = 285
# P43: tickets de Mantenimiento de Valle Norte (T01) levantados en junio, según la rúbrica.
TICKETS_P43 = [1012, 1013, 1029, 1039, 1043]


# --- Lo que la prueba copia del CSV ---------------------------------------------------------------


class DatoPersonal(str):
    """Un dato personal leído del CSV: se compara como texto, pero su repr no lo muestra.

    pytest imprime con repr() los argumentos de la prueba que falla; con este tipo la salida dice
    <dato personal> en lugar del celular o el nombre.
    """

    def __repr__(self) -> str:
        return "<dato personal>"


class TicketsDelCsv(dict):
    """Los tickets del CSV por ticket_id; su repr da la cuenta y no el texto, que trae el 1043."""

    def __repr__(self) -> str:
        return f"<{len(self)} tickets del CSV>"


def leer_tickets() -> dict[int, dict[str, str]]:
    with open(ARCHIVO_TICKETS, encoding="utf-8", newline="") as archivo:
        return {int(fila["ticket_id"]): fila for fila in csv.DictReader(archivo)}


@pytest.fixture(scope="module")
def tickets_csv():
    return TicketsDelCsv(leer_tickets())


@pytest.fixture(scope="module")
def celular(tickets_csv):
    """El celular del 1043 tal como viene escrito: diez dígitos con separadores."""
    descripcion = tickets_csv[TICKET_CON_DATOS]["descripcion"]
    encontrados = re.findall(r"\d[\d .\-]{8,}\d", descripcion)
    cuantos = len(encontrados)
    assert cuantos == 1, "el 1043 debe traer exactamente un número largo"
    diez_digitos = len(re.sub(r"\D", "", encontrados[0])) == 10
    assert diez_digitos, "el número largo del 1043 no tiene 10 dígitos"
    return DatoPersonal(encontrados[0])


@pytest.fixture(scope="module")
def nombre(tickets_csv):
    """El nombre de quien levantó el 1043: lo que sigue a «Reporta» hasta la primera coma."""
    descripcion = tickets_csv[TICKET_CON_DATOS]["descripcion"]
    encontrado = re.match(r"Reporta ([^,]+),", descripcion)
    assert encontrado, "la descripción del 1043 cambió de forma; revisa la prueba"
    valor = encontrado.group(1)
    palabras = len(valor.split())
    assert palabras >= 2, "el nombre del 1043 debe tener al menos dos palabras"
    return DatoPersonal(valor)


def variantes_celular(celular: str) -> list[str]:
    """El celular como viene, solo los dígitos y con otros separadores."""
    digitos = re.sub(r"\D", "", celular)
    grupos = re.findall(r"\d+", celular)
    return [celular, digitos, "-".join(grupos), " ".join(grupos), f"{digitos[:2]} {digitos[2:6]} {digitos[6:]}"]


def variantes_nombre(nombre: str) -> list[str]:
    """El nombre completo y cada apellido (las palabras después de la primera)."""
    palabras = nombre.split()
    return [nombre, " ".join(palabras[1:]), *palabras[1:]]


@pytest.fixture(scope="module")
def datos_del_1043(celular, nombre):
    return [DatoPersonal(valor) for valor in variantes_celular(celular) + variantes_nombre(nombre)]


def huellas(valores: list[str]) -> list[str]:
    """md5, sha1 y sha256 en hexadecimal de cada valor: K06 prohíbe guardar el hash del dato."""
    resultado = []
    for valor in valores:
        for algoritmo in ("md5", "sha1", "sha256"):
            resultado.append(DatoPersonal(hashlib.new(algoritmo, valor.encode("utf-8")).hexdigest()))
    return resultado


# Ninguna aserción compara el dato directamente ni recibe dentro del assert el texto donde se busca:
# pytest imprime los dos lados de una comparación que falla y los argumentos de cada llamada, y el
# celular o el nombre acabarían en la salida de la prueba. Se comparan booleanos o posiciones,
# calculados antes del assert, y los datos del CSV viajan en DatoPersonal y TicketsDelCsv.

# --- Lo que se busca en la base -------------------------------------------------------------------


def textos_de_la_base(ruta) -> str:
    """Todo valor de toda columna de toda tabla y vista de la base, convertido a texto.

    Se consulta a DuckDB en lugar de leer los bytes del archivo porque DuckDB comprime las
    cadenas: un valor presente podría no verse byte a byte.
    """
    partes = []
    with duckdb.connect(str(ruta), read_only=True) as con:
        columnas = con.execute(
            "SELECT table_schema, table_name, column_name FROM information_schema.columns "
            "WHERE table_catalog = current_database() ORDER BY 1, 2, 3"
        ).fetchall()
        assert columnas, "la base no trae columnas"
        for esquema, tabla, columna in columnas:
            filas = con.execute(f'SELECT CAST("{columna}" AS VARCHAR) FROM "{esquema}"."{tabla}"').fetchall()
            partes.extend(valor for (valor,) in filas if valor is not None)
    return "\n".join(partes)


def bytes_de_la_base(ruta) -> bytes:
    contenido = ruta.read_bytes()
    wal = ruta.with_name(f"{ruta.name}.wal")
    if wal.exists():
        contenido += wal.read_bytes()
    return contenido


def presentes(valores: list[str], texto: str) -> list[int]:
    """Posiciones (no valores) de los datos que aparecen en el texto, para no imprimirlos."""
    return [indice for indice, valor in enumerate(valores) if valor in texto]


# --- La búsqueda sí ve el dato cuando está (la prueba no es vacía) -------------------------------


def test_el_csv_trae_el_celular_y_el_nombre_del_1043(tickets_csv, celular, nombre):
    descripcion = tickets_csv[TICKET_CON_DATOS]["descripcion"]
    posiciones = presentes([celular, nombre], descripcion)
    assert posiciones == [0, 1]
    # Usa puntos como separador: un enmascarador de dígitos seguidos no lo ve (P43).
    con_puntos = "." in celular
    assert con_puntos, "el celular del 1043 ya no usa puntos; revisa la prueba"


def test_la_busqueda_encuentra_el_dato_en_una_base_sin_enmascarar(tmp_path, datos_del_1043):
    ruta = tmp_path / "cruda.duckdb"
    with duckdb.connect(str(ruta)) as con:
        con.execute("CREATE TABLE tickets AS SELECT * FROM read_csv(?, all_varchar = true)", [str(ARCHIVO_TICKETS)])
    texto = textos_de_la_base(ruta)
    celular_visto = presentes(datos_del_1043[:1], texto)
    apellidos_vistos = presentes(datos_del_1043[-3:], texto)
    assert celular_visto == [0]
    assert apellidos_vistos != []


# --- Criterio de aceptación: la base ---------------------------------------------------------------


def test_la_base_no_trae_el_celular_ni_el_nombre_del_1043(base_construida, datos_del_1043):
    encontrados = presentes(datos_del_1043, textos_de_la_base(base_construida.base))
    assert encontrados == []


def test_el_archivo_de_la_base_no_trae_el_celular_ni_el_nombre_byte_a_byte(base_construida, datos_del_1043):
    contenido = bytes_de_la_base(base_construida.base)
    assert [i for i, valor in enumerate(datos_del_1043) if valor.encode("utf-8") in contenido] == []


def test_la_base_no_guarda_el_hash_del_celular_ni_del_nombre(base_construida, datos_del_1043):
    marcas = huellas(datos_del_1043)
    en_los_valores = presentes(marcas, textos_de_la_base(base_construida.base))
    assert en_los_valores == []
    contenido = bytes_de_la_base(base_construida.base)
    assert [i for i, valor in enumerate(marcas) if valor.encode("ascii") in contenido] == []


def test_la_base_con_indice_de_texto_tampoco_los_trae(tmp_path, datos_del_1043):
    """La base completa, como la deja la línea de comandos. Sin fts instalada se salta: sin red no se descarga."""
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")
    resumen = construir.construir(DIR_DATOS, tmp_path / "tienda.duckdb")
    encontrados = presentes(datos_del_1043, textos_de_la_base(resumen.base))
    assert encontrados == []
    contenido = bytes_de_la_base(resumen.base)
    assert [i for i, valor in enumerate(datos_del_1043) if valor.encode("utf-8") in contenido] == []


def test_el_1043_queda_con_marcadores_por_tipo_y_consecutivo(base_construida, tickets_csv, celular, nombre):
    """Solo cambian el nombre y el celular; el resto de la descripción se conserva tal cual."""
    original = tickets_csv[TICKET_CON_DATOS]["descripcion"]
    esperada = original.replace(nombre, "<PERSONA_1>").replace(celular, "<TELEFONO_1>")
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        guardada = con.execute("SELECT descripcion FROM tickets WHERE ticket_id = ?", [TICKET_CON_DATOS]).fetchone()[0]
        vista = con.execute("SELECT descripcion FROM v_tickets WHERE ticket_id = ?", [TICKET_CON_DATOS]).fetchone()[0]
    tabla_igual = guardada == esperada
    vista_igual = vista == esperada
    assert tabla_igual, "la descripción guardada del 1043 no es la original con <PERSONA_1> y <TELEFONO_1>"
    assert vista_igual, "v_tickets no da la descripción enmascarada del 1043"


# --- Criterio de aceptación: la revisión de los 285 tickets ----------------------------------------


def test_la_revision_de_los_285_encuentra_datos_personales_solo_en_el_1043(tickets_csv):
    assert len(tickets_csv) == TICKETS_REVISADOS
    con_datos = {}
    for ticket_id, fila in tickets_csv.items():
        for campo in ("titulo", "descripcion"):
            tipos = privacidad.buscar_datos_personales(fila[campo])
            if tipos:
                con_datos[(ticket_id, campo)] = tipos
    assert con_datos == {(TICKET_CON_DATOS, "descripcion"): ["TELEFONO", "PERSONA"]}


def test_la_base_conserva_sin_cambios_los_otros_284_tickets(base_construida, tickets_csv):
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        guardados = {t: (titulo, descripcion) for t, titulo, descripcion in con.execute("SELECT ticket_id, titulo, descripcion FROM tickets").fetchall()}
    cambiados = [t for t, fila in tickets_csv.items() if guardados[t] != (fila["titulo"], fila["descripcion"])]
    assert cambiados == [TICKET_CON_DATOS]


def test_el_inventario_del_readme_clasifica_el_1043_y_anota_la_revision():
    texto = (DIR_DATOS.parent / "README.md").read_text(encoding="utf-8")
    seccion = texto.split("## Inventario de datos personales", 1)
    assert len(seccion) == 2, "falta la sección del inventario en el README"
    inventario = seccion[1].split("\n## ", 1)[0]
    assert "1043" in inventario
    assert "285" in inventario
    assert re.search(r"Moderado|Bajo|Alto", inventario)
    for columna in ("descripcion", "titulo"):
        assert columna in inventario


# --- Criterio de aceptación: P34 y P43, en los datos que leerán las herramientas -------------------


def test_p43_los_5_tickets_de_mantenimiento_de_valle_norte_en_junio_salen_sin_datos(base_construida, datos_del_1043):
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        filas = con.execute(
            "SELECT ticket_id, titulo, descripcion FROM v_tickets WHERE categoria = 'Mantenimiento' "
            "AND tienda_id = 'T01' AND month(fecha_creacion) = 6 ORDER BY ticket_id"
        ).fetchall()
    assert [fila[0] for fila in filas] == TICKETS_P43
    texto = "\n".join(f"{titulo}\n{descripcion}" for _, titulo, descripcion in filas)
    encontrados = presentes(datos_del_1043, texto)
    assert encontrados == []
    tipos = privacidad.buscar_datos_personales(texto)
    assert tipos == []


def test_p34_el_detalle_completo_del_1043_sale_sin_datos(base_construida, datos_del_1043):
    with duckdb.connect(str(base_construida.base), read_only=True) as con:
        fila = con.execute("SELECT * FROM v_tickets WHERE ticket_id = ?", [TICKET_CON_DATOS]).fetchone()
    texto = " ".join(str(valor) for valor in fila)
    encontrados = presentes(datos_del_1043, texto)
    assert encontrados == []
    tipos = privacidad.buscar_datos_personales(texto)
    assert tipos == []


# --- Mensajes de error de la carga (lo que llega a stderr y al registro) --------------------------


def test_un_ticket_mal_formado_da_un_error_que_no_copia_la_descripcion(tmp_path, datos_del_1043):
    """Si la fila del 1043 trae una fecha inválida, el error nombra el archivo sin copiar la línea."""
    datos = tmp_path / "datos"
    shutil.copytree(DIR_DATOS, datos)
    ruta = datos / "tickets_mesa_servicio.csv"
    renglones = ruta.read_text(encoding="utf-8").split("\n")
    indice = next(i for i, r in enumerate(renglones) if r.startswith(f"{TICKET_CON_DATOS},"))
    renglones[indice] = re.sub(r"^(\d+),[^,]+,", r"\1,2026-13-45 99:99,", renglones[indice])
    ruta.write_text("\n".join(renglones), encoding="utf-8", newline="\n")
    with pytest.raises(construir.CsvInvalido) as error:
        construir.construir(datos, tmp_path / "copia.duckdb", politicas=False, indice_texto=False)
    nombra_el_archivo = "tickets_mesa_servicio.csv" in str(error.value)
    assert nombra_el_archivo, "el error no nombra el archivo de tickets"
    encontrados = presentes(datos_del_1043, str(error.value))
    assert encontrados == []


# --- Reglas de D15-A: teléfonos, nombres y correos inventados -------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "cel 55.1234.5678 para coordinar",
        "cel 555.123.4567 para coordinar",
        "cel 55-1234-5678 para coordinar",
        "cel 55 1234 5678 para coordinar",
        "cel (55) 1234 5678 para coordinar",
        "cel +52 55 1234 5678 para coordinar",
        "cel 5512345678 para coordinar",
        "cel 81 12 34 56 78 para coordinar",
        "cel.55.1234.5678 para coordinar",
    ],
)
def test_enmascara_telefonos_con_punto_guion_espacio_o_seguidos(texto):
    resultado = privacidad.enmascarar(texto)
    assert re.sub(r"\D", "", resultado) == "1"
    assert "<TELEFONO_1>" in resultado
    assert resultado.endswith(" para coordinar")


def test_un_telefono_al_final_de_la_frase_conserva_el_punto():
    assert privacidad.enmascarar("Llamar al 55.1234.5678.") == "Llamar al <TELEFONO_1>."


def test_un_telefono_pegado_a_otro_numero_tambien_se_enmascara():
    assert privacidad.enmascarar("caja 2 55 1234 5678") == "caja 2 <TELEFONO_1>"


@pytest.mark.parametrize(
    "texto",
    [
        "En la recepcion de PRV-12 faltaron 14 cajas contra la factura.",
        "La impresora PrintMax TX-200 de la caja 3 no imprime.",
        "Aviso: del 14 al 25 de julio se remodela el pasillo central y el área de cajas 1 a 6.",
        "Se levantó el 2026-06-02 10:15 y se cerró el 2026-06-04 08:59.",
        "La diferencia fue de 1,430.00 pesos en la caja 12.",
        "Encontramos Queso manchego rebanado 200 g caducado en anaquel.",
        "Se nos está acabando Aguacate Hass kg y el pedido normal llega hasta el jueves.",
        "Reporta que la puerta de la entrada principal se atora.",
        "El correo del gerente no baja mensajes desde ayer.",
    ],
)
def test_no_toca_textos_sin_datos_personales(texto):
    assert privacidad.enmascarar(texto) == texto
    assert privacidad.buscar_datos_personales(texto) == []


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Reporta Juan Pérez, encargado de turno.", "Reporta <PERSONA_1>, encargado de turno."),
        ("De parte de María de la Luz Ortiz: urge el técnico.", "De parte de <PERSONA_1>: urge el técnico."),
        ("Favor de llamar a Ana Sofía Treviño mañana.", "Favor de llamar a <PERSONA_1> mañana."),
        ("Lo atendió el Sr. Ramírez en caja.", "Lo atendió el Sr. <PERSONA_1> en caja."),
        ("reportó Luis Garza y luego habló con Rosa Elena Díaz.", "reportó <PERSONA_1> y luego habló con <PERSONA_2>."),
    ],
)
def test_enmascara_nombres_despues_de_una_frase_que_presenta_a_una_persona(texto, esperado):
    assert privacidad.enmascarar(texto) == esperado


def test_el_mismo_dato_repetido_lleva_el_mismo_consecutivo():
    texto = "Reporta Juan Pérez, cel 55.1234.5678. Si no contesta, llamar a Juan Pérez al 55 1234 5678 o al 81 1111 2222."
    assert privacidad.enmascarar(texto) == (
        "Reporta <PERSONA_1>, cel <TELEFONO_1>. Si no contesta, llamar a <PERSONA_1> al <TELEFONO_1> o al <TELEFONO_2>."
    )


def test_enmascara_correos():
    assert privacidad.enmascarar("Escribir a juan.perez@ejemplo.com.mx hoy.") == "Escribir a <CORREO_1> hoy."


def test_el_consecutivo_empieza_en_1_en_cada_texto():
    """Sin estado entre llamadas: nada queda guardado que permita ligar o reidentificar."""
    assert privacidad.enmascarar("cel 55.1234.5678") == "cel <TELEFONO_1>"
    assert privacidad.enmascarar("cel 81.1111.2222") == "cel <TELEFONO_1>"


def test_buscar_datos_personales_da_los_tipos_y_no_los_valores():
    tipos = privacidad.buscar_datos_personales("Reporta Juan Pérez, cel 55.1234.5678, juan@ejemplo.com")
    assert tipos == ["TELEFONO", "CORREO", "PERSONA"]


def test_enmascarar_acepta_nulo_y_vacio():
    assert privacidad.enmascarar(None) is None
    assert privacidad.enmascarar("") == ""
