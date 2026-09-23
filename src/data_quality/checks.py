from __future__ import annotations

import pandas as pd


def validate_dataframe(df: pd.DataFrame) -> dict[str, int | str]:
    duplicate_rows = 0
    invalid_timestamps = 0
    negative_demand = 0
    missing_values = 0

    if df.empty:
        return {
            "duplicate_rows": 0,
            "invalid_timestamps": 0,
            "negative_demand": 0,
            "missing_values": 0,
            "status": "FAIL",
        }

    duplicate_rows = int(df.duplicated(subset=list(df.columns)).sum())
    missing_values = int(df.isna().sum().sum())

    if "timestamp" in df.columns:
        try:
            parsed = pd.to_datetime(df["timestamp"], errors='coerce')
            invalid_timestamps = int(parsed.isna().sum())
        except (TypeError, ValueError):
            invalid_timestamps = len(df)

    if "demand" in df.columns:
        negative_demand = int((df["demand"] < 0).sum())

    issue_count = duplicate_rows + invalid_timestamps + negative_demand
    status = "PASS"
    if duplicate_rows or invalid_timestamps or negative_demand or missing_values:
        status = "WARN" if issue_count < 10 else "FAIL"

    return {
        "duplicate_rows": duplicate_rows,
        "invalid_timestamps": invalid_timestamps,
        "negative_demand": negative_demand,
        "missing_values": missing_values,
        "status": status,
    }
