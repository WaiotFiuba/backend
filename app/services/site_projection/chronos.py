from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from app.services.site_projection.generation import hourly_generation
from app.services.site_projection.history import SiteLevelBucket, floor_to_interval
from app.services.site_projection.models import (
    ForecastContext,
    ForecastModelInfo,
    ForecastPoint,
    ForecastRequest,
    ForecastResult,
)
from app.services.site_projection.utils import (
    _as_utc,
    _ceil_to_next_interval,
    _clamp_level,
)

logger = logging.getLogger(__name__)

CHRONOS_2_SMALL_MODEL = ForecastModelInfo(
    key="chronos_2_small",
    name="Chronos-2 Small (Amazon/AutoGluon)",
    description=(
        "Modelo fundacional zero-shot para series temporales (28M params). "
        "Pronostica lo generado por hora, sin las bajadas por recolección; "
        "mediana como nivel previsto e intervalo del 10% al 90%."
    ),
    status="available",
    requires_training=False,
    supports_site_features=False,
)

_pipeline_lock = threading.Lock()
_pipeline_instance: Any = None


def _get_chronos_pipeline() -> Any:
    global _pipeline_instance
    if _pipeline_instance is None:
        with _pipeline_lock:
            if _pipeline_instance is None:
                logger.info(
                    "Cargando pipeline de Chronos-2 Small (autogluon/chronos-2-small)..."
                )
                from chronos import Chronos2Pipeline

                _pipeline_instance = Chronos2Pipeline.from_pretrained(
                    "autogluon/chronos-2-small",
                    device_map="cpu",
                )
                logger.info("Pipeline de Chronos-2 Small cargado exitosamente.")
    return _pipeline_instance


def _levels_from_hourly(current_level: float, forecast) -> list[float]:
    """Nivel actual más lo que se genere hora a hora, sin restar nunca: los
    vaciados los decide quien planifica, no el modelo."""
    levels, total = [], 0.0
    for value in forecast:
        total += max(0.0, float(value))
        levels.append(_clamp_level(current_level + total))
    return levels


def _history_with_snapshot(
    context: ForecastContext, site_id: int, interval_minutes: int
) -> list[SiteLevelBucket]:
    history = sorted(context.history, key=lambda bucket: bucket.timestamp)
    if context.snapshot_at is None:
        return history

    bucket_time = floor_to_interval(context.snapshot_at, interval_minutes)
    if history and history[-1].timestamp == bucket_time:
        return [*history[:-1], replace(history[-1], level=context.current_level)]
    return [
        *history,
        SiteLevelBucket(
            site_id=site_id,
            timestamp=bucket_time,
            level=context.current_level,
            avg_level=context.current_level,
            max_level=context.current_level,
            min_level=context.current_level,
            measurement_count=0,
            container_count=0,
            has_collection_signal=False,
        ),
    ]


