from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from app.core.config import get_settings
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


def _http_get_json(url: str, timeout: float = 10.0) -> dict | None:
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data) if data else None
    except HTTPError as e:
        if e.code == 404:
            return None
        logger.warning("Error HTTP GET %s: %s", url, e)
        return None
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        logger.debug("Esperando conexion HTTP GET %s: %s", url, e)
        return None


def _http_post_json(
    url: str, payload: dict | None = None, method: str = "POST", timeout: float = 30.0
) -> dict | None:
    data_bytes = (
        json.dumps(payload, default=str).encode("utf-8") if payload is not None else b""
    )
    req = Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data) if data else None
    except HTTPError as e:
        if e.code == 404:
            return None
        logger.warning("Error HTTP %s %s: %s", method, url, e)
        return None
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        logger.debug("Esperando conexion HTTP %s %s: %s", method, url, e)
        return None


async def run_worker() -> None:
    settings = get_settings()
    logger.info("Worker de simulacion iniciado (HTTP Backend mode).")
    first_wait = True
    while True:
        success = await _mark_interrupted_sessions_failed(
            settings.simulator_backend_url
        )
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
            active_session = await _fetch_active_session(settings.simulator_backend_url)
        except Exception:
            logger.exception("No se pudo consultar la siguiente simulacion activa.")
            await asyncio.sleep(settings.simulator_poll_seconds)
            continue

        if active_session is None or active_session.get("status") not in (
            "pending",
            "running",
            "paused",
        ):
            await asyncio.sleep(settings.simulator_poll_seconds)
            continue

        await _run_session(active_session)


async def _fetch_active_session(backend_url: str) -> dict | None:
    url = urljoin(backend_url.rstrip("/") + "/", "digital-twin/worker/active-session")
    return await asyncio.to_thread(_http_get_json, url)


async def _mark_interrupted_sessions_failed(backend_url: str) -> bool:
    url = urljoin(backend_url.rstrip("/") + "/", "digital-twin/worker/fail-interrupted")
    res = await asyncio.to_thread(_http_post_json, url, {})
    return res is not None


async def _finish_session(
    backend_url: str, simulation_id: int, status: str, error_message: str | None = None
) -> None:
    url = urljoin(
        backend_url.rstrip("/") + "/",
        f"digital-twin/worker/simulations/{simulation_id}/finish",
    )
    await asyncio.to_thread(
        _http_post_json, url, {"status": status, "error_message": error_message}
    )


async def _update_progress(
    backend_url: str, simulation_id: int, progress: dict
) -> dict | None:
    url = urljoin(
        backend_url.rstrip("/") + "/",
        f"digital-twin/worker/simulations/{simulation_id}/progress",
    )
    return await asyncio.to_thread(_http_post_json, url, progress, method="PATCH")


async def _run_session(session: dict) -> None:
    settings = get_settings()
    simulation_id = session["id"]
    logger.info("Iniciando simulacion %s.", simulation_id)
    try:
        config = scenario_from_mapping(session["scenario"])
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
        await _update_progress(
            settings.simulator_backend_url,
            simulation_id,
            {"status": "running"},
        )

        delivery_sem = asyncio.Semaphore(3)
        pending_deliveries: list[asyncio.Task] = []

        previous_controls: ControlSnapshot | None = None
        import itertools

        period_iterator = (
            range(config.periods) if config.end is not None else itertools.count()
        )
        for period in period_iterator:
            session_state = await _wait_until_runnable(
                settings.simulator_backend_url, simulation_id
            )
            if session_state is None:
                break

            simulated_time = config.start + timedelta(
                minutes=period * config.frequency_minutes
            )
            simulated_time = _as_utc(simulated_time)

            global_multiplier = effective_multiplier(
                session_state.get("global_demand_current", 1.0),
                session_state.get("global_demand_target", 1.0),
                simulated_time,
                _parse_iso(session_state.get("transition_started_at")),
                _parse_iso(session_state.get("transition_ends_at")),
            )

            overrides = session_state.get("zone_overrides", [])
            controls = ControlSnapshot(
                speedup=session_state.get("speedup", 60.0),
                global_current=global_multiplier,
                global_target=session_state.get("global_demand_target", 1.0),
                zones=tuple(
                    sorted(
                        (
                            item["neighborhood"],
                            item["multiplier_current"],
                            item["multiplier_target"],
                        )
                        for item in overrides
                    )
                ),
            )

            if _control_targets(controls) != _control_targets(previous_controls):
                _log_control_change(simulation_id, previous_controls, controls)
            previous_controls = controls

            tick_started = asyncio.get_running_loop().time()

            zone_multipliers = {
                name: current for name, current, _target in controls.zones
            }
            tick = simulator.run_tick(
                simulated_time,
                global_demand_multiplier=(
                    controls.global_current * config.high_demand_multiplier
                ),
                zone_multiplier=lambda zone, multipliers=zone_multipliers: (
                    multipliers.get(zone, 1.0)
                ),
            )

            # Enviar mediciones en background sin bloquear el reloj de simulación
            total_periods_val = config.periods if config.end is not None else 0
            task = asyncio.create_task(
                _deliver_in_background(
                    delivery_sem,
                    simulation_id,
                    tick.measurements,
                    settings.simulator_backend_url,
                    period + 1,
                    total_periods_val,
                )
            )
            pending_deliveries.append(task)
            pending_deliveries = [t for t in pending_deliveries if not t.done()]

            # Actualizar progreso en la API
            trucks_snapshot = (
                simulator.truck_fleet.get_trucks_snapshot()
                if getattr(simulator, "truck_fleet", None)
                else []
            )
            await _update_progress(
                settings.simulator_backend_url,
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

            total_periods_str = str(config.periods) if config.end is not None else "∞"
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
                settings.simulator_backend_url,
                simulation_id,
                remaining_delay,
                simulator=simulator,
                speedup=speedup,
                current_sim_time=simulated_time,
            ):
                break
        else:
            if pending_deliveries:
                await asyncio.gather(*pending_deliveries, return_exceptions=True)
            await _finish_session(
                settings.simulator_backend_url, simulation_id, "completed"
            )
            logger.info("Simulacion %s completada.", simulation_id)
    except asyncio.CancelledError:
        await _finish_session(
            settings.simulator_backend_url, simulation_id, "failed", "Worker cancelado."
        )
        raise
    except Exception as exc:
        logger.exception("La simulacion %s fallo.", simulation_id)
        await _finish_session(
            settings.simulator_backend_url, simulation_id, "failed", str(exc)
        )


