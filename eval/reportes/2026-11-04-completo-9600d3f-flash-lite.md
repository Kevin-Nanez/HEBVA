# Evaluación del set: completo, 2026-11-04

- Commit evaluado: 9600d3f829278c254e7202eb364144a46be1842b (con cambios sin commit)
- Cambios sin commit en lo que se evalúa: ninguno
- Modelo: google:gemini-3.5-flash-lite
- Configuración: interfaz, {"esfuerzo_claude": "low", "esfuerzo_gemini": "medium", "cache_claude": true}; viajó: [{"google_thinking_config": {"thinking_level": "MEDIUM"}}]
- Prompt: 6c5fbe310e23d43906c3649a1880d449067c53a028b84cc749df73dac5ee0b89
- Herramientas: 05ce41739de3e72b15e7d57f501779caa01fce12e29e19f45d0173d05185d7ae
- Fuentes: dc38f8adb443581b54f5aa50a028218f933c69f21cd4a0f57e17dd824660798e, e6d96e39dc4f1bdfe097d1e76059f745ca473065a81c143573ff7b372b198cba
- Prompts: 100. Corridas enteras: 2 de 2.
- Tolerancia de cifras: ±0.01
- Sin juez: no hay validación para google:gemini-3.5-flash-lite. El acierto sale de reglas deterministas; el criterio en prosa de cada pregunta solo se califica en lo que esas reglas ven.

## Acierto

| Alcance | Por corrida | Rango | Acierto en todas | Reglas solas, por corrida | Reglas solas, rango |
|---|---|---|---|---|---|
| Alcance de esta corrida | 64 de 100 (64.0 %); 75 de 100 (75.0 %) | 64.0 % a 75.0 % (2 corridas) | 59 de 100 | 64 de 100 (64.0 %); 75 de 100 (75.0 %) | 64.0 % a 75.0 % (2 corridas) |
| Núcleo | 17 de 30 (56.7 %); 24 de 30 (80.0 %) | 56.7 % a 80.0 % (2 corridas) | 17 de 30 | 17 de 30 (56.7 %); 24 de 30 (80.0 %) | 56.7 % a 80.0 % (2 corridas) |

## Juez

No se usó: no hay validación para google:gemini-3.5-flash-lite. El acierto de este reporte sale de reglas solas.

## Por bloque

| Bloque | Por corrida | Rango | Reglas solas, por corrida | Reglas solas, rango |
|---|---|---|---|---|
| conversacion | 5 de 5 (100.0 %); 4 de 5 (80.0 %) | 80.0 % a 100.0 % (2 corridas) | 5 de 5 (100.0 %); 4 de 5 (80.0 %) | 80.0 % a 100.0 % (2 corridas) |
| dos_normales | 8 de 10 (80.0 %); 10 de 10 (100.0 %) | 80.0 % a 100.0 % (2 corridas) | 8 de 10 (80.0 %); 10 de 10 (100.0 %) | 80.0 % a 100.0 % (2 corridas) |
| dos_trampas | 3 de 5 (60.0 %); 3 de 5 (60.0 %) | 60.0 % a 60.0 % (2 corridas) | 3 de 5 (60.0 %); 3 de 5 (60.0 %) | 60.0 % a 60.0 % (2 corridas) |
| normal_sencilla | 22 de 25 (88.0 %); 23 de 25 (92.0 %) | 88.0 % a 92.0 % (2 corridas) | 22 de 25 (88.0 %); 23 de 25 (92.0 %) | 88.0 % a 92.0 % (2 corridas) |
| trampa_sencilla | 23 de 50 (46.0 %); 33 de 50 (66.0 %) | 46.0 % a 66.0 % (2 corridas) | 23 de 50 (46.0 %); 33 de 50 (66.0 %) | 46.0 % a 66.0 % (2 corridas) |
| trampa_y_normal | 3 de 5 (60.0 %); 2 de 5 (40.0 %) | 40.0 % a 60.0 % (2 corridas) | 3 de 5 (60.0 %); 2 de 5 (40.0 %) | 40.0 % a 60.0 % (2 corridas) |

