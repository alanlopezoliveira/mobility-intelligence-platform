"""Diagnose recorded inventory extremes and monthly rain associations.

Run after rebuild_project.py. Snapshot parsing is cached by archive content hash;
weather diagnostics read the existing, interval-aligned hourly join, never rejoin.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from scripts.build_audit_report import CACHE, ROOT, digest, records, save, snapshot_frames


def inventory_diagnostics(raw):
    frame = raw.copy()
    frame["time"] = pd.to_datetime(frame.time, errors="coerce", format="mixed")
    for col in ["bikes", "capacity", "active", "unavailable"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["time"])
    fields = ["bikes", "capacity", "active", "unavailable"]
    conflicts = frame.groupby(["station", "time"])[fields].nunique(dropna=False).gt(1).any(axis=1)
    frame = frame[
        ~pd.MultiIndex.from_frame(frame[["station", "time"]]).isin(conflicts[conflicts].index)
    ]
    frame = frame.drop_duplicates(["station", "time"]).sort_values(["station", "time"])
    valid = frame.capacity.gt(0) & frame.bikes.between(0, frame.capacity)
    frame = frame[valid].copy()
    frame["empty"] = frame.bikes.eq(0)
    frame["full"] = frame.bikes.eq(frame.capacity)
    frame["hour"] = frame.time.dt.hour
    frame["period"] = np.where(frame.hour.lt(6), "Night (00-05)", "Day/evening (06-23)")
    frame["service"] = np.select(
        [
            (frame.active.eq(0) | frame.unavailable.eq(1)).fillna(False).to_numpy(dtype=bool),
            (frame.active.eq(1) & frame.unavailable.eq(0)).fillna(False).to_numpy(dtype=bool),
        ],
        ["Flagged inactive/unavailable", "Flagged active/available"],
        default="Unknown flags",
    )

    def summarize(keys):
        result = (
            frame.groupby(keys)
            .agg(samples=("time", "size"), empty=("empty", "sum"), full=("full", "sum"))
            .reset_index()
        )
        result["empty_pct"] = result["empty"] / result.samples * 100
        result["full_pct"] = result["full"] / result.samples * 100
        return records(result)

    stations = []
    for sid, part in frame.groupby("station"):
        row = {"station": str(sid), "name": str(part.name.iloc[0]), "samples": len(part)}
        for event in ["empty", "full"]:
            extreme = part[part[event]]
            row[event] = len(extreme)
            row[f"{event}_pct"] = len(extreme) / len(part) * 100
            row[f"{event}_night"] = int(extreme.hour.lt(6).sum())
            row[f"{event}_flagged"] = int(extreme.service.eq("Flagged inactive/unavailable").sum())
        row["groups"] = records(
            part.groupby(["period", "service"])
            .agg(samples=("time", "size"), empty=("empty", "sum"), full=("full", "sum"))
            .reset_index()
        )
        stations.append(row)
    return {
        "start": str(frame.time.min()),
        "end": str(frame.time.max()),
        "samples": len(frame),
        "conflicting_keys_excluded": int(conflicts.sum()),
        "periods": summarize(["period"]),
        "services": summarize(["service"]),
        "cross": summarize(["period", "service"]),
        "hours": summarize(["hour", "service"]),
        "stations": stations,
        "empty": int(frame["empty"].sum()),
        "full": int(frame["full"].sum()),
        "method": "Valid unique station/time samples. Night means source wall-clock 00:00â€“05:59; timezone unverified. Flagged inactive/unavailable means activate=0 OR no_available=1. Flagged active/available requires activate=1 AND no_available=0. Other states remain unknown. Rates are sample-weighted, not operating-time-weighted. Associations do not identify maintenance, rebalancing or customer demand as causes.",
    }


def weather_diagnostics(frame, timezone='Europe/Madrid'):
    """Summarize monthly rain associations in the provider's civil calendar."""
    frame = frame.copy()
    frame["time"] = pd.to_datetime(frame.time, utc=True)
    frame["date"] = frame.time.dt.tz_convert(timezone).dt.strftime("%Y-%m-%d")
    rows = []
    for month, part in frame.groupby("month"):
        wet = part[part.rain.ge(0.1)].copy()
        dry = part[part.rain.lt(0.1)]
        reference = dry.groupby(["hour", "weekend"]).departures.agg(dry_mean="mean", n="size")
        matched = wet.join(reference, on=["hour", "weekend"])
        matched = matched[matched.n.ge(3)]
        ratio = lambda a, b: float((a / b - 1) * 100) if b > 0 else None
        daily = (
            wet.groupby("date")
            .agg(hours=("time", "size"), rain_mm=("rain", "sum"), departures=("departures", "sum"))
            .reset_index()
        )
        # Leave-one-rainy-day-out sensitivity: correlated hours are not independent trials.
        leave_day = []
        for day in matched.date.unique():
            remainder = matched[matched.date.ne(day)]
            if len(remainder):
                leave_day.append(ratio(remainder.departures.mean(), remainder.dry_mean.mean()))
        thresholds = []
        for threshold in [0.1, 0.5, 1.0]:
            subset = matched[matched.rain.ge(threshold)]
            thresholds.append(
                {
                    "threshold_mm": threshold,
                    "hours": len(subset),
                    "days": int(subset.date.nunique()),
                    "adjusted_change_pct": ratio(subset.departures.mean(), subset.dry_mean.mean())
                    if len(subset)
                    else None,
                }
            )
        rows.append(
            {
                "month": int(month),
                "rainy_hours": len(wet),
                "dry_hours": len(dry),
                "rainy_days": int(wet.date.nunique()),
                "rain_mm": float(wet.rain.sum()),
                "rainy_mean": float(wet.departures.mean()) if len(wet) else None,
                "dry_mean": float(dry.departures.mean()),
                "rainy_total": int(wet.departures.sum()),
                "dry_total": int(dry.departures.sum()),
                "raw_change_pct": ratio(wet.departures.mean(), dry.departures.mean())
                if len(wet)
                else None,
                "adjusted_change_pct": ratio(matched.departures.mean(), matched.dry_mean.mean())
                if len(matched)
                else None,
                "matched_hours": len(matched),
                "matched_dry_mean": float(matched.dry_mean.mean()) if len(matched) else None,
                "matched_rainy_mean": float(matched.departures.mean()) if len(matched) else None,
                "rainy_daytime_pct": float(wet.hour.between(6, 23).mean() * 100)
                if len(wet)
                else None,
                "dry_daytime_pct": float(dry.hour.between(6, 23).mean() * 100),
                "rainy_temperature": float(wet.temperature_2m.mean()) if len(wet) else None,
                "dry_temperature": float(dry.temperature_2m.mean()),
                "leave_one_day_out_min": min(leave_day) if leave_day else None,
                "leave_one_day_out_max": max(leave_day) if leave_day else None,
                "thresholds": thresholds,
                "rainy_dates": records(daily),
            }
        )
    return rows


