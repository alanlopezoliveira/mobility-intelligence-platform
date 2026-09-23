# ML validation status

The station-master CSV contains station attributes and a current availability snapshot. It does not contain a timestamped demand target. The former workflow calculated `station_score` from `capacity` and `available_bikes`, then trained and evaluated on those same rows. That is target leakage, not forecasting, so the reported `MAE=0.0111` and `R²=0.9998` are invalid and are not retained.

The repository now provides chronological splitting and group-aware lag feature utilities in `src/ml/evaluation.py`. They reject invalid timestamps, duplicate aggregate timestamps, and features that cannot be generated from prior observations. The CLI records `not_evaluable` metadata until timestamped demand observations are ingested. No 30-, 60-, or 120-minute forecast is claimed from the current snapshot.

When temporal observations are available, the required evaluation contract is:

1. Sort by observation time and split into train, validation, and test periods chronologically.
2. Fit preprocessing and models on the training period only.
3. Generate station lags and rolling features within station groups using prior rows only.
4. Evaluate naive, seasonal-naive when the sampling frequency supports it, and ML predictions on the exact same test period.
5. Persist model version, feature definition, train/validation/test ranges, and MAE/RMSE/R2 metrics.