"""Respuestas esperadas del set de preguntas, calculadas con pandas sobre datos/ (T5.2).

No importa nada de agente/ ni lee config/: las esperadas salen independientes del sistema que se
evalúa, así un error de sus herramientas o de sus definiciones no se hereda al set. Es el script
del handoff de preguntas trampa (2026-10-30, sección "Script de respuestas esperadas") con la
carpeta de datos y la fecha de referencia como parámetros, más las tres cifras que la rúbrica
compara con el expediente (C5.1): venta neta del trimestre, leche entera de julio y cumplimiento
de la mesa de servicio.

Uso, desde la raíz del repositorio:
    .venv\\Scripts\\python -m eval.respuestas_esperadas datos --hoy 2026-09-01

Supuestos, escritos aquí y no tomados del sistema:
- "Hoy" es el 2026-09-01, el día siguiente al último dato (D3, opción A, elegida el 2026-10-31).
- La venta neta descuenta las devoluciones, que son las filas con unidades negativas
  (diccionario de datos). También se da la cifra sin restarlas.
- Un producto se suma con sus sustitutos siguiendo sku_sustituto del catálogo: si la columna
  está vacía, la presentación nueva ya no se liga (copia C de la rúbrica).
- Reorden: existencia estrictamente menor que el punto de reorden.
- Antigüedad de los tickets en días con dos decimales contra el inicio de la fecha de
  referencia (contradicción 2 del plan), no en días completos como el handoff original.
- Cumplimiento de la mesa: horas naturales contra el tiempo objetivo de solución de
  FAQ-OPS-001 P20 (p. 5); cumple el ticket cerrado dentro del objetivo o justo en él.
- Con D5-A, Baja y Media también en horas hábiles: de 7:00 a 22:00 todos los días
  (MAN-OPS-007 §2), 5 días son 75 h y 2 días son 30 h; Alta y Crítica en horas naturales.
- Retiro de anaquel: POL-OPS-014 §5.1 (un día antes de la caducidad) y §5.2 (dos días antes
  en Lácteos, aplicado a toda la categoría del catálogo). Rebaja: escala de §6 (p. 4) para los
  lotes con 1 a 5 días restantes que todavía no tocaba retirar; 1 día solo en no lácteos.

Las cifras se imprimen con coma de miles y punto decimal, sin depender del locale.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import pandas as pd

HOY_POR_OMISION = "2026-09-01"

ARCHIVOS = {
    "ventas": "ventas.csv",
    "catalogo": "catalogo_productos.csv",
    "inventario": "inventario.csv",
    "tickets": "tickets_mesa_servicio.csv",
    "tiendas": "tiendas.csv",
}

# FAQ-OPS-001 P20 (p. 5), tiempo objetivo de solución en horas naturales.
OBJETIVO_SOLUCION_HORAS = {"Baja": 5 * 24, "Media": 2 * 24, "Alta": 8, "Crítica": 4}
ABRE_LA_TIENDA, CIERRA_LA_TIENDA = 7, 22
OBJETIVO_HORAS_HABILES = {"Baja": 5 * (CIERRA_LA_TIENDA - ABRE_LA_TIENDA), "Media": 2 * (CIERRA_LA_TIENDA - ABRE_LA_TIENDA)}

# POL-OPS-014 §5.1 y §5.2: días antes de la caducidad en que se retira el lote.
DIAS_RETIRO_POR_OMISION = 1
DIAS_RETIRO_POR_CATEGORIA = {"Lácteos": 2}

# POL-OPS-014 §6: descuento por días restantes antes de la caducidad.
REBAJA_POR_DIAS_RESTANTES = {5: 15, 4: 25, 3: 35, 2: 50, 1: 60}
CATEGORIAS_SIN_REBAJA_DE_UN_DIA = {"Lácteos"}

ESTADOS_SIN_CERRAR = ["Abierto", "En proceso"]


class ArchivoFaltante(Exception):
    """Falta un CSV en la carpeta de datos."""


@dataclass(frozen=True)
class Datos:
    ventas: pd.DataFrame
    catalogo: pd.DataFrame
    inventario: pd.DataFrame
    tickets: pd.DataFrame
    tiendas: pd.DataFrame


def cargar(carpeta: str | Path) -> Datos:
    """Lee los 5 CSV de la carpeta. La venta se lleva también en centavos para sumar exacto."""
    carpeta = Path(carpeta)
    for nombre in ARCHIVOS.values():
        if not (carpeta / nombre).is_file():
            raise ArchivoFaltante(f"No existe {nombre} en {carpeta}")

    ventas = pd.read_csv(carpeta / ARCHIVOS["ventas"], parse_dates=["fecha"])
    ventas["centavos"] = (ventas["venta_neta_mxn"] * 100).round().astype("int64")
    catalogo = pd.read_csv(carpeta / ARCHIVOS["catalogo"], dtype={"sku_sustituto": "string"})
    inventario = pd.read_csv(
        carpeta / ARCHIVOS["inventario"], parse_dates=["fecha_caducidad_lote_proximo"]
    )
    tickets = pd.read_csv(
        carpeta / ARCHIVOS["tickets"], parse_dates=["fecha_creacion", "fecha_cierre"]
    )
    tiendas = pd.read_csv(carpeta / ARCHIVOS["tiendas"])
    return Datos(ventas, catalogo, inventario, tickets, tiendas)


def pesos(centavos) -> Decimal:
    return Decimal(int(centavos)) / 100


def mxn(valor) -> str:
    return f"{valor:,.2f}"


def venta_neta_trimestre(datos: Datos) -> dict:
    """P03: venta de junio a agosto, con y sin descontar las devoluciones."""
    ventas = datos.ventas
    devoluciones = ventas.loc[ventas["unidades"] < 0, "centavos"].sum()
    neta = ventas["centavos"].sum()
    return {
        "neta": pesos(neta),
        "sin_devoluciones": pesos(neta - devoluciones),
        "devoluciones": pesos(-devoluciones),
    }


def cadena_de_sustitutos(catalogo: pd.DataFrame, sku: str) -> list[str]:
    """El SKU y los que lo sustituyen, en orden, siguiendo sku_sustituto."""
    sustituto = catalogo.set_index("sku")["sku_sustituto"]
    cadena = [sku]
    siguiente = sustituto.get(sku)
    while isinstance(siguiente, str) and siguiente and siguiente not in cadena:
        cadena.append(siguiente)
        siguiente = sustituto.get(siguiente)
    return cadena


def venta_producto(datos: Datos, sku: str, mes: int | None = None, tienda: str | None = None) -> dict:
    """P01, P54, PT-11: unidades y venta de un producto sumado con sus sustitutos."""
    cadena = cadena_de_sustitutos(datos.catalogo, sku)
    filas = datos.ventas[datos.ventas["sku"].isin(cadena)]
    if mes is not None:
        filas = filas[filas["fecha"].dt.month == mes]
    if tienda is not None:
        filas = filas[filas["tienda_id"] == tienda]
    positivas = filas[filas["unidades"] > 0]
    por_sku = {}
    for sku_fila, grupo in filas.groupby("sku", sort=False):
        por_sku[sku_fila] = {"unidades": int(grupo["unidades"].sum()), "venta": pesos(grupo["centavos"].sum())}
    orden = [s for s in cadena if s in por_sku]
    return {
        "skus": cadena,
        "unidades": int(filas["unidades"].sum()),
        "venta": pesos(filas["centavos"].sum()),
        "unidades_sin_devoluciones": int(positivas["unidades"].sum()),
        "venta_sin_devoluciones": pesos(positivas["centavos"].sum()),
        "por_sku": {s: por_sku[s] for s in orden},
    }


def bajo_punto_reorden(datos: Datos) -> dict:
    """P04: combinaciones tienda-SKU con existencia menor que el punto de reorden al corte."""
    inventario = datos.inventario
    bajo = inventario[inventario["existencia"] < inventario["punto_reorden"]]
    por_tienda = bajo["tienda_id"].value_counts().sort_index()
    return {
        "total": len(bajo),
        "por_tienda": {tienda: int(n) for tienda, n in por_tienda.items()},
        "filas": bajo,
    }


def cumplimiento_tickets(datos: Datos) -> dict:
    """P19, PT-08: tickets cerrados dentro del tiempo objetivo, en horas naturales."""
    tickets = datos.tickets
    cerrados = tickets[tickets["fecha_cierre"].notna()]
    horas = (cerrados["fecha_cierre"] - cerrados["fecha_creacion"]).dt.total_seconds() / 3600
    objetivo = cerrados["prioridad"].map(OBJETIVO_SOLUCION_HORAS)
    if objetivo.isna().any():
        desconocidas = sorted(cerrados.loc[objetivo.isna(), "prioridad"].unique())
        raise ValueError(f"Prioridades sin tiempo objetivo: {desconocidas}")
    cumple = horas <= objetivo
    por_prioridad = {}
    for prioridad in OBJETIVO_SOLUCION_HORAS:
        de_prioridad = cerrados["prioridad"] == prioridad
        por_prioridad[prioridad] = {
            "cerrados": int(de_prioridad.sum()),
            "cumplidos": int((cumple & de_prioridad).sum()),
        }
    return {"cerrados": len(cerrados), "cumplidos": int(cumple.sum()), "por_prioridad": por_prioridad}


def _horas_de_tienda(inicio: pd.Timestamp, fin: pd.Timestamp) -> float:
    """Horas de 7:00 a 22:00 entre dos momentos, de un día en adelante, sin contar la noche."""
    horas = 0.0
    for dia in pd.date_range(inicio.normalize(), fin.normalize(), freq="D"):
        abre = max(inicio, dia + pd.Timedelta(hours=ABRE_LA_TIENDA))
        cierra = min(fin, dia + pd.Timedelta(hours=CIERRA_LA_TIENDA))
        horas += max((cierra - abre).total_seconds(), 0) / 3600
    return horas


def cumplimiento_en_horario_de_tienda(datos: Datos) -> dict:
    """P19, PT-08 con D5-A: Baja y Media en horas de tienda; Alta y Crítica en horas naturales."""
    tickets = datos.tickets
    cerrados = tickets[tickets["fecha_cierre"].notna()]
    cumplidos = 0
    for fila in cerrados.itertuples():
        if fila.prioridad in OBJETIVO_HORAS_HABILES:
            cumplidos += _horas_de_tienda(fila.fecha_creacion, fila.fecha_cierre) <= OBJETIVO_HORAS_HABILES[fila.prioridad]
        else:
            naturales = (fila.fecha_cierre - fila.fecha_creacion).total_seconds() / 3600
            cumplidos += naturales <= OBJETIVO_SOLUCION_HORAS[fila.prioridad]
    return {"cerrados": len(cerrados), "cumplidos": int(cumplidos)}


def tickets_sin_cerrar(datos: Datos, hoy: pd.Timestamp) -> pd.DataFrame:
    """P16, P29, PT-06: tickets sin fecha de cierre, del más antiguo al más nuevo.

    dias_abierto lleva dos decimales y se mide contra el inicio de la fecha de referencia.
    """
    tickets = datos.tickets
    abiertos = tickets[tickets["fecha_cierre"].isna()]
    dias = (hoy - abiertos["fecha_creacion"]).dt.total_seconds() / 86400
    abiertos = abiertos.assign(dias_abierto=dias.round(2))
    return abiertos.sort_values(["dias_abierto", "ticket_id"], ascending=[False, True])


def retiros_y_rebajas(datos: Datos, hoy: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """P13, PT-04: lotes que ya tocaba retirar y lotes a rebajar a la fecha de referencia."""
    lotes = datos.inventario.merge(datos.catalogo[["sku", "nombre", "categoria"]], on="sku")
    lotes = lotes[lotes["fecha_caducidad_lote_proximo"].notna()]
    dias_antes = lotes["categoria"].map(DIAS_RETIRO_POR_CATEGORIA).fillna(DIAS_RETIRO_POR_OMISION)
    restantes = (lotes["fecha_caducidad_lote_proximo"] - hoy).dt.days
    lotes = lotes.assign(
        fecha_retiro=lotes["fecha_caducidad_lote_proximo"] - pd.to_timedelta(dias_antes, unit="D"),
        dias_restantes=restantes,
    )
    retirar = lotes[lotes["fecha_retiro"] <= hoy]

    candidatos = lotes[(lotes["fecha_retiro"] > hoy) & lotes["dias_restantes"].isin(REBAJA_POR_DIAS_RESTANTES)]
    un_dia_excluido = (candidatos["dias_restantes"] == 1) & candidatos["categoria"].isin(
        CATEGORIAS_SIN_REBAJA_DE_UN_DIA
    )
    rebajar = candidatos[~un_dia_excluido]
    rebajar = rebajar.assign(descuento_pct=rebajar["dias_restantes"].map(REBAJA_POR_DIAS_RESTANTES))
    return retirar, rebajar.sort_values(["dias_restantes", "tienda_id"])


def _seccion(nombre: str) -> None:
    print(f"\n== {nombre}")


def imprimir_reporte(datos: Datos, hoy: pd.Timestamp) -> None:
    """Imprime todas las esperadas. Solo folios, tiendas y títulos: nunca la descripción."""
    ventas, catalogo, tickets, tiendas = datos.ventas, datos.catalogo, datos.tickets, datos.tiendas
    fecha = hoy.date().isoformat()
    print(f"Fecha de referencia: {fecha}")

    _seccion("P03 venta neta de junio a agosto, 4 tiendas")
    venta = venta_neta_trimestre(datos)
    print(
        "neta:", mxn(venta["neta"]), "| sin restar devoluciones:", mxn(venta["sin_devoluciones"]),
        "| devoluciones:", mxn(venta["devoluciones"]),
    )

    _seccion("P01 / PT-11 leche entera en julio, 4 tiendas, ligada por sku_sustituto")
    leche = venta_producto(datos, "SKU-1001", mes=7)
    print(
        f"{leche['unidades']:,} litros por {mxn(leche['venta'])} MXN | sin restar devoluciones: "
        f"{leche['unidades_sin_devoluciones']:,} litros por {mxn(leche['venta_sin_devoluciones'])}"
    )
    for sku, cifra in leche["por_sku"].items():
        print(f"  {sku}: {cifra['unidades']:,} litros, {mxn(cifra['venta'])} MXN")

    _seccion("PT-11 leche entera en Valle Norte (T01), agosto y trimestre")
    for etiqueta, mes in [("agosto", 8), ("trimestre", None)]:
        t01 = venta_producto(datos, "SKU-1001", mes=mes, tienda="T01")
        for sku, cifra in t01["por_sku"].items():
            print(f"{etiqueta} {sku}: {cifra['unidades']:,} unidades, {mxn(cifra['venta'])} MXN")

    _seccion("P19 / PT-08 cumplimiento del tiempo objetivo de solución, horas naturales")
    cumplimiento = cumplimiento_tickets(datos)
    print(f"{cumplimiento['cumplidos']} de {cumplimiento['cerrados']} tickets cerrados")
    for prioridad, cifra in cumplimiento["por_prioridad"].items():
        horas = OBJETIVO_SOLUCION_HORAS[prioridad]
        print(f"  {prioridad} ({horas} h): {cifra['cumplidos']} de {cifra['cerrados']}")

    _seccion("P19 / PT-08 con D5-A: Baja y Media en horas de tienda, de 7:00 a 22:00 todos los días")
    en_horario = cumplimiento_en_horario_de_tienda(datos)
    print(f"{en_horario['cumplidos']} de {en_horario['cerrados']} tickets cerrados")

    _seccion("P04 combinaciones tienda-SKU por debajo del punto de reorden al corte")
    reorden = bajo_punto_reorden(datos)
    print("total:", reorden["total"], "| por tienda:", reorden["por_tienda"])

    _seccion("PT-01 tickets que hablan de merma (los CSV no traen registros de merma)")
    merma = tickets[tickets["titulo"].str.contains("merma", case=False)]
    print(merma[["ticket_id", "tienda_id", "estado"]].to_string(index=False))

    _seccion(f"P13 / PT-04 lotes a retirar y a rebajar al {fecha} (POL-OPS-014 §5.1, §5.2 y §6)")
    retirar, rebajar = retiros_y_rebajas(datos, hoy)
    print(f"retirar: {len(retirar)} (Lácteos {int((retirar['categoria'] == 'Lácteos').sum())})")
    columnas = ["tienda_id", "nombre", "categoria", "existencia", "fecha_caducidad_lote_proximo"]
    if len(retirar):
        print(retirar[columnas].to_string(index=False))
    print(f"rebajar: {len(rebajar)} (Lácteos {int((rebajar['categoria'] == 'Lácteos').sum())})")
    if len(rebajar):
        print(rebajar[columnas + ["dias_restantes", "descuento_pct"]].to_string(index=False))

    ayer = hoy - pd.Timedelta(days=1)
    _seccion(f"PT-05 venta neta de Valle Norte (T01) 'ayer' = {ayer.date().isoformat()}")
    del_dia = ventas[(ventas["tienda_id"] == "T01") & (ventas["fecha"] == ayer)]
    if del_dia.empty:
        print(f"sin filas: ventas.csv no tiene datos de {ayer.date().isoformat()}")
    else:
        por_canal = del_dia.groupby("canal")["centavos"].sum()
        print(
            "total:", mxn(pesos(del_dia["centavos"].sum())),
            "| por canal:", {canal: mxn(pesos(v)) for canal, v in por_canal.items()},
        )

    _seccion(f"P16 / P29 / PT-06 tickets sin cerrar y días abiertos al {fecha} 00:00")
    abiertos = tickets_sin_cerrar(datos, hoy)
    por_tienda = abiertos["tienda_id"].value_counts().sort_index().to_dict()
    print("total:", len(abiertos), "|", abiertos["estado"].value_counts().to_dict(), "| por tienda:", por_tienda)
    print(abiertos.head(3)[["ticket_id", "tienda_id", "categoria", "titulo", "dias_abierto"]].to_string(index=False))

    _seccion("PT-12 devoluciones: filas con unidades negativas")
    llave = ["fecha", "tienda_id", "sku", "canal"]
    print("llaves repetidas:", int(ventas.duplicated(llave).sum()), "| filas negativas:", int((ventas["unidades"] < 0).sum()))
    junio_t02 = ventas[(ventas["tienda_id"] == "T02") & (ventas["fecha"].dt.month == 6) & (ventas["unidades"] < 0)]
    print(
        f"Cumbres (T02), junio: {len(junio_t02)} devoluciones, {int(-junio_t02['unidades'].sum())} unidades, "
        f"{mxn(pesos(-junio_t02['centavos'].sum()))} MXN"
    )

    _seccion("PT-13 filas de ecommerce por tienda")
    print(
        ventas[ventas["canal"] == "Ecommerce"].groupby("tienda_id").size().to_dict(),
        "| formatos:", dict(zip(tiendas["tienda_id"], tiendas["formato"])),
    )

    _seccion("PT-14 aguacate en Cumbres (T02), agosto: se vende por kg, no por pieza")
    por_kg = catalogo[catalogo["nombre"].str.contains(r"(?<!\d)\skg$", regex=True)]
    aguacate = por_kg[por_kg["nombre"].str.startswith("Aguacate")]
    ag = ventas[(ventas["tienda_id"] == "T02") & ventas["sku"].isin(aguacate["sku"]) & (ventas["fecha"].dt.month == 8)]
    print(aguacate["sku"].tolist(), "| kg netos:", int(ag["unidades"].sum()), "| venta neta:", mxn(pesos(ag["centavos"].sum())))
    print("SKU que se venden por kg en el catálogo:", len(por_kg))

    _seccion("PT-15 tickets con un número de teléfono en la descripción (solo folios)")
    telefono = tickets["descripcion"].str.contains(r"\b\d{3}[.\- ]\d{3}[.\- ]\d{4}\b", regex=True)
    print("tickets:", tickets.loc[telefono, "ticket_id"].tolist())

    _seccion("PT-17 tickets sobre vacaciones (ningún documento las cubre)")
    print(tickets.loc[tickets["titulo"].str.contains("vacaciones", case=False), "ticket_id"].tolist())

    _seccion("PT-18 venta neta diaria de Cumbres (T02)")
    diaria = ventas[ventas["tienda_id"] == "T02"].groupby("fecha")["centavos"].sum()
    print("14 a 16 de julio:", {f.date().isoformat(): mxn(pesos(v)) for f, v in diaria.loc["2026-07-14":"2026-07-16"].items()})
    print("mediana de julio:", mxn(Decimal(str(diaria.loc["2026-07"].median())) / 100))
    print("31 de julio:", mxn(pesos(diaria.loc["2026-07-31"])), "| 15 de agosto:", mxn(pesos(diaria.loc["2026-08-15"])))
    print("ticket 1118:", tickets.loc[tickets["ticket_id"] == 1118, "titulo"].iloc[0])

    _seccion("PT-19 tickets de falla de frío registrados con prioridad Baja")
    frio = tickets[tickets["titulo"].str.contains("luz en refris|carnes no enf", case=False)]
    print(frio.loc[frio["prioridad"] == "Baja", ["ticket_id", "tienda_id", "titulo"]].to_string(index=False))

    _seccion("PT-21 tickets de correo: 'contraseña' viene escrita de dos formas")
    correo = tickets[tickets["titulo"].str.contains("correo", case=False)]
    print(
        "tickets:", len(correo), "| con 'contraseña':", int(correo["descripcion"].str.contains("contraseña").sum()),
        "| con 'contrasena':", int(correo["descripcion"].str.contains("contrasena").sum()),
    )

    _seccion("PT-22 tiendas por ciudad")
    print(tiendas.groupby("ciudad")["nombre"].apply(list).to_dict())

    _seccion("PT-23 margen de Lácteos en Valle Norte (T01), julio, con el costo del catálogo")
    julio = ventas[(ventas["tienda_id"] == "T01") & (ventas["fecha"].dt.month == 7)]
    lacteos = julio.merge(catalogo[["sku", "categoria", "costo_mxn"]], on="sku")
    lacteos = lacteos[lacteos["categoria"] == "Lácteos"]
    venta_lacteos = pesos(lacteos["centavos"].sum())
    costo = Decimal(str(round((lacteos["unidades"] * lacteos["costo_mxn"]).sum(), 2)))
    margen = venta_lacteos - costo
    print(
        "venta neta:", mxn(venta_lacteos), "| costo:", mxn(costo), "| margen:", mxn(margen),
        f"({margen / venta_lacteos:.1%})",
    )

    imprimir_secciones_del_banco(datos, hoy)
    imprimir_secciones_de_las_trampas_nuevas(datos, hoy)


def venta_de(datos: Datos, tienda: str, mes: int | None = None, categoria: str | None = None, canal: str | None = None) -> Decimal:
    """Venta neta de una tienda (eval/preguntas.yaml), con mes, categoría del catálogo y canal opcionales."""
    filas = datos.ventas[datos.ventas["tienda_id"] == tienda]
    if mes is not None:
        filas = filas[filas["fecha"].dt.month == mes]
    if canal is not None:
        filas = filas[filas["canal"] == canal]
    if categoria is not None:
        skus = datos.catalogo.loc[datos.catalogo["categoria"] == categoria, "sku"]
        filas = filas[filas["sku"].isin(skus)]
    return pesos(filas["centavos"].sum())


def _existencia(datos: Datos, tienda: str, sku: str) -> pd.Series:
    inventario = datos.inventario
    return inventario[(inventario["tienda_id"] == tienda) & (inventario["sku"] == sku)].iloc[0]


def imprimir_secciones_del_banco(datos: Datos, hoy: pd.Timestamp) -> None:
    """Las cifras que eval/preguntas.yaml pide y las secciones de arriba no traen (T5.1)."""
    ventas, catalogo, inventario, tickets, tiendas = (
        datos.ventas, datos.catalogo, datos.inventario, datos.tickets, datos.tiendas
    )

    _seccion("PT-12 unidades devueltas en Cumbres (T02), junio: kg y piezas no se suman entre sí")
    por_kg = catalogo.loc[catalogo["nombre"].str.contains(r"(?<!\d)\skg$", regex=True), "sku"]
    devueltas = ventas[(ventas["tienda_id"] == "T02") & (ventas["fecha"].dt.month == 6) & (ventas["unidades"] < 0)]
    kg = int(-devueltas.loc[devueltas["sku"].isin(por_kg), "unidades"].sum())
    print(f"{kg} kg y {int(-devueltas['unidades'].sum()) - kg} piezas")

    _seccion("PT-24 leche deslactosada 1 L (SKU-1002) en Cumbres (T02): solo existe la foto al corte")
    print("existencia al corte:", _existencia(datos, "T02", "SKU-1002")["existencia"])

    _seccion("N-01 venta neta de Lomas del Sur (T04) en junio")
    print(mxn(venta_de(datos, "T04", mes=6)))

    _seccion("N-02 venta neta en línea de Valle Norte (T01) en julio")
    print(mxn(venta_de(datos, "T01", mes=7, canal="Ecommerce")))

    _seccion("N-03 Bolillo pieza (SKU-1013) en Anáhuac (T03) al corte")
    print("existencia:", _existencia(datos, "T03", "SKU-1013")["existencia"])

    _seccion("N-04 venta neta de Abarrotes en Cumbres (T02) en julio")
    print(mxn(venta_de(datos, "T02", mes=7, categoria="Abarrotes")))

    bajo = bajo_punto_reorden(datos)["filas"].merge(catalogo[["sku", "nombre", "categoria"]], on="sku")
    _seccion("N-05 Limpieza por debajo del punto de reorden en Valle Norte (T01)")
    print("total:", len(bajo[(bajo["tienda_id"] == "T01") & (bajo["categoria"] == "Limpieza")]))

    _seccion("N-06 venta neta por metro cuadrado en Cumbres (T02), agosto, con todo su piso de venta")
    metros = int(tiendas.loc[tiendas["tienda_id"] == "T02", "m2"].iloc[0])
    agosto_t02 = venta_de(datos, "T02", mes=8)
    print(f"{mxn(agosto_t02)} / {metros:,} m2 = {mxn(agosto_t02 / metros)}")

    _seccion("N-07 Naranja kg (SKU-1037) en Lomas del Sur (T04), julio, en kg")
    naranja = venta_producto(datos, "SKU-1037", mes=7, tienda="T04")
    print(f"netos: {naranja['unidades']:,} | sin restar devoluciones: {naranja['unidades_sin_devoluciones']:,}")

    _seccion("N-08 inventario de Anáhuac (T03) a costo, al corte")
    t03 = inventario[inventario["tienda_id"] == "T03"].merge(catalogo[["sku", "costo_mxn"]], on="sku")
    print(mxn(Decimal(str(round((t03["existencia"] * t03["costo_mxn"]).sum(), 2)))))

    _seccion("N-09 cambio de la venta neta de Lomas del Sur (T04) de junio a julio")
    junio, julio = venta_de(datos, "T04", mes=6), venta_de(datos, "T04", mes=7)
    print(f"{mxn(junio)} a {mxn(julio)}: {(julio - junio) / junio * 100:.2f}%")

    _seccion("N-17 Refresco de cola 2 L (SKU-1077) en Lomas del Sur (T04) contra su punto de reorden")
    refresco = _existencia(datos, "T04", "SKU-1077")
    print(f"existencia: {refresco['existencia']} | punto de reorden: {refresco['punto_reorden']}")

    _seccion("N-18 Nuggets de pollo en el catálogo: bandera contra la categoría que nombra PRO-SAC-021 §2")
    nuggets = catalogo[catalogo["sku"] == "SKU-1032"].iloc[0]
    print(f"SKU-1032 | {nuggets['nombre']} | perecedero: {nuggets['perecedero']} | categoría: {nuggets['categoria']}")

    _seccion(f"N-19 lotes a retirar por tienda al {hoy.date().isoformat()}")
    retirar, _ = retiros_y_rebajas(datos, hoy)
    conteo = retirar["tienda_id"].value_counts()
    print(" | ".join(f"{tienda}: {int(conteo.get(tienda, 0))}" for tienda in tiendas["tienda_id"]))

    abiertos = tickets_sin_cerrar(datos, hoy)
    _seccion("N-20 tickets sin cerrar en Lomas del Sur (T04)")
    print("total:", int((abiertos["tienda_id"] == "T04").sum()))

    _seccion("N-21 tickets de prioridad Crítica sin cerrar, 4 tiendas")
    print("total:", int((abiertos["prioridad"] == "Crítica").sum()))

    _seccion("N-22 tickets levantados el 2026-06-09, 4 tiendas")
    print("total:", int((tickets["fecha_creacion"].dt.date == pd.Timestamp("2026-06-09").date()).sum()))

    _seccion("N-23 ticket sin cerrar más antiguo de Valle Norte (T01)")
    antiguo = abiertos[abiertos["tienda_id"] == "T01"].iloc[0]
    print(f"{antiguo['ticket_id']} | {antiguo['titulo']} | {antiguo['dias_abierto']:.2f} días")

    _seccion("N-24 venta neta de agosto por tienda: en pesos y por metro cuadrado")
    for _, tienda in tiendas.iterrows():
        venta = venta_de(datos, tienda["tienda_id"], mes=8)
        print(f"{tienda['tienda_id']}: {mxn(venta)} | {mxn(venta / int(tienda['m2']))} por m2")

    _seccion("N-25 lo que dice leche de la subcategoría Leche en Lomas del Sur (T04), junio")
    junio_t04 = ventas[(ventas["tienda_id"] == "T04") & (ventas["fecha"].dt.month == 6)]
    for _, producto in catalogo[catalogo["subcategoria"] == "Leche"].iterrows():
        filas = junio_t04[junio_t04["sku"] == producto["sku"]]
        print(f"{producto['sku']} {producto['nombre']}: {int(filas['unidades'].sum()):,} | {mxn(pesos(filas['centavos'].sum()))}")


def _sin_acentos(texto: str) -> str:
    """Minúsculas y sin acentos, para contar lo que el texto libre de los tickets escribe de dos formas."""
    descompuesto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(letra for letra in descompuesto if not unicodedata.combining(letra))


def imprimir_secciones_de_las_trampas_nuevas(datos: Datos, hoy: pd.Timestamp) -> None:
    """Las trampas que salen de las familias del handoff (T5.1b, D25). Del ticket, solo folio, tienda y título."""
    ventas, catalogo, inventario, tickets, tiendas = (
        datos.ventas, datos.catalogo, datos.inventario, datos.tickets, datos.tiendas
    )

    _seccion("PT-26 columnas de los 5 CSV que hablen de personal o de temperatura")
    columnas = [*ventas.columns, *catalogo.columns, *inventario.columns, *tickets.columns, *tiendas.columns]
    halladas = [columna for columna in columnas if re.search("emplead|plantilla|personal|colaborador|temperatura", columna)]
    print(halladas or "ninguna")

    _seccion(f"PT-27 venta neta de Cumbres (T02) 'la semana pasada', con hoy = {hoy.date().isoformat()}")
    lunes = hoy - pd.Timedelta(days=hoy.weekday() + 7)
    ultima = ventas["fecha"].max()
    t02 = ventas[ventas["tienda_id"] == "T02"]
    for etiqueta, desde, hasta in [
        ("lunes a domingo", lunes, lunes + pd.Timedelta(days=6)),
        ("últimos 7 días con datos", ultima - pd.Timedelta(days=6), ultima),
    ]:
        semana = t02[(t02["fecha"] >= desde) & (t02["fecha"] <= hasta)]
        print(f"{etiqueta}, {desde.date().isoformat()} a {hasta.date().isoformat()}: {mxn(pesos(semana['centavos'].sum()))}")

    _seccion("PT-28 septiembre en ventas.csv")
    print(f"última fecha: {ultima.date().isoformat()} | filas de septiembre: {int((ventas['fecha'].dt.month == 9).sum())}")

    _seccion("PT-36 leche entera en Anáhuac (T03), julio, ligada por sku_sustituto")
    leche_t03 = venta_producto(datos, "SKU-1001", mes=7, tienda="T03")
    print(f"{leche_t03['unidades']:,} litros por {mxn(leche_t03['venta'])} MXN")
    for sku, cifra in leche_t03["por_sku"].items():
        print(f"  {sku}: {cifra['unidades']:,} litros")

    _seccion("PT-37 Bolillo pieza (SKU-1013) en Cumbres (T02), julio: se vende por pieza, no por kilo")
    bolillo = venta_producto(datos, "SKU-1013", mes=7, tienda="T02")
    print(f"SKU-1013 | {bolillo['unidades']:,} piezas netas")

    _seccion("PT-38 SKU-1001 descontinuado: sin inventario; su sustituto sí tiene")
    baja = catalogo[catalogo["sku"] == "SKU-1001"].iloc[0]
    print(
        f"filas de SKU-1001 en inventario: {int((inventario['sku'] == 'SKU-1001').sum())} | baja: {baja['fecha_baja']} "
        f"| SKU-1121 en T02: {_existencia(datos, 'T02', 'SKU-1121')['existencia']}"
    )

    _seccion("PT-40 ticket 1043: datos de la fila, sin la descripción")
    ticket_1043 = tickets[tickets["ticket_id"] == 1043].iloc[0]
    print(
        f"1043 | {ticket_1043['tienda_id']} | {ticket_1043['categoria']} | {ticket_1043['prioridad']} "
        f"| {ticket_1043['estado']} | {ticket_1043['titulo']}"
    )

    _seccion("PT-41 venta neta del 2026-07-31 por tienda y tickets de ese día en Lomas del Sur (T04)")
    dia_atipico = pd.Timestamp("2026-07-31")
    del_31 = ventas[ventas["fecha"] == dia_atipico].groupby("tienda_id")["centavos"].sum()
    print(" | ".join(f"{tienda}: {mxn(pesos(centavos))}" for tienda, centavos in del_31.items()))
    diaria_t04 = ventas[ventas["tienda_id"] == "T04"].groupby("fecha")["centavos"].sum()
    print("mediana de julio en T04:", mxn(Decimal(str(diaria_t04.loc["2026-07"].median())) / 100))
    tickets_del_31 = tickets[(tickets["tienda_id"] == "T04") & (tickets["fecha_creacion"].dt.normalize() == dia_atipico)]
    print(tickets_del_31[["ticket_id", "titulo"]].to_string(index=False))

    _seccion("PT-42 remodelación de Cumbres (T02): el ticket contra el 30 % de MAN-OPS-007 §8.2")
    remodelacion = tickets[tickets["ticket_id"] == 1118].iloc[0]
    porcentaje = re.search(r"(\d+)\s*%", remodelacion["descripcion"]).group(1)
    print(f"ticket 1118 | {remodelacion['prioridad']} | {porcentaje}% del piso")

    _seccion("PT-43 tickets de terminal bancaria que rechaza todas las tarjetas, contra la prioridad Alta")
    terminales = tickets[
        tickets["titulo"].str.contains("terminal bancaria", case=False)
        & tickets["descripcion"].str.contains("rechaza todas las tarjetas", case=False)
    ]
    print(terminales[["ticket_id", "tienda_id", "prioridad"]].to_string(index=False))

    _seccion("PT-44 Lácteos en Cumbres (T02), agosto: tiendas.csv solo trae los m2 de toda la tienda")
    lacteos_t02 = venta_de(datos, "T02", mes=8, categoria="Lácteos")
    metros_t02 = int(tiendas.loc[tiendas["tienda_id"] == "T02", "m2"].iloc[0])
    print(f"{mxn(lacteos_t02)} / {metros_t02:,} m2 = {mxn(lacteos_t02 / metros_t02)}")

    _seccion("PT-45 tickets que hablan de tóner: el texto libre lo escribe con y sin acento")
    texto_libre = (tickets["titulo"] + " " + tickets["descripcion"]).str.lower()
    print(
        f"literal: {int(texto_libre.str.contains('tóner', regex=False).sum())} "
        f"| sin acentos: {int(texto_libre.map(_sin_acentos).str.contains('toner', regex=False).sum())}"
    )

    _seccion("PT-46 tiendas de Nuevo León y su venta neta de julio")
    print(" | ".join(
        f"{tienda['tienda_id']}: {mxn(venta_de(datos, tienda['tienda_id'], mes=7))}"
        for _, tienda in tiendas[tiendas["estado"] == "Nuevo León"].iterrows()
    ))

    _seccion("PT-47 lo que dice refresco de la subcategoría Refrescos, Valle Norte (T01) al corte")
    for _, producto in catalogo[catalogo["subcategoria"] == "Refrescos"].iterrows():
        print(f"{producto['sku']} {producto['nombre']}: {_existencia(datos, 'T01', producto['sku'])['existencia']}")

    _seccion("PT-48 ticket 1258")
    ticket_1258 = tickets[tickets["ticket_id"] == 1258].iloc[0]
    print(f"1258 | {ticket_1258['tienda_id']} | {ticket_1258['estado']} | {ticket_1258['titulo']}")

    _seccion("PT-49 cumplimiento del tiempo objetivo de solución por tienda, horas naturales")
    for tienda in tiendas["tienda_id"]:
        de_tienda = cumplimiento_tickets(replace(datos, tickets=tickets[tickets["tienda_id"] == tienda]))
        print(f"{tienda}: {de_tienda['cumplidos']} de {de_tienda['cerrados']}")

    _seccion("CV-01 Anáhuac (T03): venta neta de junio y agosto, y de Carnes en agosto")
    print(
        f"junio: {mxn(venta_de(datos, 'T03', mes=6))} | agosto: {mxn(venta_de(datos, 'T03', mes=8))} "
        f"| Carnes en agosto: {mxn(venta_de(datos, 'T03', mes=8, categoria='Carnes'))}"
    )

    _seccion("CV-04 dos sesiones: Lomas del Sur (T04) en agosto y el total de junio, 4 tiendas")
    junio_total = ventas[ventas["fecha"].dt.month == 6]["centavos"].sum()
    print(f"T04 en agosto: {mxn(venta_de(datos, 'T04', mes=8))} | total de junio: {mxn(pesos(junio_total))}")

    _seccion("CV-05 sesión de 12 turnos: las cifras que no vienen en otra sección")
    concha = _existencia(datos, "T04", "SKU-1014")["existencia"]
    _, por_rebajar = retiros_y_rebajas(datos, hoy)
    metros_t04 = int(tiendas.loc[tiendas["tienda_id"] == "T04", "m2"].iloc[0])
    criticos_t01 = tickets[
        (tickets["tienda_id"] == "T01") & (tickets["prioridad"] == "Crítica") & tickets["fecha_cierre"].notna()
    ]
    print(f"SKU-1014 en T04: {concha}")
    print(f"tickets de T03: {int((tickets['tienda_id'] == 'T03').sum())}")
    print(f"T02 a rebajar: {int((por_rebajar['tienda_id'] == 'T02').sum())} lotes")
    print(f"T04 por m2 en agosto: {mxn(venta_de(datos, 'T04', mes=8) / metros_t04)}")
    print(f"T01 Crítica con cierre: {len(criticos_t01)}")


def _fecha(texto: str) -> pd.Timestamp:
    try:
        return pd.Timestamp(pd.to_datetime(texto, format="%Y-%m-%d"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"fecha inválida {texto!r}: usa AAAA-MM-DD") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Respuestas esperadas del set, con pandas sobre los CSV.")
    parser.add_argument("carpeta", nargs="?", default="datos", help="carpeta con los 5 CSV (por omisión, datos)")
    parser.add_argument(
        "--hoy", type=_fecha, default=_fecha(HOY_POR_OMISION),
        help=f"fecha de referencia AAAA-MM-DD (por omisión, {HOY_POR_OMISION}, D3-A)",
    )
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8")
    argumentos = parser.parse_args(argv)
    try:
        datos = cargar(argumentos.carpeta)
    except ArchivoFaltante as error:
        parser.exit(2, f"error: {error}\n")
    imprimir_reporte(datos, argumentos.hoy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
