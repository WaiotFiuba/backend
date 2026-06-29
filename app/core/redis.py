from redis.asyncio import Redis, from_url

from app.core.config import get_settings

settings = get_settings()

# Initialize a global redis client
redis_client: Redis = from_url(settings.redis_url, decode_responses=True)


async def init_redis():
    # Attempt a ping to fail early if redis is down
    await redis_client.ping()


async def close_redis():
    await redis_client.aclose()


async def get_redis() -> Redis:
    yield redis_client
