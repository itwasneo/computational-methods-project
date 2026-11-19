<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { getSession, getNavigation } from '../application';
  import { ExperimentController, initialState, shouldPoll, selection } from './controller';
  import { queryIdentity } from '../analysis-query';
  import { routeHash, type Route } from '../navigation';
  import type { Theme } from '../theme';
  import type { AnalysisQuery, JobRecord } from '../contracts';
  import { PAIRING_PATTERN } from '../results/client';
  import InstanceSetup from './InstanceSetup.svelte';
  import ConfigurationPanel from './ConfigurationPanel.svelte';
  import JobsPanel from './JobsPanel.svelte';
  import Analysis from './Analysis.svelte';
  import ResultInspection from './ResultInspection.svelte';
  import OperationStatus from '../components/OperationStatus.svelte';
  let { theme, active }: { theme: Theme; active: boolean } = $props();
  const session = getSession(),
    navigation = getNavigation();
  let authority = $state.raw(session.state),
    view = $state.raw(initialState());
  const unsubscribeSession = session.subscribe((value) => (authority = value));
  const controller = new ExperimentController(
    (value) => (view = value),
    session.api,
    undefined,
    session,
  );
  let configuration = $state<ConfigurationPanel>(),
    analysisPanel = $state<Analysis>(),
    inspection = $state<ResultInspection>();
  let secret = $state(''),
    routeError = $state(''),
    visible = $state(document.visibilityState === 'visible');
  let navigationEpoch = 0;
  let route = $state.raw(navigation.state);
  function currentRoute(): Route {
    const query = view.operations.analysis.busy ? view.analysisRequested : view.query;
    return {
      view: 'experiments',
      root: view.capabilities?.output_root?.root_id,
      job: view.selected?.job_id,
      attempt: view.inspectionAttempt ?? view.details?.details.attempt_id,
      ...(query ? { analysis: { ...query, cursor: null } } : {}),
    };
  }
  async function restoreRoute(route: Route) {
    const epoch = ++navigationEpoch;
    if (route.view !== 'experiments' || !controller.state.capabilities?.local_runs_enabled) return;
    routeError = '';
    if (!route.job) {
      if (controller.state.selected) controller.clearSelection();
      return;
    }
    if (!route.root || route.root !== controller.state.capabilities.output_root?.root_id) {
      controller.clearSelection();
      routeError =
        'This link belongs to another output root. Connect to its original owner service before restoring it.';
      return;
    }
    if (
      controller.state.selected?.job_id !== route.job ||
      !controller.state.selectedConfiguration ||
      !controller.state.resultInstance
    ) {
      let job = controller.state.jobs.find((item) => item.job_id === route.job);
      if (!job) {
        await controller.refreshJobs();
        if (epoch !== navigationEpoch) return;
        job = controller.state.jobs.find((item) => item.job_id === route.job);
      }
      if (!job) {
        controller.clearSelection();
        routeError = 'The linked job is not available in this output root.';
        return;
      }
      const result = await controller.select(job);
      if (!result.ok || epoch !== navigationEpoch) return;
    }
    await tick();
    if (epoch !== navigationEpoch) return;
    if (controller.state.selected?.result_revision) {
      const query: AnalysisQuery = route.analysis ?? { ...selection, table: 'trials' };
      const pendingSameQuery =
        controller.state.operations.analysis.busy &&
        queryIdentity(controller.state.analysisRequested) === queryIdentity(query);
      if (
        !pendingSameQuery &&
        (!controller.state.analysis ||
          queryIdentity(controller.state.query) !== queryIdentity(query))
      ) {
        analysisPanel?.restore(query);
        await controller.analyze(query);
      }
    }
    if (epoch !== navigationEpoch) return;
    if (!route.attempt) controller.closeInspection();
    else if (
      controller.state.details?.details.attempt_id !== route.attempt &&
      !(
        controller.state.operations.inspection.busy &&
        controller.state.inspectionAttempt === route.attempt
      )
    )
      await inspection?.inspect(route.attempt, false);
  }
  const unsubscribeNavigation = navigation.subscribe((value, navigated) => {
    route = value;
    if (navigated) void restoreRoute(value);
  });
  onMount(() => {
    const changed = () => (visible = document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  });
  $effect(() => {
    if (!active || !visible || !shouldPoll(view)) return;
    const timer = setTimeout(() => {
      if (active && visible) void controller.poll();
    }, 1500);
    return () => clearTimeout(timer);
  });
  onDestroy(() => {
    navigationEpoch++;
    unsubscribeNavigation();
    unsubscribeSession();
    controller.dispose();
  });
  async function connect(event: SubmitEvent) {
    event.preventDefault();
    const credential = secret;
    secret = '';
    const result = await controller.connect(credential);
    if (result.ok) await restoreRoute(navigation.state);
  }
  async function rerun() {
    const result = await controller.rerun();
    if (result.ok) {
      await tick();
      await configuration?.restore(result.value.configuration);
    }
  }
  function select(job: JobRecord) {
    if (
      navigation.state.view === 'experiments' &&
      navigation.state.job === job.job_id &&
      navigation.state.root === view.capabilities?.output_root?.root_id
    )
      void restoreRoute(navigation.state);
    else
      navigation.go({
        view: 'experiments',
        root: view.capabilities?.output_root?.root_id,
        job: job.job_id,
      });
  }
  async function analyze(query: AnalysisQuery) {
    const next = { ...currentRoute(), analysis: query };
    // Navigation expresses the requested query before its read completes. Completion
    // must never replace a subsequently chosen view or query.
    if (routeHash(next) === routeHash(navigation.state)) await controller.analyze(query);
    else navigation.go(next);
  }
  function inspectAttempt(attempt: string) {
    const pending = inspection?.inspect(attempt);
    navigation.go({ ...currentRoute(), attempt });
    return pending;
  }
  async function submit() {
    const epoch = navigationEpoch;
    const result = await controller.submit();
    if (!result.ok) return;
    if (active && epoch === navigationEpoch) navigation.go(currentRoute());
    else if (navigation.state.view !== 'experiments') navigation.rememberExperiment(currentRoute());
  }
</script>

<section class="experiment-app" aria-labelledby="experiment-heading">
  <h1 id="experiment-heading">Experiment workspace</h1>
  <p>
    Generate or load an instance, review a native NN or GA experiment, then analyze and inspect
    validated results.
  </p>
  {#if !view.connected}
    <form class="app-card" onsubmit={connect}>
      <h2>Connect to your local service</h2>
      <p>
        Start <code>tools/local_app.py</code>
        with your read workspace and execution directories. Enter the same private pairing secret here.
      </p>
      <label>
        Pairing secret
        <input
          type="password"
          bind:value={secret}
          required
          minlength="43"
          maxlength="128"
          pattern={PAIRING_PATTERN}
          autocomplete="off"
        />
      </label>
      <button disabled={authority.status === 'connecting'}>Connect</button>
      {#if authority.status === 'connecting'}<p role="status">Connecting…</p>{/if}
      {#if authority.error}<p role="alert">
          {authority.error} Ask the owner to enable fresh pairing in the terminal, then reconnect.
        </p>{/if}
      {#if route.job}<p>
          Your linked job will be restored only after pairing with its original output root. Nothing
          is automatically submitted.
        </p>{/if}
    </form>
  {:else}
    <div class="source-actions">
      <strong>Output: {view.capabilities?.output_root?.display_name ?? 'Read-only'}</strong>
      <button onclick={() => controller.disconnect()}>Disconnect</button>
    </div>
    <OperationStatus operation={view.operations.session} retry={() => controller.initialize()} />
    {#if !view.capabilities}
      <p role="status">
        {view.operations.session.busy
          ? 'Loading execution capabilities…'
          : 'Execution capabilities unavailable; retry above.'}
      </p>
    {:else if !view.capabilities.local_runs_enabled}
      <p role="status">
        Execution is disabled. Enable both owner execution flags, or use Saved results. HTTP cannot
        enable execution.
      </p>
    {:else}
      {#if view.pending || view.invalidRetry}
        <section class="app-card" aria-label="Submission recovery">
          <h2>
            {view.invalidRetry
              ? 'Saved retry metadata is unreadable'
              : 'Submission acknowledgement uncertain'}
          </h2>
          {#if view.pending}<p>
              Do not create another campaign to retry. Reuse submission key <code>
                {view.pending.key}
              </code>
              , retained in tab storage when available. Inspect jobs after reconnecting.
            </p>
            <button disabled={!!view.operations.submission.busy} onclick={submit}>
              Retry original submission
            </button>
          {:else}<p>
              The saved reference cannot be trusted. Inspect jobs in the original output root before
              clearing it. A fresh submission could duplicate accepted work.
            </p>{/if}
          <button
            disabled={!!view.operations.submission.busy}
            onclick={() => {
              if (
                window.confirm(
                  'Have you inspected jobs in the original output root? Clearing this reference does NOT cancel accepted work. A fresh submission may duplicate that work.',
                )
              )
                void controller.abandonPending();
            }}
          >
            Clear retry reference after inspection
          </button>
        </section>
      {/if}
      <InstanceSetup {view} {controller} />
      <ConfigurationPanel bind:this={configuration} {view} {controller} {submit} />
      {#if routeError}<p role="alert">{routeError}</p>{/if}
      <JobsPanel {view} {controller} {select} {rerun} />
      {#if view.selected?.result_revision && view.selectedConfiguration}
        {#key view.selected.job_id}
          <Analysis
            bind:this={analysisPanel}
            page={view.analysis}
            applied={view.query}
            configuration={view.selectedConfiguration}
            busy={!!view.operations.analysis.busy}
            inspecting={!!view.operations.inspection.busy}
            instanceId={view.resultInstance?.instance.instance_id ?? ''}
            implementations={view.capabilities.implementations
              .filter(
                (i) =>
                  i.available === true && i.algorithm === view.selectedConfiguration?.algorithm,
              )
              .map((i) => String(i.implementation))}
            references={view.artifacts.filter((a) => a.kind === 'json')}
            {analyze}
            inspect={inspectAttempt}
          />
        {/key}
        <OperationStatus
          operation={view.operations.analysis}
          retry={() =>
            controller.analyze({
              ...(view.analysisRequested ??
                view.query ?? { table: 'trials', attempt_ids: null, limit: 100 }),
              cursor: null,
            })}
        />
        <div class="source-actions" aria-label="Analysis pages">
          <button
            disabled={!!view.operations.analysis.busy || view.analysisPage === 0}
            onclick={() => controller.navigateAnalysis(0)}
          >
            First analysis page
          </button>
          <button
            disabled={!!view.operations.analysis.busy || view.analysisPage === 0}
            onclick={() => controller.navigateAnalysis(view.analysisPage - 1)}
          >
            Previous analysis page
          </button>
          <span>Page {view.analysisPage + 1} · {view.analysis?.items.length ?? 0} rows</span>
          <button
            disabled={!!view.operations.analysis.busy || !view.analysis?.next_cursor}
            onclick={() => controller.navigateAnalysis(view.analysisPage + 1)}
          >
            Next analysis page
          </button>
        </div>
      {/if}
      <ResultInspection bind:this={inspection} {view} {controller} {theme} {active} />
    {/if}
  {/if}
</section>
