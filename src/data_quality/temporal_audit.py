from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

STATES = ('OBSERVED', 'ZERO_DEMAND', 'INACTIVE', 'MISSING_OR_UNKNOWN')


def _percentile_summary(values: pd.Series) -> dict[str, float | int | None]:
    if values.empty:
        return {key: None for key in ('min', 'p25', 'median', 'p75', 'p90', 'max')}
    quantiles = values.quantile([0.25, 0.5, 0.75, 0.9])
    return {
        'min': int(values.min()),
        'p25': float(quantiles.loc[0.25]),
        'median': float(quantiles.loc[0.5]),
        'p75': float(quantiles.loc[0.75]),
        'p90': float(quantiles.loc[0.9]),
        'max': int(values.max()),
    }


def _delta_stats(timestamps: pd.Series) -> dict[str, Any]:
    values = pd.Series(pd.to_datetime(timestamps, utc=True).dropna().sort_values().unique())
    deltas = values.diff().dropna().dt.total_seconds().div(3600)
    if deltas.empty:
        return {
            'unique_timestamp_count': len(values), 'most_common_delta_hours': None,
            'median_delta_hours': None, 'minimum_delta_hours': None, 'maximum_delta_hours': None,
            'consecutive_one_hour_intervals': 0, 'gaps_gt_1h': 0, 'gaps_gt_6h': 0,
            'gaps_gt_24h': 0, 'gaps_gt_48h': 0, 'gaps_gt_7d': 0,
        }
    return {
        'unique_timestamp_count': len(values),
        'most_common_delta_hours': float(deltas.mode().iloc[0]),
        'median_delta_hours': float(deltas.median()),
        'minimum_delta_hours': float(deltas.min()),
        'maximum_delta_hours': float(deltas.max()),
        'consecutive_one_hour_intervals': int((deltas == 1).sum()),
        'gaps_gt_1h': int((deltas > 1).sum()),
        'gaps_gt_6h': int((deltas > 6).sum()),
        'gaps_gt_24h': int((deltas > 24).sum()),
        'gaps_gt_48h': int((deltas > 48).sum()),
        'gaps_gt_7d': int((deltas > 168).sum()),
    }


