const { test, expect } = require("@playwright/test");

const REMOTE = process.env.DASHBOARD_URL || "http://127.0.0.1:3000";

test.describe("Dashboard E2E", () => {

  test("T1: Login page renders", async ({ page }) => {
    await page.goto(`${REMOTE}/login`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await expect(page.locator("input").first()).toBeVisible({ timeout: 10000 });
  });

  test("T2: Mobile overview no 500", async ({ page }) => {
    const res = await page.goto(`${REMOTE}/mobile`, { waitUntil: "domcontentloaded", timeout: 15000 });
    expect(res.status()).not.toBe(500);
    const body = await page.textContent("body") || "";
    expect(body.length).toBeGreaterThan(100);
  });

  test("T3: Macro page no 500", async ({ page }) => {
    const res = await page.goto(`${REMOTE}/mobile/macro`, { waitUntil: "domcontentloaded", timeout: 15000 });
    expect(res.status()).not.toBe(500);
  });

  test("T4: Sentiment page no 500", async ({ page }) => {
    const res = await page.goto(`${REMOTE}/mobile/sentiment`, { waitUntil: "domcontentloaded", timeout: 15000 });
    expect(res.status()).not.toBe(500);
  });

  test("T5: Flow page no 500", async ({ page }) => {
    const res = await page.goto(`${REMOTE}/mobile/flow`, { waitUntil: "domcontentloaded", timeout: 15000 });
    expect(res.status()).not.toBe(500);
  });

  test("T6: Index page no 500", async ({ page }) => {
    const res = await page.goto(`${REMOTE}/mobile/index`, { waitUntil: "domcontentloaded", timeout: 15000 });
    expect(res.status()).not.toBe(500);
  });

  test("T7: API health", async ({ page }) => {
    const res = await page.goto(`${REMOTE}/api/health`, { waitUntil: "domcontentloaded", timeout: 10000 });
    const json = await res.json();
    expect(json.dashboard).toBe("ok");
    expect(json.data_service).toBe("ok");
  });

});
