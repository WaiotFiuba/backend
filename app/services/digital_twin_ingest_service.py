from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from geoalchemy2 import functions as geo_funcs

from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.data_level import DataLevel
from app.models.map.neighborhood_demographic import NeighborhoodDemographic
from app.schemas.digital_twin import TelemetryIngestPayload, TelemetryIngestResult


async def ingest_telemetry_batch(
    db: AsyncSession,
    measurements: list[TelemetryIngestPayload],
) -> TelemetryIngestResult:
    updated = 0
    not_found = 0
    changed_containers: dict[int, Container] = {}

    for measurement in measurements:
        container = await _find_container(db, measurement)
        neighborhood = await _find_neighborhood(db, container) if container else None
        db.add(_data_level_row(measurement, container, neighborhood))
        if container is None:
            not_found += 1
            continue

        container.current_level = round(measurement.telemetry.fill_level_pct)
        container.last_reading = measurement.timestamp
        container.available = True
        if container.device_imei is None:
            container.device_imei = measurement.device_id
        if measurement.flags.is_collection_detected:
            container.last_pickup = measurement.timestamp
        changed_containers[container.id] = container
        updated += 1

    for container in changed_containers.values():
        container.change_version = func.nextval("container_change_version_seq")
    await db.commit()
    return TelemetryIngestResult(
        accepted=len(measurements),
        updated=updated,
        not_found=not_found,
    )


async def _find_container(
    db: AsyncSession,
    measurement: TelemetryIngestPayload,
) -> Container | None:
    try:
        container_id = int(measurement.container_id)
    except ValueError:
        container_id = None

    if container_id is not None:
        result = await db.execute(
            _container_select().where(Container.id == container_id)
        )
        container = result.scalar_one_or_none()
        if container is not None:
            return container

    result = await db.execute(
        _container_select().where(Container.device_imei == measurement.device_id)
    )
    return result.scalar_one_or_none()


def _container_select():
    return select(Container).options(
        joinedload(Container.container_type).selectinload(ContainerType.waste_types)
    )


def _data_level_row(
    measurement: TelemetryIngestPayload,
    container: Container | None,
    neighborhood: NeighborhoodDemographic | None = None,
) -> DataLevel:
    container_type = container.container_type if container else None
    waste_type = (
        container_type.waste_types[0]
        if container_type is not None and container_type.waste_types
        else None
    )
    collection_date = (
        measurement.timestamp if measurement.flags.is_collection_detected else None
    )
    container_current_level = round(measurement.telemetry.fill_level_pct)
    container_current_m3 = _current_volume_m3(
        container_type.volume_m3 if container_type else None,
        measurement.telemetry.fill_level_pct,
    )

    return DataLevel(
        message_time=measurement.timestamp,
        imei=measurement.device_id,
        m_id="",
        reading_date=measurement.timestamp,
        reported_height=measurement.telemetry.ultrasonic_distance_cm,
        fire_alarm=measurement.flags.anomaly == "incendio",
        freeze_alarm=False,
        reported_temperature=measurement.telemetry.temperature_c,
        crash_alarm=measurement.telemetry.acceleration_g >= 2.5,
        garbage_collection_alarm=measurement.flags.is_collection_detected,
        reported_collection_date=collection_date,
        reported_low_consumption_voltage=measurement.telemetry.battery_pct <= 15,
        reported_high_consumption_voltage=False,
        container_current_level_old=container.current_level if container else None,
        container_id=container.id if container else None,
        container_name=container.description if container else None,
        container_current_level=container_current_level,
        container_type_id=container_type.id if container_type else None,
        container_type_name=container_type.name if container_type else None,
        container_type_height=container_type.height_cm if container_type else None,
        site_id=container.site_id if container else None,
        site_name=container.site_name if container else None,
        zone_id=neighborhood.id if neighborhood else None,
        zone_name=neighborhood.neighborhood if neighborhood else None,
        waste_type_id=waste_type.id if waste_type else None,
        waste_type_name=waste_type.name if waste_type else None,
        rssi=measurement.telemetry.signal_rssi_dbm,
        container_current_m3=container_current_m3,
        device_name=measurement.device_id,
        device_serial_id=measurement.device_id,
        container_serie_id=container.serie_id if container else None,
    )


async def _find_neighborhood(
    db: AsyncSession,
    container: Container,
) -> NeighborhoodDemographic | None:
    result = await db.execute(
        select(NeighborhoodDemographic)
        .where(geo_funcs.ST_Covers(NeighborhoodDemographic.geom, container.geom))
        .limit(1)
    )
    return result.scalar_one_or_none()


def _current_volume_m3(volume_m3: float | None, fill_level_pct: float) -> float | None:
    if volume_m3 is None:
        return None
    return round(volume_m3 * fill_level_pct / 100, 4)
