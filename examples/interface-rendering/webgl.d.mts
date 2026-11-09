/** Typed boundary for the shared WebGL2 reference primitive. */
export function webgl(
  canvas: HTMLCanvasElement,
  display: Float32Array,
  lost: (message: string) => void,
  route?: Uint32Array | null,
): {
  update(id: number): void;
  draw(transform: number[], selected: number, tour: boolean, dpr: number, theme?: 'light' | 'dark'): void;
  destroy(): void;
};
