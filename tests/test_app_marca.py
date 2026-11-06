"""Botón "Respuesta incorrecta" de la interfaz (T4.4, K28, C5.3).

Acepta: una marca se convierte en un caso pendiente cuya esperada llena el script de referencia, nunca la respuesta del
sistema. Aquí se prueba la mitad de la interfaz, con AppTest y el modelo simulado de tests/test_app.py: cada respuesta
trae su botón, pulsarlo agrega al registro una línea `marca` con el id del turno y la hora, sin llamar al modelo, el botón
queda indicado como marcado y un rerun no repite la línea. El comando y la línea están en tests/test_marcas.py. El botón
vive dentro del cuadro que abre "Respuesta incorrecta"; el cuadro y el comentario se prueban en tests/test_app_pie.py.

Los fixtures (banco, base, fts) y las ayudas son los de tests/test_app.py. El registro va a tmp_path, nunca a logs/.
"""

import json
import re

import pytest
import yaml

import app
from agente import agente, registro
from eval import casos_desde_marcas
from tests.test_app import (  # noqa: F401 (banco, base y fts son fixtures)
    MENSAJE_CON_DOS,
    P03,
    banco,
    base,
    elementos,
    fts,
    modelo_de_dos_preguntas,
    modelo_p03,
    modelo_que_solo_contesta,
    preguntar,
    respuestas,
    textos_de,
    todo_el_texto,
    ultima_respuesta,
)

ETIQUETA = "Marcar como incorrecta"
CAMPOS_DE_LA_MARCA = {"tipo", "version", "turno_id", "hora"}
UUID_V4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
TELEFONO_INVENTADO = "81 5550 1234"
CORREO_INVENTADO = "persona.inventada@ejemplo.test"


@pytest.fixture
def pantalla_con_registro(banco, monkeypatch):
    """El banco de tests/test_app.py, con el registro de la marca en el mismo archivo temporal que el de los turnos."""
    monkeypatch.setattr(registro, "ARCHIVO", banco.registro)
    return banco


def botones_de(nodo, etiqueta=ETIQUETA):
    return [elemento for elemento in elementos(nodo) if elemento.type == "button" and elemento.label == etiqueta]


def marcas_del_registro(banco):
    return [linea for linea in banco.lineas_del_registro() if linea.get("tipo") == registro.TIPO_MARCA]


