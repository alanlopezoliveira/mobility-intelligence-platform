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
    assert {
        "stations", "demand_observations", "model_registry", "ingestion_batches",
        "active_dataset_version", "provider_metadata", "network_metadata",
        "station_instances", "station_instance_relationships", "physical_places",
        "station_place_mappings",
    }.issubset(Base.metadata.tables)
    demand = Base.metadata.tables["demand_observations"]
    assert {"batch_id", "provider_id", "network_id", "station_instance_id"}.issubset(demand.c.keys())
    assert list(Base.metadata.tables["stations"].primary_key.columns.keys()) == [
        "provider_id", "network_id", "station_id",
    ]


def test_station_identity_distinguishes_stations_and_networks():
    from sqlalchemy import inspect

    mapper = inspect(Station)
    keys = [mapper.identity_key_from_instance(Station(
        provider_id=provider, network_id=network, station_id=station,
    )) for provider, network, station in [
        ("bicimad", "madrid", "1"), ("bicimad", "madrid", "2"),
        ("other", "madrid", "1"), ("bicimad", "other", "1"),
    ]]
    assert len(set(keys)) == 4


def test_migrations_widen_version_column_before_recording_long_revision():
    from io import StringIO

    from alembic import command
    from alembic.config import Config

    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    widening = sql.index("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)")
    recording = sql.index("SET version_num='0004_versioned_demand_publication'")
    assert widening < recording
