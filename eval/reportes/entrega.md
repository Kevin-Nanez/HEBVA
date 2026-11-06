# Entrega: verificación del paquete

Fecha: 2026-11-05.
Commit base: `22d5127`

Las condiciones del paquete se cumplen sobre el commit base: el zip pesa 1.8 MB, no trae `docs/` ni `.claude/`, gitleaks no encuentra nada en el historial ni dentro del zip, pip-audit da 0 vulnerabilidades en los 79 paquetes del lock y las 10 fuentes tienen el sha256 de la lista del 2026-10-30. El arranque desde una carpeta limpia se midió sobre `eab451f`: la interfaz abrió en 1 min 36.5 s y la primera respuesta, con la cifra del CSV, llegó a 1 min 54 s del primer comando; de ese commit al base no cambian `start.py`, `start.bat`, `requirements.txt` ni `.env.example`. De las 5 condiciones de exactitud y seguridad, 4 se cumplen y 1 es parcial porque falta revisar el video y las capturas.

Desde el paquete verificado anterior (`22a654f`), el commit base suma el reporte de esa verificación (`3b4b50d`) y `22d5127`: la plantilla y el README citan solo la corrida en esfuerzo medium, el zip sale sin `.claude/` ni los reportes que solo sirvieron durante el desarrollo, y los reportes que viajan y su generador pierden los códigos del plan. Ninguno de los dos cambia el agente, sus herramientas ni el prompt. Después entra solo el commit de este reporte; el zip que se adjunta al correo se arma sobre ese commit, con los comandos de la sección "Zip de entrega".

## Condiciones del paquete

| Condición | Umbral | Medido | Estado |
|---|---|---|---|
| Tamaño del zip | menos de 20 MB | 1,840,598 bytes (1.8 MB) | Cumple |
| Entradas de `docs/` en el zip | 0 | 0 de 203 entradas | Cumple |
| gitleaks sobre el historial | 0 hallazgos | 0 hallazgos en 152 commits (41.02 MB) | Cumple |
| gitleaks sobre el zip | 0 hallazgos | 0 hallazgos en 188 archivos (8.38 MB) | Cumple |
| pip-audit | 0 vulnerabilidades altas | 0 vulnerabilidades en 79 paquetes, con PyPI y con OSV, el 2026-11-06 en UTC (22:46 del 2026-11-05 en hora local) | Cumple |
| sha256 de las fuentes | 10 de 10 | 10 de 10 contra la lista del 2026-10-30 | Cumple |

## Condiciones de exactitud y seguridad

| Condición | Estado | Evidencia |
|---|---|---|
| 0 cifras sin herramienta | Cumple | Una corrida del set completo con Haiku 5.5 en esfuerzo medium, la configuración de la interfaz (129 turnos, `eval/reportes/2026-11-05-completo-bfe7da1.json`): 124 textos pasaron el verificador y 5 pasaron después de corregirse; sus primeras versiones traían 2 cifras y 4 citas sin evidencia. Ninguno terminó en la respuesta determinista ni en el tope de solicitudes, y ninguno tronó. Esa corrida tiene el sha256 del prompt (`8a3256eab869…`) y de las herramientas (`cb83992b000e…`) del commit base; después solo cambian `max_tokens`, que sube de 4,096 a 8,192, la plantilla, el README y el texto de los reportes. |
| 0 celulares entregados | Cumple | `python -m eval.buscar_privacidad --reporte eval/reportes/2026-11-05-completo-bfe7da1.json` da 0 coincidencias del celular y del nombre del ticket 1043 en la base, en las 4,367 líneas de `logs/turnos.jsonl`, en los prompts registrados, en las respuestas de la corrida y en los otros 132 archivos de `eval/reportes/`. En el zip, 0 de 182 archivos fuera de `datos/` los traen, también en los 8 JSON leídos sin sus escapes. El CSV de origen sí los trae, así que la búsqueda sí los ve. |
| 0 cambios en el sha256 de las fuentes | Cumple | Los 10 archivos de `datos/` y `politicas/` del zip tienen el sha256 de la lista del 2026-10-30 (tabla de abajo). `adversarial-huecos-despues.json` (2026-11-03, 15 casos con el modelo real sobre `fed6037`) trae `sin_cambios: true` y `cambiaron: []`: el hash de las fuentes es el mismo antes y después de los casos adversariales. |
| 0 secretos | Parcial | gitleaks 8.30.1 no encuentra nada en los 152 commits del historial, en el zip ni en su versión descomprimida. Falta revisar también el video y las capturas, que no se han grabado. |
| pip-audit sin hallazgos altos | Cumple | pip-audit 2.10.1 audita los 79 paquetes de `requirements.txt` con PyPI y con OSV y no encuentra vulnerabilidades. La salida está en `eval/reportes/auditoria-dependencias.txt`. |

