from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ml.canonical_demand_store import AUTHORITATIVE_GOLD_HASH
from src.config.settings import DatabaseSettings, normalize_database_url
from src.ml.canonical_forecast_builder import (
    DATASET_COLUMNS,
    SOURCE_NAME,
    build_from_database,
    validate_artifact,
)
from src.ml.forecast_contract_readiness import (
    build_contract_readiness,
    build_overall_readiness,
    build_production_model_readiness,
)

OUTPUT = ROOT / 'data' / 'gold' / 'forecasting'
DATABASE_URL = normalize_database_url(DatabaseSettings().url)

RUNTIME_METADATA_KEYS = {
    'generation_duration_seconds',
    'validation_duration_seconds',
    'dataset_artifact',
    'artifact_size_bytes',
    'stdout',
    'stderr',
    'exit_code',
    'command',
    'stdout_tail',
    'stderr_tail',
    'generated_at',
    'created_at',
    'timing',
}


def _sha256_stream(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_decompressed_gzip(path: Path) -> str:
    digest = hashlib.sha256()
    with gzip.open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _strip_runtime_metadata(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key in RUNTIME_METADATA_KEYS:
                continue
            cleaned[key] = _strip_runtime_metadata(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_runtime_metadata(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_runtime_metadata(item) for item in value)
    return value


def _semantic_json_equal(path_a: Path, path_b: Path) -> bool:
    with path_a.open('r', encoding='utf-8') as stream:
        left = _strip_runtime_metadata(json.load(stream))
    with path_b.open('r', encoding='utf-8') as stream:
        right = _strip_runtime_metadata(json.load(stream))
    return left == right


def _determinism_report(database_url: str, output_dir: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix='forecast_det_') as temp_dir:
        temp_output = Path(temp_dir) / 'forecasting'
        temp_generated = build_from_database(database_url, temp_output, skip_existing=False)
        temp_reports = temp_generated['reports']
        temp_validations = {}
        temp_leakage = {}
        with psycopg.connect(database_url) as connection:
            for horizon in (60, 120):
                path = temp_output / f'forecasting_dataset_{horizon}m.csv.gz'
                validation = validate_artifact(connection, path, horizon)
                temp_validations[str(horizon)] = validation
                temp_leakage[str(horizon)] = {
                    'horizon': horizon,
                    'rows_checked': validation['rows_checked'],
                    'violations_by_rule': validation['violations_by_rule'],
                    'total_violations': validation['total_violations'],
                    'status': validation['status'],
                    'checked_rules': [
                        'exact target key exists and target value matches',
                        'exact lag keys and availability flags match',
                        'target_timestamp strictly follows feature_timestamp',
                        'no future-derived feature columns are present',
                        'no current/future station metadata is present',
                    ],
                }
        temp_dataset_report = {
            'source_gold': {
                'access_strategy': 'validated PostgreSQL demand_observations table',
                'table': 'demand_observations',
                'source_name': SOURCE_NAME,
                'authoritative_hash': AUTHORITATIVE_GOLD_HASH,
            },
            'horizons': temp_reports,
            'artifact_validation': temp_validations,
        }
        temp_contract = {
            'status': 'PASS',
            'source_gold': temp_dataset_report['source_gold'],
            'target_definition': 'demand = departures; y(station,t,h) = departures(station,t+h)',
            'horizons_minutes': [60, 120],
            'target_observation_rule': 'Exact target row at (station_id, feature_timestamp + horizon).',
            'lag_rule': 'Exact rows at (station_id, feature_timestamp - lag); missing rows remain null.',
            'missing_feature_policy': 'No imputation or missing-to-zero conversion; availability flags are explicit.',
            'split_policy': 'Chronological 70/15/15 timestamp split with target-boundary purge.',
            'leakage_rules': temp_leakage,
            'baseline_definitions': {
                'naive': 'exact demand_lag_1h',
                'seasonal_naive_24h': 'exact demand_lag_24h',
                'seasonal_naive_168h': 'exact demand_lag_168h',
            },
            'metric_definitions': ['MAE', 'RMSE', 'R2'],
            'artifact_schema': DATASET_COLUMNS,
            'deterministic_serialization': 'UTF-8 CSV rows in deterministic builder order, incrementally SHA-256 hashed.',
            'model_training': 'Not performed.',
        }
        temp_leakage_report = {'status': 'PASS', 'horizons': temp_leakage}
        temp_contract_path = temp_output / 'forecasting_contract.json'
        temp_dataset_report_path = temp_output / 'forecasting_dataset_report.json'
        temp_leakage_path = temp_output / 'leakage_audit.json'
        temp_contract_path.write_text(json.dumps(temp_contract, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        temp_dataset_report_path.write_text(json.dumps(temp_dataset_report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        temp_leakage_path.write_text(json.dumps(temp_leakage_report, indent=2, sort_keys=True) + '\n', encoding='utf-8')

        dataset_equal = {
            '60m': _sha256_decompressed_gzip(output_dir / 'forecasting_dataset_60m.csv.gz') == _sha256_decompressed_gzip(temp_output / 'forecasting_dataset_60m.csv.gz'),
            '120m': _sha256_decompressed_gzip(output_dir / 'forecasting_dataset_120m.csv.gz') == _sha256_decompressed_gzip(temp_output / 'forecasting_dataset_120m.csv.gz'),
        }
        semantic_equal = {
            'contract': _semantic_json_equal(output_dir / 'forecasting_contract.json', temp_contract_path),
            'dataset_report': _semantic_json_equal(output_dir / 'forecasting_dataset_report.json', temp_dataset_report_path),
            'leakage_audit': _semantic_json_equal(output_dir / 'leakage_audit.json', temp_leakage_path),
        }
        status = 'PASS' if all(dataset_equal.values()) and all(semantic_equal.values()) else 'BLOCKED'
        return {
            'status': status,
            'runs_compared': 2,
            'dataset_60m_equal': dataset_equal['60m'],
            'dataset_120m_equal': dataset_equal['120m'],
            'contract_semantics_equal': semantic_equal['contract'],
            'dataset_report_semantics_equal': semantic_equal['dataset_report'],
            'leakage_semantics_equal': semantic_equal['leakage_audit'],
            'notes': 'Compared decompressed dataset content and semantically stripped JSON payloads to ignore runtime metadata.',
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-existing', action='store_true')
    args = parser.parse_args()
    generated = build_from_database(DATABASE_URL, OUTPUT, skip_existing=args.skip_existing)
    reports = generated['reports']
    validations = {}
    baselines = {}
    leakage = {}
    with psycopg.connect(DATABASE_URL) as connection:
        for horizon in (60, 120):
            path = OUTPUT / f'forecasting_dataset_{horizon}m.csv.gz'
            validation = validate_artifact(connection, path, horizon)
            validations[str(horizon)] = validation
            baselines[str(horizon)] = validation['baseline_report']
            leakage[str(horizon)] = {
                'horizon': horizon,
                'rows_checked': validation['rows_checked'],
                'violations_by_rule': validation['violations_by_rule'],
                'total_violations': validation['total_violations'],
                'status': validation['status'],
                'checked_rules': [
                    'exact target key exists and target value matches',
                    'exact lag keys and availability flags match',
                    'target_timestamp strictly follows feature_timestamp',
                    'no future-derived feature columns are present',
                    'no current/future station metadata is present',
                ],
            }
    if any(item['status'] != 'PASS' for item in validations.values()):
        raise RuntimeError(f'Forecasting artifact validation failed: {validations}')
    if any(item['status'] != 'PASS' for item in leakage.values()):
        raise RuntimeError(f'Forecasting leakage audit failed: {leakage}')
    for horizon in (60, 120):
        reports[str(horizon)].update({
            'rows_checked': validations[str(horizon)]['rows_checked'],
            'station_count': validations[str(horizon)]['station_count'],
            'split_counts': validations[str(horizon)]['split_counts'],
            'lag_availability_counts': validations[str(horizon)]['lag_availability_counts'],
            'canonical_hash': validations[str(horizon)]['canonical_hash'],
            'validation_duration_seconds': validations[str(horizon)]['validation_duration_seconds'],
            'source_gold_hash': AUTHORITATIVE_GOLD_HASH,
            'artifact_size_bytes': (OUTPUT / f'forecasting_dataset_{horizon}m.csv.gz').stat().st_size,
        })
    dataset_report = {
        'source_gold': {
            'access_strategy': 'validated PostgreSQL demand_observations table',
            'table': 'demand_observations',
            'source_name': SOURCE_NAME,
            'authoritative_hash': AUTHORITATIVE_GOLD_HASH,
        },
        'horizons': reports,
        'artifact_validation': validations,
    }
    contract = {
        'status': 'PASS',
        'source_gold': dataset_report['source_gold'],
        'target_definition': 'demand = departures; y(station,t,h) = departures(station,t+h)',
        'horizons_minutes': [60, 120],
        'target_observation_rule': 'Exact target row at (station_id, feature_timestamp + horizon).',
        'lag_rule': 'Exact rows at (station_id, feature_timestamp - lag); missing rows remain null.',
        'missing_feature_policy': 'No imputation or missing-to-zero conversion; availability flags are explicit.',
        'split_policy': 'Chronological 70/15/15 timestamp split with target-boundary purge.',
        'leakage_rules': leakage,
        'baseline_definitions': {
            'naive': 'exact demand_lag_1h',
            'seasonal_naive_24h': 'exact demand_lag_24h',
            'seasonal_naive_168h': 'exact demand_lag_168h',
        },
        'metric_definitions': ['MAE', 'RMSE', 'R2'],
        'artifact_schema': DATASET_COLUMNS,
        'deterministic_serialization': 'UTF-8 CSV rows in deterministic builder order, incrementally SHA-256 hashed.',
        'model_training': 'Not performed.',
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / 'forecasting_dataset_report.json').write_text(json.dumps(dataset_report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (OUTPUT / 'forecasting_contract.json').write_text(json.dumps(contract, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    for horizon in (60, 120):
        (OUTPUT / f'baseline_evaluation_{horizon}m.json').write_text(json.dumps(baselines[str(horizon)], indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (OUTPUT / 'leakage_audit.json').write_text(json.dumps({'status': 'PASS', 'horizons': leakage}, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    pytest_result = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=ROOT, capture_output=True, text=True, check=False)
    ruff_result = subprocess.run([sys.executable, '-m', 'ruff', 'check', 'src', 'tests'], cwd=ROOT, capture_output=True, text=True, check=False)
    tests_status = 'PASS' if pytest_result.returncode == 0 else 'BLOCKED'
    lint_status = 'PASS' if ruff_result.returncode == 0 else 'BLOCKED'

    deterministic_report = _determinism_report(DATABASE_URL, OUTPUT)
    evidence = {
        'contract_implementation': {'status': 'PASS', 'evidence': 'Database-backed exact contract builder', 'limitations': 'Model training not performed.'},
        'canonical_data_access': {'status': 'PASS', 'evidence': dataset_report['source_gold'], 'limitations': 'Uses validated canonical DB representation.'},
        'dataset_60m': {'status': 'PASS', 'evidence': reports['60'], 'limitations': 'Irregular station coverage.'},
        'dataset_120m': {'status': 'PASS', 'evidence': reports['120'], 'limitations': 'Irregular station coverage.'},
        'target_validation': {'status': 'PASS', 'evidence': {h: validations[h]['total_violations'] for h in validations}, 'limitations': 'All artifact rows checked.'},
        'lag_validation': {'status': 'PASS', 'evidence': validations, 'limitations': 'All artifact rows checked.'},
        'feature_leakage': {'status': 'PASS', 'evidence': leakage, 'limitations': 'No model training performed.'},
        'chronological_split': {'status': 'PASS', 'evidence': {h: reports[h].get('split_counts') for h in reports}, 'limitations': 'Target-boundary purge applied.'},
        'baseline_definition': {'status': 'PASS', 'evidence': baselines, 'limitations': 'Streaming baseline metrics on test rows.'},
        'metric_definition': {'status': 'PASS', 'evidence': ['MAE', 'RMSE', 'R2'], 'limitations': 'R2 is null when variance is zero.'},
        'artifact_validation': {'status': 'PASS', 'evidence': validations, 'limitations': 'All rows streamed from disk.'},
        'deterministic_generation': {
            'status': deterministic_report['status'],
            'evidence': deterministic_report,
            'limitations': 'Semantics are compared across two independent runs; runtime timing metadata is excluded from semantic equality.',
        },
        'tests': {
            'status': tests_status,
            'command': 'python -m pytest -q',
            'exit_code': pytest_result.returncode,
            'stdout_tail': pytest_result.stdout.strip().splitlines()[-5:],
            'stderr_tail': pytest_result.stderr.strip().splitlines()[-5:],
            'limitations': 'No production model trained.',
        },
        'lint': {
            'status': lint_status,
            'command': 'python -m ruff check src tests',
            'exit_code': ruff_result.returncode,
            'stdout_tail': ruff_result.stdout.strip().splitlines()[-5:],
            'stderr_tail': ruff_result.stderr.strip().splitlines()[-5:],
        },
    }
    production_model = build_production_model_readiness()
    contract_readiness = build_contract_readiness(evidence)
    overall_readiness = build_overall_readiness(contract_readiness, production_model)
    readiness = {
        'contract_readiness': {
            'status': contract_readiness['status'],
            'blocking_gates': contract_readiness['blocking_gates'],
            'gates': {key: value for key, value in evidence.items() if key in set(contract_readiness['gates'])},
        },
        'production_model_readiness': production_model,
        'overall_status': overall_readiness['overall_status'],
        'qa': {
            'pytest': {
                'status': tests_status,
                'command': 'python -m pytest -q',
                'exit_code': pytest_result.returncode,
                'stdout': pytest_result.stdout,
                'stderr': pytest_result.stderr,
            },
            'ruff': {
                'status': lint_status,
                'command': 'python -m ruff check src tests',
                'exit_code': ruff_result.returncode,
                'stdout': ruff_result.stdout,
                'stderr': ruff_result.stderr,
            },
        },
    }
    (OUTPUT / 'forecasting_readiness.json').write_text(json.dumps(readiness, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (OUTPUT / 'deterministic_generation.json').write_text(json.dumps(deterministic_report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'readiness': readiness['overall_status'], 'contract_readiness': readiness['contract_readiness']['status'], 'production_model_readiness': readiness['production_model_readiness']['status'], 'reports': reports}, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
