import io
import shutil
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
import rarfile
from src.ingestion.bicimad import (
    ParseStats,
    Trip,
    _csv_fallback_row,
    _csv_trips,
    _iter_inner_members,
    _legacy_json_trips,
    _parse_datetime_details,
    aggregate_trips,
    parse_station_snapshot_json,
    persist_demand,
)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return output.getvalue()


CSV_TRIP = b'fecha;idTrip;trip_minutes;unlock_date;lock_date;station_unlock;station_lock\n2023-01-01;t-1;12.5;2023-01-01T00:00:00;2023-01-01T00:12:30;8;45\n'


def test_dst_policy_uses_declared_pandas_runtime_stack():
    project_file = Path(__file__).resolve().parents[2] / 'pyproject.toml'
    project = tomllib.loads(project_file.read_text(encoding='utf-8'))
    dependencies = {dependency.split('>=', 1)[0].lower() for dependency in project['project']['dependencies']}
    assert 'pandas' in dependencies


def test_recursive_archive_extracts_csv_and_preserves_provenance():
    stats = ParseStats()
    trips = list(_iter_inner_members(_zip_bytes({'trips.csv': CSV_TRIP}), 2023, 'outer.zip', stats, 'bicimad', '2023.zip'))

    assert len(trips) == 1
    assert trips[0].source_member == 'outer.zip/trips.csv'
    assert trips[0].outer_archive == '2023.zip'
    assert trips[0].provider == 'bicimad'
    assert trips[0].record_identity


def test_recursive_archive_extracts_zip_containing_csv():
    nested = _zip_bytes({'nested/trips.csv': CSV_TRIP})
    trips = list(_iter_inner_members(_zip_bytes({'movement.zip': nested}), 2023, 'outer.zip'))

    assert len(trips) == 1
    assert trips[0].source_member == 'outer.zip/movement.zip/nested/trips.csv'


def test_recursive_archive_supports_configured_depth():
    payload = _zip_bytes({'trips.csv': CSV_TRIP})
    for index in range(3):
        payload = _zip_bytes({f'level-{index}.zip': payload})

    trips = list(_iter_inner_members(payload, 2023, 'outer.zip'))
    assert len(trips) == 1


def test_recursive_archive_rejects_excessive_depth():
    payload = _zip_bytes({'trips.csv': CSV_TRIP})
    for index in range(5):
        payload = _zip_bytes({f'level-{index}.zip': payload})

    with pytest.raises(ValueError, match='maximum nested ZIP depth'):
        list(_iter_inner_members(payload, 2023, 'outer.zip'))


def test_recursive_archive_rejects_path_traversal():
    with pytest.raises(ValueError, match='unsafe archive member path'):
        list(_iter_inner_members(_zip_bytes({'../trips.csv': CSV_TRIP}), 2023, 'outer.zip'))


def test_recursive_archive_reports_irrelevant_station_metadata():
    stats = ParseStats()
    station_json = b'{"station_id":"1","available_bikes":4}\n'
    trips = list(_iter_inner_members(_zip_bytes({'stations.json': station_json}), 2023, 'outer.zip', stats))

    assert trips == []
    assert any('irrelevant station metadata' in warning for warning in stats.warnings or [])


def test_recursive_archive_deduplicates_identical_movement_payloads():
    stats = ParseStats()
    trips = list(_iter_inner_members(_zip_bytes({'a.csv': CSV_TRIP, 'b.csv': CSV_TRIP}), 2023, 'outer.zip', stats))

    assert len(trips) == 1
    assert stats.skipped_records == 1
    assert any('duplicate movement member' in warning for warning in stats.warnings or [])


def test_recursive_archive_rejects_unsupported_required_content():
    with pytest.raises(ValueError, match='unsupported required member format'):
        list(_iter_inner_members(_zip_bytes({'trips.parquet': b'data'}), 2023, 'outer.zip'))


def test_parse_datetime_uses_explicit_europe_madrid_policy():
    normal, normal_policy, _ = _parse_datetime_details('2022-01-15 12:00:00')
    ambiguous, ambiguous_policy, raw = _parse_datetime_details('2022-10-30 02:21:53')
    nonexistent, nonexistent_policy, _ = _parse_datetime_details('2022-03-27 02:21:53')
    invalid, invalid_policy, _ = _parse_datetime_details('not-a-timestamp')
    aware, aware_policy, _ = _parse_datetime_details('2022-01-15T12:00:00+02:00')

    assert normal == datetime(2022, 1, 15, 11, tzinfo=UTC)
    assert normal_policy == 'normal_local_time'
    assert ambiguous == datetime(2022, 10, 30, 1, 21, 53, tzinfo=UTC)
    assert ambiguous_policy == 'ambiguous_standard_time'
    assert raw == '2022-10-30 02:21:53'
    assert nonexistent == datetime(2022, 3, 27, 1, 21, 53, tzinfo=UTC)
    assert nonexistent_policy == 'nonexistent_shifted_forward'
    assert invalid is None and invalid_policy == 'invalid'
    assert aware == datetime(2022, 1, 15, 10, tzinfo=UTC)
    assert aware_policy == 'already_timezone_aware'


