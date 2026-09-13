from __future__ import annotations

import unittest
from datetime import UTC, datetime

from geoalchemy2.elements import WKTElement
from shapely.geometry import LineString
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.map_database import MapBase
from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.data_level import DataLevel
from app.models.map.simulation import SimulationSession
from app.models.map.site import Site
from app.models.map.waste_type import WasteType
from app.services.map.site_clustering_service import (
    StreetSegment,
    StreetSpatialIndex,
    cluster_and_create_sites,
)
from app.services.map.site_service import (
    get_site_by_id,
    get_site_changes,
    get_site_level_history,
    get_site_map_snapshot,
    get_sites_clustered,
)


class TestStreetSpatialIndex(unittest.TestCase):
    def test_street_spatial_index_load_side_inference(self):
        # Calle de Sur a Norte (coordenadas: lon fija -58.4, lat de -34.60 a -34.50)
        line = LineString([(-58.4, -34.60), (-58.4, -34.50)])

        # 1. Caso CRECIENTE
        idx_creciente = StreetSpatialIndex(
            [
                StreetSegment(
                    geometry=line, nomoficial="AV CORRIENTES", sentido="CRECIENTE"
                )
            ]
        )
        # Punto al Este (-58.39, -34.55) -> a la DERECHA del avance Sur -> Norte
        self.assertEqual(idx_creciente.infer_load_side(-58.39, -34.55), "DERECHA")
        # Punto al Oeste (-58.41, -34.55) -> a la IZQUIERDA del avance Sur -> Norte
        self.assertEqual(idx_creciente.infer_load_side(-58.41, -34.55), "IZQUIERDA")

        # 2. Caso DECRECIENTE (sentido invertido Norte -> Sur)
        idx_decreciente = StreetSpatialIndex(
            [
                StreetSegment(
                    geometry=line, nomoficial="AV CORRIENTES", sentido="DECRECIENTE"
                )
            ]
        )
        # Punto al Este (-58.39, -34.55) -> a la IZQUIERDA del avance Norte -> Sur
        self.assertEqual(idx_decreciente.infer_load_side(-58.39, -34.55), "IZQUIERDA")
        # Punto al Oeste (-58.41, -34.55) -> a la DERECHA del avance Norte -> Sur
        self.assertEqual(idx_decreciente.infer_load_side(-58.41, -34.55), "DERECHA")

        # 3. Caso DOBLE
        idx_doble = StreetSpatialIndex(
            [StreetSegment(geometry=line, nomoficial="AV CORRIENTES", sentido="DOBLE")]
        )
        self.assertEqual(idx_doble.infer_load_side(-58.39, -34.55), "BILATERAL")

    def test_split_by_distance_separates_far_containers(self):
        from app.services.map.site_clustering_service import _split_by_distance

        # Contenedores 1 y 2 a 10 metros de distancia
        c1 = Container(id=1, latitude=-34.6000, longitude=-58.3800)
        c2 = Container(id=2, latitude=-34.6001, longitude=-58.3800)  # ~11 metros
        # Contenedor 3 a 500 metros de distancia
        c3 = Container(id=3, latitude=-34.6050, longitude=-58.3800)  # ~550 metros

        clusters = _split_by_distance([c1, c2, c3], max_distance_m=100.0)
        self.assertEqual(len(clusters), 2)
        self.assertEqual([c.id for c in clusters[0]], [1, 2])
        self.assertEqual([c.id for c in clusters[1]], [3])

    def test_split_by_distance_separates_by_address_block_numbers(self):
        from app.services.map.site_clustering_service import _split_by_distance

        # Calle Corrientes al 1600 (cuadra 1600-1700)
        c1 = Container(
            id=1, latitude=-34.6000, longitude=-58.3800, address="CORRIENTES 1620"
        )
        c2 = Container(
            id=2, latitude=-34.6001, longitude=-58.3801, address="CORRIENTES 1650"
        )
        # Calle Corrientes al 2600 (otra cuadra a varias cuadras)
        c3 = Container(
            id=3, latitude=-34.6002, longitude=-58.3802, address="CORRIENTES 2640"
        )

        clusters = _split_by_distance([c1, c2, c3], max_distance_m=100.0)
        self.assertEqual(len(clusters), 2)
        self.assertEqual([c.id for c in clusters[0]], [1, 2])
        self.assertEqual([c.id for c in clusters[1]], [3])


