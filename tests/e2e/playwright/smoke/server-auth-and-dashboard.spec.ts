import { test, expect } from "@playwright/test";

test("server setup and dashboard flows are reachable", async ({ page, baseURL }) => {
  await page.goto(`${baseURL}/login`);
  await page.locator("#login-username").fill("admin");
  await page.locator("#login-password").fill("pass1234");
  await page.locator("#btn-login").click();

  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  // ADR-0018: the health grid was replaced by a status strip + four
  // summary tiles. "Recorder host" is the Tier-2 tile that subsumes the
  // old "System Health" section.
  await expect(page.getByText("Recorder host")).toBeVisible();
  await expect(page.getByText("Cameras")).toBeVisible();
});
