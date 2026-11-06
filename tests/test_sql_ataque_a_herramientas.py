"""Ataca las 5 herramientas por cada campo y cada métrica, y mira el SQL que de verdad llega a DuckDB.

Completa a tests/test_sql_sin_texto_del_usuario.py, que lee el código y sigue variables solo dentro de cada
función. Aquí no importa por cuántas funciones pase el valor: si la cadena del ataque aparece en el texto de
una sentencia, la prueba falla. Los campos y las métricas salen de los modelos de entrada, así que un campo
nuevo queda cubierto sin tocar este archivo.

Corre sobre una base temporal, abierta con la misma protección que producción. Un ataque que se rechaza al
validar o que no encuentra la tienda o el producto se detiene antes de llegar a las consultas de esa ruta: eso
es correcto, pero esa ruta no se ejerció con el ataque. Las de texto libre sí llegan, y una prueba lo comprueba.
"""

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import duckdb
import pytest
from pydantic import BaseModel, ValidationError

from agente import construir, consultas
from agente.herramientas import comun
from agente.herramientas import tickets as modulo_tickets
from agente.herramientas.buscar_politicas import buscar_politicas
from agente.herramientas.caducidades import caducidades
from agente.herramientas.inventario import inventario
from agente.herramientas.tickets import tickets
from agente.herramientas.ventas import ventas

DIR_DATOS = Path(__file__).resolve().parent.parent / "datos"
MARCA = "zziny"

ATAQUES = [
    f"x'; DROP TABLE ventas; -- {MARCA}",
    f"' OR '1'='1 {MARCA}",
    f"T01' UNION SELECT ticket_id FROM tickets -- {MARCA}",
    f"x'); COPY tickets TO 'fuga.csv'; -- {MARCA}",
    f'" OR ""=" {MARCA}',
    f"{MARCA}\\'; ATTACH 'otra.duckdb' AS o; --",
    f"{MARCA}/**/OR/**/1=1",
    f"{MARCA}$$;DROP TABLE ventas;$$",
    f"{MARCA}?; SELECT 1",
]

# Los campos obligatorios que no se están atacando.
BASE_DE: dict[type[BaseModel], dict[str, str]] = {comun.EntradaPoliticas: {"consulta": "fondo fijo"}}

HERRAMIENTAS: list[tuple[Callable[..., Any], type[BaseModel]]] = [
    (ventas, comun.EntradaVentas),
    (inventario, comun.EntradaInventario),
    (caducidades, comun.EntradaCaducidades),
    (tickets, comun.EntradaTickets),
    (buscar_politicas, comun.EntradaPoliticas),
]

# Estos dos llevan el texto hasta la consulta: si dejan de llegar, la campaña ya no prueba lo que dice probar.
CAMPOS_QUE_LLEGAN_A_LA_BASE = {"EntradaTickets.texto", "EntradaPoliticas.consulta"}


class Espia:
    """Un cursor que anota cada sentencia y sus parámetros antes de ejecutarla."""

    def __init__(self, real: duckdb.DuckDBPyConnection, sentencias: list[tuple[str, Any]]):
        self._real = real
        self._sentencias = sentencias

    def execute(self, sql: str, parametros: Any = None) -> Any:
        self._sentencias.append((sql, parametros))
        return self._real.execute(sql, parametros or [])

    def cursor(self) -> "Espia":
        return Espia(self._real.cursor(), self._sentencias)

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._real, nombre)


@dataclass
class Informe:
    intentos: int = 0
    rechazados: int = 0
    ejecutados: int = 0
    sentencias: int = 0
    fugas: list[str] = field(default_factory=list)
    excepciones: list[str] = field(default_factory=list)
    llegaron_como_parametro: set[str] = field(default_factory=set)


def _metricas(modelo: type[BaseModel]) -> tuple[str | None, ...]:
    campo = modelo.model_fields.get("metrica")
    if campo is None or get_origin(campo.annotation) is not Literal:
        return (None,)
    return get_args(campo.annotation)


def _entrada(modelo: type[BaseModel], metrica: str | None, campo: str, valor: Any) -> BaseModel | None:
    datos = {**BASE_DE.get(modelo, {}), campo: valor}
    if metrica is not None and campo != "metrica":
        datos["metrica"] = metrica
    try:
        return modelo.model_validate(datos)
    except ValidationError:
        return None


def _anotar(sentencias: list[tuple[str, Any]], etiqueta: str, informe: Informe) -> None:
    informe.sentencias += len(sentencias)
    for sql, parametros in sentencias:
        if MARCA in sql.casefold():
            informe.fugas.append(f"{etiqueta}: {sql[:300]}")
        if MARCA in str(parametros).casefold():
            informe.llegaron_como_parametro.add(etiqueta.split(" ")[0])


