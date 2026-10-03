"""Offline synthetic integration check; run only in an isolated test workspace.

Exercises mapped ingestion, two timezones, real model fitting/reload, exports,
and optionally PostgreSQL publication. Synthetic fixtures never enter the
default pipeline; docker-compose.smoke.yml invokes this explicitly.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from scripts import rebuild_project
from src.config.settings import load_provider_config
from src.ml.model_comparison import candidate_specs
from src.providers.registry import provider_root


def run_smoke(root: Path, publish_database=False):
    """Fit fixture models for two providers with overlapping opaque station IDs."""
    times = pd.date_range('2022-01-01', '2023-01-01', inclusive='left', freq='h', tz='UTC')
    fixtures = {}
    for provider, timezone in [('smoke-alpha', 'UTC'), ('smoke-beta', 'America/New_York')]:
        raw_config = load_provider_config().model_dump()
        raw_config.update(provider=provider, network_id='test', name=f'SYNTHETIC {provider}',
                          city='Test city', timezone=timezone, source_name=f'{provider}_trips',
                          historical=[], attribution='Synthetic integration fixture; not real mobility data',
                          trips={'adapter': 'csv', 'file_glob': 'data/bronze/trips/*.csv',
                                 'columns': {'started_at': 'start', 'ended_at': 'end',
                                             'origin_station': 'origin', 'destination_station': 'destination',
                                             'latitude': 'lat', 'longitude': 'lon', 'name': 'name'}})
        config = type(load_provider_config()).model_validate(raw_config)
        fixtures[provider] = config
        folder = provider_root(root, config) / 'data/bronze/trips'
        folder.mkdir(parents=True, exist_ok=True)
        raw = pd.DataFrame({'start': times.strftime('%Y-%m-%dT%H:%M:%SZ'),
                            'end': (times + pd.Timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                            'origin': 'shared/01', 'destination': 'shared/01',
                            'lat': 40.4, 'lon': -3.7, 'name': 'Synthetic station'})
        extra = raw.iloc[::3].copy()
        extra['start'] = (times[::3] + pd.Timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        spillover = raw.iloc[:1].copy()
        spillover['start'], spillover['end'] = '2023-01-01T00:00:00Z', '2023-01-01T00:15:00Z'
        pd.concat([raw, extra, spillover]).to_csv(folder / 'trips.csv', index=False)

    def weather(*args):
        frame = pd.DataFrame({'time': times, 'rain': (times.dayofyear % 5 == 0).astype(float),
                              'temperature_2m': 12.0, 'precipitation': 0.0, 'wind_speed_10m': 2.0})
        return frame, {'sha256': hashlib.sha256(b'synthetic-weather').hexdigest(),
                       'source': 'Synthetic weather fixture', 'hours': len(frame),
                       'requested': {'latitude': 40.4, 'longitude': -3.7},
                       'grid_latitude': 40.4, 'grid_longitude': -3.7,
                       'documentation': '', 'cache_hit': False}

    specs = [spec for spec in candidate_specs() if spec[1] in {'baseline_recent', 'baseline_day', 'baseline_week', 'tree'}]
    with patch.object(rebuild_project, 'ROOT', root), \
         patch.object(rebuild_project, 'load_provider_config', side_effect=lambda provider: fixtures[provider]), \
         patch.object(rebuild_project, 'historical_weather', side_effect=weather), \
         patch('src.ml.model_comparison.candidate_specs', return_value=specs):
        for provider in fixtures:
            rebuild_project.run(provider=provider, publish_database=publish_database)
        # Cache verification and idempotent publication must retain both providers.
        rebuild_project.run(provider='smoke-alpha', publish_database=publish_database)
    catalog = json.loads((root / 'frontend/public/project-data/catalog.json').read_text())
    assert {row['id'] for row in catalog['datasets']} >= {
        'smoke-alpha/test/2022', 'smoke-beta/test/2022',
    }
    return fixtures


if __name__ == '__main__':
    run_smoke(ROOT, publish_database=True)
    print('PASS: two synthetic providers ingested, trained, published and cached', flush=True)
