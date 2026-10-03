import { useEffect, useState } from 'react'
import { projectDataUrl } from './dataSource'
import ModelComparison from './ModelComparison'
import type { Report } from './ProjectApp'
import './ProjectApp.css'

/** Current model evidence is independent of the optional legacy inventory audit. */
export default function ResearchPage({ view }: { view: 'models' | 'methods' }) {
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState('')
  const [horizon, setHorizon] = useState(60)
  useEffect(() => {
    const controller = new AbortController()
    fetch(projectDataUrl('report.json'), { signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error('Published research is unavailable.'); return response.json() })
      .then(setReport).catch(error => { if (!controller.signal.aborted) setError(String(error)) })
    return () => controller.abort()
  }, [])
  if (!report) return <main className="project-loading" role={error ? 'alert' : 'status'}>{error || 'Loading research…'}</main>
  const model = report.models.find(item => item.horizon_minutes === horizon)
  return <div className="project-app"><main><section className="project-section"><h1>{view === 'models' ? 'Model comparison' : 'Methods & limitations'}</h1><p>{report.scope}</p>
    {view === 'models' ? <><div className="project-controls"><label>Forecast horizon<select value={horizon} onChange={event => setHorizon(Number(event.target.value))}>{report.models.map(item => <option key={item.horizon_minutes} value={item.horizon_minutes}>+{item.horizon_minutes} minutes</option>)}</select></label></div>{model && <ModelComparison model={model} />}</>
      : <><ol>{report.assumptions.map(item => <li key={item}>{item}</li>)}</ol><p>Source: {report.provider?.attribution || 'Official BiciMAD historical data'}.</p><p>Prepared {report.generated_at}. Weather associations are descriptive; forecasts estimate recorded departures.</p><a href={projectDataUrl('report.json')} download>Download provenance and methods</a></>}
  </section></main></div>
}
