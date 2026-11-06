"""T5.13, D33-A: las notas internas salen del criterio, y cada criterio corregido queda anotado. Sin red y sin llaves.

Los criterios se leen del banco por su id: ninguno se escribe aquí (G11).
"""

import re
import subprocess

import pytest

from eval import banco, juez

CAMBIOS = banco.RAIZ / "eval" / "cambios_criterios.md"
# El último commit con el banco de antes de T5.13: «antes» en el registro es lo que dice ese commit.
COMMIT_DE_ANTES = "b1b49c1"

# Las anotaciones que leyó el juez como si fueran requisitos: decisiones, tareas, controles y referencias a la rúbrica.
NOTA_INTERNA = re.compile(r"\bD\d+-[A-D]\b|\bcontradicción \d|\bT\d+\.\d+\b|\bC\d+\.\d+\b|\bSi D\d\b|\bcomo en P\d+\b|\blee del CSV\b")
CON_NOTAS = {
    "PT-03", "PT-04", "PT-05", "PT-06", "PT-07", "PT-08", "PT-15", "PT-19", "PT-25", "PT-34",
    "PT-40", "PT-43", "PT-49", "CV-05-01", "CV-05-12", "TN-01", "TN-02", "TN-03", "TN-04", "TN-05",
    "N-09", "DN-10",
}  # fmt: skip
AMBIGUOS = {
    "N-09", "DN-10", "PT-20", "PT-51", "N-11", "N-23", "N-20", "N-21", "N-22", "PT-16", "PT-39",
    "CV-05-01", "CV-05-12", "PT-29", "PT-04", "PT-27", "PT-47", "PT-30",
}  # fmt: skip
SIN_CAMBIO = {"PT-12", "DT-03"}
SENTIDOS = {"nota", "afloja", "endurece"}


def _criterios():
    """Cada criterio del banco con su id: de cada pregunta y, en un compuesto, el suyo. Una parte heredada ya viene en su sencilla."""
    for prompt in banco.leer_banco():
        if prompt.criterio is not None:
            yield prompt.id, prompt.criterio, prompt.notas
        for paso in prompt.pasos:
            for pregunta in paso.preguntas:
                if "." not in pregunta.id:
                    yield pregunta.id, pregunta.criterio, pregunta.notas


@pytest.fixture(scope="module")
def por_id():
    return {identificador: (criterio, notas) for identificador, criterio, notas in _criterios()}


def _filas_de_cambios():
    """id, antes, después, sentido y motivo de cada fila de la tabla de cambios."""
    filas = {}
    for renglon in CAMBIOS.read_text(encoding="utf-8").splitlines():
        celdas = [celda.strip() for celda in renglon.strip().strip("|").split("|")]
        if len(celdas) == 5 and celdas[0] in _ids_del_banco():
            filas.setdefault(celdas[0], []).append(celdas[1:])
    return filas


def _ids_del_banco():
    return {identificador for identificador, _, _ in _criterios()}


def _filas_de_referencias():
    """id, antes y después de cada fila de la tabla de referencias alineadas."""
    filas = {}
    for renglon in CAMBIOS.read_text(encoding="utf-8").splitlines():
        celdas = [celda.strip() for celda in renglon.strip().strip("|").split("|")]
        if len(celdas) == 4 and celdas[0].endswith(" (referencia)"):
            filas[celdas[0].removesuffix(" (referencia)")] = celdas[1:3]
    return filas


def _referencias(prompts):
    """La referencia de cada sencilla y de cada turno: una parte de un compuesto la hereda de su sencilla."""
    return {
        pregunta.id: pregunta.respuesta_esperada
        for prompt in prompts
        for paso in prompt.pasos
        for pregunta in paso.preguntas
        if "." not in pregunta.id
    }


def _criterios_de(prompts):
    por_id = {}
    for prompt in prompts:
        if prompt.criterio is not None:
            por_id[prompt.id] = prompt.criterio
        for paso in prompt.pasos:
            for pregunta in paso.preguntas:
                if "." not in pregunta.id:
                    por_id[pregunta.id] = pregunta.criterio
    return por_id


