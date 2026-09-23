import pandas as pd
import pytest
from scripts.validate_gold_db import canonical_hash, canonical_row_values
from src.data_quality.canonical_gold import _canonicalize_frame
from src.data_quality.checks import validate_dataframe
from src.data_quality.temporal_audit import (
    classify_missingness,
    direct_horizon_support,
    station_coverage,
)


def test_gold_db_canonical_serialization_is_shared_and_hashable():
    row = {
        'provider': ' BICIMAD ',
        'station_id': '00225',
        'observed_at': '2021-10-30T17:00:00+00:00',
        'departures': 2,
        'arrivals': 2,
        'total_activity': 4,
        'net_flow': 0,
        'demand': 2.0,
    }
    values = canonical_row_values(row)

    assert values == ('bicimad', '225', '2021-10-30T17:00:00Z', '2', '2', '4', '0', '2')
    assert canonical_hash([values]) == canonical_hash([values])


def test_missing_is_not_zero_or_inactive():
    frame = pd.DataFrame([
        {'station_id': '1', 'observed_at': '2024-01-01T00:00:00Z', 'demand': 0, 'total_activity': 0},
    ])

    report = classify_missingness(frame, inactive_station_ids=[])

    assert report['counts']['ZERO_DEMAND'] == 1
    assert report['counts']['INACTIVE'] == 0
    assert report['counts']['MISSING_OR_UNKNOWN'] == 0


def test_explicit_source_status_is_distinct_from_demand_absence():
    frame = pd.DataFrame([
        {'station_id': '7', 'observed_at': '2024-01-01T00:00:00Z', 'demand': 2, 'total_activity': 2},
    ])

    report = classify_missingness(frame, inactive_station_ids=['7'])

    assert report['counts']['INACTIVE'] == 1
    assert report['counts']['ZERO_DEMAND'] == 0


def test_station_coverage_keeps_gap_as_missing_interval():
    frame = pd.DataFrame([
        {'station_id': '1', 'observed_at': '2024-01-01T00:00:00Z', 'demand': 1, 'total_activity': 1},
        {'station_id': '1', 'observed_at': '2024-01-01T02:00:00Z', 'demand': 0, 'total_activity': 0},
    ])

    coverage, _ = station_coverage(frame)

    assert coverage.iloc[0]['expected_hourly_observations'] == 3
    assert coverage.iloc[0]['missing_interval_count'] == 1


def test_horizon_validation_requires_direct_source_timestamps():
    frame = pd.DataFrame([
        {'station_id': '1', 'observed_at': '2024-01-01T00:00:00Z'},
        {'station_id': '1', 'observed_at': '2024-01-01T01:00:00Z'},
    ])

    assert direct_horizon_support(frame, 60)['status'] == 'PASS'
    assert direct_horizon_support(frame, 30)['status'] == 'BLOCKED'


def test_validate_dataframe_flags_missing_and_invalid_values():
    df = pd.DataFrame(
        {
            "station_id": [1, 1, 2, 3],
            "timestamp": [
                "2024-01-01T00:00:00",
                "2024-01-01T00:00:00",
                "2024-01-01T00:00:00",
                "bad-date",
            ],
            "demand": [10, 10, -1, 5],
            "capacity": [20, 20, 15, 10],
        }
    )

    report = validate_dataframe(df)
    assert report["duplicate_rows"] >= 1
    assert report["invalid_timestamps"] >= 1
    assert report["negative_demand"] >= 1
    assert report["status"] in {"WARN", "FAIL"}


def test_canonical_gold_aggregates_directional_rows_and_preserves_identities():
    frame = pd.DataFrame([
        {'timestamp': '2021-10-30T17:00:00Z', 'station_id': '00225', 'departures': 2, 'arrivals': 0, 'source_year': 2021},
        {'timestamp': '2021-10-30T17:00:00Z', 'station_id': '225', 'departures': 0, 'arrivals': 2, 'source_year': 2021},
    ])

    canonical, stats = _canonicalize_frame(frame, 'bicimad')

    assert len(canonical) == 1
    assert canonical.iloc[0][['departures', 'arrivals', 'total_activity', 'net_flow', 'demand']].tolist() == [2, 2, 4, 0, 2]
    assert stats['duplicate_key_count_before_aggregation'] == 1
    assert canonical.duplicated(['provider', 'station_id', 'observed_at']).sum() == 0


def test_canonical_gold_rejects_malformed_or_negative_contributions():
    frame = pd.DataFrame([
        {'timestamp': '2021-10-30T17:00:00Z', 'station_id': '225', 'departures': -1, 'arrivals': 0, 'source_year': 2021},
    ])

    with pytest.raises(ValueError, match='Negative directional contribution'):
        _canonicalize_frame(frame, 'bicimad')


def test_canonical_gold_hash_input_order_is_deterministic():
    frame = pd.DataFrame([
        {'timestamp': '2021-01-01T01:00:00Z', 'station_id': '2', 'departures': 0, 'arrivals': 1, 'source_year': 2021},
        {'timestamp': '2021-01-01T00:00:00Z', 'station_id': '1', 'departures': 1, 'arrivals': 0, 'source_year': 2021},
    ])

    first, _ = _canonicalize_frame(frame, 'bicimad')
    second, _ = _canonicalize_frame(frame.iloc[::-1], 'bicimad')

    pd.testing.assert_frame_equal(first, second)
