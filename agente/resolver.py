"""Resuelve nombres coloquiales, errores de dedo y fechas relativas (T2.8, C4.3 y C3.1).

La gerente escribe como en el celular ("cumbre", "yogurt", "lechhe entera", "jullio", "x internet"). Este
módulo lleva ese texto a lo que la consulta entiende (un id de T01 a T04, un SKU, un canal, un rango de
fechas) y deja en las notas del resultado cómo lo interpretó, para que la respuesta lo diga. Lo que no
puede resolver con claridad lo dice con el error con tipo de agente/herramientas/errores.py: una tienda
que no existe (P21), una ciudad con dos tiendas (P26), un producto que coincide con varios, un periodo que
no toca ningún día con datos. Nunca elige una en silencio.

Uso desde una herramienta (T2.3 a T2.6), con el cursor de la llamada (Consultas.sesion()):

    catalogos = cargar_catalogos(con)
    tiendas = resolver_tiendas(entrada.tiendas, catalogos)            # .ids y .notas
    producto = resolver_productos(entrada.producto, catalogos)        # .skus, .nombres y .notas; None sin texto
    periodo = resolver_periodo(entrada.periodo, entrada.desde, entrada.hasta, "ventas")   # .desde, .hasta
    Resultado(..., notas=[*tiendas.notas, *(producto.notas if producto else []), *periodo.notas])

Los alias y el catálogo salen de la base (tablas alias, tiendas y catalogo), no de config/alias.yaml:
agente/construir.py ya validó los alias contra tiendas.csv y los canales de supuestos.yaml al cargarlos.

El umbral. Una palabra es un error de dedo de otra si difflib.SequenceMatcher.ratio() llega a
UMBRAL_PALABRA (0.8) y las dos tienen 4 letras o más; las palabras cortas (san, sur, vn) y las que traen
cifras solo se comparan exactas. El parecido se mide palabra por palabra, no sobre el texto entero: el
texto entero deja pasar nombres distintos ("valle oriente" da 0.83 contra "valle norte", "lomas del este"
0.82 contra "lomas del sur"), mientras que por palabra "oriente" contra "norte" da 0.67 y "este" contra
"sur" no se compara. Un nombre de varias palabras solo se parece a otro con el mismo número de palabras y
todas parecidas. Si dos candidatas quedan a menos de MARGEN_PALABRA (0.05) no se adivina ("polo" se parece
igual a "pollo" y a "polvo"), y no se corrige hacia una palabra que está en más de
MAX_PRODUCTOS_POR_PALABRA_CORREGIDA nombres ("pizza" da 0.8 contra "pieza", que cuentan 14 productos).

Lo que se nota. Se dice cómo se interpretó todo lo que no sea el nombre oficial, con o sin artículo: "cumbre"
es Tienda Cumbres, "yogurt" es la subcategoría Yogur, "jullio" es julio de 2026, y toda fecha relativa dice
que "hoy" es el 1 de septiembre (D3-A) y que la fecha real no tiene datos (aviso_fecha_relativa).
"""

from __future__ import annotations

import calendar
import datetime as dt
import difflib
import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import duckdb

from agente.definiciones import MESES_NOMBRE, Supuestos, aviso_fecha_relativa, skus_equivalentes, supuestos
from agente.herramientas.comun import ErrorHerramienta, FuenteConCorte, consultar, en_corte, fecha_larga
from agente.herramientas.errores import CanalInexistente, EntidadAmbigua, EntidadInexistente, FueraDePeriodo

UMBRAL_PALABRA = 0.8
MARGEN_PALABRA = 0.05
LARGO_MINIMO_CORRECCION = 4
MAX_PRODUCTOS_POR_PALABRA_CORREGIDA = 10
MAX_TEXTO = 80
MAX_NOMBRES_EN_NOTA = 6

MESES = {nombre: numero for numero, nombre in enumerate(MESES_NOMBRE, start=1)} | {"setiembre": 9}
ABREVIATURAS_DE_MES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
    "sep": 9, "sept": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

# Palabras que no distinguen un nombre de otro: artículos, preposiciones y, según el caso, "tienda".
RELLENO_LUGAR = frozenset({
    "la", "el", "los", "las", "de", "del", "d", "dl", "en", "al", "a",
    "tienda", "tiendas", "sucursal", "sucursales", "super", "supermercado",
})
RELLENO_CANAL = frozenset({"la", "el", "los", "las", "de", "del", "en", "por", "x", "a", "al", "via", "canal"})
RELLENO_PRODUCTO = frozenset({"la", "el", "los", "las", "un", "una", "unos", "unas", "de", "del", "d", "y", "e",
                              "en", "al", "a", "pa", "para", "por"})
RELLENO_PERIODO = frozenset({"de", "del", "el", "la", "los", "las", "en", "durante", "mes", "dia", "dias", "ano"})
# Palabras que marcan un rango de fechas. "entre" y "desde" abren el primer extremo; las demás cierran ese
# extremo y empiezan el siguiente ("junio a agosto", "entre junio y agosto", "desde junio hasta agosto").
CONECTORES_DE_PERIODO = frozenset({"entre", "desde", "a", "al", "hasta", "y", "e"})
APERTURAS_DE_RANGO = frozenset({"entre", "desde"})
PALABRAS_DE_TODAS = frozenset({"todas", "todos", "toda", "total", "cadena", "cuatro", "4"})

# Cómo se escribe en el celular lo que en los nombres del catálogo va abreviado.
UNIDADES = {
    "kilo": "kg", "kilos": "kg", "kilogramo": "kg", "kilogramos": "kg", "kgs": "kg",
    "litro": "l", "litros": "l", "lt": "l", "lts": "l",
    "gramo": "g", "gramos": "g", "gr": "g", "grs": "g",
    "mililitro": "ml", "mililitros": "ml", "mls": "ml",
}
SIMBOLOS_DE_UNIDAD = frozenset({"kg", "g", "l", "ml"})

_ALFANUMERICAS = re.compile(r"[a-z0-9]+")
_PALABRA_O_CIFRA = re.compile(r"\d+(?:\.\d+)?|[a-z]+")
_TOKEN_DE_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}|\d{4}-\d{2}|\d+|[a-z]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_PATRON_SKU = re.compile(r"SKU-\d{4}")
_SKU_EN_ETIQUETA = re.compile(r"\bSKU-\d{4}\b")