@pytest.fixture(scope="module")
def banco_de_antes(tmp_path_factory):
    """Los prompts del commit de antes de T5.13. Sin el historial de git no hay con qué comparar."""
    salida = subprocess.run(
        ["git", "show", f"{COMMIT_DE_ANTES}:eval/preguntas.yaml"], cwd=banco.RAIZ, capture_output=True, check=False
    )
    if salida.returncode != 0:
        pytest.skip(f"no está el commit {COMMIT_DE_ANTES}")
    ruta = tmp_path_factory.mktemp("banco_de_antes") / "preguntas.yaml"
    ruta.write_bytes(salida.stdout)
    return banco.leer_banco(ruta)


def test_ningun_criterio_trae_notas_internas(por_id):
    con_nota = sorted(identificador for identificador, (criterio, _) in por_id.items() if NOTA_INTERNA.search(criterio))
    assert not con_nota


def test_las_notas_quedan_en_su_campo(por_id):
    sin_campo = sorted(identificador for identificador in CON_NOTAS if not por_id[identificador][1])
    assert not sin_campo


def test_una_nota_no_repite_el_criterio_ni_la_referencia():
    for prompt in banco.leer_banco():
        for paso in prompt.pasos:
            for pregunta in paso.preguntas:
                if pregunta.notas:
                    assert pregunta.notas not in pregunta.criterio, pregunta.id
                    assert pregunta.notas not in pregunta.respuesta_esperada, pregunta.id


def test_el_juez_no_recibe_las_notas():
    """Lo que recibe el juez es CasoDeJuicio: no tiene campo para ellas, y su mensaje no las trae."""
    assert "notas" not in juez.CasoDeJuicio.__dataclass_fields__
    for prompt in banco.leer_banco():
        for paso in prompt.pasos:
            for pregunta in paso.preguntas:
                caso = juez.CasoDeJuicio(paso.texto, pregunta.criterio, pregunta.respuesta_esperada, "de ensayo")
                if pregunta.notas:
                    assert pregunta.notas not in juez.armar_mensaje(caso), pregunta.id


def test_las_notas_de_una_sencilla_llegan_a_sus_partes():
    partes = {pregunta.id: pregunta for prompt in banco.leer_banco() for paso in prompt.pasos for pregunta in paso.preguntas}
    assert partes["DN-10.N-09"].notas == partes["N-09"].notas


def test_cada_criterio_corregido_esta_en_el_registro():
    filas = _filas_de_cambios()
    faltan = sorted((CON_NOTAS | AMBIGUOS) - set(filas))
    assert not faltan


def test_el_registro_coincide_con_el_banco(por_id):
    """Una fila por cambio: la última de cada id trae el criterio que hoy dice el banco."""
    for identificador, cambios in _filas_de_cambios().items():
        assert cambios[-1][1] == por_id[identificador][0], identificador
        for antes, despues, sentido, motivo in cambios:
            assert antes != despues, identificador
            assert sentido in SENTIDOS, identificador
            assert motivo, identificador


def test_el_antes_del_registro_es_lo_que_decia_el_commit_anterior(banco_de_antes):
    de_antes = _criterios_de(banco_de_antes)
    for identificador, cambios in _filas_de_cambios().items():
        assert cambios[0][0] == de_antes[identificador], identificador


def test_todo_criterio_que_cambio_esta_en_el_registro(banco_de_antes):
    """Un cambio sin su fila es justo lo que D33-A no permite: mover un criterio después de ver los resultados sin decirlo."""
    de_antes, de_hoy = _criterios_de(banco_de_antes), _criterios_de(banco.leer_banco())
    cambiaron = {identificador for identificador, criterio in de_hoy.items() if de_antes.get(identificador) != criterio}
    assert cambiaron == set(_filas_de_cambios())


