# Model-family comparison

Generated from the published run: 2026-09-27T18:09:46.623541+00:00.

Seven trained families and three naive baselines use the same 2022 station panel, chronological partitions and evaluation rows. This is a bounded CPU benchmark, not an exhaustive hyperparameter search or proof of global algorithm superiority.

## Selection policy fixed before execution

Minimize validation MAE. Candidates within 1% relative MAE of the best are practically tied. Prefer baseline, then linear regression, then a single tree, then an ensemble. Within a tier prefer the smaller joblib artifact (including preprocessing, compression level 1), then lower validation MAE, then name. File size is a representation-size proxy, not a universal measure of statistical complexity. The 1% tolerance is a practical choice, not a statistical equivalence test.

The winner is locked before test prediction and scoring. The test period has already been inspected in earlier project iterations, so it is a reused historical holdout, not a fresh prospective validation. No test score or runtime enters selection. No refit on train+validation changes the exported model after selection.

## Comparable inputs and bounded budgets

All candidates receive the same twelve source features and all training rows. Ridge one-hot encodes station/hour/weekday/month and scales the continuous variables using training data only; unknown future categories are ignored. Tree models use numeric versions of the same inputs. Therefore information is shared, but model-appropriate encoding differs. Calendar features refer to target time, and all demand lags are available by issue time. The trailing 24-hour and seven-day departure means and 24-hour departure-plus-arrival total end at issue time; incomplete windows are excluded. Finalized weather is excluded.

The split remains January–August for fitting, September–October for selection and November–December for testing, with purged gaps. Seed 42 and four-thread limits are used where supported. Model configurations below were set before reading new scores. Boosters with external validation stop after 20 non-improving validation-MAE rounds; sklearn histogram boosting uses 180 fixed iterations, with random internal validation disabled.

| Candidate | Configuration |
|---|---|
| Ridge regression | alpha 10, LSQR, training-only one-hot encoding and scaling |
| Decision tree | depth 10, minimum leaf 100 |
| Histogram gradient boosting | Poisson, 180 iterations, 15 leaves, learning rate 0.06 |
| LightGBM | Poisson, up to 220 trees, 31 leaves, learning rate 0.06 |
| XGBoost | Poisson, histogram method, up to 220 trees, depth 5, learning rate 0.06 |
| CatBoost | RMSE, up to 250 trees, depth 6, learning rate 0.06 |
| Random forest | 64 trees, depth 14, minimum leaf 30, bootstrap sample fraction 0.6 |
| Baselines | Latest completed hour; target-aligned previous day; target-aligned previous week |

All predictions are clipped at zero consistently before validation, test scoring, serialization checks and UI export. MAE is the selection objective even when fitting uses squared-error or Poisson loss. CatBoost uses RMSE because a local response-scale check showed Poisson with its built-in MAE did not match the count-scale MAE of predictions. A regression test checks the chosen CatBoost configuration's metric alignment.

## +60 minutes

**Selected: XGBoost Poisson.** Eligible within 1%: LightGBM Poisson, XGBoost Poisson, Random forest. Selected validation penalty relative to the minimum: 0.174%.

Train: 453,429; validation: 117,528; test: 111,220 rows.

| Model | Validation MAE | Test MAE | Test RMSE | Test R² | Fit seconds | Predict ms | Artifact KiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| Random forest | 1.511338 | 1.278687 | 1.948443 | 0.306637 | 35.551 | 238.88 | 9352.55 |
| **XGBoost Poisson** | 1.513968 | 1.284297 | 1.947317 | 0.307438 | 6.191 | 103.70 | 126.23 |
| LightGBM Poisson | 1.516530 | 1.285999 | 1.942810 | 0.310640 | 4.968 | 269.52 | 174.35 |
| Ridge regression | 1.532010 | 1.285330 | 1.976883 | 0.286248 | 1.859 | 147.61 | 3.00 |
| CatBoost RMSE | 1.532673 | 1.302625 | 1.954359 | 0.302420 | 5.014 | 18.06 | 940.16 |
| Decision tree | 1.545894 | 1.313978 | 1.999637 | 0.269723 | 3.502 | 40.88 | 41.38 |
| Histogram gradient boosting | 1.551908 | 1.319070 | 1.952268 | 0.303912 | 9.050 | 573.24 | 136.50 |
| Same hour last week | 1.872635 | 1.636720 | 2.684876 | -0.316539 | 0.000 | 9.56 | 0.04 |
| Same hour yesterday | 1.971079 | 1.617110 | 2.653405 | -0.285856 | 0.000 | 9.10 | 0.03 |
| Last completed hour | 2.189640 | 1.679779 | 2.765077 | -0.396367 | 0.000 | 10.58 | 0.04 |

