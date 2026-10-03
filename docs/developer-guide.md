# Developer guide

## Complete Docker workflow

Docker Compose runs four services: PostGIS, the API, a one-shot preparation pipeline, and Nginx. The frontend starts after migrations/API health and successful pipeline completion. A failed preparation job prevents an apparently healthy empty deployment.

Copy `.env.example` to `.env`, set `POSTGRES_PASSWORD`, and put the same credentials in `DATABASE_URL` (use `db` as the hostname inside Compose). Then:

```powershell
docker compose up --build -d
docker compose logs -f pipeline
```

Open http://localhost:3000. The default pipeline downloads the configured BiciMAD 2022 archive, extracts trips, retrieves historical weather, trains/evaluates models, publishes provider-specific JSON, and publishes normalized observations to PostgreSQL. First execution needs internet and can take substantially longer than a cached run. Database publication and static exports are separate transactions; a failed job must be rerun before treating a deployment as complete.

Data, models, web exports and PostgreSQL use separate named volumes. They survive container recreation. Nginx reads the export volume directly, so regenerating data does not require rebuilding the frontend image. The backend image contains API dependencies; the pipeline target also contains research dependencies, tests and QA tools. Both use `constraints-rebuilt.txt`.

Refresh the configured provider:

```powershell
docker compose run --rm pipeline
```

To retrain or prepare another configured provider/year:

```powershell
docker compose run --rm pipeline python scripts/rebuild_project.py --provider bicimad --year 2022 --download --force --publish-database
```

For a newly added configuration, rebuild the images first. See [provider onboarding](providers.md) for CSV mappings and source mounting. Publish jobs sequentially; simultaneous writers to the same export catalog are not supported.

Published ports default to loopback. `FRONTEND_PORT`, `BACKEND_PORT` and `DATABASE_PORT` select host ports. For a remote host, put HTTPS/reverse-proxy routing in front of the web port or deliberately set `WEB_BIND_ADDRESS=0.0.0.0`. Database/API ports remain loopback. These settings do not provision DNS or TLS. Back up all four volumes before operational changes; `docker compose down -v` destroys their contents.

## Local development

Use Python 3.12 and Node.js 22. From the repository root on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -c constraints-rebuilt.txt -e ".[dev,research]"
python scripts/rebuild_project.py --download
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5177 --strictPort
```

On Linux/macOS use `source .venv/bin/activate`. The local pipeline does not need a database unless `--publish-database` is supplied. To avoid downloading again, place the BiciMAD yearly archive at `data/bronze/historical/2022.zip` and omit `--download`.

The app discovers `frontend/public/project-data/catalog.json`; its selector changes the provider/network/year through a shareable `?dataset=provider/network/year` URL. Old unscoped exports remain readable when no catalog exists. For local static hosting, generate the exports before `npm run build`; Vite copies them into `dist`. Docker serves them from its shared runtime volume instead.

## Optional BiciMAD inventory

Trip preparation and model/method pages no longer require inventory or old benchmark files. Historical occupancy/events remain a separate BiciMAD-only capability. Prepare them with:

```powershell
python scripts/rebuild_project.py --download --inventory
```

In Docker, pass the same arguments to `docker compose run --rm pipeline python scripts/rebuild_project.py --download --inventory --publish-database`. This additionally retrieves the configured 2018–2020 archives, exports `audit-data/inventory.json` and station series, and creates `analysis-data/diagnostics.json`. Linux images include libarchive (`bsdtar`); Windows uses libarchive's `tar.exe` when available. Missing inventory is displayed as unavailable and never replaced with simulated observations.

`scripts/build_audit_report.py` is an optional legacy whole-project assessment. Unlike the inventory exporter above, it still requires the older canonical Gold and benchmark artifacts. `config/audit-findings.json` is an optional historical overlay and is not distributed. `frontend/src/config/audit-remediation.json` contains a dated assessment, not current provider results.

## API and legacy research

Current observations are published independently by provider/network. `/api/v1/stations`, `/api/v1/history` and `/api/v1/analytics/{summary,profile,stations,series}` accept `provider_id` and `network_id`. Publication chooses one current dataset per network; older years remain in the static research catalog. The database explorer shows that current dataset.

The old `make prepare-data`, `make build-forecasting-contract` and `make benchmark` workflow is a separate BiciMAD historical reference. Run it in the pipeline image, with `PERSIST_TO_DATABASE=true` for `prepare-data`; its full seven-year archive download is expensive. Its archived benchmark hashes are intentionally specific to that reference and are not used to admit new providers. Legacy replay/evaluation reject other providers. The live forecast endpoint returns 410; there is no risk endpoint. The SDK now exposes observed history/summary and explicitly named historical replay.

## Checks

```powershell
python -m pytest -q
python -m ruff check src tests
python -m mypy src
cd frontend
npm run lint
npm run build
```

For Windows environments with restricted shared temporary directories, pass `-p no:cacheprovider --basetemp .test-tmp/<unique-run-name>` to pytest. The suite exercises two synthetic providers with overlapping string IDs and different timezones through ingestion, real fitting, reload, JSON export and cache reuse. Fixtures are explicitly synthetic and never part of the default pipeline.

CI also starts a fresh isolated Compose stack using `docker-compose.smoke.yml`, migrates an empty PostGIS database, publishes both fixture providers, and verifies web/API responses with `scripts/check_deployment.py`. This validates wiring and isolation; it does not validate another operator's real-world source semantics or replace a full real-data rebuild.

The independent BiciMAD recount remains `python scripts/verify_trip_counts.py`. Refresh the checked-in reference comparison explicitly with `python scripts/document_model_comparison.py`; normal provider runs write comparisons alongside their model artifacts and do not rewrite repository documentation.