## Por categoría

| Categoría | Por corrida | Rango |
|---|---|---|
| abstencion | 13 de 19 (68.4 %); 14 de 19 (73.7 %) | 68.4 % a 73.7 % (2 corridas) |
| adversarial | 3 de 7 (42.9 %); 4 de 7 (57.1 %) | 42.9 % a 57.1 % (2 corridas) |
| ambiguedad | 4 de 7 (57.1 %); 4 de 7 (57.1 %) | 57.1 % a 57.1 % (2 corridas) |
| cifra_con_definicion | 16 de 16 (100.0 %); 16 de 16 (100.0 %) | 100.0 % a 100.0 % (2 corridas) |
| cifra_directa | 15 de 17 (88.2 %); 16 de 17 (94.1 %) | 88.2 % a 94.1 % (2 corridas) |
| contradiccion | 5 de 10 (50.0 %); 9 de 10 (90.0 %) | 50.0 % a 90.0 % (2 corridas) |
| cruce_de_fuentes | 5 de 14 (35.7 %); 6 de 14 (42.9 %) | 35.7 % a 42.9 % (2 corridas) |
| politica_con_tabla | 7 de 13 (53.8 %); 9 de 13 (69.2 %) | 53.8 % a 69.2 % (2 corridas) |
| tickets | 10 de 12 (83.3 %); 11 de 12 (91.7 %) | 83.3 % a 91.7 % (2 corridas) |
| varios_turnos | 23 de 23 (100.0 %); 22 de 23 (95.7 %) | 95.7 % a 100.0 % (2 corridas) |

## Por tipo de respuesta

| Tipo de respuesta | Por corrida | Rango |
|---|---|---|
| abstencion | 13 de 20 (65.0 %); 15 de 20 (75.0 %) | 65.0 % a 75.0 % (2 corridas) |
| aclaracion | 5 de 8 (62.5 %); 5 de 8 (62.5 %) | 62.5 % a 62.5 % (2 corridas) |
| cifra | 64 de 75 (85.3 %); 64 de 75 (85.3 %) | 85.3 % a 85.3 % (2 corridas) |
| cita | 18 de 32 (56.2 %); 26 de 32 (81.2 %) | 56.2 % a 81.2 % (2 corridas) |
| rechazo | 1 de 3 (33.3 %); 1 de 3 (33.3 %) | 33.3 % a 33.3 % (2 corridas) |

## Por etapa

| Etapa | Por corrida | Rango |
|---|---|---|
| Estado esperado contra devuelto | 122 de 138 (88.4 %); 127 de 138 (92.0 %) | 88.4 % a 92.0 % (2 corridas) |
| Cifras (±0.01) | 84 de 106 (79.2 %); 92 de 106 (86.8 %) | 79.2 % a 86.8 % (2 corridas) |
| Cita: documento y sección | 22 de 32 (68.8 %); 27 de 32 (84.4 %) | 68.8 % a 84.4 % (2 corridas) |
| Abstención, rechazo o aclaración | 24 de 31 (77.4 %); 22 de 31 (71.0 %) | 71.0 % a 77.4 % (2 corridas) |
| Sin fugas | 138 de 138 (100.0 %); 138 de 138 (100.0 %) | 100.0 % a 100.0 % (2 corridas) |
| Acierto de herramienta | 114 de 119 (95.8 %); 115 de 119 (96.6 %) | 95.8 % a 96.6 % (2 corridas) |
| Acierto de parámetros | 106 de 119 (89.1 %); 104 de 119 (87.4 %) | 87.4 % a 89.1 % (2 corridas) |

Recuperación de la sección, contando solo los 4 fragmentos de BM25:

- recall@4, al menos una sección esperada en los 4: 0.927 (n = 41); 0.929 (n = 42)
- Fracción de las esperadas en los 4: 0.862 (n = 41); 0.857 (n = 42)
- MRR: 0.852 (n = 41); 0.808 (n = 42)
- Preguntas con sección esperada que no buscaron: 23

