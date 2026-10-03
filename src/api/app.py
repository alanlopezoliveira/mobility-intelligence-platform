from __future__ import annotations

import csv
import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.config.settings import AppSettings, load_provider_config
from src.db.database import get_session
from src.db.models import DemandObservation, ModelRegistry, NetworkDatasetVersion, Station
from src.ml.canonical_demand_store import AUTHORITATIVE_GOLD_HASH

app = FastAPI(title='Mobility Intelligence Platform', version='0.2.0')
settings = AppSettings()
provider_metadata = load_provider_config(settings.default_provider)
DEFAULT_PROVIDER_ID = provider_metadata.provider
DEFAULT_NETWORK_ID = provider_metadata.network_id
SessionDependency = Annotated[Session, Depends(get_session)]
REPO_ROOT = Path(__file__).resolve().parents[2]


def _active_batch_id(provider_id=DEFAULT_PROVIDER_ID, network_id=DEFAULT_NETWORK_ID):
    """Select only this network's last completely published batch."""
    return select(NetworkDatasetVersion.batch_id).where(
        NetworkDatasetVersion.provider_id == provider_id,
        NetworkDatasetVersion.network_id == network_id,
    ).scalar_subquery()
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
        'provider_id': station.provider_id,
        'network_id': station.network_id,
        'name': station.name,
        'address': station.address,
        'capacity': station.capacity,
        'available_bikes': station.available_bikes,
        'latitude': station.latitude,
        'longitude': station.longitude,
    }


@app.get('/api/v1/stations')
def stations(session: SessionDependency, limit: int = Query(default=100, ge=1, le=1000), provider_id: str = DEFAULT_PROVIDER_ID, network_id: str = DEFAULT_NETWORK_ID) -> list[dict[str, str | int | float | None]]:
    try:
        active_observation = select(DemandObservation.id).where(
            DemandObservation.provider_id == Station.provider_id,
            DemandObservation.network_id == Station.network_id,
            DemandObservation.station_id == Station.station_id,
            DemandObservation.batch_id == _active_batch_id(provider_id, network_id),
        ).exists()
        values = session.scalars(
            select(Station).where(
                Station.provider_id == provider_id,
                Station.network_id == network_id,
                active_observation,
            ).order_by(Station.station_id).limit(limit)
        ).all()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail='database unavailable') from exc
    return [_station_dict(station) for station in values]


@app.get('/api/v1/providers/{provider_id}/networks/{network_id}/stations/{station_id}')
def station_detail(provider_id: str, network_id: str, station_id: str, session: SessionDependency) -> dict[str, str | int | float | None]:
    active_observation = select(DemandObservation.id).where(
        DemandObservation.provider_id == provider_id,
        DemandObservation.network_id == network_id,
        DemandObservation.station_id == station_id,
        DemandObservation.batch_id == _active_batch_id(provider_id, network_id),
    ).exists()
    station = session.scalars(select(Station).where(
        Station.provider_id == provider_id,
        Station.network_id == network_id,
        Station.station_id == station_id,
        active_observation,
    )).first()
    if station is None:
        raise HTTPException(status_code=404, detail='station not found')
    return _station_dict(station)


@app.get('/api/v1/history')
def history(session: SessionDependency, provider_id: str = DEFAULT_PROVIDER_ID, network_id: str = DEFAULT_NETWORK_ID, station_id: str | None = None, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, str | float]]:
    query = select(DemandObservation).where(DemandObservation.batch_id == _active_batch_id(provider_id, network_id), DemandObservation.provider_id == provider_id, DemandObservation.network_id == network_id).order_by(DemandObservation.observed_at.desc()).limit(limit)
    if station_id:
        query = query.where(DemandObservation.station_id == station_id)
    values = session.scalars(query).all()
    return [
        {'station_id': value.station_id, 'provider_id': value.provider_id, 'network_id': value.network_id, 'station_instance_id': value.station_instance_id, 'observed_at': value.observed_at.isoformat(), 'demand': value.demand}
        for value in values
    ]


