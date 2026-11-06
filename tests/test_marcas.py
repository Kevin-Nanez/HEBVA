"""Marca de "respuesta incorrecta" y `python -m eval.casos_desde_marcas` (T4.4, K28, C5.3, C7.2).

Acepta: "una marca se convierte en un caso pendiente cuya esperada llena el script de referencia, nunca la respuesta
del sistema".

Esta prueba cubre la línea `marca` del registro, que no rompe a quien lee logs/turnos.jsonl, y el comando que la
convierte en un caso de eval/casos_pendientes.yaml. El botón de la interfaz está en tests/test_app_marca.py. Corre sin
red ni llaves, con el registro y la salida en tmp_path: ninguna prueba escribe en logs/ ni en eval/.

El teléfono y el correo son inventados (un teléfono de 10 dígitos con formato de México y un dominio .test).
"""

import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

from agente import registro
from eval import banco, buscar_privacidad, casos_desde_marcas

RAIZ = Path(__file__).resolve().parent.parent
HORA = dt.datetime(2026, 11, 3, 10, 20, 23, tzinfo=dt.timezone(dt.timedelta(hours=-6)))
HORA_DE_LA_MARCA = "2026-11-03T10:20:23-06:00"
# El centinela de la respuesta del sistema: si aparece en el archivo de casos, la respuesta se copió.
CENTINELA = "ZETA-9,999,999.99-SISTEMA"
PREGUNTA = "¿Cuánto vendió Cumbres en junio?"
# 10 dígitos seguidos dentro de un UUID v4: el detector de teléfonos lo toma por un celular.
TURNO_CON_DIGITOS = "12345678-9012-4abc-8def-0123456789ab"
TELEFONO_INVENTADO = "81 5550 1234"
CORREO_INVENTADO = "persona.inventada@ejemplo.test"


@pytest.fixture
def archivo(tmp_path):
    return tmp_path / "logs" / "turnos.jsonl"


@pytest.fixture
def salida(tmp_path):
    return tmp_path / "casos_pendientes.yaml"


def un_turno(archivo, pregunta=PREGUNTA, turno_id=None):
    """Escribe en el registro, con el escritor de verdad, un turno con una respuesta que trae el centinela."""
    turno_id = turno_id or str(uuid.uuid4())
    linea = {
        "tipo": "turno",
        "turno_id": turno_id,
        "origen": "interfaz",
        "pregunta": pregunta,
        "herramientas": [{"gen_ai.tool.name": "ventas", "gen_ai.tool.call.result": {"cifras": {"venta": CENTINELA}}}],
        "respuesta": f"{CENTINELA} pesos de venta en junio.",
        "estado": "ok",
    }
    assert registro.escribir(archivo, linea)
    return turno_id


def una_marca(archivo, turno_id):
    assert registro.registrar_marca(archivo, turno_id)


def correr(archivo, salida, capsys):
    capsys.readouterr()
    codigo = casos_desde_marcas.main(["--registro", str(archivo), "--salida", str(salida)])
    captura = capsys.readouterr()
    return codigo, captura.out + captura.err


def casos_de(salida):
    return yaml.safe_load(salida.read_text(encoding="utf-8"))["casos"]


# ------------------------------------------------------------------ la línea marca en el registro


def test_la_marca_trae_solo_el_tipo_la_version_el_turno_y_la_hora():
    turno_id = str(uuid.uuid4())
    linea = registro.linea_de_marca(turno_id, HORA)
    assert linea == {"tipo": "marca", "version": registro.VERSION, "turno_id": turno_id, "hora": HORA_DE_LA_MARCA}
    assert registro.TIPO_MARCA == "marca"


def test_registrar_marca_agrega_una_linea_y_el_turno_se_sigue_encontrando_por_su_id(archivo):
    turno_id = un_turno(archivo)
    una_marca(archivo, turno_id)

    lineas = list(registro.leer(archivo))
    assert [linea["tipo"] for linea in lineas] == ["turno", "marca"]
    assert lineas[1]["turno_id"] == turno_id
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", lineas[1]["hora"])
    # La marca comparte el turno_id del turno: buscar_turno devuelve el turno, no la marca.
    assert registro.buscar_turno(archivo, turno_id)["tipo"] == "turno"
    assert registro.buscar_turno(archivo, turno_id)["pregunta"] == PREGUNTA


def test_la_linea_de_la_marca_no_trae_la_pregunta_ni_la_respuesta(archivo):
    turno_id = un_turno(archivo)
    una_marca(archivo, turno_id)

    ultima = archivo.read_text(encoding="utf-8").splitlines()[-1]
    assert PREGUNTA not in ultima and CENTINELA not in ultima
    assert set(json.loads(ultima)) == {"tipo", "version", "turno_id", "hora"}


def test_un_id_de_turno_con_diez_digitos_seguidos_se_guarda_tal_cual_en_la_marca(archivo):
    # Sin esto, el enmascarado cambiaría el id por <TELEFONO_1> y la marca ya no encontraría su turno.
    una_marca(archivo, TURNO_CON_DIGITOS)
    assert next(registro.leer(archivo))["turno_id"] == TURNO_CON_DIGITOS


