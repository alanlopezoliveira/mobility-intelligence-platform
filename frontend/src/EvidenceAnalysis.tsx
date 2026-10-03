import { datasetId, projectDataUrl } from './dataSource'
import { useEffect, useState } from 'react'
import './EvidenceAnalysis.css'

type Group = { period?: string; service?: string; samples: number; empty: number; full: number; empty_pct?: number; full_pct?: number }
type Inventory = { start: string; end: string; samples: number; empty: number; full: number; periods: Group[]; services: Group[]; cross: Group[]; method: string; stations: { station: string; name: string; samples: number; empty: number; full: number; empty_night: number; full_night: number; empty_flagged: number; full_flagged: number; groups: Group[] }[] }
type WeatherMonth = { month: number; rainy_hours: number; dry_hours: number; rainy_days: number; rainy_mean: number | null; dry_mean: number; rainy_total: number; dry_total: number; raw_change_pct: number | null; adjusted_change_pct: number | null; matched_hours: number; matched_dry_mean: number | null; matched_rainy_mean: number | null; rainy_daytime_pct: number | null; dry_daytime_pct: number; rainy_temperature: number | null; dry_temperature: number; leave_one_day_out_min: number | null; leave_one_day_out_max: number | null; thresholds: { threshold_mm: number; hours: number; days: number; adjusted_change_pct: number | null }[]; rainy_dates: { date: string; hours: number; rain_mm: number; departures: number }[] }
type Diagnostics = { generated_at: string; inventory: Inventory; weather: WeatherMonth[]; failures: {source: string; reason: string}[] }
const n = (v: number | null | undefined, digits = 2) => v == null ? '—' : v.toLocaleString('en-GB', { maximumFractionDigits: digits, minimumFractionDigits: digits })
const monthName = (m: number) => new Date(2022, m - 1, 1).toLocaleString('en', { month: 'short' })
function useDiagnostics(weatherOnly = false) {
  const [data, setData] = useState<Diagnostics | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { const controller = new AbortController(); fetch(weatherOnly && datasetId() ? projectDataUrl('diagnostics.json') : '/analysis-data/diagnostics.json', { signal: controller.signal, cache: 'no-store' }).then(r => { if (!r.ok || !r.headers.get('content-type')?.includes('application/json')) throw new Error('Analysis unavailable. Run python -m scripts.analyze_project_evidence.'); return r.json() }).then(setData).catch(e => { if (!controller.signal.aborted) setError(String(e.message)) }); return () => controller.abort() }, [weatherOnly])
  return { data, error }
}
function RateBars({ groups, event }: { groups: Group[]; event: 'empty' | 'full' }) {
  return <div className="evidence-bars">{groups.map(row => { const rate = row[event] / row.samples * 100; return <div className="evidence-bar-row" key={`${row.period}-${row.service}`}><span>{row.period || row.service}</span><div><i style={{ width: `${rate}%` }} /></div><strong>{n(rate)}%</strong><small>{n(row[event], 0)} / {n(row.samples, 0)} samples</small></div> })}</div>
}
export function InventoryEvidence({ station: controlledStation, onStationChange }: { station?: string; onStationChange?: (station: string) => void } = {}) {
  const { data, error } = useDiagnostics()
  const [event, setEvent] = useState<'empty' | 'full'>('empty')
  const [localStation, setLocalStation] = useState('all')
  const station = controlledStation ?? localStation
  const setStation = (value: string) => { setLocalStation(value); onStationChange?.(value) }
  if (!data) return <section className="evidence-panel"><p role={error ? 'alert' : 'status'}>{error || 'Loading station-state analysis…'}</p></section>
  const d = data.inventory
  const selected = d.stations.find(s => s.station === station)
  const cross = selected?.groups || d.cross
  const total = selected?.[event] ?? d[event]
  const flagged = selected?.[`${event}_flagged`] ?? d.services.find(g => g.service === 'Flagged inactive/unavailable')?.[event] ?? 0
  const collapse = (key: 'service' | 'period') => { const sums = new Map<string, Group>(); for (const g of cross) { const label = g[key]!; const row = sums.get(label) || { [key]: label, samples: 0, empty: 0, full: 0 }; row.samples += g.samples; row.empty += g.empty; row.full += g.full; sums.set(label, row) } return [...sums.values()] }
  const active = cross.filter(g => g.service === 'Flagged active/available')
  return <section className="evidence-panel" id="inventory-analysis"><h2>When are stations empty or full?</h2><p>Observed snapshots: {d.start.slice(0,10)} to {d.end.slice(0,10)}. This inventory sample has a different coverage period from the 2022 trip model.</p><div className="evidence-controls"><label>Inventory analysis station<select value={station} onChange={e => setStation(e.target.value)}><option value="all">All observed stations</option>{[...d.stations].sort((a,b) => Number(a.station)-Number(b.station)).map(s => <option key={s.station} value={s.station}>{s.station} · {s.name}</option>)}</select></label><label>Inventory state<select value={event} onChange={e => setEvent(e.target.value as 'empty' | 'full')}><option value="empty">Exactly empty</option><option value="full">Exactly full</option></select></label></div>
    <p><strong>{n(total, 0)} {event} samples; {n(total ? flagged / total * 100 : null)}% carry an inactive/unavailable flag.</strong> A flag describes the reported service state, not the reason for it. Night is defined as 00:00–05:59 in the source clock.</p>
    <h3>Rates by time of day</h3><RateBars groups={collapse('period')} event={event} />
    <h3>Rates by reported service state</h3><RateBars groups={collapse('service')} event={event} />
    <h3>Night versus day among active/available samples</h3><RateBars groups={active} event={event} />{!active.length && <p>No active/available observations for this selection; a service-normalized night/day comparison is unavailable.</p>}
    <p>Compare rates rather than raw counts: day/evening covers 18 hours and night covers 6. The service-state comparison helps distinguish recorded closures from empty/full readings while a station is flagged available. These observations cannot establish whether maintenance, rebalancing or rider movements caused a particular event.</p>
    <details><summary>Joint counts and denominator details</summary><div className="evidence-table"><table><thead><tr><th>Period</th><th>Service flags</th><th>Samples</th><th>Empty</th><th>Empty %</th><th>Full</th><th>Full %</th></tr></thead><tbody>{cross.map(g => <tr key={`${g.period}-${g.service}`}><td>{g.period}</td><td>{g.service}</td><td>{n(g.samples,0)}</td><td>{n(g.empty,0)}</td><td>{n(g.empty/g.samples*100)}</td><td>{n(g.full,0)}</td><td>{n(g.full/g.samples*100)}</td></tr>)}</tbody></table></div><p>{d.method}</p></details>
    {data.failures.length > 0 && <p role="alert">{data.failures.length} source extraction failures; analysis is partial.</p>}
  </section>
}
export function WeatherEvidence() {
  const { data, error } = useDiagnostics(true)
  const [adjusted, setAdjusted] = useState(false)
  const [month, setMonth] = useState(7)
  useEffect(() => { if (data && location.hash === '#monthly-weather') document.getElementById('monthly-weather')?.scrollIntoView() }, [data])
  if (!data) return <section className="evidence-panel"><p role={error ? 'alert' : 'status'}>{error || 'Loading monthly weather analysis…'}</p></section>
  const selected = data.weather.find(m => m.month === month)!
  const maximum = Math.max(1, ...data.weather.flatMap(m => [m.rainy_mean || 0, m.dry_mean, m.matched_dry_mean || 0, m.matched_rainy_mean || 0]))
  return <section className="evidence-panel" id="monthly-weather"><h3>Monthly rain comparison · departures per network hour</h3><p>These bars show an average per hour, not total trips or numbers of rainy days. Means are shown to two decimal places. A few busy rainy hours can have a higher average than hundreds of dry hours.</p><div className="evidence-controls"><label>Monthly comparison<select value={adjusted ? 'matched' : 'raw'} onChange={e => setAdjusted(e.target.value === 'matched')}><option value="raw">Raw hourly means</option><option value="matched">Matched month, hour and weekend</option></select></label></div>
    <div className="evidence-month-scroll"><div className="evidence-months">{data.weather.map(m => <div key={m.month}><div className="evidence-month-pair">{[{label:'Dry', value:adjusted ? m.matched_dry_mean : m.dry_mean}, {label:'Rain', value:adjusted ? m.matched_rainy_mean : m.rainy_mean}].map((b,i) => <div key={b.label}><strong>{n(b.value)}</strong><i style={{height:`${(b.value || 0)/maximum*145}px`,background:i?'#087f72':'#b6d8cf'}} /><span>{b.label}</span></div>)}</div><b>{monthName(m.month)}</b><small>{m.rainy_hours} wet hours<br/>{m.rainy_days} wet days</small></div>)}</div></div>
    <p>Rain ≥ 0.1 mm in the trip hour, from one ERA5 grid cell. A “wet day” has at least one such hour; it does not mean the whole day was rainy. Matched bars use only eligible rainy hours and their dry calendar reference.</p>
    <div className="evidence-table"><table><thead><tr><th>Month</th><th>Wet / dry hours</th><th>Wet days</th><th>Rain trips / dry trips</th><th>Raw difference</th><th>Matched difference</th></tr></thead><tbody>{data.weather.map(m => <tr key={m.month}><td>{monthName(m.month)}</td><td>{m.rainy_hours} / {m.dry_hours}</td><td>{m.rainy_days}</td><td>{n(m.rainy_total,0)} / {n(m.dry_total,0)}</td><td>{n(m.raw_change_pct)}%</td><td>{n(m.adjusted_change_pct)}%</td></tr>)}</tbody></table></div>
    <h3>Summer and small-sample sensitivity</h3><label>Inspect weather month <select value={month} onChange={e => setMonth(Number(e.target.value))}>{data.weather.map(m => <option key={m.month} value={m.month}>{monthName(m.month)}</option>)}</select></label>
    <p>{monthName(month)} has {selected.rainy_hours} rainy hours across {selected.rainy_days} days. The raw difference is {n(selected.raw_change_pct)}%; matching hour and weekday/weekend within the month gives {n(selected.adjusted_change_pct)}%. Rainy hours fall in 06:00–23:59 {n(selected.rainy_daytime_pct)}% of the time, versus {n(selected.dry_daytime_pct)}% for dry hours.</p>
    <p>Removing one rainy day at a time gives matched differences from {n(selected.leave_one_day_out_min)}% to {n(selected.leave_one_day_out_max)}%. This is a sensitivity range, not a confidence interval. Temperature averages {n(selected.rainy_temperature)}°C in wet hours and {n(selected.dry_temperature)}°C in dry hours; temperature and other confounders are not controlled by this calendar match.</p>
    <div className="evidence-table"><table><thead><tr><th>Rain threshold</th><th>Eligible hours</th><th>Days</th><th>Matched difference</th></tr></thead><tbody>{selected.thresholds.map(t => <tr key={t.threshold_mm}><td>≥ {t.threshold_mm} mm</td><td>{t.hours}</td><td>{t.days}</td><td>{t.adjusted_change_pct == null ? 'Insufficient observations' : `${n(t.adjusted_change_pct)}%`}</td></tr>)}</tbody></table></div><p>Few wet days, storm timing, rain intensity and temperature can affect these comparisons. A positive value does not show that rain caused more cycling. No monthly causal effect is established.</p>
    <details><summary>Rainy dates in {monthName(month)}</summary><div className="evidence-table"><table><thead><tr><th>Local date</th><th>Wet hours</th><th>Rain in wet hours (mm)</th><th>Trips in wet hours</th></tr></thead><tbody>{selected.rainy_dates.map(d => <tr key={d.date}><td>{d.date}</td><td>{d.hours}</td><td>{n(d.rain_mm)}</td><td>{n(d.departures,0)}</td></tr>)}</tbody></table></div></details>
  </section>
}