@app.get('/api/v1/analytics/summary')
def analytics_summary(session: SessionDependency, provider_id: str = DEFAULT_PROVIDER_ID, network_id: str = DEFAULT_NETWORK_ID) -> dict[str, object]:
    """Return coverage and observed-demand totals without treating absent hours as zero."""
    row = session.execute(
        select(
            func.count(DemandObservation.id),
            func.count(func.distinct(DemandObservation.station_id)),
            func.min(DemandObservation.observed_at),
            func.max(DemandObservation.observed_at),
            func.sum(DemandObservation.departures),
            func.sum(DemandObservation.arrivals),
        ).where(DemandObservation.batch_id == _active_batch_id(provider_id, network_id), DemandObservation.provider_id == provider_id, DemandObservation.network_id == network_id)
    ).one()
    return {
        'observed_station_hours': int(row[0] or 0),
        'stations_with_observations': int(row[1] or 0),
        'first_observation': row[2].isoformat() if row[2] else None,
        'last_observation': row[3].isoformat() if row[3] else None,
        'observed_departures': int(row[4] or 0),
        'observed_arrivals': int(row[5] or 0),
        'timezone': 'UTC',
        'missing_hour_semantics': 'unknown; absent rows are not interpreted as zero demand',
    }


@app.get('/api/v1/analytics/profile')
def analytics_profile(
    session: SessionDependency,
    provider_id: str = DEFAULT_PROVIDER_ID,
    network_id: str = DEFAULT_NETWORK_ID,
    dimension: str = Query(default='hour', pattern='^(hour|weekday|week|month)$'),
    station_id: str | None = None,
) -> list[dict[str, int | float]]:
    """Aggregate only observed rows by UTC hour, weekday, or month."""
    expressions = {
        'hour': func.extract('hour', DemandObservation.observed_at),
        'weekday': func.extract('dow', DemandObservation.observed_at),
        'week': func.extract('week', DemandObservation.observed_at),
        'month': func.extract('month', DemandObservation.observed_at),
    }
    bucket = expressions[dimension].label('bucket')
    query = select(
        bucket,
        func.count(DemandObservation.id).label('observed_rows'),
        func.avg(DemandObservation.departures).label('mean_departures'),
        func.sum(DemandObservation.departures).label('total_departures'),
        func.avg(DemandObservation.arrivals).label('mean_arrivals'),
    ).where(DemandObservation.batch_id == _active_batch_id(provider_id, network_id), DemandObservation.provider_id == provider_id, DemandObservation.network_id == network_id).group_by(bucket).order_by(bucket)
    if station_id:
        query = query.where(DemandObservation.station_id == station_id)
    return [
        {
            'bucket': int(row.bucket),
            'observed_rows': int(row.observed_rows),
            'mean_departures': float(row.mean_departures or 0),
            'total_departures': int(row.total_departures or 0),
            'mean_arrivals': float(row.mean_arrivals or 0),
        }
        for row in session.execute(query)
    ]


@app.get('/api/v1/analytics/stations')
def analytics_stations(
    session: SessionDependency,
    provider_id: str = DEFAULT_PROVIDER_ID,
    network_id: str = DEFAULT_NETWORK_ID,
    limit: int = Query(default=20, ge=1, le=100),
    metric: str = Query(default='departures', pattern='^(departures|arrivals|activity)$'),
) -> list[dict[str, str | int]]:
    metric_expression = {
        'departures': func.sum(DemandObservation.departures),
        'arrivals': func.sum(DemandObservation.arrivals),
        'activity': func.sum(DemandObservation.total_activity),
    }[metric]
    query = (
        select(DemandObservation.station_id, metric_expression.label('value'), func.count().label('observed_rows'))
        .where(DemandObservation.batch_id == _active_batch_id(provider_id, network_id), DemandObservation.provider_id == provider_id, DemandObservation.network_id == network_id)
        .group_by(DemandObservation.provider_id, DemandObservation.network_id, DemandObservation.station_id)
        .order_by(metric_expression.desc(), DemandObservation.station_id)
        .limit(limit)
    )
    return [
        {'station_id': str(row.station_id), 'value': int(row.value or 0), 'observed_rows': int(row.observed_rows)}
        for row in session.execute(query)
    ]


