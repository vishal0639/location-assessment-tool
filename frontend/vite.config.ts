import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, forward /api to the FastAPI container so the browser sees one origin.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
