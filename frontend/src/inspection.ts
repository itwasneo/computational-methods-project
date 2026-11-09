import { fixture } from '../../examples/interface-rendering/model.mjs';
import type { Trial } from './fixtures';

export type Inspection = {
  fixtureId: string;
  coordinates: Float64Array;
  route: Uint32Array | null;
};

// Explicit presentation-fixture bindings, never fallback geometry for real artifacts.
const bindings: Record<string, { fixtureId: string; cities: number; tour: boolean }> = {
  'preview-01': { fixtureId: 'geometry-preview-uniform-128', cities: 128, tour: true },
  'preview-02': { fixtureId: 'geometry-preview-uniform-128', cities: 128, tour: false },
};

export function inspectFixture(trial: Trial): Inspection | null {
  const binding = bindings[trial.id];
  if (!binding) return null;
  if (trial.cities !== binding.cities) throw new Error('Fixture city count does not match the selected experiment.');
  const coordinates = fixture(binding.cities, 'uniform', 12345);
  // Two coincident city IDs exercise picking and the non-canvas selection path.
  coordinates.set(coordinates.subarray(0, 2), 2);
  // A deliberately synthetic permutation, not a solver output or claimed optimum.
  const route = binding.tour ? Uint32Array.from({ length: binding.cities }, (_, id) => (id * 17) % binding.cities) : null;
  return { fixtureId: binding.fixtureId, coordinates, route };
}

export function nextPickedCity(hits: number[], selected: number): number {
  return hits.length ? hits[(hits.indexOf(selected) + 1) % hits.length] : selected;
}
