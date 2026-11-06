# Ejercicio práctico — Analista de Inteligencia Artificial, H-E-B México

Gracias por llegar a esta etapa. Este ejercicio es la última parte del proceso y está diseñado para conocer cómo construyes, no para ponerte a prueba con trucos. Tómalo como una semana de trabajo real con un cliente interno.

## El problema

Una gerente de tienda de H-E-B administra cuatro sucursales de la región. Cada semana pierde horas cruzando reportes de ventas, revisando inventario, buscando en PDFs de políticas y leyendo tickets de la mesa de servicio. Nos pidió lo siguiente:

> "Quiero poder preguntarle en español, como le preguntaría a mi asistente, qué está pasando en mis tiendas y qué dicen las políticas. Y que cuando no sepa, me lo diga, no que me invente cifras."

Construye una primera versión que puedas demostrarle a ella.

## Lo que te damos

- `datos/`: ventas de 3 meses (junio a agosto de 2026), inventario al 1 de septiembre, catálogo, tiendas y ~300 tickets de mesa de servicio. Todo sintético. El detalle de cada columna está en `datos/diccionario_datos.md`.
- `politicas/`: 4 documentos internos en PDF (también sintéticos).
- `plantilla_decisiones.md`: la llenas y la entregas.

## Sobre el modelo que uses

Tú eliges proveedor y modelo, y no necesitas gastar dinero: hay planes gratuitos y modelos locales que alcanzan de sobra para este ejercicio. No evaluamos cuál elegiste, sino cómo lo usas y si sabes lo que cuesta. Solo repórtalo en el documento de decisiones. Si el costo te llega a bloquear, escríbeme antes de poner dinero de tu bolsillo.

## Lo que esperamos

1. Una interfaz que una persona no técnica pueda usar (chat en Streamlit, Gradio, Chainlit, Teams, WhatsApp, lo que decidas). No evaluaremos diseño visual.
2. Que las respuestas sobre cifras salgan de los datos, no del modelo. Que las respuestas sobre políticas indiquen de qué documento y sección vienen.
3. Que el sistema reconozca lo que no puede responder y lo diga.
4. Alguna forma de saber qué tan bien responde: un set de preguntas de prueba, métricas, lo que te haga sentido.
5. Que reportes cuánto cuesta en tokens una interacción típica, con el modelo que hayas elegido.

Hay libertad total en arquitectura, frameworks y modelo. Puedes usar Claude Code, Copilot, Cursor o cualquier asistente de código; es parte del trabajo. Lo que sí necesitamos es que entiendas a fondo lo que entregas.

## Entregables

Fecha límite: **viernes 25 de septiembre de 2026, 23:59 hora del centro de México**.

1. Repositorio (liga a GitHub, o un zip si prefieres) con un README que permita correrlo en menos de 10 minutos. Todo se entrega respondiendo al correo de invitación a gayala@hebmex.com; si el zip pesa más de 20 MB, comparte una liga de descarga.
2. `plantilla_decisiones.md` llena: qué priorizaste, qué descartaste, qué harías con dos semanas más, costo por interacción.
3. Video de 3 a 5 minutos mostrando el sistema funcionando. Sin edición ni presentación formal.

## Dudas

Escríbeme a gayala@hebmex.com. Las preguntas sobre el alcance son bienvenidas y también son parte del ejercicio.

---

*Todos los datos, nombres de tiendas, productos, proveedores, personas y documentos de este paquete son ficticios y se generaron exclusivamente para este ejercicio. No representan información real de H-E-B.*
