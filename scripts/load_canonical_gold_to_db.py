from __future__ import annotations

import csv
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / 'data' / 'gold' / 'station_demand_hourly.csv'
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://mobility_user:change_me@db:5432/mobility')
SOURCE_NAME = 'bicimad_historical_trips'


def main() -> None:
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE canonical_gold_load (
                    provider text NOT NULL,
                    station_id text NOT NULL,
                    observed_at timestamptz NOT NULL,
                    departures integer NOT NULL,
                    arrivals integer NOT NULL,
                    total_activity integer NOT NULL,
                    net_flow integer NOT NULL,
                    demand double precision NOT NULL,
                    source_year integer NOT NULL
                ) ON COMMIT DROP
                """
            )
            with GOLD_PATH.open('r', encoding='utf-8', newline='') as source:
                reader = csv.DictReader(source)
                with cursor.copy(
                    "COPY canonical_gold_load (provider, station_id, observed_at, departures, arrivals, total_activity, net_flow, demand, source_year) FROM STDIN"
                ) as copy:
                    for row in reader:
                        copy.write_row([
                            row['provider'], row['station_id'], row['observed_at'], int(row['departures']),
                            int(row['arrivals']), int(row['total_activity']), int(row['net_flow']),
                            float(row['demand']), int(row['source_year']),
                        ])
            cursor.execute('SELECT COUNT(*) FROM canonical_gold_load')
            staged_count = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT provider, station_id, observed_at
                    FROM canonical_gold_load
                    GROUP BY provider, station_id, observed_at
                    HAVING COUNT(*) > 1
                ) duplicate_keys
                """
            )
            duplicate_count = cursor.fetchone()[0]
            if duplicate_count:
                raise RuntimeError(f'Canonical Gold staging contains {duplicate_count} duplicate keys')
            cursor.execute('DELETE FROM demand_observations WHERE source_name = %s', (SOURCE_NAME,))
            cursor.execute(
                """
                INSERT INTO demand_observations
                    (station_id, observed_at, departures, arrivals, total_activity, net_flow, demand, source_name)
                SELECT station_id, observed_at, departures, arrivals, total_activity, net_flow, demand, %s
                FROM canonical_gold_load
                """,
                (SOURCE_NAME,),
            )
            cursor.execute('SELECT COUNT(*) FROM demand_observations WHERE source_name = %s', (SOURCE_NAME,))
            persisted_count = cursor.fetchone()[0]
        connection.commit()
    print({'staged_count': staged_count, 'duplicate_count': duplicate_count, 'persisted_count': persisted_count})


if __name__ == '__main__':
    main()
