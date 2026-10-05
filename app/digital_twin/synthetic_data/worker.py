import asyncio
import functools
import itertools
import logging
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.digital_twin.synthetic_data.domain.entities import SimulationSession
from app.digital_twin.synthetic_data.loaders.backend_http import (
    load_topology_from_backend_api,
)
from app.digital_twin.synthetic_data.simulation.engine import SyntheticDataSimulator
from app.digital_twin.synthetic_data.simulation.scenario import scenario_from_mapping
from app.digital_twin.synthetic_data.transport.session_client import (
    SimulationSessionClient,
)
from app.digital_twin.synthetic_data.transport.telemetry_sender import (
    deliver_tick_measurements,
)
from app.services.simulation_control_service import (
    ACTIVE_STATUSES,
    effective_multiplier,
)

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=256)
def _norm_zone_name(s: str | None) -> str:
    if not s:
        return ""
    n = unicodedata.normalize("NFKD", str(s).strip().casefold())
    return "".join(c for c in n if not unicodedata.combining(c))


@dataclass(frozen=True)
class ControlSnapshot:
    speedup: float
    global_current: float
    global_target: float
    zones: tuple[tuple[str, float, float], ...]


async def run_worker() -> None:
    settings = get_settings()
    client = SimulationSessionClient(settings.simulator_backend_url)
    logger.info("Worker de simulacion iniciado (HTTP Backend mode).")
    first_wait = True
    while True:
        success = await client.fail_interrupted_sessions()
        if success:
            logger.info("Conectado con éxito a la API. Esperando simulaciones...")
            break
        if first_wait:
            logger.info(
                "Esperando a que el backend de la API finalice su inicialización en %s...",
                settings.simulator_backend_url,
            )
            first_wait = False
        await asyncio.sleep(settings.simulator_poll_seconds)

    while True:
        try:
            active_session = await client.fetch_active_session()

        except Exception:
            logger.exception("No se pudo consultar la siguiente simulacion activa.")
            await asyncio.sleep(settings.simulator_poll_seconds)
            continue

        if active_session is None or active_session.status not in (
            "pending",
            "running",
            "paused",
        ):
            await asyncio.sleep(settings.simulator_poll_seconds)
            continue

        await _run_session(client, active_session)


async def _run_session(
    client: SimulationSessionClient, session: SimulationSession
) -> None:
    settings = get_settings()
    simulation_id = session.id
    logger.info("Iniciando simulacion %s.", simulation_id)
    try:
        config = scenario_from_mapping(session.scenario)
        topology = await asyncio.to_thread(
            load_topology_from_backend_api,
            settings.simulator_backend_url,
            "/map/containers/bbox?lat_min=-90&lat_max=90&lng_min=-180&lng_max=180&zoom=18",
            settings.simulator_container_limit,
        )
        logger.info(
            "Topologia cargada para simulacion %s: contenedores=%s limite=%s.",
            simulation_id,
            len(topology.containers),
            settings.simulator_container_limit or "sin limite",
        )
        simulator = SyntheticDataSimulator(config, topology=topology)
        simulator.initialize()

        # Marcar la sesión como running en el backend tras completar la carga e inicialización
        await client.update_progress(simulation_id, {"status": "running"})

        previous_controls: ControlSnapshot | None = None

        period_iterator = (
            range(config.periods) if config.periods > 0 else itertools.count()
        )
        for period in period_iterator:
            session_state = await _wait_until_runnable(client, simulation_id)
            if session_state is None:
                break

            simulated_time = config.start + timedelta(
                minutes=period * config.frequency_minutes
            )
            simulated_time = _as_utc(simulated_time)

            global_multiplier = effective_multiplier(
                session_state.global_demand_current,
                session_state.global_demand_target,
                simulated_time,
                _parse_iso(session_state.started_at),
                _parse_iso(session_state.finished_at),
            )

            overrides = session_state.zone_overrides
            controls = ControlSnapshot(
                speedup=session_state.speedup,
                global_current=global_multiplier,
                global_target=session_state.global_demand_target,
                zones=tuple(
                    sorted(
                        (
                            item.neighborhood
                            if hasattr(item, "neighborhood")
                            else item["neighborhood"],
                            item.multiplier_current
                            if hasattr(item, "multiplier_current")
                            else item["multiplier_current"],
                            item.multiplier_target
                            if hasattr(item, "multiplier_target")
                            else item["multiplier_target"],
                        )
                        for item in overrides
                    )
                ),
            )

            if _control_targets(controls) != _control_targets(previous_controls):
                _log_control_change(simulation_id, previous_controls, controls)
            previous_controls = controls

            tick_started = asyncio.get_running_loop().time()

            has_zone_overrides = any(current != 1.0 for _, current, _ in controls.zones)
            if has_zone_overrides:
                zone_multipliers = {
                    _norm_zone_name(name): current
                    for name, current, _target in controls.zones
                }
                zone_multipliers.update(
                    {name: current for name, current, _target in controls.zones}
                )

                def zone_fn(zone: str) -> float:
                    return zone_multipliers.get(
                        zone,
                        zone_multipliers.get(_norm_zone_name(zone), 1.0),
                    )
            else:
                zone_fn = None

            tick = simulator.run_tick(
                simulated_time,
                global_demand_multiplier=(
                    controls.global_current * config.high_demand_multiplier
                ),
                zone_multiplier=zone_fn,
            )

            # 1. Enviar y persistir mediciones en la BD ANTES de avanzar el reloj
            await deliver_tick_measurements(
                tick.measurements,
                settings.simulator_backend_url,
                settings.simulator_batch_size,
            )

            # 2. Con los datos 100% guardados en Postgres, publicar el progreso y la hora simulada
            trucks_snapshot = (
                simulator.truck_fleet.get_trucks_snapshot()
                if getattr(simulator, "truck_fleet", None)
                else []
            )
            await client.update_progress(
                simulation_id,
                {
                    "simulated_time": simulated_time.isoformat(),
                    "current_period": period + 1,
                    "global_demand_current": controls.global_current,
                    "measurements_sent": len(tick.measurements),
                    "collections_generated": len(tick.collections),
                    "alarms_generated": len(tick.alarms),
                    "trucks": trucks_snapshot,
                },
            )

            tick_elapsed = asyncio.get_running_loop().time() - tick_started
            speedup = controls.speedup
            target_delay = (config.frequency_minutes * 60.0) / speedup
            remaining_delay = max(0.0, target_delay - tick_elapsed)

            total_periods_str = str(config.periods) if config.periods > 0 else "∞"
            logger.info(
                "Simulacion %s tick %s/%s: tiempo=%s mediciones=%s "
                "recolecciones=%s alarmas=%s speedup=%sx demanda=%.3f (computo=%.2fs, espera=%.2fs).",
                simulation_id,
                period + 1,
                total_periods_str,
                simulated_time.isoformat(),
                len(tick.measurements),
                len(tick.collections),
                len(tick.alarms),
                speedup,
                controls.global_current,
                tick_elapsed,
                remaining_delay,
            )

            if not await _wait_between_ticks(
                client,
                simulation_id,
                remaining_delay,
                simulator=simulator,
                speedup=speedup,
                current_sim_time=simulated_time,
            ):
                break
        else:
            await client.finish_session(simulation_id, "completed")
            logger.info("Simulacion %s completada.", simulation_id)
    except asyncio.CancelledError:
        await client.finish_session(simulation_id, "failed", "Worker cancelado.")
        raise
    except Exception as exc:
        logger.exception("La simulacion %s fallo.", simulation_id)
        await client.finish_session(simulation_id, "failed", str(exc))


