"""Caché de la extracción de PDF dentro de la suite (tests/conftest.py).

Cada construir.construir vuelve a leer los 4 PDF de políticas con pdfplumber, cerca del 90 % de su tiempo, y la
suite la llama unas 160 veces. La caché guarda cada extracción por nombre y sha256 del archivo: un PDF igual no
se vuelve a leer y uno que cambió sí. Estas pruebas fijan las dos mitades.
"""

import shutil
from pathlib import Path

import pytest

from agente import extraer_politicas
from tests import conftest

DIR_PDF = Path(__file__).resolve().parent.parent / "politicas"


@pytest.fixture
def aperturas(monkeypatch):
    """Cada ruta que pdfplumber abre, con la caché vacía."""
    monkeypatch.setattr(conftest, "_EXTRAIDOS", {})
    abiertas = []
    abrir = extraer_politicas.pdfplumber.open

    def abrir_y_anotar(ruta, *argumentos, **opciones):
        abiertas.append(Path(ruta))
        return abrir(ruta, *argumentos, **opciones)

    monkeypatch.setattr(extraer_politicas.pdfplumber, "open", abrir_y_anotar)
    return abiertas


def _dos_pdf():
    primero, segundo, *_ = sorted(DIR_PDF.glob("*.pdf"))
    return primero, segundo


def test_un_pdf_igual_se_lee_una_sola_vez(aperturas, tmp_path):
    primero, _ = _dos_pdf()
    copia = tmp_path / "politica.pdf"
    shutil.copyfile(primero, copia)

    una = extraer_politicas.extraer_pdf(copia)
    otra = extraer_politicas.extraer_pdf(copia)

    assert otra == una
    assert aperturas == [copia]


def test_un_pdf_que_cambio_se_vuelve_a_leer(aperturas, tmp_path):
    primero, segundo = _dos_pdf()
    copia = tmp_path / "politica.pdf"
    shutil.copyfile(primero, copia)
    antes = extraer_politicas.extraer_pdf(copia)

    shutil.copyfile(segundo, copia)
    despues = extraer_politicas.extraer_pdf(copia)

    assert len(aperturas) == 2
    assert despues.codigo != antes.codigo
    assert despues.codigo == extraer_politicas.extraer_pdf(segundo).codigo


def test_el_mismo_contenido_con_otro_nombre_conserva_su_nombre(aperturas, tmp_path):
    # DocumentoExtraido guarda el nombre del archivo: dos copias iguales con distinto nombre no comparten entrada.
    primero, _ = _dos_pdf()
    copia = tmp_path / "otro_nombre.pdf"
    shutil.copyfile(primero, copia)

    assert extraer_politicas.extraer_pdf(primero).archivo == primero.name
    assert extraer_politicas.extraer_pdf(copia).archivo == "otro_nombre.pdf"
