"""python -m eval.comparacion (T6.3, D7-A, #comparacion en el plan): sin modelo, sin llaves y sin tocar eval/reportes/.

Los reportes se arman a mano con cifras que la prueba conoce, salvo el último bloque, que los saca de eval.correr con turnos
armados para comprobar que el comando lee lo que el reporte escribe de verdad.
"""

import json
from decimal import Decimal

import pytest
import yaml
from pydantic_ai.models.test import TestModel

from eval import comparacion, correr
from eval.evaluadores import ResultadoPrompt
from tests.test_correr import compuesto, sencilla, turno_armado

HAIKU = "anthropic:claude-haiku-5-5"
FLASH_LITE = "google:gemini-3.5-flash-lite"
FLASH = "google:gemini-3.8-flash"
SHA = "a" * 40
OTRO_SHA = "b" * 40
# 11 turnos: el p50 cae en el sexto y el p90 en el décimo, sin interpolar.
LATENCIAS = (1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 7000, 8000, 9000)
ABIERTA = "La elección queda abierta"


def tasas(aciertos, n=30):
    por_corrida = [
        {"corrida": corrida, "aciertos": cuantos, "n": n, "porcentaje": round(100 * cuantos / n, 1)}
        for corrida, cuantos in enumerate(aciertos, start=1)
    ]
    porcentajes = [fila["porcentaje"] for fila in por_corrida]
    return {"por_corrida": por_corrida, "rango": {"minimo": min(porcentajes), "maximo": max(porcentajes), "corridas": len(aciertos)}}


def de_alcance(aciertos, n=30):
    return {"alcance": tasas(aciertos, n), "nucleo": tasas(aciertos, n)}


def medida(p50, p90):
    return {"p50": p50, "p90": p90}


def reporte(
    modelo=HAIKU,
    *,
    reglas=(27,),
    con_juez=None,
    motivo="no hay validación para el modelo",
    costo="0.05",
    latencias=LATENCIAS,
    sha=SHA,
    cambios=None,
    alcance="nucleo",
    variantes=False,
    configuracion="interfaz",
    prompts=("P01", "P02", "P03"),
    por_categoria=None,
    veredictos=(),
    uso_del_juez=None,
    llamadas=23,
    reintentos=0,
    **meta,
):
    """Un reporte con la forma del de eval.correr. Sin juez, `acierto` es igual a las reglas, como en un reporte real."""
    corridas = len(reglas)
    costo_por_turno = None if costo is None else str(Decimal(costo) / len(latencias))
    turnos = [
        {
            "corrida": 1,
            "prompt": prompts[0],
            "estado": "ok",
            "modelo": modelo,
            "latencia_ms": latencia,
            "costo_usd": costo_por_turno,
            "entrada_tokens": 1000 * (i + 1),
            "salida_tokens": 100 * (i + 1),
            "reintentos": 0,
            "espera_reintentos_segundos": 0.0,
        }
        for i, latencia in enumerate(latencias)
    ]
    return {
        "meta": {
            "fecha": "2026-11-03",
            "commit": {"sha": sha, "sucio": bool(cambios)},
            "cambios_sin_commit": cambios,
            "modelos": [modelo],
            "configuracion": configuracion,
            "alcance": alcance,
            "variantes": variantes,
            "n_prompts": 30,
            "corridas_pedidas": corridas,
            "corridas_completas": corridas,
            "completo": True,
            "detenida": None,
            **meta,
        },
        "acierto": de_alcance(con_juez if con_juez is not None else reglas),
        "acierto_reglas": de_alcance(reglas),
        "juez": {
            "usado": con_juez is not None,
            "motivo": "validado para el modelo" if con_juez is not None else motivo,
            "veredictos": list(veredictos),
            "uso": uso_del_juez or {"llamadas": 0, "entrada_tokens": 0, "salida_tokens": 0, "costo_usd": "0"},
        },
        "por_categoria": por_categoria or {"cifra_directa": tasas(reglas), "adversarial": tasas(reglas)},
        "uso_por_categoria": {
            "cifra_directa": {
                "n": len(latencias),
                "entrada_tokens": medida(4800.0, 9000.0),
                "salida_tokens": medida(450.0, 1200.0),
                "costo_usd": None if costo is None else medida(0.0004, 0.001),
                "latencia_ms": medida(4000.0, 8000.0),
            }
        },
        "uso_total": {
            "turnos": len(latencias),
            "entrada_tokens": 1234567,
            "salida_tokens": 66000,
            "n_llamadas": llamadas,
            "reintentos": reintentos,
            "latencia_minutos": 0.8,
            "costo_usd": costo,
            "costo_usd_de_los_que_tienen_precio": costo or "0",
            "turnos_sin_precio": 0 if costo else len(latencias),
        },
        "preguntas": [{"id": prompt} for prompt in prompts],
        "turnos": turnos,
    }


