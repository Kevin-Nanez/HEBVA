"""python -m eval.correr --rejuzgar (T5.13, D32-A): juzgar otra vez las respuestas guardadas, sin correr el agente. Con FunctionModel.

Los bancos y los turnos de estas pruebas son inventados, salvo la última, que lee el reporte guardado de la línea base por su
nombre y las preguntas por su id: ninguna se escribe aquí (G11).
"""

import hashlib
import json

import pytest
import yaml
from pydantic_ai import models
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from agente import agente
from eval import correr, juez
from eval.evaluadores import TurnoEvaluado
from tests.conftest import RAIZ as RAIZ_DEL_REPOSITORIO
from tests.conftest import SIN_REPORTES

NOMBRE_DEL_JUEZ = "function:guion"
LINEA_BASE = correr.DIR_REPORTES / "2026-11-02-completo-39d2639.json"


@pytest.fixture(autouse=True)
def sin_llamadas_reales(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture(autouse=True)
def sin_agente(monkeypatch):
    """Si alguien intenta correr el agente, la prueba truena."""

    def no_se_llama(*_, **__):
        raise AssertionError("juzgar otra vez no corre el agente")

    monkeypatch.setattr(agente, "responder", no_se_llama)
    monkeypatch.setattr(correr, "contestar", no_se_llama)
    monkeypatch.setattr(correr, "correr_prompt", no_se_llama)


def _texto_del_usuario(mensajes):
    return "\n".join(str(parte.content) for mensaje in mensajes for parte in mensaje.parts if parte.part_kind == "user-prompt")


def _veredicto(aprueba, motivo="motivo de ensayo"):
    parte = ToolCallPart("final_result", {"motivo": motivo, "aprueba": aprueba})
    return ModelResponse(parts=[parte], usage=RequestUsage(input_tokens=100, output_tokens=10))


class Jueza:
    """Reprueba lo que traiga una marca en la respuesta y anota lo que recibe."""

    def __init__(self, reprobar=(), caido=(), ilegible=()):
        self.reprobar, self.caido, self.ilegible = reprobar, caido, ilegible
        self.recibidos = []
        self.modelo = FunctionModel(self._contestar, model_name="guion")

    def _contestar(self, mensajes, info):
        texto = _texto_del_usuario(mensajes)
        self.recibidos.append(texto)
        respuesta = texto.split("<respuesta>\n", maxsplit=1)[1]
        if any(marca in respuesta for marca in self.caido):
            raise ModelHTTPError(status_code=429, model_name="guion", body="cuota")
        if any(marca in respuesta for marca in self.ilegible):
            sin_aprueba = ToolCallPart("final_result", {"motivo": "motivo sin veredicto"})
            return ModelResponse(parts=[sin_aprueba], usage=RequestUsage(input_tokens=100, output_tokens=10))
        return _veredicto(not any(marca in respuesta for marca in self.reprobar))


@pytest.fixture(scope="module")
def juez_validado(tmp_path_factory):
    """Una carpeta donde el juez de hoy ya está validado para el modelo de estas pruebas."""

    def seguir_la_etiqueta(mensajes, info):
        texto = _texto_del_usuario(mensajes)
        for etiqueta in juez.leer_etiquetas():
            if f"<respuesta>\n{juez._dato(etiqueta.respuesta)}\n</respuesta>" in texto:
                return _veredicto(etiqueta.etiqueta == "aprueba")
        raise AssertionError("una respuesta que no es de las etiquetas")

    carpeta = tmp_path_factory.mktemp("juez")
    modelo = FunctionModel(seguir_la_etiqueta, model_name="guion")
    assert juez.main([], modelo=modelo, dir_reportes=carpeta) == juez.SALIDA_BIEN
    return carpeta


def sencilla(identificador, **campos):
    return {
        "id": identificador,
        "bloque": "normal_sencilla",
        "prompt": f"pregunta de ensayo {identificador}",
        "categoria": "cifra_directa",
        "tipo_respuesta": "cifra",
        "estado_esperado": "ok",
        "respuesta_esperada": "x",
        "cifras_esperadas": [],
        "secciones_esperadas": [],
        "herramientas_esperadas": [],
        "parametros_esperados": {},
        "criterio": "Nombra el conflicto.",
        "negativa": False,
        **campos,
    }


def turno(prompt, corrida, texto):
    datos = TurnoEvaluado(pregunta="x", estado="ok", texto=texto, texto_mostrado=texto, modelo="guion").model_dump(mode="json")
    return {"corrida": corrida, "prompt": prompt, **datos}


def _reporte(*turnos, **meta):
    return {
        "meta": {
            "commit": {"sha": "abc1234abc1234", "sucio": False},
            "alcance": "completo",
            "variantes": False,
            "configuracion": "interfaz",
            "ajustes": {"esfuerzo": "low"},
            "completo": True,
            **meta,
        },
        "turnos": list(turnos),
    }


@pytest.fixture
def escenario(tmp_path):
    """Escribe el banco y el reporte de origen, y devuelve cómo correr el comando sobre ellos."""

    def armar(prompts, turnos, **meta):
        ruta_banco = tmp_path / "banco.yaml"
        ruta_banco.write_text(yaml.safe_dump({"prompts": list(prompts)}, allow_unicode=True), encoding="utf-8")
        ruta_origen = tmp_path / "2026-11-02-completo-abc1234.json"
        ruta_origen.write_text(json.dumps(_reporte(*turnos, **meta)), encoding="utf-8")
        return ruta_banco, ruta_origen

    return armar


def lanzar(ruta_banco, ruta_origen, modelo, dir_juez, tmp_path):
    return correr.main(
        ["--banco", str(ruta_banco), "--rejuzgar", str(ruta_origen)],
        modelo=modelo,
        dir_reportes=tmp_path / "salida",
        dir_juez=dir_juez,
    )


def _dos_corridas():
    return (
        [sencilla("N-1", nucleo=True), sencilla("PT-2", bloque="trampa_sencilla")],
        [turno("N-1", 1, "respuesta buena"), turno("PT-2", 1, "respuesta floja"), turno("N-1", 2, "respuesta buena"), turno("PT-2", 2, "respuesta buena")],
    )


def _salida(tmp_path):
    return json.loads(next((tmp_path / "salida").glob("*.json")).read_text(encoding="utf-8"))


def test_sin_validacion_no_corre_y_lo_dice(escenario, tmp_path, capsys):
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    jueza = Jueza()
    salida = lanzar(ruta_banco, ruta_origen, jueza.modelo, tmp_path / "sin_validacion", tmp_path)
    assert salida == correr.SALIDA_JUEZ_SIN_VALIDAR
    assert not jueza.recibidos
    assert not (tmp_path / "salida").exists()
    assert f"no hay validación para {NOMBRE_DEL_JUEZ}" in capsys.readouterr().err


def test_llama_solo_al_juez_y_da_el_acierto_con_juez_y_con_reglas(escenario, tmp_path, juez_validado):
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    jueza = Jueza(reprobar=("floja",))
    assert lanzar(ruta_banco, ruta_origen, jueza.modelo, juez_validado, tmp_path) == correr.SALIDA_BIEN
    assert len(jueza.recibidos) == 4
    datos = _salida(tmp_path)
    assert [fila["aciertos"] for fila in datos["acierto"]["alcance"]["por_corrida"]] == [1, 2]
    assert [fila["aciertos"] for fila in datos["acierto_reglas"]["alcance"]["por_corrida"]] == [2, 2]
    assert datos["juez"]["usado"] is True


def test_el_reporte_nombra_el_origen_el_commit_del_juez_y_el_costo(escenario, tmp_path, juez_validado, monkeypatch):
    monkeypatch.setattr(correr.registro, "commit", lambda: {"sha": "def5678def5678", "sucio": False})
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    assert lanzar(ruta_banco, ruta_origen, Jueza().modelo, juez_validado, tmp_path) == correr.SALIDA_BIEN
    rejuzgado = _salida(tmp_path)["rejuzgado"]
    assert rejuzgado["reporte_de_origen"] == ruta_origen.name
    assert rejuzgado["commit_de_origen"]["sha"] == "abc1234abc1234"
    assert rejuzgado["commit_del_juez"]["sha"] == "def5678def5678"
    assert rejuzgado["modelo_del_juez"] == NOMBRE_DEL_JUEZ
    assert rejuzgado["juez_prompt_sha256"] == juez.PROMPT_JUEZ_SHA256
    assert rejuzgado["uso_del_juez"]["llamadas"] == 4
    assert rejuzgado["uso_del_juez"]["entrada_tokens"] == 400
    markdown = next((tmp_path / "salida").glob("*.md")).read_text(encoding="utf-8")
    assert ruta_origen.name in markdown
    assert "def5678" in markdown
    assert "no se volvieron a correr" in markdown


def test_escribe_aparte_y_no_toca_el_reporte_de_origen(escenario, tmp_path, juez_validado):
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    antes = hashlib.sha256(ruta_origen.read_bytes()).hexdigest()
    correr.main(["--banco", str(ruta_banco), "--rejuzgar", str(ruta_origen)], modelo=Jueza().modelo, dir_reportes=ruta_origen.parent, dir_juez=juez_validado)
    assert hashlib.sha256(ruta_origen.read_bytes()).hexdigest() == antes
    nuevos = sorted(ruta.name for ruta in ruta_origen.parent.glob("*rejuzgado*"))
    assert len(nuevos) == 2
    assert all(nombre.startswith(f"{ruta_origen.stem}-rejuzgado-") for nombre in nuevos)


def test_las_reglas_se_recalculan_con_el_banco_de_hoy(escenario, tmp_path, juez_validado):
    """Una cifra esperada que el banco de hoy pide y la respuesta guardada no trae reprueba, y por eso no gasta una llamada."""
    prompts = [sencilla("N-1", nucleo=True, cifras_esperadas=["123.45"]), sencilla("PT-2", bloque="trampa_sencilla")]
    turnos = [turno("N-1", 1, "respuesta sin la cifra"), turno("PT-2", 1, "respuesta buena")]
    ruta_banco, ruta_origen = escenario(prompts, turnos)
    jueza = Jueza()
    assert lanzar(ruta_banco, ruta_origen, jueza.modelo, juez_validado, tmp_path) == correr.SALIDA_BIEN
    assert len(jueza.recibidos) == 1
    assert [fila["aciertos"] for fila in _salida(tmp_path)["acierto_reglas"]["alcance"]["por_corrida"]] == [1]


def test_el_juez_lee_el_criterio_de_hoy_y_no_las_notas(escenario, tmp_path, juez_validado):
    prompts = [sencilla("N-1", nucleo=True, criterio="Criterio de hoy.", notas="Nota interna de ensayo.")]
    ruta_banco, ruta_origen = escenario(prompts, [turno("N-1", 1, "respuesta buena")])
    jueza = Jueza()
    assert lanzar(ruta_banco, ruta_origen, jueza.modelo, juez_validado, tmp_path) == correr.SALIDA_BIEN
    assert "Criterio de hoy." in jueza.recibidos[0]
    assert "Nota interna de ensayo." not in jueza.recibidos[0]


def test_una_falla_del_modelo_no_deja_una_cifra_a_medias(escenario, tmp_path, juez_validado, capsys):
    """Sin veredicto no reprueba: la cifra saldría inflada, así que no se escribe y se vuelve a correr."""
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    jueza = Jueza(caido=("floja",))
    assert lanzar(ruta_banco, ruta_origen, jueza.modelo, juez_validado, tmp_path) == correr.SALIDA_SIN_MODELO
    assert not (tmp_path / "salida").exists()
    error = capsys.readouterr().err
    assert "No se escribió" in error
    assert "cuota" not in error


def test_un_veredicto_ilegible_tampoco_deja_una_cifra_inflada(escenario, tmp_path, juez_validado, capsys):
    """Sin aprueba tampoco reprueba: con 2 de 2 en el reporte, PT-2 de la corrida 1 quedaría aprobada sin que nadie la juzgara."""
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    jueza = Jueza(ilegible=("floja",))
    assert lanzar(ruta_banco, ruta_origen, jueza.modelo, juez_validado, tmp_path) == correr.SALIDA_SIN_VEREDICTO
    assert not (tmp_path / "salida").exists()
    error = capsys.readouterr().err
    assert "No se escribió" in error
    assert "PT-2" in error
    assert "corrida 1" in error


def _saldo_del_registro():
    cuenta = {"afloja": 0, "endurece": 0, "nota": 0}
    for renglon in correr.CAMBIOS_DE_CRITERIOS.read_text(encoding="utf-8").splitlines():
        celdas = [celda.strip() for celda in renglon.strip().strip("|").split("|")]
        if len(celdas) == 5 and celdas[3] in cuenta:
            cuenta[celdas[3]] += 1
    return cuenta


def test_el_reporte_da_el_saldo_de_los_criterios_corregidos(escenario, tmp_path, juez_validado):
    """D33-A: cada cambio se anota con su motivo y el reporte da el saldo."""
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    assert lanzar(ruta_banco, ruta_origen, Jueza().modelo, juez_validado, tmp_path) == correr.SALIDA_BIEN
    saldo = _saldo_del_registro()
    assert sum(saldo.values()) > 0
    assert _salida(tmp_path)["rejuzgado"]["saldo_de_criterios"] == saldo
    markdown = next((tmp_path / "salida").glob("*.md")).read_text(encoding="utf-8")
    assert f"{saldo['afloja']} se aflojan, {saldo['endurece']} se endurecen, {saldo['nota']} solo mueven una nota" in markdown
    assert correr.CAMBIOS_DE_CRITERIOS.name in markdown


def test_sin_registro_de_cambios_el_reporte_lo_dice(escenario, tmp_path, juez_validado, monkeypatch):
    monkeypatch.setattr(correr, "CAMBIOS_DE_CRITERIOS", tmp_path / "no_existe.md")
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    assert lanzar(ruta_banco, ruta_origen, Jueza().modelo, juez_validado, tmp_path) == correr.SALIDA_BIEN
    assert _salida(tmp_path)["rejuzgado"]["saldo_de_criterios"] == {}
    assert "sin registro" in next((tmp_path / "salida").glob("*.md")).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "contenido",
    [
        "{no es json",
        json.dumps({"meta": {}}),
        json.dumps(_reporte(completo=False)),
        json.dumps(_reporte()),
        json.dumps(_reporte(turno("X-9", 1, "de un prompt que no está en el banco"))),
        json.dumps(_reporte(turno("N-1", 1, "solo en la corrida 1"), turno("PT-2", 2, "solo en la corrida 2"))),
    ],
    ids=["no_es_json", "sin_turnos", "incompleto", "vacio", "prompt_desconocido", "corridas_disparejas"],
)
def test_un_reporte_que_no_sirve_se_rechaza_sin_llamar_al_modelo(escenario, tmp_path, juez_validado, capsys, contenido):
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    ruta_origen.write_text(contenido, encoding="utf-8")
    jueza = Jueza()
    assert lanzar(ruta_banco, ruta_origen, jueza.modelo, juez_validado, tmp_path) == correr.SALIDA_REPORTE_INVALIDO
    assert not jueza.recibidos
    assert "No se llamó al modelo" in capsys.readouterr().err


