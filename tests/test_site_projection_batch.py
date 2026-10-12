from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import Column, DateTime, Float, Integer, MetaData, Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.routers.map.site_projections import router
from app.schemas.map.site_projection import (
    SiteProjectionBatchRequest,
    SiteProjectionPoint,
    SiteProjectionResponse,
)
from app.services.site_projection import chronos, service
from app.services.site_projection.history import SiteLevelBucket
from app.services.site_projection.models import (
    ForecastContext,
    ForecastPoint,
    ForecastRequest,
    ForecastResult,
)


def test_projection_routes_keep_batch_and_evaluations_without_run_lookup():
    paths = {
        (method, route.path) for route in router.routes for method in route.methods
    }

    assert ("POST", "/site-projections") in paths
    assert ("POST", "/site-projections/evaluations") in paths
    assert ("GET", "/site-projections/{run_id}") not in paths


@pytest.mark.asyncio
async def test_batch_uses_explicit_time_without_writing(monkeypatch):
    as_of = datetime(2026, 10, 10, 12, 30, tzinfo=UTC)
    target_at = as_of + timedelta(hours=2)
    calls = []

    def fake_build_response(
        request, context, volume_snapshot, forecast_result, **kwargs
    ):
        calls.append(
            {
                "request": request,
                "context": context,
                "volume_snapshot": volume_snapshot,
                "forecast_result": forecast_result,
                **kwargs,
            }
        )
        site_id = request.site_id
        return SiteProjectionResponse(
            site_id=site_id,
            model_key="baseline_operational",
            generated_at=as_of,
            current_level=20,
            horizon_hours=request.horizon_hours,
            interval_minutes=60,
            critical_level=80,
            level_aggregation="avg",
            lookback_days=14,
            stop_at_full=False,
            confidence=0.65,
            points=[
                SiteProjectionPoint(
                    timestamp=as_of.replace(hour=14, minute=0),
                    predicted_level=40,
                    confidence=0.65,
                ),
                SiteProjectionPoint(
                    timestamp=as_of.replace(hour=15, minute=0),
                    predicted_level=50 + site_id,
                    confidence=0.65,
                ),
            ],
        )

    monkeypatch.setattr(service, "build_site_projection_response", fake_build_response)
    history_calls = []

    async def fake_history(**kwargs):
        history_calls.append(kwargs)
        return {1: ["site-1-history"], 2: ["site-2-history"]}

    monkeypatch.setattr(service, "get_site_level_series", fake_history)
    snapshot_calls = []

    async def fake_snapshots(_db, site_ids, snapshot_at, level_aggregation):
        snapshot_calls.append((site_ids, snapshot_at, level_aggregation))
        assert site_ids == [1, 2]
        return {
            1: service.SiteStartingSnapshot(0.0, service.SiteVolumeSnapshot(0.0, 3.0)),
            2: service.SiteStartingSnapshot(20.0, service.SiteVolumeSnapshot(1.2, 6.0)),
        }

    monkeypatch.setattr(service, "_get_site_snapshots_at", fake_snapshots)

    async def no_live_levels(*_args):
        raise AssertionError("read live levels for explicit as_of")

    monkeypatch.setattr(service, "_get_current_site_levels", no_live_levels)
    feature_calls = []

    async def fake_features(_db, site_ids):
        feature_calls.append(site_ids)
        return {1: {"demand.density_factor": 1.2}}

    monkeypatch.setattr(service, "_get_sites_features", fake_features)
    batch_calls = []

    class FakeForecaster:
        async def forecast_many(self, inputs):
            batch_calls.append(inputs)
            return {
                request.site_id: ForecastResult(
                    site_id=request.site_id,
                    model_key="baseline_operational",
                    generated_at=as_of,
                    points=[],
                )
                for request, _context in inputs
            }

    monkeypatch.setattr(service, "get_forecaster", lambda _key: FakeForecaster())
    db = SimpleNamespace()
    result = await service.project_sites(
        db,
        SiteProjectionBatchRequest(
            site_ids=[1, 2, 1], as_of=as_of, target_at=target_at
        ),
    )

    assert [site.site_id for site in result.sites] == [1, 2]
    assert [site.target_point.predicted_level for site in result.sites] == [51, 52]
    assert all(site.target_point.timestamp.hour == 15 for site in result.sites)
    assert all(call["context"].generated_at == as_of for call in calls)
    assert [call["context"].history for call in calls] == [
        ["site-1-history"],
        ["site-2-history"],
    ]
    assert len(history_calls) == 1
    assert history_calls[0]["site_ids"] == [1, 2]
    assert history_calls[0]["end_time"] == as_of
    assert snapshot_calls == [([1, 2], as_of, "avg")]
    assert [call["context"].current_level for call in calls] == [0.0, 20.0]
    assert all(call["context"].snapshot_at == as_of for call in calls)
    assert [call["volume_snapshot"].current_volume_m3 for call in calls] == [0.0, 1.2]
    assert [call["volume_snapshot"].capacity_m3 for call in calls] == [3.0, 6.0]
    assert feature_calls == [[1, 2]]
    assert [call["context"].site_features for call in calls] == [
        {"demand.density_factor": 1.2},
        {},
    ]
    assert len(batch_calls) == 1
    assert [request.site_id for request, _ in batch_calls[0]] == [1, 2]
    assert all(call["forecast_result"] is not None for call in calls)
    assert all(call["stop_at_full"] is False for call in calls)
    assert result.target_at == target_at


