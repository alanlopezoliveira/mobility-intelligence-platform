# Adding a data provider

## Supported contract

The current service supports historical station-based trip analysis. A provider can reuse the whole trip-to-web workflow by supplying a YAML configuration and mapped CSVs, or ZIPs containing CSVs. It does not automatically support every transport mode, live GBFS inventories, authentication scheme, or archive format.

`src/providers/registry.py` defines `TripProvider` and the adapter registry. An adapter returns hourly UTC counts (`time`, `station`, `departures`, `arrivals`), source station locations (`station`, `name`, `latitude`, `longitude`), and content-hashed source manifests with quality counters. Models receive only this normalized contract. `MobilityProvider` in `src/core/provider.py` is a separate abstract legacy live-feed contract; it cannot silently produce an empty successful feed.

## Configuration example

Save a configuration as `config/providers/acme.yaml`. Replace the example metadata with the actual source's provenance and terms before using real data:

```yaml
provider: acme
name: Acme Bikes
network_id: city
city: Example City
timezone: America/New_York
calendar_version: local-weekdays-v1
reference_year: 2022
source_name: acme_trips
catalog_url: https://example.org/trips
source_license_or_terms: Replace with verified source terms
attribution: Replace with the required attribution
trips:
  adapter: csv
  file_glob: data/bronze/trips/{year}*.csv
  separator: ','
  encoding: utf-8-sig
  columns:
    started_at: started_at
    ended_at: ended_at
    origin_station: start_station_id
    destination_station: end_station_id
    latitude: start_lat
    longitude: start_lng
    name: start_station_name
```

The provider ID must match the filename. Provider/network IDs use lowercase letters, digits, underscores or hyphens, with a maximum of 40 characters. The timezone must be an IANA identifier. A configuration describes one network. Add another configuration/adapter when a source requires a different contract; never just relabel BiciMAD fields.

Place files under `data/providers/acme/city/data/bronze/trips/` and run:

```powershell
python scripts/rebuild_project.py --provider acme --year 2022
```

Use `--publish-database` to publish observations after migrating PostgreSQL and configuring `DATABASE_URL`. Docker can mount a local source folder:

```powershell
docker compose build pipeline
docker compose run --rm --volume ./acme-trips:/app/data/providers/acme/city/data/bronze/trips:ro pipeline python scripts/rebuild_project.py --provider acme --year 2022 --publish-database
```

`historical` entries can optionally provide yearly direct ZIP URLs using the same metadata schema as BiciMAD. `--download` saves them under the provider workspace's `data/bronze/historical/<year>.zip`; set `file_glob` accordingly. Existing archives are retained, and malformed downloads fail before publication. Automatic credentialed source access is not implemented.

## Semantics and limits

- Station IDs are opaque strings (1–64 characters in PostgreSQL); leading zeros and slashes are preserved. Web filenames use hashes, and model station encodings are saved explicitly.
- Offset-bearing timestamps are converted to UTC. Naive clocks use the configured timezone; ambiguous/nonexistent DST clocks are rejected. Arrivals keep their own time and destination, even if their origin is invalid.
- Exact duplicate rows are removed across source files/chunks. Keep a true trip ID column in source CSVs when available so distinct simultaneous trips do not collapse. Do not use user/bicycle identifiers as trip identifiers.
- Input counts describe recorded trips, not unmet demand or live availability. Current modelling needs stations observed in all twelve months, stable coordinates, sufficient chronological train/validation/test coverage, and sufficient wet/dry weather observations for the weather comparison. Failures are explicit; no synthetic fallback is used.
- The default benchmark fits January–August, selects September–October, and tests November–December. Changing these assumptions is a model-design change, not merely source configuration.

## Isolation and artifacts

Prepared tables are under the provider workspace's `data/rebuilt/<year>/`. Models and encoding maps are under `models/providers/<provider>/<network>/<year>/`. Web results are under `frontend/public/project-data/providers/<provider>/<network>/<year>/`; `catalog.json` lists completed reports.

PostgreSQL station keys include provider, network and station ID. `network_dataset_versions` selects an independently published batch for each network. Publication validates directional counts, timezone awareness, unique keys and persisted row counts before switching the pointer in the same transaction. Replaying one provider does not replace another's active batch. This is data-source isolation, not an authenticated customer/tenant permission system.

Historical inventory export remains BiciMAD-specific. The UI displays that limitation for other providers. A new real provider must be checked against its actual clock, ID, location and licensing semantics; synthetic integration tests prove software behavior, not source validity.
