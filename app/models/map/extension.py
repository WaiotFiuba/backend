from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, ForeignKey, String, Float
from geoalchemy2 import Geometry
from app.models.map.base import MapBase


class CabaContainerSpatialMetadata(MapBase):
    """
    Tabla espejo que añade capacidades GIS y datos territoriales de CABA
    a los contenedores globales de Waiot sin ensuciar su modelo core.
    """

    __tablename__ = "caba_container_spatial_metadata"

    # Primary Key unida por relación 1:1 al ID genérico del contenedor de Waiot
    container_id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Llaves relacionales indexadas nativamente hacia la infraestructura de la ciudad
    barrio_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("barrios.id", ondelete="SET NULL")
    )
    block_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("blocks.id", ondelete="SET NULL")
    )

    # La columna geométrica nativa de PostGIS para analítica e índices espaciales
    geom: Mapped[Geometry] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=False
    )

    # Relaciones del ORM para hacer Joins limpios en tus consultas de FastAPI
    barrio = relationship("Barrio", back_populates="containers_metadata")
    block = relationship("Block", back_populates="containers_metadata")


class Barrio(MapBase):
    __tablename__ = "barrios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100))
    comuna_id: Mapped[int] = mapped_column(Integer)
    perimetro: Mapped[float] = mapped_column(Float)
    area_metro: Mapped[float] = mapped_column(Float)
    geom: Mapped[Geometry] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326), nullable=False
    )

    containers_metadata = relationship(
        "CabaContainerSpatialMetadata", back_populates="barrio"
    )


class Block(MapBase):
    __tablename__ = "blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(50))
    sm: Mapped[str] = mapped_column(String(50), unique=True)
    barrio_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("barrios.id"))
    geom: Mapped[Geometry] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326), nullable=False
    )

    containers_metadata = relationship(
        "CabaContainerSpatialMetadata", back_populates="block"
    )
