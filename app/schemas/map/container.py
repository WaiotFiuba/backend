from pydantic import BaseModel, ConfigDict
from typing import List
from datetime import datetime


class WasteTypeMapSchema(BaseModel):
    name: str
    color: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ContainersMapTypeSchema(BaseModel):
    id: int | None = None
    name: str
    height_cm: int | None = None
    volume_m3: float | None = None
    overflow_zone_cm: int | None = None
    waste_types: List[WasteTypeMapSchema]

    model_config = ConfigDict(from_attributes=True)


class ContainersMapOutputSchema(BaseModel):
    id: int
    site_id: str
    site_name: str | None = None
    device_imei: str | None = None
    latitude: float
    longitude: float
    current_level: int
    available: bool
    container_type: ContainersMapTypeSchema
    zone: str | None = None
    density_factor: float = 1.0


class ContainerCluster(BaseModel):
    latitude: float
    longitude: float
    total: int


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


class ContainerCreateSchema(BaseModel):
    site_id: str
    site_name: str | None = None
    latitude: float
    longitude: float
    container_type_id: int
    address: str | None = None
    description: str | None = None



