"""Herramienta de inventario: existencia, punto de reorden, valuación y días de cobertura (T2.4).

Solo lectura sobre DuckDB, con entrada validada (EntradaInventario de comun.py) y error con tipo. El
inventario es una foto al corte (2026-09-01 en config/supuestos.yaml): cada resultado lleva esa fecha en
corte y en la definición, y dice que la existencia de hoy no se conoce (C3.3, P62).

Métricas:

- existencia: piezas o kilos por tienda y SKU, con el punto de reorden y la caducidad del lote más
  próximo. Kilos y piezas no se suman entre sí (unidad_venta de supuestos.yaml): el total va por unidad.
  Pedir por su SKU uno descontinuado dice en el resumen y en una nota que la existencia es de su sustituto.
  Un producto sin fecha de caducidad lo dice en una nota y no se le calcula una (P39, G1).
- bajo_reorden: los casos producto-tienda que cumplen supuestos().inventario.condicion(), con existencia
  y punto. Sin limite explícito se listan todos (hasta 50), porque la lista es la respuesta (P04).
- valuacion: existencia × costo del catálogo y existencia × precio de lista, las dos bases (P27, C3.2).
- cobertura: el rango de 8 de cada 10 escenarios de D23-D, sin la cifra de D20-A. El centro es la venta
  diaria del mes base entre sus días de calendario (Supuestos.media_diaria_base, T1.1). La desviación es
  la raíz de la suma ponderada de las varianzas muestrales de la venta diaria de cada mes, cada una
  contra la media de su mes, con los pesos de cobertura.pesos_varianza. Cada extremo resuelve
  media·d ± z·σ·√d = existencia (extremos_cobertura): con la fórmula y sin simulación, así que da lo
  mismo en cada llamada (C1.3). La salida trae los dos extremos y la existencia, nunca la cifra ni la
  venta diaria con la que el modelo podría calcularla: si la mostrara, el verificador de D16 la marcaría
  como cifra que no sale de una herramienta (G1). Si el retiro del lote (POL-OPS-014, calendario() de
  caducidades.py) llega antes del fin del rango, una nota da el día (P12, T2.5).

La venta diaria de un SKU suma la de su cadena de sustitución cuando sumar_sustitutos está en true
(definiciones.skus_equivalentes): la leche entera 1 L que reemplazó a otro SKU el 15 de julio tiene una
serie continua en junio y julio. No se suman otras presentaciones (D20-A).

Los valores del usuario viajan como parámetros; el SQL solo lleva nombres de columna fijos y las
condiciones de agente/definiciones.py. La consulta del registro trae sus parámetros en la última línea,
para re-ejecutarla fuera del sistema.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import duckdb

from agente.definiciones import MESES_NOMBRE, Supuestos, skus_equivalentes, supuestos
from agente.herramientas.caducidades import calendario
from agente.herramientas.comun import (
    MAX_FILAS,
    EntradaInventario,
    ErrorHerramienta,
    Resultado,
    consultar,
    dumps,
    ejecutar,
    entero,
    fecha_larga,
    num,
    tiendas_validas,
)
from agente.herramientas.errores import EntidadInexistente, verificar_metrica
from agente.resolver import Catalogos, cargar_catalogos, resolver_categoria, resolver_productos, resolver_tiendas

_PATRON_SKU = re.compile(r"SKU-\d{4}")

HERRAMIENTA = "inventario"
FUENTE_INVENTARIO = "inventario.csv"
FUENTE_CATALOGO = "catalogo_productos.csv"
FUENTE_VENTAS = "ventas.csv"

_UNIDADES = {"kg": "kg", "pieza": "piezas"}

# La columna que agrupa, por agrupar_por. Los nombres son fijos: ningún texto del usuario entra al SQL.
_GRUPOS = {
    "tienda": "v.tienda_id || ' ' || v.tienda",
    "producto": "v.sku || ' ' || v.producto",
    "categoria": "v.categoria",
}

_COLUMNAS_FILA = (
    "v.tienda_id, v.tienda, v.sku, v.producto, v.categoria, v.perecedero, v.unidad_venta, v.existencia, "
    "v.punto_reorden, v.fecha_caducidad_lote_proximo"
)


# ------------------------------------------------------------------ fórmula de cobertura

def extremos_cobertura(existencia: float, media: float, desviacion: float, z: float) -> tuple[float, float]:
    """Los días que alcanza la existencia en el escenario rápido y en el lento (D23-D).

    Resuelve media·d + z·σ·√d = existencia (desde) y media·d − z·σ·√d = existencia (hasta), con x = √d.
    La raíz baja se escribe como 2·E / (raíz + b), que no pierde cifras al restar. Sin existencia, los dos
    extremos son 0. Sin venta en el mes base no hay rango: es el error con tipo division_entre_cero.
    """
    if media <= 0:
        raise ErrorHerramienta("division_entre_cero", "Sin venta en el mes base la cobertura no se puede estimar")
    if existencia <= 0:
        return 0.0, 0.0
    b = z * desviacion
    raiz = math.sqrt(b * b + 4 * media * existencia)
    x_desde = 2 * existencia / (raiz + b)
    x_hasta = (raiz + b) / (2 * media)
    return x_desde * x_desde, x_hasta * x_hasta


@dataclass(frozen=True)
class Cobertura:
    """Una fila del inventario con su rango. media y desviacion se quedan aquí: no llegan al modelo."""

    tienda_id: str
    tienda: str
    sku: str
    producto: str
    unidad_venta: str
    existencia: int
    caducidad: dt.date | None
    media: float
    desviacion: float
    desde: float
    hasta: float


def _meses_de_cobertura(s: Supuestos) -> list[int]:
    """Los meses de las varianzas y el mes base; un mes fuera del corte de ventas es un error de configuración."""
    meses = sorted(set(s.cobertura.pesos_varianza) | {s.cobertura.mes_base})
    anio = s.cortes.ventas.hasta.year
    for mes in meses:
        inicio = dt.date(anio, mes, 1)
        fin = dt.date(anio, mes, calendar.monthrange(anio, mes)[1])
        if inicio < s.cortes.ventas.desde or fin > s.cortes.ventas.hasta:
            raise ValueError(f"el mes {mes} de cobertura en config/supuestos.yaml queda fuera del corte de ventas")
    return meses


def _pares_de_sustitucion(catalogos: Catalogos | None, con: duckdb.DuckDBPyConnection, s: Supuestos) -> tuple[list[str], list[str]]:
    """(sku del inventario, sku cuya venta se le suma) para cada SKU con cadena de sustitución."""
    if catalogos is None:
        filas = consultar(con, "SELECT sku, sku_sustituto FROM catalogo")
        sustitutos = {f["sku"]: f["sku_sustituto"] or None for f in filas}
    else:
        sustitutos = {p.sku: p.sku_sustituto for p in catalogos.productos.values()}
    propios: list[str] = []
    ajenos: list[str] = []
    for sku in sorted(sustitutos):
        for equivalente in sorted(skus_equivalentes(sku, sustitutos, s) - {sku}):
            propios.append(sku)
            ajenos.append(equivalente)
    return propios, ajenos


def _sql_cobertura(filtro: str, s: Supuestos) -> str:
    return f"""
