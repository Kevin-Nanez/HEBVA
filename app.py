"""Interfaz de chat en Streamlit (D6, T4.1, T4.2, T4.4).

Historial por sesión, panel de traza plegado y marca de "respuesta incorrecta". Llama a
agente.agente.responder() y muestra la salida del modelo como texto. La pantalla inicial y la
pregunta "qué sabes" se responden sin llamar al modelo.

Se corre con `streamlit run app.py`. Un turno (T4.1):

1. La sesión del navegador tiene su Historial (agente/historial.py) en st.session_state. No vive en
   st.cache_resource ni en una variable de módulo: Streamlit vuelve a ejecutar este archivo completo en cada
   interacción y esos dos sitios se comparten entre sesiones, así que dos navegadores abiertos a la vez
   mezclarían contexto (P52). La interfaz tampoco guarda una conexión a la base: responder() abre y cierra la suya en
   cada turno, y en Windows una conexión abierta entre turnos haría que el siguiente asegurar_base() fallara al
   cambiar una fuente.
2. La pregunta va a responder(pregunta, historial) con modelo=None, para que lea MODELO de .env, y con
   archivo_registro=registro.ARCHIVO, para que el turno deje su línea en logs/turnos.jsonl (origen "interfaz"). Con
   la Respuesta se hace historial.agregar(pregunta, respuesta). La respuesta no se transmite: el verificador (T3.4)
   necesita el texto completo antes de que la gerente lo lea. La caja de pregunta se apaga mientras corre el turno,
   porque un Historial se usa de a un turno por vez.
3. La salida del modelo, la pregunta, los valores de los resultados y el texto de los tickets se muestran como
   texto (K23): st.text, st.code y st.dataframe, que no interpretan Markdown ni HTML. Una imagen remota en Markdown
   puede sacar datos en su URL y st.table, st.markdown y st.write sí la cargarían. A las funciones que sí lo
   interpretan solo llegan textos escritos en este archivo (tests/test_app.py lo comprueba con ast).
4. La pantalla muestra primero la respuesta sin sus notas. Lo demás va en un panel plegado ("Ver cómo se obtuvo la
   respuesta"), en este orden: las notas que escribió el modelo; la fuente y el corte de cada consulta (K29), armados con
   las evidencias del turno y no con lo que dijo el modelo (la herramienta con sus filtros y la fecha de corte, o el
   documento, la sección y la página de la política citada); el aviso de una fecha relativa, que el modelo a veces
   resume; y, por herramienta, la consulta SQL tal como la guardó el registro, los filtros, las cifras y las filas, la
   fuente y, en políticas, el fragmento citado. Cierra con las llamadas al modelo y los tokens del turno, con los de
   razonamiento aparte si el proveedor los separa. Dos textos se quedan a la vista porque avisan que hay algo por revisar:
   la nota fija de texto de ticket y el aviso de tickets sospechosos. Mover las notas es solo de la pantalla: el
   verificador, el registro y la evaluación ven el texto completo. Una respuesta que cita una política trae además, debajo
   del texto y en gris, el documento, la sección y la página citados, armados con las evidencias: el brief pide que la
   respuesta los indique, y el modelo a veces solo los escribe en la línea «Fuente:» que la pantalla quita.
5. Los estados sin cifra (tope, modelo no disponible, fuente no disponible) traen un texto fijo que se muestra tal
   cual. No llevan fuente ni panel, y la cifra de una herramienta que alcanzó a correr antes del corte tampoco se
   muestra: el texto dice que no hay ninguna cifra (K19), y esa información queda en el registro para quien
   administra el sistema. Lo mismo vale para una excepción de responder(): la gerente lee un texto fijo, sin traza
   técnica (K21), y el log solo recibe el tipo del error.
6. La pantalla inicial trae solo una frase con los temas que conoce y cómo preguntar. Lo que cubre el sistema (tiendas,
   periodos, cortes, documentos y 3 ejemplos de preguntas que no puede contestar) lo da el manifiesto de cobertura de la
   base (agente/manifiesto.py, T4.2, C4.4). Un mensaje que es solo
   "qué sabes" o ayuda, con o sin un saludo antes, se responde con ese manifiesto sin llamar a responder() ni al
   modelo; su turno
   deja en logs/turnos.jsonl una línea con 0 llamadas y 0 tokens, y su vuelta no entra al Historial, de modo que el
   modelo no la ve como contexto de la pregunta siguiente. La fuente de esa respuesta es el manifiesto y su panel dice
   que no usó el modelo. Si el manifiesto no se puede leer, la pantalla lo dice con un texto fijo y la caja de pregunta
   sigue. La pantalla inicial lee el manifiesto una vez por sesión del navegador, no en cada chat vacío.
7. Un mensaje con varias preguntas (T4.6) se separa por sus signos de cierre (agente/preguntas.py) y cada una se
   contesta con su propio turno de responder(): su texto, su fuente y su panel salen de las evidencias de ese turno y
   no se mezclan con los de otra. La pantalla las muestra en orden, cada una debajo de su pregunta y separadas por una
   línea. Se contestan hasta 3; si hay más, o si una falla por algo que la siguiente repetiría (sin modelo, sin fuente,
   una excepción), un aviso dice que las demás no se contestaron.
8. Cada respuesta que tiene turno (T4.4, K28) trae un texto discreto "Respuesta incorrecta", antes del panel: con el panel
   abierto, Tab no baja de la tabla. Abre un cuadro con un comentario opcional y el botón "Marcar como incorrecta"; abrirlo
   no escribe nada, de modo que un clic por curiosidad no deja marca. Al pulsar el botón se agrega a logs/turnos.jsonl una
   línea `marca` con el id del turno, la hora y el comentario si lo hay (agente/registro.py), sin la pregunta, sin la
   respuesta y sin llamar al modelo; el comentario pasa por el enmascarado del registro y no entra al Historial. El cuadro
   se cambia por un texto que dice que quedó marcada, y un rerun no repite la línea porque la escribe el callback del botón
   y no el cuerpo del script. Si el registro no se puede escribir, un texto lo dice y el cuadro sigue con lo escrito. Un
   error inesperado no tiene turno y no trae el cuadro. `python -m eval.casos_desde_marcas` convierte las marcas en casos
   pendientes. El botón es un st.form_submit_button dentro de un st.form con el id del turno por clave: un st.button con
   esa clave no pasaría la prueba de tests/test_app.py que exige texto fijo en todo st.button. Por esa misma prueba
   st.popover lleva su etiqueta escrita en la llamada y no recibe clave.
9. La barra lateral lista los chats de la sesión del navegador, el más reciente primero, con "Nuevo chat" arriba. Cada
   chat tiene su Historial y sus turnos, de modo que el modelo solo ve como contexto lo del chat abierto, y cada uno
   escribe en el registro con su propio id de sesión. Viven en st.session_state: recargar la página o reiniciar el
   servidor los pierde. El título sale de la primera pregunta y va en st.text (K23); por eso el botón al lado dice
   "Abrir", con texto fijo, en lugar de llevar el título como etiqueta, que interpretaría Markdown.
10. Las tablas y las listas que escribe el modelo en Markdown se reconocen por la forma de sus renglones (agente/bloques.py)
   y se dibujan sin interpretar Markdown (K23). Una lista va en st.text, un elemento por renglón. Una tabla va en un
   componente propio que recibe las celdas como datos y las escribe con textContent: st.table interpreta Markdown, y
   st.dataframe dentro del cuerpo de la respuesta atraparía el Tab antes de "Respuesta incorrecta". Los textos
   fijos (una excepción, un aviso) no se parten, y de la respuesta del manifiesto solo se parten sus tablas (tiendas,
   periodos y documentos): sus viñetas van en st.text tal como las escribe.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import streamlit as st
from pydantic_ai.exceptions import UnexpectedModelBehavior

from agente import agente
from agente import bloques as modulo_bloques
from agente import construir as modulo_construir
from agente import historial as modulo_historial
from agente import manifiesto as modulo_manifiesto
from agente import preguntas as modulo_preguntas
from agente import registro as modulo_registro
from agente import reintentos as modulo_reintentos
from agente import resolver as modulo_resolver
from agente import verificacion as modulo_verificacion
from agente.herramientas.comun import Resultado, entero, fecha_larga

_aviso = logging.getLogger(__name__)

# Quita los íconos del chat y pone las preguntas a la derecha y las respuestas a la izquierda. Streamlit no tiene ajuste
# para esto. Es un texto fijo sin datos de la gerente ni del modelo, así que no abre nada a K23: todo lo demás sigue en
# st.text. La pregunta se reconoce por su ícono, que sigue en la página aunque no se dibuje.
# El ancho es el 65% de la ventana menos los 300 px de la barra lateral, con o sin ella plegada: plegarla solo vuelve a
# centrar la conversación. Los 480 px evitan una columna angosta en ventanas chicas.
ESTILOS_DEL_CHAT = """
[data-testid="stMainBlockContainer"],
[data-testid="stBottomBlockContainer"] {
    max-width: max(480px, calc((100vw - 300px) * 0.65));
}
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {
    display: none;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    margin-left: auto;
    width: fit-content;
    max-width: 75%;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
    margin-right: auto;
    background: transparent;
}
[class*="st-key-lista-"] [data-testid="stText"] {
    padding-left: 0.45em;
    text-indent: -0.45em;
}
[class*="st-key-fuente-"] [data-testid="stText"] {
    font-size: 0.8rem;
    opacity: 0.7;
}
[class*="st-key-reporte-"] [data-testid="stPopoverButton"] {
    min-height: 1.5rem;
    padding: 0;
    opacity: 0.75;
}
[class*="st-key-reporte-"] [data-testid="stPopoverButton"]:hover,
[class*="st-key-reporte-"] [data-testid="stPopoverButton"]:focus-visible {
    opacity: 1;
}
[class*="st-key-reporte-"] [data-testid="stPopoverButton"] p,
[class*="st-key-reporte-"] [data-testid="stText"] {
    font-size: 0.8rem;
}
[data-testid="stPopoverBody"] [data-testid="InputInstructions"] span:first-child {
    display: none;
}
[data-testid="stPopoverBody"] [data-testid="InputInstructions"] span::before {
    content: none;
}
"""

# Con la caja de chat, Streamlit deja la pantalla pegada al final mientras esté abajo: si el contenido crece, baja con él, y por
# eso abrir el panel de la última respuesta llevaba la vista al final del panel. Streamlit no tiene ajuste para esto. Aquí se
# apaga ese pegado en el contenedor, que lo calcula con su scrollHeight y lo aplica escribiendo su scrollTop, y la regla es
# otra: la pantalla baja cuando la gerente pregunta o abre un chat, sigue a la respuesta mientras crece y deja de seguirla en
# cuanto la gerente toca o desplaza la pantalla. El desplazamiento a mano no pasa por esas propiedades y sigue igual.
# No lee ni escribe datos de la gerente ni del modelo, así que no abre nada a K23.
JS_DE_LA_PANTALLA = """
const AL_FINAL = Number.MAX_SAFE_INTEGER;
const ABRE_CONTENIDO = '[data-testid="stChatInputSubmitButton"], [data-testid="stSidebar"] [data-testid="stForm"] button';
let siguiendo = false;

