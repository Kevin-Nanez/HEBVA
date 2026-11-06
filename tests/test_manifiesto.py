"""Manifiesto de cobertura: lo que sabe el sistema, leído de la base y sin llamar al modelo (T4.2, C4.4, C3.1, K41).

Acepta: "el registro marca 0 tokens en ese turno; la pantalla trae tiendas, periodos, cortes, documentos y 3 ejemplos
de preguntas que no puede contestar".

Lo que cubre cada prueba sin red ni llaves, con la base de datos/ construida una vez en un directorio temporal:

- El manifiesto trae las 4 tiendas con su ciudad, su formato y sus canales; el periodo y el corte de cada fuente; los
  4 documentos con su versión y los que se mencionan y no vienen; y exactamente 3 ejemplos de preguntas que no puede
  contestar, que son los límites de K41 (ticket promedio, fecha fuera de corte y documento que no viene).
- Sale de la base y de la configuración, no de un texto fijo: con otra copia de datos/ y de config/ cambia.
- El turno de "qué sabes" deja una línea con 0 llamadas y 0 tokens en cada uno de sus campos, y no llama al modelo ni
  lee .env.
- Sin un archivo de datos dice que no pudo consultar, sin cifra, y con logs/ que no se puede escribir responde igual
  (K27).
- Solo el mensaje que es "qué sabes" o ayuda, con o sin un saludo antes, va al manifiesto; un saludo solo y una
  pregunta de datos que empieza igual van al modelo.

Que una persona que no construyó la interfaz la use sin ayuda es de una persona (pendiente_humano); las pruebas de la
pantalla están en tests/test_app.py.
"""

import dataclasses
import datetime as dt
import json
import re
import uuid

import duckdb
import pydantic_ai.models as modelos_de_pydantic
import pytest
import yaml

from agente import agente, bloques, construir, manifiesto, privacidad, registro
from agente.historial import Historial
from tests.conftest import DIR_DATOS

HOY = dt.date(2026, 10, 31)
# Sin diez dígitos seguidos: registro.py cambia por <TELEFONO_1> un id que los trae (defecto de T4.3 que se reporta
# aparte), y lo traen el 2.6 % de los UUID al azar. Con un id fijo la prueba busca el turno por su id sin depender
# de la suerte.
UUID_FIJO = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ID_SESION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

TITULOS = [
    "Tiendas (4)",
    "Periodos con datos",
    "Cortes",
    "Documentos (4)",
    "Ejemplos de preguntas que no puedo contestar",
]
CAMPOS_DE_USO = (
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_creation.input_tokens",
    "entrada_no_cacheada",
    "razonamiento",
)


# ------------------------------------------------------------------ la base


@pytest.fixture(scope="module")
def fts():
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip(
                "la extensión fts no está instalada y las pruebas corren sin red"
            )


@pytest.fixture(scope="module")
def base(fts, tmp_path_factory):
    """La base de datos/ del módulo, construida una vez con asegurar_base() como en cada turno."""
    ruta = tmp_path_factory.mktemp("manifiesto") / "tienda.duckdb"
    construir.asegurar_base(DIR_DATOS, ruta)
    return ruta


@pytest.fixture(scope="module")
def cobertura(base):
    return manifiesto.leer(base=base)


@pytest.fixture
def archivo(tmp_path):
    return tmp_path / "logs" / "turnos.jsonl"


def responder(pregunta, base, archivo=None, historial=None, **opciones):
    return manifiesto.responder(
        pregunta,
        historial,
        base=base,
        hoy_real=HOY,
        archivo_registro=archivo,
        **opciones,
    )


def fuente(cobertura, clave):
    return next(f for f in cobertura.fuentes if f.clave == clave)


def lineas_de(cobertura):
    return manifiesto.texto(cobertura).splitlines()


# ------------------------------------------------------------------ lo que trae el manifiesto