def test_registrar_marca_no_lanza_y_dice_si_escribio(tmp_path):
    ocupado = tmp_path / "logs"
    ocupado.write_text("", encoding="utf-8")
    assert registro.registrar_marca(ocupado / "turnos.jsonl", str(uuid.uuid4())) is False


# ------------------------------------------------------------------ quien lee el registro ignora la marca


def test_buscar_privacidad_no_cuenta_la_marca_como_un_turno_de_ningun_modelo(tmp_path):
    ruta = tmp_path / "turnos.jsonl"
    turno = {"tipo": "turno", "turno_id": "t1", "gen_ai.request.model": "m1", "pregunta": "hola", "respuesta": "ok"}
    marca = registro.linea_de_marca(str(uuid.uuid4()), HORA)
    ruta.write_text("".join(json.dumps(linea) + "\n" for linea in (turno, marca, turno)), encoding="utf-8")

    cuentas = buscar_privacidad.contar_registro(ruta, buscar_privacidad.buscados_del_ticket())

    assert cuentas.por_modelo == {"m1": 2}
    assert cuentas.lineas_con_marcadores == 0
    assert cuentas.modelos_con_coincidencias == {}
    assert cuentas.ilegibles == 0


# ------------------------------------------------------------------ el comando


def test_una_marca_se_vuelve_un_caso_pendiente_con_la_pregunta_del_turno(archivo, salida, capsys):
    turno_id = un_turno(archivo)
    una_marca(archivo, turno_id)
    hora_de_la_marca = next(linea for linea in registro.leer(archivo) if linea["tipo"] == "marca")["hora"]

    codigo, texto = correr(archivo, salida, capsys)

    assert codigo == 0
    (caso,) = casos_de(salida)
    assert caso["id"] == "MC-01"
    assert caso["turno_origen"] == turno_id
    assert caso["marcado"] == hora_de_la_marca
    assert caso["pregunta"] == PREGUNTA
    assert caso["estado"] == "pendiente"
    assert "1 caso nuevo" in texto


def test_la_esperada_queda_vacia_y_apunta_al_script_de_referencia(archivo, salida, capsys):
    una_marca(archivo, un_turno(archivo))
    correr(archivo, salida, capsys)

    (caso,) = casos_de(salida)
    assert caso["respuesta_esperada"] is None
    assert caso["cifras_esperadas"] == []
    assert "eval/respuestas_esperadas.py" in caso["llenar_con"]
    assert (RAIZ / "eval" / "respuestas_esperadas.py").is_file()


def test_la_respuesta_del_sistema_no_llega_a_la_esperada_ni_a_ningun_campo(archivo, salida, capsys):
    una_marca(archivo, un_turno(archivo))
    correr(archivo, salida, capsys)

    texto = salida.read_text(encoding="utf-8")
    assert CENTINELA not in texto and "pesos de venta" not in texto
    (caso,) = casos_de(salida)
    assert set(caso) == {
        "id", "turno_origen", "marcado", "pregunta", "estado", "respuesta_esperada", "cifras_esperadas", "llenar_con",
    }


def test_repetir_el_comando_no_duplica_los_casos_ni_toca_el_archivo(archivo, salida, capsys):
    una_marca(archivo, un_turno(archivo))
    correr(archivo, salida, capsys)
    antes = salida.read_bytes()

    codigo, texto = correr(archivo, salida, capsys)

    assert codigo == 0
    assert salida.read_bytes() == antes
    assert len(casos_de(salida)) == 1
    assert "0 casos nuevos" in texto


def test_dos_marcas_del_mismo_turno_dan_un_solo_caso(archivo, salida, capsys):
    turno_id = un_turno(archivo)
    una_marca(archivo, turno_id)
    una_marca(archivo, turno_id)
    correr(archivo, salida, capsys)
    assert [caso["turno_origen"] for caso in casos_de(salida)] == [turno_id]


def test_una_marca_nueva_suma_un_caso_y_conserva_lo_que_ya_se_lleno(archivo, salida, capsys):
    primero = un_turno(archivo, "¿Y en julio?")
    una_marca(archivo, primero)
    correr(archivo, salida, capsys)
    casos = yaml.safe_load(salida.read_text(encoding="utf-8"))
    casos["casos"][0].update(estado="lista", respuesta_esperada="Lo que calculó el script.", cifras_esperadas=["1,234.50"])
    salida.write_text(yaml.safe_dump(casos, allow_unicode=True, sort_keys=False), encoding="utf-8")

    segundo = un_turno(archivo, "¿Y en agosto?")
    una_marca(archivo, segundo)
    codigo, texto = correr(archivo, salida, capsys)

    assert codigo == 0
    llenado, nuevo = casos_de(salida)
    assert (llenado["id"], llenado["estado"], llenado["respuesta_esperada"]) == ("MC-01", "lista", "Lo que calculó el script.")
    assert llenado["cifras_esperadas"] == ["1,234.50"]
    assert (nuevo["id"], nuevo["turno_origen"], nuevo["estado"]) == ("MC-02", segundo, "pendiente")
    assert "1 caso nuevo" in texto


