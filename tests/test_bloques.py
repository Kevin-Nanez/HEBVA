"""Los bloques de una respuesta: párrafos, listas y tablas (agente/bloques.py).

El modelo escribe tablas y viñetas en Markdown, y la pantalla las dibuja sin interpretar Markdown (K23). Lo que se prueba
aquí es que ningún dato se pierda ni cambie al partir el texto: lo que no cuadra como tabla o lista sigue siendo texto.
"""

import pytest

from agente import agente
from agente.bloques import Lista, Parrafo, Tabla, bloques_de
from eval import evaluadores

TOLERANCIAS = (
    "| Categoría | Tolerancia | Alerta |\n"
    "|---|---|---|\n"
    "| Lácteos | 1.8 % | 2.8 % |\n"
    "| Frutas y verduras | 4.5 % | 5.5 % |"
)


# ------------------------------------------------------------------ texto sin formato


@pytest.mark.parametrize(
    "texto",
    [
        "17,228,300.26 pesos de venta",
        "Primera línea\n\nSegunda línea\nNota: una nota.",
        "4.5 % de la venta",
        "-5.0 % contra junio",
        "Ventas | Tickets",
        "Una línea con | una barra\n---\nY otra",
        "2026-08-31 es el corte.",
    ],
)
def test_un_texto_sin_tablas_ni_listas_es_un_solo_parrafo_igual_al_original(texto):
    assert bloques_de(texto) == (Parrafo(texto),)


def test_un_texto_vacio_no_da_bloques():
    assert bloques_de("") == ()
    assert bloques_de("\n\n") == ()


# ------------------------------------------------------------------ listas


@pytest.mark.parametrize("marca", ["-", "*", "•"])
def test_cada_marca_de_vineta_da_una_lista_con_la_misma_vineta(marca):
    assert bloques_de(f"{marca} Lácteos 1.8\n{marca} Panadería 3.5") == (Lista(("• Lácteos 1.8", "• Panadería 3.5")),)


def test_una_lista_numerada_conserva_sus_numeros():
    assert bloques_de("1. Primero\n2) Segundo") == (Lista(("1. Primero", "2) Segundo")),)


def test_una_lista_queda_entre_sus_parrafos():
    assert bloques_de("La regla es esta.\n\n- Uno\n- Dos\n\nNota: una nota.") == (
        Parrafo("La regla es esta."),
        Lista(("• Uno", "• Dos")),
        Parrafo("Nota: una nota."),
    )


def test_una_linea_con_sangria_sigue_al_elemento_anterior():
    assert bloques_de("- «Pregunta»\n  Porque no hay datos.\n- Otra") == (
        Lista(("• «Pregunta»\n  Porque no hay datos.", "• Otra")),
    )


# ------------------------------------------------------------------ tablas


def test_una_tabla_de_markdown_da_sus_encabezados_y_sus_filas():
    assert bloques_de(TOLERANCIAS) == (
        Tabla(
            ("Categoría", "Tolerancia", "Alerta"),
            (("Lácteos", "1.8 %", "2.8 %"), ("Frutas y verduras", "4.5 %", "5.5 %")),
        ),
    )


def test_una_tabla_queda_entre_sus_parrafos():
    partes = bloques_de(f"Sin estimado.\n\n{TOLERANCIAS}\n\nNota: una nota.")
    assert [type(parte) for parte in partes] == [Parrafo, Tabla, Parrafo]
    assert partes[0].texto == "Sin estimado."
    assert partes[2].texto == "Nota: una nota."


def test_una_tabla_pegada_al_parrafo_tambien_se_reconoce():
    partes = bloques_de(f"Tolerancias:\n{TOLERANCIAS}\nNota: una nota.")
    assert [type(parte) for parte in partes] == [Parrafo, Tabla, Parrafo]


def test_las_barras_de_las_orillas_son_opcionales_y_el_separador_admite_alineacion():
    assert bloques_de("A | B\n:---|---:\n1 | 2") == (Tabla(("A", "B"), (("1", "2"),)),)


def test_una_barra_escapada_es_parte_de_la_celda():
    assert bloques_de("| A | B |\n|---|---|\n| x \\| y | 2 |") == (Tabla(("A", "B"), (("x | y", "2"),)),)


def test_una_celda_vacia_se_conserva():
    assert bloques_de("| A | B |\n|---|---|\n| 1 |  |") == (Tabla(("A", "B"), (("1", ""),)),)


