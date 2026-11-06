"""Lo que va dentro del panel plegado, los tokens de razonamiento y la marca discreta con comentario.

La respuesta queda limpia: las notas que escribe el modelo, el detalle de cada consulta y las fuentes se leen dentro de
"Ver cómo se obtuvo la respuesta". Se quedan a la vista la nota fija de texto de ticket y el aviso de tickets sospechosos,
que existen para que la gerente sepa que debe revisar algo.

"Respuesta incorrecta" abre un cuadro con un comentario opcional: abrirlo no deja ninguna marca, y el comentario pasa por
el mismo enmascarado que el resto del registro.

Los fixtures (banco, base, fts) y las ayudas son los de tests/test_app.py. El registro va a tmp_path, nunca a logs/.
"""

import json

import pytest
import yaml
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

import app
from agente import agente, registro
from eval import casos_desde_marcas
from tests.test_app import (  # noqa: F401 (banco, base y fts son fixtures)
    P03,
    P06,
    P21,
    CONSULTA_P06,
    banco,
    base,
    elementos,
    fts,
    guion,
    linea_del_registro_con,
    modelo_p03,
    modelo_p06,
    modelo_p21,
    modelo_que_solo_contesta,
    panel_de,
    preguntar,
    redactar_regla,
    respuestas,
    textos_de,
    textos_visibles,
    ultima_respuesta,
)

PANEL = "Ver cómo se obtuvo la respuesta"
ANCLA_DE_LA_MARCA = "Respuesta incorrecta"
BOTON_DE_LA_MARCA = "Marcar como incorrecta"
TELEFONO_INVENTADO = "81 5550 1234"
CORREO_INVENTADO = "persona.inventada@ejemplo.test"
NOTA_1 = "Nota: los tickets se buscaron con la palabra «merma» en título o descripción."
NOTA_2 = "Nota: la tolerancia es una meta operativa y no una autorización para desechar producto."
CON_NOTAS = f"La tolerancia está en la política.\n\n{NOTA_1}\n{NOTA_2}"


@pytest.fixture
def pantalla_con_registro(banco, monkeypatch):
    """El banco de tests/test_app.py, con el registro de la marca en el mismo archivo temporal que el de los turnos."""
    monkeypatch.setattr(registro, "ARCHIVO", banco.registro)
    return banco


def del_tipo(nodo, tipo):
    return [elemento for elemento in elementos(nodo) if elemento.type == tipo]


def cuadro_de_la_marca(nodo):
    (cuadro,) = del_tipo(nodo, "popover")
    return cuadro


def comentario_de(nodo):
    (caja,) = del_tipo(nodo, "text_area")
    return caja


def boton_de_la_marca(nodo):
    (boton,) = [elemento for elemento in del_tipo(nodo, "button") if elemento.label == BOTON_DE_LA_MARCA]
    return boton


def marcas_del_registro(banco):
    return [linea for linea in banco.lineas_del_registro() if linea.get("tipo") == registro.TIPO_MARCA]


def marcar(mensaje, comentario=None):
    if comentario is not None:
        comentario_de(mensaje).set_value(comentario)
    boton_de_la_marca(mensaje).click().run()


# ------------------------------------------------------------------ las fuentes y las notas, dentro del panel


def test_la_fuente_con_sus_filtros_y_su_corte_va_dentro_del_panel_y_no_debajo_de_la_respuesta(banco):
    banco.modelo = modelo_p03()
    mensaje = ultima_respuesta(preguntar(banco.abrir(), P03))

    assert not any(texto.startswith("Fuente:") for texto in textos_visibles(mensaje))
    (fuente,) = [texto for texto in textos_de(panel_de(mensaje)) if texto.startswith("Fuente: ventas.csv (herramienta")]
    assert "tiendas: T01, T02, T03, T04" in fuente
    assert fuente.endswith("Corte: 31 de agosto de 2026.")