@pytest.fixture
def comparar(tmp_path, capsys):
    """Escribe cada reporte en su .json y corre el comando. Da la salida, el .md (o None) y lo que salió en consola."""
    destino = tmp_path / "comparacion-modelos.md"

    def _comparar(*reportes, ruta_banco=None, previo=None):
        rutas = []
        for i, datos in enumerate(reportes):
            ruta = tmp_path / f"r{i}.json"
            ruta.write_text(json.dumps(datos), encoding="utf-8")
            rutas.append(str(ruta))
        if previo:
            destino.write_text(previo, encoding="utf-8")
        argumentos = [*rutas, *(["--banco", str(ruta_banco)] if ruta_banco else [])]
        salida = comparacion.main(argumentos, salida=destino)
        texto = destino.read_text(encoding="utf-8") if destino.exists() else None
        return salida, texto, capsys.readouterr()

    return _comparar


def renglones_de(texto, nombre):
    return [linea for linea in texto.splitlines() if linea.startswith(f"| {nombre}")]


def banco_de(ruta, *prompts):
    ruta.write_text(yaml.safe_dump({"prompts": list(prompts)}, allow_unicode=True), encoding="utf-8")
    return ruta


# ------------------------------------------------------------------ un solo reporte


def test_un_solo_reporte_compara_lo_que_recibe_y_deja_la_eleccion_abierta(comparar):
    salida, texto, consola = comparar(reporte(HAIKU, reglas=(27,), costo="0.05"))
    assert salida == 0
    assert ABIERTA in texto
    assert ABIERTA in consola.out
    assert "Gemini 3.5 Flash-Lite" in texto and "Gemini 3.8 Flash" in texto
    assert "Elegido" not in texto
    assert "90.0 %" in renglones_de(texto, "Haiku 5.5")[0]


def test_de_los_que_faltan_no_hay_ninguna_cifra(comparar):
    """Ni un renglón en las tablas: la estimación del plan tampoco cuenta como medida."""
    _, texto, _ = comparar(reporte(HAIKU))
    assert not renglones_de(texto, "Gemini")
    faltan = [linea for linea in texto.splitlines() if "Gemini" in linea]
    assert faltan
    assert not any(cifra in linea for linea in faltan for cifra in ("0.0049", "0.0098", "0.69", "1.37", "%"))


def test_dos_de_tres_tampoco_cierran_y_nombran_al_que_falta(comparar):
    salida, texto, _ = comparar(reporte(HAIKU), reporte(FLASH_LITE, costo="0.20"))
    assert salida == 0
    assert ABIERTA in texto
    assert "Elegido" not in texto
    assert not renglones_de(texto, "Gemini 3.8 Flash")
    assert "python -m eval.correr --corridas 1 --modelo google:gemini-3.8-flash" in texto


# ------------------------------------------------------------------ un modelo que no se pudo medir


MOTIVO = "20 solicitudes por día contra unas 74 del núcleo"


def comparar_con_sin_medir(tmp_path, capsys, reportes, sin_medir):
    destino = tmp_path / "comparacion-modelos.md"
    rutas = []
    for i, datos in enumerate(reportes):
        ruta = tmp_path / f"r{i}.json"
        ruta.write_text(json.dumps(datos), encoding="utf-8")
        rutas.append(str(ruta))
    salida = comparacion.main([*rutas, "--sin-medir", sin_medir], salida=destino)
    texto = destino.read_text(encoding="utf-8") if destino.exists() else None
    return salida, texto, capsys.readouterr()


