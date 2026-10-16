from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator


def iter_timestamps(
    start: datetime,
    periods: int,
    frequency_minutes: int,
) -> Iterator[datetime]:
    step = timedelta(minutes=frequency_minutes)
    if periods > 0:
        for index in range(periods):
            yield start + index * step
    else:
        index = 0
        while True:
            yield start + index * step
            index += 1
