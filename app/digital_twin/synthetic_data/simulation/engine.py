from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

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
    pick_sensor_anomaly,
)
from app.digital_twin.synthetic_data.generators.collections import (
    level_after_collection,
    should_collect,
)
from app.digital_twin.synthetic_data.generators.filling import filling_increment
from app.digital_twin.synthetic_data.generators.sensors import (
    acceleration_g,
    battery_pct,
    signal_rssi_dbm,
    temperature_c,
    ultrasonic_distance_cm,
)
from app.digital_twin.synthetic_data.generators.topology import generate_synthetic_topology
from app.digital_twin.synthetic_data.simulation.clock import iter_timestamps
from app.digital_twin.synthetic_data.simulation.scenario import ScenarioConfig
from app.digital_twin.synthetic_data.topology import SimulationTopology


@dataclass(frozen=True)
class SimulationResult:
    sites: list[Site]
    containers: list[Container]
    devices: list[Device]
    measurements: list[Measurement]
    collections: list[CollectionEvent]
    alarms: list[Alarm]


class SyntheticDataSimulator:
    def __init__(self, config: ScenarioConfig, topology: SimulationTopology | None = None):
        self.config = config
        self.rng = random.Random(config.seed)
        self.topology = topology

    def run(self) -> SimulationResult:
        topology = self.topology or generate_synthetic_topology(self.config, self.rng)
        sites = topology.sites
        containers = topology.containers
        devices = topology.devices
        site_by_id = {site.id: site for site in sites}
        device_by_container_id = {device.container_id: device for device in devices}
        levels = dict(topology.initial_levels)
        batteries = {device.id: self.rng.uniform(70, 100) for device in devices}
        reading_offsets = {
            device.id: self.rng.randint(
                -self.config.reading_jitter_minutes,
                self.config.reading_jitter_minutes,
            )
            for device in devices
        }
        stuck_distances: dict[str, float] = {}

        measurements: list[Measurement] = []
        collection_events: list[CollectionEvent] = []
        alarms: list[Alarm] = []

        for timestamp in iter_timestamps(
            self.config.start,
            self.config.periods,
            self.config.frequency_minutes,
        ):
            for container in containers:
                site = site_by_id[container.site_id]
                device = device_by_container_id[container.id]
                reading_timestamp = timestamp + timedelta(minutes=reading_offsets[device.id])
                level_before = levels[container.id]
                level = min(
                    100.0,
                    level_before + filling_increment(timestamp, site, container, self.config, self.rng),
                )

                collected = should_collect(timestamp, level, self.config, self.rng)
                collection_detected = False
                if collected:
                    collection_level_before = level
                    collection_kind, level_after = level_after_collection(level, self.config, self.rng)
                    collection_detected = (collection_level_before - level_after) >= 20
                    collection_events.append(
                        CollectionEvent(
                            timestamp=reading_timestamp,
                            container_id=container.id,
                            kind=collection_kind,
                            level_before_pct=round(collection_level_before, 2),
                            level_after_pct=level_after,
                            detected_by_sensor=collection_detected,
                        )
                    )
                    level = level_after

                anomaly = pick_sensor_anomaly(
                    self.rng,
                    self.config.stuck_sensor_probability,
                    self.config.noisy_sensor_probability,
                )
                fire = self.rng.random() < self.config.fire_probability
                signal_lost = self.rng.random() < self.config.signal_loss_probability
                low_battery = self.rng.random() < self.config.low_battery_probability

                if anomaly == "sensor_trabado" and container.id not in stuck_distances:
                    stuck_distances[container.id] = ultrasonic_distance_cm(container, level, self.rng)
                if anomaly != "sensor_trabado":
                    stuck_distances.pop(container.id, None)

                battery = battery_pct(batteries[device.id], self.rng, force_low=low_battery)
                batteries[device.id] = battery
                acceleration = acceleration_g(self.rng, collection=collected)
                measurement = Measurement(
                    timestamp=reading_timestamp,
                    site_id=None,
                    container_id=container.id,
                    device_id=device.id,
                    fill_level_pct=round(level, 2),
                    ultrasonic_distance_cm=ultrasonic_distance_cm(
                        container,
                        level,
                        self.rng,
                        noisy=anomaly == "sensor_ruidoso",
                        stuck_value=stuck_distances.get(container.id),
                    ),
                    battery_pct=battery,
                    signal_rssi_dbm=signal_rssi_dbm(self.rng, lost=signal_lost),
                    temperature_c=temperature_c(timestamp, self.rng, fire=fire),
                    acceleration_g=acceleration,
                    is_collection_detected=collection_detected and acceleration >= 1.2,
                    anomaly="incendio" if fire else anomaly,
                )
                measurements.append(measurement)
                alarm = alarm_from_measurement(measurement)
                if alarm is not None:
                    alarms.append(alarm)

                levels[container.id] = level

        return SimulationResult(
            sites=sites,
            containers=containers,
            devices=devices,
            measurements=measurements,
            collections=collection_events,
            alarms=alarms,
        )
