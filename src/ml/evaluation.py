from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from src.ml.forecasting import naive_forecast, seasonal_naive_forecast


@dataclass(frozen=True)
class TemporalSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame


def chronological_split(
    frame: pd.DataFrame,
    timestamp_column: str = 'timestamp',
    train_fraction: float = 0.6,
    validation_fraction: float = 0.2,
) -> TemporalSplit:
    if timestamp_column not in frame.columns:
        raise ValueError(f'{timestamp_column!r} is required for chronological evaluation')
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1 or train_fraction + validation_fraction >= 1:
        raise ValueError('split fractions must be positive and leave a non-empty test period')

    ordered = frame.copy()
    ordered[timestamp_column] = pd.to_datetime(ordered[timestamp_column], errors='coerce', utc=True)
    if ordered[timestamp_column].isna().any():
        raise ValueError('timestamps must be valid before chronological splitting')
    ordered = ordered.sort_values(timestamp_column).reset_index(drop=True)
    if ordered.duplicated(subset=[timestamp_column]).any():
        raise ValueError('duplicate timestamps are not valid for this aggregate evaluation')

    train_end = int(len(ordered) * train_fraction)
    validation_end = train_end + int(len(ordered) * validation_fraction)
    if train_end < 1 or validation_end >= len(ordered):
        raise ValueError('not enough observations for train/validation/test periods')
    return TemporalSplit(ordered.iloc[:train_end], ordered.iloc[train_end:validation_end], ordered.iloc[validation_end:])


def lag_features(frame: pd.DataFrame, value_column: str, group_column: str, lags: list[int]) -> pd.DataFrame:
    if value_column not in frame.columns or group_column not in frame.columns:
        raise ValueError('value and group columns are required')
    if any(lag <= 0 for lag in lags):
        raise ValueError('lags must be positive')
    result = frame.copy()
    result['timestamp'] = pd.to_datetime(result['timestamp'], utc=True)
    result = result.sort_values([group_column, 'timestamp'])
    for lag in lags:
        result[f'{value_column}_lag_{lag}'] = result.groupby(group_column)[value_column].shift(lag)
    return result


def evaluate_baselines(actual: pd.Series, train_history: pd.Series, seasonality: int | None = None) -> dict[str, float]:
    if actual.empty or train_history.empty:
        raise ValueError('training history and test values are required')
    naive = naive_forecast(train_history, horizon=len(actual))
    predictions = {'naive': naive}
    if seasonality is not None and seasonality > 1 and len(train_history) >= seasonality:
        predictions['seasonal_naive'] = seasonal_naive_forecast(train_history, len(actual), seasonality)
    result: dict[str, float] = {}
    for name, predicted in predictions.items():
        result[f'{name}_mae'] = float(mean_absolute_error(actual, predicted))
        result[f'{name}_rmse'] = float(mean_squared_error(actual, predicted) ** 0.5)
        result[f'{name}_r2'] = float(r2_score(actual, predicted)) if actual.nunique() > 1 else float('nan')
    return result