def test_station_snapshot_parser_handles_real_mongodb_style_json():
    payload = b'''
    {
      "_id": "2018-07-01T00:27:38.220760",
      "stations": [
        {
          "activate": NumberInt(1),
          "name": "Puerta del Sol A",
          "total_bases": NumberInt(24),
          "free_bases": NumberInt(10),
          "number": "1a",
          "longitude": "-3.7024255",
          "no_available": NumberInt(0),
          "address": "Puerta del Sol 1",
          "latitude": "40.4168961",
          "id": NumberInt(1)
        }
      ]
    }
    '''

    frame = parse_station_snapshot_json(payload, year=2018, member='Bicimad_Estacions_201807.json')

    assert len(frame) == 1
    assert frame.iloc[0]['station_id'] == '1'
    assert frame.iloc[0]['station_number'] == '1a'
    assert frame.iloc[0]['name'] == 'Puerta del Sol A'
    assert frame.iloc[0]['source_year'] == 2018
    assert pd.isna(frame.iloc[0]['latitude']) is False
    assert pd.isna(frame.iloc[0]['longitude']) is False


def test_csv_tracks_timestamp_resolution_and_parse_counts():
    data = b'fecha;idTrip;trip_minutes;unlock_date;lock_date;station_unlock;station_lock\n2022-10-30;t-1;12.5;2022-10-30 02:00:00;2022-10-30 02:21:53;8;45\n'
    stats = ParseStats()
    trips = list(_csv_trips(data, 2022, 'sample.csv', stats))

    assert stats.raw_records == 1
    assert stats.parsed_records == 1
    assert trips[0].ended_at_resolution == 'ambiguous_standard_time'
    assert trips[0].ended_at_raw == '2022-10-30 02:21:53'


class RecordingSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)


class RecordingSessionFactory:
    def __init__(self):
        self.session = RecordingSession()

    def begin(self):
        return self

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def test_persist_demand_replaces_only_historical_source_on_each_run(monkeypatch):
    frame = pd.DataFrame([
        {
            'timestamp': datetime(2023, 1, 1, tzinfo=UTC),
            'station_id': '1',
            'departures': 1,
            'arrivals': 0,
            'total_activity': 1,
            'net_flow': -1,
            'demand': 1,
            'source_year': 2023,
        },
    ])
    factory = RecordingSessionFactory()
    monkeypatch.setenv('PERSIST_TO_DATABASE', 'true')

    assert persist_demand(frame, session_factory=factory) == 1
    assert persist_demand(frame, session_factory=factory) == 1

    assert sum(statement.is_delete for statement in factory.session.statements) == 2
    assert sum(statement.is_insert for statement in factory.session.statements) == 2
    assert 'source_name' in str(factory.session.statements[0])


def test_persist_demand_fails_on_duplicate_canonical_keys(monkeypatch):
    frame = pd.DataFrame([
        {
            'timestamp': datetime(2023, 1, 1, 17, 0, tzinfo=UTC),
            'station_id': '225',
            'departures': 2,
            'arrivals': 0,
            'total_activity': 2,
            'net_flow': -2,
            'demand': 2,
            'source_year': 2021,
        },
        {
            'timestamp': datetime(2023, 1, 1, 17, 0, tzinfo=UTC),
            'station_id': '225',
            'departures': 0,
            'arrivals': 2,
            'total_activity': 2,
            'net_flow': 2,
            'demand': 0,
            'source_year': 2021,
        },
    ])
    factory = RecordingSessionFactory()
    monkeypatch.setenv('PERSIST_TO_DATABASE', 'true')

    with pytest.raises(ValueError, match='duplicate canonical key|Duplicate canonical keys'):
        persist_demand(frame, session_factory=factory)


@pytest.mark.skipif(shutil.which('unrar') is None and shutil.which('unrar-free') is None, reason='requires an RAR extraction tool on PATH')
def test_real_bronze_rar_member_can_be_opened_and_parsed():
    archive_path = Path(__file__).resolve().parents[2] / 'data' / 'bronze' / 'historical' / '2018.zip'
    if not archive_path.exists():
        pytest.skip('real Bronze archive not present in workspace')
    with zipfile.ZipFile(archive_path) as archive:
        rar_member = next((name for name in archive.namelist() if name.lower().endswith('.rar') and 'estacions' in name.lower()), None)
        assert rar_member is not None, 'expected a real station snapshot .rar member in the 2018 Bronze archive'
        payload = archive.read(rar_member)
    with rarfile.RarFile(io.BytesIO(payload)) as rar:
        inner = rar.infolist()[0]
        text = rar.read(inner).decode('utf-8-sig', 'replace')
        assert 'stations' in text.lower()
        frame = parse_station_snapshot_json(rar.read(inner), year=2018, member=inner.filename)
        assert len(frame) > 0


