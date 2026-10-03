"""Provider adapters for the reproducible trip-to-web pipeline.

Adapters return hourly UTC counts, observed station locations and source
manifests. Provider-specific fields stop here; models consume a common schema.
"""

from __future__ import annotations

import hashlib
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd
import requests
from src.config.settings import ProviderConfigModel
from src.ingestion.rebuild import ingest_year, local_times


def provider_root(root: Path, config: ProviderConfigModel) -> Path:
    """Keep the original BiciMAD paths; isolate every additional network."""
    if config.provider == 'bicimad' and config.network_id == 'madrid':
        return root
    return root / 'data' / 'providers' / config.provider / config.network_id


class TripProvider(ABC):
    """Normalize source trips; never infer live availability from trip counts."""

    def __init__(self, config: ProviderConfigModel):
        self.config = config

    @abstractmethod
    def ingest(self, root: Path, year: int) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
        """Return counts(time, station, departures, arrivals), locations and provenance."""

    def download(self, root: Path, year: int) -> None:
        """Fetch configured yearly archives atomically; retain existing local inputs."""
        for source in self.config.historical:
            if source.year != year:
                continue
            path = root / 'data/bronze/historical' / f'{year}.zip'
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.download')
            try:
                with requests.get(source.direct_url, stream=True, timeout=(20, 180)) as response:
                    response.raise_for_status()
                    with temporary.open('wb') as stream:
                        for chunk in response.iter_content(1024 * 1024):
                            stream.write(chunk)
                if not zipfile.is_zipfile(temporary):
                    raise ValueError(f'Expected a ZIP archive from {source.direct_url}')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)


class BiciMadTrips(TripProvider):
    """Official exact-time BiciMAD archives; legacy rounded trips stay separate."""

    def ingest(self, root: Path, year: int):
        return ingest_year(root, year, timezone=self.config.timezone)


class CsvTrips(TripProvider):
    """Mapped CSV or CSV-in-ZIP input, including alphanumeric station identifiers.

The configured glob is relative to the provider workspace. Deduplication spans
chunks and files using all source columns, so a provider trip ID, if present,
participates without exposing it in public artifacts.
"""

    def ingest(self, root: Path, year: int):
        settings = self.config.trips
        required = {'started_at', 'ended_at', 'origin_station', 'destination_station',
                    'latitude', 'longitude'}
        if not required.issubset(settings.columns):
            raise ValueError(f'Missing CSV mappings: {sorted(required - settings.columns.keys())}')
        paths = sorted(root.glob(settings.file_glob.format(year=year)))
        if not paths:
            raise FileNotFoundError(f'No source files match {root / settings.file_glob}')
        counts, locations, manifests = [], [], []
        seen: set[int] = set()

        def consume(stream, label: str):
            quality = dict.fromkeys(['source_rows', 'blank_rows', 'duplicate_rows', 'raw_rows',
                                     'departures', 'arrivals', 'rejected_departures',
                                     'rejected_arrivals'], 0)
            for raw in pd.read_csv(stream, sep=settings.separator, encoding=settings.encoding,
                                   dtype='string', chunksize=75000):
                quality['source_rows'] += len(raw)
                quality['blank_rows'] += int(raw.isna().all(axis=1).sum())
                raw = raw.dropna(how='all')
                identities = pd.util.hash_pandas_object(raw, index=False)
                keep = ~(identities.duplicated() | identities.isin(seen))
                seen.update(identities.tolist())
                quality['duplicate_rows'] += int((~keep).sum())
                raw = raw.loc[keep]
                missing = set(settings.columns.values()) - set(raw.columns)
                if missing:
                    raise ValueError(f'{label}: missing columns {sorted(missing)}')
                frame = raw.rename(columns={v: k for k, v in settings.columns.items()})
                start = local_times(frame.started_at, self.config.timezone)
                end = local_times(frame.ended_at, self.config.timezone)
                origin = frame.origin_station.str.strip()
                destination = frame.destination_station.str.strip()
                valid_start = start.notna() & origin.notna() & origin.ne('')
                valid_end = end.notna() & destination.notna() & destination.ne('') & ~end.lt(start)
                for times, stations, valid, event in [
                    (start, origin, valid_start, 'departures'),
                    (end, destination, valid_end, 'arrivals'),
                ]:
                    counts.append(pd.DataFrame({'time': times[valid].dt.floor('h'),
                                                'station': stations[valid], event: 1}))
                    quality[event] += int(valid.sum())
                    quality[f'rejected_{event}'] += int((~valid).sum())
                quality['raw_rows'] += len(frame)
                place = pd.DataFrame({'station': origin, 'name': frame.get('name', origin),
                                      'latitude': pd.to_numeric(frame.latitude, errors='coerce'),
                                      'longitude': pd.to_numeric(frame.longitude, errors='coerce')})
                place = place[origin.notna() & origin.ne('') & place.latitude.between(-90, 90)
                              & place.longitude.between(-180, 180)]
                locations.append(place.drop_duplicates())
            return quality

        for path in paths:
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if path.suffix.lower() == '.zip':
                with zipfile.ZipFile(path) as archive:
                    for name in sorted(archive.namelist()):
                        if name.lower().endswith('.csv') and '__MACOSX' not in name:
                            with archive.open(name) as member_stream:
                                quality = consume(member_stream, f'{path.name}:{name}')
                            manifests.append({'source': f'{path.name}:{name}', 'sha256': digest,
                                              'quality': quality, 'cache_hit': False})
            else:
                quality = consume(path, path.name)
                manifests.append({'source': path.name, 'sha256': digest,
                                  'quality': quality, 'cache_hit': False})
        if not counts or not locations:
            raise ValueError('No readable trips and station coordinates were found')
        demand = pd.concat(counts).fillna({'departures': 0, 'arrivals': 0})
        demand = demand.groupby(['time', 'station'], as_index=False)[['departures', 'arrivals']].sum()
        demand[['departures', 'arrivals']] = demand[['departures', 'arrivals']].astype('int64')
        return demand, pd.concat(locations).drop_duplicates(), manifests


def get_provider(config: ProviderConfigModel) -> TripProvider:
    """Resolve the explicitly configured adapter; unsupported formats fail closed."""
    adapters = {'bicimad': BiciMadTrips, 'csv': CsvTrips}
    return adapters[config.trips.adapter](config)
