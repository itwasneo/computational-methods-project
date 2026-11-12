import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer as httpServer, request } from 'node:http';
import { createServer } from 'vite';
import config from '../vite.config.ts';
import { allowedApiRequest, backendOrigin, frontendOrigin } from '../dev-api.ts';

const allowed = changes => allowedApiRequest({ url: '/api/session', rawHeaders: [],
  headers: { host: '127.0.0.1:5173' }, ...changes });

test('development boundary rejects foreign/duplicate authority before rewriting', () => {
  assert.equal(allowed({}), true);
  for (const headers of [{ host: 'evil.example' }, { host: 'localhost:5173' },
    { host: '127.0.0.1:5173', origin: 'null' }, { host: '127.0.0.1:5173', origin: 'https://evil.example' },
    { host: '127.0.0.1:5173', 'sec-fetch-site': 'cross-site' },
    { host: '127.0.0.1:5173', 'sec-fetch-site': 'same-site' }]) assert.equal(allowed({ headers }), false);
  assert.equal(allowed({ rawHeaders: ['Origin', frontendOrigin, 'origin', frontendOrigin] }), false);
  assert.equal(allowed({ url: '/api/session?secret=never-in-a-url' }), false);
  assert.deepEqual(config.preview.proxy, {});
  assert.equal(config.server.cors, false); assert.equal(config.server.strictPort, true);
});

test('actual Vite proxy forwards only accepted same-origin API requests', async t => {
  const received = [];
  const backend = httpServer((req, res) => {
    received.push(req.headers);
    req.resume();
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end('{"ok":true}');
  });
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => backend.close(resolve)));
  const server = await createServer({ ...config, configFile: false, logLevel: 'silent',
    server: { ...config.server, port: 0, proxy: { '/api/': {
      ...config.server.proxy['/api/'], target: `http://127.0.0.1:${backend.address().port}`,
    } } } });
  t.after(() => server.close());
  // Vite's listen() treats configured port 0 as its default port. Bind the
  // underlying server instead so this test never contends with a running app.
  await new Promise((resolve, reject) => {
    server.httpServer.once('error', reject);
    server.httpServer.listen(0, '127.0.0.1', resolve);
  });
  async function send(headers) {
    return new Promise((resolve, reject) => {
      const req = request({ host: '127.0.0.1', port: server.httpServer.address().port, path: '/api/session', method: 'POST',
        headers }, res => { res.resume(); res.on('end', () => resolve(res.statusCode)); });
      req.on('error', reject); req.end('{}');
    });
  }
  assert.equal(await send({ Host: '127.0.0.1:5173', Origin: frontendOrigin, 'Sec-Fetch-Site': 'same-origin',
    'Content-Type': 'application/json', Authorization: 'Bearer fixture-token' }), 200);
  assert.equal(received.length, 1);
  assert.equal(received[0].origin, backendOrigin);
  assert.equal(received[0].host, `127.0.0.1:${backend.address().port}`);
  assert.equal(received[0].authorization, 'Bearer fixture-token');
  assert.equal(await send({ Host: '127.0.0.1:5173', Origin: 'https://evil.example' }), 403);
  assert.equal(await send(['Host', '127.0.0.1:5173', 'Origin', frontendOrigin, 'origin', frontendOrigin]), 403);
  assert.equal(received.length, 1);
});
