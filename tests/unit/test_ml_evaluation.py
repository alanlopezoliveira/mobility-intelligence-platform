import pandas as pd
import pytest
from src.ml.evaluation import chronological_split, lag_features


def test_chronological_split_orders_without_overlap():
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=10, freq="h"),
            "demand": range(10),
        }
    )
    split = chronological_split(frame)
    assert split.train["timestamp"].max() < split.validation["timestamp"].min()
    assert split.validation["timestamp"].max() < split.test["timestamp"].min()


def test_chronological_split_rejects_duplicate_timestamps():
    frame = pd.DataFrame({"timestamp": ["2024-01-01", "2024-01-01"], "demand": [1, 2]})
    with pytest.raises(ValueError, match="duplicate timestamps"):
        chronological_split(frame)


def test_lag_features_use_only_prior_group_values():
    frame = pd.DataFrame(
        {
            "station_id": ["a", "a", "b", "b"],
            "timestamp": pd.to_datetime(["2024-01-01 01:00", "2024-01-01 02:00", "2024-01-01 01:00", "2024-01-01 02:00"]),
            "demand": [1, 2, 10, 20],
        }
    )
    result = lag_features(frame, "demand", "station_id", [1])
    assert result.loc[(result.station_id == "a") & (result.demand == 2), "demand_lag_1"].item() == 1
    assert result.loc[(result.station_id == "b") & (result.demand == 20), "demand_lag_1"].item() == 10
