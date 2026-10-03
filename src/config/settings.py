from __future__ import annotations

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Select psycopg 3 explicitly when a conventional PostgreSQL URL is supplied."""
    if url.startswith('postgresql://'):
        return url.replace('postgresql://', 'postgresql+psycopg://', 1)
    return url


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')
    host: str = Field(default='0.0.0.0')
    port: int = Field(default=8000)
    prefix: str = Field(default='/api/v1')


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')
    url: str = Field(
        default='postgresql://mobility_user@localhost:5432/mobility',
        validation_alias='DATABASE_URL',
    )


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')
    app_name: str = Field(default='Mobility Intelligence Platform')
    default_language: str = Field(default='es')
    default_provider: str = Field(default='bicimad')
    default_forecast_horizon_minutes: int = Field(default=60)


class ProviderSource(BaseModel):
    source_name: str
    provider: str
    dataset_id: str
    distribution_id: str | int
    year: int
    direct_url: str
    catalog_url: str
    expected_extension: str
    expected_content_type: str
    source_license_or_terms: str
    attribution: str
    temporal_coverage: str | None = None
    spatial_coverage: str | None = None


class TripIngestionConfig(BaseModel):
    """Map provider CSV fields into the common trip contract; IDs stay opaque."""

    adapter: Literal['bicimad', 'csv'] = 'bicimad'
    file_glob: str = 'data/bronze/trips/*.csv'
    separator: str = ','
    encoding: str = 'utf-8-sig'
    columns: dict[str, str] = Field(default_factory=dict)

    @field_validator('file_glob')
    @classmethod
    def relative_glob(cls, value: str) -> str:
        if Path(value).is_absolute() or '..' in Path(value).parts or ':' in value:
            raise ValueError('file_glob must remain inside the provider workspace')
        return value


class ProviderConfigModel(BaseModel):
    """One independently published network, its provenance and ingestion policy."""
    provider: str
    name: str
    network_id: str
    timezone: str
    calendar_version: str
    source_name: str
    catalog_url: str
    source_license_or_terms: str
    attribution: str
    temporal_coverage: str | None = None
    spatial_coverage: str | None = None
    station_master: dict = Field(default_factory=dict)
    historical: list[ProviderSource] = Field(default_factory=list)
    city: str = 'Madrid'
    reference_year: int = Field(default=2022, ge=1970, le=2100)
    trips: TripIngestionConfig = Field(default_factory=TripIngestionConfig)

    @field_validator('provider', 'network_id')
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        import re

        if len(value) > 40 or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', value):
            raise ValueError('provider and network IDs must be lowercase URL-safe identifiers')
        return value

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value


def load_provider_config(provider: str = 'bicimad') -> ProviderConfigModel:
    """Load a named network configuration without allowing arbitrary file paths."""
    ProviderConfigModel.safe_identifier(provider)
    config_path = Path(__file__).resolve().parents[2] / 'config' / 'providers' / f'{provider}.yaml'
    raw = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    config = ProviderConfigModel.model_validate(raw)
    if config.provider != provider:
        raise ValueError('The provider ID must match its configuration filename')
    return config
