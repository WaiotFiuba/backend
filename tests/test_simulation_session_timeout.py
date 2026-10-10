from datetime import UTC, datetime, timedelta

from app.core.simulation_status import SimulationStatus
from app.services.simulation_session_service import pending_expiry_reason

CREATED = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
CLAIM = timedelta(seconds=60)
STARTUP = timedelta(minutes=10)


def _reason(status, claimed_at, now):
    return pending_expiry_reason(status, CREATED, claimed_at, now, CLAIM, STARTUP)


def test_unclaimed_pending_expires_after_claim_timeout():
    assert (
        _reason(SimulationStatus.PENDING, None, CREATED + timedelta(seconds=60)) is None
    )
    assert (
        _reason(SimulationStatus.PENDING, None, CREATED + timedelta(seconds=61))
        == "Ningun simulador tomo la simulacion."
    )


def test_claimed_pending_gets_startup_timeout():
    claimed = CREATED + timedelta(seconds=5)

    assert (
        _reason(SimulationStatus.PENDING, claimed, claimed + timedelta(minutes=9))
        is None
    )
    assert (
        _reason(SimulationStatus.PENDING, claimed, claimed + timedelta(minutes=11))
        == "El simulador no termino de preparar la simulacion."
    )


def test_only_pending_sessions_expire():
    later = CREATED + timedelta(days=1)

    assert _reason(SimulationStatus.RUNNING, None, later) is None
    assert _reason(SimulationStatus.PAUSED, CREATED, later) is None


def test_naive_created_at_is_treated_as_utc():
    reason = pending_expiry_reason(
        SimulationStatus.PENDING,
        datetime(2026, 1, 1, 12, 0),
        None,
        datetime(2026, 1, 1, 12, 5, tzinfo=UTC),
        CLAIM,
        STARTUP,
    )
    assert reason is not None
