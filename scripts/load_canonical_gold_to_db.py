from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import DatabaseSettings, load_provider_config, normalize_database_url

GOLD_PATH = ROOT / 'data' / 'gold' / 'station_demand_hourly.csv'
DATABASE_URL = normalize_database_url(DatabaseSettings().url)
SOURCE_NAME = 'bicimad_historical_trips'
PROVIDER_CONFIG = load_provider_config()
NETWORK_ID = PROVIDER_CONFIG.network_id


def main() -> None:
    digest = hashlib.sha256()
    with GOLD_PATH.open('rb') as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b''):
            digest.update(chunk)
    content_hash = digest.hexdigest()
    archive_manifests = []
    for source in PROVIDER_CONFIG.historical:
        archive_path = ROOT / 'data' / 'bronze' / 'historical' / f'{source.year}.zip'
        manifest_path = archive_path.with_suffix('.manifest.json')
        if not archive_path.is_file() or not manifest_path.is_file():
            raise RuntimeError(f'Missing source archive or manifest for year {source.year}; run the validated download/parse pipeline first')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        archive_digest = hashlib.sha256()
        with archive_path.open('rb') as archive_file:
            for chunk in iter(lambda: archive_file.read(1024 * 1024), b''):
                archive_digest.update(chunk)
        if (
            manifest.get('source_year') != source.year
            or manifest.get('source_url') != source.direct_url
            or manifest.get('archive_sha256') != archive_digest.hexdigest()
        ):
            raise RuntimeError(f'Source archive manifest validation failed for year {source.year}')
        archive_manifests.append(manifest)
    source_manifest_json = json.dumps({
        'canonical_gold_sha256': content_hash,
        'source_archives': archive_manifests,
        'provider_id': PROVIDER_CONFIG.provider,
        'network_id': NETWORK_ID,
        'timezone': PROVIDER_CONFIG.timezone,
    }, sort_keys=True)
    batch_id = content_hash
    staged_count = 0
    duplicate_count = 0
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT batch_id, status FROM ingestion_batches WHERE content_sha256 = %s", (content_hash,))
            existing = cursor.fetchone()
        if existing and existing[1] == 'published':
            print({'batch_id': existing[0], 'status': 'already_published', 'content_sha256': content_hash})
            return
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE canonical_gold_load (
                    provider_id text NOT NULL,
                    network_id text NOT NULL,
                    station_id text NOT NULL,
                    station_instance_id text NOT NULL,
                    source_record_id text NOT NULL,
                    source_record_hash text NOT NULL,
                    original_time_text text NOT NULL,
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
                "COPY canonical_gold_load (provider_id, network_id, station_id, station_instance_id, source_record_id, source_record_hash, original_time_text, observed_at, departures, arrivals, total_activity, net_flow, demand, source_year) FROM STDIN"
                ) as copy:
                    for line_number, row in enumerate(reader, start=2):
                        provider_id = str(row['provider']).strip().lower()
                        station_id = str(row['station_id']).strip()
                        original_values = [str(row.get(field, '')) for field in reader.fieldnames or []]
                        row_hash = hashlib.sha256('|'.join(original_values).encode('utf-8')).hexdigest()
                        copy.write_row([
                            provider_id, NETWORK_ID, station_id, f'{provider_id}:{NETWORK_ID}:{station_id}',
                            f'{content_hash}:{line_number}', row_hash, str(row['observed_at']),
                            row['observed_at'], int(row['departures']),
                            int(row['arrivals']), int(row['total_activity']), int(row['net_flow']),
                            float(row['demand']), int(row['source_year']),
                        ])
            cursor.execute('SELECT COUNT(*) FROM canonical_gold_load')
            staged_count = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT provider_id, network_id, station_instance_id, observed_at
                    FROM canonical_gold_load
                    GROUP BY provider_id, network_id, station_instance_id, observed_at
                    HAVING COUNT(*) > 1
                ) duplicate_keys
                """
            )
            duplicate_count = cursor.fetchone()[0]
            if duplicate_count:
                raise RuntimeError(f'Canonical Gold staging contains {duplicate_count} duplicate keys')
            cursor.execute(
                """SELECT COUNT(*) FROM canonical_gold_load
                   WHERE departures < 0 OR arrivals < 0
                      OR total_activity <> departures + arrivals
                      OR net_flow <> arrivals - departures
                      OR demand <> departures
                      OR length(source_record_hash) <> 64"""
            )
            invalid_count = cursor.fetchone()[0]
            if invalid_count:
                raise RuntimeError(f'Canonical Gold staging contains {invalid_count} rows that violate count/hash invariants')
            manifest = json.loads(source_manifest_json)
            manifest['expected_rows'] = staged_count
            manifest['row_invariants'] = {
                'unique_key': ['provider_id', 'network_id', 'station_instance_id', 'observed_at'],
                'total_activity': 'departures + arrivals',
                'net_flow': 'arrivals - departures',
                'demand': 'departures',
                'counts': 'non-negative integers',
            }
            source_manifest_json = json.dumps(manifest, sort_keys=True)
            cursor.execute(
                """INSERT INTO ingestion_batches
                    (batch_id, source_name, content_sha256, source_manifest_json, expected_rows, status)
                    VALUES (%s, %s, %s, %s, %s, 'loading')
                    ON CONFLICT (batch_id) DO UPDATE SET
                        source_manifest_json = EXCLUDED.source_manifest_json,
                        expected_rows = EXCLUDED.expected_rows, status = 'loading', published_at = NULL""",
                (batch_id, SOURCE_NAME, content_hash, source_manifest_json, staged_count),
            )
            cursor.execute(
                """INSERT INTO station_instances
                    (station_instance_id, provider_id, network_id, provider_station_id, valid_from, valid_to, identity_provenance)
                    SELECT station_instance_id, provider_id, network_id, station_id, MIN(observed_at), MAX(observed_at) + INTERVAL '1 hour',
                           'Source station ID instance; physical-place continuity is not asserted.'
                    FROM canonical_gold_load
                    GROUP BY station_instance_id, provider_id, network_id, station_id
                    ON CONFLICT (station_instance_id) DO NOTHING"""
            )
            # A retry may replace only its own inactive partial batch. Published data
            # from every other batch remains queryable for rollback.
            cursor.execute('DELETE FROM demand_observations WHERE batch_id = %s', (batch_id,))
            cursor.execute(
                """
                INSERT INTO demand_observations
                    (station_id, provider_id, network_id, station_instance_id, source_record_id, source_record_hash, original_time_text, observed_at, departures, arrivals, total_activity, net_flow, demand, source_name, batch_id)
                SELECT station_id, provider_id, network_id, station_instance_id, source_record_id, source_record_hash, original_time_text, observed_at, departures, arrivals, total_activity, net_flow, demand, %s, %s
                FROM canonical_gold_load
                """,
                (SOURCE_NAME, batch_id),
            )
            cursor.execute('SELECT COUNT(*) FROM demand_observations WHERE batch_id = %s', (batch_id,))
            persisted_count = cursor.fetchone()[0]
            if persisted_count != staged_count:
                raise RuntimeError(f'Batch row count mismatch: expected {staged_count}, persisted {persisted_count}')
        connection.commit()
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE ingestion_batches SET status = 'validated'
                   WHERE batch_id = %s AND expected_rows = %s
                   AND (SELECT COUNT(*) FROM demand_observations WHERE batch_id = %s) = %s
                   AND NOT EXISTS (
                       SELECT 1 FROM demand_observations
                       WHERE batch_id = %s AND (departures < 0 OR arrivals < 0
                           OR total_activity <> departures + arrivals
                           OR net_flow <> arrivals - departures OR demand <> departures
                           OR length(source_record_hash) <> 64)
                   )""",
                (batch_id, staged_count, batch_id, staged_count, batch_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError('Batch failed persisted validation; active dataset pointer was not changed')
        connection.commit()
        # The only publication operation is this short transaction. The prior
        # batch remains intact and becomes active again if this transaction fails.
        with connection.cursor() as cursor:
            cursor.execute("SELECT status FROM ingestion_batches WHERE batch_id = %s FOR UPDATE", (batch_id,))
            if cursor.fetchone()[0] != 'validated':
                raise RuntimeError('Only a validated batch can be published')
            cursor.execute(
                """INSERT INTO active_dataset_version (singleton_id, batch_id) VALUES (1, %s)
                   ON CONFLICT (singleton_id) DO UPDATE SET batch_id = EXCLUDED.batch_id""",
                (batch_id,),
            )
            cursor.execute("UPDATE ingestion_batches SET status = 'published', published_at = now() WHERE batch_id = %s", (batch_id,))
    print({'batch_id': batch_id, 'content_sha256': content_hash, 'staged_count': staged_count, 'duplicate_count': duplicate_count, 'persisted_count': persisted_count, 'status': 'published'})


if __name__ == '__main__':
    main()
