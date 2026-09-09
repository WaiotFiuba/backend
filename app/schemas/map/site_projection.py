from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SiteProjectionModel(BaseModel):
    key: str = Field(description="Identificador estable usado como model_key.")
    name: str = Field(description="Nombre legible del modelo.")
    description: str = Field(description="Resumen corto de como proyecta niveles.")
    status: str = Field(description="Estado de disponibilidad del modelo.")
    requires_training: bool = Field(
        description="Indica si el modelo necesita entrenamiento previo."
    )
    supports_site_features: bool = Field(
        description="Indica si puede consumir features genericas del sitio."
    )

    model_config = ConfigDict(from_attributes=True)


class SiteProjectionModelsResponse(BaseModel):
    default_model_key: str
    models: list[SiteProjectionModel]
