# WaiotFiuba Backend

Gemelo digital de contenedores - Ciudad de Buenos Aires.

## Requisitos

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Docker y docker-compose (para ejecución con contenedores)

## Sin Docker

### Instalación

```bash
uv sync
```

### Ejecutar el servidor

```bash
uv run uvicorn app.main:app --reload
```

La API queda disponible en `http://localhost:8000`.
La documentación interactiva en `http://localhost:8000/docs`.

### Tests

```bash
uv run --with pytest pytest
```

### Validación local

```bash
uv run --with pre-commit pre-commit run --all-files
```

### Linter rápido

```bash
uv run --with ruff ruff check .
```

### Generador de datos sintéticos

Con el backend levantado, el generador puede leer los contenedores desde el endpoint
`/map/containers/` y generar archivos offline:

```bash
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --backend-url http://localhost:8000 \
  --container-limit 100 \
  --scenario app/digital_twin/synthetic_data/config/semana_normal.yaml \
  --output datos/sinteticos_backend \
  --api-payloads
```

Para una prueba acotada:

```bash
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --container-limit 20 \
  --output datos/sinteticos_backend
```

Si se omite `--from-backend`, el módulo genera una topología sintética mínima para
tests o desarrollo local.

El escenario base es `semana_normal.yaml`. En esta primera iteración las fallas raras
como incendio, sensor trabado o pérdida de señal quedan desactivadas por defecto.
`reading_jitter_minutes` permite que cada sensor reporte algunos minutos antes o
después de la hora base, evitando timestamps idénticos para todos los dispositivos.
También se puede definir `end` para simular rangos históricos; si está presente,
`periods` se calcula desde `start`, `end` y `frequency_minutes`.

Cuando se usa `--from-backend`, solo se exportan mediciones, recolecciones, alarmas y
payloads. Para guardar también un snapshot de `sites`, `containers` y `devices`:

```bash
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --include-topology \
  --output datos/sinteticos_backend
```

Por defecto, `measurements`, `collections` y `alarms` se exportan en CSV. Para generar
Parquet:

```bash
uv run python -m app.digital_twin.synthetic_data \
  --from-backend \
  --format parquet \
  --output datos/sinteticos_backend
```

### Variables de entorno

```bash
cp .env.example .env
# editá .env con tus valores
```

## Con Docker

### Requisitos

- Docker
- docker-compose

### Levantar toda la app sin PostGIS

```bash
docker-compose up --build
```

La API queda disponible en `http://localhost:8000`.

Para levantar la API junto con PostGIS y el mapa:

```bash
docker compose --profile map up --build
```

### Qué hace Docker Compose

- construye la imagen `api` desde el backend
- monta los datos en `./db/datos` y expone `postgis` en el puerto `5432`
- levanta una base PostGIS llamada `waiot_map`
- importa automáticamente los archivos GeoJSON (`barrios`, `calles`, `comunas`, `manzanas`, `parcelas`) desde `db/datos` a la base de datos al inicializar por primera vez el contenedor.
- corre el seed con los datos del contenedor si se activa el perfil y los datos no estan ya insertados

### Inicialización e Importación de Datos GeoJSON

La carga inicial de archivos GeoJSON se realiza automáticamente cuando el contenedor de PostGIS se inicializa desde cero (volumen de base de datos vacío).

Si necesitas reiniciar la base de datos completa y recargar todos los datos GeoJSON:

```bash
docker-compose --profile map down -v
docker-compose --profile map up --build
```

#### Importación Manual sin Borrar la Base de Datos

Si has modificado o agregado archivos GeoJSON en `db/datos` y quieres cargarlos o sobreescribirlos sin destruir los datos existentes en la base de datos, podés ejecutar el script de importación directamente dentro del contenedor:

```bash
# Importar solo archivos nuevos (las tablas existentes se omiten)
docker-compose exec postgis bash /docker-entrypoint-initdb.d/20_import_geojson.sh

# Forzar la sobreescritura de todas las tablas GeoJSON
docker-compose exec postgis bash /docker-entrypoint-initdb.d/20_import_geojson.sh --force
```

### Variables de Entorno

```bash
cp .env.example .env
# editá .env con tus valores
```
El compose levanta el `.env` automáticamente.

## Notas

- El primer arranque puede tardar un poco mientras se inicializa la base y se cargan los datos.
