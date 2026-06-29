from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.digital_twin import (
    SimulationControlsUpdate,
    SimulationCreate,
    SimulationRead,
    TelemetryBatchIngestPayload,
    TelemetryIngestPayload,
    TelemetryIngestResult,
    ZoneDemandRead,
)
from app.services.digital_twin_ingest_service import queue_telemetry_batch
from app.services.simulation_control_service import (
    create_simulation,
    get_active_simulation,
    list_zone_demand,
    set_active_simulation_status,
    update_active_simulation_controls,
)

router = APIRouter(prefix="/digital-twin", tags=["digital-twin"])
logger = logging.getLogger(__name__)

MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.post("/telemetry", response_model=TelemetryIngestResult)
async def ingest_telemetry(
    payload: TelemetryIngestPayload,
) -> TelemetryIngestResult:
    result = await queue_telemetry_batch([payload])
    _log_telemetry_ingest("individual", result)
    return result


@router.post("/telemetry/batch", response_model=TelemetryIngestResult)
async def ingest_telemetry_batch_endpoint(
    payload: TelemetryBatchIngestPayload,
) -> TelemetryIngestResult:
    result = await queue_telemetry_batch(payload.measurements)
    _log_telemetry_ingest("batch", result)
    return result


@router.post("/simulations", response_model=SimulationRead)
async def start_simulation(
    payload: SimulationCreate,
    db: MapDbDep,
    current_user: CurrentUserDep,
) -> SimulationRead:
    return await create_simulation(db, payload, current_user.id)


@router.get("/simulations/active", response_model=SimulationRead)
async def read_active_simulation(
    db: MapDbDep,
    _current_user: CurrentUserDep,
) -> SimulationRead:
    return await get_active_simulation(db)


@router.post("/simulations/active/pause", response_model=SimulationRead)
async def pause_active_simulation(
    db: MapDbDep,
    _current_user: CurrentUserDep,
) -> SimulationRead:
    return await set_active_simulation_status(db, "pause")


@router.post("/simulations/active/resume", response_model=SimulationRead)
async def resume_active_simulation(
    db: MapDbDep,
    _current_user: CurrentUserDep,
) -> SimulationRead:
    return await set_active_simulation_status(db, "resume")


@router.post("/simulations/active/stop", response_model=SimulationRead)
async def stop_active_simulation(
    db: MapDbDep,
    _current_user: CurrentUserDep,
) -> SimulationRead:
    return await set_active_simulation_status(db, "stop")


@router.patch("/simulations/active/controls", response_model=SimulationRead)
async def update_active_controls(
    payload: SimulationControlsUpdate,
    db: MapDbDep,
    _current_user: CurrentUserDep,
) -> SimulationRead:
    return await update_active_simulation_controls(db, payload)


@router.get("/demand/zones", response_model=list[ZoneDemandRead])
async def read_zone_demand(
    db: MapDbDep,
    _current_user: CurrentUserDep,
) -> list[ZoneDemandRead]:
    return await list_zone_demand(db)


def _log_telemetry_ingest(mode: str, result: TelemetryIngestResult) -> None:
    logger.info(
        "Telemetria %s recibida: aceptadas=%s actualizadas=%s no_encontradas=%s.",
        mode,
        result.accepted,
        result.updated,
        result.not_found,
    )
