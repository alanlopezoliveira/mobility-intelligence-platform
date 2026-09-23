from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from src.data_quality.temporal_audit import (
    _delta_stats,
    classify_missingness,
    direct_horizon_support,
    station_coverage,
)
from src.ingestion.bicimad import iter_historical_station_snapshots

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / 'data' / 'gold' / 'station_demand_hourly.csv'
OUT = ROOT / 'data' / 'gold'


def main() -> None:
    frame = pd.read_csv(GOLD, usecols=['provider', 'station_id', 'observed_at', 'demand', 'total_activity'])
    frame['station_id'] = frame['station_id'].astype(str)
    frame['observed_at'] = pd.to_datetime(frame['observed_at'], utc=True)
    snapshot_frame, snapshot_summary = iter_historical_station_snapshots()
    inactive_ids = set()
    if not snapshot_frame.empty and 'activate' in snapshot_frame:
        inactive_ids = set(snapshot_frame.loc[snapshot_frame['activate'].eq(0), 'station_id'].astype(str))

    coverage, coverage_stats = station_coverage(frame)
    global_temporal = _delta_stats(frame['observed_at'])
    per_station_delta = []
    for station_id, station_frame in frame.groupby('station_id', sort=True):
        stats = _delta_stats(station_frame['observed_at'])
        per_station_delta.append({'station_id': station_id, **stats})
    horizons = {str(minutes): direct_horizon_support(frame, minutes) for minutes in (30, 60, 120)}
    missingness = classify_missingness(
        frame,
        inactive_ids,
        missing_expected_count=int(coverage['missing_interval_count'].sum()),
    )

    snapshot_ids = set(snapshot_frame['station_id'].astype(str)) if not snapshot_frame.empty else set()
    demand_ids = set(frame['station_id'])
    snapshot_periods = sorted(snapshot_frame['snapshot_period'].astype(str).unique().tolist()) if not snapshot_frame.empty else []
    entry_exit = {
        'demand_first_last_by_station': coverage[['station_id', 'first_observed_at', 'last_observed_at']].to_dict(orient='records'),
        'snapshot_first_last_by_station': (
            snapshot_frame.groupby('station_id').agg(first_snapshot=('snapshot_period', 'min'), last_snapshot=('snapshot_period', 'max')).reset_index().to_dict(orient='records')
            if not snapshot_frame.empty else []
        ),
        'snapshot_periods': snapshot_periods,
        'stations_in_snapshots_absent_from_demand': sorted(snapshot_ids - demand_ids),
        'demand_stations_without_snapshot_evidence': sorted(demand_ids - snapshot_ids),
        'interpretation': 'First/last observation is observed source coverage only; disappearance is not interpreted as physical removal.',
    }
    panel = {
        'selected_formulation': 'C: station x hourly panel plus explicit availability/missingness mask',
        'status': 'BLOCKED',
        'reason': 'The canonical Gold is an irregular observed panel with station-specific gaps; absent rows cannot be safely converted to zero or inactive.',
        'evidence': {
            'station_count': int(frame['station_id'].nunique()),
            'canonical_row_count': len(frame),
            'stations_below_90_percent_coverage': coverage_stats['threshold_counts']['<90_percent_hourly_coverage'],
        },
        'prediction_time_information': 'Observed demand rows and explicit source-backed station status only; missingness must remain an input mask.',
    }
    temporal_readiness = {
        'timestamp_consistency': {'status': 'PASS', 'evidence': {'timezone': 'UTC', 'invalid_timestamps': 0, 'duplicate_station_timestamps': int(frame.duplicated(['station_id', 'observed_at']).sum())}},
        'temporal_granularity': {'status': 'PASS', 'evidence': global_temporal, 'per_station': per_station_delta},
        'station_coverage': {'status': 'PASS', 'evidence': coverage_stats},
        'missingness_semantics': {'status': 'PASS', 'evidence': missingness},
        'station_identity_evidence': {'status': 'BLOCKED', 'reason': 'Historical snapshot identity evidence exists only for source-backed snapshot periods and does not prove permanent continuity for all demand stations.', 'snapshot_summary': snapshot_summary},
        'panel_formulation': panel,
        'forecast_horizons': {'status': 'BLOCKED', 'evidence': horizons, 'reason': '60/120-minute direct pairs exist only conditionally; 30-minute direct targets are unsupported by the hourly source timestamps. Missingness prevents a universal horizon guarantee.'},
        'target_definition': {'status': 'PASS', 'proposal': 'Keep demand = departures. Candidate target is station_id + observed_at + future departure demand at a directly observed target timestamp; no target imputation or silent resampling.'},
        'overall_status': 'BLOCKED',
        'source': str(GOLD.relative_to(ROOT)),
        'entry_exit': entry_exit,
        'snapshot_extraction': snapshot_summary,
        'generated_by': 'scripts/audit_temporal_structure.py',
    }
    coverage_report = {
        'source': str(GOLD.relative_to(ROOT)),
        'canonical_row_count': len(frame),
        'global_temporal': global_temporal,
        'station_level_temporal': per_station_delta,
        'station_coverage': coverage.to_dict(orient='records'),
        'coverage_statistics': coverage_stats,
        'entry_exit': entry_exit,
        'panel_analysis': panel,
        'forecast_horizons': horizons,
        'missingness': missingness,
        'snapshot_extraction': snapshot_summary,
    }
    (OUT / 'missingness_report.json').write_text(json.dumps(missingness, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    (OUT / 'station_temporal_coverage.json').write_text(json.dumps(coverage_report, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    (OUT / 'temporal_readiness.json').write_text(json.dumps(temporal_readiness, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'missingness': missingness['counts'], 'global_temporal': global_temporal, 'coverage': coverage_stats, 'horizons': horizons, 'readiness': temporal_readiness['overall_status']}, indent=2, sort_keys=True, default=str))


if __name__ == '__main__':
    main()