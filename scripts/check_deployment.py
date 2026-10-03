"""Validate the explicitly synthetic Docker smoke deployment over HTTP."""

import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def check(base):
    """Check browser exports and scoped API responses; do not accept HTML as JSON."""
    def get(path):
        with urlopen(base.rstrip('/') + path, timeout=15) as response:
            assert 'application/json' in response.headers['Content-Type'], path
            return json.load(response)

    for attempt in range(60):
        try:
            get('/health')
            break
        except (URLError, OSError):
            if attempt == 59:
                raise
            time.sleep(2)
    catalog = get('/project-data/catalog.json')
    assert {'smoke-alpha/test/2022', 'smoke-beta/test/2022'} <= {item['id'] for item in catalog['datasets']}
    for provider in ['smoke-alpha', 'smoke-beta']:
        report = get(f'/project-data/providers/{provider}/test/2022/report.json')
        assert report['provider']['id'] == provider
        assert report['departures'] == 11680
        station = report['stations'][0]
        assert station['station'] == 'shared/01'
        predictions = get(f'/project-data/providers/{provider}/test/2022/predictions-60-{station["export_key"]}.json')
        assert predictions and 'prediction' in predictions[0]
        summary = get(f'/api/v1/analytics/summary?provider_id={provider}&network_id=test')
        assert summary['observed_departures'] == 11680
        stations = get(f'/api/v1/stations?provider_id={provider}&network_id=test')
        assert len(stations) == 1 and stations[0]['provider_id'] == provider
        history = get(f'/api/v1/history?provider_id={provider}&network_id=test')
        assert history and all(row['provider_id'] == provider for row in history)
    empty = get('/api/v1/analytics/summary?provider_id=missing&network_id=test')
    assert empty['observed_departures'] == 0
    try:
        get('/project-data/missing.json')
    except HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError('Missing JSON was served as a successful response')
    with urlopen(base, timeout=15) as response:
        assert b'<div id="root">' in response.read()
    print('PASS: frontend, models, both provider APIs, isolation, and JSON 404 behavior')


if __name__ == '__main__':
    check(sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:13000')