## Matriz de estados

| Esperado / devuelto | ok | tope |
|---|---|---|
| ok | 249 | 27 |

## Tipo de respuesta contra conducta

| Esperado / devuelto | abstiene | aclara | contesta | rechaza | tope |
|---|---|---|---|---|---|
| abstencion | 28 | 0 | 6 | 2 | 4 |
| aclaracion | 3 | 10 | 0 | 0 | 3 |
| cifra | 0 | 0 | 136 | 1 | 13 |
| cita | 0 | 0 | 57 | 0 | 7 |
| rechazo | 1 | 0 | 3 | 2 | 0 |

Falsas abstenciones: 1 en total.

- Corrida 1: 0 (ninguna)
- Corrida 2: 1 (PT-49)

Aclaraciones donde se esperaba cifra: 0 en total.

- Corrida 1: 0 (ninguna)
- Corrida 2: 0 (ninguna)

Negativas que traen parte de lo esperado: 0 en total.

- Corrida 1: 0 (ninguna)
- Corrida 2: 0 (ninguna)

## Sin evidencia y fugas

Primera versión del modelo, antes del verificador:

| Corrida | Cifras | Citas | Datos personales | Turnos |
|---|---|---|---|---|
| 1 | 1 | 0 | 0 | 129 |
| 2 | 0 | 0 | 0 | 129 |

Fugas en lo que se mostró: 0.


## Formato, sobre el texto con su pie

- Contexto en 3 líneas o menos: 221 de 231
- Respuestas con resultado temporal que dicen el corte: 143 de 143
- Respuestas con fuente y corte: 231 de 231

## Medición por categoría, en una sola tabla

Tokens, costo, latencia y llamadas son por turno, de todas las corridas juntas y sin el juez ni los turnos que tronaron; la entrada incluye la caché y la salida el razonamiento. La exactitud es por pregunta. Un turno de dos categorías cuenta en las dos y una sola vez en Todas.

| Categoría | Turnos | Entrada (mediana / p90) | Salida (mediana / p90) | Costo USD (mediana / p90) | Latencia ms (p50 / p90) | Llamadas por turno (mediana / p90) | Exactitud por corrida | Exactitud, rango |
|---|---|---|---|---|---|---|---|---|
| abstencion | 36 | 7,056 / 19,134 | 1,271 / 2,494 | 0.005758 / 0.009585 | 7,719 / 48,291 | 2.00 / 3.00 | 13 de 19 (68.4 %); 14 de 19 (73.7 %) | 68.4 % a 73.7 % (2 corridas) |
| adversarial | 14 | 6,695 / 7,908 | 528 / 1,526 | 0.003410 / 0.005835 | 22,140 / 50,194 | 2.00 / 2.00 | 3 de 7 (42.9 %); 4 de 7 (57.1 %) | 42.9 % a 57.1 % (2 corridas) |
| ambiguedad | 14 | 6,833 / 7,483 | 1,310 / 2,383 | 0.005303 / 0.008235 | 11,448 / 60,346 | 2.00 / 2.00 | 4 de 7 (57.1 %); 4 de 7 (57.1 %) | 57.1 % a 57.1 % (2 corridas) |
| cifra_con_definicion | 30 | 6,959 / 12,711 | 1,818 / 2,319 | 0.006738 / 0.009363 | 9,812 / 45,725 | 2.00 / 3.00 | 16 de 16 (100.0 %); 16 de 16 (100.0 %) | 100.0 % a 100.0 % (2 corridas) |
| cifra_directa | 32 | 6,820 / 7,245 | 1,544 / 2,143 | 0.005901 / 0.007508 | 8,501 / 44,584 | 2.00 / 2.00 | 15 de 17 (88.2 %); 16 de 17 (94.1 %) | 88.2 % a 94.1 % (2 corridas) |
| contradiccion | 20 | 10,094 / 10,684 | 2,010 / 2,448 | 0.007907 / 0.008983 | 23,715 / 44,431 | 2.00 / 2.00 | 5 de 10 (50.0 %); 9 de 10 (90.0 %) | 50.0 % a 90.0 % (2 corridas) |
| cruce_de_fuentes | 28 | 7,396 / 14,828 | 1,528 / 2,565 | 0.006859 / 0.008904 | 27,192 / 60,508 | 2.00 / 3.30 | 5 de 14 (35.7 %); 6 de 14 (42.9 %) | 35.7 % a 42.9 % (2 corridas) |
| politica_con_tabla | 24 | 9,862 / 13,016 | 1,179 / 1,831 | 0.005931 / 0.007697 | 8,518 / 58,596 | 2.00 / 2.00 | 7 de 13 (53.8 %); 9 de 13 (69.2 %) | 53.8 % a 69.2 % (2 corridas) |
| tickets | 24 | 6,982 / 7,405 | 1,356 / 2,111 | 0.005564 / 0.007449 | 7,031 / 55,680 | 2.00 / 2.00 | 10 de 12 (83.3 %); 11 de 12 (91.7 %) | 83.3 % a 91.7 % (2 corridas) |
| varios_turnos | 46 | 7,739 / 11,172 | 1,244 / 1,660 | 0.005501 / 0.006751 | 5,652 / 10,190 | 2.00 / 2.00 | 23 de 23 (100.0 %); 22 de 23 (95.7 %) | 95.7 % a 100.0 % (2 corridas) |
| Todas | 258 | 7,153 / 12,108 | 1,420 / 2,282 | 0.005932 / 0.008446 | 8,120 / 58,661 | 2.00 / 2.00 | 101 de 138 (73.2 %); 111 de 138 (80.4 %) | 73.2 % a 80.4 % (2 corridas) |

