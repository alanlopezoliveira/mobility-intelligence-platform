"""Rebuild raw trips -> hourly data -> real weather -> models -> web exports.

Run: python scripts/rebuild_project.py [--force]
The verified full-year reference run is 2022; older privacy-rounded trips and
the partial 2023 distribution remain in the historical audit, not this model.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_ROOT = ROOT
sys.path.insert(0, str(ROOT))

import pandas as pd
from scripts.analyze_project_evidence import run as analyze_evidence
from scripts.analyze_project_evidence import weather_diagnostics
from scripts.document_model_comparison import write_comparison
from src.config.settings import load_provider_config
from src.ingestion.weather import historical_weather
from src.ml.rebuilt import train_models
from src.ml.weather_analysis import compare_weather
from src.providers.registry import get_provider, provider_root


def write_json(path, value):
    """Publish valid JSON atomically; a failed run cannot expose a partial report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, default=str), encoding="utf-8"
    )
    temporary.replace(path)


def station_export_key(station):
    """Use a safe filename even when a source station ID contains slashes or Unicode."""
    return hashlib.sha256(str(station).encode('utf-8')).hexdigest()


def publish_catalog():
    """Discover completed reports; the UI switches providers without overwriting data."""
    base = ROOT / 'frontend/public/project-data'
    datasets = []
    for path in sorted((base / 'providers').glob('*/*/*/report.json')):
        report = json.loads(path.read_text(encoding='utf-8'))
        provider = report['provider']
        datasets.append({'id': path.parent.relative_to(base / 'providers').as_posix(),
                         'name': provider['name'], 'city': provider['city'],
                         'year': report['year']})
    write_json(base / 'catalog.json', {'datasets': datasets})


def publish_demand(config, demand, stations):
    """Publish the normalized observations and station metadata for one network."""
    from src.db.publication import publish_network

    publish_network(config, demand, stations)


def prepare_inventory(config, workspace, download, year):
    """Run the separate historical inventory workflow only for its known adapter."""
    if config.provider != 'bicimad' or config.network_id != 'madrid':
        raise ValueError('Inventory diagnostics currently support only BiciMAD Madrid')
    if download:
        for snapshot_year in (2018, 2019, 2020):
            get_provider(config).download(workspace, snapshot_year)
    from scripts.build_audit_report import CACHE, OUT, occupancy_audit

    CACHE.mkdir(parents=True, exist_ok=True)
    write_json(OUT / 'inventory.json', {'occupancy': occupancy_audit()})
    analyze_evidence(weather_path=workspace / f'data/rebuilt/{year}/network-weather-hourly.csv')


