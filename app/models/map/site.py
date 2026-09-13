from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.map.container import Container
    from app.models.map.waste_type import WasteType

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    DOUBLE_PRECISION,
    ForeignKey,
    Integer,
    String,
    TIMESTAMP,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.map_database import MapBase


class Site(MapBase):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    longitude: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    geom: Mapped[Geometry] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=False
    )

    load_side_category: Mapped[str | None] = mapped_column(String, nullable=True)
    waste_type_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("waste_types.id"), nullable=True
    )
    client_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    containers: Mapped[list["Container"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )
    waste_type: Mapped["WasteType | None"] = relationship()
