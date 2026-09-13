from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Sequence

from app.digital_twin.synthetic_data.domain.entities import Site

# Prefijos o palabras de ruido a remover para normalizar el nombre de calle
_NOISE_TOKENS = {
    "AV",
    "AV.",
    "AVENIDA",
    "CALLE",
    "PJE",
    "PJE.",
    "PASAJE",
    "DIAGONAL",
    "SITIO",
    "RSU",
    "HUMEDA",
    "HÚMEDA",
    "SECA",
    "RECICLABLES",
    "ORGANICO",
    "ORGÁNICO",
    "INDIFERENCIADO",
    "DE",
    "DEL",
    "LA",
    "LOS",
    "LAS",
    "Y",
    "E",
}


def parse_street_address(text: str | None) -> tuple[str, int] | None:
    """Extrae el nombre de calle normalizado y la altura catastral (número).

    Ejemplos:
        "AV. CORRIENTES 1234" -> ("CORRIENTES", 1234)
        "CORRIENTES AV. 1235" -> ("CORRIENTES", 1235)
        "Sitio RSU - CALLAO 520" -> ("CALLAO", 520)
        "SAN MARTIN 450" -> ("SAN MARTIN", 450)
    """
    if not text:
        return None

    cleaned = text.upper()
    # Si viene con formato "Sitio ... - Dirección", tomar la parte después del guión si existe
    if "-" in cleaned:
        parts = cleaned.split("-", 1)
        cleaned = parts[1].strip()

    # Buscar la altura numérica (número entero de 1 a 5 dígitos)
    # Típicamente está al final de la dirección o precedido/seguido por letras
    match = re.search(r"\b(\d{1,5})\b", cleaned)
    if not match:
        return None

    number_str = match.group(1)
    try:
        number = int(number_str)
    except ValueError:
        return None

    # Extraer el texto de la calle sin el número
    street_part = re.sub(r"\b\d{1,5}\b", " ", cleaned)
    # Quitar caracteres especiales
    street_part = re.sub(r"[^\w\s]", " ", street_part)

    # Filtrar tokens de ruido
    raw_tokens = street_part.split()
    tokens = [t for t in raw_tokens if t not in _NOISE_TOKENS and len(t) > 1]

    if not tokens:
        return None

    normalized_street = " ".join(tokens)
    return normalized_street, number


def approximate_distance_meters(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Calcula la distancia geodésica aproximada en metros entre dos coordenadas en CABA."""
    dlat = (lat2 - lat1) * 111000.0
    # CABA latitud aprox -34.6 => cos(-34.6°) ≈ 0.823
    dlon = (lon2 - lon1) * 111000.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def build_opposing_sites_map(
    sites: Sequence[Site],
    max_distance_m: float = 60.0,
    max_number_diff: int = 60,
) -> dict[str, str]:
    """Identifica sitios enfrentados (en la misma calle pero en la vereda de enfrente: par vs. impar).

    Devuelve un diccionario {site_id: opposing_site_id}.
    """
    parsed_sites: list[tuple[Site, str, int]] = []

    for site in sites:
        # Intentar parsear address o name
        parsed = parse_street_address(site.address) or parse_street_address(site.name)
        if parsed is not None:
            street_name, number = parsed
            parsed_sites.append((site, street_name, number))

    # Agrupar por calle normalizada
    sites_by_street: dict[str, list[tuple[Site, int]]] = defaultdict(list)
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

            if best_odd is not None:
                # Si ninguno está ya emparejado o el nuevo es más cercano
                if even_site.id not in opposing_map and best_odd.id not in opposing_map:
                    opposing_map[even_site.id] = best_odd.id
                    opposing_map[best_odd.id] = even_site.id

    return opposing_map
