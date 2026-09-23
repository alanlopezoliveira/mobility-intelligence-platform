from __future__ import annotations

import csv
import gzip
import hashlib
import os
import tempfile
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

from src.ml.canonical_demand_store import AUTHORITATIVE_GOLD_HASH, SOURCE_NAME

LAGS_HOURS = (1, 2, 3, 6, 12, 24, 48, 168)
DATASET_COLUMNS = [
    'station_id', 'feature_timestamp', 'demand_at_feature_time', 'target_timestamp',
    'target_departures', 'demand_lag_1h', 'demand_lag_1h_available', 'demand_lag_2h',
    'demand_lag_2h_available', 'demand_lag_3h', 'demand_lag_3h_available', 'demand_lag_6h',
    'demand_lag_6h_available', 'demand_lag_12h', 'demand_lag_12h_available', 'demand_lag_24h',
    'demand_lag_24h_available', 'demand_lag_48h', 'demand_lag_48h_available', 'demand_lag_168h',
    'demand_lag_168h_available', 'hour', 'day_of_week', 'day_of_month', 'month', 'weekend',
    'horizon_minutes', 'split',
]


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _canonical_value(value: object) -> str:
    if value is None:
        return ''
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, bool):
        return str(value)
    return str(value)


def _split_boundaries(connection: psycopg.Connection) -> dict[str, datetime]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT observed_at
            FROM (
                SELECT DISTINCT observed_at
                FROM demand_observations
                WHERE source_name = %s
                ORDER BY observed_at
            ) timestamps
            """,
            (SOURCE_NAME,),
        )
        timestamps = [row[0] for row in cursor.fetchall()]
    if len(timestamps) < 3:
        raise RuntimeError('At least three distinct canonical timestamps are required')
    train_end = timestamps[max(0, int(len(timestamps) * 0.70) - 1)]
    validation_end = timestamps[max(1, int(len(timestamps) * 0.85) - 1)]
    return {
        'train_start': timestamps[0],
        'train_end': train_end,
        'validation_start': timestamps[timestamps.index(train_end) + 1],
        'validation_end': validation_end,
        'test_start': timestamps[timestamps.index(validation_end) + 1],
        'test_end': timestamps[-1],
    }


def _query(horizon: int, boundaries: dict[str, datetime]) -> tuple[str, tuple[object, ...]]:
    lag_joins = []
    select_values = []
    for lag in LAGS_HOURS:
        alias = f'lag_{lag}'
        lag_joins.append(
            f'''LEFT JOIN demand_observations {alias}
                ON {alias}.source_name = %s
               AND {alias}.station_id = feature.station_id
               AND {alias}.observed_at = feature.observed_at - INTERVAL '{lag} hours' '''
        )
        select_values.extend([f'{alias}.demand', f'{alias}.demand IS NOT NULL'])
    select_sql = ', '.join(select_values)
    sql = f'''
        SELECT feature.station_id, feature.observed_at, feature.demand,
               feature.observed_at + INTERVAL '{horizon} minutes', target.demand,
               {select_sql}
        FROM demand_observations feature
        JOIN demand_observations target
          ON target.source_name = %s
         AND target.station_id = feature.station_id
         AND target.observed_at = feature.observed_at + INTERVAL '{horizon} minutes'
        {' '.join(lag_joins)}
        WHERE feature.source_name = %s
          AND CASE
                WHEN feature.observed_at <= %s THEN target.observed_at <= %s
                WHEN feature.observed_at <= %s THEN target.observed_at <= %s
                ELSE target.observed_at <= %s
              END
        ORDER BY feature.station_id, feature.observed_at
    '''
    params: list[object] = [SOURCE_NAME]
    params.extend([SOURCE_NAME] * len(LAGS_HOURS))
    params.extend([
        SOURCE_NAME, boundaries['train_end'], boundaries['train_end'],
        boundaries['validation_end'], boundaries['validation_end'], boundaries['test_end'],
    ])
    return sql, tuple(params)


def _split(timestamp: datetime, boundaries: dict[str, datetime]) -> str:
    if timestamp <= boundaries['train_end']:
        return 'train'
    if timestamp <= boundaries['validation_end']:
        return 'validation'
    return 'test'


def _row(values: tuple[object, ...], horizon: int, boundaries: dict[str, datetime]) -> list[object]:
    station_id, feature_timestamp, feature_demand, target_timestamp, target_demand, *lags = values
    result: list[object] = [station_id, feature_timestamp, feature_demand, target_timestamp, target_demand]
    for index in range(0, len(lags), 2):
        result.extend([lags[index], lags[index + 1]])
    result.extend([
        feature_timestamp.hour, feature_timestamp.weekday(), feature_timestamp.day,
        feature_timestamp.month, feature_timestamp.weekday() in (5, 6), horizon,
        _split(feature_timestamp, boundaries),
    ])
    return result


def _stream_artifact(
    connection: psycopg.Connection,
    horizon: int,
    path: Path,
    boundaries: dict[str, datetime],
) -> dict[str, Any]:
    sql, params = _query(horizon, boundaries)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    digest = hashlib.sha256()
    counts: dict[str, int] = defaultdict(int)
    stations: set[str] = set()
    lag_available: dict[str, int] = defaultdict(int)
    started = time.perf_counter()
    with tempfile.NamedTemporaryFile('wb', delete=False, dir=path.parent, suffix='.csv.gz') as raw:
        temporary = Path(raw.name)
    try:
        with gzip.open(temporary, 'wt', newline='', encoding='utf-8') as output:
            writer = csv.writer(output, lineterminator='\n')
            writer.writerow(DATASET_COLUMNS)
            with connection.cursor(name=f'forecast_{horizon}') as cursor:
                cursor.execute(sql, params)
                while rows := cursor.fetchmany(20_000):
                    for values in rows:
                        row = _row(values, horizon, boundaries)
                        writer.writerow(row)
                        serialized = '|'.join(_canonical_value(value) for value in row)
                        digest.update(serialized.encode('utf-8'))
                        digest.update(b'\n')
                        counts[str(row[-1])] += 1
                        stations.add(str(row[0]))
                        for index, lag in enumerate(LAGS_HOURS):
                            if row[6 + index * 2 + 1]:
                                lag_available[f'demand_lag_{lag}h_available'] += 1
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return {
        'horizon_minutes': horizon,
        'source_gold_hash': AUTHORITATIVE_GOLD_HASH,
        'examples_generated': sum(counts.values()),
        'rows_after_split_boundary_purge': sum(counts.values()),
        'split_counts': dict(counts),
        'station_count': len(stations),
        'lag_availability_counts': dict(lag_available),
        'split_boundaries': {key: _utc_text(value) for key, value in boundaries.items()},
        'artifact_size_bytes': path.stat().st_size,
        'generation_duration_seconds': round(time.perf_counter() - started, 3),
        'dataset_sha256': digest.hexdigest(),
        'dataset_artifact': str(path),
    }


def build_from_database(database_url: str, output: Path, skip_existing: bool = False) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    reports: dict[str, Any] = {}
    with psycopg.connect(database_url) as connection:
        boundaries = _split_boundaries(connection)
        for horizon in (60, 120):
            path = output / f'forecasting_dataset_{horizon}m.csv.gz'
            if skip_existing and path.exists() and path.stat().st_size > 0:
                reports[str(horizon)] = {'horizon_minutes': horizon, 'dataset_artifact': str(path), 'reused': True}
                continue
            reports[str(horizon)] = _stream_artifact(connection, horizon, path, boundaries)
    return {'reports': reports, 'split_boundaries': {key: _utc_text(value) for key, value in boundaries.items()}}


def validate_artifact(
    connection: psycopg.Connection,
    path: Path,
    horizon: int,
    source_gold_hash: str = AUTHORITATIVE_GOLD_HASH,
) -> dict[str, Any]:
    started = time.perf_counter()
    digest = hashlib.sha256()
    violations: dict[str, int] = defaultdict(int)
    split_counts: dict[str, int] = defaultdict(int)
    lag_available: dict[str, int] = defaultdict(int)
    stations: set[str] = set()
    row_count = 0
    baseline: dict[str, dict[str, float]] = {
        name: {'target_rows': 0, 'evaluated_rows': 0, 'abs_error': 0.0, 'squared_error': 0.0,
               'actual_sum': 0.0, 'actual_squared_sum': 0.0}
        for name in ('naive', 'seasonal_naive_24h', 'seasonal_naive_168h')
    }
    with gzip.open(path, 'rt', newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != DATASET_COLUMNS:
            violations['schema'] += 1
        while rows := [next(reader, None) for _ in range(5000)]:
            rows = [row for row in rows if row is not None]
            if not rows:
                break
            keys: list[tuple[str, datetime]] = []
            parsed: list[tuple[dict[str, str], datetime, datetime]] = []
            for row in rows:
                feature_timestamp = datetime.fromisoformat(row['feature_timestamp'])
                target_timestamp = datetime.fromisoformat(row['target_timestamp'])
                parsed.append((row, feature_timestamp, target_timestamp))
                station_id = row['station_id']
                keys.append((station_id, target_timestamp))
                for lag in LAGS_HOURS:
                    keys.append((station_id, feature_timestamp - timedelta(hours=lag)))
            unique_keys = list(dict.fromkeys(keys))
            values = [(station_id, timestamp) for station_id, timestamp in unique_keys]
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT station_id, observed_at, demand
                    FROM demand_observations
                    WHERE source_name = %s
                      AND (station_id, observed_at) IN
                          (SELECT * FROM UNNEST(%s::text[], %s::timestamptz[]))
                    """,
                    (SOURCE_NAME, [key[0] for key in values], [key[1] for key in values]),
                )
                lookup = {(str(station_id), observed_at): float(demand) for station_id, observed_at, demand in cursor.fetchall()}
            for row, feature_timestamp, target_timestamp in parsed:
                row_count += 1
                station_id = row['station_id']
                stations.add(station_id)
                split = row['split']
                split_counts[split] += 1
                digest.update('|'.join(row.get(column, '') or '' for column in DATASET_COLUMNS).encode('utf-8'))
                digest.update(b'\n')
                if int(row['horizon_minutes']) != horizon:
                    violations['horizon'] += 1
                if target_timestamp != feature_timestamp + timedelta(minutes=horizon):
                    violations['target_timestamp'] += 1
                if target_timestamp <= feature_timestamp:
                    violations['target_after_feature'] += 1
                target = lookup.get((station_id, target_timestamp))
                if target is None:
                    violations['target_missing'] += 1
                elif float(row['target_departures']) != target:
                    violations['target_mismatch'] += 1
                for lag in LAGS_HOURS:
                    value_column = f'demand_lag_{lag}h'
                    available_column = f'{value_column}_available'
                    expected = lookup.get((station_id, feature_timestamp - timedelta(hours=lag)))
                    available = row[available_column] == 'True'
                    actual = None if row[value_column] == '' else float(row[value_column])
                    if expected is None:
                        if available or actual is not None:
                            violations[f'{value_column}_availability'] += 1
                    else:
                        lag_available[available_column] += 1
                        if not available or actual != expected:
                            violations[f'{value_column}_mismatch'] += 1
                if split == 'test':
                    for name, column in {
                        'naive': 'demand_lag_1h',
                        'seasonal_naive_24h': 'demand_lag_24h',
                        'seasonal_naive_168h': 'demand_lag_168h',
                    }.items():
                        item = baseline[name]
                        item['target_rows'] += 1
                        if row[column] != '':
                            actual = float(row['target_departures'])
                            prediction = float(row[column])
                            error = actual - prediction
                            item['evaluated_rows'] += 1
                            item['abs_error'] += abs(error)
                            item['squared_error'] += error * error
                            item['actual_sum'] += actual
                            item['actual_squared_sum'] += actual * actual
    baseline_output: dict[str, Any] = {}
    for name, item in baseline.items():
        evaluated = int(item['evaluated_rows'])
        sst = item['actual_squared_sum'] - item['actual_sum'] ** 2 / evaluated if evaluated else 0.0
        baseline_output[name] = {
            'target_rows': int(item['target_rows']),
            'evaluated_rows': evaluated,
            'prediction_coverage': evaluated / item['target_rows'] if item['target_rows'] else 0.0,
            'mae': item['abs_error'] / evaluated if evaluated else None,
            'rmse': (item['squared_error'] / evaluated) ** 0.5 if evaluated else None,
            'r2': 1.0 - item['squared_error'] / sst if sst else None,
        }
    return {
        'horizon': horizon,
        'rows_checked': row_count,
        'station_count': len(stations),
        'split_counts': dict(split_counts),
        'lag_availability_counts': dict(lag_available),
        'violations_by_rule': dict(violations),
        'total_violations': sum(violations.values()),
        'status': 'PASS' if not violations else 'BLOCKED',
        'canonical_hash': digest.hexdigest(),
        'baseline_report': {'split': 'test', 'horizons': {str(horizon): baseline_output}},
        'validation_duration_seconds': round(time.perf_counter() - started, 3),
        'source_gold_hash': source_gold_hash,
    }
