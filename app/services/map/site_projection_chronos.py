from __future__ import annotations

import asyncio
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from app.services.map.site_projection_models import (
    ForecastContext,
    ForecastModelInfo,
    ForecastPoint,
    ForecastRequest,
    ForecastResult,
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
        "Pronóstico probabilístico con intervalos de cuantiles (10%, 50%, 90%)."
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


class Chronos2SmallForecaster:
    """Forecaster zero-shot basado en Amazon Chronos-2 Small."""

    model_info = CHRONOS_2_SMALL_MODEL

    async def forecast(
        self,
        request: ForecastRequest,
        context: ForecastContext,
    ) -> ForecastResult:
        generated_at = _as_utc(context.generated_at or datetime.now(UTC))
        history = sorted(context.history, key=lambda bucket: bucket.timestamp)
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
            pipeline = _get_chronos_pipeline()

            # Normalizar serie temporal a DataFrame para Chronos2Pipeline
            # tz_localize(None) es necesario para que numpy/pandas maneje datetime64 nativo
            df = pd.DataFrame(
                {
                    "id": [request.site_id] * len(history),
                    "timestamp": pd.to_datetime(
                        [b.timestamp for b in history]
                    ).tz_localize(None),
                    "target": [float(b.level) for b in history],
                }
            )

            # Ejecutar inferencia en un hilo separado para no bloquear el event loop de FastAPI
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
            num_rows = len(forecast_df)

            for step in range(min(steps, num_rows)):
                timestamp = next_timestamp + timedelta(
                    minutes=request.interval_minutes * step
                )
                row = forecast_df.iloc[step]

                pred_raw = float(row["0.5"])
                lower_raw = float(row["0.1"])
                upper_raw = float(row["0.9"])

                # Acotar niveles al dominio real [0, 100]%
                pred_level = _clamp_level(pred_raw)
                lower_bound = _clamp_level(lower_raw)
                upper_bound = _clamp_level(upper_raw)

                # Consistencia lógica de límites
                if lower_bound > pred_level:
                    lower_bound = pred_level
                if upper_bound < pred_level:
                    upper_bound = pred_level

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

        except Exception as exc:
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

    def _fallback_forecast(
        self,
        request: ForecastRequest,
        context: ForecastContext,
        generated_at: datetime,
        steps: int,
        next_timestamp: datetime,
        reason: str,
    ) -> ForecastResult:
        current = _clamp_level(
            context.current_level if not context.history else context.history[-1].level
        )
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