## Las 10 fuentes

Las 10 fuentes del zip tienen el sha256 de la lista que se tomó al recibir los datos, el 2026-10-30, con `sha256sum datos/* politicas/*`. El hash agregado de las 10, `dc38f8adb443581b54f5aa50a028218f933c69f21cd4a0f57e17dd824660798e`, es el que anota `hash_fuentes` en cada turno y el que da el README con `sha256sum -t datos/* politicas/* | sha256sum`. Desde la raíz del zip descomprimido, ese comando da el mismo hash.

| Archivo | sha256 de la lista del 2026-10-30 | sha256 en el zip | Coincide |
|---|---|---|---|
| `datos/catalogo_productos.csv` | 7f54859b203463ab9135a87471ec2f9c768d0ebe75061a2346f642927cfb1901 | 7f54859b203463ab9135a87471ec2f9c768d0ebe75061a2346f642927cfb1901 | Sí |
| `datos/diccionario_datos.md` | 7f085294635475cbb49285bf7ae1e73c8b07c44ec96859dc47d64d3ece7b1d79 | 7f085294635475cbb49285bf7ae1e73c8b07c44ec96859dc47d64d3ece7b1d79 | Sí |
| `datos/inventario.csv` | c9115a9171db1cfc3b378f227935f62749ca3f0cfbac8dfb60eb2d3802a2e295 | c9115a9171db1cfc3b378f227935f62749ca3f0cfbac8dfb60eb2d3802a2e295 | Sí |
| `datos/tickets_mesa_servicio.csv` | 37030b40c8022c68337b6bbf4e0255b282263c0e86bf0c96e2163a3093983f43 | 37030b40c8022c68337b6bbf4e0255b282263c0e86bf0c96e2163a3093983f43 | Sí |
| `datos/tiendas.csv` | 3530feabb5e6325b110ead9549654da061bc38a25ac7f6641ead86c7ea985c41 | 3530feabb5e6325b110ead9549654da061bc38a25ac7f6641ead86c7ea985c41 | Sí |
| `datos/ventas.csv` | dcc7f4f2589fdadd39f2d7755c31d5125b5b0850ef08ea04f5d41139e897edbc | dcc7f4f2589fdadd39f2d7755c31d5125b5b0850ef08ea04f5d41139e897edbc | Sí |
| `politicas/faq_gerentes_de_tienda.pdf` | 661a87655818e7051274521f018109fcb95e0c59a1479994a3cd4dd6758b84ea | 661a87655818e7051274521f018109fcb95e0c59a1479994a3cd4dd6758b84ea | Sí |
| `politicas/manual_apertura_y_cierre_tienda.pdf` | 254a0011cec646a0ec40afcc9fdeb984bb8e830b7c1f2abbb720259db387e77a | 254a0011cec646a0ec40afcc9fdeb984bb8e830b7c1f2abbb720259db387e77a | Sí |
| `politicas/politica_mermas_y_caducidad.pdf` | 80f35a65d428d1d85b455787f7deb30393589886ca839d7fcbb3abb49edb3789 | 80f35a65d428d1d85b455787f7deb30393589886ca839d7fcbb3abb49edb3789 | Sí |
| `politicas/procedimiento_devoluciones.pdf` | 61babd8627fcbc664ab4dd2ab0ea928982be34c2081fabee60a115309225c0da | 61babd8627fcbc664ab4dd2ab0ea928982be34c2081fabee60a115309225c0da | Sí |