const pantalla = () => document.querySelector('[data-testid="stAppScrollToBottomContainer"]');

const bajar = () => pantalla()?.scrollTo(0, AL_FINAL);

const empezarASeguir = () => {
    siguiendo = true;
    bajar();
};

const dejarDeSeguir = () => {
    siguiendo = false;
};

const alPresionarPuntero = (evento) => {
    if (evento.target.closest?.(ABRE_CONTENIDO)) {
        empezarASeguir();
        return;
    }
    dejarDeSeguir();
};

const alPresionarTecla = (evento) => {
    const enLaCaja = evento.target.closest?.('[data-testid="stChatInputTextArea"]');
    if (!enLaCaja) {
        dejarDeSeguir();
        return;
    }
    if (evento.key === "Enter" && !evento.shiftKey) {
        empezarASeguir();
    }
};

const apagarPegado = (contenedor) => {
    const nativo = Object.getOwnPropertyDescriptor(Element.prototype, "scrollTop");
    Object.defineProperty(contenedor, "scrollHeight", {
        configurable: true,
        get() {
            return this.offsetHeight;
        },
    });
    Object.defineProperty(contenedor, "scrollTop", {
        configurable: true,
        get() {
            return nativo.get.call(this);
        },
        set() {},
    });
};

const prepararPantalla = () => {
    const contenedor = pantalla();
    const contenido = document.querySelector('[data-testid="stMainBlockContainer"]');
    if (!contenedor || !contenido || contenedor.hebvaLista) {
        return;
    }
    contenedor.hebvaLista = true;
    apagarPegado(contenedor);
    new ResizeObserver(() => {
        if (siguiendo) {
            bajar();
        }
    }).observe(contenido);
    contenedor.addEventListener("wheel", dejarDeSeguir, { passive: true });
    contenedor.addEventListener("touchstart", dejarDeSeguir, { passive: true });
};

export default function () {
    prepararPantalla();
    if (window.hebvaPantallaLista) {
        return;
    }
    window.hebvaPantallaLista = true;
    document.addEventListener("pointerdown", alPresionarPuntero, true);
    document.addEventListener("keydown", alPresionarTecla, true);
}
"""

# Streamlit dibuja el cuadro de "Respuesta incorrecta" al final de la página y deja el foco en el texto que lo abre: con Tab
# se pasaría por el panel, y con el panel abierto no se saldría de su tabla. Este código solo mueve el foco al comentario;
# no lee ni escribe lo que hay en la caja, así que no abre nada a K23.
JS_DE_LA_MARCA = """
const ESPERA_DEL_CUADRO_EN_MS = 100;

const enfocarElComentario = (evento) => {
    if (!evento.target.closest?.('[class*="st-key-reporte-"] [data-testid="stPopoverButton"]')) {
        return;
    }
    setTimeout(() => {
        document.querySelector('[data-testid="stPopoverBody"] textarea')?.focus();
    }, ESPERA_DEL_CUADRO_EN_MS);
};