def test_legacy_json_schema_normalizes_origin_destination_and_time():
    data = b'{"user_day_code":"trip-1","idunplug_station":41,"idplug_station":50,"travel_time":30,"unplug_hourTime":{"$date":"2023-01-01T01:00:00.000+0100"}}\n'
    trips = list(_legacy_json_trips(data, 2017, 'sample.json'))
    assert trips[0].origin_station == '41'
    assert trips[0].destination_station == '50'
    assert trips[0].started_at.tzinfo == UTC
    assert trips[0].duration_minutes == 30


def test_csv_schema_normalizes_station_fields_and_delimiter():
    data = b'fecha;idTrip;trip_minutes;unlock_date;lock_date;station_unlock;station_lock\n2023-01-01;t-1;12.5;2023-01-01T00:00:00;2023-01-01T00:12:30;8;45\n'
    trips = list(_csv_trips(data, 2023, 'sample.csv'))
    assert trips[0].origin_station == '8'
    assert trips[0].destination_station == '45'
    assert trips[0].duration_minutes == 12.5


def test_csv_schema_normalizes_semicolon_corrupted_field_names():
    data = (
        b'fecha,idTrip,idBike,trip_minutes,unlo;ck_date,lock_date,station_unlock,station_lock\n'
        b'2021-10-01,t-1,1,12.5,2021-10-01T00:00:00,2021-10-01T00:12:30,8,45\n'
    )
    trips = list(_csv_trips(data, 2021, 'sample.csv'))

    assert len(trips) == 1
    assert trips[0].origin_station == '8'
    assert trips[0].destination_station == '45'
    assert trips[0].duration_minutes == 12.5


def test_csv_schema_handles_real_2021_october_malformed_row():
    row = [
        '2021-10-01',
        '05448895B_3455_2021-10-01T00:05:29',
        '3455',
        '1',
        '5.35',
        '{\'type\':;\'Point\'',
        " ;'coordinates': ;[-3.6944768",
        ';"40.4032208]}"',
        '',
        '2021-10-01T00:00:08',
        'STATION',
        'STATION',
        '{\'type\':";\'Point\'',
        ' ;\'coordinates\': ;"[-3.6903589643353483',
        ' 40.389258643579105]}"',
        '',
        '2021-10-01T00:05:29',
        '128',
        '10',
        'Palos"; de ;la ;Frontera',
        '243',
        '22',
        'Embajadores;191',
    ]
    parsed = _csv_fallback_row(row)

    assert parsed is not None
    assert parsed['unlockdate'] == '2021-10-01T00:00:08'
    assert parsed['lockdate'] == '2021-10-01T00:05:29'
    assert parsed['stationunlock'] == '128'
    assert parsed['stationlock'] == '243'


def test_csv_trips_accepts_real_2021_october_row_shape():
    data = (
        b'fecha,idTrip,idBike,fleet,trip_minutes,geolocation_unlock,address_un;lock,unlo;ck_date,locktyp;e,unlocktype,;geolocation_lock,address_lock,lock_date,station_unlock,dock_u;nlock,unl;ock_station_nam;e,station_lock,dock_lock,lock_station_name;;;;\n'
        b'2021-10-01,05448895B_3455_2021-10-01T00:05:29,3455,1,5.35,"{\'type\':;\'Point\'"," ;\'coordinates\': ;[-3.6944768",";\"40.4032208]}\"",,2021-10-01T00:00:08,STATION,STATION,"{\'type\':\";\'Point\'"," ;\'coordinates\': ;\"[-3.6903589643353483"," 40.389258643579105]}\"",,2021-10-01T00:05:29,128,10,Palos\"; de ;la ;Frontera,243,22,Embajadores;191\n'
    )
    trips = list(_csv_trips(data, 2021, 'sample.csv'))

    assert len(trips) == 1
    assert trips[0].origin_station == '128'
    assert trips[0].destination_station == '243'
    assert trips[0].duration_minutes == 5.35
    assert trips[0].started_at_raw == '2021-10-01T00:00:08'
    assert trips[0].ended_at_raw == '2021-10-01T00:05:29'


def test_aggregate_creates_hourly_departures_arrivals_and_activity():
    trips = iter(
        [
            Trip(2023, 'a', datetime(2023, 1, 1, 0, 10, tzinfo=UTC), None, '1', '2', None, 'x'),
            Trip(2023, 'b', datetime(2023, 1, 1, 0, 40, tzinfo=UTC), None, '1', '2', None, 'x'),
        ]
    )
    frame, quality = aggregate_trips(trips)
    station_one = frame[frame.station_id == '1'].iloc[0]
    station_two = frame[frame.station_id == '2'].iloc[0]
    assert station_one.departures == 2 and station_one.arrivals == 0
    assert station_two.departures == 0 and station_two.arrivals == 2
    assert station_one.total_activity == 2 and station_one.net_flow == -2
    assert quality['trip_count'] == 2


def test_aggregate_rejects_negative_duration_without_creating_rows():
    trips = iter([Trip(2023, 'bad', datetime(2023, 1, 1, tzinfo=UTC), datetime(2022, 12, 31, tzinfo=UTC), '1', '2', -1, 'x')])
    with pytest.raises(RuntimeError, match='No valid historical trips'):
        aggregate_trips(trips)
