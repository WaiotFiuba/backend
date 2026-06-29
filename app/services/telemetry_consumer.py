import asyncio
import json
import logging
from redis.asyncio import Redis
from pydantic import ValidationError

from app.core.redis import redis_client
from app.core.map_database import MapSessionLocal
from app.schemas.digital_twin import TelemetryIngestPayload
from app.services.digital_twin_ingest_service import ingest_telemetry_batch

logger = logging.getLogger(__name__)

STREAM_KEY = "telemetry:stream"
GROUP_NAME = "telemetry_group"
CONSUMER_NAME = "worker_1"
BATCH_SIZE = 10000

async def setup_stream():
    try:
        await redis_client.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        logger.info(f"Redis stream group '{GROUP_NAME}' created.")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            pass # Group already exists
        else:
            logger.error(f"Error creating redis stream group: {e}")

async def consume_telemetry():
    await setup_stream()
    
    while True:
        try:
            # Read pending messages for this consumer or new messages
            # Block for 2 seconds
            response = await redis_client.xreadgroup(
                GROUP_NAME, CONSUMER_NAME, {STREAM_KEY: ">"}, count=BATCH_SIZE, block=2000
            )
            
            if not response:
                continue

            for stream, messages in response:
                if not messages:
                    continue
                
                payloads = []
                message_ids = []
                for message_id, fields in messages:
                    try:
                        raw = fields.get("payload")
                        if raw:
                            data = json.loads(raw)
                            payloads.append(TelemetryIngestPayload(**data))
                        message_ids.append(message_id)
                    except (json.JSONDecodeError, ValidationError) as e:
                        logger.error(f"Error decodificando payload de telemetria {message_id}: {e}")
                        message_ids.append(message_id) # Ack anyway to avoid poison pill
                
                if payloads:
                    async with MapSessionLocal() as db:
                        result = await ingest_telemetry_batch(db, payloads)
                        logger.info(f"Worker procesó {result.accepted} mediciones. Actualizadas: {result.updated}, No encontradas: {result.not_found}")
                
                if message_ids:
                    await redis_client.xack(STREAM_KEY, GROUP_NAME, *message_ids)
                    # Liberar memoria eliminando los mensajes ya procesados
                    await redis_client.xdel(STREAM_KEY, *message_ids)
                    
        except asyncio.CancelledError:
            logger.info("Consumidor de telemetría cancelado.")
            break
        except Exception as e:
            logger.exception(f"Error en el consumidor de telemetría: {e}")
            await asyncio.sleep(5)
