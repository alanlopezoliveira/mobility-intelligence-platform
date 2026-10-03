import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type { AuditReport, Profile } from './audit-types'
import './AuditApp.css'
import type { Report } from './ProjectApp'
import CurrentAuditApp from './CurrentAuditApp'
import { InventoryEvidence, WeatherEvidence } from './EvidenceAnalysis'

const fmt = new Intl.NumberFormat('en-GB')
const dec = (n: number, digits = 2) => n.toLocaleString('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: digits })
const pct = (n: number) => `${dec(n * 100, 1)}%`
const day = (s: string) => s.slice(0, 10)
const dateLabel = (s: string) => new Date(`${day(s)}T12:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
const weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
type View = 'overview' | 'occupancy' | 'model' | 'replay' | 'alerts' | 'findings' | 'weather'
const pages: { id: View; title: string; icon: string; description: string }[] = [
  { id: 'overview', title: 'Evidence overview', icon: '01', description: 'A measured picture of the local BiciMAD project.' },
  { id: 'occupancy', title: 'Station occupancy', icon: '02', description: 'Docked bikes, empty stations and full stations in the source snapshots.' },
  { id: 'model', title: 'Model comparison', icon: '03', description: 'Validation-selected models and ten candidates on identical evaluation rows.' },
  { id: 'replay', title: 'Prediction vs reality', icon: '04', description: 'Explore held-out predictions against their recorded outcomes.' },
  { id: 'alerts', title: 'Empty & full events', icon: '05', description: 'What the evidence can—and cannot—tell us about empty or full stations.' },
  { id: 'findings', title: 'Methods & limitations', icon: '06', description: 'Data contracts, evaluation scope and research limitations.' },
]
pages.push({ id: 'weather', title: 'Rain & usage', icon: '07', description: 'Monthly means, rainy-day counts and sensitivity to timing and intensity.' })
const dataUrl = (path: string) => `${import.meta.env.BASE_URL}audit-data/${path}`
async function loadJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(dataUrl(path), { signal })
  if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('Audit export is missing. Run python scripts/build_audit_report.py from the repository root, then reload.')
  return response.json() as Promise<T>
}
function Card({ title, eyebrow, children, className = '' }: { title?: string; eyebrow?: string; children: ReactNode; className?: string }) {
  return <section className={`a-card ${className}`}>{eyebrow && <p className="a-eyebrow">{eyebrow}</p>}{title && <h2>{title}</h2>}{children}</section>
}
function Kpi({ label, value, note, tone }: { label: string; value: ReactNode; note: string; tone?: string }) {
  return <div className={`a-kpi ${tone ?? ''}`}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}
function Notice({ children, danger = false }: { children: ReactNode; danger?: boolean }) {
  return <div className={`a-notice ${danger ? 'danger' : ''}`}>{children}</div>
}
function Bars({ points, metric, dimension, color = 'teal', percent = false }: { points: Profile[]; metric: keyof Profile; dimension: 'hour' | 'weekday' | 'month'; color?: string; percent?: boolean }) {
  const max = Math.max(.001, ...points.map(p => Number(p[metric] ?? 0)))
  return <div className="a-bars" aria-label={`${String(metric)} by ${dimension}`}>
    {points.map((p, i) => { const v = Number(p[metric] ?? 0); const label = dimension === 'weekday' ? weekdays[p.weekday ?? i] : dimension === 'hour' ? `${p.hour}:00` : p.month
      return <div className="a-bar-column" key={label} tabIndex={0} title={`${label}: ${percent ? pct(v) : dec(v)} · ${fmt.format(p.samples ?? p.rows ?? 0)} observations`}><strong className="a-bar-value">{percent ? `${dec(v * 100, 2)}%` : dec(v, 2)}</strong><div className="a-bar-track"><div className={`a-bar ${color}`} style={{ height: `${v / max * 100}%` }} /></div><span>{dimension !== 'hour' || i % 3 === 0 ? label : ''}</span></div>
    })}
  </div>
}
type ChartPoint = { time: string; actual: number | null; predicted?: number }
function TimeChart({ points, unit, wallTime = false, modelName = '' }: { points: ChartPoint[]; unit: string; wallTime?: boolean; modelName?: string }) {
  const [selected, setSelected] = useState(0)
  const chosen = points[Math.min(selected, points.length - 1)]
  const time = (v: string) => Date.parse(wallTime && !/[Z+]\d*$/.test(v) ? `${v}Z` : v)
  const first = points.length ? time(points[0].time) : 0
  const last = points.length ? time(points[points.length - 1].time) : 1
  const values = points.flatMap(p => [p.actual ?? 0, p.predicted ?? 0])
  const max = Math.max(unit === '%' ? 100 : 1, ...values)
  const min = Math.min(0, ...values)
  const y = (n: number) => 224 - (n - min) / (max - min) * 190
  const x = (p: ChartPoint) => 48 + (time(p.time) - first) / Math.max(last - first, 1) * 870
  const path = (key: 'actual' | 'predicted') => points.map((p, i) => {
    const value = p[key]
    if (value === undefined || value === null) return ''
    const previous = points[i - 1]
    const gap = !previous || previous[key] == null || time(p.time) - time(previous.time) > (wallTime ? 90 : 60) * 60_000
    return `${gap ? 'M' : 'L'}${x(p).toFixed(2)},${y(value).toFixed(2)}`
  }).join(' ')
  if (!points.length) return <div className="a-empty">No observed rows in this window. Missing hours are not zero.</div>
  return <div className="a-time-chart">
    <div className="a-chart-legend"><span><i className="actual" />Observed</span>{points.some(p => p.predicted != null) && <span><i className="predicted" />{modelName} prediction</span>}<span>{unit} · {wallTime ? 'source wall time' : 'UTC'}</span></div>
    <svg viewBox="0 0 950 265" role="img" aria-label={`Observed ${unit}${points[0].predicted != null ? ' and predicted departures' : ''} over the selected dates. Gaps are not connected.`}>
      {[0, 1, 2, 3, 4].map(i => { const v = min + (max - min) * i / 4; return <g key={i}><line x1="48" x2="920" y1={y(v)} y2={y(v)} className="a-grid-line" /><text x="38" y={y(v) + 4} textAnchor="end">{dec(v, 0)}</text></g> })}
      <path d={path('actual')} className="a-actual-line" /><path d={path('predicted')} className="a-predicted-line" />
      <line x1={x(chosen)} x2={x(chosen)} y1="25" y2="228" className="a-cursor" />
      {chosen.actual != null && <circle cx={x(chosen)} cy={y(chosen.actual)} r="4" className="a-actual-dot" />}
      <text x="48" y="253">{day(points[0].time)}</text><text x="920" y="253" textAnchor="end">{day(points[points.length - 1].time)}</text>
    </svg>
    <label className="a-scrubber">Inspect observation <input type="range" min="0" max={points.length - 1} value={Math.min(selected, points.length - 1)} onChange={e => setSelected(Number(e.target.value))} /></label>
    <div className="a-chart-readout"><strong>{chosen.time.replace('T', ' ').slice(0, 16)}</strong><span>Observed: <b>{chosen.actual == null ? 'not available' : dec(chosen.actual)} {unit}</b></span>{chosen.predicted != null && <span>Predicted: <b>{dec(chosen.predicted)} {unit}</b></span>}<small>{fmt.format(points.length)} observations · gaps preserved</small></div>
  </div>
}

export function Occupancy({ data }: { data: Pick<AuditReport, 'occupancy'> }) {
  const o = data.occupancy
  const stations = useMemo(() => [...o.station_stats].sort((a, b) => Number(a.station) - Number(b.station)), [o])
  const [station, setStation] = useState(stations[0]?.station ?? '')
  const [analysisStation, setAnalysisStation] = useState('all')
  const [series, setSeries] = useState<ChartPoint[]>([])
  const [start, setStart] = useState(''); const [end, setEnd] = useState('')
  const [error, setError] = useState(''); const [loading, setLoading] = useState(true)
  const [event, setEvent] = useState<'empty' | 'full'>('empty')
  useEffect(() => {
    const controller = new AbortController()
    loadJson<{ rows: [string, number | null, number | null, number | null][] }>(`occupancy-${station}.json`, controller.signal).then(payload => {
      const points = payload.rows.map(([time, bikes, capacity]) => ({ time, actual: bikes != null && capacity != null && capacity > 0 && bikes >= 0 && bikes <= capacity ? bikes / capacity * 100 : null }))
      setSeries(points); const last = day(points.at(-1)?.time ?? o.end)
      setEnd(last); setStart(new Date(Date.parse(last) - 6 * 86_400_000).toISOString().slice(0, 10)); setLoading(false); setError('')
    }).catch((e: unknown) => { if (!controller.signal.aborted) { setError(String(e)); setLoading(false) } })
    return () => controller.abort()
  }, [station, o.end])
  const filtered = series.filter(p => day(p.time) >= start && day(p.time) <= end)
  const worst = [...stations].sort((a, b) => b[`${event}_rate`] - a[`${event}_rate`]).slice(0, 15)
  const total = o.events[event]
  return <>
    <InventoryEvidence station={analysisStation} onStationChange={value => {setAnalysisStation(value); if (value !== 'all' && value !== station) {setStation(value);setLoading(true);setSeries([])} }} />
    <div className="a-kpis"><Kpi label="Exactly empty samples" value={fmt.format(o.empty_samples)} note={`${pct(o.empty_samples / o.valid_rows)} of valid inventory rows`} /><Kpi label="Exactly full samples" value={fmt.format(o.full_samples)} note={`${pct(o.full_samples / o.valid_rows)} of valid inventory rows`} /><Kpi label="Typical observation gap" value={`${dec(o.gap_minutes.median, 1)} min`} note={`${fmt.format(o.gap_minutes.over_90_minutes)} gaps exceed 90 minutes`} /><Kpi label="Unavailable-state samples" value={fmt.format(o.unavailable_values['1'] ?? 0)} note="Recorded no_available = 1" /></div>
    <Card title="A station's observed inventory"><div className="a-controls"><label>Inventory station<select value={station} onChange={e => { setStation(e.target.value); setAnalysisStation(e.target.value); setLoading(true); setSeries([]) }}>{stations.map(s => <option value={s.station} key={s.station}>{s.station} · {s.name}</option>)}</select></label><label>From<input type="date" value={start} min={day(series[0]?.time ?? o.start)} max={end} onChange={e => setStart(e.target.value)} /></label><label>Through<input type="date" value={end} min={start} max={day(series.at(-1)?.time ?? o.end)} onChange={e => setEnd(e.target.value)} /></label><span className="a-badge">Observed · not predicted</span></div>
      {error ? <Notice danger>{error}</Notice> : loading ? <p role="status">Loading observed inventory…</p> : <TimeChart key={station} points={filtered} unit="%" wallTime />}
      <p className="a-caption">dock_bikes ÷ total_bases. Source clock, timezone unverified. Invalid values and gaps are not interpolated.</p>
    </Card>
    <div className="a-two-col"><Card title="Occupancy distribution" eyebrow="All valid extracted inventory observations"><div className="a-histogram">{o.distribution.map((b, i) => <div key={i}><span>{dec(b.count / o.valid_rows * 100, 2)}%<br />{fmt.format(b.count)}</span><div style={{ height: `${b.count / Math.max(...o.distribution.map(v => v.count)) * 140}px` }} /><small>{Math.round(b.from * 100)}–{Math.round(b.to * 100)}%</small></div>)}</div><p className="a-caption">Left-closed, right-open bins; the last bin includes 100%. Sample weighted, not time weighted.</p></Card><Card title="Empty and full by weekday" eyebrow="Source wall time · sample frequencies"><div className="a-toggle"><button className={event === 'empty' ? 'selected' : ''} onClick={() => setEvent('empty')}>Empty</button><button className={event === 'full' ? 'selected' : ''} onClick={() => setEvent('full')}>Full</button></div><Bars points={o.profiles.weekday} dimension="weekday" metric={`${event}_rate`} percent color="orange" /></Card></div>
    <Card title={`Stations most often ${event}`} eyebrow="Ranked by fraction of observed samples, not hours of operation"><div className="a-table-wrap"><table><thead><tr><th>Station</th><th>{event} samples</th><th>Observed rate</th><th>Event runs</th><th>Valid samples</th><th>Unavailable flag</th></tr></thead><tbody>{worst.map(s => <tr key={s.station}><td><button className="a-table-link" onClick={() => { if (station !== s.station) { setStation(s.station); setAnalysisStation(s.station); setLoading(true); setSeries([]) } window.scrollTo({ top: 0, behavior: 'smooth' }) }}>{s.station} · {s.name}</button></td><td>{fmt.format(s[`${event}_samples`])}</td><td>{pct(s[`${event}_rate`])}</td><td>{fmt.format(s[`${event}_runs`])}</td><td>{fmt.format(s.valid_rows)}</td><td>{s.unavailable_samples == null ? 'Not available' : pct(s.unavailable_samples / s.rows)}</td></tr>)}</tbody></table></div></Card>
    <Notice><strong>Durations are observed spans.</strong> {fmt.format(total.runs)} {event} runs; {fmt.format(total.singleton_runs)} have one sample. Median observed span: {dec(total.median_span_minutes)} minutes. A run ends at a different/invalid reading or a gap over 90 minutes. True start/end times and continuous outage duration are unknown.</Notice>
  </>
}

type CurrentPrediction = { issue: string; target: string; y: number; prediction: number }
function Replay({ report }: { report: Report }) {
  const [horizon, setHorizon] = useState(60)
  const [station, setStation] = useState(String(report.stations[0].station))
  const [from, setFrom] = useState('2022-12-01')
  const [state, setState] = useState<{key: string; rows: CurrentPrediction[]; error: string}>({key:'',rows:[],error:''})
  const key = `${horizon}-${station}`
  const model = report.models.find(m => m.horizon_minutes === horizon)!
  useEffect(() => { const controller = new AbortController(); fetch(`/project-data/predictions-${key}.json`,{signal:controller.signal,cache:'no-store'}).then(r => {if(!r.ok) throw new Error('Selected-model predictions unavailable'); return r.json()}).then(rows => setState({key,rows,error:''})).catch(e => {if(!controller.signal.aborted) setState({key,rows:[],error:e.message})}); return () => controller.abort() },[key])
  const begin = Date.parse(from+'T00:00:00Z')
  const rows = state.key === key ? state.rows.filter(r => Date.parse(r.target) >= begin && Date.parse(r.target) < begin + 7*86400000) : []
  const mae = rows.length ? rows.reduce((sum,r) => sum+Math.abs(r.y-r.prediction),0)/rows.length : null
  return <Card title={`${model.selected_model} · prediction vs reality`}><div className="a-controls"><label>Demand station<select value={station} onChange={e => setStation(e.target.value)}>{report.stations.map(s => <option key={s.station} value={s.station}>{s.station} · {s.name}</option>)}</select></label><label>Replay horizon<select value={horizon} onChange={e => setHorizon(Number(e.target.value))}><option value={60}>+60 minutes</option><option value={120}>+120 minutes</option></select></label><label>Seven days starting<input type="date" value={from} min="2022-11-01" max="2022-12-25" onChange={e => setFrom(e.target.value)} /></label></div>{state.key === key && state.error ? <p role="alert">{state.error}</p> : state.key !== key ? <p role="status">Loading selected-model predictions…</p> : <TimeChart points={rows.map(r => ({time:r.target,actual:r.y,predicted:r.prediction}))} unit="departures" modelName={model.selected_model} />}<p>{rows.length} evaluated station-hours · window MAE {mae == null ? '—' : dec(mae,4)}. Target-hour starts in UTC; the selected model predicts departures during that hour. +60/+120 minutes are the lead to the hour start.</p><p className="a-caption">The chart uses the same selected-model exports as the project dashboard. Historical test observations; no occupancy prediction is implied.</p></Card>
}

export function Alerts({ data }: { data: Pick<AuditReport, 'occupancy'> }) {
  const [kind, setKind] = useState<'empty' | 'full'>('empty'); const o = data.occupancy
  const events = o.events[kind].longest
  return <>
    <div className="a-alert-status"><div className="a-alert-icon">!</div><div><p className="a-eyebrow">Model-based availability alerts</p><h2>Not available</h2><p>The saved model predicts departures. It cannot say when a station will run empty or fill up.</p></div><span className="a-badge orange">Capability gap · A05</span></div>
    <div className="a-two-col"><Card title="A clear future alert rule"><div className="a-threshold"><span>Predicted occupancy &lt; 10%</span><strong>Lack risk</strong></div><div className="a-threshold"><span>Predicted occupancy &gt; 90%</span><strong>Overfill risk</strong></div><p>These are proposed thresholds only. No prediction is evaluated against them because no predicted occupancy exists. Alert counts, precision, recall and lead time are <strong>not available</strong>.</p></Card><Card title="What can be observed now"><dl className="a-definition"><dt>Observed near-empty samples</dt><dd>{fmt.format(o.near_empty_samples)}</dd><dt>Observed near-full samples</dt><dd>{fmt.format(o.near_full_samples)}</dd><dt>Last inventory date</dt><dd>{dateLabel(o.end)}</dd></dl><p className="a-caption">Historical docked-bike ratios; not model output. A full-bike ratio and a zero-free-dock state are not always equivalent, since some bases may be reserved or unavailable.</p></Card></div>
    <Card title="Longest observed historical runs" eyebrow="Retrospective events · not live alerts"><div className="a-toggle"><button className={kind === 'empty' ? 'selected' : ''} onClick={() => setKind('empty')}>Exactly empty</button><button className={kind === 'full' ? 'selected' : ''} onClick={() => setKind('full')}>Exactly full</button></div><div className="a-event-list">{events.map((e, i) => { const station = o.station_stats.find(s => s.station === e.station); return <article key={`${e.station}-${e.start}`}><span className="a-event-number">{String(i + 1).padStart(2,'0')}</span><div><h3>Station {e.station} · {station?.name ?? 'Name unavailable'}</h3><p>{e.start.slice(0,16).replace('T',' ')} — {e.end.slice(0,16).replace('T',' ')} · source clock</p><small>{fmt.format(e.samples)} consecutive samples, with no gap greater than 90 minutes</small></div><strong>{dec(e.span_minutes / 60, 1)}<small>observed hours</small></strong></article> })}</div><Notice>Observed spans are not exact outage durations. Long empty runs may reflect station service state or sensor behavior; this panel makes no redistribution recommendation.</Notice></Card>
  </>
}

export default function AuditApp({ view }: { view: View }) {
  const [data, setData] = useState<AuditReport | null>(null); const [report, setReport] = useState<Report | null>(null); const [error, setError] = useState('')
  useEffect(() => { const controller = new AbortController(); Promise.all([loadJson<AuditReport>('report.json', controller.signal), fetch('/project-data/report.json',{signal:controller.signal,cache:'no-store'}).then(r => {if(!r.ok) throw new Error('Project report unavailable'); return r.json() as Promise<Report>})]).then(([audit,current]) => {setData(audit);setReport(current)}).catch((e: unknown) => { if (!controller.signal.aborted) setError(String(e)) }); return () => controller.abort() }, [])
  const page = pages.find(p => p.id === view)!
  return <div className="audit-app">
    <main className="a-main" id="audit-main"><header className="a-header"><div><p className="a-eyebrow">MADRID · BIKE-SHARING RESEARCH</p><h1>{page.title}</h1><p>{page.description}</p></div><div className="a-header-tag">Research evidence<span>{report ? `Published ${dateLabel(report.generated_at)}` : 'Reading local export'}</span></div></header><div className="a-content">

      {error ? <Card title="Audit evidence is not available"><Notice danger>{error}</Notice><p>The app never substitutes demo data. Generate the local export and reload this page.</p><button className="a-button" onClick={() => location.reload()}>Reload evidence</button></Card> : !data || !report ? <Card title="Reading the audit evidence"><p role="status">Loading local data and verified model results…</p></Card> : view === 'overview' || view === 'model' || view === 'findings' ? <CurrentAuditApp report={report} view={view} /> : view === 'occupancy' ? <Occupancy data={data} /> : view === 'replay' ? <Replay report={report} /> : view === 'weather' ? <WeatherEvidence /> : <><InventoryEvidence /><Alerts data={data} /></>}
      <footer className="a-footer"><span>Independent research · BiciMAD / EMT Madrid open data</span><span>Historical evidence. No live service or operational advice.</span></footer></div></main></div>
}
