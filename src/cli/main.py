from __future__ import annotations

import argparse
import io
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pandas as pd
import requests
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from src.config.settings import load_provider_config
from src.db.database import SessionLocal
from src.db.models import Station
from src.ingestion.bicimad import ParseStats, aggregate_trips, iter_historical_trips, persist_demand

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUEST_TIMEOUT = 30


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _normalize_column_name(name: object) -> str:
    return str(name).replace('\ufeff', '').strip().lower()


def _coerce_numeric(series: pd.Series) -> pd.Series:
    numeric = series.map(lambda value: str(value).replace('.', '').replace(',', '.').strip() if isinstance(value, str) else value)
    numeric = pd.to_numeric(numeric, errors='coerce')
    return numeric


def _validate_historical_demand(demand: pd.DataFrame) -> None:
    required = {'timestamp', 'station_id', 'departures', 'arrivals', 'total_activity', 'net_flow', 'demand', 'source_year'}
    missing = required - set(demand.columns)
    if missing:
        raise ValueError(f'Historical demand is missing required columns: {sorted(missing)}')
    if demand.empty:
        raise ValueError('Historical demand output is empty')
    timestamps = pd.to_datetime(demand['timestamp'], errors='coerce', utc=True)
    if timestamps.isna().any():
        raise ValueError('Historical demand contains invalid timestamps')
    if demand[['station_id', 'source_year']].isna().any().any():
        raise ValueError('Historical demand contains missing station or source-year values')
    if demand.duplicated(['timestamp', 'station_id']).any():
        raise ValueError('Historical demand contains duplicate station/time observations')


def _publish_historical_outputs(stage_dir: Path, silver_dir: Path, gold_dir: Path) -> None:
    (stage_dir / 'historical_trip_observations.csv').replace(silver_dir / 'historical_trip_observations.csv')
    (stage_dir / 'station_demand_hourly.csv').replace(gold_dir / 'station_demand_hourly.csv')


def _persist_stations(normalized: pd.DataFrame, source_name: str) -> int:
    if os.getenv('PERSIST_TO_DATABASE', 'false').lower() != 'true':
        return 0

    persisted = 0
    with SessionLocal.begin() as session:
        for row in normalized.to_dict(orient='records'):
            station_id = str(row['station_id'])
            station = session.get(Station, station_id) or Station(station_id=station_id, source_name=source_name)
            station.name = str(row.get('name') or '')
            station.address = str(row.get('address') or '')
            station.capacity = int(row['capacity']) if pd.notna(row.get('capacity')) else None
            station.available_bikes = int(row['available_bikes']) if pd.notna(row.get('available_bikes')) else None
            station.latitude = float(row['latitude']) if pd.notna(row.get('latitude')) else None
            station.longitude = float(row['longitude']) if pd.notna(row.get('longitude')) else None
            if station.latitude is not None and station.longitude is not None:
                station.location = from_shape(Point(station.longitude, station.latitude), srid=4326)
            station.source_name = source_name
            station.updated_at = datetime.now(UTC)
            session.add(station)
            persisted += 1
    return persisted


def _download_station_master() -> tuple[pd.DataFrame, dict]:
    config = load_provider_config('bicimad')
    source = config.station_master
    url = source['direct_url']
    response = requests.get(url, timeout=REQUEST_TIMEOUT)

    if response.status_code != 200:
        raise RuntimeError(f"Station master download failed with status {response.status_code}: {url}")

    expected_type = str(source.get('expected_content_type', '')).lower().split(';')[0].strip()
    content_type = str(response.headers.get('content-type', '')).lower().split(';')[0].strip()
    if expected_type and content_type and content_type != expected_type:
        raise ValueError(
            f"Unexpected station master content-type: got {content_type!r}, expected {expected_type!r} for {url}"
        )

    csv_text = response.text.encode('utf-8-sig').decode('utf-8-sig')
    raw_df = pd.read_csv(io.StringIO(csv_text), sep=';', encoding='utf-8-sig')

    metadata = {
        'dataset_id': source.get('dataset_id'),
        'direct_url': url,
        'expected_extension': source.get('expected_extension'),
        'expected_content_type': expected_type,
        'source_name': source.get('source_name'),
    }
    return raw_df, metadata


