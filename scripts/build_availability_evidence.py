from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import rarfile

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / 'data' / 'bronze' / 'historical'
GOLD = ROOT / 'data' / 'gold'

OFFICIAL_HISTORICAL_URL = 'https://datos.madrid.es/dataset/900034-0-bicimad-viajes-estaciones'
OFFICIAL_MASTER_URL = 'https://datos.madrid.es/dataset/208327-0-transporte-bicicletas-bicimad'
OFFICIAL_GBFS_URL = 'https://datos.madrid.es/dataset/900021-0-bicimad-gbfs'
OFFICIAL_DAILY_URL = 'https://datos.madrid.es/dataset/900043-0-viajes-diario-bicimad'


def _station_records(data: bytes) -> tuple[object, list[dict[str, object]]]:
    text = data.decode('utf-8-sig', 'replace')
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = json.loads(text.splitlines()[0])
    if not isinstance(payload, dict) or not isinstance(payload.get('stations'), list):
        return None, []
    return payload.get('_id'), [item for item in payload['stations'] if isinstance(item, dict)]


def _walk(data: bytes, path: str, archive_name: str) -> list[dict[str, object]]:
    if path.lower().endswith('.zip'):
        results = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.is_dir() or member.filename.startswith('__MACOSX/'):
                    continue
                results.extend(_walk(archive.read(member), f'{path}/{member.filename}', archive_name))
        return results
    if path.lower().endswith('.rar'):
        results = []
        with rarfile.RarFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                results.extend(_walk(archive.read(member), f'{path}/{member.filename}', archive_name))
        return results
    if not path.lower().endswith('.json'):
        return []
    try:
        snapshot_id, records = _station_records(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not records:
        return []
    return [{
        'source_archive': archive_name,
        'source_member': path,
        'snapshot_id': str(snapshot_id),
        **record,
    } for record in records]


def load_all_station_records() -> pd.DataFrame:
    records = []
    for archive_path in sorted(HIST.glob('*.zip')):
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir() or member.filename.startswith('__MACOSX/'):
                    continue
                lower = member.filename.lower()
                if any(token in lower for token in ('station', 'estacion')):
                    records.extend(_walk(archive.read(member), member.filename, archive_path.name))
    return pd.DataFrame(records)


def build_evidence_matrix(summary: list[dict[str, object]]) -> list[dict[str, object]]:
    snapshot_files = [item for item in summary if item.get('fields')]
    entries = []
    for item in snapshot_files:
        for field in ('activate', 'free_bases', 'total_bases', 'dock_bikes', 'no_available'):
            if field not in item['fields']:
                continue
            entries.append({
                'source_name': 'BiciMAD historical trips and station state archive',
                'official_url': OFFICIAL_HISTORICAL_URL,
                'archive_or_feed': item['archive'],
                'year_or_period': str(item['snapshot_id'])[:7],
                'member_or_file': item['file'],
                'field_name': field,
                'station_id_field': 'id',
                'timestamp_field': '_id',
                'temporal_resolution': 'one station snapshot per archive member; monthly archive coverage, not hourly series',
                'observed_value_examples': item.get('sample', {}).get(field),
                'documented_semantics': 'The official catalog describes the dataset as including anonymized trips and station state; field-level semantics are not separately documented in the downloaded member.',
                'evidence_type': 'official raw archive plus official catalog metadata',
                'supports_hourly_availability': 'NO',
                'supports_station_lifecycle': 'UNKNOWN',
                'supports_inactive_vs_unknown': 'NO',
                'limitations': 'Snapshot value is not an hourly operational history; no activation/deactivation interval or outage reason is present.',
                'confidence': 'MEDIUM' if field == 'activate' else 'HIGH for observed inventory value only',
                'production_usable': 'NO' if field != 'activate' else 'UNKNOWN',
            })
    entries.extend([
        {
            'source_name': 'BiciMAD current station master', 'official_url': OFFICIAL_MASTER_URL,
            'archive_or_feed': 'current CSV', 'year_or_period': 'current', 'member_or_file': 'configured direct CSV',
            'field_name': 'station metadata and current availability fields', 'station_id_field': 'OBJECTID',
            'timestamp_field': 'none', 'temporal_resolution': 'current snapshot', 'observed_value_examples': None,
            'documented_semantics': 'Current station master resource; not historical station status.',
            'evidence_type': 'official catalog/configuration', 'supports_hourly_availability': 'NO',
            'supports_station_lifecycle': 'NO', 'supports_inactive_vs_unknown': 'NO',
            'limitations': 'Must not substitute for historical metadata or historical operational state.',
            'confidence': 'HIGH', 'production_usable': 'NO',
        },
        {
            'source_name': 'BiciMAD GBFS', 'official_url': OFFICIAL_GBFS_URL,
            'archive_or_feed': 'official GBFS catalog resource', 'year_or_period': 'current/catalog publication',
            'member_or_file': 'not present in local Bronze inventory', 'field_name': 'station_status',
            'station_id_field': 'not inspected locally', 'timestamp_field': 'feed timestamp if supplied',
            'temporal_resolution': 'live/current feed unless historical snapshots are separately published',
            'observed_value_examples': None, 'documented_semantics': 'Official catalog identifies a GBFS feed; no historical 2017–2023 station_status snapshots are present in this repository.',
            'evidence_type': 'official catalog metadata, no local historical snapshot evidence',
            'supports_hourly_availability': 'UNKNOWN', 'supports_station_lifecycle': 'UNKNOWN',
            'supports_inactive_vs_unknown': 'UNKNOWN',
            'limitations': 'Current/live GBFS cannot be used as historical evidence; no historical feed snapshots were found locally.',
            'confidence': 'MEDIUM', 'production_usable': 'NO',
        },
        {
            'source_name': 'BiciMAD daily total trips', 'official_url': OFFICIAL_DAILY_URL,
            'archive_or_feed': 'official daily totals catalog resource', 'year_or_period': 'from 2024',
            'member_or_file': 'not present in local Bronze inventory', 'field_name': 'daily trip total',
            'station_id_field': 'none', 'timestamp_field': 'day', 'temporal_resolution': 'daily',
            'observed_value_examples': None, 'documented_semantics': 'Official catalog describes daily total trips from February 2024.',
            'evidence_type': 'official catalog metadata', 'supports_hourly_availability': 'NO',
            'supports_station_lifecycle': 'NO', 'supports_inactive_vs_unknown': 'NO',
            'limitations': 'No station-level or hourly status; outside the historical 2017–2023 target period.',
            'confidence': 'HIGH', 'production_usable': 'NO',
        },
    ])
    return sorted(entries, key=lambda entry: (entry['source_name'], entry['archive_or_feed'], entry['member_or_file'], entry['field_name']))


def build_identity_report(records: pd.DataFrame) -> dict[str, object]:
    demand = pd.read_csv(GOLD / 'station_demand_hourly.csv', usecols=['station_id'], dtype={'station_id': 'string'})
    demand_ids = set(demand['station_id'].dropna().astype(str))
    snapshot_ids = set(records['id'].dropna().astype(str)) if not records.empty else set()
    master_path = ROOT / 'data' / 'bronze' / 'bicimad_station_master.csv'
    master = pd.read_csv(master_path) if master_path.exists() else pd.DataFrame()
    master_column = next((column for column in ('OBJECTID', 'objectid', 'station_id', 'number', 'id') if column in master.columns), None)
    master_ids = set(master[master_column].dropna().astype(str)) if master_column else set()
    details = []
    for station_id in sorted(demand_ids | snapshot_ids | master_ids):
        source_presence = {
            'demand_station_id': station_id in demand_ids,
            'historical_snapshot_station_id': station_id in snapshot_ids,
            'current_master_station_id': station_id in master_ids,
        }
        snapshot_rows = records[records['id'].astype(str) == station_id] if not records.empty else pd.DataFrame()
        details.append({
            'demand_station_id': station_id,
            'historical_snapshot_station_id': station_id if station_id in snapshot_ids else None,
            'current_master_station_id': station_id if station_id in master_ids else None,
            'matching_station_number': sorted(snapshot_rows['number'].dropna().astype(str).unique().tolist()) if 'number' in snapshot_rows else [],
            'historical_names': sorted(snapshot_rows['name'].dropna().astype(str).unique().tolist()) if 'name' in snapshot_rows else [],
            'historical_coordinates': sorted({f"{lat},{lon}" for lat, lon in zip(snapshot_rows.get('latitude', []), snapshot_rows.get('longitude', []))}),
            'historical_addresses': sorted(snapshot_rows['address'].dropna().astype(str).unique().tolist()) if 'address' in snapshot_rows else [],
            'snapshot_periods': sorted(snapshot_rows['snapshot_id'].astype(str).str[:7].unique().tolist()) if not snapshot_rows.empty else [],
            'source_presence': source_presence,
            'continuity_evidence': 'UNKNOWN',
            'identity_confidence': 'PARTIAL' if sum(source_presence.values()) >= 2 else 'WEAK',
            'limitations': 'Numeric ID overlap establishes attribute/source overlap only; it does not prove physical continuity, relocation, or lifecycle identity.',
        })
    return {
        'source': 'real canonical Gold, local official historical station snapshots, current official station master',
        'demand_station_count': len(demand_ids),
        'historical_snapshot_station_count': len(snapshot_ids),
        'current_master_station_count': len(master_ids),
        'demand_without_snapshot_count': len(demand_ids - snapshot_ids),
        'snapshot_without_demand_count': len(snapshot_ids - demand_ids),
        'common_all_count': len(demand_ids & snapshot_ids & master_ids),
        'continuity_policy': 'UNKNOWN unless an official source explicitly establishes continuity; numeric ID equality is not sufficient.',
        'stations': details,
    }


def main() -> None:
    summary = json.loads((GOLD / 'station_status_field_summary.json').read_text(encoding='utf-8'))
    records = load_all_station_records()
    matrix = build_evidence_matrix(summary)
    identity = build_identity_report(records)
    (GOLD / 'availability_source_evidence.json').write_text(json.dumps(matrix, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    (GOLD / 'station_identity_evidence.json').write_text(json.dumps(identity, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    readiness_path = GOLD / 'temporal_readiness.json'
    readiness = json.loads(readiness_path.read_text(encoding='utf-8'))
    readiness.update({
        'station_lifecycle': {
            'status': 'BLOCKED',
            'evidence': {'snapshot_file_count': len(summary), 'snapshot_row_count': len(records), 'activate_values': sorted(records['activate'].dropna().astype(str).unique().tolist()) if 'activate' in records else [], 'snapshot_periods': sorted(records['snapshot_id'].astype(str).str[:7].unique().tolist()) if 'snapshot_id' in records else []},
            'limitations': 'Snapshot presence and activate=1 observations do not establish activation/deactivation dates or reasons for disappearance.',
        },
        'hourly_availability': {
            'status': 'BLOCKED',
            'evidence': {'hourly_station_status_snapshots_found': 0, 'historical_gbfs_snapshots_found': 0, 'station_snapshot_files': len(summary)},
            'limitations': 'Station snapshots are sparse archive snapshots; free_bases/dock_bikes describe inventory at snapshot time, not service availability. Current GBFS is not historical evidence.',
        },
        'station_identity': {
            'status': 'BLOCKED',
            'evidence': {'demand_station_count': identity['demand_station_count'], 'historical_snapshot_station_count': identity['historical_snapshot_station_count'], 'current_master_station_count': identity['current_master_station_count'], 'common_all_count': identity['common_all_count']},
            'limitations': 'Attribute overlap is not continuity evidence.',
        },
        'panel_formulation': {
            'status': 'BLOCKED',
            'selected_formulation': 'C: station x hourly panel plus explicit availability/missingness mask is not currently supportable; retain irregular observed panel with unknown mask until an hourly status source is proven.',
            'evidence': {'hourly_availability_status': 'BLOCKED'},
            'limitations': 'Lifecycle snapshots cannot be substituted for hourly operational availability.',
        },
        'overall_status': 'BLOCKED',
    })
    readiness_path.write_text(json.dumps(readiness, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'evidence_entries': len(matrix), 'snapshot_records': len(records), 'identity': {key: identity[key] for key in ('demand_station_count', 'historical_snapshot_station_count', 'current_master_station_count', 'demand_without_snapshot_count', 'snapshot_without_demand_count', 'common_all_count')}, 'overall_status': readiness['overall_status']}, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