## +120 minutes

**Selected: XGBoost Poisson.** Eligible within 1%: LightGBM Poisson, XGBoost Poisson, Random forest. Selected validation penalty relative to the minimum: 0.132%.

Train: 453,180; validation: 117,611; test: 111,137 rows.

| Model | Validation MAE | Test MAE | Test RMSE | Test R² | Fit seconds | Predict ms | Artifact KiB |
|---|---:|---:|---:|---:|---:|---:|---:|
| Random forest | 1.521319 | 1.290292 | 1.962934 | 0.296179 | 37.144 | 297.62 | 9327.34 |
| **XGBoost Poisson** | 1.523331 | 1.301286 | 1.958516 | 0.299344 | 5.496 | 107.61 | 119.48 |
| LightGBM Poisson | 1.523970 | 1.300047 | 1.956854 | 0.300533 | 4.755 | 356.07 | 180.20 |
| CatBoost RMSE | 1.539598 | 1.313195 | 1.967162 | 0.293144 | 4.634 | 32.56 | 943.60 |
| Ridge regression | 1.541766 | 1.293387 | 1.993465 | 0.274115 | 1.317 | 136.48 | 3.00 |
| Histogram gradient boosting | 1.552647 | 1.329211 | 1.965506 | 0.294334 | 7.828 | 679.35 | 136.62 |
| Decision tree | 1.556087 | 1.319975 | 2.012345 | 0.260300 | 2.772 | 30.47 | 40.54 |
| Same hour last week | 1.872410 | 1.636746 | 2.684637 | -0.316503 | 0.000 | 9.16 | 0.04 |
| Same hour yesterday | 1.970734 | 1.617400 | 2.653545 | -0.286185 | 0.000 | 12.94 | 0.03 |
| Last completed hour | 2.361012 | 1.772038 | 2.894566 | -0.530445 | 0.000 | 21.29 | 0.04 |

## Artifacts and reproducibility

Selected models: `models/rebuilt/departures-{60,120}m.joblib`. Use the matching contract JSON to identify the selected family and feature order. Older `.txt` boosters remain historical artifacts and are no longer the selected models. Only load trusted local joblib files.

Every candidate has its model, validation predictions, configuration, dependency versions, training duration, validation metrics and SHA-256 checks under `models/rebuilt/comparison/{60,120}m/`. `selection.json` is written before test evaluation. `all-test-predictions.csv.gz` contains all ten candidates on identical labelled rows. The app's station charts use the selected model's predictions, and its table exposes validation/test accuracy, fit time, prediction time, artifact size and the selection decision.

Fit time measures the original fit, excluding serialization. Inference is the median of three full-test runs after warm-up. Timing is hardware/load-dependent and not a tie-break input. Every candidate was saved, reloaded and checked against 1,000 validation predictions at absolute tolerance 1e-12. Candidate caches check feature/label hashes, code, settings, dependencies and artifact hashes; a changed UI does not require refitting. `--force` explicitly refits candidates.

Install benchmark dependencies with `python -m pip install -c constraints-rebuilt.txt -e ".[dev,research]"`. Run `python scripts/rebuild_project.py`; the unchanged pipeline reuses validated evidence. This document is regenerated from the published report, including on a cache hit.

References: [XGBoost Python API](https://xgboost.readthedocs.io/en/stable/python/python_api.html), [CatBoost training](https://catboost.ai/docs/en/concepts/python-reference_catboostregressor_fit), [sklearn histogram boosting](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).
