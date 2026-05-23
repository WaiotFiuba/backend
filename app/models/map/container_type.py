from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.map.container import Container
    from app.models.map.waste_type import WasteType

from sqlalchemy import (
    BigInteger,
    Boolean,
    DOUBLE_PRECISION,
    Integer,
    TIMESTAMP,
    Text,
    func,
    Table,
    Column,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.map_database import MapBase


# association table between container types and waste types
container_type_waste_types = Table(
    "container_type_waste_types",
    MapBase.metadata,
    Column(
        "container_type_id",
        BigInteger,
        ForeignKey("container_types.id"),
        primary_key=True,
    ),
    Column("waste_type_id", BigInteger, ForeignKey("waste_types.id"), primary_key=True),
)


class ContainerType(MapBase):
    __tablename__ = "container_types"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    height_cm: Mapped[int | None] = mapped_column(Integer)
    volume_m3: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    overflow_zone_cm: Mapped[int | None] = mapped_column(Integer)
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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
    containers: Mapped[list[Container]] = relationship(back_populates="container_type")
    waste_types: Mapped[list[WasteType]] = relationship(
        secondary=container_type_waste_types, back_populates="container_types"
    )
