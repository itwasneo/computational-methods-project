// Interactive reference for the chosen primitives, not a benchmark or application shell.
import {fixture, Scene} from './model.mjs';
import {webgl} from './webgl.mjs';

const $ = id => document.getElementById(id);
let scene, renderer, canvas, selected = -1, pending = false, drag;
let camera = {x: 0, y: 0, zoom: 0.85};
const dirty = new Set();
const status = message => { $('status').textContent = message; };
function controls() {
  for (const id of ['tour', 'inspect', 'move', 'fit']) $(id).disabled = !renderer;
}
function transform() {
  const side = Math.min(canvas.clientWidth, canvas.clientHeight) * camera.zoom;
  const sx = 2 * side / canvas.clientWidth, sy = 2 * side / canvas.clientHeight;
  return [sx, sy, -camera.x * sx, -camera.y * sy];
}
function world(clientX, clientY) {
  const rect = canvas.getBoundingClientRect(), [sx, sy] = transform();
  return [((clientX - rect.left) / rect.width * 2 - 1) / sx + camera.x,
    (1 - (clientY - rect.top) / rect.height * 2) / sy + camera.y];
}
function inspect(id) {
  if (!Number.isInteger(id) || id < 0 || id >= scene.points.length / 2) throw new Error('Invalid city ID');
  selected = id; $('city').value = id;
  $('x').value = scene.points[id * 2]; $('y').value = scene.points[id * 2 + 1];
  requestDraw();
}
function move(id, x, y) {
  scene.update(id, [x, y]); dirty.add(id); inspect(id);
}
function requestDraw() {
  if (pending) return;
  pending = true; requestAnimationFrame(draw);
}
function draw() {
  pending = false;
  if (!renderer) return;
  try {
    const dpr = devicePixelRatio;
    const width = Math.max(1, Math.round(canvas.clientWidth * dpr));
    const height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    for (const id of dirty) renderer.update(id);
    dirty.clear(); renderer.draw(transform(), selected, $('tour').checked, dpr);
  } catch (error) { failed(error.message); }
}
function failed(message) {
  const old = renderer; renderer = null; drag = null; old?.destroy();
  dirty.clear(); controls(); status(message);
}
function bindCanvas() {
  canvas.addEventListener('pointerdown', event => {
    if (!renderer) return;
    canvas.focus(); canvas.setPointerCapture(event.pointerId);
    const position = world(event.clientX, event.clientY);
    const radius = 8 / (Math.min(canvas.clientWidth, canvas.clientHeight) * camera.zoom);
    const hits = scene.pick(...position, radius);
    const id = hits[(hits.indexOf(selected) + 1) % hits.length];
    if (id !== undefined) inspect(id);
    status(`Picked ${hits.length} candidate(s); selected ${selected}.`);
    drag = {x: event.clientX, y: event.clientY, camera: {...camera}, moving: event.shiftKey && selected >= 0};
  });
  canvas.addEventListener('pointermove', event => {
    if (!drag) return;
    if (drag.moving) {
      const [x, y] = world(event.clientX, event.clientY);
      try { move(selected, x * scene.bounds.span + scene.bounds.x, y * scene.bounds.span + scene.bounds.y); }
      catch (error) { status(error.message); }
    } else {
      const side = Math.min(canvas.clientWidth, canvas.clientHeight) * camera.zoom;
      camera.x = drag.camera.x - (event.clientX - drag.x) / side;
      camera.y = drag.camera.y + (event.clientY - drag.y) / side; requestDraw();
    }
  });
  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) canvas.addEventListener(name, () => { drag = null; });
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    if (!renderer) return;
    const before = world(event.clientX, event.clientY);
    camera.zoom = Math.max(0.05, Math.min(10000, camera.zoom * Math.exp(-event.deltaY * 0.001)));
    const after = world(event.clientX, event.clientY);
    camera.x += before[0] - after[0]; camera.y += before[1] - after[1]; requestDraw();
  }, {passive: false});
  canvas.addEventListener('keydown', event => {
    if (!renderer) return;
    const delta = 0.05 / camera.zoom;
    const actions = {ArrowLeft: () => camera.x -= delta, ArrowRight: () => camera.x += delta,
      ArrowUp: () => camera.y += delta, ArrowDown: () => camera.y -= delta,
      '+': () => camera.zoom = Math.min(10000, camera.zoom * 1.2),
      '-': () => camera.zoom = Math.max(0.05, camera.zoom / 1.2)};
    if (actions[event.key]) { event.preventDefault(); actions[event.key](); requestDraw(); }
  });
}
function load() {
  const old = renderer; renderer = null; old?.destroy();
  dirty.clear(); drag = null; selected = -1;
  try {
    const count = Number($('count').value), kind = $('kind').value;
    scene = new Scene(fixture(count, kind)); camera = {x: 0, y: 0, zoom: 0.85};
    canvas = document.createElement('canvas'); canvas.tabIndex = 0;
    canvas.setAttribute('aria-label', 'Synthetic city view; arrows pan, plus/minus zoom. City inspection controls follow.');
    $('surface').replaceChildren(canvas); bindCanvas();
    renderer = webgl(canvas, scene.display, failed);
    $('city').max = count - 1; inspect(0);
    status(`Ready: WebGL2, ${count} ${kind} cities. Synthetic graphics only.`);
  } catch (error) { failed(error.message); }
  controls();
}
$('load').onclick = load;
$('fit').onclick = () => { camera = {x: 0, y: 0, zoom: 0.85}; requestDraw(); };
$('tour').onchange = requestDraw;
$('inspect').onclick = () => { try { inspect(Number($('city').value)); } catch (error) { status(error.message); } };
$('move').onclick = () => {
  try {
    if (selected < 0 || !$('x').value || !$('y').value) throw new Error('Inspect a city and supply X/Y');
    move(selected, Number($('x').value), Number($('y').value));
  } catch (error) { status(error.message); }
};
new ResizeObserver(requestDraw).observe($('surface'));
window.addEventListener('resize', requestDraw);
function watchDpr() {
  matchMedia(`(resolution: ${devicePixelRatio}dppx)`).addEventListener('change', () => { requestDraw(); watchDpr(); }, {once: true});
}
watchDpr(); load();
