"""Add immutable demand batches and an active dataset pointer.

Revision ID: 0004_versioned_demand_publication
Revises: 0003_station_snapshots
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_versioned_demand_publication"
down_revision = "0003_station_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep published revision IDs stable while allowing descriptive identifiers.
    # This must happen before Alembic records this 33-character revision.
    op.alter_column("alembic_version", "version_num", type_=sa.String(128))
    op.create_table(
        "ingestion_batches",
        sa.Column("batch_id", sa.String(64), primary_key=True),
        sa.Column("source_name", sa.String(128), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("expected_rows", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('loading', 'validated', 'published', 'failed')", name="ck_ingestion_batches_status"),
    )
    op.add_column("demand_observations", sa.Column("batch_id", sa.String(64), nullable=True))
    op.execute(
        """INSERT INTO ingestion_batches (batch_id, source_name, content_sha256, expected_rows, status, published_at)
           VALUES ('legacy-initial', 'legacy-mixed', repeat('0', 64),
                   (SELECT COUNT(*) FROM demand_observations),
                   'published', now())"""
    )
    op.execute("UPDATE demand_observations SET batch_id = 'legacy-initial'")
    op.alter_column("demand_observations", "batch_id", nullable=False)
    op.drop_constraint("uq_demand_station_time", "demand_observations", type_="unique")
    op.create_foreign_key(
        "fk_demand_observations_batch", "demand_observations", "ingestion_batches", ["batch_id"], ["batch_id"]
    )
    op.create_unique_constraint(
        "uq_demand_batch_station_time", "demand_observations", ["batch_id", "station_id", "observed_at"]
    )
    op.create_index("ix_demand_observations_batch_id", "demand_observations", ["batch_id"])
    op.create_table(
        "active_dataset_version",
        sa.Column("singleton_id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["ingestion_batches.batch_id"], name="fk_active_dataset_batch"),
        sa.CheckConstraint("singleton_id = 1", name="ck_active_dataset_singleton"),
    )
    op.execute("INSERT INTO active_dataset_version (singleton_id, batch_id) VALUES (1, 'legacy-initial')")


def downgrade() -> None:
    # Preserve data: downgrade is intentionally unsupported after versioned loads.
    raise RuntimeError("0004 cannot be downgraded without discarding published dataset history")
