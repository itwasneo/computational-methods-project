import { Scene } from '../../examples/interface-rendering/model.mjs';
import { webgl } from '../../examples/interface-rendering/webgl.mjs';
import { nextPickedCity } from './inspection';
import type { Inspection } from './inspection';
import type { Theme } from './theme';

export function createViewer(
  canvas: HTMLCanvasElement,
  inspection: Inspection,
  onSelect: (id: number) => void,
  onError: (message: string) => void,
) {
  const scene = new Scene(inspection.coordinates);
  let renderer: ReturnType<typeof webgl> | null = null;
  let selected = 0;
  let theme: Theme = 'dark';
  let showTour = inspection.route !== null;
  let camera = { x: 0, y: 0, zoom: 0.85 };
  let frame = 0;
  let disposed = false;
  let drag: { id: number; x: number; y: number; camera: typeof camera; moved: boolean } | null = null;
  const events = new AbortController();

  function fail(message: string) {
    renderer?.destroy();
    renderer = null;
    drag = null;
    onError(message);
  }
  function transform() {
    const side = Math.min(canvas.clientWidth, canvas.clientHeight) * camera.zoom;
    const sx = 2 * side / canvas.clientWidth, sy = 2 * side / canvas.clientHeight;
    return [sx, sy, -camera.x * sx, -camera.y * sy];
  }
  function world(x: number, y: number) {
    const rect = canvas.getBoundingClientRect();
    const [sx, sy] = transform();
    return [(2 * (x - rect.left) / rect.width - 1) / sx + camera.x,
      (1 - 2 * (y - rect.top) / rect.height) / sy + camera.y];
  }
  function draw() {
    frame = 0;
    if (disposed || !renderer || !canvas.clientWidth || !canvas.clientHeight) return;
    try {
      const dpr = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.round(canvas.clientWidth * dpr));
      const height = Math.max(1, Math.round(canvas.clientHeight * dpr));
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      renderer.draw(transform(), selected, showTour && inspection.route !== null, dpr, theme);
    } catch (error) { fail(error instanceof Error ? error.message : String(error)); }
  }
  function requestDraw() {
    if (!disposed && !frame) frame = requestAnimationFrame(draw);
  }
  function fit() { camera = { x: 0, y: 0, zoom: 0.85 }; requestDraw(); }
  function zoom(factor: number) {
    camera.zoom = Math.min(10000, Math.max(0.05, camera.zoom * factor));
    requestDraw();
  }

  renderer = webgl(canvas, scene.display, fail, inspection.route);
  canvas.addEventListener('pointerdown', event => {
    if (!renderer || event.button !== 0) return;
    canvas.focus();
    canvas.setPointerCapture(event.pointerId);
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, camera: { ...camera }, moved: false };
  }, { signal: events.signal });
  canvas.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    drag.moved ||= Math.hypot(dx, dy) > 3;
    if (!drag.moved) return;
    const side = Math.min(canvas.clientWidth, canvas.clientHeight) * camera.zoom;
    camera.x = drag.camera.x - dx / side;
    camera.y = drag.camera.y + dy / side;
    requestDraw();
  }, { signal: events.signal });
  canvas.addEventListener('pointerup', event => {
    if (!drag || drag.id !== event.pointerId) return;
    if (!drag.moved) {
      const [x, y] = world(event.clientX, event.clientY);
      const radius = 8 / (Math.min(canvas.clientWidth, canvas.clientHeight) * camera.zoom);
      selected = nextPickedCity(scene.pick(x, y, radius), selected);
      onSelect(selected);
      requestDraw();
    }
    drag = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  }, { signal: events.signal });
  for (const name of ['pointercancel', 'lostpointercapture']) {
    canvas.addEventListener(name, () => { drag = null; }, { signal: events.signal });
  }
  canvas.addEventListener('wheel', event => {
    if (!renderer) return;
    event.preventDefault();
    const before = world(event.clientX, event.clientY);
    zoom(Math.exp(-event.deltaY * 0.001));
    const after = world(event.clientX, event.clientY);
    camera.x += before[0] - after[0]; camera.y += before[1] - after[1];
  }, { passive: false, signal: events.signal });
  canvas.addEventListener('keydown', event => {
    if (!renderer) return;
    const step = 0.05 / camera.zoom;
    switch (event.key) {
      case 'ArrowLeft': camera.x -= step; break;
      case 'ArrowRight': camera.x += step; break;
      case 'ArrowUp': camera.y += step; break;
      case 'ArrowDown': camera.y -= step; break;
      case '+': case '=': zoom(1.2); break;
      case '-': zoom(1 / 1.2); break;
      case 'Home': fit(); break;
      default: return;
    }
    event.preventDefault(); requestDraw();
  }, { signal: events.signal });
  const resize = new ResizeObserver(requestDraw);
  resize.observe(canvas);
  window.addEventListener('resize', requestDraw, { signal: events.signal });
  let dprQuery: MediaQueryList;
  function watchDpr() {
    dprQuery?.removeEventListener('change', dprChanged);
    dprQuery = matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    dprQuery.addEventListener('change', dprChanged, { once: true });
  }
  function dprChanged() { requestDraw(); watchDpr(); }
  watchDpr();
  requestDraw();

  return {
    fit, zoom,
    theme(value: Theme) { theme = value; requestDraw(); },
    select(id: number) {
      if (!Number.isInteger(id) || id < 0 || id >= scene.points.length / 2) return;
      selected = id; requestDraw();
    },
    tour(visible: boolean) { showTour = visible; requestDraw(); },
    destroy() {
      disposed = true;
      cancelAnimationFrame(frame);
      events.abort(); resize.disconnect();
      dprQuery.removeEventListener('change', dprChanged);
      renderer?.destroy(); renderer = null;
    },
  };
}
