/** Presentation metadata only. Unknown columns are displayed verbatim, never assigned inferred units. */
const durationColumns = new Set([
  'input_time_s',
  'distance_preparation_wall_time_s',
  'distance_warmup_time_s',
  'preprocessing_time_s',
  'solver_warmup_time_s',
  'solve_wall_time_s',
  'search_time_s',
  'solver_validation_time_s',
  'runner_validation_time_s',
  'pre_persistence_time_s',
  'failed_phase_time_s',
  'persistence_time_s',
  'end_to_end_time_s',
  'setup_time_s',
  'trial_persistence_time_s',
  'jit_warmup_time_s',
  'time_limit_s',
  'time_overshoot_s',
  'elapsed_s',
  'search_elapsed_s',
  'observed_time_s',
  'logical_elapsed_s',
  'observation_end_s',
]);
const labels: Record<string, string> = {
  attempt_id: 'Attempt',
  baseline_attempt_id: 'Baseline attempt',
  instance_id: 'Instance',
  search_time_s: 'Search time',
  end_to_end_time_s: 'Total time',
  search_elapsed_s: 'Search elapsed',
  elapsed_s: 'Elapsed',
  logical_id: 'Logical search',
};
export function columnMetadata(field: string) {
  const statistic = field.match(/^(.*)_(mean|median|min|max|std|p25|p75|q25|q75)$/);
  const seconds = durationColumns.has(field) || !!(statistic && durationColumns.has(statistic[1]));
  return {
    seconds,
    identity: field.endsWith('_id'),
    label: labels[field] ?? (seconds ? field.replace(/_s(?=_|$)/, '').replaceAll('_', ' ') : field),
  };
}
export const preferredColumns: Record<string, string[]> = {
  trials: [
    'attempt_id',
    'implementation',
    'distance_mode',
    'repetition',
    'outcome',
    'best_cost',
    'search_time_s',
    'end_to_end_time_s',
    'campaign_complete',
    'duplicate_execution',
    'usable',
    'native_verified',
  ],
  summary: [
    'implementation',
    'distance_mode',
    'attempt_count',
    'failed_count',
    'incomplete_count',
    'duplicate_count',
    'usable_count',
    'best_cost_median',
    'search_time_s_median',
    'end_to_end_time_s_median',
  ],
  comparisons: ['attempt_id', 'baseline_attempt_id', 'metric', 'speedup', 'reason'],
  progress: [
    'attempt_id',
    'logical_id',
    'sample_index',
    'elapsed_s',
    'search_elapsed_s',
    'best_cost',
    'trial_best_cost',
    'usable',
  ],
};
