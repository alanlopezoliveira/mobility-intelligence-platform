# Data and methods

## Scope and architecture

MobilityLab's main workflow studies official BiciMAD trips from 2022, joins historical Madrid weather for descriptive analysis, and evaluates departures at +60 and +120 minutes. React/TypeScript reads local JSON exports prepared by Python. PostgreSQL and FastAPI also serve current normalized observations, independently published per provider/network. The older benchmark/replay remains a separate BiciMAD reference.

The interface also exposes historical station inventory from available months in 2018–2020. This is a separate population and period. The application does not provide live availability, occupancy predictions or operational redistribution recommendations.

See the [developer guide](developer-guide.md) for execution and the [model comparison](model-comparison.md) for measured results.

## Reuse and deployment

The original research conclusions below describe BiciMAD 2022. The pipeline now accepts a configured provider/year, routes ingestion through a registered adapter, uses provider timezones, and isolates artifacts and model encodings. See [provider onboarding](providers.md) for the normalized contract and its current full-year data requirements. Synthetic cross-provider checks do not establish the validity of an unreviewed real source.

Docker Compose runs migration/API, batch preparation and Nginx with persistent data/model/export volumes. The frontend waits for successful preparation; model/method pages do not depend on legacy inventory/benchmark exports. Inventory is an optional BiciMAD capability.

## Sources

| Source | Contribution | Limitation |
|---|---|---|
| [Official BiciMAD historical catalogue](https://datos.madrid.es/dataset/900034-0-bicimad-viajes-estaciones), [2022 distribution](https://media.emtmadrid.es/-uHaW6iZkhG) | Exact-timestamp trip CSVs, station identifiers and origin coordinates | Recorded trips cannot reliably separate customer journeys from operator movements |
| Station snapshots in downloaded BiciMAD archives | Docked bikes, free docks, capacity and service flags | Available months do not establish continuous coverage or outage causes |
| [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api), ERA5 | Hourly rain, precipitation, temperature and wind for 2022 | City-scale reanalysis, not observations at individual bike stations |

The configured historical archive URLs and auxiliary current station-master URL are in `config/providers/bicimad.yaml`. The current station master is not a source of historical forecasting targets. The reference year avoids mixing older rounded timestamps, incompatible formats and unverified station-ID changes across years.

## Data flow and storage

| Location | Role |
|---|---|
| `data/bronze/historical/`, `data/bronze/historical_members/` | Original yearly and monthly archives |
| `data/bronze/weather/` | Original weather API responses |
| `data/rebuilt/cache/` | Validated monthly aggregates |
| `data/rebuilt/2022/station-hourly.csv.gz` | Observed station-hours: `time`, `station`, `departures`, `arrivals` |
| `data/rebuilt/2022/stations.csv` | Station identifiers, names, coordinates and coordinate spans |
| `data/rebuilt/2022/network-weather-hourly.csv` | Network-hour totals joined to weather and calendar |
| `data/rebuilt/2022/report.json` | Source hashes, quality counters, assumptions, metrics and timings |
| `models/providers/bicimad/madrid/2022/` | Saved models, contracts and candidate evaluation artifacts |
| `frontend/public/project-data/providers/bicimad/madrid/2022/` | Summaries and predictions consumed by the main app |

These outputs are generated locally and excluded from Git. The final BiciMAD tables in `data/rebuilt/2022/` serve as the current gold layer. The older `data/silver/`, `data/gold/` and PostgreSQL contracts remain separate.

## Trip preparation and missing data

Departures and arrivals use their own station identifiers and timestamps. Each is validated independently, so a valid arrival can survive an invalid origin. Resolved arrivals before departure are rejected. Blank rows, exact duplicates and invalid events are counted separately; accepted plus rejected events reconcile to unique nonblank source rows.

Offset-free trip times are interpreted as `Europe/Madrid`. Ambiguous or nonexistent daylight-saving times are rejected and counted. This is a documented assumption about the source clock. Aggregation and joins use UTC; calendar features use Madrid time.

The model cohort retains station IDs present in all twelve months, with latitude and longitude spans no greater than 0.01 degrees. This retrospective selection limits generalisation and does not prove continuous physical identity.

Within hours with recorded network departures, missing station-hour combinations in the modelling panel become zero: **no recorded trip**, not proof of an operating station. Hours with no network departures remain outside coverage. Historical features require exact timestamps and complete rolling windows.

## Weather analysis

One ERA5 cell, approximately 25 km across, represents Madrid. Its requested location is derived from station-origin coordinates, and the returned grid coordinates are retained. Rain accumulation is shifted back one hour to match the trip interval's start; temperature and wind on the joined row describe the interval-end instant. Rainy means at least 0.1 mm of rain.

Raw comparisons use all rainy and dry network hours. Calendar matching compares a rainy hour with dry hours sharing local month, hour and weekday/weekend status, requiring at least three dry reference hours. Each rainy hour receives equal weight. Monthly charts show hourly means; small rainy samples and sensitivity to individual days must remain visible.

These are descriptive associations. They do not establish that rain causes a change in trips. Finalised ERA5 weather is excluded from forecasting because it was unavailable at historical issue time.

## Forecasting and evaluation

The target is recorded departures during `[target, target + 1 hour)`. A +60-minute forecast is issued one hour before that interval begins; +120 minutes uses two hours. Inputs include station, target calendar, completed-hour counts, target-aligned previous-day/week counts, trailing departure means and recent recorded movements. Real deployment would also require collection latency.

January–August trains, September–October selects, and November–December tests, with gaps at partition boundaries. Seven trained families and three simple references use identical evaluation rows. Selection uses validation MAE, with a preference for simpler candidates within 1% of the best. Test scores do not select the winner. Nonnegative predictions are used consistently for scoring and display.

The selected family and full metrics belong in the generated [model comparison](model-comparison.md) and matching model contracts, rather than duplicated prose. Models are saved and checked after reloading. The historical test period has been inspected in earlier iterations; it is not a fresh prospective validation. Rolling-origin, unseen-station and real-world validation remain outstanding.

## Inventory interpretation

Inventory histories and diagnostics are exported to `frontend/public/audit-data/` and `frontend/public/analysis-data/`. Local evidence is retained under `data/audit/`.

Service flags and inventory counts have separate meanings. Unknown flags and conflicting station/time records cannot be treated as confirmed availability. Night is defined as source wall-clock 00:00–05:59; the timezone and continuous station identity remain unverified. Empty/full percentages are weighted by observed samples, not operating duration. An observed event span does not establish an exact outage duration or its cause.

## Provenance and limitations

Source and artifact hashes validate ingestion, weather and model caches. An unchanged run verifies exports and skips retraining. Source-to-numeric station encodings are stored beside models; forecast exports restore the original identifiers. `--force` retrains while reusing valid source caches. `data/rebuilt/2022/last-run.json` records the latest execution timing. An independent recount is available through `scripts/verify_trip_counts.py`.

Raw trip/user identifiers are not published in web exports. The project does not establish latent demand, customer-only counts, a cross-year station identity mapping, causal weather effects or operational readiness. Legacy benchmark and production artifacts are not evidence for the current selected models.
