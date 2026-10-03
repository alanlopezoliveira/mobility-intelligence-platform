from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from src.cli.main import _canonicalize_historical_demand, evaluate, prepare_data, train
from src.ingestion.bicimad import Trip
from src.ml.cli_readiness import ForecastingState


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, headers: dict[str, str] | None = None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/csv"}


def fake_historical_trips():
    return iter([Trip(2023, "trip-1", datetime(2023, 1, 1, tzinfo=UTC), None, "1", "2", None, "sample")]), {"selected_members": 1, "unsupported_members": []}


def test_prepare_data_creates_local_artifacts(monkeypatch, tmp_path):
    csv_text = "number;Name;NoAvailable;TotalBase;PosicionSTR\n1;Metro Callao;2;27;-3.7056899999999997 40.4204\n2;Sol;3;18;-3.702 40.416\n"

    monkeypatch.setattr("src.cli.main.REQUEST_TIMEOUT", 15)
    monkeypatch.setattr(
        "src.cli.main.requests.get",
        lambda url, timeout: FakeResponse(csv_text, status_code=200, headers={"content-type": "text/csv"}),
    )
    monkeypatch.setattr("src.cli.main.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.cli.main.iter_historical_trips", fake_historical_trips)
    monkeypatch.setenv("PERSIST_TO_DATABASE", "true")
    monkeypatch.setattr("src.cli.main._persist_stations", lambda normalized, source_name: len(normalized))
    monkeypatch.setattr("src.cli.main.persist_demand", lambda frame: len(frame))

    prepare_data()

    assert (tmp_path / "data" / "bronze" / "bicimad_station_master.csv").exists()
    assert (tmp_path / "data" / "silver" / "bicimad_station_master.csv").exists()
    assert (tmp_path / "data" / "gold" / "station_features.csv").exists()
    assert (tmp_path / "data" / "gold" / "historical_demand_complete.json").exists()


def test_historical_demand_normalizes_and_merges_canonical_station_keys():
    timestamp = pd.Timestamp('2021-10-30T17:00:00Z')
    source = pd.DataFrame([
        {'timestamp': timestamp, 'station_id': '00225', 'departures': 2, 'arrivals': 0, 'source_year': 2021},
        {'timestamp': timestamp, 'station_id': '225', 'departures': 0, 'arrivals': 2, 'source_year': 2021},
    ])

    canonical = _canonicalize_historical_demand(source)

    assert len(canonical) == 1
    assert canonical.iloc[0]['station_id'] == '225'
    assert canonical.iloc[0][['departures', 'arrivals', 'total_activity', 'net_flow', 'demand']].tolist() == [2, 2, 4, 0, 2]


def test_prepare_data_database_failure_leaves_no_completion_marker(monkeypatch, tmp_path):
    csv_text = "number;Name;NoAvailable;TotalBase;PosicionSTR\n1;Metro Callao;2;27;-3.70 40.42\n"
    monkeypatch.setattr("src.cli.main.requests.get", lambda url, timeout: FakeResponse(csv_text))
    monkeypatch.setattr("src.cli.main.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.cli.main.iter_historical_trips", fake_historical_trips)
    monkeypatch.setenv("PERSIST_TO_DATABASE", "true")
    monkeypatch.setattr("src.cli.main._persist_stations", lambda normalized, source_name: len(normalized))

    def fail_persistence(frame):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("src.cli.main.persist_demand", fail_persistence)

    with pytest.raises(RuntimeError, match="database unavailable"):
        prepare_data()

    assert not (tmp_path / "data" / "gold" / "historical_demand_complete.json").exists()
    assert not (tmp_path / "data" / "gold" / "station_demand_hourly.csv").exists()
    assert not (tmp_path / "data" / "silver" / "historical_trip_observations.csv").exists()


def test_train_and_evaluate_do_not_use_station_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr("src.cli.main.REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        "src.cli.main.inspect_forecasting_state",
        lambda root: ForecastingState("DATA_NOT_PREPARED", "canonical demand missing", {}),
    )
    gold_path = tmp_path / "data" / "gold" / "station_features.csv"
    gold_path.parent.mkdir(parents=True, exist_ok=True)
    gold_path.write_text(
        "station_id,capacity,available_bikes,station_score\n"
        "1,27,2,15\n"
        "2,18,3,10\n"
        "3,25,5,20\n"
        "4,30,4,18\n",
        encoding="utf-8",
    )

    result = train()
    assert result["status"] == "DATA_NOT_PREPARED"
    metrics = evaluate()
    assert metrics["status"] == "DATA_NOT_PREPARED"
    assert not (tmp_path / "models" / "production").exists()
    assert not (tmp_path / "models" / "metadata").exists()


def test_ready_training_boundary_never_promotes_or_creates_model(monkeypatch, tmp_path):
    monkeypatch.setattr("src.cli.main.REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        "src.cli.main.inspect_forecasting_state",
        lambda root: ForecastingState("FORECASTING_DATA_READY", "validated artifacts", {}),
    )

    result = train()
    evaluation = evaluate()

    assert result["status"] == "FORECASTING_DATA_READY"
    assert result["training"] == "deferred"
    assert evaluation["evaluation_status"] == "NO_PRODUCTION_MODEL"
    assert not (tmp_path / "models" / "production").exists()
    assert not (tmp_path / "models" / "experiments").exists()


def test_missing_contract_state_creates_no_model_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr("src.cli.main.REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        "src.cli.main.inspect_forecasting_state",
        lambda root: ForecastingState(
            "FORECASTING_DATA_NOT_READY", "forecasting contract is missing", {"problems": ["missing contract"]}
        ),
    )

    result = train()

    assert result["status"] == "FORECASTING_DATA_NOT_READY"
    assert not (tmp_path / "models" / "production").exists()
    assert not (tmp_path / "models" / "experiments").exists()


def test_prepare_data_handles_bom_and_totalbases(monkeypatch, tmp_path):
    csv_text = (
        "\ufeffnumber;Activate;Address;PosicionSTR;Name;NoAvailable;TotalBases\n"
        "1;1;Calle A;-3.70 40.42;Station A;2;27\n"
        "2;1;Calle B;-3.71 40.43;Station B;3;18\n"
    )

    monkeypatch.setattr("src.cli.main.REQUEST_TIMEOUT", 15)
    monkeypatch.setattr(
        "src.cli.main.requests.get",
        lambda url, timeout: FakeResponse(csv_text, status_code=200, headers={"content-type": "text/csv"}),
    )
    monkeypatch.setattr("src.cli.main.REPO_ROOT", tmp_path)
    monkeypatch.setattr("src.cli.main.iter_historical_trips", fake_historical_trips)
    monkeypatch.setenv("PERSIST_TO_DATABASE", "true")
    monkeypatch.setattr("src.cli.main._persist_stations", lambda normalized, source_name: len(normalized))
    monkeypatch.setattr("src.cli.main.persist_demand", lambda frame: len(frame))

    summary = prepare_data()

    assert summary["gold_rows"] >= 2
    gold_path = tmp_path / "data" / "gold" / "station_features.csv"
    gold = pd.read_csv(gold_path)
    assert {"capacity", "available_bikes", "station_score"}.issubset(gold.columns)
    assert gold['available_bikes'].isna().all()
    assert gold['station_score'].isna().all()
