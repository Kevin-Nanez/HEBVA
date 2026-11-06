# Revisión de accesibilidad de la interfaz: 5 criterios de WCAG 2.2

Fecha de la revisión: 2026-11-04. Commit revisado: 0a28724; el botón «Respuesta incorrecta» llegó después, en 79f333e, y se probó aparte con el teclado. Streamlit 1.66.1. Chrome con el tema oscuro del sistema.

## Resultado

De los 5 criterios, 2 cumplen (1.4.1 y 2.1.1), 1 cumple en las cifras y falla en otro punto (1.3.1) y 2 son brecha (3.1.1 y 4.1.3). Lighthouse 13.4.1 da 100 en accesibilidad en las 3 corridas y no detecta ninguna de las brechas: html-has-lang aprueba porque el atributo existe y es válido, aunque diga inglés en una página en español, y no hay regla que mire si una respuesta se anuncia. Las brechas salieron de recorrer la pantalla con el teclado y de leer el árbol de accesibilidad y los roles en el DOM.

| Criterio | Veredicto | Evidencia |
|---|---|---|
| 1.3.1 Información y relaciones: cifras tabuladas con encabezados | Cumple en las cifras. Brecha en el autor de cada mensaje | La tabla de filas del panel expone 1 grid con 5 columnheader y 5 filas de gridcell. Los mensajes del chat no dicen quién los escribió |
| 1.4.1 Uso del color: estados que no dependen solo del color | Cumple | app.py no llama a los avisos de colores de Streamlit; cada estado sale como texto |
| 2.1.1 Teclado: envío y marca | Cumple | Tab llega al cuadro de pregunta en 6 pasos; Enter envía; Tab al botón de enviar y Enter también envía. El botón «Respuesta incorrecta» va antes del panel: desde el cuadro, 2 veces Mayús+Tab llegan a él, y Enter deja una línea `marca` y cambia el botón por el texto de que quedó marcada (2026-11-04) |
| 3.1.1 Idioma de la página | Brecha | El idioma declarado es inglés en una interfaz en español; Streamlit no ofrece cómo cambiarlo |
| 4.1.3 Mensajes de estado | Brecha | El aviso de espera se anuncia (rol status); la respuesta y los textos de estado fijos no |

## 1.3.1 Información y relaciones

**Cifras tabuladas: cumple.** El panel «Ver cómo se obtuvo la respuesta» muestra las filas con la función de tabla de Streamlit, que dibuja en un lienzo y trae una tabla de respaldo. El árbol de accesibilidad la expone como grid con aria-rowcount 6 y aria-colcount 5, 5 columnheader (sku, producto, unidad, venta, unidades) y celdas ligadas a su fila. Las demás cifras del panel («Resultado», «Filtros») son líneas «clave: valor» de texto simple, no una tabla, y la relación va en el mismo texto. Lighthouse aprueba aria-required-attr y aria-roles sobre ese grid (23 reglas aplicables, 23 aprobadas).

**Autor del mensaje: brecha.** El avatar de cada mensaje lleva aria-hidden y el contenedor del mensaje no tiene rol ni nombre. El árbol muestra la pregunta y la respuesta como textos seguidos, sin decir cuál es de la persona y cuál del asistente. La función de Streamlit para los mensajes del chat solo acepta nombre, avatar y ancho: no hay parámetro para un nombre accesible.

## 1.4.1 Uso del color

**Cumple.** Una búsqueda en app.py no encuentra llamadas a los avisos de colores de Streamlit (error, advertencia, éxito, información, estado, métrica, progreso ni insignia). Los estados salen como palabras: la línea «Estado: ...» del panel, los textos fijos de tope, modelo no disponible, fuente no disponible y error inesperado, «Sin resultado de la consulta» y el aviso «Consultando los datos...» mientras corre el turno. El panel plegado cambia de flecha y el árbol lo marca como plegable y expandido, no solo de color. color-contrast aprueba en las 3 corridas y link-in-text-block no aplica porque ningún texto lleva enlaces.

El botón «Respuesta incorrecta» también dice su estado con texto: al marcar, se cambia por «Marcaste esta respuesta como incorrecta. Quedó anotada para revisarla.», y si la marca no se pudo guardar, otro texto lo dice y el botón sigue.

## 2.1.1 Teclado

**Envío: cumple.** Orden de Tab en la pantalla inicial: el enlace «Skip to main content», Deploy, Main menu, el área de desplazamiento de la página, el enlace del encabezado y el cuadro «Escribe tu pregunta» (6 pasos). El botón de enviar está deshabilitado con el cuadro vacío y pasa a ser el paso 7 con texto. Se probó con el teclado:

- Enter dentro del cuadro envía la pregunta (3 turnos de prueba).
- Tab hasta el botón de enviar y Enter envía (1 turno).
- Al terminar el turno el cuadro se vuelve a habilitar y recupera el foco.
- Shift+Tab desde el cuadro llega al título del panel; Enter lo abre y lo cierra.
- Con el panel abierto, Tab alcanza el bloque SQL, el botón de copiar, los 4 botones de la barra de la tabla y la tabla (Ctrl+End lleva a la última celda).
- El foco se ve en el cuadro (borde rojo), en el título del panel y en el botón de enviar (anillo de 3.2 px).