def run(weather_path=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    signature = {p.name: digest(p) for p in sorted((ROOT / "data/bronze/historical").glob("*.zip"))}
    signature["parser"] = digest(ROOT / "src/ingestion/snapshots.py")
    signature["extractor"] = digest(ROOT / "scripts/build_audit_report.py")
    weather_path = weather_path or ROOT / "data/rebuilt/2022/network-weather-hourly.csv"
    analysis_path = ROOT / "frontend/public/analysis-data/diagnostics.json"
    analysis_inputs = {
        "snapshots": signature,
        "weather": digest(weather_path),
        "analysis": digest(ROOT / "scripts/analyze_project_evidence.py"),
    }
    analysis_meta = CACHE / "diagnostics-manifest.json"
    previous_analysis = json.loads(analysis_meta.read_text()) if analysis_meta.exists() else {}
    if (
        analysis_path.exists()
        and previous_analysis.get("inputs") == analysis_inputs
        and previous_analysis.get("sha256") == digest(analysis_path)
    ):
        print("Evidence diagnostics cache validated", flush=True)
        return
    cache = CACHE / "diagnostic-snapshots.csv.gz"
    meta = CACHE / "diagnostic-snapshots.json"
    previous = json.loads(meta.read_text()) if meta.exists() else {}
    if (
        cache.exists()
        and previous.get("inputs") == signature
        and previous.get("sha256") == digest(cache)
    ):
        raw = pd.read_csv(cache, dtype="string")
        sources, failures = previous["sources"], previous["failures"]
        print("Snapshot cache validated", flush=True)
    else:
        raw, sources, failures = snapshot_frames()
        # Archive fields can mix numbers and strings; normalize before cache serialization.
        for name in raw.columns:
            raw[name] = raw[name].astype("string")
        raw.to_csv(cache, index=False, compression={"method": "gzip", "compresslevel": 1})
        save(
            meta,
            {
                "inputs": signature,
                "sha256": digest(cache),
                "sources": sources,
                "failures": failures,
            },
        )
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "inventory": inventory_diagnostics(raw),
        "weather": weather_diagnostics(pd.read_csv(weather_path)),
        "sources": sources,
        "failures": failures,
        "weather_sha256": digest(weather_path),
        "snapshot_inputs": signature,
    }
    result["analysis_sha256"] = digest(ROOT / "scripts/analyze_project_evidence.py")
    save(ROOT / "frontend/public/analysis-data/diagnostics.json", result)
    save(ROOT / "data/audit/diagnostics.json", result)
    save(analysis_meta, {"inputs": analysis_inputs, "sha256": digest(analysis_path)})
    print(
        json.dumps(
            {
                "inventory": {
                    k: result["inventory"][k] for k in ["samples", "periods", "services", "cross"]
                },
                "summer": [r for r in result["weather"] if r["month"] in [6, 7, 8]],
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    run()
