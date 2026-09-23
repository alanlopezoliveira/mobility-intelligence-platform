"""Add station-level demand components.

Revision ID: 0002_demand_trip_counts
Revises: 0001_initial_postgis
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_demand_trip_counts"
down_revision = "0001_initial_postgis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("demand_observations", sa.Column("departures", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("demand_observations", sa.Column("arrivals", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("demand_observations", sa.Column("total_activity", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("demand_observations", sa.Column("net_flow", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_demand_observations_station_time", "demand_observations", ["station_id", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_demand_observations_station_time", table_name="demand_observations")
    op.drop_column("demand_observations", "net_flow")
    op.drop_column("demand_observations", "total_activity")
    op.drop_column("demand_observations", "arrivals")
    op.drop_column("demand_observations", "departures")