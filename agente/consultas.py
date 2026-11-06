"""Conexión de consultas a la base y búsqueda de texto sobre los fragmentos (T1.6, K09).

La interfaz y las herramientas consultan build/tienda.duckdb solo a través de una Consultas:

1. La base se abre en read_only. Un INSERT, UPDATE, DELETE, DROP o CREATE lo rechaza DuckDB, no una
   validación nuestra, y el archivo no cambia.
2. Antes de entregarla se hace, en este orden, LOAD fts, enable_external_access = false y
   lock_configuration = true. fts va primero porque con el acceso externo apagado DuckDB ya no carga
   extensiones, y match_bm25 necesita la función stem de fts; sin ella, la primera búsqueda intentaría
   instalarla y fallaría (T0.5). lock_configuration va al final porque después ya no se puede cambiar
   ningún ajuste, tampoco el acceso externo. Con eso ya no se puede leer un archivo, copiar a uno,
   adjuntar otra base ni instalar nada, ni desde un cursor.
3. Cada sesión de la interfaz toma su propio cursor() con Consultas.sesion() (hallazgo 4 del plan). En
   DuckDB 2.0 una conexión ocupada por otro hilo no espera: lanza ResourceInUseException. Un resultado
   de execute() ocupa la conexión hasta leerse completo, así que las consultas de este módulo lo leen
   con fetchall(). Un cursor cuesta unos 6 microsegundos. Pydantic AI programa en paralelo las llamadas a
   herramientas de una misma respuesta y corre las funciones síncronas en hilos: dos herramientas de
   la misma sesión que puedan coincidir no deben compartir el cursor de la sesión, cada llamada toma el
   suyo con sesion().

buscar_fragmentos es la consulta BM25 de las políticas. match_bm25 va dentro de una subconsulta:
referirse a su alias en el WHERE del mismo SELECT da un Binder Error en DuckDB 2.0.0 (hallazgo 3).

Si cambian los archivos de datos o de políticas, la base se reconstruye con construir.asegurar_base(), que
en Windows no puede reemplazar un archivo abierto: se cierran las consultas antes y se abren de nuevo
después. Con una sesión dentro de una llamada, cerrar() falla con ResourceInUseException (ver
Consultas.cerrar).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb

from agente.construir import BASE, ESQUEMA_INDICE, TABLA_INDEXADA, ErrorConstruccion, cargar_fts

# Los mejores fragmentos que devuelve buscar_politicas (T2.7).
LIMITE_FRAGMENTOS = 4

# match_bm25 suma en paralelo, y el último dígito del puntaje cambia de una ejecución a otra: la misma
# consulta dio 10.007454813828847 y 10.007454813828849. Dos fragmentos con el mismo puntaje matemático
# pueden salir en un orden u otro (con una primera versión del índice, FAQ P18 y P6 se invertían en 84
# de 300 corridas de "codigo error gestion"), y si el empate cae entre el 4.º y el 5.º cambia qué
# fragmentos llegan al modelo. Se redondea antes de ordenar, y también el puntaje que se devuelve, para
# que ese ruido sea un empate que decide el id y el resultado sea el mismo en cada ejecución.
DECIMALES_DEL_PUNTAJE = 9

SQL_BUSCAR_FRAGMENTOS = f"""
SELECT id, documento, seccion, titulo_seccion, pagina, pagina_fin, round(puntaje, {DECIMALES_DEL_PUNTAJE})
FROM (
    SELECT id, documento, seccion, titulo_seccion, pagina, pagina_fin,
           {ESQUEMA_INDICE}.match_bm25(id, ?) AS puntaje
    FROM {TABLA_INDEXADA}
)
WHERE puntaje IS NOT NULL
ORDER BY round(puntaje, {DECIMALES_DEL_PUNTAJE}) DESC, id
LIMIT ?
"""

# Dos aperturas a la vez sobre el mismo archivo comparten el motor de DuckDB: si las dos ven la
# configuración sin bloquear, la segunda falla al intentar bloquearla de nuevo.
_CERROJO_APERTURA = threading.Lock()


class BaseNoDisponible(ErrorConstruccion):
    """La base no existe o DuckDB no la puede abrir.

    base es la ruta y motivo dice por qué, sin el traceback de DuckDB. Quien responde al usuario usa este
    tipo para decir que no pudo consultar, sin dar una cifra.
    """

    def __init__(self, base: Path, motivo: str):
        self.base = base
        self.motivo = motivo
        super().__init__(f"No se pudo abrir la base {base}: {motivo}. Constrúyela con python -m agente.construir.")


@dataclass(frozen=True)
class FragmentoEncontrado:
    """Un fragmento de política que respondió a la búsqueda, con lo que hace falta para citarlo (C2.2).

    puntaje es el de BM25 redondeado a DECIMALES_DEL_PUNTAJE; no se compara con un valor exacto.
    """

    id: str
    documento: str
    seccion: str
    titulo_seccion: str
    pagina: int
    pagina_fin: int
    puntaje: float


class Consultas:
    """La base abierta para consultar, con la protección de este módulo. Una por base y proceso.

    La conexión raíz no consulta: solo reparte cursores. Cada sesión de la interfaz guarda el suyo.
    """

    def __init__(self, base: Path, conexion: duckdb.DuckDBPyConnection):
        self.base = base
        self._conexion = conexion

    def sesion(self) -> duckdb.DuckDBPyConnection:
        """Un cursor nuevo de la misma base, con su propia transacción y las mismas protecciones."""
        return self._conexion.cursor()

    def cerrar(self) -> None:
        """Cierra la conexión y sus cursores, y deja el archivo libre para reemplazarlo. Se puede repetir.

        Si alguna sesión está dentro de una llamada, DuckDB no espera a que termine: lanza
        ResourceInUseException, y los cursores que se crearon antes que el ocupado ya quedaron cerrados
        (DuckDB 2.0.0). Quien cierre las consultas para reconstruir la base no debe seguir usando las
        sesiones: repite cerrar() cuando terminen las llamadas en curso y cierra el resto.
        """
        self._conexion.close()

    def __enter__(self) -> Consultas:
        return self

    def __exit__(self, *excepcion: object) -> None:
        self.cerrar()


def _proteger(conexion: duckdb.DuckDBPyConnection) -> None:
    """LOAD fts, enable_external_access = false y lock_configuration = true, en ese orden.

    Si la configuración ya está bloqueada, otra Consultas de este proceso protegió el motor que DuckDB
    comparte entre las conexiones al mismo archivo, y no hay nada que repetir: este módulo es el único
    que bloquea la configuración. La lectura es con fetchall(): con fetchone() el resultado sigue abierto
    y load_extension falla con ResourceInUseException.
    """
    if conexion.execute("SELECT current_setting('lock_configuration')").fetchall()[0][0]:
        return
    cargar_fts(conexion, instalar=False)
    conexion.execute("SET enable_external_access = false")
    conexion.execute("SET lock_configuration = true")


def abrir_consultas(base: Path = BASE) -> Consultas:
    """Abre la base en solo lectura y la protege (ver el docstring del módulo).

    Lanza BaseNoDisponible si el archivo no existe o no se puede abrir, y ExtensionNoDisponible, de
    agente/construir.py, si fts no está instalada: la conexión de consulta solo carga la extensión,
    nunca la descarga.
    """
    ruta = Path(base)
    if not ruta.is_file():
        raise BaseNoDisponible(ruta, "el archivo no existe")
    with _CERROJO_APERTURA:
        try:
            conexion = duckdb.connect(str(ruta), read_only=True)
        except duckdb.Error as error:
            raise BaseNoDisponible(ruta, str(error).partition("\n")[0]) from error
        try:
            _proteger(conexion)
        except BaseException:
            conexion.close()
            raise
    return Consultas(ruta, conexion)


def buscar_fragmentos(con: duckdb.DuckDBPyConnection, texto: str, limite: int = LIMITE_FRAGMENTOS) -> list[FragmentoEncontrado]:
    """Los mejores fragmentos de política para el texto, por BM25, del más al menos parecido.

    con es el cursor de una sesión (Consultas.sesion()). El texto viaja como parámetro y pasa por el
    mismo índice que los fragmentos: sin acentos ni mayúsculas, sin palabras vacías y con la raíz de
    cada palabra en español. Un texto sin palabras de contenido no recupera nada. Dos puntajes iguales
    hasta DECIMALES_DEL_PUNTAJE son un empate y se resuelve por id, para que el orden sea siempre el
    mismo.
    """
    if limite < 1:
        raise ValueError(f"limite debe ser 1 o más, no {limite}")
    filas = con.execute(SQL_BUSCAR_FRAGMENTOS, [texto, limite]).fetchall()
    return [FragmentoEncontrado(*fila) for fila in filas]
