"""Contrato común de las herramientas: entrada validada, salida compacta y error con tipo (T2.1, K10 a K13).

Entrada. Cada herramienta recibe un solo modelo de Pydantic (EntradaVentas, EntradaInventario,
EntradaCaducidades, EntradaTickets, EntradaPoliticas). Pydantic AI usa su esquema como el de la
herramienta y valida los argumentos antes de llamarla: un valor fuera del enum o un campo que la
herramienta no conoce no la ejecuta y vuelve al modelo como error de validación. Métrica, agrupación,
grano, canal, estado, categoría y prioridad de tickets son enums. La tienda, el producto, la
categoría de producto y el periodo llegan como el texto del usuario porque los resuelve
agente/resolver.py (T2.8), que da la interpretación en las notas y el error con tipo de una entidad
inexistente o ambigua; lo que llega a la consulta es un id de T01 a T04 (tiendas_validas) o un SKU.
Las fechas explícitas van como AAAA-MM-DD, y en_corte las deja dentro del corte de su fuente o da el
error con tipo fuera_de_periodo.

Salida. Resultado lleva estado, unidad, definición, filtros, corte en AAAA-MM-DD, cifras ya
formateadas con coma de miles y punto decimal, hasta 50 filas, número de filas, notas, fuente y la
consulta SQL. El modelo recibe texto_modelo(): JSON compacto sin la consulta ni los campos vacíos. El
registro recibe texto_registro(), con la consulta. Los dos pasan por dumps(), que no escribe NaN ni
Infinity (K12).

Consultas. Cada llamada de herramienta toma su propio cursor con Consultas.sesion() de
agente/consultas.py, porque Pydantic AI corre en paralelo las herramientas de una misma respuesta, y
lo pasa a consultar(), que manda los valores como parámetros y convierte la división entre cero de
DuckDB (que devuelve inf o nan con / y falla con //) en el error con tipo division_entre_cero.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import typing
import unicodedata
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Annotated, Any, Literal

import duckdb
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from agente.definiciones import MESES_NOMBRE, supuestos
from agente.definiciones import fecha_larga as _fecha_larga_con_dia

MAX_FILAS = 50

Estado = Literal[
    "ok",
    "metrica_no_calculable",
    "entidad_ambigua",
    "entidad_inexistente",
    "fuera_de_periodo",
    "periodo_no_entendido",
    "canal_inexistente",
    "fuente_no_disponible",
    "documento_no_incluido",
    "sin_resultados",
    "division_entre_cero",
    "accion_no_permitida",
]


class ErrorHerramienta(Exception):
    """Error con tipo: llega al modelo antes de la redacción y nunca trae una cifra inventada."""

    def __init__(self, estado: Estado, mensaje: str, **detalle: Any):
        super().__init__(mensaje)
        self.estado = estado
        self.mensaje = mensaje
        self.detalle = detalle


# ------------------------------------------------------------------ tipos de entrada

def _clave(texto: str) -> str:
    """Sin acentos, sin mayúsculas y sin espacios a los lados, para comparar valores de un enum."""
    descompuesto = unicodedata.normalize("NFKD", texto.strip().casefold())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _canonico(tipo: Any) -> Any:
    """El enum que acepta un valor sin acentos ni mayúsculas y lo cambia por su forma exacta.

    "critica" pasa como "Crítica"; un valor que no es del enum sigue siendo error de validación.
    """
    por_clave = {_clave(valor): valor for valor in typing.get_args(tipo)}

    def validar(valor: Any) -> Any:
        if isinstance(valor, str):
            return por_clave.get(_clave(valor), valor)
        return valor

    return Annotated[tipo, BeforeValidator(validar)]


_PATRON_FECHA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _fecha_iso(valor: Any) -> Any:
    """Solo AAAA-MM-DD: Pydantic aceptaría también un número de segundos o una fecha con hora."""
    if isinstance(valor, dt.datetime):
        raise ValueError("la fecha va como AAAA-MM-DD, sin hora")
    if isinstance(valor, dt.date):
        return valor
    if isinstance(valor, str) and _PATRON_FECHA.fullmatch(valor):
        return valor
    raise ValueError("la fecha va como AAAA-MM-DD")


Fecha = Annotated[dt.date, BeforeValidator(_fecha_iso)]

TiendaId = Literal["T01", "T02", "T03", "T04"]
Canal = Literal["Piso", "Ecommerce"]
EstadoTicket = Literal["Abierto", "En proceso", "Resuelto", "Cerrado"]
CategoriaTicket = Literal["TI", "Mantenimiento", "RH", "Inventario", "Cajas"]
Prioridad = Literal["Baja", "Media", "Alta", "Crítica"]

Sku = Annotated[str, StringConstraints(pattern=r"^SKU-[0-9]{4}$")]
CodigoDocumento = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}-[A-Z]{3}-[0-9]{3}$")]
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _sin_control(texto: str) -> str:
    """Rechaza saltos de línea y otros caracteres de control.

    Va como validador y no como pattern del esquema para no repetir la expresión en cada campo de
    texto que lee el modelo.
    """
    if _CONTROL.search(texto):
        raise ValueError("el texto no puede traer saltos de línea ni caracteres de control")
    return texto


TextoEntidad = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80), AfterValidator(_sin_control)
]
TextoBusqueda = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300), AfterValidator(_sin_control)
]

# Las métricas no calculables de config/supuestos.yaml están en el enum para que pedirlas dé el error
# con tipo metrica_no_calculable (T2.2) y no un error de validación.
MetricaVentas = Literal[
    "venta", "unidades", "devoluciones", "venta_por_m2", "margen",
    "ticket_promedio", "venta_por_hora", "meta_venta",
]
AgrupacionVentas = Literal["tienda", "producto", "categoria", "canal", "dia_semana"]
Grano = Literal["total", "mes", "semana", "dia"]
Orden = Literal["mayor", "menor"]
MetricaInventario = Literal[
    "existencia", "bajo_reorden", "valuacion", "cobertura", "merma_real", "inventario_historico",
]
AgrupacionInventario = Literal["tienda", "producto", "categoria"]
AccionCaducidad = Literal["retirar", "rebajar", "todas"]
DiaDeLaSemana = Literal["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MetricaTickets = Literal[
    "conteo", "antiguedad", "cumplimiento", "mas_frecuentes", "buscar", "detalle", "primera_respuesta",
]
AgrupacionTickets = Literal["estado", "tienda", "categoria", "prioridad"]
SituacionTicket = Literal["sin_cerrar", "cerrado"]

_TIENDAS = "nombre, ciudad o id (T01 a T04) como lo dijo el usuario; vacío es todas"
_PERIODO = "periodo como lo dijo el usuario (julio, ayer, la última semana, el trimestre)"

_ADAPTADOR_TIENDAS = TypeAdapter(list[TiendaId])


def tiendas_validas(ids: Sequence[str]) -> list[TiendaId]:
    """Los ids que resolvió el resolvedor, validados de T01 a T04 antes de ir a la consulta (K10)."""
    return _ADAPTADOR_TIENDAS.validate_python(list(ids))


class Entrada(BaseModel):
    """Base de las entradas: un campo que la herramienta no conoce es error de validación."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ConPeriodo(Entrada):
    periodo: TextoEntidad | None = Field(None, description=_PERIODO)
    desde: Fecha | None = None
    hasta: Fecha | None = None

    @model_validator(mode="after")
    def _desde_antes_de_hasta(self) -> ConPeriodo:
        if self.desde and self.hasta and self.desde > self.hasta:
            raise ValueError(f"desde ({self.desde}) es posterior a hasta ({self.hasta})")
        return self


