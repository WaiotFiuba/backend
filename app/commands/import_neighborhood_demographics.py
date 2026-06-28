from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.map_database import MapSessionLocal
from app.models.map.caba_geo_extension import Barrio
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
    population_csv_path: str | Path,
    year: int = 2010,
    source: str = "Censo 2010",
) -> dict[str, object]:
    populations = _read_populations(population_csv_path, year=year, source=source)
    matched: list[str] = []
    unmatched: list[str] = []

    async with MapSessionLocal() as session:
        # Trae cada barrio junto con su área en km2, calculada desde su geom (ya en la base)
        rows = (
            await session.execute(
                select(
                    Barrio,
                    (func.ST_Area(cast(Barrio.geom, Geography)) / 1_000_000.0).label("area_km2"),
                )
            )
        ).all()

        for barrio, area_km2 in rows:
            population = populations.get(normalize_neighborhood(barrio.nombre))
            if population is None:
                unmatched.append(barrio.nombre)
                continue

            density = population.population / float(area_km2)

            stmt = (
                pg_insert(NeighborhoodDemographic)
                .values(
                    neighborhood_id=barrio.id,
                    population=population.population,
                    year=population.year,
                    source=population.source,
                    area_km2=area_km2,
                    density_per_km2=density,
                    density_factor=1.0,
                )
                .on_conflict_do_update(
                    index_elements=["neighborhood_id"],
                    set_={
                        "population": population.population,
                        "year": population.year,
                        "source": population.source,
                        "area_km2": area_km2,
                        "density_per_km2": density,
                        "updated_at": func.now(),
                    },
                )
            )
            await session.execute(stmt)
            matched.append(barrio.nombre)

        await session.flush()
        demographics = (await session.execute(select(NeighborhoodDemographic))).scalars().all()
        median_density = (
            statistics.median(row.density_per_km2 for row in demographics) if demographics else 0
        )
        if median_density:
            for row in demographics:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa demografia por barrio usando los barrios ya en PostGIS.")
    parser.add_argument("--population-csv", type=Path, required=True)
    parser.add_argument("--year", type=int, default=2010)
    parser.add_argument("--source", default="Censo 2010")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                import_neighborhood_demographics(
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