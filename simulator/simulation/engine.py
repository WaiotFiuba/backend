from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Callable

import numpy as np

from simulator.domain.entities import (
    Alarm,
    CollectionEvent,
    Measurement,
)
from simulator.generators.anomalies import alarm_from_measurement
from simulator.generators.collections import (
    collect_probabilistic,
    collect_with_trucks,
)
from simulator.generators.filling import (
    apply_filling,
    filling_increments,
    zone_multipliers,
)
from simulator.generators.sensors import _daily_wave
from simulator.generators.topology import generate_synthetic_topology
from simulator.simulation.clock import iter_timestamps
from simulator.simulation.opposing_sites import build_opposing_sites_map
from simulator.simulation.scenario import ScenarioConfig
from simulator.simulation.state import (
    SimulationResult,
    SimulationState,
    build_container_arrays,
)
from simulator.topology import SimulationTopology
from simulator.trucks.truck_engine import TruckFleetSimulator, build_truck_fleet

logger = logging.getLogger(__name__)


class SyntheticDataSimulator:
    def __init__(
        self, config: ScenarioConfig, topology: SimulationTopology | None = None
    ):
        self.config = config
        self.rng = random.Random(config.seed)
        self.topology = topology
        self.state: SimulationState | None = None
        self.truck_fleet: TruckFleetSimulator | None = None

    def initialize(self) -> SimulationState:
        self.rng = random.Random(self.config.seed)
        self.np_rng = np.random.default_rng(self.config.seed)
        topology = self.topology or generate_synthetic_topology(self.config, self.rng)
        opposing_sites = build_opposing_sites_map(topology.sites)
        site_by_id = {site.id: site for site in topology.sites}
        device_by_container_id = {
            device.container_id: device for device in topology.devices
        }
        # Mismo orden de sorteos que siempre: primero baterías, después desfasajes.
        batteries = {
            device.id: self.rng.uniform(70, 100) for device in topology.devices
        }
        reading_offsets = {
            device.id: self.rng.randint(
                -self.config.reading_jitter_minutes,
                self.config.reading_jitter_minutes,
            )
            for device in topology.devices
        }
        self.state = SimulationState(
            topology=topology,
            site_by_id=site_by_id,
            device_by_container_id=device_by_container_id,
            levels=dict(topology.initial_levels),
            batteries=batteries,
            reading_offsets=reading_offsets,
            stuck_distances={},
            opposing_site_by_site_id=opposing_sites,
            arrays=build_container_arrays(
                topology,
                site_by_id,
                device_by_container_id,
                reading_offsets,
                self.config,
            ),
        )

        self.truck_fleet = None
        try:
            self.truck_fleet = build_truck_fleet(topology, self.config, self.rng)
        except Exception as e:  # noqa: BLE001
            logger.warning("No se pudo inicializar la flota de camiones: %s", e)

        return self.state

    def run(
        self,
        global_demand_multiplier: float | None = None,
        neighborhood_multiplier: Callable[[str], float] | None = None,
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
                neighborhood_multiplier=neighborhood_multiplier,
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
        neighborhood_multiplier: Callable[[str], float] | None = None,
    ) -> SimulationResult:
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

        arrays = state.arrays

        measurements: list[Measurement] = []
        collection_events: list[CollectionEvent] = []
        alarms: list[Alarm] = []

        # Get current state as arrays
        levels = np.array(
            [state.levels.get(c.id, 0.0) for c in containers], dtype=np.float64
        )
        batteries = np.array(
            [state.batteries[d.id] for d in arrays.devices], dtype=np.float64
        )

        # 1. Llenado (ver simulator/generators/filling.py)
        zone_mults = zone_multipliers(
            arrays.zone_profiles,
            timestamp,
            arrays.calibration_factor,
            neighborhood_multiplier,
        )
        global_mult = (
            self.config.high_demand_multiplier
            if global_demand_multiplier is None
            else global_demand_multiplier
        )
        increments = filling_increments(
            arrays.demand_bases,
            arrays.waste_factors,
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
            arrays.containers_by_site_and_waste,
            arrays.containers_by_site,
        )

        # 2. Recolección (ver simulator/generators/collections.py): con la flota
        # de camiones o, si no se pudo armar, con el modelo probabilístico viejo.
        level_before_collection = levels.copy()
        if self.truck_fleet is not None:
            collection = collect_with_trucks(
                self.truck_fleet, levels, timestamp, containers, arrays, self.config
            )
        else:
            collection = collect_probabilistic(
                levels, timestamp, containers, arrays, self.config, self.np_rng
            )
        levels = collection.levels
        collected = collection.collected
        collection_events.extend(collection.events)

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

        empty_distances = arrays.heights
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

        for idx, device in enumerate(arrays.devices):
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

            site = arrays.sites[idx]
            device = arrays.devices[idx]
            reading_timestamp = timestamp + timedelta(
                minutes=int(arrays.reading_offsets[idx])
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