class EntradaVentas(ConPeriodo):
    """Ventas netas de junio a agosto de 2026. Para cuánto subió o bajó entre dos periodos usa periodo y comparar_con: el resultado trae la variación en %, no la calcules. Para qué porcentaje del total es una tienda, categoría, canal, producto o mes usa agrupar_por o grano: cada fila trae pct_del_total, no lo calcules. Ante un por qué sobre un día, llama también con ese día y agrupar_por categoria o canal: sí se puede desglosar."""

    metrica: MetricaVentas = Field("venta", description="la ganancia, o cuánto se ganó, es margen; no es venta")
    agrupar_por: AgrupacionVentas | None = None
    grano: Grano = "total"
    tiendas: list[TextoEntidad] = Field(default_factory=list, max_length=8, description=_TIENDAS)
    producto: TextoEntidad | None = None
    categoria: TextoEntidad | None = None
    canal: _canonico(Canal) | None = None
    comparar_con: TextoEntidad | None = Field(None, description="otro periodo para comparar; trae la variación en %")
    orden: Orden = "mayor"
    limite: int = Field(10, ge=1, le=MAX_FILAS)


class EntradaInventario(Entrada):
    """Inventario al corte del 1 de septiembre de 2026."""

    metrica: MetricaInventario = "existencia"
    agrupar_por: AgrupacionInventario | None = None
    tiendas: list[TextoEntidad] = Field(default_factory=list, max_length=8, description=_TIENDAS)
    producto: TextoEntidad | None = None
    sku: Sku | None = None
    categoria: TextoEntidad | None = None
    orden: Orden = "mayor"
    limite: int = Field(10, ge=1, le=MAX_FILAS)


