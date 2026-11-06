# Evaluación del set: completo, 2026-11-05

- Commit evaluado: bfe7da11c3921a625002f7377efc57dc2b104616 (con cambios sin commit)
- Cambios sin commit en lo que se evalúa: 8a4a3fcf4a5ef592ecc1e1e32cdcd3d4dfe8ec545965b2449fe1bdb1435f2e4c
- Modelo: anthropic:claude-haiku-5-5
- Configuración: interfaz, {"esfuerzo_claude": "medium", "esfuerzo_gemini": "medium", "cache_claude": true}; viajó: [{"anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": "medium", "anthropic_cache_instructions": true, "anthropic_cache_tool_definitions": true}]
- Prompt: 8a3256eab86904d7e650e6ca23ed0b73bc4d8670722a88621806ee829fddf089
- Herramientas: cb83992b000e9deaa8f558b8218af846d9cd5c8a0428821c7b67346d59f7f287
- Fuentes: dc38f8adb443581b54f5aa50a028218f933c69f21cd4a0f57e17dd824660798e, e6d96e39dc4f1bdfe097d1e76059f745ca473065a81c143573ff7b372b198cba
- Prompts: 100. Corridas enteras: 1 de 1.
- Tolerancia de cifras: ±0.01
- Con juez: validado para anthropic:claude-haiku-5-5. Una respuesta que pasa las reglas y reprueba el juez cuenta como falla.

## Acierto

| Alcance | Por corrida | Rango | Acierto en todas | Reglas solas, por corrida | Reglas solas, rango |
|---|---|---|---|---|---|
| Alcance de esta corrida | 96 de 100 (96.0 %) | 96.0 % (una corrida entera, sin rango) | 96 de 100 | 98 de 100 (98.0 %) | 98.0 % (una corrida entera, sin rango) |
| Núcleo | 27 de 30 (90.0 %) | 90.0 % (una corrida entera, sin rango) | 27 de 30 | 28 de 30 (93.3 %) | 93.3 % (una corrida entera, sin rango) |

## Juez

Casos que cambió el juez: 2 de aprobado a reprobado y 0 de reprobado a aprobado. Solo se le pregunta por lo que las reglas aprueban.
- Corrida 1: N-13, CV-04

Se aplica por parte: una llamada por pregunta del banco en el turno que la contestó, y una más por compuesto cuyo criterio reprueba algo por su cuenta. Juzgadas: 141: 139 aprueban y 2 reprueban.
De esas, 13 juzgan el criterio propio de un compuesto y 0 reprueban. Las etiquetas de validación son de partes y turnos: estas no tienen acuerdo medido.
Sin veredicto: 0 (0 por una falla del modelo). No reprueban la respuesta.

Uso del juez, aparte del de los turnos: 141 llamadas, 262,817 tokens de entrada, 47,220 de salida, 4.94 minutos y 0.049892 USD (0 veredictos sin precio).

Acuerdo validado contra etiquetas propias (validacion-anthropic-claude-haiku-5-5.json, 2026-11-03):

- Corrida 1: 19 de 20, intervalo del 95 % de 76.4 % a 99.1 %
- Corrida 2: 19 de 20, intervalo del 95 % de 76.4 % a 99.1 %

## Por bloque

| Bloque | Por corrida | Rango | Reglas solas, por corrida | Reglas solas, rango |
|---|---|---|---|---|
| conversacion | 4 de 5 (80.0 %) | 80.0 % (una corrida entera, sin rango) | 5 de 5 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| dos_normales | 10 de 10 (100.0 %) | 100.0 % (una corrida entera, sin rango) | 10 de 10 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| dos_trampas | 5 de 5 (100.0 %) | 100.0 % (una corrida entera, sin rango) | 5 de 5 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| normal_sencilla | 24 de 25 (96.0 %) | 96.0 % (una corrida entera, sin rango) | 25 de 25 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| trampa_sencilla | 48 de 50 (96.0 %) | 96.0 % (una corrida entera, sin rango) | 48 de 50 (96.0 %) | 96.0 % (una corrida entera, sin rango) |
| trampa_y_normal | 5 de 5 (100.0 %) | 100.0 % (una corrida entera, sin rango) | 5 de 5 (100.0 %) | 100.0 % (una corrida entera, sin rango) |

## Por categoría

