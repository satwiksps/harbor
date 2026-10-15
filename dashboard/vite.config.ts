import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.VITE_PROXY_TARGET || "http://harbor.localhost:8080";
const proxy = {
  target: backend,
  changeOrigin: true,
  headers: { Origin: new URL(backend).origin },
};

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": proxy,
      "/mcp": proxy,
    },
  },
});
