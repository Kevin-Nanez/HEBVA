"""El conteo de eval/medir_p12.py: qué trae una respuesta a P12 (T5.8). Sin modelo y sin red."""

from eval import medir_p12


def test_cuenta_el_retiro_y_el_rango_cuando_estan():
    texto = "8 de cada 10 veces alcanzaría entre 17.72 y 24.69 días; retiro el 10 de septiembre."
    assert medir_p12.cuenta(texto) == {"retiro": True, "rango": True}


def test_la_caducidad_del_12_no_cuenta_como_retiro():
    texto = "8 de cada 10 veces alcanzaría entre 17.72 y 24.69 días; caduca el 12 de septiembre."
    assert medir_p12.cuenta(texto) == {"retiro": False, "rango": True}


def test_un_solo_extremo_no_es_el_rango():
    assert medir_p12.cuenta("alcanza 17.72 días")["rango"] is False
    assert medir_p12.cuenta("alcanza 24.69 días")["rango"] is False
