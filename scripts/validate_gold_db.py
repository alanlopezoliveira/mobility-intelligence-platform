from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLD_PATH = ROOT / 'data' / 'gold' / 'station_demand_hourly.csv'
REPORT_PATH = ROOT / 'data' / 'gold' / 'gold_postgresql_equality.json'
from src.config.settings import DatabaseSettings, normalize_database_url

DATABASE_URL = normalize_database_url(DatabaseSettings().url)
SOURCE_NAME = 'bicimad_historical_trips'
FIELDS = ('departures', 'arrivals', 'total_activity', 'net_flow', 'demand')


def _normalize_station_id(value: object) -> str:
    text = str(value).strip()
    return str(int(text)) if text.isdigit() else text


def _normalize_timestamp(value: object) -> str:
    text = str(value).strip().replace('Z', '+00:00')
    parsed = datetime.fromisoformat(text).astimezone(UTC)
    return parsed.strftime('%Y-%m-%dT%H:%M:%SZ')


def canonical_row_values(row: dict[str, object]) -> tuple[str, ...]:
    return (
        str(row['provider']).strip().lower(),
        _normalize_station_id(row['station_id']),
        _normalize_timestamp(row['observed_at']),
        str(int(row['departures'])),
        str(int(row['arrivals'])),
        str(int(row['total_activity'])),
        str(int(row['net_flow'])),
        str(int(float(row['demand']))),
    )


