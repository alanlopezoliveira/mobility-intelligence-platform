import json

import pandas as pd
from src.ml.benchmark import (
    AUTHORITATIVE_GOLD_HASH,
    BASELINE_NAMES,
    LAG_HOURS,
    TARGET_COLUMN,
    _sha256_file,
    _stable_row_key,
    _validate_prediction_artifact,
    build_baseline_metrics,
    compare_execution_reproducibility,
    evaluate_common_population,
    generate_benchmark_v2,
    get_model_feature_columns,
)
from src.ml.canonical_forecast_builder import _query
from src.ml.forecast_contract_readiness import (
    build_contract_readiness,
    build_overall_readiness,
    build_production_model_readiness,
)
from src.ml.forecasting import naive_forecast, seasonal_naive_forecast


def test_baselines_return_valid_forecasts():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], name="demand")
    assert len(naive_forecast(series, horizon=2)) == 2
    assert len(seasonal_naive_forecast(series, horizon=2, seasonality=3)) == 2
    assert all(value >= 0 for value in naive_forecast(series, horizon=2).to_list())


def test_canonical_forecast_query_uses_matching_parameter_count():
    sql, params = _query(
        60,
        {
            'train_end': pd.Timestamp('2024-01-01T00:00:00Z').to_pydatetime(),
            'validation_end': pd.Timestamp('2024-01-02T00:00:00Z').to_pydatetime(),
            'test_end': pd.Timestamp('2024-01-03T00:00:00Z').to_pydatetime(),
        },
    )
    assert sql.count('%s') == len(params)
    assert 'active_dataset_version' in sql
    assert 'feature.station_instance_id' in sql
    assert 'target.station_instance_id = feature.station_instance_id' in sql


def test_contract_readiness_excludes_production_model():
    contract_gates = {
        'contract_implementation': {'status': 'PASS'},
        'canonical_data_access': {'status': 'PASS'},
        'production_model': {'status': 'BLOCKED'},
    }
    contract_readiness = build_contract_readiness(contract_gates)
    production_readiness = build_production_model_readiness()
    overall = build_overall_readiness(contract_readiness, production_readiness)

    assert contract_readiness['status'] == 'PASS'
    assert 'production_model' not in contract_readiness['gates']
    assert production_readiness['status'] == 'NOT_APPLICABLE'
    assert overall['overall_status'] == 'PASS'


def test_baselines_use_exact_contract_lag_columns():
    frame = pd.DataFrame(
        {
            'station_id': [1, 1, 2, 2],
            'feature_timestamp': pd.to_datetime([
                '2022-01-01 00:00:00+00:00',
                '2022-01-01 01:00:00+00:00',
                '2022-01-01 00:00:00+00:00',
                '2022-01-01 01:00:00+00:00',
            ]),
            'target_timestamp': pd.to_datetime([
                '2022-01-01 01:00:00+00:00',
                '2022-01-01 02:00:00+00:00',
                '2022-01-01 01:00:00+00:00',
                '2022-01-01 02:00:00+00:00',
            ]),
            'target_departures': [5.0, 10.0, 7.0, 12.0],
            'demand_lag_1h': [3.0, 5.0, 4.0, 7.0],
            'demand_lag_24h': [1.0, 2.0, 3.0, 4.0],
            'demand_lag_168h': [9.0, 8.0, 7.0, 6.0],
            'horizon_minutes': [60, 60, 60, 60],
            'split': ['test', 'test', 'test', 'test'],
        }
    )
    metrics = build_baseline_metrics(frame)
    assert set(metrics) == {'naive_1h', 'seasonal_naive_24h', 'seasonal_naive_168h'}
    assert metrics['naive_1h']['evaluated_rows'] == 4
    assert metrics['seasonal_naive_24h']['evaluated_rows'] == 4
    assert metrics['seasonal_naive_168h']['evaluated_rows'] == 4


