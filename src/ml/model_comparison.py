"""Bounded model-family benchmark, selected by validation MAE and simplicity.

The test period is never passed to fitting or selection. It has been inspected
in previous project iterations, so it is a reused historical holdout, not a new
prospective validation. Pickle/joblib artifacts are trusted LOCAL outputs only.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import time
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeRegressor
from threadpoolctl import threadpool_limits
from xgboost import XGBRegressor

from src.ml.rebuilt import feature_table, scores

FEATURES = [
    "station",
    "recent",
    "previous",
    "day",
    "week",
    "hour",
    "weekday",
    "month",
    "day_of_year",
    "mean_24h",
    "mean_7d",
    "movements_24h",
]
TOLERANCE = 0.01
POLICY = "Validation MAE within 1% of best; prefer baseline, linear, single tree, then ensemble. Within tier prefer smaller compressed model, then lower validation MAE, then name. Test scores do not select the winner."


def select_model(candidates, tolerance=TOLERANCE):
    """Pure, deterministic selection: no test metrics or measured runtimes consulted."""
    if not candidates or tolerance < 0:
        raise ValueError("Candidates and a nonnegative tolerance are required")
    if any(not np.isfinite(c["validation_mae"]) or c["validation_mae"] < 0 for c in candidates):
        raise ValueError("Invalid validation MAE")
    best = min(c["validation_mae"] for c in candidates)
    eligible = [c for c in candidates if c["validation_mae"] <= best * (1 + tolerance) + 1e-12]
    winner = min(
        eligible,
        key=lambda c: (c["complexity_tier"], c["model_bytes"], c["validation_mae"], c["model"]),
    )
    return winner, {
        "policy": POLICY,
        "relative_tolerance": tolerance,
        "best_validation_mae": best,
        "maximum_eligible_mae": best * (1 + tolerance),
        "eligible_models": [c["model"] for c in eligible],
        "selected_model": winner["model"],
        "selected_validation_penalty_pct": (winner["validation_mae"] / best - 1) * 100
        if best
        else 0.0,
    }


def candidate_specs():
    """Predeclared modest budgets, full shared training rows, four CPU threads."""
    return [
        ("Last completed hour", "baseline_recent", 0, {"feature": "recent"}),
        ("Same hour yesterday", "baseline_day", 0, {"feature": "day"}),
        ("Same hour last week", "baseline_week", 0, {"feature": "week"}),
        ("Ridge regression", "ridge", 1, {"alpha": 10.0, "solver": "lsqr", "tol": 1e-5}),
        (
            "Decision tree",
            "tree",
            2,
            {"max_depth": 10, "min_samples_leaf": 100, "random_state": 42},
        ),
        (
            "Histogram gradient boosting",
            "hist",
            3,
            {
                "loss": "poisson",
                "max_iter": 180,
                "max_leaf_nodes": 15,
                "learning_rate": 0.06,
                "min_samples_leaf": 60,
                "early_stopping": False,
                "random_state": 42,
            },
        ),
        (
            "LightGBM Poisson",
            "lightgbm",
            3,
            {
                "objective": "poisson",
                "n_estimators": 220,
                "learning_rate": 0.06,
                "num_leaves": 31,
                "max_bin": 127,
                "min_child_samples": 60,
                "n_jobs": 4,
                "random_state": 42,
                "deterministic": True,
                "force_col_wise": True,
                "verbosity": -1,
            },
        ),
        (
            "XGBoost Poisson",
            "xgboost",
            3,
            {
                "objective": "count:poisson",
                "n_estimators": 220,
                "max_depth": 5,
                "learning_rate": 0.06,
                "tree_method": "hist",
                "max_bin": 128,
                "min_child_weight": 20,
                "n_jobs": 4,
                "random_state": 42,
                "eval_metric": "mae",
                "early_stopping_rounds": 20,
            },
        ),
        (
            "CatBoost RMSE",
            "catboost",
            3,
            {
                "loss_function": "RMSE",
                "eval_metric": "MAE",
                "iterations": 250,
                "depth": 6,
                "learning_rate": 0.06,
                "thread_count": 4,
                "random_seed": 42,
                "allow_writing_files": False,
                "verbose": False,
            },
        ),
        (
            "Random forest",
            "forest",
            3,
            {
                "n_estimators": 64,
                "max_depth": 14,
                "min_samples_leaf": 30,
                "max_samples": 0.6,
                "n_jobs": 4,
                "random_state": 42,
            },
        ),
    ]


def fit_candidate(kind, params, train, valid):
    if kind.startswith("baseline_"):
        return params
    constructors = {
        "tree": DecisionTreeRegressor,
        "hist": HistGradientBoostingRegressor,
        "lightgbm": lgb.LGBMRegressor,
        "xgboost": XGBRegressor,
        "catboost": CatBoostRegressor,
        "forest": RandomForestRegressor,
    }
    if kind == "ridge":
        categorical = ["station", "hour", "weekday", "month"]
        encoder = ColumnTransformer(
            [
                ("categories", OneHotEncoder(handle_unknown="ignore"), categorical),
                ("continuous", StandardScaler(), [c for c in FEATURES if c not in categorical]),
            ]
        )
        model = make_pipeline(encoder, Ridge(**params))
    else:
        model = constructors[kind](**params)
    with threadpool_limits(limits=4):
        if kind == "lightgbm":
            model.fit(
                train[FEATURES],
                train.y,
                eval_set=[(valid[FEATURES], valid.y)],
                eval_metric="mae",
                callbacks=[lgb.early_stopping(20, first_metric_only=True, verbose=False)],
            )
        elif kind == "xgboost":
            model.fit(
                train[FEATURES], train.y, eval_set=[(valid[FEATURES], valid.y)], verbose=False
            )
        elif kind == "catboost":
            model.fit(
                train[FEATURES],
                train.y,
                eval_set=(valid[FEATURES], valid.y),
                early_stopping_rounds=20,
            )
        else:
            model.fit(train[FEATURES], train.y)
    return model


def predict_candidate(model, frame):
    """Same nonnegative postprocessing for validation, test, reload and serving."""
    with threadpool_limits(limits=4):
        raw = (
            frame[model["feature"]].to_numpy()
            if isinstance(model, dict)
            else model.predict(frame[FEATURES])
        )
    prediction = np.asarray(raw, dtype=float)
    if prediction.shape != (len(frame),) or not np.isfinite(prediction).all():
        raise ValueError("Invalid model predictions")
    return np.maximum(prediction, 0.0)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def benchmark_models(panel: pd.DataFrame, output: Path, year: int, force=False,
                     timezone: str = 'Europe/Madrid'):
    """Fit one provider/year on chronological partitions using its local calendar."""
    output.mkdir(parents=True, exist_ok=True)
    results, samples = [], []
    versions = {
        name: importlib.metadata.version(name)
        for name in [
            "scikit-learn",
            "lightgbm",
            "xgboost",
            "catboost",
            "numpy",
            "pandas",
            "joblib",
            "threadpoolctl",
        ]
    }
    implementation = sha(Path(__file__)) + sha(Path(__file__).with_name("rebuilt.py"))
    train_end, test_start = (
        pd.Timestamp(f"{year}-09-01", tz="UTC"),
        pd.Timestamp(f"{year}-11-01", tz="UTC"),
    )
    for horizon in (1, 2):
        data = feature_table(panel, horizon, timezone=timezone)
        train = data[data.target + pd.Timedelta(hours=horizon + 1) < train_end]
        valid = data[
            (data.target >= train_end)
            & (data.target + pd.Timedelta(hours=horizon + 1) < test_start)
        ]
        test = data[data.target >= test_start]
        if min(len(train), len(valid), len(test)) < 100:
            raise ValueError("Insufficient chronological partition coverage")
        frame_hash = hashlib.sha256(
            pd.util.hash_pandas_object(data, index=False).to_numpy().tobytes()
        ).hexdigest()
        cache = output / "comparison" / f"{horizon * 60}m"
        cache.mkdir(parents=True, exist_ok=True)
        candidates = []
        paths = {}
        for name, kind, tier, params in candidate_specs():
            fingerprint = hashlib.sha256(
                json.dumps(
                    [frame_hash, params, implementation, versions, kind], sort_keys=True
                ).encode()
            ).hexdigest()
            model_path, prediction_path, metadata_path = (
                cache / f"{kind}.joblib",
                cache / f"{kind}-validation.npz",
                cache / f"{kind}.json",
            )
            cached = (
                json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata_path.exists()
                else {}
            )
            hit = (
                not force
                and cached.get("fingerprint") == fingerprint
                and model_path.exists()
                and prediction_path.exists()
                and cached.get("model_sha256") == sha(model_path)
                and cached.get("prediction_sha256") == sha(prediction_path)
            )
            if hit:
                metadata = cached
                with np.load(prediction_path) as arrays:
                    validation_prediction = arrays["prediction"]
            else:
                start = time.perf_counter()
                estimator = fit_candidate(kind, params, train, valid)
                elapsed = time.perf_counter() - start
                validation_prediction = predict_candidate(estimator, valid)
                joblib.dump(estimator, model_path, compress=1)
                restored = joblib.load(model_path)
                reload_values = predict_candidate(restored, valid.iloc[:1000])
                error = float(np.max(np.abs(validation_prediction[:1000] - reload_values)))
                if error > 1e-12:
                    raise AssertionError(f"{name}: model reload changed predictions")
                np.savez_compressed(prediction_path, prediction=validation_prediction)
                metadata = {
                    "model": name,
                    "kind": kind,
                    "complexity_tier": tier,
                    "params": params,
                    "fingerprint": fingerprint,
                    "model_bytes": model_path.stat().st_size,
                    "model_sha256": sha(model_path),
                    "prediction_sha256": sha(prediction_path),
                    "validation_mae": scores(valid.y, validation_prediction)["mae"],
                    "validation_metrics": scores(valid.y, validation_prediction),
                    "fit_seconds": elapsed,
                    "reload_max_error": error,
                    "versions": versions,
                }
                metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                del estimator, restored
            candidates.append({**metadata, "cache_hit": hit})
            paths[name] = model_path
            print(
                f"+{horizon * 60}m {name}: validation MAE={metadata['validation_mae']:.4f}, fit={metadata['fit_seconds']:.1f}s {'(cache)' if hit else ''}",
                flush=True,
            )
        # Selection is locked BEFORE any candidate is scored on the test period.
        winner, selection = select_model(candidates)
        selected_path = output / f"departures-{horizon * 60}m.joblib"
        shutil.copyfile(paths[winner["model"]], selected_path)
        selection["selected_model_sha256"] = sha(selected_path)
        (cache / "selection.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")
        metric_rows, selected_prediction = [], None
        export_columns = [
            "station",
            "issue",
            "target",
            "y",
            "recent",
            "day",
            "week",
            "mean_24h",
            "mean_7d",
            "movements_24h",
        ]
        test_export = test[export_columns].copy()
        for candidate in candidates:
            estimator = joblib.load(paths[candidate["model"]])
            # Warm up separately, then report median time over 3 identical test predictions.
            predict_candidate(estimator, test.iloc[:1000])
            runs = []
            for _ in range(3):
                start = time.perf_counter()
                prediction = predict_candidate(estimator, test)
                runs.append(time.perf_counter() - start)
            metric_rows.append(
                {
                    **candidate,
                    **scores(test.y, prediction),
                    "predict_seconds": float(np.median(runs)),
                    "selected": candidate["model"] == winner["model"],
                    "within_tolerance": candidate["model"] in selection["eligible_models"],
                }
            )
            test_export[candidate["kind"]] = prediction
            if candidate["model"] == winner["model"]:
                selected_prediction = prediction
            del estimator
        assert selected_prediction is not None
        metric_rows.sort(key=lambda m: (not m["selected"], m["validation_mae"]))
        test_export.to_csv(
            cache / "all-test-predictions.csv.gz",
            index=False,
            compression={"method": "gzip", "compresslevel": 1},
        )
        export = test[export_columns].copy()
        export["prediction"] = selected_prediction
        export.to_csv(
            output / f"predictions-{horizon * 60}m.csv.gz",
            index=False,
            compression={"method": "gzip", "compresslevel": 1},
        )
        samples.append((horizon * 60, export))
        result = {
            "timezone": timezone,
            "horizon_minutes": horizon * 60,
            "selected_model": winner["model"],
            "selection": selection,
            "metrics": metric_rows,
            "train_rows": len(train),
            "validation_rows": len(valid),
            "test_rows": len(test),
            "train_start": str(train.target.min()),
            "train_end": str(train.target.max()),
            "validation_start": str(valid.target.min()),
            "validation_end": str(valid.target.max()),
            "test_start": str(test.target.min()),
            "test_end": str(test.target.max()),
            "reload_max_error": winner["reload_max_error"],
            "features": FEATURES,
            "importance": {},
            "model_file": selected_path.name,
            "seed": 42,
            "common_population_fraction": len(test) / max(1, int((panel.time >= test_start).sum())),
        }
        (output / f"contract-{horizon * 60}m.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        results.append(result)
        print(
            f"SELECTED +{horizon * 60}m: {winner['model']}; test MAE={metric_rows[0]['mae']:.4f}",
            flush=True,
        )
    return results, samples