export default function () {
    if (window.hebvaMarcaLista) {
        return;
    }
    window.hebvaMarcaLista = true;
    document.addEventListener("click", enfocarElComentario);
}
"""

# La tabla de una respuesta (K23). Las celdas son del modelo y llegan como datos: este código las escribe con textContent, que
# no interpreta HTML ni Markdown, así que una imagen o un script en una celda se ven como texto y no se piden ni se ejecutan.
COMPONENTE_DE_TABLA = "tabla_de_respuesta"
HTML_DE_TABLA = '<div class="marco"><table></table></div>'
CSS_DE_TABLA = """
.marco {
    overflow-x: auto;
}
table {
    border-collapse: collapse;
    color: var(--st-text-color);
    font-family: var(--st-font);
    font-size: var(--st-base-font-size);
}
th, td {
    padding: 0.35rem 0.75rem;
    text-align: left;
    vertical-align: top;
    border-bottom: 1px solid var(--st-border-color);
}
th {
    font-weight: 600;
    background: var(--st-secondary-background-color);
}
th.cifra, td.cifra {
    text-align: right;
    font-variant-numeric: tabular-nums;
}
"""
JS_DE_TABLA = """
export default function ({ data, parentElement }) {
    const renglon = (celdas, etiqueta) => {
        const fila = document.createElement("tr");
        celdas.forEach((texto, columna) => {
            const celda = document.createElement(etiqueta);
            celda.textContent = texto;
            celda.classList.toggle("cifra", data.columnas_de_cifras[columna]);
            if (etiqueta === "th") {
                celda.scope = "col";
            }
            fila.append(celda);
        });
        return fila;
    };
    const encabezado = document.createElement("thead");
    encabezado.append(renglon(data.encabezados, "th"));
    const cuerpo = document.createElement("tbody");
    cuerpo.append(...data.filas.map((celdas) => renglon(celdas, "td")));
    parentElement.querySelector("table").replaceChildren(encabezado, cuerpo);
}
"""

# Lo que lee la gerente cuando responder() lanza una excepción que ningún estado previó. Sin cifras ni jerga (K21).
MENSAJE_INESPERADO = (
    "No pude responder por un error inesperado. No te doy ninguna cifra. "
    "Vuelve a intentarlo; si se repite, avisa a quien administra el sistema."
)
# Lo que lee la gerente cuando un mensaje trae preguntas que no se contestaron (T4.6).
AVISO_PREGUNTAS_DE_MAS = (
    f"Contesté las primeras {modulo_preguntas.MAXIMO_PREGUNTAS} preguntas de tu mensaje. "
    "Envíame las demás en otro mensaje."
)
AVISO_SIN_SEGUIR = "No seguí con las demás preguntas de tu mensaje. Vuelve a enviarlas más tarde."
SIN_CONSULTA = "Fuente: esta respuesta no consultó los datos ni las políticas."
# La respuesta a "qué sabes" sí leyó algo, el manifiesto de cobertura de la base, y no usó el modelo (T4.2).
FUENTE_DEL_MANIFIESTO = (
    "Fuente: manifiesto de cobertura de los datos y los documentos cargados. Esta respuesta no usó el modelo."
)
# La marca en st.session_state de que la pantalla inicial ya revisó la base en esta sesión del navegador.
CLAVE_DATOS_REVISADOS = "datos_revisados"
# Lo que lee la gerente en lugar de lo que cubre el sistema si el manifiesto falla por algo que no es la base. Sin jerga
# ni traza (K21).
SIN_COBERTURA = (
    "No pude revisar qué datos hay disponibles. Puedes escribir tu pregunta; "
    "si el problema sigue, avisa a quien administra el sistema."
)
# Lo que lee la gerente dentro del cuadro de "Respuesta incorrecta" (T4.4, K28). Textos fijos, sin cifras. La etiqueta del
# cuadro va escrita en st.popover: la prueba de K23 no acepta ahí una constante con nombre.
ETIQUETA_DE_MARCA = "Marcar como incorrecta"
ETIQUETA_DE_COMENTARIO = "¿Qué está mal? (opcional)"
TEXTO_MARCADA = "Marcaste esta respuesta como incorrecta. Quedó anotada para revisarla."
TEXTO_SIN_GUARDAR = "No pude guardar la marca. Vuelve a intentarlo; si se repite, avisa a quien administra el sistema."
# La barra lateral de chats.
LARGO_TITULO_CHAT = 30
MARCA_CHAT_ACTUAL = "▸ "
SIN_CHATS = "Aún no hay chats."

# Las herramientas que devuelven fragmentos de política y no filas de datos.
HERRAMIENTA_DE_POLITICAS = "buscar_politicas"


# ------------------------------------------------------------------ el texto del modelo

_NEGRITAS = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")


def sin_negritas(texto: str) -> str:
    """El texto sin los marcadores de negrita de Markdown («**así**» pasa a «así»), que el modelo suele escribir.

    La interfaz no interpreta Markdown (K23): sin esto, la cifra de la primera línea se vería entre asteriscos. Solo
    quita esos marcadores; ninguna cifra, cita ni palabra cambia, de modo que lo que se muestra es lo que revisó el
    verificador. Las imágenes, los enlaces y el HTML se siguen viendo tal cual, como texto.
    """
    return _NEGRITAS.sub(lambda coincidencia: coincidencia.group(1) or coincidencia.group(2), texto)


_LINEA_DE_FUENTE = re.compile(r"[-•\s]*fuentes?\s*:", re.IGNORECASE)


def sin_linea_de_fuente(texto: str) -> str:
    """El texto sin las líneas «Fuente: ...» que el modelo escribe a veces debajo de la primera, aunque el prompt no lo pide.

    La pantalla ya trae la fuente y el corte armados con las evidencias (K29), y esa línea solo repite la del pie. La
    primera línea se queda: si le preguntan de dónde sale algo, esa es la respuesta. Se quita solo de lo que se muestra;
    el verificador y el registro ven el texto completo, y las citas de esa línea siguen eligiendo los fragmentos del pie.
    """
    primera, *resto = texto.split("\n")
    sin_fuente = "\n".join([primera, *(linea for linea in resto if not _LINEA_DE_FUENTE.match(linea))])
    return re.sub(r"\n{3,}", "\n\n", sin_fuente).rstrip()


_LINEA_DE_NOTA = re.compile(r"[-*•\s]*(?P<nota>notas?\s*:.*)", re.IGNORECASE)


def _nota_para_el_panel(linea: str) -> str | None:
    """La nota de una línea, sin su viñeta, o None si la línea se queda en la respuesta.

    La nota fija de texto de ticket se queda: existe para que la gerente sepa que debe revisar algo, y dentro del panel
    que ella misma nombra nadie la leería.
    """
    coincidencia = _LINEA_DE_NOTA.fullmatch(linea)
    if coincidencia is None or linea.strip() == agente.NOTA_TEXTO_DE_TICKET:
        return None
    return coincidencia.group("nota")


def separar_notas(texto: str) -> tuple[str, tuple[str, ...]]:
    """El texto sin las líneas «Nota: ...» y, aparte, esas líneas, que la pantalla lleva al panel.

    Como en sin_linea_de_fuente, la primera línea se queda aunque sea una nota: es la respuesta. Un renglón sin esa marca
    no se esconde, aunque siga a una nota. Se separa solo lo que se muestra: el verificador y el registro ven el texto
    completo.
    """
    primera, *resto = texto.split("\n")
    cuerpo = [primera]
    notas = []
    for linea in resto:
        nota = _nota_para_el_panel(linea)
        if nota is None:
            cuerpo.append(linea)
        else:
            notas.append(nota)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cuerpo)).rstrip(), tuple(notas)


# ------------------------------------------------------------------ la fuente y el corte (K29)

HERRAMIENTA_DE_VENTAS = "ventas"
_DEVOLUCIONES_EN_EL_PIE = {
    "con las devoluciones descontadas": "Cifra neta, con las devoluciones descontadas.",
    "sin restar las devoluciones": "Cifra sin restar las devoluciones.",
}


def formato_de_filtro(valor: Any) -> str:
    """Un valor de filtro como lo lee la gerente: una lista separada por comas, y sí o no en lugar de True o False."""
    if isinstance(valor, bool):
        return "sí" if valor else "no"
    if isinstance(valor, (list, tuple)):
        return ", ".join(str(elemento) for elemento in valor)
    return str(valor)


def filtros_en_texto(filtros: Mapping[str, Any]) -> list[str]:
    """Los filtros del resultado, uno por línea como «clave: valor», sin los que no se usaron."""
    return [
        f"{clave}: {formato_de_filtro(valor)}"
        for clave, valor in filtros.items()
        if valor is not None and valor != []
    ]


def corte_de(resultado: Resultado) -> dt.date | None:
    """El corte del resultado; en un periodo fuera del corte, el último día con datos que dice el error."""
    if resultado.corte:
        return resultado.corte
    ultimo = resultado.detalle.get("corte_hasta")
    try:
        return dt.date.fromisoformat(ultimo) if isinstance(ultimo, str) else None
    except ValueError:
        return None


def _secciones_de(fila: Mapping[str, Any]) -> set[str]:
    """La sección de un fragmento de política y las subsecciones que trae («5» con «5.1», «5.2»...)."""
    partes = (fila.get("subsecciones") or "").split("; ")
    return {fila["seccion"], *(parte.split(maxsplit=1)[0] for parte in partes if parte)}


def _cita_a(cita: Any, fila: Mapping[str, Any]) -> bool:
    """La cita nombra el documento del fragmento (la «FAQ» sola vale para la FAQ del índice) y una de sus secciones."""
    documento = fila["documento"]
    es_el_documento = cita.documento == documento or (cita.documento == "FAQ" and documento.startswith("FAQ-"))
    return es_el_documento and cita.seccion in _secciones_de(fila)


def fragmentos_citados(texto: str, filas: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Los fragmentos de política que el texto cita por su documento y su sección, o por una subsección de ella.

    Lee las citas con el mismo extractor del verificador (K17), de modo que «citado» significa lo mismo que cuando se
    verificó el texto. Una mención del documento sin sección no cita ningún fragmento. El extractor es privado de
    agente/verificacion.py, que no tiene uno público; tests/test_app.py fija lo que esta función espera de él.
    """
    citas, _ = modulo_verificacion._citas(texto)
    return [fila for fila in filas if any(_cita_a(cita, fila) for cita in citas)]