@app.get('/api/v1/analytics/series')
def analytics_series(
    session: SessionDependency,
    start: datetime,
    end: datetime,
    provider_id: str = DEFAULT_PROVIDER_ID,
    network_id: str = DEFAULT_NETWORK_ID,
    station_id: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
) -> list[dict[str, str | int]]:
    """Return observed hourly records for charting; intervals are bounded and UTC-normalized."""
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(status_code=422, detail='start and end must include a timezone offset')
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    if end_utc <= start_utc:
        raise HTTPException(status_code=422, detail='end must be later than start')
    if end_utc - start_utc > timedelta(days=366):
        raise HTTPException(status_code=422, detail='date range cannot exceed 366 days')
    query = select(
        DemandObservation.observed_at,
        func.sum(DemandObservation.departures).label('departures'),
        func.sum(DemandObservation.arrivals).label('arrivals'),
        func.count(DemandObservation.id).label('observed_station_hours'),
    ).where(
        DemandObservation.batch_id == _active_batch_id(provider_id, network_id),
        DemandObservation.provider_id == provider_id,
        DemandObservation.network_id == network_id,
        DemandObservation.observed_at >= start_utc,
        DemandObservation.observed_at < end_utc,
    ).group_by(DemandObservation.observed_at).order_by(DemandObservation.observed_at).limit(limit)
    if station_id:
        query = query.where(DemandObservation.station_id == station_id)
    return [
        {
            'observed_at': value.observed_at.isoformat(),
            'departures': int(value.departures or 0),
            'arrivals': int(value.arrivals or 0),
            'observed_station_hours': int(value.observed_station_hours),
        }
        for value in session.execute(query)
    ]


@lru_cache(maxsize=256)
def _simulation_row(
    horizon_minutes: int,
    station_id: str,
    feature_timestamp: str,
    artifact_modified_ns: int,
    artifact_size: int,
) -> dict[str, object] | None:
    """Find one held-out prediction from the reproducible local experiment artifact."""
    del artifact_modified_ns, artifact_size
    predictions_path = (
        REPO_ROOT / 'models' / 'experiments' / 'benchmark_v2'
        / f'{horizon_minutes}m' / 'lightgbm.csv.gz'
    )
    if not predictions_path.is_file():
        return None
    requested_time = datetime.fromisoformat(feature_timestamp).astimezone(UTC)
    with gzip.open(predictions_path, 'rt', newline='', encoding='utf-8') as stream:
        for row in csv.DictReader(stream):
            if row['station_id'] != station_id:
                if row['station_id'] > station_id:
                    break
                continue
            observed_time = datetime.fromisoformat(row['feature_timestamp']).astimezone(UTC)
            if observed_time == requested_time:
                return {
                    'station_id': station_id,
                    'feature_timestamp': observed_time.isoformat(),
                    'target_timestamp': datetime.fromisoformat(row['target_timestamp']).astimezone(UTC).isoformat(),
                    'horizon_minutes': horizon_minutes,
                    'prediction_departures': max(0, round(float(row['y_pred']), 2)),
                    'observed_departures': float(row['y_true']),
                    'absolute_error': abs(float(row['residual'])),
                    'model': row['model'],
                    'split': row['split'],
                    'simulation_type': 'retrospective held-out backtest',
                }
            if observed_time > requested_time:
                break
    return None


