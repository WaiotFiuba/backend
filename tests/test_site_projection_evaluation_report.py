from datetime import UTC, datetime

from app.models.map.site_projection import (
    SiteModelEvaluation,
    SiteModelEvaluationMetric,
)
from app.services.map.site_projection_evaluation_service import _evaluation_report


def metric(
    metric_scope: str,
    metric_key: str,
    metric_value: float | None,
    site_id: int | None = None,
    sample_count: int | None = None,
) -> SiteModelEvaluationMetric:
    return SiteModelEvaluationMetric(
        evaluation_id=1,
        metric_scope=metric_scope,
        metric_key=metric_key,
        metric_value=metric_value,
        site_id=site_id,
        sample_count=sample_count,
    )


def test_evaluation_report_groups_global_metrics_and_ranks_sites_by_mae():
    evaluation = SiteModelEvaluation(
        id=1,
        model_key="baseline_operational",
        status="completed",
        config={},
        horizon_hours=24,
        interval_minutes=60,
        critical_level=80,
        level_aggregation="avg",
        site_count=3,
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
        summary={"compared_points": 12},
    )
    metrics = [
        metric("global", "mae", 8.5, sample_count=12),
        metric("global", "rmse", 10.2, sample_count=12),
        metric("site", "mae", 2.0, site_id=10, sample_count=4),
        metric("site", "rmse", 3.0, site_id=10, sample_count=4),
        metric("site", "mae", 12.0, site_id=11, sample_count=4),
        metric("site", "rmse", 15.0, site_id=11, sample_count=4),
        metric("site", "mae", 6.0, site_id=12, sample_count=4),
        metric("site", "rmse", 7.0, site_id=12, sample_count=4),
    ]

    report = _evaluation_report(evaluation, metrics)

    assert report.global_metrics == {"mae": 8.5, "rmse": 10.2}
    assert [site.site_id for site in report.best_predicted_sites] == [10, 12, 11]
    assert [site.site_id for site in report.worst_predicted_sites] == [11, 12, 10]
    assert report.site_metrics[0].sample_count == 4
