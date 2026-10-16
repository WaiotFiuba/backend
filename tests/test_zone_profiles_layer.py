from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.map_database import MapBase
from app.models.map.simulation import SimulationSession
from app.models.map.zone_profile_layer import ZoneProfileLayer
from app.services.map.zone_profile_service import (
    EMPTY_LAYER,
    get_zone_profiles_geojson,
    save_zone_profiles_geojson,
)
from simulator.density_processor import get_density_processor
from simulator.exporters.zone_profiles import build_zone_profiles_geojson
from simulator.zone_classifier import get_zone_classifier, reload_zone_classifier


class TestZoneProfilesLayerSimulator(unittest.TestCase):
    def test_layer_has_one_feature_per_census_radio(self):
        processor = get_density_processor()
        layer = build_zone_profiles_geojson(processor, get_zone_classifier())

        self.assertEqual(layer["type"], "FeatureCollection")
        self.assertEqual(len(layer["features"]), len(processor.radios))
        props = layer["features"][0]["properties"]
        self.assertEqual(
            set(props),
            {
                "radio_code",
                "barrio",
                "department_name",
                "population",
                "zone_type",
                "demand_multiplier",
                "weekend_factor",
                "hour_weights",
                "weekday_factors",
            },
        )

    def test_reload_creates_new_classifier_used_by_get(self):
        before = get_zone_classifier()
        reloaded = reload_zone_classifier()

        self.assertIsNot(reloaded, before)
        self.assertIs(get_zone_classifier(), reloaded)


class TestZoneProfilesLayerBackend(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: MapBase.metadata.create_all(
                    sync_conn,
                    tables=[SimulationSession.__table__, ZoneProfileLayer.__table__],
                )
            )
        self.session_maker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        async with self.session_maker() as db:
            for sim_id in (1, 2):
                db.add(
                    SimulationSession(
                        id=sim_id, scenario={}, total_periods=0, created_by=1
                    )
                )
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _layer(*radio_codes: str) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": None, "properties": {"radio_code": c}}
                for c in radio_codes
            ],
        }

    async def test_empty_until_simulator_publishes(self):
        async with self.session_maker() as db:
            self.assertEqual(await get_zone_profiles_geojson(db), EMPTY_LAYER)

    async def test_keeps_only_latest_published_layer(self):
        async with self.session_maker() as db:
            self.assertEqual(
                await save_zone_profiles_geojson(db, 1, self._layer("a", "b")), 2
            )
            self.assertEqual(
                await save_zone_profiles_geojson(db, 2, self._layer("c")), 1
            )

        async with self.session_maker() as db:
            self.assertEqual(await get_zone_profiles_geojson(db), self._layer("c"))
            stored = await db.get(ZoneProfileLayer, 1)
            self.assertEqual(stored.simulation_id, 2)

    async def test_unknown_simulation_is_rejected(self):
        async with self.session_maker() as db:
            with self.assertRaises(HTTPException) as ctx:
                await save_zone_profiles_geojson(db, 999, self._layer("a"))
        self.assertEqual(ctx.exception.status_code, 404)
