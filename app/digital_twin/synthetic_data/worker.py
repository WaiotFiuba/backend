from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.map_database import MapSessionLocal
from app.digital_twin.synthetic_data.domain.entities import Measurement
from app.digital_twin.synthetic_data.loaders.backend_http import (
    load_topology_from_backend_api,
)
from app.digital_twin.synthetic_data.simulation.engine import SyntheticDataSimulator
from app.digital_twin.synthetic_data.simulation.scenario import scenario_from_mapping
from app.digital_twin.synthetic_data.transport.backend_http import (
    DeliveryReport,
    send_measurements_batch,
)
from app.models.map.simulation import SimulationSession, SimulationZoneOverride
import app.models.map  # noqa: F401
from app.services.simulation_control_service import (
    ACTIVE_STATUSES,
    effective_multiplier,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlSnapshot:
    speedup: float
    global_current: float
    global_target: float
    zones: tuple[tuple[str, float, float], ...]


async def run_worker() -> None:
    settings = get_settings()
    logger.info("Worker de simulacion iniciado.")
    while True:
        try:
            await _mark_interrupted_sessions_failed()
            break
        except Exception:
            logger.exception("No se pudo inicializar el estado del worker.")
            await asyncio.sleep(settings.simulator_poll_seconds)
    while True:
        try:
            simulation_id = await _next_pending_session_id()
        except Exception:
            logger.exception("No se pudo consultar la siguiente simulacion pendiente.")
            await asyncio.sleep(settings.simulator_poll_seconds)
            continue
        if simulation_id is None:
            await asyncio.sleep(settings.simulator_poll_seconds)
            continue
        await _run_session(simulation_id)


async def _run_session(simulation_id: int) -> None:
    settings = get_settings()
    logger.info("Iniciando simulacion %s.", simulation_id)
    try:
        async with MapSessionLocal() as db:
            session = await db.get(SimulationSession, simulation_id)
            if session is None:
                return
            config = scenario_from_mapping(session.scenario)
            topology = await asyncio.to_thread(
                load_topology_from_backend_api,
                settings.simulator_backend_url,
                "/map/containers/bbox?lat_min=-90&lat_max=90&lng_min=-180&lng_max=180&zoom=18",
                None,
            )
            simulator = SyntheticDataSimulator(config, topology=topology)
            simulator.initialize()

            session.status = "paused"
            session.started_at = _utc_now()
            await db.commit()

        previous_controls: ControlSnapshot | None = None
        for period in range(config.periods):
            control = await _wait_until_runnable(simulation_id)
            if control is None:
                return

            # Consolidar lectura de controles y actualización de simulated_time al inicio del tick
            async with MapSessionLocal() as db:
                session = await db.get(SimulationSession, simulation_id)
                if session is None or session.status not in ACTIVE_STATUSES:
                    return
                
                simulated_time = config.start + timedelta(
                    minutes=period * config.frequency_minutes
                )
                simulated_time = _as_utc(simulated_time)
                session.simulated_time = simulated_time
                
                global_multiplier = effective_multiplier(
                    session.global_demand_start,
                    session.global_demand_target,
                    simulated_time,
                    session.transition_started_at,
                    session.transition_ends_at,
                )
                
                overrides_result = await db.execute(
                    select(SimulationZoneOverride).where(
                        SimulationZoneOverride.simulation_id == simulation_id
                    )
                )
                overrides = overrides_result.scalars().all()
                
                controls = ControlSnapshot(
                    speedup=session.speedup,
                    global_current=global_multiplier,
                    global_target=session.global_demand_target,
                    zones=tuple(
                        sorted(
                            (
                                item.neighborhood,
                                effective_multiplier(
                                    item.multiplier_start,
                                    item.multiplier_target,
                                    simulated_time,
                                    item.transition_started_at,
                                    item.transition_ends_at,
                                ),
                                item.multiplier_target,
                            )
                            for item in overrides
                        )
                    ),
                )
                await db.commit()

            if _control_targets(controls) != _control_targets(previous_controls):
                _log_control_change(simulation_id, previous_controls, controls)
            previous_controls = controls
            zone_multipliers = {
                name: current for name, current, _target in controls.zones
            }
            tick = simulator.run_tick(
                simulated_time,
                global_demand_multiplier=(
                    controls.global_current * config.high_demand_multiplier
                ),
                zone_multiplier=lambda zone: zone_multipliers.get(zone, 1.0),
            )
            delivery = await _deliver_tick_measurements(
                simulation_id,
                tick.measurements,
                settings.simulator_backend_url,
                settings.simulator_batch_size,
            )
            if delivery is None:
                return
            async with MapSessionLocal() as db:
                session = await db.get(SimulationSession, simulation_id)
                if session is None:
                    return
                session.current_period = period + 1
                session.global_demand_current = controls.global_current
                session.collections_generated += len(tick.collections)
                session.alarms_generated += len(tick.alarms)
                session.measurements_sent += delivery.sent
                speedup = session.speedup
                await db.commit()
            logger.info(
                "Simulacion %s tick %s/%s: tiempo=%s mediciones=%s actualizadas=%s "
                "no_encontradas=%s lotes=%s recolecciones=%s alarmas=%s speedup=%sx "
                "demanda=%.3f.",
                simulation_id,
                period + 1,
                config.periods,
                simulated_time.isoformat(),
                delivery.sent,
                delivery.updated,
                delivery.not_found,
                delivery.requests,
                len(tick.collections),
                len(tick.alarms),
                speedup,
                controls.global_current,
            )
            if not await _wait_between_ticks(
                simulation_id,
                config.frequency_minutes / speedup,
            ):
                return

        await _finish_session(simulation_id, "completed")
        logger.info("Simulacion %s completada.", simulation_id)
    except asyncio.CancelledError:
        await _finish_session(simulation_id, "failed", "Worker cancelado.")
        raise
    except Exception as exc:
        logger.exception("La simulacion %s fallo.", simulation_id)
        await _finish_session(simulation_id, "failed", str(exc))


async def _wait_until_runnable(simulation_id: int) -> SimulationSession | None:
    settings = get_settings()
    paused_logged = False
    while True:
        async with MapSessionLocal() as db:
            session = await db.get(SimulationSession, simulation_id)
            if session is None:
                return None
            if session.status in ("stopping", "completed"):
                logger.info(
                    "Simulacion %s detenida por solicitud de control.", simulation_id
                )
                if session.status == "stopping":
                    session.status = "completed"
                    session.finished_at = _utc_now()
                    await db.commit()
                return None
            if session.status == "running":
                if paused_logged:
                    logger.info("Simulacion %s reanudada.", simulation_id)
                return session
            if session.status not in ACTIVE_STATUSES:
                return None
            if session.status == "paused" and not paused_logged:
                logger.info("Simulacion %s pausada.", simulation_id)
                paused_logged = True
        await asyncio.sleep(settings.simulator_poll_seconds)


async def _deliver_tick_measurements(
    simulation_id: int,
    measurements: list[Measurement],
    backend_url: str,
    batch_size: int,
) -> DeliveryReport | None:
    if batch_size <= 0:
        raise ValueError("SIMULATOR_BATCH_SIZE debe ser mayor a 0.")

    sem = asyncio.Semaphore(10)

    async def send_batch_with_sem(batch_chunk):
        async with sem:
            if await _wait_until_runnable(simulation_id) is None:
                return None
            res = await asyncio.to_thread(
                send_measurements_batch,
                batch_chunk,
                backend_url,
                batch_size=batch_size,
            )
            await _record_delivered_measurements(simulation_id, res.sent)
            return res

    tasks = []
    for offset in range(0, len(measurements), batch_size):
        chunk = measurements[offset : offset + batch_size]
        tasks.append(send_batch_with_sem(chunk))

    results = await asyncio.gather(*tasks)

    report = DeliveryReport(sent=0, updated=0, not_found=0, requests=0)
    for res in results:
        if res is None:
            return None
        report = _combine_delivery_reports(report, res)
    return report


async def _record_delivered_measurements(
    simulation_id: int,
    sent: int,
) -> None:
    pass


def _remaining_tick_delay(
    frequency_minutes: int,
    speedup: float,
    elapsed_seconds: float,
) -> float:
    target_seconds = frequency_minutes * 60 / speedup
    return max(0.0, target_seconds - elapsed_seconds)


def _combine_delivery_reports(
    left: DeliveryReport,
    right: DeliveryReport,
) -> DeliveryReport:
    return DeliveryReport(
        sent=left.sent + right.sent,
        updated=left.updated + right.updated,
        not_found=left.not_found + right.not_found,
        requests=left.requests + right.requests,
    )


async def _wait_between_ticks(simulation_id: int, delay_seconds: float) -> bool:
    settings = get_settings()
    remaining = delay_seconds
    while remaining > 0:
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(min(settings.simulator_poll_seconds, remaining))
        elapsed = asyncio.get_running_loop().time() - started
        remaining = max(0.0, remaining - elapsed)
        async with MapSessionLocal() as db:
            session = await db.get(SimulationSession, simulation_id)
            if session is None:
                return False
            if session.status in ("stopping", "completed"):
                logger.info(
                    "Simulacion %s detenida por solicitud de control.", simulation_id
                )
                if session.status == "stopping":
                    session.status = "completed"
                    session.finished_at = _utc_now()
                    await db.commit()
                return False
            if session.status == "paused":
                if await _wait_until_runnable(simulation_id) is None:
                    return False
    return True


async def _next_pending_session_id() -> int | None:
    async with MapSessionLocal() as db:
        return await db.scalar(
            select(SimulationSession.id)
            .where(SimulationSession.status == "pending")
            .order_by(SimulationSession.id)
            .limit(1)
        )


async def _mark_interrupted_sessions_failed() -> None:
    async with MapSessionLocal() as db:
        await db.execute(
            update(SimulationSession)
            .where(SimulationSession.status.in_(("running", "paused", "stopping")))
            .values(
                status="failed",
                error_message="El worker se reinicio durante la simulacion.",
                finished_at=_utc_now(),
            )
        )
        await db.commit()


async def _finish_session(
    simulation_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    async with MapSessionLocal() as db:
        session = await db.get(SimulationSession, simulation_id)
        if session:
            session.status = status
            session.error_message = error_message
            session.finished_at = _utc_now()
            await db.commit()


def _log_control_change(
    simulation_id: int,
    previous: ControlSnapshot | None,
    current: ControlSnapshot,
) -> None:
    if previous is None:
        logger.info(
            "Simulacion %s controles iniciales: speedup=%sx demanda=%.3f objetivo=%.3f zonas=%s.",
            simulation_id,
            current.speedup,
            current.global_current,
            current.global_target,
            len(current.zones),
        )
        return
    logger.info(
        "Simulacion %s cambio de controles: speedup %s->%s; demanda %.3f->%.3f "
        "(objetivo %.3f); zonas=%s.",
        simulation_id,
        previous.speedup,
        current.speedup,
        previous.global_current,
        current.global_current,
        current.global_target,
        current.zones,
    )


def _control_targets(
    controls: ControlSnapshot | None,
) -> tuple[float, float, tuple[tuple[str, float], ...]] | None:
    if controls is None:
        return None
    return (
        controls.speedup,
        controls.global_target,
        tuple((name, target) for name, _current, target in controls.zones),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