**Marca: cumple.** El botón «Respuesta incorrecta» existe desde 79f333e y va antes del panel. Desde el cuadro, 2 veces Mayús+Tab llegan a él, y Enter deja una sola línea `marca` y cambia el botón por el texto de que quedó marcada. Se probó en el navegador el 2026-11-04. Su texto de ayuda no se vio en pantalla.

## 3.1.1 Idioma de la página

**Brecha.** document.documentElement.lang vale "en". Streamlit 1.66.1 sirve su static/index.html con lang="en", y ni st.set_page_config ni streamlit config show traen una opción de idioma. La interfaz está en español. html-has-lang y html-lang-valid aprueban en las 3 corridas porque solo comprueban que el código exista y sea válido.

La única vía desde la aplicación es ejecutar JavaScript con la función html de Streamlit y unsafe_allow_javascript. En una copia, st.html("<script>document.documentElement.lang = 'es';</script>", unsafe_allow_javascript=True) dejó el idioma en "es" y un nodo de 0 px de alto. La prueba test_solo_el_texto_fijo_va_a_las_funciones_de_streamlit_que_interpretan_markdown_o_html de tests/test_app.py falló con «st.html abre contenido que no es texto (K23)», porque prohíbe esa función entera. No se propone el cambio: se debilitaría un control de seguridad.

## 4.1.3 Mensajes de estado

**Brecha.** Lo que sí se anuncia: el aviso «Consultando los datos...» (spinner de Streamlit) va dentro de un elemento con rol status, que los lectores de pantalla leen sin mover el foco.

Lo que no se anuncia, medido en el DOM tras un turno con respuesta y otro con error (0 elementos con aria-live, role status, role alert ni role log):

- La respuesta. Los mensajes del chat no son una región viva y Streamlit no ofrece cómo marcarlos. Con la caja deshabilitada durante el turno el foco pasa a la página y vuelve al cuadro al terminar, sin que nada diga que llegó una respuesta.
- Los textos fijos de estado. Con el modelo sin acceso, la pantalla muestra «No pude consultar el modelo: no tengo acceso con la configuración actual. No te doy ninguna cifra. Avisa a quien administra el sistema.» como texto simple. Lo mismo vale para tope, fuente no disponible y error inesperado.

Esto último sí se puede cambiar en la aplicación. Con una sonda corta, los avisos de error y advertencia de Streamlit salen con rol alert (aria-live assertive) y los de información y éxito con rol status (polite). Esos textos son fijos y podrían ir ahí. No es un cambio de una línea: mostrar_respuesta pasa el texto en una variable, y es_fijo en tests/test_app.py solo acepta un literal escrito dentro de la llamada. Habría que repetir cada texto fijo como literal en app.py o cambiar esa regla, que es una prueba existente: la decisión es de quien mantiene el proyecto. No se aplicó.

## Observaciones fuera de los 5 criterios

- Con el panel abierto, Tab recorre la tabla celda por celda y, desde la última celda (o después de Escape), vuelve al bloque SQL en lugar de bajar al cuadro de pregunta. Con el panel cerrado, Tab llega al cuadro. El botón «Respuesta incorrecta» va antes del panel, para que llegar a él no dependa de salir de la tabla.
- Los controles de Streamlit salen en inglés (Deploy, Main menu, Send message, Copy to clipboard, Show/hide columns, Download as CSV, Search, Fullscreen y «seconds» en el cronómetro del spinner). No hay ajuste de idioma de la interfaz en 1.66.1.

## Cómo se midió y qué no cubre

El 2026-11-04, después de esta revisión, la pantalla inicial se redujo a una frase. Las mediciones de esa pantalla son anteriores al cambio y no se repitieron.

- Lighthouse 13.4.1 desde chrome-devtools: pantalla inicial en escritorio (navegación) y en móvil (navegación), y una pantalla con 4 turnos y 2 paneles abiertos (instantánea, escritorio).

  | Corrida | Accesibilidad | Reglas aplicables | Aprobadas | No aplican |
  |---|---|---|---|---|
  | Inicial, escritorio | 100 | 20 | 20 | 46 |
  | Inicial, móvil | 100 | 20 | 20 | 46 |
  | Con respuestas y paneles abiertos | 100 | 23 | 23 | 43 |

  Las únicas reglas que fallan en las 3 corridas son de otras categorías: meta-description y robots-txt (SEO) y llms.txt (navegación de agentes).
- Teclado, árbol de accesibilidad y roles: las mismas páginas, con Tab, Shift+Tab, Enter, Escape y Ctrl+End, lecturas del árbol y consultas al DOM. Lighthouse deja 10 comprobaciones manuales sin evaluar; de ellas se cubrieron a mano el orden de Tab, las trampas de foco y el foco gestionado, y las otras 7 quedaron sin revisar.
- Las respuestas con panel salen de un arnés con un modelo simulado (FunctionModel, sin red ni llaves). La interfaz, las herramientas, el verificador y la base son las del clon; el texto de la respuesta lo escribe el arnés. Las cifras del panel son las de las herramientas.
- No se probó con un lector de pantalla (NVDA, VoiceOver). «No se anuncia» quiere decir que la página no tiene la región viva ni el rol que haría que se anunciara.
- Solo Chrome, solo el tema oscuro y solo Windows.

## Pendiente

- Decidir si los textos fijos de estado pasan a los avisos de error y advertencia de Streamlit (4.1.3): exige repetirlos como literales en app.py o cambiar es_fijo en tests/test_app.py.
