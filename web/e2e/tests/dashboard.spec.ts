import { expect, test } from "@playwright/test";

// Placeholder E2E for the WO / rmf-web dashboard (Phase 4).
// Skipped until the web UI exists. When implemented, replace with real flows:
//   - robot positions render and update in real time
//   - KPI panel updates (total distance, completed tasks, throughput)
//   - LLM reasoning log streams
test.describe("warehouse dashboard", () => {
  test.skip(true, "Web UI not implemented yet (Phase 4)");

  test("loads and shows both robots", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/bot1/i)).toBeVisible();
    await expect(page.getByText(/bot2/i)).toBeVisible();
  });
});
