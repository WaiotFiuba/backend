from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.digital_twin import (
    SavedConfigurationCreate,
    SavedConfigurationRead,
    SavedConfigurationUpdate,
    SimulationControlsUpdate,
    SimulationCreate,
    SimulationFinish,
    SimulationProgressUpdate,
    SimulationRead,
    TelemetryIngestPayload,
    TelemetryIngestResult,
    ZoneDemandRead,
    ZoneProfileLayerPayload,
)
from app.services.digital_twin_ingest_service import ingest_telemetry_batch
from app.services.map.zone_profile_service import save_zone_profiles_geojson
from app.services.simulation_session_service import (
    create_saved_configuration,
    create_simulation,
    delete_saved_configuration,
    fail_interrupted_sessions,
    finish_simulation_session,
    get_active_simulation,
    get_saved_configuration,
    list_saved_configurations,
    list_zone_demand,
    set_active_simulation_status,
    update_active_simulation_controls,
    update_saved_configuration,
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


@router.post("/configurations", response_model=SavedConfigurationRead)
async def create_configuration_endpoint(
    payload: SavedConfigurationCreate,
    db: MapDbDep,
    current_user: CurrentUserDep,
) -> SavedConfigurationRead:
    return await create_saved_configuration(db, current_user.id, payload)


@router.get("/configurations", response_model=list[SavedConfigurationRead])
async def list_configurations_endpoint(
    db: MapDbDep,
    current_user: CurrentUserDep,
) -> list[SavedConfigurationRead]:
    return await list_saved_configurations(db, current_user.id)


@router.get("/configurations/{config_id}", response_model=SavedConfigurationRead)
async def get_configuration_endpoint(
    config_id: int,
    db: MapDbDep,
    current_user: CurrentUserDep,
) -> SavedConfigurationRead:
    return await get_saved_configuration(db, current_user.id, config_id)


@router.put("/configurations/{config_id}", response_model=SavedConfigurationRead)
async def update_configuration_endpoint(
    config_id: int,
    payload: SavedConfigurationUpdate,
    db: MapDbDep,
    current_user: CurrentUserDep,
) -> SavedConfigurationRead:
    return await update_saved_configuration(db, current_user.id, config_id, payload)


@router.delete("/configurations/{config_id}", status_code=204)
async def delete_configuration_endpoint(
    config_id: int,
    db: MapDbDep,
    current_user: CurrentUserDep,
) -> None:
    await delete_saved_configuration(db, current_user.id, config_id)


@router.get("/depots")
async def get_depots_endpoint() -> dict:
    """Retorna las 7 bases operativas y plantas de transferencia de CABA."""
    from simulator.trucks.truck_depots import (
        DEPOTS_BY_ZONE,
        TRANSFER_STATIONS,
    )

    return {
        "bases": [
            {
                "id": d.id,
                "name": d.name,
                "zone": d.zone,
                "latitude": d.latitude,
                "longitude": d.longitude,
                "type": d.type,
            }
            for d in DEPOTS_BY_ZONE.values()
        ],
        "transfer_stations": [
            {
                "id": d.id,
                "name": d.name,
                "zone": d.zone,
                "latitude": d.latitude,
                "longitude": d.longitude,
                "type": d.type,
            }
            for d in TRANSFER_STATIONS
        ],
    }


@router.get("/trucks/active")
async def get_active_trucks_endpoint() -> list[dict]:
    """Retorna el estado de la flota de camiones recolectores en tiempo real."""
    from simulator.trucks.truck_engine import (
        get_latest_truck_snapshot,
    )

    return get_latest_truck_snapshot()


@router.get("/routes/{route_id}")
async def get_route_details_endpoint(route_id: str) -> dict:
    """Retorna los tramos y waypoints de un circuito de recolección."""
    from fastapi import HTTPException
    from simulator.trucks.truck_routes import load_all_collection_routes

    routes = load_all_collection_routes()
    clean_id = route_id.split(".")[0].strip()
    route = routes.get(clean_id)
    if not route:
        raise HTTPException(status_code=404, detail="Circuito no encontrado")

    is_green_route = "Contenedores Verdes" in route.service_name
    if (
        not is_green_route
        and not route.total_distance_m
        and clean_id != "RODRIGO_BUENO"
    ):
        try:
            from simulator.trucks.drpp_solver import optimize_circuit_route

            sol = optimize_circuit_route(clean_id)
            route.total_distance_m = sol.total_distance_m
            route.collection_distance_m = sol.collection_distance_m
            route.deadheading_distance_m = sol.deadheading_distance_m
            route.repeated_segments_count = sol.repeated_segments_count
            route.street_sequence = sol.street_sequence
        except Exception:
            pass

    return {
        "route_id": route.route_id,
        "zone": route.zone,
        "service_name": route.service_name,
        "site_ids": route.site_ids,
        "waypoints": route.waypoints,
        "segments_count": len(route.segments),
        "total_distance_m": route.total_distance_m,
        "collection_distance_m": route.collection_distance_m,
        "deadheading_distance_m": route.deadheading_distance_m,
        "repeated_segments_count": route.repeated_segments_count,
        "street_sequence": route.street_sequence,
    }


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
    if payload.trucks is not None:
        from simulator.trucks.truck_engine import (
            set_latest_truck_snapshot,
        )

        set_latest_truck_snapshot(payload.trucks)

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
        scenario=payload.scenario,
        total_periods=payload.total_periods,
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


@router.put("/worker/simulations/{simulation_id}/zone-profiles")
async def worker_publish_zone_profiles(
    simulation_id: int,
    payload: ZoneProfileLayerPayload,
    db: MapDbDep,
) -> dict[str, int]:
    """El simulador publica la capa de perfiles de zona con la que corre la
    sesion (segun su zone_profiles.yaml). Reemplaza la capa anterior."""
    features = await save_zone_profiles_geojson(db, simulation_id, payload.model_dump())
    return {"features": features}


def _log_telemetry_ingest(mode: str, result: TelemetryIngestResult) -> None:
    logger.info(
        "Telemetria %s recibida: aceptadas=%s actualizadas=%s no_encontradas=%s.",
        mode,
        result.accepted,
        result.updated,
        result.not_found,
    )
