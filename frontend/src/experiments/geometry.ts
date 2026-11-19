import type { VerifiedGeometry } from '../contracts.ts';
import type { Inspection } from '../inspection.ts';

function bytes(text: string, length: number) {
  if (
    text.length !== 4 * Math.ceil(length / 3) ||
    !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(text)
  )
    throw new Error('Invalid geometry encoding.');
  const raw = atob(text);
  if (raw.length !== length) throw new Error('Invalid geometry length.');
  return new DataView(Uint8Array.from(raw, (c) => c.charCodeAt(0)).buffer);
}
export function decodeGeometry(value: VerifiedGeometry): Inspection {
  const n = value.instance.n_cities;
  if (
    !Number.isSafeInteger(n) ||
    n < 2 ||
    n > 4096 ||
    value.coordinates.encoding !== 'f64-le-base64'
  )
    throw new Error('Unsupported geometry.');
  const raw = bytes(value.coordinates.data, n * 16);
  const coordinates = new Float64Array(n * 2);
  for (let i = 0; i < coordinates.length; i++) {
    coordinates[i] = raw.getFloat64(i * 8, true);
    if (!Number.isFinite(coordinates[i])) throw new Error('Nonfinite coordinate.');
  }
  let route: Uint32Array | null = null;
  if (value.tour) {
    if (value.tour.encoding !== 'u32-le-base64') throw new Error('Invalid tour encoding.');
    const rawTour = bytes(value.tour.data, n * 4),
      seen = new Uint8Array(n);
    route = new Uint32Array(n);
    for (let i = 0; i < n; i++) {
      const city = rawTour.getUint32(i * 4, true);
      if (city >= n || seen[city]) throw new Error('Invalid tour permutation.');
      route[i] = city;
      seen[city] = 1;
    }
  }
  return { coordinates, route };
}
