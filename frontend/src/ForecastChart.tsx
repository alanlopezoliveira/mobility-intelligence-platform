import { useState } from 'react'

export type Prediction = { issue: string; target: string; y: number; prediction: number; recent: number; day: number; week: number; mean_24h: number; mean_7d: number; movements_24h: number }
const fmt = (n: number) => n.toLocaleString('en-GB', { maximumFractionDigits: 3 })
const clock = (s: string) => s.slice(0, 16).replace('T', ' ') + ' UTC'

export default function ForecastChart({ rows, title, from }: { rows: Prediction[]; title: string; from: string }) {
  const [point, setPoint] = useState(0)
  const [baseline, setBaseline] = useState(false)
  const row = rows[Math.min(point, rows.length - 1)]
  const times = rows.map(p => Date.parse(p.target))
  const first = Date.parse(from + 'T00:00:00Z'), last = first + 167 * 3600000
  const x = (time: number) => 48 + (time - first) / Math.max(3600000, last - first) * 904
  const maximum = Math.max(1, ...rows.flatMap(p => [p.y, p.prediction, ...(baseline ? [p.day] : [])])) * 1.1
  const path = (field: 'y' | 'prediction' | 'day') => rows.map((p, i) => `${i && times[i] - times[i - 1] === 3600000 ? 'L' : 'M'}${x(times[i])},${240 - p[field] / maximum * 205}`).join(' ')
  const mae = (field: 'prediction' | 'recent' | 'day' | 'week') => rows.reduce((s, p) => s + Math.abs(p.y - p[field]), 0) / rows.length
  const bestBaseline = [{ name: 'Last completed hour', error: mae('recent') }, { name: 'Same hour yesterday', error: mae('day') }, { name: 'Same hour last week', error: mae('week') }].sort((a, b) => a.error - b.error)[0]
  const actual = rows.reduce((s, p) => s + p.y, 0), predicted = rows.reduce((s, p) => s + p.prediction, 0)
  const inactive = rows.filter(p => p.movements_24h === 0).length
  return <>
    <div className="forecast-metrics">
      <div><span>Recorded departures</span><strong>{fmt(actual)}</strong></div>
      <div><span>Predicted departures</span><strong>{fmt(predicted)}</strong></div>
      <div><span>Model MAE</span><strong>{fmt(mae('prediction'))}</strong></div>
      <div><span>Yesterday MAE</span><strong>{fmt(mae('day'))}</strong></div>
    </div>
    <p>{rows.length} of 168 target hours have forecasts. Missing hours remain gaps. Last-hour MAE: {fmt(mae('recent'))}; last-week MAE: {fmt(mae('week'))}. Lower MAE is better.</p>
    {mae('prediction') > bestBaseline.error && <p className="forecast-notice">{bestBaseline.name} is more accurate in this window (MAE {fmt(bestBaseline.error)}). The model was selected using the full validation period; it does not win for every station or week.</p>}
    {inactive > 0 && <p className="forecast-notice">{inactive} forecasts were issued after 24 hours with no recorded departures or arrivals at this station. This can reflect inactivity or missing station data; operational status is unknown.</p>}
    <label className="forecast-baseline"><input type="checkbox" checked={baseline} onChange={e => setBaseline(e.target.checked)} /> Show same-hour-yesterday baseline (dashed)</label>
    <svg className="project-chart" viewBox="0 0 980 285" role="img" aria-label={title}>
      <title>{title}</title>
      {[0, .5, 1].map(p => <g key={p}><line x1="48" x2="952" y1={240 - p * 205} y2={240 - p * 205} stroke="#e6ebe9" /><text x="38" y={244 - p * 205} textAnchor="end">{fmt(maximum * p)}</text></g>)}
      <path d={path('y')} fill="none" stroke="#087f72" strokeWidth="2.5" />
      <path d={path('prediction')} fill="none" stroke="#df8755" strokeWidth="2" />
      {baseline && <path d={path('day')} fill="none" stroke="#65748c" strokeWidth="1.5" strokeDasharray="6 4" />}
      <line x1={x(Date.parse(row.target))} x2={x(Date.parse(row.target))} y1="30" y2="240" stroke="#65748c" strokeDasharray="3 3" />
      <text x="48" y="271">{clock(new Date(first).toISOString())}</text><text x="952" y="271" textAnchor="end">{clock(new Date(last).toISOString())}</text>
    </svg>
    <label className="forecast-scrubber">Inspect an hourly forecast<input type="range" min="0" max={rows.length - 1} value={Math.min(point, rows.length - 1)} onChange={e => setPoint(Number(e.target.value))} /></label>
    <p className="forecast-readout" aria-live="polite">Target hour starting <strong>{clock(row.target)}</strong> · issued {clock(row.issue)}<br />Recorded: <strong>{fmt(row.y)}</strong> · predicted: <strong>{fmt(row.prediction)}</strong> · yesterday: {fmt(row.day)} · absolute error: {fmt(Math.abs(row.y - row.prediction))}</p>
    <details><summary>Exact values for all {rows.length} forecasts</summary><div className="project-table-wrap"><table><thead><tr><th>Target hour (UTC)</th><th>Issue time (UTC)</th><th>Recorded</th><th>Predicted</th><th>Yesterday</th><th>Error</th></tr></thead><tbody>{rows.map(p => <tr key={p.target}><td>{clock(p.target)}</td><td>{clock(p.issue)}</td><td>{p.y}</td><td>{fmt(p.prediction)}</td><td>{p.day}</td><td>{fmt(Math.abs(p.y - p.prediction))}</td></tr>)}</tbody></table></div></details>
  </>
}
