import pandas as pd
import pytest
from src.ml.forecast_contract import build_observation_dataset


def sample_gold() -> pd.DataFrame:
    timestamps = pd.date_range('2024-01-01T00:00:00Z', periods=20, freq='h')
    return pd.DataFrame({'provider': 'bicimad', 'station_id': '1', 'observed_at': timestamps, 'demand': range(20)})


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


def test_features_use_provider_local_calendar_and_composite_identity():
    dataset, _ = build_observation_dataset(sample_gold(), 60)
    row = dataset.loc[dataset['feature_timestamp'].eq(pd.Timestamp('2024-01-01T00:00:00Z'))].iloc[0]

    assert row['provider_id'] == 'bicimad'
    assert row['network_id'] == 'madrid'
    assert row['station_instance_id'] == 'bicimad:madrid:1'
    assert row['local_hour'] == 1
    assert not bool(row['weekend'])
    assert row['utc_offset_minutes'] == 60


def test_feature_generation_distinguishes_dst_fold_and_skips_spring_gap():
    fall_times = pd.date_range('2024-10-20T00:00:00Z', '2024-11-03T00:00:00Z', freq='h')
    fall, _ = build_observation_dataset(pd.DataFrame({
        'provider': 'bicimad', 'station_id': '1', 'observed_at': fall_times, 'demand': range(len(fall_times)),
    }), 60)
    repeated_hour = fall[
        fall['feature_timestamp'].between('2024-10-27T00:00:00Z', '2024-10-27T01:00:00Z')
    ].sort_values('feature_timestamp')
    assert repeated_hour['dst_fold'].tolist() == [0, 1]
    assert repeated_hour['utc_offset_minutes'].tolist() == [120, 60]

    spring_times = pd.date_range('2024-03-24T00:00:00Z', '2024-04-07T00:00:00Z', freq='h')
    spring, _ = build_observation_dataset(pd.DataFrame({
        'provider': 'bicimad', 'station_id': '1', 'observed_at': spring_times, 'demand': range(len(spring_times)),
    }), 60)
    spring_transition = spring[
        spring['feature_timestamp'].between('2024-03-31T00:00:00Z', '2024-03-31T02:00:00Z')
    ]
    assert spring_transition['local_hour'].tolist() == [1, 3, 4]
