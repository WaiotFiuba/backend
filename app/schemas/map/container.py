from pydantic import BaseModel, ConfigDict
from typing import List
from datetime import datetime

# GET MULTIPLE


class WasteTypeMapSchema(BaseModel):
    name: str
    color: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ContainersMapTypeSchema(BaseModel):
    name: str
    waste_types: List[WasteTypeMapSchema]

    model_config = ConfigDict(from_attributes=True)


class ContainersMapOutputSchema(BaseModel):
    id: int
    site_id: str
    latitude: float
    longitude: float
    current_level: int
    available: bool
    container_type: ContainersMapTypeSchema


class ContainerCluster(BaseModel):
    latitude: float
    longitude: float
    total: int


# GET SIMPLE


class WasteTypeDetailSchema(BaseModel):
    name: str
    description: str | None
    color: str | None

    model_config = ConfigDict(from_attributes=True)


class ContainerTypeDetailSchema(BaseModel):
    name: str
    description: str | None
    height_cm: int | None
    volume_m3: float | None
    overflow_zone_cm: int | None
    waste_types: List[WasteTypeDetailSchema]

    model_config = ConfigDict(from_attributes=True)


class ContainerDetailOutputSchema(BaseModel):
    id: int
    site_id: str
    address: str | None
    description: str | None
    latitude: float
    longitude: float
    current_level: int
    available: bool
    created_at: datetime
    updated_at: datetime | None
    container_type: ContainerTypeDetailSchema

    model_config = ConfigDict(
        from_attributes=True,
    )
