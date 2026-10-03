from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import struct
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import pandas as pd
import rarfile
import requests
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from src.config.settings import load_provider_config
from src.db.database import SessionLocal
from src.db.models import (
    ActiveDatasetVersion,
    DemandObservation,
    HistoricalStationSnapshot,
    IngestionBatch,
    NetworkDatasetVersion,
    StationInstance,
)

REQUEST_TIMEOUT = (20, 180)
BUCKET_MINUTES = 60
PROVIDER_METADATA = load_provider_config()
LOCAL_TIMEZONE = PROVIDER_METADATA.timezone
MAX_NESTED_ZIP_DEPTH = 4


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    compressed_size: int
    file_size: int
    local_offset: int
    method: int


@dataclass(frozen=True)
class Trip:
    year: int
    trip_id: str
    started_at: datetime
    ended_at: datetime | None
    origin_station: str
    destination_station: str
    duration_minutes: float | None
    source_member: str
    started_at_raw: str | None = None
    ended_at_raw: str | None = None
    started_at_resolution: str = 'not_required'
    ended_at_resolution: str = 'not_required'
    provider: str = 'bicimad'
    outer_archive: str = ''
    record_identity: str = ''


@dataclass
class ParseStats:
    raw_records: int = 0
    parsed_records: int = 0
    invalid_records: int = 0
    skipped_records: int = 0
    warnings: list[str] | None = None

    def add_warning(self, message: str) -> None:
        if self.warnings is None:
            self.warnings = []
        self.warnings.append(message)