WITH inv AS (
    SELECT v.tienda_id, v.tienda, v.sku, v.producto, v.unidad_venta, v.existencia, v.fecha_caducidad_lote_proximo
    FROM v_inventario v
    WHERE {filtro}
),
familia AS (
    SELECT sku, sku AS sku_venta FROM inv
    UNION
    SELECT unnest(?::VARCHAR[]) AS sku, unnest(?::VARCHAR[]) AS sku_venta
),
dias AS (
    SELECT CAST(d AS DATE) AS dia FROM generate_series(?::DATE, ?::DATE, INTERVAL 1 DAY) AS g(d)
    WHERE list_contains(?::INTEGER[], month(d))
),
venta_dia AS (
    SELECT x.tienda_id, x.sku, x.fecha, sum(x.unidades) AS unidades
    FROM ventas x
    WHERE {s.ventas.condicion('x')}
    GROUP BY x.tienda_id, x.sku, x.fecha
),
serie AS (
    SELECT i.tienda_id, i.sku, d.dia, coalesce(sum(vd.unidades), 0) AS unidades
    FROM (SELECT DISTINCT tienda_id, sku FROM inv) i
    JOIN (SELECT DISTINCT sku, sku_venta FROM familia) f ON f.sku = i.sku
    CROSS JOIN dias d
    LEFT JOIN venta_dia vd ON vd.tienda_id = i.tienda_id AND vd.sku = f.sku_venta AND vd.fecha = d.dia
    GROUP BY i.tienda_id, i.sku, d.dia
),
por_mes AS (
    SELECT tienda_id, sku, month(dia) AS mes, sum(unidades) AS unidades, var_samp(unidades) AS varianza
    FROM serie
    GROUP BY tienda_id, sku, month(dia)
),
pesos AS (
    SELECT unnest(?::INTEGER[]) AS mes, unnest(?::DOUBLE[]) AS peso
)
SELECT i.tienda_id, i.tienda, i.sku, i.producto, i.unidad_venta, i.existencia, i.fecha_caducidad_lote_proximo,
       sum(m.unidades) FILTER (WHERE m.mes = ?) AS unidades_mes_base,
       sum(m.varianza * p.peso) AS varianza_ponderada
