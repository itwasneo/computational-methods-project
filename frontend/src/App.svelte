<script lang="ts">
  import Results from './results/Results.svelte';
  import FixturePreview from './FixturePreview.svelte';
  import { applyTheme, type Theme } from './theme';

  let mode = $state<'live' | 'fixture'>('live');
  let theme = $state<Theme>(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
  function toggleTheme() { theme = theme === 'light' ? 'dark' : 'light'; applyTheme(theme); }
</script>

<nav class="app-nav" aria-label="Results source">
  <button aria-pressed={mode === 'live'} onclick={() => mode = 'live'}>Local results</button>
  <button aria-pressed={mode === 'fixture'} onclick={() => mode = 'fixture'}>Viewer preview</button>
  <button class="theme-toggle" onclick={toggleTheme}
    aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
    title={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">
      {#if theme === 'light'}
        <path d="M20.5 13.2A8.5 8.5 0 0 1 10.8 3.5a8.5 8.5 0 1 0 9.7 9.7Z" />
      {:else}
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
      {/if}
    </svg>
  </button>
</nav>
<!-- Keep the paired session in memory when inspecting the separate fixture preview. -->
<div hidden={mode !== 'live'}><Results /></div>
{#if mode === 'fixture'}<FixturePreview {theme} />{/if}