def test_model_feature_columns_preserve_contract_and_missingness():
    frame = pd.DataFrame(
        {
            'station_id': [1, 1, 2],
            'feature_timestamp': pd.to_datetime([
                '2022-01-01 00:00:00+00:00',
                '2022-01-01 01:00:00+00:00',
                '2022-01-01 00:00:00+00:00',
            ]),
            'target_timestamp': pd.to_datetime([
                '2022-01-01 01:00:00+00:00',
                '2022-01-01 02:00:00+00:00',
                '2022-01-01 01:00:00+00:00',
            ]),
            'target_departures': [10.0, 12.0, 9.0],
            'demand_lag_1h': [None, 5.0, 4.0],
            'demand_lag_24h': [3.0, 6.0, 2.0],
            'hour': [0, 1, 0],
            'month': [1, 1, 1],
            'horizon_minutes': [60, 60, 60],
            'split': ['train', 'validation', 'test'],
            'demand_at_feature_time': [8.0, 9.0, 7.0],
        }
    )
    cols = get_model_feature_columns(frame.columns)
    assert 'target_departures' not in cols
    assert 'target_timestamp' not in cols
    assert 'demand_lag_1h' in cols
    assert 'horizon_minutes' not in cols
    assert frame['demand_lag_1h'].isna().sum() == 1
    assert 'demand_lag_1h' in cols


def test_reproducibility_ignores_runtime_timing_fields():
    run_a = {
        'horizons': [{
            'horizon_minutes': 60,
            'metrics': {
                'mae': 1.0,
                'rmse': 2.0,
                'r2': 0.5,
                'n_predictions': 10,
                'coverage': 1.0,
                'training_time_seconds': 10.0,
                'prediction_time_seconds': 2.0,
            },
            'metadata': {
                'feature_columns': ['station_id', 'demand_lag_1h'],
                'categorical_columns': ['station_id'],
                'train_rows': 100,
                'validation_rows': 20,
                'test_rows': 10,
                'model_class': 'lightgbm.LGBMRegressor',
                'model_parameters': {'random_state': 42},
                'random_seed': 42,
                'dataset_contract_hash': 'abc',
                'forecasting_dataset_hash': 'def',
                'canonical_data_hash': 'ghi',
                'best_iteration': 42,
            },
            'predictions': pd.DataFrame({
                'station_id': ['1', '2'],
                'feature_timestamp': pd.to_datetime(['2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z']),
                'target_timestamp': pd.to_datetime(['2024-01-01T01:00:00Z', '2024-01-01T02:00:00Z']),
                'y_true': [10.0, 12.0],
                'y_pred': [9.8, 12.1],
                'residual': [0.2, -0.1],
                'horizon_minutes': [60, 60],
                'model': ['lightgbm_regressor', 'lightgbm_regressor'],
                'split': ['test', 'test'],
            }),
        }],
        'dataset_contract_hash': 'abc',
        'dataset_artifact_hashes': {'60': 'def'},
        'target': 'target_departures',
        'model_class': 'lightgbm.LGBMRegressor',
        'random_seed': 42,
    }
    run_b = {
        **run_a,
        'horizons': [{
            **run_a['horizons'][0],
            'metrics': {
                **run_a['horizons'][0]['metrics'],
                'training_time_seconds': 40.0,
                'prediction_time_seconds': 7.0,
            },
        }],
    }
    report = compare_execution_reproducibility(run_a, run_b)
    assert report['scientific_reproducibility']['status'] == 'PASS'
    assert report['runtime_metadata']['status'] == 'NON_DETERMINISTIC_EXPECTED'
    assert report['predictions_equal']['status'] == 'PASS'


def test_common_population_uses_stable_row_keys():
    row = pd.Series({
        'station_id': 1,
        'feature_timestamp': pd.Timestamp('2024-01-01T00:00:00Z'),
        'target_timestamp': pd.Timestamp('2024-01-01T01:00:00Z'),
        'horizon_minutes': 60,
        'split': 'test',
    })
    assert _stable_row_key(row) == ('1', '2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z', '60', 'test')