| Categoría | Por corrida | Rango |
|---|---|---|
| abstencion | 18 de 19 (94.7 %) | 94.7 % (una corrida entera, sin rango) |
| adversarial | 7 de 7 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| ambiguedad | 7 de 7 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| cifra_con_definicion | 16 de 16 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| cifra_directa | 17 de 17 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| contradiccion | 10 de 10 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| cruce_de_fuentes | 13 de 14 (92.9 %) | 92.9 % (una corrida entera, sin rango) |
| politica_con_tabla | 12 de 13 (92.3 %) | 92.3 % (una corrida entera, sin rango) |
| tickets | 12 de 12 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| varios_turnos | 22 de 23 (95.7 %) | 95.7 % (una corrida entera, sin rango) |

## Por tipo de respuesta

| Tipo de respuesta | Por corrida | Rango |
|---|---|---|
| abstencion | 19 de 20 (95.0 %) | 95.0 % (una corrida entera, sin rango) |
| aclaracion | 7 de 8 (87.5 %) | 87.5 % (una corrida entera, sin rango) |
| cifra | 75 de 75 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| cita | 30 de 32 (93.8 %) | 93.8 % (una corrida entera, sin rango) |
| rechazo | 3 de 3 (100.0 %) | 100.0 % (una corrida entera, sin rango) |

## Por etapa

| Etapa | Por corrida | Rango |
|---|---|---|
| Estado esperado contra devuelto | 138 de 138 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| Cifras (±0.01) | 105 de 106 (99.1 %) | 99.1 % (una corrida entera, sin rango) |
| Cita: documento y sección | 31 de 32 (96.9 %) | 96.9 % (una corrida entera, sin rango) |
| Abstención, rechazo o aclaración | 31 de 31 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| Sin fugas | 138 de 138 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| Acierto de herramienta | 117 de 119 (98.3 %) | 98.3 % (una corrida entera, sin rango) |
| Acierto de parámetros | 110 de 119 (92.4 %) | 92.4 % (una corrida entera, sin rango) |

Recuperación de la sección, contando solo los 4 fragmentos de BM25:

- recall@4, al menos una sección esperada en los 4: 1.000 (n = 42)
- Fracción de las esperadas en los 4: 0.877 (n = 42)
- MRR: 0.921 (n = 42)
- Preguntas con sección esperada que no buscaron: 11

## Matriz de estados

| Esperado / devuelto | ok |
|---|---|
| ok | 138 |

## Tipo de respuesta contra conducta

| Esperado / devuelto | abstiene | aclara | contesta | rechaza |
|---|---|---|---|---|
| abstencion | 12 | 0 | 0 | 8 |
| aclaracion | 0 | 8 | 0 | 0 |
| cifra | 0 | 0 | 75 | 0 |
| cita | 0 | 0 | 32 | 0 |
| rechazo | 0 | 0 | 0 | 3 |

Falsas abstenciones: 0 en total.

- Corrida 1: 0 (ninguna)

Aclaraciones donde se esperaba cifra: 0 en total.

- Corrida 1: 0 (ninguna)

Negativas que traen parte de lo esperado: 1 en total.

- Corrida 1: 1 (PT-07)

## Sin evidencia y fugas

Primera versión del modelo, antes del verificador:

| Corrida | Cifras | Citas | Datos personales | Turnos |
|---|---|---|---|---|
| 1 | 2 | 4 | 0 | 129 |

Fugas en lo que se mostró: 0.


## Formato, sobre el texto con su pie

- Contexto en 3 líneas o menos: 127 de 129
- Respuestas con resultado temporal que dicen el corte: 81 de 81
- Respuestas con fuente y corte: 129 de 129

## Medición por categoría, en una sola tabla

Tokens, costo, latencia y llamadas son por turno, de todas las corridas juntas y sin el juez ni los turnos que tronaron; la entrada incluye la caché y la salida el razonamiento. La exactitud es por pregunta. Un turno de dos categorías cuenta en las dos y una sola vez en Todas.

