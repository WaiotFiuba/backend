from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.schemas.map.site_projection import SiteProjectionBatchRequest
from app.services.site_projection import chronos, service
from app.services.site_projection.history import SiteLevelBucket


@pytest.mark.asyncio
async def test_chronos_batch_infers_eligible_sites_once_and_keeps_results_separate(
    monkeypatch,
):
    as_of = datetime(2026, 1, 1, 5, tzinfo=UTC)
    history = {
        site_id: [
            SiteLevelBucket(
                site_id=site_id,
                timestamp=as_of - timedelta(hours=4 - index),
                level=level,
                avg_level=level,
                max_level=level,
                min_level=level,
                measurement_count=1,
                container_count=1,
                has_collection_signal=False,
            )
            for index, level in enumerate(levels)
        ]
        for site_id, levels in {1: [10, 20, 30, 40], 2: [20, 30, 40, 50]}.items()
    }

    async def current_levels(_db, _site_ids, _aggregation):
        return {1: 40.0, 2: 50.0, 3: 0.0}

    async def site_history(**_kwargs):
        return history

    async def site_features(_db, _site_ids):
        return {}

    calls = []

    class FakePipeline:
        def predict_df(self, frame, **kwargs):
            calls.append((frame, kwargs))
            return pd.DataFrame(
                {
                    "id": [2, 2, 1, 1],
                    "0.1": [55, 60, 45, 50],
                    "0.5": [60, 65, 50, 55],
                    "0.9": [65, 70, 55, 60],
                }
            )

    monkeypatch.setattr(service, "_get_current_site_levels", current_levels)
    async def volumes(_db, site_ids):
        return {site_id: service.SiteVolumeSnapshot() for site_id in site_ids}

    monkeypatch.setattr(service, "_get_current_site_volumes", volumes)
    monkeypatch.setattr(service, "get_site_level_series", site_history)
    monkeypatch.setattr(service, "_get_sites_features", site_features)
    monkeypatch.setattr(chronos, "_get_chronos_pipeline", lambda: FakePipeline())

    result = await service.project_sites(
        object(),
        SiteProjectionBatchRequest(
            site_ids=[1, 2, 3],
            model_key="chronos_2_small",
            as_of=as_of,
            horizon_hours=2,
        ),
    )

    assert len(calls) == 1
    assert set(calls[0][0]["id"]) == {1, 2}
    assert calls[0][1]["prediction_length"] == 2
    assert [site.points[0].predicted_level for site in result.sites[:2]] == [50, 60]
    assert result.sites[2].points[0].reason == "insufficient_history"
