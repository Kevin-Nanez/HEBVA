"""plantilla_decisiones.md: las cifras que trae salen de un reporte guardado, del banco o de los precios.

La plantilla la escribe a mano su autor. Estas pruebas no juzgan la prosa ni el largo de las secciones: recalculan las
cifras de las secciones 3 y 4 desde eval/reportes/, eval/preguntas.yaml y config/, y comprueban el encabezado, los
títulos de la plantilla original, el correo y la redacción. Sin red y sin llaves.
"""

import collections
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from eval import banco

RAIZ = Path(__file__).resolve().parent.parent
PLANTILLA = RAIZ / "plantilla_decisiones.md"
REPORTES = RAIZ / "eval" / "reportes"
PRECIOS = RAIZ / "config" / "precios.yaml"
SUPUESTOS = RAIZ / "config" / "supuestos.yaml"
# El último set completo con Haiku 5.5 en esfuerzo low: con el que comparan las secciones 3 y 4.
ULTIMO_COMPLETO = REPORTES / "2026-11-05-completo-86c070e.json"
# El set completo con Haiku 5.5 en esfuerzo medium (D8-D, el de la app desde el 2026-11-05): el de la sección 4.
COMPLETO_MEDIUM = REPORTES / "2026-11-05-completo-bfe7da1.json"

TITULOS = (
    "1. Qué construí y por qué en ese orden",
    "2. Qué decidí no hacer",
    "3. Cómo sé que funciona",
    "4. Costo por interacción y cómo lo medí",
    "5. Qué haría con dos semanas más",
)
MODELOS = ("anthropic:claude-haiku-5-5", "google:gemini-3.5-flash-lite")


def _texto() -> str:
    return PLANTILLA.read_text(encoding="utf-8")


def _seccion(numero: int) -> str:
    partes = re.split(r"^## (.+)$", _texto(), flags=re.MULTILINE)
    secciones = dict(zip(partes[1::2], partes[2::2]))
    return secciones[TITULOS[numero - 1]]


def _reporte(ruta: Path = ULTIMO_COMPLETO) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _dolares(valor: Decimal, decimales: int) -> str:
    return f"${valor.quantize(Decimal(1).scaleb(-decimales), ROUND_HALF_UP):f}"


# ------------------------------------------------------------------ forma


def test_el_encabezado_trae_nombre_fecha_repositorio_y_los_modelos_por_id():
    texto = _texto()
    for campo in ("Nombre", "Fecha", "Repositorio", "Proveedor y modelos usados"):
        valor = re.search(rf"^{campo}: *(.*)$", texto, flags=re.MULTILINE)
        assert valor and valor.group(1).strip(), f"{campo} sigue vacío"
    proveedor = re.search(r"^Proveedor y modelos usados: *(.*)$", texto, flags=re.MULTILINE).group(1)
    precios = yaml.safe_load(PRECIOS.read_text(encoding="utf-8"))["modelos"]
    for modelo in MODELOS:
        assert modelo in proveedor
        assert modelo.split(":", 1)[1] in precios
    assert "latest" not in proveedor


def test_las_5_secciones_conservan_su_titulo_y_su_orden():
    titulos = re.findall(r"^## (\d\..+)$", _texto(), flags=re.MULTILINE)
    assert tuple(titulos) == TITULOS


def test_la_seccion_2_trae_cada_descarte():
    for descarte in ("SQL libre", "base vectorial", "LiteLLM", "1 llamada", "OpenTelemetry"):
        assert descarte in _seccion(2), descarte


def test_el_correo_trae_las_tres_preguntas_con_su_respuesta():
    anexo = _texto().split("### Anexo Correo y su respuesta", 1)[1]
    preguntas = [linea for linea in anexo.splitlines() if linea.startswith("- ")]
    assert len(preguntas) == 3
    assert "SIGMA" in preguntas[0] and "POL-OPS-014 §4" in preguntas[0]
    assert "Mi Tienda" in preguntas[1]
    assert "2026-09-01" in preguntas[2]
    supuestos = yaml.safe_load(SUPUESTOS.read_text(encoding="utf-8"))
    assert str(supuestos["fecha_referencia"]) == "2026-09-01"


# ------------------------------------------------------------------ sección 3