async def _wait_until_runnable(
    client: SimulationSessionClient, simulation_id: int
) -> dict | None:
    settings = get_settings()
    paused_logged = False
    missed_polls = 0
    while True:
        session = await client.fetch_active_session()
        if session is None:
            missed_polls += 1
            if missed_polls >= settings.simulator_control_miss_tolerance:
                logger.warning(
                    "Simulacion %s: no se pudo confirmar sesion activa tras %s intentos.",
                    simulation_id,
                    missed_polls,
                )
                return None
            logger.info(
                "Simulacion %s: consulta de control sin respuesta, reintentando (%s/%s).",
                simulation_id,
                missed_polls,
                settings.simulator_control_miss_tolerance,
            )
            await asyncio.sleep(settings.simulator_poll_seconds)
            continue

        if session.id != simulation_id:
            return None
        missed_polls = 0
        status = session.status
        if status == "stopping":
            logger.info(
                "Simulacion %s detenida por solicitud de control.", simulation_id
            )
            await client.finish_session(simulation_id, "completed")
            return None
        if status in ("running", "pending"):
            if status == "pending":
                await client.update_progress(simulation_id, {"status": "running"})
                session.status = "running"
            if paused_logged:
                logger.info("Simulacion %s reanudada.", simulation_id)
            return session
        if status not in ACTIVE_STATUSES:
            return None
        if status == "paused" and not paused_logged:
            logger.info("Simulacion %s pausada.", simulation_id)
            paused_logged = True
        await asyncio.sleep(settings.simulator_poll_seconds)


def _remaining_tick_delay(
    frequency_minutes: int,
    speedup: float,
    elapsed_seconds: float,
) -> float:
    target_seconds = frequency_minutes * 60 / speedup
    return max(0.0, target_seconds - elapsed_seconds)


async def _wait_between_ticks(
    client: SimulationSessionClient,
    simulation_id: int,
    delay_seconds: float,
    simulator: object | None = None,
    speedup: float = 1.0,
    current_sim_time: datetime | None = None,
) -> bool:
    settings = get_settings()
    remaining = delay_seconds
    missed_polls = 0

    while remaining > 0:
        started = asyncio.get_running_loop().time()
        step_interval = min(settings.simulator_poll_seconds, remaining)
        await asyncio.sleep(step_interval)
        elapsed = asyncio.get_running_loop().time() - started
        remaining = max(0.0, remaining - elapsed)

        session = await client.fetch_active_session()
        if session is None:
            missed_polls += 1
            if missed_polls >= settings.simulator_control_miss_tolerance:
                logger.warning(
                    "Simulacion %s: se perdio confirmacion de control tras %s intentos.",
                    simulation_id,
                    missed_polls,
                )
                return False
            logger.info(
                "Simulacion %s: fallo transitorio consultando control, reintentando (%s/%s).",
                simulation_id,
                missed_polls,
                settings.simulator_control_miss_tolerance,
            )
            continue

        if session.id != simulation_id:
            return False
        missed_polls = 0
        status = session.status
        if status == "stopping":
            logger.info(
                "Simulacion %s detenida por solicitud de control.", simulation_id
            )
            await client.finish_session(simulation_id, "completed")
            return False
        if (
            status == "paused"
            and await _wait_until_runnable(client, simulation_id) is None
        ):
            return False
    return True


def _parse_iso(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    try:
        return _as_utc(datetime.fromisoformat(str(value)))
    except (ValueError, TypeError):
        return None


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
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