# ------------------------------------------------------------------ texto y parecido

def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos ni signos y con un solo espacio entre palabras; conserva el punto y el guion."""
    texto = unicodedata.normalize("NFKD", texto.lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9\s.\-]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _alfanumericas(texto: str) -> list[str]:
    return _ALFANUMERICAS.findall(normalizar(texto))


def _citar(texto: str) -> str:
    """El texto del usuario listo para repetirse en una nota: en una línea y con tope de largo."""
    limpio = " ".join(_CONTROL.sub(" ", texto).split())
    return limpio if len(limpio) <= MAX_TEXTO else limpio[: MAX_TEXTO - 1] + "…"


def _parecido(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _con_plural(palabra: str) -> set[str]:
    return {palabra, palabra + "s", palabra + "es"}


def _son_variantes(a: str, b: str) -> bool:
    """La misma palabra o una el plural de la otra: cumbre y cumbres, papa y papas, pan y panes."""
    return b in _con_plural(a) or a in _con_plural(b)


def _tiene_cifras(*palabras: str) -> bool:
    return any(c.isdigit() for palabra in palabras for c in palabra)


def _similitud(a: str, b: str) -> float | None:
    """1.0 si es la misma palabra o su plural; el parecido de difflib si una es error de dedo de la otra."""
    if _son_variantes(a, b):
        return 1.0
    if min(len(a), len(b)) < LARGO_MINIMO_CORRECCION or _tiene_cifras(a, b):
        return None
    parecido = _parecido(a, b)
    return parecido if parecido >= UMBRAL_PALABRA else None


def _corregir(palabra: str, candidatas: Collection[str]) -> str | None:
    """La candidata de la que palabra es un error de dedo; None si ninguna lo es o si hay dos igual de buenas."""
    if len(palabra) < LARGO_MINIMO_CORRECCION:
        return None
    cercanas = difflib.get_close_matches(palabra, candidatas, n=2, cutoff=UMBRAL_PALABRA)
    if not cercanas:
        return None
    mejor = _parecido(palabra, cercanas[0])
    if len(cercanas) > 1 and _parecido(palabra, cercanas[1]) > mejor - MARGEN_PALABRA:
        return None
    return cercanas[0]


def _parecido_de_palabras(buscadas: Sequence[str], alias: Sequence[str]) -> float | None:
    """El parecido medio si cada palabra buscada es la del alias o un error de dedo de ella; None si no."""
    if len(buscadas) != len(alias):
        return None
    puntajes = [_similitud(buscada, palabra) for buscada, palabra in zip(buscadas, alias)]
    if None in puntajes:
        return None
    return sum(puntajes) / len(puntajes)


def _destinos_parecidos(buscadas: Sequence[str], mapa: Mapping[tuple[str, ...], set[str]]) -> set[str]:
    parecidos = [(_parecido_de_palabras(buscadas, alias), destinos) for alias, destinos in mapa.items()]
    puntuados = [(puntaje, destinos) for puntaje, destinos in parecidos if puntaje is not None]
    if not puntuados:
        return set()
    mejor = max(puntaje for puntaje, _ in puntuados)
    cercanos = [destinos for puntaje, destinos in puntuados if puntaje >= mejor - MARGEN_PALABRA]
    return set().union(*cercanos)


def _buscar_alias(buscadas: Sequence[str], mapa: Mapping[tuple[str, ...], set[str]]) -> set[str]:
    """Los destinos del alias que dicen las palabras: igual, igual sin espacios (vallenorte) o con un error de dedo."""
    exacto = mapa.get(tuple(buscadas))
    if exacto:
        return set(exacto)
    pegadas = "".join(buscadas)
    for alias, destinos in mapa.items():
        if "".join(alias) == pegadas:
            return set(destinos)
    return _destinos_parecidos(buscadas, mapa)


def _lista(elementos: Sequence[str]) -> str:
    """Los elementos separados con coma, con tope; los que sobran se cuentan."""
    mostrados = ", ".join(elementos[:MAX_NOMBRES_EN_NOTA])
    sobran = len(elementos) - MAX_NOMBRES_EN_NOTA
    return f"{mostrados} y {sobran} más" if sobran > 0 else mostrados


# ------------------------------------------------------------------ catálogos leídos de la base

@dataclass(frozen=True)
class Producto:
    sku: str
    nombre: str
    categoria: str
    subcategoria: str
    fecha_baja: dt.date | None
    sku_sustituto: str | None


@dataclass(frozen=True)
class _Grupo:
    """Una subcategoría o una categoría: su nombre dicho en palabras base y los SKU que reúne."""

    tipo: str
    nombre: str
    palabras: frozenset[str]
    skus: tuple[str, ...]


@dataclass(frozen=True)
class _Consulta:
    """El texto de un producto ya llevado a palabras del catálogo."""

    palabras: frozenset[str]
    unidades: frozenset[str]
    desconocidas: tuple[str, ...]
    reinterpretado: bool

    @property
    def sin_unidades(self) -> frozenset[str]:
        return self.palabras - self.unidades


@dataclass(frozen=True)
class _Coincidencia:
    skus: list[str]
    grupo: _Grupo | None
    palabras: frozenset[str]
    reinterpretado: bool


def _singulares(palabra: str) -> list[str]:
    candidatas = []
    if palabra.endswith("es"):
        candidatas.append(palabra[:-2])
    if palabra.endswith("s"):
        candidatas.append(palabra[:-1])
    return candidatas


def _formas_base(vocabulario: Collection[str]) -> dict[str, str]:
    """Cada palabra del catálogo con su forma base: refrescos y refresco son una sola."""
    base: dict[str, str] = {}
    for palabra in sorted(vocabulario, key=len):
        singular = next((s for s in _singulares(palabra) if s in base), None)
        base[palabra] = base[singular] if singular else palabra
    return base


def _se_puede_corregir_hacia(palabra: str) -> bool:
    """Una palabra del catálogo a la que se puede corregir un error de dedo: ni corta, ni cifra, ni unidad."""
    if palabra in SIMBOLOS_DE_UNIDAD:
        return False
    return len(palabra) >= LARGO_MINIMO_CORRECCION and not _tiene_cifras(palabra)


def _sin_encabezado(palabras: Sequence[str]) -> list[str]:
    """Quita del principio los artículos y "tienda": "la tienda de cumbres" queda en "cumbres"."""
    for posicion, palabra in enumerate(palabras):
        if palabra not in RELLENO_LUGAR:
            return list(palabras[posicion:])
    return []


def _palabras_de_lugar(texto: str) -> list[str]:
    return [palabra for palabra in _alfanumericas(texto) if palabra not in RELLENO_LUGAR]


def _palabras_de_canal(texto: str) -> list[str]:
    return [palabra for palabra in _alfanumericas(texto) if palabra not in RELLENO_CANAL]


def _palabras_de_producto(texto: str) -> list[str]:
    return [palabra for palabra in _PALABRA_O_CIFRA.findall(normalizar(texto)) if palabra not in RELLENO_PRODUCTO]


class Catalogos:
    """Lo que el resolvedor lee de la base: tiendas, alias y catálogo de productos.

    tiendas es {id: {nombre, ciudad, formato}}, la forma que pide errores.verificar_canal. alias_lugares
    lleva a un conjunto de ids cada nombre de tienda o de ciudad (las palabras sin relleno): un conjunto
    de dos o más es una ciudad ambigua. Se arma una vez por llamada de herramienta y no guarda la conexión.
    """

    def __init__(
        self,
        tiendas: Mapping[str, Mapping[str, str]],
        alias: Iterable[tuple[str, str, str]],
        productos: Iterable[Producto],
    ):
        filas = list(alias)
        self.tiendas = {id_: dict(datos) for id_, datos in tiendas.items()}
        self.productos = {producto.sku: producto for producto in productos}
        self.alias_lugares = self._alias_de_lugares(filas)
        self.alias_canales = self._alias_de_canales(filas)
        self.alias_productos = {normalizar(original): cambio for tipo, original, cambio in filas if tipo == "producto"}
        self._oficiales = self._nombres_oficiales()
        self._armar_vocabulario()

    # -- tiendas y canales

    def _alias_de_lugares(self, filas: list[tuple[str, str, str]]) -> dict[tuple[str, ...], set[str]]:
        mapa: dict[tuple[str, ...], set[str]] = {}

        def agregar(texto: str, id_: str) -> None:
            palabras = tuple(_palabras_de_lugar(texto))
            if palabras and id_ in self.tiendas:
                mapa.setdefault(palabras, set()).add(id_)

        for tipo, alias, valor in filas:
            if tipo in ("tienda", "ciudad"):
                agregar(alias, valor)
        for id_, datos in self.tiendas.items():
            agregar(datos["nombre"], id_)
            agregar(id_, id_)
        return mapa

    @staticmethod
    def _alias_de_canales(filas: list[tuple[str, str, str]]) -> dict[tuple[str, ...], set[str]]:
        mapa: dict[tuple[str, ...], set[str]] = {}
        for tipo, alias, valor in filas:
            if tipo != "canal":
                continue
            for texto in (alias, valor):
                palabras = tuple(_palabras_de_canal(texto))
                if palabras:
                    mapa.setdefault(palabras, set()).add(valor)
        return mapa

    def _nombres_oficiales(self) -> dict[str, str]:
        """El nombre de cada tienda, con y sin "Tienda", y su id: lo único que se resuelve sin nota."""
        oficiales: dict[str, str] = {}
        for id_, datos in self.tiendas.items():
            palabras = _alfanumericas(datos["nombre"])
            oficiales[" ".join(palabras)] = id_
            oficiales[" ".join(_sin_encabezado(palabras))] = id_
            oficiales[id_.lower()] = id_
        return oficiales

    def etiqueta_de_tienda(self, id_: str) -> str:
        datos = self.tiendas[id_]
        return f"{id_} {datos['nombre']} ({datos['ciudad']})"

    def es_nombre_oficial(self, texto: str, id_: str) -> bool:
        return self._oficiales.get(" ".join(_sin_encabezado(_alfanumericas(texto)))) == id_

    def _ids_explicitos(self, texto: str) -> list[str]:
        """Los ids del texto cuando son todo lo que dice ("T01 y T02") o es la etiqueta de una tienda.

        Un id con otras palabras ("T01 guadalupe") no se resuelve por el id: las otras palabras se
        buscarían como nombre y no se ignoran.
        """
        palabras = _alfanumericas(texto)
        ids = sorted({palabra.upper() for palabra in palabras if palabra.upper() in self.tiendas})
        otras = [palabra for palabra in palabras if palabra.upper() not in self.tiendas and palabra not in ("y", "e")]
        if ids and not otras:
            return ids
        es_etiqueta = len(ids) == 1 and palabras == _alfanumericas(self.etiqueta_de_tienda(ids[0]))
        return ids if es_etiqueta else []

    def buscar_tiendas(self, texto: str) -> tuple[list[str], bool]:
        """Los ids que dice el texto y si hubo que interpretarlo (nombre coloquial o error de dedo)."""
        palabras = _palabras_de_lugar(texto)
        if palabras and set(palabras) <= PALABRAS_DE_TODAS:
            return sorted(self.tiendas), False
        explicitas = self._ids_explicitos(texto)
        if explicitas:
            return explicitas, False
        destinos = _buscar_alias(palabras, self.alias_lugares) if palabras else set()
        if len(destinos) > 1:
            raise EntidadAmbigua("tienda", texto, [self.etiqueta_de_tienda(id_) for id_ in sorted(destinos)])
        if not destinos:
            raise EntidadInexistente("tienda", texto, [self.etiqueta_de_tienda(id_) for id_ in sorted(self.tiendas)])
        (id_,) = destinos
        return [id_], not self.es_nombre_oficial(texto, id_)

    # -- productos

    def _armar_vocabulario(self) -> None:
        nombres = {sku: _palabras_de_producto(p.nombre) for sku, p in self.productos.items()}
        grupos = {
            "subcategoría": self._nombres_por_campo("subcategoria"),
            "categoría": self._nombres_por_campo("categoria"),
        }
        palabras = {palabra for lista in nombres.values() for palabra in lista}
        for por_nombre in grupos.values():
            palabras |= {palabra for nombre in por_nombre for palabra in _palabras_de_producto(nombre)}
        self._base = _formas_base(palabras)
        self._tokens = {sku: frozenset(self._base[palabra] for palabra in lista) for sku, lista in nombres.items()}
        self._frecuencia = {base: sum(base in tokens for tokens in self._tokens.values()) for base in set(self._base.values())}
        self._candidatas = sorted(base for base in set(self._base.values()) if _se_puede_corregir_hacia(base))
        self.grupos = [
            _Grupo(tipo, nombre, frozenset(self._base[p] for p in _palabras_de_producto(nombre)), tuple(sorted(skus)))
            for tipo, por_nombre in grupos.items()
            for nombre, skus in sorted(por_nombre.items())
        ]
        patron = "|".join(re.escape(alias) for alias in sorted(self.alias_productos, key=len, reverse=True))
        self._patron_alias = re.compile(rf"(?<![a-z0-9])(?:{patron})(?![a-z0-9])") if patron else None

    def _nombres_por_campo(self, campo: str) -> dict[str, list[str]]:
        por_nombre: dict[str, list[str]] = {}
        for producto in self.productos.values():
            por_nombre.setdefault(getattr(producto, campo), []).append(producto.sku)
        return por_nombre

    @property
    def categorias(self) -> list[str]:
        return sorted({producto.categoria for producto in self.productos.values()})

    def _forma_base(self, palabra: str) -> str | None:
        for variante in (palabra, *_singulares(palabra), palabra + "s", palabra + "es"):
            if variante in self._base:
                return self._base[variante]
        return None

    def _corregir_palabra(self, palabra: str) -> str | None:
        """La palabra del catálogo de la que es error de dedo, si es clara y no es de las que están en todo."""
        corregida = _corregir(palabra, self._candidatas)
        if corregida is None or self._frecuencia.get(corregida, 0) > MAX_PRODUCTOS_POR_PALABRA_CORREGIDA:
            return None
        return corregida

    def _aplicar_alias(self, texto: str) -> str:
        if self._patron_alias is None:
            return texto
        cambiado = self._patron_alias.sub(lambda m: f" {self.alias_productos[m.group(0)]} ", texto)
        return " ".join(cambiado.split())

    def interpretar_producto(self, texto: str) -> _Consulta:
        """Lleva el texto a palabras base del catálogo: alias, unidades, plurales y errores de dedo."""
        crudo = normalizar(texto)
        sustituido = self._aplicar_alias(crudo)
        palabras: set[str] = set()
        desconocidas: list[str] = []
        corregidas = False
        for palabra in _PALABRA_O_CIFRA.findall(sustituido):
            if palabra in RELLENO_PRODUCTO:
                continue
            palabra = UNIDADES.get(palabra, palabra)
            base = self._forma_base(palabra)
            if base is None:
                base = self._corregir_palabra(palabra)
                corregidas = corregidas or base is not None
            if base is None:
                desconocidas.append(palabra)
            else:
                palabras.add(base)
        reinterpretado = corregidas or sustituido != " ".join(crudo.split())
        return _Consulta(
            frozenset(palabras), frozenset(palabras & SIMBOLOS_DE_UNIDAD), tuple(desconocidas), reinterpretado
        )

    def _grupo_de(self, palabras: frozenset[str]) -> _Grupo | None:
        return next((grupo for grupo in self.grupos if grupo.palabras == palabras), None)

    def _skus_con(self, palabras: frozenset[str]) -> list[str]:
        """Los productos cuyo nombre trae todas las palabras; si alguno es exactamente ese nombre, solo ese."""
        if not palabras:
            return []
        exactos = [sku for sku, tokens in self._tokens.items() if tokens == palabras]
        return exactos or self.con_palabra(palabras)

    def _sku_de_etiqueta(self, texto: str) -> str | None:
        """El SKU de una etiqueta con el id y el nombre, «SKU-1037 Naranja kg» o «Naranja kg (SKU-1037)».

        Así ofrece entidad_ambigua cada candidato y así lo nombra una nota, y el modelo a veces la copia entera. Un SKU con
        otras palabras no se resuelve por el SKU, igual que en las tiendas: esas palabras no se ignoran.
        """
        mayusculas = texto.upper()
        hallado = _SKU_EN_ETIQUETA.search(mayusculas)
        producto = self.productos.get(hallado.group()) if hallado else None
        if producto is None:
            return None
        sin_el_id = mayusculas.replace(hallado.group(), " ")
        return producto.sku if _alfanumericas(sin_el_id) == _alfanumericas(producto.nombre) else None

    def buscar_producto(self, texto: str) -> _Coincidencia:
        sku = texto.strip().upper()
        if _PATRON_SKU.fullmatch(sku):
            if sku not in self.productos:
                raise EntidadInexistente("producto", texto)
            return _Coincidencia([sku], None, frozenset(), False)
        de_la_etiqueta = self._sku_de_etiqueta(texto)
        if de_la_etiqueta:
            return _Coincidencia([de_la_etiqueta], None, frozenset(), False)
        consulta = self.interpretar_producto(texto)
        if consulta.desconocidas or not consulta.sin_unidades:
            raise EntidadInexistente("producto", texto)
        grupo = self._grupo_de(consulta.sin_unidades)
        if grupo:
            return _Coincidencia(list(grupo.skus), grupo, consulta.sin_unidades, consulta.reinterpretado)
        skus = self._skus_con(consulta.palabras) or self._skus_con(consulta.sin_unidades)
        if not skus:
            raise EntidadInexistente("producto", texto)
        return _Coincidencia(skus, None, consulta.sin_unidades, consulta.reinterpretado)

    def con_palabra(self, palabras: frozenset[str]) -> list[str]:
        """Los SKU cuyo nombre trae todas las palabras, sin importar si son el nombre exacto."""
        return [sku for sku, tokens in self._tokens.items() if palabras <= tokens]


def cargar_catalogos(con: duckdb.DuckDBPyConnection) -> Catalogos:
    """Lee de la base las tablas tiendas, alias y catalogo. con es el cursor de la llamada (Consultas.sesion())."""
    filas_tiendas = consultar(con, "SELECT tienda_id, nombre, ciudad, formato FROM tiendas ORDER BY tienda_id")
    tiendas = {f["tienda_id"]: {"nombre": f["nombre"], "ciudad": f["ciudad"], "formato": f["formato"]} for f in filas_tiendas}
    alias = [(f["tipo"], f["alias"], f["valor"]) for f in consultar(con, "SELECT tipo, alias, valor FROM alias")]
    filas_productos = consultar(
        con, "SELECT sku, nombre, categoria, subcategoria, fecha_baja, sku_sustituto FROM catalogo ORDER BY sku"
    )
    productos = [Producto(**{**fila, "sku_sustituto": fila["sku_sustituto"] or None}) for fila in filas_productos]
    return Catalogos(tiendas, alias, productos)


# ------------------------------------------------------------------ tiendas

@dataclass
class Tiendas:
    ids: list[str]
    notas: list[str] = field(default_factory=list)


def resolver_tiendas(textos: Sequence[str] | None, catalogos: Catalogos) -> Tiendas:
    """Los ids de las tiendas que dicen los textos; sin texto, las cuatro.

    Una ciudad con dos tiendas da entidad_ambigua con las dos; una tienda que no existe da
    entidad_inexistente con las que sí. Todo nombre que no sea el oficial deja una nota.
    """
    if not textos:
        return Tiendas(ids=sorted(catalogos.tiendas))
    ids: set[str] = set()
    notas: list[str] = []
    for texto in textos:
        encontradas, interpretado = catalogos.buscar_tiendas(texto)
        ids.update(encontradas)
        if interpretado:
            datos = catalogos.tiendas[encontradas[0]]
            notas.append(f"'{_citar(texto)}' se interpretó como {datos['nombre']} ({encontradas[0]})")
    return Tiendas(ids=sorted(ids), notas=notas)


# ------------------------------------------------------------------ canal y categoría

def resolver_canal(texto: str | None, catalogos: Catalogos, s: Supuestos | None = None) -> str | None:
    """El canal que dice el texto ("x internet" es Ecommerce) o canal_inexistente con los canales que sí hay."""
    if not texto:
        return None
    palabras = _palabras_de_canal(texto)
    destinos = _buscar_alias(palabras, catalogos.alias_canales) if palabras else set()
    if len(destinos) == 1:
        return next(iter(destinos))
    s = s or supuestos()
    raise CanalInexistente(texto, s.ventas.canales)


def _aviso_de_subcategoria(palabras: frozenset[str], catalogos: Catalogos) -> str | None:
    """Qué hacer si lo pedido como categoría es una subcategoría: ir por producto, no por la categoría padre (F6 de T5.11).

    Esa categoría suma otros productos y daría una cifra de otra cosa. Los nombres salen del catálogo y no del texto del
    modelo, que no es evidencia de una cifra (D1-A del plan de huecos).
    """
    for grupo in catalogos.grupos:
        if grupo.tipo == "subcategoría" and grupo.palabras == palabras:
            padre = catalogos.productos[grupo.skus[0]].categoria
            return (
                f"Es la subcategoría {grupo.nombre}, de {padre}: vuelve a llamar con ese nombre en producto; "
                f"no uses la categoría {padre}, que suma otros productos."
            )
    return None


def resolver_categoria(texto: str | None, catalogos: Catalogos) -> str | None:
    """La categoría del catálogo que dice el texto, o entidad_inexistente con las ocho que hay."""
    if not texto:
        return None
    consulta = catalogos.interpretar_producto(texto)
    categorias = [grupo for grupo in catalogos.grupos if grupo.tipo == "categoría"]
    aviso = None
    if not consulta.desconocidas:
        for grupo in categorias:
            if grupo.palabras == consulta.sin_unidades:
                return grupo.nombre
        aviso = _aviso_de_subcategoria(consulta.sin_unidades, catalogos)
    raise EntidadInexistente("categoria", texto, catalogos.categorias, aviso)


# ------------------------------------------------------------------ productos

@dataclass
class Productos:
    skus: list[str]
    nombres: dict[str, str] = field(default_factory=dict)
    notas: list[str] = field(default_factory=list)
    por_grupo: bool = False


def _familias(skus: Iterable[str], sustitutos: Mapping[str, str | None], s: Supuestos) -> list[set[str]]:
    """Agrupa los SKU que son un solo producto porque uno sustituye al otro, en cualquier sentido y cadena."""
    pendientes = set(skus)
    familias: list[set[str]] = []
    while pendientes:
        familia = skus_equivalentes(min(pendientes), sustitutos, s)
        familias.append(familia)
        pendientes -= familia
    return familias


def _presentaciones(cuantas: int) -> str:
    return "las dos presentaciones" if cuantas == 2 else f"las {cuantas} presentaciones"


def _nota_de_sustitucion(skus: Sequence[str], productos: Mapping[str, Producto]) -> str:
    """Quién sustituye a quién, desde cuándo, y que se suman: lo que dice la respuesta sin que se le pida."""
    enlaces = []
    encadenados: set[str] = set()
    for sku in skus:
        anterior = productos[sku]
        if anterior.sku_sustituto not in skus:
            continue
        nuevo = productos[anterior.sku_sustituto]
        vendio = f" vendió hasta el {fecha_larga(anterior.fecha_baja)} y" if anterior.fecha_baja else ""
        enlaces.append(f"{anterior.nombre} ({anterior.sku}){vendio} lo reemplaza {nuevo.nombre} ({nuevo.sku})")
        encadenados |= {anterior.sku, nuevo.sku}
    if not enlaces:
        return ""
    return f"{'; '.join(enlaces)}; se suman {_presentaciones(len(encadenados))}"


def _nombres_con_sku(skus: Iterable[str], productos: Mapping[str, Producto]) -> list[str]:
    return [f"{productos[sku].nombre} ({sku})" for sku in skus]


def resolver_productos(texto: str | None, catalogos: Catalogos, s: Supuestos | None = None) -> Productos | None:
    """Los SKU que dice el texto, sumando el SKU que sustituye al descontinuado (sumar_sustitutos).

    El nombre de una subcategoría o categoría ("pollo", "lácteos") son todos sus productos y lo dice; un
    nombre que coincide con varios productos distintos da entidad_ambigua con ellos; uno que no está en
    el catálogo, entidad_inexistente. Sin texto devuelve None: no hay filtro de producto.
    """
    if not texto or not texto.strip():
        return None
    s = s or supuestos()
    pedido = _citar(texto)
    encontrado = catalogos.buscar_producto(texto)
    sustitutos = {producto.sku: producto.sku_sustituto for producto in catalogos.productos.values()}
    familias = _familias(encontrado.skus, sustitutos, s)
    if len(familias) > 1 and encontrado.grupo is None:
        etiquetas = [f"{sku} {catalogos.productos[sku].nombre}" for sku in sorted(encontrado.skus)]
        raise EntidadAmbigua("producto", texto, etiquetas)
    skus = sorted(set().union(*familias))
    notas = _notas_de_producto(pedido, encontrado, skus, catalogos)
    return Productos(
        skus=skus,
        nombres={sku: catalogos.productos[sku].nombre for sku in skus},
        notas=notas,
        por_grupo=encontrado.grupo is not None,
    )


def _notas_de_producto(pedido: str, encontrado: _Coincidencia, skus: Sequence[str], catalogos: Catalogos) -> list[str]:
    productos = catalogos.productos
    notas: list[str] = []
    if encontrado.grupo:
        grupo = encontrado.grupo
        notas.append(f"'{pedido}' se tomó como la {grupo.tipo} {grupo.nombre}: {_lista(_nombres_con_sku(grupo.skus, productos))}")
        fuera = [sku for sku in catalogos.con_palabra(encontrado.palabras) if sku not in grupo.skus]
        if fuera:
            palabras = " ".join(sorted(encontrado.palabras))
            notas.append(f"También tienen '{palabras}' en el nombre y no se incluyen: {_lista(_nombres_con_sku(fuera, productos))}")
    elif encontrado.reinterpretado:
        notas.append(f"'{pedido}' se interpretó como {_lista(_nombres_con_sku(encontrado.skus, productos))}")
    sustitucion = _nota_de_sustitucion(skus, productos)
    if sustitucion:
        notas.append(sustitucion)
    return notas


# ------------------------------------------------------------------ periodos

@dataclass(frozen=True)
class Periodo:
    desde: dt.date | None
    hasta: dt.date
    etiqueta: str
    notas: list[str] = field(default_factory=list)

    @property
    def dias(self) -> int | None:
        return None if self.desde is None else (self.hasta - self.desde).days + 1


@dataclass(frozen=True)
class _Interpretado:
    desde: dt.date | None
    hasta: dt.date
    nota: str = ""


EJEMPLOS_DE_PERIODO = ["julio", "junio a agosto", "el trimestre", "ayer", "la última semana", "el mes pasado", "AAAA-MM-DD"]


def _no_entendido(texto: str) -> ErrorHerramienta:
    return ErrorHerramienta(
        "periodo_no_entendido", f"No entendí el periodo '{_citar(texto)}'", candidatos=EJEMPLOS_DE_PERIODO
    )


def _fin_de_mes(anio: int, mes: int) -> dt.date:
    return dt.date(anio, mes, calendar.monthrange(anio, mes)[1])


def _mes_completo(desde: dt.date, hasta: dt.date) -> bool:
    return desde.day == 1 and hasta == _fin_de_mes(desde.year, desde.month)


def _meses_completos(desde: dt.date, hasta: dt.date) -> bool:
    return desde.day == 1 and hasta == _fin_de_mes(hasta.year, hasta.month)


def _etiqueta(desde: dt.date | None, hasta: dt.date) -> str:
    if desde is None:
        return f"hasta el {fecha_larga(hasta)}"
    if desde == hasta:
        return fecha_larga(desde)
    if _mes_completo(desde, hasta):
        return fecha_larga(desde, con_dia=False)
    if _meses_completos(desde, hasta) and desde.year == hasta.year:
        return f"{MESES_NOMBRE[desde.month - 1]} a {fecha_larga(hasta, con_dia=False)}"
    if _meses_completos(desde, hasta):
        return f"{fecha_larga(desde, con_dia=False)} a {fecha_larga(hasta, con_dia=False)}"
    return f"del {fecha_larga(desde)} al {fecha_larga(hasta)}"


def _rango_en_palabras(desde: dt.date, hasta: dt.date) -> str:
    return f"del {fecha_larga(desde)} al {fecha_larga(hasta)}"


def _nombre_de_mes(anio: int, mes: int) -> str:
    return fecha_larga(dt.date(anio, mes, 1), con_dia=False)


# Fechas relativas: cada una cuenta desde el "hoy" de la referencia (D3-A) y devuelve (desde, hasta, frase).
# La frase dice qué fechas quedaron; sin frase, la nota es solo el aviso.

def _relativo_hoy(hoy: dt.date, _: re.Match[str]) -> tuple[dt.date, dt.date, str | None]:
    return hoy, hoy, None


def _relativo_ayer(hoy: dt.date, _: re.Match[str]) -> tuple[dt.date, dt.date, str | None]:
    dia = hoy - dt.timedelta(days=1)
    return dia, dia, f"ayer es el {fecha_larga(dia)}"


def _relativo_anteayer(hoy: dt.date, _: re.Match[str]) -> tuple[dt.date, dt.date, str | None]:
    dia = hoy - dt.timedelta(days=2)
    return dia, dia, f"anteayer es el {fecha_larga(dia)}"


def _lunes_de(dia: dt.date) -> dt.date:
    return dia - dt.timedelta(days=dia.weekday())


def _relativo_esta_semana(hoy: dt.date, _: re.Match[str]) -> tuple[dt.date, dt.date, str | None]:
    lunes = _lunes_de(hoy)
    domingo = lunes + dt.timedelta(days=6)
    return lunes, domingo, f"esta semana va {_rango_en_palabras(lunes, domingo)}"


def _relativo_semana_pasada(hoy: dt.date, _: re.Match[str]) -> tuple[dt.date, dt.date, str | None]:
    lunes = _lunes_de(hoy) - dt.timedelta(days=7)
    domingo = lunes + dt.timedelta(days=6)
    return lunes, domingo, f"la semana pasada va {_rango_en_palabras(lunes, domingo)}"


def _ultimos_dias(hoy: dt.date, cuantos: int) -> tuple[dt.date, dt.date]:
    return hoy - dt.timedelta(days=cuantos), hoy - dt.timedelta(days=1)


def _relativo_ultima_semana(hoy: dt.date, _: re.Match[str]) -> tuple[dt.date, dt.date, str | None]:
    desde, hasta = _ultimos_dias(hoy, 7)
    return desde, hasta, f"la última semana va {_rango_en_palabras(desde, hasta)}"


def _relativo_ultimos_dias(hoy: dt.date, coincidencia: re.Match[str]) -> tuple[dt.date, dt.date, str | None]:
    cuantos = 7 if coincidencia.group("n") == "siete" else int(coincidencia.group("n"))
    if cuantos < 1:
        raise ValueError(f"{cuantos} días no es un periodo")
    desde, hasta = _ultimos_dias(hoy, cuantos)
    return desde, hasta, f"los últimos {cuantos} días van {_rango_en_palabras(desde, hasta)}"


def _relativo_este_mes(hoy: dt.date, _: re.Match[str]) -> tuple[dt.date, dt.date, str | None]:
    return dt.date(hoy.year, hoy.month, 1), _fin_de_mes(hoy.year, hoy.month), f"este mes es {_nombre_de_mes(hoy.year, hoy.month)}"


def _relativo_mes_pasado(hoy: dt.date, _: re.Match[str]) -> tuple[dt.date, dt.date, str | None]:
    ultimo_del_anterior = dt.date(hoy.year, hoy.month, 1) - dt.timedelta(days=1)
    anio, mes = ultimo_del_anterior.year, ultimo_del_anterior.month
    return dt.date(anio, mes, 1), ultimo_del_anterior, f"el mes pasado es {_nombre_de_mes(anio, mes)}"


_FECHAS_RELATIVAS = (
    (re.compile(r"hoy"), _relativo_hoy),
    (re.compile(r"ayer"), _relativo_ayer),
    (re.compile(r"anteayer|antier|antes de ayer"), _relativo_anteayer),
    (re.compile(r"esta semana|semana actual|semana en curso"), _relativo_esta_semana),
    (re.compile(r"(?:la )?semana pasada"), _relativo_semana_pasada),
    (re.compile(r"(?:la )?ultima semana"), _relativo_ultima_semana),
    (re.compile(r"(?:los )?ultimos (?P<n>\d{1,3}|siete) dias"), _relativo_ultimos_dias),
    (re.compile(r"este mes|mes actual|mes en curso"), _relativo_este_mes),
    (re.compile(r"(?:el )?(?:mes pasado|ultimo mes)"), _relativo_mes_pasado),
)

_TODO_EL_CORTE = re.compile(r"(?:(?:el |este |todo el )?(?:trimestre|periodo(?: completo)?)|todo|total|historico|acumulado|(?:los )?tres meses)")


def _mes_de(palabra: str) -> tuple[int, bool] | None:
    """El número del mes que dice la palabra y si estaba bien escrita; None si no es un mes."""
    if palabra in MESES:
        return MESES[palabra], True
    if palabra in ABREVIATURAS_DE_MES:
        return ABREVIATURAS_DE_MES[palabra], False
    cercano = _corregir(palabra, MESES_NOMBRE)
    return (MESES[cercano], False) if cercano else None


@dataclass
class _Segmento:
    dia: int | None = None
    mes: int | None = None
    anio: int | None = None
    fecha: dt.date | None = None

    def vacio(self) -> bool:
        return all(valor is None for valor in (self.dia, self.mes, self.anio, self.fecha))

    def heredar(self, otro: _Segmento) -> None:
        """En "del 1 al 15 de julio", el 1 toma el mes y el año del 15."""
        if self.mes is None and self.dia is not None:
            self.mes = otro.mes
        if self.anio is None:
            self.anio = otro.anio


def _tokens_de_fecha(clave: str) -> list[str]:
    clave = re.sub(r"(?<![\d-])(\d{1,2})-(\d{1,2})(?![\d-])", r"\1 a \2", clave)
    clave = re.sub(r"(?<=[a-z])-(?=[a-z])", " a ", clave)
    return _TOKEN_DE_FECHA.findall(clave)


def _segmentos_de(tokens: Sequence[str]) -> tuple[list[_Segmento], set[str], bool]:
    """Parte los tokens en a lo más dos segmentos, separados por un conector de rango o de lista."""
    segmentos = [_Segmento()]
    conectores: set[str] = set()
    corregido = False
    for token in tokens:
        actual = segmentos[-1]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
            actual.fecha = dt.date.fromisoformat(token)
        elif re.fullmatch(r"\d{4}-\d{2}", token):
            actual.anio, actual.mes = int(token[:4]), int(token[5:])
            if not 1 <= actual.mes <= 12:
                raise ValueError(token)
        elif token.isdigit():
            _poner_numero(actual, int(token))
        elif token in RELLENO_PERIODO:
            continue
        elif token in CONECTORES_DE_PERIODO:
            conectores.add(token)
            if token not in APERTURAS_DE_RANGO:
                segmentos.append(_Segmento())
        else:
            mes = _mes_de(token)
            if mes is None or actual.mes is not None:
                raise ValueError(token)
            actual.mes = mes[0]
            corregido = corregido or not mes[1]
    return segmentos, conectores, corregido


def _poner_numero(segmento: _Segmento, numero: int) -> None:
    if 2000 <= numero <= 2100 and segmento.anio is None:
        segmento.anio = numero
    elif 1 <= numero <= 31 and segmento.dia is None:
        segmento.dia = numero
    else:
        raise ValueError(numero)


def _limites_de(segmento: _Segmento, anio_por_omision: int) -> tuple[dt.date, dt.date]:
    if segmento.fecha:
        return segmento.fecha, segmento.fecha
    anio = segmento.anio or anio_por_omision
    if segmento.mes is None:
        if segmento.dia is not None:
            raise ValueError("un día sin mes")
        return dt.date(anio, 1, 1), dt.date(anio, 12, 31)
    if segmento.dia is not None:
        dia = dt.date(anio, segmento.mes, segmento.dia)
        return dia, dia
    return dt.date(anio, segmento.mes, 1), _fin_de_mes(anio, segmento.mes)


def _es_lista(conectores: set[str]) -> bool:
    """Si el texto nombra dos periodos ("junio y julio") y no el rango que va de uno al otro ("entre junio y agosto")."""
    return bool(conectores & {"y", "e"}) and "entre" not in conectores


def _hay_hueco(primero: tuple[dt.date, dt.date], ultimo: tuple[dt.date, dt.date]) -> bool:
    """Entre el fin del primer periodo y el principio del último queda al menos un día: junio y agosto."""
    return primero[1] + dt.timedelta(days=1) != ultimo[0]


def _fechas_de_texto(clave: str, anio_por_omision: int) -> tuple[dt.date, dt.date, bool]:
    """Meses, días y rangos escritos con palabras o en AAAA-MM-DD; ValueError si no se entienden."""
    segmentos, conectores, corregido = _segmentos_de(_tokens_de_fecha(clave))
    if len(segmentos) > 2 or any(segmento.vacio() for segmento in segmentos):
        raise ValueError(clave)
    if len(segmentos) == 1 and conectores:
        # "desde julio" dice dónde empieza y no dónde acaba: leerlo como el mes suelto sería adivinar.
        raise ValueError(clave)
    if len(segmentos) == 2:
        segmentos[0].heredar(segmentos[1])
        segmentos[1].heredar(segmentos[0])
    primero, ultimo = _limites_de(segmentos[0], anio_por_omision), _limites_de(segmentos[-1], anio_por_omision)
    if primero[0] > ultimo[1]:
        raise ValueError(clave)
    if _es_lista(conectores) and _hay_hueco(primero, ultimo):
        raise ValueError(clave)
    return primero[0], ultimo[1], corregido


def es_aviso_de_fecha_relativa(nota: str, s: Supuestos | None = None) -> bool:
    """Si una nota de un resultado es el aviso de una fecha relativa (aviso_fecha_relativa de config/supuestos.yaml).

    La pantalla lo pone en el pie, con la fuente y el corte, en lugar de esperar que el modelo lo copie (K29). Compara el
    principio de la nota, que escribe el código: una interpretación empieza con el texto del modelo entre comillas.
    """
    s = s or supuestos()
    return nota.startswith(s.aviso_fecha_relativa.split("{", maxsplit=1)[0])


def _dice_las_mismas_fechas(
    texto: str, desde: dt.date | None, hasta: dt.date | None, fuente: FuenteConCorte, s: Supuestos, hoy_real: dt.date | None,
) -> bool:
    """Si el texto del periodo es otra forma de decir esas fechas: «julio» con el 1 y el 31 de julio no contrasta con nada."""
    try:
        interpretado = _interpretar(texto, fuente, s, hoy_real or dt.date.today())
    except ErrorHerramienta:
        return False
    return (interpretado.desde, interpretado.hasta) == (desde, hasta)


def _interpretar(texto: str, fuente: FuenteConCorte, s: Supuestos, hoy_real: dt.date) -> _Interpretado:
    clave = normalizar(texto)
    for patron, calcular in _FECHAS_RELATIVAS:
        coincidencia = patron.fullmatch(clave)
        if coincidencia is None:
            continue
        try:
            desde, hasta, frase = calcular(s.fecha_referencia, coincidencia)
        except ValueError as error:
            raise _no_entendido(texto) from error
        aviso = aviso_fecha_relativa(hoy_real, s)
        return _Interpretado(desde, hasta, aviso if frase is None else f"{aviso}; {frase}")
    if _TODO_EL_CORTE.fullmatch(clave):
        desde, hasta, _nota = en_corte(None, None, fuente)
        return _Interpretado(desde, hasta, f"'{_citar(texto)}' se tomó como {_etiqueta(desde, hasta)}")
    try:
        desde, hasta, corregido = _fechas_de_texto(clave, s.cortes.ventas.hasta.year)
    except ValueError as error:
        raise _no_entendido(texto) from error
    nota = f"'{_citar(texto)}' se interpretó como {_etiqueta(desde, hasta)}" if corregido else ""
    return _Interpretado(desde, hasta, nota)


def _recortar_al_corte(texto: str, interpretado: _Interpretado, fuente: FuenteConCorte) -> Periodo:
    try:
        desde, hasta, recorte = en_corte(interpretado.desde, interpretado.hasta, fuente)
    except ErrorHerramienta as error:
        if error.estado != "fuera_de_periodo":
            raise
        raise FueraDePeriodo(
            fuente, interpretado.desde, interpretado.hasta, _citar(texto), interpretado.nota or None
        ) from error
    notas = [nota for nota in (interpretado.nota, f"{_citar(texto)}: {recorte}" if recorte else "") if nota]
    return Periodo(desde, hasta, _etiqueta(desde, hasta), notas)


def _texto_de_fechas(desde: dt.date | None, hasta: dt.date | None) -> str:
    if desde and hasta:
        return f"{desde.isoformat()} a {hasta.isoformat()}"
    return f"desde {desde.isoformat()}" if desde else f"hasta {hasta.isoformat()}"


def resolver_periodo(
    texto: str | None = None,
    desde: dt.date | None = None,
    hasta: dt.date | None = None,
    fuente: FuenteConCorte = "ventas",
    hoy_real: dt.date | None = None,
    s: Supuestos | None = None,
) -> Periodo:
    """El periodo que dicen el texto o las fechas, recortado al corte de la fuente.

    Sin texto ni fechas es todo el corte. Las fechas explícitas mandan sobre el texto. Un periodo que
    no toca ningún día con datos da fuera_de_periodo (nunca una venta de cero); si salió de una fecha
    relativa, el error trae cómo se interpretó. Un texto que no se entiende da periodo_no_entendido,
    también el que dice un solo extremo ("desde julio", "hasta julio"): para eso están desde y hasta.
    hoy_real es la fecha del reloj, para el aviso de que esa fecha no tiene datos; en las pruebas se fija.
    """
    s = s or supuestos()
    if desde is not None or hasta is not None:
        recortado_desde, recortado_hasta, recorte = en_corte(desde, hasta, fuente)
        contrasta = texto and not _dice_las_mismas_fechas(texto, desde, hasta, fuente, s, hoy_real)
        ignorado = [f"se usaron las fechas pedidas, {_texto_de_fechas(desde, hasta)}, y no '{_citar(texto)}'"] if contrasta else []
        notas = [*ignorado, *([recorte] if recorte else [])]
        return Periodo(recortado_desde, recortado_hasta, _etiqueta(recortado_desde, recortado_hasta), notas)
    if not texto or not texto.strip():
        desde_del_corte, hasta_del_corte, _ = en_corte(None, None, fuente)
        return Periodo(desde_del_corte, hasta_del_corte, _etiqueta(desde_del_corte, hasta_del_corte))
    interpretado = _interpretar(texto, fuente, s, hoy_real or dt.date.today())
    return _recortar_al_corte(texto, interpretado, fuente)
