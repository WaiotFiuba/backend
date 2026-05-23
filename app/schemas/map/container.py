from pydantic import BaseModel, ConfigDict
from typing import List


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
