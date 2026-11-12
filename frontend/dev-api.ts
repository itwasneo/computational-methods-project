// Development only: validate the browser-facing authority BEFORE rewriting for
// the fixed loopback backend. Never turn a foreign Origin into a trusted one.
import type { IncomingMessage } from 'node:http';
import type { Plugin } from 'vite';

export const frontendOrigin = 'http://127.0.0.1:5173';
export const backendOrigin = 'http://127.0.0.1:8000';

export function allowedApiRequest(request: Pick<IncomingMessage, 'headers' | 'rawHeaders' | 'url'>): boolean {
  const guarded = new Set(['host', 'origin', 'authorization', 'content-length', 'content-type', 'sec-fetch-site', 'x-request-id']);
  const seen = new Set<string>();
  for (let index = 0; index < request.rawHeaders.length; index += 2) {
    const name = request.rawHeaders[index].toLowerCase();
    if (guarded.has(name) && seen.has(name)) return false;
    seen.add(name);
  }
  return request.headers.host === '127.0.0.1:5173'
    && (request.headers.origin === undefined || request.headers.origin === frontendOrigin)
    && ['none', 'same-origin'].includes(String(request.headers['sec-fetch-site'] ?? 'none'))
    && !request.url?.includes('?');
}

export function apiBoundary(): Plugin {
  return {
    name: 'local-api-boundary',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (!request.url?.startsWith('/api')) return next();
        if (allowedApiRequest(request)) return next();
        const supplied = request.headers['x-request-id'];
        response.writeHead(403, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
        response.end(JSON.stringify({ api_version: 1,
          request_id: typeof supplied === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(supplied) ? supplied : 'dev-proxy',
          code: 'unauthorized', message: 'Development API request authority rejected.', retryable: false }));
      });
    },
  };
}
