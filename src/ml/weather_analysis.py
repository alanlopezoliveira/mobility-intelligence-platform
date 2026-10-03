"""Descriptive weather associations; never interpret these as causal effects."""

from __future__ import annotations

import pandas as pd


def compare_weather(network: pd.DataFrame, weather: pd.DataFrame,
                    timezone: str = 'Europe/Madrid'):
    """Match trip intervals to rain accumulated over the SAME preceding hour.

    Open-Meteo rain at 14:00 covers 13:00-14:00, while a trip bucket is labelled
    by its start. Weather timestamps are therefore shifted back by one hour.
    Matched dry hours share local month, hour and weekday/weekend status.
    """
    climate = weather.copy()
    climate.time = climate.time - pd.Timedelta(hours=1)
    frame = network.merge(climate, on="time", how="inner", validate="one_to_one")
    local = frame.time.dt.tz_convert(timezone)
    frame["month"] = local.dt.month
    frame["hour"] = local.dt.hour
    frame["weekend"] = local.dt.dayofweek.ge(5)
    frame["wet"] = frame.rain.ge(0.1)
    groups = frame.groupby("wet").departures.agg(["mean", "size", "sum"])
    if len(groups) != 2:
        raise ValueError("Need both rainy and dry observations")
    strata = ["month", "hour", "weekend"]
    dry = frame[~frame.wet].groupby(strata).departures.agg(dry_mean="mean", dry_hours="size")
    matched = frame[frame.wet].join(dry, on=strata)
    matched = matched[matched.dry_hours.ge(3)]
    if matched.empty or matched.dry_mean.mean() <= 0:
        raise ValueError("Insufficient matched dry-hour reference for rain comparison")
    expected = float(matched.dry_mean.mean())
    actual = float(matched.departures.mean())
    monthly = (
        frame.groupby(["month", "wet"]).departures.agg(mean="mean", hours="size").reset_index()
    )
    daily = (
        frame.assign(date=local.dt.strftime("%Y-%m-%d"))
        .groupby("date")
        .agg(
            departures=("departures", "sum"),
            rain=("rain", "sum"),
            temperature=("temperature_2m", "mean"),
            hours=("time", "size"),
        )
        .reset_index()
    )
    return {
        "rain_threshold_mm": 0.1,
        "joined_hours": len(frame),
        "unmatched_hours": len(network) - len(frame),
        "rainy_hours": int(groups.loc[True, "size"]),
        "dry_hours": int(groups.loc[False, "size"]),
        "rainy_mean": float(groups.loc[True, "mean"]),
        "dry_mean": float(groups.loc[False, "mean"]),
        "raw_change_pct": float((groups.loc[True, "mean"] / groups.loc[False, "mean"] - 1) * 100),
        "matched_rainy_hours": len(matched),
        "matched_rainy_mean": actual,
        "matched_dry_mean": expected,
        "adjusted_change_pct": (actual / expected - 1) * 100,
        "adjustment": "Dry reference matched on local month, hour and weekday/weekend; at least 3 dry hours per stratum",
        "caveat": "Association, not causation. Reanalysis, missing trips, station closures, holidays and temperature can confound the comparison.",
        "monthly": monthly.to_dict("records"),
        "daily": daily.to_dict("records"),
    }, frame
