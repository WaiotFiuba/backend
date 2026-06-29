from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.data_level import DataLevel
from app.models.map.caba_geo_extension import Barrio, CabaContainerSpatialMetadata
from app.schemas.digital_twin import TelemetryIngestPayload, TelemetryIngestResult
from app.core.redis import redis_client


@dataclass
class ContainerCacheMeta:
    id: int
    device_imei: str | None
    description: str | None
    site_id: int | None
    site_name: str | None
    serie_id: str | None
    current_level: int | None
    container_type_id: int | None
    container_type_name: str | None
    container_type_height: float | None
    container_type_volume_m3: float | None
    waste_type_id: int | None
    waste_type_name: str | None
    zone_id: int | None
    zone_name: str | None


_cache_by_id: dict[int, ContainerCacheMeta] = {}
_cache_by_imei: dict[str, ContainerCacheMeta] = {}
_cache_loaded: bool = False


async def ensure_cache(db: AsyncSession):
    global _cache_loaded
    if _cache_loaded:
        return
    query = select(Container).options(
        joinedload(Container.container_type).selectinload(ContainerType.waste_types),
        joinedload(Container.spatial_metadata)
        .joinedload(CabaContainerSpatialMetadata.barrio)
        .selectinload(Barrio.demographic),
    )
    result = await db.execute(query)
    containers = result.scalars().all()
    for c in containers:
        ctype = c.container_type
        wtype = ctype.waste_types[0] if ctype and ctype.waste_types else None
        barrio = c.spatial_metadata.barrio if c.spatial_metadata else None
        meta = ContainerCacheMeta(
            id=c.id,
            device_imei=c.device_imei,
            description=c.description,
            site_id=c.site_id,
            site_name=c.site_name,
            serie_id=c.serie_id,
            current_level=c.current_level,
            container_type_id=ctype.id if ctype else None,
            container_type_name=ctype.name if ctype else None,
            container_type_height=ctype.height_cm if ctype else None,
            container_type_volume_m3=ctype.volume_m3 if ctype else None,
            waste_type_id=wtype.id if wtype else None,
            waste_type_name=wtype.name if wtype else None,
            zone_id=barrio.id if barrio else None,
            zone_name=barrio.nombre if barrio else None,
        )
        _cache_by_id[meta.id] = meta
        if meta.device_imei:
            _cache_by_imei[meta.device_imei] = meta
    _cache_loaded = True


async def queue_telemetry_batch(
    measurements: list[TelemetryIngestPayload],
) -> TelemetryIngestResult:
    if not measurements:
        return TelemetryIngestResult(accepted=0, updated=0, not_found=0)

    pipe = redis_client.pipeline()
    for m in measurements:
        # maxlen=500000 (~150MB) asegura que si el worker se cae, Redis no consuma toda la RAM
        pipe.xadd(
            "telemetry:stream",
            {"payload": m.model_dump_json()},
            maxlen=500000,
            approximate=True,
        )
    await pipe.execute()

    return TelemetryIngestResult(
        accepted=len(measurements),
        updated=0,
        not_found=0,
    )


async def ingest_telemetry_batch(
    db: AsyncSession,
    measurements: list[TelemetryIngestPayload],
) -> TelemetryIngestResult:
    await ensure_cache(db)

    updated = 0
    not_found = 0

    data_level_inserts = []
    container_updates = []

    for m in measurements:
        try:
            cid = int(m.container_id) if m.container_id else None
        except ValueError:
            cid = None

        meta = None
        if cid is not None and cid in _cache_by_id:
            meta = _cache_by_id[cid]
        elif m.device_id in _cache_by_imei:
            meta = _cache_by_imei[m.device_id]

        if not meta:
            not_found += 1
            continue

        # Old level is from cache
        old_level = meta.current_level
        new_level = round(m.telemetry.fill_level_pct)

        vol_m3 = (
            round(meta.container_type_volume_m3 * new_level / 100, 4)
            if meta.container_type_volume_m3 is not None
            else None
        )
        collection_date = m.timestamp if m.flags.is_collection_detected else None

        data_level_inserts.append(
            {
                "message_time": m.timestamp,
                "imei": m.device_id,
                "m_id": "",
                "reading_date": m.timestamp,
                "reported_height": m.telemetry.ultrasonic_distance_cm,
                "fire_alarm": m.flags.anomaly == "incendio",
                "freeze_alarm": False,
                "reported_temperature": m.telemetry.temperature_c,
                "crash_alarm": m.telemetry.acceleration_g >= 2.5,
                "garbage_collection_alarm": m.flags.is_collection_detected,
                "reported_collection_date": collection_date,
                "reported_low_consumption_voltage": m.telemetry.battery_pct <= 15,
                "reported_high_consumption_voltage": False,
                "container_current_level_old": old_level,
                "container_id": meta.id,
                "container_name": meta.description,
                "container_current_level": new_level,
                "container_type_id": meta.container_type_id,
                "container_type_name": meta.container_type_name,
                "container_type_height": meta.container_type_height,
                "site_id": meta.site_id,
                "site_name": meta.site_name,
                "zone_id": meta.zone_id,
                "zone_name": meta.zone_name,
                "waste_type_id": meta.waste_type_id,
                "waste_type_name": meta.waste_type_name,
                "rssi": m.telemetry.signal_rssi_dbm,
                "container_current_m3": vol_m3,
                "device_name": m.device_id,
                "device_serial_id": m.device_id,
                "container_serie_id": meta.serie_id,
            }
        )

        upd = {
            "id": meta.id,
            "current_level": new_level,
            "last_reading": m.timestamp,
            "available": True,
        }
        if meta.device_imei is None:
            upd["device_imei"] = m.device_id
            meta.device_imei = m.device_id
            _cache_by_imei[m.device_id] = meta

        if m.flags.is_collection_detected:
            upd["last_pickup"] = m.timestamp

        container_updates.append(upd)

        # Update cache for next iteration
        meta.current_level = new_level
        updated += 1

    if data_level_inserts:
        await db.execute(insert(DataLevel), data_level_inserts)

    if container_updates:
        # SQLAlchemy 2.0 Bulk Update using a list of dicts (id is the primary key)
        await db.execute(update(Container), container_updates)

        # Bulk Update the change_version sequence for the modified containers
        updated_ids = [u["id"] for u in container_updates]
        await db.execute(
            update(Container)
            .where(Container.id.in_(updated_ids))
            .values(change_version=func.nextval("container_change_version_seq"))
        )

    await db.commit()
    return TelemetryIngestResult(
        accepted=len(measurements), updated=updated, not_found=not_found
    )
