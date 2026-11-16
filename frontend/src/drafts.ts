import { wireGuard } from './wire.ts';
import type { DistanceMode, ScientificConfiguration } from './contracts.ts';

export interface ConfigurationDraft {
  backend?: 'serial' | 'multicore';
  threads?: number;
  implementations: string[];
  modes: DistanceMode[];
  starts: string;
  repetitions: number;
  solverSeed: string;
  timed: boolean;
  seconds: number;
  history: boolean;
  interval: number;
  points: number;
  original: ScientificConfiguration | null;
  originalSnapshot: string;
  snapshot: string | null;
}
const fields = [
  'implementations',
  'modes',
  'starts',
  'repetitions',
  'solverSeed',
  'timed',
  'seconds',
  'history',
  'interval',
  'points',
  'original',
  'originalSnapshot',
  'snapshot',
].sort();
export function isConfigurationDraft(value: unknown): value is ConfigurationDraft {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const draft = value as Record<string, unknown>;
  return (
    Object.keys(draft)
      .filter((key) => key !== 'backend' && key !== 'threads')
      .sort()
      .join(',') === fields.join(',') &&
    (draft.backend === undefined || draft.backend === 'serial' || draft.backend === 'multicore') &&
    (draft.threads === undefined ||
      (typeof draft.threads === 'number' &&
        Number.isInteger(draft.threads) &&
        draft.threads >= 1 &&
        draft.threads <= 8)) &&
    ((draft.backend ?? 'serial') !== 'serial' || (draft.threads ?? 1) === 1) &&
    (draft.backend !== 'multicore' || (draft.timed === false && draft.history === false)) &&
    Array.isArray(draft.implementations) &&
    draft.implementations.length <= 32 &&
    draft.implementations.every((v) => typeof v === 'string' && v.length <= 128) &&
    Array.isArray(draft.modes) &&
    draft.modes.length <= 2 &&
    draft.modes.every((v) => v === 'dense' || v === 'on_demand') &&
    ['starts', 'solverSeed', 'originalSnapshot'].every(
      (key) => typeof draft[key] === 'string' && draft[key].length <= 4096,
    ) &&
    typeof draft.solverSeed === 'string' &&
    /^(0|[1-9][0-9]{0,19})$/.test(draft.solverSeed) &&
    BigInt(draft.solverSeed) <= (1n << 64n) - 1n &&
    typeof draft.starts === 'string' &&
    (!draft.starts.trim() ||
      draft.starts
        .split(',')
        .every((v) => /^(0|[1-9][0-9]{0,3})$/.test(v.trim()) && Number(v) < 4096)) &&
    ['repetitions', 'seconds', 'interval', 'points'].every(
      (key) => typeof draft[key] === 'number' && Number.isFinite(draft[key]),
    ) &&
    typeof draft.timed === 'boolean' &&
    typeof draft.history === 'boolean' &&
    (draft.original === null || wireGuard('ScientificConfiguration')(draft.original)) &&
    (draft.snapshot === null ||
      (typeof draft.snapshot === 'string' && draft.snapshot.length <= 128))
  );
}
type DraftStorage = Pick<Storage, 'getItem' | 'setItem'>;
function browserStorage(): DraftStorage | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}
/** Optional, bounded convenience state. Never stores a grant, preflight or submission authority. */
export function configurationDraftStore(root: string, storage = browserStorage()) {
  const key = `tsp-hpc-configuration-draft-v1:${root}`;
  let last = '';
  return {
    read(): ConfigurationDraft | null {
      try {
        const raw = storage?.getItem(key);
        if (!raw || raw.length > 16_384) return null;
        const value: unknown = JSON.parse(raw);
        if (!isConfigurationDraft(value)) return null;
        last = raw;
        return value;
      } catch {
        return null;
      }
    },
    save(value: ConfigurationDraft): boolean {
      if (!isConfigurationDraft(value) || !storage || !root) return false;
      const raw = JSON.stringify(value);
      if (raw.length > 16_384) return false;
      try {
        if (raw !== last) storage.setItem(key, raw);
        last = raw;
        return true;
      } catch {
        return false;
      }
    },
  };
}