@pytest.mark.parametrize(
    "texto",
    [
        "| A | B |\n| 1 | 2 |",  # sin separador
        "| A | B |\n|---|---|",  # sin filas
        "| A | B |\n|---|---|---|\n| 1 | 2 |",  # el separador no cuadra con los encabezados
    ],
)
def test_lo_que_no_es_una_tabla_completa_se_queda_como_texto(texto):
    assert bloques_de(texto) == (Parrafo(texto),)


def test_sin_listas_solo_se_parten_las_tablas_y_las_vinetas_quedan_como_las_escribieron():
    """Así parte la pantalla el manifiesto: sus viñetas siguen en un solo texto, con su guion."""
    texto = f"Cortes\n- Ventas: 31 de agosto.\n  Porque sí.\n\nTiendas\n{TOLERANCIAS}\nNota: una nota."
    assert bloques_de(texto, listas=False) == (
        Parrafo("Cortes\n- Ventas: 31 de agosto.\n  Porque sí.\n\nTiendas"),
        bloques_de(TOLERANCIAS)[0],
        Parrafo("Nota: una nota."),
    )


def test_una_fila_que_no_cuadra_termina_la_tabla_y_no_se_pierde():
    partes = bloques_de("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 | 5 |")
    assert partes == (Tabla(("A", "B"), (("1", "2"),)), Parrafo("| 3 | 4 | 5 |"))


@pytest.mark.parametrize(
    "celda, es_cifra",
    [
        ("1.8", True),
        ("4.5 %", True),
        ("1,234.50", True),
        ("$1,234.50", True),
        ("-12.5 %", True),
        ("1,234.50 MXN", True),
        ("", True),
        ("T01", False),
        ("Lácteos", False),
        ("2026-08-31", False),
        ("12 días", False),
    ],
)
def test_una_columna_de_cifras_se_reconoce_por_sus_celdas(celda, es_cifra):
    tabla = Tabla(("Dato",), ((celda,), ("10",)))
    assert tabla.columnas_de_cifras == (es_cifra,)


def test_una_columna_toda_vacia_no_es_de_cifras():
    assert Tabla(("Dato",), (("",),)).columnas_de_cifras == (False,)


# ------------------------------------------------------------------ nada se pierde


def test_ninguna_cifra_ni_palabra_se_pierde_al_partir_el_texto():
    original = f"4.5 % de tolerancia\n\n- Lácteos 1.8\n- Panadería 3.5\n\n{TOLERANCIAS}\n\nNota: 6 tickets."
    partido = "\n".join(parte.texto for parte in bloques_de(original))
    for pieza in ("4.5 % de tolerancia", "Lácteos 1.8", "Panadería 3.5", "Frutas y verduras", "5.5 %", "Nota: 6 tickets."):
        assert pieza in partido


def test_el_markdown_de_una_celda_o_una_vineta_sigue_siendo_texto():
    imagen = "![x](http://ejemplo.test/?q=1)"
    partes = bloques_de(f"- {imagen}\n\n| A |\n|---|\n| <img src=x> {imagen} |")
    assert partes[0].elementos == (f"• {imagen}",)
    assert partes[1].filas == ((f"<img src=x> {imagen}",),)


# ------------------------------------------------------------------ lo que se le pide al modelo y lo que se mide


def test_el_prompt_pide_tabla_o_vinetas_desde_3_elementos_y_ninguna_vineta_en_lo_demas():
    assert "Desde 3 elementos: tabla Markdown o viñetas; si no, sin viñetas." in agente.PROMPT_SISTEMA


def test_c44_cuenta_una_tabla_o_una_lista_como_una_linea():
    con_tabla = f"Cifra\nDefinición.\nFiltros.\n{TOLERANCIAS}\nNota: no cuenta."
    assert len(evaluadores.lineas_de_contexto(con_tabla)) == 3
    con_lista = "Cifra\nDefinición.\n- uno\n- dos\n- tres\n- cuatro\n- cinco"
    assert len(evaluadores.lineas_de_contexto(con_lista)) == 2


def test_c44_sigue_reprobando_si_ademas_de_la_tabla_hay_cuatro_lineas():
    texto = f"Cifra\nuno\ndos\ntres\n{TOLERANCIAS}"
    assert len(evaluadores.lineas_de_contexto(texto)) == 4
