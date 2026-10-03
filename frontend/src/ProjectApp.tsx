import { projectDataUrl } from './dataSource'
import { useEffect, useMemo, useState } from 'react'
import './ProjectApp.css'
import { WeatherEvidence } from './EvidenceAnalysis'
import type { Model } from './ModelComparison'

import ForecastChart, { type Prediction } from './ForecastChart'
export type Report = {
  provider?: { id: string; name: string; network_id: string; city: string; timezone: string; attribution: string; catalog_url: string }
  generated_at: string; scope: string; year: number; departures: number; arrivals: number; rows: number; hours: number; missing_network_hours: number; observed_stations: number; modeled_stations: number
  stations: { station: number | string; export_key?: string; name: string; latitude: number; longitude: number }[]
  sources?: { source: string }[]
  models: Model[]; assumptions: string[]; fixes: string[]; quality: Record<string, number>; timings: Record<string, number>
  weather: { source: string; documentation: string; hours: number; grid_latitude: number; grid_longitude: number; spatial_policy: string; requested: { latitude: number; longitude: number }; sha256: string }
  association: { rainy_hours: number; dry_hours: number; rainy_mean: number; dry_mean: number; raw_change_pct: number; adjusted_change_pct: number; matched_rainy_mean: number; matched_dry_mean: number; matched_rainy_hours: number; adjustment: string; caveat: string; monthly: { month: number; wet: boolean; mean: number; hours: number }[]; daily: { date: string; departures: number; rain: number; temperature: number; hours: number }[] }
}
const fmt = (n: number, digits = 0) => n.toLocaleString('en-GB', { maximumFractionDigits: digits })
const date = (s: string) => s.slice(0, 10)
const EMPTY_PREDICTIONS: Prediction[] = []

function Lines({ values, second, labels, title }: { values: number[]; second?: number[]; labels: string[]; title: string }) {
  const maximum = Math.max(1, ...values, ...(second || [])) * 1.1
  const path = (items: number[]) => items.map((v, i) => `${i ? 'L' : 'M'}${48 + i / Math.max(1, items.length - 1) * 904},${240 - v / maximum * 205}`).join(' ')
  return <svg className="project-chart" viewBox="0 0 980 285" role="img" aria-label={title}>
    <title>{title}</title>
    {[0, 0.5, 1].map(p => <g key={p}><line x1="48" x2="952" y1={240 - p * 205} y2={240 - p * 205} stroke="#e6ebe9" /><text x="38" y={244 - p * 205} textAnchor="end">{fmt(maximum * p)}</text></g>)}
    <path d={path(values)} fill="none" stroke="#087f72" strokeWidth="2.5" />
    {second && <path d={path(second)} fill="none" stroke="#df8755" strokeWidth="2" />}
    <text x="48" y="271">{labels[0]}</text><text x="952" y="271" textAnchor="end">{labels.at(-1)}</text>
  </svg>
}