def _texto_de_cita(fila: Mapping[str, Any]) -> str:
    return f"{fila['cita']} ({fila['titulo']})"


def _linea_de_citados(citados: Sequence[Mapping[str, Any]]) -> str:
    return f"Fuente: {'; '.join(_texto_de_cita(fila) for fila in citados)}."


def _linea_de_politicas(resultado: Resultado, texto: str) -> str:
    citados = fragmentos_citados(texto, resultado.filas)
    if citados:
        return _linea_de_citados(citados)
    consultados = "; ".join(_texto_de_cita(fila) for fila in resultado.filas)
    return f"Fuente: fragmentos consultados de las políticas: {consultados}."


def _consulta_y_corte(herramienta: str, resultado: Resultado) -> tuple[str, str]:
    """La herramienta con sus filtros, «herramienta ventas; metrica: venta; ...», y el cierre «Corte: ...» si lo hay."""
    detalle = "; ".join([f"herramienta {herramienta}", *filtros_en_texto(resultado.filtros)])
    corte = corte_de(resultado)
    return detalle, f" Corte: {fecha_larga(corte)}." if corte else ""


def _devoluciones_en_el_pie(herramienta: str, resultado: Resultado) -> str:
    """Qué hizo con las devoluciones una cifra de ventas, según la definición que trae el resultado (C1.4).

    La redacción del modelo se puede saltar la definición, y el pie lo escribe el código. La métrica de devoluciones no
    trae ninguna de esas frases: su fuente ya dice de qué filas sale.
    """
    if herramienta != HERRAMIENTA_DE_VENTAS:
        return ""
    definicion = resultado.definicion or ""
    return next((f" {pie}" for frase, pie in _DEVOLUCIONES_EN_EL_PIE.items() if frase in definicion), "")


def _linea_de_datos(herramienta: str, resultado: Resultado) -> str:
    detalle, cierre = _consulta_y_corte(herramienta, resultado)
    devoluciones = _devoluciones_en_el_pie(herramienta, resultado)
    return f"Fuente: {resultado.fuente or herramienta} ({detalle}).{devoluciones}{cierre}"


def _linea_sin_resultado(herramienta: str, resultado: Resultado) -> str:
    detalle, cierre = _consulta_y_corte(herramienta, resultado)
    motivo = f": {resultado.resumen}" if resultado.estado == "canal_inexistente" else ""
    return f"Sin resultado de la consulta ({detalle}){motivo}.{cierre}"


def _linea_de_fuente(evidencia: agente.Evidencia, texto: str) -> str:
    resultado = evidencia.resultado
    if resultado.estado != "ok":
        return _linea_sin_resultado(evidencia.herramienta, resultado)
    if evidencia.herramienta == HERRAMIENTA_DE_POLITICAS:
        return _linea_de_politicas(resultado, texto)
    return _linea_de_datos(evidencia.herramienta, resultado)


def _avisos_de_fecha_de(evidencia: agente.Evidencia) -> list[str]:
    """El aviso de cada fecha relativa de una consulta, de sus notas o de la interpretación de un periodo sin datos."""
    resultado = evidencia.resultado
    posibles = [*resultado.notas, str(resultado.detalle.get("interpretacion", ""))]
    return [f"Nota: {nota}." for nota in posibles if modulo_resolver.es_aviso_de_fecha_relativa(nota)]


def _avisos_de_fecha(respuesta: agente.Respuesta) -> list[str]:
    """El aviso de cada fecha relativa del turno, consulta por consulta."""
    return [aviso for evidencia in respuesta.evidencias for aviso in _avisos_de_fecha_de(evidencia)]


