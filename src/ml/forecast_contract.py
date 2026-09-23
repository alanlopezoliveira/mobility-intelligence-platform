from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

HORIZONS_MINUTES = (60, 120)
LAGS_HOURS = (1, 2, 3, 6, 12, 24, 48, 168)
TARGET_COLUMN = 'target_departures'
KEY_COLUMNS = ['station_id', 'feature_timestamp', 'target_timestamp']


@dataclass(frozen=True)
class SplitBoundaries:
    train_start: str
    train_end: str
    validation_start: str
    validation_end: str
    test_start: str
    test_end: str


def _utc_text(value: pd.Timestamp) -> str:
    return value.tz_convert('UTC').strftime('%Y-%m-%dT%H:%M:%SZ')


def normalize_gold(frame: pd.DataFrame) -> pd.DataFrame:
    required = {'station_id', 'observed_at', 'demand'}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f'Gold is missing required columns: {sorted(missing)}')
    result = frame[['station_id', 'observed_at', 'demand']].copy()
    result['station_id'] = result['station_id'].astype(str).str.strip().map(
        lambda value: str(int(value)) if value.isdigit() else value
    )
    result['observed_at'] = pd.to_datetime(result['observed_at'], errors='coerce', utc=True)
    result['demand'] = pd.to_numeric(result['demand'], errors='coerce')
    if result[['station_id', 'observed_at', 'demand']].isna().any().any():
        raise ValueError('Gold contains null station, timestamp, or demand values')
    if result.duplicated(['station_id', 'observed_at']).any():
        raise ValueError('Gold contains duplicate station/timestamp observations')
    return result.sort_values(['station_id', 'observed_at']).reset_index(drop=True)


def choose_split_boundaries(frame: pd.DataFrame) -> SplitBoundaries:
    timestamps = pd.Series(frame['feature_timestamp'].sort_values().unique())
    if len(timestamps) < 3:
        raise ValueError('At least three distinct timestamps are required for chronological splits')
    train_end = timestamps.iloc[max(0, int(len(timestamps) * 0.70) - 1)]
    validation_end = timestamps.iloc[max(1, int(len(timestamps) * 0.85) - 1)]
    test_end = timestamps.iloc[-1]
    validation_start = timestamps[timestamps > train_end].iloc[0]
    test_start = timestamps[timestamps > validation_end].iloc[0]
    return SplitBoundaries(
        _utc_text(timestamps.iloc[0]), _utc_text(train_end), _utc_text(validation_start),
        _utc_text(validation_end), _utc_text(test_start), _utc_text(test_end),
    )


