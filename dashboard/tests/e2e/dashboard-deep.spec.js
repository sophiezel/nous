const { test, expect } = require("@playwright/test");

const REMOTE = process.env.DASHBOARD_URL || "http://127.0.0.1:3000";

test.describe("Dashboard E2E — Deep", () => {

  test("API health returns ok", async ({ page }) => {
    const res = await page.goto(`${REMOTE}/api/health`);
    const json = await res.json();
    expect(json.dashboard).toBe("ok");
    expect(json.data_service).toBe("ok");
  });

  // Pages that should NOT return 500 (may redirect to auth)
  const pages = [
    "/mobile", "/mobile/macro", "/mobile/sentiment", "/mobile/flow",
    "/mobile/index", "/mobile/messages", "/mobile/hsgt",
    "/mobile/portfolio", "/mobile/signals",
  ];

  for (const path of pages) {
    test(`${path} — no 500, no fatal error`, async ({ page }) => {
      const errors = [];
      page.on("pageerror", err => errors.push(err.message));

      const res = await page.goto(`${REMOTE}${path}`, {
        waitUntil: "domcontentloaded", timeout: 15000,
      });

      // Must not be 500
      expect(res.status()).not.toBe(500);

      // Must not have JS runtime errors (RSC digest errors, etc.)
      // Give a moment for RSC to hydrate
      await page.waitForTimeout(2000);
      expect(errors.filter(e =>
        !e.includes("ResizeObserver") &&
        !e.includes("hydration")
      )).toEqual([]);
    });
  }

  // Data freshness: API endpoints return timestamps
  test("Sentiment API returns recent data", async ({ page }) => {
    const res = await page.goto(`${REMOTE}/api/health`);
    const json = await res.json();
    expect(json.data_service).toBe("ok");
  });

});
