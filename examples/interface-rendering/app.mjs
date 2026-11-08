import {fixture, Scene, percentile} from './model.mjs';
import {webgl, webgpu} from './renderers.mjs';

const $ = id => document.getElementById(id);
let scene, renderer, canvas, loadedConfig, selected = -1, pending = false, generation = 0;
let camera = {x: 0, y: 0, zoom: 0.85}, drag, measure, latest;
let pickTimes = [], uploadTimes = [], frameTimes = [], previousFrame, edited = false, setupMs;
const seed = 12345;
const status = message => { $('status').textContent = message; };
function transform() {
  const side = Math.min(canvas.clientWidth, canvas.clientHeight) * camera.zoom;
  const sx = 2 * side / canvas.clientWidth, sy = 2 * side / canvas.clientHeight;
  return [sx, sy, -camera.x * sx, -camera.y * sy];
}
function world(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const [sx, sy] = transform();
  return [((clientX - rect.left) / rect.width * 2 - 1) / sx + camera.x,
    (1 - (clientY - rect.top) / rect.height * 2) / sy + camera.y];
}
function inspect(id) {
  if (!Number.isInteger(id) || id < 0 || id >= scene.points.length / 2) throw new Error('Invalid city ID');
  selected = id; $('city').value = id;
  $('x').value = scene.points[id * 2]; $('y').value = scene.points[id * 2 + 1];
  requestDraw();
}
// Only dirty point IDs are uploaded in the next animation frame.
const dirty = new Set();
function move(id, x, y) {
  if (![x, y].every(Number.isFinite)) throw new Error('Coordinates must be finite');
  scene.update(id, [x, y]); edited = true; dirty.add(id); inspect(id);
}
function requestDraw() {
  if (pending) return;
  pending = true; requestAnimationFrame(draw);
}
function draw(time) {
  pending = false;
  if (!renderer) return;
  try {
    const dpr = devicePixelRatio;
    const width = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    if (measure) {
      if (previousFrame !== undefined) frameTimes.push(time - previousFrame);
      previousFrame = time;
      const elapsed = time - measure.start;
      camera.x = Math.sin(elapsed / 900) * 0.1;
      camera.y = Math.cos(elapsed / 900) * 0.1;
      camera.zoom = 0.85 + 0.1 * Math.sin(elapsed / 500);
    }
    const start = performance.now();
    for (const id of dirty) renderer.update(id);
    dirty.clear();
    renderer.draw(transform(), selected, $('tour').checked, dpr);
    if (measure) uploadTimes.push(performance.now() - start);
    if (measure && time - measure.start >= 10000) finish();
    if (measure) requestDraw();
  } catch (error) { failed(error.message); }
}
function failed(message) {
  generation++; // Ignore asynchronous device-loss callbacks during cleanup.
  const old = renderer; renderer = null; measure = null; old?.destroy();
  for (const id of ['load', 'api', 'kind', 'count', 'tour', 'inspect', 'move']) $(id).disabled = false;
  $('measure').disabled = true;
  status(message); // Explicit failure, never silently change API.
}
const stats = values => ({samples: values.length, p50: percentile(values, 0.5),
  p95: percentile(values, 0.95), p99: percentile(values, 0.99), max: values.length ? Math.max(...values) : null});
