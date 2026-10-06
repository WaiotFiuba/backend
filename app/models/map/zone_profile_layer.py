from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, TIMESTAMP, BigInteger, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.map_database import MapBase

# Se guarda solo la ultima capa publicada: siempre la fila con este id.
LATEST_LAYER_ID = 1


class ZoneProfileLayer(MapBase):
    """Ultima capa de perfiles de zona publicada por el simulador.

    El simulador la arma al iniciar cada sesion (poligonos de radios censales +
    perfil de demanda segun su zone_profiles.yaml) y el backend solo la guarda
    y la sirve al mapa del front.
    """

    __tablename__ = "zone_profile_layers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    simulation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    geojson: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
