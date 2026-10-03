import { providerQuery } from './dataSource'
export type ProfilePoint = {
  bucket: number
  observed_rows: number
  mean_departures: number
  total_departures: number
  mean_arrivals: number
}

export type StationSummary = { station_id: string; value: number; observed_rows: number }

export type DemandPoint = {
  observed_at: string
  departures: number
  arrivals: number
  observed_station_hours: number
}

export type DatasetSummary = {
  observed_station_hours: number
  stations_with_observations: number
  first_observation: string | null
  last_observation: string | null
  observed_departures: number
  observed_arrivals: number
  timezone: string
  missing_hour_semantics: string
}

export type Simulation = {
  station_id: string
  feature_timestamp: string
  target_timestamp: string
  horizon_minutes: number
  prediction_departures: number
  observed_departures: number
  absolute_error: number
  model: string
  split: string
  simulation_type: string
  model_evidence: string
  missing_hour_semantics: string
}

export type Evaluation = {
  horizon_minutes: number
  model: string
  evaluation_period: string
  benchmark_status: string
  comparability_note: string
  mae: number | null
  rmse: number | null
  r2: number | null
  n_predictions: number | null
  prediction_coverage: number | null
  test_period?: { test_start?: string; test_end?: string }
  common_population_rows?: number | null
  models?: Record<string, { mae: number | null; rmse: number | null; r2: number | null; n_predictions: number; coverage: number }>
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

async function get<T>(path: string): Promise<T> {
  const query = providerQuery()
  const scoped = query.size ? `${path}${path.includes('?') ? '&' : '?'}${query}` : path
  const response = await fetch(`${API_BASE_URL}${scoped}`)
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json() as { detail?: string }
      detail = body.detail ?? detail
    } catch {
      detail = response.statusText
    }
    throw new Error(`${response.status}: ${detail}`)
  }
  return response.json() as Promise<T>
}

export async function fetchSummary(): Promise<DatasetSummary> {
  return get('/api/v1/analytics/summary')
}

export async function fetchProfile(dimension: 'hour' | 'weekday' | 'week' | 'month', stationId?: string): Promise<ProfilePoint[]> {
  const params = new URLSearchParams({ dimension })
  if (stationId) params.set('station_id', stationId)
  return get(`/api/v1/analytics/profile?${params.toString()}`)
}

export async function fetchStations(): Promise<StationSummary[]> {
  return get('/api/v1/analytics/stations?limit=40&metric=departures')
}

export async function fetchSeries(start: string, end: string, stationId?: string): Promise<DemandPoint[]> {
  const params = new URLSearchParams({ start, end, limit: '5000' })
  if (stationId) params.set('station_id', stationId)
  return get(`/api/v1/analytics/series?${params.toString()}`)
}

export async function fetchSimulation(stationId: string, featureTimestamp: string, horizon: number): Promise<Simulation> {
  const params = new URLSearchParams({
    station_id: stationId,
    feature_timestamp: featureTimestamp,
    horizon_minutes: String(horizon),
  })
  return get(`/api/v1/research/replay?${params.toString()}`)
}

export async function fetchEvaluation(horizon: number): Promise<Evaluation> {
  return get(`/api/v1/research/evaluation?horizon_minutes=${horizon}`)
}

export { API_BASE_URL }