def test_un_reporte_que_no_existe_se_rechaza(escenario, tmp_path, juez_validado):
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    ruta_origen.unlink()
    assert lanzar(ruta_banco, ruta_origen, Jueza().modelo, juez_validado, tmp_path) == correr.SALIDA_REPORTE_INVALIDO


@pytest.mark.parametrize("otra", ["--completo", "--variantes", "--reanudar", "--recall-sin-modelo"])
def test_no_se_mezcla_con_las_opciones_de_una_corrida(tmp_path, otra, capsys):
    with pytest.raises(SystemExit) as salida:
        correr.main(["--rejuzgar", str(tmp_path / "x.json"), otra], modelo=Jueza().modelo, dir_reportes=tmp_path / "salida")
    assert salida.value.code == 2
    assert otra in capsys.readouterr().err


def test_anuncia_cuantas_llamadas_va_a_hacer_antes_de_hacerlas(escenario, tmp_path, juez_validado, capsys):
    ruta_banco, ruta_origen = escenario(*_dos_corridas())
    lanzar(ruta_banco, ruta_origen, Jueza().modelo, juez_validado, tmp_path)
    assert "4 llamadas al juez" in capsys.readouterr().out


# ------------------------------------------------------------------ la línea base guardada


@pytest.fixture(scope="module")
def la_linea_base(juez_validado, tmp_path_factory):
    """Las respuestas guardadas con el banco de hoy y un juez que aprueba todo. No se llama a ningún modelo real."""
    if not LINEA_BASE.exists() and not (RAIZ_DEL_REPOSITORIO / "docs").exists():
        pytest.skip(SIN_REPORTES)
    carpeta = tmp_path_factory.mktemp("rejuzgado")
    jueza = Jueza()
    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(models, "ALLOW_MODEL_REQUESTS", False)
        salida = correr.main(["--rejuzgar", str(LINEA_BASE)], modelo=jueza.modelo, dir_reportes=carpeta, dir_juez=juez_validado)
    assert salida == correr.SALIDA_BIEN
    return jueza, json.loads(next(carpeta.glob("*.json")).read_text(encoding="utf-8"))