def test_un_modelo_declarado_sin_medir_cierra_la_eleccion_y_dice_que_no_compitio(tmp_path, capsys):
    salida, texto, consola = comparar_con_sin_medir(
        tmp_path,
        capsys,
        [reporte(HAIKU, reglas=(27,), costo="0.05"), reporte(FLASH_LITE, reglas=(20,), costo="0.20")],
        f"google:gemini-3.8-flash={MOTIVO}",
    )
    assert salida == 0
    assert ABIERTA not in texto
    assert "Elegido: Haiku 5.5" in texto
    assert "Sin medir" in texto and "Gemini 3.8 Flash" in texto and MOTIVO in texto
    assert "no compitió" in texto
    assert not renglones_de(texto, "Gemini 3.8 Flash")
    assert "--modelo google:gemini-3.8-flash" not in texto
    assert MOTIVO in consola.out


def test_declarar_sin_medir_a_todos_los_rivales_no_deja_que_gane_solo_el_que_compitio_sin_ninguno(tmp_path, capsys):
    """Haiku solo contra nadie sigue abierta: la declaración quita a uno, no a cualquiera que falte."""
    salida, texto, _ = comparar_con_sin_medir(
        tmp_path, capsys, [reporte(HAIKU, reglas=(27,), costo="0.05")], f"google:gemini-3.8-flash={MOTIVO}"
    )
    assert salida == 0
    assert ABIERTA in texto
    assert "Gemini 3.5 Flash-Lite" in texto


@pytest.mark.parametrize(
    "sin_medir",
    ["anthropic:claude-haiku-5-5=no aplica", "google:otro-modelo=no aplica", "google:gemini-3.8-flash", "google:gemini-3.8-flash="],
)
def test_una_declaracion_que_no_cuadra_no_escribe_la_comparacion(tmp_path, capsys, sin_medir):
    """Un modelo con reporte, un id fuera de D7-A o una declaración sin motivo fallan antes de escribir."""
    salida, texto, consola = comparar_con_sin_medir(
        tmp_path, capsys, [reporte(HAIKU, costo="0.05"), reporte(FLASH_LITE, costo="0.20")], sin_medir
    )
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert texto is None
    assert "--sin-medir" in consola.err


# ------------------------------------------------------------------ la regla


def test_gana_el_mas_barato_dentro_del_rango_y_lo_decide_el_costo(comparar):
    salida, texto, consola = comparar(
        reporte(HAIKU, reglas=(27,), costo="0.05"),
        reporte(FLASH_LITE, reglas=(27,), costo="0.20"),
        reporte(FLASH, reglas=(27,), costo="0.40"),
    )
    assert salida == 0
    assert "Elegido: Haiku 5.5" in texto
    assert "Lo decidió el costo" in texto
    assert ABIERTA not in texto
    assert "Elegido: Haiku 5.5" in consola.out


def test_el_mas_barato_fuera_del_rango_del_mejor_pierde_y_lo_dice(comparar):
    """Con una corrida el rango es un punto: 26 de 30 no entra en el rango de 27 de 30."""
    _, texto, _ = comparar(
        reporte(HAIKU, reglas=(26,), costo="0.05"),
        reporte(FLASH_LITE, reglas=(27,), costo="0.20"),
        reporte(FLASH, reglas=(27,), costo="0.40"),
    )
    assert "Elegido: Gemini 3.5 Flash-Lite" in texto
    assert "Lo decidió el costo" in texto
    assert "Haiku 5.5: exactitud" in texto


def test_con_dos_corridas_entra_quien_toca_el_rango_del_mejor(comparar):
    """El mejor va de 27 a 29: un modelo de 26 a 27 lo toca, uno de 25 a 26 no."""
    _, texto, _ = comparar(
        reporte(HAIKU, reglas=(25, 26), costo="0.05"),
        reporte(FLASH_LITE, reglas=(26, 27), costo="0.20"),
        reporte(FLASH, reglas=(27, 29), costo="0.40"),
    )
    assert "Elegido: Gemini 3.5 Flash-Lite" in texto
    assert "Haiku 5.5: exactitud" in texto


def test_si_solo_uno_pasa_lo_decidio_lo_que_saco_a_los_otros(comparar):
    _, texto, _ = comparar(
        reporte(HAIKU, reglas=(20,), costo="0.05"),
        reporte(FLASH_LITE, reglas=(21,), costo="0.20"),
        reporte(FLASH, reglas=(27,), costo="0.40"),
    )
    assert "Elegido: Gemini 3.8 Flash" in texto
    assert "Lo decidió la exactitud" in texto


