from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib
from dataclasses import dataclass

import requests

URLS = {
    2017: 'https://media.emtmadrid.es/-HoGTStPZeC',
    2018: 'https://media.emtmadrid.es/-9iZNUjrjri',
    2019: 'https://media.emtmadrid.es/-WNgfSj2ZvC',
    2020: 'https://media.emtmadrid.es/-rkuBymuFJX',
    2021: 'https://media.emtmadrid.es/-BRk4rTaAdV',
    2022: 'https://media.emtmadrid.es/-uHaW6iZkhG',
    2023: 'https://media.emtmadrid.es/-9Nii5DXo4x',
}
PATTERNS = {
    2017: 'Usage',
    2018: 'Usage',
    2019: '201904_Usage',
    2020: '202003.json',
    2021: 'trips_21_06',
    2022: 'trips_22_01',
    2023: 'trips_23_01',
}

@dataclass
class Entry:
    name: str
    compressed_size: int
    file_size: int
    local_offset: int
    method: int


def entries_for(url: str) -> list[Entry]:
    response = requests.get(url, headers={'Range': 'bytes=-4194304'}, timeout=(15, 120))
    content = response.content
    eocd = content.rfind(b'PK\x05\x06')
    _, _, _, _, total, central_size, central_offset, _ = struct.unpack_from('<4s4H2LH', content, eocd)
    start = central_offset - int(response.headers['content-range'].split()[1].split('-')[0])
    central = content[start:start + central_size]
    result = []
    offset = 0
    for _ in range(total):
        fields = struct.unpack_from('<4s6H3L5H2L', central, offset)
        name_len, extra_len, comment_len = fields[10:13]
        result.append(Entry(central[offset + 46:offset + 46 + name_len].decode('utf-8', 'replace'), fields[8], fields[9], fields[16], fields[4]))
        offset += 46 + name_len + extra_len + comment_len
    return result


def read_remote_member(url: str, entry: Entry) -> bytes:
    header = requests.get(url, headers={'Range': f'bytes={entry.local_offset}-{entry.local_offset + 4095}'}, timeout=(15, 60)).content
    name_len, extra_len = struct.unpack_from('<2H', header, 26)
    data_start = entry.local_offset + 30 + name_len + extra_len
    data = requests.get(url, headers={'Range': f'bytes={data_start}-{data_start + entry.compressed_size - 1}'}, timeout=(15, 180)).content
    if entry.method == 0:
        return data
    if entry.method == 8:
        return zlib.decompress(data, -15)
    raise ValueError(f'unsupported compression method {entry.method}')


def inspect_bytes(label: str, data: bytes) -> None:
    print('  CONTENT', label, 'bytes=', len(data), 'head=', repr(data[:300]), flush=True)
    if data[:2] == b'PK':
        with zipfile.ZipFile(io.BytesIO(data)) as nested:
            names = [name for name in nested.namelist() if not name.startswith('__MACOSX/') and not name.endswith('/')]
            print('  NESTED', names[:10], flush=True)
            for name in names[:2]:
                sample = nested.open(name).read(4096)
                print('  NESTED_SAMPLE', name, repr(sample[:500]), flush=True)
    elif data.lstrip()[:1] in (b'{', b'['):
        try:
            parsed = json.loads(data[:2_000_000].decode('utf-8-sig', 'replace'))
            item = parsed[0] if isinstance(parsed, list) else parsed
            print('  JSON_KEYS', list(item)[:50] if isinstance(item, dict) else type(item).__name__, flush=True)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            print('  JSON_PARSE', type(exc).__name__, str(exc), flush=True)

for year, url in URLS.items():
    candidates = [entry for entry in entries_for(url) if PATTERNS[year].lower() in entry.name.lower() and not entry.name.startswith('__MACOSX/')]
    if not candidates:
        print(year, 'NO_MATCH', flush=True)
        continue
    entry = candidates[0]
    print('YEAR', year, 'MEMBER', entry, flush=True)
    inspect_bytes(entry.name, read_remote_member(url, entry))
