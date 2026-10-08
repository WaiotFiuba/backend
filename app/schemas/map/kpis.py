from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class NetworkKpiSummaryResponse(BaseModel):
    """Resumen de métricas y KPIs operativas a nivel sitio para toda la red."""

    total_sites: int = Field(
        ..., description="Total de sitios georreferenciados activos en la red"
    )
    total_containers: int = Field(
        ..., description="Total de contenedores físicos monitoreados en la red"
    )
    mean_fill_level: float = Field(
        ..., description="Promedio de nivel de llenado entre sitios (0-100%)"
    )
    std_fill_level: float = Field(
        ...,
        description="Desvío estándar del nivel de llenado entre sitios (dispersión σ)",
    )
    critical_count: int = Field(
        ..., description="Cantidad de sitios en estado crítico (saturación >= 85%)"
    )
    high_count: int = Field(
        ..., description="Cantidad de sitios en estado alto (70% <= saturación < 85%)"
    )
    normal_count: int = Field(
        ...,
        description="Cantidad de sitios en estado normal / óptimo (40% <= saturación < 70%)",
    )
    low_count: int = Field(
        ..., description="Cantidad de sitios en estado bajo (0% < saturación < 40%)"
    )
    idle_count: int = Field(
        ..., description="Cantidad de sitios ociosos o vacíos (saturación == 0%)"
    )
    is_whatif: bool = Field(
        False,
        description="Indica si las métricas corresponden a la red What-If simulada",
    )
    plan_id: int | None = Field(
        None, description="Identificador del plan de redistribución (si es red What-If)"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Marca temporal UTC del cálculo de las métricas",
    )

    model_config = ConfigDict(from_attributes=True)


class WhatIfKpiComparisonResponse(BaseModel):
    """Comparativa ejecutiva instantánea entre la Red Base Real y la Red Redistribuida What-If."""

    plan_id: int = Field(..., description="ID del plan de redistribución evaluado")
    plan_status: str = Field(
        ..., description="Estado del plan: draft, active_whatif, completed"
    )
    baseline: NetworkKpiSummaryResponse = Field(
        ..., description="Métricas de la red física base real"
    )
    whatif: NetworkKpiSummaryResponse = Field(
        ..., description="Métricas de la red redistribuida virtualmente bajo el plan"
    )
    std_reduction_pct: float = Field(
        ..., description="Porcentaje relativo de reducción de la dispersión de llenado"
    )
    critical_sites_reduction: int = Field(
        ..., description="Cantidad neta de sitios críticos neutralizados por el plan"
    )
    is_improved: bool = Field(
        ..., description="True si la dispersión se redujo respecto de la línea base"
    )

    model_config = ConfigDict(from_attributes=True)