function finish() {
  const config = measure.config;
  const saved = measure.camera;
  measure = null; camera = saved; $('measure').disabled = false;
  for (const id of ['load', 'api', 'kind', 'count', 'tour', 'inspect', 'move']) $(id).disabled = false;
  latest = {schema: 'tsp-rendering-diagnostic-v1', synthetic: true, acceptance: 'NOT EVALUATED',
    limitations: ['RAF intervals are not GPU/presentation timings', 'Submission CPU time does not wait for GPU',
      'Picking samples are isolated CPU timings, not input-to-present latency',
      'Memory, idle CPU, real input feedback and concurrent ingestion/solver gates require separate measurement'],
    browser: navigator.userAgent, viewport: {width: innerWidth, height: innerHeight, dpr: devicePixelRatio},
    canvas: {width: canvas.clientWidth, height: canvas.clientHeight}, gpu: renderer.info, ...config,
    rafIntervalMs: stats(frameTimes), submissionCpuMs: stats(uploadTimes), pickingCpuMs: stats(pickTimes)};
  $('report').textContent = JSON.stringify(latest, null, 2);
  status('Diagnostic complete; not an acceptance result. Export and record environment/revision separately.');
  requestDraw();
}
function bindCanvas() {
  canvas.addEventListener('pointerdown', event => {
    if (!renderer || measure) return;
    canvas.focus(); canvas.setPointerCapture(event.pointerId);
    const position = world(event.clientX, event.clientY);
    const radius = 8 / (Math.min(canvas.clientWidth, canvas.clientHeight) * camera.zoom);
    const start = performance.now();
    const hits = scene.pick(...position, radius);
    const duration = performance.now() - start;
    const id = hits[(hits.indexOf(selected) + 1) % hits.length];
    if (id !== undefined) inspect(id);
    status(`Picked ${hits.length} candidate(s) in ${duration.toFixed(2)} ms CPU; selected ${selected}.`);
    drag = {x: event.clientX, y: event.clientY, position, camera: {...camera}, moving: event.shiftKey && selected >= 0};
  });
  canvas.addEventListener('pointermove', event => {
    if (!drag || measure) return;
    if (drag.moving) {
      const [x, y] = world(event.clientX, event.clientY);
      move(selected, x * scene.bounds.span + scene.bounds.x, y * scene.bounds.span + scene.bounds.y);
    } else {
      const side = Math.min(canvas.clientWidth, canvas.clientHeight) * camera.zoom;
      camera.x = drag.camera.x - (event.clientX - drag.x) / side;
      camera.y = drag.camera.y + (event.clientY - drag.y) / side;
      requestDraw();
    }
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(name, () => { drag = null; });
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    if (measure || !renderer) return;
    const before = world(event.clientX, event.clientY);
    camera.zoom = Math.max(0.05, Math.min(10000, camera.zoom * Math.exp(-event.deltaY * 0.001)));
    const after = world(event.clientX, event.clientY);
    camera.x += before[0] - after[0]; camera.y += before[1] - after[1]; requestDraw();
  }, {passive: false});
  canvas.addEventListener('keydown', event => {
    if (measure || !renderer) return;
    const delta = 0.05 / camera.zoom;
    const actions = {ArrowLeft: () => camera.x -= delta, ArrowRight: () => camera.x += delta,
      ArrowUp: () => camera.y += delta, ArrowDown: () => camera.y -= delta,
      '+': () => camera.zoom = Math.min(10000, camera.zoom * 1.2),
      '-': () => camera.zoom = Math.max(0.05, camera.zoom / 1.2)};
    if (actions[event.key]) { event.preventDefault(); actions[event.key](); requestDraw(); }
  });
}
async function load() {
  const current = ++generation;
  $('load').disabled = true; $('measure').disabled = true;
  const old = renderer; renderer = null; old?.destroy();
  dirty.clear(); selected = -1; latest = null; $('report').textContent = '';
  canvas = document.createElement('canvas'); canvas.tabIndex = 0;
  canvas.setAttribute('aria-label', 'Synthetic city view; arrows pan, plus/minus zoom. City inspection controls follow.');
  $('surface').replaceChildren(canvas); bindCanvas();
  const start = performance.now();
  const count = Number($('count').value), kind = $('kind').value, api = $('api').value;
  loadedConfig = {cities: count, fixture: kind, seed};
  scene = new Scene(fixture(count, kind, seed)); edited = false; camera = {x: 0, y: 0, zoom: 0.85};
  try {
    renderer = await (api === 'WebGPU' ? webgpu : webgl)(canvas, scene.display,
      message => { if (generation === current) failed(message); });
    $('city').max = count - 1; inspect(0);
    setupMs = performance.now() - start;
    status(`Ready: ${api}, ${count} ${kind} cities; fixture + setup ${setupMs.toFixed(1)} ms. Verify hardware acceleration before recording evidence.`);
    $('measure').disabled = false;
  } catch (error) { failed(error.message); }
  finally { $('load').disabled = false; }
}
$('load').onclick = load;
$('fit').onclick = () => { if (!measure) { camera = {x: 0, y: 0, zoom: 0.85}; requestDraw(); } };
$('tour').onchange = requestDraw;
$('inspect').onclick = () => { try { inspect(Number($('city').value)); } catch (error) { status(error.message); } };
$('move').onclick = () => {
  try {
    if (selected < 0 || !$('x').value || !$('y').value) throw new Error('Inspect a city and supply X/Y');
    move(selected, Number($('x').value), Number($('y').value));
  } catch (error) { status(error.message); }
};
$('measure').onclick = () => {
  if (!renderer || measure) return;
  pickTimes = []; uploadTimes = []; frameTimes = []; previousFrame = undefined;
  // Deterministic spatial queries, outside the frame diagnostic; not real pointer latency.
  for (let i = 0; i < 100; i++) {
    const [x, y] = scene.normalized(Math.floor(i * scene.points.length / 200));
    const start = performance.now(); scene.pick(x, y, 0.01); pickTimes.push(performance.now() - start);
  }
  measure = {start: performance.now(), camera: {...camera}, config: {...loadedConfig,
    tour: $('tour').checked, edited, fixtureAndSetupMs: setupMs,
    note: 'Reload fixture before each reproducible diagnostic; edited scenes are not reproducible fixtures'}};
  for (const id of ['load', 'api', 'kind', 'count', 'tour', 'measure', 'inspect', 'move']) $(id).disabled = true;
  status('Running 10-second scripted pan/zoom; leave this tab visible.'); requestDraw();
};
$('export').onclick = () => {
  if (!latest) { status('Run a diagnostic first.'); return; }
  const url = URL.createObjectURL(new Blob([JSON.stringify(latest, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = 'rendering-diagnostic.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
new ResizeObserver(requestDraw).observe($('surface'));
window.addEventListener('resize', requestDraw);
function watchDpr() {
  matchMedia(`(resolution: ${devicePixelRatio}dppx)`).addEventListener('change', () => {
    requestDraw(); watchDpr();
  }, {once: true});
}
watchDpr();
document.addEventListener('visibilitychange', () => {
  if (document.hidden && measure) failed('Diagnostic invalidated: tab was hidden. Reload the fixture and retry.');
});
load();
