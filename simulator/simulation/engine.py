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
from simulator.generators.anomalies import alarm_from_measurement, draw_anomalies
from simulator.generators.collections import (
    collect_probabilistic,
    collect_with_trucks,
)
from simulator.generators.filling import (
    apply_filling,
    filling_increments,
    zone_multipliers,
)
from simulator.generators.sensors import read_sensors
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

        # 3. Fallas sorteadas y 4. lecturas de los sensores (ver
        # simulator/generators/anomalies.py y sensors.py).
        anomalies = draw_anomalies(N, self.config, self.np_rng)
        readings = read_sensors(
            levels,
            collected,
            anomalies,
            timestamp,
            containers,
            arrays.heights,
            np.array([state.batteries[d.id] for d in arrays.devices], dtype=np.float64),
            state.stuck_distances,
            self.np_rng,
        )
        for idx, device in enumerate(arrays.devices):
            state.batteries[device.id] = float(readings.batteries[idx])

        # 5. Build results & update state
        is_collection_detected = collection_detected & (readings.accelerations >= 1.2)
        final_anomalies = np.where(anomalies.fire, "incendio", anomalies.sensor)

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
                ultrasonic_distance_cm=float(readings.distances[idx]),
                battery_pct=float(readings.batteries[idx]),
                signal_rssi_dbm=float(readings.rssi[idx]),
                temperature_c=float(readings.temperatures[idx]),
                acceleration_g=float(readings.accelerations[idx]),
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
