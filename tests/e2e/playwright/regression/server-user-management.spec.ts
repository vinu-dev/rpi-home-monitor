import { test, expect } from "@playwright/test";

test.describe("User management page", () => {
  test.beforeEach(async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/login`);
    await page.locator("#login-username").fill("admin");
    await page.locator("#login-password").fill("pass1234");
    await page.locator("#btn-login").click();
    await expect(page).toHaveURL(/\/dashboard/);
    await page.goto(`${baseURL}/users`);
  });

  test("users page renders without error", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: /users/i })
    ).toBeVisible();
  });

  test("admin user is listed", async ({ page }) => {
    await expect(page.getByText("admin")).toBeVisible();
  });

  test("add-user form is accessible", async ({ page }) => {
    const addBtn = page.locator(
      "#btn-add-user, button:has-text('Add'), button:has-text('New User')"
    );
    await expect(addBtn.first()).toBeVisible();
  });
});

test.describe("Viewer role access", () => {
  test("viewer cannot access users page", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/login`);
    await page.locator("#login-username").fill("viewer");
    await page.locator("#login-password").fill("pass1234");
    await page.locator("#btn-login").click();

    await page.goto(`${baseURL}/users`);
    // Should redirect to dashboard or show 403
    const url = page.url();
    const is403 = await page.locator("body").textContent().then(
      (t) => t?.includes("403") || t?.includes("Forbidden") || false
    );
    const redirected = !url.endsWith("/users");
    expect(is403 || redirected).toBe(true);
  });
});
