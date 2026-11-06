"""El SQL solo se arma con piezas fijas: un valor del usuario viaja siempre como parámetro.

La prueba lee el código con ast, sin importarlo ni ejecutarlo, y revisa cuatro cosas:

1. Cada f-string con SQL solo interpola expresiones de APROBADOS, por archivo y con su motivo. Una
   expresión nueva hace fallar la prueba hasta que alguien revisa de dónde sale y la agrega.
2. Una pieza aprobada no recibe el valor de un campo de la entrada dentro de su función. Elegir con ese
   campo una pieza fija es seguro: COLUMNAS[entrada.agrupar_por] o "DESC" if entrada.orden == "mayor".
3. No se arma SQL con +, % o format, salvo las concatenaciones de CONCATENACIONES_APROBADAS.
4. Lo que recibe execute, consultar o _leer como SQL no es un campo de la entrada ni un f-string sin revisar.

Lo que no ve: un valor que llega por el parámetro de otra función con el nombre de una pieza aprobada, y
un f-string con SQL que no use ninguna palabra de PATRON_SQL y no vaya directo a execute. El análisis
sigue variables dentro de cada función, no entre funciones.
"""

import ast
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
TICKETS = "agente/herramientas/tickets.py"

# Sensible a mayúsculas a propósito: el SQL del proyecto va en mayúsculas y así no confunde texto en español.
PATRON_SQL = re.compile(
    r"\b(SELECT|INSERT INTO|DELETE FROM|UPDATE \w+ SET|WHERE|FROM|JOIN|GROUP BY|ORDER BY|LIMIT|CREATE (TABLE|VIEW)"
    r"|ATTACH|PRAGMA|COPY|FILTER|AND|OR|AS|LIKE|ILIKE|IS (NOT )?NULL)\b"
    r"|\bIN \(|[=<>]\s*\?|[=<>]\s*'$|\w\s+(=|<>|!=|<=?|>=?)\s*$|, \?\)|regexp_matches\(|list_contains\("
)

HUECO_DE_VALOR = re.compile(r"%[sdrf]|\{[^{}]*\}")

# Lo que devuelve un valor con el mismo contenido que recibió; otra función se revisa por dentro.
FUNCIONES_QUE_PASAN_EL_VALOR = {"str", "repr", "format", "join", "replace", "strip", "lower", "upper"}

# Dónde cada función recibe el SQL: método o función -> posición del argumento.
POSICION_DEL_SQL = {"execute": 0, "executemany": 0, "sql": 0, "query": 0, "from_query": 0, "consultar": 1, "_leer": 2}

_COLUMNAS_FIJAS = "constante del módulo con nombres de columnas o expresiones"
_MARCADORES = "solo genera marcadores ?"
_DICCIONARIO = "sale de un diccionario fijo; la entrada solo elige la clave, validada por Literal"
_SENTIDO = "DESC o ASC, escrito en el código"
_CONDICION_ARMADA = "se arma en este archivo solo con piezas de esta lista y marcadores ?"
_DEFINICION = "condición de definiciones.py; su operador solo admite los valores de un Literal"
_NOMBRE_FIJO = "nombre de tabla o columna de FUENTES y RELACIONES, escritos en construir.py"

