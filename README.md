# Mobility Intelligence Platform

**MobilityLab** analyzes historical bike-sharing trips, with BiciMAD Madrid as its reference provider and configurable CSV adapters for other providers. It combines historical trips, weather analysis and station-level departure forecasts in an interactive web application.

The main analysis covers **2022**. Separate inventory views explore available station observations from **2018–2020**. All results are historical research: the application does not provide live bike availability or automated redistribution decisions.

## What you can explore

| Feature | What it shows |
|---|---|
| Network overview | Recorded trips, station coverage and activity over time |
| Weather and usage | Rainy versus dry hours, with calendar-matched comparisons |
| Forecast explorer | Predicted and observed departures by station, at +60 and +120 minutes |
| Model comparison | Seven trained model families and three simple reference methods |
| Station inventory | Historical occupancy and observed empty/full events |
| Data and methods | Sources, quality checks, assumptions and limitations |

Forecasts are evaluated on a chronological split. Model selection uses validation error and a preference for simpler candidates with similar accuracy. The [model comparison](docs/model-comparison.md) contains the published results and selection details.

## Technology

- **Data and modelling:** Python, pandas, scikit-learn, LightGBM, XGBoost and CatBoost.
- **Interface:** React, TypeScript and Vite, reading generated JSON exports.
- **Optional database explorer:** FastAPI, PostgreSQL/PostGIS, SQLAlchemy and Docker Compose.

The local web application can run from static exports. The complete Docker workflow additionally publishes observations to PostgreSQL and serves the API; archived BiciMAD benchmarks remain a separate reference workflow.

## Run the complete workflow in Docker

Copy `.env.example` to `.env`, set a database password and matching `DATABASE_URL`, then run:

```powershell
docker compose up --build -d
docker compose logs -f pipeline
```

Open [MobilityLab](http://localhost:3000). The batch container downloads the configured trip archive, prepares weather and models, publishes provider-specific exports and database observations, then allows the frontend to start. First execution needs internet and sufficient time for ingestion/training. Four named volumes retain data, models, exports and PostgreSQL. See the [developer guide](docs/developer-guide.md) for refresh commands, ports, optional inventory, storage and external hosting.

Other providers can supply mapped CSV/ZIP data through [provider configuration](docs/providers.md). The web selector switches between published provider/network/year datasets. Support is tested with two synthetic providers; a real source still requires validation of its schema and semantics.

## Run locally

### 1. Prepare the environment

Use Python 3.12 and Node.js 22, matching the project's CI versions. From the repository root, on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -c constraints-rebuilt.txt -e ".[dev,research]"
```

On Linux/macOS, activate the environment with `source .venv/bin/activate`. Installation and the first weather retrieval need internet access.

### 2. Prepare the source archives

Create `data/bronze/historical/` and save the official distributions with these filenames:

| Archive | Local filename | Purpose |
|---|---|---|
| [BiciMAD 2022](https://media.emtmadrid.es/-uHaW6iZkhG) | `2022.zip` | Reference trip dataset |
| [BiciMAD 2018](https://media.emtmadrid.es/-9iZNUjrjri) | `2018.zip` | Historical station snapshots |
| [BiciMAD 2019](https://media.emtmadrid.es/-WNgfSj2ZvC) | `2019.zip` | Historical station snapshots |
| [BiciMAD 2020](https://media.emtmadrid.es/-rkuBymuFJX) | `2020.zip` | Historical station snapshots |

The rebuild extracts monthly trip CSVs automatically. Only the 2022 archive is required for the default trip workflow; `--download` retrieves it automatically. Historical inventory is optional through `--inventory` and needs the 2018–2020 archives plus a RAR-capable extractor.

### 3. Generate results and start the frontend

With the Python environment active:

```powershell
python scripts/rebuild_project.py
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5177 --strictPort
```

Open [MobilityLab](http://127.0.0.1:5177). Later rebuilds reuse verified caches and unchanged model artifacts.

Prepare inventory pages with `python scripts/rebuild_project.py --download --inventory`. This exports snapshots independently of the older Gold/benchmark files. See the [developer guide](docs/developer-guide.md#optional-bicimad-inventory).

To build and preview the main frontend, run these commands from `frontend/` after preparing the exports:

```powershell
npm run build
npm run preview -- --host 127.0.0.1 --port 4173
```

## Project structure

```text
config/       Source configuration and historical assessment inputs
src/          Data ingestion, modelling, API and database modules
scripts/      Data preparation, evaluation and export commands
frontend/     Web application
alembic/      Database migrations for the optional explorer
tests/        Automated checks
docs/         Maintained guides, course submissions and illustrations
data/         Local source files and generated datasets (ignored by Git)
models/       Local trained models and evaluation artifacts (ignored by Git)
```

The BiciMAD prepared tables live in `data/rebuilt/2022/`; other networks use their provider workspace. Models live in `models/providers/<provider>/<network>/<year>/` and web exports in `frontend/public/project-data/providers/<provider>/<network>/<year>/`. Downloaded archives and generated results are not included in a fresh checkout.

## Validation

From the repository root, with the Python environment active:

```powershell
python -m pytest -q
ruff check src tests
mypy src
```

From `frontend/`, run `npm run lint` and `npm run build`. An independent trip recount is available through `python scripts/verify_trip_counts.py` after data preparation. To refresh the model comparison from existing results without retraining, run `python scripts/document_model_comparison.py`.

## Reading the results

Predictions estimate **recorded departures per station-hour**, not available bicycles or unmet demand. Zero-filled station-hours mean no recorded movement during an observed network hour; hours without network coverage remain unknown. A seven-day chart combines separate hourly forecasts, not a single seven-day forecast.

Weather comparisons describe associations, not causal effects. Inventory observations cover a different period from the forecasts. The historical test period has been examined in previous iterations, so the reported scores are not a fresh prospective validation.

## Documentation and credits

Start with the [documentation index](docs/README.md), [data and methods](docs/rebuilt-project.md), or [course submissions](docs/entregas/README.md).

**Author:** Alan López Oliveira.

The source code uses the [MIT license](LICENSE). Data credits belong to EMT Madrid / Madrid City Council and Open-Meteo / Copernicus ERA5; their usage conditions are covered in [licensing and attribution](docs/legal-and-attribution.md). This project is independent of BiciMAD, EMT Madrid and Madrid City Council.
