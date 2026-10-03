import './EvidenceAnalysis.css'
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import './App.css'
import {
  API_BASE_URL,
  fetchEvaluation,
  fetchProfile,
  fetchSeries,
  fetchSimulation,
  fetchStations,
  fetchSummary,
  type DatasetSummary,
  type DemandPoint,
  type Evaluation,
  type ProfilePoint,
  type Simulation,
  type StationSummary,
} from './api'

const number = new Intl.NumberFormat('en', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('en', { maximumFractionDigits: 2 })
const weekdayLabels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const monthLabels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function dateInput(value: Date) {
  return value.toISOString().slice(0, 10)
}

function localDateTimeInput(value: string) {
  const date = new Date(value)
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function displayDate(value: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleDateString(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' })
}

function LineChart({ points, metric = 'departures' }: { points: DemandPoint[]; metric?: 'departures' | 'arrivals' }) {
  const [hovered, setHovered] = useState<number | null>(null)
  const values = points.slice(-168)
  if (!values.length) return <div className="chart-empty">No observations in this date range.</div>
  const max = Math.max(1, ...values.map((point) => point[metric]))
  const chartPoints = values.map((point, index) => {
    const x = 12 + (index / Math.max(values.length - 1, 1)) * 676
    const y = 174 - (point[metric] / max) * 150
    const previous = index > 0 ? new Date(values[index - 1].observed_at).getTime() : null
    const current = new Date(point.observed_at).getTime()
    return `${previous !== null && current - previous === 3_600_000 ? 'L' : 'M'}${x},${y}`
  }).join(' ')
  return (
    <div className="chart-wrap">
      <svg className="line-chart" viewBox="0 0 700 200" role="img" aria-label={`Observed ${metric} over time`} onMouseLeave={() => setHovered(null)}>
        {[0, 1, 2, 3].map((step) => <line key={step} x1="12" x2="688" y1={24 + step * 50} y2={24 + step * 50} className="grid-line" />)}
        <path d={chartPoints} className={`chart-line ${metric === 'arrivals' ? 'arrivals-line' : ''}`} />
        {values.map((point, index) => {
          const x = 12 + (index / Math.max(values.length - 1, 1)) * 676
          const y = 174 - (point[metric] / max) * 150
          return <circle key={`${point.observed_at}-${index}`} cx={x} cy={y} r={hovered === index ? 5 : 2.5} className="chart-point" onMouseEnter={() => setHovered(index)}><title>{new Date(point.observed_at).toLocaleString()} · {number.format(point[metric])} {metric}</title></circle>
        })}
      </svg>
      <div className="axis-labels"><span>{new Date(values[0].observed_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', timeZone: 'UTC' })}</span><span>UTC · observed departures</span><span>{new Date(values[values.length - 1].observed_at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', timeZone: 'UTC' })}</span></div>
    </div>
  )
}

function ProfileChart({ points, dimension, selectedBucket, onSelect }: { points: ProfilePoint[]; dimension: 'hour' | 'weekday' | 'week' | 'month'; selectedBucket?: number | null; onSelect?: (bucket: number) => void }) {
  const buckets = dimension === 'hour' ? 24 : dimension === 'weekday' ? 7 : dimension === 'week' ? 53 : 12
  const lookup = new Map(points.map((point) => [point.bucket, point]))
  const labels = (bucket: number) => dimension === 'hour' ? `${String(bucket).padStart(2, '0')}:00` : dimension === 'weekday' ? weekdayLabels[bucket] : dimension === 'week' ? `W${String(bucket).padStart(2, '0')}` : monthLabels[bucket - 1]
  const max = Math.max(1, ...points.map((point) => point.mean_departures))
  return (
    <div className="profile-chart" role="img" aria-label={`Average observed departures by ${dimension}`}>
      {Array.from({ length: buckets }, (_, index) => {
        const bucket = dimension === 'weekday' ? index : dimension === 'week' || dimension === 'month' ? index + 1 : index
        const point = lookup.get(bucket)
        const height = point ? Math.max(2, (point.mean_departures / max) * 100) : 0
    return <button className={`profile-column ${selectedBucket === bucket ? 'selected' : ''}`} key={bucket} type="button" onClick={() => onSelect?.(bucket)} title={point ? `${labels(bucket)} · ${decimal.format(point.mean_departures)} average departures · ${number.format(point.observed_rows)} observed station-hours` : `${labels(bucket)} · no observations`}>
      <strong className="profile-value">{point ? point.mean_departures.toFixed(2) : '—'}</strong><div className="bar-track"><div className="profile-bar" style={{ height: `${height}%` }} /></div>
      <span>{dimension === 'hour' && bucket % 3 !== 0 || dimension === 'week' && bucket % 4 !== 1 ? '' : labels(bucket)}</span>
    </button>
      })}
    </div>
  )
}

function App() {
  const [summary, setSummary] = useState<DatasetSummary | null>(null)
  const [stations, setStations] = useState<StationSummary[]>([])
  const [hourly, setHourly] = useState<ProfilePoint[]>([])
  const [weekly, setWeekly] = useState<ProfilePoint[]>([])
  const [weeksOfYear, setWeeksOfYear] = useState<ProfilePoint[]>([])
  const [monthly, setMonthly] = useState<ProfilePoint[]>([])
  const [series, setSeries] = useState<DemandPoint[]>([])
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null)
  const [simulation, setSimulation] = useState<Simulation | null>(null)
  const [selectedStation, setSelectedStation] = useState('')
  const [rangeStart, setRangeStart] = useState('')
  const [rangeEnd, setRangeEnd] = useState('')
  const [origin, setOrigin] = useState(() => localDateTimeInput('2022-04-14T18:00:00Z'))
  const [horizon, setHorizon] = useState(60)
  const [loading, setLoading] = useState(true)
  const [simulationLoading, setSimulationLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [simulationError, setSimulationError] = useState<string | null>(null)
  const [activePage, setActivePage] = useState<'overview' | 'demand' | 'patterns' | 'outlook' | 'replay'>('overview')
  const [metric, setMetric] = useState<'departures' | 'arrivals'>('departures')
  const [profileDimension, setProfileDimension] = useState<'hour' | 'weekday' | 'week' | 'month'>('hour')
  const [selectedBucket, setSelectedBucket] = useState<number | null>(null)
  const [selectedOutlookHour, setSelectedOutlookHour] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      const [dataSummary, stationList, hourProfile, weekdayProfile, weekProfile, monthProfile] = await Promise.all([
        fetchSummary(), fetchStations(), fetchProfile('hour'), fetchProfile('weekday'), fetchProfile('week'), fetchProfile('month'),
      ])
      setSummary(dataSummary)
      setStations(stationList)
      setHourly(hourProfile)
      setWeekly(weekdayProfile)
      setWeeksOfYear(weekProfile)
      setMonthly(monthProfile)
      void fetchEvaluation(60).then(setEvaluation).catch(() => setEvaluation(null))
      setError(null)
      const defaultStation = stationList.find((station) => station.station_id === '1')?.station_id ?? stationList[0]?.station_id ?? ''
      setSelectedStation((current) => current || defaultStation)
      if (dataSummary.last_observation) {
        const last = new Date(dataSummary.last_observation)
        setRangeEnd(dateInput(new Date(last.getTime() + 86_400_000)))
        setRangeStart(dateInput(new Date(last.getTime() - 13 * 86_400_000)))
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load historical analytics.')
    } finally {
      setLoading(false)
    }
  }, [])

  // Async API loading belongs to the effect; state changes only after the requests settle.
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { void load() }, [load])

  const loadSeries = useCallback(async () => {
    if (!rangeStart || !rangeEnd) return
    try {
      const start = new Date(`${rangeStart}T00:00:00Z`).toISOString()
      const end = new Date(`${rangeEnd}T00:00:00Z`).toISOString()
      const points = await fetchSeries(start, end, selectedStation || undefined)
      setSeries(points)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load this date range.')
    }
  }, [rangeStart, rangeEnd, selectedStation])

  // Keep the chart synchronized with date and station filters via the API.
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { void loadSeries() }, [loadSeries])

  useEffect(() => {
    if (!selectedStation) return
    void Promise.all([
      fetchProfile('hour', selectedStation),
      fetchProfile('weekday', selectedStation),
      fetchProfile('week', selectedStation),
      fetchProfile('month', selectedStation),
    ]).then(([hourProfile, weekdayProfile, weekProfile, monthProfile]) => {
      setHourly(hourProfile)
      setWeekly(weekdayProfile)
      setWeeksOfYear(weekProfile)
      setMonthly(monthProfile)
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Unable to load station patterns.')
    })
  }, [selectedStation])

  const stationName = useMemo(() => stations.find((station) => station.station_id === selectedStation)?.station_id ?? selectedStation, [stations, selectedStation])
  const currentProfile = profileDimension === 'hour' ? hourly : profileDimension === 'weekday' ? weekly : profileDimension === 'week' ? weeksOfYear : monthly
  const outlookHours = useMemo(() => {
    const profile = new Map(hourly.map((point) => [point.bucket, point]))
    const now = new Date()
    now.setUTCMinutes(0, 0, 0)
    return Array.from({ length: 24 }, (_, index) => {
      const time = new Date(now.getTime() + index * 3_600_000)
      const point = profile.get(time.getUTCHours())
      return { time, departures: point?.mean_departures ?? null, arrivals: point?.mean_arrivals ?? null, rows: point?.observed_rows ?? 0 }
    })
  }, [hourly])
  const outlookTotal = outlookHours.reduce((total, hour) => total + (hour.departures ?? 0), 0)
  const outlookPeak = outlookHours.reduce((best, hour) => (hour.departures ?? -1) > (best?.departures ?? -1) ? hour : best, outlookHours[0])

  async function runSimulation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSimulationLoading(true)
    setSimulation(null)
    setSimulationError(null)
    try {
      const localDate = new Date(origin)
      const stationId = selectedStation || stations[0]?.station_id
      if (!stationId || Number.isNaN(localDate.getTime())) throw new Error('Choose a station and a valid date/time.')
      const result = await fetchSimulation(stationId, localDate.toISOString(), horizon)
      setSimulation(result)
      void fetchEvaluation(horizon).then(setEvaluation).catch(() => setEvaluation(null))
    } catch (reason) {
      setSimulationError(reason instanceof Error ? reason.message : 'Simulation unavailable for this station and time.')
    } finally {
      setSimulationLoading(false)
    }
  }

  if (loading) return <main className="state-screen"><span className="spinner" /> Loading the historical dataset</main>
  if (error && !summary) return <main className="state-screen error-state"><h1>Analytics API unavailable</h1><p>{error}</p><button className="button button-dark" onClick={() => { setLoading(true); void load() }}>Retry connection</button><small>Connection: {API_BASE_URL || window.location.origin}</small></main>

  const pageItems = [
    { id: 'overview' as const, icon: '▦', label: 'Overview' },
    { id: 'demand' as const, icon: '⌁', label: 'Demand history' },
    { id: 'patterns' as const, icon: '◷', label: 'Usage patterns' },
    { id: 'outlook' as const, icon: '↗', label: 'Next 24 hours' },
    { id: 'replay' as const, icon: '⟲', label: 'Forecast replay' },
  ]
  const pageMeta = {
    overview: ['Network overview', 'Historical activity for the selected provider and network.'],
    demand: ['Demand history', 'Explore observed departures and arrivals across time.'],
    patterns: ['Usage patterns', 'Compare typical demand across time buckets.'],
    outlook: ['Next 24 hours', 'A forward looking view based on historical hourly averages.'],
    replay: ['Forecast replay', 'Compare a held out prediction with the recorded outcome.'],
  }
  const [pageTitle, pageDescription] = pageMeta[activePage]
  const chosenPattern = currentProfile.find((point) => point.bucket === selectedBucket)
  const activeOutlook = selectedOutlookHour == null ? null : outlookHours[selectedOutlookHour]
  const pageError = error && <p className="inline-error">{error}</p>

  return (
    <div className="app-shell">
      <nav className="explorer-tabs" aria-label="Database explorer sections">{pageItems.map(item => <button key={item.id} aria-current={activePage === item.id ? 'page' : undefined} onClick={() => setActivePage(item.id)}>{item.label}</button>)}</nav>

      <main id="top" className="dashboard-main">
        <header className="dashboard-heading"><div><p className="eyebrow">BICIMAD · HISTORICAL ANALYTICS</p><h1>{pageTitle}</h1><p>{pageDescription}</p></div><div className="heading-actions"><label className="station-filter">Station<select value={selectedStation} onChange={(event) => setSelectedStation(event.target.value)}><option value="">All stations</option>{stations.map((station) => <option key={station.station_id} value={station.station_id}>Station {station.station_id}</option>)}</select></label><span className="date-chip"><i /> {displayDate(summary?.first_observation ?? null)} — {displayDate(summary?.last_observation ?? null)}</span></div></header>

        {activePage === 'overview' && <section className="screen-body overview-screen">
          <div className="stat-strip" aria-label="Dataset summary">
            <div><span className="stat-label">Observed station-hours</span><strong>{number.format(summary?.observed_station_hours ?? 0)}</strong><small>recorded activity rows</small><b className="kpi-mark blue">◷</b></div>
            <div><span className="stat-label">Stations in history</span><strong>{number.format(summary?.stations_with_observations ?? 0)}</strong><small>with recorded observations</small><b className="kpi-mark green">⌖</b></div>
            <div><span className="stat-label">Observed departures</span><strong>{number.format(summary?.observed_departures ?? 0)}</strong><small>trip starts in source data</small><b className="kpi-mark violet">↗</b></div>
            <div><span className="stat-label">Coverage period</span><strong className="date-stat">{displayDate(summary?.first_observation ?? null)} – {displayDate(summary?.last_observation ?? null)}</strong><small>timestamps in UTC</small><b className="kpi-mark orange">▤</b></div>
          </div>
          <div className="overview-grid">
            <article className="panel overview-chart"><div className="panel-heading"><div><h3>Network demand</h3><p>Recorded {metric} · latest observations in selected range</p></div><div className="segmented"><button className={metric === 'departures' ? 'selected' : ''} onClick={() => setMetric('departures')}>Departures</button><button className={metric === 'arrivals' ? 'selected' : ''} onClick={() => setMetric('arrivals')}>Arrivals</button></div></div><LineChart points={series} metric={metric} /><div className="chart-foot"><span>{selectedStation ? `Station ${stationName}` : 'All stations'}</span><button onClick={() => setActivePage('demand')}>Explore demand <span>→</span></button></div></article>
            <article className="panel overview-side"><div className="panel-heading"><div><h3>Typical activity by hour</h3><p>Average recorded departures · UTC</p></div><button className="text-button" onClick={() => setActivePage('patterns')}>Details ↗</button></div><ProfileChart points={hourly} dimension="hour" selectedBucket={selectedBucket} onSelect={setSelectedBucket} /><div className="selected-insight"><span>{chosenPattern ? `${String(chosenPattern.bucket).padStart(2, '0')}:00 UTC average` : 'Select a bar for detail'}</span><strong>{chosenPattern ? decimal.format(chosenPattern.mean_departures) : '—'} <small>trips / observed hour</small></strong></div></article>
          </div>
          <div className="overview-bottom"><article className="panel top-stations"><div className="panel-heading"><div><h3>Top stations</h3><p>Recorded departures in available history</p></div><button className="text-button" onClick={() => setActivePage('demand')}>View all ↗</button></div><div className="station-list">{stations.slice(0, 5).map((station, index) => { const maximum = Math.max(1, ...stations.slice(0, 5).map((item) => item.value)); return <button className="station-list-row" key={station.station_id} onClick={() => { setSelectedStation(station.station_id); setActivePage('demand') }}><span className="rank">{String(index + 1).padStart(2, '0')}</span><span className="station-id">Station {station.station_id}</span><span className="mini-track"><i style={{ width: `${station.value / maximum * 100}%` }} /></span><strong>{number.format(station.value)}</strong></button> })}</div></article>
            <article className="panel data-note"><span className="note-icon">i</span><div><h3>Reading this dashboard</h3><p>Trips are aggregated into observed station-hours. Missing hours stay unknown; historical IDs do not guarantee physical station continuity.</p><a href="#quality">Selected provider data and provenance →</a></div></article></div>
          {pageError}
        </section>}

        {activePage === 'demand' && <section className="screen-body demand-screen">
          <div className="screen-toolbar"><div><span className="toolbar-label">TIME WINDOW</span><label>From<input type="date" value={rangeStart} onChange={(event) => setRangeStart(event.target.value)} /></label><label>Through<input type="date" value={rangeEnd} onChange={(event) => setRangeEnd(event.target.value)} /></label></div><div className="segmented"><button className={metric === 'departures' ? 'selected' : ''} onClick={() => setMetric('departures')}>Departures</button><button className={metric === 'arrivals' ? 'selected' : ''} onClick={() => setMetric('arrivals')}>Arrivals</button></div></div>
          <div className="demand-summary"><div><span>Showing</span><strong>{series.length ? number.format(series.length) : '0'} <small>hourly records</small></strong></div><div><span>Observed {metric}</span><strong>{number.format(series.reduce((sum, point) => sum + point[metric], 0))}</strong></div><div><span>Scope</span><strong>{selectedStation ? `Station ${stationName}` : 'Full network'}</strong></div><p>Dates and chart labels use UTC. Gaps indicate hours without a recorded observation.</p></div>
          <article className="panel demand-chart"><div className="panel-heading"><div><h3>{selectedStation ? `Station ${stationName}` : 'Network'} · {metric}</h3><p>Hourly recorded activity · latest 168 points shown</p></div><span className="legend"><i className={metric === 'arrivals' ? 'arrivals-legend' : ''} /> {metric} · hover points for details</span></div><LineChart points={series} metric={metric} /></article>
          {pageError}
        </section>}

        {activePage === 'patterns' && <section className="screen-body patterns-screen">
          <div className="screen-toolbar"><div><span className="toolbar-label">GROUP BY</span><div className="segmented"><button className={profileDimension === 'hour' ? 'selected' : ''} onClick={() => { setProfileDimension('hour'); setSelectedBucket(null) }}>Hour</button><button className={profileDimension === 'weekday' ? 'selected' : ''} onClick={() => { setProfileDimension('weekday'); setSelectedBucket(null) }}>Weekday</button><button className={profileDimension === 'week' ? 'selected' : ''} onClick={() => { setProfileDimension('week'); setSelectedBucket(null) }}>ISO week</button><button className={profileDimension === 'month' ? 'selected' : ''} onClick={() => { setProfileDimension('month'); setSelectedBucket(null) }}>Month</button></div></div><span className="utc-caption">MEAN DEPARTURES · UTC</span></div>
          <div className="patterns-main-grid"><article className="panel large-pattern"><div className="panel-heading"><div><h3>Demand by {profileDimension === 'hour' ? 'hour of day' : profileDimension === 'weekday' ? 'weekday' : profileDimension === 'week' ? 'week of year' : 'month'}</h3><p>{selectedStation ? `Station ${stationName}` : 'Network average'} · missing station-hours are excluded from the mean</p></div><span className="pattern-icon">◷</span></div><ProfileChart points={currentProfile} dimension={profileDimension} selectedBucket={selectedBucket} onSelect={setSelectedBucket} /><div className="pattern-insight"><span className="insight-tag">{chosenPattern ? 'SELECTED PERIOD' : 'INTERACTIVE CHART'}</span><strong>{chosenPattern ? decimal.format(chosenPattern.mean_departures) : 'Click a bar'} <small>{chosenPattern ? 'average departures' : 'to inspect its average and sample size'}</small></strong><span>{chosenPattern ? `${number.format(chosenPattern.observed_rows)} observed station-hours · ${number.format(chosenPattern.total_departures)} total departures` : 'Bars represent mean departures across observed hours.'}</span></div></article>
            <div className="pattern-side"><article className="panel pattern-stat"><span>Highest observed average</span><strong>{currentProfile.length ? decimal.format(Math.max(...currentProfile.map((point) => point.mean_departures))) : '—'}</strong><small>departures per observed station-hour</small></article><article className="panel pattern-stat"><span>Observed buckets</span><strong>{number.format(currentProfile.length)}</strong><small>periods with source observations</small></article><article className="panel data-note compact-note"><span className="note-icon">i</span><div><h3>Coverage matters</h3><p>Unobserved station-hours are not counted as zero demand.</p></div></article></div></div>
          <article className="panel station-panel"><div className="panel-heading"><div><h3>Top stations</h3><p>Select a station to compare its time profile</p></div><span className="small-caps">RECORDED DEPARTURES</span></div><div className="station-chips">{stations.slice(0, 12).map((station) => <button key={station.station_id} className={selectedStation === station.station_id ? 'selected' : ''} onClick={() => setSelectedStation(station.station_id)}>Station {station.station_id}<b>{number.format(station.value)}</b></button>)}</div></article>
          {pageError}
        </section>}

        {activePage === 'outlook' && <section className="screen-body outlook-screen">
          <div className="outlook-banner"><span className="outlook-icon">↗</span><div><strong>Historical baseline outlook</strong><p>The live 24-hour model forecast is not available in this release. This view repeats the observed average for each UTC hour as a reference, not a trained forecast.</p></div><span className="baseline-badge">REFERENCE ONLY</span></div>
          <div className="outlook-kpis"><article className="panel"><span>24h baseline total</span><strong>{number.format(Math.round(outlookTotal))}</strong><small>sum of historical hourly means</small></article><article className="panel"><span>Highest typical hour</span><strong>{outlookPeak ? `${String(outlookPeak.time.getUTCHours()).padStart(2, '0')}:00` : '—'}</strong><small>{outlookPeak?.departures == null ? 'no observations' : `${decimal.format(outlookPeak.departures)} avg departures`}</small></article><article className="panel"><span>Station scope</span><strong>{selectedStation ? `Station ${stationName}` : 'Network'}</strong><small>change scope in the header</small></article></div>
          <article className="panel outlook-chart"><div className="panel-heading"><div><h3>Typical demand · next 24 UTC hours</h3><p>Bars rotate the historical hour-of-day profile from the current UTC hour. Select a bar to inspect it.</p></div><span className="legend"><i /> average departures</span></div><div className="outlook-bars">{outlookHours.map((hour, index) => { const max = Math.max(1, ...outlookHours.map((item) => item.departures ?? 0)); const height = hour.departures == null ? 0 : Math.max(3, hour.departures / max * 100); return <button key={hour.time.toISOString()} className={`outlook-bar ${selectedOutlookHour === index ? 'selected' : ''}`} onClick={() => setSelectedOutlookHour(index)} title={`${hour.time.toLocaleString()} · ${hour.departures == null ? 'no observations' : `${decimal.format(hour.departures)} average departures`}`}><span className="outlook-value">{hour.departures == null ? '—' : decimal.format(hour.departures)}</span><i style={{ height: `${height}%` }} /><span>{hour.time.getUTCHours() % 3 === 0 ? `${String(hour.time.getUTCHours()).padStart(2, '0')}:00` : ''}</span></button> })}</div></article>
          <div className="outlook-detail panel"><div><span>SELECTED UTC HOUR</span><strong>{activeOutlook ? activeOutlook.time.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) : 'Choose a bar'}</strong></div><div><span>HISTORICAL MEAN</span><strong>{activeOutlook?.departures == null ? '—' : `${decimal.format(activeOutlook.departures)} departures`}</strong></div><div><span>OBSERVED HOURS IN BUCKET</span><strong>{activeOutlook ? number.format(activeOutlook.rows) : '—'}</strong></div><p>Unobserved hours remain unknown. This historical average should not be used for live operations.</p></div>
        </section>}

        {activePage === 'replay' && <section className="screen-body replay-screen">
          <div className="replay-layout"><article className="simulation-form panel"><div className="simulation-number">HELD OUT BACKTEST</div><h3>Choose a test moment</h3><p>This replays an existing chronological test prediction. It does not generate a live forecast.</p><form onSubmit={runSimulation}><label>Station<select value={selectedStation} onChange={(event) => setSelectedStation(event.target.value)} required><option value="">Select a station</option>{stations.map((station) => <option key={station.station_id} value={station.station_id}>Station {station.station_id}</option>)}</select></label><label>Origin · local time<input type="datetime-local" min={evaluation?.test_period?.test_start ? localDateTimeInput(evaluation.test_period.test_start) : undefined} max={evaluation?.test_period?.test_end ? localDateTimeInput(evaluation.test_period.test_end) : undefined} value={origin} onChange={(event) => setOrigin(event.target.value)} required /></label><label>Target horizon<select value={horizon} onChange={(event) => setHorizon(Number(event.target.value))}><option value={60}>+1 hour</option><option value={120}>+2 hours</option></select></label><button className="button button-dark" disabled={simulationLoading}>{simulationLoading ? 'Replaying…' : 'Run replay'} <span>→</span></button></form>{evaluation?.test_period?.test_start && <p className="test-period-note">Test period: {new Date(evaluation.test_period.test_start).toLocaleDateString()} – {evaluation.test_period.test_end ? new Date(evaluation.test_period.test_end).toLocaleDateString() : 'end unavailable'}</p>}{simulationError && <p className="inline-error">{simulationError}</p>}</article>
            <article className="simulation-result panel"><p className="replay-label">Historical backtest replay — prediction generated for this past time using the recorded feature set. The observed outcome is shown for evaluation. This is not a live forecast.</p>{simulation ? <><div className="simulation-number">HELD OUT · {simulation.horizon_minutes} MIN</div><h3>Prediction vs observed</h3><p className="result-time">{new Date(simulation.feature_timestamp).toLocaleString()} → {new Date(simulation.target_timestamp).toLocaleTimeString()}</p><div className="comparison"><div><span>MODEL PREDICTION</span><strong>{decimal.format(simulation.prediction_departures)}</strong><small>departures</small></div><div className="comparison-divider">vs</div><div><span>OBSERVED OUTCOME</span><strong>{number.format(simulation.observed_departures)}</strong><small>departures</small></div></div><div className="error-ribbon"><span>Absolute error</span><strong>{decimal.format(simulation.absolute_error)} departures</strong></div></> : <div className="result-placeholder"><div className="placeholder-orbit"><i /><i /><i /></div><p>Run a replay to compare the model prediction with the recorded outcome.</p></div>}</article></div>
          <div className="evaluation-strip"><div><span>MODEL</span><strong>{evaluation?.model ?? 'Not available'}</strong></div><div><span>TEST MAE</span><strong>{evaluation?.mae == null ? '—' : `${decimal.format(evaluation.mae)}`}</strong></div><div><span>TEST RMSE</span><strong>{evaluation?.rmse == null ? '—' : `${decimal.format(evaluation.rmse)}`}</strong></div><div><span>TEST ROWS</span><strong>{evaluation?.common_population_rows ? number.format(evaluation.common_population_rows) : '—'}</strong></div><div><span>COVERAGE</span><strong>{evaluation?.prediction_coverage == null ? '—' : `${(evaluation.prediction_coverage * 100).toFixed(1)}%`}</strong></div></div>
          {evaluation?.models && <div className="comparison-table-wrap"><table className="comparison-table"><thead><tr><th>Method</th><th>MAE</th><th>RMSE</th><th>R²</th><th>Predictions</th></tr></thead><tbody>{Object.entries(evaluation.models).map(([name, metrics]) => <tr key={name} className={name === 'lightgbm' ? 'model-row' : ''}><td>{name === 'lightgbm' ? 'LightGBM' : name.replaceAll('_', ' ')}</td><td>{metrics.mae == null ? '—' : decimal.format(metrics.mae)}</td><td>{metrics.rmse == null ? '—' : decimal.format(metrics.rmse)}</td><td>{metrics.r2 == null ? '—' : decimal.format(metrics.r2)}</td><td>{number.format(metrics.n_predictions)}</td></tr>)}</tbody></table></div>}
          {pageError}
        </section>}
      </main>
    </div>
  )
}

export default App