def prepare_data() -> dict[str, object]:
    bronze_dir = REPO_ROOT / 'data' / 'bronze'
    silver_dir = REPO_ROOT / 'data' / 'silver'
    gold_dir = REPO_ROOT / 'data' / 'gold'
    _ensure_directory(bronze_dir)
    _ensure_directory(silver_dir)
    _ensure_directory(gold_dir)
    completion_marker = gold_dir / 'historical_demand_complete.json'
    completion_marker.unlink(missing_ok=True)
    staging_root = REPO_ROOT / 'data' / '.historical_demand_staging'
    staging_root.mkdir(parents=True, exist_ok=True)
    stage_dir = staging_root / str(uuid4())
    stage_dir.mkdir()

    raw_df, metadata = _download_station_master()
    bronze_path = bronze_dir / 'bicimad_station_master.csv'
    raw_df.to_csv(bronze_path, index=False)

    normalized = raw_df.copy()
    normalized.columns = [_normalize_column_name(column) for column in normalized.columns]

    station_id_candidates = ['objectid', 'station_id', 'number', 'id']
    station_id_column = next((candidate for candidate in station_id_candidates if candidate in normalized.columns), None)
    if station_id_column is not None:
        normalized['station_id'] = normalized[station_id_column].astype(str)
    else:
        normalized['station_id'] = normalized.index.astype(str)

    capacity_candidates = ['totalbase', 'totalbases', 'capacity']
    capacity_column = next((candidate for candidate in capacity_candidates if candidate in normalized.columns), None)
    normalized['capacity'] = _coerce_numeric(normalized[capacity_column]) if capacity_column else pd.NA

    available_candidates = ['noavailable', 'available_bikes', 'availablebikes', 'bikes_available', 'free_bikes']
    available_column = next((candidate for candidate in available_candidates if candidate in normalized.columns), None)
    normalized['available_bikes'] = _coerce_numeric(normalized[available_column]) if available_column else pd.NA

    latitude_column = next((candidate for candidate in ['point_y', 'latitude', 'lat'] if candidate in normalized.columns), None)
    longitude_column = next((candidate for candidate in ['point_x', 'longitude', 'lon', 'lng'] if candidate in normalized.columns), None)
    if latitude_column and longitude_column:
        normalized['latitude'] = _coerce_numeric(normalized[latitude_column])
        normalized['longitude'] = _coerce_numeric(normalized[longitude_column])
    elif 'posicionstr' in normalized.columns:
        def _parse_lat_lon(value: object) -> tuple[float | None, float | None]:
            if pd.isna(value):
                return None, None
            text = str(value).strip()
            parts = text.split()
            if len(parts) >= 2:
                try:
                    lon = float(parts[0].replace(',', '.'))
                    lat = float(parts[1].replace(',', '.'))
                    return lat, lon
                except ValueError:
                    return None, None
            return None, None

        coords = normalized['posicionstr'].map(_parse_lat_lon)
        normalized['latitude'] = [item[0] for item in coords]
        normalized['longitude'] = [item[1] for item in coords]

    if 'latitude' not in normalized.columns:
        normalized['latitude'] = pd.NA
    if 'longitude' not in normalized.columns:
        normalized['longitude'] = pd.NA

    normalized['name'] = normalized.get('name', pd.Series([''] * len(normalized), index=normalized.index))
    normalized['address'] = normalized.get('address', pd.Series([''] * len(normalized), index=normalized.index))
    normalized['activate'] = normalized.get('activate', pd.Series([None] * len(normalized), index=normalized.index))

    silver_columns = [
        'station_id', 'name', 'capacity', 'available_bikes', 'latitude', 'longitude', 'address', 'activate'
    ]
    for column in silver_columns:
        if column not in normalized.columns:
            normalized[column] = pd.NA
    silver_path = silver_dir / 'bicimad_station_master.csv'
    normalized[silver_columns].to_csv(silver_path, index=False)

    gold = normalized[["station_id", "capacity", "available_bikes", "latitude", "longitude"]].copy()
    gold['station_score'] = gold['capacity'].fillna(0) - gold['available_bikes'].fillna(0)
    gold = gold.dropna(subset=['capacity', 'available_bikes']).copy()
    gold['station_score'] = gold['station_score'].clip(lower=0)
    gold_path = gold_dir / 'station_features.csv'
    gold.to_csv(gold_path, index=False)
    persisted_rows = _persist_stations(normalized, str(metadata['source_name']))

    try:
        trips, inspection = iter_historical_trips()
        demand, demand_quality = aggregate_trips(trips)
        _validate_historical_demand(demand)
        demand_path = gold_dir / 'station_demand_hourly.csv'
        silver_trips_path = silver_dir / 'historical_trip_observations.csv'
        demand.to_csv(stage_dir / demand_path.name, index=False)
        demand[['timestamp', 'station_id', 'departures', 'arrivals', 'total_activity', 'net_flow', 'source_year']].to_csv(
            stage_dir / silver_trips_path.name, index=False
        )
        if os.getenv('PERSIST_TO_DATABASE', 'false').lower() != 'true':
            raise RuntimeError('Historical completion requires PERSIST_TO_DATABASE=true')
        demand_persisted = persist_demand(demand)
        if demand_persisted != len(demand):
            raise RuntimeError(f'Persisted {demand_persisted} of {len(demand)} demand observations')
        _publish_historical_outputs(stage_dir, silver_dir, gold_dir)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise

    parser_stats = cast(ParseStats, inspection.get('stats', ParseStats()))
    stats_summary = {
        'raw_records': parser_stats.raw_records,
        'parsed_records': parser_stats.parsed_records,
        'invalid_records': parser_stats.invalid_records,
        'skipped_records': parser_stats.skipped_records,
        'warnings': parser_stats.warnings or [],
    }

    summary: dict[str, object] = {
        'bronze_rows': len(raw_df),
        'silver_rows': len(normalized),
        'gold_rows': len(gold),
        'dataset_id': str(metadata['dataset_id']),
        'destination': str(gold_path),
        'persisted_rows': persisted_rows,
        'demand_observations': len(demand),
        'demand_persisted_rows': demand_persisted,
        'trip_count': demand_quality['trip_count'],
        'duplicate_trip_count': demand_quality['duplicate_trip_count'],
        'selected_archive_members': cast(int, inspection['selected_members']),
        'unsupported_archive_members': len(cast(list[str], inspection['unsupported_members'])),
        'unsupported_archive_member_names': cast(list[str], inspection['unsupported_members']),
        'ignored_archive_members': len(cast(list[str], inspection.get('ignored_members', []))),
        'raw_record_count': stats_summary['raw_records'],
        'parsed_record_count': stats_summary['parsed_records'],
        'invalid_record_count': stats_summary['invalid_records'],
        'skipped_record_count': stats_summary['skipped_records'],
        'parser_warnings': stats_summary['warnings'],
        'parser_errors': cast(list[str], inspection.get('errors', [])),
        'member_stats': inspection.get('member_stats', {}),
        'demand_start': str(demand['timestamp'].min()),
        'demand_end': str(demand['timestamp'].max()),
    }
    marker_partial = completion_marker.with_suffix('.json.partial')
    try:
        marker_partial.write_text(json.dumps(summary, indent=2, default=str), encoding='utf-8')
        marker_partial.replace(completion_marker)
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
        try:
            staging_root.rmdir()
        except OSError:
            pass
    print(f"prepare-data: downloaded {summary['bronze_rows']} station rows to {bronze_path}")
    print(f"prepare-data: parsed {summary['trip_count']} trips into {summary['demand_observations']} hourly observations")
    print(f"prepare-data: demand range {summary['demand_start']} to {summary['demand_end']}")
    print(f"prepare-data: raw={summary['raw_record_count']} parsed={summary['parsed_record_count']} skipped={summary['skipped_record_count']} invalid={summary['invalid_record_count']} duplicates={summary['duplicate_trip_count']}")
    print(f"prepare-data: selected_members={summary['selected_archive_members']} unsupported_irrelevant_members={summary['unsupported_archive_members']}")
    print(f"prepare-data: silver and gold datasets saved under {gold_dir}")
    return summary