def run(force=False, provider='bicimad', year=None, download=False, inventory=False,
        publish_database=False):
    """Prepare one provider/year and publish its isolated web exports.

    Inventory is a separate, explicitly requested BiciMAD historical workflow.
    A trip-only provider never needs station snapshot archives to complete.
    """
    start = time.perf_counter()
    timings = {}
    config = load_provider_config(provider)
    year = year or config.reference_year
    workspace = provider_root(ROOT, config)
    scope = f'{config.provider}/{config.network_id}/{year}'
    output = ROOT / 'frontend/public/project-data/providers' / scope
    model_dir = ROOT / 'models/providers' / scope
    data_dir = workspace / f'data/rebuilt/{year}'
    adapter = get_provider(config)
    if download:
        adapter.download(workspace, year)
    output.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    demand, locations, sources = adapter.ingest(workspace, year)
    if locations.empty:
        raise ValueError('No valid station locations; weather requires observed coordinates')
    timings["ingestion_seconds"] = round(time.perf_counter() - start, 3)
    # Apply the same year boundary before cache reuse and fresh publication.
    # Otherwise a cached run could publish source-year spillover to PostgreSQL.
    demand = demand[
        (demand.time >= pd.Timestamp(f'{year}-01-01', tz='UTC'))
        & (demand.time < pd.Timestamp(f'{year + 1}-01-01', tz='UTC'))
    ].copy()
    # Geographic center is derived from actual trip origin locations, not a typed city name.
    stations = (
        locations.groupby("station")
        .agg(
            name=("name", "last"),
            latitude=("latitude", "median"),
            longitude=("longitude", "median"),
            latitude_span=("latitude", lambda x: x.max() - x.min()),
            longitude_span=("longitude", lambda x: x.max() - x.min()),
        )
        .reset_index()
    )
    mark = time.perf_counter()
    weather, weather_meta = historical_weather(
        float(stations.latitude.median()),
        float(stations.longitude.median()),
        year,
        workspace / "data/bronze/weather",
    )
    timings["weather_seconds"] = round(time.perf_counter() - mark, 3)
    code_paths = [
        Path(__file__),
        CODE_ROOT / "src/ingestion/rebuild.py",
        CODE_ROOT / "src/ingestion/weather.py",
        CODE_ROOT / "src/ml/rebuilt.py",
        CODE_ROOT / "src/ml/model_comparison.py",
        CODE_ROOT / "src/ml/weather_analysis.py",
        CODE_ROOT / "src/providers/registry.py",
        CODE_ROOT / "scripts/analyze_project_evidence.py",
    ]
    dependencies = {
        name: importlib.metadata.version(name)
        for name in [
            "pandas",
            "numpy",
            "lightgbm",
            "scikit-learn",
            "requests",
            "xgboost",
            "catboost",
            "joblib",
            "threadpoolctl",
        ]
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "sources": [s["sha256"] for s in sources],
                "weather": weather_meta["sha256"],
                "code": [hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths],
                "dependencies": dependencies,
                "provider": config.model_dump(mode='json'),
                "year": year,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    report_path = output / "report.json"
    manifest_path = data_dir / "artifacts.json"
    if not force and report_path.exists() and manifest_path.exists():
        old = json.loads(report_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        intact = all(
            (ROOT / p).is_file() and hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == sha
            for p, sha in manifest.items()
        )
        if old.get("fingerprint") == fingerprint and intact:
            if publish_database:
                publish_demand(config, demand, stations)
            if inventory:
                prepare_inventory(config, workspace, download, year)
            publish_catalog()
            timings["total_seconds"] = round(time.perf_counter() - start, 3)
            write_json(data_dir / "last-run.json", {"cache_hit": True, "timings": timings})
            print(
                f"Validated cached pipeline in {timings['total_seconds']:.2f}s; models and web exports unchanged",
                flush=True,
            )
            return
    mark = time.perf_counter()
    network = demand.groupby("time", as_index=False)[["departures", "arrivals"]].sum()
    network = network[network.departures.gt(0)]
    association, joined = compare_weather(network, weather, timezone=config.timezone)
    demand.to_csv(
        data_dir / "station-hourly.csv.gz",
        index=False,
        compression={"method": "gzip", "compresslevel": 1},
    )
    joined.to_csv(data_dir / "network-weather-hourly.csv", index=False)
    stations.to_csv(data_dir / "stations.csv", index=False)
    # Retain stable source IDs present in every month and without a material location move.
    months = demand.assign(month=demand.time.dt.month).groupby("station").month.nunique()
    eligible = stations[
        stations.station.isin(months[months.eq(12)].index)
        & stations.latitude_span.le(0.01)
        & stations.longitude_span.le(0.01)
    ]
    if len(eligible) < 1:
        raise ValueError("Insufficient stable station identities")
    index = pd.MultiIndex.from_product([network.time, eligible.station], names=["time", "station"])
    panel = (
        demand[demand.station.isin(eligible.station)]
        .set_index(["time", "station"])
        .reindex(index)
        .fillna(0)
        .reset_index()
    )
    panel[["departures", "arrivals"]] = panel[["departures", "arrivals"]].astype("int32")
    timings["features_analysis_seconds"] = round(time.perf_counter() - mark, 3)
    mark = time.perf_counter()
    # Model features need numbers, but provider IDs are opaque strings. Persist
    # the reversible encoding with the models and restore source IDs in exports.
    station_ids = sorted(panel.station.unique(), key=str)
    encoding = {sid: index for index, sid in enumerate(station_ids)}
    reverse = {index: sid for sid, index in encoding.items()}
    model_panel = panel.assign(station=panel.station.map(encoding))
    metrics, predictions = train_models(model_panel, model_dir, year, force=force,
                                       timezone=config.timezone)
    write_json(model_dir / 'station-encoding.json', {str(sid): code for sid, code in encoding.items()})
    timings["training_seconds"] = round(time.perf_counter() - mark, 3)
    mark = time.perf_counter()
    for horizon, frame in predictions:
        frame = frame.assign(station=frame.station.map(reverse))
        frame.to_csv(model_dir / f'predictions-{horizon}m.csv.gz', index=False,
                     compression={'method': 'gzip', 'compresslevel': 1})
        for sid, rows in frame.groupby("station"):
            columns = [
                "issue",
                "target",
                "y",
                "prediction",
                "recent",
                "day",
                "week",
                "mean_24h",
                "mean_7d",
                "movements_24h",
            ]
            write_json(
                output / f"predictions-{horizon}-{station_export_key(sid)}.json",
                json.loads(rows[columns].to_json(orient="records", date_format="iso")),
            )
    totals = {key: sum(s["quality"][key] for s in sources) for key in sources[0]["quality"]}
    report = {
        "version": "provider-multimodel-v3",
        "provider": {'id': config.provider, 'name': config.name, 'network_id': config.network_id,
                     'city': config.city, 'timezone': config.timezone,
                     'attribution': config.attribution, 'catalog_url': config.catalog_url},
        "generated_at": datetime.now(UTC).isoformat(),
        "fingerprint": fingerprint,
        "scope": f'{config.name} {year} recorded trips; historical research, not live availability',
        "year": year,
        "rows": len(demand),
        "departures": int(demand.departures.sum()),
        "arrivals": int(demand.arrivals.sum()),
        "start": str(network.time.min()),
        "end": str(network.time.max()),
        "hours": len(network),
        "missing_network_hours": int((pd.Timestamp(f'{year + 1}-01-01') - pd.Timestamp(f'{year}-01-01')).total_seconds() / 3600) - len(network),
        "observed_stations": int(demand.station.nunique()),
        "modeled_stations": len(eligible),
        "panel_rows": len(panel),
        "stations": json.loads(eligible.assign(export_key=eligible.station.map(station_export_key)).to_json(orient="records")),
        "quality": totals,
        "sources": sources,
        "weather": weather_meta,
        "association": association,
        "models": metrics,
        "dependencies": dependencies,
        "assumptions": [
            f"Offset-free trip timestamps use {config.timezone}; ambiguous/nonexistent DST times are rejected.",
            "Counts describe recorded trips. CSVs have no reliable customer/operator flag; operator movements cannot be separated.",
            "Zero-filled station-hours mean no recorded movement within a network hour with departures, not known operational availability.",
            "Models use source IDs present in all 12 months with coordinate spans <=0.01 degrees; this reduces but cannot prove physical identity continuity.",
            "Departure and arrival counts use their own timestamps and station IDs independently. Missing origins do not discard valid arrivals; reversed resolved clocks are rejected. Both rejection counters cover every unique nonblank source row.",
            "Models use trailing 24-hour and 7-day departure means and 24-hour recorded movements, ending before issue time. Unknown network hours invalidate rolling windows; no future availability is assumed.",
            "Rain >=0.1 mm in the preceding hour defines rainy. ERA5 is a city-scale reanalysis estimate, not a station sensor.",
            "Weather is used for historical association only: finalized reanalysis would not have been available for real-time forecasting.",
            "Forecast issue time follows completion of the latest input hour. +60/+120 minutes refer to lead to the target hour START; actual ingestion latency must be added for deployment.",
            "These are departure forecasts, not occupancy forecasts. No empty/full prediction alerts are inferred from trip counts.",
            f"The {year} model is a historical research artifact, not a validated live service.",
        ],
        "fixes": [
            "Arrival hours corrected",
            "Real hourly weather with source provenance",
            "Explicit time-based lags and issue times",
            "Ten model candidates with validation-only selection and simplicity tie-break",
            "Consistent nonnegative predictions",
            "Saved model with reload equality check",
            "Identical holdout population for all baselines",
            "Content-hashed monthly caches",
            "Separate historical audit and rebuilt results",
        ],
        "timings": timings,
    }
    timings["export_seconds"] = round(time.perf_counter() - mark, 3)
    timings["total_seconds"] = round(time.perf_counter() - start, 3)
    write_json(report_path, report)
    write_json(data_dir / "report.json", report)
    write_json(output / 'diagnostics.json', {'weather': weather_diagnostics(joined, config.timezone),
                                           'failures': [], 'inventory': None})
    write_comparison(report, model_dir / 'model-comparison.md')
    if publish_database:
        publish_demand(config, demand, stations)
    if inventory:
        prepare_inventory(config, workspace, download, year)
    publish_catalog()
    write_json(data_dir / "last-run.json", {"cache_hit": False, "timings": timings})
    files = [
        *output.glob("*.json"),
        *model_dir.rglob("*"),
        data_dir / "station-hourly.csv.gz",
        data_dir / "network-weather-hourly.csv",
        data_dir / "stations.csv",
    ]
    write_json(
        manifest_path,
        {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files
            if p.is_file()
        },
    )
    print(f"Published rebuilt project in {timings['total_seconds']:.2f}s", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retrain and republish while retaining validated monthly/weather caches",
    )
    parser.add_argument('--provider', default='bicimad', help='Name under config/providers/')
    parser.add_argument('--year', type=int, help='Defaults to the provider reference year')
    parser.add_argument('--download', action='store_true', help='Download configured source archives')
    parser.add_argument('--inventory', action='store_true', help='Also prepare BiciMAD snapshot diagnostics')
    parser.add_argument('--publish-database', action='store_true', help='Publish this network to PostgreSQL')
    args = parser.parse_args()
    run(args.force, args.provider, args.year, args.download, args.inventory, args.publish_database)
