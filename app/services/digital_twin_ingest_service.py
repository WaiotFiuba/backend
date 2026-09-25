from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import time

from sqlalchemy import func, select, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.data_level import DataLevel
from app.models.map.caba_geo_extension import Barrio, CabaContainerSpatialMetadata
from app.schemas.digital_twin import TelemetryIngestPayload, TelemetryIngestResult

logger = logging.getLogger(__name__)


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

DATA_LEVEL_COLS = [
    "message_time",
    "imei",
    "m_id",
    "reading_date",
    "reported_height",
    "fire_alarm",
    "freeze_alarm",
    "reported_temperature",
    "crash_alarm",
    "garbage_collection_alarm",
    "reported_collection_date",
    "reported_low_consumption_voltage",
    "reported_high_consumption_voltage",
    "container_current_level_old",
    "container_id",
    "container_name",
    "container_current_level",
    "container_type_id",
    "container_type_name",
    "container_type_height",
    "site_id",
    "site_name",
    "zone_id",
    "zone_name",
    "waste_type_id",
    "waste_type_name",
    "rssi",
    "container_current_m3",
    "device_name",
    "device_serial_id",
    "container_serie_id",
]


async def ensure_cache(db: AsyncSession):
    global _cache_loaded
    if _cache_loaded and len(_cache_by_id) >= 20000:
        return
    _cache_by_id.clear()
    _cache_by_imei.clear()
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
    logger.info("Caché de telemetría cargada con %d contenedores.", len(_cache_by_id))


async def reset_database_container_levels(db: AsyncSession) -> None:
    """Reinicia los niveles de los contenedores en memoria y en la base de datos de forma segura."""
    global _cache_loaded
    # 1. Resetear siempre la memoria de inmediato
    for meta in _cache_by_id.values():
        meta.current_level = 0
    _cache_loaded = False

    # 2. Resetear en la base de datos los niveles de los contenedores a 0
    try:
        await db.execute(
            update(Container).values(
                current_level=0,
                last_reading=None,
                last_pickup=None,
                change_version=func.nextval("container_change_version_seq"),
            )
        )
        await db.flush()
        logger.info("Niveles de contenedores reiniciados a 0.")
    except Exception as e:
        logger.warning(
            "Aviso: El reset SQL de contenedores tuvo un problema (%s).",
            e,
        )


