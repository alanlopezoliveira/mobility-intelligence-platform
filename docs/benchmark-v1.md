# benchmark_v1 closure note

## Purpose

`benchmark_v1` was the first real-data ML benchmark in this repository comparing the deterministic baselines and the LightGBM model for the +60 minute and +120 minute forecasting horizons.

Compared methods:
- naive 1h
- seasonal naive 24h
- seasonal naive 168h
- LightGBM

Forecasting horizons:
- +60 minutes
- +120 minutes

## Data integrity

The benchmark used the canonical forecasting data and the frozen forecasting contract.

Canonical data hash:
- `0a747a132ab6401621df226d96e155b0dbf435d5992bb78e35e3c9461f24e288`

Dataset contract hash:
- `639f355e7bdf9cf263ce84313cd32cfe415d9602c8b1cc56e102ec68ea1b9d8d`

Dataset artifact hashes:
- 60m dataset hash: `6448c6a2481715e14c967d55404f3996eb835d8e76e9d7c179f0e13b1572c4a3`
- 120m dataset hash: `2ffb309ed06d10a0320a1260f55ab324f90ee8813fc8bd0b784bdc3d59bd8148`

## Reproducibility

The experiment is scientifically reproducible:
- scientific reproducibility passed;
- prediction reproducibility passed;
- configuration reproducibility passed;
- runtime timings are intentionally not expected to be deterministic across runs.

The stored predictions and hashes remain the scientific evidence for the experiment. Runtime timestamps and wall-clock timings were intentionally excluded from scientific equality checks.

## Metrics

The stored benchmark artifacts include the historical natural-coverage results for the discriminated evaluation populations.

> Natural-coverage metrics. These metrics must not be used for direct cross-model ranking because evaluation populations differ.

### +60 minutes

LightGBM metrics:
- MAE: 1.7430673443857811
- RMSE: 2.4305389405073106
- R²: 0.31295562944156496
- coverage: 1.0
- n_predictions: 1175987

Baseline natural coverage:
- naive_1h: 0.8760062823823733
- seasonal_naive_24h: 0.8722162744996331
- seasonal_naive_168h: 0.8690563756231999

### +120 minutes

LightGBM metrics:
- MAE: 1.7689031349277309
- RMSE: 2.464906032282609
- R²: 0.2816617126075679
- coverage: 1.0
- n_predictions: 1147216

Baseline natural coverage:
- naive_1h: 0.8697446688330707
- seasonal_naive_24h: 0.8713285030892177
- seasonal_naive_168h: 0.8662091532893544

## Comparability limitation

The scientific limitation is structural, not algorithmic:

- LightGBM can predict rows with missing lag features because the model natively handles missing values in the feature matrix;
- deterministic baselines require their corresponding lag value to exist;
- therefore the baseline evaluation excludes additional rows;
- as a result, the stored metrics are not calculated on the same valid-row population.

This is not a LightGBM bug and it does not imply the baseline implementation is incorrect. It is a consequence of different valid-row policies across model classes.

## Scientific interpretation

`benchmark_v1` successfully established a reproducible experimental pipeline and deterministic baseline definitions. However, it does not provide a fair direct comparison between LightGBM and the deterministic baselines because their valid prediction populations differ. The experiment is therefore closed as reproducible but non-comparable.

## What remains valid

These remain valid and intact:
- forecasting contract
- target definition
- +60/+120 horizons
- leakage audit
- chronological split
- baseline definitions
- stored predictions
- prediction hashes
- configuration hashes
- reproducibility evidence

## What is not established

`benchmark_v1` does not establish:
- LightGBM superiority
- a model ranking
- a production model
- production readiness
- optimal hyperparameters
- generalization beyond this evaluation design

## Closure status

`benchmark_v1` is reproducible, but it does not support a fair direct ranking of LightGBM against the deterministic baselines because the models are evaluated on different valid-row populations.

The existing audit remains the authoritative document for this conclusion:
- `models/experiments/benchmark_v1/comparability_audit.json`
