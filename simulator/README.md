# Simulador del gemelo digital

El simulador genera telemetría sintética para los contenedores del gemelo digital.

Del backend recibe la topología de la red (los contenedores con su sitio, ubicación,
tipo y nivel actual) y la configuración de cada simulación. El resto lo aporta el
propio simulador: radios censales, uso del suelo, rutas de recolección y los parámetros
de generación.

**Alcance:** está hecho para la Ciudad de Buenos Aires. Esos datos propios solo cubren
los límites de la ciudad, así que para un contenedor ubicado fuera de CABA los datos
generados son genéricos.

Se puede usar de dos formas:

1. **Servicio Docker** (uso principal): el servicio Docker `simulator` queda
   esperando hasta que se crea una simulación desde la API. Una vez conectado,
   genera un tick por vez y se puede pausar, acelerar o modificar su demanda en
   vivo.
2. **CLI**: genera un período completo de una vez. Puede exportarlo a archivos
   (CSV o Parquet) o enviarlo al backend en batch o en streaming. Sin
   `--from-backend` usa una topología sintética mínima, pensada para pruebas.

En los dos casos, las lecturas enviadas al backend se guardan en `data_level` y
actualizan el nivel actual del contenedor cuando coincide su `container_id` o su
`device_id`.

Para entender cómo se genera cada dato, ver [FUNCIONAMIENTO.md](FUNCIONAMIENTO.md).

## Ejecutar el simulador

### Local, con Docker

El simulador corre como el servicio `simulator` del perfil `map` de Docker
Compose, junto con la API y PostGIS. Sin PostGIS no puede ejecutar simulaciones.

```bash
docker compose --profile map up --build
```

Al arrancar, el worker espera a que la API termine de inicializarse y marca como
`failed` las simulaciones que hayan quedado a medias. Después queda esperando a
que se cree una desde el front o la API.

Para ver qué está haciendo:

```bash
docker compose --profile map logs -f simulator api
```

En local, `simulator/` y `datos/` están montados en el contenedor:

- los cambios en `scenario_defaults.yaml` o `zone_profiles.yaml` aplican en la
  próxima simulación, sin reiniciar nada.
- los cambios en el código requieren reiniciar el worker
  (`docker compose --profile map restart simulator`).

### En producción

En la VM, la imagen del simulador lleva el código y los datos adentro, sin
volúmenes. Cualquier cambio, incluidos los YAML, requiere reconstruir la imagen
del simulador.

## Correr una simulación

Con el worker levantado, la simulación se crea en el backend. El worker la
detecta en pocos segundos (`SIMULATOR_POLL_SECONDS`) y empieza a generar. Corre
una sola simulación a la vez.

