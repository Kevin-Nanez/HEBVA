# HEBVA

HEBVA (H-e-b virtual agent) es un chat en Streamlit para que la gerente de 4 tiendas pregunte en español por las ventas, el inventario, las caducidades, los tickets de la mesa de servicio y las políticas. Las cifras salen de herramientas que consultan una base DuckDB de solo lectura, no del modelo; cada política se cita con documento, sección y página; lo que los datos no cubren, el sistema lo dice y no da cifra.

## Cómo ejecutarlo

Necesitas Python 3.12 o 3.13, red la primera vez y la llave de un proveedor del modelo. Se probó en Windows 11 x64 con Python 3.13; macOS y Linux no se probaron.

En Windows, abre `start.bat` con doble clic. En cualquier sistema, desde la raíz del repositorio (en macOS y Linux el comando suele ser `python3`):

    python start.py

La primera corrida crea `.env` desde `.env.example` y se detiene para que escribas la llave. Guárdala según esta tabla y vuelve a correr el mismo comando:

| Modelo | Qué escribir en `.env` |
|---|---|
| Claude Haiku 5.5, la ruta por omisión | `ANTHROPIC_API_KEY=<tu llave>`, con `MODELO` vacío |
| Gemini 3.5 Flash-Lite, la ruta alterna, con plan gratuito | `GOOGLE_API_KEY=<tu llave>` y `MODELO=google:gemini-3.5-flash-lite` |

