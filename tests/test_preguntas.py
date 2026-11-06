"""Separar las preguntas de un mensaje (T4.6).

Lo que cubre, sin red ni modelo: un mensaje con una sola pregunta sale tal cual, y uno con varias se separa por sus
signos de cierre, con lo que sobra en las orillas quitado. Cómo se contesta y se muestra cada pregunta está en
tests/test_app.py.
"""

import pytest

from agente import preguntas


def test_se_contestan_hasta_3_preguntas_por_mensaje():
    assert preguntas.MAXIMO_PREGUNTAS == 3


@pytest.mark.parametrize(
    "mensaje",
    [
        "¿Cuánto vendimos en total de junio a agosto?",
        "cuanto vendio cumbres?",
        "cuanto vendio cumbres",
        # Sin signos no se sabe dónde termina una pregunta: el modelo lo recibe entero.
        "cuanto pan de caja tengo en cumbre y cuando se me caduca",
        "cuanto vendemos por internet y cuanto pesa en la venta?",
        "¿Cuánto vendimos en julio? gracias",
        "oye, ¿cuánto vendimos en julio?",
        "¿Cuánto vendimos en julio???",
        "¿Cuánto vendimos en julio?!",
        # Un «?» que no cierra la pregunta: dentro de unas comillas o de una dirección.
        'que dice "¿Procede?" la faq de devoluciones?',
        "que dice «¿Procede?» la faq de devoluciones?",
        "que hago con http://ejemplo.test/a?b=1 en la mesa de servicio?",
        # «¿no?» o «¿verdad?» solos no son otra pregunta.
        "¿Cuánto vendimos en julio? ¿verdad?",
        "¿no? ¿Cuánto vendimos en julio?",
        "",
        "???",
    ],
)
def test_un_mensaje_con_una_sola_pregunta_sale_tal_cual(mensaje):
    assert preguntas.separar(mensaje) == (mensaje,)


@pytest.mark.parametrize(
    "mensaje, esperadas",
    [
        (
            "¿Cuánto vendimos en julio?, y que pasa si ¿un cliente trae una licuadora?",
            ("¿Cuánto vendimos en julio?", "y que pasa si ¿un cliente trae una licuadora?"),
        ),
        ("¿Cuánto vendimos en julio? ¿Y en agosto?", ("¿Cuánto vendimos en julio?", "¿Y en agosto?")),
        ("cuanto vendimos en julio? y cuantos tickets hay?", ("cuanto vendimos en julio?", "y cuantos tickets hay?")),
        ("¿Cuánto vendimos en julio?¿Y en agosto?", ("¿Cuánto vendimos en julio?", "¿Y en agosto?")),
        ("¿Cuánto vendimos en julio?\n¿Y en agosto?", ("¿Cuánto vendimos en julio?", "¿Y en agosto?")),
        ("cuanto vendimos en julio?? y en agosto??", ("cuanto vendimos en julio??", "y en agosto??")),
        ("¿Cuánto vendimos en julio?! ¿Y en agosto?!", ("¿Cuánto vendimos en julio?!", "¿Y en agosto?!")),
        # Lo que viene antes de la primera y después de la última se queda con ellas.
        ("oye, ¿cuánto vendimos en julio? ¿Y en agosto?", ("oye, ¿cuánto vendimos en julio?", "¿Y en agosto?")),
        ("¿Cuánto vendimos en julio? ¿Y en agosto? gracias", ("¿Cuánto vendimos en julio?", "¿Y en agosto? gracias")),
        # Una muletilla se queda con la pregunta de junto.
        ("¿Cuánto vendimos en julio? ¿verdad? ¿Y en agosto?", ("¿Cuánto vendimos en julio? ¿verdad?", "¿Y en agosto?")),
        ("¿no? ¿Cuánto vendimos en julio? ¿Y en agosto?", ("¿no? ¿Cuánto vendimos en julio?", "¿Y en agosto?")),
        (
            "¿Cuánto vendimos en julio? ¿Y en agosto? ¿Cuántos tickets hay?",
            ("¿Cuánto vendimos en julio?", "¿Y en agosto?", "¿Cuántos tickets hay?"),
        ),
    ],
)
def test_un_mensaje_con_varias_preguntas_se_separa_en_orden(mensaje, esperadas):
    assert preguntas.separar(mensaje) == esperadas


def test_separar_no_recorta_en_el_maximo_porque_eso_lo_dice_la_interfaz():
    mensaje = "¿Cuánto vendimos en julio? ¿Y en agosto? ¿Cuántos tickets hay? ¿Cuántos siguen abiertos?"
    assert len(preguntas.separar(mensaje)) == 4
