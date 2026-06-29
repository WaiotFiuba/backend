from __future__ import annotations

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.map_database import MapBase


class CabaContainerSpatialMetadata(MapBase):
    """
    Tabla espejo que añade capacidades GIS y datos territoriales de CABA
    a los contenedores globales de Waiot sin ensuciar su modelo core.
    """

    __tablename__ = "caba_container_spatial_metadata"
    __table_args__ = (
        Index(
            "idx_caba_container_spatial_metadata_geom",
            "geom",
            postgresql_using="gist",
        ),
    )

    barrio_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("barrios.id", ondelete="SET NULL")
    )
    comuna_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("comunas.id", ondelete="SET NULL")
    )
    manzana_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("manzanas.id", ondelete="SET NULL")
    )
    container_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("containers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    geom: Mapped[Geometry] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )

    container = relationship("Container", back_populates="spatial_metadata")
    comuna = relationship("Comuna", back_populates="containers_metadata")
    barrio = relationship("Barrio", back_populates="containers_metadata")
    manzana = relationship("Manzana", back_populates="containers_metadata")


class Comuna(MapBase):
    __tablename__ = "comunas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comuna: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    barrios: Mapped[str | None] = mapped_column(Text, nullable=True)
    perimetro: Mapped[float | None] = mapped_column(Float, nullable=True)
    area: Mapped[float | None] = mapped_column(Float, nullable=True)
    geom: Mapped[Geometry] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=False,
    )

    barrios_items = relationship("Barrio", back_populates="comuna")
    containers_metadata = relationship(
        "CabaContainerSpatialMetadata", back_populates="comuna"
    )


class Barrio(MapBase):
    __tablename__ = "barrios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    comuna_id: Mapped[int | None] = mapped_column(
        "comuna",
        Integer,
        ForeignKey("comunas.id", ondelete="SET NULL"),
        nullable=True,
    )
    perimetro: Mapped[float | None] = mapped_column("perimetro_", Float, nullable=True)
    area_metro: Mapped[float | None] = mapped_column(Float, nullable=True)
    geom: Mapped[Geometry] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=False,
    )

    comuna = relationship("Comuna", back_populates="barrios_items")
    containers_metadata = relationship(
        "CabaContainerSpatialMetadata", back_populates="barrio"
    )
    demographic = relationship(
        "NeighborhoodDemographic", back_populates="neighborhood", uselist=False
    )


class Manzana(MapBase):
    __tablename__ = "manzanas"
    __table_args__ = (Index("idx_manzanas_geom", "geom", postgresql_using="gist"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sm: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    geom: Mapped[Geometry] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=False,
    )

    containers_metadata = relationship(
        "CabaContainerSpatialMetadata", back_populates="manzana"
    )


__all__ = [
    "CabaContainerSpatialMetadata",
    "Barrio",
    "Comuna",
    "Manzana",
]
