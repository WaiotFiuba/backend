from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Depot:
    id: str
    name: str
    zone: int
    latitude: float
    longitude: float
    type: Literal["base", "transfer_station"]


DEPOTS_BY_ZONE: dict[int, Depot] = {
    1: Depot(
        id="DEPOT-Z1",
        name="Base Operativa Saldías / Retiro",
        zone=1,
        latitude=-34.5824,
        longitude=-58.3905,
        type="base",
    ),
    2: Depot(
        id="DEPOT-Z2",
        name="Base Operativa Colegiales / Palermo",
        zone=2,
        latitude=-34.5772,
        longitude=-58.4451,
        type="base",
    ),
    3: Depot(
        id="DEPOT-Z3",
        name="Base Operativa Balvanera / Pompeya Norte",
        zone=3,
        latitude=-34.6402,
        longitude=-58.4101,
        type="base",
    ),
    4: Depot(
        id="DEPOT-Z4",
        name="Base Operativa Barracas / La Boca",
        zone=4,
        latitude=-34.6501,
        longitude=-58.3752,
        type="base",
    ),
    5: Depot(
        id="DEPOT-Z5",
        name="Base Operativa Soldati / Lugano",
        zone=5,
        latitude=-34.6720,
        longitude=-58.4550,
        type="base",
    ),
    6: Depot(
        id="DEPOT-Z6",
        name="Base Operativa Flores / Caballito",
        zone=6,
        latitude=-34.6521,
        longitude=-58.4231,
        type="base",
    ),
    7: Depot(
        id="DEPOT-Z7",
        name="Base Operativa Saavedra / Nuñez",
        zone=7,
        latitude=-34.5502,
        longitude=-58.4751,
        type="base",
    ),
}

TRANSFER_STATIONS: list[Depot] = [
    Depot(
        id="TRANSFER-COLEGIALES",
        name="Planta de Transferencia Colegiales",
        zone=2,
        latitude=-34.5772,
        longitude=-58.4451,
        type="transfer_station",
    ),
    Depot(
        id="TRANSFER-FLORES",
        name="Planta de Transferencia Flores",
        zone=6,
        latitude=-34.6521,
        longitude=-58.4231,
        type="transfer_station",
    ),
    Depot(
        id="TRANSFER-SOLDATI",
        name="Complejo Ambiental Villa Soldati (CEAMSE)",
        zone=5,
        latitude=-34.6720,
        longitude=-58.4550,
        type="transfer_station",
    ),
]


def get_depot_for_zone(zone: int | None) -> Depot:
    if zone is not None and zone in DEPOTS_BY_ZONE:
        return DEPOTS_BY_ZONE[zone]
    return DEPOTS_BY_ZONE[3]  # Default zona 3


def get_nearest_transfer_station(lat: float, lon: float) -> Depot:
    best_station = TRANSFER_STATIONS[0]
    min_dist_sq = float("inf")
    for st in TRANSFER_STATIONS:
        dy = (lat - st.latitude) * 111000.0
        dx = (lon - st.longitude) * 91400.0
        dist_sq = dx * dx + dy * dy
        if dist_sq < min_dist_sq:
            min_dist_sq = dist_sq
            best_station = st
    return best_station