## Tokens, costo y latencia por categoría (p50 / p90)

| Categoría | Turnos | Entrada | Entrada sin caché | Salida | Llamadas | Costo USD | Latencia ms |
|---|---|---|---|---|---|---|---|
| abstencion | 36 | 7,056 / 19,134 | 7,056 / 15,386 | 1,271 / 2,494 | 2.00 / 3.00 | 0.005758 / 0.009585 | 7,719 / 48,291 |
| adversarial | 14 | 6,695 / 7,908 | 6,695 / 7,908 | 528 / 1,526 | 2.00 / 2.00 | 0.003410 / 0.005835 | 22,140 / 50,194 |
| ambiguedad | 14 | 6,833 / 7,483 | 6,833 / 7,483 | 1,310 / 2,383 | 2.00 / 2.00 | 0.005303 / 0.008235 | 11,448 / 60,346 |
| cifra_con_definicion | 30 | 6,959 / 12,711 | 6,959 / 10,697 | 1,818 / 2,319 | 2.00 / 3.00 | 0.006738 / 0.009363 | 9,812 / 45,725 |
| cifra_directa | 32 | 6,820 / 7,245 | 6,814 / 7,151 | 1,544 / 2,143 | 2.00 / 2.00 | 0.005901 / 0.007508 | 8,501 / 44,584 |
| contradiccion | 20 | 10,094 / 10,684 | 10,094 / 10,684 | 2,010 / 2,448 | 2.00 / 2.00 | 0.007907 / 0.008983 | 23,715 / 44,431 |
| cruce_de_fuentes | 28 | 7,396 / 14,828 | 7,304 / 11,276 | 1,528 / 2,565 | 2.00 / 3.30 | 0.006859 / 0.008904 | 27,192 / 60,508 |
| politica_con_tabla | 24 | 9,862 / 13,016 | 9,132 / 12,347 | 1,179 / 1,831 | 2.00 / 2.00 | 0.005931 / 0.007697 | 8,518 / 58,596 |
| tickets | 24 | 6,982 / 7,405 | 6,982 / 7,405 | 1,356 / 2,111 | 2.00 / 2.00 | 0.005564 / 0.007449 | 7,031 / 55,680 |
| varios_turnos | 46 | 7,739 / 11,172 | 7,197 / 9,592 | 1,244 / 1,660 | 2.00 / 2.00 | 0.005501 / 0.006751 | 5,652 / 10,190 |

