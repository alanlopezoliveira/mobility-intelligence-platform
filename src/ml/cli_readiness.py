from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from src.config.settings import DatabaseSettings
from src.ml.canonical_demand_store import AUTHORITATIVE_GOLD_HASH, SOURCE_NAME
from src.ml.canonical_forecast_builder import DATASET_COLUMNS

HORIZONS_MINUTES = (60, 120)
FORECASTING_STATE_DATA_NOT_PREPARED = 'DATA_NOT_PREPARED'
FORECASTING_STATE_NOT_READY = 'FORECASTING_DATA_NOT_READY'
FORECASTING_STATE_READY = 'FORECASTING_DATA_READY'


@dataclass(frozen=True)
class ForecastingState:
    status: str
    reason: str
    details: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_demand_metadata(database_url: str) -> dict[str, Any] | None:
    try:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                'SELECT COUNT(*), MIN(observed_at), MAX(observed_at) '
                'FROM demand_observations WHERE source_name = %s '
                'AND batch_id = (SELECT batch_id FROM active_dataset_version WHERE singleton_id = 1)',
                (SOURCE_NAME,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            row_count, minimum, maximum = row
    except psycopg.Error:
        return None
    if not row_count:
        return None
    return {
        'table': 'demand_observations',
        'source_name': SOURCE_NAME,
        'row_count': row_count,
        'min_timestamp': minimum.isoformat(),
        'max_timestamp': maximum.isoformat(),
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _validate_forecasting_artifacts(forecasting_dir: Path) -> tuple[bool, list[str], dict[str, Any]]:
    problems: list[str] = []
    evidence: dict[str, Any] = {}
    contract = _read_json(forecasting_dir / 'forecasting_contract.json')
    report = _read_json(forecasting_dir / 'forecasting_dataset_report.json')
    readiness = _read_json(forecasting_dir / 'forecasting_readiness.json')
    if contract is None:
        problems.append('forecasting_contract.json is missing or invalid JSON')
    if report is None:
        problems.append('forecasting_dataset_report.json is missing or invalid JSON')
    if readiness is None:
        problems.append('forecasting_readiness.json is missing or invalid JSON')
    if contract is None or report is None or readiness is None:
        return False, problems, evidence

    if contract.get('horizons_minutes') != list(HORIZONS_MINUTES):
        problems.append('contract does not declare exactly the supported 60m and 120m horizons')
    if contract.get('target_definition') != 'demand = departures; y(station,t,h) = departures(station,t+h)':
        problems.append('contract target definition does not match target_departures')
    if contract.get('artifact_schema') != DATASET_COLUMNS:
        problems.append('contract artifact schema does not match the canonical dataset schema')
    source_gold = report.get('source_gold', {})
    if source_gold.get('authoritative_hash') != AUTHORITATIVE_GOLD_HASH or source_gold.get('source_name') != SOURCE_NAME:
        problems.append('dataset report does not identify the authoritative canonical demand source/hash')

    # The generated readiness artifact stores gate objects at its top level.
    readiness_gates = {
        name: value for name, value in readiness.items()
        if isinstance(value, dict) and name not in {'production_model'}
    }
    for gate, value in readiness_gates.items():
        if not isinstance(value, dict) or str(value.get('status', '')).upper() != 'PASS':
            problems.append(f'forecasting readiness gate {gate} is not PASS')
    required_gates = {
        'contract_implementation', 'canonical_data_access', 'dataset_60m', 'dataset_120m',
        'target_validation', 'lag_validation', 'feature_leakage', 'chronological_split',
        'baseline_definition', 'metric_definition', 'artifact_validation',
        'deterministic_generation', 'tests',
    }
    for gate in sorted(required_gates - set(readiness_gates)):
        problems.append(f'forecasting readiness gate {gate} is missing')

    horizon_reports = report.get('horizons', {})
    for horizon in HORIZONS_MINUTES:
        key = str(horizon)
        metadata = horizon_reports.get(key)
        path = forecasting_dir / f'forecasting_dataset_{horizon}m.csv.gz'
        if not isinstance(metadata, dict) or not path.is_file():
            problems.append(f'{horizon}m dataset artifact or report metadata is missing')
            continue
        if metadata.get('source_gold_hash') != AUTHORITATIVE_GOLD_HASH:
            problems.append(f'{horizon}m dataset references a different canonical demand hash')
        if metadata.get('dataset_sha256') != _sha256(path):
            problems.append(f'{horizon}m dataset hash does not match its artifact')
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as stream:
                header = stream.readline().rstrip('\r\n').split(',')
            if header != DATASET_COLUMNS:
                problems.append(f'{horizon}m dataset columns do not match the frozen schema')
        except (OSError, EOFError, UnicodeDecodeError):
            problems.append(f'{horizon}m dataset is not a readable gzip CSV')
        evidence[key] = {'path': str(path), 'sha256': metadata.get('dataset_sha256')}
    return not problems, problems, evidence


def inspect_forecasting_state(root: Path, database_url: str | None = None) -> ForecastingState:
    url = database_url or DatabaseSettings().url
    canonical = _canonical_demand_metadata(url)
    if canonical is None:
        return ForecastingState(
            FORECASTING_STATE_DATA_NOT_PREPARED,
            'Canonical BiciMAD historical demand is absent or the PostgreSQL demand store is unavailable; run prepare-data and verify PostgreSQL.',
            {},
        )
    valid, problems, artifacts = _validate_forecasting_artifacts(root / 'data' / 'gold' / 'forecasting')
    if not valid:
        return ForecastingState(
            FORECASTING_STATE_NOT_READY,
            'Canonical historical demand exists, but forecasting artifacts or readiness gates are missing or invalid.',
            {'canonical_demand': canonical, 'problems': problems, 'artifacts': artifacts},
        )
    return ForecastingState(
        FORECASTING_STATE_READY,
        'Canonical demand and both validated forecasting datasets satisfy the forecasting contract.',
        {'canonical_demand': canonical, 'artifacts': artifacts},
    )
