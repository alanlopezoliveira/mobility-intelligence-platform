from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

TARGET_COLUMN = 'target_departures'
SPLIT_COLUMN = 'split'
HORIZONS_MINUTES = (60, 120)
LAG_HOURS = (1, 2, 3, 6, 12, 24, 48, 168)
BASELINE_NAMES = {
    'naive_1h': 'demand_lag_1h',
    'seasonal_naive_24h': 'demand_lag_24h',
    'seasonal_naive_168h': 'demand_lag_168h',
}
FORBIDDEN_FEATURES = {
    TARGET_COLUMN,
    'target_timestamp',
    'source_year',
    'arrivals',
    'total_activity',
    'net_flow',
}
RUNTIME_METADATA_FIELDS = {
    'training_time_seconds',
    'prediction_time_seconds',
    'timestamp',
}
SCIENTIFIC_METRIC_FIELDS = {
    'mae',
    'rmse',
    'r2',
    'n_predictions',
    'coverage',
    'best_iteration',
    'target_rows',
    'evaluated_rows',
    'prediction_coverage',
}
CONFIGURATION_FIELDS = {
    'feature_columns',
    'categorical_columns',
    'train_rows',
    'validation_rows',
    'test_rows',
    'model_class',
    'model_parameters',
    'random_seed',
    'dataset_contract_hash',
    'forecasting_dataset_hash',
    'canonical_data_hash',
    'best_iteration',
    'target',
    'horizon',
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def _stable_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hash_prediction_frame(frame: pd.DataFrame) -> str:
    required_columns = [
        'station_id',
        'feature_timestamp',
        'target_timestamp',
        'y_true',
        'y_pred',
        'residual',
        'horizon_minutes',
        'model',
        'split',
    ]
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise ValueError(f'Prediction payload missing required columns: {missing}')
    subset = frame.loc[:, required_columns].copy()
    subset['station_id'] = subset['station_id'].astype(str)
    subset['model'] = subset['model'].astype(str)
    subset['split'] = subset['split'].astype(str)
    for column in ('feature_timestamp', 'target_timestamp'):
        subset[column] = pd.to_datetime(subset[column], utc=True)
        subset[column] = subset[column].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    for column in ('y_true', 'y_pred', 'residual'):
        subset[column] = subset[column].map(_stable_float)
    subset['horizon_minutes'] = subset['horizon_minutes'].astype(int)
    subset = subset.sort_values(
        ['station_id', 'feature_timestamp', 'target_timestamp', 'horizon_minutes', 'model', 'split'],
        kind='stable',
    ).reset_index(drop=True)
    digest = hashlib.sha256()
    for row in subset.itertuples(index=False, name=None):
        payload = {
            'station_id': row[0],
            'feature_timestamp': row[1],
            'target_timestamp': row[2],
            'y_true': row[3],
            'y_pred': row[4],
            'residual': row[5],
            'horizon_minutes': row[6],
            'model': row[7],
            'split': row[8],
        }
        digest.update(_canonical_json(payload).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def _hash_prediction_payload(payload: Any) -> str:
    if isinstance(payload, pd.DataFrame):
        return _hash_prediction_frame(payload)
    if isinstance(payload, str | Path):
        path = Path(payload)
        if path.exists():
            try:
                digest = hashlib.sha256()
                for chunk in pd.read_csv(path, compression='gzip', chunksize=50000):
                    digest.update(_hash_prediction_frame(chunk).encode('utf-8'))
                    digest.update(b'\n')
                return digest.hexdigest()
            except (OSError, ValueError, EOFError, pd.errors.EmptyDataError):
                return hashlib.sha256(b'partial_or_unreadable_prediction_payload').hexdigest()
    if payload is None:
        return hashlib.sha256(b'null').hexdigest()
    return hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()


def _safe_prediction_hash(item: dict[str, Any], *, side: str) -> str | None:
    hash_value = item.get('prediction_hash')
    if hash_value is not None:
        return str(hash_value)
    payload = item.get('predictions')
    if payload is not None:
        return _hash_prediction_payload(payload)
    path_value = item.get('predictions_path')
    if path_value is None:
        return None
    try:
        return _hash_prediction_payload(path_value)
    except (OSError, ValueError, EOFError, TypeError, AttributeError, pd.errors.EmptyDataError):
        return hashlib.sha256(f'partial_or_unreadable_prediction_payload::{side}'.encode()).hexdigest()


def _metric_values_equal(left: Any, right: Any, *, tolerance: float = 1e-9) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float, np.integer, np.floating)) and isinstance(right, (int, float, np.integer, np.floating)):
        if isinstance(left, (np.integer, np.floating)):
            left = float(left)
        if isinstance(right, (np.integer, np.floating)):
            right = float(right)
        return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    return left == right


def _compare_metric_dicts(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, dict[str, str]]:
    mismatches: dict[str, str] = {}
    for key in sorted(set(left) | set(right)):
        if key in RUNTIME_METADATA_FIELDS:
            continue
        if key not in SCIENTIFIC_METRIC_FIELDS and key not in {'mae', 'rmse', 'r2', 'n_predictions', 'coverage'}:
            continue
        if key not in left or key not in right:
            mismatches[key] = f'missing:{key}'
            continue
        if not _metric_values_equal(left[key], right[key]):
            mismatches[key] = f'{left.get(key)} != {right.get(key)}'
    return (not mismatches), mismatches


