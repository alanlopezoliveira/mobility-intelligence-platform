# Data architecture

The project uses a three-layer medallion design:

- Bronze: raw, unmodified source files and metadata
- Silver: cleaned, typed, deduplicated, normalized data
- Gold: analytics-ready datasets for forecasting, dashboards, and optimization

All generated data is local-only and ignored by Git.
