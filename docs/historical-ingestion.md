# Historical BiciMAD ingestion

The pipeline uses only the seven direct historical URLs in `config/providers/bicimad.yaml`. The outer downloads are ZIP archives, but their contents differ by period:

- 2017 and 2018: monthly nested `*_Usage_Bicimad.zip` members containing newline-delimited JSON. Trip fields include `unplug_hourTime`, `idunplug_station`, `idplug_station`, `travel_time`, and user identifiers.
- 2019: a mixture of newline-delimited JSON usage members, monthly movement ZIPs, and RAR station members. RAR members are recorded as unsupported in the inspection metadata; they are not silently treated as trip data.
- 2020: monthly JSON station-state snapshots and movement ZIPs. Station-state snapshots are not used as trip demand because they do not have trip origin/destination semantics. Movement members are used where present.
- 2021 and 2022: movement ZIPs, monthly JSON station snapshots, and monthly CSV trip ZIPs. CSV trip rows contain `unlock_date`, `lock_date`, `station_unlock`, `station_lock`, and `trip_minutes`.
- 2023: monthly CSV trip ZIPs with the same trip fields as the later 2021/2022 CSV members.

The parser normalizes legacy JSON-lines and later CSV records into a common trip contract: trip identifier, UTC start/end timestamps, origin station, destination station, optional duration, source year, and source member. Every timestamp is normalized to timezone-aware UTC. Naive values use an explicit Europe/Madrid policy: normal wall-clock values are localized normally; ambiguous fall-back values choose the standard-time occurrence (`fold=1`); nonexistent spring-forward values are shifted forward by the one-hour DST gap while preserving minutes and seconds; already timezone-aware values retain their stated offset. The parser records the original raw value and the resolution applied (`normal_local_time`, `ambiguous_standard_time`, `nonexistent_shifted_forward`, or `already_timezone_aware`). Invalid or missing timestamps and missing station IDs are counted as skipped records; malformed selected files and missing required columns fail loudly.

Trips are aggregated into one-hour UTC buckets because the source records are event timestamps and do not provide a defensible fixed station-state sampling interval. For each station and bucket:

- `departures`: count of trips whose origin is the station.
- `arrivals`: count of trips whose destination is the station.
- `total_activity`: departures plus arrivals.
- `net_flow`: arrivals minus departures.
- `demand`: departures, the forecasting target currently exposed by the pipeline.

A station/time row is created only when at least one trip event exists. It is not interpreted as zero demand for unobserved combinations, because archive coverage and station availability are not sufficient to distinguish no trips from missing coverage. The Gold dataset therefore represents observed activity, not a complete station panel. Future feature construction must explicitly complete a panel only after coverage semantics are established.

Raw selected nested members are written to ignored `data/bronze/historical_members/`. macOS `__MACOSX/` entries, directory entries, and `.DS_Store` metadata are ignored. The `.rar` members observed in 2018 and 2019 are station snapshots, not trip movements, so they are explicitly reported as unsupported-but-irrelevant; required movement data is never silently skipped. A selected required movement member with zero valid records fails the run. The run reports per-member progress, raw/parsed/skipped/invalid counts, duplicate counts, selected/ignored/unsupported members, and grouped failures. Historical Silver and Gold files are published atomically only after all required years parse and PostgreSQL persistence succeeds; `historical_demand_complete.json` is the completion marker. PostgreSQL stores the Gold observations in `demand_observations` with a uniqueness constraint and `(station_id, observed_at)` index.
