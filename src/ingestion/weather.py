"""Cached, validated historical weather for a real geographic location.

ERA5 is reanalysis, not a rain gauge or a forecast available at prediction time.
Keep it out of forecasting features unless its publication delay is respected.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"


def historical_weather(latitude: float, longitude: float, year: int, cache: Path):
    """Fetch one year once; validate every UTC hour and preserve the API response."""
    params: dict[str, str | float] = {
        "latitude": round(latitude, 4),
        "longitude": round(longitude, 4),
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": "temperature_2m,precipitation,rain,wind_speed_10m",
        "timezone": "GMT",
        "models": "era5",
        "timeformat": "unixtime",
    }
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:20]
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"era5-{key}.json"
    cached = path.exists()
    if cached:
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        with requests.Session() as session:
            session.mount(
                "https://",
                HTTPAdapter(
                    max_retries=Retry(
                        total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504]
                    )
                ),
            )
            response = session.get(WEATHER_URL, params=params, timeout=(15, 180))
            response.raise_for_status()
            payload = response.json()
    frame = pd.DataFrame(payload["hourly"])
    frame["time"] = pd.to_datetime(frame.time, unit="s", utc=True)
    expected = pd.date_range(
        f"{year}-01-01", f"{year + 1}-01-01", freq="h", inclusive="left", tz="UTC"
    )
    if not pd.DatetimeIndex(frame.time).equals(expected) or frame.isna().any().any():
        raise ValueError("Weather response is incomplete; no missing weather is imputed")
    if (frame[["precipitation", "rain"]] < 0).any().any():
        raise ValueError("Negative precipitation in weather response")
    if not cached:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(path)
    return frame, {
        "source": "Open-Meteo / Copernicus ERA5",
        "url": WEATHER_URL,
        "documentation": "https://open-meteo.com/en/docs/historical-weather-api",
        "license": "CC BY 4.0; credit Open-Meteo and Copernicus",
        "requested": params,
        "grid_latitude": payload["latitude"],
        "grid_longitude": payload["longitude"],
        "units": payload["hourly_units"],
        "hours": len(frame),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "cache_hit": cached,
        "spatial_policy": "One representative network grid cell (~25 km); not station-level rainfall",
    }