def test_la_composicion_del_set_de_la_seccion_3_es_la_del_banco():
    por_bloque = collections.Counter(prompt.bloque for prompt in banco.seleccionar(banco.leer_banco(), completo=True))
    con_trampa = por_bloque["trampa_sencilla"] + por_bloque["trampa_y_normal"] + por_bloque["dos_trampas"]
    normales = por_bloque["normal_sencilla"] + por_bloque["dos_normales"] + por_bloque["conversacion"]
    assert sum(por_bloque.values()) == con_trampa + normales
    assert f"{sum(por_bloque.values())} prompts ({con_trampa} trampa y {normales} normales)" in _seccion(3)


def _aciertos(reporte: dict, clave: str) -> list[int]:
    corridas = reporte[clave]["alcance"]["por_corrida"]
    assert [corrida["n"] for corrida in corridas] == [100] * len(corridas)
    return [corrida["aciertos"] for corrida in corridas]


def test_el_acierto_de_la_seccion_3_es_el_del_set_en_medium_con_reglas_y_con_juez():
    reporte = _reporte(COMPLETO_MEDIUM)
    assert reporte["meta"]["modelos"] == [MODELOS[0]]
    assert reporte["meta"]["ajustes"]["esfuerzo_claude"] == "medium"
    seccion = _seccion(3)
    assert "Haiku 5.5 en esfuerzo medium, que es la configuración de la app" in seccion
    (reglas,), (juez,) = _aciertos(reporte, "acierto_reglas"), _aciertos(reporte, "acierto")
    assert f"{reglas} de 100 respuestas pasaron las reglas y {juez} de 100 pasaron además el juez" in seccion
    # Las que fallan con reglas, las que solo reprueba el juez y lo que el verificador corrigió en los borradores.
    assert f"Ahí fallaron {100 - reglas} respuestas" in seccion
    assert f"reprobó {reglas - juez} respuestas más que las reglas aprobaban" in seccion
    (borrador,) = reporte["sin_evidencia"]["por_corrida"]
    corregidas = sum(1 for turno in reporte["turnos"] if turno["desenlace"] == "corregida")
    assert f"encontró {borrador['cifras']} cifras y {borrador['citas']} citas sin respaldo de una herramienta, y esas {corregidas} respuestas salieron corregidas" in seccion
    assert reporte["fugas"]["total"] == 0


def test_la_seccion_3_compara_con_low_y_con_flash_lite_desde_sus_reportes():
    seccion = _seccion(3)
    low = _reporte()
    assert low["meta"]["ajustes"]["esfuerzo_claude"] == "low"
    (reglas,), (juez,) = _aciertos(low, "acierto_reglas"), _aciertos(low, "acierto")
    assert f"En esfuerzo low el mismo set dio {reglas} con reglas y {juez} con juez" in seccion
    (juez_medium,) = _aciertos(_reporte(COMPLETO_MEDIUM), "acierto")
    assert f"con juez, medium acierta {juez_medium} de 100 y low {juez}." in _seccion(4)
    flash = _reporte(REPORTES / "2026-11-04-completo-9600d3f-flash-lite.json")
    assert flash["meta"]["modelos"] == [MODELOS[1]] and flash["juez"]["usado"] is False
    primera, segunda = _aciertos(flash, "acierto_reglas")
    assert f"dio {primera} y {segunda} de 100 en 2 corridas de una versión anterior, solo con reglas" in seccion


# ------------------------------------------------------------------ sección 4


