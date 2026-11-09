import { fileURLToPath } from 'node:url';
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  plugins: [svelte()],
  server: {
    host: "127.0.0.1",
    fs: {
      allow: [fileURLToPath(new URL('.', import.meta.url))],
    },
  },
  preview: { host: "127.0.0.1" },
});