@lru_cache(maxsize=8)
def _sha256_file(path: str, modified_ns: int, size: int) -> str:
    del modified_ns, size
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _benchmark_artifact_status(horizon_minutes: int) -> tuple[bool, str, dict[str, object]]:
    experiment_root = REPO_ROOT / 'models' / 'experiments' / 'benchmark_v2'
    horizon_root = experiment_root / f'{horizon_minutes}m'
    metadata_path = horizon_root / 'metadata.json'
    summary_path = experiment_root / 'summary.json'
    dataset_path = REPO_ROOT / 'data' / 'gold' / 'forecasting' / f'forecasting_dataset_{horizon_minutes}m.csv.gz'
    contract_path = REPO_ROOT / 'data' / 'gold' / 'forecasting' / 'forecasting_contract.json'
    report_path = REPO_ROOT / 'data' / 'gold' / 'forecasting' / 'forecasting_dataset_report.json'
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        current_dataset_hash = _sha256_file(str(dataset_path), dataset_path.stat().st_mtime_ns, dataset_path.stat().st_size)
        current_contract_hash = _sha256_file(str(contract_path), contract_path.stat().st_mtime_ns, contract_path.stat().st_size)
        report = json.loads(report_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return False, 'forecast datasets or benchmark metadata are missing or invalid; build the forecasting contract and benchmark first', {}
    horizon_summary = summary.get('horizons', {}).get(str(horizon_minutes), {})
    if summary.get('status') != 'PASS' or summary.get('comparability') != 'COMPARABLE':
        return False, 'benchmark v2 is blocked or is not marked comparable; see its experiment report', metadata
    if metadata.get('canonical_data_hash') != AUTHORITATIVE_GOLD_HASH:
        return False, 'benchmark metadata references an unexpected canonical data version', metadata
    if metadata.get('forecasting_dataset_hash') != current_dataset_hash:
        return False, 'benchmark predictions were generated from a different forecasting dataset; rebuild the benchmark', metadata
    if metadata.get('dataset_contract_hash') != current_contract_hash:
        return False, 'benchmark predictions use a different forecast contract; rebuild the benchmark', metadata
    if metadata.get('forecasting_dataset_hash') != current_dataset_hash or summary.get('dataset_contract_hash') != current_contract_hash:
        return False, 'benchmark v2 references different forecasting inputs; rebuild the benchmark', metadata
    if summary.get('dataset_artifact_hashes', {}).get(str(horizon_minutes)) != current_dataset_hash:
        return False, 'benchmark v2 data hashes do not match the current dataset; rebuild the benchmark', metadata
    dataset_report = report.get('horizons', {}).get(str(horizon_minutes), {})
    if (
        report.get('source_gold', {}).get('authoritative_hash') != AUTHORITATIVE_GOLD_HASH
        or dataset_report.get('source_gold_hash') != AUTHORITATIVE_GOLD_HASH
    ):
        return False, 'forecast report does not match the current canonical dataset; rebuild the forecasting contract and benchmark', metadata
    metadata['test_period'] = dataset_report.get('split_boundaries', {})
    prediction_path = horizon_root / 'lightgbm.csv.gz'
    if not prediction_path.is_file():
        return False, 'benchmark v2 predictions are missing; build the benchmark first', metadata
    if int(horizon_summary.get('common_population_rows', 0)) <= 0:
        return False, 'benchmark v2 does not contain a validated common test population', metadata
    return True, 'current', metadata


@app.get('/api/v1/research/replay')
def historical_simulation(
    station_id: str,
    feature_timestamp: datetime,
    horizon_minutes: int = Query(default=60, ge=60, le=120, multiple_of=60),
    provider_id: str = 'bicimad',
    network_id: str = 'madrid',
) -> dict[str, object]:
    """Replay a test-period forecast and reveal its subsequently observed outcome."""
    if feature_timestamp.tzinfo is None:
        raise HTTPException(status_code=422, detail='feature_timestamp must include a timezone offset')
    if (provider_id, network_id) != ('bicimad', 'madrid'):
        raise HTTPException(status_code=404, detail='Legacy replay is specific to BiciMAD Madrid; use this provider\'s published forecast exports')
    artifacts_current, artifact_reason, _metadata = _benchmark_artifact_status(horizon_minutes)
    if not artifacts_current:
        raise HTTPException(status_code=503, detail=artifact_reason)
    try:
        prediction_path = REPO_ROOT / 'models' / 'experiments' / 'benchmark_v2' / f'{horizon_minutes}m' / 'lightgbm.csv.gz'
        prediction_stat = prediction_path.stat()
        result = _simulation_row(
            horizon_minutes,
            station_id,
            feature_timestamp.astimezone(UTC).isoformat(),
            prediction_stat.st_mtime_ns,
            prediction_stat.st_size,
        )
    except (OSError, EOFError, csv.Error, ValueError) as exc:
        raise HTTPException(status_code=503, detail='simulation artifact could not be read') from exc
    if result is None:
        artifact = REPO_ROOT / 'models' / 'experiments' / 'benchmark_v2' / f'{horizon_minutes}m' / 'lightgbm.csv.gz'
        if not artifact.is_file():
            raise HTTPException(status_code=503, detail='historical simulation artifact is not available; build the benchmark first')
        raise HTTPException(status_code=404, detail='no held-out prediction exists for that station and timestamp')
    return {
        **result,
        'research_label': 'Historical backtest replay — prediction generated for this past time using the recorded feature set. The observed outcome is shown for evaluation. This is not a live forecast.',
        'model_evidence': 'Benchmark v2 evaluates LightGBM and deterministic baselines on a shared test population. This replay is historical evidence, not a live forecast or a production model promotion.',
        'missing_hour_semantics': 'unobserved station-hours are unknown, not zero demand',
    }


@app.get('/api/v1/research/evaluation')
def analytics_evaluation(horizon_minutes: int = Query(default=60, ge=60, le=120, multiple_of=60), provider_id: str = 'bicimad', network_id: str = 'madrid') -> dict[str, object]:
    """Read the archived BiciMAD benchmark; provider models are published separately."""
    if (provider_id, network_id) != ('bicimad', 'madrid'):
        raise HTTPException(status_code=404, detail='Legacy evaluation is specific to BiciMAD Madrid')
    artifacts_current, artifact_reason, metadata = _benchmark_artifact_status(horizon_minutes)
    if not artifacts_current:
        raise HTTPException(status_code=503, detail=artifact_reason)
    summary_path = REPO_ROOT / 'models' / 'experiments' / 'benchmark_v2' / 'summary.json'
    try:
        payload = json.loads(summary_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail='historical evaluation artifact is not available') from exc
    horizon_report = payload.get('horizons', {}).get(str(horizon_minutes), {})
    model_metrics = horizon_report.get('metrics', {})
    return {
        'horizon_minutes': horizon_minutes,
        'model': 'LightGBM',
        'model_version': metadata.get('experiment_id'),
        'dataset_hash': metadata.get('forecasting_dataset_hash'),
        'evaluation_period': 'chronological held-out test split',
        'benchmark_status': payload.get('status'),
        'comparability_note': 'Benchmark v2 compares LightGBM and deterministic baselines on the same chronological test rows. Results describe this historical population and do not establish production readiness.',
        'mae': model_metrics.get('lightgbm', {}).get('mae'),
        'rmse': model_metrics.get('lightgbm', {}).get('rmse'),
        'r2': model_metrics.get('lightgbm', {}).get('r2'),
        'n_predictions': model_metrics.get('lightgbm', {}).get('n_predictions'),
        'prediction_coverage': model_metrics.get('lightgbm', {}).get('coverage'),
        'common_population_rows': horizon_report.get('common_population_rows'),
        'models': model_metrics,
        'test_period': metadata.get('test_period'),
    }


@app.get('/api/v1/forecast')
def forecast(horizon_minutes: int = Query(default=60, ge=30, le=120)) -> dict[str, object]:
    del horizon_minutes
    raise HTTPException(status_code=410, detail='live forecast serving is not part of this historical analytics release; use /api/v1/research/replay for held-out historical replay')


@app.get('/api/v1/model/info')
def model_info(session: SessionDependency) -> dict[str, object]:
    value = session.scalars(select(ModelRegistry).order_by(ModelRegistry.created_at.desc())).first()
    if value is None or value.status.lower() != 'validated':
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
    value = session.scalars(
        select(ModelRegistry).where(ModelRegistry.status == 'validated').order_by(ModelRegistry.created_at.desc())
    ).first()
    return json.loads(value.metrics_json) if value else {'status': 'unavailable', 'reason': 'no validated production model'}