def test_trae_las_cuatro_tiendas_con_su_ciudad_su_formato_y_sus_canales(cobertura):
    assert [(t.id, t.nombre) for t in cobertura.tiendas] == [
        ("T01", "Tienda Valle Norte"),
        ("T02", "Tienda Cumbres"),
        ("T03", "Tienda Anáhuac"),
        ("T04", "Tienda Lomas del Sur"),
    ]
    assert [(t.ciudad, t.estado) for t in cobertura.tiendas] == [
        ("Monterrey", "Nuevo León"),
        ("Monterrey", "Nuevo León"),
        ("San Nicolás de los Garza", "Nuevo León"),
        ("Saltillo", "Coahuila"),
    ]
    assert [t.formato for t in cobertura.tiendas] == [
        "Supermercado",
        "Supermercado",
        "Mi Tienda",
        "Mi Tienda",
    ]
    # El canal en línea es solo de los supermercados (diccionario de datos): lo dice la tabla de cobertura.
    assert [t.canales for t in cobertura.tiendas] == [
        ("Piso", "Ecommerce"),
        ("Piso", "Ecommerce"),
        ("Piso",),
        ("Piso",),
    ]


def test_trae_el_periodo_y_el_corte_de_cada_fuente(cobertura):
    assert [f.clave for f in cobertura.fuentes] == ["ventas", "tickets", "inventario"]
    periodos = {f.clave: (f.desde, f.hasta) for f in cobertura.fuentes}
    assert periodos == {
        "ventas": (dt.date(2026, 6, 1), dt.date(2026, 8, 31)),
        "tickets": (dt.date(2026, 6, 2), dt.date(2026, 8, 31)),
        "inventario": (dt.date(2026, 9, 1), dt.date(2026, 9, 1)),
    }
    assert [f.corte for f in cobertura.fuentes] == [
        dt.date(2026, 8, 31),
        dt.date(2026, 8, 31),
        dt.date(2026, 9, 1),
    ]
    assert cobertura.productos == 121


def test_trae_los_cuatro_documentos_con_su_titulo_su_version_y_su_vigencia(cobertura):
    assert [
        (d.codigo, d.titulo, d.version, d.vigencia) for d in cobertura.documentos
    ] == [
        (
            "FAQ-OPS-001",
            "Preguntas frecuentes para gerentes de tienda",
            "1.4",
            dt.date(2026, 1, 12),
        ),
        (
            "MAN-OPS-007",
            "Manual de apertura y cierre de tienda",
            "2.5",
            dt.date(2025, 10, 1),
        ),
        ("POL-OPS-014", "Política de mermas y caducidad", "3.2", dt.date(2025, 3, 1)),
        (
            "PRO-SAC-021",
            "Procedimiento de devoluciones y cambios",
            "4.0",
            dt.date(2025, 6, 15),
        ),
    ]


def test_trae_los_documentos_que_se_mencionan_y_no_vienen_con_el_lugar_donde_se_citan(
    cobertura,
):
    assert [(f.codigo, f.citado_en) for f in cobertura.no_incluidos] == [
        ("GUI-CAL-002", ("POL-OPS-014 §1.2, p. 1",)),
        ("PRO-ECO-009", ("FAQ-OPS-001 P18, p. 5", "PRO-SAC-021 §1.1, p. 1")),
        ("PRO-LOG-003", ("POL-OPS-014 §1.2, p. 1", "POL-OPS-014 §9, p. 6")),
    ]


def test_trae_exactamente_tres_ejemplos_y_son_los_limites_de_k41(cobertura):
    ticket, fuera_de_corte, documento = cobertura.ejemplos
    assert len(cobertura.ejemplos) == 3

    assert ticket.pregunta == "¿Cuál es el ticket promedio de una tienda?"
    # El motivo es el que declara config/supuestos.yaml, no un texto escrito otra vez.
    assert ticket.motivo == (
        "ventas.csv tiene una fila por día, tienda, SKU y canal; no trae transacciones ni número de tickets de compra"
    )

    # La fecha sale del corte de ventas: el día siguiente nunca tiene datos.
    assert fuera_de_corte.pregunta == "¿Cuánto se vendió el 1 de septiembre de 2026?"
    assert fuera_de_corte.motivo == "las ventas llegan al 31 de agosto de 2026"

    assert documento.pregunta == "¿Qué dice el documento GUI-CAL-002?"
    assert (
        documento.motivo
        == "GUI-CAL-002 se menciona en POL-OPS-014 §1.2, p. 1, pero no viene entre los documentos"
    )


# ------------------------------------------------------------------ el texto