export default function ProjectApp({ section = 'overview' }: { section?: 'overview' | 'weather' | 'forecasts' | 'quality' }) {
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState('')
  const [horizon, setHorizon] = useState(60)
  const [station, setStation] = useState('')
  const [from, setFrom] = useState('2022-12-01')
  const [predictionState, setPredictionState] = useState<{ key: string; rows: Prediction[]; error: string }>({ key: '', rows: [], error: '' })
  const predictionKey = `${horizon}-${station}`
  const predictions = predictionState.key === predictionKey ? predictionState.rows : EMPTY_PREDICTIONS
  const predictionError = predictionState.key === predictionKey ? predictionState.error : ''
  const [adjusted, setAdjusted] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    fetch(projectDataUrl('report.json'), { signal: controller.signal }).then(r => { if (!r.ok) throw new Error('Run python scripts/rebuild_project.py to generate the project data.'); return r.json() }).then((data: Report) => { setReport(data); setFrom(`${data.year}-12-01`); setStation(String(data.stations[0]?.station || '')) }).catch(e => { if (e.name !== 'AbortError') setError(String(e.message)) })
    return () => controller.abort()
  }, [])
  useEffect(() => {
    if (!station) return
    const controller = new AbortController()
    fetch(projectDataUrl(`predictions-${horizon}-${report?.stations.find(s => String(s.station) === station)?.export_key || station}.json`), { signal: controller.signal }).then(r => { if (!r.ok) throw new Error('Prediction export is unavailable.'); return r.json() }).then(rows => setPredictionState({ key: `${horizon}-${station}`, rows, error: '' })).catch(e => { if (e.name !== 'AbortError') setPredictionState({ key: `${horizon}-${station}`, rows: [], error: e.message }) })
    return () => controller.abort()
  }, [station, horizon, report])
  const selected = useMemo(() => {
    const start = Date.parse(from + 'T00:00:00Z')
    return predictions.filter(p => Date.parse(p.target) >= start && Date.parse(p.target) < start + 7 * 86400000)
  }, [predictions, from])
  if (error) return <main className="project-loading"><h1>Project data unavailable</h1><p>{error}</p><a href="#methods">Open methods and limitations</a></main>
  if (!report) return <main className="project-loading">Loading the rebuilt project…</main>
  const r = report, a = r.association, model = r.models.find(m => m.horizon_minutes === horizon)!
  const change = adjusted ? a.adjusted_change_pct : a.raw_change_pct
  const dry = adjusted ? a.matched_dry_mean : a.dry_mean
  const wet = adjusted ? a.matched_rainy_mean : a.rainy_mean
  const selectedMae = selected.length ? selected.reduce((sum, p) => sum + Math.abs(p.y - p.prediction), 0) / selected.length : null
  return <div className="project-app" data-section={section}>

    <main>
      <section id="overview" className="project-hero"><div><div className="project-eyebrow">{r.provider?.name || 'BiciMAD'} · RESEARCH PIPELINE · {r.year}</div><h1>A clearer picture<br />of how {r.provider?.city || 'Madrid'} rides.</h1><p>Recorded journeys, historical weather, and forecasts you can inspect. Every result is tied to its configured source.</p><div className="project-pills"><span>● Historical data</span><span>Hourly resolution</span><span>Reproducible models</span></div></div><aside className="hero-note"><span className="project-eyebrow">THE WEATHER QUESTION</span><strong>{fmt(Math.abs(change), 1)}% {change < 0 ? 'fewer' : 'more'}</strong><p>recorded departures in rainy hours, compared with {adjusted ? 'matched' : 'all'} dry hours.</p><a href="#weather">Explore the comparison ↓</a><small>Descriptive association · city-scale ERA5 reanalysis</small></aside></section>
      <div className="project-kpis"><article><span>Recorded departures</span><strong>{fmt(r.departures)}</strong><small>{r.sources?.length ?? 12} source files in the published run</small></article><article><span>Observed station IDs</span><strong>{fmt(r.observed_stations)}</strong><small>{r.modeled_stations} eligible for stable panel</small></article><article><span>Weather hours</span><strong>{fmt(r.weather.hours)}</strong><small>{r.weather.source}</small></article><article><span>+{horizon}m holdout MAE</span><strong>{fmt(model.metrics[0].mae, 3)}</strong><small>Departures per station-hour</small></article></div>
      <section id="network" className="project-section"><div className="section-heading"><div><span className="project-eyebrow">01 / THE NETWORK</span><h2>A year of recorded journeys</h2></div><span className="project-badge">{r.year} · {r.provider?.timezone || 'Europe/Madrid'} local days</span></div><div className="project-card"><div className="chart-caption"><strong>Daily departures</strong><span>Network totals · partial days retained and flagged in exports</span></div><Lines values={a.daily.map(d => d.departures)} labels={a.daily.map(d => d.date)} title={`Daily recorded departures in ${r.year}`} /><p className="project-footnote">{fmt(r.hours)} network hours with recorded departures. {r.missing_network_hours} UTC hours are outside coverage or have no recorded departures; these are excluded from modeling and comparison.</p></div></section>
      <section id="weather" className="project-section"><div className="section-heading"><div><span className="project-eyebrow">02 / WEATHER & USAGE</span><h1 className="page-title">Does rain change how much people ride?</h1><p>Rainy means at least 0.1 mm of rain during the trip hour.</p></div><div className="project-toggle" aria-label="Weather comparison"><button aria-pressed={adjusted} onClick={() => setAdjusted(true)}>Matched hours</button><button aria-pressed={!adjusted} onClick={() => setAdjusted(false)}>Raw comparison</button></div></div>
        <div className="weather-grid"><article className="project-card weather-comparison"><div className="chart-caption"><strong>Mean departures per network hour</strong></div>{[{ label: 'Dry', value: dry, color: '#b6d8cf' }, { label: 'Rainy', value: wet, color: '#087f72' }].map(item => <div className="weather-bar" key={item.label}><span>{item.label}</span><div><i style={{ width: `${item.value / Math.max(dry, wet) * 100}%`, background: item.color }} /></div><strong>{fmt(item.value, 1)}</strong></div>)}<div className="weather-result">{change > 0 ? '+' : ''}{fmt(change, 1)}% <span>associated difference</span></div><p>{adjusted ? a.adjustment : 'All rainy hours compared with all dry hours, without controlling for calendar differences.'}</p><small>{fmt(a.rainy_hours)} rainy hours · {fmt(a.dry_hours)} dry hours · {fmt(a.matched_rainy_hours)} matched rainy hours</small></article>
        <article className="project-card"><span className="project-eyebrow">REAL LOCATION. REAL TIME.</span><h3>{r.provider?.city || 'Madrid'}, matched hour by hour</h3><dl><dt>Request coordinates</dt><dd>{r.weather.requested.latitude.toFixed(4)}, {r.weather.requested.longitude.toFixed(4)}</dd><dt>Returned ERA5 cell</dt><dd>{r.weather.grid_latitude}, {r.weather.grid_longitude}</dd><dt>Time alignment</dt><dd>UTC joins; rain interval ends at the next hour</dd><dt>Source</dt><dd><a href={r.weather.documentation} target="_blank" rel="noreferrer">Open-Meteo / Copernicus ERA5 ↗</a></dd></dl><p className="project-footnote">The requested center comes from trip-origin coordinates. One ~25 km grid cell represents city weather; it does not measure rain at every station. CC BY 4.0.</p></article></div>
        <WeatherEvidence />
      </section>
      <section id="forecasts" className="project-section"><div className="section-heading"><div><span className="project-eyebrow">03 / FORECAST EXPLORER</span><h1 className="page-title">Predictions that can be checked.</h1><p>Historical replay of separate hourly forecasts, each using data available at its issue time. This is not one seven-day forecast. Target intervals and date filters use UTC.</p></div></div><div className="project-card"><div className="project-controls"><label>Station<select value={station} onChange={e => setStation(e.target.value)}>{r.stations.map(s => <option key={s.station} value={s.station}>{s.station} · {s.name}</option>)}</select></label><label>Lead to target hour<select value={horizon} onChange={e => setHorizon(Number(e.target.value))}><option value={60}>+60 minutes</option><option value={120}>+120 minutes</option></select></label><label>Seven days starting<input type="date" min={`${r.year}-11-01`} max={`${r.year}-12-25`} value={from} onChange={e => setFrom(e.target.value)} /></label></div><div className="chart-caption"><strong><span className="legend-dot actual" /> Actual <span className="legend-dot predicted" /> Predicted</strong><span>{selectedMae === null ? 'No selected observations' : `${selected.length} hours · selection MAE ${fmt(selectedMae, 3)}`}</span></div>{predictionError ? <p role="alert">{predictionError}</p> : selected.length ? <ForecastChart key={`${predictionKey}-${from}`} rows={selected} from={from} title={`Actual versus predicted departures for station ${station}, lead ${horizon} minutes`} /> : <p className="project-empty">{predictions.length ? 'No observations in this date range.' : 'Loading station predictions…'}</p>}<p className="project-footnote">Selected model: {model.selected_model || model.metrics[0].model}. Predictions are expected counts and may be fractional. Recorded zeros mean no recorded departure, not confirmed station availability. The model forecasts departures, not bikes available. An empty/full station alert needs a separately validated inventory forecast and is not generated from these counts.</p></div>
      <p><a className="project-link" href="#models">Compare all models and evaluation metrics →</a></p></section>
      <section id="quality" className="project-section"><div className="section-heading"><div><span className="project-eyebrow">04 / DATA & METHODS</span><h1 className="page-title">Better results start with visible assumptions.</h1></div><a className="project-link" href={projectDataUrl('report.json')} download>Download provenance JSON ↓</a></div><div className="weather-grid"><article className="project-card"><h3>Data and model contract</h3><ul className="project-checks">{['Separate departure and arrival timestamps', 'Hourly weather with source provenance', 'Completed-hour lags and explicit issue times', 'Ten candidates; validation selection with simplicity preference', 'Nonnegative predictions, scored consistently', 'Serialized models with reload equality checks', 'Identical test rows across candidates', 'Content-validated caches'].map(f => <li key={f}>{f}</li>)}</ul><p>Execution: {fmt(r.timings.total_seconds, 1)} seconds for the published run. Validated monthly caches and weather responses are reused; unchanged models skip retraining.</p></article><article className="project-card"><h3>Data quality counters</h3><dl>{Object.entries(r.quality).map(([key, value]) => <div className="quality-row" key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{fmt(value)}</dd></div>)}</dl><p className="project-footnote">Counters cover the source files before the reference-year filter. Each event is validated independently. Accepted plus rejected events equals unique nonblank rows for both departures and arrivals. Blank and duplicate rows are counted separately.</p></article></div><details className="project-card"><summary>Read all assumptions and limitations</summary><ol>{r.assumptions.map(text => <li key={text}>{text}</li>)}</ol></details><p className="project-footnote">{r.scope}. The audit uses the same selected-model report and forecast exports.</p></section>
    </main><footer><strong>mobility lab</strong><span>{r.provider?.attribution || 'Official BiciMAD open data'} · Open-Meteo / Copernicus · Generated {date(r.generated_at)}</span><a href="#methods">Methods & limitations →</a></footer>
  </div>
}
