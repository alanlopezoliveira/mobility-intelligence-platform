from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.config.settings import AppSettings
from src.db.database import get_session
from src.db.models import DemandObservation, ModelRegistry, Station

app = FastAPI(title='Mobility Intelligence Platform', version='0.2.0')
settings = AppSettings()
SessionDependency = Annotated[Session, Depends(get_session)]
REPO_ROOT = Path(__file__).resolve().parents[2]
app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:3000', 'http://127.0.0.1:3000'],
    allow_credentials=False,
    allow_methods=['GET'],
    allow_headers=['*'],
)


@app.get('/health')
def health(session: SessionDependency) -> dict[str, str]:
    try:
        session.execute(select(1))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail='database unavailable') from exc
    return {'status': 'ok'}


@app.get('/api/v1/config')
def config() -> dict[str, str | int]:
    return {
        'app_name': settings.app_name,
        'default_language': settings.default_language,
        'default_provider': settings.default_provider,
        'default_forecast_horizon_minutes': settings.default_forecast_horizon_minutes,
    }


def _station_dict(station: Station) -> dict[str, str | int | float | None]:
    return {
        'station_id': station.station_id,
        'name': station.name,
        'address': station.address,
        'capacity': station.capacity,
        'available_bikes': station.available_bikes,
        'latitude': station.latitude,
        'longitude': station.longitude,
    }


@app.get('/api/v1/stations')
def stations(session: SessionDependency, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, str | int | float | None]]:
    try:
        values = session.scalars(select(Station).order_by(Station.station_id).limit(limit)).all()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail='database unavailable') from exc
    return [_station_dict(station) for station in values]


@app.get('/api/v1/stations/{station_id}')
def station_detail(station_id: str, session: SessionDependency) -> dict[str, str | int | float | None]:
    station = session.get(Station, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail='station not found')
    return _station_dict(station)


@app.get('/api/v1/history')
def history(session: SessionDependency, station_id: str | None = None, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, str | float]]:
    query = select(DemandObservation).order_by(DemandObservation.observed_at.desc()).limit(limit)
    if station_id:
        query = query.where(DemandObservation.station_id == station_id)
    values = session.scalars(query).all()
    return [
        {'station_id': value.station_id, 'observed_at': value.observed_at.isoformat(), 'demand': value.demand}
        for value in values
    ]


@app.get('/api/v1/forecast')
def forecast(horizon_minutes: int = Query(default=60, ge=30, le=120)) -> dict[str, object]:
    metadata_path = REPO_ROOT / 'models' / 'metadata' / 'model_metadata.json'
    if not metadata_path.exists():
        raise HTTPException(status_code=503, detail='no validated temporal forecasting model is available')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    if metadata.get('status') != 'validated':
        raise HTTPException(status_code=503, detail='forecasting model is not validated')
    return {'horizon_minutes': horizon_minutes, 'predictions': []}


@app.get('/api/v1/risk')
def risk(session: SessionDependency) -> list[dict[str, str | float | None]]:
    values = session.scalars(select(Station).order_by(Station.station_id)).all()
    result = []
    for station in values:
        ratio = None
        level = 'UNKNOWN'
        if station.capacity and station.available_bikes is not None:
            ratio = station.available_bikes / station.capacity
            level = 'LOW' if ratio < 0.2 else 'MEDIUM' if ratio < 0.5 else 'HIGH'
        result.append({'station_id': station.station_id, 'risk': level, 'availability_ratio': ratio})
    return result


@app.get('/api/v1/optimization/recommendations')
def optimization_recommendations(session: SessionDependency) -> list[dict[str, str]]:
    session.execute(select(Station.station_id).limit(1))
    return []


@app.get('/api/v1/model/info')
def model_info(session: SessionDependency) -> dict[str, object]:
    value = session.scalars(select(ModelRegistry).order_by(ModelRegistry.created_at.desc())).first()
    if value is None:
        return {'model_name': None, 'status': 'unavailable', 'reason': 'no validated temporal model'}
    return {
        'model_name': value.model_name,
        'model_version': value.model_version,
        'status': value.status,
        'feature_definition': value.feature_definition,
        'train_period': value.train_period,
        'validation_period': value.validation_period,
        'test_period': value.test_period,
        'metrics': json.loads(value.metrics_json),
    }


@app.get('/api/v1/metrics')
def metrics(session: SessionDependency) -> dict[str, object]:
    value = session.scalars(select(ModelRegistry).order_by(ModelRegistry.created_at.desc())).first()
    return json.loads(value.metrics_json) if value else {'status': 'unavailable'}