def test_toda_referencia_que_cambio_esta_en_el_registro(banco_de_antes):
    de_antes, de_hoy = _referencias(banco_de_antes), _referencias(banco.leer_banco())
    filas = _filas_de_referencias()
    assert {identificador for identificador, texto in de_hoy.items() if de_antes.get(identificador) != texto} == set(filas)
    for identificador, (antes, despues) in filas.items():
        assert antes == de_antes[identificador], identificador
        assert despues == de_hoy[identificador], identificador


def _saldo_escrito():
    """Lo que dice la sección del saldo: sentido, cuántos y sus id."""
    frases = {"se aflojan": "afloja", "se endurecen": "endurece", "solo mueven una nota": "nota"}
    renglones = re.findall(
        r"^- (\d+) (se aflojan|se endurecen|solo mueven una nota)[^:]*: ([^.]*)\.", CAMBIOS.read_text(encoding="utf-8"), re.MULTILINE
    )
    return {frases[frase]: (int(cantidad), {identificador.strip() for identificador in lista.split(",")}) for cantidad, frase, lista in renglones}


def test_el_saldo_cuadra_con_la_tabla_de_cambios():
    por_sentido = {}
    for identificador, cambios in _filas_de_cambios().items():
        por_sentido.setdefault(cambios[-1][2], set()).add(identificador)
    saldo = _saldo_escrito()
    assert set(saldo) == SENTIDOS
    for sentido, (cantidad, identificadores) in saldo.items():
        assert identificadores == por_sentido[sentido], sentido
        assert cantidad == len(identificadores), sentido


def test_el_total_del_registro_es_la_suma_del_saldo():
    total = re.search(r"Con estos (\d+) cambios", CAMBIOS.read_text(encoding="utf-8"))
    assert int(total.group(1)) == sum(cantidad for cantidad, _ in _saldo_escrito().values())
    assert int(total.group(1)) == sum(len(cambios) for cambios in _filas_de_cambios().values())


def test_la_prediccion_cuadra_con_sus_filas():
    """Base, lo que resta cada criterio y el total: editar una sola fila ya no pasa."""
    filas = []
    for renglon in CAMBIOS.read_text(encoding="utf-8").splitlines():
        celdas = [celda.strip().strip("*") for celda in renglon.strip().strip("|").split("|")]
        if len(celdas) == 3 and re.fullmatch(r"-?\d+", celdas[1]) and re.fullmatch(r"-?\d+", celdas[2]):
            filas.append((int(celdas[1]), int(celdas[2])))
    assert len(filas) >= 3
    base, *restas, total = filas
    assert (base[0] + sum(fila[0] for fila in restas), base[1] + sum(fila[1] for fila in restas)) == total


def test_el_registro_da_la_decision_sobre_los_tres_endurecimientos():
    """D33-A dice que PT-04, PT-27 y PT-29 aclaran si el requisito es de verdad: el registro dice quién lo decidió y que no fue el usuario."""
    seccion = CAMBIOS.read_text(encoding="utf-8").partition("## Decisión sobre tres endurecimientos")[2].partition("\n## ")[0]
    for identificador in ("PT-04", "PT-27", "PT-29"):
        assert identificador in seccion
    assert "antes de validar" in seccion
    assert "No los confirmó el usuario" in seccion


@pytest.mark.parametrize("identificador", sorted(SIN_CAMBIO))
def test_lo_que_corrige_la_herramienta_no_se_afloja(identificador):
    assert identificador not in _filas_de_cambios()


def test_pt12_sigue_pidiendo_identificar_las_devoluciones_por_las_filas_negativas(por_id):
    assert "negativas" in por_id["PT-12"][0]


def test_quitar_una_condicion_reprueba_de_un_compuesto_le_ahorra_su_llamada():
    """DN-10 ya no reprueba nada por su cuenta: sus partes se juzgan, y él no gasta otra llamada."""
    leidos = {prompt.id: prompt for prompt in banco.leer_banco()}
    assert juez._CONDICION_PROPIA not in leidos["DN-10"].criterio


