from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DOUBLE_PRECISION,
    ForeignKey,
    Integer,
    TIMESTAMP,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.map_database import MapBase


class NeighborhoodDemographic(MapBase):
    __tablename__ = "neighborhood_demographics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    neighborhood_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("barrios.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    population: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    area_km2: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    density_per_km2: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    density_factor: Mapped[float] = mapped_column(
        DOUBLE_PRECISION,
        nullable=False,
        default=1.0,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    neighborhood = relationship("Barrio", back_populates="demographic")
