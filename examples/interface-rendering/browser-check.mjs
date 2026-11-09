// Optional real-browser correctness check via an already running local WebDriver.
// Uses readback ONLY in this test, never in the renderer or timing diagnostic.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';

const [endpoint = 'http://127.0.0.1:4444', browser = 'firefox', dpr = '1'] = process.argv.slice(2);
const url = new URL(endpoint);
assert.ok(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname), 'Use a local WebDriver');
assert.ok(['firefox', 'chrome'].includes(browser) && ['1', '2'].includes(dpr), 'Choose firefox/chrome and DPR 1/2');
const files = new Map(await Promise.all(['index.html', 'app.mjs', 'model.mjs', 'renderers.mjs'].map(async name =>
  [name === 'index.html' ? '/' : `/${name}`, await readFile(new URL(name, import.meta.url))])));
const server = createServer((request, response) => {
  const data = files.get(request.url);
  response.writeHead(data ? 200 : 404, {'Content-Type': request.url === '/' ? 'text/html' : 'text/javascript'});
  response.end(data ?? 'Not found');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
async function call(method, path, body) {
  const response = await fetch(`${endpoint.replace(/\/$/, '')}${path}`, {method,
    headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(30000)});
  const {value} = await response.json();
  if (!response.ok) throw new Error(JSON.stringify(value));
  return value;
}
let session;
try {
  const options = browser === 'firefox'
    ? {'moz:firefoxOptions': {args: ['-headless'], prefs: {'layout.css.devPixelsPerPx': dpr}}}
    : {'goog:chromeOptions': {args: ['--headless=new', `--force-device-scale-factor=${dpr}`]}};
  const opened = await call('POST', '/session', {capabilities: {alwaysMatch: {browserName: browser, ...options}}});
  session = opened.sessionId;
  const root = `/session/${session}`;
  const js = (script, args = []) => call('POST', `${root}/execute/sync`, {script, args});
  const asyncJS = (script, args = []) => call('POST', `${root}/execute/async`, {script, args});
  await call('POST', `${root}/window/rect`, {width: 1440, height: 1050});
  await call('POST', `${root}/url`, {url: `http://127.0.0.1:${server.address().port}`});
  assert.equal(await js('return devicePixelRatio'), Number(dpr));
  const results = {};
  for (const api of ['WebGL2', 'WebGPU']) {
    let cases = 0;
    for (const count of [1000, 10000, 50000]) {
      for (const kind of ['uniform', 'clustered', 'duplicates', 'collinear', 'coincident']) {
        const status = await asyncJS(`
          const [api, count, kind, done] = arguments;
          (async () => {
            const $ = id => document.getElementById(id);
            // Wait for the automatic first load before starting a controlled fixture.
            while ($('load').disabled) await new Promise(resolve => setTimeout(resolve, 10));
            $('api').value = api; $('count').value = count; $('kind').value = kind;
            await $('load').onclick(); done($('status').textContent);
          })().catch(error => done({error: error.message}));
        `, [api, count, kind]);
        if (api === 'WebGPU' && typeof status === 'string' && !status.startsWith('Ready:')) {
          // Absence is explicit, never a passing WebGPU case. Shader/validation failures are errors.
          assert.match(status, /WebGPU unavailable|No WebGPU adapter/);
          results[api] = {unsupported: status}; break;
        }
        assert.match(status, new RegExp(`^Ready: ${api}, ${count} ${kind}`));
        for (const city of kind === 'coincident' ? [0, count - 1, 0] : [0]) {
          const sampled = await asyncJS(`
            const [city, done] = arguments;
            (async () => {
              const {Scene, fixture} = await import('./model.mjs');
              const $ = id => document.getElementById(id);
              const scene = new Scene(fixture(Number($('count').value), $('kind').value));
              const [x, y] = scene.normalized(city);
              $('city').value = city; $('inspect').click(); $('tour').checked = true; $('tour').onchange();
              requestAnimationFrame(() => {
                try {
                  const canvas = document.querySelector('#surface canvas');
                  const copy = document.createElement('canvas'); copy.width = canvas.width; copy.height = canvas.height;
                  const context = copy.getContext('2d', {willReadFrequently: true}); context.drawImage(canvas, 0, 0);
                  const side = Math.min(canvas.clientWidth, canvas.clientHeight) * 0.85 * devicePixelRatio;
                  done({color: [...context.getImageData(Math.floor(canvas.width / 2 + x * side),
                    Math.floor(canvas.height / 2 - y * side), 1, 1).data], status: $('status').textContent});
                } catch (error) { done({error: error.message}); }
              });
            })().catch(error => done({error: error.message}));
          `, [city]);
          assert.ok(sampled.color, JSON.stringify(sampled));
          assert.match(sampled.status, /^Ready:/);
          assert.ok(sampled.color.every((value, i) => Math.abs(value - [255, 153, 26, 255][i]) <= 1),
            `${api}/${count}/${kind}/city ${city}: ${sampled.color}`);
          cases++;
        }
      }
      if (results[api]?.unsupported) break;
    }
    results[api] ??= {selectedPixelChecks: cases};
  }
  console.log(JSON.stringify({functionalOnly: true, headless: true,
    browser: opened.capabilities.browserVersion, dpr: Number(dpr), results}, null, 2));
} finally {
  try { if (session) await call('DELETE', `/session/${session}`); }
  finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}
