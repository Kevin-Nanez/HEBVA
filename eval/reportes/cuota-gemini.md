# Cuota de Gemini observada con una llave de plan gratuito

Google no publica estas cifras en su página de límites: remite a la consola de cada proyecto. Lo que sigue es lo que dijo el servicio en el cuerpo de sus 429, con la llave de este proyecto, el 2026-11-03. Son valores de ese proyecto y de ese día, no un compromiso del proveedor.

| Modelo | Qué límite | Valor | Primera vez | Veces registradas |
|---|---|---|---|---|
| gemini-3.5-flash-lite | Generate content requests per minute limit exceeded on Free Tier | 15 | 2026-11-03 14:27:58 | 4 |
| gemini-3.5-flash-lite | Generate content requests per day limit exceeded on Free Tier | 500 | 2026-11-03 14:54:22 | 13 |
| gemini-3.8-flash | Generate content requests per day limit exceeded on Free Tier | 20 | 2026-11-03 14:04:55 | 17 |

Cada cifra sale del texto `limit: N, model: M, description: D` del 429; el campo `quotaMetric` es `generate_content_free_tier_requests`. Las horas son de la máquina donde corrió, en horario del centro de México. Un 429 por día pidió reintentar en unas 11 a 12 horas (`retryDelay`, 43005 s en la primera lectura de Gemini 3.8 Flash), y uno por minuto, en menos de 60 s.
