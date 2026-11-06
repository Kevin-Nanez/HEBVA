"""Herramienta de ventas: totales, rankings, devoluciones, venta por m², margen y comparaciones (T2.3).

Solo lectura sobre v_ventas_dia, con entrada validada (EntradaVentas de comun.py) y error con tipo.

Qué filas entran a la venta. La regla vive en config/supuestos.yaml y la condición SQL la da
agente/definiciones.py: supuestos().ventas.condicion("v") para la venta neta y condicion_devolucion("v")
para las devoluciones. Este módulo no escribe esa regla; la toma del Supuestos de la llamada, así que
cambiar incluir_devoluciones en el YAML cambia la cifra sin tocar el código (C1.4). Por eso no se usan
las columnas booleanas que trae la vista, que se calcularon con el YAML del día de la construcción.

Lo que nunca se suma. Kilos con piezas o paquetes (catalogo_unidad, C1.4): las unidades van siempre por
unidad de venta y un ranking en unidades ordena cada unidad por separado. Las devoluciones también.

El producto. Un SKU con sustituto se cuenta junto con su sustituto (sumar_sustitutos): en un ranking por
producto la familia es una fila, y con un producto pedido las filas desglosan cada SKU para que la
respuesta diga qué presentación se vendió en cada fecha.

Comparación. comparar_con es otro periodo con los mismos filtros. La variación va siempre del periodo
anterior al posterior, y también se da por día de calendario, porque los meses no tienen los mismos días. Por mes
o por semana sin comparar_con, detalle["para_comparar"] le dice al modelo cómo pedir la variación.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import duckdb

from agente.definiciones import MESES_NOMBRE, Supuestos, skus_equivalentes, supuestos
from agente.herramientas.comun import (
    EntradaVentas,
    ErrorHerramienta,
    Filtro,
    Resultado,
    consultar,
    corte_de,
    dividir,
    ejecutar,
    entero,
    num,
    pct,
    tiendas_validas,
)
from agente.herramientas.errores import verificar_canal, verificar_metrica
from agente.resolver import (
    Catalogos,
    Periodo,
    cargar_catalogos,
    resolver_categoria,
    resolver_periodo,
    resolver_productos,
    resolver_tiendas,
)

HERRAMIENTA = "ventas"
FUENTE = "ventas.csv"
FUENTE_DE_DEVOLUCIONES = f"{FUENTE}, filas con unidades negativas"

# Unidad de venta de catalogo_unidad -> sufijo de la cifra y nombre para el modelo.
UNIDADES = {"kg": ("kg", "kg"), "pieza": ("piezas", "piezas o paquetes")}
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_CONTENIDO_EN_EL_NOMBRE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?) (L|ml)\b")
DECIMALES_DEVOLUCION = 3
# La celda de cada fila con su parte de la venta del total de la consulta.
PARTE_DEL_TOTAL = "pct_del_total"

_CLAVES_TIENDA = (("v.tienda_id", "tienda_id"), ("v.tienda", "tienda"), ("t.m2", "m2"))
_CLAVES_PRODUCTO = (("v.sku", "sku"), ("v.producto", "producto"), ("v.unidad_venta", "unidad_venta"))
CLAVES = {
    "tienda": _CLAVES_TIENDA,
    "producto": _CLAVES_PRODUCTO,
    "categoria": (("v.categoria", "categoria"),),
    "canal": (("v.canal", "canal"),),
    "dia_semana": (("isodow(v.fecha)", "dia_semana"),),
}
GRANOS = {
    "mes": "strftime(v.fecha, '%Y-%m')",
    "semana": "CAST(date_trunc('week', v.fecha) AS DATE)",
    "dia": "v.fecha",
}

PRIMARIA = {"venta": "venta", "devoluciones": "pesos", "venta_por_m2": "venta_por_m2", "margen": "margen"}
UNIDAD_RESULTADO = {
    "venta": "pesos (MXN)",
    "devoluciones": "registros, unidades y pesos (MXN)",
    "venta_por_m2": "pesos (MXN) por m²",
    "margen": "pesos (MXN)",
}


# ------------------------------------------------------------------ consulta

def _agregados(s: Supuestos) -> str:
    """Las sumas de cada grupo. Qué fila es venta neta y cuál devolución lo dice definiciones.py."""
    neta = f"({s.ventas.condicion('v')})"
    devolucion = f"({s.ventas.condicion_devolucion('v')})"
    columnas = [
        "COUNT(*) AS filas",
        f"COALESCE(SUM(v.venta_neta_mxn) FILTER (WHERE {neta}), 0) AS venta",
        f"COALESCE(SUM(v.unidades * c.costo_mxn) FILTER (WHERE {neta}), 0) AS costo",
        f"COUNT(*) FILTER (WHERE {devolucion}) AS dev_registros",
        f"COALESCE(-SUM(v.venta_neta_mxn) FILTER (WHERE {devolucion}), 0) AS dev_pesos",
    ]
    for unidad in UNIDADES:
        de_la_unidad = f"v.unidad_venta = '{unidad}'"
        columnas += [
            f"COALESCE(SUM(v.unidades) FILTER (WHERE {neta} AND {de_la_unidad}), 0) AS unidades_{unidad}",
            f"COUNT(*) FILTER (WHERE {neta} AND {de_la_unidad}) AS filas_{unidad}",
            f"COALESCE(-SUM(v.unidades) FILTER (WHERE {devolucion} AND {de_la_unidad}), 0) AS dev_unidades_{unidad}",
            f"COUNT(*) FILTER (WHERE {devolucion} AND {de_la_unidad}) AS dev_filas_{unidad}",
        ]
    return ",\n       ".join(columnas)


def _marcas(cuantas: int) -> str:
    return ", ".join("?" for _ in range(cuantas))


@dataclass(frozen=True)
class _Alcance:
    """Lo que filtra la consulta, ya resuelto: ids de tienda validados, SKU, categoría y canal."""

    tiendas: list[str]
    skus: list[str] | None = None
    categoria: str | None = None
    canal: str | None = None


def _sql(claves: Sequence[tuple[str, str]], alcance: _Alcance, periodo: Periodo, s: Supuestos) -> tuple[str, list[Any]]:
    seleccion = [f"{expresion} AS {nombre}" for expresion, nombre in claves]
    condiciones = ["v.fecha BETWEEN ? AND ?", f"v.tienda_id IN ({_marcas(len(alcance.tiendas))})"]
    parametros: list[Any] = [periodo.desde, periodo.hasta, *alcance.tiendas]
    if alcance.skus:
        condiciones.append(f"v.sku IN ({_marcas(len(alcance.skus))})")
        parametros += alcance.skus
    if alcance.categoria:
        condiciones.append("v.categoria = ?")
        parametros.append(alcance.categoria)
    if alcance.canal:
        condiciones.append("v.canal = ?")
        parametros.append(alcance.canal)
    sql = (
        f"SELECT {', '.join([*seleccion, _agregados(s)])}\n"
        "FROM v_ventas_dia v\n"
        "JOIN tiendas t ON t.tienda_id = v.tienda_id\n"
        "JOIN catalogo c ON c.sku = v.sku\n"
        f"WHERE {' AND '.join(condiciones)}"
    )
    if claves:
        sql += f"\nGROUP BY {', '.join(expresion for expresion, _ in claves)}"
    return sql, parametros


@dataclass
class _Consultas:
    """El cursor de la llamada y el texto de cada consulta, para el registro."""

    con: duckdb.DuckDBPyConnection
    s: Supuestos
    textos: list[str] = field(default_factory=list)

    def filas(self, claves: Sequence[tuple[str, str]], alcance: _Alcance, periodo: Periodo) -> list[dict[str, Any]]:
        sql, parametros = _sql(claves, alcance, periodo, self.s)
        if sql not in self.textos:
            self.textos.append(sql)
        return consultar(self.con, sql, parametros)

    def total(self, alcance: _Alcance, periodo: Periodo) -> dict[str, Any]:
        return self.filas((), alcance, periodo)[0]


# ------------------------------------------------------------------ medidas

@dataclass(frozen=True)
class _Medida:
    valor: Any
    formato: Callable[[Any], str]

    def texto(self) -> str:
        return self.formato(self.valor)


def _pct_devolucion(valor: Any) -> str:
    return pct(valor, DECIMALES_DEVOLUCION)


def _unidades(suma: dict[str, Any], prefijo: str = "") -> dict[str, _Medida]:
    """Una cifra por unidad de venta que tuvo filas; kilos y piezas nunca se suman."""
    medidas = {}
    for unidad, (sufijo, _) in UNIDADES.items():
        if suma[f"{prefijo}filas_{unidad}"]:
            medidas[f"unidades_{sufijo}"] = _Medida(suma[f"{prefijo}unidades_{unidad}"], entero)
    return medidas


def _medidas(metrica: str, suma: dict[str, Any], con_unidades: bool = False) -> dict[str, _Medida]:
    venta = suma["venta"]
    if metrica == "unidades":
        return _unidades(suma)
    if metrica == "devoluciones":
        medidas = {"registros": _Medida(suma["dev_registros"], entero), **_unidades(suma, "dev_"),
                   "pesos": _Medida(suma["dev_pesos"], num)}
        if venta:
            medidas["pct_de_la_venta"] = _Medida(dividir(suma["dev_pesos"], venta), _pct_devolucion)
        return medidas
    if metrica == "venta_por_m2":
        return {"venta": _Medida(venta, num), "m2": _Medida(suma["m2"], entero),
                "venta_por_m2": _Medida(dividir(venta, suma["m2"]), num)}
    if metrica == "margen":
        margen = venta - suma["costo"]
        medidas = {"venta": _Medida(venta, num), "costo": _Medida(suma["costo"], num), "margen": _Medida(margen, num)}
        if venta:
            medidas["margen_pct"] = _Medida(dividir(margen, venta), pct)
        return medidas
    medidas = {"venta": _Medida(venta, num)}
    return {**medidas, **_unidades(suma)} if con_unidades else medidas


def _primaria(metrica: str, medidas: dict[str, _Medida]) -> str | None:
    """La medida que ordena el ranking; en unidades, la única unidad que haya (None si hay dos)."""
    if metrica != "unidades":
        return PRIMARIA[metrica]
    return next(iter(medidas)) if len(medidas) == 1 else None


def _textos(medidas: dict[str, _Medida]) -> dict[str, str]:
    return {nombre: medida.texto() for nombre, medida in medidas.items()}


def _sumar(sumas: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total: dict[str, Any] = {}
    for suma in sumas:
        for clave, valor in suma.items():
            total[clave] = total.get(clave, 0) + valor
    return total


_CAMPOS_DE_SUMA = ("filas", "venta", "costo", "dev_registros", "dev_pesos",
                   *(f"{prefijo}{campo}_{unidad}" for unidad in UNIDADES
                     for prefijo in ("", "dev_") for campo in ("unidades", "filas")))


def _separar(fila: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Una fila de la consulta en (claves, sumas)."""
    sumas = {campo: fila[campo] for campo in _CAMPOS_DE_SUMA if campo in fila}
    claves = {campo: valor for campo, valor in fila.items() if campo not in sumas}
    return claves, sumas