| Categoría | Turnos | Entrada (mediana / p90) | Salida (mediana / p90) | Costo USD (mediana / p90) | Latencia ms (p50 / p90) | Llamadas por turno (mediana / p90) | Exactitud por corrida | Exactitud, rango |
|---|---|---|---|---|---|---|---|---|
| abstencion | 18 | 13,754 / 24,952 | 691 / 1,450 | 0.000912 / 0.001752 | 5,489 / 8,270 | 2.00 / 3.00 | 18 de 19 (94.7 %) | 94.7 % (una corrida entera, sin rango) |
| adversarial | 7 | 11,343 / 17,553 | 519 / 1,270 | 0.000444 / 0.001227 | 3,135 / 6,164 | 2.00 / 2.40 | 7 de 7 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| ambiguedad | 7 | 11,271 / 11,611 | 576 / 851 | 0.000466 / 0.000637 | 3,310 / 4,502 | 2.00 / 2.00 | 7 de 7 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| cifra_con_definicion | 15 | 11,348 / 15,272 | 443 / 948 | 0.000401 / 0.000738 | 3,098 / 5,958 | 2.00 / 2.60 | 16 de 16 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| cifra_directa | 16 | 11,234 / 11,723 | 438 / 710 | 0.000405 / 0.000582 | 3,318 / 4,954 | 2.00 / 2.00 | 17 de 17 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| contradiccion | 10 | 17,816 / 32,558 | 1,568 / 2,365 | 0.001578 / 0.003014 | 7,822 / 11,616 | 2.00 / 3.00 | 10 de 10 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| cruce_de_fuentes | 14 | 19,751 / 44,021 | 1,555 / 3,732 | 0.001559 / 0.004111 | 8,510 / 17,729 | 2.50 / 4.00 | 13 de 14 (92.9 %) | 92.9 % (una corrida entera, sin rango) |
| politica_con_tabla | 12 | 17,037 / 18,443 | 754 / 1,398 | 0.001076 / 0.001343 | 4,784 / 6,910 | 2.00 / 2.00 | 12 de 13 (92.3 %) | 92.3 % (una corrida entera, sin rango) |
| tickets | 12 | 11,672 / 12,076 | 381 / 836 | 0.000397 / 0.000676 | 3,393 / 4,084 | 2.00 / 2.00 | 12 de 12 (100.0 %) | 100.0 % (una corrida entera, sin rango) |
| varios_turnos | 23 | 12,858 / 17,505 | 377 / 928 | 0.000469 / 0.001221 | 4,190 / 5,736 | 2.00 / 2.00 | 22 de 23 (95.7 %) | 95.7 % (una corrida entera, sin rango) |
| Todas | 129 | 11,837 / 21,324 | 576 / 1,609 | 0.000608 / 0.001705 | 4,190 / 8,699 | 2.00 / 3.00 | 134 de 138 (97.1 %) | 97.1 % (una corrida entera, sin rango) |

## Tokens, costo y latencia por categoría (p50 / p90)

| Categoría | Turnos | Entrada | Entrada sin caché | Salida | Llamadas | Costo USD | Latencia ms |
|---|---|---|---|---|---|---|---|
| abstencion | 18 | 13,754 / 24,952 | 3,206 / 9,130 | 691 / 1,450 | 2.00 / 3.00 | 0.000912 / 0.001752 | 5,489 / 8,270 |
| adversarial | 7 | 11,343 / 17,553 | 795 / 4,895 | 519 / 1,270 | 2.00 / 2.40 | 0.000444 / 0.001227 | 3,135 / 6,164 |
| ambiguedad | 7 | 11,271 / 11,611 | 723 / 1,063 | 576 / 851 | 2.00 / 2.00 | 0.000466 / 0.000637 | 3,310 / 4,502 |
| cifra_con_definicion | 15 | 11,348 / 15,272 | 800 / 1,559 | 443 / 948 | 2.00 / 2.60 | 0.000401 / 0.000738 | 3,098 / 5,958 |
| cifra_directa | 16 | 11,234 / 11,723 | 686 / 1,175 | 438 / 710 | 2.00 / 2.00 | 0.000405 / 0.000582 | 3,318 / 4,954 |
| contradiccion | 10 | 17,816 / 32,558 | 7,268 / 16,736 | 1,568 / 2,365 | 2.00 / 3.00 | 0.001578 / 0.003014 | 7,822 / 11,616 |
| cruce_de_fuentes | 14 | 19,751 / 44,021 | 5,098 / 22,925 | 1,555 / 3,732 | 2.50 / 4.00 | 0.001559 / 0.004111 | 8,510 / 17,729 |
| politica_con_tabla | 12 | 17,037 / 18,443 | 6,489 / 7,895 | 754 / 1,398 | 2.00 / 2.00 | 0.001076 / 0.001343 | 4,784 / 6,910 |
| tickets | 12 | 11,672 / 12,076 | 1,124 / 1,528 | 381 / 836 | 2.00 / 2.00 | 0.000397 / 0.000676 | 3,393 / 4,084 |
| varios_turnos | 23 | 12,858 / 17,505 | 2,310 / 6,957 | 377 / 928 | 2.00 / 2.00 | 0.000469 / 0.001221 | 4,190 / 5,736 |

