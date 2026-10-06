import asyncio
import itertools
import logging
from datetime import UTC, datetime, timedelta

from simulator.config.settings import get_settings
from simulator.demography.commands.process_land_use import process_land_use_async
from simulator.density_processor import get_density_processor
from simulator.domain.entities import (
    SimulationSession,
    SimulationStatus,
)
from simulator.exporters.zone_profiles import build_zone_profiles_geojson
from simulator.loaders.backend_http import (
    load_topology_from_backend_api,
)
from simulator.simulation.controls import ControlSnapshot
from simulator.simulation.engine import SyntheticDataSimulator
from simulator.simulation.scenario import scenario_from_mapping, scenario_to_record
from simulator.transport.session_client import (
    SimulationSessionClient,
)
from simulator.transport.telemetry_sender import (
    deliver_tick_measurements,
)
from simulator.zone_classifier import reload_zone_classifier

logger = logging.getLogger(__name__)


# Todos los contenedores: el bbox cubre el mundo entero.
_ALL_CONTAINERS_PATH = (
    "/map/containers/bbox?lat_min=-90&lat_max=90&lng_min=-180&lng_max=180&zoom=18"
)


async def run_worker() -> None:
    settings = get_settings()
    client = SimulationSessionClient(settings.backend_url)
    logger.info("Worker de simulacion iniciado (HTTP Backend mode).")
    await _prepare_derived_data()
    first_wait = True
    while True:
        success = await client.fail_interrupted_sessions()
        if success:
            logger.info("Conectado con éxito a la API. Esperando simulaciones...")
            break
        if first_wait:
            logger.info(
                "Esperando a que el backend de la API finalice su inicialización en %s...",
                settings.backend_url,
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
            SimulationStatus.PENDING,
            SimulationStatus.RUNNING,
            SimulationStatus.PAUSED,
        ):
            await asyncio.sleep(settings.simulator_poll_seconds)
            continue

        await _run_session(client, active_session)


async def _prepare_derived_data() -> None:
    """Datos derivados que usa el simulador (antes los generaba el seed del
    backend): uso del suelo por radio censal. Se regenera solo si hace falta;
    si algo falla, el worker sigue igual."""
    try:
        await process_land_use_async()
    except Exception:
        logger.exception("No se pudo procesar el uso del suelo.")


async def _run_session(
    client: SimulationSessionClient, session: SimulationSession
) -> None:
    settings = get_settings()
    simulation_id = session.id
    logger.info("Iniciando simulacion %s.", simulation_id)
    try:
        config = scenario_from_mapping(session.scenario)
        # Releer zone_profiles.yaml: cada sesion usa el YAML vigente al iniciarla.
        classifier = await asyncio.to_thread(reload_zone_classifier)
        topology = await asyncio.to_thread(
            load_topology_from_backend_api,
            settings.backend_url,
            _ALL_CONTAINERS_PATH,
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

        # Publicar la capa de perfiles de zona con la que corre esta sesion, para
        # el mapa del front. Si falla, la simulacion sigue igual.
        layer = await asyncio.to_thread(
            build_zone_profiles_geojson, get_density_processor(), classifier
        )
        if not await client.publish_zone_profiles(simulation_id, layer):
            logger.warning(
                "Simulacion %s: no se pudo publicar la capa de perfiles de zona.",
                simulation_id,
            )

        # Marcar la sesión como running en el backend tras completar la carga e
        # inicialización, junto con el escenario efectivo (con los defaults del
        # simulador) y la cantidad real de periodos.
        await client.update_progress(
            simulation_id,
            {
                "status": SimulationStatus.RUNNING,
                "scenario": scenario_to_record(config),
                "total_periods": max(config.periods, 0),
            },
        )

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

            controls = ControlSnapshot.from_session(session_state)
            if previous_controls is None or (
                controls.targets() != previous_controls.targets()
            ):
                _log_control_change(simulation_id, previous_controls, controls)
            previous_controls = controls

            tick_started = asyncio.get_running_loop().time()

            tick = simulator.run_tick(
                simulated_time,
                global_demand_multiplier=(
                    controls.global_current * config.high_demand_multiplier
                ),
                zone_multiplier=controls.zone_multiplier_fn(),
            )

            # 1. Enviar y persistir mediciones en la BD ANTES de avanzar el reloj
            await deliver_tick_measurements(
                tick.measurements,
                settings.backend_url,
                settings.simulator_batch_size,
            )

            # 2. Con los datos 100% guardados en Postgres, publicar el progreso y la hora simulada
            await client.update_progress(
                simulation_id,
                {
                    "simulated_time": simulated_time.isoformat(),
                    "current_period": period + 1,
                    "global_demand_current": controls.global_current,
                    "measurements_sent": len(tick.measurements),
                    "collections_generated": len(tick.collections),
                    "alarms_generated": len(tick.alarms),
                },
            )

            tick_elapsed = asyncio.get_running_loop().time() - tick_started
            speedup = controls.speedup
            remaining_delay = _remaining_tick_delay(
                config.frequency_minutes, speedup, tick_elapsed
            )

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

            if not await _wait_between_ticks(client, simulation_id, remaining_delay):
                break
        else:
            await client.finish_session(simulation_id, SimulationStatus.COMPLETED)
            logger.info("Simulacion %s completada.", simulation_id)
    except asyncio.CancelledError:
        await client.finish_session(
            simulation_id, SimulationStatus.FAILED, "Worker cancelado."
        )
        raise
    except Exception as exc:
        logger.exception("La simulacion %s fallo.", simulation_id)
        await client.finish_session(simulation_id, SimulationStatus.FAILED, str(exc))


async def _wait_until_runnable(
    client: SimulationSessionClient, simulation_id: int
) -> SimulationSession | None:
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
        if status == SimulationStatus.STOPPING:
            logger.info(
                "Simulacion %s detenida por solicitud de control.", simulation_id
            )
            await client.finish_session(simulation_id, SimulationStatus.COMPLETED)
            return None
        if status in (SimulationStatus.RUNNING, SimulationStatus.PENDING):
            if status == SimulationStatus.PENDING:
                await client.update_progress(
                    simulation_id, {"status": SimulationStatus.RUNNING}
                )
            if paused_logged:
                logger.info("Simulacion %s reanudada.", simulation_id)
            return session
        if status.is_finished:
            return None
        if status == SimulationStatus.PAUSED and not paused_logged:
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
        if status == SimulationStatus.STOPPING:
            logger.info(
                "Simulacion %s detenida por solicitud de control.", simulation_id
            )
            await client.finish_session(simulation_id, SimulationStatus.COMPLETED)
            return False
        if (
            status == SimulationStatus.PAUSED
            and await _wait_until_runnable(client, simulation_id) is None
        ):
            return False
    return True


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
