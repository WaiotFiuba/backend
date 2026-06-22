from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.digital_twin.synthetic_data.simulation.scenario import scenario_from_mapping
from app.models.map.neighborhood_demographic import NeighborhoodDemographic
from app.models.map.simulation import SimulationSession, SimulationZoneOverride
from app.schemas.digital_twin import (
    SimulationControlsUpdate,
    SimulationCreate,
    SimulationRead,
    SimulationZoneState,
    ZoneDemandRead,
)

ACTIVE_STATUSES = ("pending", "running", "paused", "stopping")


async def create_simulation(
    db: AsyncSession,
    payload: SimulationCreate,
    user_id: int,
) -> SimulationRead:
    active = await _active_session(db)
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una simulacion activa.",
        )

    try:
        scenario_data = dict(payload.scenario)
        if payload.start_time is not None:
            scenario_data["start"] = payload.start_time
        if "frequency_minutes" not in scenario_data and payload.transition_minutes is not None:
            scenario_data["frequency_minutes"] = payload.transition_minutes
        config = scenario_from_mapping(scenario_data)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _validate_neighborhoods(
        db,
        [item.neighborhood for item in payload.zone_overrides],
    )
    scenario = _scenario_record(config)
    session = SimulationSession(
        status="pending",
        scenario=scenario,
        speedup=payload.speedup,
        global_demand_current=payload.global_demand_multiplier,
        global_demand_start=payload.global_demand_multiplier,
        global_demand_target=payload.global_demand_multiplier,
        transition_minutes=payload.transition_minutes,
        simulated_time=None,
        total_periods=config.periods,
        created_by=user_id,
    )
    db.add(session)
    await db.flush()
    for override in payload.zone_overrides:
        db.add(
            SimulationZoneOverride(
                simulation_id=session.id,
                neighborhood=override.neighborhood,
                multiplier_current=override.multiplier,
                multiplier_start=override.multiplier,
                multiplier_target=override.multiplier,
            )
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una simulacion activa.",
        ) from exc
    return await get_simulation(db, session.id)


async def get_active_simulation(db: AsyncSession) -> SimulationRead:
    session = await _active_session(db)
    if session is None:
        raise HTTPException(status_code=404, detail="No hay una simulacion activa.")
    return await _simulation_read(db, session)


async def set_active_simulation_status(
    db: AsyncSession,
    action: str,
) -> SimulationRead:
    session = await _active_session(db)
    if session is None:
        raise HTTPException(status_code=404, detail="No hay una simulacion activa.")
    return await set_simulation_status(db, session.id, action)


async def update_active_simulation_controls(
    db: AsyncSession,
    payload: SimulationControlsUpdate,
) -> SimulationRead:
    session = await _active_session(db)
    if session is None:
        raise HTTPException(status_code=404, detail="No hay una simulacion activa.")
    return await update_simulation_controls(db, session.id, payload)


