from datetime import UTC, datetime, timedelta

import pytest

from app.schemas.map.site_projection import SiteProjectionEvaluationRequest
from app.services.site_projection import evaluation as evaluation_service
from app.services.site_projection.history import SiteLevelBucket


class FakeSession:
    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, _evaluation_id):
        return self.state["evaluation"]

    def add(self, evaluation):
        evaluation.id = 1
        self.state["evaluation"] = evaluation

    def add_all(self, metrics):
        self.state["metrics"].extend(metrics)

    async def commit(self):
        return None

    async def refresh(self, _evaluation):
        return None


def bucket(site_id: int, timestamp: datetime, level: float) -> SiteLevelBucket:
    return SiteLevelBucket(
        site_id=site_id,
        timestamp=timestamp,
        level=level,
        avg_level=level,
        max_level=level,
        min_level=level,
        measurement_count=1,
        container_count=1,
        has_collection_signal=False,
    )


@pytest.mark.asyncio
async def test_background_evaluation_persists_status_and_batch_metrics(monkeypatch):
    state = {"evaluation": None, "metrics": []}
    monkeypatch.setattr(
        evaluation_service, "MapSessionLocal", lambda: FakeSession(state)
    )
    cutoff = datetime(2026, 1, 1, 10, tzinfo=UTC)

    async def resolve_cutoffs(_db, _request):
        return [cutoff], False

    async def resolve_sites(_db, _request, _cutoffs):
        return [1, 2]

    async def fetch_samples(*_args, **_kwargs):
        return []

    async def fetch_features(_db, _site_ids):
        return {}

    def aggregate(_samples, **_kwargs):
        return {
            site_id: [
                bucket(site_id, cutoff + timedelta(hours=hour), level)
                for hour, level in [(-2, 10), (-1, 20), (0, 30), (1, 40), (2, 50)]
            ]
            for site_id in [1, 2]
        }

    monkeypatch.setattr(evaluation_service, "_resolve_cutoffs", resolve_cutoffs)
    monkeypatch.setattr(
        evaluation_service, "_resolve_evaluation_site_ids", resolve_sites
    )
    monkeypatch.setattr(evaluation_service, "fetch_site_measurements", fetch_samples)
    monkeypatch.setattr(evaluation_service, "_get_features_by_site", fetch_features)
    monkeypatch.setattr(evaluation_service, "aggregate_site_measurements", aggregate)

    request = SiteProjectionEvaluationRequest(site_ids=[1, 2], horizon_hours=2)
    created = await evaluation_service.create_site_projection_evaluation(
        FakeSession(state), request
    )
    assert created.status == "pending"
    assert created.cutoff is None

    await evaluation_service.run_site_projection_evaluation(created.id, request)

    evaluation = state["evaluation"]
    assert evaluation.status == "completed"
    assert evaluation.site_count == 2
    assert evaluation.summary["compared_points"] == 4
    assert evaluation.config["selected_site_ids"] == [1, 2]
    assert len(state["metrics"]) > 0
    status = await evaluation_service.get_site_projection_evaluation_status(
        FakeSession(state), created.id
    )
    assert status.status == "completed"
    assert status.cutoff == cutoff
    assert status.metrics == []


@pytest.mark.asyncio
async def test_background_evaluation_records_failure(monkeypatch):
    state = {"evaluation": None, "metrics": []}
    monkeypatch.setattr(
        evaluation_service, "MapSessionLocal", lambda: FakeSession(state)
    )

    async def fail_cutoffs(_db, _request):
        raise ValueError("No hay mediciones")

    monkeypatch.setattr(evaluation_service, "_resolve_cutoffs", fail_cutoffs)
    request = SiteProjectionEvaluationRequest(site_ids=[1])
    created = await evaluation_service.create_site_projection_evaluation(
        FakeSession(state), request
    )

    await evaluation_service.run_site_projection_evaluation(created.id, request)

    assert state["evaluation"].status == "failed"
    assert "No se pudo completar" in state["evaluation"].summary["error"]


@pytest.mark.asyncio
async def test_background_evaluation_reads_once_and_evaluates_every_cutoff(
    monkeypatch,
):
    state = {"evaluation": None, "metrics": []}
    monkeypatch.setattr(
        evaluation_service, "MapSessionLocal", lambda: FakeSession(state)
    )
    cutoffs = [
        datetime(2026, 1, 1, 10, tzinfo=UTC),
        datetime(2026, 1, 1, 23, tzinfo=UTC),
    ]
    fetches = []

    async def resolve_cutoffs(_db, _request):
        return cutoffs, False

    async def resolve_sites(_db, _request, _cutoffs):
        return [1, 2]

    async def fetch_samples(_db, site_ids, **kwargs):
        fetches.append((site_ids, kwargs))
        return []

    async def fetch_features(_db, _site_ids):
        return {}

    def aggregate(_samples, **_kwargs):
        start = cutoffs[0] - timedelta(hours=2)
        return {
            site_id: [
                bucket(site_id, start + timedelta(hours=hour), 10 + hour)
                for hour in range(18)
            ]
            for site_id in [1, 2]
        }

    monkeypatch.setattr(evaluation_service, "_resolve_cutoffs", resolve_cutoffs)
    monkeypatch.setattr(
        evaluation_service, "_resolve_evaluation_site_ids", resolve_sites
    )
    monkeypatch.setattr(evaluation_service, "fetch_site_measurements", fetch_samples)
    monkeypatch.setattr(evaluation_service, "_get_features_by_site", fetch_features)
    monkeypatch.setattr(evaluation_service, "aggregate_site_measurements", aggregate)

    request = SiteProjectionEvaluationRequest(
        site_ids=[1, 2], horizon_hours=2, lookback_days=1
    )
    created = await evaluation_service.create_site_projection_evaluation(
        FakeSession(state), request
    )
    await evaluation_service.run_site_projection_evaluation(created.id, request)

    evaluation = state["evaluation"]
    assert evaluation.status == "completed"
    assert len(fetches) == 1
    assert fetches[0][1]["end_time"] == cutoffs[-1] + timedelta(hours=2)
    assert fetches[0][1]["lookback_days"] == 2
    assert evaluation.summary["site_count"] == 2
    assert evaluation.summary["cutoff_count"] == 2
    assert evaluation.summary["case_count"] == 4
    assert evaluation.summary["short_history"] is False
    assert evaluation.summary["events_observed"] == 0
    global_keys = {
        metric.metric_key
        for metric in state["metrics"]
        if metric.metric_scope == "global"
    }
    assert {"event_precision", "event_recall", "event_f1"} <= global_keys
    assert evaluation.config["cutoffs"] == [c.isoformat() for c in cutoffs]
    assert evaluation.cutoff_start_at == cutoffs[0]
    assert evaluation.cutoff_end_at == cutoffs[-1]
    site_mae = [
        (metric.site_id, metric.sample_count)
        for metric in state["metrics"]
        if metric.metric_scope == "site" and metric.metric_key == "mae"
    ]
    assert site_mae == [(1, 4), (2, 4)]