def canonical_hash(rows: Iterable[tuple[str, ...]]) -> str:
    digest = hashlib.sha256()
    for values in rows:
        digest.update('|'.join(values).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def _load_gold(cursor: psycopg.Cursor) -> dict[str, object]:
    cursor.execute(
        """
        CREATE TEMP TABLE canonical_gold_reference (
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
            'COPY canonical_gold_reference (provider, station_id, observed_at, departures, arrivals, total_activity, net_flow, demand, source_year) FROM STDIN'
        ) as copy:
            for row in reader:
                values = canonical_row_values(row)
                copy.write_row([*values[:3], *[int(value) for value in values[3:]], int(row['source_year'])])
    cursor.execute('SELECT COUNT(*) FROM canonical_gold_reference')
    row_count = cursor.fetchone()[0]
    cursor.execute(
        'SELECT COUNT(*) FROM (SELECT provider, station_id, observed_at FROM canonical_gold_reference GROUP BY 1,2,3 HAVING COUNT(*) > 1) duplicates'
    )
    duplicate_count = cursor.fetchone()[0]
    if duplicate_count:
        raise RuntimeError(f'Gold reference contains {duplicate_count} duplicate canonical keys')
    return {'row_count': row_count, 'duplicate_count': duplicate_count}


def _fetch_stats(cursor: psycopg.Cursor, table: str, where: str = '', params: tuple[object, ...] = ()) -> dict[str, object]:
    cursor.execute(
        f"""
        SELECT COUNT(*), COUNT(DISTINCT station_id), COUNT(DISTINCT observed_at), MIN(observed_at), MAX(observed_at),
               COALESCE(SUM(departures), 0), COALESCE(SUM(arrivals), 0), COALESCE(SUM(total_activity), 0),
               COALESCE(SUM(net_flow), 0), COALESCE(SUM(demand), 0)
        FROM {table} {where}
        """,
        params,
    )
    row = cursor.fetchone()
    return dict(zip(
        ('row_count', 'station_count', 'timestamp_count', 'min_timestamp', 'max_timestamp', 'departures', 'arrivals', 'total_activity', 'net_flow', 'demand'),
        row,
    ))


def _json_safe_stats(stats: dict[str, object]) -> dict[str, object]:
    output = dict(stats)
    for key in ('min_timestamp', 'max_timestamp'):
        if output[key] is not None:
            output[key] = output[key].astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    if isinstance(output.get('demand'), float) and output['demand'].is_integer():
        output['demand'] = int(output['demand'])
    return output


def main() -> None:
    validation_timestamp = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    with psycopg.connect(DATABASE_URL) as connection, connection.cursor() as cursor:
        gold_load = _load_gold(cursor)
        gold_stats = _fetch_stats(cursor, 'canonical_gold_reference')
        db_stats = _fetch_stats(cursor, 'demand_observations', 'WHERE source_name = %s', (SOURCE_NAME,))

        cursor.execute(
            """
            SELECT COUNT(*) FROM canonical_gold_reference gold
            LEFT JOIN demand_observations db ON db.source_name = %s
                AND db.station_id = gold.station_id AND db.observed_at = gold.observed_at
            WHERE db.id IS NULL
            """,
            (SOURCE_NAME,),
        )
        gold_only_count = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT COUNT(*) FROM demand_observations db
            LEFT JOIN canonical_gold_reference gold
                ON gold.station_id = db.station_id AND gold.observed_at = db.observed_at
            WHERE db.source_name = %s AND gold.station_id IS NULL
            """,
            (SOURCE_NAME,),
        )
        db_only_count = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT COUNT(*) FROM canonical_gold_reference gold
            JOIN demand_observations db ON db.source_name = %s
                AND db.station_id = gold.station_id AND db.observed_at = gold.observed_at
            """,
            (SOURCE_NAME,),
        )
        intersection_count = cursor.fetchone()[0]

        mismatch_counts = {}
        for field in FIELDS:
            cursor.execute(
                f"""
                SELECT COUNT(*) FROM canonical_gold_reference gold
                JOIN demand_observations db ON db.source_name = %s
                    AND db.station_id = gold.station_id AND db.observed_at = gold.observed_at
                WHERE gold.{field}::double precision IS DISTINCT FROM db.{field}::double precision
                """,
                (SOURCE_NAME,),
            )
            mismatch_counts[field] = cursor.fetchone()[0]
        exact_value_mismatch_count = sum(mismatch_counts.values())

        cursor.execute('SELECT source_name, COUNT(*) FROM demand_observations GROUP BY source_name ORDER BY source_name')
        source_counts = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute(
            'SELECT COUNT(*) FROM (SELECT station_id, observed_at FROM demand_observations WHERE source_name = %s GROUP BY 1,2 HAVING COUNT(*) > 1) duplicates',
            (SOURCE_NAME,),
        )
        db_duplicate_key_count = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT COUNT(*) FROM demand_observations db
            LEFT JOIN canonical_gold_reference gold ON gold.station_id = db.station_id AND gold.observed_at = db.observed_at
            WHERE db.source_name = %s AND gold.station_id IS NULL
            """,
            (SOURCE_NAME,),
        )
        stale_bicimad_rows = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT con.conname, pg_get_constraintdef(con.oid)
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            WHERE rel.relname = 'demand_observations'
            ORDER BY con.conname
            """
        )
        constraints = [{'name': row[0], 'definition': row[1]} for row in cursor.fetchall()]
        cursor.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'demand_observations' ORDER BY indexname"
        )
        indexes = [{'name': row[0], 'definition': row[1]} for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT provider, station_id, observed_at, departures, arrivals, total_activity, net_flow, demand
            FROM canonical_gold_reference ORDER BY provider, station_id, observed_at
            """
        )
        authoritative_hash = hashlib.sha256()
        for row in cursor:
            values = (
                str(row[0]).strip().lower(), _normalize_station_id(row[1]),
                row[2].astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
                str(row[3]), str(row[4]), str(row[5]), str(row[6]), str(int(row[7])),
            )
            authoritative_hash.update('|'.join(values).encode('utf-8'))
            authoritative_hash.update(b'\n')
        authoritative_hash = authoritative_hash.hexdigest()
        cursor.execute(
            """
            SELECT 'bicimad', station_id, observed_at, departures, arrivals, total_activity, net_flow, demand
            FROM demand_observations WHERE source_name = %s ORDER BY station_id, observed_at
            """,
            (SOURCE_NAME,),
        )
        db_hash = hashlib.sha256()
        for row in cursor:
            values = (
                str(row[0]).strip().lower(), _normalize_station_id(row[1]),
                row[2].astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
                str(row[3]), str(row[4]), str(row[5]), str(row[6]), str(int(row[7])),
            )
            db_hash.update('|'.join(values).encode('utf-8'))
            db_hash.update(b'\n')
        db_hash = db_hash.hexdigest()

    gold_stats = _json_safe_stats(gold_stats)
    db_stats = _json_safe_stats(db_stats)
    aggregate_match = gold_stats == db_stats
    status = 'PASS' if (
        gold_only_count == 0 and db_only_count == 0 and exact_value_mismatch_count == 0
        and aggregate_match and authoritative_hash == db_hash and stale_bicimad_rows == 0
        and db_duplicate_key_count == 0
    ) else 'BLOCKED'
    report = {
        'validation_timestamp': validation_timestamp,
        'gold_reference': {
            'artifact': str(GOLD_PATH.relative_to(ROOT)),
            'legacy_rebuild_sha256': '56a087784443691156f88d0840ae37e4863594c393eb7df1a0fa769572762ea4',
            'gold_load': gold_load,
        },
        'postgresql_reference': {'table': 'demand_observations', 'source_name': SOURCE_NAME},
        'canonical_serialization': {
            'provider': 'lowercase, trim whitespace',
            'station_id': 'trim whitespace; decimal digit strings converted to base-10 without leading zeroes',
            'observed_at': 'UTC, formatted as YYYY-MM-DDTHH:MM:SSZ',
            'columns': ['provider', 'station_id', 'observed_at', *FIELDS],
            'numeric_representation': 'base-10 integer text; demand integer-valued text',
            'null_representation': 'NULL is forbidden for canonical keys or compared values',
            'row_order': 'provider, station_id, observed_at ascending',
            'delimiter': 'ASCII pipe (|), one LF-terminated row per record, no header',
            'encoding': 'UTF-8',
        },
        'authoritative_equality_hash': authoritative_hash,
        'postgresql_equality_hash': db_hash,
        'legacy_hash_explanation': {
            'gold_rebuild_sha256': '56a087784443691156f88d0840ae37e4863594c393eb7df1a0fa769572762ea4',
            'gold_rebuild_input': 'canonical DataFrame serialized by pandas to CSV with header, comma delimiter, source_year included, and pandas timestamp text',
            'previous_normalized_hash': '5465b80faf905bab60e689b01eabdcfccccd84f93860c806348c3e3c2c43c70a',
            'previous_normalized_input': 'pipe-delimited rows excluding source_year, sorted by provider/station/timestamp, with integer-valued floats normalized',
            'expected_difference': True,
            'reason': 'The hashes covered different column sets and serialization formats; neither is the single authoritative equality hash unless both sides use the shared specification above.',
        },
        'gold': gold_stats,
        'postgresql': db_stats,
        'key_comparison': {
            'gold_only_count': gold_only_count,
            'postgresql_only_count': db_only_count,
            'intersection_count': intersection_count,
        },
        'value_comparison': {
            'exact_matching_rows': intersection_count if exact_value_mismatch_count == 0 else None,
            'exact_value_mismatch_count': exact_value_mismatch_count,
            'mismatch_count_by_field': mismatch_counts,
        },
        'aggregate_comparison': {'match': aggregate_match, 'gold': gold_stats, 'postgresql': db_stats},
        'stale_row_audit': {
            'source_counts': source_counts,
            'stale_bicimad_rows': stale_bicimad_rows,
            'unrelated_sources_preserved': {key: value for key, value in source_counts.items() if key != SOURCE_NAME},
        },
        'constraint_audit': {
            'constraints': constraints,
            'indexes': indexes,
            'canonical_duplicate_attempts_fail_loudly': any('uq_demand_station_time' in item['name'] for item in constraints),
            'source_level_aggregation_required': True,
        },
        'final_status': status,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == '__main__':
    main()
