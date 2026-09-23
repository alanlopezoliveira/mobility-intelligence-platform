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
