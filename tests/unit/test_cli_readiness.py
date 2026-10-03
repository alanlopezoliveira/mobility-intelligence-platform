from __future__ import annotations

import gzip
import hashlib
import json

from src.ml import cli_readiness
from src.ml.canonical_demand_store import AUTHORITATIVE_GOLD_HASH, SOURCE_NAME
from src.ml.canonical_forecast_builder import DATASET_COLUMNS


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def _ready_fixture(root):
    output = root / 'data' / 'gold' / 'forecasting'
    output.mkdir(parents=True)
    horizons = {}
    for horizon in (60, 120):
        path = output / f'forecasting_dataset_{horizon}m.csv.gz'
        with gzip.open(path, 'wt', encoding='utf-8', newline='') as stream:
            stream.write(','.join(DATASET_COLUMNS) + '\n')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        horizons[str(horizon)] = {
            'source_gold_hash': AUTHORITATIVE_GOLD_HASH,
            'dataset_sha256': digest,
        }
    _write_json(output / 'forecasting_contract.json', {
        'horizons_minutes': [60, 120],
        'target_definition': 'demand = departures; y(station,t,h) = departures(station,t+h)',
        'artifact_schema': DATASET_COLUMNS,
    })
    _write_json(output / 'forecasting_dataset_report.json', {
        'source_gold': {'source_name': SOURCE_NAME, 'authoritative_hash': AUTHORITATIVE_GOLD_HASH},
        'horizons': horizons,
    })
    gates = {
        name: {'status': 'PASS'} for name in (
            'contract_implementation', 'canonical_data_access', 'dataset_60m', 'dataset_120m',
            'target_validation', 'lag_validation', 'feature_leakage', 'chronological_split',
            'baseline_definition', 'metric_definition', 'artifact_validation',
            'deterministic_generation', 'tests',
        )
    }
    _write_json(output / 'forecasting_readiness.json', gates)


def test_absent_canonical_data_is_data_not_prepared(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_readiness, '_canonical_demand_metadata', lambda database_url: None)

    state = cli_readiness.inspect_forecasting_state(tmp_path, 'postgresql://unused')

    assert state.status == 'DATA_NOT_PREPARED'


def test_demand_without_contract_artifacts_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_readiness, '_canonical_demand_metadata', lambda database_url: {'row_count': 3})

    state = cli_readiness.inspect_forecasting_state(tmp_path, 'postgresql://unused')

    assert state.status == 'FORECASTING_DATA_NOT_READY'
    assert state.details['problems']


def test_valid_contract_and_both_horizon_artifacts_are_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_readiness, '_canonical_demand_metadata', lambda database_url: {'row_count': 3})
    _ready_fixture(tmp_path)

    state = cli_readiness.inspect_forecasting_state(tmp_path, 'postgresql://unused')

    assert state.status == 'FORECASTING_DATA_READY'
    assert set(state.details['artifacts']) == {'60', '120'}


def test_readiness_gate_failure_blocks_even_when_csvs_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_readiness, '_canonical_demand_metadata', lambda database_url: {'row_count': 3})
    _ready_fixture(tmp_path)
    readiness_path = tmp_path / 'data' / 'gold' / 'forecasting' / 'forecasting_readiness.json'
    readiness = json.loads(readiness_path.read_text())
    readiness['feature_leakage']['status'] = 'BLOCKED'
    _write_json(readiness_path, readiness)

    state = cli_readiness.inspect_forecasting_state(tmp_path, 'postgresql://unused')

    assert state.status == 'FORECASTING_DATA_NOT_READY'
    assert any('feature_leakage' in item for item in state.details['problems'])