def lineas_de_fuente(respuesta: agente.Respuesta) -> tuple[str, ...]:
    """La fuente y el corte de la respuesta (K29), una línea por herramienta que corrió y sin repetir, y el aviso de fecha.

    Salen de las evidencias del turno. Un estado sin cifra (tope, modelo o fuente no disponible) no trae ninguna: su
    texto ya dice que no hay cifra. Una respuesta sin herramientas lo dice, en lugar de dejar creer que consultó algo.
    """
    if respuesta.estado != "ok":
        return ()
    if modulo_manifiesto.es_del_manifiesto(respuesta):
        return (FUENTE_DEL_MANIFIESTO,)
    if not respuesta.evidencias:
        return (SIN_CONSULTA,)
    lineas = [_linea_de_fuente(evidencia, respuesta.texto) for evidencia in respuesta.evidencias]
    return tuple(dict.fromkeys([*lineas, *_avisos_de_fecha(respuesta)]))


def fuentes_por_consulta(respuesta: agente.Respuesta) -> tuple[tuple[str, ...], ...]:
    """Las mismas líneas de lineas_de_fuente(), repartidas: la fuente, el corte y el aviso de fecha de cada consulta.

    Va una tupla por evidencia, en su orden, para que el panel las ponga debajo de la consulta de la que salen. Un estado
    sin cifra no trae ninguna, igual que en lineas_de_fuente().
    """
    if respuesta.estado != "ok":
        return tuple(() for _ in respuesta.evidencias)
    return tuple(
        tuple(dict.fromkeys([_linea_de_fuente(evidencia, respuesta.texto), *_avisos_de_fecha_de(evidencia)]))
        for evidencia in respuesta.evidencias
    )


def _fuentes_de_politicas_citadas(respuesta: agente.Respuesta) -> tuple[str, ...]:
    """El documento y la sección de cada política que la respuesta cita, sin repetir; una respuesta sin citas no trae línea."""
    if respuesta.estado != "ok":
        return ()
    consultas = [evidencia.resultado for evidencia in respuesta.evidencias if evidencia.herramienta == HERRAMIENTA_DE_POLITICAS]
    citados = [fragmentos_citados(respuesta.texto, resultado.filas) for resultado in consultas if resultado.estado == "ok"]
    return tuple(dict.fromkeys(_linea_de_citados(filas) for filas in citados if filas))


# ------------------------------------------------------------------ el panel de traza


def linea_de_filas(resultado: Resultado) -> str:
    """Cuántas filas dio la consulta y cuántas se muestran, con coma de miles; un total no tiene filas que listar."""
    total, mostradas = resultado.n_filas, len(resultado.filas)
    if total == 0 and resultado.cifras:
        return "La consulta devuelve totales, no filas."
    if total == mostradas:
        return f"Filas del resultado: {entero(total)}."
    return f"Filas del resultado: {entero(total)}; se muestran {entero(mostradas)}."


def hay_panel(respuesta: agente.Respuesta) -> bool:
    """El panel de traza va en toda respuesta contestada, no en los estados sin cifra (K19).

    También en una sin herramientas: ahí van sus notas y la línea que dice que no consultó los datos.
    """
    return respuesta.estado == "ok"


def _tal_cual(texto: str) -> str:
    """El texto como debe llegar a st.code para que se vea sin cambios.

    st.code quita un salto de línea al principio y otro al final. Agregar uno de cada lado deja el texto igual al que
    guardó el registro, también cuando empieza o termina con un salto (la consulta de buscar_politicas).
    """
    return f"\n{texto}\n"


def _mostrar_fragmentos(resultado: Resultado, texto: str) -> None:
    citados = fragmentos_citados(texto, resultado.filas)
    if len(citados) == 1:
        st.caption("Fragmento citado")
    elif citados:
        st.caption("Fragmentos citados")
    else:
        st.caption("Fragmentos consultados")
    mostrados = citados or list(resultado.filas)
    for fila in mostrados:
        st.text(_texto_de_cita(fila))
        st.code(_tal_cual(fila["texto"]), language=None, wrap_lines=True)
    otros = [fila for fila in resultado.filas if not any(fila is mostrado for mostrado in mostrados)]
    if otros:
        st.caption("Otros fragmentos consultados")
        st.text("\n".join(_texto_de_cita(fila) for fila in otros))
    documentos = resultado.detalle.get("documentos") or {}
    versiones = [f"{codigo}: {descripcion}" for codigo, descripcion in documentos.items()]
    if versiones:
        st.caption("Documentos")
        st.text("\n".join(versiones))


def _mostrar_evidencia(
    evidencia: agente.Evidencia, texto: str, numero: int, total: int, fuentes: Sequence[str] = ()
) -> None:
    """El detalle de una consulta, que abre con su fuente y su corte (K29): cada consulta lleva las suyas debajo."""
    resultado = evidencia.resultado
    if total > 1:
        st.text(f"Consulta {numero} de {total}")
    for linea in fuentes:
        st.text(linea)
    st.text(f"{evidencia.herramienta}: {resultado.resumen}")
    if resultado.estado != "ok":
        st.text(f"Estado: {resultado.estado}")
    fuente = [f"Fuente: {resultado.fuente}"] if resultado.fuente else []
    corte = corte_de(resultado)
    if corte:
        fuente.append(f"Corte: {fecha_larga(corte)}")
    if fuente:
        st.text("\n".join(fuente))
    if resultado.definicion:
        st.caption("Definición")
        st.text(resultado.definicion)
    filtros = filtros_en_texto(resultado.filtros)
    if filtros:
        st.caption("Filtros")
        st.text("\n".join(filtros))
    st.caption("Consulta (SQL)")
    if resultado.consulta:
        st.code(_tal_cual(resultado.consulta), language="sql", wrap_lines=True)
    else:
        st.text("No se ejecutó ninguna consulta SQL.")
    if resultado.cifras:
        st.caption("Resultado")
        st.text("\n".join(f"{clave}: {valor}" for clave, valor in resultado.cifras.items()))
    if evidencia.herramienta == HERRAMIENTA_DE_POLITICAS:
        _mostrar_fragmentos(resultado, texto)
    elif resultado.filas or resultado.estado == "ok":
        st.caption("Filas")
        st.text(linea_de_filas(resultado))
        if resultado.filas:
            st.dataframe(list(resultado.filas), hide_index=True)
    if resultado.notas:
        st.caption("Notas")
        st.text("\n".join(resultado.notas))


def _mostrar_uso_sin_modelo(respuesta: agente.Respuesta) -> None:
    """Lo que el panel dice de una respuesta del manifiesto: que no llamó al modelo."""
    st.text("Respuesta armada con el manifiesto de cobertura, sin llamar al modelo.")


def _mostrar_uso(respuesta: agente.Respuesta) -> None:
    """El cierre del panel: las llamadas y los tokens del turno, los mismos que guarda el registro.

    El razonamiento ya viene dentro de los tokens de salida, y por eso se dice como parte de ellos. Con cero no se dice
    nada: puede ser que el modelo no razonó o que el proveedor no lo separa.
    """
    traza = respuesta.traza
    razonamiento = sum(llamada.razonamiento_tokens for llamada in traza.llamadas)
    de_razonamiento = f", de ellos {entero(razonamiento)} de razonamiento" if razonamiento else ""
    st.text(
        f"Llamadas al modelo: {entero(len(traza.llamadas))}. Tokens de entrada: {entero(traza.entrada_tokens)}. "
        f"Tokens de salida: {entero(traza.salida_tokens)}{de_razonamiento}."
    )