APROBADOS: dict[str, dict[str, str]] = {
    "agente/construir.py": {
        **dict.fromkeys(["', '.join(COLUMNAS_FRAGMENTOS)", "TABLA_PALABRAS_VACIAS", "TABLA_INDEXADA"], _COLUMNAS_FIJAS),
        "marcadores": _MARCADORES,
        **dict.fromkeys(["PATRON_MONTO", "TIPO_MONTO", "DIALECTO", "IGNORAR_EN_INDICE", "_GRUPOS_FUENTES_SQL"], "constante de construir.py"),
        **dict.fromkeys(
            ["nombre", "', '.join(columnas)", "tabla", "tipos", "seleccion",
             "relacion.columna", "relacion.tabla", "relacion.referida", "relacion.columna_referida"],
            _NOMBRE_FIJO,
        ),
        **dict.fromkeys(
            ["s.ventas.condicion('v')", "s.ventas.condicion_devolucion('v')", "s.inventario.condicion('i')",
             "s.tickets.condicion_sin_cerrar('k')", "s.tickets.condicion_cerrado('k')"],
            _DEFINICION,
        ),
        "s.cortes.inventario.fecha.isoformat()": "fecha de config/supuestos.yaml, validada como date por Pydantic",
        "fin_corte": "fecha de config/supuestos.yaml, validada como date por Pydantic",
        "int(s.tickets.antiguedad_decimales)": "entero de config/supuestos.yaml",
        **dict.fromkeys(
            ["len(sin_tema)", "texto_tickets.ARCHIVO_TEMAS", "texto_tickets.TEMA_SIN_CLASIFICAR"],
            "mensaje de aviso que muestra una consulta para copiarla; no se ejecuta",
        ),
        "ruta": "ruta de la base con las comillas escapadas; ATTACH no acepta parámetros y el usuario no la escribe",
    },
    "agente/consultas.py": dict.fromkeys(
        ["ESQUEMA_INDICE", "DECIMALES_DEL_PUNTAJE", "TABLA_INDEXADA"], "constante de consultas.py o de construir.py"
    ),
    "agente/definiciones.py": {"_columna(tabla, 'fecha_cierre')": "alias de tabla escrito por quien llama y un nombre de columna fijo"},
    "agente/herramientas/caducidades.py": {"filtro": "una de dos condiciones fijas con marcador ?"},
    "agente/herramientas/inventario.py": {
        "_COLUMNAS_FILA": _COLUMNAS_FIJAS,
        "filtro": _CONDICION_ARMADA,
        "condicion": _CONDICION_ARMADA,
        "sentido": _SENTIDO,
        "grupo": _DICCIONARIO,
        "_GRUPOS[entrada.agrupar_por]": _DICCIONARIO,
        "s.ventas.condicion('x')": _DEFINICION,
        "s.inventario.condicion('v')": _DEFINICION,
    },
    "agente/herramientas/tickets.py": {
        **dict.fromkeys(["TEXTO_CON_TEMA if self.con_tema else TEXTO_NORMALIZADO", "TEXTO_NORMALIZADO", "COLUMNAS_DE_LISTA"], _COLUMNAS_FIJAS),
        **dict.fromkeys(["_marcadores(len(ids))", "_marcadores(len(estados))"], _MARCADORES),
        "campo": "sale de una tupla fija de nombres de columna escrita en el for",
        "columna": _DICCIONARIO,
        "seleccion": _DICCIONARIO,
        **dict.fromkeys(["where", "where_total", "condicion"], _CONDICION_ARMADA),
        "MAX_PARECIDOS": "constante entera de tickets.py",
    },
    "agente/herramientas/ventas.py": {
        **dict.fromkeys(["neta", "devolucion"], _DEFINICION),
        **dict.fromkeys(["unidad", "de_la_unidad"], "sale de las claves de UNIDADES, escritas en ventas.py"),
        **dict.fromkeys(
            ["nombre", "expresion", "', '.join((expresion for expresion, _ in claves))", "', '.join([*seleccion, _agregados(s)])"],
            "sale de CLAVES y GRANOS, escritos en ventas.py; la entrada solo elige la clave, validada por Literal",
        ),
        **dict.fromkeys(["_marcas(len(alcance.tiendas))", "_marcas(len(alcance.skus))"], _MARCADORES),
        "' AND '.join(condiciones)": _CONDICION_ARMADA,
    },
    "eval/buscar_privacidad.py": dict.fromkeys(
        ["columna", "esquema", "tabla"], "nombres leídos de information_schema de la base propia; herramienta de desarrollo"
    ),
}

CONCATENACIONES_APROBADAS: dict[str, dict[str, str]] = {
    "agente/herramientas/tickets.py": {"'WHERE ' + '\\n  AND '.join(condiciones)": _CONDICION_ARMADA},
}


