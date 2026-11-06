"""Errores con tipo de las herramientas: por qué no hay cifra, dicho antes de que el modelo redacte (T2.2).

Una herramienta devuelve una cifra con su unidad, definición, filtros y corte, o uno de estos
errores. Todos son ErrorHerramienta (agente/herramientas/comun.py): comun.ejecutar() los convierte
en un Resultado con su estado y su detalle, sin cifras ni filas, y ese texto vuelve al modelo como
el resultado de la herramienta. La abstención sale de la herramienta y no depende solo de una frase
del prompt (C6.3); el modelo no tiene un número que presentar (G1).

    estado                 clase                 quién la lanza
    metrica_no_calculable  MetricaNoCalculable   verificar_metrica(), al inicio de cada herramienta
    entidad_ambigua        EntidadAmbigua        el resolvedor (agente/resolver.py, T2.8)
    entidad_inexistente    EntidadInexistente    el resolvedor
    fuera_de_periodo       FueraDePeriodo        el resolvedor, con una fecha relativa, y las herramientas
    canal_inexistente      CanalInexistente      verificar_canal(), en ventas
    fuente_no_disponible   FuenteNoDisponible    fuente_no_disponible_de(), cuando la base no se puede usar
    documento_no_incluido  DocumentoNoIncluido   buscar_politicas

El detalle trae lo que el modelo necesita para explicar el límite y ofrecer lo que sí se puede dar
(C3.1 y C3.2): los candidatos de una entidad ambigua, las que sí existen cuando una no existe, el
corte y la interpretación de una fecha relativa (C3.3), los canales que sí tiene una tienda, las
alternativas de una métrica, lo que el modelo debe hacer antes de contestar (antes_de_contestar) y los
documentos que sí vienen. Es texto corto: un error pesa menos de 300 tokens, igual que una cifra simple. Las razones de una métrica no calculable salen de
config/supuestos.yaml, no de este módulo (C1.4).

El texto que escribió el usuario se repite limpio y con tope de largo. Una fuente que falla se nombra
solo por el nombre del archivo: el texto de un error de construcción trae la ruta absoluta, con el
usuario de Windows, y no se repite (K04, K21).

Las fechas explícitas fuera del corte las lanza comun.en_corte() con solo los límites del corte.
FueraDePeriodo agrega lo que solo sabe quien interpretó una fecha relativa: lo que pidió el usuario
y cómo lo interpretó (D3-A: "hoy" es el día siguiente al último dato y la fecha real no tiene datos).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import singledispatch
from typing import Any, Literal, NamedTuple

from agente.consultas import BaseNoDisponible
from agente.construir import ArchivoFaltante, BaseEnUso, CsvInvalido, ErrorConstruccion, FuentesCambiaron
from agente.definiciones import Supuestos, supuestos
from agente.herramientas.comun import ErrorHerramienta, FuenteConCorte, _limites_corte, fecha_larga

MAX_TEXTO = 80
MAX_LISTA = 8
MAX_ORACION = 300
MAX_INTERPRETACION = 300

Entidad = Literal["tienda", "producto", "categoria"]
MotivoFuente = Literal["falta", "cambio", "invalida", "en_uso", "no_abre", "no_preparada"]

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_SEPARADOR_DE_RUTA = re.compile(r"[\\/]")
# "y" se escribe "e" delante de una palabra que empieza con i o hi ("ventas e inventario").
_ANTES_DE_I = re.compile(r"h?i(?![aeou])", re.IGNORECASE)
# Las razones de config/supuestos.yaml citan la decisión del plan, "(D1-A)"; la gerente no la conoce.
_ETIQUETA_DE_DECISION = re.compile(r"\s*\(D\d+(?:-[A-Z])?\)")


# ------------------------------------------------------------------ texto

def _texto(valor: object, maximo: int = MAX_TEXTO) -> str:
    """Texto listo para repetirse: sin saltos de línea ni caracteres de control, y con tope de largo."""
    limpio = " ".join(_CONTROL.sub(" ", str(valor)).split())
    return limpio if len(limpio) <= maximo else limpio[: maximo - 1] + "…"


def _textos(valores: Sequence[object]) -> list[str]:
    return [_texto(valor) for valor in valores][:MAX_LISTA]


def _lista_con_total(clave: str, valores: Sequence[object]) -> dict[str, Any]:
    """La lista acotada a MAX_LISTA bajo su clave y, si se recortó, cuántos había en total."""
    recorte = {"total": len(valores)} if len(valores) > MAX_LISTA else {}
    return {clave: _textos(valores), **recorte}


def _unir(elementos: Sequence[str]) -> str:
    """'a', 'a y b' o 'a, b y c'; la y pasa a e delante de una palabra que empieza con i."""
    if len(elementos) < 2:
        return "".join(elementos)
    conjuncion = "e" if _ANTES_DE_I.match(elementos[-1]) else "y"
    return f"{', '.join(elementos[:-1])} {conjuncion} {elementos[-1]}"


class _Nombres(NamedTuple):
    uno: str
    plural: str


_ENTIDADES: dict[str, _Nombres] = {
    "tienda": _Nombres("una tienda", "tiendas"),
    "producto": _Nombres("un producto", "productos"),
    "categoria": _Nombres("una categoría", "categorías"),
}


def _nombres(entidad: str) -> _Nombres:
    if entidad not in _ENTIDADES:
        raise ValueError(f"entidad {entidad!r} desconocida; el error conoce {', '.join(_ENTIDADES)}")
    return _ENTIDADES[entidad]


# ------------------------------------------------------------------ métrica no calculable

_NOMBRES_DE_METRICA = {
    "ticket_promedio": "el ticket promedio",
    "venta_por_hora": "la venta por hora",
    "merma_real": "la merma real",
    "meta_venta": "la meta de venta",
    "primera_respuesta": "la primera respuesta de un ticket",
    "inventario_historico": "el inventario de una fecha anterior al corte",
}

# Lo que sí se puede dar en lugar de la métrica (C3.1: razón y alternativa). Cada texto nombra algo
# que dan las herramientas de T2.3 a T2.7. {corte} es la fecha del corte del inventario. Una herramienta
# entre paréntesis es otra que contesta esa alternativa: el modelo la llama en el mismo turno (F2 de T5.11).
_ALTERNATIVAS_DE_METRICA = {
    "ticket_promedio": ("la venta total de la tienda en el periodo", "la venta por día o por producto"),
    "venta_por_hora": ("la venta por día", "la venta por día de la semana"),
    "merma_real": (
        "la tolerancia de merma de POL-OPS-014 (buscar_politicas)",
        "los tickets que mencionan merma (tickets, metrica buscar)",
    ),
    "meta_venta": (
        "la venta del periodo comparada con la del periodo anterior, dicha como comparación y no como meta",
    ),
    "primera_respuesta": ("el tiempo de solución de los tickets cerrados",),
    "inventario_historico": ("la existencia al corte del {corte}",),
}

# Cómo contestar una métrica cuando sus alternativas no bastan para decirlo. La merma real: las tolerancias son una tabla de
# POL-OPS-014, y el sistema donde se registra la merma no lo tiene que conocer quien pide la cifra.
_AL_CONTESTAR_METRICA = {
    "merma_real": (
        "Si la pregunta no nombra una categoría, da las tolerancias de todas en una tabla Markdown. "
        "No digas en qué sistema están los registros ni a quién pedirlos."
    ),
}


_HERRAMIENTA_DE_ALTERNATIVA = re.compile(r"\((buscar_politicas|tickets|ventas|inventario|caducidades)\b")


def _antes_de_contestar(alternativas: Sequence[str]) -> str | None:
    """Lo que el modelo hace antes de contestar si una alternativa nombra otra herramienta; None si ninguna la nombra."""
    herramientas = list(dict.fromkeys(m.group(1) for texto in alternativas for m in _HERRAMIENTA_DE_ALTERNATIVA.finditer(texto)))
    if not herramientas:
        return None
    return (
        f"Antes de contestar llama a {_unir(herramientas)}: lo que traigan sí se da, con su cita o su folio. "
        "No lo dejes como alternativa."
    )


class MetricaNoCalculable(ErrorHerramienta):
    """Los datos no permiten calcular la métrica (ticket promedio, merma real, venta por hora, meta de venta).

    La razón sale de metricas_no_calculables en config/supuestos.yaml. Una métrica que supuestos no
    declara es un defecto de quien llama, no un error con tipo.
    """

    def __init__(self, metrica: str, s: Supuestos | None = None):
        s = s or supuestos()
        if metrica not in s.metricas_no_calculables:
            raise ValueError(f"{metrica!r} no está en metricas_no_calculables de config/supuestos.yaml")
        razon = _ETIQUETA_DE_DECISION.sub("", s.metricas_no_calculables[metrica])
        nombre = _NOMBRES_DE_METRICA.get(metrica, f"la métrica {metrica.replace('_', ' ')}")
        corte = fecha_larga(s.cortes.inventario.fecha)
        alternativas = [texto.format(corte=corte) for texto in _ALTERNATIVAS_DE_METRICA.get(metrica, ())]
        detalle: dict[str, Any] = {"metrica": metrica}
        if alternativas:
            detalle["alternativas"] = alternativas
        indicaciones = [_antes_de_contestar(alternativas), _AL_CONTESTAR_METRICA.get(metrica)]
        siguiente = " ".join(indicacion for indicacion in indicaciones if indicacion)
        if siguiente:
            detalle["antes_de_contestar"] = siguiente
        super().__init__("metrica_no_calculable", f"No se puede calcular {nombre}: {razon}", **detalle)


def verificar_metrica(metrica: str, s: Supuestos | None = None) -> None:
    """Lanza MetricaNoCalculable si config/supuestos.yaml declara que los datos no la permiten."""
    s = s or supuestos()
    if metrica in s.metricas_no_calculables:
        raise MetricaNoCalculable(metrica, s)


# ------------------------------------------------------------------ entidad ambigua e inexistente

_PREGUNTA_QUE_DECIDE = (
    "Si la unidad o la presentación que dice la pregunta deja un solo candidato, vuelve a llamar con su SKU en el campo "
    "producto y di en una nota cómo la interpretaste. Si la pregunta no decide, pregunta cuál."
)


class EntidadAmbigua(ErrorHerramienta):
    """El texto del usuario coincide con dos o más tiendas, productos o categorías (C3.2).

    candidatos son etiquetas como "T01 Tienda Valle Norte (Monterrey)": la primera palabra es el id
    que acepta la herramienta, para consultar cada candidato por separado. En un producto, la pregunta puede
    decidir por su unidad o su presentación, y antes_de_contestar se lo dice al modelo (F1 de T5.11).
    """

    def __init__(self, entidad: Entidad, texto: str, candidatos: Sequence[str]):
        nombres = _nombres(entidad)
        if len(candidatos) < 2:
            raise ValueError(f"una entidad ambigua necesita dos candidatos o más, no {len(candidatos)}")
        pedido = _texto(texto)
        decide = {"antes_de_contestar": _PREGUNTA_QUE_DECIDE} if entidad == "producto" else {}
        super().__init__(
            "entidad_ambigua",
            f"'{pedido}' coincide con {len(candidatos)} {nombres.plural}",
            entidad=entidad,
            texto=pedido,
            **_lista_con_total("candidatos", candidatos),
            **decide,
        )


class EntidadInexistente(ErrorHerramienta):
    """El texto del usuario no coincide con ninguna tienda, producto o categoría (C3.1).

    existentes son todas las que sí hay, no las más parecidas: una tienda inexistente no se
    sustituye por una con nombre parecido. antes_de_contestar dice qué hacer cuando el texto sí es otra
    cosa del catálogo; lo escribe el código, sin repetir lo que pidió el modelo (D1-A del plan de huecos).
    """

    def __init__(self, entidad: Entidad, texto: str, existentes: Sequence[str] = (), antes_de_contestar: str | None = None):
        nombres = _nombres(entidad)
        pedido = _texto(texto)
        detalle: dict[str, Any] = {"entidad": entidad, "texto": pedido}
        if existentes:
            detalle.update(_lista_con_total("existentes", existentes))
        if antes_de_contestar:
            detalle["antes_de_contestar"] = antes_de_contestar
        super().__init__("entidad_inexistente", f"No hay {nombres.uno} que coincida con '{pedido}'", **detalle)


# ------------------------------------------------------------------ fuera de periodo

_NOMBRE_DE_FUENTE = {"ventas": "ventas", "tickets": "tickets", "inventario": "inventario", "caducidades": "inventario"}


def _toca_el_corte(desde: dt.date, hasta: dt.date, inicio: dt.date | None, fin: dt.date) -> bool:
    antes_del_corte = inicio is not None and hasta < inicio
    despues_del_corte = desde > fin
    return not (antes_del_corte or despues_del_corte)


def _limites_sin_datos(fuente: str, desde: dt.date, hasta: dt.date) -> tuple[dt.date | None, dt.date]:
    """Los límites del corte de la fuente, si el periodo de verdad queda fuera de ellos; si no, un defecto."""
    if fuente not in _NOMBRE_DE_FUENTE:
        raise ValueError(f"la fuente {fuente!r} no tiene corte; las que tienen son {', '.join(_NOMBRE_DE_FUENTE)}")
    if desde > hasta:
        raise ValueError(f"desde ({desde}) es posterior a hasta ({hasta})")
    inicio, fin = _limites_corte(fuente)
    if _toca_el_corte(desde, hasta, inicio, fin):
        raise ValueError(f"el periodo {desde} a {hasta} toca el corte de {fuente}: sí hay datos")
    return inicio, fin


def _describir_periodo(desde: dt.date, hasta: dt.date) -> str:
    if desde == hasta:
        return f"del {fecha_larga(desde)}"
    return f"del {fecha_larga(desde)} al {fecha_larga(hasta)}"


def _describir_corte(inicio: dt.date | None, fin: dt.date) -> str:
    if inicio is None:
        return f"llegan al {fecha_larga(fin)}"
    if inicio == fin:
        return f"son una foto al {fecha_larga(fin)}"
    return f"van del {fecha_larga(inicio)} al {fecha_larga(fin)}"


class FueraDePeriodo(ErrorHerramienta):
    """El periodo pedido no toca ningún día con datos de la fuente (C3.3).

    desde y hasta son las fechas que quedaron después de interpretar el periodo y periodo_pedido es
    lo que escribió el usuario. Si salió de una fecha relativa (hoy, ayer, el mes pasado), quien la
    interpretó pasa interpretacion: cómo la entendió, con el aviso de que la fecha real no tiene
    datos (aviso_fecha_relativa, de agente/definiciones.py). El detalle trae el corte de la fuente,
    para ofrecer lo último que sí tiene datos. Un periodo que sí toca el corte es un defecto de quien
    llama: decir que no hay datos donde sí los hay es una falsa abstención.
    """

    def __init__(
        self,
        fuente: FuenteConCorte,
        desde: dt.date,
        hasta: dt.date,
        periodo_pedido: str | None = None,
        interpretacion: str | None = None,
    ):
        inicio, fin = _limites_sin_datos(fuente, desde, hasta)
        detalle: dict[str, Any] = {}
        if periodo_pedido:
            detalle["periodo_pedido"] = _texto(periodo_pedido)
        detalle["desde"] = desde.isoformat()
        detalle["hasta"] = hasta.isoformat()
        if interpretacion:
            detalle["interpretacion"] = _texto(interpretacion, MAX_INTERPRETACION)
        if inicio:
            detalle["corte_desde"] = inicio.isoformat()
        detalle["corte_hasta"] = fin.isoformat()

        mensaje = (
            f"No hay datos de {_NOMBRE_DE_FUENTE[fuente]} {_describir_periodo(desde, hasta)}: "
            f"{_describir_corte(inicio, fin)}"
        )
        super().__init__("fuera_de_periodo", mensaje, **detalle)


# ------------------------------------------------------------------ canal inexistente

def _motivo_del_canal(canal: str, s: Supuestos) -> str:
    """Por qué una tienda no tiene el canal, según config/supuestos.yaml; vacío si la regla no lo dice."""
    if canal != "Ecommerce":
        return ""
    formatos = s.ventas.canal_ecommerce_formatos
    if len(formatos) == 1:
        return f"solo el formato {formatos[0]} lo tiene"
    return f"solo los formatos {_unir(formatos)} lo tienen"


def _sin_el_canal(tiendas: Sequence[str], canal: str, motivo: str) -> str:
    verbo = "tiene" if len(tiendas) == 1 else "tienen"
    frase = f"{_unir(tiendas)} no {verbo} el canal {canal}"
    return f"{frase}: {motivo}" if motivo else frase


class CanalInexistente(ErrorHerramienta):
    """El canal no existe, o ninguna de las tiendas consultadas lo tiene (C3.1).

    Sin tiendas_sin_canal, el canal no existe y canales_disponibles son los que sí hay. Con ellas,
    el canal existe pero esas tiendas no lo tienen (un Mi Tienda no vende por Ecommerce) y
    canales_disponibles son los que sí tienen. En los dos casos una venta de cero sería falsa: no
    hay canal que haya vendido cero.
    """

    def __init__(
        self,
        canal: str,
        canales_disponibles: Sequence[str],
        tiendas_sin_canal: Sequence[str] = (),
        motivo: str = "",
    ):
        pedido = _texto(canal)
        disponibles = _textos(canales_disponibles)
        detalle: dict[str, Any] = {"canal": pedido}
        if tiendas_sin_canal:
            sin_canal = _textos(tiendas_sin_canal)
            detalle["tiendas_sin_canal"] = sin_canal
            mensaje = _sin_el_canal(sin_canal, pedido, _texto(motivo, MAX_INTERPRETACION))
        else:
            mensaje = f"No existe el canal '{pedido}'; los canales son {_unir(disponibles)}"
        detalle["canales_disponibles"] = disponibles
        super().__init__("canal_inexistente", mensaje, **detalle)


@dataclass(frozen=True)
class TiendasConCanal:
    """Las tiendas que sí tienen el canal y, si alguna no lo tiene, la nota que lo dice."""

    ids: list[str]
    nota: str = ""


def _nombre_y_formato(tienda: Mapping[str, str]) -> str:
    return f"{tienda['nombre']} ({tienda['formato']})"


def verificar_canal(
    canal: str | None,
    tiendas: Mapping[str, Mapping[str, str]],
    ids: Sequence[str] | None = None,
    s: Supuestos | None = None,
) -> TiendasConCanal:
    """Las tiendas consultadas que tienen el canal; CanalInexistente si el canal no existe o ninguna lo tiene.

    tiendas es el catálogo {tienda_id: {nombre, formato, ...}} e ids las tiendas consultadas (todas
    si no se indica). Si solo algunas tienen el canal, devuelve las que sí y una nota que nombra las
    demás, para que la herramienta la agregue al resultado: una tienda sin el canal queda fuera, no
    cuenta como venta de cero. Sin canal (todos los canales) todas pasan. La regla sale de
    config/supuestos.yaml (ventas.canales y canal_ecommerce_formatos).
    """
    s = s or supuestos()
    consultadas = list(tiendas if ids is None else ids)
    if not consultadas:
        raise ValueError("no hay tiendas que verificar")
    if canal is None:
        return TiendasConCanal(consultadas)
    if canal not in s.ventas.canales:
        raise CanalInexistente(canal, s.ventas.canales)

    con_canal = [t for t in consultadas if s.ventas.canal_existe(tiendas[t]["formato"], canal)]
    sin_canal = [t for t in consultadas if t not in con_canal]
    if not sin_canal:
        return TiendasConCanal(con_canal)
    nombres = [_nombre_y_formato(tiendas[t]) for t in sin_canal]
    motivo = _motivo_del_canal(canal, s)
    if not con_canal:
        formatos = {tiendas[t]["formato"] for t in sin_canal}
        disponibles = [c for c in s.ventas.canales if any(s.ventas.canal_existe(f, c) for f in formatos)]
        raise CanalInexistente(canal, disponibles, nombres, motivo)
    return TiendasConCanal(con_canal, f"{_sin_el_canal(nombres, canal, motivo)}; quedan fuera del resultado")


# ------------------------------------------------------------------ fuente no disponible

# Qué dice el mensaje según el motivo. Con archivos: (uno solo, varios).
_MENSAJE_CON_ARCHIVOS = {
    "falta": ("el archivo no está con ese nombre", "los archivos no están con ese nombre"),
    "cambio": (
        "cambió mientras se preparaba la base; vuelve a intentarlo en un momento",
        "cambiaron mientras se preparaba la base; vuelve a intentarlo en un momento",
    ),
    "invalida": ("no tiene el formato del diccionario de datos", "no tienen el formato del diccionario de datos"),
}
_MENSAJE_SIN_ARCHIVOS = {
    "en_uso": "otro programa tiene abierta la base; vuelve a intentarlo",
    "no_abre": "la base no existe o no se pudo abrir",
    "no_preparada": "la base no se pudo preparar",
}
_MOTIVOS = (*_MENSAJE_CON_ARCHIVOS, *_MENSAJE_SIN_ARCHIVOS)


def _nombre_de_archivo(ruta: str) -> str:
    """Solo el nombre: sin carpetas, que pueden traer el usuario de Windows."""
    return _SEPARADOR_DE_RUTA.split(str(ruta))[-1]


class FuenteNoDisponible(ErrorHerramienta):
    """Un archivo de datos o la base de consulta no se puede usar, y sin ellos no hay cifra (C6.3, K04).

    archivos son los archivos afectados, por su nombre. Los motivos falta, cambio e invalida los
    piden; en_uso, no_abre y no_preparada hablan de la base y no nombran ninguno.
    """

    def __init__(self, archivos: Sequence[str] = (), motivo: MotivoFuente = "falta"):
        if motivo not in _MOTIVOS:
            raise ValueError(f"motivo {motivo!r} desconocido; los que conoce el error son {', '.join(_MOTIVOS)}")
        con_archivos = motivo in _MENSAJE_CON_ARCHIVOS
        nombres = list(dict.fromkeys(_nombre_de_archivo(ruta) for ruta in archivos))
        if con_archivos and not nombres:
            raise ValueError(f"el motivo {motivo!r} necesita los archivos afectados")

        detalle: dict[str, Any] = {"motivo": motivo}
        if nombres:
            detalle["archivos"] = nombres
        if con_archivos:
            uno, varios = _MENSAJE_CON_ARCHIVOS[motivo]
            mensaje = f"No pude consultar {_unir(nombres)}: {uno if len(nombres) == 1 else varios}"
        else:
            mensaje = f"No pude consultar los datos: {_MENSAJE_SIN_ARCHIVOS[motivo]}"
        super().__init__("fuente_no_disponible", mensaje, **detalle)


@singledispatch
def fuente_no_disponible_de(error: ErrorConstruccion) -> FuenteNoDisponible:
    """El error con tipo que corresponde a un error de la construcción o de la apertura de la base.

    Es lo que debe llamar quien responde cuando asegurar_base() o abrir_consultas() fallan, para
    decir que no pudo consultar y no dar cifra. Nunca usa str(error): su texto trae la ruta
    absoluta. Un error de construcción sin caso propio es la base que no se pudo preparar.
    """
    if not isinstance(error, ErrorConstruccion):
        raise TypeError(f"se esperaba un ErrorConstruccion y llegó {type(error).__name__}")
    return FuenteNoDisponible(motivo="no_preparada")


@fuente_no_disponible_de.register(ArchivoFaltante)
def _fuente_que_falta(error: ArchivoFaltante) -> FuenteNoDisponible:
    return FuenteNoDisponible(error.archivos, "falta")


@fuente_no_disponible_de.register(FuentesCambiaron)
def _fuente_que_cambio(error: FuentesCambiaron) -> FuenteNoDisponible:
    return FuenteNoDisponible(error.archivos, "cambio")


@fuente_no_disponible_de.register(CsvInvalido)
def _fuente_invalida(error: CsvInvalido) -> FuenteNoDisponible:
    return FuenteNoDisponible([error.archivo], "invalida")


@fuente_no_disponible_de.register(BaseEnUso)
def _base_en_uso(error: BaseEnUso) -> FuenteNoDisponible:
    return FuenteNoDisponible(motivo="en_uso")


@fuente_no_disponible_de.register(BaseNoDisponible)
def _base_que_no_abre(error: BaseNoDisponible) -> FuenteNoDisponible:
    return FuenteNoDisponible(motivo="no_abre")


# ------------------------------------------------------------------ documento no incluido

_ALTERNATIVA_DE_DOCUMENTO = "lo que dicen sobre el tema los documentos disponibles (buscar_politicas)"


class DocumentoNoIncluido(ErrorHerramienta):
    """El documento pedido no viene entre los disponibles: lo que diga es desconocido (C3.1).

    incluidos son los que sí vienen. citado_en son los lugares donde otro documento lo menciona
    ("POL-OPS-014 §1.2"): decir que existe y no viene es cierto, atribuirle contenido no lo es (G1).
    """

    def __init__(self, documento: str, incluidos: Sequence[str], citado_en: Sequence[str] = (), dicho_en_esas_citas: Sequence[str] = ()):
        codigo = _texto(documento)
        citas = _textos(citado_en)
        mensaje = f"El documento {codigo} no viene entre los documentos disponibles"
        detalle: dict[str, Any] = {"documento": codigo, "incluidos": _textos(incluidos)}
        if citas:
            mensaje += f"; se cita en {_unir(citas)}"
            detalle["citado_en"] = citas
        if dicho_en_esas_citas:
            detalle["dicho_en_esas_citas"] = [_texto(dicho, MAX_ORACION) for dicho in dicho_en_esas_citas][:MAX_LISTA]
        detalle["alternativas"] = [_ALTERNATIVA_DE_DOCUMENTO]
        super().__init__("documento_no_incluido", mensaje, **detalle)
