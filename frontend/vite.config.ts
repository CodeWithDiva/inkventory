import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

declare const process: { env: Record<string, string | undefined> };

// The API runs on :8000 by default; proxy /api so the browser sees one origin.
const apiTarget = process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": apiTarget } },
  preview: { proxy: { "/api": apiTarget } },
});
