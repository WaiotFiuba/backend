from __future__ import annotations

import functools
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from simulator.domain.entities import SimulationSession


@dataclass(frozen=True)
class ControlSnapshot:
    """Controles en vivo de una sesion, tal como los reporta el backend."""

    speedup: float
    global_current: float
    global_target: float
    zones: tuple[tuple[str, float, float], ...]  # (barrio, actual, objetivo)

    @classmethod
    def from_session(cls, session: SimulationSession) -> ControlSnapshot:
        return cls(
            speedup=session.speedup,
            global_current=session.global_demand_current,
            global_target=session.global_demand_target,
            zones=tuple(
                sorted(
                    (zone.neighborhood, zone.multiplier_current, zone.multiplier_target)
                    for zone in session.zone_overrides
                )
            ),
        )

    def targets(self) -> tuple[float, float, tuple[tuple[str, float], ...]]:
        """Valores pedidos por el usuario, sin el avance de las transiciones."""
        return (
            self.speedup,
            self.global_target,
            tuple((name, target) for name, _current, target in self.zones),
        )

    def zone_multiplier_fn(self) -> Callable[[str], float] | None:
        """Funcion zona -> multiplicador actual, o None si ninguna zona lo modifica."""
        if not any(current != 1.0 for _, current, _ in self.zones):
            return None
        multipliers = {
            _norm_zone_name(name): current for name, current, _target in self.zones
        }
        multipliers.update({name: current for name, current, _target in self.zones})

        def zone_fn(zone: str) -> float:
            return multipliers.get(zone, multipliers.get(_norm_zone_name(zone), 1.0))

        return zone_fn


@functools.lru_cache(maxsize=256)
def _norm_zone_name(s: str | None) -> str:
    if not s:
        return ""
    n = unicodedata.normalize("NFKD", str(s).strip().casefold())
    return "".join(c for c in n if not unicodedata.combining(c))
