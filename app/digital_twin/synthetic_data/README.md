# Simulador del gemelo digital

Este módulo genera telemetría sintética de contenedores y permite utilizarla de dos
formas:

1. Como generador CLI para exportar archivos o reproducir un histórico contra el
   backend.
2. Como servicio Docker `simulator`, que ejecuta una simulación incremental controlada
   mediante la API.

Las lecturas enviadas al backend se guardan en `data_level`, con una forma compatible
con la base original, y actualizan el estado actual del contenedor cuando coincide su
`container_id` o `device_id`.

## Componentes principales

- `simulation/engine.py`: conserva el estado y genera un tick temporal.
- `simulation/scenario.py`: carga y valida escenarios YAML o JSON.
- `worker.py`: ejecuta la única sesión activa y aplica controles en vivo.
- `generators/`: demanda, sensores, recolecciones, anomalías y topología sintética.
- `loaders/`: carga contenedores reales desde el backend.
- `transport/`: envío HTTP individual o por lotes.
- `exporters/`: exportación CSV, Parquet y payloads de API.
- `validation/`: validaciones del resultado generado.
- `config/semana_normal.yaml`: escenario base documentado.

## Requisitos

Para utilizar contenedores reales, demanda geográfica, ingesta de telemetría o el
servicio controlable, se debe levantar el perfil `map`:

```bash
docker compose --profile map up --build
```

Este perfil levanta `api`, `postgis` y `simulator`. El worker permanece ocioso hasta
que se crea una sesión desde la API.

Para observar su funcionamiento:

```bash
docker compose --profile map logs -f simulator api
```

## Generador CLI

### Generar archivos con contenedores del backend

```bash
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --backend-url http://localhost:8000 \
  --container-limit 100 \
  --scenario app/digital_twin/synthetic_data/config/semana_normal.yaml \
  --output datos/sinteticos_backend \
  --api-payloads
```

Si se omite `--from-backend`, se genera una topología sintética mínima para tests o
desarrollo local.

Por defecto se exportan `measurements`, `collections` y `alarms` en CSV. Opciones
adicionales:

```bash
# Incluir snapshot de sitios, contenedores y dispositivos
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --include-topology \
  --output datos/sinteticos_backend

# Exportar series temporales en Parquet
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --format parquet \
  --output datos/sinteticos_backend
```

### Inyectar un histórico en batch

```bash
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --backend-url http://localhost:8000 \
  --delivery batch \
  --batch-size 250
```

Utiliza `POST /digital-twin/telemetry/batch`.

### Reproducir telemetría en streaming

```bash
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --backend-url http://localhost:8000 \
  --delivery stream \
  --stream-delay-seconds 1
```

Utiliza `POST /digital-twin/telemetry` y envía una medición por request.

`--stream-delay-seconds 1` espera un segundo real entre mediciones.
`--stream-speedup` conserva las diferencias entre timestamps, pero comprime el tiempo
simulado. Por ejemplo:

```bash
# Simular una hora en un minuto
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --backend-url http://localhost:8000 \
  --delivery stream \
  --stream-speedup 60
```

Al interrumpir el streaming con `Ctrl+C`, el programa cierra elegantemente e informa
cuántas mediciones alcanzó a enviar.

## Escenarios

El escenario base es
[`config/semana_normal.yaml`](config/semana_normal.yaml). Sus campos principales son:

- `seed`: semilla para obtener resultados reproducibles.
- `start`, `end`, `periods`, `frequency_minutes`: reloj y duración simulada.
- `container_limit`: máximo de contenedores reales cargados desde el backend.
- `reading_jitter_minutes`: variación del timestamp de cada dispositivo.
- `collection_hours` y probabilidades de recolección.
- `high_demand_multiplier`: demanda general propia del escenario.
- `overflow_stress_multiplier`: presión adicional para escenarios de desborde.
- `waste_type_factors`: demanda relativa por tipo de residuo.
- probabilidades de anomalías y fallas.

Si se define `end`, la cantidad de períodos se calcula usando `start`, `end` y
`frequency_minutes`.

## Demanda geográfica

El seed automático del perfil `map` busca:

- `db/datos/barrios.geojson` o `barrios.json`, con propiedades `barrio` y `comuna`.
- `db/datos/poblacion_barrios.csv`, con columnas `barrio,poblacion`.

Los datos se cargan o actualizan automáticamente junto con los contenedores. Si faltan
archivos, el seed muestra un warning y continúa levantando la aplicación.

La carga también puede ejecutarse manualmente:

```bash
docker compose --profile map exec api uv run python \
  -m app.commands.import_neighborhood_demographics \
  --geojson db/datos/barrios.geojson \
  --population-csv db/datos/poblacion_barrios.csv
```

El importador calcula área, densidad y factor base contra la mediana de CABA. Los
barrios sin correspondencia entre archivos se reportan como `unmatched`. Por defecto
se registra año `2010` y fuente `Censo 2010`; pueden modificarse con `--year` y
`--source`.

Cada contenedor se asocia espacialmente a un barrio mediante `ST_Covers`. Si no existe
coincidencia, utiliza factor neutral `1.0`.

La demanda final combina:

```text
factor de densidad
x multiplicador global
x multiplicador del barrio
x factor del tipo de residuo
x factor horario
x factor semanal
x ruido
```

