import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // Straight into the folder site_server.py serves, and the one
    // HyperFetch.spec bundles. One place, so a stale copy cannot be shipped.
    outDir: "../site",
    emptyOutDir: true,
    // The site is served from a phone's own network, so a smaller bundle is
    // worth more here than readable stack traces in production.
    sourcemap: false,
  },
  server: {
    // `npm run dev` talks to the real Python server rather than a mock, so the
    // front end is always developed against the API it will actually meet.
    proxy: { "/api": "http://127.0.0.1:5001" },
  },
});