def atacar(abiertas: consultas.Consultas, herramienta: Callable[..., Any], modelo: type[BaseModel], campos: list[str] | None = None) -> Informe:
    """Corre cada ataque en cada campo y cada métrica. Un campo de lista recibe el ataque dentro de una lista."""
    informe = Informe()
    for metrica in _metricas(modelo):
        for campo in campos or list(modelo.model_fields):
            for ataque in ATAQUES:
                for valor in (ataque, [ataque]):
                    informe.intentos += 1
                    entrada = _entrada(modelo, metrica, campo, valor)
                    if entrada is None:
                        informe.rechazados += 1
                        continue
                    etiqueta = f"{modelo.__name__}.{campo} metrica={metrica} ataque={ataque!r}"
                    sentencias: list[tuple[str, Any]] = []
                    with abiertas.sesion() as cursor:
                        try:
                            herramienta(Espia(cursor, sentencias), entrada)
                            informe.ejecutados += 1
                        except Exception as error:
                            informe.excepciones.append(f"{etiqueta}: {type(error).__name__}: {str(error)[:200]}")
                    _anotar(sentencias, etiqueta, informe)
    return informe


def _sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def base_con_indice(tmp_path_factory) -> Path:
    # La base de la fixture base_construida no trae el índice de texto que necesita buscar_politicas.
    with duckdb.connect() as prueba:
        try:
            construir.cargar_fts(prueba, instalar=False)
        except construir.ExtensionNoDisponible:
            pytest.skip("la extensión fts no está instalada y las pruebas corren sin red")
    destino = tmp_path_factory.mktemp("ataque") / "tienda.duckdb"
    return construir.construir(DIR_DATOS, destino).base


@pytest.fixture
def abiertas(base_con_indice) -> Iterator[consultas.Consultas]:
    with consultas.abrir_consultas(base_con_indice) as conexion:
        yield conexion


@dataclass
class Campana:
    informes: dict[str, Informe]
    sha256_antes: str
    sha256_despues: str
    filas_despues: tuple[int, int]


@pytest.fixture(scope="module")
def campana(base_con_indice) -> Campana:
    # Corre una sola vez: cada prueba de abajo mira una cara del mismo resultado. La base se cierra antes de
    # leer sus bytes porque el motor la mantiene abierta.
    antes = _sha256(base_con_indice)
    with consultas.abrir_consultas(base_con_indice) as conexion:
        informes = {modelo.__name__: atacar(conexion, herramienta, modelo) for herramienta, modelo in HERRAMIENTAS}
        with conexion.sesion() as cursor:
            filas = cursor.execute("SELECT (SELECT count(*) FROM ventas), (SELECT count(*) FROM tickets)").fetchone()
    return Campana(informes, antes, _sha256(base_con_indice), filas)


# ------------------------------------------------------------------ la campaña

@pytest.mark.parametrize("modelo", [modelo.__name__ for _, modelo in HERRAMIENTAS])
def test_ningun_ataque_aparece_en_el_texto_del_sql(campana, modelo):
    assert campana.informes[modelo].fugas == []


@pytest.mark.parametrize("modelo", [modelo.__name__ for _, modelo in HERRAMIENTAS])
def test_ningun_ataque_rompe_una_consulta(campana, modelo):
    # ejecutar() deja pasar los errores sin tipo: una comilla que rompiera el SQL llegaría aquí como error de DuckDB.
    assert campana.informes[modelo].excepciones == []


@pytest.mark.parametrize("modelo", [modelo.__name__ for _, modelo in HERRAMIENTAS])
def test_cada_herramienta_ejecuto_ataques_y_sentencias(campana, modelo):
    # Sin esto, una campaña que se detuviera toda en la validación pasaría sin probar nada.
    assert campana.informes[modelo].ejecutados > 0
    assert campana.informes[modelo].sentencias > 0


def test_el_texto_libre_llega_a_la_base_solo_como_parametro(campana):
    llegaron = set().union(*(informe.llegaron_como_parametro for informe in campana.informes.values()))
    assert CAMPOS_QUE_LLEGAN_A_LA_BASE <= llegaron


def test_la_campana_no_cambia_la_base(campana):
    assert campana.sha256_despues == campana.sha256_antes
    assert campana.filas_despues == (41859, 285)


# ------------------------------------------------------------------ los controles: si hay fuga, la prueba la ve

def test_control_una_herramienta_insegura_se_detecta(abiertas):
    def insegura(con, entrada):
        return con.execute(f"SELECT count(*) FROM tickets WHERE titulo = '{entrada.texto.replace(chr(39), '')}'").fetchall()

    informe = atacar(abiertas, insegura, comun.EntradaTickets, campos=["texto"])
    assert informe.fugas != []


def test_control_un_valor_que_entra_al_sql_desde_otra_funcion_se_detecta(abiertas, monkeypatch):
    # El caso que tests/test_sql_sin_texto_del_usuario.py no ve: where está aprobada y el valor entra en otra función.
    original = modulo_tickets.Filtros.where

    def where_inseguro(self, *argumentos, **opciones):
        where, parametros = original(self, *argumentos, **opciones)
        for termino in self.terminos:
            where += f" AND titulo <> '{(termino.palabra or '').replace(chr(39), '')}'"
        return where, parametros

    monkeypatch.setattr(modulo_tickets.Filtros, "where", where_inseguro)
    informe = atacar(abiertas, tickets, comun.EntradaTickets, campos=["texto"])
    assert informe.fugas != []


def test_control_una_comilla_que_rompe_el_sql_se_reporta(abiertas):
    def rota(con, entrada):
        return con.execute(f"SELECT '{entrada.texto}").fetchall()

    informe = atacar(abiertas, rota, comun.EntradaTickets, campos=["texto"])
    assert informe.excepciones != []
