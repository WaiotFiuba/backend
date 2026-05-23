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

### Levantar toda la app sin PostGIS

```bash
docker-compose up --build
```

La API queda disponible en `http://localhost:8000`.

### Qué hace Docker Compose

- construye la imagen `api` desde el backend
- monta los datos en `./db/datos` y expone `postgis` en el puerto `5432`
- levanta una base PostGIS llamada `waiot_map`
- corre el seed con los datos del contenedor si se activa el perfil y los datos no estan ya insertados

### Inicialización de la base PostGIS


Si necesitas reiniciar la base y recargar datos:

```bash
docker-compose --profile map down -v
docker-compose --profile map up --build
```

### Variables de Entorno

```bash
cp .env.example .env
# editá .env con tus valores
```
El compose levanta el `.env` automáticamente.

## Notas

- El primer arranque puede tardar un poco mientras se inicializa la base y se cargan los datos.
