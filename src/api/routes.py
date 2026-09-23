from fastapi import APIRouter

router = APIRouter(prefix='/api/v1')


@router.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@router.get('/stations')
def stations() -> list[dict[str, str | int | float]]:
    return [{"station_id": "1", "name": "Station 1", "capacity": 30, "latitude": 40.42, "longitude": -3.7}]