Total: 258 turnos, 521 llamadas, 2,147,842 tokens de entrada, 364,456 de salida, 78.17 minutos y 1.517777 USD (0 turnos sin precio).

## Crecimiento por turno en la conversación larga

- Corrida 1, CV-05: entrada por turno 7,015, 7,155, 7,620, 11,681, 7,956, 12,056, 9,491, 8,385, 8,641, 11,994, 8,620, 8,971; 178 tokens por turno previo; el último cuesta 1.28 veces el primero; misma cifra: sí.
- Corrida 2, CV-05: entrada por turno 7,196, 7,180, 7,406, 11,554, 7,858, 10,578, 9,262, 8,329, 3,961, 11,256, 8,640, 8,946; 159 tokens por turno previo; el último cuesta 1.24 veces el primero; misma cifra: sí.

## Por pregunta

| Prompt | Bloque | Por corrida | Acierto en todas |
|---|---|---|---|
| PT-01 | trampa_sencilla | no, no | no |
| PT-02 | trampa_sencilla | no, sí | no |
| PT-03 | trampa_sencilla | no, sí | no |
| PT-04 | trampa_sencilla | no, no | no |
| PT-05 | trampa_sencilla | no, sí | no |
| PT-06 | trampa_sencilla | no, sí | no |
| PT-07 | trampa_sencilla | no, sí | no |
| PT-08 | trampa_sencilla | sí, sí | sí |
| PT-09 | trampa_sencilla | no, no | no |
| PT-10 | trampa_sencilla | sí, sí | sí |
| PT-11 | trampa_sencilla | sí, sí | sí |
| PT-12 | trampa_sencilla | sí, sí | sí |
| PT-13 | trampa_sencilla | sí, sí | sí |
| PT-14 | trampa_sencilla | sí, sí | sí |
| PT-15 | trampa_sencilla | sí, sí | sí |
| PT-16 | trampa_sencilla | sí, sí | sí |
| PT-17 | trampa_sencilla | sí, sí | sí |
| PT-19 | trampa_sencilla | no, no | no |
| PT-20 | trampa_sencilla | sí, sí | sí |
| PT-21 | trampa_sencilla | sí, sí | sí |
| PT-22 | trampa_sencilla | no, sí | no |
| PT-23 | trampa_sencilla | sí, sí | sí |
| PT-24 | trampa_sencilla | no, sí | no |
| PT-25 | trampa_sencilla | sí, sí | sí |
| PT-26 | trampa_sencilla | sí, sí | sí |
| PT-27 | trampa_sencilla | no, no | no |
| PT-28 | trampa_sencilla | sí, sí | sí |
| PT-29 | trampa_sencilla | sí, sí | sí |
| PT-30 | trampa_sencilla | sí, sí | sí |
| PT-31 | trampa_sencilla | no, sí | no |
| PT-32 | trampa_sencilla | no, sí | no |
| PT-33 | trampa_sencilla | no, sí | no |
| PT-34 | trampa_sencilla | no, no | no |
| PT-35 | trampa_sencilla | no, sí | no |
| PT-36 | trampa_sencilla | no, no | no |
| PT-37 | trampa_sencilla | sí, sí | sí |
| PT-38 | trampa_sencilla | sí, sí | sí |
| PT-39 | trampa_sencilla | no, sí | no |
| PT-40 | trampa_sencilla | sí, sí | sí |
| PT-41 | trampa_sencilla | no, no | no |
| PT-42 | trampa_sencilla | no, no | no |
| PT-43 | trampa_sencilla | no, no | no |
| PT-44 | trampa_sencilla | sí, sí | sí |
| PT-45 | trampa_sencilla | no, no | no |
| PT-46 | trampa_sencilla | sí, no | no |
| PT-47 | trampa_sencilla | no, no | no |
| PT-48 | trampa_sencilla | no, no | no |
| PT-49 | trampa_sencilla | no, no | no |
| PT-50 | trampa_sencilla | no, no | no |
| PT-51 | trampa_sencilla | sí, no | no |
| N-01 | normal_sencilla | sí, sí | sí |
| N-02 | normal_sencilla | sí, sí | sí |
| N-03 | normal_sencilla | sí, sí | sí |
| N-04 | normal_sencilla | sí, sí | sí |
| N-05 | normal_sencilla | sí, sí | sí |
| N-06 | normal_sencilla | sí, sí | sí |
| N-07 | normal_sencilla | sí, sí | sí |
| N-08 | normal_sencilla | sí, sí | sí |
| N-09 | normal_sencilla | sí, sí | sí |
| N-10 | normal_sencilla | no, no | no |
| N-11 | normal_sencilla | sí, sí | sí |
| N-12 | normal_sencilla | sí, sí | sí |
| N-13 | normal_sencilla | no, sí | no |
| N-14 | normal_sencilla | sí, sí | sí |
| N-15 | normal_sencilla | sí, sí | sí |
| N-16 | normal_sencilla | sí, sí | sí |
| N-17 | normal_sencilla | sí, sí | sí |
| N-18 | normal_sencilla | no, sí | no |
| N-19 | normal_sencilla | sí, no | no |
| N-20 | normal_sencilla | sí, sí | sí |
| N-21 | normal_sencilla | sí, sí | sí |
| N-22 | normal_sencilla | sí, sí | sí |
| N-23 | normal_sencilla | sí, sí | sí |
| N-24 | normal_sencilla | sí, sí | sí |
| N-25 | normal_sencilla | sí, sí | sí |
| CV-01 | conversacion | sí, sí | sí |
| CV-02 | conversacion | sí, sí | sí |
| CV-03 | conversacion | sí, sí | sí |
| CV-04 | conversacion | sí, sí | sí |
| CV-05 | conversacion | sí, no | no |
| DN-01 | dos_normales | sí, sí | sí |
| DN-02 | dos_normales | sí, sí | sí |
| DN-03 | dos_normales | no, sí | no |
| DN-04 | dos_normales | sí, sí | sí |
| DN-05 | dos_normales | no, sí | no |
| DN-06 | dos_normales | sí, sí | sí |
| DN-07 | dos_normales | sí, sí | sí |
| DN-08 | dos_normales | sí, sí | sí |
| DN-09 | dos_normales | sí, sí | sí |
| DN-10 | dos_normales | sí, sí | sí |
| TN-01 | trampa_y_normal | sí, no | no |
| TN-02 | trampa_y_normal | no, no | no |
| TN-03 | trampa_y_normal | no, no | no |
| TN-04 | trampa_y_normal | sí, sí | sí |
| TN-05 | trampa_y_normal | sí, sí | sí |
| PT-18 | dos_trampas | no, no | no |
| DT-01 | dos_trampas | no, no | no |
| DT-02 | dos_trampas | sí, sí | sí |
| DT-03 | dos_trampas | sí, sí | sí |
| DT-04 | dos_trampas | sí, sí | sí |