def test_las_notas_del_modelo_van_al_panel_y_la_respuesta_queda_limpia(banco):
    banco.modelo = modelo_que_solo_contesta(CON_NOTAS)
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "qué tolerancia hay"))

    assert textos_visibles(mensaje) == ["La tolerancia está en la política."]
    textos = textos_de(panel_de(mensaje))
    assert NOTA_1 in textos and NOTA_2 in textos


def test_lo_que_se_guarda_en_el_registro_y_en_el_historial_sigue_con_las_notas(banco):
    """Mover las notas es de la pantalla: el verificador y el registro ven el texto completo."""
    banco.modelo = modelo_que_solo_contesta(CON_NOTAS)
    preguntar(banco.abrir(), "qué tolerancia hay")

    assert NOTA_1 in linea_del_registro_con(banco, "qué tolerancia hay")["respuesta"]
    (respuesta,) = banco.respuestas
    assert NOTA_2 in respuesta.texto


def test_el_detalle_de_una_consulta_sin_resultado_va_al_panel(banco):
    banco.modelo = modelo_p21()
    mensaje = ultima_respuesta(preguntar(banco.abrir(), P21))

    assert not any(texto.startswith("Sin resultado de la consulta") for texto in textos_visibles(mensaje))
    assert "Sin resultado de la consulta (herramienta ventas; periodo: de junio a agosto; tiendas: guadalupe)." in textos_de(
        panel_de(mensaje)
    )


def test_una_respuesta_sin_herramientas_trae_el_panel_con_lo_que_antes_iba_debajo(banco):
    banco.modelo = modelo_que_solo_contesta("No tengo una herramienta para eso.\nNota: solo contesto de las tiendas.")
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "cuentame un chiste"))

    assert textos_visibles(mensaje) == ["No tengo una herramienta para eso."]
    textos = textos_de(panel_de(mensaje))
    assert app.SIN_CONSULTA in textos
    assert "Nota: solo contesto de las tiendas." in textos


def test_la_respuesta_de_que_sabes_lleva_su_fuente_al_panel(banco):
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "qué sabes"))

    assert app.FUENTE_DEL_MANIFIESTO not in textos_visibles(mensaje)
    assert app.FUENTE_DEL_MANIFIESTO in textos_de(panel_de(mensaje))


def test_el_panel_abre_con_las_notas_sigue_con_las_fuentes_y_cierra_con_los_tokens(banco):
    def redactar(partes):
        return f"{partes[-1].content and 'Listo.'}\n{NOTA_1}"

    banco.modelo = guion([("ventas", {"periodo": "de junio a agosto"})], redactar=redactar)
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "ventas de junio a agosto"))

    textos = textos_de(panel_de(mensaje))
    assert textos[0] == NOTA_1
    assert textos[1].startswith("Fuente: ventas.csv (herramienta ventas")
    assert textos[-1].startswith("Llamadas al modelo:")


def test_con_dos_consultas_cada_fuente_va_debajo_de_su_consulta_y_no_todas_juntas_arriba(banco):
    def redactar(partes):
        return f"Julio y agosto, listos.\n{NOTA_1}"

    banco.modelo = guion([("ventas", {"periodo": "julio"}), ("ventas", {"periodo": "agosto"})], redactar=redactar)
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "ventas de julio y de agosto"))

    textos = textos_de(panel_de(mensaje))
    primera, segunda = textos.index("Consulta 1 de 2"), textos.index("Consulta 2 de 2")
    fuentes = [numero for numero, texto in enumerate(textos) if texto.startswith("Fuente: ventas.csv (herramienta")]
    assert textos[0] == NOTA_1
    assert len(fuentes) == 2
    assert primera < fuentes[0] < segunda < fuentes[1]
    assert "desde: 2026-07-01" in textos[fuentes[0]] and "desde: 2026-08-01" in textos[fuentes[1]]
    assert textos[-1].startswith("Llamadas al modelo:")


def test_una_nota_repetida_sale_una_sola_vez_en_el_panel(banco):
    banco.modelo = modelo_que_solo_contesta(f"Sin cifra.\n{NOTA_1}\n{NOTA_1}")
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "qué tolerancia hay"))

    assert textos_de(panel_de(mensaje)).count(NOTA_1) == 1


