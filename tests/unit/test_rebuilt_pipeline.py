"""Regression checks for time, inventory, weather and forecast availability bugs."""

import json
import zipfile
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from src.cli.main import _coerce_numeric
from src.ingestion.bicimad import Trip, aggregate_trips, parse_station_snapshot_json
from src.ingestion.rebuild import ingest_year, local_times, normalize_chunk
from src.ingestion.weather import historical_weather
from src.ml.rebuilt import feature_table
from src.ml.weather_analysis import compare_weather


def test_arrivals_cross_hour_independently_of_departures():
    raw = pd.DataFrame(
        {
            "unlock_date": ["2022-01-01T12:59:00"],
            "lock_date": ["2022-01-01T13:10:00"],
            "station_unlock": ["1"],
            "station_lock": ["2"],
        }
    )
    frame, quality = normalize_chunk(raw)
    assert frame.loc[(pd.Timestamp("2022-01-01T11:00Z"), 1), "departures"] == 1
    assert frame.loc[(pd.Timestamp("2022-01-01T12:00Z"), 2), "arrivals"] == 1
    assert quality["rejected_arrivals"] == 0


def test_missing_arrival_is_never_assigned_to_departure_hour():
    trips = [Trip(2022, "a", datetime(2022, 1, 1, tzinfo=UTC), None, "1", "2", None, "x")]
    frame, _ = aggregate_trips(trips)
    assert frame.departures.sum() == 1
    assert frame.arrivals.sum() == 0
    assert "2" not in frame.station_id.tolist()


def test_dst_ambiguity_is_rejected_not_shifted():
    result = local_times(
        pd.Series(["2022-10-30T02:30:00", "2022-03-27T02:30:00", "2022-07-01T12:00:00"])
    )
    assert result.isna().tolist() == [True, True, False]
    assert result.iloc[2] == pd.Timestamp("2022-07-01T10:00Z")


def test_inventory_keeps_all_times_and_correct_bike_field():
    snapshots = [
        {
            "_id": f"2018-07-01T0{i}:00:00",
            "stations": [{"id": 1, "dock_bikes": i, "free_bases": 20 - i, "total_bases": 20}],
        }
        for i in range(2)
    ]
    frame = parse_station_snapshot_json(
        "\n".join(map(json.dumps, snapshots)).encode(), 2018, "test"
    )
    assert frame.available_bikes.tolist() == [0, 1]
    assert frame.free_docks.tolist() == [20, 19]
    assert frame.observed_at.nunique() == 2


def test_decimal_dot_coordinates_are_not_destroyed():
    assert _coerce_numeric(pd.Series(["-3.703", "40,42", "1.234,5"])).tolist() == [
        -3.703,
        40.42,
        1234.5,
    ]


def test_forecast_lags_are_time_aligned_and_end_before_issue():
    timestamps = pd.date_range("2022-01-01", periods=210, freq="h", tz="UTC")
    panel = pd.DataFrame({"time": timestamps, "station": 1, "departures": np.arange(210)})
    frame = feature_table(panel, 1)
    row = frame.iloc[0]
    assert row.y == 169 and row.recent == 167 and row.day == 145 and row.week == 1
    assert row.mean_24h == np.mean(np.arange(144, 168))
    assert row.mean_7d == np.mean(np.arange(168))
    assert row.issue == row.target - pd.Timedelta(hours=1)
    # Removing the precise recent hour must drop the row; another row cannot stand in for it.
    missing = feature_table(panel[panel.departures.ne(167)], 1)
    assert row.target not in missing.target.tolist()
    changed = panel.copy()
    changed.loc[changed.time >= row.issue, "departures"] += 1000
    later = feature_table(changed, 1).set_index("target").loc[row.target]
    assert later.recent == row.recent and later.day == row.day
    assert later.mean_24h == row.mean_24h and later.movements_24h == row.movements_24h


