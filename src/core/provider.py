from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderConfig:
    provider: str
    dataset_id: str | None = None
    year: int | None = None
    direct_url: str | None = None
    catalog_url: str | None = None
    expected_extension: str | None = None
    expected_content_type: str | None = None


@dataclass
class Station:
    station_id: str
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    capacity: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StationObservation:
    station_id: str
    observed_at: str
    available_bikes: int | None = None
    free_docks: int | None = None
    capacity: int | None = None
    status: str | None = None


@dataclass
class TripRecord:
    trip_id: str | None = None
    station_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_minutes: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MobilityProvider(ABC):
    """Legacy live-feed contract; batch trip adapters live in providers.registry.

    Unsupported operations must be implemented explicitly, never represented as
    a successful empty data feed.
    """
    name: str = "base"

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or ProviderConfig(provider=self.name)

    @abstractmethod
    def list_stations(self) -> list[Station]:
        """Return source station metadata, preserving provider identifiers."""
        raise NotImplementedError

    @abstractmethod
    def fetch_station_observations(self) -> list[StationObservation]:
        """Return observed inventories; unknown values remain missing."""
        raise NotImplementedError

    @abstractmethod
    def fetch_trips(self) -> list[TripRecord]:
        """Return journeys with their independent origin/destination timestamps."""
        raise NotImplementedError
