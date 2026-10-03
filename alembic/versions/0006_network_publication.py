"""Publish each network independently while retaining the legacy reference pointer."""

import sqlalchemy as sa
from alembic import op

revision = '0006_network_publication'
down_revision = '0005_provider_network_station_identity'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'network_dataset_versions',
        sa.Column('provider_id', sa.String(64), primary_key=True),
        sa.Column('network_id', sa.String(64), primary_key=True),
        sa.Column('batch_id', sa.String(64), sa.ForeignKey('ingestion_batches.batch_id'), nullable=False),
    )
    op.execute('''INSERT INTO network_dataset_versions (provider_id, network_id, batch_id)
                  SELECT DISTINCT d.provider_id, d.network_id, d.batch_id
                  FROM demand_observations d JOIN active_dataset_version a ON a.batch_id = d.batch_id''')


def downgrade() -> None:
    op.drop_table('network_dataset_versions')