def test_si_solo_uno_pasa_por_latencia_lo_decidio_la_latencia(comparar):
    lentas = (6000,) * 11
    _, texto, _ = comparar(
        reporte(HAIKU, costo="0.05", latencias=lentas),
        reporte(FLASH_LITE, costo="0.20", latencias=lentas),
        reporte(FLASH, costo="0.40"),
    )
    assert "Elegido: Gemini 3.8 Flash" in texto
    assert "Lo decidió la latencia" in texto


@pytest.mark.parametrize(
    "latencias, dato",
    [
        ((5500, 5600, 5700, 5800, 5900, 6000, 6100, 6200, 6300, 6400, 6500), "p50"),
        ((1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 10500, 10500), "p90"),
        ((1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 60500), "máximo"),
    ],
)
def test_quien_incumple_una_meta_de_latencia_queda_fuera_aunque_sea_el_mas_barato(comparar, latencias, dato):
    _, texto, _ = comparar(
        reporte(HAIKU, costo="0.05", latencias=latencias),
        reporte(FLASH_LITE, costo="0.20"),
        reporte(FLASH, costo="0.40"),
    )
    assert "Elegido: Gemini 3.5 Flash-Lite" in texto
    assert f"Haiku 5.5: latencia {dato}" in texto


def test_justo_en_las_metas_de_latencia_cumple(comparar):
    latencias = (1000, 1000, 1000, 1000, 1000, 5000, 5000, 5000, 5000, 10000, 60000)
    _, texto, _ = comparar(
        reporte(HAIKU, costo="0.05", latencias=latencias),
        reporte(FLASH_LITE, costo="0.20"),
        reporte(FLASH, costo="0.40"),
    )
    assert "Elegido: Haiku 5.5" in texto


def test_con_empate_en_costo_gana_el_del_plan_gratuito_y_lo_dice(comparar):
    _, texto, _ = comparar(
        reporte(HAIKU, costo="0.20"),
        reporte(FLASH_LITE, costo="0.20"),
        reporte(FLASH, costo="0.40"),
    )
    assert "Elegido: Gemini 3.5 Flash-Lite" in texto
    assert "Lo decidió el plan gratuito" in texto


def test_el_empate_entre_dos_con_plan_gratuito_no_lo_resuelve_la_regla(comparar):
    _, texto, _ = comparar(
        reporte(HAIKU, costo="0.40"),
        reporte(FLASH_LITE, costo="0.20"),
        reporte(FLASH, costo="0.20"),
    )
    assert ABIERTA in texto
    assert "Elegido" not in texto


def test_el_costo_se_compara_a_precio_de_pago_aunque_gemini_sea_gratis(comparar):
    """Un $0 del plan gratuito haría ganar a Gemini siempre, y el desempate del README no tendría caso."""
    _, texto, _ = comparar(
        reporte(HAIKU, costo="0.05"),
        reporte(FLASH_LITE, costo="0.20"),
        reporte(FLASH, costo="0.40"),
    )
    assert "Elegido: Haiku 5.5" in texto
    assert "precio de pago" in texto


def test_sin_precio_no_se_elige_y_se_dice_por_que(comparar):
    """Hoy config/precios.yaml no trae gemini-3.8-flash: su costo sale vacío y no vale cero."""
    salida, texto, _ = comparar(
        reporte(HAIKU, costo="0.05"),
        reporte(FLASH_LITE, costo="0.20"),
        reporte(FLASH, costo=None),
    )
    assert salida == 0
    assert ABIERTA in texto
    assert "Gemini 3.8 Flash" in texto.split(ABIERTA)[1].splitlines()[0]
    assert "config/precios.yaml" in texto
    assert "Elegido" not in texto


def test_el_que_salio_por_exactitud_no_estorba_aunque_no_tenga_precio(comparar):
    _, texto, _ = comparar(
        reporte(HAIKU, costo="0.05"),
        reporte(FLASH_LITE, costo="0.20"),
        reporte(FLASH, reglas=(20,), costo=None),
    )
    assert "Elegido: Haiku 5.5" in texto


def test_si_nadie_cumple_la_latencia_queda_abierta(comparar):
    lentas = (6000,) * 11
    _, texto, _ = comparar(
        reporte(HAIKU, costo="0.05", latencias=lentas),
        reporte(FLASH_LITE, costo="0.20", latencias=lentas),
        reporte(FLASH, costo="0.40", latencias=lentas),
    )
    assert ABIERTA in texto
    assert "Elegido" not in texto


