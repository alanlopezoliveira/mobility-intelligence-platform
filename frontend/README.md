# MobilityLab web client

React and TypeScript interface for historical provider-scoped mobility analysis. The main pages consume JSON exports prepared by `python scripts/rebuild_project.py` from the repository root. See the [developer guide](../docs/developer-guide.md) for installation, data preparation and the optional database workflow.

## Development

From this directory, after preparing the required exports:

```powershell
npm ci
npm run dev -- --host 127.0.0.1 --port 5177
```

Checks and preview:

```powershell
npm run lint
npm run build
npm run preview -- --host 127.0.0.1 --port 4173
```

## Pages and inputs

`RootApp.tsx` owns navigation, browser history and the shared menu.

| Pages | Input |
|---|---|
| `#overview`, `#weather`, `#forecasts`, `#models`, `#quality` | `public/project-data/`; weather diagnostics also use `public/analysis-data/` |
| `#occupancy`, `#events` | Optional `public/audit-data/inventory.json`, station series and `public/analysis-data/` |
| `#methods` | Selected provider report and its explicit assumptions |
| `#explorer` | FastAPI and PostgreSQL; the development proxy targets `127.0.0.1:8000` |

The current report, forecasts, model comparison and methods use the selected provider/network/year under `project-data/providers/`. `project-data/catalog.json` powers the dataset selector; its selection is retained in the `dataset` URL query. The legacy unscoped path is a fallback for older local exports.

Generate inventory with `rebuild_project.py --inventory`; it does not require a legacy benchmark. Other providers explicitly report inventory as unavailable. JSON exports are ignored by Git. For a standalone static build, prepare them first. In Docker, Nginx reads a shared runtime export volume, and missing JSON returns 404 instead of the SPA HTML fallback.

Forecasts concern historical departures, not current bike availability. Methods and measured results are maintained in [project documentation](../docs/README.md).
