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

    async def resolve_cutoff(_db, _request):
        return cutoff

    async def resolve_sites(_db, _request, _cutoff):
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

    monkeypatch.setattr(evaluation_service, "_resolve_automatic_cutoff", resolve_cutoff)
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

    async def fail_cutoff(_db, _request):
        raise ValueError("No hay mediciones")

    monkeypatch.setattr(evaluation_service, "_resolve_automatic_cutoff", fail_cutoff)
    request = SiteProjectionEvaluationRequest(site_ids=[1])
    created = await evaluation_service.create_site_projection_evaluation(
        FakeSession(state), request
    )

    await evaluation_service.run_site_projection_evaluation(created.id, request)

    assert state["evaluation"].status == "failed"
    assert "No se pudo completar" in state["evaluation"].summary["error"]