def test_con_una_corrida_dice_que_el_rango_es_un_punto(comparar):
    _, una, _ = comparar(reporte(HAIKU, reglas=(27,)))
    _, dos, _ = comparar(reporte(HAIKU, reglas=(27, 28)))
    aviso = "rango del mejor es un solo punto"
    assert aviso in una
    assert aviso not in dos


# ------------------------------------------------------------------ el juez, al lado y sin mezclar


def test_la_regla_usa_las_reglas_solas_aunque_el_juez_de_haiku_lo_baje(comparar):
    """Con el juez, Haiku tendría 20 de 30 y saldría del rango: la comparación no los mezcla."""
    _, texto, _ = comparar(
        reporte(HAIKU, reglas=(27,), con_juez=(20,), costo="0.05"),
        reporte(FLASH_LITE, reglas=(27,), costo="0.20"),
        reporte(FLASH, reglas=(27,), costo="0.40"),
    )
    assert "Elegido: Haiku 5.5" in texto
    renglon = renglones_de(texto, "Haiku 5.5")[0]
    assert "90.0 %" in renglon and "66.7 %" in renglon


def test_el_juez_de_otro_modelo_no_presta_su_cifra(comparar):
    """Sin validar, `acierto` es igual a las reglas: escribirlo como «con juez» diría que el juez lo vio."""
    _, texto, _ = comparar(
        reporte(HAIKU, reglas=(27,), con_juez=(20,)),
        reporte(FLASH_LITE, reglas=(25,), motivo="no hay validación para google:gemini-3.5-flash-lite", costo="0.20"),
    )
    renglon = renglones_de(texto, "Gemini 3.5 Flash-Lite")[0]
    assert renglon.count("83.3 %") == 1
    assert "no hay validación para google:gemini-3.5-flash-lite" in renglon


def test_por_categoria_saca_lo_que_reprobo_el_juez(comparar, tmp_path):
    """`por_categoria` trae lo que reprobó el juez. Lo que sale aquí son las reglas, con el juez en su columna."""
    ruta_banco = banco_de(
        tmp_path / "banco.yaml",
        sencilla("N-1", "uno", categoria="cifra_directa", nucleo=True),
        sencilla("N-2", "dos", categoria="politica", nucleo=True),
        sencilla("N-3", "tres", categoria="politica", nucleo=True),
        compuesto("DN-1", "con_signos", "¿cuanto vendio valle en ensayo? ¿cuanto vendio cumbres en ensayo?", "N-1", "N-2"),
    )
    veredictos = [
        {"corrida": 1, "prompt": "N-2", "turno": 0, "pregunta": "N-2", "veredicto": "reprueba"},
        {"corrida": 1, "prompt": "N-3", "turno": 0, "pregunta": "N-3", "veredicto": "aprueba"},
        # El veredicto del criterio propio del compuesto alcanza a sus dos partes.
        {"corrida": 1, "prompt": "DN-1", "turno": 1, "pregunta": "DN-1", "veredicto": "reprueba"},
        # Una variante no entra a por_categoria.
        {"corrida": 1, "prompt": "N-1-v1", "turno": 0, "pregunta": "N-1-v1", "veredicto": "reprueba"},
    ]
    por_categoria = {"cifra_directa": tasas((1,), n=2), "politica": tasas((1,), n=3)}
    salida, texto, _ = comparar(
        reporte(HAIKU, con_juez=(20,), prompts=("N-1", "N-2", "N-3", "DN-1"), por_categoria=por_categoria, veredictos=veredictos),
        ruta_banco=ruta_banco,
    )
    assert salida == 0
    cifra, politica = (renglones_de(texto, nombre)[0] for nombre in ("cifra_directa", "politica"))
    # Reglas: 1 + la parte N-1 del compuesto; con juez: el 1 de 2 que traía el reporte.
    assert cifra.split("|")[2].strip() == "2 de 2 (100.0 %)"
    assert cifra.split("|")[3].strip() == "1 de 2 (50.0 %)"
    # Reglas: 1 + N-2 suelta + la parte N-2 del compuesto.
    assert politica.split("|")[2].strip() == "3 de 3 (100.0 %)"
    assert politica.split("|")[3].strip() == "1 de 3 (33.3 %)"


