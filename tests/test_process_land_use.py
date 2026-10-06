from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from shapely.geometry import box

from simulator.demography.commands.process_land_use import (
    ZONE_PROFILES_YAML,
    _barrios_by_radio,
    process_land_use_async,
)
import simulator.zone_classifier as zone_classifier


class TestProcessLandUseSkips(unittest.TestCase):
    def test_existing_output_is_kept_even_without_source_files(self):
        # El worker lo llama al arrancar: si el resultado ya existe no hace
        # falta el archivo fuente (que el contenedor del simulador no tiene).
        with tempfile.TemporaryDirectory() as tmp:
            out_radio = Path(tmp) / "land_use_by_radio.csv"
            out_radio.write_text("radio_code\n")

            result = asyncio.run(
                process_land_use_async(
                    land_use_csv=Path(tmp) / "no_existe.csv",
                    output_radio_csv=out_radio,
                    output_barrio_csv=Path(tmp) / "land_use_by_barrio.csv",
                )
            )

        self.assertEqual(result["status"], "already_exists")

    def test_missing_output_and_source_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(
                process_land_use_async(
                    land_use_csv=Path(tmp) / "no_existe.csv",
                    output_radio_csv=Path(tmp) / "land_use_by_radio.csv",
                    output_barrio_csv=Path(tmp) / "land_use_by_barrio.csv",
                )
            )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "land_use_csv_missing")


class TestProcessLandUseConfig(unittest.TestCase):
    def test_reads_the_same_zone_profiles_yaml_as_the_classifier(self):
        # Antes apuntaba a app/digital_twin/synthetic_data/config/, que ya no
        # existe, y caia en silencio a valores escritos en el codigo.
        classifier_yaml = (
            Path(zone_classifier.__file__).resolve().parent
            / "config"
            / "zone_profiles.yaml"
        )
        self.assertTrue(ZONE_PROFILES_YAML.exists())
        self.assertEqual(ZONE_PROFILES_YAML.resolve(), classifier_yaml)


class TestBarriosByRadio(unittest.TestCase):
    def _barrios_file(self, tmp: str) -> Path:
        # Dos barrios lado a lado: Palermo en x 0..10 y Recoleta en x 10..20.
        features = [
            {
                "type": "Feature",
                "properties": {"nombre": name},
                "geometry": box(x0, 0, x0 + 10, 10).__geo_interface__,
            }
            for name, x0 in (("Palermo", 0), ("Recoleta", 10))
        ]
        path = Path(tmp) / "barrios.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        return path

    def test_each_radio_gets_the_barrio_that_covers_most_of_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            barrios = _barrios_by_radio(
                ["adentro", "cruza", "afuera"],
                [box(2, 2, 4, 4), box(8, 2, 14, 4), box(30, 30, 31, 31)],
                self._barrios_file(tmp),
            )

        # El que cruza el borde queda en Recoleta (4 de sus 6 unidades); el que
        # no toca ningun barrio no tiene entrada (se usa el de las parcelas).
        self.assertEqual(barrios, {"adentro": "PALERMO", "cruza": "RECOLETA"})

    def test_missing_barrios_file_returns_no_barrios(self):
        self.assertEqual(
            _barrios_by_radio(["r"], [box(0, 0, 1, 1)], Path("/no/existe.geojson")),
            {},
        )


if __name__ == "__main__":
    unittest.main()
