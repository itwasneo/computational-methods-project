import { createContext } from 'svelte';
import type { WorkspaceSession } from './session';
import type { Navigation } from './navigation';
export const [getSession, provideSession] = createContext<WorkspaceSession>();
export const [getNavigation, provideNavigation] = createContext<Navigation>();
