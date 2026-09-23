"""Create initial PostGIS domain schema.

Revision ID: 0001_initial_postgis
Revises:
"""
import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision = "0001_initial_postgis"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "stations",
        sa.Column("station_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("address", sa.Text()),
        sa.Column("capacity", sa.Integer()),
        sa.Column("available_bikes", sa.Integer()),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("location", Geometry("POINT", srid=4326, spatial_index=False)),
        sa.Column("source_name", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_stations_location", "stations", ["location"], postgresql_using="gist")
    op.create_table(
        "demand_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_id", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("demand", sa.Float(), nullable=False),
        sa.Column("source_name", sa.String(128), nullable=False),
        sa.UniqueConstraint("station_id", "observed_at", name="uq_demand_station_time"),
    )
    op.create_index("ix_demand_observations_station_id", "demand_observations", ["station_id"])
    op.create_index("ix_demand_observations_observed_at", "demand_observations", ["observed_at"])
    op.create_table(
        "model_registry",
        sa.Column("model_version", sa.String(128), primary_key=True),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("feature_definition", sa.Text(), nullable=False),
        sa.Column("train_period", sa.Text()),
        sa.Column("validation_period", sa.Text()),
        sa.Column("test_period", sa.Text()),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("artifact_path", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("model_registry")
    op.drop_index("ix_demand_observations_observed_at", table_name="demand_observations")
    op.drop_index("ix_demand_observations_station_id", table_name="demand_observations")
    op.drop_table("demand_observations")
    op.drop_index("idx_stations_location", table_name="stations")
    op.drop_table("stations")
