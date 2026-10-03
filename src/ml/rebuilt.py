"""Reproducible count forecasts with explicit issue times and saved boosters."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


def scores(y, prediction):
    """Score the exact nonnegative floating-point predictions served to the UI."""
    return {
        "mae": float(mean_absolute_error(y, prediction)),
        "rmse": float(np.sqrt(mean_squared_error(y, prediction))),
        "r2": float(r2_score(y, prediction)),
        "rows": len(y),
    }


def feature_table(panel: pd.DataFrame, horizon: int, timezone: str = 'Europe/Madrid'):
    """At issue t+1h, use counts ending at t+1h to predict issue+horizon.

    All lookups are by UTC time, never row offsets across missing hours.
    The label is departures over [target, target+1h); +60m is lead to its start.
    """
    series = panel.set_index(["time", "station"]).departures.sort_index()
    frame = (
        panel[["time", "station", "departures"]]
        .rename(columns={"time": "target", "departures": "y"})
        .copy()
    )
    frame["issue"] = frame.target - pd.Timedelta(hours=horizon)
    frame["feature_end"] = frame.issue
    for name, hours in [
        ("recent", horizon + 1),
        ("previous", horizon + 2),
        ("day", 24),
        ("week", 168),
    ]:
        keys = pd.MultiIndex.from_arrays([frame.target - pd.Timedelta(hours=hours), frame.station])
        frame[name] = series.reindex(keys).to_numpy()
    # Reindex the clock before rolling: an unknown network hour must not masquerade
    # as a zero or a neighboring observation. Every window ends before issue time.
    hours = pd.date_range(panel.time.min(), panel.time.max(), freq="h")
    history = panel.pivot(index="time", columns="station", values="departures").reindex(hours)
    recent_keys = pd.MultiIndex.from_arrays([frame.issue - pd.Timedelta(hours=1), frame.station])
    for name, window in [("mean_24h", 24), ("mean_7d", 168)]:
        rolling = history.rolling(window, min_periods=window).mean().stack()
        frame[name] = rolling.reindex(recent_keys).to_numpy()
    if "arrivals" in panel:
        incoming = panel.pivot(index="time", columns="station", values="arrivals").reindex(hours)
    else:
        incoming = history * 0  # Support departure-only research fixtures.
    movement = (history + incoming).rolling(24, min_periods=24).sum().stack()
    frame["movements_24h"] = movement.reindex(recent_keys).to_numpy()
    local = frame.target.dt.tz_convert(timezone)
    frame["hour"] = local.dt.hour
    frame["weekday"] = local.dt.dayofweek
    frame["month"] = local.dt.month
    frame["day_of_year"] = local.dt.dayofyear
    return frame.dropna().reset_index(drop=True)


def train_models(panel: pd.DataFrame, output: Path, year: int, force=False,
                 timezone: str = 'Europe/Madrid'):
    """Compare model families and publish the validation-selected forecast."""
    from src.ml.model_comparison import benchmark_models

    return benchmark_models(panel, output, year, force=force, timezone=timezone)
