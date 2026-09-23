export type Config = {
  app_name: string
  default_language: string
  default_provider: string
  default_forecast_horizon_minutes: number
}

export type Station = {
  station_id: string
  name: string | null
  address: string | null
  capacity: number | null
  available_bikes: number | null
  latitude: number | null
  longitude: number | null
}

export type Risk = {
  station_id: string
  risk: string
  availability_ratio: number | null
}

export type ModelInfo = {
  model_name: string | null
  model_version?: string
  status: string
  reason?: string
  feature_definition?: string
  metrics?: Record<string, unknown>
}

export type Forecast = {
  horizon_minutes: number
  predictions: unknown[]
}

export type DashboardData = {
  health: { status: string }
  config: Config
  stations: Station[]
  risks: Risk[]
  model: ModelInfo
  history: Array<Record<string, string | number>>
  recommendations: Array<Record<string, string>>
  metrics: Record<string, unknown>
  forecast: Forecast | null
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`)
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`${response.status}: ${detail || response.statusText}`)
  }
  return response.json() as Promise<T>
}

export async function fetchDashboard(): Promise<DashboardData> {
  const [health, config, stations, risks, model, history, recommendations, metrics] = await Promise.all([
    checkHealth(),
    get<Config>('/api/v1/config'),
    get<Station[]>('/api/v1/stations?limit=100'),
    get<Risk[]>('/api/v1/risk'),
    get<ModelInfo>('/api/v1/model/info'),
    get<Array<Record<string, string | number>>>('/api/v1/history?limit=20'),
    get<Array<Record<string, string>>>('/api/v1/optimization/recommendations'),
    get<Record<string, unknown>>('/api/v1/metrics'),
  ])
  const forecast = await get<Forecast>('/api/v1/forecast').catch(() => null)
  return { health, config, stations, risks, model, history, recommendations, metrics, forecast }
}

export async function checkHealth(): Promise<{ status: string }> {
  return get<{ status: string }>('/health')
}

export async function fetchStation(stationId: string): Promise<Station> {
  return get<Station>(`/api/v1/stations/${encodeURIComponent(stationId)}`)
}

export { API_BASE_URL }
