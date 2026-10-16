from enum import StrEnum


class SimulationStatus(StrEnum):
    """Estados de una sesion de simulacion (columna simulation_sessions.status)."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"


# Sesiones que todavia no terminaron: las que el worker puede ejecutar o detener.
ACTIVE_STATUSES = (
    SimulationStatus.PENDING,
    SimulationStatus.RUNNING,
    SimulationStatus.PAUSED,
    SimulationStatus.STOPPING,
)
