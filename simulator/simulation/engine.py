from __future__ import annotations

import logging
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from simulator.zone_classifier import get_zone_classifier
from simulator.domain.entities import (
    Alarm,
    CollectionEvent,
    Container,
    Device,
    Measurement,
    Site,
)
from simulator.generators.anomalies import (
    alarm_from_measurement,
)
from simulator.generators.filling import (
    apply_filling,
    calibration_factor,
    filling_increments,
    zone_multipliers,
)
from simulator.simulation.opposing_sites import (
    build_opposing_sites_map,
)
from simulator.generators.topology import (
    generate_synthetic_topology,
)
from simulator.simulation.clock import iter_timestamps
from simulator.simulation.scenario import ScenarioConfig
from simulator.topology import SimulationTopology

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
            from simulator.trucks.truck_routes import (
                build_collection_routes,
            )
            from simulator.trucks.truck_engine import (
                TruckFleetSimulator,
                _is_recyclable_container,
            )

            # Rutas de recolección armadas con los sitios de esta topología: las
            # paradas quedan con el id de sitio del backend, el mismo con el que
            # run_tick agrupa los contenedores. Un sitio va a las rutas de
            # húmedos si tiene algún contenedor húmedo y a las de secos si tiene
            # alguno reciclable (puede estar en las dos).
            site_streams: dict[str, set[bool]] = {}
            for container in topology.containers:
                site_streams.setdefault(str(container.site_id), set()).add(
                    _is_recyclable_container({"waste_type": container.waste_type})
                )
            site_records = [
                {
                    "id": s.id,
                    "address": s.address,
                    "name": s.name,
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                }
                for s in topology.sites
            ]
            routes = build_collection_routes(
                black_container_sites=[
                    s for s in site_records if False in site_streams.get(s["id"], ())
                ],
                green_container_sites=[
                    s for s in site_records if True in site_streams.get(s["id"], ())
                ],
            )

            sites_dict = {s.id: (s.latitude, s.longitude) for s in topology.sites}
            self.truck_fleet = TruckFleetSimulator(
                routes=routes,
                sites_dict=sites_dict,
                collection_hours=self.config.collection_hours,
                no_collection_days=self.config.no_collection_days,
                collection_threshold_pct=0.0,
                rng=self.rng,
            )
        except Exception as e:  # noqa: BLE001
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
        from simulator.generators.sensors import _daily_wave

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
            zone_profiles = []

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
                zone_profiles.append(get_zone_classifier().get_profile(site.zone))

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
            state._cached_zone_profiles = zone_profiles
            state._cached_calibration_factor = calibration_factor(zone_profiles)
            state._cached_containers_by_site_and_waste = containers_by_site_and_waste
            state._cached_containers_by_site = containers_by_site
            state._cached_arrays = True

        measurements: list[Measurement] = []
        collection_events: list[CollectionEvent] = []
        alarms: list[Alarm] = []

        # Get current state as arrays
        levels = np.array(
            [state.levels.get(c.id, 0.0) for c in containers], dtype=np.float64
        )
        batteries = np.array(
            [state.batteries[d.id] for d in state._cached_devices], dtype=np.float64
        )

        # 1. Llenado (ver simulator/generators/filling.py)
        zone_mults = zone_multipliers(
            state._cached_zone_profiles,
            timestamp,
            state._cached_calibration_factor,
            zone_multiplier,
        )
        global_mult = (
            self.config.high_demand_multiplier
            if global_demand_multiplier is None
            else global_demand_multiplier
        )
        increments = filling_increments(
            state._cached_demand_bases,
            state._cached_waste_factors,
            zone_mults,
            global_mult,
            self.config,
            self.np_rng,
        )
        levels = apply_filling(
            levels,
            increments,
            containers,
            state.opposing_site_by_site_id,
            state._cached_containers_by_site_and_waste,
            state._cached_containers_by_site,
        )

        # 2. Collections (Simulación con Flota de Camiones)
        level_before_collection = levels.copy()
        collected = np.zeros(N, dtype=bool)

        if getattr(self, "truck_fleet", None) is not None:
            # Contenedores de cada sitio, con su nivel actual, para los camiones.
            items = [
                {
                    "id": containers[i].id,
                    "index": i,
                    "current_level": float(levels[i]),
                    "waste_type": containers[i].waste_type,
                }
                for i in range(N)
            ]
            containers_by_site_dict = {
                str(site_id): [items[idx] for idx in indices]
                for site_id, indices in state._cached_containers_by_site.items()
            }

            truck_events = self.truck_fleet.step(
                simulated_time=timestamp,
                dt_seconds=self.config.frequency_minutes * 60.0,
                speedup=1.0,
                containers_by_site=containers_by_site_dict,
            )

            for ev in truck_events:
                idx = ev["container_index"]
                levels[idx] = ev["level_after"]
                collected[idx] = True
                reading_timestamp = timestamp + timedelta(
                    minutes=int(state._cached_reading_offsets[idx])
                )
                collection_events.append(
                    CollectionEvent(
                        timestamp=reading_timestamp,
                        container_id=containers[idx].id,
                        kind="total",
                        level_before_pct=float(np.round(ev["level_before"], 2)),
                        level_after_pct=float(np.round(ev["level_after"], 2)),
                        detected_by_sensor=True,
                    )
                )

        else:
            is_no_collection_day = timestamp.weekday() in getattr(
                self.config, "no_collection_days", ()
            )
            is_collection_hour = (
                timestamp.hour in self.config.collection_hours
            ) and not is_no_collection_day
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

        # Sensor trabado: repite la distancia guardada mientras siga trabado.
        final_distances = calculated_distances.copy()
        for idx, container in enumerate(containers):
            if anomalies[idx] == "sensor_trabado":
                final_distances[idx] = state.stuck_distances.setdefault(
                    container.id, float(calculated_distances[idx])
                )
            else:
                state.stuck_distances.pop(container.id, None)

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