Para crearla (requiere un usuario autenticado, ver [Primer usuario](../README.md#primer-usuario) en el README del backend):

```bash
curl -X POST http://localhost:8000/digital-twin/simulations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "speedup": 1000,
    "global_demand_multiplier": 1.0,
    "scenario": {
      "start": "2026-01-05T00:00:00",
      "end": "2026-01-12T00:00:00",
      "frequency_minutes": 60
    },
    "zone_overrides": [{"neighborhood": "Palermo", "multiplier": 1.25}]
  }'
```

Al crearla se le pasa:

- **Un escenario:** el período (`start` y `end`, o `periods`),
  `frequency_minutes`, las horas y días de recolección, y cualquier otro
  parámetro de [Configuración](#configuración). Lo que no se pase sale de
  `scenario_defaults.yaml`. Sin `end` ni `periods`, corre hasta que se la detenga.
- **Controles:** `speedup` (segundos simulados por segundo real), multiplicador
  global de demanda y multiplicadores por barrio. Se pueden cambiar con la
  simulación en curso, aplican desde el tick siguiente.

Para cambiar los controles, pausar, reanudar o detener la simulación en curso:

```text
PATCH /digital-twin/simulations/active/controls
POST  /digital-twin/simulations/active/pause
POST  /digital-twin/simulations/active/resume
POST  /digital-twin/simulations/active/stop
```

`PATCH .../controls` acepta `speedup`, `global_demand_multiplier`,
`zone_overrides` y `transition_minutes`: los cambios de demanda se aplican
gradualmente a lo largo de esos minutos simulados.

Si la velocidad pedida no se sostiene (los ticks tardan más que la espera entre
ellos), bajar `speedup` o limitar los contenedores con `SIMULATOR_CONTAINER_LIMIT`.

## Generar datos por CLI

La CLI corre un período completo de una vez y entrega el resultado al final.
Sirve para exportar datasets o para cargar un histórico en el backend. Guarda
todo en memoria antes de entregarlo, así que para períodos largos con toda la
ciudad conviene el servicio.

El escenario se pasa con `--scenario` (por defecto,
[`config/semana_normal.yaml`](config/semana_normal.yaml)). Pisa a
`scenario_defaults.yaml`: lo que no defina sale de ahí.

La topología se elige así:

- `--from-backend` usa los contenedores reales del backend
  (`--backend-url`, por defecto `http://localhost:8000`).
- `--container-limit N` toma solo los primeros N. Si no se pasa, usa el
  `container_limit` del escenario.
- Sin `--from-backend`, genera una topología sintética mínima, solo para pruebas.

### Exportar a archivos

```bash
uv run python -m simulator \
  --from-backend \
  --container-limit 100 \
  --output datos/sinteticos_backend
```

Genera `measurements`, `collections` y `alarms` en el directorio de salida. Los
archivos de una corrida anterior en ese mismo directorio se reemplazan.

- `--format parquet`: genera esos tres archivos en Parquet en vez de CSV.
- `--include-topology`: agrega `sites.csv`, `containers.csv` y `devices.csv`.
  Sin `--from-backend`, se agregan siempre.
- `--api-payloads`: agrega `api_payloads.jsonl`, con cada medición en el formato
  de la API de ingesta.

### Cargar un histórico en el backend

```bash
uv run python -m simulator \
  --from-backend \
  --delivery batch \
  --batch-size 250
```

Envía las mediciones en lotes a `POST /digital-twin/telemetry/batch`.

### Reproducir en streaming

```bash
uv run python -m simulator \
  --from-backend \
  --delivery stream \
  --stream-delay-seconds 1
```

Envía una medición por request a `POST /digital-twin/telemetry`. Es lento: sirve
para pocos contenedores.

- `--stream-delay-seconds`: espera fija entre mediciones.
- `--stream-speedup`: respeta la separación entre timestamps, comprimida. Cada
  hora simulada tarda `60 / speedup` segundos reales: con `--stream-speedup 60`,
  una hora simulada pasa en un segundo.

`Ctrl+C` corta el streaming e informa cuántas mediciones se llegaron a enviar.

En `batch` y `stream` solo se envían mediciones. Las recolecciones y las alarmas
quedan únicamente en los archivos.

## Configuración

Hay tres lugares para configurar el simulador:

| Dónde | Qué se configura | Cuándo aplica |
| --- | --- | --- |
| Escenario (en la sesión o en el YAML de la CLI) | Parámetros de una corrida | En esa corrida |
| `config/scenario_defaults.yaml` | Valores por defecto del escenario | En la próxima simulación |
| `config/zone_profiles.yaml` | Cómo genera basura cada tipo de zona | En la próxima simulación |
| Variables de entorno (`.env`) | Conexión, rendimiento y demanda base | Al reiniciar el worker |

### Parámetros del escenario

Lo que trae el escenario pisa a `scenario_defaults.yaml`. Un valor `null` no pisa
el default, y `waste_type_factors` se mezcla clave por clave. Los campos
desconocidos se ignoran.

| Parámetro | Default | Qué hace |
| --- | --- | --- |
| `start` | hoy a las 06:00 | Fecha y hora del primer tick. |
| `end` | — | Fecha y hora del último tick. Si está, define la cantidad de ticks. |
| `periods` | `0` | Cantidad de ticks, si no hay `end`. `0` significa sin fin (solo en el servicio). |
| `frequency_minutes` | `60` | Duración de cada tick. |
| `seed` | `42` | Semilla: con la misma semilla y la misma topología, el resultado se repite. |
| `reading_jitter_minutes` | `10` | Desfase de cada dispositivo alrededor de la hora del tick. `0` alinea todas las lecturas. |
| `collection_hours` | `21` a `6` | Horas del día en que salen los camiones. |
| `collection_days` | lunes a viernes | Días con recolección, como números (`0` = lunes) o nombres (`lunes`, `mon`). También se puede pasar `no_collection_days`. |
| `high_demand_multiplier` | `1.0` | Multiplicador general de llenado. |
| `overflow_stress_multiplier` | `1.0` | Multiplicador extra para forzar desbordes. |
| `waste_type_factors` | reciclables y vidrio `0.55`, húmedos `1.0` | Llenado relativo por tipo de residuo. |
| `collection_probability`, `partial_collection_probability` | `0.98`, `0.03` | Solo para el método secundario de recolección (si no se pudo armar la flota). |

Solo para la CLI: `name`, `container_limit`, `api_payloads`,
`synthetic_site_count` y `synthetic_containers_per_site`. El worker limita los
contenedores con `SIMULATOR_CONTAINER_LIMIT`.

### Perfiles de zona

`config/zone_profiles.yaml` define cuánto y en qué horario genera basura cada tipo
de zona. Tiene cuatro bloques:

- `global_demand_calibration`: nivel global de generación.
- `classification_thresholds`: umbrales para clasificar cada radio censal.
- `category_demand_weights`: multiplicadores por categoría de uso del suelo.
- `profiles`: curvas horarias y semanales por tipo de zona.

Los cambios en `global_demand_calibration` y `profiles` aplican en la próxima
simulación. Los de `classification_thresholds` y `category_demand_weights`
requieren regenerar el uso del suelo (ver [FUNCIONAMIENTO.md](FUNCIONAMIENTO.md)).

### Variables de entorno

| Variable | Default | Uso |
| --- | --- | --- |
| `BACKEND_URL` | `http://api:8000` | URL de la API que usa el worker. |
| `SIMULATOR_POLL_SECONDS` | `2` | Cada cuánto consulta la sesión y sus controles. |
| `SIMULATOR_BATCH_SIZE` | `100000` | Mediciones por request. Si supera la cantidad de contenedores, cada tick va en un solo request. |
| `SIMULATOR_CONTAINER_LIMIT` | sin límite | Máximo de contenedores que carga el worker. |
| `SIMULATOR_CONTROL_MISS_TOLERANCE` | `5` | Consultas fallidas seguidas antes de abandonar la sesión. |
| `PROMEDIO_GENERACION_BASURA_PERSONAS_24H` | `1.5` | kg de basura por persona por día. |
| `DENSIDAD_BASURA_KG_M3` | `150` | Densidad de la basura suelta, para pasar kg a % de llenado. |
| `DENSITY_STREET_BUFFER_M` | `18` | Distancia máxima, en metros, entre un contenedor y un radio censal que le aporta basura. |
| `DENSITY_MIN_DAILY_FILL_PCT_FLOOR` | `20` | Llenado diario mínimo (%) de un contenedor. |
| `DENSITY_FALLBACK_DAILY_WASTE_KG` | `150` | kg/día de un contenedor sin radio censal (fuera de CABA). |

## Logs

```bash
docker compose --profile map logs -f simulator
```

El worker registra:

- el inicio, la finalización y los fallos de cada sesión.
- la topología cargada, la calibración global y las rutas armadas.
- los controles iniciales y cada cambio de velocidad o demanda.
- la pausa, la reanudación y la detención.
- un resumen por tick: hora simulada, mediciones, recolecciones, alarmas,
  velocidad, demanda, tiempo de cómputo y espera.

Ejemplo de un tick:

```text
Simulacion 12 tick 5/169: tiempo=2026-01-05T04:00:00+00:00 mediciones=22000
recolecciones=1840 alarmas=3 speedup=1000.0x demanda=1.000 (computo=1.12s, espera=2.48s).
```

Si `espera` queda en `0.00s` tick tras tick, la velocidad pedida no se sostiene.

## Verificación

Tests del simulador:

```bash
uv run pytest -q tests/test_synthetic_data.py tests/test_simulation_scenario.py \
  tests/test_scenario_defaults.py tests/test_density_processor.py \
  tests/test_zone_classifier.py tests/test_engine_calibration.py \
  tests/test_engine_neighborhood_override.py tests/test_geographic_simulation.py \
  tests/test_street_spillover.py tests/test_secondary_collection.py \
  tests/test_truck_simulation.py tests/test_truck_routes_topology.py \
  tests/test_process_land_use.py tests/test_zone_profiles_layer.py
```