def test_una_marca_sin_su_turno_no_da_caso_y_el_comando_lo_dice(archivo, salida, capsys):
    una_marca(archivo, str(uuid.uuid4()))
    codigo, texto = correr(archivo, salida, capsys)

    assert codigo == 0
    assert not salida.exists()
    assert "1 marca sin turno en el registro" in texto


def test_los_casos_siguen_el_orden_de_las_marcas(archivo, salida, capsys):
    a, b = un_turno(archivo, "¿A?"), un_turno(archivo, "¿B?")
    una_marca(archivo, b)
    una_marca(archivo, a)
    correr(archivo, salida, capsys)
    assert [(caso["id"], caso["pregunta"]) for caso in casos_de(salida)] == [("MC-01", "¿B?"), ("MC-02", "¿A?")]


def test_una_linea_cortada_y_las_de_otro_tipo_del_registro_no_detienen_el_comando(archivo, salida, capsys):
    turno_id = un_turno(archivo)
    with open(archivo, "a", encoding="utf-8") as registro_abierto:
        registro_abierto.write('{"tipo": "marca", "turno_id": "cortada\n')
        registro_abierto.write(json.dumps({"tipo": "marca", "turno_id": 7}) + "\n")
        registro_abierto.write(json.dumps({"tipo": "huella"}) + "\n")
    una_marca(archivo, turno_id)
    codigo, _ = correr(archivo, salida, capsys)

    assert codigo == 0
    assert [caso["turno_origen"] for caso in casos_de(salida)] == [turno_id]


def test_sin_un_registro_el_comando_sale_con_1_y_lo_dice_sin_crear_nada(archivo, salida, capsys):
    codigo, texto = correr(archivo, salida, capsys)
    assert codigo == 1
    assert "No hay un registro" in texto
    assert not salida.exists()


def test_el_comando_escribe_en_un_archivo_aparte_y_nunca_en_el_banco(archivo, salida, capsys):
    assert casos_desde_marcas.SALIDA != banco.BANCO
    assert casos_desde_marcas.SALIDA == RAIZ / "eval" / "casos_pendientes.yaml"
    huella = hashlib.sha256(banco.BANCO.read_bytes()).hexdigest()

    una_marca(archivo, un_turno(archivo))
    correr(archivo, salida, capsys)

    assert hashlib.sha256(banco.BANCO.read_bytes()).hexdigest() == huella
    assert salida.name != banco.BANCO.name


def test_el_telefono_y_el_correo_de_la_pregunta_no_llegan_al_archivo_de_casos(archivo, salida, capsys):
    # El registro enmascara al escribir (K26), y el comando copia la pregunta tal como quedó.
    pregunta = f"llámale al {TELEFONO_INVENTADO} o escribe a {CORREO_INVENTADO}, ¿cuánto vendió Cumbres?"
    una_marca(archivo, un_turno(archivo, pregunta))
    correr(archivo, salida, capsys)

    texto = salida.read_text(encoding="utf-8")
    for dato in (TELEFONO_INVENTADO, "5550 1234", CORREO_INVENTADO, "persona.inventada"):
        assert dato not in texto, dato
    (caso,) = casos_de(salida)
    assert "<TELEFONO_1>" in caso["pregunta"] and "<CORREO_1>" in caso["pregunta"]


def test_una_linea_del_registro_sin_enmascarar_tampoco_lleva_el_dato_al_archivo(archivo, salida, capsys):
    # Una línea escrita a mano o por una versión anterior: el comando no se fía de que el registro ya la haya limpiado.
    turno_id = str(uuid.uuid4())
    sin_enmascarar = {"tipo": "turno", "turno_id": turno_id, "pregunta": f"cel {TELEFONO_INVENTADO}, correo {CORREO_INVENTADO}"}
    archivo.parent.mkdir(parents=True)
    archivo.write_text(json.dumps(sin_enmascarar) + "\n", encoding="utf-8")
    una_marca(archivo, turno_id)
    correr(archivo, salida, capsys)

    texto = salida.read_text(encoding="utf-8")
    for dato in (TELEFONO_INVENTADO, "5550 1234", CORREO_INVENTADO):
        assert dato not in texto, dato
    assert "<TELEFONO_1>" in texto and "<CORREO_1>" in texto


def test_el_comando_no_carga_el_agente_ni_a_un_proveedor_de_modelos():
    codigo = (
        "import sys, eval.casos_desde_marcas; "
        "cargados = [m for m in sys.modules if m.split('.')[0] in ('anthropic', 'openai') or m == 'agente.agente'];"
        "print(cargados)"
    )
    resultado = subprocess.run([sys.executable, "-c", codigo], cwd=RAIZ, capture_output=True, text=True, check=True)
    assert resultado.stdout.strip() == "[]"
