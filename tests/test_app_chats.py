"""Barra lateral con el historial de chats de la sesión del navegador.

Cada chat tiene su Historial y sus turnos: abrir otro cambia lo que se ve y lo que recibe el modelo como contexto. Los
chats viven en st.session_state, así que dos navegadores no se los comparten (P52) y ninguno sobrevive a un reinicio.
El título sale de la pregunta y se muestra como texto (K23).

Los fixtures (banco, base, fts) y las ayudas son los de tests/test_app.py.
"""

import app
from agente import manifiesto
from tests.test_app import (  # noqa: F401 (banco, base y fts son fixtures)
    banco,
    base,
    bloques_de_la_pantalla_inicial,
    elementos,
    fts,
    modelo_que_solo_contesta,
    preguntar,
    textos_de,
)

PRIMERA = "¿Cuánto vendimos en junio?"
SEGUNDA = "¿Cuántos tickets hay abiertos?"
NUEVO = "Nuevo chat"
ABRIR = "Abrir"
SIN_CHATS = "Aún no hay chats."


def textos_laterales(pantalla) -> list[str]:
    return textos_de(pantalla.sidebar)


def boton_lateral(pantalla, etiqueta: str, posicion: int = 0):
    botones = [elemento for elemento in elementos(pantalla.sidebar) if getattr(elemento, "label", "") == etiqueta]
    return botones[posicion]


def preguntas_en_pantalla(pantalla) -> list[str]:
    return [textos_de(mensaje)[0] for mensaje in pantalla.chat_message if mensaje.name == "user"]


def nuevo_chat(pantalla):
    boton_lateral(pantalla, NUEVO).click().run()
    assert not pantalla.exception, [e.value for e in pantalla.exception]
    return pantalla


def dos_chats(banco):
    banco.modelo = modelo_que_solo_contesta("Respuesta.")
    pantalla = preguntar(banco.abrir(), PRIMERA)
    return preguntar(nuevo_chat(pantalla), SEGUNDA)


def test_sin_preguntas_la_barra_dice_que_no_hay_chats(banco):
    pantalla = banco.abrir()

    assert textos_laterales(pantalla) == [SIN_CHATS]
    assert boton_lateral(pantalla, NUEVO)


def test_el_chat_aparece_en_la_barra_con_su_primera_pregunta_y_el_actual_no_trae_abrir(banco):
    banco.modelo = modelo_que_solo_contesta("Respuesta.")
    pantalla = preguntar(banco.abrir(), PRIMERA)

    assert textos_laterales(pantalla) == [f"{app.MARCA_CHAT_ACTUAL}{PRIMERA}"]
    assert not [e for e in elementos(pantalla.sidebar) if getattr(e, "label", "") == ABRIR]


def test_nuevo_chat_limpia_la_pantalla_y_el_contexto_y_conserva_el_anterior(banco):
    banco.modelo = modelo_que_solo_contesta("Respuesta.")
    pantalla = preguntar(banco.abrir(), PRIMERA)
    anterior = pantalla.session_state["historial"]

    nuevo_chat(pantalla)

    assert preguntas_en_pantalla(pantalla) == []
    assert pantalla.session_state["historial"] is not anterior
    assert len(pantalla.session_state["historial"]) == 0
    assert textos_laterales(pantalla) == [PRIMERA]


def test_nuevo_chat_sobre_un_chat_vacio_no_acumula_chats(banco):
    pantalla = nuevo_chat(nuevo_chat(banco.abrir()))

    assert len(pantalla.session_state["chats"]) == 1


def test_el_mas_reciente_va_primero_y_abrir_vuelve_a_mostrar_el_anterior_con_su_contexto(banco):
    pantalla = dos_chats(banco)
    assert textos_laterales(pantalla) == [f"{app.MARCA_CHAT_ACTUAL}{SEGUNDA}", PRIMERA]
    historial_primero = pantalla.session_state["chats"][0].historial

    boton_lateral(pantalla, ABRIR).click().run()

    assert not pantalla.exception
    assert preguntas_en_pantalla(pantalla) == [PRIMERA]
    assert textos_laterales(pantalla) == [SEGUNDA, f"{app.MARCA_CHAT_ACTUAL}{PRIMERA}"]

    preguntar(pantalla, "¿Y en julio?")
    assert preguntas_en_pantalla(pantalla) == [PRIMERA, "¿Y en julio?"]
    assert banco.llamadas[-1]["historial"] is historial_primero
    assert [c.titulo for c in pantalla.session_state["chats"]] == [PRIMERA, SEGUNDA]


def test_abrir_otro_chat_descarta_el_chat_vacio_en_curso(banco):
    banco.modelo = modelo_que_solo_contesta("Respuesta.")
    pantalla = nuevo_chat(preguntar(banco.abrir(), PRIMERA))

    boton_lateral(pantalla, ABRIR).click().run()

    assert len(pantalla.session_state["chats"]) == 1
    assert preguntas_en_pantalla(pantalla) == [PRIMERA]


def test_dos_navegadores_no_comparten_chats(banco):
    banco.modelo = modelo_que_solo_contesta("Respuesta.")
    preguntar(banco.abrir(), PRIMERA)

    assert textos_laterales(banco.abrir()) == [SIN_CHATS]


def test_el_titulo_junta_los_espacios_y_recorta_el_largo():
    assert app.titulo_de_chat("  ventas \n de   junio ") == "ventas de junio"
    largo = app.titulo_de_chat("x" * 200)
    assert len(largo) == app.LARGO_TITULO_CHAT
    assert largo.endswith("...")


def test_un_titulo_con_markdown_remoto_se_ve_como_texto_en_la_barra(banco):
    banco.modelo = modelo_que_solo_contesta("Respuesta.")
    hostil = "![x](http://ejemplo.test/a)"
    pantalla = preguntar(banco.abrir(), hostil)

    assert textos_laterales(pantalla) == [f"{app.MARCA_CHAT_ACTUAL}{hostil}"]
    for elemento in elementos(pantalla.sidebar):
        if elemento.type != "text":
            assert "ejemplo.test" not in f"{getattr(elemento, 'value', '')} {getattr(elemento, 'label', '')}"


def test_un_chat_nuevo_no_vuelve_a_revisar_los_datos_en_la_misma_sesion(banco):
    """La revisión de la pantalla inicial corre una vez por sesión del navegador; cada pregunta sigue revisando la base."""
    pantalla = nuevo_chat(dos_chats(banco))
    pantalla.run()

    assert len(banco.lecturas) == 1
    assert preguntas_en_pantalla(pantalla) == []


def test_si_la_revision_inicial_fallo_la_siguiente_pantalla_la_intenta_otra_vez(banco, monkeypatch):
    """Una base ocupada al abrir (otra conexión la tiene) no deja el aviso puesto toda la sesión."""
    leer_del_banco = manifiesto.leer
    intentos = []

    def falla_la_primera(**opciones):
        intentos.append(opciones)
        if len(intentos) == 1:
            raise RuntimeError("base ocupada")
        return leer_del_banco(**opciones)

    monkeypatch.setattr(manifiesto, "leer", falla_la_primera)
    pantalla = banco.abrir()
    assert bloques_de_la_pantalla_inicial(pantalla) == [app.SIN_COBERTURA]

    pantalla.run()

    assert len(intentos) == 2
    assert bloques_de_la_pantalla_inicial(pantalla) == []
    pantalla.run()
    assert len(intentos) == 2
