from __future__ import annotations

import io
import json
import zipfile
from collections import Counter
from pathlib import Path

import rarfile

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / 'data' / 'bronze' / 'historical'


def json_station_summary(data: bytes, file_name: str, archive_name: str) -> dict[str, object] | None:
    text = data.decode('utf-8-sig', 'replace')
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            payload = json.loads(text.splitlines()[0])
        except (json.JSONDecodeError, IndexError):
            return None
    if not isinstance(payload, dict) or not isinstance(payload.get('stations'), list):
        return None
    stations = [item for item in payload['stations'] if isinstance(item, dict)]
    if not stations:
        return None
    fields = sorted(set().union(*(item.keys() for item in stations)))
    activate = Counter(str(item.get('activate')) for item in stations if 'activate' in item)
    return {
        'archive': archive_name,
        'file': file_name,
        'snapshot_id': payload.get('_id'),
        'station_row_count': len(stations),
        'fields': fields,
        'activate_values': dict(activate),
        'sample': stations[0],
    }


def inspect_member(data: bytes, member_name: str, archive_name: str) -> list[dict[str, object]]:
    lower = member_name.lower()
    if lower.endswith('.zip'):
        output = []
        with zipfile.ZipFile(io.BytesIO(data)) as nested:
            for member in nested.infolist():
                if member.is_dir() or member.filename.startswith('__MACOSX/'):
                    continue
                output.extend(inspect_member(nested.read(member), f'{member_name}/{member.filename}', archive_name))
        return output
    if lower.endswith('.rar'):
        output = []
        with rarfile.RarFile(io.BytesIO(data)) as nested:
            for member in nested.infolist():
                if member.is_dir():
                    continue
                output.extend(inspect_member(nested.read(member), f'{member_name}/{member.filename}', archive_name))
        return output
    if lower.endswith('.json'):
        summary = json_station_summary(data, member_name, archive_name)
        return [summary] if summary else []
    return []


def main() -> None:
    summaries = []
    for archive_path in sorted(HIST.glob('*.zip')):
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir() or member.filename.startswith('__MACOSX/'):
                    continue
                lower = member.filename.lower()
                if any(token in lower for token in ('station', 'estacion')):
                    summaries.extend(inspect_member(archive.read(member), member.filename, archive_path.name))
    output_path = ROOT / 'data' / 'gold' / 'station_status_field_summary.json'
    output_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'snapshot_file_count': len(summaries), 'output': str(output_path.relative_to(ROOT))}))


if __name__ == '__main__':
    main()