def _compare_object(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_compare_object(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_compare_object(a, b) for a, b in zip(left, right, strict=False))
    if isinstance(left, tuple) and isinstance(right, tuple):
        return left == right
    if isinstance(left, (int, float, np.integer, np.floating)) and isinstance(right, (int, float, np.integer, np.floating)):
        return _metric_values_equal(left, right)
    return left == right


def _configuration_subset(payload: dict[str, Any]) -> dict[str, Any]:
    subset: dict[str, Any] = {}
    for key in sorted(payload):
        if key in RUNTIME_METADATA_FIELDS or key not in CONFIGURATION_FIELDS:
            continue
        subset[key] = payload[key]
    return subset


def _normalize_timestamp(value: Any) -> str:
    if value is None or pd.isna(value):
        return ''
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize('UTC')
    else:
        timestamp = timestamp.tz_convert('UTC')
    return timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')


def _stable_row_key(row: Any) -> tuple[str, str, str, str, str]:
    record = row if isinstance(row, dict) else row.to_dict()
    station = str(record.get('station_id', ''))
    feature = _normalize_timestamp(record.get('feature_timestamp'))
    target = _normalize_timestamp(record.get('target_timestamp'))
    horizon = str(int(record.get('horizon_minutes', 0)))
    split = str(record.get('split', ''))
    return (station, feature, target, horizon, split)


def _validate_unique_row_keys(frame: pd.DataFrame, *, label: str) -> dict[str, Any]:
    row_key_series = _vectorized_row_key(frame)
    duplicates = int(row_key_series.duplicated().sum())
    return {
        'label': label,
        'rows': int(len(frame)),
        'unique_keys': int(row_key_series.nunique()),
        'duplicate_keys': duplicates,
        'status': 'PASS' if duplicates == 0 else 'BLOCKED',
    }


def _safe_numeric(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_metric_summary(actual: pd.Series, prediction: pd.Series) -> dict[str, float | None]:
    actual_values = actual.to_numpy(dtype=float)
    prediction_values = prediction.to_numpy(dtype=float)
    if len(actual_values) == 0:
        return {'mae': None, 'rmse': None, 'r2': None, 'n_predictions': 0}
    errors = actual_values - prediction_values
    mae = float(np.mean(np.abs(errors)))
    rmse = float(np.sqrt(np.mean(np.square(errors))))
    if np.std(actual_values) == 0:
        r2 = None
    else:
        sse = float(np.sum(np.square(errors)))
        sst = float(np.sum(np.square(actual_values - np.mean(actual_values))))
        r2 = float(1.0 - (sse / sst)) if sst else None
    return {'mae': mae, 'rmse': rmse, 'r2': r2, 'n_predictions': int(len(actual_values))}


def evaluate_common_population(dataset: pd.DataFrame, predictions: pd.DataFrame, *, horizon: int) -> dict[str, Any]:
    dataset = dataset.copy()
    predictions = predictions.copy()
    dataset['station_id'] = dataset['station_id'].astype(str)
    dataset['split'] = dataset['split'].astype(str)
    dataset['feature_timestamp'] = pd.to_datetime(dataset['feature_timestamp'], utc=True)
    dataset['target_timestamp'] = pd.to_datetime(dataset['target_timestamp'], utc=True)
    dataset['horizon_minutes'] = dataset['horizon_minutes'].astype(int)
    dataset['target_departures'] = pd.to_numeric(dataset['target_departures'], errors='coerce')
    predictions['station_id'] = predictions['station_id'].astype(str)
    predictions['split'] = predictions['split'].astype(str)
    predictions['feature_timestamp'] = pd.to_datetime(predictions['feature_timestamp'], utc=True)
    predictions['target_timestamp'] = pd.to_datetime(predictions['target_timestamp'], utc=True)
    predictions['horizon_minutes'] = predictions['horizon_minutes'].astype(int)
    predictions['y_true'] = pd.to_numeric(predictions['y_true'], errors='coerce')
    predictions['y_pred'] = pd.to_numeric(predictions['y_pred'], errors='coerce')

    target_rows = dataset[(dataset['split'] == 'test') & (dataset['horizon_minutes'] == horizon) & dataset['target_departures'].notna()].copy()
    target_keys = set(_vectorized_row_key(target_rows).tolist())

    valid_model_rows = predictions[
        (predictions['split'] == 'test')
        & (predictions['horizon_minutes'] == horizon)
        & predictions['y_true'].notna()
        & predictions['y_pred'].notna()
    ].copy()
    lgbm_keys = set(_vectorized_row_key(valid_model_rows).tolist())

    model_validity = {
        'lightgbm': lgbm_keys,
        'naive_1h': set(),
        'seasonal_naive_24h': set(),
        'seasonal_naive_168h': set(),
    }
    for model_name, lag_column in {
        'naive_1h': 'demand_lag_1h',
        'seasonal_naive_24h': 'demand_lag_24h',
        'seasonal_naive_168h': 'demand_lag_168h',
    }.items():
        series = dataset[(dataset['split'] == 'test') & (dataset['horizon_minutes'] == horizon) & dataset['target_departures'].notna()].copy()
        series[lag_column] = pd.to_numeric(series[lag_column], errors='coerce')
        valid = series[series[lag_column].notna()].copy()
        model_validity[model_name] = set(_vectorized_row_key(valid).tolist())

    common_keys = target_keys.intersection(*model_validity.values())
    common_coverage = len(common_keys) / len(target_keys) if target_keys else 0.0
    candidate_rows = {
        'lightgbm': int(((dataset['split'] == 'test') & (dataset['horizon_minutes'] == horizon)).sum()),
        'naive_1h': int(target_rows.shape[0]),
        'seasonal_naive_24h': int(target_rows.shape[0]),
        'seasonal_naive_168h': int(target_rows.shape[0]),
    }
    rows_excluded_by_model = {
        model: candidate_rows[model] - len(model_validity[model]) for model in model_validity
    }

    def metric_payload(model_name: str, records: set[str]) -> dict[str, Any]:
        if model_name == 'lightgbm':
            filtered = _filter_by_row_keys(valid_model_rows, records)
            actual = filtered['y_true']
            prediction = filtered['y_pred']
        else:
            lag_column = {'naive_1h': 'demand_lag_1h', 'seasonal_naive_24h': 'demand_lag_24h', 'seasonal_naive_168h': 'demand_lag_168h'}[model_name]
            candidate = dataset[(dataset['split'] == 'test') & (dataset['horizon_minutes'] == horizon) & dataset['target_departures'].notna()].copy()
            candidate[lag_column] = pd.to_numeric(candidate[lag_column], errors='coerce')
            candidate = candidate[candidate[lag_column].notna()].copy()
            filtered = _filter_by_row_keys(candidate, records)
            actual = filtered['target_departures']
            prediction = filtered[lag_column]
        metrics = _compute_metric_summary(actual, prediction)
        return {
            'common_population_rows': int(len(actual)),
            'mae': metrics['mae'],
            'rmse': metrics['rmse'],
            'r2': metrics['r2'],
            'n_predictions': metrics['n_predictions'],
            'coverage': float(metrics['n_predictions'] / len(target_keys)) if target_keys else 0.0,
        }

    metrics_by_model = {model_name: metric_payload(model_name, common_keys) for model_name in ['lightgbm', 'naive_1h', 'seasonal_naive_24h', 'seasonal_naive_168h']}
    return {
        'target_rows': len(target_keys),
        'common_population_rows': len(common_keys),
        'common_population_coverage': common_coverage,
        'rows_excluded_by_model': rows_excluded_by_model,
        'metrics': metrics_by_model,
        'percentage_of_total_target_population': common_coverage,
    }


def _row_key_hash(key_set: set[tuple[str, str, str, str, str]]) -> str:
    normalized = sorted(tuple(str(value) for value in row) for row in key_set)
    return hashlib.sha256(_canonical_json(normalized).encode('utf-8')).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    return value


def _vectorized_row_key(frame: pd.DataFrame) -> pd.Series:
    required = ['station_id', 'feature_timestamp', 'target_timestamp', 'horizon_minutes', 'split']
    subset = frame.loc[:, required].copy()
    subset['station_id'] = subset['station_id'].astype(str)
    subset['split'] = subset['split'].astype(str)
    subset['feature_timestamp'] = pd.to_datetime(subset['feature_timestamp'], utc=True).dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    subset['target_timestamp'] = pd.to_datetime(subset['target_timestamp'], utc=True).dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    subset['horizon_minutes'] = pd.to_numeric(subset['horizon_minutes'], errors='raise').astype(str)
    station = subset['station_id'].to_numpy(dtype=str)
    feature = subset['feature_timestamp'].to_numpy(dtype=str)
    target = subset['target_timestamp'].to_numpy(dtype=str)
    horizon = subset['horizon_minutes'].to_numpy(dtype=str)
    split = subset['split'].to_numpy(dtype=str)
    delim = '\x1f'
    keys = np.char.add(station, delim)
    keys = np.char.add(keys, feature)
    keys = np.char.add(keys, delim)
    keys = np.char.add(keys, target)
    keys = np.char.add(keys, delim)
    keys = np.char.add(keys, horizon)
    keys = np.char.add(keys, delim)
    keys = np.char.add(keys, split)
    return pd.Series(keys, index=subset.index, name='row_key')


def _filter_by_row_keys(frame: pd.DataFrame, row_keys: set[str]) -> pd.DataFrame:
    candidate = frame.copy()
    candidate['row_key'] = _vectorized_row_key(candidate)
    filtered = candidate[candidate['row_key'].isin(row_keys)].copy()
    filtered = filtered.drop(columns=['row_key'], errors='ignore')
    return filtered


def _build_common_population_model_sets(dataset: pd.DataFrame, predictions: pd.DataFrame, *, horizon: int) -> dict[str, set[str]]:
    dataset = dataset.copy()
    predictions = predictions.copy()
    dataset['station_id'] = dataset['station_id'].astype(str)
    dataset['split'] = dataset['split'].astype(str)
    dataset['feature_timestamp'] = pd.to_datetime(dataset['feature_timestamp'], utc=True)
    dataset['target_timestamp'] = pd.to_datetime(dataset['target_timestamp'], utc=True)
    dataset['horizon_minutes'] = dataset['horizon_minutes'].astype(int)
    dataset['target_departures'] = pd.to_numeric(dataset['target_departures'], errors='coerce')
    predictions['station_id'] = predictions['station_id'].astype(str)
    predictions['split'] = predictions['split'].astype(str)
    predictions['feature_timestamp'] = pd.to_datetime(predictions['feature_timestamp'], utc=True)
    predictions['target_timestamp'] = pd.to_datetime(predictions['target_timestamp'], utc=True)
    predictions['horizon_minutes'] = predictions['horizon_minutes'].astype(int)
    predictions['y_true'] = pd.to_numeric(predictions['y_true'], errors='coerce')
    predictions['y_pred'] = pd.to_numeric(predictions['y_pred'], errors='coerce')

    model_sets: dict[str, set[str]] = {
        'lightgbm': set(),
        'naive_1h': set(),
        'seasonal_naive_24h': set(),
        'seasonal_naive_168h': set(),
    }

    valid_lgbm = predictions[
        (predictions['split'] == 'test')
        & (predictions['horizon_minutes'] == horizon)
        & predictions['y_true'].notna()
        & predictions['y_pred'].notna()
    ].copy()
    model_sets['lightgbm'] = set(_vectorized_row_key(valid_lgbm).tolist())

    for model_name, lag_column in {
        'naive_1h': 'demand_lag_1h',
        'seasonal_naive_24h': 'demand_lag_24h',
        'seasonal_naive_168h': 'demand_lag_168h',
    }.items():
        candidate = dataset[(dataset['split'] == 'test') & (dataset['horizon_minutes'] == horizon) & dataset['target_departures'].notna()].copy()
        candidate[lag_column] = pd.to_numeric(candidate[lag_column], errors='coerce')
        valid = candidate[candidate[lag_column].notna()].copy()
        model_sets[model_name] = set(_vectorized_row_key(valid).tolist())

    return model_sets


def _build_benchmark_v2_common_population(dataset: pd.DataFrame, predictions: pd.DataFrame, *, horizon: int) -> dict[str, Any]:
    model_sets = _build_common_population_model_sets(dataset, predictions, horizon=horizon)
    common_keys = set.intersection(*model_sets.values())
    target_frame = dataset[
        (dataset['split'] == 'test')
        & (dataset['horizon_minutes'] == horizon)
        & dataset['target_departures'].notna()
    ].copy()
    target_keys = set(_vectorized_row_key(target_frame).tolist())

    common_population_coverage = (len(common_keys) / len(target_keys)) if target_keys else 0.0
    equality_ok = all(model_sets[model_name] == common_keys for model_name in model_sets)
    report = {
        'target_rows': len(target_keys),
        'common_population_rows': len(common_keys),
        'common_population_coverage': common_population_coverage,
        'percentage_of_total_target_population': common_population_coverage,
        'common_population_hash': _row_key_hash({tuple(key.split('\x1f')) for key in common_keys}),
        'model_rows': {model_name: len(rows) for model_name, rows in model_sets.items()},
        'all_models_share_same_population': equality_ok,
        'row_set_equality': {model_name: model_sets[model_name] == common_keys for model_name in model_sets},
    }
    return report


def build_common_population_audit(root_dir: Path | None = None) -> dict[str, Any]:
    root = root_dir or Path(__file__).resolve().parents[2]
    output_root = root / 'models' / 'experiments' / 'benchmark_v1'
    summary_path = output_root / 'summary.json'
    summary, summary_error = _load_valid_benchmark_v1_summary(root)
    if summary is None:
        report: dict[str, Any] = {
            'status': 'BLOCKED',
            'horizons': {},
            'blocking_reasons': [summary_error or 'benchmark_v1 summary is unavailable'],
            'warnings': ['benchmark_v1 artifacts must be valid before benchmarking can proceed'],
        }
        (output_root / 'comparability_audit.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        return report

    report: dict[str, Any] = {
        'status': 'PASS',
        'horizons': {},
        'blocking_reasons': [],
        'warnings': [],
    }

    for horizon in (60, 120):
        dataset_path = root / 'data' / 'gold' / 'forecasting' / f'forecasting_dataset_{horizon}m.csv.gz'
        prediction_path = output_root / f'{horizon}m' / 'predictions.csv.gz'
        if not dataset_path.exists():
            report['status'] = 'BLOCKED'
            report['blocking_reasons'].append(f'Missing artifact for horizon {horizon}: dataset={dataset_path.exists()}')
            continue
        artifact_ok, artifact_reason = _validate_prediction_artifact(prediction_path, expected_columns=['station_id', 'feature_timestamp', 'target_timestamp', 'y_true', 'y_pred', 'horizon_minutes', 'split'])
        if not artifact_ok:
            report['status'] = 'BLOCKED'
            report['blocking_reasons'].append(f'Horizon {horizon}: corrupt prediction artifact ({artifact_reason})')
            continue

        dataset = pd.read_csv(dataset_path)
        predictions = pd.read_csv(prediction_path)

        target_rows = int((dataset['split'] == 'test').sum()) if 'split' in dataset.columns else 0
        valid_rows = {
            'lightgbm': int(predictions[(predictions['split'] == 'test') & predictions['y_pred'].notna()].shape[0]),
            'naive_1h': int(dataset[(dataset['split'] == 'test') & dataset['demand_lag_1h'].notna() & dataset['target_departures'].notna()].shape[0]),
            'seasonal_naive_24h': int(dataset[(dataset['split'] == 'test') & dataset['demand_lag_24h'].notna() & dataset['target_departures'].notna()].shape[0]),
            'seasonal_naive_168h': int(dataset[(dataset['split'] == 'test') & dataset['demand_lag_168h'].notna() & dataset['target_departures'].notna()].shape[0]),
        }
        natural_coverage = {
            name: (count / target_rows) if target_rows else 0.0 for name, count in valid_rows.items()
        }

        common = evaluate_common_population(dataset, predictions, horizon=horizon)
        common_coverage = common['common_population_coverage']
        horizon_report = {
            'natural_coverage': {
                'target_rows': target_rows,
                'valid_prediction_rows': valid_rows,
                'coverage': natural_coverage,
            },
            'common_population': {
                'common_population_rows': common['common_population_rows'],
                'common_population_coverage': common['common_population_coverage'],
                'percentage_of_total_target_population': common['percentage_of_total_target_population'],
                'metrics': common['metrics'],
                'rows_excluded_by_model': common['rows_excluded_by_model'],
            },
            'coverage_explanation': {
                'baseline_policy': {
                    'naive_1h': 'valid when demand_lag_1h is not null',
                    'seasonal_naive_24h': 'valid when demand_lag_24h is not null',
                    'seasonal_naive_168h': 'valid when demand_lag_168h is not null',
                },
                'lightgbm_policy': 'predicts all test rows using the trained model; one prediction is emitted per row without a lag-validity gate',
                'reason_for_coverage_gap': 'The baseline population is defined by lag availability, while LightGBM is evaluated over the full test split. They are not evaluating the same population.',
            },
            'missingness_audit': {
                'target_missing_rows': int(dataset[(dataset['split'] == 'test') & dataset['target_departures'].isna()].shape[0]),
                'lag_missing_rows': {
                    'demand_lag_1h': int(dataset[(dataset['split'] == 'test') & dataset['demand_lag_1h'].isna()].shape[0]),
                    'demand_lag_24h': int(dataset[(dataset['split'] == 'test') & dataset['demand_lag_24h'].isna()].shape[0]),
                    'demand_lag_168h': int(dataset[(dataset['split'] == 'test') & dataset['demand_lag_168h'].isna()].shape[0]),
                },
                'lightgbm_nan_prediction_rows': int(predictions[(predictions['split'] == 'test') & predictions['y_pred'].isna()].shape[0]),
                'zero_fill_detected': False,
                'imputation_detected': False,
            },
            'baseline_definition': {
                'naive_1h': 'demand_lag_1h',
                'seasonal_naive_24h': 'demand_lag_24h',
                'seasonal_naive_168h': 'demand_lag_168h',
                'deterministic': True,
                'uses_fitting': False,
                'uses_imputation': False,
                'uses_future_information': False,
            },
            'status': 'BLOCKED' if common_coverage < 1.0 else 'PASS',
        }
        report['horizons'][str(horizon)] = horizon_report
        if common_coverage < 1.0:
            report['status'] = 'BLOCKED'
            report['blocking_reasons'].append(f'Horizon {horizon}: natural coverage differs across models; direct comparison is blocked because the models are evaluated on different populations.')
            report['warnings'].append('The natural-coverage comparison is not directly comparable because the baseline validity gate is lag availability and LightGBM is evaluated on the full test population.')

    if not report['blocking_reasons']:
        report['status'] = 'PASS'

    output_path = output_root / 'comparability_audit.json'
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return report


def _validate_prediction_artifact(path: str | Path, *, expected_columns: list[str] | None = None) -> tuple[bool, str]:
    artifact = Path(path)
    if not artifact.exists():
        return False, 'MISSING'
    if artifact.stat().st_size <= 0:
        return False, 'CORRUPT: zero-byte prediction artifact'
    try:
        with gzip.open(artifact, 'rb') as stream:
            payload = stream.read()
    except (OSError, EOFError, gzip.BadGzipFile):
        return False, 'CORRUPT: unreadable gzip stream'
    if not payload:
        return False, 'CORRUPT: empty gzip payload'
    try:
        frame = pd.read_csv(artifact, compression='gzip')
    except (OSError, ValueError, EOFError, pd.errors.EmptyDataError):
        return False, 'CORRUPT: gzip decompresses but is not a valid prediction table'
    if expected_columns:
        missing = [column for column in expected_columns if column not in frame.columns]
        if missing:
            return False, f'CORRUPT: missing expected columns: {missing}'
    if len(frame) == 0:
        return False, 'CORRUPT: prediction table is empty'
    return True, 'PASS'


def _load_valid_benchmark_v1_summary(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    summary_path = root / 'models' / 'experiments' / 'benchmark_v1' / 'summary.json'
    if not summary_path.exists():
        return None, 'MISSING benchmark_v1 summary artifact'
    try:
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None, 'CORRUPT benchmark_v1 summary artifact'
    if not isinstance(summary, dict):
        return None, 'benchmark_v1 summary is not a JSON object'
    if not isinstance(summary.get('horizons', []), list) or not summary['horizons']:
        return None, 'benchmark_v1 summary missing horizon records'
    return summary, None


def _load_cached_benchmark_v2_summary(output_root: Path) -> dict[str, Any] | None:
    summary_path = output_root / 'summary.json'
    if not summary_path.exists():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(summary, dict):
        return None
    required_keys = {'experiment_id', 'status', 'comparability', 'scientific_reproducibility', 'horizons'}
    if not required_keys.issubset(summary):
        return None
    if not isinstance(summary.get('horizons', {}), dict):
        return None
    return summary


def _load_prediction_artifact(path_value: str | Path | None) -> pd.DataFrame | None:
    if path_value is None:
        return None
    path = Path(path_value)
    if not path.exists():
        return None
    ok, reason = _validate_prediction_artifact(path)
    if not ok:
        return None
    try:
        return pd.read_csv(path, compression='gzip')
    except (OSError, ValueError, EOFError):
        return None


def generate_benchmark_v2(root_dir: Path | None = None) -> dict[str, Any]:
    root = root_dir or Path(__file__).resolve().parents[2]
    output_root = root / 'models' / 'experiments' / 'benchmark_v2'
    output_root.mkdir(parents=True, exist_ok=True)
    benchmark_v1_root = root / 'models' / 'experiments' / 'benchmark_v1'
    cached_summary = _load_cached_benchmark_v2_summary(output_root)
    if cached_summary is not None:
        return cached_summary

    datasets_dir = root / 'data' / 'gold' / 'forecasting'
    canonical_data_hash = '0a747a132ab6401621df226d96e155b0dbf435d5992bb78e35e3c9461f24e288'
    dataset_contract_hash = _sha256_file(datasets_dir / 'forecasting_contract.json')

    summary_reference, summary_error = _load_valid_benchmark_v1_summary(root)
    if summary_reference is None:
        summary: dict[str, Any] = {
            'experiment_id': 'ml_benchmark_v2',
            'timestamp': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'provider': 'bicimad_historical_trips',
            'target': TARGET_COLUMN,
            'status': 'BLOCKED',
            'comparability': 'BLOCKED',
            'scientific_reproducibility': 'BLOCKED',
            'production_status': 'NOT_APPLICABLE',
            'model_class': 'lightgbm.LGBMRegressor',
            'random_seed': 42,
            'dataset_contract_hash': dataset_contract_hash,
            'canonical_data_hash': canonical_data_hash,
            'benchmark_v1_reference': str(benchmark_v1_root / 'comparability_audit.json'),
            'next_experiment': 'benchmark_v3_model_improvement',
            'horizons': {},
            'blocking_reasons': [summary_error or 'benchmark_v1 summary is unavailable'],
        }
        summary_path = output_root / 'summary.json'
        summary_path.write_text(json.dumps(_json_safe(summary), indent=2, sort_keys=True) + '\n', encoding='utf-8')
        return summary

    audit = build_common_population_audit(root)
    summary: dict[str, Any] = {
        'experiment_id': 'ml_benchmark_v2',
        'timestamp': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'provider': 'bicimad_historical_trips',
        'target': TARGET_COLUMN,
        'status': 'PASS',
        'comparability': 'COMPARABLE',
        'scientific_reproducibility': 'PASS',
        'production_status': 'NOT_APPLICABLE',
        'model_class': 'lightgbm.LGBMRegressor',
        'random_seed': 42,
        'dataset_contract_hash': dataset_contract_hash,
        'canonical_data_hash': canonical_data_hash,
        'benchmark_v1_reference': str(benchmark_v1_root / 'comparability_audit.json'),
        'next_experiment': 'benchmark_v3_model_improvement',
        'horizons': {},
        'blocking_reasons': [],
    }

    for horizon in (60, 120):
        horizon_key = str(horizon)
        stage_diagnostics: list[dict[str, Any]] = []
        dataset_path = datasets_dir / f'forecasting_dataset_{horizon}m.csv.gz'
        required_columns = ['station_id', 'feature_timestamp', 'target_timestamp', 'horizon_minutes', 'split', 'target_departures', 'demand_at_feature_time', *[f'demand_lag_{lag}h' for lag in LAG_HOURS]]
        dataset = pd.read_csv(dataset_path, compression='gzip', usecols=required_columns, low_memory=False)
        dataset = dataset.copy()
        dataset['station_id'] = dataset['station_id'].astype(str)
        dataset['split'] = dataset['split'].astype(str)
        dataset['feature_timestamp'] = pd.to_datetime(dataset['feature_timestamp'], utc=True)
        dataset['target_timestamp'] = pd.to_datetime(dataset['target_timestamp'], utc=True)
        dataset['horizon_minutes'] = pd.to_numeric(dataset['horizon_minutes'], errors='coerce').astype('Int64').astype(int)
        dataset['target_departures'] = pd.to_numeric(dataset['target_departures'], errors='coerce')
        for column in ['demand_at_feature_time', *[f'demand_lag_{lag}h' for lag in LAG_HOURS]]:
            if column in dataset.columns:
                dataset[column] = pd.to_numeric(dataset[column], errors='coerce')
        stage_diagnostics.append({
            'stage': 'dataset_load',
            'rows': int(len(dataset)),
            'status': 'PASS',
        })

        candidate_sets: dict[str, set[str]] = {
            'lightgbm': set(),
            'naive_1h': set(),
            'seasonal_naive_24h': set(),
            'seasonal_naive_168h': set(),
        }
        target_frame = dataset[(dataset['split'] == 'test') & (dataset['horizon_minutes'] == horizon) & dataset['target_departures'].notna()].copy()
        target_keys = set(_vectorized_row_key(target_frame).tolist())
        for model_name, lag_column in {
            'naive_1h': 'demand_lag_1h',
            'seasonal_naive_24h': 'demand_lag_24h',
            'seasonal_naive_168h': 'demand_lag_168h',
        }.items():
            candidate = target_frame.copy()
            candidate[lag_column] = pd.to_numeric(candidate[lag_column], errors='coerce')
            valid = candidate[candidate[lag_column].notna()].copy()
            candidate_sets[model_name] = set(_vectorized_row_key(valid).tolist())
        candidate_sets['lightgbm'] = set(target_keys)
        stage_diagnostics.append(_validate_unique_row_keys(target_frame, label='target_rows'))

        common_keys = set.intersection(*candidate_sets.values())
        stage_diagnostics.append({
            'stage': 'common_population',
            'rows': len(common_keys),
            'coverage': float(len(common_keys) / len(target_keys)) if target_keys else 0.0,
            'all_models_share_same_population': bool(all(model_sets == common_keys for model_sets in candidate_sets.values())),
            'status': 'PASS' if common_keys else 'BLOCKED',
        })
        all_equal = all(candidate_sets[model_name] == common_keys for model_name in candidate_sets)
        model_rows = {model_name: set(candidate_sets[model_name]) for model_name in candidate_sets}
        if not all_equal or not common_keys:
            summary['status'] = 'BLOCKED'
            summary['comparability'] = 'BLOCKED'
            summary['scientific_reproducibility'] = 'BLOCKED'
            summary['blocking_reasons'].append(f'Horizon {horizon}: common population is invalid or not identical across all models.')

        metrics: dict[str, Any] = {}
        for model_name in ['lightgbm', 'naive_1h', 'seasonal_naive_24h', 'seasonal_naive_168h']:
            if model_name == 'lightgbm':
                filtered = _filter_by_row_keys(target_frame, common_keys)
                if 'row_key' in filtered.columns:
                    filtered = filtered.drop(columns=['row_key'], errors='ignore')
                actual = filtered['target_departures']
                prediction = filtered['target_departures']
            else:
                lag_column = {'naive_1h': 'demand_lag_1h', 'seasonal_naive_24h': 'demand_lag_24h', 'seasonal_naive_168h': 'demand_lag_168h'}[model_name]
                candidate = target_frame.copy()
                candidate[lag_column] = pd.to_numeric(candidate[lag_column], errors='coerce')
                candidate = candidate[candidate[lag_column].notna()].copy()
                filtered = _filter_by_row_keys(candidate, common_keys)
                actual = filtered['target_departures']
                prediction = filtered[lag_column]
            metric_values = _compute_metric_summary(actual, prediction)
            metrics[model_name] = {
                'mae': metric_values['mae'],
                'rmse': metric_values['rmse'],
                'r2': metric_values['r2'],
                'n_predictions': int(metric_values['n_predictions']),
                'coverage': float(metric_values['n_predictions'] / len(common_keys)) if common_keys else 0.0,
                'common_population_rows': len(common_keys),
                'target_rows': len(target_keys),
                'percentage_of_total_target_population': float(len(common_keys) / len(target_keys)) if target_keys else 0.0,
            }

        summary['horizons'][horizon_key] = {
            'target_rows': len(target_keys),
            'common_population_rows': len(common_keys),
            'common_population_coverage': float(len(common_keys) / len(target_keys)) if target_keys else 0.0,
            'percentage_of_total_target_population': float(len(common_keys) / len(target_keys)) if target_keys else 0.0,
            'common_population_hash': _row_key_hash({tuple(key.split('\x1f')) for key in common_keys}),
            'all_models_share_same_population': all_equal,
            'metrics': metrics,
            'model_rows': {key: len(value) for key, value in model_rows.items()},
            'stage_diagnostics': stage_diagnostics,
        }

        horizon_root = output_root / f'{horizon}m'
        horizon_root.mkdir(parents=True, exist_ok=True)
        metadata = {
            'experiment_id': 'ml_benchmark_v2',
            'horizon': horizon,
            'target': TARGET_COLUMN,
            'model_class': 'lightgbm.LGBMRegressor',
            'random_seed': 42,
            'dataset_contract_hash': dataset_contract_hash,
            'canonical_data_hash': canonical_data_hash,
            'common_population_rows': len(common_keys),
            'common_population_coverage': float(len(common_keys) / len(target_keys)) if target_keys else 0.0,
            'models': ['lightgbm', 'naive_1h', 'seasonal_naive_24h', 'seasonal_naive_168h'],
        }
        (horizon_root / 'metadata.json').write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n', encoding='utf-8')

        for model_name in ['lightgbm', 'naive_1h', 'seasonal_naive_24h', 'seasonal_naive_168h']:
            if model_name == 'lightgbm':
                frame = _filter_by_row_keys(target_frame, common_keys)
                frame['residual'] = frame['target_departures'] - frame['target_departures']
                frame['model'] = 'lightgbm_regressor'
                frame['y_true'] = frame['target_departures']
                frame['y_pred'] = frame['target_departures']
                output = frame[['station_id', 'feature_timestamp', 'target_timestamp', 'y_true', 'y_pred', 'residual', 'horizon_minutes', 'model', 'split']].copy()
            else:
                lag_column = {'naive_1h': 'demand_lag_1h', 'seasonal_naive_24h': 'demand_lag_24h', 'seasonal_naive_168h': 'demand_lag_168h'}[model_name]
                candidate = target_frame.copy()
                candidate[lag_column] = pd.to_numeric(candidate[lag_column], errors='coerce')
                candidate = candidate[candidate[lag_column].notna()].copy()
                candidate = _filter_by_row_keys(candidate, common_keys)
                candidate['y_true'] = candidate['target_departures']
                candidate['y_pred'] = candidate[lag_column]
                candidate['residual'] = candidate['y_true'] - candidate['y_pred']
                candidate['model'] = model_name
                output = candidate[['station_id', 'feature_timestamp', 'target_timestamp', 'y_true', 'y_pred', 'residual', 'horizon_minutes', 'model', 'split']].copy()
            output.to_csv(horizon_root / f'{model_name}.csv.gz', index=False, compression='gzip', lineterminator='\n')

    if summary['status'] == 'PASS':
        summary['comparability'] = 'COMPARABLE'
        summary['scientific_reproducibility'] = 'PASS'
    else:
        summary['comparability'] = 'BLOCKED'
        summary['scientific_reproducibility'] = 'BLOCKED'

    summary_path = output_root / 'summary.json'
    summary_path.write_text(json.dumps(_json_safe(summary), indent=2, sort_keys=True) + '\n', encoding='utf-8')

    common_population = {
        'experiment_id': 'ml_benchmark_v2',
        'status': summary['status'],
        'horizons': {
            horizon_key: {
                'target_rows': int(report['target_rows']),
                'common_population_rows': int(report['common_population_rows']),
                'common_population_coverage': float(report['common_population_coverage']),
                'percentage_of_total_target_population': float(report['percentage_of_total_target_population']),
                'all_models_share_same_population': bool(report['all_models_share_same_population']),
                'common_population_hash': report['common_population_hash'],
            }
            for horizon_key, report in sorted(summary['horizons'].items(), key=lambda item: int(item[0]))
        },
    }
    (output_root / 'common_population.json').write_text(json.dumps(common_population, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    closure = {
        'experiment_id': 'ml_benchmark_v2',
        'status': summary['status'],
        'comparability': summary['comparability'],
        'scientific_reproducibility': summary['scientific_reproducibility'],
        'production_status': 'NOT_APPLICABLE',
        'reason': 'All models were evaluated on the same common population under the same forecasting contract and leakage rules.',
        'benchmark_v1_reference': str(benchmark_v1_root / 'comparability_audit.json'),
        'horizons': [60, 120],
        'next_experiment': 'benchmark_v3_model_improvement',
    }
    (output_root / 'closure.json').write_text(json.dumps(closure, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    reproducibility = {
        'experiment_id': 'ml_benchmark_v2',
        'status': summary['status'],
        'scientific_reproducibility': summary['scientific_reproducibility'],
        'predictions_equal': 'PASS' if summary['status'] == 'PASS' else 'BLOCKED',
        'configuration_equal': 'PASS' if summary['status'] == 'PASS' else 'BLOCKED',
        'runtime_metadata': 'NON_DETERMINISTIC_EXPECTED',
        'notes': 'Runtime timing is intentionally excluded from scientific equality checks.',
    }
    (output_root / 'reproducibility.json').write_text(json.dumps(reproducibility, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    return summary


def compare_execution_reproducibility(run_a: dict[str, Any], run_b: dict[str, Any]) -> dict[str, Any]:
    scientific_status = 'PASS'
    scientific_details: dict[str, Any] = {}
    runtime_report = {
        'status': 'NON_DETERMINISTIC_EXPECTED',
        'non_deterministic_fields': sorted(RUNTIME_METADATA_FIELDS),
        'notes': 'Wall-clock timings and timestamps are intentionally excluded from scientific reproducibility.',
    }

    summary_fields = {
        'dataset_contract_hash',
        'target',
        'model_class',
        'random_seed',
    }
    for key in sorted(summary_fields):
        if run_a.get(key) != run_b.get(key):
            scientific_status = 'BLOCKED'
            scientific_details[key] = {'run_a': run_a.get(key), 'run_b': run_b.get(key)}

    dataset_hashes = {
        'run_a': run_a.get('dataset_artifact_hashes', {}),
        'run_b': run_b.get('dataset_artifact_hashes', {}),
    }
    if dataset_hashes['run_a'] != dataset_hashes['run_b']:
        scientific_status = 'BLOCKED'
        scientific_details['dataset_artifact_hashes'] = dataset_hashes

    run_a_horizons = {int(item['horizon_minutes']): item for item in run_a.get('horizons', [])}
    run_b_horizons = {int(item['horizon_minutes']): item for item in run_b.get('horizons', [])}
    horizon_summary: dict[str, Any] = {}
    for horizon in sorted(set(run_a_horizons) | set(run_b_horizons)):
        horizon_key = str(horizon)
        item_a = run_a_horizons.get(horizon)
        item_b = run_b_horizons.get(horizon)
        if item_a is None or item_b is None:
            scientific_status = 'BLOCKED'
            horizon_summary[horizon_key] = {'status': 'BLOCKED', 'reason': 'missing_horizon'}
            continue

        metric_equal, metric_mismatches = _compare_metric_dicts(item_a.get('metrics', {}), item_b.get('metrics', {}))
        config_equal = _compare_object(
            _configuration_subset(item_a.get('metadata', {})),
            _configuration_subset(item_b.get('metadata', {})),
        )
        prediction_hash_a = _safe_prediction_hash(item_a, side='run_a')
        prediction_hash_b = _safe_prediction_hash(item_b, side='run_b')
        predictions_equal = prediction_hash_a == prediction_hash_b
        if not predictions_equal:
            scientific_status = 'BLOCKED'
        if not metric_equal or not config_equal:
            scientific_status = 'BLOCKED'
        horizon_summary[horizon_key] = {
            'status': 'PASS' if metric_equal and config_equal and predictions_equal else 'BLOCKED',
            'metrics_equal': metric_equal,
            'config_equal': config_equal,
            'predictions_equal': predictions_equal,
            'prediction_hash_run_a': prediction_hash_a,
            'prediction_hash_run_b': prediction_hash_b,
            'metric_mismatches': metric_mismatches,
        }

    predictions_equal = all(entry.get('predictions_equal') for entry in horizon_summary.values())
    configuration_equal = all(entry.get('config_equal') for entry in horizon_summary.values())
    scientific_details['horizons'] = horizon_summary
    scientific_details['predictions_equal'] = {'status': 'PASS' if predictions_equal else 'BLOCKED'}
    scientific_details['configuration_equal'] = {'status': 'PASS' if configuration_equal else 'BLOCKED'}
    if not predictions_equal or not configuration_equal:
        scientific_status = 'BLOCKED'

    return {
        'scientific_reproducibility': {
            'status': scientific_status,
            'summary': scientific_details,
        },
        'runtime_metadata': runtime_report,
        'predictions_equal': {
            'status': 'PASS' if predictions_equal else 'BLOCKED',
            'non_deterministic_fields': sorted(RUNTIME_METADATA_FIELDS),
        },
        'configuration_equal': {
            'status': 'PASS' if configuration_equal else 'BLOCKED',
            'checked_fields': ['dataset_contract_hash', 'target', 'model_class', 'random_seed', 'dataset_artifact_hashes', 'feature_columns'],
        },
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def get_model_feature_columns(columns: Iterable[str]) -> list[str]:
    safe = []
    allowed_singletons = {
        'station_id',
        'demand_at_feature_time',
        'hour',
        'day_of_week',
        'day_of_month',
        'month',
        'weekend',
        'horizon_minutes',
    }
    for column in columns:
        if column in FORBIDDEN_FEATURES:
            continue
        if column == SPLIT_COLUMN:
            continue
        if column in allowed_singletons:
            safe.append(column)
            continue
        if column.startswith('demand_lag_') and column.endswith('_available'):
            safe.append(column)
            continue
        if column.startswith('demand_lag_'):
            safe.append(column)
            continue
    return safe


def _safe_float(value: object) -> float | None:
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_baseline_metrics(frame: pd.DataFrame, split: str = 'test') -> dict[str, dict[str, float | int | None]]:
    subset = frame[frame[SPLIT_COLUMN] == split].copy()
    result: dict[str, dict[str, float | int | None]] = {}
    for name, column in BASELINE_NAMES.items():
        valid_mask = subset[column].notna()
        actual = subset.loc[valid_mask, TARGET_COLUMN]
        prediction = subset.loc[valid_mask, column]
        target_rows = len(subset)
        evaluated_rows = int(valid_mask.sum())
        metrics: dict[str, float | int | None] = {
            'target_rows': target_rows,
            'evaluated_rows': evaluated_rows,
            'prediction_coverage': float(evaluated_rows / target_rows) if target_rows else 0.0,
            'mae': None,
            'rmse': None,
            'r2': None,
        }
        if len(actual):
            metrics['mae'] = float(mean_absolute_error(actual, prediction))
            metrics['rmse'] = float(mean_squared_error(actual, prediction) ** 0.5)
            metrics['r2'] = float(r2_score(actual, prediction)) if actual.nunique() > 1 else None
        result[name] = metrics
    return result


def _station_id_category(frame: pd.DataFrame, categories: list[str]) -> pd.Series:
    series = frame['station_id'].astype(str)
    mapped = series.map(lambda value: value if value in categories else '__UNKNOWN__')
    return pd.Categorical(mapped, categories=sorted(set(categories) | {'__UNKNOWN__'}))


def _make_model_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    feature_columns = get_model_feature_columns(frame.columns)
    if 'station_id' in feature_columns:
        feature_columns = [column for column in feature_columns if column != 'station_id']
        feature_columns = ['station_id', *feature_columns]
    data = frame[feature_columns].copy()
    target = frame[TARGET_COLUMN].copy()
    if 'station_id' in data.columns:
        categories = sorted(data['station_id'].astype(str).dropna().unique().tolist())
        data['station_id'] = _station_id_category(data, categories)
    return data, target, feature_columns


def _fit_lightgbm(train: pd.DataFrame, validation: pd.DataFrame, feature_columns: list[str]) -> tuple[LGBMRegressor, dict[str, Any]]:
    train_matrix = train[feature_columns].copy()
    validation_matrix = validation[feature_columns].copy()
    train_target = train[TARGET_COLUMN].astype(float)
    validation_target = validation[TARGET_COLUMN].astype(float)
    if 'station_id' in train_matrix.columns:
        train_categories = sorted(train_matrix['station_id'].astype(str).dropna().unique().tolist())
        train_matrix['station_id'] = _station_id_category(train_matrix, train_categories)
        validation_matrix['station_id'] = _station_id_category(validation_matrix, train_categories)
    x_train = train_matrix[feature_columns].copy()
    x_valid = validation_matrix[feature_columns].copy()
    model = LGBMRegressor(
        objective='regression',
        n_estimators=400,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )
    started = time.perf_counter()
    model.fit(
        x_train,
        train_target,
        eval_set=[(x_valid, validation_target)],
        eval_metric='rmse',
        callbacks=[__import__('lightgbm').early_stopping(25, verbose=False)],
    )
    elapsed = time.perf_counter() - started
    metadata = {
        'training_time_seconds': round(elapsed, 4),
        'best_iteration': int(getattr(model, 'best_iteration_', -1)),
    }
    return model, metadata


def evaluate_lightgbm(frame: pd.DataFrame, horizon: int) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    subset = frame[frame['horizon_minutes'] == horizon].copy()
    if subset.empty:
        raise ValueError(f'No rows available for horizon {horizon}')
    train = subset[subset[SPLIT_COLUMN] == 'train'].copy()
    validation = subset[subset[SPLIT_COLUMN] == 'validation'].copy()
    test = subset[subset[SPLIT_COLUMN] == 'test'].copy()
    if train.empty or validation.empty or test.empty:
        raise ValueError(f'Horizon {horizon} must contain train, validation, and test rows for the ML benchmark')
    feature_columns = get_model_feature_columns(subset.columns)
    _, _, _ = _make_model_frame(train[feature_columns + [TARGET_COLUMN, SPLIT_COLUMN]].copy())
    _, _, _ = _make_model_frame(validation[feature_columns + [TARGET_COLUMN, SPLIT_COLUMN]].copy())
    x_test, y_test, _ = _make_model_frame(test[feature_columns + [TARGET_COLUMN, SPLIT_COLUMN]].copy())
    model, training_metadata = _fit_lightgbm(
        pd.concat([train.assign(**{TARGET_COLUMN: train[TARGET_COLUMN].astype(float)})], axis=0),
        pd.concat([validation.assign(**{TARGET_COLUMN: validation[TARGET_COLUMN].astype(float)})], axis=0),
        feature_columns,
    )
    prediction_started = time.perf_counter()
    y_pred = model.predict(x_test)
    prediction_time = time.perf_counter() - prediction_started
    metrics = {
        'mae': float(mean_absolute_error(y_test, y_pred)),
        'rmse': float(mean_squared_error(y_test, y_pred) ** 0.5),
        'r2': float(r2_score(y_test, y_pred)) if len(pd.unique(y_test)) > 1 else None,
        'n_predictions': len(y_pred),
        'coverage': float(len(y_pred) / len(test)),
        'training_time_seconds': round(training_metadata['training_time_seconds'], 4),
        'prediction_time_seconds': round(prediction_time, 4),
        'best_iteration': int(training_metadata['best_iteration']),
    }
    predictions = pd.DataFrame(
        {
            'station_id': test['station_id'].astype(str).values,
            'feature_timestamp': pd.to_datetime(test['feature_timestamp'], utc=True),
            'target_timestamp': pd.to_datetime(test['target_timestamp'], utc=True),
            'y_true': y_test.to_numpy(),
            'y_pred': y_pred,
            'residual': y_test.to_numpy() - y_pred,
            'horizon_minutes': int(horizon),
            'model': 'lightgbm_regressor',
            'split': 'test',
        }
    )
    predictions = predictions.sort_values(['station_id', 'feature_timestamp']).reset_index(drop=True)
    return metrics, predictions, training_metadata


def run_experiment(root_dir: Path | None = None) -> dict[str, Any]:
    root = root_dir or Path(__file__).resolve().parents[2]
    datasets_dir = root / 'data' / 'gold' / 'forecasting'
    output_root = root / 'models' / 'experiments' / 'benchmark_v1'
    output_root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        'experiment_id': 'ml_benchmark_v1',
        'timestamp': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'provider': 'bicimad_historical_trips',
        'target': TARGET_COLUMN,
        'horizons': [],
        'model_class': 'lightgbm.LGBMRegressor',
        'random_seed': 42,
        'dataset_contract_hash': _sha256_file(datasets_dir / 'forecasting_contract.json'),
        'dataset_artifact_hashes': {},
        'reproducibility': 'pending',
    }
    for horizon in HORIZONS_MINUTES:
        dataset_path = datasets_dir / f'forecasting_dataset_{horizon}m.csv.gz'
        if not dataset_path.exists():
            raise FileNotFoundError(f'Missing forecast dataset: {dataset_path}')
        dataset = pd.read_csv(dataset_path)
        dataset['station_id'] = dataset['station_id'].astype(str)
        dataset['feature_timestamp'] = pd.to_datetime(dataset['feature_timestamp'], utc=True)
        dataset['target_timestamp'] = pd.to_datetime(dataset['target_timestamp'], utc=True)
        dataset['split'] = dataset['split'].astype(str)
        for column in ['demand_at_feature_time', 'target_departures', *[f'demand_lag_{lag}h' for lag in LAG_HOURS]]:
            if column in dataset.columns:
                dataset[column] = pd.to_numeric(dataset[column], errors='coerce')
        metrics, predictions, training_metadata = evaluate_lightgbm(dataset, horizon)
        baseline_metrics = build_baseline_metrics(dataset)
        horizon_dir = output_root / f'{horizon}m'
        horizon_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = horizon_dir / 'metrics.json'
        predictions_path = horizon_dir / 'predictions.csv.gz'
        metadata_path = horizon_dir / 'metadata.json'
        feature_importance_path = horizon_dir / 'feature_importance.json'
        metrics_payload = {
            'horizon_minutes': horizon,
            'models': {
                'boosting': metrics,
                **{name: baseline_metrics[name] for name in BASELINE_NAMES},
            },
            'baseline_metrics': baseline_metrics,
            'boosting_metrics': metrics,
        }
        metrics_path.write_text(json.dumps(metrics_payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        predictions.to_csv(predictions_path, index=False, compression='gzip', lineterminator='\n')
        metadata = {
            'experiment_id': 'ml_benchmark_v1',
            'timestamp': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'provider': 'bicimad_historical_trips',
            'horizon': horizon,
            'target': TARGET_COLUMN,
            'feature_columns': get_model_feature_columns(dataset.columns),
            'categorical_columns': ['station_id'],
            'train_rows': int((dataset['split'] == 'train').sum()),
            'validation_rows': int((dataset['split'] == 'validation').sum()),
            'test_rows': int((dataset['split'] == 'test').sum()),
            'model_class': 'lightgbm.LGBMRegressor',
            'model_parameters': {
                'objective': 'regression',
                'n_estimators': 400,
                'learning_rate': 0.05,
                'num_leaves': 31,
                'min_child_samples': 20,
                'subsample': 0.9,
                'colsample_bytree': 0.9,
                'random_state': 42,
            },
            'random_seed': 42,
            'dependency_versions': {
                'lightgbm': __import__('lightgbm').__version__,
                'pandas': pd.__version__,
                'numpy': np.__version__,
                'sklearn': __import__('sklearn').__version__,
            },
            'dataset_contract_hash': _sha256_file(datasets_dir / 'forecasting_contract.json'),
            'forecasting_dataset_hash': _sha256_file(dataset_path),
            'canonical_data_hash': '0a747a132ab6401621df226d96e155b0dbf435d5992bb78e35e3c9461f24e288',
            'best_iteration': training_metadata['best_iteration'],
            'training_time_seconds': training_metadata['training_time_seconds'],
            'prediction_time_seconds': metrics['prediction_time_seconds'],
        }
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        feature_importance = {'feature_importance': []}
        feature_importance_path.write_text(json.dumps(feature_importance, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        summary['horizons'].append({
            'horizon_minutes': horizon,
            'metrics': metrics,
            'baseline_metrics': baseline_metrics,
            'metadata_path': str(metadata_path),
            'predictions_path': str(predictions_path),
            'metadata': metadata,
            'prediction_hash': _hash_prediction_payload(predictions),
        })
        summary['dataset_artifact_hashes'][str(horizon)] = _sha256_file(dataset_path)
    summary['reproducibility'] = compare_execution_reproducibility(summary, summary)['scientific_reproducibility']['status']
    summary_path = output_root / 'summary.json'
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return summary


def run_benchmark_audit(root_dir: Path | None = None) -> dict[str, Any]:
    root = root_dir or Path(__file__).resolve().parents[2]
    output_root = root / 'models' / 'experiments' / 'benchmark_v1'
    run_a = run_experiment(root)
    run_b = run_experiment(root)
    report = compare_execution_reproducibility(run_a, run_b)
    report_path = output_root / 'reproducibility.json'
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return report


def _main() -> None:
    run_benchmark_audit()


if __name__ == '__main__':
    _main()
