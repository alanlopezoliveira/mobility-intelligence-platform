"""Small read-only client for observed demand and historical research endpoints."""

from __future__ import annotations

import json
from urllib import request
from urllib.parse import urlencode


class MobilityClient:
    """Scope observations to a provider/network; no live availability is promised."""

    def __init__(self, base_url: str = 'http://localhost:8000', *,
                 provider_id: str = 'bicimad', network_id: str = 'madrid', timeout: float = 30) -> None:
        self.base_url = base_url.rstrip('/')
        self.scope = {'provider_id': provider_id, 'network_id': network_id}
        self.timeout = timeout

    def get(self, path: str, **params):
        """Read JSON; HTTP/network errors propagate to the caller."""
        suffix = '?' + urlencode(params) if params else ''
        with request.urlopen(f'{self.base_url}{path}{suffix}', timeout=self.timeout) as response:
            return json.loads(response.read().decode('utf-8'))

    def stations(self, limit: int = 100) -> list[dict]:
        """List stations with observations in this network's current dataset."""
        return self.get('/api/v1/stations', **self.scope, limit=limit)

    def summary(self) -> dict:
        """Return observed counts and coverage for this network."""
        return self.get('/api/v1/analytics/summary', **self.scope)

    def history(self, station_id: str, limit: int = 100) -> list[dict]:
        """Return recorded station-hours; absent hours are unknown, not zero."""
        return self.get('/api/v1/history', **self.scope, station_id=station_id, limit=limit)

    def historical_replay(self, station_id: str, feature_timestamp: str,
                          horizon_minutes: int = 60) -> dict:
        """Replay the separately prepared legacy BiciMAD benchmark, never a live forecast."""
        if self.scope != {'provider_id': 'bicimad', 'network_id': 'madrid'}:
            raise ValueError('Legacy benchmark replay is only available for BiciMAD Madrid')
        return self.get('/api/v1/research/replay', station_id=station_id,
                        feature_timestamp=feature_timestamp, horizon_minutes=horizon_minutes)

    def model_info(self) -> dict:
        """Read the legacy production registry; current research models live in web exports."""
        if self.scope != {'provider_id': 'bicimad', 'network_id': 'madrid'}:
            raise ValueError('Legacy model registry is only available for BiciMAD Madrid')
        return self.get('/api/v1/model/info')
