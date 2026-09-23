from pathlib import Path

from src.data_quality.canonical_gold import rebuild_canonical_gold

ROOT = Path(__file__).resolve().parents[1]
report = rebuild_canonical_gold(
    ROOT / 'data' / 'silver' / 'historical_trip_observations.csv',
    ROOT / 'data' / 'gold' / 'station_demand_hourly.csv',
    ROOT / 'data' / 'gold' / 'canonical_gold_validation.json',
)
print(report)