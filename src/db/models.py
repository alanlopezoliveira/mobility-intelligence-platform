from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class HistoricalStationSnapshot(Base):
    __tablename__ = "historical_station_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "network_id",
            "station_id",
            "snapshot_period",
            "source_member",
            name="uq_historical_station_snapshot_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    station_id: Mapped[str] = mapped_column(String(64), index=True)
    provider_id: Mapped[str] = mapped_column(String(64), index=True)
    network_id: Mapped[str] = mapped_column(String(64), index=True)
    station_instance_id: Mapped[str] = mapped_column(String(160), index=True)
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

    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    network_id: Mapped[str] = mapped_column(String(64), primary_key=True)
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
    __table_args__ = (
        UniqueConstraint("batch_id", "provider_id", "network_id", "station_instance_id", "observed_at", name="uq_demand_batch_provider_network_instance_time"),
        ForeignKeyConstraint(
            ["station_instance_id", "provider_id", "network_id"],
            ["station_instances.station_instance_id", "station_instances.provider_id", "station_instances.network_id"],
            name="fk_demand_station_instance_scope",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[str] = mapped_column(String(64), index=True)
    provider_id: Mapped[str] = mapped_column(String(64), index=True)
    network_id: Mapped[str] = mapped_column(String(64), index=True)
    station_instance_id: Mapped[str] = mapped_column(String(160), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    departures: Mapped[int] = mapped_column(Integer, default=0)
    arrivals: Mapped[int] = mapped_column(Integer, default=0)
    total_activity: Mapped[int] = mapped_column(Integer, default=0)
    net_flow: Mapped[int] = mapped_column(Integer, default=0)
    demand: Mapped[float] = mapped_column(Float, default=0)
    source_name: Mapped[str] = mapped_column(String(128))
    batch_id: Mapped[str] = mapped_column(ForeignKey("ingestion_batches.batch_id"), index=True)
    source_record_id: Mapped[str | None] = mapped_column(Text)
    source_record_hash: Mapped[str | None] = mapped_column(String(64))
    original_time_text: Mapped[str | None] = mapped_column(Text)


class IngestionBatch(Base):
    __tablename__ = "ingestion_batches"
    __table_args__ = (CheckConstraint("status IN ('loading', 'validated', 'published', 'failed')", name="ck_ingestion_batches_status"),)

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(128))
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    source_manifest_json: Mapped[str | None] = mapped_column(Text)
    expected_rows: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActiveDatasetVersion(Base):
    __tablename__ = "active_dataset_version"
    __table_args__ = (CheckConstraint("singleton_id = 1", name="ck_active_dataset_singleton"),)

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("ingestion_batches.batch_id"))


class ProviderMetadata(Base):
    __tablename__ = "provider_metadata"

    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64))
    calendar_version: Mapped[str] = mapped_column(String(64))


class NetworkDatasetVersion(Base):
    """Current validated observations for each independently published network."""

    __tablename__ = 'network_dataset_versions'
    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    network_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey('ingestion_batches.batch_id'))


class NetworkMetadata(Base):
    __tablename__ = "network_metadata"
    __table_args__ = (ForeignKeyConstraint(["provider_id"], ["provider_metadata.provider_id"]),)

    provider_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    network_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))


class StationInstance(Base):
    __tablename__ = "station_instances"
    __table_args__ = (
        UniqueConstraint("station_instance_id", "provider_id", "network_id", name="uq_station_instance_scope"),
        ForeignKeyConstraint(["provider_id", "network_id"], ["network_metadata.provider_id", "network_metadata.network_id"]),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_station_instance_validity"),
    )

    station_instance_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(64))
    network_id: Mapped[str] = mapped_column(String(64))
    provider_station_id: Mapped[str] = mapped_column(String(64))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identity_provenance: Mapped[str] = mapped_column(Text)


class StationInstanceRelationship(Base):
    __tablename__ = "station_instance_relationships"
    __table_args__ = (CheckConstraint("relationship_type IN ('rename', 'merge', 'split', 'relocation', 'replacement')", name="ck_station_relationship_type"),)

    relationship_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_station_instance_id: Mapped[str] = mapped_column(ForeignKey("station_instances.station_instance_id"))
    to_station_instance_id: Mapped[str] = mapped_column(ForeignKey("station_instances.station_instance_id"))
    relationship_type: Mapped[str] = mapped_column(String(24))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[str] = mapped_column(Text)


class PhysicalPlace(Base):
    __tablename__ = "physical_places"

    physical_place_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provenance: Mapped[str] = mapped_column(Text)


class StationPlaceMapping(Base):
    __tablename__ = "station_place_mappings"
    __table_args__ = (CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_station_place_confidence"),)

    station_instance_id: Mapped[str] = mapped_column(ForeignKey("station_instances.station_instance_id"), primary_key=True)
    physical_place_id: Mapped[str] = mapped_column(ForeignKey("physical_places.physical_place_id"))
    provenance: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)


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