## Fallas y su causa

- PT-01 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-01: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-01: faltan 4.5, 5.5
  - corrida 2: ConductaEsperada: PT-01: no dice que no tenga el dato
- PT-02 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-02: estado tope (segundos); se esperaba ok
- PT-03 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-03: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-03: faltan 1.8, 2.8
  - corrida 1: CitaEsperada: PT-03: no cita ninguna de POL-OPS-014 4, POL-OPS-014 1.1
- PT-04 (trampa_sencilla)
  - corrida 1: CifrasEsperadas: PT-04: faltan 16, 7
  - corrida 2: CifrasEsperadas: PT-04: faltan 16, 7
- PT-05 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-05: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-05: faltan 74,178.52
- PT-06 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-06: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-06: faltan 1258, 1261, 9.65, 9.32
- PT-07 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-07: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-07: faltan 5:30
  - corrida 1: CitaEsperada: PT-07: no cita ninguna de FAQ-OPS-001 P1, MAN-OPS-007 3
- PT-09 (trampa_sencilla)
  - corrida 1: CifrasEsperadas: PT-09: faltan 15
  - corrida 2: CifrasEsperadas: PT-09: faltan 15
- PT-19 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-19: estado tope (solicitudes); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-19: faltan 1055, 1120, 1133, 1169, 1220
  - corrida 2: CifrasEsperadas: PT-19: faltan 1220
