import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import basicSsl from "@vitejs/plugin-basic-ssl";

// HTTPS is required for phone camera access (getUserMedia needs a secure context off localhost).
// The self-signed certificate is accepted once in the phone browser. API/WS are proxied to FastAPI on :8000.
const API = process.env.BUILDLOOP_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  // Set BUILDLOOP_HTTP=1 for plain http on localhost (browser automation cannot pass the certificate interstitial).
  plugins: [react(), tailwindcss(), ...(process.env.BUILDLOOP_HTTP ? [] : [basicSsl()])],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target: API, changeOrigin: true },
      "/ws": { target: API.replace(/^http/, "ws"), ws: true },
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
