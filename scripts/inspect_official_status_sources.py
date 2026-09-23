from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import rarfile

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / 'data' / 'bronze' / 'historical'


def inspect_bytes(data: bytes, name: str, depth: int = 0) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    lower = name.lower()
    if lower.endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.filename.startswith('__MACOSX/') or member.is_dir():
                    continue
                found.extend(inspect_bytes(archive.read(member), f'{name}/{member.filename}', depth + 1))
        return found
    if lower.endswith('.rar'):
        with rarfile.RarFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                found.extend(inspect_bytes(archive.read(member), f'{name}/{member.filename}', depth + 1))
        return found
    if not lower.endswith(('.json', '.csv', '.txt')):
        return found
    if lower.endswith('.json'):
        text = data.decode('utf-8-sig', 'replace')
        records = []
        for line in text.splitlines()[:3]:
            try:
                value = json.loads(line)
                records.append(value)
            except json.JSONDecodeError:
                break
        if records and isinstance(records[0], dict):
            keys = sorted(records[0].keys())
            found.append({'file': name, 'format': 'json-lines', 'keys': keys, 'sample': records[0]})
        else:
            found.append({'file': name, 'format': 'json', 'bytes': len(data), 'text_prefix': text[:300]})
    else:
        prefix = data.decode('utf-8-sig', 'replace')[:500]
        found.append({'file': name, 'format': lower.rsplit('.', 1)[-1], 'text_prefix': prefix})
    return found


def main() -> None:
    results: list[dict[str, object]] = []
    for archive_path in sorted(HIST.glob('*.zip')):
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.filename.startswith('__MACOSX/') or member.is_dir():
                    continue
                lower = member.filename.lower()
                if any(token in lower for token in ('station', 'estacion')) or lower.endswith(('.rar', '.json')):
                    results.extend(inspect_bytes(archive.read(member), f'{archive_path.name}/{member.filename}'))
    output_path = ROOT / 'data' / 'gold' / 'official_status_source_inventory.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'result_count': len(results), 'output': str(output_path.relative_to(ROOT))}))


if __name__ == '__main__':
    main()
