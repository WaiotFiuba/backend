# WaiotFiuba Backend
 
Gemelo digital de contenedores - Ciudad de Buenos Aires.
 
## Requisitos
 
- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
## Sin Docker
 
### Instalación
 
```bash
uv sync
```
 
### Correr el servidor
 
```bash
uv run uvicorn app.main:app --reload
```
 
La API queda disponible en `http://localhost:8000`.  
La documentación interactiva en `http://localhost:8000/docs`.
 
### Tests
 
```bash
uv run --with pytest pytest
```

### Linter

```bash
uv run --with ruff ruff check .
```

### Variables de entorno
 
```bash
cp .env.example .env
# editá .env con tus valores
```
 
---

 
## Con Docker
 
### Requisitos
 
- Docker
- docker-compose
### Correr
 
```bash
docker-compose up --build
```
 
La API queda disponible en `http://localhost:8000`.
 
### Variables de entorno
 
```bash
cp .env.example .env
# editá .env con tus valores
```
 
El compose levanta el `.env` automáticamente.