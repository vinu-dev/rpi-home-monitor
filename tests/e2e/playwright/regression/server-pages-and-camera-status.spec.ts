import { test, expect } from "@playwright/test";

/**
 * Regression: core post-login pages still render their structural anchors.
 *
 * Login is JS-driven (fetch + redirect to /dashboard), so we wait for the
 * dashboard URL before navigating onward — otherwise the goto races against
 * the cookie/redirect and lands back at /login.
 */
test("server live and recordings pages render core controls", async ({ page, baseURL }) => {
  await page.goto(`${baseURL}/login`);
  await page.locator("#login-username").fill("admin");
  await page.locator("#login-password").fill("pass1234");
  await Promise.all([
    page.waitForURL(/\/dashboard/),
    page.locator("#btn-login").click(),
  ]);

  await page.goto(`${baseURL}/live`);
  await expect(page.getByRole("heading", { name: "Live View" })).toBeVisible();
  await expect(page.locator("#live-camera-select")).toBeVisible();
  await expect(page.locator("#live-video")).toBeVisible();

  await page.goto(`${baseURL}/recordings`);
  await expect(page.getByRole("heading", { name: "Recordings" })).toBeVisible();
});

/**
 * Camera status page section labels were redesigned (issue #109 tap-target
 * refresh). Old assertions (Device Info / Connection / Server Pairing /
 * System Health) no longer exist — the navigation is now Status / Settings /
 * Updates / Reset with an initial pairing banner when unpaired.
 */
test("camera status login and device page render", async ({ page }) => {
  await page.goto("https://127.0.0.1:5444/login");
  await page.locator("#username").fill("admin");
  await page.locator("#password").fill("pass1234");
  await Promise.all([
    page.waitForURL((url) => !url.pathname.endsWith("/login")),
    page.getByRole("button", { name: /sign in|login/i }).click(),
  ]);

  // Section nav links — these are stable structural anchors per the
  // rewritten status.html (see <nav aria-label="Page sections">).
  await expect(page.getByRole("link", { name: "Status" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Updates" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Reset" })).toBeVisible();
});
