<script lang="ts">
  import { onMount, type Snippet } from 'svelte';
  let {
    label,
    busy = false,
    children,
  }: { label: string; busy?: boolean; children: Snippet } = $props();
  let region = $state<HTMLDivElement>(),
    height = $state(0);
  onMount(() => {
    const observer = new ResizeObserver(() => {
      if (!busy && region?.checkVisibility()) height = region.getBoundingClientRect().height;
    });
    if (region) observer.observe(region);
    return () => observer.disconnect();
  });
</script>

<!-- Explicit keyboard access to both scroll axes, including tables with no interactive cells. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
  bind:this={region}
  class="table-region"
  style:min-height={busy ? `min(${height}px, 70dvh)` : undefined}
  role="region"
  aria-label={label}
  tabindex="0"
  aria-busy={busy}
>
  {@render children()}
</div>
