# Documento de decisiones

Nombre: Kevin Nañez
Fecha: 2026-11-03
Repositorio: zip con `git archive`.
Proveedor y modelos usados: Anthropic, `anthropic:claude-haiku-5-5` en esfuerzo medium (ruta por omisión de la app, set completo con reglas y con juez, y el juez mismo); Google, `google:gemini-3.5-flash-lite` (ruta alterna, núcleo y set completo solo con reglas: el juez no está validado para Gemini)


## 1. Qué construí y por qué en ese orden

Construí HEBVA, un chat en Streamlit con 5 herramientas de solo lectura (ventas, inventario, caducidades, tickets y buscar_politicas) que consultan una base DuckDB armada con los CSV y los PDF. El modelo redacta pero no calcula: cada cifra sale de una herramienta, y un verificador rechaza la que no. El orden lo decidió una regla: no escribir el agente hasta saber qué tenía que contestar y cómo iba a comprobar que lo hacía bien.

Por eso empecé por los datos. Al revisar las tablas y los documentos con un agente aparecieron huecos que cambiaban respuestas: no hay registros de merma, nada decía qué fecha es «hoy» y no estaba claro si Mi Tienda era la tienda de conveniencia ampliada del manual. Mandé esas preguntas por correo (anexo) antes de diseñar, porque cada respuesta se volvía una regla del agente: decir que con estos datos no mide la merma real y tomar el 2026-09-01 como hoy.

Después armé el banco de 100 preguntas, 60 de ellas trampa, con las respuestas esperadas calculadas directo de los CSV. Lo hice antes que el agente para que la evaluación no se acomodara a lo que el agente ya contestaba ni lo calificara con sus propios números.

La arquitectura vino después de investigar estándares y los riesgos conocidos de los agentes, porque de esos riesgos salieron las decisiones de diseño: herramientas tipadas en lugar de SQL libre, la base abierta en solo lectura, la interfaz escuchando solo en el equipo donde corre y el nombre y el celular del único ticket con datos personales enmascarados antes de llegar a cualquier prompt.

Con eso escribí un plan por fases y lo ejecuté en ciclos de medir y corregir. Cada cambio pasaba por la suite de pruebas, unas 4,500 sin red y sin llaves, y cuando tocaba las respuestas, por el set completo con el modelo real. El formato de las respuestas y la interfaz quedaron al final: mientras las cifras no salieran bien, pulir cómo se mostraban no cambiaba lo que la gerente iba a recibir.


## 2. Qué decidí no hacer

Permitir al agente redactar SQL libre (las cifras salen de 5 herramientas tipadas), base vectorial (62 fragmentos) y LiteLLM (versiones con malware en PyPI).

Responder con solo 1 llamada por cifra simple y la implementación de OpenTelemetry (basta `logs/turnos.jsonl`).



## 3. Cómo sé que funciona

En la última corrida del set de 100 prompts (60 trampa y 40 normales), con Haiku 5.5 en esfuerzo medium, que es la configuración de la app, 98 de 100 respuestas pasaron las reglas y 96 de 100 pasaron además el juez. La corrida es del 2026-11-05.

Las reglas son código: comparan cada cifra con la calculada de los CSV, con tolerancia de ±0.01, y revisan la cita, la abstención cuando no hay dato y que no se filtren datos personales. Ahí fallaron 2 respuestas: a una le faltaron 2 de las cifras esperadas y otra no citó el documento que correspondía. El juez es otra llamada a Haiku que califica los criterios en prosa, y reprobó 2 respuestas más que las reglas aprobaban. Ninguna cifra inventada llegó a la pantalla: en los borradores del modelo, el verificador encontró 2 cifras y 4 citas sin respaldo de una herramienta, y esas 5 respuestas salieron corregidas.

En esfuerzo low el mismo set dio 98 con reglas y 93 con juez. Gemini 3.5 Flash-Lite, la ruta alterna, dio 64 y 75 de 100 en 2 corridas de una versión anterior, solo con reglas.

Además, la suite de pytest corre sin red y sin llaves en cada commit, 24 casos adversariales con Haiku no dejaron pasar ninguna cifra falsa de un ticket hostil y probé a mano la interfaz.


## 4. Costo por interacción y cómo lo medí

Con Haiku 5.5 en esfuerzo medium, una pregunta costó $0.00093 en promedio: $0.119934 entre los 129 turnos del set, medidos el 2026-11-05.
La mitad costó $0.00061 o menos, 9 de cada 10 costaron $0.00171 o menos y la más cara $0.00711. En tiempo, la mediana fue de 4.2 s y 9 de cada 10 tardaron 8.7 s o menos.
Con caché caliente, cada pregunta lee el prefijo de 5,274 tokens a $0.01 por millón. Con caché fría (dura 5 minutos) lo escribe a $0.125 por millón: $0.00061 más, $0.00154 en promedio.
En esfuerzo low el mismo set costó $0.00080 por pregunta con 3.6 s de mediana: medium cuesta 16 % más y razona 441 tokens por pregunta contra 242.
Elegí medium por una pregunta que low contestaba mal: rebajar 60 % un pastel a 1 día de caducar. En 3 pasadas, medium leyó el lote y la política las 3 veces; low no leyó el lote en una y en otra dijo que la política no contempla el 60 %. En el set, con juez, medium acierta 96 de 100 y low 93.


## 5. Qué haría con dos semanas más

Primero cerraría las fallas que ya están medidas, porque la gerente las puede ver hoy: las 4 respuestas que fallaron en el set y la inconsistencia del juez, que en una misma pregunta aprueba y reprueba respuestas casi iguales. El juez tampoco corre en la app, porque no alcanzó lo que pedí para encenderlo: en una medición detectó de 38 a 44 % de las respuestas malas, con 11 a 13 % de falsas alarmas.

Después ampliaría las pruebas adversariales. Hoy son 24 casos con Haiku y ninguno con Gemini, y un ticket hostil todavía puede hacer que el modelo repita una afirmación falsa sin cifras; contra eso solo está el modelo. Para correrlas sin tocar el equipo donde vive la app armaría un ambiente sandbox, con llaves de prueba y una copia de los datos, porque cada corrida gasta llave y escribe registros.

Con el agente estable, escribiría un marco de gobierno para el agente. Hoy el prompt, el modelo, los topes y los precios cambian con un commit que solo revisamos yo y la suite. El marco diría quién aprueba un cambio de modelo o de prompt, qué set tiene que pasar antes de publicarlo y cada cuánto se revisan los registros de `logs/turnos.jsonl`.

El presupuesto y el ROI irían al final porque los dos dependen de datos que todavía no existen. El costo por pregunta ya está medido, pero el gasto del mes depende de cuántas preguntas hace la gerente, y eso solo se sabe con uso real. El ROI necesita además cuánto tarda hoy en conseguir esas cifras sin el agente, para compararlo con los 4.2 s de mediana de una respuesta.



### Anexo Correo y su respuesta

- ¿Existe un archivo con los registros de merma? «No»: viven en SIGMA, fuera del ejercicio. El agente cita la tolerancia de POL-OPS-014 §4 y dice que con estos datos no mide la merma real.
- ¿Mi Tienda es la tienda de conveniencia ampliada del manual? sí
- Voy a tomar el 2026-09-01 como "hoy" para la prueba: es la fecha de corte del inventario, y las ventas y los tickets llegan al 31 de agosto. ¿Está bien? Sí, toma el 2026-09-01 como hoy