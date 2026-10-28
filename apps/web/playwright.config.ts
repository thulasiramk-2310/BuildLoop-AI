import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

// Dedicated e2e config: its own API (:8100, temp DB) and plain-http Vite (:5183) so it never collides with dev servers.
// Uses the installed Chrome (no browser download), one worker, headless, to keep memory low.
const root = path.resolve(here, "../..");
const db = path.join(root, ".run", "e2e.db");

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5183", channel: "chrome", headless: true, trace: "retain-on-failure", screenshot: "only-on-failure",
    // PW_VIDEO=1 records a slowed-down walkthrough video (used for docs/demo/videos); normal runs stay fast and video-free.
    ...(process.env.PW_VIDEO ? { video: { mode: "on" as const, size: { width: 1440, height: 860 } }, viewport: { width: 1440, height: 860 } } : {}),
    launchOptions: { args: ["--disable-gpu", "--no-sandbox"], ...(process.env.PW_VIDEO ? { slowMo: 700 } : {}) },
  },
  webServer: [
    {
      command: `"${path.join(root, ".venv", "Scripts", "python.exe")}" -m uvicorn buildloop.main:app --port 8100`,
      cwd: path.join(root, "apps", "api"),
      url: "http://127.0.0.1:8100/api/health",
      reuseExistingServer: false,
      env: { DATABASE_URL: `sqlite:///${db.replace(/\\/g, "/")}`, MODEL_API_KEY: "", HF_TOKEN: "", OPEN_WEIGHT_API_KEY: "" }, // e2e never calls live models
      timeout: 60_000,
    },
    {
      command: "node node_modules/vite/bin/vite.js --port 5183 --strictPort",
      cwd: here,
      url: "http://localhost:5183",
      reuseExistingServer: false,
      env: { BUILDLOOP_HTTP: "1", BUILDLOOP_API: "http://127.0.0.1:8100" },
      timeout: 60_000,
    },
  ],
});