def test_un_banco_que_no_es_el_de_la_corrida_falla_y_no_inventa_categorias(comparar, tmp_path):
    ruta_banco = banco_de(tmp_path / "banco.yaml", sencilla("N-1", "uno", categoria="otra", nucleo=True))
    veredictos = [{"corrida": 1, "prompt": "N-1", "turno": 0, "pregunta": "N-1", "veredicto": "reprueba"}]
    salida, texto, consola = comparar(
        reporte(HAIKU, con_juez=(20,), prompts=("N-1",), por_categoria={"cifra_directa": tasas((0,), n=1)}, veredictos=veredictos),
        ruta_banco=ruta_banco,
    )
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert texto is None
    assert "banco" in consola.err


# ------------------------------------------------------------------ falla con un mensaje claro


@pytest.mark.parametrize(
    "cambio, palabra",
    [
        ({"sha": OTRO_SHA}, "commits distintos"),
        ({"cambios": "c" * 64}, "commits distintos"),
        ({"alcance": "completo"}, "alcances distintos"),
        ({"variantes": True}, "alcances distintos"),
        ({"prompts": ("P01", "P02", "P99")}, "alcances distintos"),
        ({"configuracion": "base"}, "configuraciones distintas"),
        ({"reglas": (27, 27)}, "corridas distintas"),
    ],
)
def test_reportes_que_no_se_comparan_fallan_sin_tocar_el_archivo(comparar, cambio, palabra):
    salida, texto, consola = comparar(reporte(HAIKU), reporte(FLASH_LITE, **cambio), previo="lo de antes")
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert texto == "lo de antes"
    assert palabra in consola.err
    assert "r0.json" in consola.err and "r1.json" in consola.err


def test_el_mensaje_de_commits_nombra_los_dos(comparar):
    _, _, consola = comparar(reporte(HAIKU), reporte(FLASH_LITE, sha=OTRO_SHA))
    assert SHA[:7] in consola.err and OTRO_SHA[:7] in consola.err


def test_dos_commits_del_mismo_modelo_fallan_por_el_commit(comparar):
    _, _, consola = comparar(reporte(HAIKU), reporte(HAIKU, sha=OTRO_SHA))
    assert "commits distintos" in consola.err


def test_un_reporte_a_medias_no_compite(comparar):
    salida, texto, consola = comparar(reporte(HAIKU, completo=False, detenida="límite de 8 minutos"))
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert texto is None
    assert "--reanudar" in consola.err


def test_dos_reportes_del_mismo_modelo_fallan(comparar):
    salida, _, consola = comparar(reporte(HAIKU), reporte(HAIKU))
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert "mismo modelo" in consola.err


def test_un_modelo_fuera_de_d7_falla(comparar):
    salida, _, consola = comparar(reporte("anthropic:claude-sonnet-5-5"))
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert "D7" in consola.err


def test_un_reporte_con_dos_modelos_falla(comparar):
    datos = reporte(HAIKU)
    datos["meta"]["modelos"] = [HAIKU, FLASH]
    salida, _, consola = comparar(datos)
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert "un modelo" in consola.err


def test_un_rejuzgado_no_entra(comparar):
    """Su commit es el del juez y sus respuestas salen de otro: el chequeo de commits se engañaría."""
    datos = reporte(HAIKU)
    datos["rejuzgado"] = {"commit_de_origen": {"sha": OTRO_SHA, "sucio": False}}
    salida, _, consola = comparar(datos)
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert "rejuzgado" in consola.err


def test_un_json_que_no_es_del_set_falla(comparar):
    salida, _, consola = comparar({"fecha": "2026-11-03", "casos": []})
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert "no es un reporte" in consola.err


def test_un_reporte_de_antes_del_juez_falla_y_dice_que_le_falta(comparar):
    datos = reporte(HAIKU)
    del datos["acierto_reglas"]
    salida, _, consola = comparar(datos)
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert "acierto_reglas" in consola.err


def test_un_reporte_sin_commit_falla(comparar):
    datos = reporte(HAIKU)
    datos["meta"]["commit"] = None
    salida, _, consola = comparar(datos)
    assert salida == comparacion.SALIDA_REPORTES_INVALIDOS
    assert "commit" in consola.err


