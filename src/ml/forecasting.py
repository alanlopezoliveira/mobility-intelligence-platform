from __future__ import annotations

import pandas as pd


def naive_forecast(series: pd.Series, horizon: int = 1) -> pd.Series:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    last_value = float(series.iloc[-1]) if len(series) else 0.0
    return pd.Series([max(0.0, last_value)] * horizon, name=series.name)


def seasonal_naive_forecast(series: pd.Series, horizon: int = 1, seasonality: int = 1) -> pd.Series:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if seasonality <= 0:
        raise ValueError("seasonality must be positive")
    if len(series) < seasonality:
        return naive_forecast(series, horizon=horizon)
    values = list(series.iloc[-seasonality:])
    forecast = [max(0.0, float(value)) for value in values[:horizon]]
    if len(forecast) < horizon:
        forecast.extend([max(0.0, float(values[-1]))] * (horizon - len(forecast)))
    return pd.Series(forecast, name=series.name)
