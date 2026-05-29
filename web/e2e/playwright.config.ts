import { defineConfig, devices } from "@playwright/test";

// E2E for the project's web surfaces:
//   - WO画面 (Warehouse Orchestrator dashboard, custom — Phase 4)
//   - rmf-web (Open-RMF dashboard — Mode C)
// Point BASE_URL at the running dashboard (defaults to the WO dev server).
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "html" : "list",
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
