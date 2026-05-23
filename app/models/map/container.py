from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.map.container_type import ContainerType

from geoalchemy2 import Geometry
from sqlalchemy import (
    ForeignKey,
    func,
    TIMESTAMP,
    Boolean,
    String,
    Text,
    BigInteger,
    DOUBLE_PRECISION,
    Integer,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.map_database import MapBase


class Container(MapBase):
    __tablename__ = "containers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(String, unique=True)
    geom: Mapped[Geometry] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=False
    )

    serie_id: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    latitude: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    longitude: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    current_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    last_pickup: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_reading: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # client_id: Mapped[int | None] = mapped_column(BIGINT)
    # site_id: Mapped[int | None] = mapped_column(BIGINT)
    site_name: Mapped[str | None] = mapped_column(String, nullable=True)
    device_imei: Mapped[str | None] = mapped_column(String, nullable=True)
    container_type_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("container_types.id")
    )
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
    container_type: Mapped[ContainerType] = relationship(back_populates="containers")
