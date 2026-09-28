from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OptimizationConfig(BaseModel):
    """Parámetros configurables para la generación de un plan de redistribución."""

    window_days: int | None = Field(
        None,
        description="Días de historial a analizar. None = usar todo el disponible.",
    )
    max_distance_km: float = Field(
        2.0,
        description="Distancia máxima en km para mover un contenedor entre sitios.",
    )
    min_utilization_donor: float = Field(
        0.35,
        description="Score máximo de utilización para que un sitio sea donante.",
    )
    min_utilization_receiver: float = Field(
        0.65,
        description="Score mínimo de utilización para que un sitio sea receptor.",
    )
    target_utilization: float = Field(
        0.50,
        description="Nivel de utilización objetivo para todos los sitios.",
    )
    algorithm: str = Field(
        "greedy",
        description="Algoritmo de optimización: 'lp' (programación lineal) o 'greedy'.",
    )
    apply_capacity_constraints: bool = Field(
        True,
        description="Si True, limita la asignación de contenedores a la capacidad física máxima de la cuadra (max_containers). Si False, opera solo por estadística.",
    )

    model_config = ConfigDict(from_attributes=True)


class SiteUtilizationMetric(BaseModel):
    """Métricas de utilización calculadas para un sitio."""

    site_id: int
    site_name: str
    latitude: float
    longitude: float
    container_count: int
    max_containers: int | None = None
    puede_ingresar: bool = True
    waste_type_id: int | None = None
    waste_type_name: str | None = None
    container_type_id: int | None = None
    container_type_name: str | None = None
    avg_fill_level: float = Field(description="Promedio de nivel de llenado (0-100)")
    peak_fill_rate: float = Field(
        description="Porcentaje de lecturas con nivel > 80% (0-1)"
    )
    overflow_frequency: int = Field(
        description="Cantidad de veces que se alcanzó 100% antes de recolección"
    )
    time_to_full_hours: float | None = Field(
        None,
        description="Horas promedio desde recolección hasta alcanzar 80%",
    )
    utilization_score: float = Field(description="Score compuesto de utilización (0-1)")
    category: str = Field(
        description="Categoría: 'critical', 'high', 'normal', 'low', 'idle'"
    )

    model_config = ConfigDict(from_attributes=True)


class RedistributionMove(BaseModel):
    """Un movimiento individual en el plan de redistribución."""

    container_id: int
    from_site_id: int
    from_site_name: str
    to_site_id: int
    to_site_name: str
    distance_km: float
    from_lat: float | None = None
    from_lng: float | None = None
    to_lat: float | None = None
    to_lng: float | None = None

    model_config = ConfigDict(from_attributes=True)


class RedistributionPlanRead(BaseModel):
    """Plan completo de redistribución con su lista de movimientos."""

    id: int
    status: str = Field(
        description="Estado del plan: 'draft', 'active_whatif', 'completed'"
    )
    config: OptimizationConfig
    moves: list[RedistributionMove]
    total_containers_moved: int
    sites_emptied: int
    sites_receiving: int
    sites_donating: int
    avg_distance_km: float
    expected_improvement: float = Field(
        description="Reducción esperada en el score promedio de sitios críticos"
    )
    created_at: datetime
    simulation_id: int | None = None
    original_mean_fill: float = 0.0
    original_std_fill: float = 0.0
    optimized_mean_fill: float = 0.0
    optimized_std_fill: float = 0.0

    model_config = ConfigDict(from_attributes=True)


class WhatIfSiteComparison(BaseModel):
    """Comparación del nivel real vs optimizado para un sitio."""

    site_id: int
    site_name: str
    latitude: float
    longitude: float
    real_avg_level: float
    optimized_avg_level: float
    real_container_count: int
    optimized_container_count: int
    improvement_pct: float = Field(
        description="Diferencia porcentual (negativo = mejoró en optimizado)"
    )

    model_config = ConfigDict(from_attributes=True)


class WhatIfComparisonResponse(BaseModel):
    """Respuesta completa de comparación what-if."""

    plan_id: int
    plan_status: str
    real_global_avg: float
    optimized_global_avg: float
    sites: list[WhatIfSiteComparison]
    total_sites_improved: int
    total_sites_worsened: int

    model_config = ConfigDict(from_attributes=True)


class OptimizationMetricsResponse(BaseModel):
    """Respuesta con las métricas de utilización de todos los sitios."""

    total_sites: int
    total_containers: int = 0
    mean_fill_level: float = 0.0
    std_fill_level: float = 0.0
    critical_count: int
    high_count: int
    normal_count: int
    low_count: int
    idle_count: int
    window_start: datetime | None = None
    window_end: datetime | None = None
    sites: list[SiteUtilizationMetric]

    model_config = ConfigDict(from_attributes=True)
