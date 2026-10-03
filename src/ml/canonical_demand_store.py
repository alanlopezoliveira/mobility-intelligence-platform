from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import psycopg

# Identity of the archived BiciMAD benchmark, not a constraint on new providers.
# Current trip-to-web publication uses actual source hashes and network batches.
AUTHORITATIVE_GOLD_HASH = '0a747a132ab6401621df226d96e155b0dbf435d5992bb78e35e3c9461f24e288'
SOURCE_NAME = 'bicimad_historical_trips'


@dataclass(frozen=True)
class ObservationKey:
    provider_id: str
    network_id: str
    station_instance_id: str
    observed_at: datetime


class CanonicalDemandStore:
    """Bounded keyed access to the validated canonical Gold PostgreSQL copy."""

    def __init__(self, connection: psycopg.Connection, source_name: str = SOURCE_NAME) -> None:
        self.connection = connection
        self.source_name = source_name

    def get_observations(self, keys: Iterable[ObservationKey]) -> dict[ObservationKey, float]:
        unique_keys = list(dict.fromkeys(keys))
        if not unique_keys:
            return {}
        values = [(key.provider_id, key.network_id, key.station_instance_id, key.observed_at) for key in unique_keys]
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT provider_id, network_id, station_instance_id, observed_at, demand
                FROM demand_observations
                WHERE source_name = %s
                  AND batch_id = (SELECT batch_id FROM active_dataset_version WHERE singleton_id = 1)
                  AND (provider_id, network_id, station_instance_id, observed_at)
                      IN (SELECT * FROM UNNEST(%s::text[], %s::text[], %s::text[], %s::timestamptz[]))
                """,
                (self.source_name, *[[item[index] for item in values] for index in range(4)]),
            )
            return {
                ObservationKey(str(provider_id), str(network_id), str(instance_id), observed_at): float(demand)
                for provider_id, network_id, instance_id, observed_at, demand in cursor.fetchall()
            }

    def get_metadata(self) -> dict[str, object]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT station_instance_id), MIN(observed_at), MAX(observed_at)
                FROM demand_observations
                WHERE source_name = %s
                  AND batch_id = (SELECT batch_id FROM active_dataset_version WHERE singleton_id = 1)
                  AND provider_id IS NOT NULL AND network_id IS NOT NULL AND station_instance_id IS NOT NULL
                """,
                (self.source_name,),
            )
            row = cursor.fetchone()
        if row is None:
            row_count, station_count, min_timestamp, max_timestamp = 0, 0, None, None
        else:
            row_count, station_count, min_timestamp, max_timestamp = row
        return {
            'table': 'demand_observations',
            'source_name': self.source_name,
            'active_batch_id': self._active_batch_id(),
            'authoritative_gold_hash': AUTHORITATIVE_GOLD_HASH,
            'row_count': row_count,
            'station_count': station_count,
            'min_timestamp': min_timestamp.isoformat() if min_timestamp else None,
            'max_timestamp': max_timestamp.isoformat() if max_timestamp else None,
        }

    def _active_batch_id(self) -> str | None:
        with self.connection.cursor() as cursor:
            cursor.execute('SELECT batch_id FROM active_dataset_version WHERE singleton_id = 1')
            row = cursor.fetchone()
        return str(row[0]) if row else None

    def assert_validated(self) -> None:
        """Validate the published batch against its manifest, not historical row totals."""
        metadata = self.get_metadata()
        with self.connection.cursor() as cursor:
            cursor.execute('SELECT status, expected_rows FROM ingestion_batches WHERE batch_id = %s',
                           (metadata['active_batch_id'],))
            batch = cursor.fetchone()
        if not batch or batch[0] != 'published' or not metadata['row_count'] or metadata['row_count'] != batch[1]:
            raise RuntimeError(f'Canonical DB metadata does not match its published batch: {metadata}')
