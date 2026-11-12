import { fileURLToPath } from 'node:url';
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { apiBoundary, backendOrigin } from './dev-api.ts';

export default defineConfig({
  plugins: [apiBoundary(), svelte()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    cors: false,
    proxy: {
      '/api/': {
        target: backendOrigin,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (outgoing, incoming) => {
            if (incoming.headers.origin) outgoing.setHeader('Origin', backendOrigin);
          });
        },
      },
    },
    fs: {
      allow: [fileURLToPath(new URL('.', import.meta.url))],
    },
  },
  // Preview is static-only; do not inherit a proxy without configureServer guards.
  preview: { host: "127.0.0.1", cors: false, proxy: {} },
});
