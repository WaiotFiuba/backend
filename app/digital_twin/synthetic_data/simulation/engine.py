from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from app.digital_twin.synthetic_data.zone_classifier import get_zone_classifier

from app.digital_twin.synthetic_data.domain.entities import (
    Alarm,
    CollectionEvent,
    Container,
    Device,
    Measurement,
    Site,
)
from app.digital_twin.synthetic_data.generators.anomalies import (
    alarm_from_measurement,
)
from app.digital_twin.synthetic_data.generators.street_pairing import (
    build_opposing_sites_map,
)
from app.digital_twin.synthetic_data.generators.topology import (
    generate_synthetic_topology,
)
from app.digital_twin.synthetic_data.simulation.clock import iter_timestamps
from app.digital_twin.synthetic_data.simulation.scenario import ScenarioConfig
from app.digital_twin.synthetic_data.topology import SimulationTopology

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimulationResult:
    sites: list[Site]
    containers: list[Container]
    devices: list[Device]
    measurements: list[Measurement]
    collections: list[CollectionEvent]
    alarms: list[Alarm]


@dataclass
class SimulationState:
    topology: SimulationTopology
    site_by_id: dict[str, Site]
    device_by_container_id: dict[str, Device]
    levels: dict[str, float]
    batteries: dict[str, float]
    reading_offsets: dict[str, int]
    stuck_distances: dict[str, float]
    opposing_site_by_site_id: dict[str, str]


