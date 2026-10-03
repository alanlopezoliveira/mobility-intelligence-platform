from fastapi.testclient import TestClient
from src.api.app import app
from src.db.database import get_session


class FakeSession:
    def execute(self, _query):
        return None


def fake_session():
    yield FakeSession()


app.dependency_overrides[get_session] = fake_session

client = TestClient(app)


def test_health_endpoint():
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_config_endpoint():
    response = client.get('/api/v1/config')
    assert response.status_code == 200
    assert 'app_name' in response.json()


def test_observed_series_rejects_timezone_free_bounds():
    response = client.get('/api/v1/analytics/series', params={
        'start': '2022-01-01T00:00:00',
        'end': '2022-01-02T00:00:00Z',
    })
    assert response.status_code == 422


def test_observed_series_rejects_unbounded_range():
    response = client.get('/api/v1/analytics/series', params={
        'start': '2022-01-01T00:00:00Z',
        'end': '2024-01-03T00:00:00Z',
    })
    assert response.status_code == 422


def test_live_forecast_is_gone_from_the_historical_release():
    assert client.get('/api/v1/forecast').status_code == 410


def test_legacy_evaluation_cannot_be_mislabeled_as_another_provider():
    response = client.get('/api/v1/research/evaluation', params={'provider_id': 'other'})
    assert response.status_code == 404


def test_simulation_rejects_unsupported_horizon_before_reading_artifacts():
    response = client.get('/api/v1/research/replay', params={
        'station_id': '1',
        'feature_timestamp': '2022-04-14T18:00:00Z',
        'horizon_minutes': 90,
    })
    assert response.status_code == 422
