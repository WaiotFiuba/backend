from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.schemas.map.site_projection import SiteProjectionBatchRequest
from app.services.site_projection import chronos, service
from app.services.site_projection.history import SiteLevelBucket
from app.services.site_projection.models import ForecastContext, ForecastRequest


@pytest.mark.asyncio
async def test_chronos_single_uses_snapshot_as_last_input(monkeypatch):
    as_of = datetime(2026, 1, 1, 5, 30, tzinfo=UTC)
    history = [
        SiteLevelBucket(
            site_id=1,
            timestamp=as_of.replace(hour=hour, minute=0),
            level=level,
            avg_level=level,
            max_level=level,
            min_level=level,
            measurement_count=1,
            container_count=1,
            has_collection_signal=False,
        )
        for hour, level in [(2, 10), (3, 20), (4, 30), (5, 40)]
    ]
    seen = []

    class FakePipeline:
        def predict_df(self, frame, **_kwargs):
            seen.append(frame)
            return pd.DataFrame({"0.1": [45], "0.5": [50], "0.9": [55]})

    monkeypatch.setattr(chronos, "_get_chronos_pipeline", lambda: FakePipeline())
    result = await chronos.Chronos2SmallForecaster().forecast(
        ForecastRequest(site_id=1, horizon_hours=1),
        ForecastContext(
            current_level=45,
            history=history,
            site_features={},
            generated_at=as_of,
            snapshot_at=as_of,
        ),
    )

    assert seen[0].iloc[-1]["target"] == 45
    assert seen[0].iloc[-1]["timestamp"] == pd.Timestamp("2026-01-01 05:00:00")
    assert result.points[0].predicted_level == 50


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

    async def snapshots(_db, site_ids, _as_of, _aggregation, skip_incomplete=False):
        assert skip_incomplete is True
        return {
            site_id: service.SiteStartingSnapshot(
                level={1: 40.0, 2: 50.0, 3: 0.0}[site_id],
                volume=service.SiteVolumeSnapshot(),
            )
            for site_id in site_ids
        }

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

    monkeypatch.setattr(service, "_get_site_snapshots_at", snapshots)
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
    for site_id, level in [(1, 40), (2, 50)]:
        site_rows = calls[0][0].loc[calls[0][0]["id"] == site_id]
        assert site_rows.iloc[-1]["timestamp"] == pd.Timestamp(
            as_of.replace(tzinfo=None)
        )
        assert site_rows.iloc[-1]["target"] == level
    assert calls[0][1]["prediction_length"] == 2
    assert [site.points[0].predicted_level for site in result.sites[:2]] == [50, 60]
    assert result.sites[2].points[0].reason == "insufficient_history"