def test_cada_pregunta_de_un_mensaje_lleva_sus_notas_a_su_propio_panel(banco):
    textos = iter(["Una respuesta.\nNota: nota de la primera.", "Otra respuesta.\nNota: nota de la segunda."])

    def contesta(mensajes, info):
        return ModelResponse(parts=[TextPart(next(textos))])

    banco.modelo = FunctionModel(contesta, model_name="dos")
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "primera pregunta? segunda pregunta?"))

    primero, segundo = del_tipo(mensaje, "expander")
    assert "Nota: nota de la primera." in textos_de(primero) and "Nota: nota de la primera." not in textos_de(segundo)
    assert "Nota: nota de la segunda." in textos_de(segundo) and "Nota: nota de la segunda." not in textos_de(primero)
    assert not any(texto.startswith("Nota:") for texto in textos_visibles(mensaje))


def test_la_nota_de_texto_de_ticket_se_queda_a_la_vista_porque_avisa_que_hay_algo_por_revisar(banco):
    banco.modelo = guion(
        [("tickets", {"metrica": "detalle", "ticket_id": 1001})],
        redactar=lambda partes: f"Hay varios tickets abiertos.\n{NOTA_1}",
    )
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "dame el detalle de ese ticket"))

    (cuerpo,) = [texto for texto in textos_visibles(mensaje) if texto.startswith("Hay varios")]
    assert agente.NOTA_TEXTO_DE_TICKET in cuerpo
    assert NOTA_1 not in cuerpo
    assert NOTA_1 in textos_de(panel_de(mensaje))


@pytest.mark.parametrize(
    ("texto", "cuerpo", "notas"),
    [
        ("Cifra.", "Cifra.", ()),
        (f"Cifra.\n{NOTA_1}", "Cifra.", (NOTA_1,)),
        (f"Cifra.\n\n{NOTA_1}\n\n{NOTA_2}", "Cifra.", (NOTA_1, NOTA_2)),
        # Un renglón que sigue a una nota sin ser nota no se esconde: no se sabe si es parte de ella.
        (f"Cifra.\n{NOTA_1}\nOtra cosa.", "Cifra.\nOtra cosa.", (NOTA_1,)),
        # Con viñeta o con otra capitalización, como lo escribe el modelo a veces. El panel la muestra sin la viñeta.
        ("Cifra.\n- Nota: una.\n- Lo demás.", "Cifra.\n- Lo demás.", ("Nota: una.",)),
        ("Cifra.\nNOTAS: dos.", "Cifra.", ("NOTAS: dos.",)),
        # La primera línea se queda aunque sea una nota: es la respuesta.
        ("Nota: no hay datos.", "Nota: no hay datos.", ()),
        # Una palabra que empieza igual no es una nota, ni la nota a media línea.
        ("Cifra.\nNotable: subió.", "Cifra.\nNotable: subió.", ()),
        ("Cifra.\nVer la nota: abajo.", "Cifra.\nVer la nota: abajo.", ()),
        # Lo que avisa que hay algo por revisar se queda.
        (f"Cifra.\n{agente.NOTA_TEXTO_DE_TICKET}", f"Cifra.\n{agente.NOTA_TEXTO_DE_TICKET}", ()),
        ("Cifra.\nAviso: el ticket 1402 queda marcado.", "Cifra.\nAviso: el ticket 1402 queda marcado.", ()),
    ],
)
def test_separar_notas(texto, cuerpo, notas):
    assert app.separar_notas(texto) == (cuerpo, notas)


# ------------------------------------------------------------------ los tokens de razonamiento


def modelo_con_razonamiento(razonamiento):
    def contesta(mensajes, info):
        uso = RequestUsage(input_tokens=27748, output_tokens=1378, output_reasoning_tokens=razonamiento)
        return ModelResponse(parts=[TextPart("Sin cifra.")], usage=uso)

    return FunctionModel(contesta, model_name="con-razonamiento")


