from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OptimizationConfig(BaseModel):
    """Parámetros configurables para la generación de un plan de redistribución."""

    window_days: int | None = Field(
        None,
        ge=1,
        le=365,
        description="Días de historial a analizar. None = usar todo el disponible.",
    )
    max_distance_km: float = Field(
        2.0,
        gt=0,
        le=20,
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
    algorithm: Literal["lp", "greedy", "overload"] = Field(
        "greedy",
        description=(
            "Algoritmo de optimización: 'lp' (programación lineal) o 'greedy' del "
            "modelo por score, u 'overload' (modelo de sobrecarga: minimiza las "
            "horas en zona crítica y desbordado)."
        ),
    )
    apply_capacity_constraints: bool = Field(
        True,
        description="Si True, limita la asignación de contenedores a la capacidad física máxima de la cuadra (max_containers). Si False, opera solo por estadística.",
    )

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _thresholds_in_order(self) -> OptimizationConfig:
        # Con donante < objetivo < receptor ningún sitio es donante y receptor a
        # la vez, así que el plan no arma relevos (A -> B -> C).
        if not (
            self.min_utilization_donor
            < self.target_utilization
            < self.min_utilization_receiver
        ):
            raise ValueError(
                "Los umbrales tienen que cumplir donante < objetivo < receptor."
            )
        return self


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
    donor_fill_before: float | None = Field(
        None, description="Nivel promedio de llenado del donante antes del movimiento"
    )
    donor_fill_after: float | None = Field(
        None,
        description="Nivel promedio de llenado del donante tras ceder el contenedor",
    )
    receiver_fill_before: float | None = Field(
        None, description="Nivel promedio de llenado del receptor antes del movimiento"
    )
    receiver_fill_after: float | None = Field(
        None,
        description="Nivel promedio de llenado del receptor tras recibir el contenedor",
    )
    net_decongestion_pct: float | None = Field(
        None,
        description="Balance neto de descongestión entre la suma de los 2 sitios (ΔReceptor + ΔDonante)",
    )
    overload_reduction: float | None = Field(
        None,
        description=(
            "Solo modelo 'overload': cuánto baja la sobrecarga total con este "
            "movimiento (horas en crítico + peso x horas desbordado, en la ventana)"
        ),
    )

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
    # Usuario que generó el plan (None en planes anteriores a registrarlo).
    created_by: int | None = None
    original_mean_fill: float = 0.0
    original_std_fill: float = 0.0
    optimized_mean_fill: float = 0.0
    optimized_std_fill: float = 0.0
    # Solo modelo 'overload': horas en la ventana de historia, sumadas sobre los
    # sitios candidatos, con la distribución actual y con la del plan.
    critical_hours_before: float | None = None
    critical_hours_after: float | None = None
    overflow_hours_before: float | None = None
    overflow_hours_after: float | None = None

    model_config = ConfigDict(from_attributes=True)


class WhatIfMeasure(BaseModel):
    """Un valor de la red real (sin el plan) y de la red what-if (con el plan)."""

    real: float
    with_plan: float


class WhatIfComparisonResponse(BaseModel):
    """Cómo le va a la red con el plan, comparada con la red real."""

    plan_id: int
    plan_status: str
    dispersion: WhatIfMeasure | None = Field(
        None,
        description="Desvío del nivel entre sitios, antes y después, estimado al generar el plan.",
    )
    observed_hours: float = Field(
        description="Horas medidas desde que se activó el plan (0: todavía sin datos)."
    )
    avg_critical_sites: WhatIfMeasure | None = Field(
        None, description="Sitios críticos en promedio desde que se activó el plan."
    )
    avg_full_containers: WhatIfMeasure | None = Field(
        None,
        description="Contenedores al 100 % en promedio desde que se activó el plan.",
    )
    full_container_hours: WhatIfMeasure | None = Field(
        None,
        description="Horas de contenedores al 100 % desde la activación. Solo con el plan activo.",
    )
    critical_sites_now: WhatIfMeasure | None = Field(
        None, description="Sitios críticos en este momento. Solo con el plan activo."
    )
    full_containers_now: WhatIfMeasure | None = Field(
        None,
        description="Contenedores al 100 % en este momento. Solo con el plan activo.",
    )

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