def test_un_archivo_que_no_existe_o_no_se_lee_falla(tmp_path, capsys):
    destino = tmp_path / "comparacion-modelos.md"
    roto = tmp_path / "roto.json"
    roto.write_text("{no es json", encoding="utf-8")
    assert comparacion.main([str(tmp_path / "no-esta.json")], salida=destino) == comparacion.SALIDA_REPORTES_INVALIDOS
    assert "no-esta.json" in capsys.readouterr().err
    assert comparacion.main([str(roto)], salida=destino) == comparacion.SALIDA_REPORTES_INVALIDOS
    assert "roto.json" in capsys.readouterr().err
    assert not destino.exists()


def test_sin_reportes_argparse_lo_rechaza(tmp_path):
    with pytest.raises(SystemExit) as salida:
        comparacion.main([], salida=tmp_path / "x.md")
    assert salida.value.code == 2


# ------------------------------------------------------------------ lo que escribe


def test_cada_modelo_lleva_commit_exactitud_tokens_costo_y_latencia(comparar):
    _, texto, _ = comparar(reporte(HAIKU, costo="0.05", llamadas=23))
    assert SHA[:7] in texto
    assert "90.0 %" in texto
    assert "1,234,567" in texto
    assert "66,000" in texto
    assert "0.050000" in texto
    # p50, p90 y máximo de LATENCIAS en segundos, con punto decimal.
    assert "4.00 / 8.00 / 9.00" in texto
    # Entrada del turno de 1,000 a 11,000: p50 6,000 y p90 10,000.
    assert "6,000 / 10,000" in texto


def test_el_commit_dice_si_traia_cambios_sin_commit(comparar):
    _, texto, _ = comparar(reporte(HAIKU, cambios="c" * 64))
    assert f"{SHA[:7]} con cambios sin commit {'c' * 8}" in texto


def test_por_categoria_da_el_acierto_de_cada_corrida_con_su_n(comparar):
    por_categoria = {"adversarial": tasas((5, 6), n=7)}
    _, texto, _ = comparar(reporte(HAIKU, reglas=(27, 28), por_categoria=por_categoria))
    assert "5 de 7 (71.4 %); 6 de 7 (85.7 %)" in renglones_de(texto, "adversarial")[0]


def test_cuenta_las_solicitudes_por_modelo_para_t6_4(comparar):
    uso = {"llamadas": 9, "entrada_tokens": 100, "salida_tokens": 10, "costo_usd": "0.01"}
    _, texto, consola = comparar(reporte(HAIKU, reglas=(27, 27), con_juez=(26, 26), llamadas=46, reintentos=3, uso_del_juez=uso))
    renglon = next(linea for linea in renglones_de(texto, "Haiku 5.5") if "| 46 |" in linea)
    assert "| 23.00 |" in renglon  # por corrida
    assert "| 3 |" in renglon
    assert "| 9 |" in renglon
    assert "46" in consola.out


def test_el_gasto_de_gemini_va_aparte_del_de_anthropic(comparar):
    uso = {"llamadas": 9, "entrada_tokens": 100, "salida_tokens": 10, "costo_usd": "0.01"}
    _, texto, _ = comparar(
        reporte(HAIKU, costo="0.05", con_juez=(26,), uso_del_juez=uso),
        reporte(FLASH_LITE, costo="0.20"),
        reporte(FLASH, costo="0.40"),
    )
    [anthropic] = renglones_de(texto, "Anthropic")
    [google] = renglones_de(texto, "Google")
    assert "0.050000" in anthropic and "0.010000" in anthropic
    assert "0.600000" in google
    assert "0.600000" not in anthropic


def test_un_gasto_con_un_modelo_sin_precio_no_suma_de_menos(comparar):
    _, texto, _ = comparar(reporte(FLASH_LITE, costo="0.20"), reporte(FLASH, costo=None))
    [google] = renglones_de(texto, "Google")
    assert "sin precio" in google
    assert "0.200000" not in google


def test_un_proveedor_sin_corrida_no_escribe_gasto(comparar):
    _, texto, _ = comparar(reporte(HAIKU))
    [google] = renglones_de(texto, "Google")
    assert "sin reporte" in google
    assert not any(caracter.isdigit() for caracter in google)


