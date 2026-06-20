import { defineConfig, devices } from "@playwright/test";

// E2E smoke for web/console — the Web Observability SPA (doc22 §15). The webServer serves the
// static export at web/console/out (build it first: `cd ../console && npm run build`). Set
// BASE_URL to point at an already-running server (e.g. a dev gateway) and the webServer is skipped.
const BASE_URL = process.env.BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "html" : "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: "npx serve ../console/out -l 3000",
        url: "http://localhost:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