async def _wait_until_runnable(backend_url: str, simulation_id: int) -> dict | None:
    settings = get_settings()
    paused_logged = False
    missed_polls = 0
    while True:
        session = await _fetch_active_session(backend_url)
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

        if session.get("id") != simulation_id:
            return None
        missed_polls = 0
        status = session.get("status")
        if status == "stopping":
            logger.info(
                "Simulacion %s detenida por solicitud de control.", simulation_id
            )
            await _finish_session(backend_url, simulation_id, "completed")
            return None
        if status in ("running", "pending"):
            if status == "pending":
                await _update_progress(
                    backend_url, simulation_id, {"status": "running"}
                )
                session["status"] = "running"
            if paused_logged:
                logger.info("Simulacion %s reanudada.", simulation_id)
            return session
        if status not in ACTIVE_STATUSES:
            return None
        if status == "paused" and not paused_logged:
            logger.info("Simulacion %s pausada.", simulation_id)
            paused_logged = True
        await asyncio.sleep(settings.simulator_poll_seconds)


async def _deliver_tick_measurements(
    measurements: list[Measurement],
    backend_url: str,
) -> DeliveryReport:
    if not measurements:
        return DeliveryReport(sent=0, updated=0, not_found=0, requests=0)

    settings = get_settings()
    return await asyncio.to_thread(
        send_measurements_batch,
        measurements,
        backend_url,
        batch_size=settings.simulator_batch_size,
    )


async def _deliver_in_background(
    sem: asyncio.Semaphore,
    simulation_id: int,
    measurements: list[Measurement],
    backend_url: str,
    tick_number: int,
    total_ticks: int,
) -> None:
    async with sem:
        try:
            report = await _deliver_tick_measurements(measurements, backend_url)
            logger.debug(
                "Simulacion %s tick %s/%s entrega: enviadas=%s actualizadas=%s no_encontradas=%s.",
                simulation_id,
                tick_number,
                total_ticks,
                report.sent,
                report.updated,
                report.not_found,
            )
        except Exception:
            logger.exception(
                "Simulacion %s tick %s/%s: error enviando mediciones.",
                simulation_id,
                tick_number,
                total_ticks,
            )


def _remaining_tick_delay(
    frequency_minutes: int,
    speedup: float,
    elapsed_seconds: float,
) -> float:
    target_seconds = frequency_minutes * 60 / speedup
    return max(0.0, target_seconds - elapsed_seconds)


async def _wait_between_ticks(
    backend_url: str,
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

        session = await _fetch_active_session(backend_url)
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

        if session.get("id") != simulation_id:
            return False
        missed_polls = 0
        status = session.get("status")
        if status == "stopping":
            logger.info(
                "Simulacion %s detenida por solicitud de control.", simulation_id
            )
            await _finish_session(backend_url, simulation_id, "completed")
            return False
        if (
            status == "paused"
            and await _wait_until_runnable(backend_url, simulation_id) is None
        ):
            return False
    return True


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
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
