"""Chunked ingestion of the exact-timestamp BiciMAD CSV distributions.

Monthly content hashes invalidate cached aggregates. No raw trip IDs are exported.
Offset-free source clocks follow the configured Madrid timezone; ambiguous and
nonexistent DST clocks are rejected instead of guessed. Zero counts mean no
recorded trip, not proof that a station was operating.
"""

from __future__ import annotations

import ast
import hashlib
import json
import zipfile
from pathlib import Path

import pandas as pd

VERSION = "exact-csv-v3"


def local_times(values: pd.Series, timezone: str = 'Europe/Madrid') -> pd.Series:
    """Resolve local civil clocks to UTC, leaving ambiguous DST clocks missing."""
    # Offset-bearing timestamps already identify an instant; naive values need
    # the provider's civil timezone. Handle both without guessing DST folds.
    text = values.astype('string')
    aware = text.str.contains(r'(?:Z|[+-]\d{2}:?\d{2})$', na=False)
    result = pd.Series(pd.NaT, index=values.index, dtype='datetime64[ns, UTC]')
    result.loc[aware] = pd.to_datetime(text[aware], errors='coerce', format='mixed', utc=True)
    result.loc[~aware] = (
        pd.to_datetime(text[~aware], errors='coerce', format='mixed')
        .dt.tz_localize(timezone, ambiguous='NaT', nonexistent='NaT').dt.tz_convert('UTC')
    )
    return result


def normalize_chunk(raw: pd.DataFrame, timezone: str = 'Europe/Madrid'):
    """Return independent departure/arrival events and explicit rejection counts."""
    raw = raw.dropna(how="all").copy()
    started, ended = local_times(raw.unlock_date, timezone), local_times(raw.lock_date, timezone)
    origin = pd.to_numeric(raw.station_unlock, errors="coerce")
    destination = pd.to_numeric(raw.station_lock, errors="coerce")
    valid = started.notna() & origin.gt(0) & origin.mod(1).eq(0)
    # A missing/invalid origin does not erase an independently dated destination event.
    # Reject reversed clocks only when both timestamps can actually be resolved.
    arrivals = ended.notna() & ~ended.lt(started) & destination.gt(0) & destination.mod(1).eq(0)
    departures = pd.DataFrame(
        {
            "time": started[valid].dt.floor("h"),
            "station": origin[valid].astype(int),
            "departures": 1,
        }
    )
    incoming = pd.DataFrame(
        {
            "time": ended[arrivals].dt.floor("h"),
            "station": destination[arrivals].astype(int),
            "arrivals": 1,
        }
    )
    counts = (
        pd.concat([departures, incoming])
        .fillna(0)
        .groupby(["time", "station"])[["departures", "arrivals"]]
        .sum()
    )
    quality = {
        "raw_rows": len(raw),
        "departures": int(valid.sum()),
        "arrivals": int(arrivals.sum()),
        "rejected_departures": int((~valid).sum()),
        "rejected_arrivals": int((~arrivals).sum()),
        "arrivals_without_valid_departure": int((arrivals & ~valid).sum()),
        "invalid_departure_time": int(started.isna().sum()),
        "invalid_departure_station": int((started.notna() & ~valid).sum()),
        "invalid_arrival_time": int(ended.isna().sum()),
        "invalid_arrival_station": int(
            (ended.notna() & ~(destination.gt(0) & destination.mod(1).eq(0))).sum()
        ),
        "reversed_arrival_time": int(
            (ended.notna() & destination.gt(0) & destination.mod(1).eq(0) & ended.lt(started)).sum()
        ),
    }
    return counts, quality