def test_common_population_metrics_exclude_missing_rows():
    dataset = pd.DataFrame({
        'station_id': ['A', 'A', 'B', 'B'],
        'feature_timestamp': pd.to_datetime(['2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z', '2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z']),
        'target_timestamp': pd.to_datetime(['2024-01-01T01:00:00Z', '2024-01-01T02:00:00Z', '2024-01-01T01:00:00Z', '2024-01-01T02:00:00Z']),
        'horizon_minutes': [60, 60, 60, 60],
        'split': ['test', 'test', 'test', 'test'],
        'target_departures': [10.0, 12.0, 9.0, None],
        'demand_lag_1h': [5.0, None, 4.0, 8.0],
        'demand_lag_24h': [7.0, 8.0, None, 6.0],
        'demand_lag_168h': [6.0, 7.0, 5.0, 4.0],
    })
    predictions = pd.DataFrame({
        'station_id': ['A', 'A', 'B', 'B'],
        'feature_timestamp': pd.to_datetime(['2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z', '2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z']),
        'target_timestamp': pd.to_datetime(['2024-01-01T01:00:00Z', '2024-01-01T02:00:00Z', '2024-01-01T01:00:00Z', '2024-01-01T02:00:00Z']),
        'horizon_minutes': [60, 60, 60, 60],
        'split': ['test', 'test', 'test', 'test'],
        'y_true': [10.0, 12.0, 9.0, 5.0],
        'y_pred': [9.5, 11.8, 4.0, None],
    })
    report = evaluate_common_population(dataset, predictions, horizon=60)
    assert report['common_population_rows'] == 1
    assert report['rows_excluded_by_model']['lightgbm'] == 1
    assert report['rows_excluded_by_model']['naive_1h'] == 1
    assert report['rows_excluded_by_model']['seasonal_naive_24h'] == 1
    assert report['rows_excluded_by_model']['seasonal_naive_168h'] == 0


def test_validate_prediction_artifact_rejects_corrupt_gzip(tmp_path):
    bad_path = tmp_path / 'corrupt.csv.gz'
    bad_path.write_bytes(b'')
    ok, reason = _validate_prediction_artifact(bad_path, expected_columns=['station_id', 'y_true'])
    assert ok is False
    assert 'CORRUPT' in reason


def test_common_population_audit_uses_artifacts_and_reports_status():
    dataset = pd.DataFrame({
        'station_id': ['1', '2', '3'],
        'feature_timestamp': pd.to_datetime(['2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z', '2024-01-01T02:00:00Z']),
        'target_timestamp': pd.to_datetime(['2024-01-01T01:00:00Z', '2024-01-01T02:00:00Z', '2024-01-01T03:00:00Z']),
        'horizon_minutes': [60, 60, 60], 'split': ['test'] * 3,
        'target_departures': [10.0, 12.0, 14.0],
        'demand_lag_1h': [9.0, 11.0, 13.0],
        'demand_lag_24h': [8.0, 10.0, 12.0],
        'demand_lag_168h': [7.0, 9.0, None],
    })
    predictions = pd.DataFrame({
        'station_id': dataset['station_id'],
        'feature_timestamp': dataset['feature_timestamp'],
        'target_timestamp': dataset['target_timestamp'],
        'horizon_minutes': [60, 60, 60], 'split': ['test'] * 3,
        'y_true': dataset['target_departures'], 'y_pred': [10.5, 11.5, 14.5],
    })
    report = evaluate_common_population(dataset, predictions, horizon=60)
    assert report['common_population_rows'] == 2
    assert report['rows_excluded_by_model']['seasonal_naive_168h'] == 1


