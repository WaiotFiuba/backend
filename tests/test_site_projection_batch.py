from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.map.site_projections import router
from app.schemas.map.site_projection import (
    SiteProjectionBatchRequest,
    SiteProjectionPoint,
    SiteProjectionResponse,
)
from app.services.map import site_projection_service as service


def test_projection_routes_keep_batch_and_evaluations_without_run_lookup():
    paths = {(method, route.path) for route in router.routes for method in route.methods}

    assert ("POST", "/site-projections") in paths
    assert ("POST", "/site-projections/evaluations") in paths
    assert ("GET", "/site-projections/{run_id}") not in paths


@pytest.mark.asyncio
async def test_batch_uses_explicit_time_without_writing(monkeypatch):
    as_of = datetime(2026, 10, 10, 12, 30, tzinfo=UTC)
    target_at = as_of + timedelta(hours=2)
    calls = []

    async def fake_project_site_level(**kwargs):
        calls.append(kwargs)
        site_id = kwargs["site_id"]
        return SiteProjectionResponse(
            site_id=site_id,
            model_key="baseline_operational",
            generated_at=as_of,
            current_level=20,
            horizon_hours=kwargs["horizon_hours"],
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

    monkeypatch.setattr(service, "project_site_level", fake_project_site_level)
    history_calls = []

    async def fake_history(**kwargs):
        history_calls.append(kwargs)
        return {1: ["site-1-history"], 2: ["site-2-history"]}

    monkeypatch.setattr(service, "get_site_level_series", fake_history)
    level_calls = []

    async def fake_current_levels(_db, site_ids, level_aggregation):
        level_calls.append((site_ids, level_aggregation))
        return {1: 0.0, 2: 20.0}

    monkeypatch.setattr(service, "_get_current_site_levels", fake_current_levels)
    db = SimpleNamespace()
    result = await service.project_sites(
        db,
        SiteProjectionBatchRequest(site_ids=[1, 2, 1], as_of=as_of, target_at=target_at),
    )

    assert [site.site_id for site in result.sites] == [1, 2]
    assert [site.target_point.predicted_level for site in result.sites] == [51, 52]
    assert all(site.target_point.timestamp.hour == 15 for site in result.sites)
    assert all(call["as_of"] == as_of and call["db"] is db for call in calls)
    assert [call["history"] for call in calls] == [
        ["site-1-history"],
        ["site-2-history"],
    ]
    assert len(history_calls) == 1
    assert history_calls[0]["site_ids"] == [1, 2]
    assert history_calls[0]["end_time"] == as_of
    assert level_calls == [([1, 2], "avg")]
    assert [call["current_level"] for call in calls] == [0.0, 20.0]
    assert all(call["stop_at_full"] is False for call in calls)
    assert result.target_at == target_at


@pytest.mark.asyncio
async def test_explicit_as_of_skips_simulation_clock(monkeypatch):
    async def fail_if_called(_db):
        raise AssertionError("simulation clock was used")

    async def current_level(_db, **_kwargs):
        return 20.0

    async def history(**_kwargs):
        return {}

    async def features(_db, _site_id):
        return {}

    monkeypatch.setattr(service, "_projection_clock", fail_if_called)
    monkeypatch.setattr(service, "_get_current_site_level", current_level)
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
