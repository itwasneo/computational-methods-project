<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import type { Inspection } from './inspection';
  import { createViewer } from './viewer';
  import type { Theme } from './theme';

  let { inspection, theme }: { inspection: Inspection; theme: Theme } = $props();
  let canvas = $state<HTMLCanvasElement>();
  let surface = $state<HTMLDivElement>();
  let fullscreen = $state(false);
  let fullscreenAvailable = $state(false);
  let fullscreenError = $state('');

  onMount(() => {
    fullscreenAvailable = document.fullscreenEnabled;
    const changed = () => { fullscreen = document.fullscreenElement === surface; };
    document.addEventListener('fullscreenchange', changed);
    return () => document.removeEventListener('fullscreenchange', changed);
  });

  async function toggleFullscreen() {
    fullscreenError = '';
    try {
      if (document.fullscreenElement === surface) await document.exitFullscreen();
      else await surface?.requestFullscreen();
    } catch {
      fullscreenError = 'Fullscreen is unavailable in this browser context.';
    }
  }
  let viewer = $state<ReturnType<typeof createViewer> | null>(null);
  let selected = $state(0);
  let retry = $state(0);
  let error = $state('');
  let showTour = $state(untrack(() => inspection.route !== null));

  function selectCity(id: number) {
    selected = id;
    viewer?.select(id);
  }

  $effect(() => {
    retry;
    if (!canvas) return;
    error = '';
    let instance: ReturnType<typeof createViewer> | null = null;
    try {
      instance = createViewer(canvas, inspection, selectCity, message => {
        error = message;
      });
      instance.select(untrack(() => selected));
      instance.tour(untrack(() => showTour));
      instance.theme(untrack(() => theme));
      viewer = instance;
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
      viewer = null;
    }
    return () => { instance?.destroy(); };
  });

  $effect(() => { viewer?.theme(theme); });
</script>

  <div class="instance-inspection" role="region" aria-label="Instance inspection">
    <div class="canvas-area" bind:this={surface}>
    <div class="viewer-controls" role="group" aria-label="Viewer controls">
      <button disabled={!viewer || !!error} aria-label="Fit instance to view" title="Fit instance to view (Home)" onclick={() => viewer?.fit()}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
          <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />
          <rect x="8" y="8" width="8" height="8" rx="1" />
        </svg>
      </button>
      <button disabled={!viewer || !!error} aria-label="Zoom in" onclick={() => viewer?.zoom(1.2)}>+</button>
      <button disabled={!viewer || !!error} aria-label="Zoom out" onclick={() => viewer?.zoom(1 / 1.2)}>−</button>
      {#if inspection.route}
        <label><input type="checkbox" bind:checked={showTour} onchange={() => viewer?.tour(showTour)} /> Fixture tour</label>
      {/if}
      <button
        class="fullscreen-button"
        disabled={!fullscreenAvailable}
        aria-label={fullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
        title={fullscreen ? 'Exit fullscreen (Escape)' : 'Enter fullscreen'}
        onclick={toggleFullscreen}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
          {#if fullscreen}
            <path d="M3 8h5V3m8 0v5h5M8 21v-5H3m18 0h-5v5" />
          {:else}
            <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />
          {/if}
        </svg>
      </button>
    </div>
        {#key retry}
          <!-- Canvas keyboard pan/zoom is implemented by createViewer. -->
          <canvas
            bind:this={canvas}
            tabindex="0"
            aria-label="City view. Drag or use arrow keys to pan; plus and minus zoom; Home fits. Click to select a city."
            title="Drag to pan · scroll to zoom · click to select"
          ></canvas>
        {/key}
        {#if error}
          <div class="viewer-error" role="status"><p>{error}</p><button onclick={() => retry += 1}>Retry viewer</button></div>
        {:else if fullscreenError}
          <div class="viewer-error" role="status"><p>{fullscreenError}</p></div>
        {/if}
      </div>
  </div>
