import { useMemo, useState } from 'react'

export type Metric = {
  model: string; mae: number; rmse: number; r2: number; rows: number
  validation_mae: number; fit_seconds: number; predict_seconds: number
  model_bytes: number; selected: boolean; within_tolerance: boolean; complexity_tier: number
}
export type Model = {
  horizon_minutes: number; selected_model: string; metrics: Metric[]
  train_rows: number; validation_rows: number; test_rows: number
  train_start: string; train_end: string; validation_start: string; validation_end: string; test_start: string; test_end: string
  reload_max_error: number; common_population_fraction: number; importance: Record<string, number>
  selection: { policy: string; best_validation_mae: number; relative_tolerance: number; maximum_eligible_mae: number; eligible_models: string[]; selected_validation_penalty_pct: number }
}
const format = (n: number, digits = 3) => n.toLocaleString('en-GB', { maximumFractionDigits: digits })

export default function ModelComparison({ model }: { model: Model }) {
  const [sort, setSort] = useState('validation_mae')
  const rows = useMemo(() => [...model.metrics].sort((a, b) => a[sort as 'validation_mae' | 'mae' | 'predict_seconds' | 'model_bytes'] - b[sort as 'validation_mae' | 'mae' | 'predict_seconds' | 'model_bytes']), [model.metrics, sort])
  if (!model.selection) return <div className="project-card">The expanded model benchmark is being generated. Reload after the pipeline completes.</div>
  const winner = model.metrics.find(m => m.selected)!
  const bestTest = [...model.metrics].sort((a, b) => a.mae - b.mae)[0]
  return <div className="project-card model-comparison" id="model-comparison">
    <div className="section-heading"><div><span className="project-eyebrow">VALIDATION-SELECTED MODEL · +{model.horizon_minutes} MIN</span><h3>{model.selected_model}</h3></div><span className="project-badge">{model.metrics.length} candidates · identical evaluation rows</span></div>
    <p>The lowest validation MAE is <strong>{format(model.selection.best_validation_mae, 4)}</strong>. Models within {format(model.selection.relative_tolerance * 100, 1)}% qualify for the simplicity tie-break. The selected model is {format(model.selection.selected_validation_penalty_pct, 2)}% above that minimum.</p>
    <p><strong>Eligible:</strong> {model.selection.eligible_models.join(', ')}. Preference: baseline → linear → single tree → ensemble; within a tier, smaller saved model first. This is a practical tolerance, not a statistical equivalence test.</p>
    <div className="comparison-toolbar"><h3>Same data. Multiple model families.</h3><label>Sort comparison <select value={sort} onChange={e => setSort(e.target.value)}><option value="validation_mae">Validation MAE</option><option value="mae">Test MAE</option><option value="predict_seconds">Prediction time</option><option value="model_bytes">Model size</option></select></label></div>
    <div className="project-table-wrap"><table><thead><tr><th>Model</th><th>Validation MAE ↓</th><th>Test MAE ↓</th><th>Test RMSE ↓</th><th>Test R² ↑</th><th>Fit seconds ↓</th><th>Predict ms ↓</th><th>Saved KiB ↓</th></tr></thead><tbody>{rows.map(m => <tr key={m.model} data-selected={m.selected}><td>{m.model}{m.selected ? <small className="model-tag">Selected</small> : m.within_tolerance ? <small className="model-tag muted">Within 1%</small> : null}</td><td>{format(m.validation_mae, 4)}</td><td>{format(m.mae, 4)}</td><td>{format(m.rmse, 4)}</td><td>{format(m.r2, 4)}</td><td>{format(m.fit_seconds, 2)}</td><td>{format(m.predict_seconds * 1000, 1)}</td><td>{format(m.model_bytes / 1024, 2)}</td></tr>)}</tbody></table></div>
    <p className="project-footnote">Each model sees {format(model.train_rows, 0)} training rows and the same {format(model.validation_rows, 0)} validation / {format(model.test_rows, 0)} test rows. Prediction time is the median of three runs over the full test set after a warm-up. All predictions use the same nonnegative postprocessing. Model size includes preprocessing and uses identical joblib compression.</p>
    <div className="split-strip"><span>Train · {model.train_start.slice(0, 10)} → {model.train_end.slice(0, 10)}<small>{format(model.train_rows, 0)} rows</small></span><span>Validate · {model.validation_start.slice(0, 10)} → {model.validation_end.slice(0, 10)}<small>{format(model.validation_rows, 0)} rows</small></span><span>Test · {model.test_start.slice(0, 10)} → {model.test_end.slice(0, 10)}<small>{format(model.test_rows, 0)} rows</small></span></div>
    <p className="project-footnote">Selected test MAE: {format(winner.mae, 4)}. Lowest test MAE: {bestTest.model} ({format(bestTest.mae, 4)}). Test results are reported after selection and do not change the winner. This historical test period was inspected in previous iterations; it is not a fresh prospective evaluation.</p>
    <p className="project-footnote">Seed 42 · bounded model budgets · chronological splits with gaps · common holdout coverage {format(model.common_population_fraction * 100, 1)}% · selected model reload error {model.reload_max_error}. Weather reanalysis stays outside forecasting features because it was finalized after prediction time.</p>
  </div>
}