# ------------------------------------------------------------------ grupos

@dataclass
class _Grupo:
    """Una fila del resultado antes de formatear: sus celdas de clave y sus sumas."""

    orden: tuple
    celdas: dict[str, str]
    suma: dict[str, Any]
    unidad: str | None = None


def _familias(catalogos: Catalogos, s: Supuestos) -> dict[str, tuple[str, ...]]:
    """Cada SKU con su familia de sustitución (él solo si no tiene, o con sumar_sustitutos en false)."""
    sustitutos = {producto.sku: producto.sku_sustituto for producto in catalogos.productos.values()}
    familia_de: dict[str, tuple[str, ...]] = {}
    for sku in sorted(catalogos.productos):
        if sku not in familia_de:
            familia = tuple(sorted(skus_equivalentes(sku, sustitutos, s)))
            familia_de.update(dict.fromkeys(familia, familia))
    return familia_de


def _celda_periodo(valor: Any) -> str:
    return valor.isoformat() if isinstance(valor, dt.date) else str(valor)


def _grupos(filas: list[dict[str, Any]], agrupar: str | None, por_familia: dict[str, tuple[str, ...]] | None) -> list[_Grupo]:
    """Las filas de la consulta como grupos; con por_familia, los SKU de una familia se suman en uno."""
    reunidos: dict[tuple, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for fila in filas:
        claves, sumas = _separar(fila)
        llave = tuple(claves.get(c) for c in ("periodo",)) + _llave(agrupar, claves, por_familia)
        reunidos.setdefault(llave, []).append((claves, sumas))
    return [_grupo(llave, partes, agrupar) for llave, partes in reunidos.items()]


def _llave(agrupar: str | None, claves: dict[str, Any], por_familia: dict[str, tuple[str, ...]] | None) -> tuple:
    if agrupar == "producto" and por_familia is not None:
        return (por_familia.get(claves["sku"], (claves["sku"],)), claves["unidad_venta"])
    if agrupar == "producto":
        return ((claves["sku"],), claves["unidad_venta"])
    if agrupar == "tienda":
        return (claves["tienda_id"],)
    return (claves[agrupar],) if agrupar else ()


def _grupo(llave: tuple, partes: list[tuple[dict[str, Any], dict[str, Any]]], agrupar: str | None) -> _Grupo:
    claves = partes[0][0]
    suma = _sumar([sumas for _, sumas in partes])
    celdas: dict[str, str] = {}
    if "periodo" in claves:
        celdas["periodo"] = _celda_periodo(claves["periodo"])
    unidad = None
    if agrupar == "producto":
        presentes = sorted(partes, key=lambda parte: parte[0]["sku"])
        celdas["sku"] = " + ".join(c["sku"] for c, _ in presentes)
        celdas["producto"] = " + ".join(c["producto"] for c, _ in presentes)
        unidad = claves["unidad_venta"]
        celdas["unidad"] = UNIDADES[unidad][1]
    elif agrupar == "tienda":
        celdas.update(tienda_id=claves["tienda_id"], tienda=claves["tienda"])
        suma["m2"] = claves["m2"]
    elif agrupar == "dia_semana":
        celdas["dia_semana"] = DIAS_SEMANA[claves["dia_semana"] - 1]
    elif agrupar:
        celdas[agrupar] = claves[agrupar]
    return _Grupo(orden=llave, celdas=celdas, suma=suma, unidad=unidad)


def _m2(grupos: list[_Grupo]) -> int:
    """Los m² de las tiendas con venta, cada una una vez aunque el grano la repita por periodo."""
    return sum({g.celdas["tienda_id"]: g.suma["m2"] for g in grupos}.values())


def _dias_de_la_semana(periodo: Periodo) -> dict[str, int]:
    cuenta = dict.fromkeys(DIAS_SEMANA, 0)
    dia = periodo.desde
    while dia <= periodo.hasta:
        cuenta[DIAS_SEMANA[dia.weekday()]] += 1
        dia += dt.timedelta(days=1)
    return cuenta


# ------------------------------------------------------------------ armado del resultado

@dataclass
class _Pedido:
    """Todo lo que la llamada pidió, ya resuelto."""

    entrada: EntradaVentas
    alcance: _Alcance
    periodo: Periodo
    comparado: Periodo | None
    nombres_tienda: dict[str, str]
    notas: list[str]
    por_familia: dict[str, tuple[str, ...]]
    producto_pedido: bool

    @property
    def metrica(self) -> str:
        return self.entrada.metrica


def _claves(pedido: _Pedido) -> tuple[list[tuple[str, str]], str | None]:
    """Las claves de la consulta agrupada y la agrupación efectiva; ([], None) si solo va el total."""
    entrada = pedido.entrada
    agrupar = entrada.agrupar_por
    if pedido.metrica == "venta_por_m2" and agrupar != "tienda":
        if agrupar:
            pedido.notas.append(f"la venta por m² se calcula por tienda; no se agrupa por {agrupar}")
        agrupar = "tienda"
    if agrupar is None and entrada.grano == "total":
        agrupar = _desglose(pedido)
    claves: list[tuple[str, str]] = []
    if entrada.grano != "total":
        claves.append((GRANOS[entrada.grano], "periodo"))
        if entrada.grano == "semana":
            pedido.notas.append("las semanas van de lunes a domingo; la primera y la última pueden quedar incompletas")
    if agrupar:
        claves += CLAVES[agrupar]
    return claves, agrupar


def _desglose(pedido: _Pedido) -> str | None:
    """Sin agrupación, un producto de varios SKU se desglosa por SKU y un canal de varias tiendas, por tienda."""
    if pedido.producto_pedido and len(pedido.alcance.skus or []) > 1:
        return "producto"
    if pedido.alcance.canal and len(pedido.alcance.tiendas) > 1:
        return "tienda"
    return None


def _ordenar(grupos: list[_Grupo], pedido: _Pedido, agrupar: str | None) -> tuple[list[_Grupo], int]:
    """Cronológico con grano; ranking por la medida principal sin él. Devuelve los grupos y cuántos había."""
    total = len(grupos)
    if pedido.entrada.grano != "total" or agrupar is None:
        return sorted(grupos, key=lambda g: g.orden), total
    desglose = pedido.entrada.agrupar_por is None and pedido.metrica != "venta_por_m2"
    if desglose:
        return sorted(grupos, key=lambda g: g.orden), total
    if pedido.metrica == "unidades" and agrupar == "producto":
        return _ranking_por_unidad(grupos, pedido), total
    primaria = _primaria(pedido.metrica, _medidas(pedido.metrica, _sumar([g.suma for g in grupos])))
    if primaria is None:
        pedido.notas.append("kilos y piezas no se suman: cada fila trae las dos cifras y no se ordena por unidades")
        return sorted(grupos, key=lambda g: g.orden), total
    ordenados = _rankear(grupos, pedido, primaria)
    return ordenados[: pedido.entrada.limite], total


def _valor(grupo: _Grupo, pedido: _Pedido, primaria: str) -> Any:
    medida = _medidas(pedido.metrica, grupo.suma).get(primaria)
    return medida.valor if medida else 0


def _rankear(grupos: list[_Grupo], pedido: _Pedido, primaria: str) -> list[_Grupo]:
    mayor = pedido.entrada.orden == "mayor"
    return sorted(grupos, key=lambda g: (_valor(g, pedido, primaria), g.celdas.get("sku", "")), reverse=mayor)


def _ranking_por_unidad(grupos: list[_Grupo], pedido: _Pedido) -> list[_Grupo]:
    """Cada unidad de venta con su propio ranking: los kilos no se comparan con las piezas."""
    elegidos: list[_Grupo] = []
    rankings = {}
    for unidad in UNIDADES:
        de_la_unidad = [g for g in grupos if g.unidad == unidad]
        if de_la_unidad:
            rankings[unidad] = _rankear(de_la_unidad, pedido, f"unidades_{UNIDADES[unidad][0]}")[: pedido.entrada.limite]
    # Primero la unidad cuyo primer lugar vendió más, sin convertir kilos en piezas: solo decide el orden.
    presentes = sorted(rankings, key=lambda unidad: _valor(rankings[unidad][0], pedido, f"unidades_{UNIDADES[unidad][0]}"),
                       reverse=pedido.entrada.orden == "mayor")
    for unidad in presentes:
        elegidos += rankings[unidad]
    if len(presentes) > 1:
        pedido.notas.append(
            f"las unidades se ordenan por separado: primero {UNIDADES[presentes[0]][1]} y después "
            f"{UNIDADES[presentes[1]][1]}; kilos y piezas no se comparan"
        )
    return elegidos


def _celdas_de_medidas(grupo: _Grupo, pedido: _Pedido, agrupar: str | None) -> dict[str, str]:
    con_unidades = agrupar == "producto" and pedido.metrica == "venta"
    medidas = _medidas(pedido.metrica, grupo.suma, con_unidades=con_unidades)
    if grupo.unidad:
        # En una fila de producto la unidad ya va en su celda: la cifra se llama unidades a secas.
        sufijo = f"unidades_{UNIDADES[grupo.unidad][0]}"
        medidas = {("unidades" if nombre == sufijo else nombre): medida for nombre, medida in medidas.items()}
    celdas = _textos(medidas)
    if agrupar == "dia_semana":
        dias = _dias_de_la_semana(pedido.periodo)[grupo.celdas["dia_semana"]]
        celdas["dias"] = entero(dias)
        primaria = _primaria(pedido.metrica, medidas)
        if primaria and dias:
            celdas["promedio_por_dia"] = num(dividir(medidas[primaria].valor, dias))
    return celdas


def _filas(grupos: list[_Grupo], pedido: _Pedido, agrupar: str | None) -> list[dict[str, str]]:
    filas = []
    for grupo in grupos:
        celdas = dict(grupo.celdas)
        if agrupar == "dia_semana":
            celdas = {"dia_semana": celdas.pop("dia_semana"), **celdas}
        filas.append({**celdas, **_celdas_de_medidas(grupo, pedido, agrupar)})
    return filas


def _nota_de_familias(grupos: list[_Grupo]) -> str:
    unidos = [g.celdas["sku"] for g in grupos if " + " in g.celdas.get("sku", "")]
    if not unidos:
        return ""
    return f"se suma cada SKU con el que lo sustituye: {', '.join(unidos)}"


# ------------------------------------------------------------------ cifras del total

def _cifras_del_total(total: dict[str, Any], pedido: _Pedido, consultas: _Consultas) -> dict[str, str]:
    metrica = pedido.metrica
    medidas = _medidas(metrica, total, con_unidades=pedido.producto_pedido)
    cifras = _textos(medidas)
    if metrica == "venta":
        clave = "devoluciones_descontadas" if consultas.s.ventas.incluir_devoluciones else "devoluciones_sin_restar"
        cifras[clave] = num(total["dev_pesos"])
        if pedido.alcance.canal:
            cifras.update(_participacion(total["venta"], pedido, consultas))
    return cifras


def _tiendas_en_total(consultas: _Consultas) -> int:
    return consultar(consultas.con, "SELECT count(*) AS tiendas FROM tiendas")[0]["tiendas"]


def _participacion(venta_canal: Decimal, pedido: _Pedido, consultas: _Consultas) -> dict[str, str]:
    """Cuánto pesa el canal en la venta de las tiendas consultadas y en la de las que tienen el canal."""
    sin_canal = _Alcance(tiendas=list(pedido.nombres_tienda), skus=pedido.alcance.skus, categoria=pedido.alcance.categoria)
    por_tienda = consultas.filas(CLAVES["tienda"], sin_canal, pedido.periodo)
    venta_de = {fila["tienda_id"]: fila["venta"] for fila in por_tienda}
    base_todas = sum(venta_de.values(), Decimal(0))
    cifras = {"pct_de_la_venta_de_las_tiendas": pct(dividir(venta_canal, base_todas))}
    if len(pedido.nombres_tienda) < _tiendas_en_total(consultas):
        pedido.notas.append(
            f"{pedido.alcance.canal} pesa {cifras['pct_de_la_venta_de_las_tiendas']} de la venta de "
            f"{_lista_tiendas(pedido.nombres_tienda)}, no de la venta de todas las tiendas"
        )
    if set(pedido.alcance.tiendas) != set(pedido.nombres_tienda):
        base_canal = sum((venta_de.get(t, Decimal(0)) for t in pedido.alcance.tiendas), Decimal(0))
        cifras["pct_de_la_venta_de_las_tiendas_con_canal"] = pct(dividir(venta_canal, base_canal))
        pedido.notas.append(
            f"pct_de_la_venta_de_las_tiendas tiene como base {_lista_tiendas(pedido.nombres_tienda)}; "
            f"pct_de_la_venta_de_las_tiendas_con_canal, {_lista_tiendas({t: pedido.nombres_tienda[t] for t in pedido.alcance.tiendas})}"
        )
    return cifras


def _lista_tiendas(nombres: dict[str, str]) -> str:
    etiquetas = [f"{nombre} ({id_})" for id_, nombre in nombres.items()]
    if len(etiquetas) == 1:
        return etiquetas[0]
    return f"{', '.join(etiquetas[:-1])} y {etiquetas[-1]}"


# ------------------------------------------------------------------ comparación

def _variacion(anterior: Any, posterior: Any) -> str | None:
    """De anterior a posterior, con signo; None si el anterior es cero y no hay base."""
    if not anterior:
        return None
    texto = pct(dividir(posterior - anterior, anterior))
    sin_cifra = not any(c in "123456789" for c in texto)
    return texto if texto.startswith("-") or sin_cifra else f"+{texto}"


def _en_orden(a: Periodo, b: Periodo) -> tuple[Periodo, Periodo]:
    return (a, b) if (a.desde, a.hasta) <= (b.desde, b.hasta) else (b, a)


def _etiqueta_dias(periodo: Periodo) -> str:
    return f"{periodo.etiqueta}, {periodo.dias} días"


def _cifras_comparadas(totales: dict[str, dict[str, Any]], pedido: _Pedido, anterior: Periodo, posterior: Periodo) -> dict[str, str]:
    metrica = pedido.metrica
    medidas = {p.etiqueta: _medidas(metrica, totales[p.etiqueta], con_unidades=pedido.producto_pedido)
               for p in (anterior, posterior)}
    cifras: dict[str, str] = {}
    nombres = list(dict.fromkeys([*medidas[anterior.etiqueta], *medidas[posterior.etiqueta]]))
    for nombre in nombres:
        valores = [medidas[p.etiqueta].get(nombre) for p in (anterior, posterior)]
        formato = next(m.formato for m in valores if m)
        for periodo, medida in zip((anterior, posterior), valores):
            cifras[f"{nombre} ({periodo.etiqueta})"] = formato(medida.valor if medida else 0)
        variacion = _variacion(*(m.valor if m else 0 for m in valores))
        if variacion:
            cifras[f"variacion {nombre}"] = variacion
    primaria = _primaria(metrica, medidas[posterior.etiqueta])
    if primaria in PRIMARIA.values():
        por_dia = [dividir(medidas[p.etiqueta][primaria].valor, p.dias) for p in (anterior, posterior)
                   if primaria in medidas[p.etiqueta]]
        if len(por_dia) == 2:
            for periodo, valor in zip((anterior, posterior), por_dia):
                cifras[f"{primaria}_por_dia ({_etiqueta_dias(periodo)})"] = num(valor)
            variacion = _variacion(*por_dia)
            if variacion:
                cifras[f"variacion {primaria}_por_dia"] = variacion
    return cifras


@dataclass(frozen=True)
class _Cambio:
    """Un grupo de la comparación con su medida principal en los dos periodos, sin formatear, y el texto de su variación."""

    nombre: str
    anterior: Any
    posterior: Any
    variacion: str | None


def _filas_comparadas(
    grupos: dict[str, list[_Grupo]], pedido: _Pedido, agrupar: str, anterior: Periodo, posterior: Periodo,
) -> tuple[list[dict[str, str]], list[_Cambio]]:
    """Cada grupo con sus medidas en los dos periodos; un grupo que no vendió en uno lleva cero ahí.

    Devuelve también la medida principal de cada grupo sin formatear: el resumen cuenta con ella y no con el texto del %.
    """
    por_llave: dict[tuple, dict[str, _Grupo]] = {}
    for etiqueta, lista in grupos.items():
        for grupo in lista:
            por_llave.setdefault(grupo.orden, {})[etiqueta] = grupo
    con_unidades = agrupar == "producto" and pedido.metrica == "venta"
    filas = []
    cambios = []
    for llave in sorted(por_llave):
        de_cada = por_llave[llave]
        celdas = dict(next(iter(de_cada.values())).celdas)
        medidas = [_medidas(pedido.metrica, de_cada[p.etiqueta].suma, con_unidades) if p.etiqueta in de_cada else {}
                   for p in (anterior, posterior)]
        formatos = {nombre: medida.formato for lado in medidas for nombre, medida in lado.items()}
        for periodo, lado in zip((anterior, posterior), medidas):
            for nombre, formato in formatos.items():
                celdas[f"{nombre} ({periodo.etiqueta})"] = lado[nombre].texto() if nombre in lado else formato(0)
        primaria = _primaria(pedido.metrica, medidas[1] or medidas[0])
        if primaria:
            valores = [lado[primaria].valor if primaria in lado else 0 for lado in medidas]
            variacion = _variacion(*valores)
            if variacion:
                celdas[f"variacion {primaria}"] = variacion
            cambios.append(_Cambio(celdas[agrupar], valores[0], valores[1], variacion))
        filas.append(celdas)
    return filas, cambios


# ------------------------------------------------------------------ la herramienta

def _resolver(con: duckdb.DuckDBPyConnection, entrada: EntradaVentas, s: Supuestos, hoy_real: dt.date | None) -> _Pedido:
    verificar_metrica(entrada.metrica, s)
    catalogos = cargar_catalogos(con)
    tiendas = resolver_tiendas(entrada.tiendas, catalogos)
    con_canal = verificar_canal(entrada.canal, catalogos.tiendas, tiendas.ids, s)
    productos = resolver_productos(entrada.producto, catalogos, s)
    categoria = resolver_categoria(entrada.categoria, catalogos)
    periodo = resolver_periodo(entrada.periodo, entrada.desde, entrada.hasta, "ventas", hoy_real, s)
    comparado = None
    if entrada.comparar_con:
        comparado = resolver_periodo(entrada.comparar_con, None, None, "ventas", hoy_real, s)
    if comparado and (comparado.desde, comparado.hasta) == (periodo.desde, periodo.hasta):
        raise ErrorHerramienta(
            "periodo_no_entendido",
            f"comparar_con da el mismo periodo que se pidió ({periodo.etiqueta}); no hay nada que comparar",
        )
    notas = [*tiendas.notas, *(productos.notas if productos else []), *periodo.notas]
    if comparado:
        notas += comparado.notas
    if con_canal.nota:
        notas.append(con_canal.nota)
    if entrada.agrupar_por == "canal":
        notas += _nota_sin_ecommerce(catalogos, tiendas.ids, s)
    alcance = _Alcance(
        tiendas=list(tiendas_validas(con_canal.ids)),
        skus=productos.skus if productos else None,
        categoria=categoria,
        canal=entrada.canal,
    )
    nombres = {id_: catalogos.tiendas[id_]["nombre"] for id_ in tiendas_validas(tiendas.ids)}
    return _Pedido(entrada, alcance, periodo, comparado, nombres, notas, _familias(catalogos, s), productos is not None)


def _nota_sin_ecommerce(catalogos: Catalogos, ids: Sequence[str], s: Supuestos) -> list[str]:
    notas = []
    for canal in s.ventas.canales:
        sin = [catalogos.tiendas[t]["nombre"] for t in ids if not s.ventas.canal_existe(catalogos.tiendas[t]["formato"], canal)]
        if sin:
            notas.append(f"{' y '.join(sin)} no tienen canal {canal}: no aparece, no es venta de cero")
    return notas


def _definicion(metrica: str, s: Supuestos) -> str:
    if s.ventas.incluir_devoluciones:
        base = s.ventas.definicion
    else:
        base = "venta neta sin restar las devoluciones: las filas con unidades negativas no entran (incluir_devoluciones en false)"
    if metrica == "devoluciones":
        return "devoluciones: las filas de ventas.csv con unidades negativas, en positivo"
    if metrica == "unidades":
        return f"{base}; {s.unidad_venta.definicion}"
    if metrica == "venta_por_m2":
        return f"{base}; entre los m² de piso de venta de la tienda (tiendas.csv)"
    if metrica == "margen":
        return f"{base}; margen = venta − unidades × costo del catálogo"
    return base


def _unidad(metrica: str, cifras: dict[str, str]) -> str:
    if metrica != "unidades":
        return UNIDAD_RESULTADO[metrica]
    presentes = [nombre for sufijo, nombre in UNIDADES.values() if any(c.startswith(f"unidades_{sufijo}") for c in cifras)]
    return " y ".join(presentes) + (", por separado" if len(presentes) > 1 else "")


def _sin_resultados(periodo: Periodo) -> ErrorHerramienta:
    return ErrorHerramienta("sin_resultados", f"No hay filas de venta con esos filtros en {periodo.etiqueta}")


def _calcular(con: duckdb.DuckDBPyConnection, entrada: EntradaVentas, s: Supuestos, hoy_real: dt.date | None) -> Resultado:
    pedido = _resolver(con, entrada, s, hoy_real)
    consultas = _Consultas(con, s)
    claves, agrupar = _claves(pedido)
    periodos = [pedido.periodo] if pedido.comparado is None else list(_en_orden(pedido.periodo, pedido.comparado))
    totales: dict[str, dict[str, Any]] = {}
    grupos: dict[str, list[_Grupo]] = {}
    for periodo in periodos:
        total = consultas.total(pedido.alcance, periodo)
        if not total["filas"]:
            raise _sin_resultados(periodo)
        totales[periodo.etiqueta] = _separar(total)[1]
        if claves:
            familias = pedido.por_familia if pedido.entrada.agrupar_por == "producto" else None
            grupos[periodo.etiqueta] = _grupos(consultas.filas(claves, pedido.alcance, periodo), agrupar, familias)
        if pedido.metrica == "venta_por_m2":
            totales[periodo.etiqueta]["m2"] = _m2(grupos[periodo.etiqueta])
    if pedido.comparado is None:
        cifras, filas, n_filas = _sin_comparar(pedido, consultas, totales, grupos, agrupar)
    else:
        cifras, filas, n_filas = _comparando(pedido, totales, grupos, agrupar, *periodos)
    if pedido.metrica == "margen":
        pedido.notas.append(s.margen.supuesto)
    if pedido.metrica == "unidades" and pedido.comparado is None:
        cifras.update(_en_litros(con, pedido, totales[pedido.periodo.etiqueta]))
    pedido.notas.extend(_notas_de_la_metrica(pedido))
    return Resultado(
        herramienta=HERRAMIENTA,
        resumen=f"{pedido.metrica.replace('_', ' ')}, {pedido.periodo.etiqueta}",
        unidad=_unidad(pedido.metrica, cifras),
        definicion=_definicion(pedido.metrica, s),
        filtros=_filtros(pedido),
        corte=corte_de("ventas"),
        cifras=cifras,
        filas=filas,
        n_filas=n_filas,
        notas=pedido.notas,
        fuente=FUENTE_DE_DEVOLUCIONES if pedido.metrica == "devoluciones" else FUENTE,
        consulta=";\n".join(consultas.textos),
        detalle=_para_comparar(pedido),
    )


def _litros_por_pieza(nombres: Sequence[str]) -> tuple[Decimal, str] | None:
    """Lo que trae cada pieza, si todas traen lo mismo en su nombre y ninguna es un paquete; y cómo lo dice el nombre."""
    contenidos = {}
    for nombre in nombres:
        hallado = _CONTENIDO_EN_EL_NOMBRE.search(nombre)
        if not hallado or "pack" in nombre.casefold():
            return None
        cantidad = Decimal(hallado.group(1))
        contenidos[cantidad if hallado.group(2) == "L" else cantidad / 1000] = hallado.group(0)
    return next(iter(contenidos.items())) if len(contenidos) == 1 else None


def _en_litros(con: duckdb.DuckDBPyConnection, pedido: _Pedido, suma: dict[str, Any]) -> dict[str, str]:
    """Las piezas en litros: el catálogo las cuenta por pieza, pero el nombre del producto dice cuánto trae cada una."""
    if not pedido.alcance.skus or not suma["filas_pieza"] or suma["filas_kg"]:
        return {}
    sql = "SELECT nombre FROM catalogo WHERE list_contains(CAST(? AS VARCHAR[]), sku) ORDER BY sku"
    contenido = _litros_por_pieza([fila["nombre"] for fila in consultar(con, sql, [pedido.alcance.skus])])
    if contenido is None:
        return {}
    litros = num(suma["unidades_pieza"] * contenido[0])
    pedido.notas.append(f"el nombre del producto dice que cada pieza trae {contenido[1]}: {entero(suma['unidades_pieza'])} piezas son {litros} litros")
    return {"litros": litros}


def _notas_de_la_metrica(pedido: _Pedido) -> list[str]:
    """Lo que la respuesta tiene que decir de la métrica, en una nota porque la definición no siempre se copia."""
    if pedido.metrica == "devoluciones":
        return ["las devoluciones salen de las filas con unidades negativas de ventas.csv"]
    if pedido.metrica == "venta_por_m2" and (pedido.alcance.categoria or pedido.alcance.skus):
        return [
            "supuesto: los m² son de toda la tienda (tiendas.csv), no de lo filtrado; no hay m² por categoría ni por producto, "
            "así que la cifra queda por debajo de la que daría el espacio de lo filtrado"
        ]
    return []


def _para_comparar(pedido: _Pedido) -> dict[str, str]:
    """Cómo pedir la variación, si llamó por mes o por semana sin comparar_con (F7 de T5.11).

    Va en detalle y no en notas: es para el modelo y no debe imprimirse en la respuesta.
    """
    un_solo_dia = pedido.periodo.desde == pedido.periodo.hasta
    if un_solo_dia and pedido.metrica == "venta" and pedido.entrada.agrupar_por is None:
        return {
            "para_desglosar": "para saber qué pesó en un día, vuelve a llamar con ese día y agrupar_por categoria, canal o producto; "
                              "con comparar_con y agrupar_por trae cada grupo en los dos días"
        }
    if pedido.comparado is not None or pedido.entrada.grano not in ("mes", "semana"):
        return {}
    return {"para_comparar": "para la variación en % entre periodos, vuelve a llamar con periodo y comparar_con"}


_Tabla = tuple[dict[str, str], list[dict[str, str]], int]


def _sin_comparar(pedido: _Pedido, consultas: _Consultas, totales: dict[str, dict[str, Any]],
                  grupos: dict[str, list[_Grupo]], agrupar: str | None) -> _Tabla:
    """Cifras del total, filas ordenadas y cuántos grupos había, para un solo periodo."""
    etiqueta = pedido.periodo.etiqueta
    lista = grupos.get(etiqueta, [])
    cifras = _cifras_del_total(totales[etiqueta], pedido, consultas)
    cifras.update(_referencia_del_mes(pedido, consultas))
    tiendas_con_venta = {g.celdas["tienda_id"] for g in lista} if pedido.metrica == "venta_por_m2" else set()
    if len(tiendas_con_venta) > 1:
        pedido.notas.append(f"las cifras juntan la venta y los m² de las {len(tiendas_con_venta)} tiendas; cada fila es una tienda")
    ordenados, n_filas = _ordenar(lista, pedido, agrupar)
    if len(ordenados) < n_filas:
        pedido.notas.append(f"se muestran {len(ordenados)} de {n_filas} grupos")
    nota = _nota_de_familias(ordenados)
    if nota:
        pedido.notas.append(nota)
    pedido.notas.extend(_nota_sin_venta(lista, pedido, consultas, agrupar))
    filas = _filas(ordenados, pedido, agrupar)
    _con_lugar_por_venta(filas, ordenados, pedido, agrupar)
    if _parten_el_total(pedido, agrupar):
        partes = _parte_de_cada_grupo(lista, totales[etiqueta]["venta"])
        for fila, grupo in zip(filas, ordenados):
            if grupo.orden in partes:
                fila[PARTE_DEL_TOTAL] = partes[grupo.orden]
    return cifras, filas, n_filas


def _parten_el_total(pedido: _Pedido, agrupar: str | None) -> bool:
    """Si las filas reparten la venta del total por una sola dimensión: una agrupación o un grano, no los dos.

    Con las dos, «qué parte» podría ser del periodo de la fila o de todo lo consultado, y la fila no diría cuál.
    """
    if pedido.metrica != "venta":
        return False
    con_grano = pedido.entrada.grano != "total"
    return (agrupar is not None) != con_grano


def _parte_de_cada_grupo(grupos: list[_Grupo], venta_total: Decimal) -> dict[tuple, str]:
    """Qué parte de la venta del total es cada grupo, por su llave: el modelo no divide, y un porcentaje suyo no pasa la revisión.

    La base es el total de la consulta, con sus filtros, aunque el límite deje filas sin mostrar. Sin base no hay parte:
    con el total en cero o negativo, o con un grupo en negativo (solo devoluciones), repartir el 100% no dice nada.
    """
    if venta_total <= 0:
        return {}
    if any(grupo.suma["venta"] < 0 for grupo in grupos):
        return {}
    return {grupo.orden: pct(dividir(grupo.suma["venta"], venta_total)) for grupo in grupos}


def _con_lugar_por_venta(filas: list[dict[str, str]], grupos: list[_Grupo], pedido: _Pedido, agrupar: str | None) -> None:
    """Cada día con su lugar: ordenar a ojo una lista cronológica es donde la respuesta se equivoca."""
    if pedido.entrada.grano != "dia" or pedido.metrica != "venta" or agrupar is not None:
        return
    orden = sorted(range(len(grupos)), key=lambda indice: grupos[indice].suma["venta"], reverse=True)
    for lugar, indice in enumerate(orden, start=1):
        filas[indice]["lugar_de_mayor_a_menor"] = entero(lugar)


def _nota_sin_venta(lista: list[_Grupo], pedido: _Pedido, consultas: _Consultas, agrupar: str | None) -> list[str]:
    """El SKU del grupo pedido que no está en las filas ni en el total, con la razón: otra categoría, otro canal o sin venta.

    Un filtro de canal o de categoría deja fuera SKU que sí vendieron: «sin venta en este periodo» solo va para los que no
    vendieron en ningún canal de esas tiendas en el periodo (RC-21).
    """
    if agrupar != "producto" or not pedido.producto_pedido:
        return []
    vendidos = {sku for grupo in lista for sku in grupo.celdas["sku"].split(" + ")}
    faltantes = [sku for sku in pedido.alcance.skus or [] if sku not in vendidos]
    if not faltantes:
        return []
    filas = consultar(
        consultas.con,
        "SELECT sku, nombre, categoria FROM catalogo WHERE list_contains(CAST(? AS VARCHAR[]), sku) ORDER BY sku",
        [faltantes],
    )
    alcance = pedido.alcance
    de_otra_categoria = [fila for fila in filas if alcance.categoria and fila["categoria"] != alcance.categoria]
    de_la_categoria = [fila for fila in filas if fila not in de_otra_categoria]
    en_otro_canal = _vendidos_sin_canal(de_la_categoria, pedido, consultas) if alcance.canal else set()
    razones = (
        (f"no es de la categoría {alcance.categoria}", de_otra_categoria),
        (f"sin venta en {alcance.canal} en este periodo", [fila for fila in de_la_categoria if fila["sku"] in en_otro_canal]),
        ("sin venta en este periodo", [fila for fila in de_la_categoria if fila["sku"] not in en_otro_canal]),
    )
    return [f"{razon}: {_nombres_de_sku(grupo)}; no está en las filas ni en el total" for razon, grupo in razones if grupo]


def _nombres_de_sku(filas: Sequence[dict[str, Any]]) -> str:
    return ", ".join(f"{fila['nombre']} ({fila['sku']})" for fila in filas)


def _vendidos_sin_canal(filas: Sequence[dict[str, Any]], pedido: _Pedido, consultas: _Consultas) -> set[str]:
    """Los SKU que vendieron en las mismas tiendas y el mismo periodo en cualquier canal."""
    skus = [fila["sku"] for fila in filas]
    if not skus:
        return set()
    sin_canal = _Alcance(tiendas=pedido.alcance.tiendas, skus=skus)
    return {fila["sku"] for fila in consultas.filas([("v.sku", "sku")], sin_canal, pedido.periodo)}


def _referencia_del_mes(pedido: _Pedido, consultas: _Consultas) -> dict[str, str]:
    """Con qué comparar una venta por día: la mediana y el promedio diarios de su mes, con los mismos filtros.

    Sin esto la respuesta no puede decir si una venta diaria fue alta o baja, y tiende a nombrar el total como promedio.
    """
    periodo = pedido.periodo
    if pedido.entrada.grano != "dia" or pedido.metrica != "venta":
        return {}
    if periodo.desde is None or (periodo.desde.year, periodo.desde.month) != (periodo.hasta.year, periodo.hasta.month):
        return {}
    dias_del_mes = calendar.monthrange(periodo.desde.year, periodo.desde.month)[1]
    primero = periodo.desde.replace(day=1)
    ultimo = primero.replace(day=dias_del_mes)
    filas = consultas.filas([(GRANOS["dia"], "periodo")], pedido.alcance, Periodo(primero, ultimo, periodo.etiqueta))
    ventas_del_dia = {fila["periodo"]: fila["venta"] for fila in filas}
    valores = [ventas_del_dia.get(primero + dt.timedelta(days=dia), Decimal(0)) for dia in range(dias_del_mes)]
    mes = f"{MESES_NOMBRE[primero.month - 1]}_de_{primero.year}"
    pedido.notas.append(
        f"mediana_diaria_de_{mes} y promedio_diario_de_{mes} son de los {dias_del_mes} días de ese mes, con estos mismos filtros"
    )
    return {
        f"mediana_diaria_de_{mes}": num(statistics.median(valores)),
        f"promedio_diario_de_{mes}": num(sum(valores, Decimal(0)) / dias_del_mes),
    }


def _comparando(pedido: _Pedido, totales: dict[str, dict[str, Any]], grupos: dict[str, list[_Grupo]],
                agrupar: str | None, anterior: Periodo, posterior: Periodo) -> _Tabla:
    """Cifras de los dos periodos con su variación y, si hay agrupación, cada grupo en los dos."""
    if pedido.entrada.grano != "total":
        pedido.notas.append("la comparación va sobre el total de cada periodo; el grano no aplica")
    pedido.notas.append(f"las variaciones van de {anterior.etiqueta} a {posterior.etiqueta}")
    cifras = _cifras_comparadas(totales, pedido, anterior, posterior)
    if not agrupar or pedido.entrada.grano != "total":
        return cifras, [], 0
    filas, cambios = _filas_comparadas(grupos, pedido, agrupar, anterior, posterior)
    cifras.update(_resumen_de_variaciones(cambios))
    return cifras, filas, len(filas)


def _con_cifra(variacion: str | None) -> bool:
    """Si el texto de una variación dice algo distinto de cero: «0.00%» no es un cambio que nombrar."""
    return bool(variacion) and any(c in "123456789" for c in variacion)


def _resumen_de_variaciones(cambios: list[_Cambio]) -> dict[str, str]:
    """Cuántos grupos subieron, bajaron o quedaron igual, y cuál varió más: contarlo a ojo en la tabla es donde la respuesta se equivoca.

    Se cuenta con las cifras de los dos periodos y no con el texto del %: un grupo que pasa de 0 a vender subió aunque no
    tenga variación en % (RC-17). grupos_sin_base dice cuántos no la tienen, porque en el primer periodo no vendieron o
    quedaron en negativo; ya están entre los que subieron, bajaron o quedaron igual. La mayor variación es la de mayor
    magnitud, suba o baje (RC-18), entre los grupos con base que cambiaron; si no hay ninguno, no va.
    """
    if not cambios:
        return {}
    resumen = {
        "grupos_que_subieron": entero(sum(cambio.posterior > cambio.anterior for cambio in cambios)),
        "grupos_que_bajaron": entero(sum(cambio.posterior < cambio.anterior for cambio in cambios)),
    }
    sin_cambio = sum(cambio.posterior == cambio.anterior for cambio in cambios)
    if sin_cambio:
        resumen["grupos_sin_cambio"] = entero(sin_cambio)
    sin_base = sum(cambio.anterior <= 0 for cambio in cambios)
    if sin_base:
        resumen["grupos_sin_base"] = entero(sin_base)
    con_base = [cambio for cambio in cambios if cambio.anterior > 0 and _con_cifra(cambio.variacion)]
    if con_base:
        mayor = max(con_base, key=lambda cambio: abs(dividir(cambio.posterior - cambio.anterior, cambio.anterior)))
        resumen["mayor_variacion"] = f"{mayor.nombre} ({mayor.variacion})"
    return resumen


def _filtros(pedido: _Pedido) -> dict[str, Filtro]:
    entrada = pedido.entrada
    filtros: dict[str, Filtro] = {
        "metrica": entrada.metrica,
        "tiendas": pedido.alcance.tiendas,
        "desde": pedido.periodo.desde,
        "hasta": pedido.periodo.hasta,
        "productos": pedido.alcance.skus,
        "categoria": pedido.alcance.categoria,
        "canal": pedido.alcance.canal,
        "agrupar_por": entrada.agrupar_por,
        "grano": None if entrada.grano == "total" else entrada.grano,
    }
    if pedido.comparado:
        filtros.update(comparar_desde=pedido.comparado.desde, comparar_hasta=pedido.comparado.hasta)
    return {clave: valor for clave, valor in filtros.items() if valor is not None}


def _filtros_pedidos(entrada: EntradaVentas) -> dict[str, Filtro]:
    """Lo que pidió el modelo, para el resultado de un error con tipo."""
    return entrada.model_dump(exclude_defaults=True)


def ventas(
    con: duckdb.DuckDBPyConnection,
    entrada: EntradaVentas,
    s: Supuestos | None = None,
    hoy_real: dt.date | None = None,
) -> Resultado:
    """Ventas netas de junio a agosto de 2026 con los filtros de la entrada.

    con es el cursor de la llamada (Consultas.sesion()). s son los supuestos (config/supuestos.yaml si no
    se pasan) y hoy_real la fecha del reloj, para el aviso de las fechas relativas. Un error con tipo
    (métrica no calculable, tienda ambigua o inexistente, canal inexistente, fuera de periodo, sin
    resultados, división entre cero) vuelve como Resultado con su estado y sin cifras.
    """
    s = s or supuestos()
    return ejecutar(HERRAMIENTA, lambda: _calcular(con, entrada, s, hoy_real), _filtros_pedidos(entrada))