class EntradaCaducidades(Entrada):
    """Lotes por retirar o por rebajar al corte del inventario. Llámala también si preguntan hasta cuándo se vende un producto o qué descuento o retiro le toca: con producto trae el retiro, el último día de venta y la rebaja de sus lotes. Si dicen qué día de la semana caduca, pásalo en caduca_el, y si dicen cuántos días le faltan, en caduca_en_dias: cuenta desde ahí, y no los lotes de la tienda. No pidas los días: salen del lote."""

    accion: AccionCaducidad = "todas"
    tiendas: list[TextoEntidad] = Field(default_factory=list, max_length=8, description=_TIENDAS)
    producto: TextoEntidad | None = None
    sku: Sku | None = None
    categoria: TextoEntidad | None = None
    ventana_dias: int | None = Field(None, ge=0, le=30, description="días antes de caducar; vacío usa la política")
    caduca_el: _canonico(DiaDeLaSemana) | None = Field(None, description="día de la semana en que caduca, si la pregunta lo dice")
    caduca_en_dias: int | None = Field(None, ge=0, le=30, description="días que le faltan para caducar, si la pregunta lo dice; mañana es 1")
    limite: int = Field(MAX_FILAS, ge=1, le=MAX_FILAS)


class EntradaTickets(ConPeriodo):
    """Tickets de la mesa de servicio al 31 de agosto de 2026. Ante un por qué sobre una venta o una fecha, antes de decir que no hay causa llama con metrica buscar y la fecha en texto, sin periodo: el periodo filtra por la creación y un ticket de antes puede mencionarla. Si preguntan si una prioridad está bien puesta, llama también a buscar_politicas con el tipo de falla."""

    metrica: MetricaTickets = Field(
        "conteo",
        description="detalle trae cada ticket con su descripción: con filtros, todos los que cumplan, sin pedir antes los folios",
    )
    agrupar_por: AgrupacionTickets | None = None
    tiendas: list[TextoEntidad] = Field(default_factory=list, max_length=8, description=_TIENDAS)
    situacion: SituacionTicket | None = None
    estados: list[_canonico(EstadoTicket)] = Field(
        default_factory=list, max_length=4,
        description="solo si la pregunta nombra un estado; para tickets cerrados o sin cerrar usa situacion",
    )
    categoria: _canonico(CategoriaTicket) | None = None
    prioridad: _canonico(Prioridad) | None = None
    texto: TextoBusqueda | None = Field(None, description="palabras a buscar en título y descripción, o una fecha (14 de marzo)")
    ticket_id: int | None = Field(None, ge=1, le=99999)
    limite: int = Field(10, ge=1, le=MAX_FILAS)


class EntradaPoliticas(Entrada):
    """Fragmentos de las políticas y manuales, con documento, sección y página."""

    consulta: TextoBusqueda = Field(description="con el producto y las palabras de quien pregunta; no los cambies por algo más general")
    documento: CodigoDocumento | None = Field(None, description="código del documento, como POL-OPS-014")


# ------------------------------------------------------------------ periodo dentro del corte

FuenteConCorte = Literal["ventas", "tickets", "inventario", "caducidades"]


def _limites_corte(fuente: FuenteConCorte) -> tuple[dt.date | None, dt.date]:
    cortes = supuestos().cortes
    if fuente == "ventas":
        return cortes.ventas.desde, cortes.ventas.hasta
    if fuente == "tickets":
        return cortes.tickets.desde, cortes.tickets.hasta
    return cortes.inventario.fecha, cortes.inventario.fecha


