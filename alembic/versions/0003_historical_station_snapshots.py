"""Add historical station snapshot table.

Revision ID: 0003_historical_station_snapshots
Revises: 0002_demand_trip_counts
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_historical_station_snapshots"
down_revision = "0002_demand_trip_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historical_station_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("station_id", sa.String(64), nullable=False),
        sa.Column("snapshot_period", sa.String(32), nullable=False),
        sa.Column("station_number", sa.String(64), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("total_bases", sa.Integer(), nullable=True),
        sa.Column("free_bases", sa.Integer(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("activate", sa.Integer(), nullable=True),
        sa.Column("source_archive", sa.String(256), nullable=True),
        sa.Column("source_member", sa.String(256), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("record_identity", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "station_id", "snapshot_period", "source_member", name="uq_historical_station_snapshot"),
    )
    op.create_index("ix_historical_station_snapshots_provider", "historical_station_snapshots", ["provider"])
    op.create_index("ix_historical_station_snapshots_station_id", "historical_station_snapshots", ["station_id"])
    op.create_index("ix_historical_station_snapshots_snapshot_period", "historical_station_snapshots", ["snapshot_period"])
    op.create_index("ix_historical_station_snapshots_record_identity", "historical_station_snapshots", ["record_identity"])


def downgrade() -> None:
    op.drop_index("ix_historical_station_snapshots_record_identity", table_name="historical_station_snapshots")
    op.drop_index("ix_historical_station_snapshots_snapshot_period", table_name="historical_station_snapshots")
    op.drop_index("ix_historical_station_snapshots_station_id", table_name="historical_station_snapshots")
    op.drop_index("ix_historical_station_snapshots_provider", table_name="historical_station_snapshots")
    op.drop_table("historical_station_snapshots")
