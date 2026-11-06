"""Carga config/supuestos.yaml: definiciones, cortes y alcance en un solo lugar (T1.1).

Las herramientas y la carga no repiten ninguna regla: piden a este módulo la condición SQL o el
cálculo. Así, cambiar una línea del YAML cambia la cifra sin tocar el prompt ni el código (C1.4).
Las condiciones SQL solo usan nombres de columna fijos y el operador validado, nunca texto libre
del YAML.
"""

from __future__ import annotations

import calendar
import datetime as dt
import math
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator, model_validator

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config"

MESES_NOMBRE = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                "septiembre", "octubre", "noviembre", "diciembre"]
DIAS_NOMBRE = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
HORAS_DE_UN_DIA = 24


class Estricto(BaseModel):
    """Rechaza claves que el modelo no conoce: una errata en el YAML es error, no un valor ignorado."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _hora(hora: dt.time) -> str:
    return f"{hora.hour}:{hora.minute:02d}"


def _columna(tabla: str | None, columna: str) -> str:
    return f"{tabla}.{columna}" if tabla else columna


class Corte(Estricto):
    fuente: str
    origen: str
    desde: dt.date | None = None
    hasta: dt.date | None = None
    fecha: dt.date | None = None


class Cortes(Estricto):
    ventas: Corte
    tickets: Corte
    inventario: Corte


class DefVentas(Estricto):
    incluir_devoluciones: bool
    definicion: str
    canales: list[str]
    canal_ecommerce_formatos: list[str]
    sumar_sustitutos: bool

    def condicion(self, tabla: str | None = None) -> str:
        """Filas de ventas que entran a la venta neta, como condición SQL para un WHERE."""
        if self.incluir_devoluciones:
            return "TRUE"
        return f"{_columna(tabla, 'unidades')} > 0"

    @staticmethod
    def condicion_devolucion(tabla: str | None = None) -> str:
        """Las devoluciones son las filas con unidades negativas (diccionario de datos)."""
        return f"{_columna(tabla, 'unidades')} < 0"

    def canal_existe(self, formato: str, canal: str) -> bool:
        if canal not in self.canales:
            return False
        if canal == "Ecommerce":
            return formato in self.canal_ecommerce_formatos
        return True


class DefUnidadVenta(Estricto):
    patron_kg: str
    definicion: str

    @field_validator("patron_kg")
    @classmethod
    def _patron_valido(cls, valor: str) -> str:
        re.compile(valor)
        return valor

    def de(self, nombre: str) -> Literal["kg", "pieza"]:
        """kg si el producto se vende a granel; pieza si se vende por presentación."""
        return "kg" if re.search(self.patron_kg, nombre.strip()) else "pieza"

    def compatibles(self, nombres: Iterable[str]) -> bool:
        """True si todos los productos se venden en la misma unidad y se pueden sumar."""
        return len({self.de(nombre) for nombre in nombres}) <= 1


class DefMargen(Estricto):
    supuesto: str


class DefInventario(Estricto):
    operador_reorden: Literal["<", "<="]

    def bajo_reorden(self, existencia: int, punto_reorden: int) -> bool:
        if self.operador_reorden == "<":
            return existencia < punto_reorden
        return existencia <= punto_reorden

    def condicion(self, tabla: str | None = None) -> str:
        existencia = _columna(tabla, "existencia")
        punto = _columna(tabla, "punto_reorden")
        return f"{existencia} {self.operador_reorden} {punto}"


class DefCobertura(Estricto):
    mes_base: int
    pesos_varianza: dict[int, float]
    z: float
    probabilidad: str
    metodo: str

    @field_validator("pesos_varianza")
    @classmethod
    def _pesos_suman_uno(cls, pesos: dict[int, float]) -> dict[int, float]:
        if not math.isclose(sum(pesos.values()), 1.0, abs_tol=1e-9):
            raise ValueError(f"los pesos de varianza deben sumar 1 y suman {sum(pesos.values())}")
        return pesos


class HorarioHabil(Estricto):
    """La ventana de D5-A: en qué horas y qué días corre el reloj de las prioridades en días hábiles."""

    desde: dt.time
    hasta: dt.time
    dias_semana: frozenset[int]
    prioridades: tuple[str, ...]
    origen: str

    @field_validator("hasta")
    @classmethod
    def _cierra_despues_de_abrir(cls, hasta: dt.time, info: ValidationInfo) -> dt.time:
        desde = info.data.get("desde")
        if desde is not None and hasta <= desde:
            raise ValueError(f"la ventana cierra a las {hasta} y abre a las {desde}")
        return hasta

    @field_validator("dias_semana")
    @classmethod
    def _dias_validos(cls, dias: frozenset[int]) -> frozenset[int]:
        if not dias or not dias <= frozenset(range(7)):
            raise ValueError(f"dias_semana va de 0 (lunes) a 6 y no puede estar vacío: {sorted(dias)}")
        return dias

    @property
    def horas_por_dia(self) -> float:
        hoy = dt.date.min
        return (dt.datetime.combine(hoy, self.hasta) - dt.datetime.combine(hoy, self.desde)).total_seconds() / 3600

    def horas_entre(self, inicio: dt.datetime, fin: dt.datetime) -> float:
        """Horas de la ventana entre dos momentos: el reloj se detiene de noche y en los días que no cuentan."""
        segundos = 0.0
        dia = inicio.date()
        while dia <= fin.date():
            if dia.weekday() in self.dias_semana:
                abre = max(inicio, dt.datetime.combine(dia, self.desde))
                cierra = min(fin, dt.datetime.combine(dia, self.hasta))
                segundos += max((cierra - abre).total_seconds(), 0.0)
            dia += dt.timedelta(days=1)
        return segundos / 3600

    @property
    def definicion(self) -> str:
        """El supuesto tal como lo lee la gerente, armado con la ventana: cambiar el YAML cambia el texto."""
        dias = (
            "todos los días" if len(self.dias_semana) == len(DIAS_NOMBRE)
            else ", ".join(DIAS_NOMBRE[dia] for dia in sorted(self.dias_semana))
        )
        return (
            f"se toma como horario hábil de {_hora(self.desde)} a {_hora(self.hasta)} {dias}, {self.origen}, "
            "porque ningún documento define qué es hábil; "
            f"aplica a {' y '.join(self.prioridades)}, y las demás prioridades van en horas naturales"
        )


class DefTickets(Estricto):
    sin_cerrar: str
    cerrado: str
    antiguedad_decimales: int
    objetivo_solucion_horas: dict[str, float]
    horario_habil: HorarioHabil | None
    primera_respuesta_medible: bool

    @model_validator(mode="after")
    def _prioridades_conocidas(self) -> "DefTickets":
        if self.horario_habil is not None:
            desconocidas = set(self.horario_habil.prioridades) - set(self.objetivo_solucion_horas)
            if desconocidas:
                raise ValueError(f"prioridades sin tiempo objetivo en horario_habil: {sorted(desconocidas)}")
        return self

    @staticmethod
    def condicion_sin_cerrar(tabla: str | None = None) -> str:
        return f"{_columna(tabla, 'fecha_cierre')} IS NULL"

    @staticmethod
    def condicion_cerrado(tabla: str | None = None) -> str:
        return f"{_columna(tabla, 'fecha_cierre')} IS NOT NULL"

    def cumple_objetivo(self, prioridad: str, horas: float) -> bool:
        """Cumple el ticket cerrado en el tiempo objetivo o antes, en horas naturales."""
        return horas <= self.objetivo_solucion_horas[prioridad]

    def objetivo_habil(self, prioridad: str) -> float | None:
        """Los días de FAQ-OPS-001 P20 en horas de la ventana; None si la prioridad va en horas naturales."""
        horario = self.horario_habil
        if horario is None or prioridad not in horario.prioridades:
            return None
        return self.objetivo_solucion_horas[prioridad] / HORAS_DE_UN_DIA * horario.horas_por_dia

    def cumple_en_horario_habil(self, prioridad: str, creacion: dt.datetime, cierre: dt.datetime) -> bool:
        """D5-A: cuenta las horas dentro de la ventana; lo que no va en días hábiles, en horas naturales."""
        objetivo = self.objetivo_habil(prioridad)
        if objetivo is None:
            return self.cumple_objetivo(prioridad, (cierre - creacion).total_seconds() / 3600)
        return self.horario_habil.horas_entre(creacion, cierre) <= objetivo


class Duda(Estricto):
    opcion: str | None
    recomendada: str
    estado: Literal["abierta", "cerrada"]
    texto: str


class AlcanceDocumento(Estricto):
    duda: str
    formatos_por_opcion: dict[str, list[str]]
    opciones_por_supuesto: list[str]
    salvedad: str


class Alcance(Estricto):
    cubre: bool
    salvedad: str | None = None
    por_supuesto: bool = False


class Supuestos(Estricto):
    version: str
    cortes: Cortes
    fecha_referencia: dt.date
    aviso_fecha_relativa: str
    ventas: DefVentas
    unidad_venta: DefUnidadVenta
    margen: DefMargen
    inventario: DefInventario
    cobertura: DefCobertura
    tickets: DefTickets
    metricas_no_calculables: dict[str, str]
    dudas: dict[str, Duda]
    alcance_documentos: dict[str, AlcanceDocumento]

    def fin_corte_tickets(self) -> dt.datetime:
        """El fin del último día del corte de tickets: el 31 de agosto a las 24:00."""
        siguiente = self.cortes.tickets.hasta + dt.timedelta(days=1)
        return dt.datetime.combine(siguiente, dt.time())

    def antiguedad_dias(self, fecha_creacion: dt.datetime) -> float:
        """Días con dos decimales entre la creación y el fin del corte, nunca contra el reloj (P29)."""
        dias = (self.fin_corte_tickets() - fecha_creacion).total_seconds() / 86400
        return round(dias, self.tickets.antiguedad_decimales)

    def dias_mes_base(self) -> int:
        """Días de calendario del mes base de cobertura, del año del corte de ventas."""
        return calendar.monthrange(self.cortes.ventas.hasta.year, self.cobertura.mes_base)[1]

    def media_diaria_base(self, unidades_mes_base: float) -> float:
        """Venta diaria de D20-A: unidades netas del mes base entre todos sus días de calendario."""
        return unidades_mes_base / self.dias_mes_base()

    def opcion_vigente(self, duda: str) -> str:
        """La respuesta de la duda o, mientras siga sin respuesta, su opción recomendada."""
        registro = self.dudas[duda]
        return registro.opcion or registro.recomendada

    def alcance(self, documento: str, formato: str) -> Alcance:
        """Si el texto del documento cubre el formato con la opción vigente de su duda; KeyError si no tiene alcance declarado.

        Si no lo cubre, por_supuesto dice si la regla se aplica igual, aclarando el supuesto, o queda solo como orientación.
        """
        declarado = self.alcance_documentos[documento]
        opcion = self.opcion_vigente(declarado.duda)
        if formato in declarado.formatos_por_opcion[opcion]:
            return Alcance(cubre=True)
        return Alcance(cubre=False, salvedad=declarado.salvedad, por_supuesto=opcion in declarado.opciones_por_supuesto)


def cargar(ruta: Path | None = None) -> Supuestos:
    ruta = ruta or CONFIG / "supuestos.yaml"
    with open(ruta, encoding="utf-8") as f:
        return Supuestos.model_validate(yaml.safe_load(f))


@lru_cache(maxsize=1)
def supuestos() -> Supuestos:
    return cargar()


def cargar_yaml(nombre: str) -> dict:
    with open(CONFIG / nombre, encoding="utf-8") as f:
        return yaml.safe_load(f)


def fecha_larga(fecha: dt.date) -> str:
    return f"{fecha.day} de {MESES_NOMBRE[fecha.month - 1]} de {fecha.year}"


def ayer(s: Supuestos | None = None) -> dt.date:
    s = s or supuestos()
    return s.fecha_referencia - dt.timedelta(days=1)


def aviso_fecha_relativa(fecha_real: dt.date, s: Supuestos | None = None) -> str:
    """Texto que acompaña toda respuesta con fecha relativa (D3-A, P50)."""
    s = s or supuestos()
    return s.aviso_fecha_relativa.format(hoy=fecha_larga(s.fecha_referencia), fecha_real=fecha_larga(fecha_real))


def skus_equivalentes(sku: str, sustitutos: Mapping[str, str | None], s: Supuestos | None = None) -> set[str]:
    """El SKU con toda su cadena de sustitución, hacia adelante y hacia atrás.

    sustitutos va de cada SKU del catálogo a su sku_sustituto (None o vacío si no tiene). Con
    sumar_sustitutos en false devuelve solo el SKU pedido.
    """
    s = s or supuestos()
    if not s.ventas.sumar_sustitutos:
        return {sku}
    vecinos: dict[str, set[str]] = {}
    for origen, destino in sustitutos.items():
        if destino:
            vecinos.setdefault(origen, set()).add(destino)
            vecinos.setdefault(destino, set()).add(origen)
    encontrados = {sku}
    pendientes = [sku]
    while pendientes:
        actual = pendientes.pop()
        for vecino in vecinos.get(actual, set()) - encontrados:
            encontrados.add(vecino)
            pendientes.append(vecino)
    return encontrados
