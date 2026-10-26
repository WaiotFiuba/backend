# WaiotFiuba Backend

API del prototipo Waiot Intelligence: un gemelo digital de la red de contenedores
de la Ciudad de Buenos Aires con los módulos de proyección del llenado y de
redistribución de contenedores.

El backend:

- construye la red de contenedores y sitios a partir de datos abiertos de CABA,
  sobre PostgreSQL con PostGIS.
- recibe telemetría de llenado y la guarda como historial.
- proyecta el llenado de cada sitio y recomienda redistribución de contenedores
  entre sitios, con un entorno What-If para explorar sus efectos.
- gestiona las simulaciones que ejecuta el simulador.

Documentación:

- [docs/DATOS.md](docs/DATOS.md): cómo se arma la red y cómo se guarda la telemetría.
- [docs/PROYECCION.md](docs/PROYECCION.md): módulo de proyección.
- [docs/REDISTRIBUCION.md](docs/REDISTRIBUCION.md): módulo de redistribución.
- [simulator/README.md](simulator/README.md): simulador de telemetría.

## Ejecutar el backend

### Con Docker

Requisitos: Docker, Docker Compose y Python 3.13+ (o `uv`) para descargar los
datos semilla.

Desde la carpeta `backend`:

```bash
cp .env.example .env
python scripts/download_seed_data.py
docker compose --profile map up --build
```

La API queda en `http://localhost:8000`, y su documentación interactiva en
`http://localhost:8000/docs`.

Para detener los servicios:

```bash
docker compose --profile map down
```

El perfil `map` levanta tres servicios:

- `postgis`: la base de datos, PostgreSQL con PostGIS. Al crearse importa las
  capas geográficas (calles y barrios).
- `api`: aplica las migraciones, carga los datos semilla y levanta FastAPI.
- `simulator`: el worker del simulador (ver [simulator/README.md](simulator/README.md)).

Usuarios, red de contenedores, telemetría y simulaciones viven en la misma base,
así que sin el perfil `map` la API arranca pero no puede atender pedidos.

El primer arranque tarda: Docker construye las imágenes, PostGIS importa las
capas, Alembic crea el esquema y el seed carga los contenedores y arma los sitios.
Los arranques siguientes reutilizan lo que ya está en la base.

### Datos semilla

Algunos archivos de `datos/` no están en git: se descargan del release público
`seed-data-v1`, y el script verifica el `sha256` de cada uno:

```bash
python scripts/download_seed_data.py            # descarga lo que falta
python scripts/download_seed_data.py --force    # vuelve a descargar todo
```

Quedan en `datos/digital_twin/` (red de contenedores, calles, barrios y
restricciones) y en `datos/simulator/` (datos del simulador). De dónde sale cada
uno y cómo se procesa está en [docs/DATOS.md](docs/DATOS.md).

### Sin Docker

No se recomienda: habría que instalar y preparar a mano todo lo que resuelve
Docker (PostgreSQL con PostGIS, GDAL, la importación de las capas geográficas y
la configuración de la conexión).

### Primer usuario

Casi todos los endpoints requieren un usuario. Desde `/docs`: crearlo con
`POST /auth/register` y después usar el botón **Authorize** con su email y
contraseña.

Con `curl`:

```bash
# Crear el usuario (la contraseña debe tener al menos 8 caracteres)
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "name": "Admin", "password": "cambiar123"}'

# Obtener el token (el email va como username)
curl -X POST http://localhost:8000/auth/login \
  -d "username=admin@example.com&password=cambiar123"
```

El login devuelve un `access_token`, que se envía en cada pedido:

```bash
TOKEN="<access_token>"
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/users/me
```

El `access_token` vence a los `ACCESS_TOKEN_EXPIRE_MINUTES` (60 por defecto). Para
renovarlo sin volver a loguearse se usa el `refresh_token` con
`POST /auth/refresh`.

## Configuración

La API se configura con variables de entorno o con el archivo `.env` (ver
`.env.example`). Las del simulador están en [simulator/README.md](simulator/README.md).

