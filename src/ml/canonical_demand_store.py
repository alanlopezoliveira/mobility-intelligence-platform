from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import psycopg

AUTHORITATIVE_GOLD_HASH = '0a747a132ab6401621df226d96e155b0dbf435d5992bb78e35e3c9461f24e288'
SOURCE_NAME = 'bicimad_historical_trips'


@dataclass(frozen=True)
class ObservationKey:
    station_id: str
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
        values = [(key.station_id, key.observed_at) for key in unique_keys]
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT station_id, observed_at, demand
                FROM demand_observations
                WHERE source_name = %s
                  AND (station_id, observed_at) IN (SELECT * FROM UNNEST(%s::text[], %s::timestamptz[]))
                """,
                (self.source_name, [item[0] for item in values], [item[1] for item in values]),
            )
            return {
                ObservationKey(str(station_id), observed_at): float(demand)
                for station_id, observed_at, demand in cursor.fetchall()
            }

    def get_metadata(self) -> dict[str, object]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT station_id), MIN(observed_at), MAX(observed_at)
                FROM demand_observations
                WHERE source_name = %s
                """,
                (self.source_name,),
            )
            row_count, station_count, min_timestamp, max_timestamp = cursor.fetchone()
        return {
            'table': 'demand_observations',
            'source_name': self.source_name,
            'authoritative_gold_hash': AUTHORITATIVE_GOLD_HASH,
            'row_count': row_count,
            'station_count': station_count,
            'min_timestamp': min_timestamp.isoformat() if min_timestamp else None,
            'max_timestamp': max_timestamp.isoformat() if max_timestamp else None,
        }

    def assert_validated(self) -> None:
        metadata = self.get_metadata()
        if metadata['row_count'] != 7_977_034 or metadata['station_count'] != 3_174:
            raise RuntimeError(f'Canonical DB metadata does not match validated Gold: {metadata}')