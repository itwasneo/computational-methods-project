import { wireGuard } from './wire.ts';
import type { AnalysisQuery } from './contracts.ts';
export interface Route {
  view: 'experiments' | 'saved' | 'preview';
  root?: string;
  job?: string;
  attempt?: string;
  analysis?: AnalysisQuery;
}
const queryNames = [
  'ScalarAnalysisQuery',
  'ProgressAnalysisQuery',
  'TargetAnalysisQuery',
  'ComparisonAnalysisQuery',
  'ReferenceAnalysisQuery',
  'FacetAnalysisQuery',
] as const;
export function parseRoute(hash: string): Route {
  if (hash.length > 8192) return { view: 'experiments' };
  const [view, search = ''] = hash.replace(/^#\/?/, '').split('?');
  if (view === 'saved' || view === 'preview') return { view };
  const values = new URLSearchParams(search),
    route: Route = { view: 'experiments' };
  for (const key of ['root', 'job', 'attempt'] as const) {
    const value = values.get(key);
    if (value && value.length <= 128) route[key] = value;
  }
  const raw = values.get('analysis');
  if (raw && raw.length <= 4096) {
    try {
      const query: unknown = JSON.parse(raw);
      if (queryNames.some((name) => wireGuard(name)(query))) {
        const checked = query as AnalysisQuery;
        if (
          checked.limit >= 1 &&
          checked.limit <= 500 &&
          checked.cursor === null &&
          (!checked.attempt_ids || checked.attempt_ids.length <= 128)
        )
          route.analysis = checked;
      }
    } catch {
      /* Malformed URL data never becomes authority or a request body. */
    }
  }
  return route;
}
export function routeHash(route: Route) {
  const values = new URLSearchParams();
  if (route.view === 'experiments') {
    for (const key of ['root', 'job', 'attempt'] as const)
      if (route[key]) values.set(key, route[key]);
    if (route.analysis) values.set('analysis', JSON.stringify({ ...route.analysis, cursor: null }));
  }
  return `#${route.view}${values.size ? `?${values}` : ''}`;
}
export class Navigation {
  state: Route;
  experiments: Route;
  #listeners = new Set<(route: Route, navigated: boolean) => void>();
  #read = () => this.#publish(parseRoute(window.location.hash));
  constructor() {
    this.state = parseRoute(window.location.hash);
    this.experiments = this.state.view === 'experiments' ? this.state : { view: 'experiments' };
    window.addEventListener('hashchange', this.#read);
  }
  #publish(route: Route) {
    this.state = route;
    if (route.view === 'experiments') this.experiments = route;
    for (const listener of this.#listeners) listener(route, true);
  }
  rememberExperiment(route: Route) {
    if (route.view !== 'experiments') return;
    this.experiments = route;
    // Update the return link, not the active URL, focus, or route-loading commands.
    for (const listener of this.#listeners) listener(this.state, false);
  }
  subscribe(listener: (route: Route, navigated: boolean) => void) {
    this.#listeners.add(listener);
    listener(this.state, true);
    return () => {
      this.#listeners.delete(listener);
    };
  }
  go(route: Route, replace = false) {
    const hash = routeHash(route);
    if (hash === window.location.hash) return;
    window.history[replace ? 'replaceState' : 'pushState'](null, '', hash);
    this.#publish(route);
  }
  dispose() {
    window.removeEventListener('hashchange', this.#read);
    this.#listeners.clear();
  }
}
