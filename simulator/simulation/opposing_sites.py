"""Sitios enfrentados (misma calle, vereda de enfrente), para el desborde de
basura de un sitio al de enfrente en el engine."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol

from simulator.geo.addresses import approximate_distance_meters, parse_street_address


class SiteLike(Protocol):
    id: Any
    address: str | None
    name: str | None
    latitude: float
    longitude: float


def build_opposing_sites_map(
    sites: Any,
    max_distance_m: float = 60.0,
    max_number_diff: int = 60,
) -> dict[str, str]:
    """Identifica sitios enfrentados (en la misma calle pero en la vereda de enfrente: par vs. impar).

    Devuelve un diccionario {site_id: opposing_site_id}.
    """
    parsed_sites: list[tuple[SiteLike, str, int]] = []

    for site in sites:
        # Intentar parsear address o name
        parsed = parse_street_address(site.address) or parse_street_address(site.name)
        if parsed is not None:
            street_name, number = parsed
            parsed_sites.append((site, street_name, number))

    # Agrupar por calle normalizada
    sites_by_street: dict[str, list[tuple[SiteLike, int]]] = defaultdict(list)
    for site, street_name, number in parsed_sites:
        sites_by_street[street_name].append((site, number))

    opposing_map: dict[str, str] = {}

    for street_name, street_sites in sites_by_street.items():
        if len(street_sites) < 2:
            continue

        evens = [(s, num) for s, num in street_sites if num % 2 == 0]
        odds = [(s, num) for s, num in street_sites if num % 2 != 0]

        if not evens or not odds:
            continue

        # Para cada sitio par, buscar el impar más cercano que cumpla criterios
        for even_site, even_num in evens:
            best_odd = None
            best_dist = float("inf")

            for odd_site, odd_num in odds:
                if abs(even_num - odd_num) <= max_number_diff:
                    dist = approximate_distance_meters(
                        even_site.latitude,
                        even_site.longitude,
                        odd_site.latitude,
                        odd_site.longitude,
                    )
                    if dist <= max_distance_m and dist < best_dist:
                        best_dist = dist
                        best_odd = odd_site

            # Si ninguno está ya emparejado o el nuevo es más cercano
            if (
                best_odd is not None
                and even_site.id not in opposing_map
                and best_odd.id not in opposing_map
            ):
                opposing_map[even_site.id] = best_odd.id
                opposing_map[best_odd.id] = even_site.id

    return opposing_map