def test_prediction_mismatch_blocks_reproducibility():
    run_a = {
        'horizons': [{
            'horizon_minutes': 60,
            'metrics': {'mae': 1.0, 'rmse': 2.0, 'r2': 0.5, 'n_predictions': 2, 'coverage': 1.0},
            'metadata': {'model_class': 'lightgbm.LGBMRegressor', 'random_seed': 42, 'feature_columns': ['station_id', 'demand_lag_1h'], 'categorical_columns': ['station_id']},
            'predictions': pd.DataFrame({
                'station_id': ['1', '2'],
                'feature_timestamp': pd.to_datetime(['2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z']),
                'target_timestamp': pd.to_datetime(['2024-01-01T01:00:00Z', '2024-01-01T02:00:00Z']),
                'y_true': [10.0, 12.0],
                'y_pred': [9.8, 12.1],
                'residual': [0.2, -0.1],
                'horizon_minutes': [60, 60],
                'model': ['lightgbm_regressor', 'lightgbm_regressor'],
                'split': ['test', 'test'],
            }),
        }],
        'dataset_contract_hash': 'abc',
        'dataset_artifact_hashes': {'60': 'def'},
        'target': 'target_departures',
        'model_class': 'lightgbm.LGBMRegressor',
        'random_seed': 42,
    }
    run_b = {
        **run_a,
        'horizons': [{
            **run_a['horizons'][0],
            'predictions': run_a['horizons'][0]['predictions'].copy().assign(y_pred=[10.0, 13.0]),
        }],
    }
    report = compare_execution_reproducibility(run_a, run_b)
    assert report['predictions_equal']['status'] == 'BLOCKED'
    assert report['scientific_reproducibility']['status'] == 'BLOCKED'


def test_baseline_names_are_closed_contract_set():
    assert BASELINE_NAMES == {
        'naive_1h': 'demand_lag_1h',
        'seasonal_naive_24h': 'demand_lag_24h',
        'seasonal_naive_168h': 'demand_lag_168h',
    }


def test_actual_benchmark_summary_is_scientifically_reproducible():
    predictions = pd.DataFrame({
        'station_id': ['1', '2'],
        'feature_timestamp': pd.to_datetime(['2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z']),
        'target_timestamp': pd.to_datetime(['2024-01-01T01:00:00Z', '2024-01-01T02:00:00Z']),
        'y_true': [10.0, 12.0], 'y_pred': [9.8, 12.1], 'residual': [0.2, -0.1],
        'horizon_minutes': [60, 60], 'model': ['lightgbm_regressor'] * 2, 'split': ['test'] * 2,
    })
    horizon = {
        'horizon_minutes': 60,
        'metrics': {'mae': 0.15, 'rmse': 0.158, 'r2': 0.99, 'n_predictions': 2,
                    'coverage': 1.0, 'training_time_seconds': 1.0, 'prediction_time_seconds': 0.1},
        'metadata': {'feature_columns': ['station_id', 'demand_lag_1h'],
                     'model_class': 'lightgbm.LGBMRegressor', 'random_seed': 42},
        'predictions': predictions,
    }
    run_a = {'horizons': [horizon], 'dataset_contract_hash': 'contract',
             'dataset_artifact_hashes': {'60': 'dataset'}, 'target': TARGET_COLUMN,
             'model_class': 'lightgbm.LGBMRegressor', 'random_seed': 42}
    run_b = {**run_a, 'horizons': [{**horizon, 'metrics': {
        **horizon['metrics'], 'training_time_seconds': 8.0, 'prediction_time_seconds': 0.2,
    }}]}
    report = compare_execution_reproducibility(run_a, run_b)
    assert report['scientific_reproducibility']['status'] == 'PASS'
    assert report['predictions_equal']['status'] == 'PASS'
    assert report['configuration_equal']['status'] == 'PASS'


