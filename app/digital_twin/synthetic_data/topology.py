from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.digital_twin.synthetic_data.domain.entities import Container, Device, Site


@dataclass(frozen=True)
class BackendContainerRecord:
    id: int
    site_id: str
    site_name: str | None
    latitude: float
    longitude: float
    current_level: int
    device_imei: str | None
    container_type: str | None
    waste_type: str | None
    height_cm: float | None
    volume_m3: float | None
    zone: str | None = None
    demand_base: float = 1.0


@dataclass(frozen=True)
class SimulationTopology:
    sites: list[Site]
    containers: list[Container]
    devices: list[Device]
    initial_levels: dict[str, float]


from app.digital_twin.synthetic_data.density_processor import get_density_processor


def topology_from_backend_records(
    records: Sequence[BackendContainerRecord],
) -> SimulationTopology:
    sites_by_id: dict[str, Site] = {}
    containers: list[Container] = []
    devices: list[Device] = []
    initial_levels: dict[str, float] = {}

    # Procesamiento espacial de densidad censal para todos los contenedores
    containers_input = [
        {
            "id": r.id,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "volume_m3": r.volume_m3,
        }
        for r in records
    ]
    processor = get_density_processor()
    demands, _ = processor.process_containers(containers_input)

    for record in records:
        container_id = str(record.id)
        site_id = record.site_id
        demand_info = demands.get(record.id)
        calculated_demand = (
            demand_info.hourly_fill_pct if demand_info is not None else record.demand_base
        )

        if site_id not in sites_by_id:
            zone_name = (
                demand_info.department_name
                if (demand_info and demand_info.department_name != "UNKNOWN")
                else (record.zone or "")
            )
            sites_by_id[site_id] = Site(
                id=site_id,
                name=record.site_name or "",
                zone=zone_name,
                latitude=record.latitude,
                longitude=record.longitude,
                demand_base=calculated_demand,
            )

        containers.append(
            Container(
                id=container_id,
                site_id=site_id,
                name=record.container_type or f"Container {container_id}",
                waste_type=record.waste_type or record.container_type or "residuos",
                height_cm=float(record.height_cm or 145),
                volume_m3=record.volume_m3,
            )
        )
        devices.append(
            Device(
                id=record.device_imei or f"sim-device-{container_id}",
                container_id=container_id,
            )
        )
        initial_levels[container_id] = float(record.current_level)

    return SimulationTopology(
        sites=list(sites_by_id.values()),
        containers=containers,
        devices=devices,
        initial_levels=initial_levels,
    )


def topology_from_backend_api(
    payload: Sequence[dict[str, object]],
) -> SimulationTopology:
    records = [_record_from_backend_api_item(item) for item in payload]
    return topology_from_backend_records(records)


def _record_from_backend_api_item(item: dict[str, object]) -> BackendContainerRecord:
    container_type = item.get("container_type")
    container_type_name = None
    waste_type_name = None
    height_cm = None
    volume_m3 = None

    if isinstance(container_type, dict):
        raw_container_type_name = container_type.get("name")
        container_type_name = (
            str(raw_container_type_name) if raw_container_type_name else None
        )
        raw_height_cm = container_type.get("height_cm")
        height_cm = float(raw_height_cm) if raw_height_cm is not None else None
        raw_volume_m3 = container_type.get("volume_m3")
        volume_m3 = float(raw_volume_m3) if raw_volume_m3 is not None else None
        waste_types = container_type.get("waste_types")
        if isinstance(waste_types, list) and waste_types:
            first_waste_type = waste_types[0]
            if isinstance(first_waste_type, dict):
                raw_waste_type_name = first_waste_type.get("name")
                waste_type_name = (
                    str(raw_waste_type_name) if raw_waste_type_name else None
                )

    container_id = item["id"]
    site_id = item["site_id"]

    return BackendContainerRecord(
        id=int(container_id),
        site_id=str(site_id),
        site_name=str(item["site_name"]) if item.get("site_name") else None,
        latitude=float(item["latitude"]),
        longitude=float(item["longitude"]),
        current_level=int(item.get("current_level", 0)),
        device_imei=str(item["device_imei"]) if item.get("device_imei") else None,
        container_type=container_type_name,
        waste_type=waste_type_name,
        height_cm=height_cm,
        volume_m3=volume_m3,
        zone=str(item["zone"]) if item.get("zone") else None,
        demand_base=float(item.get("density_factor", 1.0)),
    )