def test_el_texto_tiene_las_cinco_secciones_en_orden_y_abre_con_lo_que_cubre(cobertura):
    secciones = manifiesto.secciones(cobertura)
    assert [seccion.titulo for seccion in secciones] == TITULOS

    texto = manifiesto.texto(cobertura)
    posiciones = [texto.index(titulo) for titulo in TITULOS]
    assert posiciones == sorted(posiciones)
    primera = texto.splitlines()[0]
    assert primera == (
        "Consulto las ventas, el inventario, las caducidades y los tickets de la mesa de servicio de 4 tiendas, "
        "y 4 documentos de políticas. Todo lo que respondo sale de esos datos; lo que no cubren, te lo respondo."
    )
    # Cada sección empieza por su título y sigue con sus líneas: es lo que la pantalla inicial dibuja bloque a bloque.
    for seccion in secciones:
        assert seccion.texto in texto
        assert seccion.texto.splitlines()[0] == seccion.titulo


def tablas_de(cobertura):
    """Las tablas del texto tal como las parte la pantalla (agente/bloques.py)."""
    return [
        bloque
        for bloque in bloques.bloques_de(manifiesto.texto(cobertura), listas=False)
        if isinstance(bloque, bloques.Tabla)
    ]


def test_tiendas_periodos_y_documentos_van_en_tabla_y_cortes_y_ejemplos_en_vinetas(
    cobertura,
):
    tiendas, periodos, documentos = tablas_de(cobertura)
    assert tiendas.encabezados[0] == "Tienda"
    assert periodos.encabezados[0] == "Fuente"
    assert documentos.encabezados[0] == "Código"
    lineas = lineas_de(cobertura)
    # Cada tabla va justo debajo del título de su sección.
    for titulo in ("Tiendas (4)", "Periodos con datos", "Documentos (4)"):
        assert lineas[lineas.index(titulo) + 1].startswith("| ")
    for titulo in ("Cortes", "Ejemplos de preguntas que no puedo contestar"):
        assert lineas[lineas.index(titulo) + 1].startswith("- ")


def test_las_tiendas_se_leen_una_por_fila_con_sus_canales(cobertura):
    tiendas = tablas_de(cobertura)[0]
    assert tiendas.encabezados == (
        "Tienda",
        "Clave",
        "Ciudad",
        "Estado",
        "Formato",
        "Ventas",
    )
    ambos_canales = (
        "Monterrey",
        "Nuevo León",
        "Supermercado",
        "En piso y en línea (Ecommerce)",
    )
    assert tiendas.filas == (
        ("Tienda Valle Norte", "T01", *ambos_canales),
        ("Tienda Cumbres", "T02", *ambos_canales),
        (
            "Tienda Anáhuac",
            "T03",
            "San Nicolás de los Garza",
            "Nuevo León",
            "Mi Tienda",
            "Solo en piso",
        ),
        (
            "Tienda Lomas del Sur",
            "T04",
            "Saltillo",
            "Coahuila",
            "Mi Tienda",
            "Solo en piso",
        ),
    )


def test_una_barra_en_un_dato_no_parte_la_celda(cobertura):
    tienda = dataclasses.replace(
        cobertura.tiendas[0], nombre="Tienda | Falsa", ciudad="Monterrey\nNorte"
    )
    otra = dataclasses.replace(cobertura, tiendas=(tienda,))
    (fila,) = tablas_de(otra)[0].filas
    assert fila[:3] == ("Tienda | Falsa", "T01", "Monterrey Norte")


def test_los_periodos_y_los_cortes_se_leen_con_fecha_larga(cobertura):
    periodos = tablas_de(cobertura)[1]
    assert periodos.encabezados == ("Fuente", "Datos")
    assert periodos.filas == (
        ("Ventas", "Del 1 de junio de 2026 al 31 de agosto de 2026"),
        (
            "Tickets de la mesa de servicio",
            "Del 2 de junio de 2026 al 31 de agosto de 2026",
        ),
        (
            "Inventario",
            "Una sola foto, la del 1 de septiembre de 2026; no hay historial",
        ),
        ("Catálogo", "121 productos"),
    )

    lineas = lineas_de(cobertura)
    desde = lineas.index("Cortes")
    assert lineas[desde + 1 : desde + 5] == [
        "- Ventas: 31 de agosto de 2026.",
        "- Tickets: 31 de agosto de 2026.",
        "- Inventario: 1 de septiembre de 2026.",
        "No tengo datos posteriores a esas fechas.",
    ]


