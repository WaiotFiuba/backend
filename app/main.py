from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers.auth import router as auth_router
from app.routers.digital_twin import router as digital_twin_router
from app.routers.map import router as map_router
from app.routers.users import router as users_router

# El esquema de la base lo crean las migraciones de Alembic (alembic upgrade
# head), no la app al arrancar.
app = FastAPI()

settings = get_settings()
cors_allowed_origins = [
    origin.strip()
    for origin in settings.cors_allowed_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins or ["*"],
    allow_credentials="*" not in cors_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(map_router)
app.include_router(digital_twin_router)


@app.get("/")
def read_root():
    return {"message": "Hello World"}