def _fuentes_del_panel(respuesta: agente.Respuesta) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """La fuente que va arriba del panel y la de cada consulta, que va debajo de ella. No lanza.

    Arriba solo va la fuente de una respuesta que no consultó nada (el manifiesto o una respuesta sin herramientas), o
    el aviso de que la fuente no se pudo armar.
    """
    try:
        if respuesta.evidencias:
            return (), fuentes_por_consulta(respuesta)
        return lineas_de_fuente(respuesta), ()
    except Exception as error:  # noqa: BLE001 (K21: la fuente no puede tumbar la pantalla)
        _aviso.warning("La fuente de la respuesta no se pudo armar (%s).", type(error).__name__)
        return ("No pude armar la fuente de esta respuesta.",), tuple(() for _ in respuesta.evidencias)


def lineas_del_panel(respuesta: agente.Respuesta, notas: Sequence[str]) -> tuple[str, ...]:
    """Con lo que abre el panel: las notas, sin repetir, y la fuente que no es de ninguna consulta. No lanza.

    La fuente, el corte y el aviso de fecha de cada consulta van debajo de ella (fuentes_por_consulta), y una nota del
    modelo igual a uno de esos avisos no se repite arriba.
    """
    generales, por_consulta = _fuentes_del_panel(respuesta)
    de_las_consultas = {linea for lineas in por_consulta for linea in lineas}
    return tuple(linea for linea in dict.fromkeys([*notas, *generales]) if linea not in de_las_consultas)


def mostrar_panel(respuesta: agente.Respuesta, notas: Sequence[str] = ()) -> None:
    """El panel de traza, plegado: las notas y, por cada herramienta que corrió, su fuente y lo que hace falta para revisar su cifra.

    Cierra con el id del turno, para encontrar su línea en logs/turnos.jsonl, y con las llamadas al modelo y los tokens
    del turno. En una respuesta del manifiesto no hay herramientas: dice que no llamó al modelo. Un detalle que no se
    pueda armar no tumba la respuesta ni las siguientes: la pantalla vuelve a dibujar todos los turnos en cada
    interacción.
    """
    with st.expander("Ver cómo se obtuvo la respuesta", expanded=False):
        try:
            for linea in lineas_del_panel(respuesta, notas):
                st.text(linea)
            _, por_consulta = _fuentes_del_panel(respuesta)
            evidencias = respuesta.evidencias
            for numero, (evidencia, fuentes) in enumerate(zip(evidencias, por_consulta, strict=True), start=1):
                _mostrar_evidencia(evidencia, respuesta.texto, numero, len(evidencias), fuentes)
            st.text(f"Turno en el registro: {respuesta.turno_id}")
            if modulo_manifiesto.es_del_manifiesto(respuesta):
                _mostrar_uso_sin_modelo(respuesta)
            _mostrar_uso(respuesta)
        except Exception as error:  # noqa: BLE001 (K21: el detalle no puede tumbar la pantalla)
            _aviso.warning("El panel de traza no se pudo armar (%s).", type(error).__name__)
            st.text("No pude mostrar el detalle completo de esta respuesta.")


# ------------------------------------------------------------------ la conversación


@dataclass(frozen=True)
class TurnoMostrado:
    """Un turno como lo muestra la pantalla: lo que se preguntó y lo que se contestó.

    respuesta es None si responder() lanzó una excepción: entonces solo hay un texto fijo, sin fuente ni panel.
    En un mensaje con varias preguntas (T4.6), partes trae un TurnoMostrado por pregunta, texto solo trae el aviso de las
    que no se contestaron, si las hay, y respuesta es None.
    """

    pregunta: str
    texto: str
    respuesta: agente.Respuesta | None = None
    partes: tuple[TurnoMostrado, ...] = ()


def atender(pregunta: str, historial: modulo_historial.Historial) -> TurnoMostrado:
    """Contesta el mensaje de la gerente: una pregunta o, si trae varias, cada una con su turno (T4.6). No lanza."""
    preguntas = _preguntas_del_mensaje(pregunta)
    if len(preguntas) == 1:
        return _atender_una(pregunta, historial)
    return _atender_varias(pregunta, preguntas, historial)


def _preguntas_del_mensaje(mensaje: str) -> tuple[str, ...]:
    """Las preguntas por contestar.

    Un mensaje que pasa del tope de largo no se separa: responder() lo rechaza entero, y separado sus trozos pasarían el
    tope (K19). Uno que solo pide la cobertura tampoco: se contesta una vez, aunque la pida de dos maneras.
    """
    if len(mensaje) > agente.TOPES.caracteres or modulo_manifiesto.pide_la_cobertura(mensaje):
        return (mensaje,)
    return modulo_preguntas.separar(mensaje)


def _atender_varias(mensaje: str, preguntas: Sequence[str], historial: modulo_historial.Historial) -> TurnoMostrado:
    """Una pregunta por turno, en orden: cada una deja su vuelta en el Historial y la siguiente la ve como contexto.

    Se detiene si una pregunta falla por algo que la siguiente repetiría; un tope no, porque es de ese turno.
    """
    por_contestar = preguntas[: modulo_preguntas.MAXIMO_PREGUNTAS]
    partes: list[TurnoMostrado] = []
    for pregunta in por_contestar:
        partes.append(_atender_una(pregunta, historial))
        if _no_hay_como_seguir(partes[-1]):
            break
    aviso = ""
    if len(por_contestar) < len(preguntas):
        aviso = AVISO_PREGUNTAS_DE_MAS
    if len(partes) < len(por_contestar):
        aviso = AVISO_SIN_SEGUIR
    return TurnoMostrado(mensaje, aviso, partes=tuple(partes))


def _no_hay_como_seguir(parte: TurnoMostrado) -> bool:
    return parte.respuesta is None or parte.respuesta.estado in ("modelo_no_disponible", "fuente_no_disponible")


def _atender_una(pregunta: str, historial: modulo_historial.Historial) -> TurnoMostrado:
    """Llama a responder() con el Historial de la sesión y guarda la vuelta. No lanza.

    Una excepción de responder() (una respuesta vacía del modelo, un defecto) no llega a la pantalla: la gerente lee un
    texto fijo y el log recibe solo el tipo del error, porque su texto puede traer rutas, la pregunta o una llave.

    Un mensaje que es solo "qué sabes" o ayuda (T4.2) no llega a responder(): lo contesta el manifiesto de cobertura,
    sin modelo, y esa vuelta no se guarda en el Historial.
    """
    if modulo_manifiesto.pide_la_cobertura(pregunta):
        return _atender_con_el_manifiesto(pregunta, historial)
    try:
        respuesta = agente.responder(
            pregunta, historial, modelo=None, archivo_registro=modulo_registro.ARCHIVO, origen="interfaz"
        )
    except Exception as error:  # noqa: BLE001 (K21: ninguna excepción llega a la pantalla)
        _aviso.warning("responder() lanzó %s.", type(error).__name__)
        vacia = isinstance(error, UnexpectedModelBehavior)
        return TurnoMostrado(pregunta, modulo_reintentos.MENSAJES_DE_FALLA["modelo"] if vacia else MENSAJE_INESPERADO)
    historial.agregar(pregunta, respuesta)
    return TurnoMostrado(pregunta, respuesta.texto, respuesta)