def test_el_panel_dice_cuantos_tokens_de_la_salida_fueron_de_razonamiento_con_el_dato_del_registro(banco):
    banco.modelo = modelo_con_razonamiento(512)
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "qué tolerancia hay"))

    assert linea_del_registro_con(banco, "qué tolerancia hay")["razonamiento"] == 512
    assert textos_de(panel_de(mensaje))[-1] == (
        "Llamadas al modelo: 1. Tokens de entrada: 27,748. Tokens de salida: 1,378, de ellos 512 de razonamiento."
    )


def test_sin_tokens_de_razonamiento_el_panel_no_los_menciona(banco):
    """Cero puede ser que no razonó o que el proveedor no lo separa: la pantalla no afirma ninguna de las dos."""
    banco.modelo = modelo_con_razonamiento(0)
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "qué tolerancia hay"))

    assert textos_de(panel_de(mensaje))[-1] == "Llamadas al modelo: 1. Tokens de entrada: 27,748. Tokens de salida: 1,378."


# ------------------------------------------------------------------ la marca discreta, con comentario


def test_la_marca_es_un_cuadro_discreto_que_abre_con_un_texto_y_abrirlo_no_deja_marca(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_p03()
    mensaje = ultima_respuesta(preguntar(banco.abrir(), P03))

    cuadro = cuadro_de_la_marca(mensaje)
    assert cuadro.proto.popover.label == ANCLA_DE_LA_MARCA
    assert cuadro.proto.popover.type == "tertiary"
    # Lo que escribe la marca está dentro del cuadro, no a la vista: un clic por curiosidad solo lo abre.
    assert boton_de_la_marca(cuadro).label == BOTON_DE_LA_MARCA
    assert comentario_de(cuadro).value == ""
    assert marcas_del_registro(banco) == []


def test_la_marca_va_antes_del_panel_para_llegar_con_tab_sin_pasar_por_la_tabla(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_p03()
    mensaje = ultima_respuesta(preguntar(banco.abrir(), P03))

    orden = [elemento.type for elemento in elementos(mensaje)]
    assert orden.index("popover") < orden.index("expander")


def test_al_abrir_el_cuadro_el_foco_pasa_al_comentario_y_tab_no_se_queda_en_el_panel():
    """Streamlit dibuja el cuadro al final de la página. Que el foco llegue se comprobó con Enter en el navegador."""
    for parte in ("st-key-reporte-", "stPopoverButton", "stPopoverBody", "textarea", ".focus()"):
        assert parte in app.JS_DE_LA_MARCA
    for peligroso in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", ".value"):
        assert peligroso not in app.JS_DE_LA_MARCA


def test_el_comentario_queda_en_la_linea_de_la_marca_y_la_marca_sin_comentario_no_lo_trae(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    pantalla = preguntar(banco.abrir(), "uno")
    preguntar(pantalla, "dos")
    primera, segunda = banco.respuestas

    marcar(respuestas(pantalla)[0], "  La tienda no es esa.  ")
    marcar(respuestas(pantalla)[1])

    con_comentario, sin_comentario = marcas_del_registro(banco)
    assert con_comentario["turno_id"] == primera.turno_id
    assert con_comentario["comentario"] == "La tienda no es esa."
    assert sin_comentario["turno_id"] == segunda.turno_id
    assert "comentario" not in sin_comentario
    assert app.TEXTO_MARCADA in textos_de(respuestas(pantalla)[0])
    # La respuesta marcada ya no ofrece el cuadro, ni muestra lo que se escribió.
    assert del_tipo(respuestas(pantalla)[0], "popover") == []
    assert "La tienda no es esa." not in textos_de(respuestas(pantalla)[0])


def test_un_comentario_de_puros_espacios_es_una_marca_sin_comentario(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    pantalla = preguntar(banco.abrir(), "uno")

    marcar(ultima_respuesta(pantalla), "   \n  ")

    (marca,) = marcas_del_registro(banco)
    assert "comentario" not in marca


def test_el_comentario_no_llega_al_modelo_ni_a_la_siguiente_pregunta(pantalla_con_registro):
    banco = pantalla_con_registro
    vistos = []

    def contesta(mensajes, info):
        vistos.append(str(mensajes))
        return ModelResponse(parts=[TextPart("Entendido.")])

    banco.modelo = FunctionModel(contesta, model_name="espia")
    pantalla = preguntar(banco.abrir(), "uno")
    marcar(ultima_respuesta(pantalla), "CENTINELA-COMENTARIO")
    assert len(banco.llamadas) == 1

    preguntar(pantalla, "dos")

    assert len(vistos) == 2
    assert not any("CENTINELA-COMENTARIO" in visto for visto in vistos)


def test_el_comentario_no_guarda_telefonos_ni_correos_ni_llaves(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    pantalla = preguntar(banco.abrir(), "uno")

    marcar(ultima_respuesta(pantalla), f"Llámame al {TELEFONO_INVENTADO} o a {CORREO_INVENTADO}, sk-ant-abc123.")

    ultima = banco.registro.read_text(encoding="utf-8").splitlines()[-1]
    assert json.loads(ultima)["tipo"] == registro.TIPO_MARCA
    for dato in (TELEFONO_INVENTADO, "5550 1234", CORREO_INVENTADO, "sk-ant-abc123"):
        assert dato not in ultima, dato


def test_si_el_registro_no_se_puede_escribir_el_comentario_sigue_para_reintentar(
    pantalla_con_registro, monkeypatch, tmp_path
):
    banco = pantalla_con_registro
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    pantalla = preguntar(banco.abrir(), "uno")
    ocupado = tmp_path / "ocupado"
    ocupado.write_text("", encoding="utf-8")
    monkeypatch.setattr(registro, "ARCHIVO", ocupado / "turnos.jsonl")

    marcar(ultima_respuesta(pantalla), "La tienda no es esa.")

    assert app.TEXTO_SIN_GUARDAR in textos_de(ultima_respuesta(pantalla))
    assert comentario_de(ultima_respuesta(pantalla)).value == "La tienda no es esa."
    monkeypatch.setattr(registro, "ARCHIVO", banco.registro)
    marcar(ultima_respuesta(pantalla))
    (marca,) = marcas_del_registro(banco)
    assert marca["comentario"] == "La tienda no es esa."


def test_la_caja_del_comentario_tiene_tope_y_el_registro_lo_respeta_aunque_llegue_mas_largo(pantalla_con_registro):
    banco = pantalla_con_registro
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "uno"))

    assert comentario_de(mensaje).max_chars == registro.MAXIMO_CARACTERES_COMENTARIO
    linea = registro.linea_de_marca("id", registro.ahora(), "x" * (registro.MAXIMO_CARACTERES_COMENTARIO + 50))
    assert len(linea["comentario"]) == registro.MAXIMO_CARACTERES_COMENTARIO


def test_el_caso_pendiente_lleva_el_comentario_enmascarado_y_la_esperada_sigue_vacia(pantalla_con_registro, tmp_path, capsys):
    banco = pantalla_con_registro
    banco.modelo = modelo_que_solo_contesta("Respuesta del sistema: CENTINELA-ZETA.")
    pantalla = preguntar(banco.abrir(), "cuánto vendimos")
    marcar(ultima_respuesta(pantalla), f"La cifra no cuadra, llámame al {TELEFONO_INVENTADO}.")
    salida = tmp_path / "casos_pendientes.yaml"

    codigo = casos_desde_marcas.main(["--registro", str(banco.registro), "--salida", str(salida)])

    assert codigo == 0, capsys.readouterr()
    texto = salida.read_text(encoding="utf-8")
    assert TELEFONO_INVENTADO not in texto and "CENTINELA-ZETA" not in texto
    (caso,) = yaml.safe_load(texto)["casos"]
    assert caso["comentario"].startswith("La cifra no cuadra, llámame al <TELEFONO_1>")
    assert caso["respuesta_esperada"] is None and caso["cifras_esperadas"] == []


def test_el_caso_de_una_marca_sin_comentario_no_trae_ese_campo(pantalla_con_registro, tmp_path, capsys):
    banco = pantalla_con_registro
    banco.modelo = modelo_que_solo_contesta("Entendido.")
    pantalla = preguntar(banco.abrir(), "cuánto vendimos")
    marcar(ultima_respuesta(pantalla))
    salida = tmp_path / "casos_pendientes.yaml"

    assert casos_desde_marcas.main(["--registro", str(banco.registro), "--salida", str(salida)]) == 0, capsys.readouterr()

    (caso,) = yaml.safe_load(salida.read_text(encoding="utf-8"))["casos"]
    assert "comentario" not in caso


# ------------------------------------------------------------------ la fuente de una política, a la vista


def textos_fuera_del_panel(nodo) -> list[str]:
    """Todo lo que se lee sin abrir el panel, también dentro de los contenedores; textos_visibles solo ve los hijos directos."""
    textos = []
    for hijo in nodo.children.values():
        if hijo.type == "text":
            textos.append(hijo.value)
        elif hijo.type != "expander" and hasattr(hijo, "children"):
            textos.extend(textos_fuera_del_panel(hijo))
    return textos


def fuentes_a_la_vista(mensaje) -> list[str]:
    return [texto for texto in textos_fuera_del_panel(mensaje) if texto.startswith("Fuente:")]


def test_la_fuente_de_una_politica_citada_se_lee_sin_abrir_el_panel_y_debajo_de_la_respuesta(banco):
    banco.modelo = modelo_p06()
    mensaje = ultima_respuesta(preguntar(banco.abrir(), P06))

    (fuente,) = fuentes_a_la_vista(mensaje)
    assert "POL-OPS-014 §5, pp. 3-4" in fuente
    textos = textos_fuera_del_panel(mensaje)
    assert textos.index(fuente) > 0
    assert textos[0].startswith("Se retira del anaquel")
    # El panel la sigue trayendo, junto con el fragmento citado.
    assert fuente in textos_de(panel_de(mensaje))


def test_la_fuente_de_una_politica_va_en_un_contenedor_con_el_estilo_gris(banco):
    banco.modelo = modelo_p06()
    mensaje = ultima_respuesta(preguntar(banco.abrir(), P06))

    assert '[class*="st-key-fuente-"]' in app.ESTILOS_DEL_CHAT


def test_una_cita_que_el_modelo_solo_puso_en_su_linea_de_fuente_sigue_a_la_vista(banco):
    def redactar(partes):
        return f"Se retira del anaquel antes de caducar.\nFuente: {redactar_regla(partes).split('(')[1].rstrip(').')}"

    banco.modelo = guion([("buscar_politicas", {"consulta": CONSULTA_P06})], redactar=redactar)
    mensaje = ultima_respuesta(preguntar(banco.abrir(), P06))

    (fuente,) = fuentes_a_la_vista(mensaje)
    assert "POL-OPS-014 §5, pp. 3-4" in fuente
    assert "Fuente: POL-OPS" not in textos_fuera_del_panel(mensaje)[0]


def test_una_respuesta_de_politicas_sin_cita_no_dice_a_la_vista_una_fuente_que_no_uso(banco):
    banco.modelo = guion(
        [("buscar_politicas", {"consulta": CONSULTA_P06})],
        redactar=lambda partes: "No encontré esa regla en las políticas.",
    )
    mensaje = ultima_respuesta(preguntar(banco.abrir(), "cuántos días de vacaciones tengo"))

    assert fuentes_a_la_vista(mensaje) == []
    assert any(texto.startswith("Fuente: fragmentos consultados") for texto in textos_de(panel_de(mensaje)))


def test_las_cifras_siguen_con_su_fuente_solo_en_el_panel(banco):
    banco.modelo = modelo_p03()
    mensaje = ultima_respuesta(preguntar(banco.abrir(), P03))

    assert fuentes_a_la_vista(mensaje) == []