class Chronos2SmallForecaster:
    """Forecaster zero-shot basado en Amazon Chronos-2 Small."""

    model_info = CHRONOS_2_SMALL_MODEL

    async def forecast(
        self,
        request: ForecastRequest,
        context: ForecastContext,
    ) -> ForecastResult:
        generated_at = _as_utc(context.generated_at or datetime.now(UTC))
        history = _history_with_snapshot(
            context, request.site_id, request.interval_minutes
        )
        steps = max(1, int(request.horizon_hours * 60 / request.interval_minutes))
        next_timestamp = _ceil_to_next_interval(generated_at, request.interval_minutes)

        # Si el histórico es insuficiente para un modelo secuencial (mínimo 4 observaciones)
        if len(history) < 4:
            return self._fallback_forecast(
                request=request,
                context=context,
                generated_at=generated_at,
                steps=steps,
                next_timestamp=next_timestamp,
                reason="insufficient_history",
            )

        try:
            pipeline = await asyncio.to_thread(_get_chronos_pipeline)

            # Normalizar serie temporal a DataFrame para Chronos2Pipeline
            # tz_localize(None) es necesario para que numpy/pandas maneje datetime64 nativo
            df = pd.DataFrame(
                {
                    "id": [request.site_id] * len(history),
                    "timestamp": pd.to_datetime(
                        [b.timestamp for b in history]
                    ).tz_localize(None),
                    "target": hourly_generation(history),
                }
            )

            forecast_df = await asyncio.to_thread(
                pipeline.predict_df,
                df,
                prediction_length=steps,
                quantile_levels=[0.1, 0.5, 0.9],
                id_column="id",
                timestamp_column="timestamp",
                target="target",
            )

            points: list[ForecastPoint] = []
            num_rows = min(steps, len(forecast_df))
            predicted, lower, upper = (
                _levels_from_hourly(
                    history[-1].level, forecast_df[str(q)].iloc[:num_rows]
                )
                for q in (0.5, 0.1, 0.9)
            )

            for step in range(num_rows):
                timestamp = next_timestamp + timedelta(
                    minutes=request.interval_minutes * step
                )
                pred_level = predicted[step]
                lower_bound = lower[step]
                upper_bound = upper[step]

                # Consistencia lógica de límites
                lower_bound = min(lower_bound, pred_level)
                upper_bound = max(upper_bound, pred_level)

                # Confianza en base a dispersión del cuantil y longitud del histórico
                spread = upper_bound - lower_bound
                base_conf = 0.85 if len(history) >= 24 else 0.70
                if spread > 30.0:
                    confidence = round(max(0.40, base_conf - 0.25), 2)
                elif spread > 15.0:
                    confidence = round(max(0.55, base_conf - 0.10), 2)
                else:
                    confidence = round(base_conf, 2)

                points.append(
                    ForecastPoint(
                        timestamp=timestamp,
                        predicted_level=round(pred_level, 2),
                        confidence=confidence,
                        lower_bound=round(lower_bound, 2),
                        upper_bound=round(upper_bound, 2),
                        reason=None,
                    )
                )

                if request.stop_at_full and pred_level >= 100.0:
                    break

            return ForecastResult(
                site_id=request.site_id,
                model_key=self.model_info.key,
                generated_at=generated_at,
                points=points,
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Error en inferencia de Chronos-2 Small para sitio {request.site_id}: {exc}. "
                "Aplicando fallback operativo."
            )
            return self._fallback_forecast(
                request=request,
                context=context,
                generated_at=generated_at,
                steps=steps,
                next_timestamp=next_timestamp,
                reason="chronos_inference_fallback",
            )

    async def forecast_many(
        self,
        inputs: list[tuple[ForecastRequest, ForecastContext]],
    ) -> dict[int, ForecastResult]:
        """Forecast site series with one Chronos call; batch inputs share an interval."""
        results: dict[int, ForecastResult] = {}
        eligible: list[tuple[ForecastRequest, ForecastContext]] = []
        rows: list[dict[str, Any]] = []
        current_levels: dict[int, float] = {}

        for request, context in inputs:
            generated_at = _as_utc(context.generated_at or datetime.now(UTC))
            steps = max(1, int(request.horizon_hours * 60 / request.interval_minutes))
            next_timestamp = _ceil_to_next_interval(
                generated_at, request.interval_minutes
            )
            history = _history_with_snapshot(
                context, request.site_id, request.interval_minutes
            )
            if len(history) < 4:
                results[request.site_id] = self._fallback_forecast(
                    request,
                    context,
                    generated_at,
                    steps,
                    next_timestamp,
                    reason="insufficient_history",
                )
                continue
            eligible.append((request, context))
            current_levels[request.site_id] = history[-1].level
            rows.extend(
                {
                    "id": request.site_id,
                    "timestamp": pd.Timestamp(bucket.timestamp).tz_localize(None),
                    "target": value,
                }
                for bucket, value in zip(
                    history, hourly_generation(history), strict=True
                )
            )

        if not eligible:
            return results

        request = eligible[0][0]
        steps = max(1, int(request.horizon_hours * 60 / request.interval_minutes))
        try:
            pipeline = await asyncio.to_thread(_get_chronos_pipeline)
            forecast_df = await asyncio.to_thread(
                pipeline.predict_df,
                pd.DataFrame(rows),
                prediction_length=steps,
                quantile_levels=[0.1, 0.5, 0.9],
                id_column="id",
                timestamp_column="timestamp",
                target="target",
            )
            for request, context in eligible:
                generated_at = _as_utc(context.generated_at or datetime.now(UTC))
                next_timestamp = _ceil_to_next_interval(
                    generated_at, request.interval_minutes
                )
                site_rows = forecast_df.loc[forecast_df["id"] == request.site_id]
                if len(site_rows) < steps:
                    raise ValueError(
                        f"Missing Chronos forecast for site {request.site_id}"
                    )

                points: list[ForecastPoint] = []
                predicted_levels, lower_levels, upper_levels = (
                    _levels_from_hourly(
                        current_levels[request.site_id],
                        site_rows[str(q)].iloc[:steps],
                    )
                    for q in (0.5, 0.1, 0.9)
                )
                for step in range(steps):
                    predicted = predicted_levels[step]
                    lower = min(predicted, lower_levels[step])
                    upper = max(predicted, upper_levels[step])
                    spread = upper - lower
                    base_conf = 0.85 if len(context.history) >= 24 else 0.70
                    if spread > 30.0:
                        confidence = round(max(0.40, base_conf - 0.25), 2)
                    elif spread > 15.0:
                        confidence = round(max(0.55, base_conf - 0.10), 2)
                    else:
                        confidence = round(base_conf, 2)
                    points.append(
                        ForecastPoint(
                            timestamp=next_timestamp
                            + timedelta(minutes=request.interval_minutes * step),
                            predicted_level=round(predicted, 2),
                            confidence=confidence,
                            lower_bound=round(lower, 2),
                            upper_bound=round(upper, 2),
                        )
                    )
                    if request.stop_at_full and predicted >= 100.0:
                        break
                results[request.site_id] = ForecastResult(
                    site_id=request.site_id,
                    model_key=self.model_info.key,
                    generated_at=generated_at,
                    points=points,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Chronos batch inference failed: %s. Applying fallback.", exc
            )
            for request, context in eligible:
                generated_at = _as_utc(context.generated_at or datetime.now(UTC))
                steps = max(
                    1, int(request.horizon_hours * 60 / request.interval_minutes)
                )
                results[request.site_id] = self._fallback_forecast(
                    request,
                    context,
                    generated_at,
                    steps,
                    _ceil_to_next_interval(generated_at, request.interval_minutes),
                    reason="chronos_inference_fallback",
                )
        return results

    def _fallback_forecast(
        self,
        request: ForecastRequest,
        context: ForecastContext,
        generated_at: datetime,
        steps: int,
        next_timestamp: datetime,
        reason: str,
    ) -> ForecastResult:
        history = _history_with_snapshot(
            context, request.site_id, request.interval_minutes
        )
        current = _clamp_level(history[-1].level if history else context.current_level)
        points: list[ForecastPoint] = []

        for step in range(steps):
            timestamp = next_timestamp + timedelta(
                minutes=request.interval_minutes * step
            )
            current = _clamp_level(current + 1.0)
            points.append(
                ForecastPoint(
                    timestamp=timestamp,
                    predicted_level=round(current, 2),
                    confidence=0.35,
                    lower_bound=round(_clamp_level(current - 15.0), 2),
                    upper_bound=round(_clamp_level(current + 15.0), 2),
                    reason=reason,
                )
            )
            if request.stop_at_full and current >= 100.0:
                break

        return ForecastResult(
            site_id=request.site_id,
            model_key=self.model_info.key,
            generated_at=generated_at,
            points=points,
        )
