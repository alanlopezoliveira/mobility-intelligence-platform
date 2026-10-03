export type Profile = { hour?: number; weekday?: number; month?: string; mean_departures?: number; occupancy?: number; empty_rate?: number; full_rate?: number; samples?: number; rows?: number }
export type Station = { station: string; name: string; rows: number; valid_rows: number; mean_occupancy: number; empty_rate: number; full_rate: number; empty_samples: number; full_samples: number; empty_runs: number; full_runs: number; unavailable_samples?: number; latitude: number; longitude: number }
export type EventRun = { station: string; start: string; end: string; samples: number; span_minutes: number }
export type EventSummary = { runs: number; singleton_runs: number; observed_span_hours: number; median_span_minutes: number; max_span_minutes: number; longest: EventRun[] }
export type Score = { mae: number; rmse: number; r2: number; rows: number; negative_predictions: number; duplicates: number; matches_saved_metrics: boolean }
export type Prediction = { feature_timestamp: string; target_timestamp: string; y_true: number; y_pred: number }
export type Horizon = { metadata: { feature_columns: string[]; train_rows: number; validation_rows: number; test_rows: number; dependency_versions: Record<string, string>; model_parameters: Record<string, string | number>; best_iteration: number }; metrics: Record<string, Score>; input_hashes_match: boolean; common_keys_and_labels_equal: boolean; sample_stations: { station: string; rows: number }[]; start: string; end: string }
export type Issue = { id: string; priority: string; title: string; detail: string; evidence: string; recommendation: string }
export type AuditReport = {
  generated_at: string
  source_of_truth: string
  verdicts: { category: string; rating: string; reason: string }[]
  issues: Issue[]
  assumptions: string[]
  references: { title: string; url: string }[]
  schemas: { source: string; fields: string; meaning: string }[]
  source: {
    definition_count: number; documented_count: number; tracked_files: number
    tracked_inappropriate_candidates: string[]; tracked_over_1mb: string[]
    junk: { path: string; files: number; bytes: number }[]
    modules: { module: string; definitions: number; docstrings: number; rating: string; missing_examples: string[] }[]
    libraries: { library: string; declared: string; installed: string; group: string; used_in: string[]; purpose?: string }[]
    unreferenced_candidates: { file: string; line: number; name: string }[]
  }
  demand: { rows: number; stations: number; start: string; end: string; departures: number; negative_counts: number; invalid_invariants: number; duplicate_keys_hash_check: number; zero_departure_rows: number; unique_hours: number; calendar_hours: number; missing_global_hours: number; full_rectangle_coverage: number; missing: Record<string, number>; sha256: string; profiles: Record<string, Profile[]>; top_stations: { station_id: string; rows: number; departures: number; start: string; end: string }[] }
  occupancy: { status: string; rows: number; stations: number; valid_rows: number; start: string; end: string; timezone: string; empty_samples: number; full_samples: number; near_empty_samples: number; near_full_samples: number; duplicate_rows: number; conflicting_keys_excluded: number; negative_bikes: number; bikes_over_capacity: number; invalid_capacity: number; inventory_not_balanced: number; missing: Record<string, number>; active_values: Record<string, number>; unavailable_values: Record<string, number>; gap_minutes: { median: number; p95: number; max: number; over_90_minutes: number }; distribution: { from: number; to: number; count: number }[]; station_stats: Station[]; heatmap: { station: string; hour: number; occupancy: number; samples: number }[]; profiles: Record<string, Profile[]>; events: Record<string, EventSummary>; sources: { source: string; rows: number; snapshots: number; sha256: string }[]; failures: { source: string; reason: string }[] }
  model: { horizons: Record<string, Horizon>; serialization: string; summary: { timestamp: string; horizons: Record<string, { common_population_rows: number; common_population_coverage: number; target_rows: number }> } }
}