def _remote_entries(url: str) -> list[RemoteEntry]:
    response = requests.get(url, headers={'Range': 'bytes=-4194304'}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    content = response.content
    eocd_offset = content.rfind(b'PK\x05\x06')
    if eocd_offset < 0:
        raise ValueError(f'Configured historical source is not a readable ZIP: {url}')
    _, _, _, _, total, central_size, central_offset, _ = struct.unpack_from('<4s4H2LH', content, eocd_offset)
    range_start = int(response.headers['content-range'].split()[1].split('-')[0])
    central_start = central_offset - range_start
    central = content[central_start:central_start + central_size]
    entries: list[RemoteEntry] = []
    offset = 0
    for _ in range(total):
        fields = struct.unpack_from('<4s6H3L5H2L', central, offset)
        name_len, extra_len, comment_len = fields[10:13]
        name = central[offset + 46:offset + 46 + name_len].decode('utf-8', 'replace')
        entries.append(RemoteEntry(name, fields[8], fields[9], fields[16], fields[4]))
        offset += 46 + name_len + extra_len + comment_len
    return entries


def _remote_member(url: str, entry: RemoteEntry) -> bytes:
    header = requests.get(url, headers={'Range': f'bytes={entry.local_offset}-{entry.local_offset + 4095}'}, timeout=REQUEST_TIMEOUT).content
    name_len, extra_len = struct.unpack_from('<2H', header, 26)
    data_start = entry.local_offset + 30 + name_len + extra_len
    response = requests.get(url, headers={'Range': f'bytes={data_start}-{data_start + entry.compressed_size - 1}'}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    if entry.method == 0:
        return response.content
    if entry.method == 8:
        return zlib.decompress(response.content, -15)
    raise ValueError(f'Unsupported ZIP compression method {entry.method} for {entry.name}')


def _download_outer_archive(year: int, url: str) -> Path:
    destination = Path('data/bronze/historical') / f'{year}.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and zipfile.is_zipfile(destination):
        _write_archive_manifest(destination, year, url, retrieved_at=None)
        return destination
    retrieved_at = datetime.now(UTC)
    temporary = destination.with_suffix('.zip.partial')
    existing_size = temporary.stat().st_size if temporary.exists() else 0
    headers = {'Range': f'bytes={existing_size}-'} if existing_size else {}
    response = requests.get(url, headers=headers, stream=True, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    if existing_size and response.status_code != 206:
        existing_size = 0
    mode = 'ab' if existing_size else 'wb'
    with temporary.open(mode) as output:
        for chunk in response.iter_content(1024 * 1024):
            if chunk:
                output.write(chunk)
    temporary.replace(destination)
    if not zipfile.is_zipfile(destination):
        raise ValueError(f'Downloaded historical source is not a valid ZIP: {url}')
    _write_archive_manifest(destination, year, url, retrieved_at=retrieved_at)
    return destination


def _write_archive_manifest(path: Path, year: int, url: str, retrieved_at: datetime | None) -> None:
    digest = hashlib.sha256()
    with path.open('rb') as archive:
        for chunk in iter(lambda: archive.read(1024 * 1024), b''):
            digest.update(chunk)
    manifest_path = path.with_suffix('.manifest.json')
    existing: dict[str, object] = {}
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            existing = {}
    manifest = {
        'source_year': year,
        'source_url': url,
        'archive_path': str(path),
        'archive_sha256': digest.hexdigest(),
        'archive_size_bytes': path.stat().st_size,
        'retrieved_at_utc': retrieved_at.isoformat() if retrieved_at else existing.get('retrieved_at_utc'),
        'retrieval_time_known': retrieved_at is not None or existing.get('retrieval_time_known') is True,
    }
    if existing.get('archive_sha256') == manifest['archive_sha256'] and existing.get('source_url') == url:
        return
    temporary = manifest_path.with_suffix('.json.partial')
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(manifest_path)


def _parse_datetime_details(value: object) -> tuple[datetime | None, str, str | None]:
    if value is None or value == '':
        return None, 'missing', None
    if isinstance(value, dict) and '$date' in value:
        value = value['$date']
    raw_value = str(value)
    parsed = pd.to_datetime(raw_value, errors='coerce')
    if pd.isna(parsed):
        return None, 'invalid', raw_value
    timestamp = parsed if isinstance(parsed, pd.Timestamp) else pd.Timestamp(parsed)
    if timestamp.tzinfo is not None:
        resolution = 'already_timezone_aware'
    else:
        try:
            timestamp = timestamp.tz_localize(LOCAL_TIMEZONE, ambiguous='raise', nonexistent='raise')
            resolution = 'normal_local_time'
        except Exception:  # noqa: BLE001 - pandas exposes different DST exception classes by timezone backend.
            try:
                timestamp = timestamp.tz_localize(LOCAL_TIMEZONE, ambiguous=False, nonexistent='raise')
                resolution = 'ambiguous_standard_time'
            except Exception:  # noqa: BLE001 - same backend portability issue as the first localization.
                timestamp = timestamp.tz_localize(LOCAL_TIMEZONE, nonexistent=timedelta(hours=1))
                resolution = 'nonexistent_shifted_forward'
    return timestamp.tz_convert('UTC').to_pydatetime().astimezone(UTC), resolution, raw_value


def _parse_datetime(value: object) -> datetime | None:
    return _parse_datetime_details(value)[0]


def _snapshot_period_for_year_month(value: object, year: int, member: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if re.fullmatch(r'\d{4}-\d{2}', candidate):
            return candidate
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', candidate):
            return candidate[:7]
    if isinstance(value, dict) and '$date' in value:
        return _snapshot_period_for_year_month(value['$date'], year, member)
    ts = pd.to_datetime(value, errors='coerce')
    if pd.isna(ts):
        return f'{year:04d}-01'
    return ts.strftime('%Y-%m')


def _station_id(value: object) -> str:
    text = str(value).strip()
    return text.removesuffix('.0')


def _legacy_json_trips(
    data: bytes,
    year: int,
    member: str,
    stats: ParseStats | None = None,
    provider: str = 'bicimad',
    outer_archive: str = '',
    member_identity: str = '',
) -> Iterator[Trip]:
    stream = io.TextIOWrapper(io.BytesIO(data), encoding='utf-8-sig', errors='replace')
    for index, line in enumerate(stream):
        if not line.strip():
            continue
        if stats:
            stats.raw_records += 1
        record = json.loads(line)
        started, started_resolution, started_raw = _parse_datetime_details(record.get('unplug_hourTime'))
        origin = record.get('idunplug_station')
        destination = record.get('idplug_station')
        if started is None or origin in (None, '') or destination in (None, ''):
            if stats:
                stats.invalid_records += started is None
                stats.skipped_records += 1
            continue
        duration = record.get('travel_time')
        # Legacy starts are privacy-rounded to an hour. Duration is in seconds;
        # it cannot reconstruct an exact arrival clock from that rounded start.
        ended = None
        if stats:
            stats.parsed_records += 1
        trip_id = str(record.get('user_day_code') or record.get('_id', {}).get('$oid') or f'{year}-{member}-{index}')
        yield Trip(
            year, trip_id, started, ended, _station_id(origin), _station_id(destination),
            float(duration) / 60 if duration not in (None, '') else None, member, started_raw, None,
            started_resolution, 'unknown_from_hour_rounded_start', provider, outer_archive,
            f'{member_identity}:{trip_id}:{index}',
        )


def _normalize_csv_field_name(name: object) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(name or '').strip().lower())


def _csv_required_columns_missing(fieldnames: list[str] | None) -> list[str]:
    aliases = {
        'unlockdate': 'unlock_date',
        'lockdate': 'lock_date',
        'stationunlock': 'station_unlock',
        'stationlock': 'station_lock',
    }
    normalized = {_normalize_csv_field_name(name): name for name in fieldnames or () if name is not None}
    required = {'unlockdate', 'lockdate', 'stationunlock', 'stationlock'}
    missing = required - set(normalized)
    return sorted(aliases.get(column, column) for column in missing)


def _csv_fallback_row(row: list[str]) -> dict[str, object] | None:
    if not row:
        return None
    flat: list[str] = []
    for value in row:
        flat.extend(part.strip() for part in re.split(r',', str(value)) if part.strip())
    if len(flat) < 6:
        return None
    timestamp_pattern = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')
    timestamp_positions: list[int] = [
        index for index, token in enumerate(flat) if timestamp_pattern.search(token)
    ]
    if len(timestamp_positions) < 2:
        return None
    trip_minutes_index = next(
        (index for index, token in enumerate(flat) if re.fullmatch(r'\d+(?:\.\d+)?', token) and '.' in token),
        None,
    )
    if trip_minutes_index is None:
        return None
    unlock_index = next(
        (index for index in timestamp_positions if index > trip_minutes_index),
        None,
    )
    if unlock_index is None:
        return None
    lock_index = next((index for index in timestamp_positions if index > unlock_index), None)
    if lock_index is None:
        return None
    unlock_value = flat[unlock_index]
    lock_value = flat[lock_index]
    numeric_positions = [
        (position, int(token))
        for position, token in enumerate(flat[lock_index + 1:], start=lock_index + 1)
        if re.fullmatch(r'\d+', token)
    ]
    if len(numeric_positions) < 2:
        return None
    station_tokens = [
        flat[position]
        for position, _ in sorted(numeric_positions, key=lambda item: item[1], reverse=True)[:2]
    ]
    station_unlock, station_lock = sorted(station_tokens, key=lambda value: flat.index(value))
    trip_id = next((token for token in flat[1:unlock_index] if re.search(r'[A-Za-z]', token)), 'fallback-trip')
    trip_minutes = flat[trip_minutes_index]
    return {
        'unlockdate': unlock_value,
        'lockdate': lock_value,
        'stationunlock': station_unlock,
        'stationlock': station_lock,
        'tripminutes': trip_minutes,
        'idtrip': trip_id,
    }


def _csv_trips(
    data: bytes,
    year: int,
    member: str,
    stats: ParseStats | None = None,
    provider: str = 'bicimad',
    outer_archive: str = '',
    member_identity: str = '',
) -> Iterator[Trip]:
    text = data.decode('utf-8-sig', 'replace')
    header: list[str] | None = None
    rows_iter: Iterator[tuple[int, list[str]]] | None = None
    for delimiter in (';', ','):
        csv_rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        if not csv_rows:
            continue
        header = csv_rows[0]
        normalized_fieldnames = {_normalize_csv_field_name(name): name for name in header if name is not None}
        if {'unlockdate', 'lockdate', 'stationunlock', 'stationlock'} <= set(normalized_fieldnames):
            rows_iter = (
                (index, row)
                for index, row in enumerate(csv_rows[1:])
                if row and any(value.strip() for value in row)
            )
            break
    if rows_iter is None:
        csv_rows = list(csv.reader(io.StringIO(text), delimiter=','))
        rows_iter = (
            (index, row)
            for index, row in enumerate(csv_rows[1:])
            if row and any(value.strip() for value in row)
        )
    for index, row in rows_iter or ():
        if stats:
            stats.raw_records += 1
        normalized_row: dict[str, object]
        if header is not None and len(row) == len(header):
            normalized_row = {
                _normalize_csv_field_name(key): value
                for key, value in zip(header, row)
                if key is not None
            }
        else:
            fallback_row = _csv_fallback_row(row)
            if fallback_row is None:
                if stats:
                    stats.invalid_records += 1
                    stats.skipped_records += 1
                continue
            normalized_row = fallback_row
        if any(key not in normalized_row for key in ('unlockdate', 'lockdate', 'stationunlock', 'stationlock')):
            fallback_row = _csv_fallback_row(row)
            if fallback_row is None:
                if stats:
                    stats.invalid_records += 1
                    stats.skipped_records += 1
                continue
            normalized_row = fallback_row
        started, started_resolution, started_raw = _parse_datetime_details(normalized_row.get('unlockdate'))
        origin = normalized_row.get('stationunlock')
        destination = normalized_row.get('stationlock')
        if started is None or origin in (None, '') or destination in (None, ''):
            if stats:
                stats.invalid_records += started is None
                stats.skipped_records += 1
            continue
        ended, ended_resolution, ended_raw = _parse_datetime_details(normalized_row.get('lockdate'))
        if ended_resolution == 'invalid':
            if stats:
                stats.invalid_records += 1
                stats.skipped_records += 1
            continue
        if ended_resolution == 'missing' and stats:
            stats.add_warning(f'{year}:{member}: missing lock_date at row {index}')
        duration = normalized_row.get('tripminutes')
        if stats:
            stats.parsed_records += 1
        duration_value = float(str(duration)) if duration not in (None, '') else None
        trip_id = str(normalized_row.get('idtrip') or normalized_row.get('idbike') or f'{year}-{member}-{index}')
        yield Trip(
            year, trip_id, started, ended, _station_id(origin), _station_id(destination),
            duration_value, member, started_raw, ended_raw,
            started_resolution, ended_resolution, provider, outer_archive,
            f'{member_identity}:{trip_id}:{index}',
        )


def _safe_member_path(name: str) -> str | None:
    normalized = name.replace('\\', '/')
    path = PurePosixPath(normalized)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'unsafe archive member path: {name}')
    if not normalized or normalized == '.DS_Store' or normalized.startswith('__MACOSX/'):
        return None
    return str(path)


def _looks_like_trip_json(data: bytes) -> bool:
    stream = io.TextIOWrapper(io.BytesIO(data), encoding='utf-8-sig', errors='replace')
    for line in stream:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return False
        if isinstance(record, list):
            record = record[0] if record else {}
        if not isinstance(record, dict):
            return False
        return bool({'unplug_hourTime', 'idunplug_station', 'idplug_station'} <= record.keys())
    return False


def parse_station_snapshot_json(data: bytes, year: int, member: str) -> pd.DataFrame:
    """Parse every inventory snapshot, preserving source time and service flags."""
    from src.ingestion.snapshots import parse_snapshot_bytes

    frame = parse_snapshot_bytes(data, member).rename(columns={
        'station': 'station_id', 'time': 'observed_at', 'bikes': 'available_bikes',
        'free': 'free_docks', 'active': 'activate', 'unavailable': 'no_available',
    })
    frame['source_year'] = year
    frame['source_member'] = member
    for column in ['available_bikes', 'free_docks', 'capacity', 'latitude', 'longitude']:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors='coerce')
    return frame


def _claim_member(data: bytes, year: int, member_path: str, stats: ParseStats, seen_member_ids: set[str]) -> str | None:
    member_id = hashlib.sha256(data).hexdigest()
    if member_id in seen_member_ids:
        stats.skipped_records += 1
        stats.add_warning(f'{year}:{member_path}: duplicate movement member skipped')
        return None
    seen_member_ids.add(member_id)
    return member_id


def _station_snapshot_rows_from_archive_payload(data: bytes, year: int, member: str, archive_name: str) -> pd.DataFrame:
    frame = parse_station_snapshot_json(data, year, member)
    if frame.empty:
        return frame
    frame = frame.copy()
    frame['provider'] = 'bicimad'
    frame['snapshot_period'] = pd.to_datetime(frame['observed_at'], errors='raise').dt.strftime('%Y-%m')
    frame['source_archive'] = archive_name
    frame['source_member'] = member
    frame['source_path'] = f'{archive_name}/{member}'
    frame['record_identity'] = frame.apply(
        lambda row: hashlib.sha256(
            f"{row['provider']}|{row['station_id']}|{row['snapshot_period']}|{row['source_member']}|{row['source_archive']}|{row['name']}|{row['address']}".encode()
        ).hexdigest(),
        axis=1,
    )
    return frame[[
        'provider', 'station_id', 'snapshot_period', 'station_number', 'name', 'address',
        'capacity', 'available_bikes', 'latitude', 'longitude', 'activate',
        'source_archive', 'source_member', 'source_path', 'record_identity', 'source_year'
    ]]


def persist_historical_station_snapshots(frame: pd.DataFrame, source_name: str = 'bicimad_historical_stations', session_factory=SessionLocal) -> int:
    if frame.empty:
        return 0
    if {'provider', 'station_id', 'snapshot_period', 'record_identity'} - set(frame.columns):
        raise ValueError('Historical station snapshot frame missing required metadata columns')
    rows = frame.to_dict(orient='records')
    persisted = 0
    with session_factory.begin() as session:
        for row in rows:
            record = {
                'provider': str(row.get('provider') or 'bicimad'),
                'station_id': str(row['station_id']),
                'provider_id': str(row.get('provider') or PROVIDER_METADATA.provider).strip().lower(),
                'network_id': PROVIDER_METADATA.network_id,
                'station_instance_id': f"{str(row.get('provider') or PROVIDER_METADATA.provider).strip().lower()}:{PROVIDER_METADATA.network_id}:{row['station_id']!s}",
                'snapshot_period': str(row['snapshot_period']),
                'station_number': str(row['station_number']) if row.get('station_number') not in (None, '') else None,
                'name': str(row['name']) if row.get('name') not in (None, '') else None,
                'address': str(row['address']) if row.get('address') not in (None, '') else None,
                'total_bases': int(row['capacity']) if pd.notna(row.get('capacity')) else None,
                'free_bases': int(row['available_bikes']) if pd.notna(row.get('available_bikes')) else None,
                'latitude': float(row['latitude']) if pd.notna(row.get('latitude')) else None,
                'longitude': float(row['longitude']) if pd.notna(row.get('longitude')) else None,
                'activate': int(row['activate']) if row.get('activate') not in (None, '') else None,
                'source_archive': str(row.get('source_archive') or source_name),
                'source_member': str(row.get('source_member') or ''),
                'source_path': str(row.get('source_path') or ''),
                'record_identity': str(row['record_identity']),
            }
            session.execute(
                insert(HistoricalStationSnapshot).values(**record).on_conflict_do_nothing(
                    index_elements=['provider_id', 'network_id', 'station_id', 'snapshot_period', 'source_member']
                )
            )
            persisted += 1
    return persisted


def _iter_real_station_snapshot_members() -> Iterator[tuple[int, str, str, bytes]]:
    bronze_dir = Path('data/bronze/historical')
    if not bronze_dir.exists():
        return
    for archive_path in sorted(bronze_dir.glob('*.zip')):
        with zipfile.ZipFile(archive_path) as archive:
            for member_name in archive.namelist():
                lower = member_name.lower()
                if member_name.startswith('__MACOSX/') or member_name.endswith('/') or lower == '.ds_store':
                    continue
                if not lower.endswith('.rar'):
                    continue
                payload = archive.read(member_name)
                year = int(archive_path.stem)
                yield year, archive_path.name, member_name, payload


def iter_historical_station_snapshots() -> tuple[pd.DataFrame, dict[str, object]]:
    rows: list[pd.DataFrame] = []
    summary: dict[str, object] = {
        'archive_count': 0,
        'rar_member_count': 0,
        'snapshot_row_count': 0,
        'station_count': 0,
        'snapshot_periods': [],
    }
    for year, archive_name, member_name, payload in _iter_real_station_snapshot_members():
        try:
            with rarfile.RarFile(io.BytesIO(payload)) as rar:
                members = rar.infolist()
        except rarfile.RarCannotExec as exc:
            raise RuntimeError(f'{archive_name}:{member_name}: RAR extraction unavailable in runtime: {exc}') from exc
        archive_count = summary['archive_count']
        rar_member_count = summary['rar_member_count']
        assert isinstance(archive_count, int) and isinstance(rar_member_count, int)
        summary['archive_count'] = archive_count + 1
        summary['rar_member_count'] = rar_member_count + 1
        for member in members:
            if member.filename.lower().endswith(('.json', '.txt', '.csv')):
                data = rar.read(member)
                frame = parse_station_snapshot_json(data, year, member.filename)
                if frame.empty:
                    continue
                snapshot_rows = _station_snapshot_rows_from_archive_payload(data, year, member.filename, archive_name)
                if not snapshot_rows.empty:
                    rows.append(snapshot_rows)
    if not rows:
        combined = pd.DataFrame(columns=[
            'provider', 'station_id', 'snapshot_period', 'station_number', 'name', 'address',
            'capacity', 'available_bikes', 'latitude', 'longitude', 'activate',
            'source_archive', 'source_member', 'source_path', 'record_identity', 'source_year'
        ])
        summary['snapshot_row_count'] = 0
        summary['station_count'] = 0
        return combined, summary
    combined = pd.concat(rows, ignore_index=True)
    combined = combined.drop_duplicates(subset=['provider', 'station_id', 'snapshot_period', 'source_member'], keep='last')
    summary['snapshot_row_count'] = len(combined)
    summary['station_count'] = int(combined['station_id'].nunique())
    summary['snapshot_periods'] = sorted(combined['snapshot_period'].dropna().astype(str).unique().tolist())
    return combined, summary


def _iter_inner_members(
    data: bytes,
    year: int,
    outer_member: str,
    stats: ParseStats | None = None,
    provider: str = 'bicimad',
    outer_archive: str = '',
    depth: int = 0,
    seen_member_ids: set[str] | None = None,
) -> Iterator[Trip]:
    stats = stats or ParseStats()
    seen_member_ids = seen_member_ids if seen_member_ids is not None else set()
    if depth > MAX_NESTED_ZIP_DEPTH:
        raise ValueError(f'{year}:{outer_member}: maximum nested ZIP depth exceeded')
    if data[:2] != b'PK':
        member_id = _claim_member(data, year, outer_member, stats, seen_member_ids)
        if member_id is None:
            return
        lower = outer_member.lower()
        if lower.endswith('.json') and _looks_like_trip_json(data):
            yield from _legacy_json_trips(data, year, outer_member, stats, provider, outer_archive, member_id)
            return
        if lower.endswith('.csv'):
            yield from _csv_trips(data, year, outer_member, stats, provider, outer_archive, member_id)
            return
        stats.add_warning(f'{year}:{outer_member}: irrelevant non-trip metadata skipped')
        return
    if depth >= MAX_NESTED_ZIP_DEPTH:
        raise ValueError(f'{year}:{outer_member}: maximum nested ZIP depth exceeded')
    with zipfile.ZipFile(io.BytesIO(data)) as nested:
        for name in nested.namelist():
            safe_name = _safe_member_path(name)
            if safe_name is None or safe_name.endswith('/'):
                continue
            payload = nested.read(name)
            member_path = f'{outer_member}/{safe_name}'
            lower = safe_name.lower()
            if lower.endswith('.zip'):
                before = stats.parsed_records
                yield from _iter_inner_members(
                    payload, year, member_path, stats, provider, outer_archive,
                    depth + 1, seen_member_ids,
                )
                if stats.parsed_records == before:
                    stats.add_warning(f'{year}:{member_path}: irrelevant nested archive skipped')
            elif lower.endswith('.csv'):
                member_id = _claim_member(payload, year, member_path, stats, seen_member_ids)
                if member_id is None:
                    continue
                yield from _csv_trips(
                    payload, year, member_path, stats, provider, outer_archive, member_id,
                )
            elif lower.endswith('.json'):
                member_id = _claim_member(payload, year, member_path, stats, seen_member_ids)
                if member_id is None:
                    continue
                if _looks_like_trip_json(payload):
                    yield from _legacy_json_trips(
                        payload, year, member_path, stats, provider, outer_archive, member_id,
                    )
                else:
                    stats.add_warning(f'{year}:{member_path}: irrelevant station metadata skipped')
            else:
                raise ValueError(f'{year}:{member_path}: unsupported required member format')


def iter_historical_trips() -> tuple[Iterator[Trip], dict[str, object]]:
    config = load_provider_config('bicimad')
    unsupported: list[str] = []
    inspection: dict[str, object] = {
        'selected_members': 0,
        'unsupported_members': unsupported,
        'ignored_members': [],
        'errors': [],
        'stats': ParseStats(),
        'member_stats': {},
    }
    selected: list[tuple[int, Path, str]] = []
    for source in config.historical:
        archive_path = _download_outer_archive(source.year, source.direct_url)
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
        movement_entries = [
            name for name in names
            if not name.startswith('__MACOSX/')
            and not name.endswith('/')
            and name.lower().endswith('.zip')
            and any(token in name.lower() for token in ('usage', 'movement', 'trips_'))
        ]
        movement_stems = {Path(name).stem.lower().replace('-json', '') for name in movement_entries}
        for name in names:
            lower = name.lower()
            if name.startswith('__MACOSX/') or name.endswith('/') or name == '.DS_Store':
                ignored = inspection['ignored_members']
                if isinstance(ignored, list):
                    ignored.append(f'{source.year}:{name}')
                continue
            if lower.endswith('.rar'):
                # These are station snapshots, not trip movements; demand reconstruction does not need them.
                unsupported.append(f'{source.year}:{name}')
            elif name in movement_entries or lower.endswith('.json') and 'usage' in lower and Path(name).stem.lower() not in movement_stems:
                selected.append((source.year, archive_path, name))
    inspection['selected_members'] = len(selected)
    years_with_members = {year for year, _, _ in selected}
    missing_years = [source.year for source in config.historical if source.year not in years_with_members]
    if missing_years:
        raise RuntimeError(f'No required movement members selected for years: {missing_years}')

    def generator() -> Iterator[Trip]:
        bronze_members = Path('data/bronze/historical_members')
        bronze_members.mkdir(parents=True, exist_ok=True)
        seen_member_ids: set[str] = set()
        for year, archive_path, name in selected:
            print(f'prepare-data: processing year={year} member={name}')
            member_stats = ParseStats()
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    payload = archive.read(name)
                filename = re.sub(r'[^A-Za-z0-9_.-]+', '_', f'{year}_{Path(name).name}')
                (bronze_members / filename).write_bytes(payload)
                yield from _iter_inner_members(
                    payload, year, name, member_stats, 'bicimad', archive_path.name,
                    seen_member_ids=seen_member_ids,
                )
                if member_stats.parsed_records == 0:
                    raise ValueError(f'{year}:{name}: no valid trip records were parsed')
                total_stats = inspection['stats']
                if isinstance(total_stats, ParseStats):
                    total_stats.raw_records += member_stats.raw_records
                    total_stats.parsed_records += member_stats.parsed_records
                    total_stats.invalid_records += member_stats.invalid_records
                    total_stats.skipped_records += member_stats.skipped_records
                    for warning in member_stats.warnings or []:
                        total_stats.add_warning(warning)
                member_stats_by_file = inspection['member_stats']
                if isinstance(member_stats_by_file, dict):
                    member_stats_by_file[f'{year}:{name}'] = {
                        'raw_records': member_stats.raw_records,
                        'parsed_records': member_stats.parsed_records,
                        'invalid_records': member_stats.invalid_records,
                        'skipped_records': member_stats.skipped_records,
                        'warnings': member_stats.warnings or [],
                    }
                print(f'prepare-data: member={name} raw={member_stats.raw_records} parsed={member_stats.parsed_records} skipped={member_stats.skipped_records}')
            except Exception as error:
                message = f'{year}:{name}: {type(error).__name__}: {error}'
                errors = inspection['errors']
                if isinstance(errors, list):
                    errors.append(message)
                print(f'prepare-data: ERROR {message}')
                raise RuntimeError(message) from error

    return generator(), inspection


def aggregate_trips(trips: Iterator[Trip]) -> tuple[pd.DataFrame, dict[str, int]]:
    bucket_totals: dict[tuple[datetime, str], dict[str, object]] = {}
    duplicate_ids: set[str] = set()
    seen_ids: set[str] = set()
    for trip in trips:
        identity = trip.record_identity or f'{trip.source_member}:{trip.trip_id}'
        if identity in seen_ids:
            duplicate_ids.add(trip.trip_id)
            continue
        seen_ids.add(identity)
        if trip.ended_at and trip.ended_at < trip.started_at:
            continue
        bucket = trip.started_at.replace(minute=(trip.started_at.minute // BUCKET_MINUTES) * BUCKET_MINUTES, second=0, microsecond=0)
        origin_key = (bucket, trip.origin_station)
        events = [(origin_key, 1, 0)]
        if trip.ended_at is not None:
            arrival_bucket = trip.ended_at.replace(minute=(trip.ended_at.minute // BUCKET_MINUTES) * BUCKET_MINUTES, second=0, microsecond=0)
            events.append(((arrival_bucket, trip.destination_station), 0, 1))
        for key, departures, arrivals in events:
            entry = bucket_totals.setdefault(key, {
                'timestamp': key[0],
                'station_id': key[1],
                'departures': 0,
                'arrivals': 0,
                'source_year': trip.year,
            })
            current_departures = entry['departures']
            current_arrivals = entry['arrivals']
            source_year = entry['source_year']
            assert isinstance(current_departures, int) and isinstance(current_arrivals, int)
            assert isinstance(source_year, int)
            entry['departures'] = current_departures + departures
            entry['arrivals'] = current_arrivals + arrivals
            entry['source_year'] = min(source_year, trip.year)
    if not bucket_totals:
        raise RuntimeError('No valid historical trips were parsed from configured sources')

    frame = pd.DataFrame(bucket_totals.values())
    if frame.empty:
        raise RuntimeError('No valid historical trips were parsed from configured sources')

    duplicate_keys = frame[['timestamp', 'station_id']]
    duplicate_keys = duplicate_keys[duplicate_keys.duplicated(subset=['timestamp', 'station_id'], keep=False)]
    if not duplicate_keys.empty:
        conflicting = duplicate_keys.drop_duplicates().sort_values(['timestamp', 'station_id']).head(10).to_dict(orient='records')
        raise ValueError(f'Duplicate canonical keys detected after aggregation: {conflicting}')

    grouped = frame.groupby(['timestamp', 'station_id'], as_index=False).agg({'departures': 'sum', 'arrivals': 'sum', 'source_year': 'min'})
    grouped['total_activity'] = grouped['departures'] + grouped['arrivals']
    grouped['net_flow'] = grouped['arrivals'] - grouped['departures']
    grouped['demand'] = grouped['departures']
    grouped = grouped.sort_values(['timestamp', 'station_id']).reset_index(drop=True)

    if grouped['departures'].isna().any() or grouped['arrivals'].isna().any():
        raise ValueError('Canonical demand contains null directional totals')
    if grouped['total_activity'].ne(grouped['departures'] + grouped['arrivals']).any():
        raise ValueError('Canonical demand violates total_activity = departures + arrivals')
    if grouped['net_flow'].ne(grouped['arrivals'] - grouped['departures']).any():
        raise ValueError('Canonical demand violates net_flow = arrivals - departures')
    return grouped, {'trip_count': len(seen_ids), 'duplicate_trip_count': len(duplicate_ids)}


def persist_demand(
    frame: pd.DataFrame,
    source_name: str = 'bicimad_historical_trips',
    session_factory=SessionLocal,
    batch_size: int = 10000,
) -> int:
    if os.getenv('PERSIST_TO_DATABASE', 'false').lower() != 'true':
        return 0
    if frame.empty:
        raise ValueError('Cannot replace historical demand with an empty frame')

    required_cols = {'timestamp', 'station_id', 'departures', 'arrivals', 'total_activity', 'net_flow', 'demand'}
    missing_cols = sorted(required_cols - set(frame.columns))
    if missing_cols:
        raise ValueError(f'Historical demand frame missing required columns: {missing_cols}')

    duplicate_members = frame[frame.duplicated(subset=['timestamp', 'station_id'], keep=False)]
    if not duplicate_members.empty:
        conflicting = duplicate_members[['timestamp', 'station_id']].drop_duplicates().sort_values(['timestamp', 'station_id']).head(10).to_dict(orient='records')
        raise ValueError(f'Duplicate canonical keys detected before persistence: {conflicting}')

    provider_id = PROVIDER_METADATA.provider.lower()
    network_id = PROVIDER_METADATA.network_id
    records = []
    station_ranges: dict[str, tuple[datetime, datetime]] = {}
    digest = hashlib.sha256()
    for record in frame.sort_values(['station_id', 'timestamp']).to_dict(orient='records'):
        station_id = str(record['station_id'])
        timestamp = record['timestamp'].to_pydatetime() if hasattr(record['timestamp'], 'to_pydatetime') else record['timestamp']
        if timestamp.tzinfo is None:
            raise ValueError('Canonical observation timestamps must be timezone-aware UTC instants')
        timestamp = timestamp.astimezone(UTC)
        station_instance_id = f'{provider_id}:{network_id}:{station_id}'
        departures, arrivals = int(record['departures']), int(record['arrivals'])
        total_activity, net_flow, demand = int(record['total_activity']), int(record['net_flow']), int(record['demand'])
        if min(departures, arrivals) < 0 or total_activity != departures + arrivals or net_flow != arrivals - departures or demand != departures:
            raise ValueError(f'Canonical count invariant failed for station {station_id} at {timestamp.isoformat()}')
        original_time_text = str(record['timestamp'])
        source_record_id = f'{station_instance_id}:{timestamp.isoformat()}'
        row_hash_text = '|'.join((provider_id, network_id, station_instance_id, original_time_text, str(departures), str(arrivals), str(total_activity), str(net_flow), str(demand)))
        source_record_hash = hashlib.sha256(row_hash_text.encode('utf-8')).hexdigest()
        values = {
            'station_id': station_id,
            'provider_id': provider_id,
            'network_id': network_id,
            'station_instance_id': station_instance_id,
            'observed_at': timestamp,
            'departures': departures,
            'arrivals': arrivals,
            'total_activity': total_activity,
            'net_flow': net_flow,
            'demand': float(demand),
            'source_name': source_name,
            'source_record_id': source_record_id,
            'source_record_hash': source_record_hash,
            'original_time_text': original_time_text,
        }
        digest.update(row_hash_text.encode('utf-8'))
        digest.update(b'\n')
        records.append(values)
        first, last = station_ranges.get(station_instance_id, (timestamp, timestamp))
        station_ranges[station_instance_id] = (min(first, timestamp), max(last, timestamp))

    content_hash = digest.hexdigest()
    batch_id = content_hash
    archive_manifest = []
    bronze = Path('data/bronze/historical')
    for manifest_path in sorted(bronze.glob('*.manifest.json')):
        archive_manifest.append(json.loads(manifest_path.read_text(encoding='utf-8')))
    manifest_json = json.dumps({
        'canonical_rows_sha256': content_hash,
        'expected_rows': len(records),
        'source_archives': archive_manifest,
        'provider_id': provider_id,
        'network_id': network_id,
        'provider_timezone': PROVIDER_METADATA.timezone,
    }, sort_keys=True)
    with session_factory.begin() as session:
        existing = session.execute(
            select(IngestionBatch).where(IngestionBatch.batch_id == batch_id)
        ).scalar_one_or_none()
        if existing is not None and existing.status == 'published':
            return len(records)
        if existing is None:
            session.add(IngestionBatch(
                batch_id=batch_id,
                source_name=source_name,
                content_sha256=content_hash,
                source_manifest_json=manifest_json,
                expected_rows=len(records),
                status='loading',
                created_at=datetime.now(UTC),
                published_at=None,
            ))
        else:
            existing.status = 'loading'
            existing.expected_rows = len(records)
            existing.source_manifest_json = manifest_json
            existing.published_at = None
        for instance_id, (first_seen, last_seen) in station_ranges.items():
            session.execute(insert(StationInstance).values(
                station_instance_id=instance_id,
                provider_id=provider_id,
                network_id=network_id,
                provider_station_id=instance_id.rsplit(':', 1)[-1],
                valid_from=first_seen,
                valid_to=last_seen + timedelta(minutes=BUCKET_MINUTES),
                identity_provenance='Source station ID instance; physical-place continuity is not asserted.',
            ).on_conflict_do_nothing(index_elements=['station_instance_id']))
        # Only rows from this inactive content-addressed batch can be replaced on retry.
        session.execute(delete(DemandObservation).where(DemandObservation.batch_id == batch_id))
        for start in range(0, len(records), batch_size):
            batch_values = [{**record, 'batch_id': batch_id} for record in records[start:start + batch_size]]
            session.execute(insert(DemandObservation), batch_values)

    try:
        with session_factory.begin() as session:
            persisted = session.execute(
                select(func.count()).select_from(DemandObservation).where(DemandObservation.batch_id == batch_id)
            ).scalar_one()
            invalid = session.execute(
                select(func.count()).select_from(DemandObservation).where(
                    DemandObservation.batch_id == batch_id,
                    (DemandObservation.departures < 0)
                    | (DemandObservation.arrivals < 0)
                    | (DemandObservation.total_activity != DemandObservation.departures + DemandObservation.arrivals)
                    | (DemandObservation.net_flow != DemandObservation.arrivals - DemandObservation.departures)
                    | (DemandObservation.demand != DemandObservation.departures)
                    | (func.length(DemandObservation.source_record_hash) != 64),
                )
            ).scalar_one()
            if persisted != len(records) or invalid:
                session.execute(
                    select(IngestionBatch).where(IngestionBatch.batch_id == batch_id).with_for_update()
                ).scalar_one().status = 'failed'
                raise RuntimeError('Persisted demand batch failed count or invariant validation; active version unchanged')
            session.execute(
                select(IngestionBatch).where(IngestionBatch.batch_id == batch_id).with_for_update()
            ).scalar_one().status = 'validated'

        with session_factory.begin() as session:
            batch = session.execute(
                select(IngestionBatch).where(IngestionBatch.batch_id == batch_id).with_for_update()
            ).scalar_one()
            if batch.status != 'validated':
                raise RuntimeError('Only a validated demand batch may be published')
            session.execute(insert(ActiveDatasetVersion).values(
                singleton_id=1, batch_id=batch_id
            ).on_conflict_do_update(
                index_elements=['singleton_id'], set_={'batch_id': batch_id}
            ))
            batch.status = 'published'
            batch.published_at = datetime.now(UTC)
            session.execute(insert(NetworkDatasetVersion).values(
                provider_id=provider_id, network_id=network_id, batch_id=batch_id,
            ).on_conflict_do_update(
                index_elements=['provider_id', 'network_id'], set_={'batch_id': batch_id},
            ))
    except Exception:
        with session_factory.begin() as session:
            batch = session.execute(
                select(IngestionBatch).where(IngestionBatch.batch_id == batch_id).with_for_update()
            ).scalar_one_or_none()
            if batch is not None and batch.status == 'loading':
                batch.status = 'failed'
        raise
    return len(records)
