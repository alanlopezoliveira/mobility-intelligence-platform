from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')
    host: str = Field(default='0.0.0.0')
    port: int = Field(default=8000)
    prefix: str = Field(default='/api/v1')


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')
    url: str = Field(
        default='postgresql://mobility_user:change_me@db:5432/mobility',
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


class ProviderConfigModel(BaseModel):
    provider: str
    name: str
    source_name: str
    catalog_url: str
    source_license_or_terms: str
    attribution: str
    temporal_coverage: str | None = None
    spatial_coverage: str | None = None
    station_master: dict
    historical: list[ProviderSource]


def load_provider_config(provider: str = 'bicimad') -> ProviderConfigModel:
    config_path = Path(__file__).resolve().parents[2] / 'config' / 'providers' / f'{provider}.yaml'
    raw = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    return ProviderConfigModel.model_validate(raw)
