"""Tablas y viñetas en la pantalla, sin abrir nada a K23.

Las viñetas van en st.text. Una tabla no va en st.table, que interpreta Markdown, ni en st.dataframe: dentro del cuerpo de
la respuesta atraparía el Tab antes del botón "Respuesta incorrecta". Va en un componente propio que recibe las celdas como
datos y las escribe con textContent, de modo que ninguna se interpreta. Que el navegador no pida la imagen de una celda hostil
se comprobó a mano y no es de esta suite.

Los fixtures (banco, base, fts) y las ayudas son los de tests/test_app.py.
"""

import app
from tests.test_app import (  # noqa: F401 (banco, base y fts son fixtures)
    banco,
    base,
    elementos,
    fts,
    modelo_que_solo_contesta,
    preguntar,
    tablas_de,
    textos_de,
    ultima_respuesta,
)

TABLA = (
    "| Categoría | Tolerancia |\n"
    "|---|---|\n"
    "| Lácteos | 1.8 |\n"
    "| Panadería | 3.5 |"
)
# El verificador acepta las cifras que trae la pregunta (K16): así estas pruebas no dependen de una herramienta.
PREGUNTA = "dame las tolerancias de 1.8 y 3.5"
IMAGEN = "![x](http://ejemplo.test/imagen)"
SCRIPT = "<script>alerta()</script>"


def test_una_tabla_del_modelo_se_dibuja_como_tabla_y_no_como_texto_con_barras(banco):
    banco.modelo = modelo_que_solo_contesta(f"Sin estimado.\n\n{TABLA}\n\nNota: una nota.")
    mensaje = ultima_respuesta(preguntar(banco.abrir(), PREGUNTA))

    textos = textos_de(mensaje)
    assert textos[0] == "Sin estimado."
    assert textos[1] == "Nota: una nota."
    assert not any("|" in texto for texto in textos[:2])
    assert tablas_de(mensaje) == [
        {
            "encabezados": ["Categoría", "Tolerancia"],
            "filas": [["Lácteos", "1.8"], ["Panadería", "3.5"]],
            "columnas_de_cifras": [False, True],
        }
    ]


def test_el_orden_es_texto_tabla_nota_y_despues_la_fuente(banco):
    banco.modelo = modelo_que_solo_contesta(f"Sin estimado.\n\n{TABLA}\n\nNota: una nota.")
    mensaje = ultima_respuesta(preguntar(banco.abrir(), PREGUNTA))

    orden = [elemento.type for elemento in elementos(mensaje)]
    indice_tabla = orden.index("bidi_component")
    assert orden[indice_tabla - 1] == "text"
    # La nota y la fuente ya no van debajo de la tabla: abren el panel, en ese orden.
    assert orden.index("expander") > indice_tabla
    assert textos_de(mensaje)[1] == "Nota: una nota."
    assert textos_de(mensaje)[2].startswith("Fuente:")


def test_las_vinetas_del_modelo_salen_como_texto_con_vineta(banco):
    banco.modelo = modelo_que_solo_contesta("Regla.\n\n- Lácteos 1.8\n- Panadería 3.5")
    mensaje = ultima_respuesta(preguntar(banco.abrir(), PREGUNTA))

    assert textos_de(mensaje)[:3] == ["Regla.", "• Lácteos 1.8", "• Panadería 3.5"]


def test_una_respuesta_sin_formato_sigue_siendo_un_solo_texto(banco):
    texto = "Primera línea.\n\nSegunda línea.\nNota: una nota."
    banco.modelo = modelo_que_solo_contesta(texto)
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "hola"))

    # La nota se va al panel; lo que queda sigue siendo un solo texto.
    assert textos_de(mensaje)[0] == "Primera línea.\n\nSegunda línea."
    assert tablas_de(mensaje) == []


def test_la_misma_tabla_en_dos_turnos_no_tumba_la_pantalla(banco):
    banco.modelo = modelo_que_solo_contesta(f"Cifra.\n\n{TABLA}\n\n- uno\n- dos")
    pantalla = preguntar(banco.abrir(), PREGUNTA)
    pantalla = preguntar(pantalla, f"{PREGUNTA} otra vez")

    assert len(tablas_de(pantalla.main)) == 2


def test_dos_tablas_en_una_respuesta_salen_las_dos(banco):
    banco.modelo = modelo_que_solo_contesta(f"Cifra.\n\n{TABLA}\n\nOtra.\n\n{TABLA}")
    mensaje = ultima_respuesta(preguntar(banco.abrir(), PREGUNTA))

    assert len(tablas_de(mensaje)) == 2


def test_cada_pregunta_de_un_mensaje_trae_su_propia_tabla(banco):
    banco.modelo = modelo_que_solo_contesta(f"Cifra.\n\n{TABLA}")
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "¿Cuánto es 1.8 y 3.5 en junio? ¿Cuánto es 1.8 y 3.5 en julio?"))

    assert len(tablas_de(mensaje)) == 2


def test_una_celda_hostil_llega_como_dato_y_nada_la_interpreta(banco):
    tabla = f"| Dato |\n|---|\n| {IMAGEN} {SCRIPT} |"
    banco.modelo = modelo_que_solo_contesta(f"Cifra.\n\n{tabla}\n\n- {IMAGEN}")
    pantalla = preguntar(banco.abrir(), "dame el dato")

    mensaje = ultima_respuesta(pantalla)
    assert tablas_de(mensaje)[0]["filas"] == [[f"{IMAGEN} {SCRIPT}"]]
    assert f"• {IMAGEN}" in textos_de(mensaje)
    for elemento in elementos(pantalla.main):
        assert elemento.type not in {"markdown", "html", "imgs", "iframe", "table", "dataframe"}, elemento.type


def test_el_componente_escribe_las_celdas_como_texto_y_sin_interpretar():
    assert "textContent" in app.JS_DE_TABLA
    for peligroso in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "src"):
        assert peligroso not in app.JS_DE_TABLA
    assert "<table" in app.HTML_DE_TABLA
    assert "src" not in app.HTML_DE_TABLA


def test_la_pantalla_solo_baja_al_preguntar_y_deja_de_seguir_al_tocarla():
    """Que el panel se quede en su lugar al abrirlo se comprobó a mano en el navegador y no es de esta suite."""
    for parte in ("pointerdown", "keydown", "wheel", "stAppScrollToBottomContainer", "stChatInputTextArea"):
        assert parte in app.JS_DE_LA_PANTALLA
    for peligroso in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert peligroso not in app.JS_DE_LA_PANTALLA