async def get_simulation(db: AsyncSession, simulation_id: int) -> SimulationRead:
    session = await db.get(SimulationSession, simulation_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Simulacion no encontrada.")
    return await _simulation_read(db, session)


async def set_simulation_status(
    db: AsyncSession,
    simulation_id: int,
    action: str,
) -> SimulationRead:
    session = await db.get(SimulationSession, simulation_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Simulacion no encontrada.")
    allowed = {
        "pause": ({"running"}, "paused"),
        "resume": ({"paused"}, "running"),
        "stop": ({"pending", "running", "paused"}, "stopping"),
    }
    valid_from, target = allowed[action]
    if session.status not in valid_from:
        raise HTTPException(
            status_code=409,
            detail=f"No se puede ejecutar {action} desde estado {session.status}.",
        )
    if action == "stop" and session.status == "pending":
        session.status = "completed"
        session.finished_at = datetime.now(timezone.utc)
    else:
        session.status = target
    await db.commit()
    return await get_simulation(db, simulation_id)


async def update_simulation_controls(
    db: AsyncSession,
    simulation_id: int,
    payload: SimulationControlsUpdate,
) -> SimulationRead:
    session = await db.get(SimulationSession, simulation_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Simulacion no encontrada.")
    if session.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="La simulacion ya finalizo.")

    transition_minutes = (
        payload.transition_minutes
        if payload.transition_minutes is not None
        else session.transition_minutes
    )
    if payload.speedup is not None:
        session.speedup = payload.speedup
    if payload.transition_minutes is not None:
        session.transition_minutes = payload.transition_minutes
    if payload.global_demand_multiplier is not None:
        _set_session_transition(
            session,
            payload.global_demand_multiplier,
            transition_minutes,
        )
    if payload.zone_overrides is not None:
        await _validate_neighborhoods(
            db,
            [item.neighborhood for item in payload.zone_overrides],
        )
        await _set_zone_transitions(
            db,
            session,
            payload.zone_overrides,
            transition_minutes,
        )
    await db.commit()
    return await get_simulation(db, simulation_id)


async def list_zone_demand(db: AsyncSession) -> list[ZoneDemandRead]:
    active = await _active_session(db)
    overrides = {}
    if active:
        overrides = {
            item.neighborhood: effective_multiplier(
                item.multiplier_start,
                item.multiplier_target,
                active.simulated_time,
                item.transition_started_at,
                item.transition_ends_at,
            )
            for item in await _zone_overrides(db, active.id)
        }
    rows = (
        await db.execute(
            select(NeighborhoodDemographic).order_by(NeighborhoodDemographic.neighborhood)
        )
    ).scalars()
    return [
        ZoneDemandRead(
            neighborhood=row.neighborhood,
            commune=row.commune,
            population=row.population,
            year=row.year,
            source=row.source,
            area_km2=row.area_km2,
            density_per_km2=row.density_per_km2,
            density_factor=row.density_factor,
            multiplier_effective=overrides.get(row.neighborhood, 1.0),
        )
        for row in rows
    ]


def effective_multiplier(
    start: float,
    target: float,
    simulated_time: datetime | None,
    transition_started_at: datetime | None,
    transition_ends_at: datetime | None,
) -> float:
    if not simulated_time or not transition_started_at or not transition_ends_at:
        return target
    duration = (transition_ends_at - transition_started_at).total_seconds()
    if duration <= 0 or simulated_time >= transition_ends_at:
        return target
    if simulated_time <= transition_started_at:
        return start
    elapsed = (simulated_time - transition_started_at).total_seconds()
    return start + (target - start) * elapsed / duration


def _set_session_transition(
    session: SimulationSession,
    target: float,
    transition_minutes: int,
) -> None:
    now = session.simulated_time
    current = effective_multiplier(
        session.global_demand_start,
        session.global_demand_target,
        now,
        session.transition_started_at,
        session.transition_ends_at,
    )
    session.global_demand_current = current
    session.global_demand_start = current
    session.global_demand_target = target
    session.transition_started_at = now
    session.transition_ends_at = now + timedelta(minutes=transition_minutes) if now else None


async def _set_zone_transitions(db, session, requested, transition_minutes) -> None:
    existing = {item.neighborhood: item for item in await _zone_overrides(db, session.id)}
    for override in requested:
        item = existing.get(override.neighborhood)
        if item is None:
            item = SimulationZoneOverride(
                simulation_id=session.id,
                neighborhood=override.neighborhood,
            )
            db.add(item)
        current = effective_multiplier(
            item.multiplier_start,
            item.multiplier_target,
            session.simulated_time,
            item.transition_started_at,
            item.transition_ends_at,
        )
        item.multiplier_current = current
        item.multiplier_start = current
        item.multiplier_target = override.multiplier
        item.transition_started_at = session.simulated_time
        item.transition_ends_at = (
            session.simulated_time + timedelta(minutes=transition_minutes)
            if session.simulated_time
            else None
        )


async def _active_session(db: AsyncSession) -> SimulationSession | None:
    result = await db.execute(
        select(SimulationSession)
        .where(SimulationSession.status.in_(ACTIVE_STATUSES))
        .order_by(SimulationSession.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _zone_overrides(db: AsyncSession, simulation_id: int) -> list[SimulationZoneOverride]:
    return list(
        (
            await db.execute(
                select(SimulationZoneOverride).where(
                    SimulationZoneOverride.simulation_id == simulation_id
                )
            )
        ).scalars()
    )


async def _validate_neighborhoods(db: AsyncSession, names: list[str]) -> None:
    if not names:
        return
    existing = set(
        (
            await db.execute(
                select(NeighborhoodDemographic.neighborhood).where(
                    NeighborhoodDemographic.neighborhood.in_(names)
                )
            )
        ).scalars()
    )
    missing = sorted(set(names) - existing)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"unknown_neighborhoods": missing},
        )


async def _simulation_read(db: AsyncSession, session: SimulationSession) -> SimulationRead:
    overrides = await _zone_overrides(db, session.id)
    return SimulationRead(
        id=session.id,
        status=session.status,
        scenario=session.scenario,
        speedup=session.speedup,
        global_demand_current=effective_multiplier(
            session.global_demand_start,
            session.global_demand_target,
            session.simulated_time,
            session.transition_started_at,
            session.transition_ends_at,
        ),
        global_demand_target=session.global_demand_target,
        transition_minutes=session.transition_minutes,
        simulated_time=session.simulated_time,
        current_period=session.current_period,
        total_periods=session.total_periods,
        measurements_sent=session.measurements_sent,
        collections_generated=session.collections_generated,
        alarms_generated=session.alarms_generated,
        error_message=session.error_message,
        created_by=session.created_by,
        created_at=session.created_at,
        started_at=session.started_at,
        finished_at=session.finished_at,
        zone_overrides=[
            SimulationZoneState(
                neighborhood=item.neighborhood,
                multiplier_current=effective_multiplier(
                    item.multiplier_start,
                    item.multiplier_target,
                    session.simulated_time,
                    item.transition_started_at,
                    item.transition_ends_at,
                ),
                multiplier_target=item.multiplier_target,
            )
            for item in overrides
        ],
    )


def _scenario_record(config) -> dict[str, object]:
    return {
        key: value.isoformat() if isinstance(value, datetime) else list(value) if isinstance(value, tuple) else value
        for key, value in config.__dict__.items()
    }