El factor de densidad se calcula como:

```text
clamp(1 + 0.5 * (densidad / mediana_densidad - 1), 0.5, 2.0)
```

## Servicio controlable

Existe una única simulación activa. Las sesiones finalizadas quedan persistidas para
histórico y métricas, pero no pueden modificarse.

Estados posibles:

```text
pending, running, paused, stopping, completed, failed
```

Si el worker reinicia durante una ejecución, la sesión se marca como `failed`; esta
versión no reanuda desde checkpoints.

Todos los endpoints de sesión y demanda requieren el access token de un usuario
registrado.

### Crear una simulación

```bash
curl -X POST http://localhost:8000/digital-twin/simulations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "speedup": 60,
    "global_demand_multiplier": 1.0,
    "transition_minutes": 60,
    "scenario": {
      "start": "2026-01-05T00:00:00",
      "end": "2026-01-05T06:00:00",
      "frequency_minutes": 60,
      "container_limit": 100
    },
    "zone_overrides": [
      {"neighborhood": "Palermo", "multiplier": 1.25}
    ]
  }'
```

`speedup: 60` significa que 60 segundos simulados transcurren por cada segundo real.

### Consultar estado y demanda zonal

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/digital-twin/simulations/active

curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/digital-twin/demand/zones
```

### Cambiar controles en vivo

```bash
curl -X PATCH http://localhost:8000/digital-twin/simulations/active/controls \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "speedup": 120,
    "global_demand_multiplier": 1.4,
    "transition_minutes": 30,
    "zone_overrides": [
      {"neighborhood": "Palermo", "multiplier": 1.8}
    ]
  }'
```

Los cambios de demanda se interpolan linealmente durante `transition_minutes`
simulados. Los cambios de velocidad se aplican desde la siguiente espera del worker.

### Pausar, reanudar y detener

```text
POST /digital-twin/simulations/active/pause
POST /digital-twin/simulations/active/resume
POST /digital-twin/simulations/active/stop
```

Ejemplo:

```bash
curl -X POST http://localhost:8000/digital-twin/simulations/active/pause \
  -H "Authorization: Bearer $TOKEN"
```

La pausa se aplica entre lotes de telemetría. Puede finalizar el request que ya estaba
en vuelo, pero no inicia nuevos lotes hasta reanudar. El reloj simulado tampoco avanza.

Al detener, el worker cierra la sesión elegantemente y registra las métricas finales.

## Endpoints

| Método | Ruta | Descripción |
| --- | --- | --- |
| `POST` | `/digital-twin/telemetry` | Ingesta de una medición |
| `POST` | `/digital-twin/telemetry/batch` | Ingesta de un lote |
| `POST` | `/digital-twin/simulations` | Crear e iniciar una sesión |
| `GET` | `/digital-twin/simulations/active` | Consultar sesión activa |
| `PATCH` | `/digital-twin/simulations/active/controls` | Cambiar controles |
| `POST` | `/digital-twin/simulations/active/pause` | Pausar |
| `POST` | `/digital-twin/simulations/active/resume` | Reanudar |
| `POST` | `/digital-twin/simulations/active/stop` | Detener |
| `GET` | `/digital-twin/demand/zones` | Consultar demanda por barrio |

La especificación interactiva está disponible en `http://localhost:8000/docs`.

## Variables de entorno

| Variable | Default | Uso |
| --- | --- | --- |
| `SIMULATOR_BACKEND_URL` | `http://api:8000` | URL interna utilizada por el worker |
| `SIMULATOR_POLL_SECONDS` | `2` | Frecuencia de consulta de sesión y controles |
| `SIMULATOR_BATCH_SIZE` | `250` | Mediciones enviadas por request |
| `ENABLE_MAP_DB` | `false` | Habilita la base PostGIS |
| `MAP_DATABASE_URL` | ver `.env.example` | Conexión a PostGIS |

## Logs

La API registra cada lote recibido, incluyendo mediciones aceptadas, actualizadas y no
encontradas. El simulador registra:

- inicio, finalización y fallos de sesión;
- controles iniciales y cambios de velocidad o demanda;
- pausa, reanudación y detención;
- resumen por tick: tiempo, mediciones, lotes, recolecciones, alarmas y controles.

```bash
docker compose --profile map logs -f simulator api
```

## Verificación

```bash
uv run --with pytest pytest -q -s
uv run --with ruff ruff check app tests alembic
```

Para verificar filas ingresadas:

```bash
docker compose --profile map exec postgis psql -U waiot -d waiot_map \
  -c "select id, reading_date, container_id, container_current_level, zone_name
      from data_level order by id desc limit 20;"
```

Para verificar sesiones:

```bash
docker compose --profile map exec postgis psql -U waiot -d waiot_map \
  -c "select id, status, current_period, total_periods, measurements_sent
      from simulation_sessions order by id desc limit 10;"
```

## Consideraciones operativas

- El servicio necesita el perfil `map`; sin PostGIS no puede ejecutar sesiones.
- Una sesión `paused` sigue siendo activa e impide crear otra.
- Reiniciar el worker durante una sesión activa la marca como `failed`.
- La pausa no cancela el request HTTP que ya está procesando la API.
- Los contenedores no asociados a un barrio usan demanda geográfica neutral.
