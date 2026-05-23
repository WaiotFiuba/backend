from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.map.container_type import ContainerType

from sqlalchemy import BigInteger, TIMESTAMP, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.map_database import MapBase
from app.models.map.container_type import container_type_waste_types


class WasteType(MapBase):
    __tablename__ = "waste_types"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    container_types: Mapped[list[ContainerType]] = relationship(
        secondary=container_type_waste_types,
        back_populates="waste_types",
    )
