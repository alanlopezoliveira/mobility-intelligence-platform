# Data sources

## BiciMAD historical data

The project uses the official historical source archive series from 2017 to 2023.

Direct URLs configured in config/providers/bicimad.yaml:
- 2017: https://media.emtmadrid.es/-HoGTStPZeC
- 2018: https://media.emtmadrid.es/-9iZNUjrjri
- 2019: https://media.emtmadrid.es/-WNgfSj2ZvC
- 2020: https://media.emtmadrid.es/-rkuBymuFJX
- 2021: https://media.emtmadrid.es/-BRk4rTaAdV
- 2022: https://media.emtmadrid.es/-uHaW6iZkhG
- 2023: https://media.emtmadrid.es/-9Nii5DXo4x

## Station master data

The station master resource is explicitly configured as the current direct CSV download:
- https://datos.madrid.es/dataset/208327-0-transporte-bicicletas-bicimad/resource/208327-2-transporte-bicicletas-bicimad/download/208327-2-transporte-bicicletas-bicimad.csv

The runtime does not discover replacements dynamically. If the configured source fails, the pipeline must fail clearly and require configuration changes.

## Historical station-state evidence

The downloaded historical archives contain station snapshot JSON members in 2018, 2019, and one 2020 station archive. Observed fields include `id`, `number`, `name`, `address`, `latitude`, `longitude`, `total_bases`, `free_bases`, `dock_bikes`, `no_available`, and `activate`, with a snapshot identifier in `_id`.

The raw local audit found 18 station snapshot files, 3,315 station rows, and 216 distinct snapshot station IDs. Every observed `activate` value was `1`. The snapshot `_id` values are point-in-time archive observations, not an activation/deactivation interval. `free_bases`, `dock_bikes`, and `no_available` are inventory fields at the snapshot observation; the source evidence does not establish an hourly operational-availability mask or distinguish closure, outage, not-yet-created, and unknown absence.

The official historical catalog is [Históricos de BiciMAD (2017-2023)](https://datos.madrid.es/dataset/900034-0-bicimad-viajes-estaciones). The official GBFS catalog is [BiciMAD GBFS](https://datos.madrid.es/dataset/900021-0-bicimad-gbfs); current/live GBFS is not used as historical evidence, and no historical GBFS snapshots are present in this repository. The official daily totals resource, [BiciMAD historical daily totals](https://datos.madrid.es/dataset/900043-0-viajes-diario-bicimad), is daily and begins in 2024, so it cannot establish historical hourly station availability for 2017-2023.

Machine-readable evidence is generated in `data/gold/availability_source_evidence.json`, and identity limitations are recorded in `data/gold/station_identity_evidence.json`.
