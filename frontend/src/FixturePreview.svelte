<script lang="ts">
  import InstanceInspection from './InstanceInspection.svelte';
  import { createPreview } from './inspection';
  import type { Theme } from './theme';

  let { theme }: { theme: Theme } = $props();
  const inspection = createPreview();
</script>

<main>
  <h1>Viewer preview</h1>
  <p class="notice">Synthetic 128-city geometry and a synthetic tour, not experiment evidence. Loaded results never use this geometry.</p>
  <InstanceInspection {inspection} {theme} />
</main>