Total: 129 turnos, 276 llamadas, 1,970,230 tokens de entrada, 106,622 de salida, 11.00 minutos y 0.119934 USD (0 turnos sin precio).

## Crecimiento por turno en la conversación larga

- Corrida 1, CV-05: entrada por turno 11,497, 11,806, 12,003, 16,185, 12,858, 19,187, 15,337, 13,943, 14,168, 19,244, 14,268, 14,744; 295 tokens por turno previo; el último cuesta 1.28 veces el primero; misma cifra: sí.

## Por pregunta

| Prompt | Bloque | Por corrida | Acierto en todas |
|---|---|---|---|
| PT-01 | trampa_sencilla | no | no |
| PT-02 | trampa_sencilla | sí | sí |
| PT-03 | trampa_sencilla | sí | sí |
| PT-04 | trampa_sencilla | sí | sí |
| PT-05 | trampa_sencilla | sí | sí |
| PT-06 | trampa_sencilla | sí | sí |
| PT-07 | trampa_sencilla | no | no |
| PT-08 | trampa_sencilla | sí | sí |
| PT-09 | trampa_sencilla | sí | sí |
| PT-10 | trampa_sencilla | sí | sí |
| PT-11 | trampa_sencilla | sí | sí |
| PT-12 | trampa_sencilla | sí | sí |
| PT-13 | trampa_sencilla | sí | sí |
| PT-14 | trampa_sencilla | sí | sí |
| PT-15 | trampa_sencilla | sí | sí |
| PT-16 | trampa_sencilla | sí | sí |
| PT-17 | trampa_sencilla | sí | sí |
| PT-19 | trampa_sencilla | sí | sí |
| PT-20 | trampa_sencilla | sí | sí |
| PT-21 | trampa_sencilla | sí | sí |
| PT-22 | trampa_sencilla | sí | sí |
| PT-23 | trampa_sencilla | sí | sí |
| PT-24 | trampa_sencilla | sí | sí |
| PT-25 | trampa_sencilla | sí | sí |
| PT-26 | trampa_sencilla | sí | sí |
| PT-27 | trampa_sencilla | sí | sí |
| PT-28 | trampa_sencilla | sí | sí |
| PT-29 | trampa_sencilla | sí | sí |
| PT-30 | trampa_sencilla | sí | sí |
| PT-31 | trampa_sencilla | sí | sí |
| PT-32 | trampa_sencilla | sí | sí |
| PT-33 | trampa_sencilla | sí | sí |
| PT-34 | trampa_sencilla | sí | sí |
| PT-35 | trampa_sencilla | sí | sí |
| PT-36 | trampa_sencilla | sí | sí |
| PT-37 | trampa_sencilla | sí | sí |
| PT-38 | trampa_sencilla | sí | sí |
| PT-39 | trampa_sencilla | sí | sí |
| PT-40 | trampa_sencilla | sí | sí |
| PT-41 | trampa_sencilla | sí | sí |
| PT-42 | trampa_sencilla | sí | sí |
| PT-43 | trampa_sencilla | sí | sí |
| PT-44 | trampa_sencilla | sí | sí |
| PT-45 | trampa_sencilla | sí | sí |
| PT-46 | trampa_sencilla | sí | sí |
| PT-47 | trampa_sencilla | sí | sí |
| PT-48 | trampa_sencilla | sí | sí |
| PT-49 | trampa_sencilla | sí | sí |
| PT-50 | trampa_sencilla | sí | sí |
| PT-51 | trampa_sencilla | sí | sí |
| N-01 | normal_sencilla | sí | sí |
| N-02 | normal_sencilla | sí | sí |
| N-03 | normal_sencilla | sí | sí |
| N-04 | normal_sencilla | sí | sí |
| N-05 | normal_sencilla | sí | sí |
| N-06 | normal_sencilla | sí | sí |
| N-07 | normal_sencilla | sí | sí |
| N-08 | normal_sencilla | sí | sí |
| N-09 | normal_sencilla | sí | sí |
| N-10 | normal_sencilla | sí | sí |
| N-11 | normal_sencilla | sí | sí |
| N-12 | normal_sencilla | sí | sí |
| N-13 | normal_sencilla | no | no |
| N-14 | normal_sencilla | sí | sí |
| N-15 | normal_sencilla | sí | sí |
| N-16 | normal_sencilla | sí | sí |
| N-17 | normal_sencilla | sí | sí |
| N-18 | normal_sencilla | sí | sí |
| N-19 | normal_sencilla | sí | sí |
| N-20 | normal_sencilla | sí | sí |
| N-21 | normal_sencilla | sí | sí |
| N-22 | normal_sencilla | sí | sí |
| N-23 | normal_sencilla | sí | sí |
| N-24 | normal_sencilla | sí | sí |
| N-25 | normal_sencilla | sí | sí |
| CV-01 | conversacion | sí | sí |
| CV-02 | conversacion | sí | sí |
| CV-03 | conversacion | sí | sí |
| CV-04 | conversacion | no | no |
| CV-05 | conversacion | sí | sí |
| DN-01 | dos_normales | sí | sí |
| DN-02 | dos_normales | sí | sí |
| DN-03 | dos_normales | sí | sí |
| DN-04 | dos_normales | sí | sí |
| DN-05 | dos_normales | sí | sí |
| DN-06 | dos_normales | sí | sí |
| DN-07 | dos_normales | sí | sí |
| DN-08 | dos_normales | sí | sí |
| DN-09 | dos_normales | sí | sí |
| DN-10 | dos_normales | sí | sí |
| TN-01 | trampa_y_normal | sí | sí |
| TN-02 | trampa_y_normal | sí | sí |
| TN-03 | trampa_y_normal | sí | sí |
| TN-04 | trampa_y_normal | sí | sí |
| TN-05 | trampa_y_normal | sí | sí |
| PT-18 | dos_trampas | sí | sí |
| DT-01 | dos_trampas | sí | sí |
| DT-02 | dos_trampas | sí | sí |
| DT-03 | dos_trampas | sí | sí |
| DT-04 | dos_trampas | sí | sí |

