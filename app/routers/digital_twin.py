from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.map_database import get_map_db
from app.schemas.digital_twin import (
    TelemetryBatchIngestPayload,
    TelemetryIngestPayload,
    TelemetryIngestResult,
)
from app.services.digital_twin_ingest_service import ingest_telemetry_batch

router = APIRouter(prefix="/digital-twin", tags=["digital-twin"])

MapDbDep = Annotated[AsyncSession, Depends(get_map_db)]


@router.post("/telemetry", response_model=TelemetryIngestResult)
async def ingest_telemetry(
    payload: TelemetryIngestPayload,
    db: MapDbDep,
) -> TelemetryIngestResult:
    return await ingest_telemetry_batch(db, [payload])


@router.post("/telemetry/batch", response_model=TelemetryIngestResult)
async def ingest_telemetry_batch_endpoint(
    payload: TelemetryBatchIngestPayload,
    db: MapDbDep,
) -> TelemetryIngestResult:
    return await ingest_telemetry_batch(db, payload.measurements)