FROM inv i
JOIN por_mes m ON m.tienda_id = i.tienda_id AND m.sku = i.sku
LEFT JOIN pesos p ON p.mes = m.mes
GROUP BY ALL
ORDER BY i.tienda_id, i.sku
""".strip()


def _consulta_cobertura(
    con: duckdb.DuckDBPyConnection,
    filtro: str,
    parametros_filtro: Sequence[Any],
    s: Supuestos,
    catalogos: Catalogos | None = None,
) -> tuple[str, list[Any], list[dict[str, Any]]]:
    meses = _meses_de_cobertura(s)
    anio = s.cortes.ventas.hasta.year
    inicio = dt.date(anio, meses[0], 1)
    fin = dt.date(anio, meses[-1], calendar.monthrange(anio, meses[-1])[1])
    propios, ajenos = _pares_de_sustitucion(catalogos, con, s)
    pesos = sorted(s.cobertura.pesos_varianza.items())
    sql = _sql_cobertura(filtro, s)
    parametros = [
        *parametros_filtro,
        propios, ajenos,
        inicio, fin, meses,
        [mes for mes, _ in pesos], [peso for _, peso in pesos],
        s.cobertura.mes_base,
    ]
    return sql, parametros, consultar(con, sql, parametros)


def _a_cobertura(fila: dict[str, Any], s: Supuestos) -> Cobertura | None:
    """El rango de una fila; None si no vendió en el mes base y no hay de dónde estimarlo."""
    media = s.media_diaria_base(float(fila["unidades_mes_base"] or 0))
    if media <= 0:
        return None
    desviacion = math.sqrt(max(float(fila["varianza_ponderada"] or 0), 0.0))
    desde, hasta = extremos_cobertura(fila["existencia"], media, desviacion, s.cobertura.z)
    return Cobertura(
        tienda_id=fila["tienda_id"], tienda=fila["tienda"], sku=fila["sku"], producto=fila["producto"],
        unidad_venta=fila["unidad_venta"], existencia=fila["existencia"],
        caducidad=fila["fecha_caducidad_lote_proximo"], media=media, desviacion=desviacion,
        desde=desde, hasta=hasta,
    )


def coberturas(con: duckdb.DuckDBPyConnection, s: Supuestos | None = None) -> list[Cobertura]:
    """El rango de cada fila del inventario que vendió en el mes base, con la media y la desviación.

    Sirve para revisar el método en las 480 filas (D23); la herramienta no entrega media ni desviación.
    """
    s = s or supuestos()
    _, _, filas = _consulta_cobertura(con, "TRUE", [], s)
    return [c for c in (_a_cobertura(fila, s) for fila in filas) if c is not None]


# ------------------------------------------------------------------ textos

def _fecha_corte(s: Supuestos) -> str:
    return fecha_larga(s.cortes.inventario.fecha)


def _al_corte(s: Supuestos) -> str:
    return f"al {_fecha_corte(s)}, el último corte; la de hoy no se conoce"


def _definicion_cobertura(s: Supuestos) -> str:
    c = s.cobertura
    mes_base = MESES_NOMBRE[c.mes_base - 1]
    pesos = ", ".join(f"{MESES_NOMBRE[mes - 1]} {num(peso * 100, 0)}%" for mes, peso in sorted(c.pesos_varianza.items()))
    devoluciones = "devoluciones descontadas" if s.ventas.incluir_devoluciones else "sin restar devoluciones"
    canales = " y ".join(s.ventas.canales)
    return (
        f"{c.probabilidad} escenarios: media·d ± {c.z:g}·σ·√d = existencia. "
        f"media: venta de {mes_base} / {s.dias_mes_base()} días, {canales}, {devoluciones}, por SKU. "
        f"σ: varianzas muestrales diarias contra la media de su mes, {pesos}. "
        "Sin pedidos ni caducidad"
    )


def _definicion_reorden(s: Supuestos) -> str:
    operador = "menor que" if s.inventario.operador_reorden == "<" else "menor o igual que"
    return f"casos producto-tienda con existencia {operador} el punto de reorden {_al_corte(s)}"


def _definicion_valuacion(s: Supuestos) -> str:
    return (
        f"existencia {_al_corte(s)} × costo de adquisición del catálogo (sin fecha) y × precio de lista sin descuento"
    )


def _definicion_existencia(s: Supuestos) -> str:
    return f"existencia en inventario {_al_corte(s)}"


def _unidad(unidad_venta: str) -> str:
    return _UNIDADES.get(unidad_venta, unidad_venta)


def _tienda(fila: dict[str, Any]) -> str:
    return f"{fila['tienda_id']} {fila['tienda']}"


def _con_parametros(sql: str, parametros: Sequence[Any]) -> str:
    """La consulta y, en su última línea, los parámetros en JSON: así se re-ejecuta tal cual."""
    return f"{sql}\n-- parámetros: {dumps(list(parametros), compacto=True)}"


# ------------------------------------------------------------------ filtros

@dataclass
class _Filtros:
    tiendas: list[str]
    skus: list[str] | None
    categoria: str | None
    notas: list[str]
    descontinuado: str | None = None

    def sql(self) -> tuple[str, list[Any]]:
        """La condición del WHERE sobre v_inventario y sus parámetros."""
        condiciones = ["list_contains(?::VARCHAR[], v.tienda_id)"]
        parametros: list[Any] = [self.tiendas]
        if self.skus is not None:
            condiciones.append("list_contains(?::VARCHAR[], v.sku)")
            parametros.append(self.skus)
        if self.categoria is not None:
            condiciones.append("v.categoria = ?")
            parametros.append(self.categoria)
        return " AND ".join(condiciones), parametros

    def como_dict(self, metrica: str, agrupar_por: str | None) -> dict[str, Any]:
        filtros: dict[str, Any] = {"metrica": metrica, "tiendas": self.tiendas}
        if self.skus is not None:
            filtros["skus"] = self.skus
        if self.categoria is not None:
            filtros["categoria"] = self.categoria
        if agrupar_por:
            filtros["agrupar_por"] = agrupar_por
        return filtros


def _skus_explicitos(sku: str, catalogos: Catalogos, s: Supuestos) -> tuple[list[str], list[str]]:
    if sku not in catalogos.productos:
        raise EntidadInexistente("producto", sku)
    sustitutos = {p.sku: p.sku_sustituto for p in catalogos.productos.values()}
    skus = sorted(skus_equivalentes(sku, sustitutos, s))
    otros = [f"{catalogos.productos[otro].nombre} ({otro})" for otro in skus if otro != sku]
    notas = [f"{sku} se suma con su cadena de sustitución: {', '.join(otros)}"] if otros else []
    return skus, notas


_ES_DE_SU_SUSTITUTO = "la existencia es de su sustituto, "


def _aviso_de_descontinuado(entrada: EntradaInventario, skus: list[str] | None, catalogos: Catalogos) -> str | None:
    """Que el SKU pedido se dio de baja, desde cuándo y de quién es la existencia; None si no pidieron uno así (A4 de T5.11).

    Un SKU descontinuado ya no tiene inventario (diccionario de datos): la fila es de su sustituto, y sin esto la cifra se
    lee como del SKU pedido. Solo aplica si lo pidieron por su SKU: por el nombre, la nota de la sustitución ya lo dice.
    """
    pedido = entrada.sku or (entrada.producto or "").strip().upper()
    if skus is None or not _PATRON_SKU.fullmatch(pedido):
        return None
    producto = catalogos.productos.get(pedido)
    if producto is None or producto.fecha_baja is None or producto.sku_sustituto not in skus:
        return None
    sustituto = catalogos.productos[producto.sku_sustituto]
    return (
        f"{producto.sku} ({producto.nombre}) está descontinuado desde el {fecha_larga(producto.fecha_baja)} y ya no tiene "
        f"inventario; {_ES_DE_SU_SUSTITUTO}{sustituto.nombre} ({sustituto.sku})"
    )


def _resolver(entrada: EntradaInventario, catalogos: Catalogos, s: Supuestos) -> _Filtros:
    tiendas = resolver_tiendas(entrada.tiendas, catalogos)
    notas = list(tiendas.notas)
    skus: list[str] | None = None
    if entrada.sku:
        skus, notas_sku = _skus_explicitos(entrada.sku, catalogos, s)
        notas += notas_sku
        if entrada.producto:
            notas.append(f"se usó el SKU {entrada.sku}; el texto del producto no se usó")
    elif entrada.producto:
        productos = resolver_productos(entrada.producto, catalogos, s)
        if productos is not None:
            skus = productos.skus
            notas += productos.notas
    categoria = resolver_categoria(entrada.categoria, catalogos)
    descontinuado = _aviso_de_descontinuado(entrada, skus, catalogos)
    if descontinuado:
        # El aviso dice de quién es la existencia: «se suma con su sustituto» ya no aporta y se leería como dos cifras.
        notas = [descontinuado, *(nota for nota in notas if "se suma" not in nota)]
    return _Filtros(
        tiendas=tiendas_validas(tiendas.ids), skus=skus, categoria=categoria, notas=notas, descontinuado=descontinuado
    )


def _limite(entrada: EntradaInventario, todas_por_omision: bool) -> int:
    """El limite pedido; sin pedirlo, todas las filas (hasta 50) cuando la lista es la respuesta."""
    if todas_por_omision and "limite" not in entrada.model_fields_set:
        return MAX_FILAS
    return entrada.limite


def _nota_de_recorte(mostradas: int, total: int) -> list[str]:
    return [f"se muestran {mostradas} de {total} filas"] if mostradas < total else []


# ------------------------------------------------------------------ métricas

def _totales_de_existencia(totales: dict[str, int]) -> tuple[str, dict[str, str]]:
    """La unidad y la cifra del total; kilos y piezas no se suman entre sí."""
    if len(totales) == 1:
        unidad = next(iter(totales))
        return unidad, {"existencia": entero(totales[unidad])}
    unidad = f"{' y '.join(sorted(totales))} (no se suman entre sí)"
    return unidad, {f"existencia_{u}": entero(total) for u, total in sorted(totales.items())}


def _existencia(con, entrada: EntradaInventario, filtros: _Filtros, s: Supuestos, base: dict[str, Any]) -> Resultado:
    condicion, parametros = filtros.sql()
    if entrada.agrupar_por:
        return _existencia_agrupada(con, entrada, condicion, parametros, s, base)
    sentido = "DESC" if entrada.orden == "mayor" else "ASC"
    sql = (f"SELECT {_COLUMNAS_FILA} FROM v_inventario v WHERE {condicion} "
           f"ORDER BY v.existencia {sentido}, v.tienda_id, v.sku")
    filas = consultar(con, sql, parametros)
    base["consulta"] = _con_parametros(sql, parametros)
    if not filas:
        return Resultado(**base, estado="sin_resultados", resumen="No hay filas de inventario con esos filtros")

    totales: dict[str, int] = {}
    for fila in filas:
        unidad = _unidad(fila["unidad_venta"])
        totales[unidad] = totales.get(unidad, 0) + fila["existencia"]
    notas = list(base.pop("notas"))
    if len(filas) == 1:
        return _existencia_de_una_fila(filas[0], s, base, notas)

    unidad, cifras = _totales_de_existencia(totales)
    limite = _limite(entrada, todas_por_omision=False)
    mostradas = [_fila_existencia(fila) for fila in filas[:limite]]
    bajo = sum(1 for fila in filas if s.inventario.bajo_reorden(fila["existencia"], fila["punto_reorden"]))
    if bajo:
        notas.append(f"{bajo} de {len(filas)} están debajo de su punto de reorden")
    notas += _nota_de_recorte(len(mostradas), len(filas))
    return Resultado(**base, unidad=unidad, cifras=cifras, filas=mostradas, n_filas=len(filas), notas=notas)


def _fila_existencia(fila: dict[str, Any]) -> dict[str, str | None]:
    caducidad = fila["fecha_caducidad_lote_proximo"]
    return {
        "tienda": _tienda(fila),
        "sku": fila["sku"],
        "producto": fila["producto"],
        "existencia": entero(fila["existencia"]),
        "unidad": _unidad(fila["unidad_venta"]),
        "punto_reorden": entero(fila["punto_reorden"]),
        "caducidad_lote": caducidad.isoformat() if caducidad else "sin fecha",
    }


def _nota_sin_caducidad(fila: dict[str, Any]) -> str:
    motivo = "el catálogo lo marca como no perecedero" if not fila["perecedero"] else "el inventario no trae la fecha"
    return f"no tiene fecha de caducidad registrada; {motivo}"


def _existencia_de_una_fila(fila: dict[str, Any], s: Supuestos, base: dict[str, Any], notas: list[str]) -> Resultado:
    cifras = {"existencia": entero(fila["existencia"]), "punto_reorden": entero(fila["punto_reorden"])}
    caducidad = fila["fecha_caducidad_lote_proximo"]
    if caducidad:
        cifras["caducidad_lote"] = caducidad.isoformat()
    else:
        notas.append(_nota_sin_caducidad(fila))
    if s.inventario.bajo_reorden(fila["existencia"], fila["punto_reorden"]):
        notas.append(f"está debajo de su punto de reorden ({entero(fila['punto_reorden'])})")
    resumen = f"{fila['producto']} en {fila['tienda']}"
    return Resultado(**base, resumen=resumen, unidad=_unidad(fila["unidad_venta"]), cifras=cifras, n_filas=1, notas=notas)


def _existencia_agrupada(con, entrada, condicion, parametros, s, base) -> Resultado:
    grupo = _GRUPOS[entrada.agrupar_por]
    sentido = "DESC" if entrada.orden == "mayor" else "ASC"
    sql = (f"SELECT {grupo} AS grupo, v.unidad_venta, sum(v.existencia) AS existencia, count(*) AS filas "
           f"FROM v_inventario v WHERE {condicion} GROUP BY ALL ORDER BY existencia {sentido}, grupo, v.unidad_venta")
    filas = consultar(con, sql, parametros)
    base["consulta"] = _con_parametros(sql, parametros)
    if not filas:
        return Resultado(**base, estado="sin_resultados", resumen="No hay filas de inventario con esos filtros")
    notas = list(base.pop("notas"))
    limite = _limite(entrada, todas_por_omision=False)
    mostradas = [
        {entrada.agrupar_por: f["grupo"], "unidad": _unidad(f["unidad_venta"]), "existencia": entero(f["existencia"])}
        for f in filas[:limite]
    ]
    notas += _nota_de_recorte(len(mostradas), len(filas))
    totales: dict[str, int] = {}
    for f in filas:
        unidad_de_fila = _unidad(f["unidad_venta"])
        totales[unidad_de_fila] = totales.get(unidad_de_fila, 0) + f["existencia"]
    unidad, cifras = _totales_de_existencia(totales)
    return Resultado(**base, unidad=unidad, cifras=cifras, filas=mostradas, n_filas=len(filas), notas=notas)


def _bajo_reorden(con, entrada: EntradaInventario, filtros: _Filtros, s: Supuestos, base: dict[str, Any]) -> Resultado:
    condicion, parametros = filtros.sql()
    sentido = "DESC" if entrada.orden == "mayor" else "ASC"
    sql = (f"SELECT {_COLUMNAS_FILA}, v.punto_reorden - v.existencia AS faltante FROM v_inventario v "
           f"WHERE {condicion} AND {s.inventario.condicion('v')} "
           f"ORDER BY v.tienda_id, faltante {sentido}, v.sku")
    filas = consultar(con, sql, parametros)
    base["consulta"] = _con_parametros(sql, parametros)
    base["definicion"] = _definicion_reorden(s)
    notas = list(base.pop("notas"))

    cifras = {"casos": entero(len(filas))}
    agrupar = entrada.agrupar_por or "tienda"
    por_grupo: dict[str, int] = {}
    for fila in filas:
        clave = _clave_de_grupo(fila, agrupar)
        por_grupo[clave] = por_grupo.get(clave, 0) + 1
    if len(por_grupo) > 1 or entrada.agrupar_por:
        cifras |= {clave: entero(cuantos) for clave, cuantos in sorted(por_grupo.items())}
    limite = _limite(entrada, todas_por_omision=True)
    mostradas = [
        {"tienda": _tienda(f), "sku": f["sku"], "producto": f["producto"], "existencia": entero(f["existencia"]),
         "punto_reorden": entero(f["punto_reorden"]), "unidad": _unidad(f["unidad_venta"])}
        for f in filas[:limite]
    ]
    notas += _nota_de_recorte(len(mostradas), len(filas))
    return Resultado(**base, unidad="casos producto-tienda", cifras=cifras, filas=mostradas, n_filas=len(filas), notas=notas)


def _clave_de_grupo(fila: dict[str, Any], agrupar: str) -> str:
    if agrupar == "producto":
        return f"{fila['sku']} {fila['producto']}"
    if agrupar == "categoria":
        return fila["categoria"]
    return _tienda(fila)


def _valuacion(con, entrada: EntradaInventario, filtros: _Filtros, s: Supuestos, base: dict[str, Any]) -> Resultado:
    condicion, parametros = filtros.sql()
    base["definicion"] = _definicion_valuacion(s)
    base["fuente"] = f"{FUENTE_INVENTARIO} y {FUENTE_CATALOGO}"
    total_sql = (
        "SELECT sum(v.existencia * c.costo_mxn) AS a_costo, sum(v.existencia * c.precio_lista_mxn) AS a_precio "
        f"FROM v_inventario v JOIN catalogo c ON c.sku = v.sku WHERE {condicion}"
    )
    total = consultar(con, total_sql, parametros)[0]
    base["consulta"] = _con_parametros(total_sql, parametros)
    if total["a_costo"] is None:
        return Resultado(**base, estado="sin_resultados", resumen="No hay filas de inventario con esos filtros")
    cifras = {"a_costo": num(total["a_costo"]), "a_precio": num(total["a_precio"])}
    notas = list(base.pop("notas"))
    if not entrada.agrupar_por:
        return Resultado(**base, unidad="MXN", cifras=cifras, n_filas=1, notas=notas)

    sentido = "DESC" if entrada.orden == "mayor" else "ASC"
    sql = (
        f"SELECT {_GRUPOS[entrada.agrupar_por]} AS grupo, sum(v.existencia * c.costo_mxn) AS a_costo, "
        "sum(v.existencia * c.precio_lista_mxn) AS a_precio "
        f"FROM v_inventario v JOIN catalogo c ON c.sku = v.sku WHERE {condicion} "
        f"GROUP BY ALL ORDER BY a_costo {sentido}, grupo"
    )
    filas = consultar(con, sql, parametros)
    base["consulta"] = f"{base['consulta']}\n{_con_parametros(sql, parametros)}"
    limite = _limite(entrada, todas_por_omision=False)
    mostradas = [
        {entrada.agrupar_por: f["grupo"], "a_costo": num(f["a_costo"]), "a_precio": num(f["a_precio"])}
        for f in filas[:limite]
    ]
    notas += _nota_de_recorte(len(mostradas), len(filas))
    return Resultado(**base, unidad="MXN", cifras=cifras, filas=mostradas, n_filas=len(filas), notas=notas)


def _cobertura(con, entrada: EntradaInventario, filtros: _Filtros, s: Supuestos, base: dict[str, Any],
               catalogos: Catalogos) -> Resultado:
    condicion, parametros_filtro = filtros.sql()
    sql, parametros, filas = _consulta_cobertura(con, condicion, parametros_filtro, s, catalogos)
    base["consulta"] = _con_parametros(sql, parametros)
    base["definicion"] = _definicion_cobertura(s)
    base["fuente"] = f"{FUENTE_INVENTARIO} y {FUENTE_VENTAS}"
    notas = list(base.pop("notas"))
    if entrada.agrupar_por:
        notas.append("la cobertura se da por producto y tienda; no se agrupa")
    if not filas:
        return Resultado(**base, estado="sin_resultados", resumen="No hay filas de inventario con esos filtros", notas=notas)

    calculadas = [_a_cobertura(fila, s) for fila in filas]
    validas = [c for c in calculadas if c is not None]
    sin_venta = [fila for fila, c in zip(filas, calculadas) if c is None]
    if not validas:
        raise ErrorHerramienta(
            "division_entre_cero",
            f"Sin venta en {MESES_NOMBRE[s.cobertura.mes_base - 1]} la cobertura no se puede estimar",
            productos=[f"{f['sku']} {f['producto']}" for f in sin_venta[:8]],
        )
    if sin_venta:
        notas.append(f"{len(sin_venta)} filas no vendieron en {MESES_NOMBRE[s.cobertura.mes_base - 1]} y quedan fuera")

    if len(validas) == 1:
        return _cobertura_de_una_fila(validas[0], s, base, notas, catalogos)

    validas.sort(key=lambda c: (c.desde, c.tienda_id, c.sku), reverse=entrada.orden == "mayor")
    limite = _limite(entrada, todas_por_omision=False)
    mostradas = [_fila_cobertura(c) for c in validas[:limite]]
    caducan = sum(1 for c in validas if _caduca_antes(c, s))
    if caducan:
        notas.append(f"en {caducan} filas el lote más próximo caduca antes del fin del rango")
    notas += _nota_de_recorte(len(mostradas), len(validas))
    return Resultado(**base, unidad="días", filas=mostradas, n_filas=len(validas), notas=notas)


def _fin_del_rango(c: Cobertura, s: Supuestos) -> dt.date:
    return s.cortes.inventario.fecha + dt.timedelta(days=c.hasta)


def _caduca_antes(c: Cobertura, s: Supuestos) -> bool:
    if c.caducidad is None:
        return False
    return c.caducidad <= _fin_del_rango(c, s)


def _nota_de_retiro(c: Cobertura, catalogos: Catalogos, s: Supuestos) -> list[str]:
    """El día de retiro del lote si llega antes del fin del rango (P12). La regla es la de caducidades.py, no otra."""
    if c.caducidad is None:
        return []
    retiro = calendario(catalogos.productos[c.sku].categoria, c.caducidad).fecha_retiro
    if retiro > _fin_del_rango(c, s):
        return []
    return [f"retiro el {retiro.day} de {MESES_NOMBRE[retiro.month - 1]}"]


def _fila_cobertura(c: Cobertura) -> dict[str, str | None]:
    fila: dict[str, str | None] = {
        "tienda": f"{c.tienda_id} {c.tienda}",
        "sku": c.sku,
        "producto": c.producto,
        "existencia": entero(c.existencia),
        "dias_desde": num(c.desde),
        "dias_hasta": num(c.hasta),
    }
    if c.caducidad:
        fila["caducidad_lote"] = c.caducidad.isoformat()
    return fila


def _cobertura_de_una_fila(c: Cobertura, s: Supuestos, base: dict[str, Any], notas: list[str],
                           catalogos: Catalogos) -> Resultado:
    desde, hasta = num(c.desde), num(c.hasta)
    cifras = {"dias_desde": desde, "dias_hasta": hasta, "existencia": entero(c.existencia)}
    if c.caducidad:
        cifras["caducidad_lote"] = c.caducidad.isoformat()
    if _caduca_antes(c, s):
        notas.append("el lote más próximo caduca antes del fin del rango")
    notas += _nota_de_retiro(c, catalogos, s)
    resumen = f"{s.cobertura.probabilidad} veces alcanzaría entre {desde} y {hasta} días"
    return Resultado(**base, resumen=resumen, unidad="días", cifras=cifras, n_filas=1, notas=notas)


# ------------------------------------------------------------------ entrada de la herramienta

def inventario(con: duckdb.DuckDBPyConnection, entrada: EntradaInventario, s: Supuestos | None = None) -> Resultado:
    """Existencia, casos bajo el punto de reorden, valuación o cobertura al corte del inventario.

    con es el cursor de la llamada (Consultas.sesion()). s permite probar otra configuración; sin él se
    usa config/supuestos.yaml. Un error con tipo (métrica no calculable, tienda inexistente o ambigua)
    vuelve como Resultado con su estado; un error sin tipo se deja pasar.
    """
    s = s or supuestos()
    filtros_pedidos = entrada.model_dump(mode="json", exclude_defaults=True)
    return ejecutar(HERRAMIENTA, lambda: _calcular(con, entrada, s), filtros_pedidos)


def _calcular(con: duckdb.DuckDBPyConnection, entrada: EntradaInventario, s: Supuestos) -> Resultado:
    verificar_metrica(entrada.metrica, s)
    catalogos = cargar_catalogos(con)
    filtros = _resolver(entrada, catalogos, s)
    base: dict[str, Any] = {
        "herramienta": HERRAMIENTA,
        "definicion": _definicion_existencia(s),
        "filtros": filtros.como_dict(entrada.metrica, entrada.agrupar_por),
        "corte": s.cortes.inventario.fecha,
        "fuente": FUENTE_INVENTARIO,
        "notas": filtros.notas,
    }
    resultado = _por_metrica(con, entrada, filtros, s, base, catalogos)
    # Solo en existencia: el resumen de la cobertura se copia tal cual y no lleva nada delante.
    if filtros.descontinuado and resultado.estado == "ok" and entrada.metrica == "existencia":
        cifras = {**resultado.cifras, "existencia_es_de": filtros.descontinuado.partition(_ES_DE_SU_SUSTITUTO)[2]}
        return resultado.model_copy(update={"resumen": f"{filtros.descontinuado}. {resultado.resumen}".strip(), "cifras": cifras})
    return resultado


def _por_metrica(con, entrada: EntradaInventario, filtros: _Filtros, s: Supuestos, base: dict[str, Any],
                 catalogos: Catalogos) -> Resultado:
    if entrada.metrica == "bajo_reorden":
        return _bajo_reorden(con, entrada, filtros, s, base)
    if entrada.metrica == "valuacion":
        return _valuacion(con, entrada, filtros, s, base)
    if entrada.metrica == "cobertura":
        return _cobertura(con, entrada, filtros, s, base, catalogos)
    return _existencia(con, entrada, filtros, s, base)
