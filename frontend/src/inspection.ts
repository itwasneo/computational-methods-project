import { fixture } from './rendering/model.mjs';

/** Display data only; never a binding from a real result to an instance artifact. */
export type Inspection = {
  coordinates: Float64Array;
  route: Uint32Array | null;
};

export function createPreview(): Inspection {
  const cities = 128;
  const coordinates = fixture(cities, 'uniform', 12345);
  // Coincident city IDs exercise picking without changing canonical coordinate ownership.
  coordinates.set(coordinates.subarray(0, 2), 2);
  // Synthetic permutation, not a solver result or claimed optimum.
  const route = Uint32Array.from({ length: cities }, (_, id) => (id * 17) % cities);
  return { coordinates, route };
}

export function nextPickedCity(hits: number[], selected: number): number {
  return hits.length ? hits[(hits.indexOf(selected) + 1) % hits.length] : selected;
}
