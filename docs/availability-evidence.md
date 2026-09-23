# Availability evidence

The project does not infer station inactivity from absent demand. The canonical Gold contains observed demand rows only; absent station-hour combinations remain `MISSING_OR_UNKNOWN`.

The official historical archive snapshots expose station inventory and metadata fields, including `free_bases`, `total_bases`, `dock_bikes`, `no_available`, and `activate`, together with a snapshot `_id`. The local audit found 18 station snapshot files and 3,315 station rows across 2018-2020. All observed `activate` values were `1`. These observations establish that station-state snapshots exist, but they do not establish hourly service availability, outage reasons, activation/deactivation intervals, or physical continuity.

The strongest defensible conclusion is **no reliable hourly availability mask**. A lifecycle mask is also not established because the source contains snapshot presence and an always-active observed value, but no authoritative lifecycle dates or inactive observations. The forecasting readiness status therefore remains `BLOCKED`.

The evidence matrix is [availability_source_evidence.json](../data/gold/availability_source_evidence.json), generated locally from the configured official archives and official catalog URLs. Current GBFS is not historical evidence and is not added as a runtime dependency.