## El zip de verificación

`entrega/asistente-tiendas-22d5127.zip` tiene 203 entradas: 188 archivos y 15 carpetas, sin `docs/`, `.claude/` ni `.env`. Su sha256 es `d4c59f96bd63b8948ce90e896c4df3945fb204ad0cc18daf54405298096dc483`, y el comentario del zip guarda el hash completo del commit.

De `eval/reportes/` viajan 17 archivos, según `.gitattributes`: las corridas del set que cita el README o la plantilla (Haiku 5.5 en medium y en low, Flash-Lite y el núcleo de Haiku), los casos adversariales, la cuota de Gemini, accesibilidad, la auditoría de dependencias, este reporte y la validación del juez con su bitácora, que `eval.correr` necesita para calificar con juez. Los otros 90 siguen en el repositorio.

    mkdir -p entrega
    git -c core.autocrlf=false archive --format=zip --prefix=asistente-tiendas/ -o entrega/asistente-tiendas-22d5127.zip 22d5127

`core.autocrlf=false` hace que los archivos de texto salgan con los bytes del commit: con el `core.autocrlf=true` de Git para Windows, `git archive` pasa a CRLF `README.md` y `requirements.txt`.

## Arranque en una carpeta limpia

Windows 11 x64 con Python 3.13.15. El zip de `eab451f` se descomprimió en una carpeta vacía y `start.py` corrió sin caché de pip (`PIP_NO_CACHE_DIR=1`) y con un directorio personal vacío, así que también bajó la extensión `fts`. Nada más corría en la máquina. No se repitió con el commit base: de `eab451f` a `22d5127` no cambian `start.py`, `start.bat`, `requirements.txt` ni `.env.example`.

- Primera corrida, sin llave: sale con código 1, crea `.env` desde `.env.example` y dice qué línea escribir.
- Segunda corrida, con `ANTHROPIC_API_KEY` en el entorno y `MODELO` vacío: crea `.venv`, instala, construye la base y la interfaz responde a los 1 min 36.5 s del primer comando.
- «¿Cuántos tickets siguen sin cerrar en Tienda Valle Norte?»: 5 tickets, 2 Abierto y 3 En proceso de 72, lo mismo que da el CSV. El turno tardó 2.7 s y costó $0.0009, y la respuesta llegó a 1 min 54 s del primer comando, contando lo que tardó en abrirse el navegador.

## gitleaks

gitleaks 8.30.1, con sus reglas por omisión y sin `.gitleaks.toml` ni `.gitleaksignore` en el paquete. Los tres comandos salieron con código 0:

    gitleaks git --redact --no-banner -v .
    gitleaks dir --max-archive-depth 2 --redact --no-banner -v entrega/asistente-tiendas-22d5127.zip
    gitleaks dir --redact --no-banner -v <carpeta donde se descomprimió el zip>

El historial (`git log -p` de todas las ramas) son 152 commits y 41.02 MB. El zip y su carpeta descomprimida son 8.38 MB en 188 archivos. `--max-archive-depth` es obligatorio para revisar el zip: sin él, gitleaks lee 0 bytes de un zip y sale limpio. Si alguno de los tres comandos encuentra una llave, primero se revoca y se rota en el panel del proveedor (ver el README).

## pip-audit

pip-audit no encuentra vulnerabilidades en los 79 paquetes del lock, ni con PyPI ni con OSV. `.venv/Scripts/python -m eval.auditar_dependencias` se corrió a las 22:46 del 2026-11-05 en hora local sobre el `requirements.txt` de `22a654f`, el mismo del commit base; el reporte anota la fecha en UTC, 2026-11-06, y el README cita esa. El reporte cambió respecto al anterior solo en la hora, porque el lock no cambió (sha256 `293378a477e34c66c94dd37b1cdcea197cd566de0ce3bb370b849b59219931d3`).

