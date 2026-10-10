import pytest

from app.core.config import Settings
from app.core.thresholds import FULL_LEVEL, LevelThresholds, get_level_thresholds


def test_default_thresholds():
    thresholds = get_level_thresholds()

    assert thresholds == LevelThresholds(normal=40, high=70, critical=80)
    assert thresholds.full == FULL_LEVEL == 100


def test_thresholds_can_be_overridden_from_settings():
    settings = Settings(
        level_threshold_normal=30,
        level_threshold_high=60,
        level_threshold_critical=90,
    )

    thresholds = LevelThresholds.from_settings(settings)

    assert (thresholds.normal, thresholds.high, thresholds.critical) == (30, 60, 90)


@pytest.mark.parametrize(
    ("normal", "high", "critical"),
    [
        (0, 70, 80),  # normal debe ser positivo
        (70, 70, 80),  # normal < high
        (40, 80, 80),  # high < critical
        (40, 70, 101),  # critical <= full
    ],
)
def test_invalid_thresholds_are_rejected(normal, high, critical):
    with pytest.raises(ValueError):
        LevelThresholds(normal=normal, high=high, critical=critical)
