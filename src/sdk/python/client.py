from __future__ import annotations

import json
from urllib import request


class MobilityClient:
    def __init__(self, base_url: str = 'http://localhost:8000') -> None:
        self.base_url = base_url.rstrip('/')

    def get(self, path: str) -> dict:
        with request.urlopen(f'{self.base_url}{path}') as response:
            return json.loads(response.read().decode('utf-8'))

    def stations(self) -> dict:
        return self.get('/api/v1/stations')

    def forecast(self) -> dict:
        return self.get('/api/v1/forecast')

    def risk(self) -> dict:
        return self.get('/api/v1/risk')

    def model_info(self) -> dict:
        return self.get('/api/v1/model/info')