def test_los_documentos_se_leen_con_codigo_titulo_version_y_vigencia(cobertura):
    documentos = tablas_de(cobertura)[2]
    assert documentos.encabezados == (
        "Código",
        "Documento",
        "Versión",
        "Vigente desde",
    )
    assert documentos.filas == (
        (
            "FAQ-OPS-001",
            "Preguntas frecuentes para gerentes de tienda",
            "1.4",
            "12 de enero de 2026",
        ),
        (
            "MAN-OPS-007",
            "Manual de apertura y cierre de tienda",
            "2.5",
            "1 de octubre de 2025",
        ),
        (
            "POL-OPS-014",
            "Política de mermas y caducidad",
            "3.2",
            "1 de marzo de 2025",
        ),
        (
            "PRO-SAC-021",
            "Procedimiento de devoluciones y cambios",
            "4.0",
            "15 de junio de 2025",
        ),
    )
    # Los que no vienen siguen en una línea debajo de la tabla.
    lineas = lineas_de(cobertura)
    nota = "No vienen, aunque se mencionan en ellos: GUI-CAL-002, PRO-ECO-009 y PRO-LOG-003. No tengo su contenido."
    assert lineas[lineas.index(nota) - 1] == (
        "| PRO-SAC-021 | Procedimiento de devoluciones y cambios | 4.0 | 15 de junio de 2025 |"
    )


def test_los_ejemplos_traen_la_pregunta_entre_comillas_y_el_motivo_debajo(cobertura):
    lineas = lineas_de(cobertura)
    desde = lineas.index("Ejemplos de preguntas que no puedo contestar")
    assert lineas[desde + 1 :] == [
        "- «¿Cuál es el ticket promedio de una tienda?»",
        "  Porque ventas.csv tiene una fila por día, tienda, SKU y canal; "
        "no trae transacciones ni número de tickets de compra.",
        "- «¿Cuánto se vendió el 1 de septiembre de 2026?»",
        "  Porque las ventas llegan al 31 de agosto de 2026.",
        "- «¿Qué dice el documento GUI-CAL-002?»",
        "  Porque GUI-CAL-002 se menciona en POL-OPS-014 §1.2, p. 1, pero no viene entre los documentos.",
    ]
    assert len([linea for linea in lineas if linea.startswith("- «¿")]) == 3


def test_el_texto_se_lee_como_texto_plano_y_con_el_formato_de_numeros_del_proyecto(
    cobertura,
):
    texto = manifiesto.texto(cobertura)
    # Ni Markdown ni la raya como conector ni fechas ISO: la gerente lee fechas largas (K23, C4.4).
    for marca in ("**", "__", "`", "](", "—", "\t"):
        assert marca not in texto, marca
    assert not re.search(r"\d{4}-\d{2}-\d{2}", texto)
    # Punto decimal y coma de miles; nunca coma decimal (AGENTS.md).
    assert not re.search(r"\d,\d{1,2}(?!\d)", texto)
    assert "| 3.2 |" in texto
    # Ningún teléfono ni correo: es un texto de la base, no de los tickets.
    assert privacidad.buscar_datos_personales(texto) == []


def test_el_manifiesto_guarda_el_hash_de_las_fuentes_de_la_base_que_leyo(
    base, cobertura
):
    guardados = construir.leer_hashes(base)
    assert cobertura.hash_fuentes == guardados.hash_fuentes.sha256
    assert cobertura.base_reconstruida is False


# ------------------------------------------------------------------ sale de la base y de la configuración


