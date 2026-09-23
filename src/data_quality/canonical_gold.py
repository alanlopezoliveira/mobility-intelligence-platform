from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

CANONICAL_COLUMNS = [
    'provider', 'station_id', 'observed_at', 'departures', 'arrivals',
    'total_activity', 'net_flow', 'demand', 'source_year',
]
REQUIRED_COLUMNS = {'timestamp', 'station_id', 'departures', 'arrivals', 'source_year'}


def normalize_station_id(value: object) -> str:
    text = str(value).strip()
    if not text or text.lower() == 'nan':
        return ''
    if text.isdigit():
        return str(int(text))
    return text


def _canonicalize_frame(frame: pd.DataFrame, provider: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f'Canonical Gold input is missing required columns: {missing}')

    working = frame.copy()
    working['observed_at'] = pd.to_datetime(working['timestamp'], errors='coerce', utc=True)
    working['station_id'] = working['station_id'].map(normalize_station_id)
    working['provider'] = provider
    numeric_columns = ['departures', 'arrivals', 'source_year']
    for column in numeric_columns:
        working[column] = pd.to_numeric(working[column], errors='coerce')
    invalid_mask = working[['observed_at', 'station_id'] + numeric_columns].isna().any(axis=1)
    invalid_rows = int(invalid_mask.sum())
    if invalid_rows:
        offending = working.loc[
            invalid_mask,
            ['timestamp', 'station_id', 'departures', 'arrivals', 'source_year'],
        ].head(10).to_dict(orient='records')
        raise ValueError(f'Invalid canonical input rows ({invalid_rows}): {offending}')

    if ((working['departures'] < 0) | (working['arrivals'] < 0)).any():
        offending = working.loc[
            (working['departures'] < 0) | (working['arrivals'] < 0),
            ['timestamp', 'station_id', 'departures', 'arrivals'],
        ].head(10).to_dict(orient='records')
        raise ValueError(f'Negative directional contribution: {offending}')

    key_columns = ['provider', 'station_id', 'observed_at']
    duplicate_mask = working.duplicated(key_columns, keep=False)
    duplicate_before = int(duplicate_mask.sum())
    duplicate_key_count_before = int(working.loc[duplicate_mask, key_columns].drop_duplicates().shape[0])
    grouped = (
        working.groupby(key_columns, as_index=False, sort=True)
        .agg(departures=('departures', 'sum'), arrivals=('arrivals', 'sum'), source_year=('source_year', 'min'))
    )
    grouped['departures'] = grouped['departures'].astype('int64')
    grouped['arrivals'] = grouped['arrivals'].astype('int64')
    grouped['source_year'] = grouped['source_year'].astype('int64')
    grouped['total_activity'] = grouped['departures'] + grouped['arrivals']
    grouped['net_flow'] = grouped['arrivals'] - grouped['departures']
    grouped['demand'] = grouped['departures']
    grouped['observed_at'] = grouped['observed_at'].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    grouped = grouped[CANONICAL_COLUMNS].sort_values(key_columns).reset_index(drop=True)

    duplicate_after = int(grouped.duplicated(key_columns).sum())
    if duplicate_after:
        raise ValueError('Canonical Gold contains duplicate keys after aggregation')
    if grouped[['provider', 'station_id', 'observed_at']].isna().any().any():
        raise ValueError('Canonical Gold contains null canonical keys')
    if (grouped[['departures', 'arrivals']] < 0).any().any():
        raise ValueError('Canonical Gold contains negative directional totals')
    if not grouped['total_activity'].eq(grouped['departures'] + grouped['arrivals']).all():
        raise ValueError('Canonical Gold violates total_activity identity')
    if not grouped['net_flow'].eq(grouped['arrivals'] - grouped['departures']).all():
        raise ValueError('Canonical Gold violates net_flow identity')
    if not grouped['demand'].eq(grouped['departures']).all():
        raise ValueError('Canonical Gold demand is inconsistent: demand must equal departures')

    return grouped, {
        'source_row_count': len(frame),
        'canonical_row_count': len(grouped),
        'duplicate_key_row_count_before_aggregation': duplicate_before,
        'duplicate_key_count_before_aggregation': duplicate_key_count_before,
        'duplicate_key_count_after_aggregation': duplicate_after,
        'invalid_row_count': invalid_rows,
        'aggregation': 'sum directional contribution rows by provider, normalized station_id, observed_at',
        'demand_definition': 'departures',
    }


def _frame_hash(frame: pd.DataFrame) -> str:
    payload = frame.to_csv(index=False, lineterminator='\n').encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def rebuild_canonical_gold(input_path: Path, output_path: Path, report_path: Path, provider: str = 'bicimad') -> dict[str, Any]:
    source = pd.read_csv(input_path, dtype={'station_id': 'string'})
    canonical, stats = _canonicalize_frame(source, provider)
    report: dict[str, Any] = {
        **stats,
        'provider': provider,
        'distinct_station_count': int(canonical['station_id'].nunique()),
        'distinct_timestamp_count': int(canonical['observed_at'].nunique()),
        'min_timestamp': canonical['observed_at'].min(),
        'max_timestamp': canonical['observed_at'].max(),
        'total_departures': int(canonical['departures'].sum()),
        'total_arrivals': int(canonical['arrivals'].sum()),
        'total_activity': int(canonical['total_activity'].sum()),
        'total_net_flow': int(canonical['net_flow'].sum()),
        'total_demand': int(canonical['demand'].sum()),
        'deterministic_sha256': _frame_hash(canonical),
        'validation_status': 'PASS',
    }
    known_keys = [
        ('225', '2021-10-30T17:00:00Z'),
        ('257', '2021-10-29T16:00:00Z'),
        ('257', '2021-10-30T17:00:00Z'),
    ]
    report['known_duplicate_examples'] = [
        {
            'station_id': station_id,
            'observed_at': observed_at,
            'canonical_row': canonical.loc[
                (canonical['station_id'] == station_id) & (canonical['observed_at'] == observed_at)
            ].to_dict(orient='records'),
        }
        for station_id, observed_at in known_keys
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=output_path.parent, suffix='.csv', encoding='utf-8', newline='') as output_temp:
        canonical.to_csv(output_temp, index=False, lineterminator='\n')
        output_temp_path = Path(output_temp.name)
    with tempfile.NamedTemporaryFile('w', delete=False, dir=report_path.parent, suffix='.json', encoding='utf-8') as report_temp:
        json.dump(report, report_temp, indent=2, sort_keys=True)
        report_temp.write('\n')
        report_temp_path = Path(report_temp.name)
    os.replace(output_temp_path, output_path)
    os.replace(report_temp_path, report_path)
    return report