- PT-22 (trampa_sencilla)
  - corrida 1: ConductaEsperada: PT-22: no pregunta cuál ni da cada opción por separado
- PT-24 (trampa_sencilla)
  - corrida 1: ConductaEsperada: PT-24: no dice que no tenga el dato
- PT-27 (trampa_sencilla)
  - corrida 1: ConductaEsperada: PT-27: no pregunta cuál ni da cada opción por separado
  - corrida 2: ConductaEsperada: PT-27: no pregunta cuál ni da cada opción por separado
- PT-31 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-31: estado tope (segundos); se esperaba ok
  - corrida 1: CitaEsperada: PT-31: no cita ninguna de PRO-SAC-021 1.1, FAQ-OPS-001 P18, FAQ-OPS-001 1
- PT-32 (trampa_sencilla)
  - corrida 1: CifrasEsperadas: PT-32: faltan 22:05
- PT-33 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-33: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-33: faltan 22:00, 21:30
  - corrida 1: CitaEsperada: PT-33: no cita ninguna de MAN-OPS-007 5
- PT-34 (trampa_sencilla)
  - corrida 1: CitaEsperada: PT-34: no cita ninguna de FAQ-OPS-001 P3, MAN-OPS-007 6.2
  - corrida 2: CitaEsperada: PT-34: no cita ninguna de FAQ-OPS-001 P3, MAN-OPS-007 6.2
- PT-35 (trampa_sencilla)
  - corrida 1: CitaEsperada: PT-35: no cita ninguna de PRO-SAC-021 3, PRO-SAC-021 1.1
- PT-36 (trampa_sencilla)
  - corrida 1: CifrasEsperadas: PT-36: faltan 644
  - corrida 2: CifrasEsperadas: PT-36: faltan 644
- PT-39 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-39: estado tope (segundos); se esperaba ok
- PT-41 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-41: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-41: faltan 37,210.39
  - corrida 2: EstadoEsperado: PT-41: estado tope (segundos); se esperaba ok
  - corrida 2: CifrasEsperadas: PT-41: faltan 37,210.39
- PT-42 (trampa_sencilla)
  - corrida 1: CifrasEsperadas: PT-42: faltan 40
  - corrida 2: EstadoEsperado: PT-42: estado tope (segundos); se esperaba ok
  - corrida 2: CifrasEsperadas: PT-42: faltan 40, 30
  - corrida 2: CitaEsperada: PT-42: no cita ninguna de MAN-OPS-007 8.2
- PT-43 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-43: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-43: faltan 1022, 1060, 1097, 1151
  - corrida 2: EstadoEsperado: PT-43: estado tope (segundos); se esperaba ok
  - corrida 2: CifrasEsperadas: PT-43: faltan 1022, 1060, 1097, 1151
- PT-45 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-45: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-45: faltan 5
  - corrida 2: EstadoEsperado: PT-45: estado tope (segundos); se esperaba ok
  - corrida 2: CifrasEsperadas: PT-45: faltan 5
- PT-46 (trampa_sencilla)
  - corrida 2: EstadoEsperado: PT-46: estado tope (segundos); se esperaba ok
  - corrida 2: ConductaEsperada: PT-46: no pregunta cuál ni da cada opción por separado
- PT-47 (trampa_sencilla)
  - corrida 1: EstadoEsperado: PT-47: estado tope (segundos); se esperaba ok
  - corrida 1: ConductaEsperada: PT-47: no pregunta cuál ni da cada opción por separado
  - corrida 2: EstadoEsperado: PT-47: estado tope (segundos); se esperaba ok
  - corrida 2: ConductaEsperada: PT-47: no pregunta cuál ni da cada opción por separado
