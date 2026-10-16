"""Parseo de direcciones para el clustering de sitios.

El simulador tiene su propia copia del parseo (simulator/geo/addresses.py).
"""

from __future__ import annotations

import re


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


def _strip_dash_prefix(text: str) -> str:
    """ "Sitio RSU - CALLAO 520" -> "CALLAO 520" (toma la parte tras el guión, si hay)."""
    cleaned = text.upper()
    if "-" in cleaned:
        cleaned = cleaned.split("-", 1)[1].strip()
    # "°" y "º" se usan indistintamente en los datos reales para lo mismo (ej. "Cabo 2°" / "Cabo 2º")
    # pasamos a uno solo para que ambos lados de un match usen siempre el mismo.
    cleaned = cleaned.replace("º", "°")
    return cleaned


def _clean_street_tokens(text_without_number: str) -> str | None:
    """Quita caracteres especiales y tokens de ruido (AV., CALLE, comas, etc.)."""
    street_part = re.sub(r"[^\w\s]", " ", text_without_number)
    tokens = [t for t in street_part.split() if t not in _NOISE_TOKENS and len(t) > 1]
    if not tokens:
        return None
    return " ".join(tokens)


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

    cleaned = _strip_dash_prefix(text)

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
    normalized_street = _clean_street_tokens(street_part)
    if not normalized_street:
        return None

    return normalized_street, number