def build_observation_dataset(gold: pd.DataFrame, horizon_minutes: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    if horizon_minutes not in HORIZONS_MINUTES:
        raise ValueError('Only 60-minute and 120-minute horizons are supported')
    base = normalize_gold(gold)
    target_index = base.set_index(['station_id', 'observed_at'])['demand']
    result = base.rename(columns={'observed_at': 'feature_timestamp', 'demand': 'demand_at_feature_time'})
    result['target_timestamp'] = result['feature_timestamp'] + pd.to_timedelta(horizon_minutes, unit='min')
    target_keys = pd.MultiIndex.from_frame(result[['station_id', 'target_timestamp']])
    result[TARGET_COLUMN] = target_index.reindex(target_keys).to_numpy()
    result = result[result[TARGET_COLUMN].notna()].reset_index(drop=True)
    for lag_hours in LAGS_HOURS:
        lag_name = f'demand_lag_{lag_hours}h'
        available_name = f'{lag_name}_available'
        lag_keys = pd.MultiIndex.from_arrays([
            result['station_id'],
            result['feature_timestamp'] - pd.to_timedelta(lag_hours, unit='h'),
        ])
        result[lag_name] = target_index.reindex(lag_keys).to_numpy()
        result[available_name] = result[lag_name].notna()
    if result.empty:
        raise ValueError('No valid target observations were generated for the requested horizon and dataset')
    result['hour'] = result['feature_timestamp'].dt.hour
    result['day_of_week'] = result['feature_timestamp'].dt.dayofweek
    result['day_of_month'] = result['feature_timestamp'].dt.day
    result['month'] = result['feature_timestamp'].dt.month
    result['weekend'] = result['day_of_week'].isin([5, 6])
    result['horizon_minutes'] = horizon_minutes
    result = result.sort_values(KEY_COLUMNS).reset_index(drop=True)
    target_observed_count = len(result)
    boundaries = choose_split_boundaries(result)
    result['split'] = 'test'
    train_end = pd.Timestamp(boundaries.train_end)
    validation_start = pd.Timestamp(boundaries.validation_start)
    validation_end = pd.Timestamp(boundaries.validation_end)
    result.loc[result['feature_timestamp'] <= train_end, 'split'] = 'train'
    result.loc[
        result['feature_timestamp'].between(validation_start, validation_end), 'split'
    ] = 'validation'
    result['target_within_split'] = result['target_timestamp'].le(
        result['split'].map({'train': train_end, 'validation': validation_end, 'test': pd.Timestamp(boundaries.test_end)})
    )
    before_purge = len(result)
    result = result[result['target_within_split']].drop(columns=['target_within_split']).reset_index(drop=True)
    feature_columns = [column for column in result.columns if column not in KEY_COLUMNS + [TARGET_COLUMN, 'split']]
    validate_feature_contract(result, feature_columns)
    report = {
        'horizon_minutes': horizon_minutes,
        'candidate_target_rows': len(base),
        'target_observed_rows': int(target_observed_count),
        'target_unobserved_rows': int(len(base) - target_observed_count),
        'target_unobserved_reason': 'target_unobserved: no exact canonical row at station, feature_timestamp + horizon',
        'rows_before_split_boundary_purge': before_purge,
        'rows_after_split_boundary_purge': len(result),
        'split_boundaries': boundaries.__dict__,
        'split_counts': result['split'].value_counts().sort_index().to_dict(),
        'feature_columns': feature_columns,
        'lag_availability': {column: float(result[column].mean()) for column in result.columns if column.endswith('_available')},
    }
    return result, report


def validate_feature_contract(frame: pd.DataFrame, feature_columns: list[str]) -> None:
    forbidden = {TARGET_COLUMN, 'target_timestamp', 'source_year', 'arrivals', 'total_activity', 'net_flow'}
    leaked = forbidden.intersection(feature_columns)
    if leaked:
        raise ValueError(f'Forbidden future/target columns in features: {sorted(leaked)}')
    feature_timestamp = pd.to_datetime(frame['feature_timestamp'], utc=True)
    target_timestamp = pd.to_datetime(frame['target_timestamp'], utc=True)
    if (target_timestamp <= feature_timestamp).any():
        raise ValueError('Target timestamp must be strictly after feature timestamp')
    for column in feature_columns:
        if (
            column.startswith('demand_lag_')
            and not column.endswith('_available')
            and frame[column].notna().any()
            and (target_timestamp[frame[column].notna()] <= feature_timestamp[frame[column].notna()]).any()
        ):
            raise ValueError(f'Future value detected in feature {column}')


def evaluate_baselines(frame: pd.DataFrame, split: str = 'test') -> dict[str, Any]:
    subset = frame[frame['split'] == split].copy()
    output: dict[str, Any] = {'split': split, 'horizons': {}}
    for horizon in sorted(subset['horizon_minutes'].unique()):
        current = subset[subset['horizon_minutes'] == horizon]
        horizon_report: dict[str, Any] = {}
        for name, column in {
            'naive': 'demand_lag_1h',
            'seasonal_naive_24h': 'demand_lag_24h',
            'seasonal_naive_168h': 'demand_lag_168h',
        }.items():
            valid = current[column].notna()
            actual = current.loc[valid, TARGET_COLUMN]
            prediction = current.loc[valid, column]
            metrics: dict[str, Any] = {
                'target_rows': len(current), 'evaluated_rows': int(valid.sum()),
                'prediction_coverage': float(valid.mean()) if len(current) else 0.0,
            }
            if len(actual):
                metrics.update({
                    'mae': float(mean_absolute_error(actual, prediction)),
                    'rmse': float(mean_squared_error(actual, prediction) ** 0.5),
                    'r2': float(r2_score(actual, prediction)) if actual.nunique() > 1 else None,
                })
            else:
                metrics.update({'mae': None, 'rmse': None, 'r2': None})
            horizon_report[name] = metrics
        output['horizons'][str(horizon)] = horizon_report
    return output


def dataframe_hash(frame: pd.DataFrame) -> str:
    row_hashes = pd.util.hash_pandas_object(frame, index=False)
    return hashlib.sha256(row_hashes.to_numpy().tobytes()).hexdigest()


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', delete=False, dir=path.parent, suffix='.csv.gz') as temp:
        frame.to_csv(temp, index=False, lineterminator='\n', compression='gzip')
        temporary = Path(temp.name)
    os.replace(temporary, path)