def _es_campo_de_entrada(nodo: ast.AST) -> bool:
    return isinstance(nodo, ast.Attribute) and isinstance(nodo.value, ast.Name) and nodo.value.id == "entrada"


def _lleva_valor_de_entrada(nodo: ast.AST | None, contaminados: set[str]) -> bool:
    """Si la expresión da el valor de un campo de la entrada, no una pieza elegida con él."""
    if nodo is None or isinstance(nodo, ast.Compare):
        return False
    if _es_campo_de_entrada(nodo):
        return True
    if isinstance(nodo, ast.Name):
        return nodo.id in contaminados
    if isinstance(nodo, ast.Subscript):
        return _lleva_valor_de_entrada(nodo.value, contaminados)
    if isinstance(nodo, ast.IfExp):
        return any(_lleva_valor_de_entrada(lado, contaminados) for lado in (nodo.body, nodo.orelse))
    if isinstance(nodo, ast.Call):
        return _la_llamada_pasa_el_valor(nodo, contaminados)
    return any(_lleva_valor_de_entrada(hijo, contaminados) for hijo in ast.iter_child_nodes(nodo))


def _la_llamada_pasa_el_valor(nodo: ast.Call, contaminados: set[str]) -> bool:
    """texto.strip() y ", ".join(tiendas) pasan el valor. _consulta(skus) lo manda como parámetro y se revisa aparte."""
    es_metodo = isinstance(nodo.func, ast.Attribute)
    nombre = nodo.func.attr if es_metodo else getattr(nodo.func, "id", None)
    if es_metodo and _lleva_valor_de_entrada(nodo.func.value, contaminados):
        return True
    if nombre not in FUNCIONES_QUE_PASAN_EL_VALOR:
        return False
    return any(_lleva_valor_de_entrada(argumento, contaminados) for argumento in nodo.args)


def _asignaciones(funcion: ast.AST) -> list[tuple[list[ast.AST], ast.AST]]:
    """Pares (destinos, valor) de cada asignación y cada for dentro de la función."""
    pares: list[tuple[list[ast.AST], ast.AST]] = []
    for nodo in ast.walk(funcion):
        if isinstance(nodo, ast.Assign):
            pares.append((nodo.targets, nodo.value))
        elif isinstance(nodo, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and nodo.value is not None:
            pares.append(([nodo.target], nodo.value))
        elif isinstance(nodo, ast.For):
            pares.append(([nodo.target], nodo.iter))
    return pares


def _contaminados(funcion: ast.AST) -> set[str]:
    """Los nombres de la función que reciben un campo de la entrada, directo o a través de otras variables."""
    pares = _asignaciones(funcion)
    nombres: set[str] = set()
    while True:
        nuevos = {
            nombre.id
            for destinos, valor in pares
            if _lleva_valor_de_entrada(valor, nombres)
            for destino in destinos
            for nombre in ast.walk(destino)
            if isinstance(nombre, ast.Name)
        }
        if nuevos <= nombres:
            return nombres
        nombres |= nuevos


def _es_sql(texto: object) -> bool:
    """Parte el texto en cada hueco de % o format: "a = '%s'" tiene "a = '" justo antes del valor."""
    if not isinstance(texto, str):
        return False
    return any(PATRON_SQL.search(pedazo) for pedazo in HUECO_DE_VALOR.split(texto))


def _es_fstring_sql(nodo: ast.JoinedStr) -> bool:
    return any(isinstance(parte, ast.Constant) and _es_sql(parte.value) for parte in nodo.values)


def _argumento_sql(nodo: ast.Call) -> ast.AST | None:
    nombre = nodo.func.attr if isinstance(nodo.func, ast.Attribute) else getattr(nodo.func, "id", None)
    posicion = POSICION_DEL_SQL.get(nombre)
    if posicion is None or len(nodo.args) <= posicion:
        return None
    # Con *argumentos la posición ya no se conoce: lo que se desempaca se arma en otra parte y ahí se revisa.
    if any(isinstance(argumento, ast.Starred) for argumento in nodo.args[: posicion + 1]):
        return None
    return nodo.args[posicion]


def _fstrings_de_sql(arbol: ast.AST) -> list[ast.JoinedStr]:
    """Los f-string que parecen SQL y los que van directo a execute, consultar o _leer."""
    en_sumidero = [_argumento_sql(nodo) for nodo in ast.walk(arbol) if isinstance(nodo, ast.Call)]
    return [
        nodo
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.JoinedStr) and (_es_fstring_sql(nodo) or any(nodo is argumento for argumento in en_sumidero))
    ]


