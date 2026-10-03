"""Regenerate the model-comparison document from the published evidence JSON."""

import json
from pathlib import Path


def write_comparison(report, destination: Path):
    lines = [
        "# Model-family comparison",
        "",
        f"Generated from the published run: {report['generated_at']}.",
        "",
        f"Seven trained families and three naive baselines use the same {report['year']} station panel, chronological partitions and evaluation rows. This is a bounded CPU benchmark, not an exhaustive hyperparameter search or proof of global algorithm superiority.",
        "",
        "## Selection policy fixed before execution",
        "",
        "Minimize validation MAE. Candidates within 1% relative MAE of the best are practically tied. Prefer baseline, then linear regression, then a single tree, then an ensemble. Within a tier prefer the smaller joblib artifact (including preprocessing, compression level 1), then lower validation MAE, then name. File size is a representation-size proxy, not a universal measure of statistical complexity. The 1% tolerance is a practical choice, not a statistical equivalence test.",
        "",
        "The winner is locked before test prediction and scoring. The test period has already been inspected in earlier project iterations, so it is a reused historical holdout, not a fresh prospective validation. No test score or runtime enters selection. No refit on train+validation changes the exported model after selection.",
        "",
        "## Comparable inputs and bounded budgets",
        "",
        "All candidates receive the same twelve source features and all training rows. Ridge one-hot encodes station/hour/weekday/month and scales the continuous variables using training data only; unknown future categories are ignored. Tree models use numeric versions of the same inputs. Therefore information is shared, but model-appropriate encoding differs. Calendar features refer to target time, and all demand lags are available by issue time. The trailing 24-hour and seven-day departure means and 24-hour departure-plus-arrival total end at issue time; incomplete windows are excluded. Finalized weather is excluded.",
        "",
        "The split remains January–August for fitting, September–October for selection and November–December for testing, with purged gaps. Seed 42 and four-thread limits are used where supported. Model configurations below were set before reading new scores. Boosters with external validation stop after 20 non-improving validation-MAE rounds; sklearn histogram boosting uses 180 fixed iterations, with random internal validation disabled.",
        "",
        "| Candidate | Configuration |",
        "|---|---|",
        "| Ridge regression | alpha 10, LSQR, training-only one-hot encoding and scaling |",
        "| Decision tree | depth 10, minimum leaf 100 |",
        "| Histogram gradient boosting | Poisson, 180 iterations, 15 leaves, learning rate 0.06 |",
        "| LightGBM | Poisson, up to 220 trees, 31 leaves, learning rate 0.06 |",
        "| XGBoost | Poisson, histogram method, up to 220 trees, depth 5, learning rate 0.06 |",
        "| CatBoost | RMSE, up to 250 trees, depth 6, learning rate 0.06 |",
        "| Random forest | 64 trees, depth 14, minimum leaf 30, bootstrap sample fraction 0.6 |",
        "| Baselines | Latest completed hour; target-aligned previous day; target-aligned previous week |",
        "",
        "All predictions are clipped at zero consistently before validation, test scoring, serialization checks and UI export. MAE is the selection objective even when fitting uses squared-error or Poisson loss. CatBoost uses RMSE because a local response-scale check showed Poisson with its built-in MAE did not match the count-scale MAE of predictions. A regression test checks the chosen CatBoost configuration's metric alignment.",
    ]
    for model in report["models"]:
        decision = model["selection"]
        lines += [
            "",
            f"## +{model['horizon_minutes']} minutes",
            "",
            f"**Selected: {model['selected_model']}.** Eligible within 1%: {', '.join(decision['eligible_models'])}. Selected validation penalty relative to the minimum: {decision['selected_validation_penalty_pct']:.3f}%.",
            "",
            f"Train: {model['train_rows']:,}; validation: {model['validation_rows']:,}; test: {model['test_rows']:,} rows.",
            "",
            "| Model | Validation MAE | Test MAE | Test RMSE | Test R² | Fit seconds | Predict ms | Artifact KiB |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in sorted(model["metrics"], key=lambda x: x["validation_mae"]):
            name = f"**{row['model']}**" if row["selected"] else row["model"]
            lines.append(
                f"| {name} | {row['validation_mae']:.6f} | {row['mae']:.6f} | {row['rmse']:.6f} | {row['r2']:.6f} | {row['fit_seconds']:.3f} | {row['predict_seconds'] * 1000:.2f} | {row['model_bytes'] / 1024:.2f} |"
            )
    lines += [
        "",
        "## Artifacts and reproducibility",
        "",
        "Selected models: `models/providers/<provider>/<network>/<year>/departures-{60,120}m.joblib`. Use the matching contract JSON to identify the selected family and feature order. Older `.txt` boosters remain historical artifacts and are no longer the selected models. Only load trusted local joblib files.",
        "",
        "Every candidate has its model, validation predictions, configuration, dependency versions, training duration, validation metrics and SHA-256 checks under `models/providers/<provider>/<network>/<year>/comparison/{60,120}m/`. `selection.json` is written before test evaluation. `all-test-predictions.csv.gz` contains all ten candidates on identical labelled rows. The app's station charts use the selected model's predictions, and its table exposes validation/test accuracy, fit time, prediction time, artifact size and the selection decision.",
        "",
        "Fit time measures the original fit, excluding serialization. Inference is the median of three full-test runs after warm-up. Timing is hardware/load-dependent and not a tie-break input. Every candidate was saved, reloaded and checked against 1,000 validation predictions at absolute tolerance 1e-12. Candidate caches check feature/label hashes, code, settings, dependencies and artifact hashes; a changed UI does not require refitting. `--force` explicitly refits candidates.",
        "",
        'Install benchmark dependencies with `python -m pip install -c constraints-rebuilt.txt -e ".[dev,research]"`. Run `python scripts/rebuild_project.py`; the unchanged pipeline reuses validated evidence. A provider-specific comparison is generated alongside its models. Refresh this reference document explicitly with scripts/document_model_comparison.py.',
        "",
        "References: [XGBoost Python API](https://xgboost.readthedocs.io/en/stable/python/python_api.html), [CatBoost training](https://catboost.ai/docs/en/concepts/python-reference_catboostregressor_fit), [sklearn histogram boosting](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).",
        "",
    ]
    destination.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    write_comparison(
        json.loads((root / "data/rebuilt/2022/report.json").read_text(encoding="utf-8")),
        root / "docs/model-comparison.md",
    )
