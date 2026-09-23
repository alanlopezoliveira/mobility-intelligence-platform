import pandas as pd
import pytest
from src.ml.forecast_contract import build_observation_dataset


def sample_gold() -> pd.DataFrame:
    timestamps = pd.date_range('2024-01-01T00:00:00Z', periods=20, freq='h')
    return pd.DataFrame({'station_id': '1', 'observed_at': timestamps, 'demand': range(20)})


def test_exact_horizons_and_missing_target_policy():
    dataset, report = build_observation_dataset(sample_gold(), 60)

    assert len(dataset) > 3
    assert dataset.iloc[0]['target_departures'] == 1
    assert report['target_unobserved_rows'] == 1
    with pytest.raises(ValueError, match='Only 60-minute and 120-minute'):
        build_observation_dataset(sample_gold(), 30)


def test_exact_lag_and_availability_semantics():
    dataset, _ = build_observation_dataset(sample_gold(), 60)

    row = dataset.loc[dataset['feature_timestamp'].eq(pd.Timestamp('2024-01-01T02:00:00Z'))].iloc[0]
    assert row['demand_lag_1h'] == 1
    assert row['demand_lag_1h_available']
    assert pd.isna(row['demand_lag_6h'])
    assert not row['demand_lag_6h_available']


def test_target_is_not_a_feature():
    dataset, _ = build_observation_dataset(sample_gold(), 60)

    assert 'target_departures' not in [column for column in dataset.columns if column.startswith('demand_') and column != 'demand_at_feature_time']