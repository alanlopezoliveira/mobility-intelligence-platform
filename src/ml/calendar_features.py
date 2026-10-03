from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def local_calendar_features(timestamp: datetime, timezone_name: str) -> dict[str, int | float | bool]:
    """Compute wall-clock calendar values from an aware UTC instant.

    Missing spring-forward wall times are never synthesized; callers emit one
    feature row per observed UTC instant. ``dst_fold`` and the UTC offset retain
    the distinction between the two fall-back occurrences of the same hour.
    """
    if timestamp.tzinfo is None:
        raise ValueError('calendar timestamp must be timezone-aware')
    local = timestamp.astimezone(ZoneInfo(timezone_name))
    hour = local.hour + local.minute / 60
    weekday = local.weekday()
    month_angle = 2 * math.pi * (local.month - 1) / 12
    hour_angle = 2 * math.pi * hour / 24
    weekday_angle = 2 * math.pi * weekday / 7
    return {
        'local_hour': local.hour,
        'hour_sin': math.sin(hour_angle),
        'hour_cos': math.cos(hour_angle),
        'local_weekday': weekday,
        'weekday_sin': math.sin(weekday_angle),
        'weekday_cos': math.cos(weekday_angle),
        'day_of_month': local.day,
        'month': local.month,
        'month_sin': math.sin(month_angle),
        'month_cos': math.cos(month_angle),
        'year': local.year,
        'weekend': weekday >= 5,
        'utc_offset_minutes': int((local.utcoffset() or timedelta()).total_seconds() / 60),
        'dst_fold': local.fold,
    }
