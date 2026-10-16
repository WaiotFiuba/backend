from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.map.container import Container

from app.core.map_database import MapBase


class Neighborhood(MapBase):
    __tablename__ = "neighborhoods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    geom: Mapped[Geometry] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=True), nullable=False
    )

    containers: Mapped[list["Container"]] = relationship(back_populates="neighborhood")