def test_cada_respuesta_trae_su_boton_y_aun_no_hay_ninguna_marca(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    assert len(botones_de(ultima_respuesta(pantalla))) == 1
    assert marcas_del_registro(banco) == []


def test_pulsar_el_boton_agrega_una_linea_marca_con_el_id_del_turno_y_no_llama_al_modelo(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)
    (respuesta,) = banco.respuestas

    botones_de(ultima_respuesta(pantalla))[0].click().run()

    assert not pantalla.exception, [e.value for e in pantalla.exception]
    (marca,) = marcas_del_registro(banco)
    assert set(marca) == CAMPOS_DE_LA_MARCA
    assert marca["turno_id"] == respuesta.turno_id and UUID_V4.fullmatch(marca["turno_id"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", marca["hora"])
    assert len(banco.llamadas) == 1 and banco.sin_modelo == []
    # La línea del turno sigue siendo la única con ese id y tipo "turno".
    assert registro.buscar_turno(banco.registro, respuesta.turno_id)["pregunta"] == P03


def test_la_linea_de_la_marca_no_trae_la_pregunta_ni_la_respuesta(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)
    (respuesta,) = banco.respuestas

    botones_de(ultima_respuesta(pantalla))[0].click().run()

    ultima = banco.registro.read_text(encoding="utf-8").splitlines()[-1]
    assert json.loads(ultima)["tipo"] == "marca"
    assert P03 not in ultima and respuesta.texto not in ultima and "17,228,300" not in ultima


def test_el_boton_queda_indicado_como_marcado_y_un_rerun_no_repite_la_linea(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    botones_de(ultima_respuesta(pantalla))[0].click().run()

    assert botones_de(ultima_respuesta(pantalla)) == []
    assert app.TEXTO_MARCADA in textos_de(ultima_respuesta(pantalla))
    # Un rerun a secas y una pregunta nueva vuelven a dibujar la respuesta marcada y no agregan otra línea.
    pantalla.run()
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    preguntar(pantalla, "gracias")
    assert len(marcas_del_registro(banco)) == 1
    primera, segunda = respuestas(pantalla)
    assert botones_de(primera) == [] and app.TEXTO_MARCADA in textos_de(primera)
    assert len(botones_de(segunda)) == 1 and app.TEXTO_MARCADA not in textos_de(segunda)


def test_cada_respuesta_se_marca_por_separado_con_su_propio_id(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    pantalla = preguntar(banco.abrir(), "uno")
    preguntar(pantalla, "dos")
    primera, segunda = banco.respuestas

    botones_de(respuestas(pantalla)[1])[0].click().run()

    assert [marca["turno_id"] for marca in marcas_del_registro(banco)] == [segunda.turno_id]
    assert len(botones_de(respuestas(pantalla)[0])) == 1
    botones_de(respuestas(pantalla)[0])[0].click().run()
    assert [marca["turno_id"] for marca in marcas_del_registro(banco)] == [segunda.turno_id, primera.turno_id]


def test_un_mensaje_con_dos_preguntas_trae_un_boton_por_pregunta_y_marca_solo_la_que_se_pulsa(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_de_dos_preguntas()
    pantalla = preguntar(banco.abrir(), MENSAJE_CON_DOS)
    primera, segunda = banco.respuestas

    botones = botones_de(ultima_respuesta(pantalla))
    assert len(botones) == 2

    botones[1].click().run()

    assert [marca["turno_id"] for marca in marcas_del_registro(banco)] == [segunda.turno_id]
    assert len(botones_de(ultima_respuesta(pantalla))) == 1
    assert primera.turno_id != segunda.turno_id


def test_si_el_registro_no_se_puede_escribir_la_pantalla_lo_dice_y_el_boton_sigue(pantalla_con_registro, monkeypatch, tmp_path):
    banco = pantalla_con_registro
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)
    ocupado = tmp_path / "ocupado"
    ocupado.write_text("", encoding="utf-8")
    monkeypatch.setattr(registro, "ARCHIVO", ocupado / "turnos.jsonl")

    botones_de(ultima_respuesta(pantalla))[0].click().run()

    assert not pantalla.exception, [e.value for e in pantalla.exception]
    assert app.TEXTO_SIN_GUARDAR in textos_de(ultima_respuesta(pantalla))
    assert len(botones_de(ultima_respuesta(pantalla))) == 1
    assert app.TEXTO_MARCADA not in todo_el_texto(pantalla)
    # Con el registro de vuelta, el mismo botón guarda la marca.
    monkeypatch.setattr(registro, "ARCHIVO", banco.registro)
    botones_de(ultima_respuesta(pantalla))[0].click().run()
    assert len(marcas_del_registro(banco)) == 1
    assert app.TEXTO_MARCADA in textos_de(ultima_respuesta(pantalla))


def test_un_error_inesperado_no_trae_boton_porque_no_hay_turno_que_marcar(pantalla_con_registro, monkeypatch):
    def con_error(*argumentos, **opciones):
        raise RuntimeError("falla")

    monkeypatch.setattr(agente, "responder", con_error)
    pantalla = preguntar(pantalla_con_registro.abrir(), P03)

    assert textos_de(ultima_respuesta(pantalla)) == [app.MENSAJE_INESPERADO]
    assert botones_de(ultima_respuesta(pantalla)) == []


def test_la_respuesta_de_que_sabes_tambien_trae_su_boton(pantalla_con_registro):
    banco = pantalla_con_registro
    pantalla = preguntar(banco.abrir(), "qué sabes")

    assert len(botones_de(ultima_respuesta(pantalla))) == 1
    botones_de(ultima_respuesta(pantalla))[0].click().run()
    (marca,) = marcas_del_registro(banco)
    assert marca["turno_id"] == banco.respuestas_sin_modelo[0].turno_id


def test_el_boton_no_cambia_el_texto_ni_el_orden_de_la_respuesta(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_p03()
    pantalla = preguntar(banco.abrir(), P03)

    contenedores = list(ultima_respuesta(pantalla).children.values())
    hijos = [hijo.type for hijo in contenedores]
    # El texto de la cifra sigue como estaba, la fuente va en el panel, y la marca va antes del panel plegado: un
    # contenedor con el cuadro, y dentro el formulario con la caja del comentario y el botón.
    assert hijos == ["text", "flex_container", "expander"]
    assert [elemento.type for elemento in elementos(contenedores[1])] == ["popover", "form", "text_area", "button"]


def test_de_la_marca_al_caso_pendiente_sin_la_respuesta_del_sistema_y_sin_el_telefono_ni_el_correo(
    pantalla_con_registro, tmp_path, capsys
):
    banco = pantalla_con_registro
    banco.modelo = modelo_que_solo_contesta("Respuesta del sistema: CENTINELA-ZETA.")
    pregunta = f"mi contacto es {TELEFONO_INVENTADO} y {CORREO_INVENTADO}, ¿cuánto vendimos?"
    pantalla = preguntar(banco.abrir(), pregunta)
    (respuesta,) = banco.respuestas
    botones_de(ultima_respuesta(pantalla))[0].click().run()
    salida = tmp_path / "casos_pendientes.yaml"

    codigo = casos_desde_marcas.main(["--registro", str(banco.registro), "--salida", str(salida)])

    assert codigo == 0, capsys.readouterr()
    texto = salida.read_text(encoding="utf-8")
    for dato in (TELEFONO_INVENTADO, "5550 1234", CORREO_INVENTADO, "CENTINELA-ZETA"):
        assert dato not in texto, dato
    (caso,) = yaml.safe_load(texto)["casos"]
    assert caso["turno_origen"] == respuesta.turno_id
    assert "<TELEFONO_1>" in caso["pregunta"] and "<CORREO_1>" in caso["pregunta"]
    assert caso["respuesta_esperada"] is None and caso["estado"] == "pendiente"