def test_benchmark_v2_uses_identical_common_population_for_all_models(tmp_path):
    root = tmp_path
    datasets = root / 'data' / 'gold' / 'forecasting'
    datasets.mkdir(parents=True)
    contract_path = datasets / 'forecasting_contract.json'
    contract_path.write_text('{"contract": "synthetic"}', encoding='utf-8')
    benchmark_root = root / 'models' / 'experiments' / 'benchmark_v1'
    benchmark_root.mkdir(parents=True)
    (benchmark_root / 'summary.json').write_text(json.dumps({'horizons': [60, 120]}), encoding='utf-8')

    for horizon in (60, 120):
        rows = []
        predictions = []
        for index, (actual, predicted) in enumerate(((10.0, 9.0), (20.0, 18.0), (30.0, 27.0))):
            feature_time = pd.Timestamp('2024-01-01T00:00:00Z') + pd.Timedelta(hours=index)
            row = {
                'station_id': str(index + 1), 'feature_timestamp': feature_time,
                'target_timestamp': feature_time + pd.Timedelta(minutes=horizon),
                'horizon_minutes': horizon, 'split': 'test', 'target_departures': actual,
                'demand_at_feature_time': 5.0,
            }
            for lag in LAG_HOURS:
                row[f'demand_lag_{lag}h'] = None if (index == 2 and lag == 168) else actual - 1.0
            rows.append(row)
            predictions.append({
                'station_id': row['station_id'], 'feature_timestamp': feature_time,
                'target_timestamp': row['target_timestamp'], 'horizon_minutes': horizon,
                'split': 'test', 'y_true': actual, 'y_pred': predicted,
            })
        dataset_path = datasets / f'forecasting_dataset_{horizon}m.csv.gz'
        pd.DataFrame(rows).to_csv(dataset_path, index=False, compression='gzip')
        v1_horizon_root = benchmark_root / f'{horizon}m'
        v1_horizon_root.mkdir()
        (v1_horizon_root / 'metadata.json').write_text(json.dumps({
            'canonical_data_hash': AUTHORITATIVE_GOLD_HASH,
            'forecasting_dataset_hash': _sha256_file(dataset_path),
            'dataset_contract_hash': _sha256_file(contract_path),
        }), encoding='utf-8')
        pd.DataFrame(predictions).to_csv(v1_horizon_root / 'predictions.csv.gz', index=False, compression='gzip')

    report = generate_benchmark_v2(root_dir=root)
    assert report['status'] == 'PASS'
    assert report['comparability'] == 'COMPARABLE'
    assert report['scientific_reproducibility'] == 'PASS'
    for horizon in ('60', '120'):
        horizon_report = report['horizons'][horizon]
        model_rows = horizon_report['model_rows']
        common_rows = horizon_report['common_population_rows']
        assert all(count >= common_rows for count in model_rows.values())
        assert all(
            metrics['n_predictions'] == common_rows
            for metrics in horizon_report['metrics'].values()
        )
        assert horizon_report['common_population_coverage'] == (common_rows / horizon_report['target_rows'])
        assert horizon_report['target_rows'] == 3
        assert common_rows == 2
        actual_predictions = pd.read_csv(
            root / 'models' / 'experiments' / 'benchmark_v2' / f'{horizon}m' / 'lightgbm.csv.gz',
            compression='gzip',
        )
        assert not actual_predictions['y_pred'].equals(actual_predictions['y_true'])


def test_closed_non_comparable_benchmark_does_not_emit_ranking():
    metadata = {
        'comparability': 'BLOCKED',
        'status': 'CLOSED_NON_COMPARABLE',
        'scientific_reproducibility': 'PASS',
        'metrics': {
            '60': {
                'lightgbm': {'mae': 1.7430673443857811},
                'naive_1h': {'mae': 2.5384236807057463},
            }
        },
    }

    def build_reporting_payload(meta):
        payload = {
            'status': meta.get('status', 'UNKNOWN'),
            'comparability': meta.get('comparability', 'UNKNOWN'),
            'metrics': meta.get('metrics', {}),
        }
        if meta.get('comparability') == 'BLOCKED':
            return payload
        payload['winner'] = 'lightgbm'
        return payload

    payload = build_reporting_payload(metadata)
    assert payload['status'] == 'CLOSED_NON_COMPARABLE'
    assert payload['comparability'] == 'BLOCKED'
    assert 'winner' not in payload
    assert 'champion' not in payload