Con la llave escrita, el comando crea `.venv`, instala `requirements.txt` con `--require-hashes`, construye la base y abre la interfaz en `http://127.0.0.1:8501`. Desde cero, sin caché de pip y bajando la extensión `fts` de DuckDB (47 MB), tardó de 1.5 a 7 minutos en la máquina de desarrollo, según su carga. Las corridas siguientes solo abren la interfaz. Para detenerla: Ctrl+C en la terminal. Si un paso falla, el script se detiene y dice qué hacer ([sin red](#construcción-de-la-base-y-extensión-fts)).

Lo que escribas después de `start.py` va a `streamlit run`, por ejemplo `python start.py --server.port 8600`. La interfaz solo escucha en el equipo donde corre (`address = "127.0.0.1"` en `.streamlit/config.toml`). Con `--server.address 0.0.0.0` se abre a la red, y cualquiera en esa red podrá preguntar con tu llave.

### Primera pregunta

Escribe en el cuadro de abajo y presiona Enter; por ejemplo «¿Cuánto vendió Tienda Cumbres en julio?», «¿Qué dice la política sobre la merma de los lácteos?» o «¿Cuántos tickets siguen sin cerrar en Tienda Valle Norte?». El panel «Ver cómo se obtuvo la respuesta» muestra la consulta SQL, los filtros y las filas, o el fragmento citado, y los tokens del turno. Un mensaje que sea solo «qué sabes» muestra lo que cubre el sistema sin llamar al modelo.

### La llave

La llave vive solo en `.env`, que git ignora; `.env.example` trae los nombres sin valores. Viaja solo como credencial a su proveedor, nunca dentro de un prompt. Si se filtra, primero se revoca y se rota en el panel del proveedor.

## Alcance y límites

El sistema contesta sobre 4 tiendas y 4 documentos de políticas: ventas del 1 de junio de 2026 al 31 de agosto de 2026, tickets del 2 de junio de 2026 al 31 de agosto de 2026 e inventario en una sola foto, la del 1 de septiembre de 2026. Los datos son sintéticos. Una fecha relativa («hoy», «ayer») se contesta como si hoy fuera el 1 de septiembre de 2026, y la respuesta lo dice.

Los datos no permiten calcular el ticket promedio, la venta por hora, la merma real, la meta de venta, la primera respuesta de un ticket ni el inventario de otra fecha: el sistema da la razón, sin cifra. Tampoco contesta con datos posteriores al corte de cada fuente ni sobre GUI-CAL-002, PRO-ECO-009 y PRO-LOG-003, que los documentos citan y no vienen.

Cada turno tiene topes: 4 solicitudes al modelo, 12 ejecuciones de herramientas, 60 s y 1,500 caracteres de pregunta. Al llegar a uno, la respuesta sale con un texto fijo y sin cifras. Un mensaje con varias preguntas se contesta por separado, hasta 3. Las brechas de accesibilidad, casi todas de Streamlit, están en `eval/reportes/accesibilidad.md`.

## Modelos y costo

Haiku 5.5 es la ruta medida con la versión actual, en esfuerzo medium como la app: 98 de 100 prompts con reglas y 96 con juez en la última corrida. «Con reglas» es lo que decide el código (estado, cifras con tolerancia de ±0.01, citas, abstención y fugas); «con juez» suma otra llamada al modelo para los criterios en prosa.

| | Claude Haiku 5.5 | Gemini 3.5 Flash-Lite |
|---|---|---|
| `MODELO` en `.env` | vacío, equivale a `anthropic:claude-haiku-5-5` | `google:gemini-3.5-flash-lite` |
| Llave | `ANTHROPIC_API_KEY` (de pago) | `GOOGLE_API_KEY` (sirve la del plan gratuito) |
| Set de 100 prompts | 98 con reglas y 96 con juez, 1 corrida | 64 y 75 con reglas, sin juez, 2 corridas de una versión anterior |
| Latencia del set (p50 / p90) | 4.19 / 8.70 s | 8.12 / 58.66 s |
| Costo por pregunta | $0.00093 con caché caliente y $0.00154 con fría, en esfuerzo medium | $0 en el plan gratuito |
| Cuota | Sin tope diario; 4,000 solicitudes por minuto en el nivel Start | 15 solicitudes por minuto y 500 por día, observadas el 2026-11-03 |

El costo por pregunta es el promedio del set con Haiku 5.5 en esfuerzo medium, el que usa la app; la cuenta y la comparación con low están en `plantilla_decisiones.md`. Los precios están en `config/precios.yaml`. Al pasar la cuota diaria de Google, cada pregunta termina con un aviso sin cifras hasta que la cuota se repone.

## Evaluación del sistema

`python -m eval.correr` mide el acierto por el mismo camino que la interfaz y gasta la llave de `.env`. Cada corrida escribe su reporte en `eval/reportes/` y deja una línea por turno en `logs/turnos.jsonl`. Desde la raíz del repositorio, con el entorno virtual activo:

    python -m eval.correr
    python -m eval.correr --completo

| Comando | Qué corre, en 2 corridas | Con Haiku 5.5 |
|---|---|---|
| `python -m eval.correr` | Núcleo: 30 prompts, 35 turnos por corrida | 7.5 minutos y $0.08 |
| `python -m eval.correr --completo` | Set: 100 prompts, 129 turnos por corrida | 31.9 minutos y $0.34 |

`--corridas N` cambia el número de corridas, `--modelo ID` el modelo, `--reanudar` sigue donde se quedó sin repetir llamadas y `python -m eval.correr --help` lista las demás banderas.

## Pruebas

Construye la base una vez con red antes de correr `pytest`:

    python -m agente.construir
    python -m pytest -q

Sin la extensión `fts` (47 MB, queda en `~/.duckdb/extensions/`), `pytest` salta unas 700 pruebas sin decir que falta. La suite tiene unas 4,500 pruebas, tarda de 15 a 30 minutos en un proceso según la carga de la máquina y corre sin red y sin llaves, con modelos simulados. El paquete de entrega no trae `.git`, `docs/` ni los reportes que solo sirvieron durante el desarrollo: ahí 77 pruebas se saltan con su motivo.

Para comprobar que las fuentes no cambiaron, con `sha256sum` (viene con Git Bash en Windows):

    sha256sum -t datos/* politicas/* | sha256sum

El comando imprime `dc38f8adb443581b54f5aa50a028218f933c69f21cd4a0f57e17dd824660798e` y un guion que cambia según el sistema.

## Construcción de la base y extensión fts

`python -m agente.construir` lee `datos/`, `politicas/` y `politicas_md/`, y deja la base en `build/tienda.duckdb`. La interfaz la reconstruye sola si cambia una fuente.

La primera construcción necesita red: `INSTALL fts` baja la extensión de `extensions.duckdb.org` a `~/.duckdb/extensions/`, porque no viene dentro del paquete `duckdb`. Sin red, se detiene con `ExtensionNoDisponible` y dice qué hacer. Detrás de un proxy, define `HTTP_PROXY`. En un equipo sin red, copia a la misma ruta la carpeta `~/.duckdb/extensions/v2.0.0/windows_amd64/` de un equipo con la misma plataforma y la misma versión de DuckDB. Hazlo antes de las [pruebas](#pruebas).

## Datos y seguridad

### Inventario de datos personales

De los 5 CSV de `datos/`, solo el texto libre de los tickets (`titulo` y `descripcion`) puede traer datos personales, y de los 285 tickets solo el 1043 los trae: el nombre y el celular de quien lo levantó, con impacto Moderado según NIST SP 800-122. Los dos se enmascaran al cargar, antes de llegar a la base y a cualquier prompt (`<PERSONA_1>`, `<TELEFONO_1>`). Un nombre en minúsculas o sin una frase que lo presente no se detecta. Lo comprueba `python -m pytest -q tests/test_privacidad.py`.

### Datos que salen al proveedor del modelo

En cada turno salen la pregunta, las últimas 6 vueltas de la sesión, el prompt de sistema, la definición de las cinco herramientas y lo que ellas devuelven, hasta 50 filas. No salen los CSV ni los PDF completos, la consulta SQL, `logs/` ni el nombre y el celular del 1043. Un nombre que la gerente escriba en la pregunta sí sale. Streamlit no envía estadísticas ni pide un correo (`gatherUsageStats = false` y `showEmailPrompt = false`).

| Ruta | ¿Usa prompts y respuestas para mejorar sus productos? | Fuente, consultada el 2026-11-01 |
|---|---|---|
| API de Anthropic con llave comercial | No | [Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms), secciones B y C |
| Gemini API sin facturación (la cuota gratuita) | Sí, y personas revisoras pueden leerlos | [Gemini API Additional Terms of Service](https://ai.google.dev/gemini-api/terms), "Unpaid Services" |
| Gemini API con facturación activa | No | La misma página, "Paid Services" |

### Qué puede hacer un ticket hostil

Un ticket hostil no puede cambiar datos ni dejar una cifra suya como si viniera de una herramienta: el modelo no manda SQL, la base se abre en solo lectura y el verificador rechaza un número que solo venga en el texto de un ticket. Sí puede hacer que el modelo repita una afirmación falsa sin cifras; contra eso solo está el modelo. Con Haiku 5.5, en 24 casos adversariales, ninguna cifra falsa ni clave del ticket llegó a la respuesta; Gemini 3.5 Flash-Lite no se midió. Un CSV o un PDF alterado cambia las respuestas sin aviso, y cada turno anota el sha256 de las fuentes en `logs/turnos.jsonl`.

### Dependencias

`requirements.txt` fija 79 paquetes con sus hashes, para Python 3.12 y 3.13. La auditoría de pip-audit del 2026-11-06 da 0 vulnerabilidades (`eval/reportes/auditoria-dependencias.txt`). Para repetirla hace falta red; en macOS y Linux el intérprete es `.venv/bin/python`:

    .venv/Scripts/python -m pip install --require-hashes -r requirements-dev.txt
    .venv/Scripts/python -m eval.auditar_dependencias

## Mapa del repositorio

| Ruta | Qué hay |
|---|---|
| `app.py` | La interfaz de Streamlit |
| `start.py`, `start.bat` | El arranque de un solo comando |
| `agente/` | El turno (`agente.py`), la construcción de la base (`construir.py`), el verificador de cifras y citas (`verificacion.py`), el registro (`registro.py`) y el prompt (`prompt_sistema.md`) |
| `agente/herramientas/` | Las cinco herramientas de solo lectura: `ventas`, `inventario`, `caducidades`, `tickets` y `buscar_politicas` |
| `config/` | Las definiciones (`supuestos.yaml`), los alias, los temas de tickets, los choques entre fuentes y los precios |
| `datos/`, `politicas/` | Los 5 CSV, el diccionario de datos y los 4 PDF del brief, sin cambios |
| `politicas_md/` | La copia revisada de cada PDF, que la construcción compara con lo que extrae |
| `eval/` | El set de preguntas (`preguntas.yaml`), el comando (`correr.py`), el juez (`juez.py`), la bitácora del asistente de código (`bitacora-asistente.md`) y los reportes (`eval/reportes/`; el paquete de entrega trae solo los que citan este README y la plantilla) |
| `tests/` | La suite de pytest |
| `build/`, `logs/` | La base y el registro de turnos; git los ignora |
| `docs/` | El plan de implementación; el paquete de entrega sale sin esta carpeta |
| `plantilla_decisiones.md` | Las decisiones, los descartes y el costo por interacción que pide el brief |
| `BRIEF.md` | El brief |
