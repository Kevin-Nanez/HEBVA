"""Herramienta de caducidades: lotes por retirar y por rebajar al corte (T2.5, C4.1 y C2.3).

Aplica POL-OPS-014 al lote más próximo de cada tienda y SKU (fecha_caducidad_lote_proximo de
inventario.csv) con los días que faltan desde el corte del inventario (1 de septiembre de 2026),
nunca desde el reloj:

- Retiro de anaquel. Los Lácteos se retiran dos días antes de la caducidad (§5.2, p. 3) y los demás
  perecederos un día antes (§5.1, p. 3). El último día de venta es el anterior al retiro.
- Rebaja escalonada (§6, p. 4): 5 días 15%, 4 días 25%, 3 días 35%, 2 días 50% y 1 día 60%, esta
  última solo para perecederos no lácteos. Un lote que ya llegó a su día de retiro se retira y no
  se rebaja. Para los lácteos lo dice §6.1 (p. 5): salen del módulo al llegar a dos días. Para los
  demás es criterio propio: la fila de 1 día cae en el día de retiro de §5.1, la política no dice
  cuál prevalece, y se toma el retiro porque §6.1 (p. 4) registra como Caducidad, «sin excepción»,
  lo que llega a ese día (P59; CH-04 de config/contradicciones.yaml). La definición del resultado lo
  dice con esas palabras y no le atribuye a §6.1 una prohibición de rebajar que su texto no trae.

Lecturas declaradas en las notas del resultado:

- §5.2 se aplica a toda la categoría Lácteos del catálogo. Su lista solo nombra leche fresca, yogur,
  quesos frescos, cremas y postres refrigerados; los lotes de productos que no nombra (huevo,
  mantequilla, quesos madurados) se listan con la cuenta que daría §5.1 para ellos (P13 acepta 2 y 5,
  o 1 y 6).
- POL-OPS-014 §1.1 solo menciona el formato supermercado. La regla se da para todas las tiendas, y
  la nota dice cuántos lotes son de tiendas que ese texto no cubre, con la salvedad de
  config/supuestos.yaml (alcance_documentos, D2-C): se les aplica la regla como supuesto.
- La existencia es la del SKU en la tienda: el inventario no dice cuántas piezas son del lote
  próximo, así que no se cuantifica la merma (P12).

Sin producto ni ventana, la herramienta da los lotes que piden acción al corte: los que caducan a
5 días o menos, la fila más alta de la tabla de §6. Con producto da todos sus lotes con su calendario
(retiro, último día de venta y escala de rebaja), aunque no pidan acción todavía (P12). ventana_dias
da los lotes que caducan a ese número de días o menos del corte, en todas las categorías si no se
pide una (cambio en vivo de C9.3). Las cifras cuentan retiros y rebajas de todo el alcance pedido,
aunque las filas se filtren por acción, para que "de esos, ¿cuáles son de Cumbres?" pueda decir que
Cumbres solo tiene rebajas (P32).

Con caduca_el o caduca_en_dias, la caducidad la da quien pregunta ("caduca el viernes", P06; "caduca mañana", P59): el
retiro, el último día de venta o la acción se cuentan desde ahí con la regla de la categoría del producto, y no se leen
lotes, que son otro producto y otra fecha.

Uso, con el cursor de la llamada (Consultas.sesion()):

    resultado = caducidades(con, EntradaCaducidades(categoria="lacteos"))
    resultado.texto_modelo()
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import duckdb

from agente.definiciones import DIAS_NOMBRE, Supuestos, aviso_fecha_relativa, supuestos
from agente.herramientas.comun import (
    EntradaCaducidades,
    Filtro,
    Resultado,
    consultar,
    corte_de,
    dumps,
    ejecutar,
    entero,
    fecha_larga,
    tiendas_validas,
)
from agente.herramientas.errores import EntidadInexistente
from agente.resolver import Catalogos, Productos, cargar_catalogos, resolver_categoria, resolver_productos, resolver_tiendas

HERRAMIENTA = "caducidades"
DOCUMENTO = "POL-OPS-014"

# ------------------------------------------------------------------ reglas de POL-OPS-014

CATEGORIA_LACTEOS = "Lácteos"
DIAS_RETIRO_LACTEOS = 2  # §5.2 (p. 3)
DIAS_RETIRO_PERECEDEROS = 1  # §5.1 (p. 3)
# Días restantes antes de la caducidad -> descuento en %, tabla de §6 (p. 4). La fila de 1 día es solo
# para perecederos no lácteos; clasificar() la deja fuera porque cae en el día de retiro.
ESCALA_REBAJA = {5: 15, 4: 25, 3: 35, 2: 50, 1: 60}
VENTANA_POLITICA = max(ESCALA_REBAJA)
MAXIMO_DE_LOTES_EN_LA_NOTA = 5

# Lo que nombra la lista de §5.2: leche fresca, yogur, quesos frescos, cremas y postres refrigerados.
# El catálogo no tiene subcategoría de postres; de los quesos, solo los frescos.
LISTA_52 = "leche fresca, yogur, quesos frescos, cremas y postres refrigerados"
SUBCATEGORIAS_NOMBRADAS_EN_52 = frozenset({"Leche", "Yogur", "Cremas"})
SUBCATEGORIA_QUESOS = "Quesos"
QUESOS_FRESCOS = ("fresco", "panela", "oaxaca", "cottage", "requesón")

FUENTE = (
    "inventario.csv (fecha_caducidad_lote_proximo), catalogo_productos.csv (categoría) y tiendas.csv (formato); "
    "POL-OPS-014 §5.1 y §5.2 (p. 3), §6 (p. 4) y §6.1 (p. 4 y 5)"
)

# Con el día que da quien pregunta no se lee inventario: solo la categoría del producto y la regla.
FUENTE_DEL_DIA_DADO = "catalogo_productos.csv (categoría); POL-OPS-014 §5.1 y §5.2 (p. 3), §6 (p. 4) y §6.1 (p. 4 y 5)"

Accion = Literal["retirar", "rebajar", "ninguna"]


@dataclass(frozen=True)
class Clasificacion:
    accion: Accion
    regla: str
    descuento: int | None = None


@dataclass(frozen=True)
class Calendario:
    fecha_retiro: dt.date
    ultimo_dia_venta: dt.date
    escala: list[tuple[dt.date, int]]

    @property
    def rebaja_desde(self) -> dt.date | None:
        return self.escala[0][0] if self.escala else None


def _regla_de_retiro(categoria: str) -> tuple[int, str]:
    if categoria == CATEGORIA_LACTEOS:
        return DIAS_RETIRO_LACTEOS, "§5.2"
    return DIAS_RETIRO_PERECEDEROS, "§5.1"


def _clasificar_con(dias: int, dias_retiro: int, regla_retiro: str) -> Clasificacion:
    if dias <= dias_retiro:
        return Clasificacion("retirar", regla_retiro)
    if dias in ESCALA_REBAJA:
        return Clasificacion("rebajar", "§6", ESCALA_REBAJA[dias])
    return Clasificacion("ninguna", regla_retiro)


def clasificar(categoria: str, dias: int) -> Clasificacion:
    """Lo que pide la política al corte para un lote que caduca en `dias` días (negativo: ya caducó)."""
    return _clasificar_con(dias, *_regla_de_retiro(categoria))


def calendario(categoria: str, caducidad: dt.date) -> Calendario:
    """Día de retiro, último día de venta y escala de rebaja de un lote, contados desde su caducidad."""
    dias_retiro, _ = _regla_de_retiro(categoria)
    fecha_retiro = caducidad - dt.timedelta(days=dias_retiro)
    escala = [
        (caducidad - dt.timedelta(days=dias), descuento)
        for dias, descuento in sorted(ESCALA_REBAJA.items(), reverse=True)
        if dias > dias_retiro
    ]
    return Calendario(fecha_retiro, fecha_retiro - dt.timedelta(days=1), escala)


def _nombrado_en_52(producto: str, subcategoria: str) -> bool:
    if subcategoria in SUBCATEGORIAS_NOMBRADAS_EN_52:
        return True
    if subcategoria != SUBCATEGORIA_QUESOS:
        return False
    nombre = producto.casefold()
    return any(palabra in nombre for palabra in QUESOS_FRESCOS)


# ------------------------------------------------------------------ consulta

_SQL_LOTES = """
SELECT i.tienda_id, i.tienda, i.formato, i.sku, i.producto, i.categoria, c.subcategoria, i.perecedero,
       i.existencia, i.fecha_caducidad_lote_proximo AS caducidad,
       date_diff('day', CAST(? AS DATE), i.fecha_caducidad_lote_proximo) AS dias
