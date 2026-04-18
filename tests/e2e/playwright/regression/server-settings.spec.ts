import { test, expect } from "@playwright/test";

test.describe("Settings page", () => {
  test.beforeEach(async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/login`);
    await page.locator("#login-username").fill("admin");
    await page.locator("#login-password").fill("pass1234");
    await page.locator("#btn-login").click();
    await expect(page).toHaveURL(/\/dashboard/);
    await page.goto(`${baseURL}/settings`);
  });

  test("settings page renders general section", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: /settings/i })
    ).toBeVisible();
    await expect(page.locator("#settings-hostname, [data-field='hostname'], input[name='hostname']")).toBeVisible();
  });

  test("settings page renders system section with time controls", async ({ page }) => {
    // Either a tab or a section labelled "System" or "Time"
    const systemSection = page.locator(
      "#settings-system, [data-section='system'], section:has-text('Time')"
    );
    await expect(systemSection.first()).toBeVisible();
  });

  test("settings page renders OTA section", async ({ page }) => {
    const otaSection = page.locator(
      "#settings-ota, [data-section='ota'], section:has-text('OTA'), section:has-text('Update')"
    );
    await expect(otaSection.first()).toBeVisible();
  });

  test("settings page renders wifi section", async ({ page }) => {
    const wifiSection = page.locator(
      "#settings-wifi, [data-section='wifi'], section:has-text('Wi-Fi'), section:has-text('WiFi')"
    );
    await expect(wifiSection.first()).toBeVisible();
  });
});
