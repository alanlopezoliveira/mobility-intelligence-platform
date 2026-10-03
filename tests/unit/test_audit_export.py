"""Contract checks for audit evidence, not production-pipeline fixtures."""

import json

import pandas as pd
import pytest
from scripts.build_audit_report import event_spans, parse_snapshot_bytes


def test_audit_reads_every_snapshot_and_preserves_inventory_semantics():
    snapshots = [
        {
            "_id": "2019-01-01T00:00:00",
            "stations": [{"id": 1, "dock_bikes": 2, "free_bases": 7, "total_bases": 10}],
        },
        {
            "_id": "2019-01-01T01:00:00",
            "stations": [{"id": 1, "dock_bikes": 0, "free_bases": 9, "total_bases": 10}],
        },
    ]
    frame = parse_snapshot_bytes("\n".join(map(json.dumps, snapshots)).encode(), "test.json")
    assert len(frame) == 2
    assert frame.bikes.tolist() == [2, 0]
    assert frame.free.tolist() == [7, 9]
    assert frame.time.nunique() == 2


def test_audit_does_not_silently_skip_malformed_jsonl():
    with pytest.raises(ValueError):
        parse_snapshot_bytes(b'{"stations":[]}\nnot-json', "bad.json")


def test_audit_reads_mongo_numberint_without_rewriting_quoted_names():
    payload = b'{"_id":"2018-07-01", "stations":[{"id":NumberInt(1), "dock_bikes":NumberInt(0), "name":"NumberInt(7)"}]}\n{ "_id":"2018-07-02", "stations":[]}'
    frame = parse_snapshot_bytes(payload, "legacy.json")
    assert frame.bikes.tolist() == [0]
    assert frame.name.tolist() == ["NumberInt(7)"]


def test_legacy_station_names_retain_accents():
    payload = '{"_id":"2018-08-01","stations":[{"id":1,"name":"Antón Martín"}]}'.encode("latin-1")
    assert parse_snapshot_bytes(payload, "latin1.json").name.iloc[0] == "Antón Martín"


def test_event_spans_do_not_bridge_missing_periods_or_other_stations():
    frame = pd.DataFrame(
        {
            "station": ["1"] * 5 + ["2"],
            "time": pd.to_datetime(
                [
                    "2019-01-01 00:00",
                    "2019-01-01 01:00",
                    "2019-01-01 04:00",
                    "2019-01-01 05:00",
                    "2019-01-01 06:00",
                    "2019-01-01 06:00",
                ]
            ),
        }
    )
    mask = pd.Series([True, True, True, False, True, True])
    runs = event_spans(frame, mask)
    assert runs.samples.tolist() == [2, 1, 1, 1]
    assert runs.span_minutes.tolist() == [60, 0, 0, 0]