@pytest.mark.asyncio
async def test_explicit_as_of_skips_simulation_clock(monkeypatch):
    async def fail_if_called(_db):
        raise AssertionError("simulation clock was used")

    async def snapshot(_db, _site_ids, _as_of, _aggregation):
        return {
            1: service.SiteStartingSnapshot(20.0, service.SiteVolumeSnapshot(1.5, 3.0))
        }

    async def history(**_kwargs):
        return {}

    async def features(_db, _site_id):
        return {}

    monkeypatch.setattr(service, "_projection_clock", fail_if_called)
    monkeypatch.setattr(service, "_get_site_snapshots_at", snapshot)
    monkeypatch.setattr(service, "get_site_level_series", history)
    monkeypatch.setattr(service, "_get_site_features", features)
    as_of = datetime(2026, 10, 10, 12, tzinfo=UTC)

    result = await service.project_site_level(object(), site_id=1, as_of=as_of)

    assert result.generated_at == as_of


@pytest.mark.asyncio
async def test_batch_rejects_target_before_reference():
    as_of = datetime(2026, 10, 10, 12, tzinfo=UTC)
    with pytest.raises(HTTPException) as exc:
        await service.project_sites(
            object(),
            SiteProjectionBatchRequest(site_ids=[1], as_of=as_of, target_at=as_of),
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_batch_uses_baseline_forecast_many(monkeypatch):
    async def current_levels(_db, _site_ids, _aggregation):
        return {1: 20.0, 2: 40.0}

    async def volumes(_db, _site_ids):
        return {
            1: service.SiteVolumeSnapshot(0.6, 3.0),
            2: service.SiteVolumeSnapshot(1.2, 3.0),
        }

    async def clock(_db):
        return datetime(2026, 10, 10, 12, tzinfo=UTC)

    async def no_history(**_kwargs):
        return {}

    async def no_features(_db, _site_ids):
        return {}

    monkeypatch.setattr(service, "_get_current_site_levels", current_levels)
    monkeypatch.setattr(service, "_get_current_site_volumes", volumes)
    monkeypatch.setattr(service, "_projection_clock", clock)
    monkeypatch.setattr(service, "get_site_level_series", no_history)
    monkeypatch.setattr(service, "_get_sites_features", no_features)

    result = await service.project_sites(
        object(),
        SiteProjectionBatchRequest(
            site_ids=[1, 2],
            horizon_hours=1,
        ),
    )

    assert result.model_key == "baseline_operational"
    assert [site.points[0].predicted_level for site in result.sites] == [21, 41]
    assert [site.current_volume_m3 for site in result.sites] == [0.6, 1.2]
    assert [site.capacity_m3 for site in result.sites] == [3.0, 3.0]
    assert [site.points[0].predicted_volume_m3 for site in result.sites] == [
        0.63,
        1.23,
    ]


@pytest.mark.asyncio
async def test_current_volume_uses_each_container_capacity_and_requires_complete_data():
    rows = [
        SimpleNamespace(site_id=1, current_level=20, volume_m3=3.0),
        SimpleNamespace(site_id=1, current_level=80, volume_m3=2.0),
        SimpleNamespace(site_id=2, current_level=30, volume_m3=None),
        SimpleNamespace(site_id=2, current_level=50, volume_m3=3.0),
    ]

    class FakeDB:
        async def execute(self, _statement):
            return SimpleNamespace(all=lambda: rows)

    assert await service._get_current_site_volumes(FakeDB(), [1, 2, 3]) == {
        1: service.SiteVolumeSnapshot(2.2, 5.0),
        2: service.SiteVolumeSnapshot(),
        3: service.SiteVolumeSnapshot(),
    }


def test_projected_volume_uses_current_volume_and_one_site_forecast():
    as_of = datetime(2026, 10, 10, 12, tzinfo=UTC)
    history = [
        SiteLevelBucket(
            site_id=1,
            timestamp=as_of - timedelta(hours=1),
            level=20,
            avg_level=20,
            max_level=20,
            min_level=20,
            measurement_count=1,
            container_count=1,
            has_collection_signal=False,
        )
    ]
    result = service.build_site_projection_response(
        ForecastRequest(site_id=1),
        ForecastContext(
            current_level=50, history=history, site_features={}, generated_at=as_of
        ),
        service.SiteVolumeSnapshot(2.2, 5.0),
        ForecastResult(
            site_id=1,
            model_key="baseline_operational",
            generated_at=as_of,
            points=[
                ForecastPoint(
                    timestamp=as_of + timedelta(hours=1),
                    predicted_level=60,
                    confidence=0.7,
                )
            ],
        ),
        stop_at_full=True,
    )

    assert result.points[0].predicted_level == 60
    assert result.points[0].predicted_volume_m3 == 2.7
    assert service._estimate_site_volume(service.SiteVolumeSnapshot(), 50, 60) is None
    assert (
        service._estimate_site_volume(service.SiteVolumeSnapshot(4.8, 5), 20, 40) == 5
    )
    assert (
        service._estimate_site_volume(service.SiteVolumeSnapshot(0.2, 5), 80, 20) == 0
    )


def test_explicit_snapshot_replaces_last_chronos_bucket():
    as_of = datetime(2026, 10, 10, 12, tzinfo=UTC)
    history = [
        SiteLevelBucket(
            site_id=1,
            timestamp=as_of,
            level=20,
            avg_level=20,
            max_level=20,
            min_level=20,
            measurement_count=1,
            container_count=1,
            has_collection_signal=False,
        )
    ]
    context = ForecastContext(
        current_level=50,
        history=history,
        site_features={},
        generated_at=as_of,
        snapshot_at=as_of,
    )
    prepared = chronos._history_with_snapshot(context, 1, 60)
    assert len(prepared) == 1
    assert prepared[-1].timestamp == as_of
    assert prepared[-1].level == 50
    assert history[-1].level == 20

    result = service.build_site_projection_response(
        ForecastRequest(site_id=1),
        context,
        service.SiteVolumeSnapshot(2.2, 5.0),
        ForecastResult(
            site_id=1,
            model_key="chronos_2_small",
            generated_at=as_of,
            points=[
                ForecastPoint(
                    timestamp=as_of + timedelta(hours=1),
                    predicted_level=60,
                    lower_bound=50,
                    upper_bound=70,
                    confidence=0.7,
                ),
                ForecastPoint(
                    timestamp=as_of + timedelta(hours=2),
                    predicted_level=100,
                    confidence=0.7,
                ),
                ForecastPoint(
                    timestamp=as_of + timedelta(hours=3),
                    predicted_level=100,
                    confidence=0.7,
                ),
            ],
        ),
        stop_at_full=True,
    )

    assert result.current_level == 50
    assert result.points[0].predicted_level == 60
    assert result.points[0].lower_bound == 50
    assert result.points[0].upper_bound == 70
    assert result.points[0].predicted_volume_m3 == 2.7
    assert len(result.points) == 3
    assert result.points[-1].predicted_level == 100


@pytest.mark.asyncio
async def test_explicit_as_of_reconstructs_levels_and_volume_from_past_readings():
    metadata = MetaData()
    sites = Table(
        "sites",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("deleted_at", DateTime),
    )
    containers = Table(
        "containers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("site_id", Integer),
        Column("container_type_id", Integer),
        Column("deleted_at", DateTime),
    )
    container_types = Table(
        "container_types",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("volume_m3", Float),
    )
    readings = Table(
        "data_level",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("container_id", Integer),
        Column("reading_date", DateTime(timezone=True)),
        Column("container_current_level", Integer),
    )
    as_of = datetime(2026, 10, 10, 12, tzinfo=UTC)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.execute(sites.insert(), [{"id": 1}, {"id": 2}])
            await conn.execute(
                container_types.insert(),
                [
                    {"id": 1, "volume_m3": 3.0},
                    {"id": 2, "volume_m3": 2.0},
                ],
            )
            await conn.execute(
                containers.insert(),
                [
                    {"id": 10, "site_id": 1, "container_type_id": 1},
                    {"id": 11, "site_id": 1, "container_type_id": 2},
                    {"id": 12, "site_id": 2, "container_type_id": 1},
                ],
            )
            await conn.execute(
                readings.insert(),
                [
                    {
                        "id": 5,
                        "container_id": 10,
                        "reading_date": as_of - timedelta(hours=2),
                        "container_current_level": 5,
                    },
                    {
                        "id": 1,
                        "container_id": 10,
                        "reading_date": as_of - timedelta(hours=1),
                        "container_current_level": 20,
                    },
                    {
                        "id": 2,
                        "container_id": 11,
                        "reading_date": as_of - timedelta(hours=1),
                        "container_current_level": 80,
                    },
                    {
                        "id": 3,
                        "container_id": 10,
                        "reading_date": as_of + timedelta(hours=1),
                        "container_current_level": 80,
                    },
                    {
                        "id": 4,
                        "container_id": 11,
                        "reading_date": as_of + timedelta(hours=1),
                        "container_current_level": 10,
                    },
                ],
            )

        session_factory = async_sessionmaker(engine)
        async with session_factory() as db:
            snapshot = (await service._get_site_snapshots_at(db, [1], as_of, "avg"))[1]
            assert snapshot == service.SiteStartingSnapshot(
                level=50.0,
                volume=service.SiteVolumeSnapshot(2.2, 5.0),
            )
            later = (
                await service._get_site_snapshots_at(
                    db, [1], as_of + timedelta(hours=1), "max"
                )
            )[1]
            assert later.level == 80
            assert later.volume.current_volume_m3 == 2.6

            with pytest.raises(HTTPException) as exc:
                await service._get_site_snapshots_at(db, [1, 2], as_of, "avg")
            assert exc.value.status_code == 422
            assert "2" in exc.value.detail
    finally:
        await engine.dispose()
