# Mobility Intelligence Platform

Mobility Intelligence Platform is an independent project for shared-mobility intelligence. BiciMAD is the initial real-world provider used to validate the platform, not the product identity.

This repository intentionally does not include the real BiciMAD dataset. A fresh clone only contains source code, configuration, tests, documentation, notebooks, Docker files, and CI files. The user must explicitly run the data preparation workflow to download and prepare provider data locally.

## Clean-clone workflow

```bash
git clone <repo-url>
cd mobility-intelligence-platform
cp .env.example .env
docker compose up -d
docker compose exec backend make prepare-data
docker compose exec backend make train
docker compose exec backend make evaluate
```

## Important data rule

The repository does not contain the official BiciMAD historical data, raw exports, generated Bronze/Silver/Gold data, database dumps, trained model artifacts, or any real production fixtures. Those files are generated locally after the explicit preparation step and are git-ignored.

The station-master preparation workflow persists normalized stations in PostgreSQL/PostGIS through Alembic-managed tables. The current station-master resource is a snapshot without timestamped demand observations, so forecasting training and evaluation deliberately stop with `not_evaluable`. The previous same-snapshot `MAE=0.0111` and `R²=0.9998` result is invalid because the target was constructed directly from the feature snapshot. A validated forecast model requires timestamped observations and chronological train/validation/test periods.

## Official BiciMAD sources used by the project

Historical trip/station dataset (2017-2023):
- 2017: https://media.emtmadrid.es/-HoGTStPZeC
- 2018: https://media.emtmadrid.es/-9iZNUjrjri
- 2019: https://media.emtmadrid.es/-WNgfSj2ZvC
- 2020: https://media.emtmadrid.es/-rkuBymuFJX
- 2021: https://media.emtmadrid.es/-BRk4rTaAdV
- 2022: https://media.emtmadrid.es/-uHaW6iZkhG
- 2023: https://media.emtmadrid.es/-9Nii5DXo4x

Official catalog references:
- https://datos.gob.es/es/catalogo/l01280796-historicos-de-bicimad-2017-20231
- https://datos.madrid.es/dataset/900034-0-bicimad-viajes-estaciones

BiciMAD station master resource (explicit direct CSV URL configured in config/providers/bicimad.yaml):
- https://datos.madrid.es/dataset/208327-0-transporte-bicicletas-bicimad/resource/208327-2-transporte-bicicletas-bicimad/download/208327-2-transporte-bicicletas-bicimad.csv

This project documents the sources and uses only the explicit direct URLs configured in the provider configuration. Runtime discovery is not used.

## Architecture

The project uses a modular monolith with clear domain boundaries: core, provider adapters, ingestion, transformations, data quality, ML, API, CLI, SDK, frontend, and infrastructure.

## Commands

```bash
make prepare-data
make train
make evaluate
make test
make clean-data
```

## Licensing and attribution

The code is MIT licensed. The BiciMAD datasets are third-party data and are subject to their own terms and attribution requirements. See docs/legal-and-attribution.md.

The project includes a legal notice:

> Mobility Intelligence Platform is an independent project. BiciMAD, EMT Madrid and Madrid City Council are not affiliated with or endorsing this project.
