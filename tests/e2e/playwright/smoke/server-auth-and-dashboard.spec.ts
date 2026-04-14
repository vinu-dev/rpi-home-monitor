import { test, expect } from "@playwright/test";

test("server setup and dashboard flows are reachable", async ({ page, baseURL }) => {
  await page.goto(`${baseURL}/login`);
  await page.locator("#login-username").fill("admin");
  await page.locator("#login-password").fill("pass1234");
  await page.locator("#btn-login").click();

  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("System Health")).toBeVisible();
  await expect(page.getByText("Cameras")).toBeVisible();
});