class TestSiteServices(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy import event

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        @event.listens_for(self.engine.sync_engine, "connect")
        def do_connect(dbapi_connection, connection_record):
            dbapi_connection.create_function(
                "RecoverGeometryColumn", -1, lambda *args: 1
            )
            dbapi_connection.create_function("InitSpatialMetaData", -1, lambda *args: 1)
            dbapi_connection.create_function("AddGeometryColumn", -1, lambda *args: 1)
            dbapi_connection.create_function(
                "DiscardGeometryColumn", -1, lambda *args: 1
            )
            dbapi_connection.create_function(
                "CheckSpatialIndex", -1, lambda *args: None
            )
            dbapi_connection.create_function(
                "DisableSpatialIndex", -1, lambda *args: None
            )
            dbapi_connection.create_function("bool_or", 1, lambda x: bool(x))
            dbapi_connection.create_function("GeomFromEWKT", 1, lambda x: str(x))
            dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda x: str(x))
            dbapi_connection.create_function(
                "ST_AsEWKB",
                1,
                lambda x: str(x).encode("utf-8") if x is not None else None,
            )
            dbapi_connection.create_function(
                "ST_AsBinary",
                1,
                lambda x: str(x).encode("utf-8") if x is not None else None,
            )
            dbapi_connection.create_function(
                "AsEWKB", 1, lambda x: str(x).encode("utf-8") if x is not None else None
            )
            dbapi_connection.create_function(
                "AsBinary",
                1,
                lambda x: str(x).encode("utf-8") if x is not None else None,
            )

        from app.models.map.container_type import container_type_waste_types
        from app.models.map.simulation import SimulationSession

        tables = [
            WasteType.__table__,
            ContainerType.__table__,
            container_type_waste_types,
            Container.__table__,
            Site.__table__,
            DataLevel.__table__,
            SimulationSession.__table__,
        ]
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: MapBase.metadata.create_all(sync_conn, tables=tables)
            )
        self.session_maker = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        from app.models.map.container_type import container_type_waste_types

        tables = [
            SimulationSession.__table__,
            Site.__table__,
            DataLevel.__table__,
            Container.__table__,
            container_type_waste_types,
            ContainerType.__table__,
            WasteType.__table__,
        ]
        async with self.engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: MapBase.metadata.drop_all(sync_conn, tables=tables)
            )
        await self.engine.dispose()

    async def test_cluster_and_create_sites_monotype(self):
        async with self.session_maker() as session:
            # Crear tipos de residuo
            wt_humedo = WasteType(id=1, name="RSU Fracción Húmeda", color="#000000")
            wt_seco = WasteType(id=2, name="RSU Fracción Seca", color="#00FF00")
            session.add_all([wt_humedo, wt_seco])
            await session.flush()

            # Crear tipos de contenedor
            ct_lateral = ContainerType(
                id=1, name="Carga Lateral", height_cm=145, volume_m3=3.2
            )
            ct_lateral.waste_types.append(wt_humedo)

            ct_campana = ContainerType(
                id=2, name="Campana Verde", height_cm=160, volume_m3=2.5
            )
            ct_campana.waste_types.append(wt_seco)

            session.add_all([ct_lateral, ct_campana])
            await session.flush()

            # Contenedores con números de puerta distintos pero a menos de 5 metros:
            # 2 contenedores de fracción húmeda
            c1 = Container(
                id=1,
                serie_id="CONT-1",
                address="CORDOBA AV. 2576",
                latitude=-34.598671,
                longitude=-58.403441,
                geom=WKTElement("POINT(-58.403441 -34.598671)", srid=4326),
                current_level=30,
                available=True,
                container_type=ct_lateral,
            )
            c2 = Container(
                id=2,
                serie_id="CONT-2",
                address="CORDOBA AV. 2578",
                latitude=-34.598658,
                longitude=-58.403466,
                geom=WKTElement("POINT(-58.403466 -34.598658)", srid=4326),
                current_level=70,
                available=True,
                container_type=ct_lateral,
            )
            # 1 contenedor de fracción seca (verde) en la misma ubicación
            c3 = Container(
                id=3,
                serie_id="CONT-3",
                address="CORDOBA AV. 2576",
                latitude=-34.598671,
                longitude=-58.403441,
                geom=WKTElement("POINT(-58.403441 -34.598671)", srid=4326),
                current_level=50,
                available=True,
                container_type=ct_campana,
            )
            session.add_all([c1, c2, c3])
            await session.commit()

            # Ejecutar clustering monotipo
            sites_count = await cluster_and_create_sites(
                session, distance_threshold_m=20.0
            )
            self.assertEqual(
                sites_count,
                2,
                "Deben crearse 2 sitios monotipo: 1 para húmedos (agrupando 2576 y 2578) y 1 para secos",
            )

            # Verificar sitios creados
            res_sites = await session.execute(select(Site).order_by(Site.id))
            sites = res_sites.scalars().all()
            self.assertEqual(len(sites), 2)

            # Sitio 1 (Húmedo): agrupa c1 y c2
            site_humedo = sites[0]
            self.assertEqual(site_humedo.waste_type_id, wt_humedo.id)
            self.assertIn("CORDOBA AV. 2576", site_humedo.name)

            # Verificar que los contenedores tienen asignado el site_id correcto
            res_c1 = await session.get(Container, 1)
            res_c2 = await session.get(Container, 2)
            res_c3 = await session.get(Container, 3)

            self.assertEqual(res_c1.site_id, site_humedo.id)
            self.assertEqual(res_c2.site_id, site_humedo.id)
            self.assertEqual(res_c3.site_id, sites[1].id)

    async def test_site_service_level_aggregation_avg_and_max(self):
        async with self.session_maker() as session:
            # Setup de tipos
            wt = WasteType(id=1, name="RSU Fracción Húmeda", color="#333333")
            ct = ContainerType(id=1, name="Carga Lateral", height_cm=145, volume_m3=3.2)
            ct.waste_types.append(wt)
            session.add_all([wt, ct])
            await session.flush()

            site = Site(
                id=1,
                name="Sitio Test Agregación",
                address="CALLE FALSA 123",
                latitude=-34.6000,
                longitude=-58.4000,
                geom=WKTElement("POINT(-58.4000 -34.6000)", srid=4326),
                waste_type_id=1,
            )
            session.add(site)
            await session.flush()

            c1 = Container(
                id=1,
                site_id=1,
                serie_id="C1",
                latitude=-34.6000,
                longitude=-58.4000,
                geom=WKTElement("POINT(-58.4000 -34.6000)", srid=4326),
                current_level=20,
                change_version=10,
                available=True,
                container_type=ct,
            )
            c2 = Container(
                id=2,
                site_id=1,
                serie_id="C2",
                latitude=-34.6000,
                longitude=-58.4000,
                geom=WKTElement("POINT(-58.4000 -34.6000)", srid=4326),
                current_level=80,
                change_version=10,
                available=True,
                container_type=ct,
            )
            session.add_all([c1, c2])
            await session.commit()

            # 1. Consulta con level_aggregation="avg" -> round((20 + 80) / 2) = 50
            site_avg = await get_site_by_id(session, site_id=1, level_aggregation="avg")
            self.assertEqual(site_avg.current_level, 50)
            self.assertEqual(site_avg.container_count, 2)
            self.assertEqual(len(site_avg.containers), 2)

            # 2. Consulta con level_aggregation="max" -> max(20, 80) = 80
            site_max = await get_site_by_id(session, site_id=1, level_aggregation="max")
            self.assertEqual(site_max.current_level, 80)

            # 3. Snapshot de sitios
            snapshot = await get_site_map_snapshot(session, level_aggregation="avg")
            self.assertEqual(snapshot.total, 1)
            self.assertEqual(snapshot.latest_cursor, 10)
            self.assertEqual(snapshot.sites[0].current_level, 50)

            # 4. Changes de sitios
            changes = await get_site_changes(session, after=0, level_aggregation="avg")
            self.assertEqual(len(changes.sites), 1)
            self.assertEqual(changes.latest_cursor, 10)
            self.assertEqual(changes.sites[0].id, 1)

            no_changes = await get_site_changes(
                session, after=10, level_aggregation="avg"
            )
            self.assertEqual(len(no_changes.sites), 0)
            self.assertEqual(no_changes.latest_cursor, 10)

            active_sim = SimulationSession(
                id=1,
                status="running",
                scenario={"start": "2026-01-01T00:00:00Z"},
                simulated_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                total_periods=100,
                created_by=1,
            )
            session.add(active_sim)
            session.add_all(
                [
                    DataLevel(
                        id=1,
                        reading_date=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
                        container_id=1,
                        container_current_level=20,
                        fire_alarm=False,
                        freeze_alarm=False,
                        crash_alarm=False,
                        garbage_collection_alarm=False,
                        reported_low_consumption_voltage=False,
                        reported_high_consumption_voltage=False,
                    ),
                    DataLevel(
                        id=2,
                        reading_date=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
                        container_id=2,
                        container_current_level=80,
                        fire_alarm=False,
                        freeze_alarm=False,
                        crash_alarm=False,
                        garbage_collection_alarm=False,
                        reported_low_consumption_voltage=False,
                        reported_high_consumption_voltage=False,
                    ),
                    DataLevel(
                        id=3,
                        reading_date=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
                        container_id=1,
                        container_current_level=40,
                        fire_alarm=False,
                        freeze_alarm=False,
                        crash_alarm=False,
                        garbage_collection_alarm=False,
                        reported_low_consumption_voltage=False,
                        reported_high_consumption_voltage=False,
                    ),
                ]
            )
            await session.commit()

            history = await get_site_level_history(session, site_id=1)
            self.assertEqual(len(history.points), 2)
            self.assertEqual(history.points[0].avg_level, 50)
            self.assertEqual(history.points[0].max_level, 80)
            self.assertEqual(history.points[0].min_level, 20)
            self.assertEqual(history.points[0].measurement_count, 2)

    async def test_get_sites_clustered_bbox(self):
        async with self.session_maker() as session:
            wt = WasteType(id=1, name="RSU Fracción Húmeda")
            ct = ContainerType(id=1, name="Carga Lateral", height_cm=145, volume_m3=3.2)
            ct.waste_types.append(wt)
            session.add_all([wt, ct])
            await session.flush()

            site = Site(
                id=1,
                name="Sitio BBox",
                latitude=-34.6000,
                longitude=-58.4000,
                geom=WKTElement("POINT(-58.4000 -34.6000)", srid=4326),
                waste_type_id=1,
            )
            session.add(site)
            await session.flush()

            c = Container(
                id=1,
                site_id=1,
                latitude=-34.6000,
                longitude=-58.4000,
                geom=WKTElement("POINT(-58.4000 -34.6000)", srid=4326),
                current_level=45,
                available=True,
                container_type=ct,
            )
            session.add(c)
            await session.commit()

            # Zoom alto (>= 18): Devuelve lista de SiteMapOutputSchema
            res_high_zoom = await get_sites_clustered(
                db=session,
                lat_min=-34.61,
                lat_max=-34.59,
                lng_min=-58.41,
                lng_max=-58.39,
                zoom=18,
                level_aggregation="avg",
            )
            self.assertEqual(len(res_high_zoom), 1)
            self.assertEqual(res_high_zoom[0].id, 1)
            self.assertEqual(res_high_zoom[0].current_level, 45)

            # Zoom bajo (< 18): Devuelve lista de SiteCluster
            res_low_zoom = await get_sites_clustered(
                db=session,
                lat_min=-34.61,
                lat_max=-34.59,
                lng_min=-58.41,
                lng_max=-58.39,
                zoom=14,
                level_aggregation="avg",
            )
            self.assertEqual(len(res_low_zoom), 1)
            self.assertEqual(res_low_zoom[0].count, 1)
            self.assertEqual(res_low_zoom[0].avg_level, 45.0)

    async def test_sites_http_endpoints(self):
        from httpx import ASGITransport, AsyncClient

        from app.core.map_database import get_map_db
        from app.main import app

        async with self.session_maker() as session:
            wt = WasteType(id=1, name="RSU Fracción Húmeda", color="#000000")
            ct = ContainerType(id=1, name="Carga Lateral", height_cm=145, volume_m3=3.2)
            ct.waste_types.append(wt)
            session.add_all([wt, ct])
            await session.flush()

            site = Site(
                id=1,
                name="Sitio Endpoint Test",
                address="Av. Corrientes 1000",
                latitude=-34.6000,
                longitude=-58.4000,
                geom=WKTElement("POINT(-58.4000 -34.6000)", srid=4326),
                waste_type_id=1,
            )
            session.add(site)
            await session.flush()

            c = Container(
                id=1,
                site_id=1,
                serie_id="C1",
                latitude=-34.6000,
                longitude=-58.4000,
                geom=WKTElement("POINT(-58.4000 -34.6000)", srid=4326),
                current_level=60,
                change_version=5,
                available=True,
                container_type=ct,
            )
            session.add(c)
            await session.commit()

        async def override_get_map_db():
            async with self.session_maker() as s:
                yield s

        app.dependency_overrides[get_map_db] = override_get_map_db
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # 1. Test /map/sites/bbox (zoom >= 18: individual sites)
                res_bbox = await client.get(
                    "/map/sites/bbox",
                    params={
                        "lat_min": -34.61,
                        "lat_max": -34.59,
                        "lng_min": -58.41,
                        "lng_max": -58.39,
                        "zoom": 19,
                        "level_aggregation": "avg",
                    },
                )
                self.assertEqual(res_bbox.status_code, 200)
                data_bbox = res_bbox.json()
                self.assertEqual(len(data_bbox), 1)
                self.assertEqual(data_bbox[0]["id"], 1)
                self.assertEqual(data_bbox[0]["current_level"], 60)

                # 1b. Test /map/sites/bbox (zoom < 18: clusters)
                res_cluster = await client.get(
                    "/map/sites/bbox",
                    params={
                        "lat_min": -34.70,
                        "lat_max": -34.50,
                        "lng_min": -58.55,
                        "lng_max": -58.30,
                        "zoom": 13,
                        "level_aggregation": "avg",
                    },
                )
                self.assertEqual(res_cluster.status_code, 200)
                data_cluster = res_cluster.json()
                self.assertGreaterEqual(len(data_cluster), 1)
                self.assertIn("cluster_id", data_cluster[0])
                self.assertEqual(data_cluster[0]["count"], 1)

                # 2. Test /map/sites/bbox/snapshot
                res_snap = await client.get(
                    "/map/sites/bbox/snapshot?level_aggregation=avg"
                )
                self.assertEqual(res_snap.status_code, 200)
                data_snap = res_snap.json()
                self.assertEqual(data_snap["total"], 1)
                self.assertEqual(data_snap["latest_cursor"], 5)

                # 3. Test /map/sites/changes
                res_changes = await client.get(
                    "/map/sites/changes?after=0&level_aggregation=avg"
                )
                self.assertEqual(res_changes.status_code, 200)
                data_changes = res_changes.json()
                self.assertEqual(len(data_changes["sites"]), 1)
                self.assertEqual(data_changes["latest_cursor"], 5)

                # 4. Test /map/sites/1
                res_single = await client.get("/map/sites/1?level_aggregation=avg")
                self.assertEqual(res_single.status_code, 200)
                data_single = res_single.json()
                self.assertEqual(data_single["name"], "Sitio Endpoint Test")
                self.assertEqual(data_single["current_level"], 60)
                self.assertEqual(len(data_single["containers"]), 1)
        finally:
            app.dependency_overrides.pop(get_map_db, None)
