# Model approach

The forecasting layer currently includes baseline strategies designed for station-level demand forecasting.

## Baselines

- naive baseline
- seasonal naive baseline

These are used for comparison and for operational fallback logic.

## Production model policy

The API loads the persisted production model artifact rather than retraining during startup. Training artifacts remain local and are not committed to Git.