def _revisar_fstring(nodo: ast.JoinedStr, aprobadas: dict[str, str], contaminados: set[str], lugar: str) -> list[str]:
    hallazgos = []
    for parte in nodo.values:
        if not isinstance(parte, ast.FormattedValue):
            continue
        expresion = ast.unparse(parte.value)
        if expresion not in aprobadas:
            hallazgos.append(f"{lugar}:{nodo.lineno} interpola {expresion!r}, que no está en APROBADOS")
        elif _lleva_valor_de_entrada(parte.value, contaminados):
            hallazgos.append(f"{lugar}:{nodo.lineno} {expresion!r} está aprobada, pero aquí recibe un campo de la entrada")
    return hallazgos


def _revisar_operacion(nodo: ast.AST, uniones: dict[str, str], lugar: str) -> list[str]:
    """Reprueba SQL armado con +, % o format."""
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "format":
        base = nodo.func.value
        if isinstance(base, ast.Constant) and _es_sql(base.value):
            return [f"{lugar}:{nodo.lineno} arma SQL con format"]
        return []
    if not (isinstance(nodo, ast.BinOp) and isinstance(nodo.op, (ast.Add, ast.Mod))):
        return []
    literales = [lado for lado in (nodo.left, nodo.right) if isinstance(lado, ast.Constant)]
    if len(literales) != 1 or not _es_sql(literales[0].value):
        return []
    if ast.unparse(nodo) in uniones:
        return []
    return [f"{lugar}:{nodo.lineno} arma SQL con un operador: {ast.unparse(nodo)!r}"]


def _revisar_sumidero(nodo: ast.Call, contaminados: set[str], lugar: str) -> list[str]:
    argumento = _argumento_sql(nodo)
    if argumento is not None and _lleva_valor_de_entrada(argumento, contaminados):
        return [f"{lugar}:{nodo.lineno} ejecuta como SQL un campo de la entrada: {ast.unparse(argumento)!r}"]
    return []


