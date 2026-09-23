from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class HistoricalStationSnapshot(Base):
    __tablename__ = "historical_station_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "station_id",
            "snapshot_period",
            "source_member",
            name="uq_historical_station_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    station_id: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_period: Mapped[str] = mapped_column(String(32), index=True)
    station_number: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    total_bases: Mapped[int | None] = mapped_column(Integer)
    free_bases: Mapped[int | None] = mapped_column(Integer)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    activate: Mapped[int | None] = mapped_column(Integer)
    source_archive: Mapped[str | None] = mapped_column(String(256))
    source_member: Mapped[str | None] = mapped_column(String(256))
    source_path: Mapped[str | None] = mapped_column(Text)
    record_identity: Mapped[str] = mapped_column(String(512), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Station(Base):
    __tablename__ = "stations"

    station_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    capacity: Mapped[int | None] = mapped_column(Integer)
    available_bikes: Mapped[int | None] = mapped_column(Integer)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    location: Mapped[object | None] = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    source_name: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class DemandObservation(Base):
    __tablename__ = "demand_observations"
    __table_args__ = (UniqueConstraint("station_id", "observed_at", name="uq_demand_station_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(String(64), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    departures: Mapped[int] = mapped_column(Integer, default=0)
    arrivals: Mapped[int] = mapped_column(Integer, default=0)
    total_activity: Mapped[int] = mapped_column(Integer, default=0)
    net_flow: Mapped[int] = mapped_column(Integer, default=0)
    demand: Mapped[float] = mapped_column(Float, default=0)
    source_name: Mapped[str] = mapped_column(String(128))


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    model_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    feature_definition: Mapped[str] = mapped_column(Text)
    train_period: Mapped[str | None] = mapped_column(Text)
    validation_period: Mapped[str | None] = mapped_column(Text)
    test_period: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[str] = mapped_column(Text)
    artifact_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
