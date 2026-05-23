from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.database import init_db
from app.core.map_database import init_map_db
from app.routers.auth import router as auth_router
from app.routers.users import router as users_router
from app.routers.map import router as map_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_create_db and settings.app_env.lower() != "production":
        await init_db()
        await init_map_db()
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(map_router)


@app.get("/")
def read_root():
    return {"message": "Hello World"}
