"""Reloj de referencia de los datos del gemelo digital.

Proyección, optimización y evaluación miden el tiempo respecto de la última
lectura registrada y no del reloj del servidor. Así funcionan igual con
sensores reales (la última lectura es aproximadamente "ahora") y con el
simulador, que puede correr adelantado o empezar en otra fecha.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.map.data_level import DataLevel


async def get_reference_time(db: AsyncSession) -> datetime:
    """Fecha de la última lectura de nivel, o la hora actual si no hay lecturas."""
    latest = await db.scalar(
        select(func.max(DataLevel.reading_date)).where(
            DataLevel.container_current_level.is_not(None)
        )
    )
    if latest is None:
        return datetime.now(UTC)
    if latest.tzinfo is None:
        return latest.replace(tzinfo=UTC)
    return latest.astimezone(UTC)