def _atender_con_el_manifiesto(pregunta: str, historial: modulo_historial.Historial) -> TurnoMostrado:
    """La respuesta de cobertura de un turno. No lanza, y no guarda la vuelta: el historial es lo que ve el modelo."""
    try:
        respuesta = modulo_manifiesto.responder(
            pregunta, historial, archivo_registro=modulo_registro.ARCHIVO, origen="interfaz"
        )
    except Exception as error:  # noqa: BLE001 (K21: ninguna excepción llega a la pantalla)
        _aviso.warning("El manifiesto de cobertura lanzó %s.", type(error).__name__)
        return TurnoMostrado(pregunta, MENSAJE_INESPERADO)
    return TurnoMostrado(pregunta, respuesta.texto, respuesta)


def _mostrar_partes(turno: TurnoMostrado) -> None:
    """Cada pregunta del mensaje con su texto, su fuente y su panel, separadas por una línea (T4.6)."""
    for numero, parte in enumerate(turno.partes):
        if numero:
            st.divider()
        st.text(parte.pregunta)
        mostrar_respuesta(parte)
    if turno.texto:
        st.divider()
        st.text(turno.texto)


def _marcar(turno_id: str) -> None:
    """Callback del botón: agrega la marca al registro, una sola vez por turno. No lanza.

    Streamlit corre el callback una vez por pulsación, antes de volver a ejecutar el script; un rerun cualquiera no lo
    corre. Si ya quedó guardada, una segunda pulsación (dos clics antes de que la pantalla cambie) no agrega otra línea.
    """
    marcas = st.session_state.setdefault("marcas", {})
    if marcas.get(turno_id):
        return
    comentario = st.session_state.get(f"comentario-{turno_id}", "")
    marcas[turno_id] = modulo_registro.registrar_marca(modulo_registro.ARCHIVO, turno_id, comentario)


def mostrar_marca(respuesta: agente.Respuesta) -> None:
    """El cuadro "Respuesta incorrecta" de una respuesta (T4.4, K28), o el texto que dice que ya quedó marcada.

    Abrir el cuadro no escribe nada: la marca la deja el botón de adentro. st.session_state["marcas"] guarda por id de
    turno si la marca se escribió (True) o no se pudo (False). Si no se pudo, un texto lo dice y el cuadro sigue, con el
    comentario escrito, para volver a intentarlo. La clave del contenedor le da la clase con que ESTILOS_DEL_CHAT lo achica.
    """
    turno_id = respuesta.turno_id
    guardada = st.session_state.get("marcas", {}).get(turno_id)
    with st.container(key=f"reporte-{turno_id}", horizontal_alignment="right"):
        if guardada:
            st.text(TEXTO_MARCADA)
            return
        if guardada is False:
            st.text(TEXTO_SIN_GUARDAR)
        with st.popover("Respuesta incorrecta", type="tertiary"), st.form(f"marca-{turno_id}", border=False):
            st.text_area(
                ETIQUETA_DE_COMENTARIO, key=f"comentario-{turno_id}", max_chars=modulo_registro.MAXIMO_CARACTERES_COMENTARIO
            )
            st.form_submit_button(ETIQUETA_DE_MARCA, on_click=_marcar, args=(turno_id,))


def _mostrar_tabla(tabla: modulo_bloques.Tabla, clave: str) -> None:
    componente = st.components.v2.component(COMPONENTE_DE_TABLA, html=HTML_DE_TABLA, css=CSS_DE_TABLA, js=JS_DE_TABLA)
    componente(
        data={
            "encabezados": list(tabla.encabezados),
            "filas": [list(fila) for fila in tabla.filas],
            "columnas_de_cifras": list(tabla.columnas_de_cifras),
        },
        key=clave,
    )


def _mostrar_lista(lista: modulo_bloques.Lista, clave: str) -> None:
    """Un st.text por elemento: la clave le da al contenedor la clase con que ESTILOS_DEL_CHAT sangra los renglones."""
    with st.container(key=clave, gap=None):
        for elemento in lista.elementos:
            st.text(elemento)


def _mostrar_texto(texto: str, respuesta: agente.Respuesta) -> None:
    """El texto de la respuesta por bloques. Del manifiesto solo se parten sus tablas: sus viñetas van como las escribe.

    Cada clave lleva el id del turno: así dos respuestas iguales no chocan en la página.
    """
    listas = not modulo_manifiesto.es_del_manifiesto(respuesta)
    for numero, bloque in enumerate(modulo_bloques.bloques_de(texto, listas=listas)):
        if isinstance(bloque, modulo_bloques.Tabla):
            _mostrar_tabla(bloque, f"tabla-{respuesta.turno_id}-{numero}")
        elif isinstance(bloque, modulo_bloques.Lista):
            _mostrar_lista(bloque, f"lista-{respuesta.turno_id}-{numero}")
        else:
            st.text(bloque.texto)


def _notas_aparte(texto: str, respuesta: agente.Respuesta) -> tuple[str, tuple[str, ...]]:
    """El texto y sus notas para el panel. Un estado sin cifra no tiene panel, y el manifiesto lo arma el código: van enteros."""
    if not hay_panel(respuesta) or modulo_manifiesto.es_del_manifiesto(respuesta):
        return texto, ()
    return separar_notas(texto)


def _mostrar_fuente_de_politicas(respuesta: agente.Respuesta) -> None:
    """La línea gris con el documento y la sección de la política citada. No lanza: si no se arma, el panel lo dice.

    La clave del contenedor le da la clase con que ESTILOS_DEL_CHAT la pone en gris. Va en st.text (K23).
    """
    try:
        lineas = _fuentes_de_politicas_citadas(respuesta)
    except Exception as error:  # noqa: BLE001 (K21: la fuente no puede tumbar la pantalla)
        _aviso.warning("La fuente de la política no se pudo armar (%s).", type(error).__name__)
        return
    if not lineas:
        return
    with st.container(key=f"fuente-{respuesta.turno_id}", gap=None):
        for linea in lineas:
            st.text(linea)


def mostrar_respuesta(turno: TurnoMostrado) -> None:
    """La respuesta: su texto primero, sin nada antes de la cifra; la fuente si cita una política; la marca y el panel."""
    if turno.partes:
        _mostrar_partes(turno)
        return
    texto = sin_linea_de_fuente(sin_negritas(turno.texto))
    respuesta = turno.respuesta
    if respuesta is None:
        st.text(texto)
        return
    cuerpo, notas = _notas_aparte(texto, respuesta)
    _mostrar_texto(cuerpo, respuesta)
    _mostrar_fuente_de_politicas(respuesta)
    mostrar_marca(respuesta)
    if hay_panel(respuesta):
        mostrar_panel(respuesta, notas)