def test_el_costo_de_la_seccion_4_sale_del_set_en_medium_y_de_los_precios():
    """Solo lo medido: el set en medium, sus percentiles y su caché fría, y la comparación con el de low."""
    seccion = _seccion(4)
    medium, low = _reporte(COMPLETO_MEDIUM), _reporte()
    assert medium["meta"]["modelos"] == [MODELOS[0]]
    assert medium["meta"]["ajustes"]["esfuerzo_claude"] == "medium"
    assert low["meta"]["ajustes"]["esfuerzo_claude"] == "low"
    assert "Haiku 5.5 en esfuerzo medium" in seccion
    costo, turnos = Decimal(medium["uso_total"]["costo_usd"]), medium["uso_total"]["turnos"]
    caliente = costo / turnos
    assert (
        f"una pregunta costó {_dolares(caliente, 5)} en promedio: {_dolares(costo, 6)} entre los {turnos} turnos del set"
    ) in seccion
    eficiencia = medium["eficiencia_total"]
    mas_cara = max(Decimal(str(turno["costo_usd"])) for turno in medium["turnos"])
    assert (
        f"La mitad costó {_dolares(Decimal(str(eficiencia['costo_usd']['mediana'])), 5)} o menos, "
        f"9 de cada 10 costaron {_dolares(Decimal(str(eficiencia['costo_usd']['p90'])), 5)} o menos "
        f"y la más cara {_dolares(mas_cara, 5)}"
    ) in seccion
    latencia = eficiencia["latencia_ms"]
    assert (
        f"la mediana fue de {latencia['p50'] / 1000:.1f} s y 9 de cada 10 tardaron {latencia['p90'] / 1000:.1f} s o menos"
    ) in seccion
    # Caché fría: cada pregunta escribe el prefijo en lugar de leerlo.
    prefijos = {turno["cache_escritura_tokens"] for turno in medium["turnos"] if turno.get("cache_escritura_tokens")}
    assert len(prefijos) == 1
    prefijo = prefijos.pop()
    haiku = yaml.safe_load(PRECIOS.read_text(encoding="utf-8"))["modelos"]["claude-haiku-5-5"]
    escritura, lectura = Decimal(haiku["cache_escritura"]), Decimal(haiku["cache_lectura"])
    extra = prefijo * (escritura - lectura) / 1_000_000
    assert f"lee el prefijo de {prefijo:,} tokens a ${lectura} por millón" in seccion
    assert (
        f"lo escribe a ${escritura} por millón: {_dolares(extra, 5)} más, {_dolares(caliente + extra, 5)} en promedio"
    ) in seccion
    # La comparación con low: mismo set, otro esfuerzo.
    assert low["uso_total"]["turnos"] == turnos
    caliente_low = Decimal(low["uso_total"]["costo_usd"]) / turnos
    mas = (caliente / caliente_low - 1) * 100
    razona = [round(sum(t.get("razonamiento_tokens") or 0 for t in r["turnos"]) / turnos) for r in (medium, low)]
    assert (
        f"En esfuerzo low el mismo set costó {_dolares(caliente_low, 5)} por pregunta con "
        f"{low['eficiencia_total']['latencia_ms']['p50'] / 1000:.1f} s de mediana: medium cuesta "
        f"{mas.quantize(Decimal(1), ROUND_HALF_UP)} % más y razona {razona[0]} tokens por pregunta contra {razona[1]}"
    ) in seccion
    assert COMPLETO_MEDIUM.with_suffix(".md").exists()


# ------------------------------------------------------------------ redacción


def test_no_hay_coma_decimal_raya_ni_emoji():
    texto = _texto()
    assert not re.search(r"\d,\d{1,2}(?!\d)", texto)
    assert "—" not in texto
    assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", texto)


def test_no_cita_nada_que_no_viaje_con_la_entrega():
    """docs/ no entra en el zip, y la rúbrica simulada tampoco."""
    texto = _texto()
    assert "docs/" not in texto
    assert "rúbrica" not in texto.lower()
    assert not re.search(r"\b[CG]\d\.\d\b", texto)
    assert not re.search(r"\bP\d{2}\b", texto)


def test_no_trae_un_telefono_ni_un_correo():
    texto = _texto()
    assert not re.search(r"\d{2,3}[.\- ]\d{3,4}[.\- ]\d{4}", texto)
    assert not re.search(r"[\w.]+@[\w.]+", texto)


def test_no_usa_el_vocabulario_de_relleno():
    patron = (
        r"robust[oa]s?|integral(es)?|holístic[oa]|exhaustiv[oa]|potenci(ar|ando|ado|amos|an)\b|apalanc|sinergi|sin fricci"
        r"|de vanguardia|de punta|innovador|disruptiv|transformador|ecosistema|panorama|paradigma|empoder|desbloque"
        r"|mejores prácticas|valor agregado|a nivel de|cabe (destacar|mencionar|resaltar)|es importante (destacar|señalar|mencionar)"
        r"|vale la pena (destacar|señalar|mencionar)|en resumen|en definitiva|significativamente|drásticamente"
    )
    assert not re.search(patron, _texto(), flags=re.IGNORECASE)