def test_las_variantes_no_entran_a_la_latencia_de_la_regla(comparar):
    datos = reporte(HAIKU, costo="0.05")
    datos["turnos"].append({**datos["turnos"][0], "prompt": "P01-v1", "latencia_ms": 90000})
    _, texto, _ = comparar(datos, reporte(FLASH_LITE, costo="0.20"), reporte(FLASH, costo="0.40"))
    assert "Elegido: Haiku 5.5" in texto
    assert "4.00 / 8.00 / 9.00" in texto


def test_un_turno_que_trono_no_entra_a_la_latencia(comparar):
    datos = reporte(HAIKU, costo="0.05")
    datos["turnos"].append({**datos["turnos"][0], "estado": "excepcion", "latencia_ms": 90000})
    _, texto, _ = comparar(datos, reporte(FLASH_LITE, costo="0.20"), reporte(FLASH, costo="0.40"))
    assert "Elegido: Haiku 5.5" in texto


def test_el_archivo_va_en_utf8_con_lf_y_acentos(comparar, tmp_path):
    comparar(reporte(HAIKU))
    crudo = (tmp_path / "comparacion-modelos.md").read_bytes()
    assert b"\r\n" not in crudo
    assert "Comparación".encode() in crudo


def test_el_archivo_por_omision_es_el_del_acepta():
    assert comparacion.SALIDA.parts[-3:] == ("eval", "reportes", "comparacion-modelos.md")


# ------------------------------------------------------------------ con reportes escritos por eval.correr


@pytest.fixture
def correr_modelo(tmp_path, monkeypatch):
    """eval.correr entero con turnos armados: el .json es el que escribe el comando de verdad, sin llamar a ningún modelo."""
    monkeypatch.setattr(correr, "_hash_de_las_fuentes", lambda conf: "hash-de-ensayo")
    monkeypatch.setattr(correr.registro, "commit", lambda: {"sha": SHA, "sucio": False})
    original = correr.huella_de

    def sin_cambios_de_otros(*argumentos, **opciones):
        # Otros agentes pueden estar editando el checkout: dos corridas de la prueba no deben ver cambios distintos.
        return {**original(*argumentos, **opciones), "cambios_sin_commit": None}

    monkeypatch.setattr(correr, "huella_de", sin_cambios_de_otros)
    ruta_banco = banco_de(
        tmp_path / "banco.yaml",
        sencilla("N-1", "uno de ensayo", nucleo=True),
        sencilla("N-2", "dos de ensayo", categoria="politica", nucleo=True),
    )

    def _correr_modelo(modelo, costo, latencia):
        turno = turno_armado(
            modelo=modelo, costo_usd=Decimal(costo), latencia_ms=latencia, entrada_tokens=3000, salida_tokens=200, n_llamadas=2
        )
        monkeypatch.setattr(correr, "correr_prompt", lambda prompt, conf: ResultadoPrompt(turnos=(turno,)))
        carpeta = tmp_path / modelo.replace(":", "-")
        salida = correr.main(
            ["--banco", str(ruta_banco), "--corridas", "1"],
            modelo=TestModel(call_tools=[], custom_output_text="sin uso"),
            archivo_registro=tmp_path / "turnos.jsonl",
            dir_reportes=carpeta,
            base=tmp_path / "sin_base.duckdb",
            raiz_copias=tmp_path / "copias",
            dir_juez=tmp_path / "sin_validar",
        )
        assert salida == 0
        [ruta] = carpeta.glob("*.json")
        return ruta

    return _correr_modelo


def test_lee_lo_que_escribe_eval_correr_y_cierra_cuando_llegan_los_tres(correr_modelo, tmp_path):
    destino = tmp_path / "comparacion-modelos.md"
    haiku = correr_modelo(HAIKU, "0.001", 4000)

    assert comparacion.main([str(haiku)], salida=destino) == 0
    assert ABIERTA in destino.read_text(encoding="utf-8")

    flash_lite = correr_modelo(FLASH_LITE, "0.002", 4000)
    flash = correr_modelo(FLASH, "0.003", 4000)
    assert comparacion.main([str(haiku), str(flash_lite), str(flash)], salida=destino) == 0
    texto = destino.read_text(encoding="utf-8")
    assert "Elegido: Haiku 5.5" in texto
    assert "Lo decidió el costo" in texto
    assert SHA[:7] in texto
    # 2 prompts de 1 turno a 0.001: el costo total sale de uso_total del reporte.
    assert "0.002000" in renglones_de(texto, "Haiku 5.5")[0]
