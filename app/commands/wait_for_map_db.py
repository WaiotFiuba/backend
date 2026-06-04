import asyncio
import sys
from time import monotonic

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings


async def wait_for_map_db(timeout_seconds: int = 90, interval_seconds: float = 2.0) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.map_database_url, echo=False, future=True)
    deadline = monotonic() + timeout_seconds
    last_error: Exception | None = None

    try:
        while monotonic() < deadline:
            try:
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                print("Base de mapa disponible.")
                return
            except Exception as exc:
                last_error = exc
                print("Esperando base de mapa...")
                await asyncio.sleep(interval_seconds)
    finally:
        await engine.dispose()

    print(f"No se pudo conectar a la base de mapa despues de {timeout_seconds}s: {last_error}")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(wait_for_map_db())
