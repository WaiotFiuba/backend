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

### Variables de entorno

```bash
cp .env.example .env
# editá .env con tus valores
```

## Con Docker

### Requisitos

- Docker
- docker-compose

### Levantar toda la app

```bash
docker-compose up --build
```

La API queda disponible en `http://localhost:8000`.

### Qué hace Docker Compose

- construye la imagen `api` desde el backend
- construye la imagen `postgis` desde `Dockerfile.postgis`
- monta los scripts de inicialización de `db/init` en `/docker-entrypoint-initdb.d`
- monta los datos en `./db/datos` y expone `postgis` en el puerto `5432`
- levanta una base PostGIS llamada `waiot_map`

### Inicialización de la base PostGIS

Al arrancar por primera vez, PostGIS ejecuta:

1. `db/init/01_setup.sql`
   - crea la extensión `postgis`
   - crea la tabla `contenedores`
   - crea índices espaciales
2. `db/init/02_load_data.sh`
   - ejecuta `db/init/load_contenedores.py`
   - carga `datos/contenedores.json` en la tabla `contenedores`

Si necesitas reiniciar la base y recargar datos:

```bash
docker-compose down -v
docker-compose up --build
```

### Variables de Entorno

```bash
cp .env.example .env
# editá .env con tus valores
```
El compose levanta el `.env` automáticamente.

## Notas

- El servicio Docker `postgis` usa `Dockerfile.postgis` para precargar Python y `psycopg2`.
- El primer arranque puede tardar un poco mientras se inicializa la base y se cargan los datos.
- Si cambia el esquema de `contenedores`, actualiza también `db/init/01_setup.sql` y los modelos/queries en el backend.