def train() -> dict[str, object]:
    gold_path = REPO_ROOT / 'data' / 'gold' / 'station_features.csv'
    if not gold_path.exists():
        prepare_data()

    gold = pd.read_csv(gold_path)
    model_dir = REPO_ROOT / 'models' / 'production'
    metadata_dir = REPO_ROOT / 'models' / 'metadata'
    _ensure_directory(model_dir)
    _ensure_directory(metadata_dir)
    model_path = model_dir / 'bicimad_baseline.joblib'
    if model_path.exists():
        model_path.unlink()

    metadata: dict[str, object] = {
        'model_name': 'bicimad_forecaster',
        'model_version': 'unvalidated-no-temporal-target',
        'status': 'blocked',
        'reason': 'station master data has no timestamped demand target; the former snapshot score was target leakage',
        'feature_definition': [],
        'train_period': None,
        'validation_period': None,
        'test_period': None,
        'metrics': {'status': 'not_evaluable', 'rows': len(gold)},
    }
    (metadata_dir / 'model_metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print('train: blocked because no timestamped demand target is available; no model artifact was created')
    return {'status': 'blocked', 'reason': metadata['reason']}


def evaluate() -> dict[str, object]:
    gold_path = REPO_ROOT / 'data' / 'gold' / 'station_features.csv'
    if not gold_path.exists():
        prepare_data()

    gold = pd.read_csv(gold_path)
    metadata_dir = REPO_ROOT / 'models' / 'metadata'
    _ensure_directory(metadata_dir)
    metrics: dict[str, object] = {
        'status': 'not_evaluable',
        'reason': 'no timestamped demand observations; chronological train/validation/test evaluation is impossible',
        'original_snapshot_mae': 'invalid_due_to_target_leakage',
        'original_snapshot_r2': 'invalid_due_to_target_leakage',
        'rows_inspected': len(gold),
    }
    (metadata_dir / 'evaluation_metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    print('evaluate: not evaluable; no timestamped demand target is present')
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description='Mobility Intelligence Platform CLI')
    subparsers = parser.add_subparsers(dest='command', required=True)

    subparsers.add_parser('prepare-data')
    subparsers.add_parser('train')
    subparsers.add_parser('evaluate')

    args = parser.parse_args()
    if args.command == 'prepare-data':
        prepare_data()
    elif args.command == 'train':
        train()
    elif args.command == 'evaluate':
        evaluate()


if __name__ == '__main__':
    main()