## Pruebas sobre el zip descomprimido

En el paquete del commit base pasan 4,508 pruebas, 0 fallan, 0 dan error, 86 se saltan y 1 es xfail. Corrieron en la carpeta donde se descomprimió el zip, con el `.venv` del repositorio, que instala el mismo `requirements.txt` y además `requirements-dev.txt` (el del arranque limpio no se volvió a armar), sin llaves, con un proxy que no responde y repartidas en 8 procesos, con `tests/test_reintentos.py`, que mide reloj, solo al final. Tardaron 7 min 10 s. En el repositorio, el hook de pre-commit de `22d5127`, con el mismo reparto, pasó 4,585, 9 se saltaron y 1 fue xfail, en 7 min 6 s: la diferencia son las 77 de abajo.

De las 86 que se saltan, 77 suponen algo que el zip no trae y en el repositorio corren:

| Prueba | Qué necesita |
|---|---|
| `tests/test_proyeccion.py`, 44 pruebas | `eval/reportes/proyeccion-mensual.md`, los reportes que cita y el plan, en `docs/` |
| `tests/test_etiquetas_reales.py`, 12 pruebas | la corrida del 2026-11-02 y su revisión, en `eval/reportes/` |
| `tests/test_rejuzgar.py`, 4 pruebas | la corrida del 2026-11-02, en `eval/reportes/` |
| `tests/test_correr.py`, 2 pruebas | las respuestas guardadas del 2026-11-01, en `eval/reportes/` |
| `tests/test_readme.py`, 5 pruebas | un repositorio git, para saber qué trae un clon |
| `tests/test_registro.py`, 3 pruebas | un repositorio git |
| `tests/test_notas_de_criterios.py`, 3 pruebas | un commit del historial |
| `tests/test_correr.py::test_el_reporte_da_el_commit_y_el_modelo` | un repositorio git |
| `tests/test_copias_evaluacion.py::test_por_omision_las_copias_viven_en_eval_fixtures_y_git_no_las_versiona` | un repositorio git |
| `tests/test_entrega.py::test_la_lista_de_esta_prueba_es_la_que_escribe_el_handoff` | la lista original de hashes, en `docs/` |
| `tests/test_banco_preguntas.py`, 1 prueba | un documento de evaluación interno, que no viaja |

Las otras 9 se saltan también en el repositorio o dependen del equipo: 6 necesitan red (`PROBAR_CON_RED=1`), 2 necesitan red y pip-audit, que está en `requirements-dev.txt` y no en el `.venv` que arma `start.py`, y 1 prueba permisos de POSIX que Windows no tiene.

## Zip de entrega

El zip que se adjunta al correo se arma después de confirmar los archivos de esta verificación (este reporte y los demás documentos del mismo commit), con el árbol limpio:

    git status --porcelain
    mkdir -p entrega
    git -c core.autocrlf=false archive --format=zip --prefix=asistente-tiendas/ -o entrega/asistente-tiendas-$(git rev-parse --short HEAD).zip HEAD

`git status --porcelain` debe salir vacío. Después:

1. Leer el commit que guarda el zip: `.venv/Scripts/python -c "import sys, zipfile; print(zipfile.ZipFile(sys.argv[1]).comment.decode())" entrega/<zip>`.
2. Anotar en el correo ese hash y el `sha256sum` del zip.
3. Descomprimir en una carpeta vacía y comprobar las fuentes desde la raíz del paquete: `sha256sum -t datos/* politicas/* | sha256sum` debe dar el hash agregado de arriba.
4. Repetir `gitleaks dir --max-archive-depth 2` sobre el zip si entre el commit base y el de entrega cambió algo más que esos archivos.

Pendiente: grabar y revisar el video y las capturas.
