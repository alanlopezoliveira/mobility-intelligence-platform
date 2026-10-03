"""Transactional publication of normalized provider observations.

Only a completely validated batch becomes visible. Replaying a prior batch
reactivates it for its own network, without changing any other network's data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from src.config.settings import ProviderConfigModel
from src.db.database import SessionLocal
from src.db.models import (
    DemandObservation,
    IngestionBatch,
    NetworkDatasetVersion,
    NetworkMetadata,
    ProviderMetadata,
    Station,
    StationInstance,
)


def publish_network(config: ProviderConfigModel, demand: pd.DataFrame, stations: pd.DataFrame,
                    session_factory=SessionLocal) -> str:
    """Validate and publish one network; return its content-addressed batch ID.

    Demand uses UTC `time`, opaque `station`, and integer directional counts.
    Station locations are optional enrichment, never fabricated observations.
    All writes and the active-pointer switch share one database transaction.
    """
    frame = demand[['time', 'station', 'departures', 'arrivals']].copy()
    frame['station'] = frame.station.astype(str)
    if frame.empty or frame.duplicated(['time', 'station']).any():
        raise ValueError('Publication requires nonempty, unique station-hour observations')
    if frame.station.str.len().gt(64).any() or frame.station.eq('').any():
        raise ValueError('Station identifiers must contain 1–64 characters')
    frame['time'] = pd.to_datetime(frame.time)
    if frame.time.dt.tz is None or frame.time.isna().any():
        raise ValueError('Publication timestamps must be timezone-aware instants')
    frame['time'] = frame.time.dt.tz_convert('UTC')
    counts = frame[['departures', 'arrivals']]
    if counts.isna().any().any() or (counts < 0).any().any() or (counts % 1 != 0).any().any():
        raise ValueError('Directional counts must be nonnegative integers')
    frame = frame.sort_values(['station', 'time']).reset_index(drop=True)
    scope = {'provider_id': config.provider, 'network_id': config.network_id}
    digest = hashlib.sha256(json.dumps({**scope, 'source': config.source_name}, sort_keys=True).encode())
    digest.update(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes())
    batch_id = digest.hexdigest()
    with session_factory.begin() as session:
        session.execute(insert(ProviderMetadata).values(
            provider_id=config.provider, timezone=config.timezone, calendar_version=config.calendar_version,
        ).on_conflict_do_update(index_elements=['provider_id'], set_={
            'timezone': config.timezone, 'calendar_version': config.calendar_version,
        }))
        session.execute(insert(NetworkMetadata).values(**scope, name=config.name).on_conflict_do_update(
            index_elements=['provider_id', 'network_id'], set_={'name': config.name},
        ))
        metadata = stations.copy()
        metadata['station'] = metadata.station.astype(str)
        metadata = metadata.drop_duplicates('station').set_index('station')
        for sid, rows in frame.groupby('station'):
            sid = str(sid)
            instance = f'{config.provider}:{config.network_id}:{sid}'
            if len(instance) > 160:
                raise ValueError('Combined station instance ID exceeds 160 characters')
            first, last = rows.time.min().to_pydatetime(), rows.time.max().to_pydatetime() + timedelta(hours=1)
            session.execute(insert(StationInstance).values(
                **scope, station_instance_id=instance, provider_station_id=sid,
                valid_from=first, valid_to=last,
                identity_provenance='Source identifier; physical-place continuity is not asserted.',
            ).on_conflict_do_update(index_elements=['station_instance_id'], set_={
                'valid_from': func.least(StationInstance.valid_from, first),
                'valid_to': func.greatest(StationInstance.valid_to, last),
            }))
            place = metadata.loc[sid].to_dict() if sid in metadata.index else {}
            values = {key: value if pd.notna(value) else None for key, value in place.items()
                      if key in {'name', 'latitude', 'longitude'}}
            values.update(source_name=config.source_name, updated_at=datetime.now(UTC))
            session.execute(insert(Station).values(**scope, station_id=sid, **values).on_conflict_do_update(
                index_elements=['provider_id', 'network_id', 'station_id'], set_=values,
            ))
        existing = session.get(IngestionBatch, batch_id)
        if existing is None:
            session.add(IngestionBatch(
                batch_id=batch_id, source_name=config.source_name, content_sha256=batch_id,
                source_manifest_json=json.dumps({**scope, 'timezone': config.timezone}),
                expected_rows=len(frame), status='loading', created_at=datetime.now(UTC),
            ))
            session.flush()
            chunk = []
            for row in frame.itertuples(index=False):
                instant = row.time.to_pydatetime()
                instance = f'{config.provider}:{config.network_id}:{row.station}'
                record_id = f'{instance}:{instant.isoformat()}'
                departures, arrivals = int(row.departures), int(row.arrivals)
                chunk.append({**scope, 'batch_id': batch_id, 'station_id': row.station,
                              'station_instance_id': instance, 'observed_at': instant,
                              'departures': departures, 'arrivals': arrivals,
                              'total_activity': departures + arrivals, 'net_flow': arrivals - departures,
                              'demand': departures, 'source_name': config.source_name,
                              'source_record_id': record_id, 'original_time_text': instant.isoformat(),
                              'source_record_hash': hashlib.sha256(f'{record_id}:{departures}:{arrivals}'.encode()).hexdigest()})
                if len(chunk) >= 10000:
                    session.execute(insert(DemandObservation), chunk)
                    chunk = []
            if chunk:
                session.execute(insert(DemandObservation), chunk)
        actual = session.scalar(select(func.count()).select_from(DemandObservation).where(
            DemandObservation.batch_id == batch_id,
        ))
        if actual != len(frame):
            raise ValueError('Published row count differs from the validated input')
        batch = session.get(IngestionBatch, batch_id)
        assert batch is not None
        batch.status, batch.published_at = 'published', datetime.now(UTC)
        session.execute(insert(NetworkDatasetVersion).values(**scope, batch_id=batch_id).on_conflict_do_update(
            index_elements=['provider_id', 'network_id'], set_={'batch_id': batch_id},
        ))
    return batch_id
