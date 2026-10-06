from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from simulator.demography.commands.process_land_use import process_land_use_async


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


if __name__ == "__main__":
    unittest.main()
