from pathlib import Path

from geoalchemy2 import Geometry
from src.db.models import Base, Station


def test_alembic_revision_and_postgis_station_geometry_exist():
    revision = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0001_initial_postgis.py"
    assert revision.exists()
    location = Station.__table__.c.location.type
    assert isinstance(location, Geometry)
    assert location.geometry_type == "POINT"
    assert location.srid == 4326
    assert {"stations", "demand_observations", "model_registry"}.issubset(Base.metadata.tables)
