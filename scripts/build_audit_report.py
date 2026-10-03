"""Audit local evidence and export a database-free dashboard; never train or impute data.

Run from the repository root: python scripts/build_audit_report.py
Outputs: data/audit/report.json, data/audit/full-audit.md, frontend/public/audit-data/.
Only configured Bronze archives, canonical Gold and benchmark_v2 are evidence.
Test fixtures, temporary forecasts and legacy production metrics are excluded.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import io
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import rarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "frontend/public/audit-data"
CACHE = ROOT / "data/audit"


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, default=str), encoding="utf-8"
    )


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def records(frame):
    return json.loads(frame.to_json(orient="records", date_format="iso"))


from src.ingestion.snapshots import parse_snapshot_bytes


def snapshot_frames():
    frames, sources, failures = [], [], []
    # GNU tar cannot read RAR. Prefer libarchive on Linux and Windows' tar.exe.
    extractor = shutil.which('bsdtar') or (shutil.which('tar') if sys.platform == 'win32' else None)
    if extractor:
        rarfile.BSDTAR_TOOL = extractor

    def visit(data, name):
        try:
            if name.lower().endswith(".rar") and extractor:
                # Windows tar handles these original RAR archives, while the
                # rarfile subprocess path fails. Stream stdout, never extract
                # archive-controlled paths into the working tree.
                with tempfile.NamedTemporaryFile(dir=CACHE, suffix=".rar", delete=False) as stream:
                    stream.write(data)
                    temporary = Path(stream.name)
                try:
                    with rarfile.RarFile(io.BytesIO(data)) as archive:
                        for item in archive.infolist():
                            if item.is_dir() or not item.filename.lower().endswith(".json"):
                                continue
                            output = subprocess.run(
                                [
                                    extractor,
                                    "-xOf",
                                    temporary.relative_to(ROOT).as_posix(),
                                    "--",
                                    item.filename,
                                ],
                                cwd=ROOT,
                                capture_output=True,
                                check=True,
                            )
                            visit(output.stdout, name + "/" + item.filename)
                finally:
                    temporary.unlink(missing_ok=True)
                return
            if name.lower().endswith((".zip", ".rar")):
                cls = zipfile.ZipFile if name.lower().endswith(".zip") else rarfile.RarFile
                with cls(io.BytesIO(data)) as archive:
                    for item in archive.infolist():
                        if not item.is_dir() and "__MACOSX" not in item.filename:
                            visit(archive.read(item), name + "/" + item.filename)
            elif name.lower().endswith(".json"):
                frame = parse_snapshot_bytes(data, name)
                frames.append(frame)
                sources.append(
                    {
                        "source": name,
                        "rows": len(frame),
                        "snapshots": int(frame.time.nunique()),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
        except (
            rarfile.Error,
            OSError,
            ValueError,
            TypeError,
            subprocess.CalledProcessError,
        ) as exc:
            failures.append({"source": name, "reason": str(exc)})

    for path in sorted((ROOT / "data/bronze/historical").glob("*.zip")):
        print("Inventory archive:", path.name, flush=True)
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                name = item.filename.lower()
                if (
                    not item.is_dir()
                    and "__macosx" not in name
                    and any(s in name for s in ("station", "estacion"))
                ):
                    visit(archive.read(item), path.name + "/" + item.filename)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(), sources, failures


def event_spans(frame, mask):
    """Observed event runs, split at non-event samples or gaps over 90 minutes.

    Durations are last minus first observed time, never time to the next sample.
    Singleton runs have zero observed span; this does not mean zero true duration.
    """
    gap = frame.groupby("station").time.diff().dt.total_seconds().div(60)
    previous = mask.groupby(frame.station).shift(fill_value=False)
    starts = mask & (~previous | gap.gt(90) | gap.isna() | gap.le(0))
    group = starts.cumsum()
    runs = (
        frame.loc[mask, ["station", "time"]]
        .groupby(group[mask])
        .agg(
            station=("station", "first"),
            start=("time", "min"),
            end=("time", "max"),
            samples=("time", "size"),
        )
    )
    runs["span_minutes"] = (runs.end - runs.start).dt.total_seconds() / 60
    return runs


def occupancy_audit():
    """Summarize all readable station histories; export per-station stock series."""
    frame, sources, failures = snapshot_frames()
    if frame.empty:
        return {"status": "not_available", "sources": sources, "failures": failures}
    frame["time"] = pd.to_datetime(frame.time, errors="coerce", format="mixed")
    for col in ["bikes", "capacity", "free", "active", "unavailable", "latitude", "longitude"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    missing = frame.isna().sum().to_dict()
    duplicates = int(frame.duplicated(["station", "time"]).sum())
    conflicts = (
        frame.groupby(["station", "time"])[["bikes", "capacity", "free"]]
        .nunique()
        .gt(1)
        .any(axis=1)
    )
    conflicting_keys = int(conflicts.sum())
    frame = (
        frame.dropna(subset=["time"])
        .drop_duplicates(["station", "time"])
        .sort_values(["station", "time"])
        .reset_index(drop=True)
    )
    if conflicting_keys:
        bad_keys = conflicts[conflicts].index
        frame = frame[
            ~pd.MultiIndex.from_frame(frame[["station", "time"]]).isin(bad_keys)
        ].reset_index(drop=True)
    valid = frame.capacity.gt(0) & frame.bikes.between(0, frame.capacity)
    frame["ratio"] = (frame.bikes / frame.capacity).where(valid)
    frame["empty"] = valid & frame.bikes.eq(0)
    frame["full"] = valid & frame.bikes.eq(frame.capacity)
    frame["near_empty"] = valid & frame.ratio.lt(0.1)
    frame["near_full"] = valid & frame.ratio.gt(0.9)
    # Invalid measurements remain in sequence, breaking runs rather than bridging them.
    empty_runs = event_spans(frame, frame["empty"])
    full_runs = event_spans(frame, frame["full"])
    gaps = frame.groupby("station").time.diff().dt.total_seconds().div(60).dropna()
    stats = (
        frame.groupby("station")
        .agg(
            name=("name", "first"),
            rows=("time", "size"),
            valid_rows=("ratio", "count"),
            mean_occupancy=("ratio", "mean"),
            empty_samples=("empty", "sum"),
            full_samples=("full", "sum"),
            near_empty_samples=("near_empty", "sum"),
            near_full_samples=("near_full", "sum"),
            unavailable_samples=("unavailable", lambda s: int(s.eq(1).sum())),
            latitude=("latitude", "first"),
            longitude=("longitude", "first"),
        )
        .reset_index()
    )
    stats["empty_rate"] = stats.empty_samples / stats.valid_rows
    stats["full_rate"] = stats.full_samples / stats.valid_rows
    stats["empty_runs"] = stats.station.map(empty_runs.station.value_counts()).fillna(0).astype(int)
    stats["full_runs"] = stats.station.map(full_runs.station.value_counts()).fillna(0).astype(int)
    for sid, part in frame.groupby("station"):
        save(
            OUT / f"occupancy-{sid}.json",
            {
                "station": sid,
                "columns": ["time", "bikes", "capacity", "free"],
                "rows": json.loads(
                    part[["time", "bikes", "capacity", "free"]].to_json(
                        orient="values", date_format="iso"
                    )
                ),
            },
        )
    frame["hour"] = frame.time.dt.hour
    frame["weekday"] = frame.time.dt.dayofweek
    frame["month"] = frame.time.dt.strftime("%Y-%m")
    heatmap = (
        frame.groupby(["station", "hour"])
        .agg(occupancy=("ratio", "mean"), samples=("ratio", "count"))
        .reset_index()
    )
    profiles = {}
    for dimension in ["hour", "weekday", "month"]:
        p = (
            frame.groupby(dimension)
            .agg(
                occupancy=("ratio", "mean"),
                samples=("ratio", "count"),
                empty_samples=("empty", "sum"),
                full_samples=("full", "sum"),
            )
            .reset_index()
        )
        p["empty_rate"] = p.empty_samples / p.samples
        p["full_rate"] = p.full_samples / p.samples
        profiles[dimension] = records(p)
    hist, bins = np.histogram(frame.ratio.dropna(), bins=np.linspace(0, 1, 11))
    spans = {}
    for label, runs in [("empty", empty_runs), ("full", full_runs)]:
        spans[label] = {
            "runs": len(runs),
            "singleton_runs": int(runs.samples.eq(1).sum()),
            "observed_span_hours": float(runs.span_minutes.sum() / 60),
            "median_span_minutes": float(runs.span_minutes.median()) if len(runs) else None,
            "max_span_minutes": float(runs.span_minutes.max()) if len(runs) else None,
            "longest": records(runs.nlargest(10, "span_minutes")),
        }
    return {
        "status": "partial" if failures else "available",
        "rows": len(frame),
        "stations": int(frame.station.nunique()),
        "start": str(frame.time.min()),
        "end": str(frame.time.max()),
        "timezone": "Source wall time; offset not provided, UTC alignment unverified",
        "valid_rows": int(valid.sum()) if not conflicting_keys else int(frame.ratio.notna().sum()),
        "missing": missing,
        "duplicate_rows": duplicates,
        "conflicting_keys_excluded": conflicting_keys,
        "negative_bikes": int(frame.bikes.lt(0).sum()),
        "bikes_over_capacity": int(frame.bikes.gt(frame.capacity).sum()),
        "invalid_capacity": int(frame.capacity.le(0).sum()),
        "inventory_not_balanced": int((frame.bikes + frame.free).ne(frame.capacity).sum()),
        "active_values": frame.active.value_counts().to_dict(),
        "unavailable_values": frame.unavailable.value_counts().to_dict(),
        "gap_minutes": {
            "median": float(gaps.median()),
            "p95": float(gaps.quantile(0.95)),
            "max": float(gaps.max()),
            "over_90_minutes": int(gaps.gt(90).sum()),
        },
        "empty_samples": int(frame["empty"].sum()),
        "full_samples": int(frame["full"].sum()),
        "near_empty_samples": int(frame.near_empty.sum()),
        "near_full_samples": int(frame.near_full.sum()),
        "distribution": [
            {"from": float(bins[i]), "to": float(bins[i + 1]), "count": int(n)}
            for i, n in enumerate(hist)
        ],
        "events": spans,
        "station_stats": records(stats),
        "heatmap": records(heatmap),
        "profiles": profiles,
        "sources": sources,
        "failures": failures,
    }


def demand_audit():
    """Scan every canonical row without loading the full demand table in RAM."""
    path = ROOT / "data/gold/station_demand_hourly.csv"
    counts = Counter()
    missing = Counter()
    stations = set()
    times = set()
    key_hashes = []
    profiles = defaultdict(list)
    station_parts = []
    minimum = None
    maximum = None
    calendar = {}
    for frame in pd.read_csv(path, dtype={"station_id": str}, chunksize=300_000):
        counts["rows"] += len(frame)
        missing.update(frame.isna().sum().to_dict())
        stations.update(frame.station_id)
        times.update(frame.observed_at)
        minimum = min(minimum or frame.observed_at.min(), frame.observed_at.min())
        maximum = max(maximum or frame.observed_at.max(), frame.observed_at.max())
        key_hashes.append(
            pd.util.hash_pandas_object(
                frame[["provider", "station_id", "observed_at"]], index=False
            ).to_numpy()
        )
        counts["negative_counts"] += int((frame[["departures", "arrivals"]] < 0).any(axis=1).sum())
        counts["invalid_invariants"] += int(
            (
                frame.demand.ne(frame.departures)
                | frame.total_activity.ne(frame.departures + frame.arrivals)
                | frame.net_flow.ne(frame.arrivals - frame.departures)
            ).sum()
        )
        counts["departures"] += int(frame.departures.sum())
        counts["arrivals"] += int(frame.arrivals.sum())
        counts["zero_departure_rows"] += int(frame.departures.eq(0).sum())
        fresh = sorted(set(frame.observed_at) - calendar.keys())
        if fresh:
            dt = pd.to_datetime(fresh, utc=True).tz_convert("Europe/Madrid")
            calendar.update(zip(fresh, zip(dt.hour, dt.dayofweek, dt.strftime("%Y-%m"))))
        local = pd.DataFrame(
            frame.observed_at.map(calendar).tolist(),
            index=frame.index,
            columns=["hour", "weekday", "month"],
        )
        frame[["hour", "weekday", "month"]] = local
        for col in ["hour", "weekday", "month"]:
            profiles[col].append(
                frame.groupby(col).agg(
                    departures=("departures", "sum"),
                    arrivals=("arrivals", "sum"),
                    rows=("demand", "size"),
                )
            )
        station_parts.append(
            frame.groupby("station_id").agg(
                departures=("departures", "sum"),
                rows=("demand", "size"),
                start=("observed_at", "min"),
                end=("observed_at", "max"),
            )
        )
        print("Demand rows checked:", counts["rows"], flush=True)
    merged_profiles = {}
    for col, values in profiles.items():
        p = pd.concat(values).groupby(level=0).sum()
        p["mean_departures"] = p.departures / p.rows
        merged_profiles[col] = records(p.reset_index())
    by_station = (
        pd.concat(station_parts)
        .groupby(level=0)
        .agg({"departures": "sum", "rows": "sum", "start": "min", "end": "max"})
    )
    hashes = np.concatenate(key_hashes)
    counts["duplicate_keys_hash_check"] = len(hashes) - len(np.unique(hashes))
    hours = int((pd.Timestamp(maximum) - pd.Timestamp(minimum)).total_seconds() / 3600) + 1
    return {
        **dict(counts),
        "start": minimum,
        "end": maximum,
        "stations": len(stations),
        "unique_hours": len(times),
        "calendar_hours": hours,
        "missing_global_hours": hours - len(times),
        "full_rectangle_coverage": counts["rows"] / (hours * len(stations)),
        "missing": dict(missing),
        "sha256": digest(path),
        "profiles": merged_profiles,
        "top_stations": records(by_station.nlargest(20, "departures").reset_index()),
        "granularity": "Hourly, irregular observed station-hours; absent rows are unknown",
    }


def model_audit():
    """Recalculate saved v2 metrics and verify shared keys, labels and input hashes."""
    summary = read_json("models/experiments/benchmark_v2/summary.json")
    report = {
        "summary": summary,
        "horizons": {},
        "serialization": "No fitted estimator artifact found; saved predictions only.",
    }
    for horizon in [60, 120]:
        print("Verifying model artifacts:", horizon, flush=True)
        base = ROOT / f"models/experiments/benchmark_v2/{horizon}m"
        metadata = read_json(f"models/experiments/benchmark_v1/{horizon}m/metadata.json")
        dataset = ROOT / f"data/gold/forecasting/forecasting_dataset_{horizon}m.csv.gz"
        hashes_ok = (
            digest(dataset) == summary["dataset_artifact_hashes"][str(horizon)]
            and digest(ROOT / "data/gold/forecasting/forecasting_contract.json")
            == summary["dataset_contract_hash"]
        )
        metrics = {}
        reference = None
        common_keys_equal = True
        for name in ["lightgbm", "naive_1h", "seasonal_naive_24h", "seasonal_naive_168h"]:
            f = pd.read_csv(base / f"{name}.csv.gz", dtype={"station_id": str})
            keys = f[["station_id", "feature_timestamp", "target_timestamp"]].astype(str)
            keyed = pd.util.hash_pandas_object(keys, index=False).to_numpy()
            if reference is None:
                reference = (keyed, f.y_true.to_numpy())
            else:
                common_keys_equal &= np.array_equal(reference[0], keyed) and np.array_equal(
                    reference[1], f.y_true.to_numpy()
                )
            err = f.y_true - f.y_pred
            denom = ((f.y_true - f.y_true.mean()) ** 2).sum()
            values = {
                "mae": float(err.abs().mean()),
                "rmse": float(np.sqrt((err**2).mean())),
                "r2": float(1 - (err**2).sum() / denom),
                "rows": len(f),
                "negative_predictions": int(f.y_pred.lt(0).sum()),
                "duplicates": int(keys.duplicated().sum()),
            }
            expected = summary["horizons"][str(horizon)]["metrics"][name]
            values["matches_saved_metrics"] = all(
                np.isclose(values[k], expected[k], rtol=1e-9, atol=1e-9)
                for k in ["mae", "rmse", "r2"]
            )
            metrics[name] = values
            if name == "lightgbm":
                station_counts = f.station_id.value_counts()
                # Deterministic high-coverage stations; no error-based cherry-picking.
                selected = station_counts.head(20).index.tolist()
                for sid in selected:
                    part = f[f.station_id.eq(sid)].sort_values("target_timestamp")
                    save(
                        OUT / f"prediction-{horizon}-{sid}.json",
                        records(
                            part[["feature_timestamp", "target_timestamp", "y_true", "y_pred"]]
                        ),
                    )
                sample_stations = [
                    {"station": sid, "rows": int(station_counts[sid])} for sid in selected
                ]
                start, end = f.target_timestamp.min(), f.target_timestamp.max()
        report["horizons"][str(horizon)] = {
            "metadata": metadata,
            "metrics": metrics,
            "input_hashes_match": hashes_ok,
            "common_keys_and_labels_equal": bool(common_keys_equal),
            "sample_stations": sample_stations,
            "start": start,
            "end": end,
            "dataset_columns": pd.read_csv(dataset, nrows=0).columns.tolist(),
        }
    return report


def source_audit():
    """Inventory imports/docstrings and conservatively list unused-code candidates."""
    files = [
        p
        for base in ["src", "scripts", "tests", "examples", "alembic"]
        for p in (ROOT / base).rglob("*.py")
        if "__pycache__" not in p.parts and p.name != Path(__file__).name
    ]
    refs = Counter()
    definitions = []
    imports = defaultdict(set)
    modules = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                refs[node.id] += 1
            if isinstance(node, ast.Attribute):
                refs[node.attr] += 1
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                imports[(node.module or "").split(".")[0]].add(rel)
                for alias in node.names:
                    refs[alias.name] += 1
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports[alias.name.split(".")[0]].add(rel)
        ds = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        if rel.startswith("src/") and ds:
            documented = sum(bool(ast.get_docstring(n)) for n in ds)
            modules.append(
                {
                    "module": rel,
                    "definitions": len(ds),
                    "docstrings": documented,
                    "rating": "Good"
                    if documented == len(ds)
                    else "Partial"
                    if documented
                    else "Missing",
                    "missing_examples": [n.name for n in ds if not ast.get_docstring(n)][:4],
                }
            )
        definitions.extend((rel, n.lineno, n.name, bool(n.decorator_list)) for n in ds)
    deps = []
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    mapping = {
        "scikit-learn": "sklearn",
        "pyyaml": "yaml",
        "pydantic-settings": "pydantic_settings",
        "python-dotenv": "dotenv",
        "pydantic-extra-types": "pydantic_extra_types",
        "types-requests": "requests",
    }
    for group, requirements in [
        ("runtime", config["project"]["dependencies"]),
        *config["project"]["optional-dependencies"].items(),
    ]:
        for spec in requirements:
            name = spec.split(">=")[0].split("[")[0]
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                version = "not installed"
            deps.append(
                {
                    "library": name,
                    "declared": spec,
                    "installed": version,
                    "group": group,
                    "used_in": sorted(imports.get(mapping.get(name, name), [])),
                }
            )
    package = read_json("frontend/package.json")
    lock = read_json("frontend/package-lock.json")
    for group in ["dependencies", "devDependencies"]:
        for name, spec in package[group].items():
            deps.append(
                {
                    "library": name,
                    "declared": spec,
                    "installed": lock["packages"]
                    .get("node_modules/" + name, {})
                    .get("version", "unknown"),
                    "group": "frontend " + group,
                    "used_in": [],
                }
            )
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    tracked_bad = [
        p
        for p in tracked
        if Path(p).suffix in [".zip", ".rar", ".pkl", ".pyc", ".joblib", ".ipynb"]
        or p == ".env"
        or "/__pycache__/" in p
    ]
    junk = []
    for directory in ["$out", "$root", "data/.pytest-tmp"]:
        paths = [p for p in (ROOT / directory).rglob("*") if p.is_file()]
        junk.append(
            {"path": directory, "files": len(paths), "bytes": sum(p.stat().st_size for p in paths)}
        )
    for p in (ROOT / "data/gold/forecasting").glob("tmp*"):
        junk.append({"path": p.relative_to(ROOT).as_posix(), "files": 1, "bytes": p.stat().st_size})
    return {
        "modules": modules,
        "libraries": deps,
        "tracked_files": len(tracked),
        "tracked_inappropriate_candidates": tracked_bad,
        "tracked_over_1mb": [
            p for p in tracked if (ROOT / p).is_file() and (ROOT / p).stat().st_size > 1_000_000
        ],
        "junk": junk,
        "unreferenced_candidates": [
            {"file": p, "line": line, "name": name}
            for p, line, name, decorated in definitions
            if p.startswith("src/")
            and not decorated
            and refs[name] == 0
            and not name.startswith("__")
        ],
        "definition_count": sum(m["definitions"] for m in modules),
        "documented_count": sum(m["docstrings"] for m in modules),
    }


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    # Each cached analysis is opt-in; normal execution always re-reads local evidence.
    reuse = "--reuse-analysis" in sys.argv
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_of_truth": "Local working tree and artifacts, not the remote repository.",
    }
    for name, fn in [
        ("source", source_audit),
        ("demand", demand_audit),
        ("occupancy", occupancy_audit),
        ("model", model_audit),
    ]:
        path = CACHE / f"{name}.json"
        result[name] = read_json(path.relative_to(ROOT)) if reuse and path.exists() else fn()
        save(path, result[name])
        print("Completed:", name, flush=True)
    purposes = {
        "fastapi": "HTTP API and request validation",
        "uvicorn": "ASGI server (CLI/Docker)",
        "pydantic": "Typed configuration and source contracts",
        "sqlalchemy": "Database ORM and SQL queries",
        "alembic": "Schema migrations",
        "psycopg": "PostgreSQL access and canonical feature export",
        "geoalchemy2": "PostGIS geometry columns",
        "pandas": "Ingestion, tabular features and evaluation",
        "numpy": "Numeric operations and metrics",
        "pyyaml": "Provider YAML configuration",
        "requests": "Source downloads and archive diagnostics",
        "httpx": "FastAPI test client dependency",
        "scikit-learn": "Regression metrics",
        "lightgbm": "Experimental boosted-tree regression",
        "pydantic-settings": "Environment-based application settings",
        "python-dotenv": "Indirect .env parsing",
        "orjson": "Declared JSON accelerator; no direct import found",
        "pydantic-extra-types": "Declared extra validators; no direct import found",
        "shapely": "Station point geometry",
        "rarfile": "RAR directory inspection/extraction; external extractor required",
        "pytest": "Unit/contract tests",
        "pytest-asyncio": "Optional async test tooling",
        "pytest-cov": "Optional test coverage tooling",
        "ruff": "Static lint/format tooling",
        "mypy": "Static type checks",
        "types-requests": "Type stubs for HTTP client",
        "xgboost": "Optional research dependency; no active model",
        "catboost": "Optional research dependency; no active model",
        "react": "Interactive user interface",
        "react-dom": "Browser rendering",
        "@types/node": "Node tooling type definitions",
        "@types/react": "React type definitions",
        "@types/react-dom": "React DOM type definitions",
        "@vitejs/plugin-react": "React compilation integration",
        "oxlint": "Frontend lint tooling",
        "typescript": "Frontend static type checking",
        "vite": "Frontend development server and production build",
    }
    for library in result["source"]["libraries"]:
        library["purpose"] = purposes.get(library["library"], "See import references")
    result["model"]["summary"]["benchmark_v1_reference"] = (
        "models/experiments/benchmark_v1/comparability_audit.json"
    )
    result["schemas"] = [
        {
            "source": "Canonical Gold / station_demand_hourly.csv",
            "fields": "provider, station_id, observed_at (UTC hour), departures, arrivals, total_activity, net_flow, demand, source_year",
            "meaning": "demand = departures; arrivals currently assigned to start-hour (A02). No capacity, inventory or weather.",
        },
        {
            "source": "Bronze station JSONL inside yearly ZIP/RAR archives",
            "fields": "_id timestamp; station id/number, dock_bikes, free_bases, total_bases, activate, no_available, name/address, latitude/longitude",
            "meaning": "Inventory snapshots; naive source clock and service flags retained separately. Some capacity is neither occupied nor free.",
        },
        {
            "source": "Station master / station_features.csv",
            "fields": "station_id, capacity, available_bikes, latitude, longitude, station_score",
            "meaning": "Auxiliary snapshot only; identifier and availability semantics invalid for historical joins (A01/A10).",
        },
        {
            "source": "Saved benchmark_v2 prediction CSVs",
            "fields": "station_id, feature_timestamp, target_timestamp, y_true, y_pred, residual, horizon_minutes, model, split",
            "meaning": "Saved chronological test outputs; no occupancy target.",
        },
    ]
    findings = ROOT / "config/audit-findings.json"
    if findings.exists():
        result.update(read_json("config/audit-findings.json"))
    save(CACHE / "report.json", result)
    save(OUT / "report.json", result)
    lines = [
        "# Full project audit",
        "",
        f"Evidence generated: {result['generated_at']}",
        "",
        "This report combines recomputed legacy evidence with historical assessment notes. It does not describe current model selection. See docs/rebuilt-project.md and docs/model-comparison.md for the maintained scope and results.",
        "",
    ]
    for item in result.get("verdicts", []):
        lines += [f"## {item['category']}: {item['rating']}", item["reason"], ""]
    lines += [
        "## Recomputed evidence",
        f"- Demand: {result['demand']['rows']:,} rows, {result['demand']['stations']:,} source station IDs; {result['demand']['start']} to {result['demand']['end']}.",
        f"- Occupancy: {result['occupancy'].get('rows', 0):,} rows from full readable snapshot files. Status: {result['occupancy']['status']}.",
        f"- Empty samples: {result['occupancy'].get('empty_samples', 0):,}; full samples: {result['occupancy'].get('full_samples', 0):,}. These are samples, not durations.",
        "",
        "Full schemas, null counts, event definitions, distributions, source hashes, measured metrics and module/library inventory are in `data/audit/report.json` and the dashboard export. No fixture or obsolete production score is treated as model evidence.",
        "",
    ]
    for schema in result["schemas"]:
        lines += [f"### {schema['source']}", schema["fields"], "", schema["meaning"], ""]
    lines += [
        "### Model results recomputed from saved exports",
        "| Horizon | Method | MAE | RMSE | R² | Rows |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for horizon, values in result["model"]["horizons"].items():
        for method, score in values["metrics"].items():
            lines.append(
                f"| +{horizon}m | {method} | {score['mae']:.4f} | {score['rmse']:.4f} | {score['r2']:.4f} | {score['rows']:,} |"
            )
    lines += [
        "",
        "Coverage is approximately 70% of test targets. Scores do not validate occupancy prediction or current-source reproduction.",
        "",
    ]
    for item in result.get("issues", []):
        lines += [
            f"## {item['id']} · {item['priority']} · {item['title']}",
            item["detail"],
            "",
            f"Evidence: {item['evidence']}",
            "",
            f"Recommendation: {item['recommendation']}",
            "",
        ]
    lines += [
        "## Assumptions and limitations",
        *["- " + x for x in result.get("assumptions", [])],
        "",
        "## Documentation by module",
        "| Module | Rating | Documented definitions | Gap examples |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {m['module']} | {m['rating']} | {m['docstrings']}/{m['definitions']} | {', '.join(m['missing_examples'])} |"
        for m in result["source"]["modules"]
    ]
    lines += [
        "",
        "## Dependency inventory",
        "Installed versions refer to this audit environment; frontend versions come from package-lock.json. Training versions are separately recorded in the export.",
        "| Library | Requirement | Installed / locked | Scope / purpose |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {d['library']} | {d['declared']} | {d['installed']} | {d['group']}: {d['purpose']} |"
        for d in result["source"]["libraries"]
    ]
    (CACHE / "full-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