def corte_de(fuente: FuenteConCorte) -> dt.date:
    """Fecha del corte de cada resultado (K13): ventas y tickets, su último día; inventario y caducidades, la foto."""
    return _limites_corte(fuente)[1]


def en_corte(desde: dt.date | None, hasta: dt.date | None, fuente: FuenteConCorte) -> tuple[dt.date | None, dt.date, str]:
    """El periodo recortado al corte de la fuente, con la nota del recorte; sin fechas es todo el corte.

    Lanza fuera_de_periodo si no toca ningún día del corte, con los límites del corte en el detalle.
    """
    inicio, fin = _limites_corte(fuente)
    pedido_desde = desde or inicio
    pedido_hasta = hasta or fin
    fuera_por_arriba = pedido_desde is not None and pedido_desde > fin
    fuera_por_abajo = inicio is not None and pedido_hasta < inicio
    if fuera_por_arriba or fuera_por_abajo:
        raise ErrorHerramienta(
            "fuera_de_periodo",
            f"No hay datos de {fuente} en ese periodo",
            corte_desde=inicio.isoformat() if inicio else None,
            corte_hasta=fin.isoformat(),
        )
    recortado_desde = max(pedido_desde, inicio) if inicio and pedido_desde else pedido_desde
    recortado_hasta = min(pedido_hasta, fin)
    if (recortado_desde, recortado_hasta) == (pedido_desde, pedido_hasta):
        return recortado_desde, recortado_hasta, ""
    desde_texto = f"del {fecha_larga(recortado_desde)} " if recortado_desde else ""
    return recortado_desde, recortado_hasta, f"solo hay datos {desde_texto}al {fecha_larga(recortado_hasta)}"


# ------------------------------------------------------------------ salida común

Celda = str | None
Filtro = str | int | bool | dt.date | list[str] | None


class Resultado(BaseModel):
    """Salida común. Las cifras y las celdas van ya formateadas: un número sin formato es error."""

    herramienta: str
    estado: Estado = "ok"
    resumen: str = ""
    unidad: str | None = None
    definicion: str | None = None
    filtros: dict[str, Filtro] = Field(default_factory=dict)
    corte: Fecha | None = None
    cifras: dict[str, str] = Field(default_factory=dict)
    filas: list[dict[str, Celda]] = Field(default_factory=list)
    n_filas: int = Field(0, ge=0)
    notas: list[str] = Field(default_factory=list)
    fuente: str | None = None
    consulta: str | None = None
    detalle: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _recortar_filas(cls, datos: Any) -> Any:
        """Deja 50 filas como máximo; n_filas guarda cuántas devolvió la consulta y una nota lo dice."""
        if not isinstance(datos, dict):
            return datos
        filas = list(datos.get("filas") or [])
        total = max(datos.get("n_filas") or 0, len(filas))
        datos = {**datos, "n_filas": total}
        if len(filas) > MAX_FILAS:
            datos["filas"] = filas[:MAX_FILAS]
            datos["notas"] = [*(datos.get("notas") or []), f"se muestran {MAX_FILAS} de {total} filas"]
        return datos

    def para_modelo(self) -> dict[str, Any]:
        """Lo que ve el modelo: sin la consulta SQL, que solo va a la traza y al registro, y sin campos vacíos."""
        datos = self.model_dump(mode="json", exclude={"consulta"}, exclude_none=True)
        if not datos.get("filas"):
            # Un total sin desglose no tiene filas: «0 filas» se lee como que la cifra no tiene sustento.
            datos.pop("n_filas", None)
        return {clave: valor for clave, valor in datos.items() if valor not in ({}, [], "")}

    def texto_modelo(self) -> str:
        """El JSON compacto que devuelve la herramienta al modelo."""
        return dumps(self.para_modelo(), compacto=True)

    def texto_registro(self) -> str:
        """El JSON completo, con la consulta SQL, para el registro del turno."""
        return dumps(self.model_dump(mode="json"))


def error(herramienta: str, e: ErrorHerramienta, filtros: dict[str, Filtro] | None = None) -> Resultado:
    return Resultado(herramienta=herramienta, estado=e.estado, resumen=e.mensaje,
                     filtros=filtros or {}, detalle=e.detalle)


def ejecutar(herramienta: str, calcular: Callable[[], Resultado], filtros: dict[str, Filtro] | None = None) -> Resultado:
    """Corre el cálculo y convierte un ErrorHerramienta en un Resultado con su estado.

    Un error sin tipo (un KeyError, un error de DuckDB) es un defecto y se deja pasar.
    """
    try:
        return calcular()
    except ErrorHerramienta as e:
        return error(herramienta, e, filtros)