def cambiar_configuracion(copia_config, cambio):
    ruta = copia_config / "supuestos.yaml"
    contenido = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    cambio(contenido)
    ruta.write_text(
        yaml.safe_dump(contenido, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def test_sigue_a_los_datos_y_a_la_configuracion_y_no_a_un_texto_fijo(
    fts, tmp_path, copia_datos, copia_config
):
    tiendas = copia_datos / "tiendas.csv"
    tiendas.write_bytes(
        tiendas.read_bytes().replace(b"Tienda Cumbres", b"Tienda Cumbres Nueva")
    )
    cambiar_configuracion(
        copia_config, lambda c: c["cortes"]["ventas"].update(hasta=dt.date(2026, 7, 31))
    )
    destino = tmp_path / "otra.duckdb"
    construir.asegurar_base(copia_datos, destino, dir_config=copia_config)

    cambiado = manifiesto.leer(
        base=destino, dir_datos=copia_datos, dir_config=copia_config
    )

    assert cambiado.tiendas[1].nombre == "Tienda Cumbres Nueva"
    ventas = fuente(cambiado, "ventas")
    # El periodo sale de los datos y el corte de la configuración: cada uno dice lo suyo.
    assert (ventas.hasta, ventas.corte) == (dt.date(2026, 8, 31), dt.date(2026, 7, 31))
    lineas = lineas_de(cambiado)
    assert (
        "| Tienda Cumbres Nueva | T02 | Monterrey | Nuevo León | Supermercado | En piso y en línea (Ecommerce) |"
    ) in lineas
    assert "| Ventas | Del 1 de junio de 2026 al 31 de agosto de 2026 |" in lineas
    assert "- Ventas: 31 de julio de 2026." in lineas
    assert "- «¿Cuánto se vendió el 1 de agosto de 2026?»" in lineas
    assert "  Porque las ventas llegan al 31 de julio de 2026." in lineas


# ------------------------------------------------------------------ los ejemplos que no puede contestar

METRICAS = {
    "ticket_promedio": "ventas.csv tiene una fila por día; no trae transacciones",
    "venta_por_hora": "las ventas vienen por día, sin hora",
    "meta_venta": "no hay metas de venta en los datos ni en los documentos",
    "primera_respuesta": "los tickets no registran la fecha de primera respuesta",
    "merma_real": "ningún archivo registra merma (D1-A): se cita la tolerancia de POL-OPS-014 §4",
    "inventario_historico": "el inventario es una sola foto al 2026-09-01; no hay historial",
}
FALTANTE = manifiesto.NoIncluido("PRO-ECO-009", ("PRO-SAC-021 §1.1, p. 1",))
CORTE = dt.date(2026, 8, 31)


def preguntas(ejemplos):
    return [ejemplo.pregunta for ejemplo in ejemplos]


def test_los_ejemplos_van_en_el_orden_de_k41_ticket_promedio_fecha_y_documento():
    ejemplos = manifiesto.elegir_ejemplos(METRICAS, CORTE, [FALTANTE])
    assert preguntas(ejemplos) == [
        "¿Cuál es el ticket promedio de una tienda?",
        "¿Cuánto se vendió el 1 de septiembre de 2026?",
        "¿Qué dice el documento PRO-ECO-009?",
    ]


def test_sin_documentos_que_falten_el_tercer_ejemplo_es_otro_limite_declarado():
    ejemplos = manifiesto.elegir_ejemplos(METRICAS, CORTE, [])
    assert preguntas(ejemplos) == [
        "¿Cuál es el ticket promedio de una tienda?",
        "¿Cuánto se vendió el 1 de septiembre de 2026?",
        "¿A qué hora se vende más en una tienda?",
    ]
    assert ejemplos[2].motivo == "las ventas vienen por día, sin hora"


def test_sin_ticket_promedio_declarado_entran_los_siguientes_limites():
    sin_ticket = {
        clave: texto for clave, texto in METRICAS.items() if clave != "ticket_promedio"
    }
    ejemplos = manifiesto.elegir_ejemplos(sin_ticket, CORTE, [])
    assert preguntas(ejemplos) == [
        "¿Cuánto se vendió el 1 de septiembre de 2026?",
        "¿A qué hora se vende más en una tienda?",
        "¿Cuánto falta para llegar a la meta de venta?",
    ]


def test_un_limite_con_jerga_de_los_supuestos_no_se_ofrece_como_ejemplo():
    solo_con_jerga = {
        clave: METRICAS[clave] for clave in ("merma_real", "inventario_historico")
    }
    ejemplos = manifiesto.elegir_ejemplos(solo_con_jerga, CORTE, [])
    # Con D1-A y una fecha ISO en su texto, esos dos motivos no los lee la gerente: solo queda el de la fecha.
    assert preguntas(ejemplos) == ["¿Cuánto se vendió el 1 de septiembre de 2026?"]


@pytest.mark.parametrize(
    "corte",
    [
        dt.date(2026, 8, 31),
        dt.date(2026, 8, 15),
        dt.date(2026, 12, 31),
        dt.date(2028, 2, 28),
    ],
)
def test_la_fecha_del_ejemplo_es_siempre_el_dia_siguiente_al_corte_y_nunca_tiene_datos(
    corte,
):
    ejemplos = manifiesto.elegir_ejemplos({}, corte, [])
    (fecha,) = ejemplos
    siguiente = corte + dt.timedelta(days=1)
    assert (
        str(siguiente.day) in fecha.pregunta and str(siguiente.year) in fecha.pregunta
    )
    assert fecha.motivo.startswith("las ventas llegan al ")


# ------------------------------------------------------------------ qué mensaje es "qué sabes"


@pytest.mark.parametrize(
    "pregunta",
    [
        "qué sabes",
        "Qué sabes",
        "¿Qué sabes?",
        "que sabes",
        "QUE SABES",
        "  qué sabes  ",
        "qué sabes hacer",
        "¿Qué sabes hacer?",
        "q sabes",
        "ke sabes hacer",
        "qué sabes hacer tú",
        "qué puedes hacer",
        "¿Qué puedes hacer?",
        "qué puedes hacer por mí",
        "qué puedes decirme",
        "qué haces",
        "qué cubres",
        "qué datos tienes",
        "qué información tienes",
        "qué información tienes disponible",
        "qué documentos tienes",
        "qué tiendas manejas",
        "en qué me puedes ayudar",
        "con qué me puedes ayudar",
        "cómo me puedes ayudar",
        "qué puedo preguntarte",
        "qué te puedo preguntar",
        "qué puedo preguntar aquí",
        "ayuda",
        "Ayuda!",
        "ayúdame",
        "necesito ayuda",
        "me ayudas",
        "menú",
        "cómo funcionas",
        "cómo te uso",
        "para qué sirves",
        "quién eres",
        "qué eres",
        "hola, qué sabes",
        "hola qué puedes hacer",
        "buenos días, ¿en qué me puedes ayudar?",
        "oye, qué sabes hacer",
        "dime qué sabes",
        "qué sabes por favor",
        "ayuda por favor",
        "qué sabes, gracias",
    ],
)
def test_estos_mensajes_piden_la_cobertura(pregunta):
    assert manifiesto.pide_la_cobertura(pregunta), pregunta


@pytest.mark.parametrize(
    "pregunta",
    [
        "",
        "   ",
        "?",
        "qué sabes de las ventas de cumbres en julio?",
        "qué sabes de cumbres",
        "qué sabes de los tickets abiertos",
        "dime todo lo que sabes de anáhuac",
        "hola, cuánto vendió cumbres en julio",
        "hola cuántos tickets siguen sin cerrar",
        "buenos días, cuántos tickets siguen sin cerrar",
        "ayuda con el inventario de valle norte",
        "ayúdame a ver los tickets abiertos",
        "necesito ayuda para entender el reporte de ventas",
        "qué datos tienes de anáhuac en julio",
        "qué documentos tienes sobre devoluciones",
        "qué puedes decirme de los tickets abiertos",
        "qué puedes hacer con el yogurt que caduca el viernes",
        "qué tal estuvo agosto en cumbres",
        "qué haces con los lotes que caducan",
        "quién eres para decirme eso",
        "en qué tiendas hay tickets abiertos",
        "cuánto vendimos en total",
        "cómo funciona la devolución de un producto",
        "para qué sirve el punto de reorden",
        "hola " * 30,
        "qué sabes " * 30,
        # Un saludo solo sigue yendo al modelo (ver pide_la_cobertura): dos pruebas de T4.1 dependen de ello.
        "hola",
        "Hola!",
        "buenos días",
        "buenas tardes",
        "buenas",
        "qué tal",
        "hey",
    ],
)
def test_estos_mensajes_son_de_datos_y_van_al_modelo(pregunta):
    assert not manifiesto.pide_la_cobertura(pregunta), pregunta


def test_un_mensaje_enorme_no_es_de_cobertura_aunque_termine_en_que_sabes():
    # El largo máximo de K19 y la forma del peor caso de una expresión regular: se descarta sin recorrerlo.
    enorme = "hola " * 200 + "qué sabes"
    assert manifiesto.pide_la_cobertura(enorme) is False


# ------------------------------------------------------------------ el turno sin modelo y su línea del registro


def test_el_turno_de_que_sabes_deja_una_linea_con_cero_llamadas_y_cero_tokens(
    base, cobertura, archivo, monkeypatch
):
    monkeypatch.setattr(uuid, "uuid4", lambda: UUID_FIJO)
    historial = Historial()
    historial.id_sesion = ID_SESION

    respuesta = responder("qué sabes", base, archivo, historial)

    assert respuesta.estado == "ok"
    assert respuesta.texto == manifiesto.texto(cobertura)
    assert respuesta.turno_id == str(UUID_FIJO)
    assert respuesta.evidencias == () and respuesta.mensajes == ()
    assert respuesta.traza.llamadas == ()
    assert (respuesta.traza.entrada_tokens, respuesta.traza.salida_tokens) == (0, 0)
    assert manifiesto.es_del_manifiesto(respuesta)

    (linea,) = registro.leer(archivo)
    assert registro.buscar_turno(archivo, respuesta.turno_id) == linea
    assert linea["tipo"] == "turno" and linea["turno_id"] == respuesta.turno_id
    assert linea["gen_ai.conversation.id"] == ID_SESION
    assert linea["origen"] == "interfaz" and linea["categoria"] is None
    assert linea["pregunta"] == "qué sabes" and linea["respuesta"] == respuesta.texto
    assert linea["estado"] == "ok" and linea["tope"] is None and linea["falla"] is None
    # El criterio de aceptación: 0 tokens en ese turno, en cada campo de uso y en el total.
    assert linea["n_llamadas"] == 0 and linea["llamadas"] == []
    for campo in CAMPOS_DE_USO:
        assert linea[campo] == 0, campo
    assert linea["costo_usd"] == 0
    assert linea["n_herramientas"] == 0 and linea["herramientas"] == []
    assert linea["reintentos"] == [] and linea["espera_reintentos_segundos"] == 0
    assert linea["historial"]["n_mensajes"] == 0
    # Se puede reconocer como un turno sin modelo y ubicar en la versión de los datos que lo respondió.
    assert linea["gen_ai.request.model"] == manifiesto.MODELO
    assert linea["hash_fuentes"] == cobertura.hash_fuentes
    assert linea["latencia_ms"] >= 0


def test_la_linea_pasa_json_loads_y_trae_la_hora_con_desfase_numerico(base, archivo):
    responder("qué sabes", base, archivo)
    responder("ayuda", base, archivo)

    lineas = archivo.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2
    for texto in lineas:
        linea = json.loads(texto)
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", linea["hora"]
        )
        assert linea["hoy_real"] == HOY.isoformat()


def test_el_turno_no_llama_al_modelo_ni_a_responder_ni_lee_el_entorno(
    base, archivo, monkeypatch
):
    def no_debe_correr(*argumentos, **opciones):
        raise AssertionError("el turno de cobertura llamó al modelo")

    monkeypatch.setattr(agente.AGENTE, "run_sync", no_debe_correr)
    monkeypatch.setattr(agente, "responder", no_debe_correr)
    # Es la función que lee .env con load_dotenv(): la cobertura no necesita saber qué modelo hay configurado.
    monkeypatch.setattr(agente, "_modelo_configurado", no_debe_correr)
    monkeypatch.setattr(modelos_de_pydantic, "ALLOW_MODEL_REQUESTS", False)

    respuesta = responder("qué sabes", base, archivo)

    assert respuesta.estado == "ok"


def test_responder_no_guarda_la_vuelta_en_el_historial_del_modelo(base):
    historial = Historial()

    responder("qué sabes", base, historial=historial)

    assert len(historial) == 0 and historial.mensajes() == ()


def test_sin_archivo_de_registro_no_escribe_nada(base, tmp_path):
    respuesta = responder("qué sabes", base)

    assert respuesta.estado == "ok"
    assert not (tmp_path / "logs").exists()


def test_cada_turno_lleva_su_propio_id_de_turno_uuid_v4(base):
    ids = {responder("qué sabes", base).turno_id for _ in range(3)}

    assert len(ids) == 3
    for turno_id in ids:
        assert uuid.UUID(turno_id).version == 4


def test_con_logs_que_no_se_puede_escribir_el_turno_se_responde_igual(base, tmp_path):
    # K27: logs es un archivo y no una carpeta, así que crear logs/turnos.jsonl falla.
    (tmp_path / "logs").write_text(
        "esto es un archivo, no una carpeta", encoding="utf-8"
    )

    respuesta = responder("qué sabes", base, tmp_path / "logs" / "turnos.jsonl")

    assert respuesta.estado == "ok" and "Tiendas (4)" in respuesta.texto


def test_el_origen_y_la_categoria_del_turno_llegan_a_la_linea(base, archivo):
    responder("qué sabes", base, archivo, origen="evaluacion", categoria="abstencion")

    (linea,) = registro.leer(archivo)
    assert linea["origen"] == "evaluacion" and linea["categoria"] == "abstencion"


def test_sin_un_archivo_de_datos_dice_que_no_pudo_consultar_sin_cifra_ni_rutas(
    fts, tmp_path, copia_datos, archivo
):
    (copia_datos / "tickets_mesa_servicio.csv").unlink()

    respuesta = responder(
        "qué sabes", tmp_path / "otra.duckdb", archivo, dir_datos=copia_datos
    )

    assert respuesta.estado == "fuente_no_disponible"
    assert respuesta.texto.startswith("No pude consultar tickets_mesa_servicio.csv")
    assert str(tmp_path) not in respuesta.texto and not re.search(
        r"\d", respuesta.texto
    )
    assert respuesta.evidencias == () and manifiesto.es_del_manifiesto(respuesta)
    (linea,) = registro.leer(archivo)
    assert linea["estado"] == "fuente_no_disponible" and linea["hash_fuentes"] is None
    assert linea["n_llamadas"] == 0 and all(
        linea[campo] == 0 for campo in CAMPOS_DE_USO
    )


def test_el_mensaje_sin_fuente_nombra_el_archivo_y_no_la_ruta(copia_datos, tmp_path):
    (copia_datos / "ventas.csv").unlink()
    with pytest.raises(construir.ArchivoFaltante) as error:
        manifiesto.leer(base=tmp_path / "otra.duckdb", dir_datos=copia_datos)

    mensaje = manifiesto.mensaje_sin_fuente(error.value)
    assert (
        mensaje.startswith("No pude consultar ventas.csv")
        and str(tmp_path) not in mensaje
    )


def test_un_turno_de_cobertura_se_distingue_de_uno_con_modelo(base):
    respuesta = responder("qué sabes", base)
    con_modelo = dataclasses.replace(
        respuesta,
        traza=dataclasses.replace(respuesta.traza, modelo="anthropic:claude-haiku-5-5"),
    )

    assert manifiesto.es_del_manifiesto(respuesta) and not manifiesto.es_del_manifiesto(
        con_modelo
    )


def test_leer_cierra_la_conexion_a_la_base_aunque_alguien_conserve_su_referencia(
    base, monkeypatch
):
    # Windows no reemplaza una base abierta, y DuckDB no abre para escribir una que sigue abierta para leer en el mismo
    # proceso: si leer() la dejara abierta, el siguiente asegurar_base() de un cambio de fuentes fallaría. La prueba
    # retiene la Consultas para que no la cierre el recolector de basura al salir de leer(): solo la cierra leer().
    abiertas = []
    abrir = manifiesto.abrir_consultas

    def abrir_y_retener(ruta):
        consultas = abrir(ruta)
        abiertas.append(consultas)
        return consultas

    monkeypatch.setattr(manifiesto, "abrir_consultas", abrir_y_retener)

    manifiesto.leer(base=base)

    (consultas,) = abiertas
    with pytest.raises(duckdb.ConnectionException):
        consultas.sesion()
    duckdb.connect(str(base)).close()
