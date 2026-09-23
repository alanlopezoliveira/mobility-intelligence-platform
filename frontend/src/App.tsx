import { useCallback, useEffect, useState } from 'react'
import './App.css'
import { API_BASE_URL, fetchStation, type DashboardData, type Station, fetchDashboard } from './api'

function App() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedStation, setSelectedStation] = useState<Station | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await fetchDashboard())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to reach the API')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (loading) {
    return <main className="state-screen"><span className="spinner" /> Loading live mobility data</main>
  }

  if (error || !data) {
    return (
      <main className="state-screen error-state">
        <h1>API connection unavailable</h1>
        <p>{error ?? 'The dashboard could not load its data.'}</p>
        <button onClick={() => void load()}>Retry</button>
        <small>{API_BASE_URL}</small>
      </main>
    )
  }

  const highRisk = data.risks.filter((item) => item.risk === 'HIGH').length
  const availableBikes = data.stations.reduce((total, station) => total + (station.available_bikes ?? 0), 0)
  const metricMae = typeof data.metrics.mae === 'number' ? data.metrics.mae.toFixed(3) : 'n/a'

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">{data.config.default_provider} / live API</p>
          <h1>Operations overview</h1>
        </div>
        <div className="api-badge"><span className="status-dot" /> {API_BASE_URL}</div>
      </header>

      <section className="kpi-grid">
        <article className="kpi-card primary"><span>Stations loaded</span><strong>{data.stations.length}</strong></article>
        <article className="kpi-card warning"><span>High risk stations</span><strong>{highRisk}</strong></article>
        <article className="kpi-card success"><span>Available bikes</span><strong>{availableBikes}</strong></article>
        <article className="kpi-card neutral"><span>Model MAE</span><strong>{metricMae}</strong></article>
      </section>

      <section className="dashboard-grid">
        <div className="panel map-panel">
          <h2>Station network</h2>
          {data.stations.length === 0 ? <p className="empty-state">No station records are persisted yet.</p> : (
            <div className="station-grid">
              {data.stations.slice(0, 24).map((station) => <span className="station-dot" title={station.name ?? station.station_id} key={station.station_id} />)}
            </div>
          )}
        </div>
        <div className="panel alerts-panel">
          <h2>Model status</h2>
          <p className={`model-status ${data.model.status}`}>{data.model.status}</p>
          <p>{data.model.reason ?? data.model.feature_definition ?? 'Model metadata loaded from the API.'}</p>
          <p className="empty-state">{data.forecast ? `Forecast horizon: ${data.forecast.horizon_minutes} minutes` : 'Forecast unavailable: no validated temporal model.'}</p>
          <h2>Recommendations</h2>
          {data.recommendations.length === 0 ? <p className="empty-state">No recommendations returned.</p> : <ul>{data.recommendations.map((item, index) => <li key={index}>{JSON.stringify(item)}</li>)}</ul>}
        </div>
      </section>

      <section className="panel table-panel">
        <div className="section-heading"><h2>Stations</h2><button onClick={() => void load()}>Refresh</button></div>
        {data.stations.length === 0 ? <p className="empty-state">The API returned no stations.</p> : (
          <table><thead><tr><th>Station</th><th>Capacity</th><th>Available</th><th>Risk</th></tr></thead><tbody>
            {data.stations.map((station) => {
              const stationRisk = data.risks.find((risk) => risk.station_id === station.station_id)
              return <tr key={station.station_id}><td><button className="station-link" onClick={() => void fetchStation(station.station_id).then(setSelectedStation).catch(() => setError('Unable to load station details'))}>{station.name ?? station.station_id}</button></td><td>{station.capacity ?? 'n/a'}</td><td>{station.available_bikes ?? 'n/a'}</td><td className={`risk ${(stationRisk?.risk ?? 'unknown').toLowerCase()}`}>{stationRisk?.risk ?? 'UNKNOWN'}</td></tr>
            })}
          </tbody></table>
        )}
        {selectedStation && <p className="detail-state">Selected: {selectedStation.name ?? selectedStation.station_id} · {selectedStation.address ?? 'No address'}</p>}
      </section>
    </div>
  )
}

export default App
