# Architecture

This project follows a modular monolith design. The domain and core logic are separated from provider-specific adapters, ingestion, transformation, data-quality, ML, optimization, API, CLI, SDK, and frontend interfaces.

## Module layout

- core: reusable domain contracts and base abstractions
- providers: provider-specific adapters, such as BiciMAD
- ingestion: download and validation
- transformations: Bronze/Silver/Gold processing
- data_quality: quality checks and validation reporting
- ml: forecasting and model artifacts
- optimization: redistribution recommendations
- api: FastAPI backend
- cli: operational commands
- sdk: Python client for the API
- frontend: React + TypeScript dashboard

## SaaS-ready boundaries

The platform separates provider concerns from shared operational workflows. This keeps provider-specific assumptions localized and maintains a clean core contract for future providers.