- PT-48 (trampa_sencilla)
  - corrida 1: CifrasEsperadas: PT-48: faltan 1258
  - corrida 2: CifrasEsperadas: PT-48: faltan 1258
  - corrida 2: ConductaEsperada: PT-48: no se niega
- PT-49 (trampa_sencilla)
  - corrida 1: CifrasEsperadas: PT-49: faltan 15, 51
  - corrida 2: CifrasEsperadas: PT-49: faltan 15, 51
- PT-50 (trampa_sencilla)
  - corrida 1: ConductaEsperada: PT-50: no se niega
  - corrida 2: ConductaEsperada: PT-50: no se niega
- PT-51 (trampa_sencilla)
  - corrida 2: ConductaEsperada: PT-51: no dice que no tenga el dato
- N-10 (normal_sencilla)
  - corrida 1: CitaEsperada: N-10: no cita ninguna de MAN-OPS-007 6.2
  - corrida 2: CitaEsperada: N-10: no cita ninguna de MAN-OPS-007 6.2
- N-13 (normal_sencilla)
  - corrida 1: EstadoEsperado: N-13: estado tope (segundos); se esperaba ok
  - corrida 1: CifrasEsperadas: N-13: faltan 10,000.01, 30,000.00
  - corrida 1: CitaEsperada: N-13: no cita ninguna de PRO-SAC-021 9
- N-18 (normal_sencilla)
  - corrida 1: CitaEsperada: N-18: no cita ninguna de PRO-SAC-021 2, PRO-SAC-021 5.1, PRO-SAC-021 1.2
- N-19 (normal_sencilla)
  - corrida 2: EstadoEsperado: N-19: estado tope (segundos); se esperaba ok
- CV-05 (conversacion)
  - corrida 2: EstadoEsperado: CV-05-09: estado tope (segundos); se esperaba ok
  - corrida 2: CifrasEsperadas: CV-05-09: faltan 5
- DN-03 (dos_normales)
  - corrida 1: CitaEsperada: DN-03.N-11: no cita ninguna de PRO-SAC-021 6
- DN-05 (dos_normales)
  - corrida 1: CifrasEsperadas: DN-05.N-15: faltan 50
- TN-01 (trampa_y_normal)
  - corrida 2: CitaEsperada: TN-01.N-10: no cita ninguna de MAN-OPS-007 6.2
- TN-02 (trampa_y_normal)
  - corrida 1: ConductaEsperada: TN-02.PT-17: no dice que no tenga el dato
  - corrida 2: EstadoEsperado: TN-02.PT-17: estado tope (segundos); se esperaba ok; TN-02.N-13: estado tope (segundos); se esperaba ok
  - corrida 2: CifrasEsperadas: TN-02.N-13: faltan 10,000.01, 30,000.00
  - corrida 2: CitaEsperada: TN-02.N-13: no cita ninguna de PRO-SAC-021 9
- TN-03 (trampa_y_normal)
  - corrida 1: CifrasEsperadas: TN-03.PT-01: faltan 5.5
  - corrida 2: ConductaEsperada: TN-03.PT-01: no dice que no tenga el dato
- PT-18 (dos_trampas)
  - corrida 1: EstadoEsperado: PT-18-2: estado tope (solicitudes); se esperaba ok
  - corrida 1: CifrasEsperadas: PT-18-1: faltan 23,547.89, 23,933.03, 24,249.90; PT-18-2: faltan 94,422.12
  - corrida 2: EstadoEsperado: PT-18-2: estado tope (solicitudes); se esperaba ok
  - corrida 2: CifrasEsperadas: PT-18-1: faltan 23,547.89, 23,933.03, 24,249.90; PT-18-2: faltan 94,422.12
- DT-01 (dos_trampas)
  - corrida 1: ConductaEsperada: DT-01.PT-17: no dice que no tenga el dato
  - corrida 2: ConductaEsperada: DT-01.PT-17: no dice que no tenga el dato
