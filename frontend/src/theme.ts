export type Theme = 'light' | 'dark';
const key = 'tsp-hpc-theme';

export function resolveTheme(saved: string | null, prefersDark: boolean): Theme {
  return saved === 'light' || saved === 'dark' ? saved : prefersDark ? 'dark' : 'light';
}

export function readThemePreference(): Theme | 'system' {
  try {
    const saved = localStorage.getItem(key);
    return saved === 'light' || saved === 'dark' ? saved : 'system';
  } catch {
    return 'system';
  }
}
export function setThemePreference(preference: Theme | 'system'): Theme {
  if (preference === 'system') {
    try {
      localStorage.removeItem(key);
    } catch {
      /* Preference still applies in memory. */
    }
    const theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    applyTheme(theme, false);
    return theme;
  }
  applyTheme(preference);
  return preference;
}
export function readTheme(): Theme {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(key);
  } catch {
    /* Storage can be disabled. */
  }
  return resolveTheme(saved, matchMedia('(prefers-color-scheme: dark)').matches);
}

export function applyTheme(theme: Theme, persist = true) {
  document.documentElement.dataset.theme = theme;
  if (persist) {
    try {
      localStorage.setItem(key, theme);
    } catch {
      /* Theme still works for this session. */
    }
  }
}