def revisar(
    codigo: str,
    archivo: str = "prueba.py",
    aprobados: dict[str, dict[str, str]] | None = None,
    concatenaciones: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Los hallazgos de un archivo; vacío si cumple. aprobados y concatenaciones sirven a las pruebas del detector."""
    aprobadas = (APROBADOS if aprobados is None else aprobados).get(archivo, {})
    uniones = (CONCATENACIONES_APROBADAS if concatenaciones is None else concatenaciones).get(archivo, {})
    arbol = ast.parse(codigo)
    # ast.walk va de afuera hacia adentro: la función más interna de cada nodo escribe al último.
    contaminados_de: dict[ast.AST, set[str]] = {}
    for funcion in ast.walk(arbol):
        if isinstance(funcion, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            nombres = _contaminados(funcion)
            for nodo in ast.walk(funcion):
                contaminados_de[nodo] = nombres

    hallazgos: list[str] = []
    for fstring in _fstrings_de_sql(arbol):
        hallazgos += _revisar_fstring(fstring, aprobadas, contaminados_de.get(fstring, set()), archivo)
    for nodo in ast.walk(arbol):
        contaminados = contaminados_de.get(nodo, set())
        hallazgos += _revisar_operacion(nodo, uniones, archivo)
        if isinstance(nodo, ast.Call):
            hallazgos += _revisar_sumidero(nodo, contaminados, archivo)
    return hallazgos


# ------------------------------------------------------------------ el detector ve las formas de armar SQL

CODIGO_PELIGROSO = {
    "f-string con comillas": """
def f(con, entrada):
    con.execute(f"SELECT * FROM t WHERE a = '{entrada.texto}'")
""",
    "f-string sin comillas": """
def f(con, valor):
    return f"SELECT * FROM t WHERE a = {valor}"
""",
    "fragmento de condición": """
def f(condiciones, valor):
    condiciones.append(f"tienda = '{valor}'")
""",
    "fragmento sin palabra clave": """
def f(condiciones, valor):
    condiciones.append(f"tienda = {valor}")
""",
    "concatenación": """
def f(con, valor):
    con.execute("SELECT * FROM t WHERE a = '" + valor + "'")
""",
    "operador de porcentaje": """
def f(con, valor):
    con.execute("SELECT * FROM t WHERE a = '%s'" % valor)
""",
    "format": """
def f(con, valor):
    con.execute("SELECT * FROM t WHERE a = '{}'".format(valor))
""",
    "fragmento con porcentaje": """
def f(condiciones, valor):
    condiciones.append("tienda = '%s'" % valor)
""",
    "fragmento con format": """
def f(condiciones, valor):
    condiciones.append("tienda = '{}'".format(valor))
""",
    "f-string en consultar sin palabra clave": """
def f(con, valor):
    consultar(con, f"{valor}")
""",
    "f-string en _leer sin palabra clave": """
def f(con, filtros, valor):
    _leer(con, filtros, f"{valor}")
""",
    "campo de la entrada directo": """
def f(con, entrada):
    con.execute(entrada.texto)
""",
    "variable que recibe el campo de la entrada": """
def f(con, entrada):
    sql = entrada.texto
    con.execute(sql)
""",
    "campo de la entrada con un método": """
def f(con, entrada):
    sql = entrada.texto.strip()
    con.execute(sql)
""",
    "campo de la entrada con str": """
def f(con, entrada):
    sql = str(entrada.texto)
    con.execute(sql)
""",
    "columna y valor interpolados con comillas": """
def f(condiciones, columna, valor):
    condiciones.append(f"{columna} = '{valor}'")
""",
}


@pytest.mark.parametrize("forma", CODIGO_PELIGROSO)
def test_el_detector_reprueba_cada_forma_de_meter_un_valor_al_sql(forma):
    assert revisar(CODIGO_PELIGROSO[forma]) != []


def test_una_pieza_aprobada_que_recibe_un_campo_de_la_entrada_tambien_se_reprueba():
    codigo = """
def f(con, entrada):
    where = entrada.texto
    con.execute(f"SELECT * FROM t {where}")
"""
    assert revisar(codigo, aprobados={"prueba.py": {"where": "motivo"}}) != []


def test_el_campo_de_la_entrada_tambien_se_sigue_a_traves_de_varias_variables():
    codigo = """
def f(con, entrada):
    texto = entrada.texto
    where = texto
    con.execute(f"SELECT * FROM t {where}")
"""
    assert revisar(codigo, aprobados={"prueba.py": {"where": "motivo"}}) != []


def test_una_pieza_aprobada_solo_vale_en_su_archivo():
    codigo = 'SQL = f"SELECT {COLUMNAS} FROM t"\n'
    assert revisar(codigo, aprobados={"prueba.py": {"COLUMNAS": "motivo"}}) == []
    assert revisar(codigo, "otro.py", aprobados={"prueba.py": {"COLUMNAS": "motivo"}}) != []


# ------------------------------------------------------------------ el detector no reprueba lo correcto

CODIGO_CORRECTO = {
    "parámetro": """
def f(con, entrada):
    con.execute("SELECT * FROM t WHERE a = ?", [entrada.texto])
""",
    "parámetro en consultar": """
def f(con, entrada):
    consultar(con, "SELECT * FROM t WHERE a = ?", [entrada.texto])
""",
    "texto que no es SQL": """
def f(entrada):
    return f"no encontré {entrada.texto}"
""",
    "pregunta que no es SQL": """
def f(entrada):
    return f"¿Cuánto se vendió el {entrada.texto}?"
""",
    "expresión regular que no es SQL": """
def f(palabra):
    return f"(?:^| )(?:{palabra})(?: |$)"
""",
    "variable de entorno en un mensaje": """
def f(modelo):
    return f"Escribe MODELO={modelo}."
""",
    "etiqueta en un mensaje": """
def f(nombre):
    return f"<{nombre}>"
""",
    "el campo de la entrada viaja a otra función y el SQL sale fijo": """
def f(con, entrada):
    sql, parametros = _consulta(entrada.texto)
    con.execute(sql, parametros)
""",
}


@pytest.mark.parametrize("forma", CODIGO_CORRECTO)
def test_el_detector_acepta_lo_correcto(forma):
    assert revisar(CODIGO_CORRECTO[forma]) == []


def test_elegir_una_pieza_fija_con_un_campo_de_la_entrada_es_seguro():
    codigo = """
def f(con, entrada):
    columna = COLUMNAS[entrada.agrupar_por]
    sentido = "DESC" if entrada.orden == "mayor" else "ASC"
    con.execute(f"SELECT {columna} FROM t ORDER BY a {sentido}")
"""
    assert revisar(codigo, aprobados={"prueba.py": {"columna": "motivo", "sentido": "motivo"}}) == []


# ------------------------------------------------------------------ el código real

def _archivos() -> list[Path]:
    """Todo lo que corre en producción, y de eval/ solo lo que abre DuckDB: lo demás imprime reportes."""
    eval_con_base = [ruta for ruta in sorted((RAIZ / "eval").rglob("*.py")) if "duckdb" in ruta.read_text(encoding="utf-8")]
    return [RAIZ / "app.py", RAIZ / "start.py", *sorted((RAIZ / "agente").rglob("*.py")), *eval_con_base]


def _nombre(ruta: Path) -> str:
    return ruta.relative_to(RAIZ).as_posix()


def test_el_sql_del_proyecto_no_lleva_valores_fuera_de_la_lista_aprobada():
    hallazgos = [hallazgo for ruta in _archivos() for hallazgo in revisar(ruta.read_text(encoding="utf-8"), _nombre(ruta))]
    assert hallazgos == []


@pytest.mark.parametrize("archivo", APROBADOS)
def test_cada_pieza_aprobada_sigue_en_el_codigo_y_tiene_motivo(archivo):
    # Una lista con piezas que ya no existen aprobaría de antemano lo que alguien escriba con ese nombre.
    arbol = ast.parse((RAIZ / archivo).read_text(encoding="utf-8"))
    usadas = {
        ast.unparse(parte.value)
        for fstring in _fstrings_de_sql(arbol)
        for parte in fstring.values
        if isinstance(parte, ast.FormattedValue)
    }
    assert set(APROBADOS[archivo]) - usadas == set()
    assert all(motivo.strip() for motivo in APROBADOS[archivo].values())


@pytest.mark.parametrize("archivo", CONCATENACIONES_APROBADAS)
def test_cada_concatenacion_aprobada_sigue_en_el_codigo(archivo):
    arbol = ast.parse((RAIZ / archivo).read_text(encoding="utf-8"))
    usadas = {ast.unparse(nodo) for nodo in ast.walk(arbol) if isinstance(nodo, ast.BinOp)}
    assert set(CONCATENACIONES_APROBADAS[archivo]) - usadas == set()


def test_una_inyeccion_escrita_en_el_codigo_real_de_tickets_se_detecta():
    # Sobre el texto en memoria: el archivo no cambia. Sin el cambio no hay hallazgos.
    original = (RAIZ / TICKETS).read_text(encoding="utf-8")
    seguro = 'f.condiciones.append(f"{campo} = ?")'
    assert seguro in original
    alterado = original.replace(seguro, "f.condiciones.append(f\"{campo} = '{entrada.texto}'\")")
    assert revisar(original, TICKETS) == []
    assert revisar(alterado, TICKETS) != []
