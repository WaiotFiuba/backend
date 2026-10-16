from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.services.simulation_session_service as svc


class TestValidateNeighborhoods(unittest.IsolatedAsyncioTestCase):
    # Regresion: _validate_neighborhoods y list_zone_demand le pasaban a
    # inspect() la Session que entrega AsyncSession.run_sync, no una conexion,
    # y fallaba con NoInspectionAvailable: crear una simulacion con ajustes por
    # barrio daba 500.

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session_maker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _create_barrios(self, *names: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE barrios (id INTEGER PRIMARY KEY, nombre TEXT)")
            )
            for name in names:
                await conn.execute(
                    text("INSERT INTO barrios (nombre) VALUES (:n)"), {"n": name}
                )

    async def test_known_neighborhoods_pass(self):
        await self._create_barrios("Palermo", "Almagro")
        async with self.session_maker() as db:
            await svc._validate_neighborhoods(db, ["Palermo"])

    async def test_unknown_neighborhoods_are_rejected_with_422(self):
        await self._create_barrios("Palermo")
        async with self.session_maker() as db:
            with self.assertRaises(HTTPException) as ctx:
                await svc._validate_neighborhoods(db, ["Palermo", "Narnia"])
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail, {"unknown_neighborhoods": ["Narnia"]})

    async def test_without_barrios_table_overrides_are_rejected_with_422(self):
        async with self.session_maker() as db:
            with self.assertRaises(HTTPException) as ctx:
                await svc._validate_neighborhoods(db, ["Palermo"])
        self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