FROM v_inventario i
JOIN catalogo c ON c.sku = i.sku
WHERE list_contains(CAST(? AS VARCHAR[]), i.tienda_id)"""
_FILTRO_SKUS = "\n  AND list_contains(CAST(? AS VARCHAR[]), i.sku)"
_FILTRO_CATEGORIA = "\n  AND i.categoria = ?"
_FILTRO_VENTANA = (
    "\n  AND i.fecha_caducidad_lote_proximo IS NOT NULL"
    "\n  AND date_diff('day', CAST(? AS DATE), i.fecha_caducidad_lote_proximo) <= ?"
)
_ORDEN = "\nORDER BY dias NULLS LAST, i.tienda_id, i.producto"


def _consulta(
    corte: dt.date, tiendas: Sequence[str], skus: Sequence[str] | None, categoria: str | None, ventana: int | None
) -> tuple[str, list[Any]]:
    """SQL fijo con los valores como parámetros: el texto del usuario nunca entra a la consulta."""
    sql = _SQL_LOTES
    parametros: list[Any] = [corte, list(tiendas)]
    if skus is not None:
        sql += _FILTRO_SKUS
        parametros.append(list(skus))
    if categoria is not None:
        sql += _FILTRO_CATEGORIA
        parametros.append(categoria)
    if ventana is not None:
        sql += _FILTRO_VENTANA
        parametros.extend([corte, ventana])
    return sql + _ORDEN, parametros


def _consulta_con_parametros(sql: str, parametros: Sequence[Any]) -> str:
    """El SQL del registro con sus parámetros en un comentario, para rastrear cada cifra hasta su consulta (C1.1).

    Solo va al registro (texto_registro); la consulta que corre DuckDB lleva los valores como parámetros.
    """
    return f"{sql}\n-- parámetros: {dumps(list(parametros))}"


@dataclass(frozen=True)
class Lote:
    tienda_id: str
    tienda: str
    formato: str
    sku: str
    producto: str
    categoria: str
    subcategoria: str
    perecedero: bool
    existencia: int
    caducidad: dt.date | None
    dias: int | None


# ------------------------------------------------------------------ textos

def _contar(n: int, singular: str, plural: str) -> str:
    return f"{entero(n)} {singular if n == 1 else plural}"


def _unir(elementos: Sequence[str]) -> str:
    if len(elementos) <= 1:
        return "".join(elementos)
    return f"{', '.join(elementos[:-1])} y {elementos[-1]}"


def _desglose(clasificaciones: Iterable[Clasificacion]) -> str:
    """'2 retiros y 3 rebajas', con solo las acciones que tienen lotes."""
    acciones = [c.accion for c in clasificaciones]
    partes = [
        _contar(acciones.count("retirar"), "retiro", "retiros"),
        _contar(acciones.count("rebajar"), "rebaja", "rebajas"),
        _contar(acciones.count("ninguna"), "lote sin acción al corte", "lotes sin acción al corte"),
    ]
    return _unir([parte for parte, accion in zip(partes, ("retirar", "rebajar", "ninguna")) if accion in acciones])


def _retiros_y_rebajas(clasificaciones: Sequence[Clasificacion]) -> str:
    acciones = [c.accion for c in clasificaciones]
    retiros = _contar(acciones.count("retirar"), "retiro", "retiros")
    rebajas = _contar(acciones.count("rebajar"), "rebaja", "rebajas")
    return f"{retiros} y {rebajas}"


def _ventana_con_fechas(corte: dt.date, ventana: int) -> str:
    fin = corte + dt.timedelta(days=ventana)
    if ventana == 0:
        rango = f"el {fecha_larga(corte)}"
    elif (fin.month, fin.year) == (corte.month, corte.year):
        rango = f"del {corte.day} al {fecha_larga(fin)}"
    else:
        rango = f"del {fecha_larga(corte)} al {fecha_larga(fin)}"
    return f"caducidad {rango} ({ventana} días o menos desde el corte)"


def _escala() -> str:
    return ", ".join(f"{dias} días {descuento}%" for dias, descuento in ESCALA_REBAJA.items() if dias > DIAS_RETIRO_PERECEDEROS)


def _rebaja_en_el_dia_de_retiro() -> str:
    """Lo que la política dice de rebajar en el día de retiro, y lo que es criterio propio.

    §6.1 saca a los lácteos del módulo de rebaja. De los demás perecederos solo dice que lo que llega a su fecha de retiro
    se registra como Caducidad, «sin excepción»: que por eso no les toque la fila de 1 día de §6 es criterio propio.
    """
    fila = f"{DIAS_RETIRO_PERECEDEROS} día {ESCALA_REBAJA[DIAS_RETIRO_PERECEDEROS]}%"
    return (
        f"los lácteos salen del módulo de rebaja a {DIAS_RETIRO_LACTEOS} días (§6.1). §6 también trae la fila de {fila} "
        "(solo perecederos no lácteos), que cae en el día de retiro de §5.1, y la política no dice cuál prevalece: con "
        "criterio propio manda el retiro, porque §6.1 registra como Caducidad, «sin excepción», lo que llega a ese día"
    )


def _definicion(corte: dt.date, ventana: int | None) -> str:
    escala = _escala()
    texto = (
        "lote más próximo de cada tienda y SKU; días = caducidad − corte. "
        f"Retiro: {CATEGORIA_LACTEOS} del catálogo {DIAS_RETIRO_LACTEOS} días antes (§5.2), otros perecederos "
        f"{DIAS_RETIRO_PERECEDEROS} día antes (§5.1); el último día de venta es el anterior al retiro. "
        f"Rebaja (§6): {escala}; {_rebaja_en_el_dia_de_retiro()}"
    )
    if ventana is None:
        return texto
    return f"{texto}. Ventana: {_ventana_con_fechas(corte, ventana)}"


def _fila(lote: Lote, clasificacion: Clasificacion, puntual: bool, con_categoria: bool) -> dict[str, str]:
    """Una fila por lote. La lista lleva lo que pide la acción al corte; un producto puntual, todo su calendario.

    Las celdas que no aplican no van (un lote a retirar no tiene descuento): el modelo no recibe nulos.
    """
    agenda = calendario(lote.categoria, lote.caducidad)
    fila = {"tienda": lote.tienda, "producto": lote.producto}
    if puntual:
        fila["sku"] = lote.sku
    if con_categoria:
        fila["categoria"] = lote.categoria
    fila |= {
        "existencia": entero(lote.existencia),
        "caducidad": lote.caducidad.isoformat(),
        "dias": entero(lote.dias),
        "accion": clasificacion.accion,
        "regla": clasificacion.regla,
    }
    if clasificacion.descuento is not None:
        fila["descuento"] = f"{clasificacion.descuento}%"
    fila["fecha_retiro"] = agenda.fecha_retiro.isoformat()
    if puntual:
        fila["ultimo_dia_venta"] = agenda.ultimo_dia_venta.isoformat()
        if agenda.escala:
            fila["rebaja_desde"] = agenda.rebaja_desde.isoformat()
            fila["escala"] = ", ".join(f"{fecha.isoformat()} {descuento}%" for fecha, descuento in agenda.escala)
    return fila


# ------------------------------------------------------------------ notas

def _nota_sin_fecha(lotes: Sequence[Lote]) -> list[str]:
    """Un producto sin fecha de caducidad registrada lo dice: la fecha no se calcula con la vida útil (P39)."""
    notas = []
    vistos: set[str] = set()
    for lote in lotes:
        if lote.sku in vistos:
            continue
        vistos.add(lote.sku)
        if lote.perecedero:
            notas.append(f"{lote.producto} no tiene fecha de caducidad registrada en el inventario")
        else:
            notas.append(f"{lote.producto} no tiene fecha de caducidad registrada: el catálogo lo marca como no perecedero")
    return notas


def _nota_alcance(clasificados: Sequence[tuple[Lote, Clasificacion]], s: Supuestos) -> list[str]:
    """Cuántos lotes son de tiendas que POL-OPS-014 §1.1 no cubre, con la salvedad de la opción vigente de D2."""
    fuera = []
    salvedad = None
    por_supuesto = False
    for lote, clasificacion in clasificados:
        alcance = s.alcance(DOCUMENTO, lote.formato)
        if not alcance.cubre:
            fuera.append((lote, clasificacion))
            salvedad = alcance.salvedad
            por_supuesto = alcance.por_supuesto
    if not fuera:
        return []
    tiendas = sorted({(lote.tienda_id, lote.tienda) for lote, _ in fuera})
    formatos = sorted({lote.formato for lote, _ in fuera})
    nombres = _unir([f"{nombre} ({id_})" for id_, nombre in tiendas])
    conteo = _desglose(c for _, c in fuera)
    verbo = "es" if len(fuera) == 1 else "son"
    cierre = "les aplico la misma regla como supuesto" if por_supuesto else "la regla se da como referencia"
    return [f"{salvedad}. {conteo} de esta consulta {verbo} de {nombres}, formato {_unir(formatos)}; {cierre}"]


def _nota_lista_52(clasificados: Sequence[tuple[Lote, Clasificacion]]) -> list[str]:
    """Los lácteos que §5.2 no nombra, y la cuenta si a ellos se les aplicara §5.1 (P13: 1 retiro y 6 rebajas)."""
    no_nombrados = [
        lote for lote, _ in clasificados
        if lote.categoria == CATEGORIA_LACTEOS and not _nombrado_en_52(lote.producto, lote.subcategoria)
    ]
    if not no_nombrados:
        return []
    nombres = sorted({lote.producto for lote in no_nombrados})
    nota = (
        f"§5.2 se aplicó a toda la categoría {CATEGORIA_LACTEOS} del catálogo; la lista de {DOCUMENTO} §5.2 ({LISTA_52}) "
        f"no nombra: {', '.join(nombres)}"
    )
    vigente = [c for _, c in clasificados]
    alterna = [
        _clasificar_con(lote.dias, DIAS_RETIRO_PERECEDEROS, "§5.1") if lote in no_nombrados else c
        for lote, c in clasificados
    ]
    if [c.accion for c in alterna] != [c.accion for c in vigente]:
        nota += f". Con §5.1 para ellos serían {_retiros_y_rebajas(alterna)}"
    return [nota]


def _notas_de_retiro(categorias_y_dias: Iterable[tuple[str, int]]) -> list[str]:
    """El choque de §6 con el día de retiro, si algo llega justo a ese día."""
    pares = list(categorias_y_dias)
    notas = []
    lacteos_en_retiro = any(categoria == CATEGORIA_LACTEOS and dias == DIAS_RETIRO_LACTEOS for categoria, dias in pares)
    if lacteos_en_retiro:
        notas.append(
            f"la fila de {DIAS_RETIRO_LACTEOS} días ({ESCALA_REBAJA[DIAS_RETIRO_LACTEOS]}%) de §6 choca con §5.2 y §6.1, que "
            f"coinciden: los lácteos salen del módulo de rebaja al llegar a {DIAS_RETIRO_LACTEOS} días (§6.1, p. 5) y se retiran (§5.2)"
        )
    otros_en_retiro = any(categoria != CATEGORIA_LACTEOS and dias == DIAS_RETIRO_PERECEDEROS for categoria, dias in pares)
    if otros_en_retiro:
        notas.append(
            f"la fila de {DIAS_RETIRO_PERECEDEROS} día ({ESCALA_REBAJA[DIAS_RETIRO_PERECEDEROS]}%) de §6 cae en el día "
            "de retiro de §5.1; con criterio propio manda §5.1, porque §6.1 registra como Caducidad, «sin excepción», lo que llega a ese día"
        )
    return notas


def _nota_de_otra_accion(clasificados: Sequence[tuple[Lote, Clasificacion]], pedida: str) -> list[str]:
    """Lo que las cifras cuentan y las filas no traen, porque pidieron otra acción: sin esto, la respuesta no puede nombrarlo."""
    if pedida == "todas":
        return []
    otros = [(lote, c) for lote, c in clasificados if c.accion not in (pedida, "ninguna")]
    if not otros:
        return []
    if len(otros) > MAXIMO_DE_LOTES_EN_LA_NOTA:
        return [f"fuera de la acción pedida hay {_contar(len(otros), 'lote', 'lotes')} más: se ven con accion todas"]
    lista = "; ".join(_lote_en_la_nota(lote, c) for lote, c in otros)
    return [f"fuera de la acción pedida, y dentro de la ventana: {lista}"]


def _lote_en_la_nota(lote: Lote, clasificacion: Clasificacion) -> str:
    descuento = f" {clasificacion.descuento}%" if clasificacion.descuento is not None else ""
    return f"{lote.producto} en {lote.tienda}, caduca el {fecha_larga(lote.caducidad)}: {clasificacion.accion}{descuento}"


TABLAS_SEPARADAS = "Pon los retiros y las rebajas en tablas separadas."
FECHA_DE_RETIRO_DE_LAS_REBAJAS = (
    "En la tabla de rebajas, cada lote lleva la columna «Acción hoy», con la acción al corte y su descuento "
    "(«Rebajar 50%»), y la columna «Retiro si no se vende», con su fecha_retiro: el día que sale del anaquel si no se vende."
)


def _como_presentar(filas: Sequence[dict[str, str]]) -> dict[str, str]:
    """Lo que el modelo hace con las filas al contestar: una rebaja sin su fecha de retiro se lee como si siguiera a la venta."""
    acciones = {fila["accion"] for fila in filas}
    if "rebajar" not in acciones:
        return {}
    indicacion = FECHA_DE_RETIRO_DE_LAS_REBAJAS
    if "retirar" in acciones:
        indicacion = f"{TABLAS_SEPARADAS} {indicacion}"
    return {"antes_de_contestar": indicacion}


def _notas_de_reglas(clasificados: Sequence[tuple[Lote, Clasificacion]]) -> list[str]:
    notas = _notas_de_retiro((lote.categoria, lote.dias) for lote, _ in clasificados)
    caducados = sum(1 for lote, _ in clasificados if lote.dias < 0)
    if caducados:
        notas.append(f"{_contar(caducados, 'lote ya pasó', 'lotes ya pasaron')} su fecha de caducidad al corte")
    return notas


# ------------------------------------------------------------------ herramienta

def _skus_pedidos(entrada: EntradaCaducidades, productos: Productos | None, catalogos: Catalogos) -> list[str] | None:
    """Los SKU del filtro de producto, o None si no hay filtro."""
    if entrada.sku is None:
        return productos.skus if productos else None
    if entrada.sku not in catalogos.productos:
        raise EntidadInexistente("producto", entrada.sku)
    if productos is not None and entrada.sku not in productos.skus:
        raise EntidadInexistente("producto", f"{entrada.producto} ({entrada.sku})")
    return [entrada.sku]


def _resumen(lotes: Sequence[Lote], clasificaciones: Sequence[Clasificacion], corte: dt.date) -> str:
    if not lotes:
        return "No hay lotes con esos filtros en el inventario"
    if not clasificaciones:
        return "Ningún lote con fecha de caducidad registrada"
    total = _contar(len(clasificaciones), "lote", "lotes")
    return f"{total}: {_retiros_y_rebajas(clasificaciones)} al corte del {fecha_larga(corte)}"


PAGINA_DE_LA_REGLA_DE_RETIRO = 3  # §5.1 y §5.2


def _dia_antes(dia: str, dias: int) -> str:
    return DIAS_NOMBRE[(DIAS_NOMBRE.index(dia) - dias) % len(DIAS_NOMBRE)]


def _fila_del_dia_dado(categoria: str, dia: str) -> dict[str, str]:
    dias_retiro, regla = _regla_de_retiro(categoria)
    return {
        "categoria": categoria,
        "caduca": dia,
        "retiro": _dia_antes(dia, dias_retiro),
        "ultimo_dia_venta": _dia_antes(dia, dias_retiro + 1),
        "regla": regla,
    }


def _categorias_perecederas(con: duckdb.DuckDBPyConnection, skus: list[str] | None, categoria: str | None) -> list[str]:
    """Las categorías con algo perecedero en lo pedido: la regla de retiro va por categoría."""
    filtro, parametro = ("list_contains(CAST(? AS VARCHAR[]), sku)", skus) if skus is not None else ("categoria = ?", categoria)
    sql = f"SELECT DISTINCT categoria FROM catalogo WHERE {filtro} AND perecedero ORDER BY categoria"
    return [fila["categoria"] for fila in consultar(con, sql, [parametro])]


def _notas_de_alcance_del_dia_dado(entrada: EntradaCaducidades, ids: Sequence[str], catalogos: Catalogos, s: Supuestos) -> list[str]:
    """Una tienda que POL-OPS-014 §1.1 no cubre lleva su salvedad, igual que sus lotes (D2-C)."""
    notas = []
    for id_ in ids if entrada.tiendas else ():
        tienda = catalogos.tiendas[id_]
        alcance = s.alcance(DOCUMENTO, tienda["formato"])
        if not alcance.cubre:
            cierre = "le aplico la misma regla como supuesto" if alcance.por_supuesto else "la regla se da como referencia"
            notas.append(f"{tienda['nombre']} ({id_}): {alcance.salvedad}; {cierre}")
    return notas


def _fila_de_los_dias_dados(categoria: str, dias: int) -> dict[str, str]:
    clasificacion = clasificar(categoria, dias)
    fila = {"categoria": categoria, "dias": entero(dias), "accion": clasificacion.accion, "regla": clasificacion.regla}
    if clasificacion.descuento is not None:
        fila["descuento"] = f"{clasificacion.descuento}%"
    return fila


def _resumen_de_una_fila(fila: dict[str, str]) -> str:
    if "caduca" in fila:
        return f"{fila['categoria']} que caduca el {fila['caduca']}: retiro el {fila['retiro']}, último día de venta el {fila['ultimo_dia_venta']}"
    descuento = f" {fila['descuento']}" if "descuento" in fila else ""
    return f"{fila['categoria']} a {fila['dias']} días de caducar: {fila['accion']}{descuento} ({fila['regla']})"


def _resumen_del_dia_dado(filas: Sequence[dict[str, str]]) -> str:
    return "; ".join(_resumen_de_una_fila(fila) for fila in filas)


def _con_dia_dado(
    con: duckdb.DuckDBPyConnection, entrada: EntradaCaducidades, skus: list[str] | None, categoria: str | None,
    ids: Sequence[str], catalogos: Catalogos, notas: list[str], corte: dt.date, s: Supuestos,
) -> Resultado:
    if skus is None and categoria is None:
        resumen, categorias = "Para contar desde ese día hace falta el producto o la categoría", []
    else:
        categorias = _categorias_perecederas(con, skus, categoria)
        resumen = "El catálogo lo marca como no perecedero: POL-OPS-014 no le fija retiro"
    dias = entrada.caduca_en_dias
    if dias is None:
        filas = [_fila_del_dia_dado(nombre, entrada.caduca_el) for nombre in categorias]
        filtros: dict[str, Filtro] = {"caduca_el": entrada.caduca_el}
    else:
        filas = [_fila_de_los_dias_dados(nombre, dias) for nombre in categorias]
        filtros = {"caduca_en_dias": dias}
        notas = [*notas, *_notas_de_retiro((nombre, dias) for nombre in categorias)]
    if categoria:
        filtros["categoria"] = categoria
    if skus is not None:
        filtros["skus"] = list(skus)
    return Resultado(
        herramienta=HERRAMIENTA,
        estado="ok" if filas else "sin_resultados",
        resumen=_resumen_del_dia_dado(filas) if filas else resumen,
        unidad="días de la semana",
        definicion=(
            f"contado desde el día de caducidad que dio quien pregunta, sin leer lotes. {DOCUMENTO}: {CATEGORIA_LACTEOS} del catálogo "
            f"{DIAS_RETIRO_LACTEOS} días antes (§5.2, p. {PAGINA_DE_LA_REGLA_DE_RETIRO}), otros perecederos {DIAS_RETIRO_PERECEDEROS} día antes "
            f"(§5.1, p. {PAGINA_DE_LA_REGLA_DE_RETIRO}); el último día de venta es el anterior al retiro. Rebaja (§6): {_escala()}"
        ),
        filtros=filtros,
        corte=corte,
        filas=filas,
        n_filas=len(filas),
        notas=[*notas, *_notas_de_alcance_del_dia_dado(entrada, ids, catalogos, s)],
        fuente=FUENTE_DEL_DIA_DADO,
        detalle={"antes_de_contestar": "Este resultado no lee lotes: no digas qué productos o lotes están a esos días."} if filas else {},
    )


def _regla_de_los_dias_de_una_accion(
    con: duckdb.DuckDBPyConnection, entrada: EntradaCaducidades, skus: list[str] | None, categoria: str | None,
) -> list[str] | None:
    """None si los días cuentan desde quien pregunta. Una acción sin producto pide lotes: esos días no los filtran.

    «Qué tengo que retirar» es una lista de lotes y no se contesta con la regla de un lote imaginario. La regla de esos días
    se conserva en una nota, por si la pregunta también era esa.
    """
    if entrada.accion == "todas" or entrada.caduca_en_dias is None or skus is not None:
        return None
    categorias = _categorias_perecederas(con, None, categoria) if categoria else []
    filas = [_fila_de_los_dias_dados(nombre, entrada.caduca_en_dias) for nombre in categorias]
    return [f"un lote de {fila['categoria']} a {fila['dias']} días: {_accion_con_descuento(fila)}" for fila in filas]


def _plazo(dias: int) -> str:
    """«hoy», «en 1 día» o «en 3 días»: lo que dijo quien pregunta, en palabras."""
    if dias == 0:
        return "hoy"
    return "en 1 día" if dias == 1 else f"en {dias} días"


def _nota_de_los_dias_que_no_filtran(entrada: EntradaCaducidades, mostrados: Sequence[tuple[Lote, Any]], ventana: int) -> str:
    """Una acción con días y sin producto trae la lista de la ventana: esos días no la filtran, y la nota lo dice (RC-27).

    También cuántos de esos lotes caducan justo en esos días, para que la cifra de la lista no se lea como la respuesta.
    """
    plazo = _plazo(entrada.caduca_en_dias)
    if not mostrados:
        return f"la lista no se filtró por lo que caduca {plazo}, y en la ventana de {ventana} días no hay lotes a {entrada.accion}"
    justos = sum(lote.dias == entrada.caduca_en_dias for lote, _ in mostrados)
    verbo = "caduca" if justos == 1 else "caducan"
    cuantos = f"{justos} de ellos {verbo} {plazo}" if justos else f"ninguno caduca {plazo}"
    lotes = "el lote" if len(mostrados) == 1 else f"los {len(mostrados)} lotes"
    return f"la lista no se filtró por lo que caduca {plazo}: trae {lotes} a {entrada.accion} de la ventana de {ventana} días; {cuantos}"


def _accion_con_descuento(fila: dict[str, str]) -> str:
    descuento = f" {fila['descuento']}" if "descuento" in fila else ""
    return f"{fila['accion']}{descuento} ({fila['regla']})"


def _filtros_pedidos(entrada: EntradaCaducidades) -> dict[str, Filtro]:
    return entrada.model_dump(exclude_none=True, exclude_defaults=True)


def _calcular(con: duckdb.DuckDBPyConnection, entrada: EntradaCaducidades, hoy_real: dt.date) -> Resultado:
    s = supuestos()
    corte = corte_de("caducidades")
    catalogos = cargar_catalogos(con)
    tiendas = resolver_tiendas(entrada.tiendas, catalogos)
    ids = tiendas_validas(tiendas.ids)
    categoria = resolver_categoria(entrada.categoria, catalogos)
    productos = resolver_productos(entrada.producto, catalogos, s)
    skus = _skus_pedidos(entrada, productos, catalogos)
    regla_de_los_dias = _regla_de_los_dias_de_una_accion(con, entrada, skus, categoria)
    if regla_de_los_dias is None and (entrada.caduca_el is not None or entrada.caduca_en_dias is not None):
        notas_de_pedido = [*tiendas.notas, *(productos.notas if productos else [])]
        return _con_dia_dado(con, entrada, skus, categoria, ids, catalogos, notas_de_pedido, corte, s)
    # Un producto puntual da todos sus lotes con su calendario (P12); una categoría o subcategoría dicha
    # como producto ("lácteos", "yogurt") es una lista de lotes, como la categoría.
    puntual = entrada.sku is not None or (productos is not None and not productos.por_grupo)
    ventana = entrada.ventana_dias
    if ventana is None and not puntual:
        ventana = VENTANA_POLITICA

    sql, parametros = _consulta(corte, ids, skus, categoria, ventana)
    lotes = [Lote(**fila) for fila in consultar(con, sql, parametros)]
    con_fecha = [lote for lote in lotes if lote.caducidad is not None]
    sin_fecha = [lote for lote in lotes if lote.caducidad is None]
    clasificados = [(lote, clasificar(lote.categoria, lote.dias)) for lote in con_fecha]
    mostrados = [(l, c) for l, c in clasificados if entrada.accion in ("todas", c.accion)]

    acciones = [c.accion for _, c in clasificados]
    cifras = {
        "lotes": entero(len(clasificados)),
        "lotes_a_retirar": entero(acciones.count("retirar")),
        "lotes_a_rebajar": entero(acciones.count("rebajar")),
    }
    filas = [_fila(lote, c, puntual, con_categoria=categoria is None) for lote, c in mostrados[: entrada.limite]]

    notas = [*tiendas.notas, *(productos.notas if productos else []), *(regla_de_los_dias or [])]
    if regla_de_los_dias is not None:
        notas.append(_nota_de_los_dias_que_no_filtran(entrada, mostrados, ventana))
    notas += _nota_de_otra_accion(clasificados, entrada.accion)
    notas += _nota_sin_fecha(sin_fecha)
    notas += _nota_alcance(clasificados, s)
    notas += _nota_lista_52(clasificados)
    notas += _notas_de_reglas(clasificados)
    if filas:
        notas.append("existencia es la del SKU en la tienda al corte; el inventario no dice cuántas piezas son del lote próximo: pueden ser todas o solo una parte")
    if len(mostrados) > entrada.limite:
        notas.append(f"se muestran {entrada.limite} de {len(mostrados)} lotes")
    notas.append(aviso_fecha_relativa(hoy_real, s))

    filtros: dict[str, Filtro] = {"accion": entrada.accion, "ventana_dias": ventana}
    if regla_de_los_dias is not None:
        filtros["caduca_en_dias"] = entrada.caduca_en_dias
    if entrada.tiendas:
        filtros["tiendas"] = list(ids)
    if categoria:
        filtros["categoria"] = categoria
    if skus is not None:
        filtros["skus"] = list(skus)

    return Resultado(
        herramienta=HERRAMIENTA,
        estado="ok" if lotes else "sin_resultados",
        resumen=_resumen(lotes, [c for _, c in clasificados], corte),
        unidad="lotes",
        definicion=_definicion(corte, ventana),
        filtros={clave: valor for clave, valor in filtros.items() if valor is not None},
        corte=corte,
        cifras=cifras,
        filas=filas,
        n_filas=len(mostrados),
        notas=notas,
        fuente=FUENTE,
        consulta=_consulta_con_parametros(sql, parametros),
        detalle=_como_presentar(filas),
    )


def caducidades(con: duckdb.DuckDBPyConnection, entrada: EntradaCaducidades, hoy_real: dt.date | None = None) -> Resultado:
    """Lotes a retirar y a rebajar al corte del inventario según POL-OPS-014.

    con es el cursor de la llamada (Consultas.sesion()). Una tienda, un producto o una categoría que
    no existe, o una ciudad con dos tiendas, vuelve como Resultado con el estado del error con tipo.
    hoy_real es la fecha del reloj: los días de un lote se cuentan desde el corte, y la nota lo avisa (D3-A).
    """
    return ejecutar(HERRAMIENTA, lambda: _calcular(con, entrada, hoy_real or dt.date.today()), _filtros_pedidos(entrada))