# ------------------------------------------------------------------ consultas

def _no_finito(valor: Any) -> bool:
    if isinstance(valor, float):
        return not math.isfinite(valor)
    if isinstance(valor, Decimal):
        return not valor.is_finite()
    return False


def _error_division() -> ErrorHerramienta:
    return ErrorHerramienta("division_entre_cero", "El cálculo divide entre cero; no hay cifra")


def consultar(con: duckdb.DuckDBPyConnection, sql: str, parametros: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """Corre SQL fijo con los valores como parámetros y devuelve las filas como diccionarios.

    con es el cursor de la llamada (Consultas.sesion()). El resultado se lee completo con fetchall()
    para no dejar ocupada la conexión. Un inf o nan en el resultado, o la división entera entre cero
    de DuckDB, es el error con tipo division_entre_cero (K12).
    """
    try:
        cursor = con.execute(sql, list(parametros))
        columnas = [descripcion[0] for descripcion in cursor.description]
        filas = [dict(zip(columnas, fila)) for fila in cursor.fetchall()]
    except duckdb.InvalidInputException as e:
        if "Division by zero" in str(e):
            raise _error_division() from e
        raise
    for fila in filas:
        if any(_no_finito(valor) for valor in fila.values()):
            raise _error_division()
    return filas


# ------------------------------------------------------------------ formato de cifras

def num(valor: int | float | Decimal, decimales: int = 2) -> str:
    """1234567.5 -> '1,234,567.50'. Coma de miles y punto decimal fijos, sin depender del locale."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float, Decimal)):
        raise TypeError(f"num espera un número, no {type(valor).__name__}")
    if _no_finito(valor):
        raise _error_division()
    texto = f"{valor:,.{decimales}f}"
    if texto.startswith("-") and not any(c in "123456789" for c in texto):
        texto = texto[1:]
    return texto


def entero(valor: int | float | Decimal) -> str:
    """Un conteo con coma de miles; un valor con decimales es un error de programación."""
    texto = num(valor, 0)
    if valor != int(valor):
        raise ValueError(f"entero recibió {valor}, que tiene decimales")
    return texto


def pct(valor: int | float | Decimal, decimales: int = 2) -> str:
    """Una proporción como porcentaje: 0.3209 -> '32.09%'."""
    return f"{num(valor * 100, decimales)}%"


def dividir(a: int | float | Decimal, b: int | float | Decimal) -> float:
    """a / b, o el error con tipo division_entre_cero si b es cero (K12)."""
    if b == 0:
        raise _error_division()
    resultado = a / b
    if _no_finito(resultado):
        raise _error_division()
    return resultado


def fecha_larga(fecha: dt.date, con_dia: bool = True) -> str:
    """'4 de julio de 2026', o 'julio de 2026' sin el día. Los meses son los de agente/definiciones.py."""
    if con_dia:
        return _fecha_larga_con_dia(fecha)
    return f"{MESES_NOMBRE[fecha.month - 1]} de {fecha.year}"


# ------------------------------------------------------------------ JSON y peso

def _a_json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (dt.date, dt.datetime)):
        return valor.isoformat()
    raise TypeError(f"No serializable: {type(valor)}")


def dumps(datos: Any, compacto: bool = False) -> str:
    """JSON sin NaN ni Infinity (K12): allow_nan=False falla en lugar de escribirlos."""
    separadores = (",", ":") if compacto else None
    return json.dumps(datos, ensure_ascii=False, allow_nan=False, default=_a_json, separators=separadores)


_PIEZAS = re.compile(r"[^\W\d_]+|\S")


def estimar_tokens(texto: str) -> int:
    """Tokens estimados por arriba, sin red: cada dígito o signo es uno, y cada palabra uno por cada 3 letras.

    El conteo exacto es el de count_tokens del proveedor, que pide la llave; el tokenizador junta
    dígitos y letras en piezas más grandes, así que esta cuenta da más que la real en el texto de
    los resultados.
    """
    total = 0
    for pieza in _PIEZAS.findall(texto):
        letras = pieza[0].isalpha()
        total += math.ceil(len(pieza) / 3) if letras else 1
    return total
