from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, text

from app.core.map_database import MapSessionLocal
from app.models.map.neighborhood_demographic import NeighborhoodDemographic


@dataclass(frozen=True)
class PopulationRecord:
    population: int
    year: int
    source: str


def normalize_neighborhood(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(
        character for character in normalized if not unicodedata.combining(character)
    )


async def import_neighborhood_demographics(
    geojson_path: str | Path,
    population_csv_path: str | Path,
    year: int = 2010,
    source: str = "Censo 2010",
) -> dict[str, object]:
    populations = _read_populations(population_csv_path, year=year, source=source)
    features = _read_features(geojson_path)
    matched: list[str] = []
    unmatched: list[str] = []

    async with MapSessionLocal() as session:
        for feature in features:
            properties = feature.get("properties") or {}
            neighborhood = _property(properties, "barrio", "neighborhood", "nombre")
            if not neighborhood:
                continue
            population = populations.get(normalize_neighborhood(neighborhood))
            if population is None:
                unmatched.append(neighborhood)
                continue

            commune = _property(properties, "comuna", "commune")
            geometry = json.dumps(feature.get("geometry"))
            area_km2 = await session.scalar(
                select(
                    text(
                        "ST_Area(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)::geography) / 1000000.0"
                    )
                ).params(geometry=geometry)
            )
            density = population.population / float(area_km2)
            await session.execute(
                text(
                    """
                    INSERT INTO neighborhood_demographics
                        (neighborhood, commune, population, year, source, area_km2,
                         density_per_km2, density_factor, geom)
                    VALUES
                        (:neighborhood, :commune, :population, :year, :source, :area_km2,
                         :density, 1.0,
                         ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)))
                    ON CONFLICT (neighborhood) DO UPDATE SET
                        commune = EXCLUDED.commune,
                        population = EXCLUDED.population,
                        year = EXCLUDED.year,
                        source = EXCLUDED.source,
                        area_km2 = EXCLUDED.area_km2,
                        density_per_km2 = EXCLUDED.density_per_km2,
                        geom = EXCLUDED.geom,
                        updated_at = now()
                    """
                ),
                {
                    "neighborhood": neighborhood,
                    "commune": commune,
                    "population": population.population,
                    "year": population.year,
                    "source": population.source,
                    "area_km2": area_km2,
                    "density": density,
                    "geometry": geometry,
                },
            )
            matched.append(neighborhood)

        await session.flush()
        rows = (await session.execute(select(NeighborhoodDemographic))).scalars().all()
        median_density = (
            statistics.median(row.density_per_km2 for row in rows) if rows else 0
        )
        if median_density:
            for row in rows:
                row.density_factor = density_factor(row.density_per_km2, median_density)
        await session.commit()

    return {
        "imported": len(matched),
        "unmatched": sorted(unmatched),
        "median_density_per_km2": round(median_density, 2),
    }


def density_factor(density_per_km2: float, median_density_per_km2: float) -> float:
    if median_density_per_km2 <= 0:
        return 1.0
    factor = 1 + 0.5 * (density_per_km2 / median_density_per_km2 - 1)
    return round(min(2.0, max(0.5, factor)), 4)


def _read_populations(
    path: str | Path,
    year: int = 2010,
    source: str = "Censo 2010",
) -> dict[str, PopulationRecord]:
    with Path(path).open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        required = {"barrio", "poblacion"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("El CSV debe contener barrio,poblacion.")
        return {
            normalize_neighborhood(row["barrio"]): PopulationRecord(
                population=int(row["poblacion"]),
                year=year,
                source=source,
            )
            for row in reader
        }


def _read_features(path: str | Path) -> list[dict[str, object]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("El GeoJSON debe ser un FeatureCollection.")
    return features


def _property(properties: dict[str, object], *names: str) -> str | None:
    normalized = {str(key).casefold(): value for key, value in properties.items()}
    for name in names:
        value = normalized.get(name.casefold())
        if value not in (None, ""):
            return str(value)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa demografia por barrio a PostGIS."
    )
    parser.add_argument("--geojson", type=Path, required=True)
    parser.add_argument("--population-csv", type=Path, required=True)
    parser.add_argument("--year", type=int, default=2010)
    parser.add_argument("--source", default="Censo 2010")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                import_neighborhood_demographics(
                    args.geojson,
                    args.population_csv,
                    year=args.year,
                    source=args.source,
                )
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
