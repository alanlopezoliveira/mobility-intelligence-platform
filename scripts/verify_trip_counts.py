"""Independent raw CSV recount; does not call the production event normalizer.

Run after rebuilding. Writes aggregate evidence only, never raw trip identifiers.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def verify_saved(expected, sources, totals):
    """Compare independently reconstructed events with data, targets and web exports."""
    saved = pd.read_csv(
        ROOT / "data/rebuilt/2022/station-hourly.csv.gz", parse_dates=["time"]
    ).set_index(["time", "station"])
    delta = expected.subtract(saved, fill_value=0)
    result = {
        "sources": sources,
        "totals": {k: sum(s[k] for s in sources) for k in totals},
        "year_counts": expected.sum().astype(int).to_dict(),
        "station_hours": len(expected),
        "mismatching_station_hours": int(delta.ne(0).any(axis=1).sum()),
        "difference": delta.sum().to_dict(),
        "forecasts": [],
    }
    report = json.loads((ROOT / "data/rebuilt/2022/report.json").read_text(encoding="utf-8"))
    result["pipeline_fingerprint"] = report["fingerprint"]
    result["verification_code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for event in ["departures", "arrivals"]:
        assert (
            result["totals"][event] + result["totals"][f"rejected_{event}"]
            == report["quality"]["raw_rows"]
        )
        assert result["totals"][event] == report["quality"][event]
    assert result["totals"]["duplicate_rows"] == report["quality"]["duplicate_rows"]
    for horizon in [60, 120]:
        predictions = pd.read_csv(
            ROOT / f"models/providers/bicimad/madrid/2022/predictions-{horizon}m.csv.gz", parse_dates=["target", "issue"]
        )
        keys = pd.MultiIndex.from_frame(predictions[["target", "station"]])
        actual = expected.departures.reindex(keys, fill_value=0).to_numpy()
        mismatch = int(np.count_nonzero(actual != predictions.y))
        assert (predictions.target - predictions.issue).eq(pd.Timedelta(minutes=horizon)).all()
        for name, delay in [("recent", horizon // 60 + 1), ("day", 24), ("week", 168)]:
            keys = pd.MultiIndex.from_arrays(
                [predictions.target - pd.Timedelta(hours=delay), predictions.station]
            )
            assert np.array_equal(
                predictions[name], expected.departures.reindex(keys, fill_value=0)
            )
        web_rows, max_error = 0, 0.0
        for sid, station in predictions.groupby("station"):
            web = pd.read_json(
                ROOT / f"frontend/public/project-data/providers/bicimad/madrid/2022/predictions-{horizon}-{hashlib.sha256(str(sid).encode()).hexdigest()}.json",
                convert_dates=False,
            )
            assert list(pd.to_datetime(web.target, utc=True)) == list(station.target)
            assert list(pd.to_datetime(web.issue, utc=True)) == list(station.issue)
            assert np.array_equal(web.y, station.y)
            max_error = max(
                max_error,
                float(np.max(np.abs(web.prediction.to_numpy() - station.prediction.to_numpy()))),
            )
            web_rows += len(web)
        result["forecasts"].append(
            {
                "horizon_minutes": horizon,
                "target_mismatches": mismatch,
                "rows": len(predictions),
                "verified_web_rows": web_rows,
                "maximum_web_rounding_error": max_error,
            }
        )
        assert mismatch == 0 and max_error < 1e-8
    (ROOT / "data/rebuilt/2022/trip-verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in result.items() if k != "sources"}, indent=2))
    assert result["mismatching_station_hours"] == 0, "Raw events and saved aggregates differ"
    return result


def run():
    parts, sources = [], []
    global_rows: set[int] = set()
    global_ids: set[int] = set()
    for path in sorted((ROOT / "data/bronze/historical_members").glob("2022_*csv.zip")):
        totals = {
            key: 0
            for key in [
                "source_rows",
                "blank_rows",
                "duplicate_rows",
                "repeated_trip_ids",
                "departures",
                "arrivals",
                "recovered_arrivals",
                "rejected_departures",
                "rejected_arrivals",
                "reversed_clocks",
            ]
        }
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.endswith(".csv") and "__MACOSX" not in n)
            with archive.open(name) as stream:
                for raw in pd.read_csv(stream, sep=";", dtype=str, chunksize=100000):
                    totals["source_rows"] += len(raw)
                    totals["blank_rows"] += int(raw.isna().all(axis=1).sum())
                    raw = raw.dropna(how="all")
                    hashes = pd.util.hash_pandas_object(raw, index=False)
                    duplicate = hashes.duplicated() | hashes.map(global_rows.__contains__)
                    global_rows.update(hashes.tolist())
                    totals["duplicate_rows"] += int(duplicate.sum())
                    raw = raw.loc[~duplicate]
                    ids = pd.util.hash_pandas_object(
                        raw.get("idTrip", pd.Series(dtype=str)).dropna(), index=False
                    )
                    totals["repeated_trip_ids"] += int(
                        (ids.duplicated() | ids.map(global_ids.__contains__).astype(bool)).sum()
                    )
                    global_ids.update(ids.tolist())
                    times = {}
                    stations = {}
                    valid = {}
                    for event, suffix in [("departures", "unlock"), ("arrivals", "lock")]:
                        clock = pd.to_datetime(
                            raw[f"{suffix}_date"], format="mixed", errors="coerce"
                        )
                        times[event] = clock.dt.tz_localize(
                            "Europe/Madrid", ambiguous="NaT", nonexistent="NaT"
                        ).dt.tz_convert("UTC")
                        stations[event] = pd.to_numeric(raw[f"station_{suffix}"], errors="coerce")
                        valid[event] = (
                            times[event].notna()
                            & stations[event].gt(0)
                            & stations[event].mod(1).eq(0)
                        )
                    reversed_clock = times["arrivals"].lt(times["departures"])
                    valid["arrivals"] &= ~reversed_clock
                    totals["reversed_clocks"] += int(reversed_clock.sum())
                    totals["recovered_arrivals"] += int(
                        (valid["arrivals"] & ~valid["departures"]).sum()
                    )
                    for event in ["departures", "arrivals"]:
                        ok = valid[event]
                        totals[event] += int(ok.sum())
                        totals[f"rejected_{event}"] += int((~ok).sum())
                        timespan = times[event].ge(pd.Timestamp("2022-01-01", tz="UTC")) & times[
                            event
                        ].lt(pd.Timestamp("2023-01-01", tz="UTC"))
                        keep = ok & timespan
                        frame = pd.DataFrame(
                            {
                                "time": times[event][keep].dt.floor("h"),
                                "station": stations[event][keep].astype(int),
                                event: 1,
                            }
                        )
                        parts.append(frame.groupby(["time", "station"])[[event]].sum())
        sources.append(
            {"source": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **totals}
        )
        print(path.name, totals, flush=True)
    expected = pd.concat(parts).fillna(0).groupby(level=[0, 1]).sum().astype(int)
    expected.reset_index().to_csv(ROOT / "data/rebuilt/2022/independent-counts.csv.gz", index=False)
    return verify_saved(expected, sources, totals)


if __name__ == "__main__":
    run()
