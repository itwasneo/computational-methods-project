/** Typed boundary for display-only geometry; not a scientific artifact API. */
export function fixture(count: number, kind?: string, seed?: number): Float64Array;
export function bounds(points: Float64Array): { x: number; y: number; span: number };
export class Scene {
  constructor(points: Float64Array);
  points: Float64Array;
  bounds: { x: number; y: number; span: number };
  display: Float32Array;
  normalized(id: number): number[];
  update(id: number, coordinates?: number[]): void;
  pick(x: number, y: number, radius: number): number[];
}
