from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import init_db
from app.core.map_database import init_map_db
from app.routers.auth import router as auth_router
from app.routers.digital_twin import router as digital_twin_router
from app.routers.users import router as users_router
from app.routers.map import router as map_router
from app.core.redis import init_redis, close_redis
from app.services.telemetry_consumer import consume_telemetry
import asyncio


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_create_db and settings.app_env.lower() != "production":
        await init_db()
        if settings.enable_map_db and settings.auto_create_map_db:
            await init_map_db()

    await init_redis()
    consumer_task = asyncio.create_task(consume_telemetry())

    yield

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await close_redis()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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
