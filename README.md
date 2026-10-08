# WaiotFiuba Backend

Backend del gemelo digital de contenedores de la Ciudad de Buenos Aires.

## Probar con Docker

Requisitos:

- Docker
- Docker Compose
- Python 3.13+ o `uv` para descargar los datos semilla

Desde esta carpeta (`backend`):

```bash
cp .env.example .env
python scripts/download_seed_data.py
docker compose --profile map up --build
```

Si usas `uv`, tambien podes descargar los datos con:

```bash
uv run python scripts/download_seed_data.py
```

La API queda disponible en:

```text
http://localhost:8000
http://localhost:8000/docs
```

El perfil `map` levanta:

- `postgis`: base local con PostGIS.
- `api`: ejecuta migraciones, siembra los datos de `datos/` y levanta FastAPI.
- `simulator`: worker del simulador apuntando a la API local.

El primer arranque puede tardar mientras Docker construye imagenes, PostGIS queda
listo, Alembic migra el esquema y el seed carga contenedores/sitios.

## Datos Semilla

Los archivos pesados de `datos/` no se versionan en git. Se descargan desde el
release publico `seed-data-v1`:

```bash
python scripts/download_seed_data.py
```

El script recrea la estructura local:

```text
datos/digital_twin/
datos/simulator/demography/
datos/simulator/land_use/
datos/simulator/routes/
```

Tambien valida `sha256` de cada archivo. Para forzar una redescarga:

```bash
python scripts/download_seed_data.py --force
```

Para usar otro release:

```bash
python scripts/download_seed_data.py --tag seed-data-v2
```

## Comandos Utiles

Detener los servicios:

```bash
docker compose --profile map down
```

Recrear la base local desde cero:

```bash
docker compose --profile map down -v
docker compose --profile map up --build
```

Ejecutar tests localmente:

```bash
uv run --with pytest pytest
```

Validar lint/pre-commit:

```bash
uv run --with pre-commit pre-commit run --all-files
```

## Sin Docker

```bash
uv sync
cp .env.example .env
uv run python scripts/download_seed_data.py
uv run alembic upgrade head
uv run python -m app.commands.seed --recluster
uv run uvicorn app.main:app --reload
```

## Hosting

La guia de despliegue continuo y hosting esta en
[`docs/HOSTING.md`](docs/HOSTING.md).

La documentacion del simulador esta en
[`simulator/README.md`](simulator/README.md).
