<script lang="ts">
  import { terminal, type ExperimentController, type ExperimentState } from './controller';
  import type { JobRecord } from '../contracts';
  import { compactId, actionLabel } from '../display';
  import TableRegion from '../components/TableRegion.svelte';
  import OperationStatus from '../components/OperationStatus.svelte';
  let {
    view,
    controller,
    select,
    rerun,
  }: {
    view: ExperimentState;
    controller: ExperimentController;
    select: (job: JobRecord) => unknown;
    rerun: () => unknown;
  } = $props();
  let confirmedFor = $state('');
  let controlling = $derived(!!view.operations.control.busy);
</script>

<section class="app-card" aria-labelledby="jobs-heading">
  <h2 id="jobs-heading">3. Jobs and recovery</h2>
  <div class="source-actions">
    <button
      disabled={!!view.operations.jobs.busy || controlling}
      onclick={() => controller.refreshJobs()}
    >
      Refresh jobs
    </button>
    <span>Dispatch: {view.queue?.dispatch ?? 'unknown'}</span>
    {#if view.queue?.dispatch === 'held'}<button
        disabled={controlling ||
          !!view.operations.submission.busy ||
          !!view.operations.jobs.busy ||
          !!view.operations.jobs.error}
        onclick={() => controller.continueQueue()}
      >
        Continue held queue
      </button>{/if}
  </div>
  {#if view.queue?.dispatch === 'held'}<p role="status">
      The queue is held. Inspect interrupted evidence, then explicitly continue dispatch when ready.
      Do not resubmit an existing job.
    </p>{/if}
  <p>
    Disconnecting does not cancel work. Restart holds dispatch. Completed campaigns may contain
    failed trials; inspect the counts.
  </p>
  <OperationStatus operation={view.operations.jobs} retry={() => controller.refreshJobs()} />
  <TableRegion label="Experiment jobs" busy={!!view.operations.jobs.busy}>
    <table>
      <caption class="sr-only">
        Jobs in the authorized output root. Select a job to view its evidence and controls.
      </caption>
      <thead>
        <tr>
          <th scope="col">Job</th>
          <th scope="col">State</th>
          <th scope="col">Recorded / planned</th>
          <th scope="col">Failed</th>
        </tr>
      </thead>
      <tbody>
        {#each view.jobs as job (job.job_id)}<tr>
            <td>
              <button
                title={job.job_id}
                aria-label={actionLabel('Select job', job.job_id)}
                aria-pressed={view.selected?.job_id === job.job_id}
                onclick={() => {
                  confirmedFor = '';
                  void select(job);
                }}
              >
                {compactId(job.job_id)}
              </button>
            </td>
            <td>{job.state}</td>
            <td class="numeric">{job.recorded_trials} / {job.planned_trials}</td>
            <td class="numeric">{job.failed_trials}</td>
          </tr>{:else}<tr><td colspan="4">No submitted experiments.</td></tr>{/each}
      </tbody>
    </table>
  </TableRegion>
  <OperationStatus operation={view.operations.selection} />
  {#if view.selected}
    <h3>Selected job: {view.selected.state}</h3>
    <p>
      <span class="sr-only">Full job ID:</span>
      <code>{view.selected.job_id}</code>
    </p>
    {#if view.selected.failure_code}<p role="alert">
        Operational failure: {view.selected.failure_code}. Valid recorded evidence may still be
        available.
      </p>{/if}
    <div class="source-actions">
      {#if ['queued', 'held'].includes(view.selected.state)}
        <button disabled={controlling} onclick={() => controller.control('cancel_queued')}>
          Cancel queued job
        </button>
      {:else if !terminal(view.selected)}
        <button disabled={controlling} onclick={() => controller.control('stop_after_trial')}>
          Stop after trial
        </button>
        <label>
          <input
            type="checkbox"
            checked={confirmedFor === view.selected.job_id}
            onchange={(event) =>
              (confirmedFor = event.currentTarget.checked ? view.selected!.job_id : '')}
          />
          Confirm immediate interruption
        </label>
        <button
          disabled={confirmedFor !== view.selected.job_id || controlling}
          onclick={() => {
            confirmedFor = '';
            void controller.control('stop_now');
          }}
        >
          Stop now
        </button>
      {/if}
      {#if terminal(view.selected)}<button
          disabled={!!view.operations.review.busy ||
            !!view.operations.instance.busy ||
            !!view.operations.submission.busy ||
            !!view.pending ||
            view.invalidRetry}
          onclick={rerun}
        >
          Review fresh rerun
        </button>{/if}
    </div>
    {#if terminal(view.selected) && !view.selected.result_revision}<p>
        No validated result source is available. No result or completion is fabricated.
      </p>{/if}
  {/if}
  <OperationStatus operation={view.operations.control} />
</section>