async def ingest_telemetry_batch(
    db: AsyncSession,
    measurements: list[TelemetryIngestPayload | dict],
) -> TelemetryIngestResult:
    t_start = time.perf_counter()

    await ensure_cache(db)
    t_cache = time.perf_counter()

    updated = 0
    not_found = 0

    data_level_tuples = []
    data_level_inserts = []
    container_updates = []
    update_tuples = []
    _cache_by_serie: dict[str, ContainerCacheMeta] = {}
    for meta in _cache_by_id.values():
        if meta.serie_id:
            _cache_by_serie[str(meta.serie_id).strip()] = meta
            _cache_by_serie[str(meta.serie_id).split("|")[-1].strip()] = meta

    for m in measurements:
        if isinstance(m, dict):
            container_id_raw = m.get("container_id")
            device_id = m.get("device_id")
            ts = m.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except Exception:
                    ts = datetime.now()
            tel = m.get("telemetry") or {}
            fill_level = tel.get("fill_level_pct", 0.0)
            distance = tel.get("ultrasonic_distance_cm")
            battery = tel.get("battery_pct", 100.0)
            rssi = tel.get("signal_rssi_dbm")
            temp = tel.get("temperature_c")
            acc = tel.get("acceleration_g", 1.0)
            flags = m.get("flags") or {}
            is_pickup = bool(flags.get("is_collection_detected", False))
            anomaly = flags.get("anomaly")
        else:
            container_id_raw = m.container_id
            device_id = m.device_id
            ts = m.timestamp
            tel = m.telemetry
            fill_level = tel.fill_level_pct
            distance = tel.ultrasonic_distance_cm
            battery = tel.battery_pct
            rssi = tel.signal_rssi_dbm
            temp = tel.temperature_c
            acc = tel.acceleration_g
            flags = m.flags
            is_pickup = bool(flags.is_collection_detected)
            anomaly = flags.anomaly

        raw_str = (
            str(container_id_raw).split("|")[-1].strip()
            if container_id_raw is not None
            else ""
        )
        try:
            cid = int(raw_str) if raw_str.isdigit() else None
        except ValueError:
            cid = None

        meta = None
        if cid is not None and cid in _cache_by_id:
            meta = _cache_by_id[cid]
        elif raw_str and raw_str in _cache_by_serie:
            meta = _cache_by_serie[raw_str]
        elif str(container_id_raw) in _cache_by_serie:
            meta = _cache_by_serie[str(container_id_raw)]
        elif device_id in _cache_by_imei:
            meta = _cache_by_imei[device_id]

        if not meta:
            not_found += 1
            continue

        old_level = meta.current_level
        new_level = round(fill_level)

        vol_m3 = (
            round(meta.container_type_volume_m3 * new_level / 100, 4)
            if meta.container_type_volume_m3 is not None
            else None
        )
        collection_date = ts if is_pickup else None

        ctype_height_int = (
            int(round(meta.container_type_height))
            if meta.container_type_height is not None
            else None
        )
        reported_height_float = float(distance) if distance is not None else None
        temp_float = float(temp) if temp is not None else None
        rssi_float = float(rssi) if rssi is not None else None
        vol_m3_float = float(vol_m3) if vol_m3 is not None else None

        # Record tuple for fast asyncpg COPY
        data_level_tuples.append(
            (
                ts,
                device_id,
                "",
                ts,
                reported_height_float,
                anomaly == "incendio",
                False,
                temp_float,
                acc >= 2.5,
                is_pickup,
                collection_date,
                battery <= 15,
                False,
                old_level,
                meta.id,
                meta.description,
                new_level,
                meta.container_type_id,
                meta.container_type_name,
                ctype_height_int,
                str(meta.site_id) if meta.site_id is not None else None,
                meta.site_name,
                meta.zone_id,
                meta.zone_name,
                meta.waste_type_id,
                meta.waste_type_name,
                rssi_float,
                vol_m3_float,
                device_id,
                device_id,
                meta.serie_id,
            )
        )

        update_tuples.append((meta.id, new_level, ts, is_pickup))

        # Fallback dicts for standard SQLAlchemy
        data_level_inserts.append(
            {
                "message_time": ts,
                "imei": device_id,
                "m_id": "",
                "reading_date": ts,
                "reported_height": distance,
                "fire_alarm": anomaly == "incendio",
                "freeze_alarm": False,
                "reported_temperature": temp,
                "crash_alarm": acc >= 2.5,
                "garbage_collection_alarm": is_pickup,
                "reported_collection_date": collection_date,
                "reported_low_consumption_voltage": battery <= 15,
                "reported_high_consumption_voltage": False,
                "container_current_level_old": old_level,
                "container_id": meta.id,
                "container_name": meta.description,
                "container_current_level": new_level,
                "container_type_id": meta.container_type_id,
                "container_type_name": meta.container_type_name,
                "container_type_height": ctype_height_int,
                "site_id": str(meta.site_id) if meta.site_id is not None else None,
                "site_name": meta.site_name,
                "zone_id": meta.zone_id,
                "zone_name": meta.zone_name,
                "waste_type_id": meta.waste_type_id,
                "waste_type_name": meta.waste_type_name,
                "rssi": rssi,
                "container_current_m3": vol_m3,
                "device_name": device_id,
                "device_serial_id": device_id,
                "container_serie_id": meta.serie_id,
            }
        )

        upd = {
            "id": meta.id,
            "current_level": new_level,
            "last_reading": ts,
            "available": True,
        }
        if meta.device_imei is None and device_id:
            upd["device_imei"] = device_id
            meta.device_imei = device_id
            _cache_by_imei[device_id] = meta

        if is_pickup:
            upd["last_pickup"] = ts

        container_updates.append(upd)
        meta.current_level = new_level
        updated += 1

    t_dict_prep = time.perf_counter()

    # Try high-performance native asyncpg COPY + Staging Table in PostgreSQL
    try:
        conn = await db.connection()
        raw_conn = await conn.get_raw_connection()
        asyncpg_conn = getattr(raw_conn, "driver_connection", raw_conn)

        if hasattr(asyncpg_conn, "copy_records_to_table"):
            if data_level_tuples:
                await asyncpg_conn.copy_records_to_table(
                    "data_level",
                    records=data_level_tuples,
                    columns=DATA_LEVEL_COLS,
                )
            if update_tuples:
                await asyncpg_conn.execute(
                    """
                    CREATE TEMP TABLE IF NOT EXISTS temp_container_updates (
                        id bigint,
                        level integer,
                        reading_date timestamptz,
                        is_pickup boolean
                    );
                    TRUNCATE temp_container_updates;
                    """
                )
                await asyncpg_conn.copy_records_to_table(
                    "temp_container_updates",
                    records=update_tuples,
                    columns=["id", "level", "reading_date", "is_pickup"],
                )
                await asyncpg_conn.execute(
                    """
                    UPDATE containers AS c
                    SET current_level = a.level,
                        last_reading = a.reading_date,
                        last_pickup = COALESCE(a.pickup_date, c.last_pickup),
                        available = true,
                        change_version = nextval('container_change_version_seq')
                    FROM (
                        SELECT
                            id,
                            (array_agg(level ORDER BY reading_date DESC, ctid DESC))[1] AS level,
                            max(reading_date) AS reading_date,
                            max(reading_date) FILTER (WHERE is_pickup) AS pickup_date
                        FROM temp_container_updates
                        GROUP BY id
                    ) AS a
                    WHERE c.id = a.id
                      AND (c.last_reading IS NULL OR c.last_reading < a.reading_date);
                    """
                )
            await db.commit()

            # Actualizar niveles virtuales de what-if si hay un plan activo
            await _update_whatif_levels(db, update_tuples)

            return TelemetryIngestResult(
                accepted=len(measurements), updated=updated, not_found=not_found
            )
    except Exception as e:
        logger.warning("Fast PostgreSQL ingest fallback: %s", e)
        try:
            await db.rollback()
        except Exception:
            pass

    # Standard SQLAlchemy Fallback
    t_insert_start = time.perf_counter()
    if data_level_inserts:
        await db.execute(insert(DataLevel), data_level_inserts)
    t_insert_end = time.perf_counter()

    t_update_start = time.perf_counter()
    if container_updates:
        await db.execute(update(Container), container_updates)
    t_update_end = time.perf_counter()

    t_change_version_start = time.perf_counter()
    if container_updates:
        updated_ids = [u["id"] for u in container_updates]
        await db.execute(
            update(Container)
            .where(Container.id.in_(updated_ids))
            .values(change_version=func.nextval("container_change_version_seq"))
        )
    t_change_version_end = time.perf_counter()

    t_commit_start = time.perf_counter()
    await db.commit()
    t_commit_end = time.perf_counter()

    total_time = t_commit_end - t_start
    print(
        f"[PERF DB INGEST] Total: {total_time:.3f}s | "
        f"Cache: {t_cache - t_start:.3f}s | "
        f"Prep dicts ({len(data_level_inserts)}): {t_dict_prep - t_cache:.3f}s | "
        f"Insert DataLevel: {t_insert_end - t_insert_start:.3f}s | "
        f"Update Container: {t_update_end - t_update_start:.3f}s | "
        f"Update ChangeVersion IN: {t_change_version_end - t_change_version_start:.3f}s | "
        f"Commit: {t_commit_end - t_commit_start:.3f}s",
        flush=True,
    )

    # Actualizar niveles virtuales de what-if si hay un plan activo
    await _update_whatif_levels(db, update_tuples)

    return TelemetryIngestResult(
        accepted=len(measurements), updated=updated, not_found=not_found
    )


async def _update_whatif_levels(
    db: AsyncSession,
    update_tuples: list[tuple],
) -> None:
    """
    Si hay un plan what-if activo, actualiza los niveles virtuales de los
    contenedores usando el mapping optimizado.
    """
    from app.services.map.optimization_whatif_service import (
        is_whatif_active,
        update_virtual_levels_batch,
    )

    if not is_whatif_active() or not update_tuples:
        return

    try:
        # update_tuples: (container_id, level, reading_date, is_pickup)
        virtual_updates = [(t[0], t[1]) for t in update_tuples]
        await update_virtual_levels_batch(db, virtual_updates)
    except Exception:
        logger.warning("Error actualizando niveles virtuales what-if.", exc_info=True)
