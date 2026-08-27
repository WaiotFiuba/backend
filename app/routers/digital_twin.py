from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.digital_twin import (
    SimulationControlsUpdate,
    SimulationCreate,
    SimulationFinish,
    SimulationProgressUpdate,
    SimulationRead,
    TelemetryIngestPayload,
    TelemetryIngestResult,
    ZoneDemandRead,
)
from app.services.digital_twin_ingest_service import ingest_telemetry_batch
from app.services.simulation_control_service import (
    create_simulation,
    fail_interrupted_sessions,
    finish_simulation_session,
    get_active_simulation,
    list_zone_demand,
    set_active_simulation_status,
    update_active_simulation_controls,
    update_simulation_progress,
)

router = APIRouter(prefix="/digital-twin", tags=["digital-twin"])
logger = logging.getLogger(__name__)

MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.post("/telemetry", response_model=TelemetryIngestResult)
async def ingest_telemetry(
    payload: TelemetryIngestPayload,
    db: MapDbDep,
) -> TelemetryIngestResult:
    result = await ingest_telemetry_batch(db, [payload])
    _log_telemetry_ingest("individual", result)
    return result


@router.post("/telemetry/batch", response_model=TelemetryIngestResult)
async def ingest_telemetry_batch_endpoint(
    request: Request,
    db: MapDbDep,
) -> TelemetryIngestResult:
    import time

    t0 = time.perf_counter()
    body = await request.json()
    raw_measurements = body.get("measurements", []) if isinstance(body, dict) else []
    result = await ingest_telemetry_batch(db, raw_measurements)
    print(
        f"[PERF API ENDPOINT] /telemetry/batch completado en {time.perf_counter() - t0:.3f}s para {len(raw_measurements)} mediciones",
        flush=True,
    )
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


# Endpoints utilizados por el Simulator Worker (sin acceso directo a BD)
@router.get("/worker/active-session", response_model=SimulationRead | None)
async def read_worker_active_session(
    db: MapDbDep,
) -> SimulationRead | None:
    try:
        return await get_active_simulation(db)
    except Exception:
        return None


@router.post("/worker/fail-interrupted")
async def worker_fail_interrupted(
    db: MapDbDep,
) -> dict[str, int]:
    count = await fail_interrupted_sessions(db)
    return {"failed_count": count}


@router.patch(
    "/worker/simulations/{simulation_id}/progress", response_model=SimulationRead
)
async def worker_update_progress(
    simulation_id: int,
    payload: SimulationProgressUpdate,
    db: MapDbDep,
) -> SimulationRead:
    return await update_simulation_progress(
        db=db,
        simulation_id=simulation_id,
        simulated_time=payload.simulated_time,
        current_period=payload.current_period,
        global_demand_current=payload.global_demand_current,
        measurements_sent=payload.measurements_sent,
        collections_generated=payload.collections_generated,
        alarms_generated=payload.alarms_generated,
        status=payload.status,
    )


@router.post(
    "/worker/simulations/{simulation_id}/finish", response_model=SimulationRead
)
async def worker_finish_simulation(
    simulation_id: int,
    payload: SimulationFinish,
    db: MapDbDep,
) -> SimulationRead:
    return await finish_simulation_session(
        db=db,
        simulation_id=simulation_id,
        status=payload.status,
        error_message=payload.error_message,
    )


def _log_telemetry_ingest(mode: str, result: TelemetryIngestResult) -> None:
    logger.info(
        "Telemetria %s recibida: aceptadas=%s actualizadas=%s no_encontradas=%s.",
        mode,
        result.accepted,
        result.updated,
        result.not_found,
    )
