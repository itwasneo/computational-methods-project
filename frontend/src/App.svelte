<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import Results from './results/Results.svelte';
  import Experiments from './experiments/Experiments.svelte';
  import FixturePreview from './FixturePreview.svelte';
  import { applyTheme, readThemePreference, setThemePreference, type Theme } from './theme';
  import { WorkspaceSession } from './session';
  import { Navigation, routeHash, type Route } from './navigation';
  import { provideSession, provideNavigation } from './application';
  const session = new WorkspaceSession(),
    navigation = new Navigation();
  provideSession(session);
  provideNavigation(navigation);
  let route = $state.raw(navigation.state),
    lastExperiments = $state.raw<Route>({ view: 'experiments' });
  let workspace = $state<HTMLElement>();
  let preference = $state<Theme | 'system'>(readThemePreference());
  let theme = $state<Theme>(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
  const unsubscribe = navigation.subscribe((value) => {
    const changedView = value.view !== route.view;
    route = value;
    lastExperiments = navigation.experiments;
    document.title = `${value.view === 'experiments' ? 'Experiments' : value.view === 'saved' ? 'Saved results' : 'Viewer preview'} · TSP workspace`;
    if (changedView) void tick().then(() => workspace?.focus({ preventScroll: true }));
  });
  onMount(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const changed = () => {
      if (preference === 'system') {
        theme = media.matches ? 'dark' : 'light';
        applyTheme(theme, false);
      }
    };
    media.addEventListener('change', changed);
    return () => media.removeEventListener('change', changed);
  });
  onDestroy(() => {
    unsubscribe();
    navigation.dispose();
    session.dispose();
  });
  function toggleTheme() {
    preference = theme === 'light' ? 'dark' : 'light';
    theme = setThemePreference(preference);
  }
  function follow(event: MouseEvent, next: Route) {
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigation.go(next);
  }
</script>

<a
  class="skip-link"
  href="#workspace-content"
  onclick={(event) => {
    event.preventDefault();
    workspace?.focus();
  }}
>
  Skip to workspace
</a>
<nav class="app-nav" aria-label="Workspace navigation">
  <a
    href={routeHash(lastExperiments)}
    aria-current={route.view === 'experiments' ? 'page' : undefined}
    onclick={(event) => follow(event, lastExperiments)}
  >
    Experiments
  </a>
  <a
    href="#saved"
    aria-current={route.view === 'saved' ? 'page' : undefined}
    onclick={(event) => follow(event, { view: 'saved' })}
  >
    Saved results
  </a>
  <a
    href="#preview"
    aria-current={route.view === 'preview' ? 'page' : undefined}
    onclick={(event) => follow(event, { view: 'preview' })}
  >
    Viewer preview
  </a>
  <button
    class="theme-toggle"
    onclick={toggleTheme}
    aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
    title={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`}
  >
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.6"
      aria-hidden="true"
    >
      {#if theme === 'light'}<path d="M20.5 13.2A8.5 8.5 0 0 1 10.8 3.5a8.5 8.5 0 1 0 9.7 9.7Z" />
      {:else}<circle cx="12" cy="12" r="4" />
        <path
          d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"
        />{/if}
    </svg>
  </button>
  <button
    aria-pressed={preference === 'system'}
    onclick={() => {
      preference = 'system';
      theme = setThemePreference('system');
    }}
  >
    Use system theme
  </button>
</nav>
<main id="workspace-content" bind:this={workspace} tabindex="-1">
  <!-- Mounted feature state survives navigation; feature disposal never clears shared credentials. -->
  <div hidden={route.view !== 'experiments'}>
    <Experiments active={route.view === 'experiments'} {theme} />
  </div>
  <div hidden={route.view !== 'saved'}><Results /></div>
  {#if route.view === 'preview'}<FixturePreview {theme} />{/if}
</main>