def ingest_year(root: Path, year: int, timezone: str = 'Europe/Madrid'):
    """Aggregate available monthly archives without materializing raw trips in RAM."""
    sources = sorted((root / "data/bronze/historical_members").glob(f"{year}_*csv.zip"))
    if not sources:
        # Reuse the official downloaded outer distribution, extracting only CSV ZIPs.
        outer = root / f"data/bronze/historical/{year}.zip"
        with zipfile.ZipFile(outer) as archive:
            for item in archive.infolist():
                if item.filename.lower().endswith("csv.zip") and "__MACOSX" not in item.filename:
                    path = (
                        root
                        / "data/bronze/historical_members"
                        / f"{year}_{Path(item.filename).name}"
                    )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(item))
        sources = sorted((root / "data/bronze/historical_members").glob(f"{year}_*csv.zip"))
    if not sources:
        raise FileNotFoundError(
            f"No exact-timestamp CSV archives for {year}; download the official yearly ZIP first"
        )
    cache = root / "data/rebuilt/cache"
    cache.mkdir(parents=True, exist_ok=True)
    frames, manifests, stations = [], [], []
    seen_archives: set[str] = set()
    for path in sources:
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if sha in seen_archives:
            continue
        seen_archives.add(sha)
        policy = hashlib.sha256(timezone.encode()).hexdigest()[:12]
        stem = cache / f"{VERSION}-{policy}-{sha}"
        metadata = stem.with_suffix(".json")
        hit = metadata.exists() and stem.with_suffix(".csv.gz").exists()
        if hit:
            report = json.loads(metadata.read_text(encoding="utf-8"))
            hit = (
                report.get("aggregate_sha256")
                == hashlib.sha256(stem.with_suffix(".csv.gz").read_bytes()).hexdigest()
            )
        if hit:
            frame = pd.read_csv(stem.with_suffix(".csv.gz"))
            frame.time = pd.to_datetime(frame.time, utc=True)
        else:
            parts, locations = [], []
            totals: dict[str, int] = {}
            seen_rows: set[int] = set()
            with zipfile.ZipFile(path) as archive:
                names = [
                    n
                    for n in archive.namelist()
                    if n.lower().endswith(".csv") and "__MACOSX" not in n
                ]
                if len(names) != 1:
                    raise ValueError(f"Expected one movement CSV: {path}")
                with archive.open(names[0]) as stream:
                    for raw in pd.read_csv(
                        stream,
                        sep=";",
                        encoding="utf-8",
                        encoding_errors="replace",
                        chunksize=75000,
                        dtype=str,
                    ):
                        if not {
                            "unlock_date",
                            "lock_date",
                            "station_unlock",
                            "station_lock",
                        }.issubset(raw.columns):
                            raise ValueError(f"Unsupported CSV schema: {path}")
                        # Across chunk boundaries too; do not mistake a bike/user ID for a trip ID.
                        totals["source_rows"] = totals.get("source_rows", 0) + len(raw)
                        totals["blank_rows"] = totals.get("blank_rows", 0) + int(
                            raw.isna().all(axis=1).sum()
                        )
                        raw = raw.dropna(how="all")
                        identities = pd.util.hash_pandas_object(raw, index=False)
                        keep = ~(identities.duplicated() | identities.isin(seen_rows))
                        totals["duplicate_rows"] = totals.get("duplicate_rows", 0) + int(
                            (~keep).sum()
                        )
                        seen_rows.update(identities.tolist())
                        raw = raw.loc[keep]
                        part, quality = normalize_chunk(raw, timezone)
                        parts.append(part)
                        for key, value in quality.items():
                            totals[key] = totals.get(key, 0) + value
                        if "geolocation_unlock" in raw:
                            locations.append(
                                raw[
                                    ["station_unlock", "unlock_station_name", "geolocation_unlock"]
                                ].drop_duplicates()
                            )
            frame = pd.concat(parts).groupby(level=[0, 1]).sum().reset_index()
            frame[["departures", "arrivals"]] = frame[["departures", "arrivals"]].astype(int)
            location_rows = []
            location_frame = pd.concat(locations).drop_duplicates() if locations else pd.DataFrame()
            for row in location_frame.itertuples(index=False, name=None):
                try:
                    sid, name, geo = row
                    lon, lat = ast.literal_eval(geo)["coordinates"]
                    if 40 < lat < 41 and -4 < lon < -3 and float(sid) > 0:
                        location_rows.append(
                            {
                                "station": int(float(sid)),
                                "name": name,
                                "latitude": lat,
                                "longitude": lon,
                            }
                        )
                except (ValueError, TypeError, SyntaxError, KeyError):
                    continue
            report = {
                "source": path.name,
                "sha256": sha,
                "quality": totals,
                "stations": location_rows,
                "start": str(frame.time.min()),
                "end": str(frame.time.max()),
            }
            frame.to_csv(
                stem.with_suffix(".csv.gz"),
                index=False,
                compression={"method": "gzip", "compresslevel": 1},
            )
            report["aggregate_sha256"] = hashlib.sha256(
                stem.with_suffix(".csv.gz").read_bytes()
            ).hexdigest()
            metadata.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        print(
            f"Ingestion {path.name}: {len(frame):,} station-hours ({'cache' if hit else 'rebuilt'})",
            flush=True,
        )
        frames.append(frame)
        stations.extend(report["stations"])
        manifests.append({k: v for k, v in report.items() if k != "stations"} | {"cache_hit": hit})
    result = (
        pd.concat(frames)
        .groupby(["time", "station"], as_index=False)[["departures", "arrivals"]]
        .sum()
    )
    return result, pd.DataFrame(stations).drop_duplicates(), manifests
