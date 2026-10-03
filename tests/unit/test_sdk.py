"""SDK calls must use supported routes and preserve provider scope."""

import json
from io import BytesIO

import pytest
from src.sdk.python.client import MobilityClient


def test_client_encodes_station_and_provider_scope(monkeypatch):
    urls = []

    def open_url(url, timeout):
        urls.append(url)
        assert timeout == 30
        return BytesIO(json.dumps([]).encode())

    monkeypatch.setattr('src.sdk.python.client.request.urlopen', open_url)
    client = MobilityClient(provider_id='other', network_id='city')
    assert client.history('A/B & C') == []
    assert 'provider_id=other&network_id=city' in urls[0]
    assert 'station_id=A%2FB+%26+C' in urls[0]
    assert not hasattr(client, 'risk')
    assert not hasattr(client, 'forecast')
    with pytest.raises(ValueError, match='only available'):
        client.historical_replay('1', '2022-01-01T00:00:00Z')