def station_coverage(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    working = frame[['station_id', 'observed_at', 'demand', 'total_activity']].copy()
    working['observed_at'] = pd.to_datetime(working['observed_at'], utc=True)
    working = working.sort_values(['station_id', 'observed_at'])
    grouped = working.groupby('station_id', sort=True)
    rows: list[dict[str, Any]] = []
    for station_id, station_frame in grouped:
        timestamps = station_frame['observed_at']
        deltas = timestamps.diff().dropna().dt.total_seconds().div(3600)
        expected = int((timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds() // 3600) + 1
        missing_intervals = max(expected - len(timestamps), 0)
        longest_missing = float(deltas.max()) if not deltas.empty else 0.0
        rows.append({
            'station_id': str(station_id),
            'first_observed_at': timestamps.iloc[0].strftime('%Y-%m-%dT%H:%M:%SZ'),
            'last_observed_at': timestamps.iloc[-1].strftime('%Y-%m-%dT%H:%M:%SZ'),
            'observation_count': len(timestamps),
            'expected_hourly_observations': expected,
            'observed_coverage_ratio': float(len(timestamps) / expected) if expected else 1.0,
            'missing_interval_count': missing_intervals,
            'longest_missing_interval_hours': longest_missing,
            'zero_demand_count': int((station_frame['total_activity'] == 0).sum()),
            'zero_demand_ratio': float((station_frame['total_activity'] == 0).mean()),
        })
    coverage = pd.DataFrame(rows)
    return coverage, {
        'station_count': len(coverage),
        'distributions': {
            key: _percentile_summary(coverage[key])
            for key in ('observation_count', 'expected_hourly_observations', 'missing_interval_count')
        },
        'coverage_ratio_distribution': {
            key: float(value) for key, value in coverage['observed_coverage_ratio'].describe(
                percentiles=[0.25, 0.5, 0.75, 0.9]
            ).to_dict().items() if key in ('min', '25%', '50%', '75%', '90%', 'max')
        },
        'threshold_counts': {
            '<10_observations': int((coverage['observation_count'] < 10).sum()),
            '<24_observations': int((coverage['observation_count'] < 24).sum()),
            '<7_days': int(coverage['expected_hourly_observations'].lt(7 * 24).sum()),
            '<30_days': int(coverage['expected_hourly_observations'].lt(30 * 24).sum()),
            '<90_days': int(coverage['expected_hourly_observations'].lt(90 * 24).sum()),
            '<90_percent_hourly_coverage': int((coverage['observed_coverage_ratio'] < 0.9).sum()),
            '<50_percent_hourly_coverage': int((coverage['observed_coverage_ratio'] < 0.5).sum()),
        },
    }


def direct_horizon_support(frame: pd.DataFrame, horizon_minutes: int) -> dict[str, Any]:
    working = frame[['station_id', 'observed_at']].copy()
    working['observed_at'] = pd.to_datetime(working['observed_at'], utc=True)
    working = working.sort_values(['station_id', 'observed_at'])
    target = working.assign(target_at=working['observed_at'] + pd.to_timedelta(horizon_minutes, unit='min'))
    matches = target.merge(working, left_on=['station_id', 'target_at'], right_on=['station_id', 'observed_at'])
    return {
        'horizon_minutes': horizon_minutes,
        'direct_observed_pairs': len(matches),
        'status': 'PASS' if len(matches) else 'BLOCKED',
        'interpretation': 'Direct timestamp pairs exist, but missing station-time rows remain unknown; no interpolation was used.' if len(matches) else 'No direct source timestamp pairs support this horizon.',
    }


def classify_missingness(
    frame: pd.DataFrame,
    inactive_station_ids: Iterable[str],
    missing_expected_count: int = 0,
) -> dict[str, Any]:
    inactive = {str(value) for value in inactive_station_ids}
    zero_demand = int((frame['total_activity'] == 0).sum())
    inactive_observed = int(frame['station_id'].astype(str).isin(inactive).sum())
    observed = len(frame) - zero_demand - inactive_observed
    total_classified = observed + zero_demand + inactive_observed + missing_expected_count
    return {
        'counts': {
            'OBSERVED': observed,
            'ZERO_DEMAND': zero_demand,
            'INACTIVE': inactive_observed,
            'MISSING_OR_UNKNOWN': missing_expected_count,
        },
        'percentages_of_audited_station_hour_slots': {
            'OBSERVED': float(observed / total_classified * 100) if total_classified else 0.0,
            'ZERO_DEMAND': float(zero_demand / total_classified * 100) if total_classified else 0.0,
            'INACTIVE': float(inactive_observed / total_classified * 100) if total_classified else 0.0,
            'MISSING_OR_UNKNOWN': float(missing_expected_count / total_classified * 100) if total_classified else 0.0,
        },
        'rules': {
            'OBSERVED': 'A canonical demand row exists.',
            'ZERO_DEMAND': 'total_activity equals zero in a source-backed canonical row.',
            'INACTIVE': 'An official station snapshot explicitly reports an inactive/status value for the station; no such row is inferred from demand absence.',
            'MISSING_OR_UNKNOWN': 'Absent station-time combinations are not imputed or counted as zero; they are unknown unless an explicit availability source supports a stronger state.',
        },
        'source_evidence': ['canonical Gold demand rows', 'official historical station snapshots when explicit activate/status is present'],
        'unsupported_distinctions': ['Demand absence cannot distinguish inactive from missing.', 'The available monthly snapshots do not establish station status for every hourly gap.'],
        'limitations': ['The row-state counts cover observed canonical rows; unobserved station-hour combinations are not materialized as synthetic rows and therefore remain MISSING_OR_UNKNOWN by rule, not as fabricated rows.'],
    }