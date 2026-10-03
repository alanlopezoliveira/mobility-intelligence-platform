"""Exercise service-state denominators and calendar-confounded rain means."""

import pandas as pd
import pytest
from scripts.analyze_project_evidence import inventory_diagnostics, weather_diagnostics


def test_inventory_keeps_unknown_flags_and_excludes_conflicts():
    raw = pd.DataFrame(
        [
            ["1", "2020-01-01 01:00", 0, 10, 1, 0, "A"],
            ["1", "2020-01-01 08:00", 0, 10, 0, 0, "A"],
            ["1", "2020-01-01 09:00", 10, 10, None, None, "A"],
            ["1", "2020-01-01 10:00", 2, 10, 1, 0, "A"],
            ["1", "2020-01-01 10:00", 2, 10, 1, 1, "A"],
            ["1", "2020-01-01 11:00", 20, 10, 1, 0, "A"],
        ],
        columns=["station", "time", "bikes", "capacity", "active", "unavailable", "name"],
    )
    result = inventory_diagnostics(raw)
    assert result["samples"] == 3
    assert result["empty"] == 2
    assert result["full"] == 1
    assert result["conflicting_keys_excluded"] == 1
    unknown = next(g for g in result["services"] if g["service"] == "Unknown flags")
    assert unknown["samples"] == unknown["full"] == 1
    assert result["stations"][0]["empty_flagged"] == 1
    assert result["stations"][0]["empty_night"] == 1
    assert sum(g["samples"] for g in result["cross"]) == result["samples"]


def test_hour_matching_can_reverse_raw_rain_association():
    rows = []
    for day in range(1, 7):
        for hour in [1, 12]:
            wet = day <= 2 and hour == 12
            rows.append(
                {
                    "time": pd.Timestamp(f"2022-06-{day:02d} {hour:02d}:00", tz="Europe/Madrid"),
                    "month": 6,
                    "hour": hour,
                    "weekend": False,
                    "rain": 0.2 if wet else 0,
                    "temperature_2m": 25,
                    "departures": 80 if wet else 100 if hour == 12 else 10,
                }
            )
    result = weather_diagnostics(pd.DataFrame(rows))[0]
    assert result["raw_change_pct"] > 0
    assert result["adjusted_change_pct"] == pytest.approx(-20)
    assert result["rainy_days"] == result["rainy_hours"] == 2
    assert result["leave_one_day_out_min"] == pytest.approx(-20)
    assert result["thresholds"][1]["adjusted_change_pct"] is None
    assert result["thresholds"][1]["hours"] == 0