| Variable | Uso |
| --- | --- |
| `MAP_DATABASE_URL` | Conexión a la base. |
| `MAP_DB_POOL_SIZE`, `MAP_DB_MAX_OVERFLOW` | Pool de conexiones a la base. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_PORT` | Credenciales con las que Docker crea la base y con las que se importan las capas geográficas. |
| `SECRET_KEY` | Clave para firmar los tokens. **Cambiarla fuera de desarrollo.** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Vigencia del `access_token`. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Vigencia del `refresh_token`. |
| `CORS_ALLOWED_ORIGINS` | Orígenes permitidos, separados por coma. Con `*` no se aceptan credenciales. |
| `LEVEL_THRESHOLD_NORMAL`, `LEVEL_THRESHOLD_HIGH`, `LEVEL_THRESHOLD_CRITICAL` | Umbrales de llenado (%) que usan el mapa, los KPIs, la proyección y la redistribución. |
| `DATA_LEVEL_RETENTION_PERIODS` | Períodos de telemetría que se conservan (cada uno ocupa ~17 MB). `0` = sin límite. |
| `DATA_LEVEL_SIZE_WARNING_GB` | Tamaño de `data_level` a partir del cual se loguea un aviso. |
| `CHRONOS_FORECAST_QUANTILE` | Cuantil de Chronos-2 que se toma como nivel previsto (entre 0,1 y 0,9). |
| `SIMULATION_CLAIM_TIMEOUT_SECONDS` | Tiempo para que un simulador tome una simulación nueva antes de marcarla como fallida. |
| `SIMULATION_STARTUP_TIMEOUT_MINUTES` | Tiempo para que el simulador termine de preparar la simulación. |
| `LOG_LEVEL` | Nivel de logs. |

Las variables se leen al arrancar la API: un cambio requiere reiniciarla.

## API

La referencia completa de endpoints, con sus parámetros y respuestas, está en
`/docs` (OpenAPI). Los grupos principales son:

| Prefijo | Qué hace | Requiere token |
| --- | --- | --- |
| `/auth` | Registro, login y renovación del token. | No |
| `/users` | Datos del usuario logueado. | Sí |
| `/map/config` | Configuración del mapa: centro, límites, umbrales y tipos de residuo. | No |
| `/map/containers`, `/map/sites` | Contenedores y sitios, con su nivel y su historial. | Sí |
| `/map/kpis` | Indicadores de la red. | Sí |
| `/map/site-projections` | Proyección del llenado por sitio y su evaluación. | Sí |
| `/map/optimization` | Planes de redistribución y entorno What-If. | Sí |
| `/map/zone-profiles` | Capa de perfiles de zona del simulador. | Sí |
| `/digital-twin/telemetry` | Ingesta de telemetría. | No |
| `/digital-twin/simulations`, `/configurations`, `/demand` | Simulaciones y sus configuraciones guardadas. | Sí |
| `/digital-twin/worker/*` | Endpoints que usa el worker del simulador. | No |

## Comandos útiles

```bash
docker compose --profile map logs -f api           # ver los logs de la API
docker compose --profile map down -v               # detener y borrar la base
docker compose --profile map up --build            # volver a levantar (recrea la base después de -v)
```

Los comandos de `app.commands` se corren dentro del servicio `api`:

```bash
docker compose --profile map exec api uv run python -m app.commands.<comando>
```

| Comando | Qué hace | Cuándo usarlo |
| --- | --- | --- |
| `seed` | Carga tipos de residuo, tipos de contenedor y contenedores, arma los sitios y carga la población por barrio. | Lo corre la API al arrancar. |
| `seed --recluster` | Borra los sitios y los vuelve a armar a partir de los contenedores. | Después de cambiar los contenedores o el criterio de agrupamiento. |
| `import_neighborhood_demographics --population-csv <archivo>` | Carga o actualiza la población por barrio. Acepta `--year` y `--source` (por defecto, `2010` y `Censo 2010`). | Al actualizar los datos de población. |

Los scripts de `scripts/` no están en la imagen de la API: se corren desde la
máquina local, con `uv`.

| Script | Qué hace |
| --- | --- |
| `scripts/download_seed_data.py` | Descarga los datos semilla (ver [Datos semilla](#datos-semilla)). |
| `scripts/generar_restricciones_contenedores.py` | Regenera `datos/digital_twin/restricciones_contenedores.csv`, la capacidad máxima de contenedores por tramo de calle, a partir de los datasets de estacionamiento, ciclovías, garajes, carga y descarga y mobiliario urbano. |

Si se regenera un archivo de `datos/` que se distribuye en el release, hay que
publicarlo de nuevo y actualizar su `sha256` en `scripts/seed_data_manifest.json`.

## Tests y calidad de código

```bash
uv run pytest                                         # tests
uv run --with pre-commit pre-commit run --all-files   # formato y lint (ruff)
```

pre-commit corre `ruff`, `ruff-format` y controles básicos: espacios al final de
línea, fin de archivo, YAML válido y archivos de más de 15 MB.

## Integración y despliegue continuo

| Workflow | Cuándo corre | Qué hace |
| --- | --- | --- |
| `tests.yml` | Push a `dev` y pull requests a `main` que tocan archivos `.py` | Corre `pytest`. |
| `pre-commit.yml` | Push a `dev` y pull requests a `main` | Corre los hooks de pre-commit. |
| `release-production.yml` | Push a `main`, o a mano desde Actions | Corre los tests y, si pasan, despliega en dos lugares, de forma independiente. |

El despliegue de `release-production.yml` tiene dos destinos:

- **Render + Supabase:** aplica las migraciones sobre la base de Supabase y
  dispara el deploy de Render.
- **VM de Oracle Cloud:** se conecta por SSH y ejecuta el script de deploy de la
  VM, que actualiza el código a `origin/main` y reconstruye los servicios con
  `docker compose`. Después espera hasta 5 minutos a que la API responda.

Los secretos que usa (URL de la base, deploy hook de Render, clave SSH, host)
están configurados en el repositorio de GitHub. La configuración de cada destino
está en [docs/HOSTING.md](docs/HOSTING.md).
