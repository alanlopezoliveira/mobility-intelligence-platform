from __future__ import annotations

import struct

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

for year, url in URLS.items():
    response = requests.get(url, headers={'Range': 'bytes=-4194304'}, timeout=(15, 120))
    content = response.content
    eocd_offset = content.rfind(b'PK\x05\x06')
    if eocd_offset < 0:
        print(year, 'NO_EOCD', response.headers.get('content-range'), flush=True)
        continue
    _, _, _, entries_disk, entries_total, central_size, central_offset, comment_size = struct.unpack_from('<4s4H2LH', content, eocd_offset)
    range_start = int(response.headers['content-range'].split()[1].split('-')[0])
    central_start = central_offset - range_start
    central = content[central_start:central_start + central_size]
    print(year, 'entries=', entries_total, 'central_size=', central_size, 'central_offset=', central_offset, flush=True)
    offset = 0
    for _ in range(entries_total):
        if central[offset:offset + 4] != b'PK\x01\x02':
            print('  CENTRAL_PARSE_ERROR', offset, flush=True)
            break
        fields = struct.unpack_from('<4s6H3L5H2L', central, offset)
        method = fields[4]
        crc = fields[7]
        compressed_size = fields[8]
        file_size = fields[9]
        name_len = fields[10]
        extra_len = fields[11]
        comment_len = fields[12]
        local_offset = fields[16]
        name_start = offset + 46
        name = central[name_start:name_start + name_len].decode('utf-8', 'replace')
        print(' ', name, 'method=', method, 'compressed=', compressed_size, 'size=', file_size, 'local_offset=', local_offset, flush=True)
        offset += 46 + name_len + extra_len + comment_len
