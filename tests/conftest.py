"""Fixtures compartidas: la base se construye en un directorio temporal, nunca en build/ (T1.2).

Cada sesión de pytest (y cada proceso, si las pruebas corren en paralelo) construye su propia
copia en tmp_path_factory: así ninguna prueba bloquea el archivo de otra ni el de build/, que
DuckDB solo deja abrir a un proceso cuando escribe.
"""

import hashlib
import shutil
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATOS = RAIZ / "datos"
DIR_CONFIG = RAIZ / "config"

# El paquete de entrega sale de git archive, sin .git y sin docs/ (T8.4, D17-A). Lo que depende de ellos se salta ahí y corre en el
# repositorio.
SIN_GIT = "el paquete de entrega no trae .git: esta prueba consulta git"
SIN_DOCS = "el paquete de entrega no trae docs/: esta prueba lee el plan o sus índices"
requiere_git = pytest.mark.skipif(not (RAIZ / ".git").exists(), reason=SIN_GIT)
requiere_docs = pytest.mark.skipif(not (RAIZ / "docs").exists(), reason=SIN_DOCS)
# Del zip también salen los reportes que solo sirvieron para decidir durante el desarrollo (.gitattributes). La señal
# de estar en el paquete es la falta de docs/: en el repositorio, un reporte que falte sigue haciendo fallar su prueba.
SIN_REPORTES = "el paquete de entrega no trae este reporte: solo viajan los que cita el README o la plantilla"
requiere_reportes = pytest.mark.skipif(not (RAIZ / "docs").exists(), reason=SIN_REPORTES)

# Cada construir.construir vuelve a leer los 4 PDF de políticas con pdfplumber, cerca del 90 % de su tiempo, y la
# suite la llama unas 160 veces. Dentro de la suite cada extracción se guarda por nombre y sha256 del archivo: un
# PDF igual no se vuelve a leer y uno que cambió (una copia alterada) sí. Cada proceso tiene la suya.
_EXTRAIDOS = {}


def _extraer_pdf_una_vez(extraer):
    def extraer_pdf(ruta):
        ruta = Path(ruta)
        llave = (ruta.name, hashlib.sha256(ruta.read_bytes()).hexdigest())
        if llave not in _EXTRAIDOS:
            _EXTRAIDOS[llave] = extraer(ruta)
        return _EXTRAIDOS[llave]

    return extraer_pdf


@pytest.fixture(scope="session", autouse=True)
def extraccion_de_pdf_en_cache():
    """Pone la caché de _EXTRAIDOS delante de extraer_politicas.extraer_pdf durante toda la sesión."""
    from agente import extraer_politicas

    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(extraer_politicas, "extraer_pdf", _extraer_pdf_una_vez(extraer_politicas.extraer_pdf))
        yield


@pytest.fixture(scope="session")
def base_construida(tmp_path_factory):
    """Base completa (datos, supuestos, alias, cobertura y fragmentos) sin el índice de texto.

    El índice necesita la extensión fts, que se descarga de la red la primera vez (T0.5); las
    pruebas corren sin red, así que esta base no lo trae. Devuelve el ResumenConstruccion, cuyo
    atributo base es la ruta del archivo.
    """
    from agente import construir

    destino = tmp_path_factory.mktemp("base") / "tienda.duckdb"
    return construir.construir(DIR_DATOS, destino, indice_texto=False)


@pytest.fixture
def copia_datos(tmp_path):
    """Copia de datos/ en tmp_path para alterarla sin tocar los originales (copias A a D)."""
    destino = tmp_path / "datos"
    shutil.copytree(DIR_DATOS, destino)
    return destino


@pytest.fixture
def copia_config(tmp_path):
    """Copia de config/ en tmp_path para cambiar una definición sin tocar la original."""
    destino = tmp_path / "config"
    shutil.copytree(DIR_CONFIG, destino)
    return destino
