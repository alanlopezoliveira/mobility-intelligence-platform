"""Scope station identity by provider and network.

Revision ID: 0005_provider_network_station_identity
Revises: 0004_versioned_demand_publication
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_provider_network_station_identity"
down_revision = "0004_versioned_demand_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Also accommodate installations that previously widened the column only
    # enough for revision 0004 using a manual migration workaround.
    op.alter_column("alembic_version", "version_num", type_=sa.String(128))
    op.add_column("ingestion_batches", sa.Column("source_manifest_json", sa.Text(), nullable=True))
    op.create_table(
        "provider_metadata",
        sa.Column("provider_id", sa.String(64), primary_key=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("calendar_version", sa.String(64), nullable=False),
    )
    op.create_table(
        "network_metadata",
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("network_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("provider_id", "network_id", name="pk_network_metadata"),
        sa.ForeignKeyConstraint(["provider_id"], ["provider_metadata.provider_id"], name="fk_network_provider"),
    )
    op.create_table(
        "station_instances",
        sa.Column("station_instance_id", sa.String(160), primary_key=True),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("network_id", sa.String(64), nullable=False),
        sa.Column("provider_station_id", sa.String(64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("identity_provenance", sa.Text(), nullable=False),
        sa.UniqueConstraint("station_instance_id", "provider_id", "network_id", name="uq_station_instance_scope"),
        sa.ForeignKeyConstraint(["provider_id", "network_id"], ["network_metadata.provider_id", "network_metadata.network_id"], name="fk_station_instance_network"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_station_instance_validity"),
    )
    op.create_index("ix_station_instance_provider_network_station", "station_instances", ["provider_id", "network_id", "provider_station_id"])
    op.create_table(
        "station_instance_relationships",
        sa.Column("relationship_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("from_station_instance_id", sa.String(160), nullable=False),
        sa.Column("to_station_instance_id", sa.String(160), nullable=False),
        sa.Column("relationship_type", sa.String(24), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.CheckConstraint("relationship_type IN ('rename', 'merge', 'split', 'relocation', 'replacement')", name="ck_station_relationship_type"),
        sa.ForeignKeyConstraint(["from_station_instance_id"], ["station_instances.station_instance_id"], name="fk_station_relationship_from"),
        sa.ForeignKeyConstraint(["to_station_instance_id"], ["station_instances.station_instance_id"], name="fk_station_relationship_to"),
    )
    op.create_table(
        "physical_places",
        sa.Column("physical_place_id", sa.String(64), primary_key=True),
        sa.Column("provenance", sa.Text(), nullable=False),
    )
    op.create_table(
        "station_place_mappings",
        sa.Column("station_instance_id", sa.String(160), primary_key=True),
        sa.Column("physical_place_id", sa.String(64), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_station_place_confidence"),
        sa.ForeignKeyConstraint(["station_instance_id"], ["station_instances.station_instance_id"], name="fk_station_place_instance"),
        sa.ForeignKeyConstraint(["physical_place_id"], ["physical_places.physical_place_id"], name="fk_station_place_place"),
    )
    op.execute("INSERT INTO provider_metadata (provider_id, timezone, calendar_version) VALUES ('bicimad', 'Europe/Madrid', 'madrid-weekdays-v1')")
    op.execute("INSERT INTO network_metadata (provider_id, network_id, name) VALUES ('bicimad', 'madrid', 'BiciMAD Madrid')")
    op.execute(
        """INSERT INTO station_instances
             (station_instance_id, provider_id, network_id, provider_station_id, valid_from, valid_to, identity_provenance)
           SELECT 'bicimad:madrid:' || station_id, 'bicimad', 'madrid', station_id,
                  MIN(observed_at), MAX(observed_at) + INTERVAL '1 hour',
                  'Legacy source station identifier; extent is observation-bounded and physical place is intentionally unmapped.'
           FROM demand_observations GROUP BY station_id"""
    )
    op.add_column("demand_observations", sa.Column("provider_id", sa.String(64), nullable=True))
    op.add_column("demand_observations", sa.Column("network_id", sa.String(64), nullable=True))
    op.add_column("demand_observations", sa.Column("station_instance_id", sa.String(160), nullable=True))
    op.execute(
        """UPDATE demand_observations SET provider_id = 'bicimad', network_id = 'madrid',
           station_instance_id = 'bicimad:madrid:' || station_id"""
    )
    op.alter_column("demand_observations", "provider_id", nullable=False)
    op.alter_column("demand_observations", "network_id", nullable=False)
    op.alter_column("demand_observations", "station_instance_id", nullable=False)
    op.drop_constraint("uq_demand_batch_station_time", "demand_observations", type_="unique")
    op.create_unique_constraint(
        "uq_demand_batch_provider_network_instance_time", "demand_observations",
        ["batch_id", "provider_id", "network_id", "station_instance_id", "observed_at"],
    )
    op.create_foreign_key(
        "fk_demand_station_instance_scope", "demand_observations", "station_instances",
        ["station_instance_id", "provider_id", "network_id"],
        ["station_instance_id", "provider_id", "network_id"],
    )
    op.create_index("ix_demand_observations_provider_network_time", "demand_observations", ["provider_id", "network_id", "observed_at"])
    op.create_index("ix_demand_observations_provider_network_instance_time", "demand_observations", ["provider_id", "network_id", "station_instance_id", "observed_at"])
    op.add_column("demand_observations", sa.Column("source_record_id", sa.Text(), nullable=True))
    op.add_column("demand_observations", sa.Column("source_record_hash", sa.String(64), nullable=True))
    op.add_column("demand_observations", sa.Column("original_time_text", sa.Text(), nullable=True))
    op.execute(
        """UPDATE demand_observations
           SET source_record_id = 'legacy-canonical-row:' || id::text,
               original_time_text = observed_at::text"""
    )

    op.add_column("historical_station_snapshots", sa.Column("provider_id", sa.String(64), nullable=True))
    op.add_column("historical_station_snapshots", sa.Column("network_id", sa.String(64), nullable=True))
    op.add_column("historical_station_snapshots", sa.Column("station_instance_id", sa.String(160), nullable=True))
    op.execute(
        """UPDATE historical_station_snapshots SET provider_id = lower(provider), network_id = 'madrid',
           station_instance_id = lower(provider) || ':madrid:' || station_id"""
    )
    op.alter_column("historical_station_snapshots", "provider_id", nullable=False)
    op.alter_column("historical_station_snapshots", "network_id", nullable=False)
    op.alter_column("historical_station_snapshots", "station_instance_id", nullable=False)
    op.drop_constraint("uq_historical_station_snapshot", "historical_station_snapshots", type_="unique")
    op.create_unique_constraint(
        "uq_historical_station_snapshot_scope", "historical_station_snapshots",
        ["provider_id", "network_id", "station_id", "snapshot_period", "source_member"],
    )
    op.create_index("ix_historical_snapshot_provider_network_station", "historical_station_snapshots", ["provider_id", "network_id", "station_instance_id"])

    op.add_column("stations", sa.Column("provider_id", sa.String(64), nullable=True))
    op.add_column("stations", sa.Column("network_id", sa.String(64), nullable=True))
    op.execute("UPDATE stations SET provider_id = 'bicimad', network_id = 'madrid'")
    op.alter_column("stations", "provider_id", nullable=False)
    op.alter_column("stations", "network_id", nullable=False)
    op.drop_constraint("stations_pkey", "stations", type_="primary")
    op.create_primary_key("pk_stations_provider_network_station", "stations", ["provider_id", "network_id", "station_id"])


def downgrade() -> None:
    raise RuntimeError("0005 cannot be downgraded without discarding provider-scoped identity")