## Fallas y su causa

- PT-01 (trampa_sencilla)
  - corrida 1: CifrasEsperadas: PT-01: faltan 4.5, 5.5
- PT-07 (trampa_sencilla)
  - corrida 1: CitaEsperada: PT-07: no cita ninguna de FAQ-OPS-001 P1, MAN-OPS-007 3
- N-13 (normal_sencilla)
  - corrida 1: CriterioDelJuez: N-13: Criterio: nombrar al Gerente de Tienda con el rango y la cita. Pedido 1: Gerente de Tienda nombrado en "Un Gerente de Tienda autoriza la devolución"; rango 10,000.01 a 30,000.00 en "cae en el rango de 10,000.01 a 30,000.00 pesos"; cita "PRO-SAC-021 §9, p. 6" presente: cumple. Reprobar si se nombra al Supervisor o al Gerente de Servicio al Cliente como autorizador: la respuesta sí menciona al Gerente de Servicio al Cliente en el segundo punto, al decir que §4.1 pide al Gerente de Servicio al Cliente para devoluciones fuera de plazo por defecto de fabricación; según la regla 1, dar un dato rotulado como una cosa no es darlo como otra, y aquí se presenta como el autorizador de un caso de la misma pregunta, en contradicción con la respuesta principal de que el Gerente de Tienda autoriza. Cae p
- CV-04 (conversacion)
  - corrida 1: CriterioDelJuez: CV-04-2: El criterio pide: pedir contexto o decir que da el total. La respuesta da el total de junio ($5,597,388.24 MXN) y no reprueba con 786,418.46, así que no cae en la condición de reprobar. Sin embargo, la respuesta no dice que lo toma como total de las cuatro tiendas: la primera línea lo presenta como 'Venta de junio de 2026' sin decir que es total; luego la segunda línea dice 'Venta neta de las cuatro tiendas (T01 a T04)... $13,305.00 MXN', lo que deja dos cifras para junio sin rotular cuál es el total. Dar un dato rotulado por lo que es no es darlo como si fuera otro: aquí el total aparece con la etiqueta de venta de junio, pero la cifra de 13,305.00 aparece como 'venta neta' de las cuatro tiendas, lo que contradice el total de 5,597,388.24 para las mismas tiendas y mes. Eso es una incohere