def test_arrivals_survive_missing_origins_and_counters_reconcile():
    raw = pd.DataFrame(
        {
            "unlock_date": [None, "2022-01-01T12:00", "2022-01-01T14:00", "bad"],
            "lock_date": ["2022-01-01T13:00"] * 4,
            "station_unlock": ["1", "invalid", "1", "1"],
            "station_lock": ["2", "2", "2", "0"],
        }
    )
    counts, quality = normalize_chunk(raw)
    assert counts.departures.sum() == 1
    assert counts.arrivals.sum() == 2  # Missing start and bad origin are independent.
    assert quality["arrivals_without_valid_departure"] == 2
    assert quality["reversed_arrival_time"] == 1
    for event in ["departures", "arrivals"]:
        assert quality[event] + quality[f"rejected_{event}"] == quality["raw_rows"]


def test_duplicate_counters_include_duplicates_in_same_chunk(tmp_path):
    folder = tmp_path / "data/bronze/historical_members"
    folder.mkdir(parents=True)
    row = "2022-01-01T12:00;2022-01-01T13:00;1;2\n"
    with zipfile.ZipFile(folder / "2022_example-csv.zip", "w") as archive:
        archive.writestr(
            "trips.csv", "unlock_date;lock_date;station_unlock;station_lock\n" + row * 2
        )
    counts, _, sources = ingest_year(tmp_path, 2022)
    quality = sources[0]["quality"]
    assert counts.departures.sum() == counts.arrivals.sum() == 1
    assert quality["source_rows"] == 2 and quality["duplicate_rows"] == 1


def test_rain_accumulation_matches_trip_interval_not_next_hour():
    times = pd.date_range("2022-01-03T12:00Z", periods=5, freq="7D")
    network = pd.DataFrame({"time": times, "departures": [100, 100, 100, 100, 50]})
    weather = pd.DataFrame(
        {"time": times + pd.Timedelta(hours=1), "rain": [0, 0, 0, 0, 1], "temperature_2m": 10}
    )
    result, joined = compare_weather(network, weather)
    assert len(joined) == 5 and result["rainy_hours"] == 1
    assert result["raw_change_pct"] == -50
    assert result["adjusted_change_pct"] == -50


def test_ingestion_cache_rejects_corruption_and_duplicate_archive(tmp_path):
    folder = tmp_path / "data/bronze/historical_members"
    folder.mkdir(parents=True)
    archive = folder / "2022_one-csv.zip"
    csv = "unlock_date;lock_date;station_unlock;station_lock;unlock_station_name;geolocation_unlock\n2022-01-01T12:59:00;2022-01-01T13:10:00;1;2;Test;{'coordinates': [-3.7, 40.4]}\n"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("trips.csv", csv)
    (folder / "2022_copy-csv.zip").write_bytes(archive.read_bytes())
    first, _, sources = ingest_year(tmp_path, 2022)
    assert first.departures.sum() == 1 and len(sources) == 1
    _, _, cached = ingest_year(tmp_path, 2022)
    assert cached[0]["cache_hit"]
    artifact = next((tmp_path / "data/rebuilt/cache").glob("*.csv.gz"))
    artifact.write_bytes(b"broken gzip")
    repaired, _, rebuilt = ingest_year(tmp_path, 2022)
    assert not rebuilt[0]["cache_hit"] and repaired.departures.sum() == 1


def test_weather_uses_validated_cache_without_a_second_network_call(monkeypatch, tmp_path):
    times = pd.date_range("2022-01-01", "2023-01-01", inclusive="left", freq="h", tz="UTC")
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "latitude": 40.5,
                "longitude": -3.75,
                "hourly_units": {"rain": "mm"},
                "hourly": {
                    "time": (times.as_unit("s").astype("int64")).tolist(),
                    "rain": [0.0] * len(times),
                    "precipitation": [0.0] * len(times),
                    "temperature_2m": [10.0] * len(times),
                    "wind_speed_10m": [2.0] * len(times),
                },
            }

    def get(*args, **kwargs):
        calls.append(kwargs["params"])
        return Response()

    monkeypatch.setattr("requests.Session.get", get)
    first, meta = historical_weather(40.4, -3.7, 2022, tmp_path)
    second, cached = historical_weather(40.4, -3.7, 2022, tmp_path)
    assert len(first) == len(second) == 8760 and len(calls) == 1
    assert cached["cache_hit"] and cached["sha256"] == meta["sha256"]