def test_la_linea_base_da_88_y_88_por_reglas_con_el_banco_corregido(la_linea_base):
    """Ningún cambio de D33-A mueve las reglas: solo Reprueba con una cifra se lee del criterio, y ninguno se tocó."""
    _, datos = la_linea_base
    assert [fila["aciertos"] for fila in datos["acierto_reglas"]["alcance"]["por_corrida"]] == [88, 88]


def test_con_un_juez_que_aprueba_todo_el_acierto_con_juez_es_el_de_reglas(la_linea_base):
    _, datos = la_linea_base
    assert [fila["aciertos"] for fila in datos["acierto"]["alcance"]["por_corrida"]] == [88, 88]


def test_de_la_linea_base_salen_las_llamadas_del_juez_y_ninguna_de_mas(la_linea_base):
    """259 de la corrida guardada menos las 2 de DN-10, que ya no reprueba nada por su cuenta (D33-A)."""
    jueza, datos = la_linea_base
    assert len(jueza.recibidos) == 257
    assert datos["juez"]["juzgadas"] == 257
    assert datos["rejuzgado"]["reporte_de_origen"] == LINEA_BASE.name


def test_los_turnos_del_reporte_son_los_guardados(la_linea_base):
    _, datos = la_linea_base
    origen = json.loads(LINEA_BASE.read_text(encoding="utf-8"))
    assert len(datos["turnos"]) == len(origen["turnos"])
    assert {turno["turno_id"] for turno in datos["turnos"]} == {turno["turno_id"] for turno in origen["turnos"]}
