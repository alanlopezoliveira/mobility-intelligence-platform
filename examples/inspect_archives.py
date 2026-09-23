from __future__ import annotations

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
    head = requests.head(url, allow_redirects=True, timeout=(15, 30))
    tail = requests.get(url, headers={'Range': 'bytes=-2097152'}, timeout=(15, 60))
    print(
        year,
        'status=', head.status_code,
        'type=', head.headers.get('content-type'),
        'length=', head.headers.get('content-length'),
        'range=', head.headers.get('accept-ranges'),
        'range_status=', tail.status_code,
        'content_range=', tail.headers.get('content-range'),
        'tail_bytes=', len(tail.content),
        flush=True,
    )
