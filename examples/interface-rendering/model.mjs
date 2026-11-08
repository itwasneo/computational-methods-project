// Synthetic graphics fixtures only: not solver inputs or benchmark results.
export function fixture(count, kind = 'uniform', seed = 12345) {
  if (!Number.isInteger(count) || count < 2) throw new Error('count must be >= 2');
  if (!['uniform', 'clustered', 'duplicates', 'collinear', 'coincident'].includes(kind)) {
    throw new Error('unknown fixture');
  }
  let state = seed >>> 0;
  const random = () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 2 ** 32;
  };
  const points = new Float64Array(count * 2);
  for (let i = 0; i < count; i++) {
    let x = random(), y = random();
    if (kind === 'clustered') {
      x = (i % 2) * 0.7 + x * 0.1;
      y = (Math.floor(i / 2) % 2) * 0.7 + y * 0.1;
    }
    if (kind === 'duplicates' && i % 3 !== 0) {
      x = points[(i - i % 3) * 2];
      y = points[(i - i % 3) * 2 + 1];
    }
    if (kind === 'collinear') y = 0;
    if (kind === 'coincident') x = y = 0;
    points.set([x, y], i * 2);
  }
  return points;
}

export function bounds(points) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i < points.length; i += 2) {
    minX = Math.min(minX, points[i]); maxX = Math.max(maxX, points[i]);
    minY = Math.min(minY, points[i + 1]); maxY = Math.max(maxY, points[i + 1]);
  }
  const extent = Math.max(maxX - minX, maxY - minY);
  return {x: minX / 2 + maxX / 2, y: minY / 2 + maxY / 2,
    span: (Number.isFinite(extent) ? extent : Math.max(Math.abs(minX), Math.abs(maxX), Math.abs(minY), Math.abs(maxY))) || 1};
}

function project(value, center, span) {
  const delta = value - center;
  return Number.isFinite(delta) ? delta / span : value / span - center / span;
}

// Fixed projection is display-only; never round-trip through float32 to edit data.
export class Scene {
  constructor(points) {
    if (!(points instanceof Float64Array) || points.length < 4 || points.length % 2 || !points.every(Number.isFinite)) {
      throw new Error('Scene requires at least two finite float64 coordinate pairs');
    }
    this.points = points.slice();
    this.bounds = bounds(points);
    this.display = new Float32Array(points.length);
    this.grid = new Map();
    for (let id = 0; id < points.length / 2; id++) this.update(id);
  }
  normalized(id) {
    return [project(this.points[id * 2], this.bounds.x, this.bounds.span),
      project(this.points[id * 2 + 1], this.bounds.y, this.bounds.span)];
  }
  key(x, y) { return `${Math.floor(x * 50)},${Math.floor(y * 50)}`; }
  update(id, coordinates) {
    if (!Number.isInteger(id) || id < 0 || id >= this.points.length / 2) throw new Error('Invalid city ID');
    if (coordinates && (coordinates.length !== 2 || !coordinates.every(Number.isFinite))) {
      throw new Error('Coordinates must be finite pairs');
    }
    const next = coordinates ?? this.points.subarray(id * 2, id * 2 + 2);
    const x = project(next[0], this.bounds.x, this.bounds.span);
    const y = project(next[1], this.bounds.y, this.bounds.span);
    // Reject an unsupported edit before changing canonical data, the grid or GPU staging data.
    // A production viewer may instead rebase its projection; never clamp scientific coordinates.
    if (![x, y].every(value => Number.isFinite(Math.fround(value)))) {
      throw new Error('Edit exceeds this fixture’s float32 display projection; reload/rebase before editing');
    }
    const [oldX, oldY] = this.normalized(id);
    const oldKey = this.key(oldX, oldY), oldCell = this.grid.get(oldKey);
    oldCell?.delete(id);
    if (oldCell?.size === 0) this.grid.delete(oldKey);
    if (coordinates) this.points.set(coordinates, id * 2);
    this.display.set([x, y], id * 2);
    const key = this.key(x, y);
    if (!this.grid.has(key)) this.grid.set(key, new Set());
    this.grid.get(key).add(id);
  }
  pick(x, y, radius) {
    if (![x, y, radius].every(Number.isFinite) || radius < 0) throw new Error('Pick requires finite coordinates and a nonnegative radius');
    const hits = [];
    const minX = Math.floor((x - radius) * 50), maxX = Math.floor((x + radius) * 50);
    const minY = Math.floor((y - radius) * 50), maxY = Math.floor((y + radius) * 50);
    const width = maxX - minX + 1, height = maxY - minY + 1;
    // Large float64 cell indices cannot reliably advance by one. Bound traversal by
    // safe integer offsets, falling back to a finite scan for unsafe or huge ranges.
    const scan = ![minX, maxX, minY, maxY].every(Number.isSafeInteger)
      || width * height > this.points.length / 2;
    const consider = id => {
      const [px, py] = this.normalized(id);
      const distance = Math.hypot(px - x, py - y);
      if (distance <= radius) hits.push({id, distance});
    };
    if (scan) {
      for (let id = 0; id < this.points.length / 2; id++) consider(id);
    } else {
      for (let ix = 0; ix < width; ix++) {
        for (let iy = 0; iy < height; iy++) {
          for (const id of this.grid.get(`${minX + ix},${minY + iy}`) ?? []) consider(id);
        }
      }
    }
    return hits.sort((a, b) => a.distance - b.distance || a.id - b.id).map(hit => hit.id);
  }
}

export function percentile(values, fraction) {
  if (!values.length) return null;
  return [...values].sort((a, b) => a - b)[Math.ceil(values.length * fraction) - 1];
}
