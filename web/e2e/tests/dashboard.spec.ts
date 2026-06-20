import { expect, test } from "@playwright/test";

// Smoke E2E for web/console (the observability SPA, doc22 §15). Runs against the STATIC export
// with NO gateway, so the dashboard boots and the panels mount in their idle/empty states (the
// live WebSocket data path is exercised by live runs, not here). Mode defaults to "none" (Mode A)
// without /config, so the Mode-gated conversation / ringi panels are visible too (doc22 §12.1).

test.describe("web/console observability dashboard", () => {
  test("/live boots and mounts every panel", async ({ page }) => {
    await page.goto("/live/");
    await expect(page.getByRole("heading", { name: "倉庫観測コンソール" })).toBeVisible();
    for (const title of [
      "2Dマップ",
      "ロボット状態",
      "会話タイムライン",
      "稟議フロー",
      "司令官の判断",
      "緊急イベント",
    ]) {
      await expect(page.getByRole("heading", { name: title })).toBeVisible();
    }
    // observe-only idle state renders with no gateway connected.
    await expect(page.getByText("交渉なし（通常サイクル）")).toBeVisible();
  });

  test("/runs loads the runs picker", async ({ page }) => {
    await page.goto("/runs/");
    await expect(page.getByRole("heading", { name: "記録 (runs)" })).toBeVisible();
  });
});
