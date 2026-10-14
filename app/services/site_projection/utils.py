from datetime import UTC, datetime, timedelta


def _ceil_to_next_interval(timestamp: datetime, interval_minutes: int) -> datetime:
    # Las predicciones salen alineadas a buckets fijos: 08:00, 09:00, etc.
    timestamp = _as_utc(timestamp).replace(second=0, microsecond=0)
    minute_of_day = timestamp.hour * 60 + timestamp.minute
    remainder = minute_of_day % interval_minutes
    if remainder == 0:
        return timestamp + timedelta(minutes=interval_minutes)
    return timestamp + timedelta(minutes=interval_minutes - remainder)


def _clamp_level(value: float) -> float:
    return min(100.0, max(0.0, value))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