class SyntheticDataSimulator:
    def __init__(
        self, config: ScenarioConfig, topology: SimulationTopology | None = None
    ):
        self.config = config
        self.rng = random.Random(config.seed)
        self.topology = topology
        self.state: SimulationState | None = None

    def initialize(self) -> SimulationState:
        import numpy as np

        self.rng = random.Random(self.config.seed)
        self.np_rng = np.random.default_rng(self.config.seed)
        topology = self.topology or generate_synthetic_topology(self.config, self.rng)
        opposing_sites = build_opposing_sites_map(topology.sites)
        self.state = SimulationState(
            topology=topology,
            site_by_id={site.id: site for site in topology.sites},
            device_by_container_id={
                device.container_id: device for device in topology.devices
            },
            levels=dict(topology.initial_levels),
            batteries={
                device.id: self.rng.uniform(70, 100) for device in topology.devices
            },
            reading_offsets={
                device.id: self.rng.randint(
                    -self.config.reading_jitter_minutes,
                    self.config.reading_jitter_minutes,
                )
                for device in topology.devices
            },
            stuck_distances={},
            opposing_site_by_site_id=opposing_sites,
        )

        try:
            from app.services.simulation.truck_route_service import (
                assign_sites_to_routes,
                load_routes_from_csv,
            )
            from app.digital_twin.synthetic_data.simulation.truck_engine import (
                TruckFleetSimulator,
            )

            # Carga las definiciones de circuitos/rutas de recolección de camiones desde el archivo CSV
            # Retorna un diccionario {route_id: TruckRoute} con metadatos de zona, paradas y coordenadas
            routes = load_routes_from_csv()

            # Normaliza la lista de sitios de la topología actual
            # necesario para el algoritmo de asignación geográfica por calle y altura:
            # - 'id': Identificador único del sitio/contenedor
            # - 'address': Dirección normalizada (calle y altura) para vincular con los circuitos de la ruta
            # - 'name': Nombre de referencia del sitio
            # - 'latitude' / 'longitude': Coordenadas GPS para asignación espacial por cercanía y distancias
            sites_raw = [
                {
                    "id": s.id,
                    "address": s.address,
                    "name": s.name,
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                }
                for s in topology.sites
            ]
            assign_sites_to_routes(sites_raw, routes)
            sites_dict = {s.id: (s.latitude, s.longitude) for s in topology.sites}
            self.truck_fleet = TruckFleetSimulator(
                routes=routes,
                sites_dict=sites_dict,
                collection_hours=self.config.collection_hours,
                collection_threshold_pct=60.0,
            )
        except Exception as e:
            logger.warning("No se pudo inicializar la flota de camiones: %s", e)
            self.truck_fleet = None

        return self.state

    def run(
        self,
        global_demand_multiplier: float | None = None,
        zone_multiplier: Callable[[str], float] | None = None,
    ) -> SimulationResult:
        state = self.state or self.initialize()
        measurements: list[Measurement] = []
        collection_events: list[CollectionEvent] = []
        alarms: list[Alarm] = []

        for timestamp in iter_timestamps(
            self.config.start,
            self.config.periods,
            self.config.frequency_minutes,
        ):
            tick = self.run_tick(
                timestamp,
                global_demand_multiplier=global_demand_multiplier,
                zone_multiplier=zone_multiplier,
            )
            measurements.extend(tick.measurements)
            collection_events.extend(tick.collections)
            alarms.extend(tick.alarms)

        return SimulationResult(
            sites=state.topology.sites,
            containers=state.topology.containers,
            devices=state.topology.devices,
            measurements=measurements,
            collections=collection_events,
            alarms=alarms,
        )

    def run_tick(
        self,
        timestamp: datetime,
        global_demand_multiplier: float | None = None,
        zone_multiplier: Callable[[str], float] | None = None,
    ) -> SimulationResult:
        import numpy as np
        from app.digital_twin.synthetic_data.generators.filling import (
            _hour_factor,
            _weekday_factor,
        )
        from app.digital_twin.synthetic_data.generators.sensors import _daily_wave

        state = self.state or self.initialize()
        containers = state.topology.containers
        N = len(containers)
        if N == 0:
            return SimulationResult(
                sites=state.topology.sites,
                containers=state.topology.containers,
                devices=state.topology.devices,
                measurements=[],
                collections=[],
                alarms=[],
            )

        if not hasattr(state, "_cached_arrays"):
            # Cache static mappings to avoid rebuilding on every tick
            site_list = []
            device_list = []
            demand_bases = []
            waste_factors = []
            heights = []
            reading_offsets = []

            from collections import defaultdict

            containers_by_site_and_waste: dict[tuple[str, str], list[int]] = (
                defaultdict(list)
            )
            containers_by_site: dict[str, list[int]] = defaultdict(list)

            for idx, container in enumerate(containers):
                site = state.site_by_id[container.site_id]
                device = state.device_by_container_id[container.id]
                site_list.append(site)
                device_list.append(device)
                demand_bases.append(site.demand_base)
                waste_factors.append(
                    self.config.waste_type_factors.get(container.waste_type, 1.0)
                )
                heights.append(container.height_cm)
                reading_offsets.append(state.reading_offsets[device.id])

                containers_by_site_and_waste[
                    (container.site_id, container.waste_type)
                ].append(idx)
                containers_by_site[container.site_id].append(idx)

            state._cached_sites = site_list
            state._cached_devices = device_list
            state._cached_demand_bases = np.array(demand_bases, dtype=np.float64)
            state._cached_waste_factors = np.array(waste_factors, dtype=np.float64)
            state._cached_heights = np.array(heights, dtype=np.float64)
            state._cached_reading_offsets = np.array(reading_offsets, dtype=np.int32)
            state._cached_containers_by_site_and_waste = containers_by_site_and_waste
            state._cached_containers_by_site = containers_by_site
            state._cached_arrays = True

        measurements: list[Measurement] = []
        collection_events: list[CollectionEvent] = []
        alarms: list[Alarm] = []

        # Get current state as arrays
        levels = np.array(
            [
                state.levels.get(
                    c.id,
                    state.levels.get(
                        str(c.id),
                        state.levels.get(
                            int(c.id) if str(c.id).isdigit() else c.id, 0.0
                        ),
                    ),
                )
                for c in containers
            ],
            dtype=np.float64,
        )
        batteries = np.array(
            [state.batteries[d.id] for d in state._cached_devices], dtype=np.float64
        )

        # 1. Calculate filling increments and spillover to opposing sites

        if zone_multiplier:
            # Multiplicador externo explícito (override desde el front u otros callers)
            zone_mults = np.array(
                [zone_multiplier(site.zone) for site in state._cached_sites],
                dtype=np.float64,
            )
            # Mantener los factores globales horarios/semanales como fallback
            h_factor = _hour_factor(timestamp.hour)
            wd_factor = _weekday_factor(timestamp.weekday())
            time_factor = h_factor * wd_factor
        else:
            # ZoneClassifier: cada sitio recibe su multiplicador diferenciado
            # según el tipo de zona (barrio) — demand_multiplier × hour_weight × weekday_factor
            zone_classifier = get_zone_classifier()
            zone_mults = np.array(
                [
                    zone_classifier.get_multiplier(
                        site.zone, timestamp.hour, timestamp.weekday()
                    )
                    for site in state._cached_sites
                ],
                dtype=np.float64,
            )
            # time_factor ya está embebido en zone_mults (el ZoneClassifier combina todo)
            time_factor = 1.0

            # ── Calibración global de demanda ──────────────────────────────────
            # Verifica si el promedio del zone_mults se mantiene dentro del rango
            # aceptable configurado en zone_profiles.yaml (target ± tolerance_pct).
            # Si se sale del rango, normaliza el array para llevarlo al target.
            # Esto garantiza que el volumen global de residuos simulado tienda a
            # target_zone_mult × 1.5 kg/habitante/día (fuente CEAMSE/INDEC).
            current_mean = float(zone_mults.mean())
            target = zone_classifier.calibration_target
            tol = zone_classifier.calibration_tolerance_pct / 100.0
            lower = target * (1.0 - tol)
            upper = target * (1.0 + tol)
            if current_mean > 0.0 and not (lower <= current_mean <= upper):
                logger.warning(
                    "zone_mults media=%.4f fuera del rango [%.4f, %.4f]. "
                    "Normalizando al target=%.4f.",
                    current_mean, lower, upper, target,
                )
                zone_mults = zone_mults * (target / current_mean)

        global_mult = (
            self.config.high_demand_multiplier
            if global_demand_multiplier is None
            else global_demand_multiplier
        )

        noises = self.np_rng.lognormal(0.0, 0.18, size=N)
        noises = np.maximum(0.2, noises)
        time_ratio = self.config.frequency_minutes / 60.0

        increments = (
            state._cached_demand_bases
            * time_ratio
            * time_factor
            * state._cached_waste_factors
            * global_mult
            * zone_mults
            * self.config.overflow_stress_multiplier
            * noises
        )
        increments = np.round(increments, 4)

        # Traspaso de exceso a sitios de enfrente si un contenedor/sitio está al 100%
        initial_levels_tick = levels.copy()
        tentative_levels = initial_levels_tick + increments

        from collections import defaultdict

        opposing_spillovers: dict[int, float] = defaultdict(float)

        for i in range(N):
            container = containers[i]
            site_id = container.site_id
            opposing_site_id = state.opposing_site_by_site_id.get(site_id)

            if opposing_site_id:
                if initial_levels_tick[i] >= 100.0:
                    excess = increments[i]
                elif tentative_levels[i] > 100.0:
                    excess = tentative_levels[i] - 100.0
                else:
                    excess = 0.0

                if excess > 0.0:
                    targets = state._cached_containers_by_site_and_waste.get(
                        (opposing_site_id, container.waste_type)
                    ) or state._cached_containers_by_site.get(opposing_site_id, [])

                    if targets:
                        share = excess / len(targets)
                        for target_idx in targets:
                            opposing_spillovers[target_idx] += share

        for target_idx, added_level in opposing_spillovers.items():
            tentative_levels[target_idx] += added_level

        levels = np.minimum(100.0, tentative_levels)

        # 2. Collections (Simulación con Flota de Camiones)
        level_before_collection = levels.copy()
        collected = np.zeros(N, dtype=bool)

        if getattr(self, "truck_fleet", None) is not None:
            containers_by_site_dict = defaultdict(list)
            for i in range(N):
                c = containers[i]
                item = {
                    "id": c.id,
                    "index": i,
                    "current_level": float(levels[i]),
                    "waste_type": c.waste_type,
                }
                # Registrar todos los alias posibles (ID contenedor, ID sitio, serie_id)
                keys_to_index = {
                    c.id,
                    str(c.id),
                    c.site_id,
                    str(c.site_id),
                }
                if getattr(c, "serie_id", None):
                    keys_to_index.add(c.serie_id)
                    keys_to_index.add(str(c.serie_id).split("|")[-1])

                c_id_raw = str(c.id).split("|")[-1]
                keys_to_index.add(c_id_raw)
                keys_to_index.add(f"contenedores_negros|{c_id_raw}")
                keys_to_index.add(f"SITE-{c_id_raw}")

                site_id_raw = str(c.site_id).split("|")[-1]
                keys_to_index.add(site_id_raw)
                keys_to_index.add(f"contenedores_negros|{site_id_raw}")
                keys_to_index.add(f"SITE-{site_id_raw}")

                for k in keys_to_index:
                    if k is not None:
                        containers_by_site_dict[k].append(item)
            truck_events = self.truck_fleet.step(
                simulated_time=timestamp,
                dt_seconds=self.config.frequency_minutes * 60.0,
                speedup=1.0,
                containers_by_site=containers_by_site_dict,
            )
            self.truck_fleet.get_trucks_snapshot()

            for ev in truck_events:
                c_id = ev["container_id"]
                for i in range(N):
                    if containers[i].id == c_id or str(containers[i].id) == str(c_id):
                        levels[i] = ev["level_after"]
                        state.levels[containers[i].id] = ev["level_after"]
                        state.levels[str(containers[i].id)] = ev["level_after"]
                        collected[i] = True
                        reading_timestamp = timestamp + timedelta(
                            minutes=int(state._cached_reading_offsets[i])
                        )
                        collection_events.append(
                            CollectionEvent(
                                timestamp=reading_timestamp,
                                container_id=str(containers[i].id),
                                kind="total",
                                level_before_pct=float(np.round(ev["level_before"], 2)),
                                level_after_pct=float(np.round(ev["level_after"], 2)),
                                detected_by_sensor=True,
                            )
                        )
                        break

        else:
            is_collection_hour = timestamp.hour in self.config.collection_hours
            if is_collection_hour:
                omitted_roll = self.np_rng.random(size=N)
                not_omitted = omitted_roll >= self.config.omitted_collection_probability

                threshold = 62.0 if timestamp.weekday() < 5 else 55.0
                above_threshold = levels >= threshold

                collect_roll = self.np_rng.random(size=N)
                will_collect = collect_roll < self.config.collection_probability

                collected = not_omitted & above_threshold & will_collect
            else:
                collected = np.zeros(N, dtype=bool)

            partial_roll = self.np_rng.random(size=N)
            is_partial = partial_roll < self.config.partial_collection_probability

            reduction = self.np_rng.uniform(25.0, 55.0, size=N)
            total_val = self.np_rng.uniform(0.0, 8.0, size=N)

            partial_level = np.maximum(0.0, levels - reduction)
            new_levels_if_collected = np.where(is_partial, partial_level, total_val)
            new_levels_if_collected = np.round(new_levels_if_collected, 2)

            levels = np.where(collected, new_levels_if_collected, levels)

            collected_indices = np.where(collected)[0]
            for idx in collected_indices:
                container = containers[idx]
                reading_timestamp = timestamp + timedelta(
                    minutes=int(state._cached_reading_offsets[idx])
                )
                kind = "partial" if is_partial[idx] else "total"
                collection_events.append(
                    CollectionEvent(
                        timestamp=reading_timestamp,
                        container_id=container.id,
                        kind=kind,
                        level_before_pct=float(
                            np.round(level_before_collection[idx], 2)
                        ),
                        level_after_pct=float(levels[idx]),
                        detected_by_sensor=bool(
                            (level_before_collection[idx] - levels[idx]) >= 20.0
                        ),
                    )
                )

        collection_detected = (level_before_collection - levels) >= 20.0

        # 3. Anomalies
        anomaly_rolls = self.np_rng.random(size=N)
        stuck_p = self.config.stuck_sensor_probability
        noisy_p = self.config.noisy_sensor_probability

        anomalies = np.empty(N, dtype=object)
        anomalies[:] = None
        anomalies[anomaly_rolls < stuck_p] = "sensor_trabado"
        anomalies[(anomaly_rolls >= stuck_p) & (anomaly_rolls < stuck_p + noisy_p)] = (
            "sensor_ruidoso"
        )

        fire_rolls = self.np_rng.random(size=N)
        fire = fire_rolls < self.config.fire_probability

        signal_rolls = self.np_rng.random(size=N)
        signal_lost = signal_rolls < self.config.signal_loss_probability

        low_battery_rolls = self.np_rng.random(size=N)
        low_battery = low_battery_rolls < self.config.low_battery_probability

        # 4. Sensor readings
        is_noisy_anomaly = anomalies == "sensor_ruidoso"
        sigmas = np.where(is_noisy_anomaly, 6.5, 1.2)
        ultrasonic_noises = self.np_rng.normal(0.0, sigmas)

        empty_distances = state._cached_heights
        calculated_distances = (
            empty_distances * (1.0 - levels / 100.0) + ultrasonic_noises
        )
        calculated_distances = np.maximum(2.0, calculated_distances)
        calculated_distances = np.round(calculated_distances, 2)

        # Handle stuck distances
        for idx, container in enumerate(containers):
            anom = anomalies[idx]
            if anom == "sensor_trabado":
                if container.id not in state.stuck_distances:
                    state.stuck_distances[container.id] = float(
                        calculated_distances[idx]
                    )
            else:
                state.stuck_distances.pop(container.id, None)

        final_distances = calculated_distances.copy()
        for idx, container in enumerate(containers):
            stuck_val = state.stuck_distances.get(container.id)
            if stuck_val is not None:
                final_distances[idx] = stuck_val

        # Batteries
        discharges = self.np_rng.uniform(0.002, 0.025, size=N)
        normal_batteries = np.maximum(0.0, batteries - discharges)
        force_low_vals = self.np_rng.uniform(3.0, 14.0, size=N)
        final_batteries = np.where(low_battery, force_low_vals, normal_batteries)
        final_batteries = np.round(final_batteries, 2)

        for idx, device in enumerate(state._cached_devices):
            state.batteries[device.id] = float(final_batteries[idx])

        # Signal RSSI
        lost_rssi = self.np_rng.uniform(-125.0, -116.0, size=N)
        normal_rssi = self.np_rng.normal(-76.0, 8.0, size=N)
        rssis = np.where(signal_lost, lost_rssi, normal_rssi)
        rssis = np.round(rssis, 2)

        # Temperature
        base_temp = 19.0 + 7.0 * _daily_wave(timestamp.hour)
        fire_temps = self.np_rng.uniform(75.0, 130.0, size=N)
        normal_temps = base_temp + self.np_rng.normal(0.0, 1.8, size=N)
        temps = np.where(fire, fire_temps, normal_temps)
        temps = np.round(temps, 2)

        # Acceleration
        coll_acc = self.np_rng.uniform(1.45, 3.4, size=N)
        normal_acc = self.np_rng.normal(0.03, 0.025, size=N)
        normal_acc = np.maximum(0.0, normal_acc)
        accelerations = np.where(collected, coll_acc, normal_acc)
        accelerations = np.round(accelerations, 3)

        # 5. Build results & update state
        is_collection_detected = collection_detected & (accelerations >= 1.2)
        final_anomalies = np.where(fire, "incendio", anomalies)

        for idx, container in enumerate(containers):
            state.levels[container.id] = float(levels[idx])

            site = state._cached_sites[idx]
            device = state._cached_devices[idx]
            reading_timestamp = timestamp + timedelta(
                minutes=int(state._cached_reading_offsets[idx])
            )

            measurement = Measurement(
                timestamp=reading_timestamp,
                site_id=site.id,
                container_id=container.id,
                device_id=device.id,
                fill_level_pct=float(levels[idx]),
                ultrasonic_distance_cm=float(final_distances[idx]),
                battery_pct=float(final_batteries[idx]),
                signal_rssi_dbm=float(rssis[idx]),
                temperature_c=float(temps[idx]),
                acceleration_g=float(accelerations[idx]),
                is_collection_detected=bool(is_collection_detected[idx]),
                anomaly=final_anomalies[idx],
            )
            measurements.append(measurement)
            alarm = alarm_from_measurement(measurement)
            if alarm is not None:
                alarms.append(alarm)

        return SimulationResult(
            sites=state.topology.sites,
            containers=state.topology.containers,
            devices=state.topology.devices,
            measurements=measurements,
            collections=collection_events,
            alarms=alarms,
        )