def mostrar_turno(turno: TurnoMostrado) -> None:
    with st.chat_message("user"):
        st.text(turno.pregunta)
    with st.chat_message("assistant"):
        mostrar_respuesta(turno)


def mostrar_inicio() -> None:
    """La pantalla antes de la primera pregunta: una frase con los temas que conoce; el detalle lo da "qué sabes".

    El manifiesto se sigue leyendo aunque no se dibuje: esa lectura deja lista la base antes de la primera pregunta. Se
    lee una vez por sesión del navegador y no en cada chat vacío, porque Streamlit vuelve a correr este archivo en cada
    clic y responder() revisa la base en cada pregunta. Si no se puede leer, la pantalla lo dice con un texto fijo, la
    caja de pregunta sigue disponible y la siguiente pantalla lo intenta otra vez.
    """
    st.caption(
        "Pregunta por las ventas, el inventario, los tickets de la mesa de servicio o las políticas de tus tiendas. "
        "Escribe abajo y presiona Enter. Si no lo sé, te lo digo."
    )
    if st.session_state.get(CLAVE_DATOS_REVISADOS):
        return
    try:
        with st.spinner("Revisando qué datos hay disponibles..."):
            modulo_manifiesto.leer()
    except modulo_construir.ErrorConstruccion as error:
        st.text(modulo_manifiesto.mensaje_sin_fuente(error))
        return
    except Exception as error:  # noqa: BLE001 (K21: la cobertura no puede tumbar la pantalla)
        _aviso.warning("La cobertura de los datos no se pudo leer (%s).", type(error).__name__)
        st.text(SIN_COBERTURA)
        return
    st.session_state[CLAVE_DATOS_REVISADOS] = True


# ------------------------------------------------------------------ los chats de la barra lateral


@dataclass
class Chat:
    """Una conversación de la sesión del navegador. Sin título todavía no tiene preguntas y no se lista."""

    historial: modulo_historial.Historial = field(default_factory=modulo_historial.Historial)
    turnos: list[TurnoMostrado] = field(default_factory=list)
    titulo: str = ""


def titulo_de_chat(pregunta: str) -> str:
    """La pregunta en una línea y recortada, para que quepa en la barra lateral."""
    titulo = " ".join(pregunta.split())
    if len(titulo) <= LARGO_TITULO_CHAT:
        return titulo
    return f"{titulo[: LARGO_TITULO_CHAT - 3].rstrip()}..."


def _activar_chat(chat: Chat) -> None:
    st.session_state["historial"] = chat.historial
    st.session_state["turnos"] = chat.turnos


def _chat_actual() -> Chat:
    return next(chat for chat in st.session_state["chats"] if chat.historial is st.session_state["historial"])


def _nuevo_chat() -> None:
    """Callback de "Nuevo chat". Si el chat abierto sigue vacío ya es uno nuevo: no se crea otro."""
    if not _chat_actual().titulo:
        return
    chat = Chat()
    st.session_state["chats"].append(chat)
    _activar_chat(chat)


def _abrir_chat(id_sesion: str) -> None:
    """Callback de "Abrir": cambia de chat y descarta el vacío que estuviera abierto."""
    chats = [chat for chat in st.session_state["chats"] if chat.titulo]
    st.session_state["chats"] = chats
    _activar_chat(next(chat for chat in chats if chat.historial.id_sesion == id_sesion))


def _mostrar_fila_de_chat(chat: Chat, actual: Chat) -> None:
    if chat is actual:
        st.text(f"{MARCA_CHAT_ACTUAL}{chat.titulo}")
        return
    titulo, boton = st.columns([3, 1], vertical_alignment="center")
    titulo.text(chat.titulo)
    with boton, st.form(f"abrir-{chat.historial.id_sesion}", border=False):
        st.form_submit_button("Abrir", on_click=_abrir_chat, args=(chat.historial.id_sesion,))


def mostrar_barra_lateral() -> None:
    """Los chats de esta sesión, el más reciente primero. El abierto va marcado y no trae botón."""
    with st.sidebar:
        with st.form("nuevo-chat", border=False):
            st.form_submit_button("Nuevo chat", on_click=_nuevo_chat, width="stretch")
        st.subheader("Chats")
        con_preguntas = [chat for chat in reversed(st.session_state["chats"]) if chat.titulo]
        if not con_preguntas:
            st.text(SIN_CHATS)
        actual = _chat_actual()
        for chat in con_preguntas:
            _mostrar_fila_de_chat(chat, actual)


def _sesion() -> tuple[modulo_historial.Historial, list[TurnoMostrado]]:
    """El Historial y los turnos del chat abierto en esta sesión del navegador, creados en la primera interacción.

    También se crean de nuevo si cambió un archivo con la sesión abierta (un cambio en vivo, C9.3): Streamlit recarga los
    módulos, lo guardado queda de las clases de antes y responder() ya no reconocería ese Historial.
    """
    if not isinstance(st.session_state.get("historial"), modulo_historial.Historial):
        chat = Chat()
        st.session_state["chats"] = [chat]
        _activar_chat(chat)
    return st.session_state["historial"], st.session_state["turnos"]


def main() -> None:
    # "viewer" quita de la barra superior las opciones de desarrollo, entre ellas Deploy.
    st.set_option("client.toolbarMode", "viewer")
    st.set_page_config(
        page_title="HEBVA",
        menu_items={
            "Get help": None,
            "Report a bug": None,
            "About": "HEBVA (H-e-b virtual agent): asistente para consultar las ventas, el inventario, los tickets y las políticas de las tiendas.",
        },
    )
    st.components.v2.component("estilos_del_chat", css=ESTILOS_DEL_CHAT, js=JS_DE_LA_PANTALLA, isolate_styles=False)()
    st.components.v2.component("foco_de_la_marca", js=JS_DE_LA_MARCA, isolate_styles=False)()
    st.title("HEBVA")
    historial, turnos = _sesion()
    escrito = st.chat_input("Escribe tu pregunta", max_chars=agente.TOPES.caracteres, submit_mode="disable")
    pregunta = (escrito or "").strip()
    # El título se pone antes de dibujar la barra: así el chat ya aparece mientras corre su primer turno.
    if pregunta and not _chat_actual().titulo:
        _chat_actual().titulo = titulo_de_chat(pregunta)
    mostrar_barra_lateral()
    if not turnos and not pregunta:
        mostrar_inicio()
    for turno in turnos:
        mostrar_turno(turno)
    if not pregunta:
        return
    with st.chat_message("user"):
        st.text(pregunta)
    with st.chat_message("assistant"):
        with st.spinner("Consultando los datos...", show_time=True):
            # La vuelta se guarda antes de salir del spinner: salir de él es una llamada a Streamlit, que detiene el
            # script si la gerente pidió pararlo o repetirlo, y el turno ya habría corrido sin quedar en la sesión.
            turno = atender(pregunta, historial)
            turnos.append(turno)
        mostrar_respuesta(turno)


if __name__ == "__main__":
    main()
